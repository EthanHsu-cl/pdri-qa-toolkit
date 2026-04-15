# Changelog

## What Changed in v3.2

### Status-Weighted Defect Predictions

Bug status is now factored into the prediction model rather than treating all bugs equally.

| Status | Weight | Reason |
|--------|--------|--------|
| Open / Active | 1.0 | Full signal — unresolved, ongoing risk |
| Closed / Fixed (`close`, `hqqa close`, `fae close`) | 0.5 | Real bug that happened, but resolved — partial signal |
| Invalid (`nab`, `won't fix`, `not reproducible`, `new feature`, `external issue`, etc.) | 0.0 | Not a confirmed defect — excluded entirely |

**What this affects:**

| File | Change |
|------|--------|
| `scripts/parse_ecl_export.py` | `INACTIVE_STATUSES` split into `CLOSED_STATUSES` and `INVALID_STATUSES`. New `classify_status_weight()` function. New `status_weight` column (`1.0` / `0.5` / `0.0`) added to all parsed CSVs alongside the existing `status_active`. |
| `scripts/predict_defects.py` | `build_features()` now reads `status_weight`: drops invalid bugs before aggregation, then scales `bug_count`, `sev_w`, `crit`, and `major` by status weight so closed bugs contribute at half intensity. Falls back to treating all bugs as weight 1.0 if the column is absent (old CSV compatibility). |

**To apply:** rerun the pipeline from stage 2 (`parse_ecl_export.py`) onwards so the new `status_weight` column is present in the parsed CSV before predictions are built:

```bash
./refresh_pipeline.sh
# or, to skip the slower stages:
./refresh_pipeline.sh --skip-ollama --skip-cluster
```

### Dashboard — Severity Escalation Alerts

| Area | Change |
|------|--------|
| **Sort order** | Escalation alerts now sort by risk level first (Critical → High → Medium), then by escalation speed within each group. Previously sorted by escalation speed only, which could bury a worsening Critical-risk module below a Medium-risk one. |
| **"Overdue for a critical bug" section removed** | This section flagged modules that had produced S1 bugs historically but had been quiet recently. Removed — historical S1s don't reliably predict future ones, and the severity escalation trend and predicted counts already surface modules that need attention now. |
| **"builds" → "versions"** | All user-visible text (alert captions, chart labels, metric labels, column headers, feature importance chart) now uses "version" (e.g. 16.4.0) instead of "build". |

### Pipeline Cron Schedule — Sunday Removed

The weekend full-refresh (all products, 36-month window) now runs on **Saturday only**. Sunday is skipped — no new bugs are filed over the weekend, and the full run can take up to a day, so Saturday is sufficient.

---

## What Changed in v3.1

### Multi-Product: Vivid Glam (iOS) added (`vvg`)

| File | Change |
|------|--------|
| `scripts/fetch_from_n8n.py` | Added `"vvg": "Vivid Glam (iOS)"` to `PRODUCT_MAP`. The `--product vvg` flag is now a valid choice and the webhook payload will send `product_name = "Vivid Glam (iOS)"` to the n8n workflow. |
| `scripts/bug_heatmap_dashboard.py` | `vvg` was already present in `PRODUCT_MAP`. Updated `_PRODUCT_ORDER` to insert `"phd"` between `"pdr"` and `"vvg"`, so the product selector order is: `pdri → pdra → phdi → phda → pdr → phd → vvg → promeo`. |
| `refresh_pipeline.sh` | Added `vvg:36` to `WEEKEND_PRODUCTS`. Vivid Glam (iOS) will now run in the weekend full-refresh schedule (36-month data window) alongside all other products. It is not included in the weekday schedule (`WEEKDAY_PRODUCTS`). To run it on demand: `./refresh_pipeline.sh --products vvg`. |

---

## What Changed in v3.0

### `cluster_bugs.py` → v3.0

| Area | Change |
|------|--------|
| **Severity stratification** | `--stratify-severity` flag splits clustering into two independent passes: S1/S2 (Critical/Major) and S3/S4 (Normal/Minor). Produces separate summary files `_cluster_summary_s12.csv` and `_cluster_summary_s34.csv` so you can investigate crash-class bugs independently of cosmetic ones. |
| **`cluster_velocity_ratio`** | Added to cluster summary. Ratio of bug count in the most recent 3 versions vs the prior 3 versions per cluster. Values above 1.5 = theme accelerating (prioritise in testing); below 0.67 = theme declining (fixes may be holding). |
| **`cluster_trend`** | `"growing"` / `"stable"` / `"declining"` label derived from velocity ratio. Quick filter: sort summary by `cluster_trend == "growing"` before the weekly team meeting. |
| **`recurrence_rate`** | Fraction of recent bugs in a cluster whose source module also contributed the same cluster in the prior version window. Above 0.5 = root cause not being fixed — escalate to RD. |
| **`cluster_entropy` (per module)** | New `*_module_entropy.csv` output. Shannon entropy of a module's cluster distribution. Below 1.0 = all bugs in one theme (easy to target). Above 2.0 = bugs everywhere (needs comprehensive coverage). |
| **New output files** | `*_cluster_summary_s12.csv`, `*_cluster_summary_s34.csv` (with `--stratify-severity`); `*_module_entropy.csv` (always). |

### `predict_defects.py` → v3.0

