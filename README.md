# PDR-I QA Toolkit v2.5 — Complete Step-by-Step Implementation Guide

> Mac Mini M1 · Python 3.14 · Appium · Jenkins · Streamlit  
> Updated from v2.4 based on session changes.

***

## What Changed in v2.5

| File | Change |
|------|--------|
| `parse_ecl_export.py` | **v2.4** — JSON input support for n8n webhook integration. Accepts `.json` files (list of bug objects) produced by `fetch_from_n8n.py` in addition to `.xlsx` / `.csv`. Handles both plain list and n8n's `{"json": {...}}` wrapped shape. Applies `_N8N_COL_MAP` remapping table so the webhook's output field names (`Short Description`, `Create Date`, `Closed Date`, `Build#`, `Close Build#`) map explicitly to internal parser names — all downstream pipeline code unchanged. `Reproduce Probability` is absent from the API; `repro_rate` defaults to `0.5` for all JSON-sourced rows (same as the existing no-repro-column fallback). |
| `fetch_from_n8n.py` | **New script.** POSTs to the n8n webhook, normalises the n8n item-wrapper shape, audits that all required fields are present (warns on optional missing fields), saves the flat list to `data/ecl_raw.json`. Run with `--then-parse` to chain directly into `parse_ecl_export.py` in one command. |
| `Dashboard_Query_eBug_List_v3.json` | **Updated n8n workflow.** Renames the `Get Columns_v2` Set node to `Get Columns_v3` with corrected field mappings using the real API field names confirmed from a live sample response: `$json.Build` → `Build#`, `$json.CloseToBuild` → `Close Build#`, `$json.ShortDescription` → `Short Description`, `$json.CreateTime` → `Create Date`, `$json.CloseTime` → `Closed Date`. Adds `Creator` and `Handler` pass-through fields. Removes the fragile `TemplateName.split(ProductName)` expression for `ProjectName` (field unused downstream). |

---

## What Changed in v2.4


| File | Change |
|------|--------|
| `bug_heatmap_dashboard.py` | **v2.14** — Fixed treemap click detection for modules whose name matches their category name (e.g. the "Shortcut" module inside the "Shortcut" category was silently ignored). Root cause: the old guard `clicked_label not in all_categories` evaluated to `False` when label and category are identical. Fix: replaced name-based check with Plotly's `parent` field — category-root nodes have an empty `parent`, module-leaf nodes always have a non-empty `parent`, so the two are unambiguously distinguished. Also reverted responsive layout back to a fixed left/right `[6, 4]` column split; removed the JS width-detection snippet (`components.html` import); removed `wide_layout` / vertical layout logic and the `render_detail_panel` helper. v2.12: dynamic treemap height (min 700 px, max 1100 px, scales with module count); scrollable right panel div capped at treemap height. |

---

## What Changed in v2.3

| File | Change |
|------|--------|
| `parse_ecl_export.py` | **Automatic sub-variant normalisation** in `normalize_module`: strips trailing parentheticals `Module(Sub)` → `Module`, comma-splits `A, B, C` → `A`, handles `A>B` → `A`. Only 4 hardcoded aliases remain (leading/trailing punctuation, pure acronyms). Added `parsed_module_raw` column (Option B) preserving original ECL string. O(1) alias lookup via lowercase shadow dict. `VersionMappingStore.lookup()` now caches confirmed files to avoid repeated disk scans across 10k+ rows. |
| `compute_risk_scores.py` | Per-version files now stored in `data/risk_register_versions/` subfolder. Single `groupby` for all tag columns (was one per tag). Single pivot for status counts (was 3 separate groupbys). `low_memory=False` on CSV read. |
| `ai_risk_scorer.py` | Resume/checkpoint support — skips already-scored modules on restart, saves to disk every 10 modules. Ollama retry logic (2 retries + 2s delay before heuristic fallback). Expanded `IMPACT_OVERRIDES` (all lowercase keys, more modules). Per-version scored files stored in `data/risk_register_versions/`. Scoring method summary + Q4/Q3/Q2/Q1 results printed after each file. |
| `bug_heatmap_dashboard.py` | **Version-aware risk loading**: single version selected → loads per-version scored file; multiple/all → loads combined `_all` file. Version context banner in Tab 7. `normalise_module()` safety net in dashboard. All `use_container_width` → `width='stretch'`. `low_memory=False` on all CSV reads. `BugCode` column is now a clickable link (regex `display_text`). Dynamic treemap height. Scrollable right panel. Comprehensive `📖` help expanders on all tabs explaining the full I×P×D pipeline. |
| `predict_defects.py` | Logs number of rows dropped due to non-numeric `Build#` values before the `<20 samples` check. |

