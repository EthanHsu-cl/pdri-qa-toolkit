# PDR-I QA Toolkit v2.6 — Complete Step-by-Step Implementation Guide

> Mac Mini M1 · Python 3.14 · Appium · Jenkins · Streamlit  
> Updated from v2.5 to reflect all session changes through v2.16.

***

## What Changed in v2.6

### Dashboard (`bug_heatmap_dashboard.py` → v2.16)

| Area | Change |
|------|--------|
| **Tab 8 — 🔬 Bug Clusters** | New tab. Loads `ecl_parsed_clustered.csv` + `ecl_parsed_cluster_summary.csv` (Sidebar Step 3). Shows headline metrics (themes found, bugs grouped), a colour-coded bar chart of all themes by size and severity, and expandable per-theme detail cards with sample bug descriptions and a plain-English action line. Designed to be readable by anyone on the team, not just QA. The **📖 How to read this tab** expander is a full reference guide covering: headline metric meanings, colour-coding key, bar chart interpretation (length vs colour), how to investigate a pattern (isolated module vs shared infrastructure), when to escalate, and the re-run cadence recommendation. |
| **Tab 9 — 🔮 Defect Forecast** | New tab. Loads `ecl_parsed_predictions.csv` + `_focus_summary.txt` + `_leading_indicators.csv` (Sidebar Step 4). Shows forecast bug counts per module for the next build, an actual-vs-predicted comparison chart, per-module "what to test" cards in plain English, and a leading-indicators chart explaining which current signals best predict future bugs. The **📖 How to read this tab** expander is a full reference guide covering: the prediction target definition, all six model features in plain English, risk level thresholds with testing cadence, all headline metric meanings, module card field explanations (dominant_bug_type, leading_signal), actual-vs-predicted comparison guidance, leading indicators chart interpretation, and data/model limitations. |
| **Sidebar Steps 3 & 4** | Two new optional data loaders added below the existing risk score loader. Step 3 = cluster files, Step 4 = prediction files. Neither is required; the new tabs show a clear setup prompt when files are missing. |
| **Heatmap colour conflict** | Module × Severity heatmap cells now use a **blue gradient** (`HEATMAP_COLOR_SCALE = "Blues"`) instead of red/orange/yellow, so cell shading never visually clashes with the `[P1]`/`[P2]` priority badge text on the same rows. A caption explains the two-channel encoding. |
| **Heatmap sort order** | Module × Severity heatmap now sorts rows by **total weighted count** across all severities, so the busiest modules always float to the top. Previously sorted by Critical count only, which buried modules with many Major/Normal bugs. |
| **Scatter plot overlap** | Risk Score vs Probability scatter now adds ±0.35 random x-jitter so dots at the same integer probability level separate visually. `size_max` reduced 30 → 18, `opacity` set to 0.75. X-axis tick labels remain pinned to integers 1–5. A caption explains the jitter. |

### `predict_defects.py` → v2.2

| Area | Change |
|------|--------|
| **Target clarification** | A `PREDICTION_GUIDE` box printed at startup explains exactly what `bug_count` (the target) means, what every feature column represents, what `target` vs `predicted` means, and what each risk level threshold is. A plain-English legend is printed before the results table. |
| **`dominant_bug_type`** | Each predicted module now carries its most common historical severity (e.g. "crash/Critical (S1)", "Major functional (S2)") so readers know *what kind* of bugs to expect, not just how many. |
| **Leading indicators** | `compute_leading_indicators()` computes Pearson correlation of each lag feature against the next-build target. Identifies which current bug signals (e.g. "critical-bug momentum in last 3 builds") are most strongly predictive of future issues. Saved to `_leading_indicators.csv`. |
| **Focus summary** | `generate_focus_summary()` writes a plain-English paragraph per top-risk module: what type of bugs to expect, which signal is driving the risk, and what specific testing action to take. Saved to `_focus_summary.txt`. |
| **Output files** | Now produces four files: `_predictions.csv`, `_importance.csv`, `_leading_indicators.csv`, `_focus_summary.txt`. |

### `fetch_from_n8n.py`

| Area | Change |
|------|--------|
| **Status comparison** | `save_json()` now compares incoming `Status` against the cached value for each `BugCode`. Changed records get `_status_changed=True` + `_prev_status` stored. Unchanged records have `_status_changed=False` explicitly reset so stale flags from previous runs are cleared. Summary prints `New / Status-changed / Unchanged` counts. |
| **Weekday/weekend scheduling** | `resolve_scope()` + `--scope auto\|latest\|all` flag. Auto mode: weekdays → `scope=latest` (fast, newest version only); weekends → `scope=all` (full history, lets you measure total update time). The actual latest version string is read from `version_catalogue.csv` and sent as `latest_version` in the webhook payload, so n8n filters by an exact version string rather than guessing. |

### `parse_ecl_export.py`

| Area | Change |
|------|--------|
| **Version from `Version` column** | `parsed_version` is now read from the dedicated ECL `Version` column first, normalised to the first `X.Y.Z` segment. The previous regex scan of `Short Description` is kept only as a fallback when the column is absent or blank. Non-matching rows also inherit the Version column value instead of getting `None`. |
| **Version catalogue** | `build_version_catalogue()` groups all bugs by `parsed_version`, finds `max(Create Date)` per group, and ranks versions newest-first by recency — not by string sort order. Versions with fewer than 5 bugs (`VERSION_SPARSE_THRESHOLD`) are flagged `version_is_sparse=True` and ranked after all real versions. Saved to `data/version_catalogue.csv` for all downstream tools to consume. |

### `cluster_bugs.py` → v2.3

