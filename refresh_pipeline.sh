#!/usr/bin/env bash
# =============================================================================
# PDR-I Daily Refresh Pipeline
# Keeps Streamlit running on port 8501 throughout the entire update process.
#
# Strategy: all scripts write to data/staging/, then files are atomically
# swapped into data/ with `mv` (rename syscall) only after each stage
# succeeds. Streamlit always reads a complete, self-consistent set of files.
#
# Usage:
#   ./refresh_pipeline.sh                    # run immediately
#   ./refresh_pipeline.sh --dry-run          # print steps, don't execute
#   ./refresh_pipeline.sh --skip-ollama      # use heuristic scorer (faster)
#   ./refresh_pipeline.sh --skip-cluster     # skip clustering (saves ~5 min)
#   ./refresh_pipeline.sh --skip-predict     # skip predictions
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
OLLAMA_MODEL="llama3.1"
STREAMLIT_PORT=8501

# ── Flags ─────────────────────────────────────────────────────────────────────
DRY_RUN=false
SKIP_OLLAMA=false
SKIP_CLUSTER=false
SKIP_PREDICT=false

for arg in "$@"; do
  case $arg in
    --dry-run)      DRY_RUN=true ;;
    --skip-ollama)  SKIP_OLLAMA=true ;;
    --skip-cluster) SKIP_CLUSTER=true ;;
    --skip-predict) SKIP_PREDICT=true ;;
  esac
done

# ── Helpers ───────────────────────────────────────────────────────────────────
mkdir -p "$STAGING" "$LOGS"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG_FILE"; }
run() {
  if $DRY_RUN; then
    log "  [DRY-RUN] $*"
  else
    log "  Running: $*"
    eval "$*" >> "$LOG_FILE" 2>&1
  fi
}

# Atomically promote a staging file to live data.
# Usage: promote staging/file.csv data/file.csv
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
  # Relaunch Streamlit in the background if it stopped (e.g. after a reboot).
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

# ── Main ──────────────────────────────────────────────────────────────────────

log "======================================================"
log "PDR-I Daily Refresh  $(date '+%Y-%m-%d %H:%M:%S')"
log "dry_run=$DRY_RUN  skip_ollama=$SKIP_OLLAMA  skip_cluster=$SKIP_CLUSTER  skip_predict=$SKIP_PREDICT"
log "======================================================"

# 1. Make sure Streamlit is up before we do anything
ensure_streamlit_running

# Activate venv for all subsequent python calls
source "$VENV"

# ── Stage 1: Fetch + Parse ────────────────────────────────────────────────────
# Output goes to staging; live data/ecl_parsed.csv is untouched until success.
log ""
log "── Stage 1: Fetch + Parse ──────────────────────────────"

run "python scripts/fetch_from_n8n.py \
  --scope auto \
  --output '$STAGING/ecl_raw.json' \
  --parsed-output '$STAGING/ecl_parsed.csv' \
  --then-parse"

promote "$STAGING/ecl_raw.json"    "$DATA/ecl_raw.json"
promote "$STAGING/ecl_parsed.csv"  "$DATA/ecl_parsed.csv"

# version_catalogue.csv is also produced by parse_ecl_export.py
# It writes to the same directory as the output CSV, so we need to move it too
[ -f "$STAGING/version_catalogue.csv" ] && \
  promote "$STAGING/version_catalogue.csv" "$DATA/version_catalogue.csv"

log "Stage 1 complete."

# ── Stage 2: Risk Register ────────────────────────────────────────────────────
log ""
log "── Stage 2: Risk Register ──────────────────────────────"

run "python scripts/compute_risk_scores.py \
  '$DATA/ecl_parsed.csv' \
  '$STAGING/risk_register_all.csv'"

promote "$STAGING/risk_register_all.csv" "$DATA/risk_register_all.csv"

# per-version files land in a versions/ subdirectory relative to the output CSV;
# since we staged to STAGING/, they will be at STAGING/risk_register_versions/
promote_dir "$STAGING/risk_register_versions" "$DATA/risk_register_versions"

log "Stage 2 complete."

