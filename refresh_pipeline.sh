#!/usr/bin/env bash
# =============================================================================
# Mobile QA Daily Refresh Pipeline
# Keeps Streamlit running on port 8501 throughout the entire update process.
#
# Strategy: all scripts write to data/staging/<slug>/, then files are
# atomically swapped into data/products/<slug>/ with `mv` (rename syscall)
# only after each stage succeeds. Streamlit always reads a complete,
# self-consistent set of files per product.
#
# Schedule behaviour (auto-detected unless overridden):
#   Weekdays (Mon–Fri): PDR-i and PHD-i, last 1 month
#   Weekends (Sat–Sun): PDR-i, PDR-a, PHD-i, PHD-a, last 36 months
#
# Usage:
#   ./refresh_pipeline.sh                        # auto weekday/weekend schedule
#   ./refresh_pipeline.sh --dry-run              # print steps, don't execute
#   ./refresh_pipeline.sh --products=pdri,phdi   # override which products to run
#   ./refresh_pipeline.sh --duration=1           # override fetch duration (months)
#   ./refresh_pipeline.sh --skip-ollama          # use heuristic scorer / TF-IDF
#   ./refresh_pipeline.sh --skip-cluster         # skip clustering (saves ~5 min)
#   ./refresh_pipeline.sh --skip-predict         # skip predictions
#
# Also accepted for compatibility:
#   --skip_ollama[=true|false]
#   --skip_cluster[=true|false]
#   --skip_predict[=true|false]
#   --dry_run[=true|false]
#
# Cron example (runs every day at 03:00):
#   0 3 * * * /Users/yourname/pdri-qa-toolkit/refresh_pipeline.sh >> /Users/yourname/pdri-qa-toolkit/logs/refresh.log 2>&1
# =============================================================================

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV="$SCRIPT_DIR/.venv/bin/activate"
DATA="$SCRIPT_DIR/data"
LOGS="$SCRIPT_DIR/logs"
LOG_FILE="$LOGS/refresh_$(date +%Y%m%d_%H%M%S).log"
OLLAMA_MODEL="llama3.1"
STREAMLIT_PORT=8501

# ── Schedule defaults (overridden by --products / --duration) ─────────────────
# Weekday: fast daily refresh of the two primary products
WEEKDAY_PRODUCTS="pdri phdi"
WEEKDAY_DURATION=1
# Weekend: full historical refresh of all products
WEEKEND_PRODUCTS="pdri pdra phdi phda"
WEEKEND_DURATION=36

# ── Flags ─────────────────────────────────────────────────────────────────────
DRY_RUN=false
SKIP_OLLAMA=false
SKIP_CLUSTER=false
SKIP_PREDICT=false
OVERRIDE_PRODUCTS=""   # comma-separated slug list, e.g. "pdri,phdi"
OVERRIDE_DURATION=""   # integer months, e.g. "1"

parse_bool_flag() {
  local raw
  raw="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  case "$raw" in
    true|1|yes|on|"")  echo true ;;
    false|0|no|off)     echo false ;;
    *)
      echo "ERROR: invalid boolean value '$1'" >&2
      exit 1
      ;;
  esac
}

for arg in "$@"; do
  case $arg in
    --dry-run|--dry_run)                  DRY_RUN=true ;;
    --dry-run=*|--dry_run=*)              DRY_RUN=$(parse_bool_flag "${arg#*=}") ;;
    --skip-ollama|--skip_ollama)          SKIP_OLLAMA=true ;;
    --skip-ollama=*|--skip_ollama=*)      SKIP_OLLAMA=$(parse_bool_flag "${arg#*=}") ;;
    --skip-cluster|--skip_cluster)        SKIP_CLUSTER=true ;;
    --skip-cluster=*|--skip_cluster=*)    SKIP_CLUSTER=$(parse_bool_flag "${arg#*=}") ;;
    --skip-predict|--skip_predict)        SKIP_PREDICT=true ;;
    --skip-predict=*|--skip_predict=*)    SKIP_PREDICT=$(parse_bool_flag "${arg#*=}") ;;
    --products=*)                         OVERRIDE_PRODUCTS="${arg#*=}" ;;
    --duration=*)                         OVERRIDE_DURATION="${arg#*=}" ;;
    *)
      echo "ERROR: unknown argument '$arg'" >&2
      exit 1
      ;;
  esac