***

## File Inventory

| # | File | Version | Purpose |
|---|------|---------|---------|
| 1 | `scripts/parse_ecl_export.py` | v2.4 | Parse ECL Excel / CSV / n8n JSON → enriched CSV |
| 2 | `scripts/compute_risk_scores.py` | v2.3 | Module metric aggregation → risk register |
| 3 | `scripts/ai_risk_scorer.py` | v2.3 | Impact/Detectability scoring (heuristic/Ollama/OpenAI) |
| 4 | `scripts/auto_tag_tests.py` | v2.1 | pytest skeleton generator |
| 5 | `scripts/bug_heatmap_dashboard.py` | v2.14 | Streamlit dashboard — 7 tabs, dual-input |
| 6 | `scripts/cluster_bugs.py` | v2.2 | TF-IDF + K-Means/DBSCAN clustering |
| 7 | `scripts/predict_defects.py` | v2.1 | Gradient Boosting defect prediction |
| 8 | `scripts/visual_regression.py` | v2.1 | Screenshot diff engine |
| 9 | `Jenkinsfile` | — | Q4→Q3→Q2→Q1 CI/CD pipeline |
| 10 | `tests/conftest.py` | — | Appium + visual regression fixtures |
| 11 | `pytest.ini` | — | Marker registration |
| 12 | `requirements.txt` | — | Python dependencies |
| 13 | `tests/test_ai_storytelling.py` | — | Example Q4 test |
| 14 | `setup_mac_mini.sh` | — | One-command M1 setup + LaunchAgent |
| 15 | `scripts/fetch_from_n8n.py` | v1.0 | Fetch bugs from n8n webhook → `data/ecl_raw.json` |
| 16 | `n8n/Dashboard_Query_eBug_List_v3.json` | v3 | n8n workflow — queries eCL eBug API and returns normalised bug records |

***

## Project Directory Structure

