#!/usr/bin/env bash
# =============================================================================
# QA Daily Refresh Pipeline — Multi-Product
# Keeps Streamlit running on port 8501 throughout the entire update process.
#
# Strategy: all scripts write to data/staging/, then files are atomically
# swapped into data/ with `mv` (rename syscall) only after each stage
# succeeds. Streamlit always reads a complete, self-consistent set of files.
#
# Product scheduling:
#   Weekdays (Mon–Fri): PDRi + PHDi with 1-month data window
#   Weekends (Sat–Sun): All products with 36-month data window
#   Override with --products and --duration-months
#
# Usage:
#   ./refresh_pipeline.sh                                # auto schedule
#   ./refresh_pipeline.sh --dry-run                      # print steps, don't execute
#   ./refresh_pipeline.sh --products pdri,phdi           # specific products
#   ./refresh_pipeline.sh --products pdri --duration-months 1
#   ./refresh_pipeline.sh --weekday                      # force weekday schedule
#   ./refresh_pipeline.sh --weekend                      # force weekend (full) schedule
#   ./refresh_pipeline.sh --skip-ollama                  # use heuristic scorer
#   ./refresh_pipeline.sh --skip-cluster                 # skip clustering
#   ./refresh_pipeline.sh --skip-predict                 # skip predictions
#   ./refresh_pipeline.sh --ollama-model gemma4                    # default
#   ./refresh_pipeline.sh --ollama-model gemma4:e2b-it-q4_K_M     # quantised variant
#   ./refresh_pipeline.sh --embed-model nomic-embed-text           # default embed model
#   ./refresh_pipeline.sh --embed-model mxbai-embed-large          # alternative embed model
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
STAGING="$SCRIPT_DIR/data/staging"
LOGS="$SCRIPT_DIR/logs"
LOG_FILE="$LOGS/refresh_$(date +%Y%m%d_%H%M%S).log"
OLLAMA_MODEL="gemma4"
EMBED_MODEL="nomic-embed-text"
STREAMLIT_PORT=8501
export PYTHONUNBUFFERED=1   # force Python to flush stdout line-by-line when piped

# ── Product Schedule ─────────────────────────────────────────────────────────
# Format: "slug:duration_months slug:duration_months ..."
WEEKDAY_PRODUCTS="pdri:1 phdi:1"
WEEKEND_PRODUCTS="pdri:36 phdi:36 pdra:36 phda:36 pdr:36 phd:36 promeo:36"

# ── Flags ─────────────────────────────────────────────────────────────────────
DRY_RUN=false
SKIP_OLLAMA=false
SKIP_CLUSTER=false
SKIP_PREDICT=false
OVERRIDE_PRODUCTS=""
OVERRIDE_DURATION=""
FORCE_SCHEDULE=""

for arg in "$@"; do
  case $arg in
    --dry-run)            DRY_RUN=true ;;
    --skip-ollama)        SKIP_OLLAMA=true ;;
    --skip-cluster)       SKIP_CLUSTER=true ;;
    --skip-predict)       SKIP_PREDICT=true ;;
    --weekday)            FORCE_SCHEDULE=weekday ;;
    --weekend|--full)     FORCE_SCHEDULE=weekend ;;
    --products=*)         OVERRIDE_PRODUCTS="${arg#*=}" ;;
    --products)           ;; # value comes in next arg
    --duration-months=*)  OVERRIDE_DURATION="${arg#*=}" ;;
    --duration-months)    ;; # value comes in next arg
    --ollama-model=*)     OLLAMA_MODEL="${arg#*=}" ;;
    --ollama-model)       ;; # value comes in next arg
    --embed-model=*)      EMBED_MODEL="${arg#*=}" ;;
    --embed-model)        ;; # value comes in next arg
    *)
      # Handle --products VALUE and --duration-months VALUE (space-separated)
      ;;
  esac
done

