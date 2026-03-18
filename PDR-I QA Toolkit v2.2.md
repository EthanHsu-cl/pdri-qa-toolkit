# PDR-I QA Toolkit v2.2 — Complete Step-by-Step Implementation Guide

> Mac Mini M1 · Python 3.12 · Appium · Jenkins · Streamlit  
> All 14 files tested and packaged. Updated from v2.1 based on session changes.

***

## What Changed in v2.2

| File | Change |
|------|--------|
| `parse_ecl_export.py` | +64 aliases, +222 flat overrides, priority-order `get_category()` — near-zero uncategorized |
| `bug_heatmap_dashboard.py` | **Dual-input**: accepts both `ecl_parsed.csv` AND `risk_register_scored.csv`. All tabs enriched with LLM risk scores. Tab 7 fully rebuilt as risk-based treemap/sunburst |
| `README.md` | Updated workflow and usage instructions |

***

## File Inventory

| # | File | Size | Purpose |
|---|------|------|---------|
| 1 | `scripts/parse_ecl_export.py` | 26 KB | Parse ECL Excel → enriched CSV |
| 2 | `scripts/compute_risk_scores.py` | 3.1 KB | Module metric aggregation |
| 3 | `scripts/ai_risk_scorer.py` | 6.0 KB | Impact/Detectability scoring (heuristic/Ollama/OpenAI) |
| 4 | `scripts/auto_tag_tests.py` | 4.9 KB | pytest skeleton generator |
| 5 | `scripts/bug_heatmap_dashboard.py` | 21 KB | Streamlit dashboard — 7 tabs, dual-input |
| 6 | `scripts/cluster_bugs.py` | 3.8 KB | TF-IDF + K-Means/DBSCAN clustering |
| 7 | `scripts/predict_defects.py` | 3.4 KB | Gradient Boosting defect prediction |
| 8 | `scripts/visual_regression.py` | 3.5 KB | Screenshot diff engine |
| 9 | `Jenkinsfile` | 2.2 KB | Q4→Q3→Q2→Q1 CI/CD pipeline |
| 10 | `tests/conftest.py` | 1.9 KB | Appium + visual regression fixtures |
| 11 | `pytest.ini` | 0.2 KB | Marker registration |
| 12 | `requirements.txt` | 0.2 KB | Python dependencies |
| 13 | `tests/test_ai_storytelling.py` | 1.5 KB | Example Q4 test |
| 14 | `setup_mac_mini.sh` | 2.9 KB | One-command M1 setup + LaunchAgent |

***

## Project Directory Structure

```
pdri-qa-toolkit-v2.2/
├── setup_mac_mini.sh
├── requirements.txt
├── pytest.ini
├── Jenkinsfile
├── README.md
├── data/
│   ├── ecl_export.xlsx          ← you provide this (ECL export)
│   ├── ecl_parsed.csv           ← Step 2.2 output
│   ├── risk_register.csv        ← Step 3.1 output
│   ├── risk_register_scored.csv ← Step 3.2 output  ← Dashboard input
│   ├── ecl_clustered.csv        ← Step 5.1 output
│   ├── predictions.csv          ← Step 5.2 output
│   └── quadrant_summary.md      ← Step 3.3 output
├── scripts/
│   ├── parse_ecl_export.py
│   ├── compute_risk_scores.py
│   ├── ai_risk_scorer.py
│   ├── auto_tag_tests.py
│   ├── bug_heatmap_dashboard.py
│   ├── cluster_bugs.py
│   ├── predict_defects.py
│   └── visual_regression.py
├── tests/
│   ├── conftest.py
│   ├── test_ai_storytelling.py
│   └── generated/               ← auto-created by Step 3.3
├── visual_baselines/
└── visual_results/
```

***

## Phase 1 — Mac Mini M1 Environment Setup (Day 1)

### Step 1.1 — Download and Extract

```bash
cd ~/Desktop
unzip pdri-qa-toolkit-v2.2.zip
mv pdri-qa-toolkit-v2.2-clean pdri-qa-toolkit
cd pdri-qa-toolkit
```

### Step 1.2 — Run Automated Setup

```bash
chmod +x setup_mac_mini.sh
./setup_mac_mini.sh
```