```
pdri-qa-toolkit/
├── setup_mac_mini.sh
├── requirements.txt
├── pytest.ini
├── Jenkinsfile
├── README.md
├── data/
│   ├── ecl_export.xlsx                    ← you provide this (ECL export, optional)
│   ├── ecl_raw.json                       ← Step 2.1b output (from n8n webhook)
│   ├── ecl_parsed.csv                     ← Step 2.2 output
│   ├── risk_register_all.csv              ← Step 3.1 output (combined)
│   ├── risk_register_scored_all.csv       ← Step 3.2 output ← Dashboard Step 2 input
│   ├── risk_register_versions/            ← per-version files (auto-created)
│   │   ├── risk_register_16.4.0.csv
│   │   ├── risk_register_scored_16.4.0.csv
│   │   └── ...
│   ├── module_mappings/                   ← fuzzy match store (auto-created)
│   │   ├── permanent/mappings_global.json ← promoted confirmed mappings
│   │   └── versions/                      ← per-version pending/confirmed files
│   ├── ecl_clustered.csv                  ← Step 6.1 output
│   ├── predictions.csv                    ← Step 6.2 output
│   └── quadrant_summary.md               ← Step 3.3 output
├── scripts/
│   ├── fetch_from_n8n.py
│   ├── parse_ecl_export.py
│   ├── compute_risk_scores.py
│   ├── ai_risk_scorer.py
│   ├── auto_tag_tests.py
│   ├── bug_heatmap_dashboard.py
│   ├── cluster_bugs.py
│   ├── predict_defects.py
│   └── visual_regression.py
├── n8n/
│   └── Dashboard_Query_eBug_List_v3.json  ← import into n8n to enable webhook
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
unzip pdri-qa-toolkit.zip
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

There are now **two ways** to get bug data into the pipeline. Use whichever fits your workflow.

---

### Path A — n8n Webhook (recommended for regular refreshes)

#### Step 2.1a — Import the n8n Workflow

1. Open your n8n instance
2. Import `n8n/Dashboard_Query_eBug_List_v3.json`
3. Activate the workflow — the webhook endpoint is now live

#### Step 2.1b — Fetch Bugs via Webhook

```bash
source .venv/bin/activate
python scripts/fetch_from_n8n.py   --webhook-url https://your-n8n-host/webhook/82746bb5-e140-4720-98a3-d1965900274d   --output data/ecl_raw.json
```

Or fetch and parse in one command:

```bash
python scripts/fetch_from_n8n.py   --webhook-url https://your-n8n-host/webhook/82746bb5-e140-4720-98a3-d1965900274d   --then-parse
```

**What fields the API provides:**

| Field delivered | Maps to parser column | Notes |
|---|---|---|
| `ShortDescription` | `Short Description` | Module, tags, version all parsed from this |
| `EbugSeverity` (int) | `Severity` | Integer `1`–`4` |
| `EbugPriority` (int) | `Priority` | Integer `1`–`5` |
| `Status` | `Status` | Exact match |
| `CreateTime` | `Create Date` | ISO datetime |
| `CloseTime` | `Closed Date` | ISO datetime, nullable |
| `Version` | `Version` | e.g. `16.3.5` |
| `Build` | `Build#` | Numeric build ID |
| `CloseToBuild` | `Close Build#` | Nullable |
| `Creator` | `Creator` | Username string |
| `BugCode` | `BugCode` | Used for dashboard ECL links |
| `BugBelong` | `BugBelong` | RD / QA attribution |
| `Handler` | `Handler` | Current assignee |
| `Reproduce Probability` | — | **Not in API** — `repro_rate` defaults to `0.5` |

Then proceed to **Step 2.2**.

---

### Path B — Manual ECL Excel Export (original method)

#### Step 2.1b — Export Bug Data from ECL

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

---

### Step 2.2 — Parse the Data

**From n8n JSON (Path A):**
```bash
source .venv/bin/activate
python scripts/parse_ecl_export.py data/ecl_raw.json data/ecl_parsed.csv
```

**From Excel export (Path B):**
```bash
source .venv/bin/activate
python scripts/parse_ecl_export.py data/ecl_export.xlsx data/ecl_parsed.csv
```

The parser now accepts `.json`, `.xlsx`, and `.csv` inputs transparently — all produce the same `ecl_parsed.csv` output format.

What it does:
- Parses every Short Description in format `PDR-I 16.2.5 - [EDF][UX] AI Storytelling: subtitle misplaced`
- Extracts: product code, version, tags, module name, description
- **Automatic sub-variant normalisation** (new in v2.3):
  - Strips trailing parentheticals: `Auto Edit(Pet 02)` → `Auto Edit`, `Text(Neon_01)` → `Text`
  - Comma-splits compound names: `Text, Title, MGT` → `Text`, `Launcher, Shortcut` → `Launcher`
  - Handles `>` notation: `Menu>Sign in` → `Menu`
- Applies typo aliases (e.g. `HQ Auido Denoise` → `HQ Audio Denoise`)
- Falls back to fuzzy matching (rapidfuzz, threshold 85%) for unrecognised strings
- Low-confidence matches go to `data/module_mappings/versions/<ver>_pending.json` for your review
- Confirmed matches are saved to `data/module_mappings/versions/<ver>_confirmed.json` and promoted to `permanent/mappings_global.json`
- Maps every module to one of 20 top-level categories
- Creates boolean tag columns: `tag_edf`, `tag_ux`, `tag_side_effect`, `tag_at_found`, `tag_mui`, etc.
- Saves both `parsed_module` (normalised) and `parsed_module_raw` (original ECL string) for traceability
- Parses severity to numeric (1–4) and weighted (S1×10 / S2×5 / S3×2 / S4×1)
- Parses reproduce probability to float (0.0–1.0)
- Computes `days_to_close` and `builds_to_fix`