| Area | Change |
|------|--------|
| **Ollama semantic embeddings** | `--provider ollama` now uses `/api/embeddings` to get meaning-aware vector representations per bug description, enabling richer cluster groupings than TF-IDF keyword overlap alone. Falls back to TF-IDF automatically if Ollama is unreachable. |
| **LLM cluster labels** | In Ollama mode, each cluster's name is generated by asking the LLM to summarise the top 8 sample descriptions into a 3–6 word plain-English label (e.g. "subtitle rendering delay") instead of raw keyword concatenation. |
| **`embed_source` column** | Output CSV now includes `embed_source` (`"ollama"` or `"tfidf"`) so the dashboard and downstream tools can report which mode produced the data. |
| **Model flag** | `--model` flag added (default `llama3.1`) for both embedding and labelling calls. |

### `ai_risk_scorer.py` → v2.4

| Area | Change |
|------|--------|
| **Default model** | Default Ollama model updated from `llama3.1` → `qwen3:7b` to match current recommended model for the Mac Mini setup. |
| **Per-version output normalisation** | Version separator dots are now replaced with underscores in output filenames (`1.0.0` → `1_0_0`) to prevent filesystem issues on case-insensitive volumes. |
| **Scored-file input guard** | Per-version scoring loop now explicitly excludes files whose names already start with `risk_register_scored_` so a partial run can never feed a scored file back in as input. |



| Area | Change |
|------|--------|
| **Recency-ordered per-version registers** | `_version_order()` reads `version_catalogue.csv` (falls back to re-deriving from `Create Date` if absent). Per-version risk registers are generated newest-first. |
| **Sparse version skipping** | Versions below the sparse threshold are skipped for per-version register generation with a `⚠️ SKIP` log line. They are still included in the combined all-versions register. |

---

## What Changed in v2.5

| File | Change |
|------|--------|
| `parse_ecl_export.py` | **v2.4** — JSON input support for n8n webhook integration. Accepts `.json` files (list of bug objects) produced by `fetch_from_n8n.py` in addition to `.xlsx` / `.csv`. Handles both plain list and n8n's `{"json": {...}}` wrapped shape. Applies `_N8N_COL_MAP` remapping table so the webhook's output field names map explicitly to internal parser names. `Reproduce Probability` is absent from the API; `repro_rate` defaults to `0.5` for all JSON-sourced rows. |
| `fetch_from_n8n.py` | **New script.** POSTs to the n8n webhook, normalises the n8n item-wrapper shape, audits required fields, saves the flat list to `data/ecl_raw.json`. Run with `--then-parse` to chain directly into `parse_ecl_export.py` in one command. |
| `Dashboard_Query_eBug_List_v3.json` | **Updated n8n workflow.** Renames `Get Columns_v2` → `Get Columns_v3` with corrected field mappings using real API field names. Adds `Creator` and `Handler` pass-through. Removes the fragile `TemplateName.split(ProductName)` expression. |

---

## What Changed in v2.4

| File | Change |
|------|--------|
| `bug_heatmap_dashboard.py` | **v2.14** — Fixed treemap click detection for modules whose name matches their category name. Root cause: old guard `clicked_label not in all_categories` evaluated to `False` when label and category are identical. Fix: replaced with Plotly's `parent` field check. Also reverted responsive layout to fixed `[6, 4]` split; removed JS width-detection snippet; removed `render_detail_panel`. v2.12: dynamic treemap height; scrollable right panel. |

---

## What Changed in v2.3

| File | Change |
|------|--------|
| `parse_ecl_export.py` | Automatic sub-variant normalisation, `parsed_module_raw` column, O(1) alias lookup, `VersionMappingStore` caching. |
| `compute_risk_scores.py` | Per-version files in `risk_register_versions/` subfolder, single `groupby` for tag columns, single pivot for status counts. |
| `ai_risk_scorer.py` | Resume/checkpoint support, Ollama retry logic, expanded `IMPACT_OVERRIDES`, per-version scored files. |
| `bug_heatmap_dashboard.py` | Version-aware risk loading, version context banner, `normalise_module()` safety net, all `use_container_width` → `width='stretch'`, clickable `BugCode` links, dynamic treemap height, comprehensive `📖` expanders on all tabs. |
| `predict_defects.py` | Logs dropped row count for non-numeric `Build#` before the `<20 samples` check. |

***

## File Inventory

