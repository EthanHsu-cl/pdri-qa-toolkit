#!/bin/bash
# PDR-I QA Toolkit - Mac Mini M1 Setup v2.2
set -e
echo "=== PDR-I QA Toolkit Setup (Mac Mini M1) ==="

TOOLKIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$TOOLKIT_DIR"

# ── [1/8] Prerequisites ───────────────────────────────────────────────────────
echo ""
echo "[1/8] Checking prerequisites..."
which brew || { echo "ERROR: Homebrew not found. Install from https://brew.sh"; exit 1; }

PY_BIN=$(which python3.14 2>/dev/null || true)
if [ -z "$PY_BIN" ]; then
    echo "ERROR: python3.14 not found. Install via: brew install python@3.14"
    exit 1
fi
echo "  Using Python: $PY_BIN"

# ── [2/8] System dependencies ─────────────────────────────────────────────────
echo ""
echo "[2/8] Installing system dependencies..."
brew install node 2>/dev/null || true
npm install -g appium 2>/dev/null || true
appium driver install xcuitest 2>/dev/null || true

# ── [3/8] Python environment ──────────────────────────────────────────────────
echo ""
echo "[3/8] Setting up Python environment..."
"$PY_BIN" -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# ── [4/8] Directory structure ─────────────────────────────────────────────────
echo ""
echo "[4/8] Creating directories..."
mkdir -p \
    data/staging \
    data/products \
    logs \
    visual_baselines \
    visual_results \
    tests/generated
echo "  Directories ready."

# ── [5/8] Ollama + models ─────────────────────────────────────────────────────
echo ""
echo "[5/8] Installing Ollama and pulling models..."
if ! which ollama > /dev/null 2>&1; then
    brew install ollama
fi

# Start Ollama server if not already running (needed for pulls)
OLLAMA_STARTED=false
if ! pgrep -x ollama > /dev/null 2>&1; then
    echo "  Starting Ollama server for model pulls..."
    ollama serve &>/dev/null &
    sleep 5
    OLLAMA_STARTED=true
fi

echo "  Pulling gemma4:e2b-it-q4_K_M  (primary LLM — AI scoring, clustering, predictions)..."
ollama pull gemma4:e2b-it-q4_K_M

echo "  Pulling llama3.1               (fallback LLM)..."
ollama pull llama3.1

echo "  Pulling nomic-embed-text       (embedding model — bug clustering)..."
ollama pull nomic-embed-text

if $OLLAMA_STARTED; then
    echo "  Stopping temporary Ollama server (LaunchAgent will manage it in production)..."
    pkill -x ollama 2>/dev/null || true
fi

# ── [6/8] Verify Python packages ─────────────────────────────────────────────
echo ""
echo "[6/8] Verifying Python packages..."
python -c "import pandas, streamlit, plotly, sklearn, PIL, rapidfuzz, tqdm; print('  All packages OK')"

# ── [7/8] Streamlit LaunchAgent ───────────────────────────────────────────────
echo ""
echo "[7/8] Creating LaunchAgent for Streamlit dashboard..."
PLIST=~/Library/LaunchAgents/com.pdri.dashboard.plist
cat > "$PLIST" << EOFPLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.pdri.dashboard</string>
    <key>ProgramArguments</key>
    <array>
        <string>${TOOLKIT_DIR}/.venv/bin/python3</string>
        <string>-m</string>
        <string>streamlit</string>
        <string>run</string>
        <string>${TOOLKIT_DIR}/scripts/bug_heatmap_dashboard.py</string>
        <string>--server.address</string>
        <string>0.0.0.0</string>
        <string>--server.port</string>
        <string>8501</string>
        <string>--server.headless</string>
        <string>true</string>
    </array>
    <key>WorkingDirectory</key><string>${TOOLKIT_DIR}</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>${TOOLKIT_DIR}/data/dashboard.log</string>
    <key>StandardErrorPath</key><string>${TOOLKIT_DIR}/data/dashboard_error.log</string>
</dict>
</plist>
EOFPLIST

if launchctl list com.pdri.dashboard &>/dev/null; then
    launchctl unload "$PLIST" 2>/dev/null || true
fi
launchctl load "$PLIST"
echo "  Dashboard LaunchAgent loaded (starts at login, auto-restarts on crash)."

# ── [8/8] Daily refresh cron job ─────────────────────────────────────────────
echo ""
echo "[8/8] Setting up daily refresh cron job (03:00 daily)..."
CRON_LINE="0 3 * * * ${TOOLKIT_DIR}/refresh_pipeline.sh >> ${TOOLKIT_DIR}/logs/cron.log 2>&1"
if crontab -l 2>/dev/null | grep -qF "refresh_pipeline.sh"; then
    echo "  Cron job already present — skipped."
else
    (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
    echo "  Cron job added: runs daily at 03:00."
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "=== SETUP COMPLETE ==="
echo "  Dashboard:  http://$(hostname):8501"
echo "  Pipeline:   ./refresh_pipeline.sh [--dry-run | --skip-ollama | --weekend]"
echo "  Cron log:   ${TOOLKIT_DIR}/logs/cron.log"
echo ""
echo "  Default models:"
echo "    LLM:    gemma4:e2b-it-q4_K_M"
echo "    Embeds: nomic-embed-text"
echo ""
echo "  To run a full refresh now:"
echo "    ./refresh_pipeline.sh --weekend"