done

# ── Determine active products and duration ────────────────────────────────────
# Weekday (0=Mon…4=Fri) → fast schedule; Weekend (5=Sat,6=Sun) → full schedule.
DOW=$(python3 -c "import datetime; print(datetime.datetime.now().weekday())")
if [ -n "$OVERRIDE_PRODUCTS" ]; then
  # Replace commas with spaces for easy iteration
  ACTIVE_PRODUCTS="${OVERRIDE_PRODUCTS//,/ }"
elif [ "$DOW" -ge 5 ]; then
  ACTIVE_PRODUCTS="$WEEKEND_PRODUCTS"
else
  ACTIVE_PRODUCTS="$WEEKDAY_PRODUCTS"
fi

if [ -n "$OVERRIDE_DURATION" ]; then
  ACTIVE_DURATION="$OVERRIDE_DURATION"
elif [ "$DOW" -ge 5 ]; then
  ACTIVE_DURATION="$WEEKEND_DURATION"
else
  ACTIVE_DURATION="$WEEKDAY_DURATION"
fi

# ── Helpers ───────────────────────────────────────────────────────────────────
mkdir -p "$LOGS"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

run() {
  if $DRY_RUN; then
    log "  [DRY-RUN] $*"
  else
    log "  Running: $*"
    eval "$*" 2>&1 | tee -a "$LOG_FILE"
    return "${PIPESTATUS[0]}"
  fi
}

run_silent() {
  if $DRY_RUN; then
    log "  [DRY-RUN] $*"
  else
    eval "$*" >> "$LOG_FILE" 2>&1
    return $?
  fi
}

promote() {
  local src="$1" dst="$2"
  if $DRY_RUN; then
    log "  [DRY-RUN] promote $src -> $dst"
    return
  fi
  if [ ! -f "$src" ]; then
    log "  ERROR: staging file not found: $src"
    return 1
  fi
  mv "$src" "$dst"
  log "  ✅ Promoted: $(basename "$dst")"
}