**Expected output:**
```
Loaded 9992 bugs
Parsing Short Descriptions...

PARSING SUMMARY
Total bugs:            9992
Successfully parsed:   9654 (96.6%)
Unique modules:        ~90–150 (depends on data)
Fuzzy threshold used:  85%

Top 10 modules:
  Auto Edit                     991
  Shortcut                      853
  ...

PENDING review for version 16.4.0 (N entries):
  'Some Module(Sub)' → suggested: 'Some Module'
  ...

✅ No uncategorized modules!  (or small number needing confirmation)
```

### Step 2.3 — Review and Confirm Pending Mappings

After parsing, check `data/module_mappings/versions/` for `*_pending.json` files. These contain strings the fuzzy matcher found a near-match for (score 65–84%) but didn't auto-confirm.

To confirm a batch of pending entries, edit the JSON file and set `"confirmed": true` for each correct suggestion, then re-run the parser — confirmed entries will be used on the next run and promoted to `permanent/mappings_global.json`.

**Action items if parse rate < 90%:**
- Check if Short Descriptions follow `PRODUCT VERSION - [TAGS] MODULE: description` format
- Genuinely new module names will be caught by fuzzy matching automatically
- Only add to `MODULE_ALIASES` in `parse_ecl_export.py` for strings that fuzzy matching structurally cannot handle (leading/trailing punctuation, pure acronyms)

### Step 2.4 — Review Parser Output

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

***

## Phase 3 — Risk Scoring & Quadrant Assignment (Days 3–4)

### Step 3.1 — Generate Risk Register

```bash
python scripts/compute_risk_scores.py data/ecl_parsed.csv data/risk_register_all.csv
```

Per-module metrics computed:
- Total bugs, severity-weighted total (S1×10 + S2×5 + S3×2 + S4×1)
- Critical/Major/Normal/Minor counts
- Side Effect (regression) rate, AT Found (automation catch) rate
- Average repro rate, days-to-close, builds-to-fix
- Unique reporter count, open/closed/postponed counts
- Auto-calculated probability score (1–5 via quintile percentile ranking of bug count)

Produces:
- `data/risk_register_all.csv` — combined across all versions
- `data/risk_register_versions/risk_register_<ver>.csv` — one per version (stored in subfolder)

### Step 3.2 — Score Impact & Detectability with Local LLM

```bash
python scripts/ai_risk_scorer.py data/risk_register_all.csv data/risk_register_scored_all.csv --provider heuristic
```

Three provider options:

**Option A — Heuristic (instant, no dependencies, recommended first run):**
```bash
python scripts/ai_risk_scorer.py data/risk_register_all.csv data/risk_register_scored_all.csv --provider heuristic
```

**Option B — Ollama (local LLM, free, ~2–5 minutes for ~90 modules):**
```bash
# Ollama is likely already running; if not: ollama serve &>/dev/null & sleep 5
python scripts/ai_risk_scorer.py data/risk_register_all.csv data/risk_register_scored_all.csv \
  --provider ollama --model qwen3:7b
# 8 GB Mac Mini:
python scripts/ai_risk_scorer.py data/risk_register_all.csv data/risk_register_scored_all.csv \
  --provider ollama --model phi3
```

**Option C — OpenAI API (best reasoning, ~1 min, ~$0.05):**
```bash
export OPENAI_API_KEY=sk-...
python scripts/ai_risk_scorer.py data/risk_register_all.csv data/risk_register_scored_all.csv \
  --provider openai
```

**Resume support:** If the run is interrupted, simply rerun the same command. Already-scored modules are skipped automatically. Progress is checkpointed to disk every 10 modules so at most 10 modules of work are lost on a crash.

**Output per module (all three modes):**
- `impact_score` (1–5) — domain severity if module breaks
- `detectability_score` (1–5) — how hard bugs are to catch
- `probability_score_auto` (1–5) — quintile rank of historical bug density
- `risk_score_final` = Impact × Probability × Detectability (max 125)
- `quadrant`: Q4 (≥60), Q3 (30–59), Q2 (10–29), Q1 (<10)

Produces:
- `data/risk_register_scored_all.csv` — combined scored file (Dashboard Step 2 input)
- `data/risk_register_versions/risk_register_scored_<ver>.csv` — one per version