# Parse positional-style --products VALUE and --duration-months VALUE
while [[ $# -gt 0 ]]; do
  case $1 in
    --products)
      if [[ $# -gt 1 && ! "$2" =~ ^-- ]]; then
        OVERRIDE_PRODUCTS="$2"
        shift
      fi
      ;;
    --duration-months)
      if [[ $# -gt 1 && ! "$2" =~ ^-- ]]; then
        OVERRIDE_DURATION="$2"
        shift
      fi
      ;;
    --ollama-model)
      if [[ $# -gt 1 && ! "$2" =~ ^-- ]]; then
        OLLAMA_MODEL="$2"
        shift
      fi
      ;;
    --embed-model)
      if [[ $# -gt 1 && ! "$2" =~ ^-- ]]; then
        EMBED_MODEL="$2"
        shift
      fi
      ;;
  esac
  shift
done

# ── Helpers ───────────────────────────────────────────────────────────────────
mkdir -p "$STAGING" "$LOGS"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

# run CMD
#   Executes CMD and pipes both stdout and stderr through `tee -a` so output
#   appears in the terminal in real time AND is captured in the log file.
run() {
  if $DRY_RUN; then
    log "  [DRY-RUN] $*"
  else
    log "  Running: $*"
    # stdout (print lines) → log file only; stderr (tqdm bars + errors) → terminal
    eval "$*" >> "$LOG_FILE"
  fi
}

# run_silent CMD
#   Like run() but suppresses terminal output.
run_silent() {
  if $DRY_RUN; then
    log "  [DRY-RUN] $*"
  else
    eval "$*" >> "$LOG_FILE" 2>&1
    return $?
  fi
}

# Atomically promote a staging file to live data.
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
  mkdir -p "$(dirname "$dst")"
  mv "$src" "$dst"
  log "  ✅ Promoted: $(basename "$dst")"
}

# Promote an entire staging sub-directory.
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

# ── Per-Product Pipeline ─────────────────────────────────────────────────────
# Runs Stages 1–6 for a single product slug with a given duration.
run_product_pipeline() {
  local SLUG="$1"
  local DURATION="$2"

  local DATA_PRODUCT="$DATA/products/$SLUG"
  local STAGING_PRODUCT="$STAGING/products/$SLUG"
  mkdir -p "$DATA_PRODUCT" "$STAGING_PRODUCT"

  log ""
  log "╔════════════════════════════════════════════════════════╗"
  log "║  Product: $SLUG  (duration: ${DURATION} months)"
  log "╚════════════════════════════════════════════════════════╝"

  # ── Stage 1: Fetch + Parse ──────────────────────────────────────────────
  log ""
  log "── [$SLUG] Stage 1: Fetch + Parse ───────────────────────"

  run "python scripts/fetch_from_n8n.py \
    --product '$SLUG' \
    --duration-months '$DURATION' \
    --scope all \
    --output '$STAGING_PRODUCT/ecl_raw.json' \
    --parsed-output '$STAGING_PRODUCT/ecl_parsed.csv' \
    --then-parse"

  promote "$STAGING_PRODUCT/ecl_raw.json"    "$DATA_PRODUCT/ecl_raw.json"
  promote "$STAGING_PRODUCT/ecl_parsed.csv"  "$DATA_PRODUCT/ecl_parsed.csv"

  [ -f "$STAGING_PRODUCT/version_catalogue.csv" ] && \
    promote "$STAGING_PRODUCT/version_catalogue.csv" "$DATA_PRODUCT/version_catalogue.csv"

  log "[$SLUG] Stage 1 complete."

  # ── Stage 2: Risk Register ─────────────────────────────────────────────
  log ""
  log "── [$SLUG] Stage 2: Risk Register ───────────────────────"

  run "python scripts/compute_risk_scores.py \
    '$DATA_PRODUCT/ecl_parsed.csv' \
    '$STAGING_PRODUCT/risk_register_all.csv'"

  promote "$STAGING_PRODUCT/risk_register_all.csv" "$DATA_PRODUCT/risk_register_all.csv"

  if [ -d "$STAGING_PRODUCT/risk_register_versions" ]; then
    promote_dir "$STAGING_PRODUCT/risk_register_versions" "$DATA_PRODUCT/risk_register_versions"
  fi

  log "[$SLUG] Stage 2 complete."

  # ── Stage 3: AI Risk Scoring ───────────────────────────────────────────
  log ""
  log "── [$SLUG] Stage 3: AI Risk Scoring ─────────────────────"

  # Seed staging with the last good scored file so resume/checkpoint works.
  if [ -f "$DATA_PRODUCT/risk_register_scored_all.csv" ]; then
    cp "$DATA_PRODUCT/risk_register_scored_all.csv" "$STAGING_PRODUCT/risk_register_scored_all.csv"
    log "  Seeded staging with previous scored file (resume cache active)."
  fi
  if [ -d "$DATA_PRODUCT/risk_register_versions" ]; then
    mkdir -p "$STAGING_PRODUCT/risk_register_versions"
    for f in "$DATA_PRODUCT/risk_register_versions"/risk_register_scored_*.csv; do
      [ -f "$f" ] && cp "$f" "$STAGING_PRODUCT/risk_register_versions/$(basename "$f")"
    done
  fi

  if $SKIP_OLLAMA; then
    log "  Using heuristic scorer (--skip-ollama)"
    run "python scripts/ai_risk_scorer.py \
      '$DATA_PRODUCT/risk_register_all.csv' \
      '$STAGING_PRODUCT/risk_register_scored_all.csv' \
      --provider heuristic"
  else
    run "python scripts/ai_risk_scorer.py \
      '$DATA_PRODUCT/risk_register_all.csv' \
      '$STAGING_PRODUCT/risk_register_scored_all.csv' \
      --provider ollama \
      --model '$OLLAMA_MODEL'"
  fi

  promote "$STAGING_PRODUCT/risk_register_scored_all.csv" "$DATA_PRODUCT/risk_register_scored_all.csv"
  if [ -d "$STAGING_PRODUCT/risk_register_versions" ]; then
    promote_dir "$STAGING_PRODUCT/risk_register_versions" "$DATA_PRODUCT/risk_register_versions"
  fi

  log "[$SLUG] Stage 3 complete."

  # ── Stage 4: Test Skeletons ────────────────────────────────────────────
  log ""
  log "── [$SLUG] Stage 4: Test Skeletons + Summary ────────────"

  run_silent "python scripts/auto_tag_tests.py \
    '$DATA_PRODUCT/risk_register_scored_all.csv' \
    --generate-skeletons tests/generated/$SLUG/ \
    --summary"

  log "[$SLUG] Stage 4 complete."

  # ── Stage 5: Clustering ────────────────────────────────────────────────
  if $SKIP_CLUSTER; then
    log ""
    log "── [$SLUG] Stage 5: Clustering SKIPPED ─────────────────"
  else
    log ""
    log "── [$SLUG] Stage 5: Clustering ─────────────────────────"

    CLUSTER_OUT="$DATA_PRODUCT/clusters"
    mkdir -p "$CLUSTER_OUT"

    if $SKIP_OLLAMA; then
      run "python scripts/cluster_bugs.py \
        '$DATA_PRODUCT/ecl_parsed.csv' \
        '$CLUSTER_OUT/ecl_parsed_clustered.csv'"
    else
      run "python scripts/cluster_bugs.py \
        '$DATA_PRODUCT/ecl_parsed.csv' \
        '$CLUSTER_OUT/ecl_parsed_clustered.csv' \
        --provider ollama \
        --model '$OLLAMA_MODEL' \
        --embed-model '$EMBED_MODEL'"
    fi

    log "[$SLUG] Stage 5 complete."
  fi

  # ── Stage 6: Defect Predictions ────────────────────────────────────────
  if $SKIP_PREDICT; then
    log ""
    log "── [$SLUG] Stage 6: Predictions SKIPPED ────────────────"
  else
    log ""
    log "── [$SLUG] Stage 6: Defect Predictions ─────────────────"

    PRED_STAGING="$STAGING_PRODUCT/predictions"
    mkdir -p "$PRED_STAGING"

    if $SKIP_OLLAMA; then
      run "python scripts/predict_defects.py \
        '$DATA_PRODUCT/ecl_parsed.csv' \
        '$PRED_STAGING/ecl_parsed_predictions.csv'"
    else
      run "python scripts/predict_defects.py \
        '$DATA_PRODUCT/ecl_parsed.csv' \
        '$PRED_STAGING/ecl_parsed_predictions.csv' \
        --provider ollama \
        --model '$OLLAMA_MODEL'"
    fi

    promote_dir "$PRED_STAGING" "$DATA_PRODUCT/predictions"

    log "[$SLUG] Stage 6 complete."
  fi

  log ""
  log "═══ [$SLUG] All stages complete ═════════════════════════"
}

# ── Determine Active Products ────────────────────────────────────────────────
resolve_product_schedule() {
  if [ -n "$OVERRIDE_PRODUCTS" ]; then
    # User override: parse comma-separated slugs, apply override duration or default 36
    local dur="${OVERRIDE_DURATION:-36}"
    local products=""
    IFS=',' read -ra slugs <<< "$OVERRIDE_PRODUCTS"
    for slug in "${slugs[@]}"; do
      slug="$(echo "$slug" | xargs)"  # trim whitespace
      products+="$slug:$dur "
    done
    echo "$products"
    return
  fi

  local schedule="$FORCE_SCHEDULE"
  if [ -z "$schedule" ]; then
    # Auto-detect from day of week
    local dow
    dow=$(date +%u)  # 1=Mon … 7=Sun
    if [ "$dow" -ge 6 ]; then
      schedule=weekend
    else
      schedule=weekday
    fi
  fi

  if [ "$schedule" = "weekend" ]; then
    log "📅 Full schedule (--weekend or auto-detected) → all products" >&2
    echo "$WEEKEND_PRODUCTS"
  else
    log "📅 Limited schedule (--weekday or auto-detected) → weekday products" >&2
    echo "$WEEKDAY_PRODUCTS"
  fi
}

# ── Main ──────────────────────────────────────────────────────────────────────

log "======================================================"
log "QA Daily Refresh  $(date '+%Y-%m-%d %H:%M:%S')"
log "dry_run=$DRY_RUN  skip_ollama=$SKIP_OLLAMA  skip_cluster=$SKIP_CLUSTER  skip_predict=$SKIP_PREDICT  force_schedule=$FORCE_SCHEDULE  ollama_model=$OLLAMA_MODEL  embed_model=$EMBED_MODEL"
log "======================================================"

# 1. Make sure Streamlit is up before we do anything
ensure_streamlit_running

# Activate venv for all subsequent python calls
source "$VENV"

# Start Ollama once (shared across all products)
if ! $SKIP_OLLAMA; then
  if ! pgrep -x ollama >/dev/null 2>&1; then
    log "  Starting Ollama..."
    run_silent "ollama serve &>/dev/null &"
    run_silent "sleep 5"
  else
    log "  Ollama already running."
  fi
fi

# Resolve which products to run
ACTIVE_PRODUCTS=$(resolve_product_schedule)
log "Active products: $ACTIVE_PRODUCTS"

# Run pipeline for each product
for entry in $ACTIVE_PRODUCTS; do
  SLUG="${entry%%:*}"
  DURATION="${entry##*:}"
  run_product_pipeline "$SLUG" "$DURATION"
done

# ── Restart Streamlit ────────────────────────────────────────────────────────
log ""
log "── Restarting Streamlit ────────────────────────────────"

# Collect ALL pids listening on the Streamlit port (parent + children).
STREAMLIT_PIDS=$(lsof -ti tcp:"$STREAMLIT_PORT" 2>/dev/null | sort -u || true)
if [ -n "$STREAMLIT_PIDS" ]; then
  if $DRY_RUN; then
    log "  [DRY-RUN] kill PIDs: $STREAMLIT_PIDS"
  else
    # Graceful stop first (SIGTERM)
    for _pid in $STREAMLIT_PIDS; do
      kill "$_pid" 2>/dev/null || true
    done
    # Wait up to 10s for all processes to exit
    for _i in $(seq 1 10); do
      lsof -ti tcp:"$STREAMLIT_PORT" >/dev/null 2>&1 || break
      sleep 1
    done
    # Force-kill anything still lingering (SIGKILL)
    REMAINING=$(lsof -ti tcp:"$STREAMLIT_PORT" 2>/dev/null | sort -u || true)
    if [ -n "$REMAINING" ]; then
      log "  Streamlit still running after 10s — sending SIGKILL"
      for _pid in $REMAINING; do
        kill -9 "$_pid" 2>/dev/null || true
      done
      sleep 2
    fi
    # Final verification
    if lsof -ti tcp:"$STREAMLIT_PORT" >/dev/null 2>&1; then
      log "  ERROR: port $STREAMLIT_PORT still in use — cannot restart Streamlit"
    else
      log "  Stopped Streamlit (PIDs: $(echo $STREAMLIT_PIDS | tr '\n' ' '))"
    fi
  fi
else
  log "  Streamlit was not running — nothing to kill."
fi

# Clear any on-disk Streamlit cache to avoid stale data
if [ -d "$SCRIPT_DIR/.streamlit/cache" ]; then
  rm -rf "$SCRIPT_DIR/.streamlit/cache"
  log "  Cleared .streamlit/cache"
fi

if $DRY_RUN; then
  log "  [DRY-RUN] streamlit run scripts/bug_heatmap_dashboard.py"
else
  # Only start if the port is actually free
  if lsof -ti tcp:"$STREAMLIT_PORT" >/dev/null 2>&1; then
    log "  ERROR: port $STREAMLIT_PORT still occupied — skipping Streamlit start"
  else
    nohup streamlit run "$SCRIPT_DIR/scripts/bug_heatmap_dashboard.py" \
      --server.address 0.0.0.0 \
      --server.port "$STREAMLIT_PORT" \
      --server.headless true \
      >> "$LOGS/streamlit.log" 2>&1 &
    NEW_PID=$!
    sleep 3
    # Verify the new process is actually listening
    if lsof -ti tcp:"$STREAMLIT_PORT" >/dev/null 2>&1; then
      log "  Streamlit restarted (PID $NEW_PID) on :$STREAMLIT_PORT"
    else
      log "  WARNING: Streamlit process started but not yet listening on :$STREAMLIT_PORT"
      log "  Check $LOGS/streamlit.log for errors"
    fi
  fi
fi

# ── Done ──────────────────────────────────────────────────────────────────────
log ""
log "======================================================"
log "Refresh complete  $(date '+%Y-%m-%d %H:%M:%S')"
log "Streamlit restarted fresh — no stale cache."
log "Log: $LOG_FILE"
log "======================================================"

# Prune logs older than 30 days
find "$LOGS" -name "refresh_*.log" -mtime +30 -delete 2>/dev/null || true
