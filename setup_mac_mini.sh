#!/bin/bash
# PDR-I QA Toolkit - Mac Mini M1 Setup v2.1
set -e
echo "=== PDR-I QA Toolkit Setup (Mac Mini M1) ==="

echo "\n[1/7] Checking prerequisites..."
which brew || { echo "ERROR: Homebrew not found. Install from https://brew.sh"; exit 1; }

PY_BIN=$(which python3.14 || true)
if [ -z "$PY_BIN" ]; then
    echo "ERROR: python3.14 not found. Install via: brew install python@3.14"
    exit 1
fi
echo "Using Python: $PY_BIN"


echo "\n[2/7] Installing system dependencies..."
brew install node 2>/dev/null || true
npm install -g appium 2>/dev/null || true
appium driver install xcuitest 2>/dev/null || true

echo "\\n[3/7] Setting up Python environment..."
"$PY_BIN" -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo "\n[4/7] Creating directories..."
mkdir -p data visual_baselines visual_results tests/generated

echo "\n[5/7] Installing Ollama (optional)..."
if ! which ollama > /dev/null 2>&1; then
    brew install ollama
fi
echo "To pull model: ollama pull llama3.1"
echo "  (llama3.1 for M1 16GB, phi3 for M1 8GB)"

echo "\\n[6/7] Verifying..."
python -c "import pandas, streamlit, plotly, sklearn, PIL; print('All packages OK')"

echo "\n[7/7] Creating LaunchAgent..."
PLIST=~/Library/LaunchAgents/com.pdri.dashboard.plist
TOOLKIT_DIR=$(pwd)
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
launchctl load "$PLIST" 2>/dev/null || true

echo "\n=== SETUP COMPLETE ==="
echo "Dashboard URL: http://$(hostname):8501"
echo ""
echo "Quick start:"
echo "  source .venv/bin/activate"
echo "  python scripts/parse_ecl_export.py data/ecl_export.xlsx data/ecl_parsed.csv"
echo "  python scripts/compute_risk_scores.py data/ecl_parsed.csv data/risk_register.csv"
echo "  ollama serve & python scripts/ai_risk_scorer.py data/risk_register.csv data/risk_register_scored.csv --provider ollama --model llama3.1"
echo "  python scripts/auto_tag_tests.py data/risk_register_scored.csv --generate-skeletons tests/generated/ --summary"
echo "  streamlit run scripts/bug_heatmap_dashboard.py"