**Expected output:**
```
Scoring 90 modules from data/risk_register_all.csv

RISK SCORING RESULTS
Q4 - Test First (12 modules):
  AI Storytelling   Score:100  I:5 P:5 D:4
  Export            Score: 75  I:5 P:5 D:3
  Auto Edit         Score: 60  I:5 P:4 D:3
Q3 - Test Second (23 modules):
  Auto Captions     Score: 48  I:4 P:4 D:3
  ...

heuristic   : 90 modules
```

### Step 3.3 — Generate Test Skeletons & Management Summary

```bash
python scripts/auto_tag_tests.py data/risk_register_scored_all.csv \
  --generate-skeletons tests/generated/ --summary
```

Creates:
- One `test_<module>.py` per module with correct `@pytest.mark.q4` (or q3/q2/q1) markers
- Allure `suite`/`sub-suite`/`tag` decorators
- Severity matched to quadrant (Q4=CRITICAL, Q1=TRIVIAL)
- Three skeleton test methods per file
- `data/quadrant_summary.md` — formatted table ready for management

### Step 3.4 — Optional: Team Refinement Workshop

1. Upload `data/risk_register_scored_all.csv` to Google Sheets
2. Share with team
3. In a 2-hour meeting: review AI-assigned `impact_score` and `detectability_score`
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

Access from any team device on the same network at: `http://<mac-mini-ip>:8501`

```bash
# Find Mac Mini IP:
ipconfig getifaddr en0
```

**Note:** Port 8501 may already be in use if Streamlit is running. Find and kill the existing process:
```bash
lsof -i :8501        # find the PID
kill -9 <PID>        # kill it
```
Or use a different port with `--server.port 8502`.

### Step 4.2 — Load Data into Dashboard

The dashboard uses a **dual-input system**:

**Step 1 (required) — Bug data (File Path mode):**
Set path to `data/ecl_parsed.csv` → unlocks tabs 1–6

**Step 2 (optional, recommended) — Risk scores (File Path mode):**
Set path to `data/risk_register_scored_all.csv` → enriches all tabs with risk scores, unlocks Tab 7

The dashboard automatically resolves per-version scored files from `data/risk_register_versions/` — when a single version is selected in the sidebar filter, it loads that version's specific scores. When multiple or all versions are selected, it uses the combined `_all` file.

### Step 4.3 — Dashboard Tabs

| Tab | Name | Data Source | What It Shows |
|-----|------|-------------|---------------|
| 1 | Module × Severity | Bug-level | Heatmap of bug density by module/category and severity (1–4). `[Q4]`/`[Q3]` badges when risk data is loaded |
| 2 | Version Timeline | Bug-level | Severity-weighted bugs per module across versions — reveals regressions |
| 3 | Tag Analysis | Bug-level | Tag distribution heatmap. Regression hotspots (Side Effect), AT blind spots |
| 4 | Priority vs Severity | Bug-level | QA severity vs RD priority mismatch matrix |
| 5 | Team Coverage | Bug-level | Tester × Category matrix — knowledge silo detection |
| 6 | KPI Dashboard | Bug-level + Risk | Total bugs, critical count, weekly trend, severity pie. Q4 count + avg risk score when risk data loaded |
| 7 | Risk Heatmap | **Risk-scored** | Interactive treemap. Version-aware: shows per-version or combined scores based on sidebar filter. Click any module to see its bugs in the right panel. Module click detection uses Plotly's `parent` field (v2.14) so modules whose name matches their category (e.g. "Shortcut") are correctly handled. |

Each tab has a **📖 How to read this chart** expander explaining the data source, what the numbers mean, and how the scores were calculated.

### Step 4.4 — Risk Heatmap Tab (Tab 7) — Detail

**Version context:** A banner at the top shows which data is currently displayed:
- Single version selected → per-version scored file used (falls back to combined if not available)
- Multiple or all versions → combined `risk_register_scored_all.csv` used

**Color options (radio button):**
- **Risk Score (LLM)** — Color = `risk_score_final` (I×P×D). Scale: yellow (low) → red (high). Primary view.
- **Quadrant** — Color by Q4 (red) / Q3 (orange) / Q2 (yellow) / Q1 (green)
- **Critical Count** — Color = number of S1 bugs
- **Severity Weight** — Color = weighted sum (S1×10 + S2×5 + S3×2 + S4×1)