| # | File | Version | Purpose |
|---|------|---------|---------|
| 1 | `scripts/parse_ecl_export.py` | v2.6+ | Parse ECL Excel / CSV / n8n JSON → enriched CSV + version catalogue |
| 2 | `scripts/compute_risk_scores.py` | v2.4 | Module metric aggregation → risk register (recency-ordered, sparse-skipping) |
| 3 | `scripts/ai_risk_scorer.py` | v2.4 | Impact/Detectability scoring (heuristic / Ollama / OpenAI) with resume/checkpoint |
| 4 | `scripts/auto_tag_tests.py` | v2.1 | pytest skeleton generator + Allure decorator output |
| 5 | `scripts/bug_heatmap_dashboard.py` | v2.16 | Streamlit dashboard — 9 tabs, 4-step sidebar |
| 6 | `scripts/cluster_bugs.py` | v2.3 | TF-IDF / Ollama semantic embeddings + K-Means/DBSCAN clustering + LLM cluster labels |
| 7 | `scripts/predict_defects.py` | v2.2 | Gradient Boosting defect prediction with leading indicators + plain-English focus summary |
| 8 | `scripts/visual_regression.py` | v2.1 | Screenshot diff engine |
| 9 | `scripts/fetch_from_n8n.py` | v1.2 | Fetch bugs from n8n webhook → `data/ecl_raw.json` (status comparison, scope scheduling, version catalogue integration) |
| 10 | `Jenkinsfile` | — | P1→P2→P3→P4 CI/CD pipeline |
| 11 | `tests/conftest.py` | — | Appium + visual regression fixtures |
| 12 | `pytest.ini` | — | Marker registration |
| 13 | `requirements.txt` | — | Python dependencies |
| 14 | `tests/test_ai_storytelling.py` | — | Example P1 test |
| 15 | `setup_mac_mini.sh` | — | One-command M1 setup + LaunchAgent |
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
│   ├── ecl_export.xlsx                          ← you provide this (ECL export, optional)
│   ├── ecl_raw.json                             ← Step 2.1a output (from n8n webhook)
│   ├── ecl_parsed.csv                           ← Step 2.2 output  ← Dashboard Step 1
│   ├── version_catalogue.csv                    ← Step 2.2 output  (recency-ordered version list)
│   ├── risk_register_all.csv                    ← Step 3.1 output (combined)
│   ├── risk_register_scored_all.csv             ← Step 3.2 output  ← Dashboard Step 2
│   ├── risk_register_versions/                  ← per-version files (auto-created, newest-first)
│   │   ├── risk_register_16.4.0.csv
│   │   ├── risk_register_scored_16.4.0.csv
│   │   └── ...
│   ├── module_mappings/                         ← fuzzy match store (auto-created)
│   │   ├── permanent/mappings_global.json
│   │   └── versions/
│   ├── ecl_parsed_clustered.csv                 ← Step 6.1 output  ← Dashboard Step 3
│   ├── ecl_parsed_cluster_summary.csv           ← Step 6.1 output  ← Dashboard Step 3
│   ├── ecl_parsed_predictions.csv               ← Step 6.2 output  ← Dashboard Step 4
│   ├── ecl_parsed_predictions_focus_summary.txt ← Step 6.2 output  ← Dashboard Step 4
│   ├── ecl_parsed_predictions_leading_indicators.csv  ← Step 6.2 output  ← Dashboard Step 4
│   ├── ecl_parsed_predictions_importance.csv    ← Step 6.2 output (model feature importances)
│   └── quadrant_summary.md                      ← Step 3.3 output
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
│   └── Dashboard_Query_eBug_List_v3.json
├── tests/
│   ├── conftest.py
│   ├── test_ai_storytelling.py
│   └── generated/
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

There are two ways to get bug data into the pipeline. Use whichever fits your workflow.

---

### Path A — n8n Webhook (recommended for regular refreshes)

#### Step 2.1a — Import the n8n Workflow

1. Open your n8n instance
2. Import `n8n/Dashboard_Query_eBug_List_v3.json`
3. Activate the workflow — the webhook endpoint is now live

#### Step 2.1b — Fetch Bugs via Webhook

```bash
source .venv/bin/activate
python scripts/fetch_from_n8n.py \
  --webhook-url https://your-n8n-host/webhook/82746bb5-e140-4720-98a3-d1965900274d \
  --output data/ecl_raw.json
```

Or fetch and parse in one command:

```bash
python scripts/fetch_from_n8n.py \
  --webhook-url https://your-n8n-host/webhook/82746bb5-e140-4720-98a3-d1965900274d \
  --then-parse
```

**Scheduling mode (new in v2.6):**
```bash
# Auto mode (default): weekdays fetch latest version only, weekends fetch all
python scripts/fetch_from_n8n.py --scope auto --then-parse

# Force full fetch regardless of day
python scripts/fetch_from_n8n.py --scope all --then-parse
```

The script prints `New: N | Status-changed: N | Unchanged: N` after each run. Records whose Status changed since the last fetch are flagged with `_status_changed=True` in `ecl_raw.json` for traceability.

**What fields the API provides:**

| Field delivered | Maps to parser column | Notes |
|---|---|---|
| `ShortDescription` | `Short Description` | Module, tags, version all parsed from this |
| `EbugSeverity` (int) | `Severity` | Integer `1`–`4` |
| `EbugPriority` (int) | `Priority` | Integer `1`–`5` |
| `Status` | `Status` | Exact match |
| `CreateTime` | `Create Date` | ISO datetime |
| `CloseTime` | `Closed Date` | ISO datetime, nullable |
| `Version` | `Version` | e.g. `16.3.5` — now used as authoritative `parsed_version` source |
| `Build` | `Build#` | Numeric build ID |
| `CloseToBuild` | `Close Build#` | Nullable |
| `Creator` | `Creator` | Username string |
| `BugCode` | `BugCode` | Used for dashboard ECL links |
| `BugBelong` | `BugBelong` | RD / QA attribution |
| `Handler` | `Handler` | Current assignee |
| `Reproduce Probability` | — | **Not in API** — `repro_rate` defaults to `0.5` |

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

The parser accepts `.json`, `.xlsx`, and `.csv` inputs transparently — all produce the same `ecl_parsed.csv` format.

What it does:
- Parses every Short Description in format `PDR-I 16.2.5 - [EDF][UX] AI Storytelling: subtitle misplaced`
- Extracts: product code, version, tags, module name, description
- **`parsed_version` from `Version` column (new in v2.6):** reads the dedicated `Version` field instead of regex-scraping it from Short Description. Falls back to Short Description scan only when the field is absent or blank. This avoids mismatches caused by copy-paste errors or version prefix formatting differences.
- **Automatic sub-variant normalisation:** strips trailing parentheticals (`Auto Edit(Pet 02)` → `Auto Edit`), comma-splits compound names, handles `>` notation
- Applies typo aliases and fuzzy matching (rapidfuzz, threshold 85%)
- Maps every module to one of 20 top-level categories
- Creates boolean tag columns: `tag_edf`, `tag_ux`, `tag_side_effect`, `tag_at_found`, etc.
- Saves both `parsed_module` (normalised) and `parsed_module_raw` (original ECL string)
- Parses severity to numeric (1–4) and weighted (S1×10 / S2×5 / S3×2 / S4×1)
- Computes `days_to_close` and `builds_to_fix`
- **Generates `version_catalogue.csv` (new in v2.6):** ranks all versions by recency (`max(Create Date)` per version group). Versions with fewer than 5 bugs are flagged `version_is_sparse=True` and ranked last to prevent typo versions from appearing as "newest."