This script:
1. Checks prerequisites (python3, Homebrew)
2. Installs Node.js, Appium, XCUITest driver
3. Creates Python virtual environment and installs all packages
4. Creates directory structure (`data/`, `visual_baselines/`, `visual_results/`, `tests/generated/`)
5. Installs Ollama (optional — skip with Ctrl+C)
6. Verifies all Python packages import correctly
7. Creates a macOS LaunchAgent so Streamlit auto-starts on login

### Step 1.3 — Mac Mini M1 RAM Considerations

| Mode | RAM Used | Notes |
|------|----------|-------|
| Dashboard + pipeline (no Ollama) | ~2.2 GB | Fine on 8 GB |
| + Ollama phi3 | ~4.5 GB | Recommended for 8 GB M1 |
| + Ollama qwen3:7b | ~7 GB | Tight on 8 GB; use heuristic if Appium also running |
| + Appium running | +1 GB | Use `--provider heuristic` on 8 GB |

### Step 1.4 — Pull Ollama Model (Optional)

```bash
# 8 GB Mac Mini — use phi3
ollama pull phi3

# 16 GB Mac Mini — use qwen3:7b
ollama pull qwen3:7b
```

### Step 1.5 — Verify Installation

```bash
source .venv/bin/activate
python3 -c "import pandas, streamlit, plotly, sklearn, PIL; print('All packages OK')"
appium --version
```

Expected output: `All packages OK` and Appium version number.

***

## Phase 2 — ECL Data Export & Parsing (Days 2–3)

### Step 2.1 — Export Bug Data from ECL

1. Log into ECL
2. Navigate to bug export
3. Select project: **PDR-I**
4. Select these columns:

**MUST (9 columns):**
`Short Description`, `Severity`, `Status`, `Creator`, `Build#`, `Create Date`, `Version`, `BugBelong`, `Reproduce Probability`

**SHOULD (6 columns):**
`Priority`, `Closed Date`, `Close Build#`, `Handler`, `Issue ID`, `Latest UpdateTime`

**NICE (3 columns):**
`Effort(man-day)`, `Module Owner`, `Comment`

5. Export as **Excel (.xlsx)**
6. Save to `data/ecl_export.xlsx`

### Step 2.2 — Parse the Export

```bash
source .venv/bin/activate
python scripts/parse_ecl_export.py data/ecl_export.xlsx data/ecl_parsed.csv
```

What it does:
- Parses every Short Description in format `PDR-I 16.2.5 - [EDF][UX] AI Storytelling: subtitle misplaced`
- Extracts: product code, version, tags, module name, description
- Normalizes 64 known typo aliases (`HQ Auido Denoise` → `HQ Audio Denoise`, `Boay Effects` → `Body Effects`, etc.)
- Maps every module to one of 20 top-level categories using 222 flat overrides + partial matching
- Creates boolean tag columns: `tag_edf`, `tag_ux`, `tag_side_effect`, `tag_at_found`, `tag_mui`, etc.
- Parses severity to numeric (1–4) and weighted (10/5/2/1)
- Parses reproduce probability to float (0.0–1.0)
- Computes `days_to_close` and `builds_to_fix`

**Expected output:**
```
Loaded 10437 bugs
Parsing Short Descriptions...

PARSING SUMMARY
Total bugs:            10437
Successfully parsed:   10201 (97.7%)
Unique modules:        89
Unique categories:     20

Top 10 modules:
  AI Storytelling                  1247
  Export                            893
  ...

✅ No uncategorized modules!
```

### Step 2.3 — Review Parser Output

```bash
python3 -c "
import pandas as pd
df = pd.read_csv('data/ecl_parsed.csv')
print(f'Total rows: {len(df)}')
print(f'Module parse rate: {df.parsed_module.notna().mean()*100:.1f}%')
print('\nTop 10 modules:')
print(df.parsed_module.value_counts().head(10))
print('\nTag columns:', [c for c in df.columns if c.startswith('tag_')])
print('\nSeverity distribution:')
print(df.severity_num.value_counts().sort_index())
"
```

**Action items if parse rate < 90%:**
- Check if Short Descriptions follow `PRODUCT VERSION - [TAGS] MODULE: description` format
- Add new typo variants to `MODULE_ALIASES` in `parse_ecl_export.py`
- Add any listed `UNCATEGORIZED MODULES` to `MODULE_CATEGORIES`

***

## Phase 3 — Risk Scoring & Quadrant Assignment (Days 3–4)

### Step 3.1 — Generate Risk Register

```bash
python scripts/compute_risk_scores.py data/ecl_parsed.csv data/risk_register.csv
```