| Area | Change |
|------|--------|
| **`--cluster-csv` flag** | Auto-detected from default path if present. Feeds `cluster_entropy_3`, `cluster_entropy_5`, and `top_cluster_velocity` features into the model. |
| **New features** | `severity_escalation` (mean severity delta — catches modules about to hit a critical before counts spike); `builds_since_last_crit` (flags modules historically prone to S1s that have been quiet — potentially overdue); `cluster_entropy_3/_5` (rising diversity of bug themes = new *kinds* of failures); `top_cluster_velocity` (growth rate of dominant theme — early-warning for theme-specific regressions). |
| **`_predictions_by_cluster.csv`** | New output. Per-module, per-theme predicted counts: `module, cluster_id, cluster_label, historical_pct, predicted_count`. Tells you not just how many bugs to expect, but what *type*. |
| **`--provider claude`** | New provider option for cluster-aware narratives via the Claude API (in addition to existing `ollama`). |

### `auto_tag_tests.py` → v3.0

| Area | Change |
|------|--------|
| **`--cluster-predictions` flag** | Accepts `*_predictions_by_cluster.csv`. When provided, each generated `test_<module>.py` now contains one `test_<theme_name>()` method per predicted bug theme, with expected count in the TODO comment and Allure severity inferred from cluster label keywords. |
| **`--cluster-plan` flag** | Writes `data/cluster_test_plan.md` — a scenario-level test plan for P1/P2 modules. One table per module listing each predicted theme, its expected bug count, and a one-sentence test scenario description auto-generated from cluster label keywords. |
| **`quadrant_summary.md`** | P1/P2 rows now include a "Predicted bug themes" column showing the top 2 expected cluster labels when `--cluster-predictions` is supplied. |

### Dashboard (`bug_heatmap_dashboard.py` → v3.0)

| Area | Change |
|------|--------|
| **Sidebar Step 3 — Module entropy field** | Optional "Module entropy CSV" path field added. Stratified summaries (`_s12.csv`, `_s34.csv`) load automatically from their default locations when the base cluster files are loaded. |
| **Sidebar Step 4 — Bug-type predictions field** | Optional "Bug-type predictions CSV" path field added (`_by_cluster.csv`). |
| **Tab 8 — Bug Clusters (new features)** | Velocity chart (cluster trend over time), module entropy chart, stratified S1/S2 vs S3/S4 tabs — all shown when the corresponding files are loaded. |
| **Tab 9 — Defect Forecast (new feature)** | Module forecast cards now show a per-theme breakdown under "What to expect" (e.g. "~3 bugs — login crash / timeout") when `_by_cluster.csv` is loaded. |

All v3.0 changes are strictly additive — every existing command from v2.6 works without modification. See [Backward compatibility](README.md#backward-compatibility) for a full compatibility table.

---

## What Changed in v2.6

### Dashboard (`bug_heatmap_dashboard.py` → v2.16)

| Area | Change |
|------|--------|
| **Tab 8 — 🔬 Bug Clusters** | New tab. Loads `ecl_parsed_clustered.csv` + `ecl_parsed_cluster_summary.csv` (Sidebar Step 3). Shows headline metrics (themes found, bugs grouped), a colour-coded bar chart of all themes by size and severity, and expandable per-theme detail cards with sample bug descriptions and a plain-English action line. Designed to be readable by anyone on the team, not just QA. The **📖 How to read this tab** expander is a full reference guide covering: headline metric meanings, colour-coding key, bar chart interpretation (length vs colour), how to investigate a pattern (isolated module vs shared infrastructure), when to escalate, and the re-run cadence recommendation. |
| **Tab 9 — 🔮 Defect Forecast** | New tab. Loads `ecl_parsed_predictions.csv` + `_focus_summary.txt` + `_leading_indicators.csv` (Sidebar Step 4). Shows forecast bug counts per module for the next version, an actual-vs-predicted comparison chart, per-module "what to test" cards in plain English, and a leading-indicators chart explaining which current signals best predict future bugs. The **📖 How to read this tab** expander is a full reference guide covering: the prediction target definition, all six model features in plain English, risk level thresholds with testing cadence, all headline metric meanings, module card field explanations (dominant_bug_type, leading_signal), actual-vs-predicted comparison guidance, leading indicators chart interpretation, and data/model limitations. |
| **Sidebar Steps 3 & 4** | Two new optional data loaders added below the existing risk score loader. Step 3 = cluster files, Step 4 = prediction files. Neither is required; the new tabs show a clear setup prompt when files are missing. |
| **Heatmap colour conflict** | Module × Severity heatmap cells now use a **blue gradient** (`HEATMAP_COLOR_SCALE = "Blues"`) instead of red/orange/yellow, so cell shading never visually clashes with the `[P1]`/`[P2]` priority badge text on the same rows. A caption explains the two-channel encoding. |
| **Heatmap sort order** | Module × Severity heatmap now sorts rows by **total weighted count** across all severities, so the busiest modules always float to the top. Previously sorted by Critical count only, which buried modules with many Major/Normal bugs. |
| **Scatter plot overlap** | Risk Score vs Probability scatter now adds ±0.35 random x-jitter so dots at the same integer probability level separate visually. `size_max` reduced 30 → 18, `opacity` set to 0.75. X-axis tick labels remain pinned to integers 1–5. A caption explains the jitter. |

### `predict_defects.py` → v2.2

| Area | Change |
|------|--------|
| **Target clarification** | A `PREDICTION_GUIDE` box printed at startup explains exactly what `bug_count` (the target) means, what every feature column represents, what `target` vs `predicted` means, and what each risk level threshold is. A plain-English legend is printed before the results table. |
| **`dominant_bug_type`** | Each predicted module now carries its most common historical severity (e.g. "crash/Critical (S1)", "Major functional (S2)") so readers know *what kind* of bugs to expect, not just how many. |
| **Leading indicators** | `compute_leading_indicators()` computes Pearson correlation of each lag feature against the next-version target. Identifies which current bug signals (e.g. "critical-bug momentum in last 3 versions") are most strongly predictive of future issues. Saved to `_leading_indicators.csv`. |
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