**Expected output:**
```
Loaded 9992 bugs

PARSING SUMMARY
Total bugs:            9992
Successfully parsed:   9654 (96.6%)
Unique modules:        ~90–150

VERSION CATALOGUE (sorted by recency, typo/sparse versions last):
  #   Version             Bugs   Latest Create Date       Sparse?
  1   16.4.0              412    2025-03-18               
  2   16.3.5              387    2025-02-04               
  3   16.3.0              290    2024-11-11               
  4   16.0a               2      2024-10-30               ⚠️  sparse
```

### Step 2.3 — Review and Confirm Pending Mappings

After parsing, check `data/module_mappings/versions/` for `*_pending.json` files. These contain strings the fuzzy matcher found a near-match for (score 65–84%) but didn't auto-confirm. Edit the JSON and set `"confirmed": true` for each correct suggestion, then re-run the parser.

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

## Phase 3 — Risk Scoring & Priority Assignment (Days 3–4)

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
- `data/risk_register_versions/risk_register_<ver>.csv` — one per non-sparse version, **newest first** (new in v2.6). Sparse/typo versions are skipped with a `⚠️ SKIP` warning.

### Step 3.2 — Score Impact & Detectability

Three provider options:

**Option A — Heuristic (instant, no dependencies, recommended first run):**
```bash
python scripts/ai_risk_scorer.py data/risk_register_all.csv data/risk_register_scored_all.csv \
  --provider heuristic
```

**Option B — Ollama (local LLM, free, ~2–5 min for ~90 modules):**
```bash
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

**Resume support:** If the run is interrupted, simply rerun the same command. Already-scored modules are skipped automatically. Progress is checkpointed every 10 modules.

**Output per module:**
- `impact_score` (1–5) — domain severity if module breaks
- `detectability_score` (1–5) — how hard bugs are to catch before release
- `probability_score_auto` (1–5) — quintile rank of historical bug density
- `risk_score_final` = Impact × Probability × Detectability (max 125)
- `quadrant`: P1 ≥60 / P2 30–59 / P3 10–29 / P4 <10

Produces:
- `data/risk_register_scored_all.csv` ← Dashboard Step 2 input
- `data/risk_register_versions/risk_register_scored_<ver>.csv` — one per version

### Step 3.3 — Generate Test Skeletons & Management Summary

```bash
python scripts/auto_tag_tests.py data/risk_register_scored_all.csv \
  --generate-skeletons tests/generated/ --summary
```

Creates one `test_<module>.py` per module with `@pytest.mark.p1` (or p2/p3/p4) markers, Allure decorators, and `data/quadrant_summary.md`.

### Step 3.4 — Optional: Team Refinement Workshop

1. Upload `data/risk_register_scored_all.csv` to Google Sheets
2. Review AI-assigned `impact_score` and `detectability_score` with team
3. Re-export corrected CSV, then re-run:

```bash
python scripts/ai_risk_scorer.py data/risk_register_corrected.csv data/risk_register_final.csv \
  --provider heuristic
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

Access from any team device on the same network: `http://<mac-mini-ip>:8501`

```bash
# Find Mac Mini IP:
ipconfig getifaddr en0
```

### Step 4.2 — Load Data into Dashboard

The dashboard uses a **four-step sidebar**:

| Step | File | Required | Unlocks |
|------|------|----------|---------|
| **Step 1** — Bug data | `data/ecl_parsed.csv` | ✅ Required | Tabs 1–6 |
| **Step 2** — Risk scores | `data/risk_register_scored_all.csv` | Optional | Tab 7 (Risk Heatmap), risk badges on all other tabs |
| **Step 3** — Bug clusters | `data/ecl_parsed_clustered.csv` + `ecl_parsed_cluster_summary.csv` | Optional | Tab 8 (Bug Clusters) |
| **Step 4** — Defect forecast | `data/ecl_parsed_predictions.csv` + `_focus_summary.txt` + `_leading_indicators.csv` | Optional | Tab 9 (Defect Forecast) |

The dashboard reads `data/version_catalogue.csv` (produced by the parser) to order the version picker by recency and exclude sparse/typo versions from the default selection. When the catalogue is absent it falls back to deriving the same ordering live from the bug data.

### Step 4.3 — Dashboard Tabs

| Tab | Name | Requires | What It Shows |
|-----|------|----------|---------------|
| 1 | 🗺️ Module × Severity | Step 1 | Heatmap of bug density by module/category and severity. Cell shade uses a **blue gradient** (separate from priority colours). `[P1]`/`[P2]` badges when risk data loaded. Sorted by total weighted count (busiest modules at top). |
| 2 | 📅 Version Timeline | Step 1 | Severity-weighted bugs per module across versions — reveals regressions |
| 3 | 🏷️ Tag Analysis | Step 1 | Tag distribution heatmap, regression rate, AT coverage blind spots |
| 4 | ⚖️ P/S Alignment | Step 1 | QA severity vs RD priority mismatch matrix |
| 5 | 👥 Team Coverage | Step 1 | Tester × Category matrix — knowledge silo detection |
| 6 | 📊 KPI Dashboard | Steps 1–2 | Total bugs, critical count, weekly trend, severity pie. P1/P2 counts + avg risk score when risk data loaded. |
| 7 | 🔥 Risk Heatmap | Steps 1–2 | Interactive treemap with version-aware scores. Click any module to see its bugs. P1 bar chart. Risk vs Probability scatter (jittered for readability). |
| 8 | 🔬 Bug Clusters | Steps 1+3 | Theme overview bar chart, expandable per-theme cards with sample bugs and plain-English action lines. |
| 9 | 🔮 Defect Forecast | Steps 1+4 | Forecast bar chart, actual-vs-predicted comparison, per-module "what to test" cards, leading indicators chart. |

