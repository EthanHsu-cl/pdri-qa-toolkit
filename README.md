# PDR-I QA Toolkit v2.2

Risk-based testing + bug heatmap pipeline for PDR-I (PowerDirector iOS) mobile QA.

## What's New in v2.2

| File | Change |
|------|--------|
| `scripts/parse_ecl_export.py` | +120 MODULE_ALIASES, +200 flat category overrides. Near-zero uncategorized modules |
| `scripts/bug_heatmap_dashboard.py` | **Dual-input**: loads `ecl_parsed.csv` (bug-level) AND `risk_register_scored.csv` (LLM scores). All 7 tabs now risk-enriched |
| Tab 7: Risk Heatmap | Treemap/sunburst. Color by: Risk Score (LLM) / Quadrant / Critical Count / Severity Weight |
| v2.1 bug: wrong upload message | Fixed — correct file guidance per tab |

## Quick Start

```bash
chmod +x setup_mac_mini.sh && ./setup_mac_mini.sh
```

## Full Demo (5 commands)

```bash
# 1. Parse ECL export
python scripts/parse_ecl_export.py data/ecl_export.xlsx data/ecl_parsed.csv

# 2. Compute module metrics
python scripts/compute_risk_scores.py data/ecl_parsed.csv data/risk_register.csv

# 3. Score with local LLM (heuristic = instant, ollama = Qwen3/phi3)
python scripts/ai_risk_scorer.py data/risk_register.csv data/risk_register_scored.csv --provider heuristic

# 4. Generate test skeletons + management summary
python scripts/auto_tag_tests.py data/risk_register_scored.csv --generate-skeletons tests/generated/ --summary

# 5. Launch dashboard
streamlit run scripts/bug_heatmap_dashboard.py --server.address 0.0.0.0 --server.port 8501
```

## Dashboard Usage

1. **Step 1 (required)**: Upload `data/ecl_parsed.csv`  
2. **Step 2 (optional but recommended)**: Upload `data/risk_register_scored.csv`  
3. All 7 tabs update. Tab 7 (Risk Heatmap) unlocks with risk data.

## File Inventory

| # | File | Purpose |
|---|------|---------|
| 1 | `scripts/parse_ecl_export.py` | Parse ECL Excel → enriched CSV (module, tags, severity) |
| 2 | `scripts/compute_risk_scores.py` | Aggregate per-module risk metrics → risk register |
| 3 | `scripts/ai_risk_scorer.py` | Score Impact + Detectability via heuristic/Ollama/OpenAI |
| 4 | `scripts/auto_tag_tests.py` | Generate pytest skeletons with Q1–Q4 markers |
| 5 | `scripts/bug_heatmap_dashboard.py` | Streamlit dashboard — 7 tabs, dual-input |
| 6 | `scripts/cluster_bugs.py` | NLP defect clustering (TF-IDF + K-Means/DBSCAN) |
| 7 | `scripts/predict_defects.py` | ML defect prediction (Gradient Boosting) |
| 8 | `scripts/visual_regression.py` | Screenshot comparison engine |
| 9 | `Jenkinsfile` | CI/CD pipeline (Q4→Q3→Q2→Q1 + Allure) |
| 10 | `tests/conftest.py` | Appium + visual regression pytest fixtures |
| 11 | `pytest.ini` | Marker registration (q1–q4, visual) |
| 12 | `requirements.txt` | Python dependencies |
| 13 | `tests/test_ai_storytelling.py` | Example test with Q4 markers |
| 14 | `setup_mac_mini.sh` | One-command M1 setup + LaunchAgent |

## Mac Mini M1 RAM Notes

| Mode | RAM | Notes |
|------|-----|-------|
| Dashboard + pipeline (no Ollama) | ~2.2 GB | Fine on 8 GB |
| + Ollama phi3 | ~4.5 GB | Recommended for 8 GB |
| + Ollama qwen3:7b | ~7 GB | Needs 8 GB, close to limit |
| + Appium running | +1 GB | Use heuristic if 8 GB |