Per-module metrics computed:
- Total bugs, severity-weighted total (Critical×10 + Major×5 + Normal×2 + Minor×1)
- Critical/Major/Normal/Minor counts
- Side Effect (regression) rate, AT Found (automation catch) rate
- Average repro rate, days-to-close, builds-to-fix
- Unique reporter count, open/closed/postponed counts
- Auto-calculated probability score (1–5 via percentile ranking)

**Expected output:** Top 10 riskiest modules, automation blind spots, regression hotspots.

### Step 3.2 — Score Impact & Detectability with Local LLM

Three provider options:

**Option A — Heuristic (instant, no dependencies, recommended first run):**
```bash
python scripts/ai_risk_scorer.py data/risk_register.csv data/risk_register_scored.csv --provider heuristic
```

**Option B — Ollama (local LLM, free, ~2–5 minutes for 90 modules):**
```bash
ollama serve &
python scripts/ai_risk_scorer.py data/risk_register.csv data/risk_register_scored.csv \
  --provider ollama --model qwen3:7b
# 8 GB Mac Mini:
python scripts/ai_risk_scorer.py data/risk_register.csv data/risk_register_scored.csv \
  --provider ollama --model phi3
```

**Option C — OpenAI API (best reasoning, ~1 min, ~$0.05):**
```bash
export OPENAI_API_KEY=sk-...
python scripts/ai_risk_scorer.py data/risk_register.csv data/risk_register_scored.csv \
  --provider openai
```

**Output per module (all three modes):**
- `impact_score_ai` (1–5)
- `detectability_score_ai` (1–5)
- `risk_score_final` = Impact × Probability × Detectability
- `quadrant`: Q4 (≥60), Q3 (30–59), Q2 (10–29), Q1 (<10)

**Expected output:**
```
RISK SCORING RESULTS
Q4 - Test First (12 modules):
  AI Storytelling   Score:100  I:5 P:5 D:4
  Export            Score: 75  I:5 P:5 D:3
  Auto Edit         Score: 60  I:5 P:4 D:3
Q3 - Test Second (23 modules):
  Auto Captions     Score: 48  I:4 P:4 D:3
  ...
```

### Step 3.3 — Generate Test Skeletons & Management Summary

```bash
python scripts/auto_tag_tests.py data/risk_register_scored.csv \
  --generate-skeletons tests/generated/ --summary
```

Creates:
- One `test_<module>.py` per module with correct `@pytest.mark.q4` (or q3/q2/q1) markers
- Allure `suite`/`sub-suite`/`tag` decorators
- Severity matched to quadrant (Q4=CRITICAL, Q1=TRIVIAL)
- Three skeleton test methods per file
- `data/quadrant_summary.md` — formatted table ready for management

### Step 3.4 — Optional: Team Refinement Workshop

1. Upload `data/risk_register_scored.csv` to Google Sheets
2. Share with team
3. In a 2-hour meeting: review AI-assigned `impact_score_ai` and `detectability_score_ai`
4. Override scores the team disagrees with (AI is a starting point, not final)
5. Re-export corrected CSV, then re-run:

```bash
python scripts/ai_risk_scorer.py data/risk_register_corrected.csv data/risk_register_final.csv --provider heuristic
python scripts/auto_tag_tests.py data/risk_register_final.csv --tag-existing tests/ --summary
```

***

## Phase 4 — Dashboard Setup (Days 5–8)

### Step 4.1 — Start Streamlit

```bash
source .venv/bin/activate
streamlit run scripts/bug_heatmap_dashboard.py \
  --server.address 0.0.0.0 \
  --server.port 8501 \
  --server.headless true
```

Access from any team device at: `http://<mac-mini-ip>:8501`

```bash
# Find Mac Mini IP:
ipconfig getifaddr en0
```

### Step 4.2 — Upload Data to Dashboard

The dashboard uses a **dual-input system**:

**Step 1 (required) — Bug data:**  
Upload `data/ecl_parsed.csv` → unlocks tabs 1–6

**Step 2 (optional, recommended) — Risk scores:**  
Upload `data/risk_register_scored.csv` → enriches all tabs with LLM scores, unlocks Tab 7

The dashboard validates both files and shows a clear error if the wrong file is uploaded.

### Step 4.3 — Dashboard Tabs