Each tab has a **📖 How to read this chart** expander written for both QA engineers and non-technical team members.

### Step 4.4 — Bug Clusters Tab (Tab 8) — Detail

Tab 8 groups all bugs into named **themes** using natural language analysis of their short descriptions. Instead of reviewing hundreds of individual bugs, you can immediately see what categories of problems keep recurring and which ones are the most severe.

**What "themes" are:**
`cluster_bugs.py` groups bugs by the keywords that appear most often together in their descriptions. The default algorithm uses TF-IDF (term frequency scoring) + K-Means globally (25 clusters) and DBSCAN per-module on the five busiest modules. When run with `--provider ollama`, it uses semantic embeddings from an Ollama LLM for richer, meaning-aware grouping and LLM-generated plain-English cluster labels. In TF-IDF mode, each cluster is named by its top 2–3 co-occurring keywords (e.g. `ai storytelling | subtitle | timing`). Think of each cluster as a *recurring complaint type*.

**Headline metrics (top row):**
| Metric | What it means |
|--------|--------------|
| Total Bugs Analysed | The bug-level count in the loaded `ecl_parsed_clustered.csv` |
| Distinct Themes Found | Number of named clusters (excludes the "Unclustered" noise bucket) |
| Bugs Grouped into Themes | Count (and %) of bugs that belong to a named cluster — higher is better |
| Uncategorised Bugs | Bugs whose descriptions were too short or too unique to fit any cluster |

**Colour coding on the overview bar chart:** Each bar is coloured by the average severity of the bugs it contains:
- 🔴 Red = Mostly Critical (avg severity ≤ 1.5)
- 🟠 Orange = Mostly Major (1.5 – 2.5)
- 🟡 Yellow = Mostly Normal (2.5 – 3.5)
- 🟢 Green = Mostly Minor (> 3.5)

**How to act on the chart:**
- **Big bars** = widespread recurring problem pattern. These warrant a dedicated investigation or a focused test run.
- **Red/orange bars** = the theme is causing serious bugs, not just cosmetic ones. Escalate to RD regardless of bar size.
- **Clusters spanning many modules** = the root cause may be shared infrastructure (a common component, shared API, or base library), not an isolated module bug.

**Theme detail cards:**
Each expandable card shows: bug count, average severity score (1 = Critical, 4 = Minor), share of all clustered bugs, affected modules, 6 sample bug descriptions, and a plain-English action line:
- 🚨 **Immediate attention** (avg sev ≤ 1.5) — crash-level or data-loss bugs. Escalate to RD.
- ⚠️ **High priority** (avg sev ≤ 2.5) — major functional issues. Add modules to next sprint's regression list.
- 📋 **Standard priority** (avg sev ≤ 3.5) — normal-severity issues. Cover in release-candidate testing.
- ✅ **Low priority** (avg sev > 3.5) — cosmetic or minor issues. Include in full release cycle.

**Files loaded in Sidebar Step 3:**
| File | Contents |
|------|---------|
| `ecl_parsed_clustered.csv` | Full bug-level file with `cluster_id` and `cluster_label` columns added |
| `ecl_parsed_cluster_summary.csv` | One row per cluster: keyword label, bug count, affected modules, average severity |

Both files are stored under `data/clusters/` when `cluster_bugs.py` is run with default paths.

**Ollama vs TF-IDF mode:**
Running with `--provider ollama --model <model>` produces richer, meaning-aware cluster names (e.g. "subtitle rendering delay" rather than "subtitle | render | slow") and is recommended when the Mac Mini has enough free RAM. TF-IDF mode (the default, no Ollama required) is faster and perfectly adequate for most uses. The dashboard tab displays correctly regardless of which mode was used to generate the files.

### Step 4.5 — Defect Forecast Tab (Tab 9) — Detail

Tab 9 shows a **machine-learning forecast** of how many bugs each module is likely to produce in the **next build**, based on its recent history. It is designed to be readable by QA engineers and non-technical team members alike — no data-science background required.

**What is being predicted:**
`predict_defects.py` counts how many new bugs were filed against each module in each past build, trains a Gradient Boosting model on that history, and forecasts the count for the next build. The target is a raw bug count (not severity-weighted). At startup the script prints a full `PREDICTION_GUIDE` box explaining every feature column, the target variable, and how to interpret the output.

**Features the model uses (lag signals):**
- Bug count in the most recent 1 / 3 / 5 builds
- Critical bug count in the most recent 3 builds (most predictive signal for crash-heavy modules)
- Trend (direction and rate of change over the last 3 builds)
- Severity-weighted bug total in the last 3 builds

**Risk level thresholds:**
| Level | Predicted bugs | What to do |
|-------|---------------|------------|
| 🔴 Critical | > 10 | Must test every build — module is highly unstable |
| 🟠 High | 6–10 | Test every sprint. Watch for side-effect regressions in adjacent modules. |
| 🟡 Medium | 3–5 | Include in release-candidate pass. Spot-check recently changed areas. |
| 🟢 Low | < 3 | Standard release-cycle coverage. No special urgency. |

**Headline metrics (top row):**
| Metric | What it means |
|--------|--------------|
| 🔴 Critical modules | Modules predicted to produce > 10 bugs next build |
| 🟠 High-risk modules | Modules predicted to produce 6–10 bugs |
| Total modules forecast | All modules the model has enough history to score (≥ 20 build-module rows) |
| Model accuracy (MAE) | Mean absolute error vs the last known build's actual count — lower is better |

**Forecast bar chart:**
Bar height = predicted bug count. Colour = risk level. Hover to see the current actual count (last build) alongside the prediction.

**Actual vs Predicted expander:**
Grouped bar chart comparing the model's most-recent prediction against what actually happened. Bars close to equal = accurate model; bars far apart = a surprise spike occurred (or the module's behaviour recently changed significantly).