# ── Stage 3: AI Risk Scoring ──────────────────────────────────────────────────
log ""
log "── Stage 3: AI Risk Scoring ────────────────────────────"

if $SKIP_OLLAMA; then
  log "  Using heuristic scorer (--skip-ollama)"
  run "python scripts/ai_risk_scorer.py \
    '$DATA/risk_register_all.csv' \
    '$STAGING/risk_register_scored_all.csv' \
    --provider heuristic"
else
  # Start Ollama if not already running
  if ! pgrep -x ollama >/dev/null 2>&1; then
    log "  Starting Ollama..."
    run "ollama serve &>/dev/null &"
    run "sleep 5"
  else
    log "  Ollama already running."
  fi

  run "python scripts/ai_risk_scorer.py \
    '$DATA/risk_register_all.csv' \
    '$STAGING/risk_register_scored_all.csv' \
    --provider ollama \
    --model '$OLLAMA_MODEL'"
fi

promote "$STAGING/risk_register_scored_all.csv" "$DATA/risk_register_scored_all.csv"
promote_dir "$STAGING/risk_register_versions"   "$DATA/risk_register_versions"

log "Stage 3 complete."

# ── Stage 4: Test Skeletons ───────────────────────────────────────────────────
log ""
log "── Stage 4: Test Skeletons + Summary ───────────────────"

run "python scripts/auto_tag_tests.py \
  '$DATA/risk_register_scored_all.csv' \
  --generate-skeletons tests/generated/ \
  --summary"

log "Stage 4 complete."

# ── Stage 5: Clustering ───────────────────────────────────────────────────────
if $SKIP_CLUSTER; then
  log ""
  log "── Stage 5: Clustering SKIPPED ─────────────────────────"
else
  log ""
  log "── Stage 5: Clustering ─────────────────────────────────"

  CLUSTER_STAGING="$STAGING/clusters"
  mkdir -p "$CLUSTER_STAGING"

  if $SKIP_OLLAMA; then
    run "python scripts/cluster_bugs.py \
      '$DATA/ecl_parsed.csv' \
      '$CLUSTER_STAGING/ecl_parsed_clustered.csv'"
  else
    run "python scripts/cluster_bugs.py \
      '$DATA/ecl_parsed.csv' \
      '$CLUSTER_STAGING/ecl_parsed_clustered.csv' \
      --provider ollama \
      --model '$OLLAMA_MODEL'"
  fi

  # cluster_bugs.py writes summary to clusters/ subdir relative to input
  # Move all cluster output files into place atomically
  promote_dir "$CLUSTER_STAGING" "$DATA/clusters"

  log "Stage 5 complete."
fi

# ── Stage 6: Defect Predictions ───────────────────────────────────────────────
if $SKIP_PREDICT; then
  log ""
  log "── Stage 6: Predictions SKIPPED ────────────────────────"
else
  log ""
  log "── Stage 6: Defect Predictions ─────────────────────────"

  PRED_STAGING="$STAGING/predictions"
  mkdir -p "$PRED_STAGING"

  if $SKIP_OLLAMA; then
    run "python scripts/predict_defects.py \
      '$DATA/ecl_parsed.csv' \
      '$PRED_STAGING/ecl_parsed_predictions.csv'"
  else
    run "python scripts/predict_defects.py \
      '$DATA/ecl_parsed.csv' \
      '$PRED_STAGING/ecl_parsed_predictions.csv' \
      --provider ollama \
      --model '$OLLAMA_MODEL'"
  fi

  promote_dir "$PRED_STAGING" "$DATA/predictions"

  log "Stage 6 complete."
fi

# ── Done ──────────────────────────────────────────────────────────────────────
log ""
log "======================================================"
log "Refresh complete  $(date '+%Y-%m-%d %H:%M:%S')"
log "Streamlit is still running on :$STREAMLIT_PORT"
log "Users will see updated data on their next page interaction."
log "Log: $LOG_FILE"
log "======================================================"

# Prune logs older than 30 days
find "$LOGS" -name "refresh_*.log" -mtime +30 -delete 2>/dev/null || true