promote_dir() {
  local src_dir="$1" dst_dir="$2"
  if $DRY_RUN; then
    log "  [DRY-RUN] promote_dir $src_dir -> $dst_dir"
    return
  fi
  mkdir -p "$dst_dir"
  for f in "$src_dir"/*; do
    [ -f "$f" ] && mv "$f" "$dst_dir/$(basename "$f")"
  done
  log "  ✅ Promoted dir: $(basename "$dst_dir")"
}

ensure_streamlit_running() {
  if lsof -ti tcp:"$STREAMLIT_PORT" >/dev/null 2>&1; then
    log "  Streamlit already running on :$STREAMLIT_PORT — leaving it alone."
  else
    log "  Streamlit not running — starting it on :$STREAMLIT_PORT"
    source "$VENV"
    nohup streamlit run "$SCRIPT_DIR/scripts/bug_heatmap_dashboard.py" \
      --server.address 0.0.0.0 \
      --server.port "$STREAMLIT_PORT" \
      --server.headless true \
      >> "$LOGS/streamlit.log" 2>&1 &
    sleep 3
    log "  Streamlit PID: $!"
  fi
}

# ── Per-product pipeline ───────────────────────────────────────────────────────
# run_product_pipeline SLUG DURATION_MONTHS
# All outputs go to data/products/<slug>/; staging goes to data/staging/<slug>/
run_product_pipeline() {
  local SLUG="$1"
  local DURATION="$2"

  local PROD_DATA="$DATA/products/$SLUG"
  local STAGING="$DATA/staging/$SLUG"
  mkdir -p "$PROD_DATA" "$STAGING"

  log ""
  log "╔══════════════════════════════════════════════════════╗"
  log "║  Product: $SLUG   duration=${DURATION}m              "
  log "╚══════════════════════════════════════════════════════╝"

  # ── Stage 1: Fetch + Parse ──────────────────────────────────────────────────
  log ""
  log "── [$SLUG] Stage 1: Fetch + Parse ──────────────────────"

  run "python scripts/fetch_from_n8n.py \
    --product '$SLUG' \
    --duration '$DURATION' \
    --scope all \
    --output '$STAGING/ecl_raw.json' \
    --parsed-output '$STAGING/ecl_parsed.csv' \
    --then-parse"

  promote "$STAGING/ecl_raw.json"    "$PROD_DATA/ecl_raw.json"
  promote "$STAGING/ecl_parsed.csv"  "$PROD_DATA/ecl_parsed.csv"
  [ -f "$STAGING/version_catalogue.csv" ] && \
    promote "$STAGING/version_catalogue.csv" "$PROD_DATA/version_catalogue.csv"

  log "[$SLUG] Stage 1 complete."

  # ── Stage 2: Risk Register ──────────────────────────────────────────────────
  log ""
  log "── [$SLUG] Stage 2: Risk Register ──────────────────────"

  run "python scripts/compute_risk_scores.py \
    '$PROD_DATA/ecl_parsed.csv' \
    '$STAGING/risk_register_all.csv'"

  promote "$STAGING/risk_register_all.csv" "$PROD_DATA/risk_register_all.csv"
  promote_dir "$STAGING/risk_register_versions" "$PROD_DATA/risk_register_versions"

  log "[$SLUG] Stage 2 complete."

  # ── Stage 3: AI Risk Scoring ────────────────────────────────────────────────
  log ""
  log "── [$SLUG] Stage 3: AI Risk Scoring ────────────────────"

  if [ -f "$PROD_DATA/risk_register_scored_all.csv" ]; then
    cp "$PROD_DATA/risk_register_scored_all.csv" "$STAGING/risk_register_scored_all.csv"
    log "  Seeded staging with previous scored file (resume cache active)."
  fi
  if [ -d "$PROD_DATA/risk_register_versions" ]; then
    mkdir -p "$STAGING/risk_register_versions"
    for f in "$PROD_DATA/risk_register_versions"/risk_register_scored_*.csv; do
      [ -f "$f" ] && cp "$f" "$STAGING/risk_register_versions/$(basename "$f")"
    done
  fi

  if $SKIP_OLLAMA; then
    log "  Using heuristic scorer (--skip-ollama)"
    run "python scripts/ai_risk_scorer.py \
      '$PROD_DATA/risk_register_all.csv' \
      '$STAGING/risk_register_scored_all.csv' \
      --provider heuristic"
  else
    if ! pgrep -x ollama >/dev/null 2>&1; then
      log "  Starting Ollama..."
      run_silent "ollama serve &>/dev/null &"
      run_silent "sleep 5"
    else
      log "  Ollama already running."
    fi

    run "python scripts/ai_risk_scorer.py \
      '$PROD_DATA/risk_register_all.csv' \
      '$STAGING/risk_register_scored_all.csv' \
      --provider ollama \
      --model '$OLLAMA_MODEL'"
  fi

  promote "$STAGING/risk_register_scored_all.csv" "$PROD_DATA/risk_register_scored_all.csv"
  promote_dir "$STAGING/risk_register_versions"   "$PROD_DATA/risk_register_versions"

  log "[$SLUG] Stage 3 complete."

  # ── Stage 4: Test Skeletons ─────────────────────────────────────────────────
  log ""
  log "── [$SLUG] Stage 4: Test Skeletons + Summary ───────────"

  run_silent "python scripts/auto_tag_tests.py \
    '$PROD_DATA/risk_register_scored_all.csv' \
    --generate-skeletons 'tests/generated/$SLUG/' \
    --summary"

  log "[$SLUG] Stage 4 complete."

  # ── Stage 5: Clustering ─────────────────────────────────────────────────────
  if $SKIP_CLUSTER; then
    log ""
    log "── [$SLUG] Stage 5: Clustering SKIPPED ─────────────────"
  else
    log ""
    log "── [$SLUG] Stage 5: Clustering ─────────────────────────"

    CLUSTER_OUT="$PROD_DATA/clusters"
    mkdir -p "$CLUSTER_OUT"

    if $SKIP_OLLAMA; then
      run "python scripts/cluster_bugs.py \
        '$PROD_DATA/ecl_parsed.csv' \
        '$CLUSTER_OUT/ecl_parsed_clustered.csv' \
        --provider tfidf"
    else
      run "python scripts/cluster_bugs.py \
        '$PROD_DATA/ecl_parsed.csv' \
        '$CLUSTER_OUT/ecl_parsed_clustered.csv' \
        --provider ollama \
        --model '$OLLAMA_MODEL'"
    fi

    log "[$SLUG] Stage 5 complete."
  fi

  # ── Stage 6: Defect Predictions ─────────────────────────────────────────────
  if $SKIP_PREDICT; then
    log ""
    log "── [$SLUG] Stage 6: Predictions SKIPPED ────────────────"
  else
    log ""
    log "── [$SLUG] Stage 6: Defect Predictions ─────────────────"

    PRED_STAGING="$STAGING/predictions"
    mkdir -p "$PRED_STAGING"

    if $SKIP_OLLAMA; then
      run "python scripts/predict_defects.py \
        '$PROD_DATA/ecl_parsed.csv' \
        '$PRED_STAGING/ecl_parsed_predictions.csv'"
    else
      run "python scripts/predict_defects.py \
        '$PROD_DATA/ecl_parsed.csv' \
        '$PRED_STAGING/ecl_parsed_predictions.csv' \
        --provider ollama \
        --model '$OLLAMA_MODEL'"
    fi

    promote_dir "$PRED_STAGING" "$PROD_DATA/predictions"

    log "[$SLUG] Stage 6 complete."
  fi
}

# ── Main ──────────────────────────────────────────────────────────────────────

log "======================================================"
log "Mobile QA Daily Refresh  $(date '+%Y-%m-%d %H:%M:%S')"
log "day_of_week=$DOW  products=[$ACTIVE_PRODUCTS]  duration=${ACTIVE_DURATION}m"
log "dry_run=$DRY_RUN  skip_ollama=$SKIP_OLLAMA  skip_cluster=$SKIP_CLUSTER  skip_predict=$SKIP_PREDICT"
log "======================================================"

ensure_streamlit_running

source "$VENV"

# Run the full pipeline for each active product
for SLUG in $ACTIVE_PRODUCTS; do
  run_product_pipeline "$SLUG" "$ACTIVE_DURATION"
done

# ── Restart Streamlit ────────────────────────────────────────────────────────
log ""
log "── Restarting Streamlit ────────────────────────────────"

STREAMLIT_PID=$(lsof -ti tcp:"$STREAMLIT_PORT" -sTCP:LISTEN 2>/dev/null || true)
if [ -n "$STREAMLIT_PID" ]; then
  if $DRY_RUN; then
    log "  [DRY-RUN] kill $STREAMLIT_PID"
  else
    kill "$STREAMLIT_PID" 2>/dev/null || true
    for _i in $(seq 1 10); do
      lsof -ti tcp:"$STREAMLIT_PORT" -sTCP:LISTEN >/dev/null 2>&1 || break
      sleep 1
    done
    log "  Stopped Streamlit (PID $STREAMLIT_PID)"
  fi
else
  log "  Streamlit was not running — nothing to kill."
fi

if $DRY_RUN; then
  log "  [DRY-RUN] streamlit run scripts/bug_heatmap_dashboard.py"
else
  nohup streamlit run "$SCRIPT_DIR/scripts/bug_heatmap_dashboard.py" \
    --server.address 0.0.0.0 \
    --server.port "$STREAMLIT_PORT" \
    --server.headless true \
    >> "$LOGS/streamlit.log" 2>&1 &
  NEW_PID=$!
  sleep 3
  log "  Streamlit restarted (PID $NEW_PID) on :$STREAMLIT_PORT"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
log ""
log "======================================================"
log "Refresh complete  $(date '+%Y-%m-%d %H:%M:%S')"
log "Products processed: [$ACTIVE_PRODUCTS]"
log "Streamlit restarted fresh — no stale cache."
log "Log: $LOG_FILE"
log "======================================================"

find "$LOGS" -name "refresh_*.log" -mtime +30 -delete 2>/dev/null || true