**Module forecast cards:**
One card per Critical or High-risk module. Each card shows:
- **Predicted bugs** — the model's next-build estimate
- **Actual last build** — what actually happened (when available)
- **Risk level**
- **Typical bug type** — the module's most common historical severity (e.g. "crash/Critical (S1)", "Major functional (S2)"), so the team knows what *kind* of bugs to expect
- **Why high risk** — the leading signal driving this module's score (e.g. "critical-bug momentum in last 3 builds", "sustained high volume over last 5 builds")
- **What to test** — a plain-English testing instruction matching the risk level

**Leading Indicators chart:**
Shows which current bug signals are most strongly correlated (Pearson r) with future bug counts across all modules. A red bar (positive correlation) means: when this signal is high now, more bugs tend to follow next build. A green bar (negative correlation) means: when this signal is high now, fewer bugs tend to follow. The chart is sorted by absolute correlation strength so the most reliable predictors appear first. Bars are labelled in plain English (e.g. "Critical bugs (last 3 builds)"), not raw feature variable names.

**Files produced by `predict_defects.py` (load in Sidebar Step 4):**
| File | Contents |
|------|---------|
| `ecl_parsed_predictions.csv` | Per-module: `predicted` (next build count), `target` (last-build actual), `risk_level`, `dominant_bug_type`, `leading_signal` |
| `ecl_parsed_predictions_focus_summary.txt` | Plain-English paragraph per top-risk module: what to test, which signal is driving risk, what bug type to expect |
| `ecl_parsed_predictions_leading_indicators.csv` | Pearson correlation of each lag feature vs future bug count — identifies which current signals best predict next build |
| `ecl_parsed_predictions_importance.csv` | Gradient Boosting internal feature importance ranking |

All four files are stored under `data/predictions/` by default.

**Example focus summary output:**
```
📍 Export  →  predicted 14 bugs  [Critical]
   Bug type to expect : crash/Critical (S1)
   Leading signal     : critical-bug momentum (last 3 builds)
   Testing advice     : Mandatory — add to test suite for every build.
                        Focus on crash scenarios, data loss, and any
                        recently changed functionality.
```

**Data requirements:**
- `Build#` must be numeric integers, not version strings. Non-numeric values are dropped automatically (the count is logged at startup).
- At least 20 build-module data points are needed to train the model (approximately 5+ builds per module). If there is insufficient history the script exits with a clear message — run predictions again after more builds have been captured.
- Modules with very few historical bugs receive less reliable forecasts. The `dominant_bug_type` and `leading_signal` columns will show "mixed" when history is too sparse for confident attribution.

### Step 4.6 — Enable Auto-Start on Login

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

# 1. Fetch fresh data (auto-mode: weekday → latest version only, weekend → all)
python scripts/fetch_from_n8n.py \
  --webhook-url https://your-n8n-host/webhook/82746bb5-e140-4720-98a3-d1965900274d \
  --scope auto \
  --output data/ecl_raw.json

# 2. Re-parse (updates ecl_parsed.csv and version_catalogue.csv)
python scripts/parse_ecl_export.py data/ecl_raw.json data/ecl_parsed.csv

# 3. Re-score (only needed if quadrant assignments need updating)
python scripts/compute_risk_scores.py data/ecl_parsed.csv data/risk_register_all.csv
python scripts/ai_risk_scorer.py data/risk_register_all.csv data/risk_register_scored_all.csv \
  --provider heuristic

# 4. Refresh clustering and predictions (run Friday or when patterns feel stale)
python scripts/cluster_bugs.py data/ecl_parsed.csv data/ecl_parsed_clustered.csv
python scripts/predict_defects.py data/ecl_parsed.csv data/ecl_parsed_predictions.csv

# 5. Refresh dashboard — reload the browser tab (Streamlit auto-picks up new CSVs)
```

**Weekend full-refresh timing experiment:**
On the first Saturday after setup, run with `--scope all` to measure total fetch + parse + score + cluster + predict time. This gives a benchmark for how long a full data refresh takes so you can decide whether it fits in an overnight cron job.

```bash
python scripts/fetch_from_n8n.py --scope all --then-parse
time python scripts/cluster_bugs.py data/ecl_parsed.csv data/ecl_parsed_clustered.csv
time python scripts/predict_defects.py data/ecl_parsed.csv data/ecl_parsed_predictions.csv
```

***

## Phase 6 — NLP Clustering & Prediction

### Step 6.1 — Cluster Similar Bugs

```bash
python scripts/cluster_bugs.py data/ecl_parsed.csv data/ecl_parsed_clustered.csv
```

Uses TF-IDF + K-Means globally (25 clusters) and DBSCAN per-module on the 5 busiest modules. Falls back from bigrams to unigrams if vocabulary is too sparse. With `--provider ollama` it uses Ollama semantic embeddings for meaning-aware grouping and LLM-generated plain-English cluster labels.

**Ollama mode (richer labels, recommended when RAM allows):**
```bash
python scripts/cluster_bugs.py data/ecl_parsed.csv data/ecl_parsed_clustered.csv \
  --provider ollama --model qwen3:7b