**Rectangle size** = bug count from `ecl_parsed.csv`

**Hierarchy**: Category → Module. Click any module block to show its individual bugs in the right panel.

**Sunburst view** available in expandable section below treemap.

**Category Summary table** shows: Modules, Total Bugs, Severity Weight, Avg Risk Score, Q4 count per category.

**Q4 bar chart** ranks Q4 modules by risk score descending.

**Risk vs Probability scatter** appears when `probability_score_auto` is available.

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

# 1a. Fetch fresh data via n8n webhook (recommended)
python scripts/fetch_from_n8n.py \
  --webhook-url https://your-n8n-host/webhook/82746bb5-e140-4720-98a3-d1965900274d \
  --output data/ecl_raw.json

# 1b. Or export manually from ECL → save as data/ecl_export.xlsx

# 2. Re-parse (works with both .json and .xlsx inputs)
python scripts/parse_ecl_export.py data/ecl_raw.json data/ecl_parsed.csv
# or: python scripts/parse_ecl_export.py data/ecl_export.xlsx data/ecl_parsed.csv

# 3. Re-score (only needed if quadrant assignments need updating)
python scripts/compute_risk_scores.py data/ecl_parsed.csv data/risk_register_all.csv
python scripts/ai_risk_scorer.py data/risk_register_all.csv data/risk_register_scored_all.csv \
  --provider heuristic