| Tab | Name | Data Source | What It Shows |
|-----|------|-------------|---------------|
| 1 | Module × Severity | Bug-level | Heatmap of bug density by module/category and severity (1–4). Annotated with `[Q4]`/`[Q3]` badges when risk data is loaded |
| 2 | Version Timeline | Bug-level | Severity-weighted bugs per module across versions — reveals regressions |
| 3 | Tag Analysis | Bug-level | Tag distribution heatmap. Regression hotspots (Side Effect), AT blind spots |
| 4 | Priority vs Severity | Bug-level | QA severity vs RD priority mismatch matrix |
| 5 | Team Coverage | Bug-level | Tester × Category matrix — knowledge silo detection |
| 6 | KPI Dashboard | Bug-level + Risk | Total bugs, critical count, weekly trend, severity pie. Q4 count + avg risk score when risk data loaded |
| 7 | Risk Heatmap | **Risk-scored** | Interactive treemap/sunburst. Requires `risk_register_scored.csv`. See below |

### Step 4.4 — Risk Heatmap Tab (Tab 7) — Detail

**Color options (radio button):**
- **Risk Score (LLM)** — Color = `risk_score_final` (I×P×D). Scale: yellow (low) → red (high). This is the primary view.
- **Quadrant** — Color by Q4 (red) / Q3 (orange) / Q2 (yellow) / Q1 (green)
- **Critical Count** — Color = number of severity-1 bugs
- **Severity Weight** — Color = weighted sum (same as v2.1 bug-count mode)

**Rectangle size** = bug count from `ecl_parsed.csv` (bug-level data joined with risk scores)

**Hierarchy**: Category → Module (click to drill down)

**Sunburst view** available in expandable section below treemap.

**Category Summary table** shows: Modules, Total Bugs, Severity Weight, Avg Risk Score, Q4 Module count per category.

**Q4 bar chart** below treemap ranks Q4 modules by risk score descending.

**Risk vs Probability scatter** appears when `probability_score_auto` is available in risk data.

### Step 4.5 — Enable Auto-Start on Login

```bash
launchctl load ~/Library/LaunchAgents/com.pdri.qa-dashboard.plist
```

To disable: `launchctl unload ~/Library/LaunchAgents/com.pdri.qa-dashboard.plist`

Logs: `~/pdri-qa-toolkit/dashboard.log`

***

## Phase 5 — Weekly Data Refresh

Every Monday morning (or after each ECL export):

```bash
cd ~/pdri-qa-toolkit
source .venv/bin/activate

# 1. Export fresh data from ECL → save as data/ecl_export.xlsx

# 2. Re-parse
python scripts/parse_ecl_export.py data/ecl_export.xlsx data/ecl_parsed.csv

# 3. Re-score (only needed if quadrant assignments need updating)
python scripts/compute_risk_scores.py data/ecl_parsed.csv data/risk_register.csv
python scripts/ai_risk_scorer.py data/risk_register.csv data/risk_register_scored.csv \
  --provider heuristic

# 4. Refresh dashboard — click Streamlit's "Rerun" button or reload browser
```

***

## Phase 6 — NLP Clustering & Prediction (Optional)

### Step 6.1 — Cluster Similar Bugs

```bash
python scripts/cluster_bugs.py data/ecl_parsed.csv data/ecl_clustered.csv
```

Uses TF-IDF + K-Means globally (25 clusters) and DBSCAN per-module (top 5 modules). Falls back from bigrams to unigrams if vocabulary too sparse. Outputs `ecl_clustered.csv` and `data/cluster_summary.csv`.

### Step 6.2 — Predict Next Build's Defects

```bash
python scripts/predict_defects.py data/ecl_parsed.csv data/predictions.csv
```

Requires numeric build numbers in `Build#` column. Needs ≥20 build-module data points. Uses lag features (bugs in last 1/3/5 builds, severity trends) in a Gradient Boosting model.

***

## Phase 7 — Visual Regression Testing

### Step 7.1 — Configure Appium Connection

```bash
export APPIUM_URL=http://127.0.0.1:4723
export BUNDLE_ID=com.cyberlink.powerdirector
export DEVICE_NAME="iPhone 15"
export VISUAL_THRESHOLD=0.95
```

Or edit `tests/conftest.py` directly.

### Step 7.2 — Create Baseline Screenshots

```bash
source .venv/bin/activate
python -m pytest tests/ -m visual -v
```

All tests pass on first run (auto-saving baselines). Subsequent runs compare against saved baselines.