```

**How the algorithm works:**
1. Extracts `parsed_description` text from each bug.
2. Converts descriptions to TF-IDF vectors (or Ollama embeddings in `--provider ollama` mode).
3. Runs K-Means (k=25) globally to find broad themes.
4. For each of the top 5 modules by bug count, runs DBSCAN (ε=0.6, min_samples=2) for tighter within-module grouping.
5. Each cluster is named by its top 3 TF-IDF keywords (TF-IDF mode) or an LLM-generated 3–6 word label (Ollama mode).
6. Produces a per-cluster summary CSV and a full bug-level CSV with `cluster_id` and `cluster_label` columns added.

**Output files (load in Dashboard Sidebar Step 3):**
- `data/clusters/ecl_parsed_clustered.csv` — full bug-level file with `cluster_id`, `cluster_label`, and `embed_source` columns
- `data/clusters/ecl_parsed_cluster_summary.csv` — one row per cluster: keyword label, bug count, affected modules, average severity

### Step 6.2 — Predict Next Build's Defects

```bash
python scripts/predict_defects.py data/ecl_parsed.csv data/ecl_parsed_predictions.csv
```

Trains a Gradient Boosting model on historical build-module bug counts and forecasts how many bugs each module will produce in the **next build**. At startup the script prints a `PREDICTION_GUIDE` explaining exactly what the target variable is, what each feature means, and how to interpret the output — no data-science background required.

**Requirements:**
- `Build#` must be numeric integers. Version strings (e.g. "16.3.5") are dropped automatically; the count dropped is printed at startup.
- At least 20 build-module data points are needed (≈5+ builds per module with bugs).

**Features used by the model:**
| Feature | Plain-English meaning |
|---------|-----------------------|
| `bugs_lag1` | Bug count in the most recent build |
| `bugs_lag3` | Bug count averaged over the last 3 builds |
| `bugs_lag5` | Bug count averaged over the last 5 builds |
| `crit_lag3` | Critical bug count over the last 3 builds |
| `trend_3` | Rate of change (slope) over the last 3 builds |
| `sev_weighted_lag3` | Severity-weighted bug total over the last 3 builds |

**Output files (load in Dashboard Sidebar Step 4):**

| File | Contains |
|------|----------|
| `ecl_parsed_predictions.csv` | Per-module: `predicted` (next build), `target` (last build actual), `risk_level`, `dominant_bug_type`, `leading_signal` |
| `ecl_parsed_predictions_focus_summary.txt` | Plain-English paragraph per top-risk module: what to test, why risk is high, what bug type to expect |
| `ecl_parsed_predictions_leading_indicators.csv` | Pearson correlation of each feature vs future bug count — shows which current signals best predict next build |
| `ecl_parsed_predictions_importance.csv` | Model feature importances (Gradient Boosting internal ranking) |

All four files are stored under `data/predictions/` by default.

**Example focus summary output:**
```
📍 Export  →  predicted 14 bugs  [Critical]
   Bug type to expect : crash/Critical (S1)
   Leading signal     : critical-bug momentum (last 3 builds)
   Testing advice     : Mandatory — add to test suite for every build.
                        Focus on crash scenarios, data loss, and any
                        recently changed functionality.
```

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

```
Setup → P1 (Critical) → P2 (High) → P3 (Medium) → P4 (Low) → Visual Regression
```

Use the `TEST_SCOPE` parameter to control which stages run:
- `p1` — smoke tests only (fastest, ~5 min)
- `p1,p2` — critical + high (~15 min)
- `p1,p2,p3,p4` — full suite
- `visual` — visual regression only

### Step 8.3 — Allure Reporting

Results are published to Allure automatically. Each test shows:
- **Suite**: Module Category (e.g. `AI Features`)
- **Sub-suite**: Module name (e.g. `AI Storytelling`)
- **Tag**: Priority (`P1`, `P2`, `P3`, `P4`)

***

## Phase 9 — Ongoing Maintenance

### Weekly Cadence

| Day | Task | Time |
|-----|------|------|
| Monday | Fetch fresh ECL data (`--scope auto`) → parse + risk score | 20 min |
| Monday | Review pending mappings in `module_mappings/versions/` | 10 min |
| Monday | Review dashboard for new hotspots (Tabs 1, 6, 7) | 15 min |
| Wednesday | Review Allure report, update failing tests | 1 hr |
| Friday | Re-run clustering + predictions, check Tab 8 and Tab 9 for new patterns | 15 min |
| Friday | Review `_focus_summary.txt` with team — confirm next build test focus | 10 min |

### Monthly Cadence

- Re-run `ai_risk_scorer.py` with latest data (priority assignments may shift)
- Run `auto_tag_tests.py --tag-existing tests/` to update markers on existing tests
- Review `data/quadrant_summary.md` with the team — validate P1/P2 modules are still correct
- Compare this month's `_leading_indicators.csv` against last month — check whether the predictive signals are shifting
- Promote confirmed pending mappings (they auto-promote to `mappings_global.json` on next parse run)

### When New Modules Are Added

1. Re-run the parser — new modules are caught by fuzzy matching automatically
2. Review `*_pending.json` for the new module's suggestions and confirm
3. If the new module is a high-impact feature, add it to `IMPACT_OVERRIDES` in `ai_risk_scorer.py`
4. Re-run the full pipeline (Steps 2.2 → 3.2 → 3.3 → 6.1 → 6.2)

**You no longer need to manually add new modules to `MODULE_ALIASES` or `MODULE_CATEGORIES`** unless the module name is structurally unparseable (leading/trailing punctuation, pure acronym with no word overlap).

***

## Quick Reference — Full Pipeline (One Command)

**Via n8n webhook (Path A):**
```bash
source .venv/bin/activate && \
python scripts/fetch_from_n8n.py \
  --scope all --then-parse && \
python scripts/compute_risk_scores.py data/ecl_parsed.csv data/risk_register_all.csv && \
ollama serve &>/dev/null & sleep 5 && \
python scripts/ai_risk_scorer.py data/risk_register_all.csv data/risk_register_scored_all.csv --provider ollama --model llama3.1 && \
python scripts/auto_tag_tests.py data/risk_register_scored_all.csv \
  --generate-skeletons tests/generated/ --summary && \
python scripts/cluster_bugs.py data/ecl_parsed.csv data/ecl_parsed_clustered.csv \
  --provider ollama --model llama3.1 && \
python scripts/predict_defects.py data/ecl_parsed.csv data/ecl_parsed_predictions.csv \
  --provider ollama --model llama3.1 && \
pkill -f streamlit && \
streamlit run scripts/bug_heatmap_dashboard.py --server.address 0.0.0.0 --server.port 8501
```