# 4. Refresh dashboard — click Streamlit's "Rerun" button or reload browser
```

**Note on pending mappings:** After re-parsing, check for new entries in `data/module_mappings/versions/*_pending.json`. These are new module strings the fuzzy matcher found but wasn't confident enough to auto-confirm. Review and confirm them once — they'll be used automatically on all future runs.

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

**Note:** If `Build#` contains version strings (e.g. `16.3.0.2847`) rather than plain integers, those rows are automatically dropped. The script will log how many were dropped so you can verify this isn't removing too much data.

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
| Monday | Fetch fresh ECL data via n8n webhook (or manual export) → parse + risk score | 20 min |
| Monday | Review pending mappings in `module_mappings/versions/` | 10 min |
| Monday | Review dashboard for new hotspots | 15 min |
| Wednesday | Review Allure report, update failing tests | 1 hr |
| Friday | Run clustering to find new bug patterns | 15 min |

### Monthly Cadence

- Re-run `ai_risk_scorer.py` with latest data (quadrant assignments may shift)
- Run `auto_tag_tests.py --tag-existing tests/` to update markers on existing tests
- Review `data/quadrant_summary.md` with team — validate Q4/Q3 modules still correct
- Promote confirmed pending mappings: open `*_confirmed.json` files and check they look correct, then they auto-promote to `mappings_global.json` on next parse run

### When New Modules Are Added

1. Re-run the parser — new modules will be caught by fuzzy matching automatically
2. Review `*_pending.json` for the new module's suggestions and confirm
3. If the new module is a high-impact feature, add it to `IMPACT_OVERRIDES` in `ai_risk_scorer.py`
4. Re-run the full pipeline (Steps 2.2 → 3.2 → 3.3)

**You no longer need to manually add new modules to `MODULE_ALIASES` or `MODULE_CATEGORIES`** unless the module name is structurally unparseable (leading/trailing punctuation, pure acronym with no word overlap).

***

## Quick Reference — Full Pipeline (One Command)

**Via n8n webhook (Path A):**
```bash
source .venv/bin/activate && \
python scripts/fetch_from_n8n.py \
  --webhook-url https://your-n8n-host/webhook/82746bb5-e140-4720-98a3-d1965900274d \
  --then-parse && \
python scripts/compute_risk_scores.py data/ecl_parsed.csv data/risk_register_all.csv && \
python scripts/ai_risk_scorer.py data/risk_register_all.csv data/risk_register_scored_all.csv --provider ollama --model llama3.1 && \
python scripts/auto_tag_tests.py data/risk_register_scored_all.csv --generate-skeletons tests/generated/ --summary && \
streamlit run scripts/bug_heatmap_dashboard.py --server.address 0.0.0.0 --server.port 8501
```

**Via manual Excel export (Path B):**
```bash
source .venv/bin/activate && \
python scripts/parse_ecl_export.py data/ecl_export.xlsx data/ecl_parsed.csv && \
python scripts/compute_risk_scores.py data/ecl_parsed.csv data/risk_register_all.csv && \
ollama serve &>/dev/null & sleep 5 && \
python scripts/ai_risk_scorer.py data/risk_register_all.csv data/risk_register_scored_all.csv --provider ollama --model llama3.1 && \
python scripts/auto_tag_tests.py data/risk_register_scored_all.csv --generate-skeletons tests/generated/ --summary && \
streamlit run scripts/bug_heatmap_dashboard.py --server.address 0.0.0.0 --server.port 8501
```

In the dashboard sidebar: set **Step 1** path to `data/ecl_parsed.csv`, set **Step 2** path to `data/risk_register_scored_all.csv`.

***

## Troubleshooting

| Issue | Cause | Fix |
|-------|-------|-----|
| Dashboard: "Wrong file!" on ecl_parsed.csv | File is risk_register_scored_all.csv (lacks `severity_num`) | Use `ecl_parsed.csv` from Step 2.2 |
| Dashboard: Tab 7 shows warning, not treemap | Risk data not loaded in sidebar Step 2 | Set path to `risk_register_scored_all.csv` in sidebar |
| Dashboard: "No per-version score file" caption | Per-version scoring not yet run | Run `ai_risk_scorer.py` — it auto-scores all files in `risk_register_versions/` |
| Dashboard: clicking a module block shows nothing (right panel stays empty) | Module name is identical to its parent category name (e.g. "Shortcut" module inside "Shortcut" category) — fixed in v2.14 | Update to `bug_heatmap_dashboard.py` v2.14; the fix uses Plotly's `parent` field instead of the name-based category exclusion |
| Port 8501 not available | Previous Streamlit instance still running | `lsof -i :8501` then `kill -9 <PID>` |
| Parse rate below 90% | Non-standard Short Description format or new module patterns | Check `*_pending.json` files; confirm suggestions or add alias for truly unparseable strings |
| 1000+ uncategorized modules | `parse_ecl_export.py` is the old version without auto-strip | Replace with v2.3 and delete `module_mappings/versions/*_pending.json`, then re-run |
| risk_register_all.csv has 1000+ rows | Old scorer file used — sub-variants not stripped | Ensure `parse_ecl_export.py` v2.3 is in use; re-run full pipeline |
| Ollama timeout errors | Model too large for available RAM | Switch to `phi3` or use `--provider heuristic` |
| Scorer stops mid-run | Timeout / crash | Re-run same command — resume support skips already-scored modules automatically |
| `predict_defects.py` drops many rows | `Build#` contains version strings not plain integers | Expected — script logs the count. Use numeric build numbers for better predictions |
| `cluster_bugs.py` not enough terms | Module has very few unique bug descriptions | Expected — script handles gracefully |
| Visual regression too sensitive | Minor font rendering differences | Raise threshold: `export VISUAL_THRESHOLD=0.92` |
| Appium can't find device | `DEVICE_NAME` mismatch | Run `idevice_id -l` and use exact device UUID |
| `fetch_from_n8n.py`: connection error | n8n not running or wrong URL | Check n8n is active; verify webhook URL matches the imported workflow |
| `fetch_from_n8n.py`: missing required fields | n8n `Get Columns_v3` Set node misconfigured | Re-import `Dashboard_Query_eBug_List_v3.json`; the script lists which fields are absent |
| `fetch_from_n8n.py`: empty response | n8n date range filter returns no bugs | Check the `CreateTime` range in the eCL eBug query node; try widening the range |
| `repro_rate` all 0.5 after JSON parse | `Reproduce Probability` is not in the eCL API response | Expected — 0.5 is the correct default; Detectability scoring is unaffected |
| JSON parse: "Short Description column not found" | n8n workflow still using old `Get Columns_v2` node | Re-import `Dashboard_Query_eBug_List_v3.json` which outputs `Short Description` correctly |