### Step 7.3 — Review Visual Diffs

Failed visual tests produce diff images in `visual_results/` with red overlays on changed pixels. Adjust threshold via `VISUAL_THRESHOLD` env var if too sensitive (try `0.92` for minor rendering changes).

***

## Phase 8 — CI/CD Pipeline

### Step 8.1 — Configure Jenkins

1. Install Allure plugin in Jenkins
2. Create a new **Pipeline** job
3. Point SCM to the toolkit repository
4. Jenkins auto-detects `Jenkinsfile`

### Step 8.2 — Pipeline Stages

The Jenkinsfile runs tests in quadrant priority order:

```
Setup → Q4 (Critical) → Q3 (Important) → Q2 (Standard) → Q1 (Low Risk) → Visual Regression
```

Use the `TEST_SCOPE` parameter to control which stages run:
- `q4` — smoke tests only (fastest, ~5 min)
- `q4,q3` — critical + important (~15 min)
- `q4,q3,q2,q1` — full suite
- `visual` — visual regression only

### Step 8.3 — Allure Reporting

Results are published to Allure automatically. Each test shows:
- **Suite**: Module Category (e.g., `AI Features`)
- **Sub-suite**: Module name (e.g., `AI Storytelling`)
- **Tag**: Quadrant (`Q4`, `Q3`, `Q2`, `Q1`)

***

## Phase 9 — Ongoing Maintenance

### Weekly Cadence

| Day | Task | Time |
|-----|------|------|
| Monday | Export fresh ECL data → parse + risk score | 30 min |
| Monday | Review dashboard for new hotspots | 15 min |
| Wednesday | Review Allure report, update failing tests | 1 hr |
| Friday | Run clustering to find new bug patterns | 15 min |

### Monthly Cadence

- Re-run `ai_risk_scorer.py` with latest data (quadrant assignments may shift)
- Run `auto_tag_tests.py --tag-existing tests/` to update markers on existing tests
- Review `data/quadrant_summary.md` with team — validate Q4/Q3 modules still correct

### When New Modules Are Added

1. Add to `MODULE_CATEGORIES` in `parse_ecl_export.py`
2. Add `IMPACT_OVERRIDES` entry in `ai_risk_scorer.py` if needed
3. Re-run the full pipeline (Steps 2.2 → 3.2 → 3.3)

***

## Quick Reference — Full Demo (5 Commands)

```bash
source .venv/bin/activate
python scripts/parse_ecl_export.py data/ecl_export.xlsx data/ecl_parsed.csv
python scripts/compute_risk_scores.py data/ecl_parsed.csv data/risk_register.csv
ollama serve & python scripts/ai_risk_scorer.py data/risk_register.csv data/risk_register_scored.csv --provider ollama --model llama3.1
python scripts/auto_tag_tests.py data/risk_register_scored.csv --generate-skeletons tests/generated/ --summary
streamlit run scripts/bug_heatmap_dashboard.py --server.address 0.0.0.0 --server.port 8501
# Upload ecl_parsed.csv (Step 1) then risk_register_scored.csv (Step 2) in sidebar
```

***

## Troubleshooting

| Issue | Cause | Fix |
|-------|-------|-----|
| Dashboard: "Wrong file!" on ecl_parsed.csv | File is risk_register_scored.csv (lacks `severity_num`) | Use `ecl_parsed.csv` from Step 2.2 |
| Dashboard: Tab 7 shows warning, not treemap | Risk data not loaded in sidebar Step 2 | Upload `risk_register_scored.csv` in sidebar |
| Parse rate below 80% | Non-standard Short Description format | Check format compliance; add aliases |
| UNCATEGORIZED MODULES in parse output | New modules not in `MODULE_CATEGORIES` | Add them to `_FLAT_OVERRIDES` dict and re-run |
| Ollama timeout errors | Model too large for available RAM | Switch to `phi3` or use `--provider heuristic` |
| `cluster_bugs.py` not enough terms | Module has very few unique bug descriptions | Expected — script handles gracefully |
| `predict_defects.py` not enough data | Fewer than 20 build-module data points | Need more build history; skip prediction for now |
| Visual regression too sensitive | Minor font rendering differences | Raise threshold: `export VISUAL_THRESHOLD=0.92` |
| Appium can't find device | `DEVICE_NAME` mismatch | Run `idevice_id -l` and use exact device UUID |