**Via manual Excel export (Path B):**
```bash
source .venv/bin/activate && \
python scripts/parse_ecl_export.py data/ecl_export.xlsx data/ecl_parsed.csv && \
python scripts/compute_risk_scores.py data/ecl_parsed.csv data/risk_register_all.csv && \
ollama serve &>/dev/null & sleep 5 && \
python scripts/ai_risk_scorer.py data/risk_register_all.csv data/risk_register_scored_all.csv --provider ollama --model llama3.1 && \
python scripts/auto_tag_tests.py data/risk_register_scored_all.csv \
  --generate-skeletons tests/generated/ --summary && \
python scripts/cluster_bugs.py data/ecl_parsed.csv data/ecl_parsed_clustered.csv && \
python scripts/predict_defects.py data/ecl_parsed.csv data/ecl_parsed_predictions.csv && \
pkill -f streamlit && \
streamlit run scripts/bug_heatmap_dashboard.py --server.address 0.0.0.0 --server.port 8501
```

**Dashboard sidebar setup after launch:**
- **Step 1** → `data/ecl_parsed.csv`
- **Step 2** → `data/risk_register_scored_all.csv`
- **Step 3** → `data/ecl_parsed_clustered.csv` + `ecl_parsed_cluster_summary.csv`
- **Step 4** → `data/ecl_parsed_predictions.csv` + `_focus_summary.txt` + `_leading_indicators.csv`

***

## Troubleshooting

| Issue | Cause | Fix |
|-------|-------|-----|
| Dashboard: "Wrong file!" on ecl_parsed.csv | File is risk_register_scored_all.csv (lacks `severity_num`) | Use `ecl_parsed.csv` from Step 2.2 |
| Dashboard: Tab 7 shows warning, not treemap | Risk data not loaded in sidebar Step 2 | Set path to `risk_register_scored_all.csv` in sidebar |
| Dashboard: Tab 8 shows setup prompt | Cluster files not loaded | Run `cluster_bugs.py` then set path in sidebar Step 3 |
| Dashboard: Tab 9 shows setup prompt | Prediction files not loaded | Run `predict_defects.py` then set path in sidebar Step 4 |
| Dashboard: Tab 9 "Missing columns" error | Predictions file is from `predict_defects.py` v2.1 (lacks new columns) | Re-run with `predict_defects.py` v2.2 |
| Dashboard: "No per-version score file" caption | Per-version scoring not yet run | Run `ai_risk_scorer.py` — it auto-scores all files in `risk_register_versions/` |
| Dashboard: clicking a module block shows nothing | Module name matches its parent category name — fixed in v2.14 | Ensure `bug_heatmap_dashboard.py` v2.14+ is in use |
| Dashboard: version picker shows "9.0" before "16.3.0" | Using old dashboard without version catalogue | Update to v2.15+ and re-run `parse_ecl_export.py` to generate `version_catalogue.csv` |
| Dashboard: typo version appears in default selection | Sparse versions not filtered | Re-run parser — version catalogue flags versions with < 5 bugs; dashboard excludes them from defaults |
| Dashboard: heatmap cells are red/orange, clashing with [P1] badges | Using dashboard older than v2.15 | Update to v2.15+ — heatmap cells now use a blue gradient |
| Port 8501 not available | Previous Streamlit instance still running | `lsof -i :8501` then `kill -9 <PID>` |
| Parse rate below 90% | Non-standard Short Description format | Check `*_pending.json` files; confirm suggestions |
| 1000+ uncategorized modules | Old parser without auto-strip | Replace with v2.3+, delete `module_mappings/versions/*_pending.json`, re-run |
| `predict_defects.py` drops many rows | `Build#` contains version strings not plain integers | Expected — script logs the count. Use numeric build numbers for better accuracy. |
| `predict_defects.py` "Not enough data" | Fewer than 20 build-module data points | Need at least ~5 builds per module. Run predictions after more build history is available. |
| `cluster_bugs.py` not enough terms | Module has very few unique bug descriptions | Expected — script handles gracefully, produces "Unclustered" label |
| Ollama timeout errors | Model too large for available RAM | Switch to `phi3` or use `--provider heuristic` |
| Scorer stops mid-run | Timeout / crash | Re-run same command — resume support skips already-scored modules automatically |
| Visual regression too sensitive | Minor font rendering differences | Raise threshold: `export VISUAL_THRESHOLD=0.92` |
| Appium can't find device | `DEVICE_NAME` mismatch | Run `idevice_id -l` and use exact device UUID |
| `fetch_from_n8n.py`: connection error | n8n not running or wrong URL | Check n8n is active; verify webhook URL matches the imported workflow |
| `fetch_from_n8n.py`: missing required fields | n8n `Get Columns_v3` Set node misconfigured | Re-import `Dashboard_Query_eBug_List_v3.json`; script lists which fields are absent |
| `fetch_from_n8n.py`: empty response | n8n date range filter returns no bugs | Widen the `CreateTime` range in the eCL eBug query node |
| `fetch_from_n8n.py`: `_status_changed` all False | First-ever run (no existing cache to compare against) | Expected — flags will populate on second+ runs |
| `repro_rate` all 0.5 after JSON parse | `Reproduce Probability` not in the eCL API | Expected — 0.5 is the correct default; Detectability scoring is unaffected |
| `version_catalogue.csv` not found | Parser not yet re-run after v2.6 update | Re-run `parse_ecl_export.py` — catalogue is generated automatically |
| `parsed_version` shows wrong version | Parser is pre-v2.6 (reads from Short Description) | Update to v2.6+ parser; new version reads from the `Version` column directly |