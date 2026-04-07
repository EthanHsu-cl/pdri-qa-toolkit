#!/usr/bin/env python3
"""QA Bug Dashboard v3.0

Changes from v2.21:
  - Renamed from "PDR-I QA Dashboard" to "QA Bug Dashboard" for multi-product.
  - Added product selector dropdown in sidebar (scans data/products/ for
    available product directories containing ecl_parsed.csv).
  - All data paths are now dynamic based on selected product slug.
  - Falls back to legacy data/ paths when no product directories exist.
  - Version bumped to v3.0.

Changes from v2.20:
  - Tab 9 refactored to scenario-based bug forecasting layout.
    Primary view now shows "What to Test Next Build" with concrete bug
    scenario predictions per module, grouped by risk tier.
  - New "Predicted Bug Scenarios by Module" section with per-module
    expanders showing scenario text, confidence badges, and source examples.
  - Module forecast cards enhanced with top predicted scenarios.
  - Count-based sections (Predicted Bug Count chart, Actual vs Predicted,
    Module Signals Table, Full predictions table) demoted into a single
    collapsed "Advanced / Model Diagnostics" expander at the bottom.
  - Bug Categories stacked bar chart promoted to higher position.
  - Loads new _predictions_by_scenario.csv (v5.0) from predict_defects.py.
  - Headline metrics replace MAE with scenario count.

Changes from v2.19:

Changes from v2.18:
  - Sidebar Steps 1–4 consolidated into a single "📁 Data Sources" expander.
    Previously four separate expanders; now one panel with all four steps
    separated by st.divider() lines inside it.  The expander label shows a
    live badge for each dataset (e.g. "✅ bugs · ✅ risk · ✅ clusters · ✅
    forecast") so status is readable from the collapsed state.
    Expander auto-opens only when the required bug data file is not found.

Changes from v2.17:
  - Tab 9 forecast bar chart: fixed "High" risk bars disappearing from chart
    when the visible top-N slice contains no High-risk modules initially.
    Root cause: pd.Categorical with ordered=True caused Plotly to silently
    drop absent categories from both bars and legend.
    Fix: risk_level cast to plain str; sort order driven by a _risk_rank
    helper column instead; invisible zero-height add_bar() traces inserted for
    any of the four risk levels absent from the current slice, keeping all four
    legend colours always visible.

Changes from v2.16:
  - Sidebar Steps 1–4 are now collapsible expanders (collapsed by default).
    Each expander header shows a live ✅ badge when its default file path
    exists on disk, so you can confirm all data is loaded at a glance without
    opening any panel.  Step 1 auto-expands on first run (when the default
    path doesn't exist yet) and collapses itself once the file is found.
  - Compact data-status strip added below the expanders, always visible:
    shows bug count and which optional datasets are active
    (e.g. "📊 9,654 bugs  ·  🔥 risk  ·  🔬 clusters  ·  🔮 forecast").
  - Removed the sidebar success/info boxes that duplicated information now
    shown by the status strip and expander badges.
  - All sidebar widget calls inside expanders now use st.* instead of
    st.sidebar.* (Streamlit renders them in the correct context automatically).

Changes from v2.15:
  - Tab 8 (Bug Clusters): Expanded "📖 How to read this tab" expander into a
    full reference guide covering headline metrics, colour coding, bar chart
    interpretation, theme detail cards, investigation workflow, Ollama vs
    TF-IDF mode explanation, and re-run cadence guidance.
  - Tab 9 (Defect Forecast): Expanded "📖 How to read this tab" expander into
    a full reference guide covering the prediction target, all six model
    features in plain English, risk level thresholds, headline metric meanings,
    module card fields (dominant_bug_type, leading_signal), actual-vs-predicted
    comparison guidance, leading indicators chart interpretation, model
    limitations, and data requirements.

Changes from v2.14:
  - Heatmap color scale: Module×Severity cells now use a blue gradient
    (HEATMAP_COLOR_SCALE) so continuous cell shading never clashes with the
    red/orange/yellow P1/P2 priority badge text on the same rows.
  - Module×Severity heatmap now sorted by total weighted count (all severities
    combined) so the busiest modules always float to the top.
  - Risk Score vs Probability scatter: added ±0.35 x-jitter, reduced bubble
    size_max from 30→18, added 0.75 opacity so overlapping dots are legible.
  - predict_defects.py companion: target variable documented, leading-indicator
    correlation added, plain-English focus summary generated per top-risk module.
  - fetch_from_n8n.py: status comparison now flags changed records with
    _status_changed=True; weekday/weekend scope auto-scheduling added.
  - parse_ecl_export.py: parsed_version now read from the dedicated Version
    column rather than regex-scraped from Short Description.

Changes from v2.13:
  - Reverted responsive layout — always uses left/right [6,4] split.
  - Removed JS width-detection snippet (components.html import gone).
  - Removed wide_layout / vertical logic and render_detail_panel helper.

Changes from v2.12:
  - dynamic treemap height (min 700, max 1100, scales with module count).
  - right panel scrollable div capped at treemap height.

Usage:
  streamlit run scripts/bug_heatmap_dashboard.py \\
    --server.address 0.0.0.0 --server.port 8501
"""
import os
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from pathlib import Path


st.set_page_config(page_title="QA Bug Dashboard", layout="wide", page_icon="🔥")
st.sidebar.title("🔥 QA Bug Dashboard v3.0")

# ── Product selector ─────────────────────────────────────────────────────
PRODUCT_MAP = {
    "pdri":   "PowerDirector Mobile (iOS)",
    "phdi":   "PhotoDirector Mobile (iOS)",
    "pdra":   "PowerDirector Mobile (Android)",
    "phda":   "PhotoDirector Mobile (Android)",
    "pdr":    "PowerDirector",
    "phd":    "PhotoDirector",
    "promeo": "Promeo",
}

_products_dir = Path("data/products")
_available_products = []
if _products_dir.exists():
    for d in sorted(_products_dir.iterdir()):
        if d.is_dir() and (d / "ecl_parsed.csv").exists():
            slug = d.name
            label = PRODUCT_MAP.get(slug, slug)
            _available_products.append((slug, label))

if _available_products:
    _product_labels = [f"{label} ({slug})" for slug, label in _available_products]
    _selected_idx = st.sidebar.selectbox(
        "Product",
        range(len(_product_labels)),
        format_func=lambda i: _product_labels[i],
        key="product_selector",
    )
    selected_slug = _available_products[_selected_idx][0]
    st.session_state["selected_product"] = selected_slug
    _product_dir = f"data/products/{selected_slug}"
else:
    # Legacy fallback: no product directories, use flat data/
    selected_slug = None
    _product_dir = "data"

# Reset product-scoped widget/session state when switching products.
# Streamlit widget keys keep their prior values across reruns, so a changed
# default path alone will not update text_input/radio values unless we clear
# the old keys first.
_product_state_key = selected_slug or "__legacy__"
_prev_product_state_key = st.session_state.get("_last_product_state_key")
if _prev_product_state_key != _product_state_key:
    _prev_slug = None if _prev_product_state_key == "__legacy__" else _prev_product_state_key
    _new_slug = None if _product_state_key == "__legacy__" else _product_state_key
    for _k in [
        "ds_bugs", "up_bugs", "fp_bugs",
        "ds_risk", "fp_risk",
        "ds_cluster", "fp_cluster", "fp_cluster_sum", "fp_cluster_ent",
        "ds_pred", "fp_pred", "fp_pred_sum", "fp_pred_li",
        "fp_pred_cluster", "fp_pred_category", "fp_pred_scenario",
        "version_multiselect",
    ]:
        st.session_state.pop(_k, None)
    st.session_state["_product_switched_notice"] = (
        f"Product changed: {(_prev_slug or 'legacy data')} -> {(_new_slug or 'legacy data')}. "
        "Data source paths were reset to this product's defaults."
    )
    st.session_state["_last_product_state_key"] = _product_state_key

if st.session_state.get("_product_switched_notice"):
    st.toast(st.session_state.pop("_product_switched_notice"), icon="🔄")


# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------

ECL_BUG_URL = "https://ecl.cyberlink.com/Ebug/eBugHandle/HandleMainEbug2.asp?BugCode={bug_code}"

QUADRANT_COLORS = {
    "P1 - Critical": "#ef4444",
    "P2 - High":     "#f97316",
    "P3 - Medium":   "#eab308",
    "P4 - Low":      "#22c55e",
}

# Separate color scale for continuous heatmap cells (severity-weighted counts).
# Uses blue→purple so it never clashes with the red/orange/yellow priority badge
# colors that appear as text labels on the same chart rows.
HEATMAP_COLOR_SCALE = "Blues"

PRIORITY_LABEL_MAP = {
    1: "1-Fix Now",
    2: "2-Must Fix",
    3: "3-Better Fix",
    4: "4-No Matter",
    5: "5-N/A",
}
PRIORITY_ORDER = ["1-Fix Now", "2-Must Fix", "3-Better Fix", "4-No Matter", "5-N/A"]

BARE_TO_FULL_PRIORITY = {
    "Fix Now":    "1-Fix Now",
    "Must Fix":   "2-Must Fix",
    "Better Fix": "3-Better Fix",
    "No Matter":  "4-No Matter",
    "N/A":        "5-N/A",
}

INACTIVE_STATUSES = {
    "close", "need more info", "nab", "propose nab",
    "wont fix", "won't fix", "propose wont fix", "qa propose wont fix",
    "not reproducible", "notreproducible", "not a bug",
    "new feature", "external issue", "hqqa close", "fae close",
}

TAB_NAMES = [
    "🗺️ Module × Severity",
    "📅 Version Timeline",
    "🏷️ Tag Analysis",
    "⚖️ P/S Alignment",
    "👥 Team Coverage",
    "📊 KPI Dashboard",
    "🔥 Risk Heatmap",
    "🔬 Bug Clusters",
    "🔮 Defect Forecast",
]

SEV_OPTIONS = ["All", "1-Critical", "2-Major", "3-Normal", "4-Minor"]
SEV_NUM_MAP  = {"1-Critical": 1, "2-Major": 2, "3-Normal": 3, "4-Minor": 4}


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def is_active(status_val) -> bool:
    if pd.isna(status_val):
        return True
    return str(status_val).strip().lower() not in INACTIVE_STATUSES


def _file_mtime(fp: str) -> float:
    """Return file mtime for cache-busting; 0 if missing."""
    try:
        return os.path.getmtime(fp)
    except OSError:
        return 0.0


@st.cache_data
def _load_csv_cached(fp: str, _mtime: float) -> pd.DataFrame:
    df = pd.read_csv(fp, low_memory=False)
    for dc in ["Create Date", "Closed Date"]:
        if dc in df.columns:
            df[dc] = pd.to_datetime(df[dc], errors="coerce")
    return df


def load_csv(fp: str) -> pd.DataFrame:
    """Load CSV with automatic cache-busting when the file changes on disk."""
    return _load_csv_cached(fp, _file_mtime(fp))


def normalise_module(name: str) -> str:
    """Strip parenthetical sub-variants from module names.
    e.g. "Auto Edit(Pet 02)" -> "Auto Edit"
         "AI Storytelling (Describe your clips)" -> "AI Storytelling"
    Also collapses multiple spaces and strips whitespace.
    """
    import re
    if not isinstance(name, str):
        return name
    # Remove anything in parentheses (including the parens)
    name = re.sub(r'\s*\([^)]*\)', '', name)
    # Collapse multiple spaces
    name = re.sub(r' +', ' ', name).strip()
    return name


def render_bug_table(frame: pd.DataFrame) -> None:
    display_cols_wanted = [
        "BugCode", "Short Description", "Status", "severity_label",
        "priority_label", "parsed_version", "Creator", "parsed_module",
    ]
    display_cols = [c for c in display_cols_wanted if c in frame.columns]
    disp = frame[display_cols].copy()
    bug_code_col = next((c for c in frame.columns if "bugcode" in c.lower()), None)
    if bug_code_col and bug_code_col in disp.columns:
        # Replace BugCode cell values with the full ECL URL.
        # display_text regex extracts the BugCode from the URL so the cell
        # shows e.g. "DRI261841-0001" but is clickable and opens ECL.
        disp[bug_code_col] = frame[bug_code_col].apply(
            lambda x: ECL_BUG_URL.format(bug_code=x) if pd.notna(x) else ""
        )
        st.dataframe(
            disp,
            column_config={
                bug_code_col: st.column_config.LinkColumn(
                    "BugCode",
                    display_text=r"BugCode=([^&]+)",
                )
            },
            width='stretch',
            hide_index=True,
        )
    else:
        st.dataframe(disp, width='stretch', hide_index=True)


# ---------------------------------------------------------------------
# Session state bootstrap
# ---------------------------------------------------------------------

if "tm_selected_module"   not in st.session_state:
    st.session_state["tm_selected_module"]   = None
if "tm_selected_category" not in st.session_state:
    st.session_state["tm_selected_category"] = None


# ---------------------------------------------------------------------
# Sidebar – Navigation
# ---------------------------------------------------------------------

st.sidebar.subheader("📑 Navigation")
active_tab = st.sidebar.radio(
    "View", TAB_NAMES, key="active_tab", label_visibility="collapsed",
)


# ---------------------------------------------------------------------
# Sidebar – load data  (Steps 1–4 inside one collapsible expander)
# ---------------------------------------------------------------------
# All four data-loading steps live inside a single expander so the sidebar
# stays compact once paths are configured.  The expander label shows a live
# status badge so you know what is loaded without opening it.
# Step 1 is the only blocking load; Steps 2–4 are all optional.
#
# NOTE: derived-field computation (severity_weight, priority_label, etc.)
# runs OUTSIDE the expander block — those lines produce no sidebar widgets
# and must execute unconditionally regardless of whether the expander is open.
# ---------------------------------------------------------------------

# Probe all default paths up front so the expander label can show a
# meaningful status badge even before the user opens it.
_bugs_default         = f"{_product_dir}/ecl_parsed.csv"
_risk_default         = f"{_product_dir}/risk_register_scored_all.csv"
_cluster_default      = f"{_product_dir}/clusters/ecl_parsed_clustered.csv"
_cluster_sum_default  = f"{_product_dir}/clusters/ecl_parsed_cluster_summary.csv"
_cluster_ent_default  = f"{_product_dir}/clusters/ecl_parsed_module_entropy.csv"
_cluster_s12_default  = f"{_product_dir}/clusters/ecl_parsed_cluster_summary_s12.csv"
_cluster_s34_default  = f"{_product_dir}/clusters/ecl_parsed_cluster_summary_s34.csv"
_pred_default         = f"{_product_dir}/predictions/ecl_parsed_predictions.csv"
_pred_sum_def         = f"{_product_dir}/predictions/ecl_parsed_predictions_focus_summary.txt"
_pred_li_def          = f"{_product_dir}/predictions/ecl_parsed_predictions_leading_indicators.csv"
_pred_cluster_def     = f"{_product_dir}/predictions/ecl_parsed_predictions_by_cluster.csv"
_pred_category_def    = f"{_product_dir}/predictions/ecl_parsed_predictions_by_category.csv"
_pred_scenario_def    = f"{_product_dir}/predictions/ecl_parsed_predictions_by_scenario.csv"

_bugs_probe    = Path(_bugs_default).exists()
_risk_probe    = Path(_risk_default).exists()
_cluster_probe = Path(_cluster_default).exists()
_pred_probe    = Path(_pred_default).exists()

# Build a compact badge: ✅ for each path that exists
_badge_parts = ["✅ bugs" if _bugs_probe else "⚠️ bugs"]
if _risk_probe:    _badge_parts.append("✅ risk")
if _cluster_probe: _badge_parts.append("✅ clusters")
if _pred_probe:    _badge_parts.append("✅ forecast")
_datasources_label = f"📁 Data Sources — {' · '.join(_badge_parts)}"

# Auto-open only on first run (when the required bug data file isn't found yet)
with st.sidebar.expander(_datasources_label, expanded=not _bugs_probe):

    # ── Step 1 — Bug data (required) ────────────────────────────────────────
    st.markdown("**Step 1 — Bug data** (required)")
    ds = st.radio("Bug data source", ["Upload CSV", "File Path"], key="ds_bugs", index=1)

    if ds == "Upload CSV":
        up = st.file_uploader("Upload ecl_parsed.csv", type="csv", key="up_bugs")
        if up:
            df = pd.read_csv(up, low_memory=False)
            for dc in ["Create Date", "Closed Date"]:
                if dc in df.columns:
                    df[dc] = pd.to_datetime(df[dc], errors="coerce")
        else:
            st.info("📂 Upload **ecl_parsed.csv** (from `parse_ecl_export.py`) to begin.")
            st.stop()
    else:
        fp = st.text_input("CSV path", value=_bugs_default, key="fp_bugs")
        if Path(fp).exists():
            df = load_csv(fp)
            st.caption(f"✅ {len(df):,} bugs loaded")
        else:
            st.error(f"File not found: {fp}")
            st.stop()

    st.divider()

    # ── Step 2 — Risk scores (optional) ─────────────────────────────────────
    # Source info stored here; actual merge happens after the version filter.
    st.markdown("**Step 2 — Risk scores** (optional, enriches all charts)")

    risk_base_path: str = ""
    risk_ver_dir:   str = ""
    risk_uploaded       = None

    rds = st.radio(
        "Risk data source", ["File Path", "None"], key="ds_risk", index=0
    )
    if rds == "File Path":
        rfp = st.text_input(
            "Risk CSV path", value=_risk_default, key="fp_risk",
        )
        if Path(rfp).exists():
            risk_base_path = rfp
            risk_ver_dir   = str(Path(rfp).parent / "risk_register_versions")
            st.caption("✅ Risk scores found")
        elif rfp:
            st.caption("⚠️ File not found — run ai_risk_scorer.py first.")

    st.divider()

    # ── Step 3 — Bug clusters (optional) ────────────────────────────────────
    st.markdown("**Step 3 — Bug clusters** (optional, unlocks Tab 8)")

    cluster_df      = None
    cluster_sum_df  = None
    cluster_ent_df  = None   # v3.0: module entropy
    cluster_s12_df  = None   # v3.0: S1/S2 stratified summary
    cluster_s34_df  = None   # v3.0: S3/S4 stratified summary

    cds = st.radio(
        "Cluster data source", ["File Path", "None"], key="ds_cluster", index=0
    )
    if cds == "File Path":
        cfp = st.text_input(
            "Clustered CSV path", value=_cluster_default, key="fp_cluster",
        )
        csfp = st.text_input(
            "Cluster summary CSV", value=_cluster_sum_default, key="fp_cluster_sum",
        )
        cefp = st.text_input(
            "Module entropy CSV (v3.0)", value=_cluster_ent_default, key="fp_cluster_ent",
        )
        if Path(cfp).exists():
            cluster_df = load_csv(cfp)
            st.caption(f"✅ {len(cluster_df):,} bugs loaded")
        elif cfp:
            st.caption("⚠️ File not found — run cluster_bugs.py first.")
        if Path(csfp).exists():
            cluster_sum_df = load_csv(csfp)
        if Path(cefp).exists():
            cluster_ent_df = load_csv(cefp)
        # Optional stratified summaries (auto-loaded if present, no UI input needed)
        if Path(_cluster_s12_default).exists():
            cluster_s12_df = load_csv(_cluster_s12_default)
        if Path(_cluster_s34_default).exists():
            cluster_s34_df = load_csv(_cluster_s34_default)

    st.divider()

    # ── Step 4 — Defect forecast (optional) ─────────────────────────────────
    st.markdown("**Step 4 — Defect forecast** (optional, unlocks Tab 9)")

    pred_df             = None
    pred_summary_txt    = ""
    pred_leading_df     = None
    pred_cluster_df     = None   # v3.0: per-cluster bug-type predictions
    pred_category_df    = None   # v4.0: per-category bug-type predictions
    pred_scenario_df    = None   # v5.0: per-module bug scenario predictions

    pds = st.radio(
        "Prediction data source", ["File Path", "None"], key="ds_pred", index=0
    )
    if pds == "File Path":
        pfp = st.text_input(
            "Predictions CSV path", value=_pred_default, key="fp_pred",
        )
        psfp = st.text_input(
            "Focus summary .txt", value=_pred_sum_def, key="fp_pred_sum",
        )
        plfp = st.text_input(
            "Leading indicators CSV", value=_pred_li_def, key="fp_pred_li",
        )
        pcfp = st.text_input(
            "Bug-type predictions CSV (v3.0)", value=_pred_cluster_def, key="fp_pred_cluster",
        )
        pcatfp = st.text_input(
            "Bug category predictions CSV (v4.0)", value=_pred_category_def, key="fp_pred_category",
        )
        pscfp = st.text_input(
            "Bug scenario predictions CSV (v5.0)", value=_pred_scenario_def, key="fp_pred_scenario",
        )
        if Path(pfp).exists():
            pred_df = load_csv(pfp)
            st.caption(f"✅ {len(pred_df):,} modules loaded")
        elif pfp:
            st.caption("⚠️ File not found — run predict_defects.py first.")
        if Path(psfp).exists():
            pred_summary_txt = Path(psfp).read_text(encoding="utf-8")
        if Path(plfp).exists():
            pred_leading_df = load_csv(plfp)
        if Path(pcfp).exists():
            pred_cluster_df = load_csv(pcfp)
        if Path(pcatfp).exists():
            pred_category_df = load_csv(pcatfp)
        if Path(pscfp).exists():
            pred_scenario_df = load_csv(pscfp)

# ── End of expander ────────────────────────────────────────────────────────
# Derived-field computation runs here, outside the expander, so it always
# executes regardless of whether the panel is open or closed.

required = ["parsed_module", "severity_num"]
missing = [c for c in required if c not in df.columns]
if missing:
    st.error("❌ Wrong file! Upload **ecl_parsed.csv** (bug-level), not risk_register_scored.csv")
    st.warning(f"Missing columns: {', '.join(missing)}")
    st.stop()

if "risk_score_final" in df.columns and "quadrant" in df.columns and "bug_count" in df.columns:
    st.error("❌ This looks like risk_register_scored.csv. Please upload ecl_parsed.csv instead.")
    st.stop()

if "severity_weight" not in df.columns:
    df["severity_weight"] = df["severity_num"].map({1: 10, 2: 5, 3: 2, 4: 1}).fillna(2)
if "module_category" not in df.columns:
    df["module_category"] = "Uncategorized"

if "priority_label" not in df.columns and "Priority" in df.columns:
    df["priority_num"] = pd.to_numeric(df["Priority"], errors="coerce").fillna(5).astype(int)
    df["priority_label"] = df["priority_num"].map(PRIORITY_LABEL_MAP).fillna("5-N/A")
elif "priority_label" not in df.columns:
    df["priority_label"] = "5-N/A"

df["priority_label"] = df["priority_label"].apply(
    lambda v: BARE_TO_FULL_PRIORITY.get(str(v).strip(), str(v).strip())
    if pd.notna(v) else "5-N/A"
)

if "status_active" not in df.columns:
    if "Status" in df.columns:
        df["status_active"] = df["Status"].apply(is_active)
    else:
        df["status_active"] = True

if "parsed_module" in df.columns:
    df["parsed_module"] = df["parsed_module"].apply(
        lambda x: normalise_module(x) if pd.notna(x) else x
    )

sl_map = {1: "1-Critical", 2: "2-Major", 3: "3-Normal", 4: "4-Minor"}
if "severity_label" not in df.columns:
    df["severity_label"] = df["severity_num"].map(sl_map)


# ---------------------------------------------------------------------
# Sidebar – filters
# ---------------------------------------------------------------------

st.sidebar.subheader("🔍 Filters")

include_inactive = st.sidebar.checkbox(
    "Include closed/inactive bugs (Close, NAB, Won't Fix, etc.)",
    value=False,
    help="Inactive = Close, NAB, Won't Fix, Not Reproducible, New Feature, Need More Info",
)
if not include_inactive:
    df = df[df["status_active"] == True]

if "parsed_version" in df.columns:
    # ── Version ordering: recency-first, sparse versions last ─────────────
    # We read version_catalogue.csv (produced by parse_ecl_export.py) when
    # available because it contains recency order based on max(Create Date)
    # per version — robust against version strings that don't sort
    # lexicographically and against typo/sparse versions.
    #
    # Fallback: if the catalogue isn't present, derive the same ordering
    # directly from the loaded dataframe.

    _cat_path = Path(fp if ds != "Upload CSV" else "").parent / "version_catalogue.csv" \
        if ds != "Upload CSV" else None

    def _build_version_order_from_df(vdf):
        """Derive recency-ordered version list directly from bug data."""
        VERSION_SPARSE_THRESHOLD = 5
        rows = []
        for ver, grp in vdf.groupby("parsed_version", dropna=True):
            n = len(grp)
            latest = pd.to_datetime(grp["Create Date"], errors="coerce").max() \
                if "Create Date" in grp.columns else pd.NaT
            rows.append({"v": ver, "n": n, "d": latest,
                         "sparse": n < VERSION_SPARSE_THRESHOLD})
        if not rows:
            return [], []
        import pandas as _pd
        tmp = _pd.DataFrame(rows)
        tmp["_k"] = tmp["d"].fillna(_pd.Timestamp.min)
        real   = tmp[~tmp["sparse"]].sort_values("_k", ascending=False)
        sparse = tmp[ tmp["sparse"]].sort_values("_k", ascending=False)
        ordered = _pd.concat([real, sparse], ignore_index=True)
        all_vers  = ordered["v"].tolist()
        real_vers = ordered[~ordered["sparse"]]["v"].tolist()
        return all_vers, real_vers

    vers_all   = []   # all versions, recency order
    vers_real  = []   # non-sparse only, for default selection

    if _cat_path and _cat_path.exists():
        try:
            _cat = load_csv(str(_cat_path))
            if {"parsed_version", "version_rank", "version_is_sparse"}.issubset(_cat.columns):
                _cat = _cat.sort_values("version_rank")
                vers_all  = [str(r) for r in _cat["parsed_version"].dropna()]
                vers_real = [
                    str(r["parsed_version"])
                    for _, r in _cat.iterrows()
                    if pd.notna(r["parsed_version"]) and not r["version_is_sparse"]
                ]
                n_sparse = int(_cat["version_is_sparse"].sum())
                if n_sparse:
                    st.sidebar.caption(
                        f"ℹ️ {n_sparse} sparse/typo version(s) hidden from default selection "
                        f"(shown in full list below)."
                    )
        except Exception:
            pass  # fall through to dataframe derivation

    if not vers_all:
        vers_all, vers_real = _build_version_order_from_df(df)

    # Keep only versions that actually exist in the current (possibly filtered) df
    present = set(df["parsed_version"].dropna().unique())
    vers_all  = [v for v in vers_all  if v in present]
    vers_real = [v for v in vers_real if v in present]

    # Default: all non-sparse versions; fall back to all versions.
    # Session state key "version_multiselect" is seeded on first load only,
    # so "All versions" / "Clear" buttons can override it without being
    # overwritten by the default= argument on subsequent renders.
    default_vers = vers_real[:3] if vers_real else vers_all[:3]
    if "version_multiselect" not in st.session_state:
        st.session_state["version_multiselect"] = default_vers

    # Quick-select buttons above the multiselect
    _btn_col1, _btn_col2 = st.sidebar.columns(2)
    if _btn_col1.button("All versions", use_container_width=True):
        st.session_state["version_multiselect"] = vers_all
    if _btn_col2.button("Clear", use_container_width=True):
        st.session_state["version_multiselect"] = []

    sel_v = st.sidebar.multiselect(
        "Version", vers_all, key="version_multiselect",
    )
    if sel_v:
        df = df[df["parsed_version"].isin(sel_v)]
else:
    sel_v = []

# Version-aware risk file selection
# Rule: 1 version selected  -> try per-version scored file first
#       0 or 2+ selected    -> use combined _all file
def _load_risk_df(base_path, ver_dir, sel_versions, uploaded):
    if uploaded is not None:
        return pd.read_csv(uploaded, low_memory=False)
    if not base_path:
        return None
    if len(sel_versions) == 1:
        ver_safe = str(sel_versions[0]).replace(" ", "_")
        ver_path = Path(ver_dir) / f"risk_register_scored_{ver_safe}.csv"
        if ver_path.exists():
            return load_csv(str(ver_path))
        st.sidebar.caption(
            f"No per-version score file for {sel_versions[0]} — using combined scores."
        )
    return load_csv(base_path)

risk_df = _load_risk_df(risk_base_path, risk_ver_dir, sel_v, risk_uploaded)

if risk_df is not None:
    risk_ok = all(c in risk_df.columns for c in ["parsed_module", "risk_score_final", "quadrant"])
    if not risk_ok:
        st.sidebar.warning("Risk CSV missing required columns — ignoring.")
        risk_df = None

if risk_df is not None:
    risk_cols = [c for c in [
        "parsed_module", "quadrant", "risk_score_final",
        "impact_score", "detectability_score",
        "impact_score_ai", "detectability_score_ai",
        "probability_score_auto",
    ] if c in risk_df.columns]

    risk_df["parsed_module"] = risk_df["parsed_module"].apply(
        lambda x: normalise_module(x) if pd.notna(x) else x
    )
    risk_df_dedup = (
        risk_df[risk_cols]
        .sort_values("risk_score_final", ascending=False)
        .drop_duplicates(subset=["parsed_module"], keep="first")
    )
    df = df.merge(risk_df_dedup, on="parsed_module", how="left")
    df["quadrant"] = df.get("quadrant", pd.Series(["P4 - Low"] * len(df))).fillna("P4 - Low")
    df["risk_score_final"] = df.get("risk_score_final", pd.Series([0.0] * len(df))).fillna(0.0)
    risk_available = True

    n_unique    = risk_df_dedup["parsed_module"].nunique()
    ver_context = (
        f"version {sel_v[0]}" if len(sel_v) == 1
        else f"{len(sel_v)} versions selected" if sel_v
        else "all versions"
    )
    # Status is shown in the compact strip below the expanders, not as a
    # separate success box, to keep the sidebar tidy.
else:
    df["quadrant"]        = "Unknown"
    df["risk_score_final"] = 0.0
    risk_available         = False
if "Status" in df.columns:
    stats = sorted(df["Status"].dropna().unique().tolist())
    sel_s = st.sidebar.multiselect("Status", stats, default=stats)
    if sel_s:
        df = df[df["Status"].isin(sel_s)]

if "severity_num" in df.columns:
    sel_sev = st.sidebar.multiselect(
        "Severity", [1, 2, 3, 4], default=[1, 2, 3, 4],
        format_func=lambda x: {1: "S1-Critical", 2: "S2-Major", 3: "S3-Normal", 4: "S4-Minor"}[x],
    )
    if sel_sev:
        df = df[df["severity_num"].isin(sel_sev)]

if risk_available:
    all_q = sorted(df["quadrant"].dropna().unique().tolist())
    sel_q = st.sidebar.multiselect("Priority", all_q, default=all_q)
    if sel_q:
        df = df[df["quadrant"].isin(sel_q)]

active_label = "active " if not include_inactive else ""
st.sidebar.markdown(f"**Showing {len(df):,} {active_label}bugs**")


# =====================================================================
# TAB 1 – Module × Severity
# =====================================================================

if active_tab == "🗺️ Module × Severity":
    st.header("Module × Severity Heatmap")

    with st.expander("📖 How to read this chart", expanded=False):
        st.markdown("""
**What this shows:** Each row is a module (or category), each column is a severity level.
Cell colour and number = sum of **severity-weighted** bug counts:

| Severity | Weight | Meaning |
|----------|--------|---------|
| S1 — Critical | ×10 | App crash, data loss, showstopper |
| S2 — Major    | ×5  | Key feature broken, no workaround |
| S3 — Normal   | ×2  | Feature impaired, workaround exists |
| S4 — Minor    | ×1  | Cosmetic, low impact |

Weighting means a module with 2 critical bugs ranks higher than one with 20 minor bugs.

**Sort order:** Rows are sorted by **S1 (Critical) count descending** first, then by **total weighted count descending**. This guarantees that modules with crash-level bugs always float to the top of the chart regardless of how many minor bugs exist alongside them.

**View toggle:** Switch between **Category** (20 broad groups) and **Module (top 30)** (the 30 individual modules with the highest total weighted bug count).
- Use **Category** first for a quick executive overview of which product area is most problematic.
- Switch to **Module** to identify the specific sub-feature causing the most bugs — particularly useful when a category is large and bugs are concentrated in one or two spots inside it.

**`[P1]` / `[P2]` badges** appear next to module names when risk data is loaded (sidebar Step 2).
They show the module's test priority — see the Risk Heatmap tab for full detail.

**Click any cell** to instantly filter the drill-down table below the chart to that exact module + severity combination. The drill-down table lists every matching bug with its ECL link, severity, priority, and Short Description so you can immediately investigate without leaving the dashboard.

---
**Where this data comes from:** Bugs are parsed from the ECL Excel export by `parse_ecl_export.py`. Severity is extracted from the ECL `Severity` column and mapped to S1–S4, with weights S1=10, S2=5, S3=2, S4=1 applied at parse time. The `[P1]`/`[P2]` priority badges come from `risk_register_scored.csv` loaded in sidebar Step 2 — produced by the I×P×D scoring pipeline described in the Risk Heatmap tab.
""")

    vl = st.radio("View", ["Category", "Module (top 30)"], horizontal=True, key="t1v")
    gc = "module_category" if vl.startswith("C") else "parsed_module"

    df["severity_label"] = df["severity_num"].map(sl_map)

    pv = df.pivot_table(
        index=gc, columns="severity_label",
        values="severity_weight", aggfunc="sum", fill_value=0,
    )
    for lbl in ["1-Critical", "2-Major", "3-Normal", "4-Minor"]:
        if lbl not in pv.columns:
            pv[lbl] = 0
    pv = pv[["1-Critical", "2-Major", "3-Normal", "4-Minor"]]

    if vl.startswith("M"):
        pv = pv.loc[pv.sum(axis=1).nlargest(30).index]

    # Sort so highest-count modules appear at the top of the heatmap.
    # Primary sort: S1 (Critical) descending so the most critical modules
    # always float to the top; ties broken by total weighted count descending.
    sort_key = pd.DataFrame({
        "s1":    pv["1-Critical"],
        "total": pv.sum(axis=1),
    })
    pv = pv.loc[sort_key.sort_values(["s1", "total"], ascending=[False, False]).index]

    pv_display = pv.copy()
    if risk_available:
        q_map = df.groupby(gc)["quadrant"].first().to_dict()
        pv_display.index = [f"{y} [{q_map.get(y, '—')}]" for y in pv.index]

    # Use HEATMAP_COLOR_SCALE (blue gradient) so the continuous cell shading
    # does NOT conflict with the red/orange/yellow priority badge text on each row.
    fig = px.imshow(
        pv_display,
        labels=dict(x="Severity", y=gc, color="Weighted Count"),
        color_continuous_scale=HEATMAP_COLOR_SCALE,
        aspect="auto",
        text_auto=True,
    )
    fig.update_layout(
        height=max(400, len(pv_display) * 28),
        clickmode="event+select",
    )
    if risk_available:
        st.caption(
            "🔵 Cell shade = severity-weighted bug count (darker = more/heavier bugs). "
            "🔴 [P1] / 🟠 [P2] badges next to row labels = risk priority — kept separate "
            "from cell colour to avoid red-on-red confusion."
        )

    available_groups = sorted(df[gc].dropna().unique().tolist())
    if "dd_group" not in st.session_state or st.session_state["dd_group"] not in available_groups:
        st.session_state["dd_group"] = available_groups[0] if available_groups else None
    if "dd_sev" not in st.session_state or st.session_state["dd_sev"] not in SEV_OPTIONS:
        st.session_state["dd_sev"] = "All"

    event = st.plotly_chart(
        fig,
        width='stretch',
        on_select="rerun",
        selection_mode=["points"],
        key="heatmap_click",
    )

    if event and hasattr(event, "selection"):
        sel = event.selection
        pts = sel.points if hasattr(sel, "points") else sel.get("points", [])
        if pts:
            pt    = pts[0]
            raw_x = pt.get("x") if isinstance(pt, dict) else getattr(pt, "x", None)
            raw_y = pt.get("y") if isinstance(pt, dict) else getattr(pt, "y", None)
            clean_y = str(raw_y).split(" [")[0].strip() if raw_y is not None else None
            if clean_y and clean_y in available_groups:
                st.session_state["dd_group"] = clean_y
            if raw_x is not None and str(raw_x) in SEV_OPTIONS:
                st.session_state["dd_sev"] = str(raw_x)

    st.markdown("---")
    st.subheader("🔗 Bug Drill-Down")
    st.caption("Click a heatmap cell above to auto-fill, or select manually below.")

    col_a, col_b = st.columns(2)
    with col_a:
        drill_group = st.selectbox("Module / Category", options=available_groups, key="dd_group")
    with col_b:
        drill_sev = st.selectbox("Severity", options=SEV_OPTIONS, key="dd_sev")

    drill_df = df[df[gc] == drill_group].copy()
    if drill_sev != "All":
        drill_df = drill_df[drill_df["severity_num"] == SEV_NUM_MAP[drill_sev]]

    if len(drill_df) == 0:
        st.info("No bugs match this selection.")
    else:
        render_bug_table(drill_df)
        st.caption(f"Showing {len(drill_df)} bugs for **{drill_group}** / **{drill_sev}**")

    with st.expander("Raw pivot data"):
        st.dataframe(pv, width='stretch')


# =====================================================================
# TAB 2 – Version Timeline
# =====================================================================

elif active_tab == "📅 Version Timeline":
    st.header("Module × Version Timeline")

    with st.expander("📖 How to read this chart", expanded=False):
        st.markdown("""
**What this shows:** Severity-weighted bug counts per module (rows) per version (columns).
Columns are sorted in **ascending version order** — oldest release on the left, most recent on the right — so you can read the history left to right as a timeline.

**How to interpret the cells:**
- A single cell that suddenly darkens in one column = a regression was introduced in that specific release for that module.
- A row that stays consistently warm across all versions = a **chronic problem** — this module is never stable regardless of release cadence. It likely needs an architectural fix, not just a patch.
- A column that is uniformly dark across many rows = a **systemically bad release** — a large merge, SDK update, or new feature likely affected many areas at once.
- A cell that goes dark → light → dark again = a fix was applied and then regressed in a later build. Escalate to RD for a root-cause review.
- A cell that gradually lightens over time = bugs are being resolved and not reintroduced (healthy trend — ideally what every row looks like toward the end of a release cycle).

**Version filter:** Use the **version multiselect** in the sidebar to narrow the columns to a specific release window.
- Default shows the 3 most recent versions — fast for spotting recent regressions.
- Select all versions to see the full history and identify chronically unstable modules.

**View toggle:** Switch between **Category** (broad groups) and **Module (top 25)** (the 25 individual modules with the highest total weighted bug count).
- **Category** is faster for identifying which product area had the worst release.
- **Module** pinpoints the exact sub-feature — useful once you know which category to investigate.

**Severity weighting** is the same as Tab 1: S1×10, S2×5, S3×2, S4×1. A single S1 bug registers darker than many minor ones, keeping critical issues visually prominent.

---
**Where this data comes from:** `parsed_version` is extracted from the ECL `Version` field by `parse_ecl_export.py`. Severity weighting (S1=10, S2=5, S3=2, S4=1) is stored as `severity_weight` per bug at parse time. `compute_risk_scores.py` also produces one `risk_register_<version>.csv` per version so the full I×P×D scoring can be run per-release if needed.
""")

    if "parsed_version" in df.columns:
        vl2 = st.radio("View", ["Category", "Module (top 25)"], horizontal=True, key="t2v")
        gc2 = "module_category" if vl2.startswith("C") else "parsed_module"

        tl = df.pivot_table(
            index=gc2, columns="parsed_version",
            values="severity_weight", aggfunc="sum", fill_value=0,
        )
        tl = tl[sorted(tl.columns)]
        row_weights = tl.sum(axis=1)
        if vl2.startswith("M"):
            tl = tl.loc[row_weights.nlargest(25).sort_values(ascending=False).index]
        else:
            tl = tl.loc[row_weights.sort_values(ascending=False).index]

        fig2 = px.imshow(
            tl,
            labels=dict(x="Version", y=gc2, color="Weighted"),
            color_continuous_scale="YlOrRd",
            aspect="auto",
            text_auto=True,
        )
        fig2.update_layout(height=max(400, len(tl) * 28))
        st.plotly_chart(fig2, width='stretch')
    else:
        st.warning("No `parsed_version` column found.")


# =====================================================================
# TAB 3 – Tag Analysis
# =====================================================================

elif active_tab == "🏷️ Tag Analysis":
    st.header("Tag Distribution")

    with st.expander("📖 How to read this chart", expanded=False):
        st.markdown("""
**Tags** are parsed from the `[TAGS]` prefix in each bug's Short Description.
For example: `PDR-I 16.2.5 - [EDF][UX] AI Storytelling: subtitle misplaced` → tags `edf`, `ux`.

**Common tags and what they mean:**

| Tag | Meaning |
|-----|---------|
| `Side Effect` | Regression bug — something that worked before broke after a code change |
| `AT Found` | Caught by automated testing (good signal — AT is working) |
| `EDF` | Engineering Design Flaw — root cause is an architectural/design issue |
| `UX` | User experience issue |
| `MUI` | Multi-UI / platform consistency issue |

**The main heatmap** (top of the tab) shows the raw count of each tag per module/category. Darker cells = more bugs with that tag in that area.

**Regression Bug Rate (Side Effect %):** A dedicated bar chart below the main heatmap ranks modules by their side-effect rate — the fraction of all their bugs that are regressions.
- A rate above **30%** means nearly 1 in 3 bugs is a regression — this module needs dedicated regression testing every single build.
- Computed as: `(bugs tagged [Side Effect]) ÷ (total bugs) × 100`.

**AT Found Rate bar chart:** Ranks modules by what fraction of their bugs were caught by automated testing.
- 🟢 **≥30%** — strong automation coverage; AT is an effective safety net for this module.
- 🟡 **10–29%** — partial coverage; automation helps but manual gaps remain.
- 🔴 **<10%** — automation blind spot; nearly all bugs reach human testers or are missed entirely.

**Automation Blind Spots** (bottom of the tab) = modules with 0% AT-found rate. These are the highest-priority candidates for adding new automated tests — every bug in these modules currently relies entirely on manual discovery.

---
**Where tags come from:** `parse_ecl_export.py` reads `[TAG]` prefixes in each bug's Short Description and creates boolean columns: `tag_side_effect`, `tag_at_found`, `tag_edf`, `tag_ux`, `tag_mui`, etc.

These feed directly into the risk scoring pipeline:
- `regression_rate` = `side_effect_count / total_bugs` — computed in `compute_risk_scores.py`
- `automation_catch_rate` = `at_found_count / total_bugs` — also from `compute_risk_scores.py`

Both rates feed into **Detectability (D)** in `ai_risk_scorer.py`: a module with 0% AT coverage gets D+1 (harder to detect), pushing its overall I×P×D risk score up.
""")

    tc = [c for c in df.columns if c.startswith("tag_")]
    if tc:
        vl3 = st.radio("View", ["Category", "Module (top 25)"], horizontal=True, key="t3v")
        gc3 = "module_category" if vl3.startswith("C") else "parsed_module"

        tp = df.groupby(gc3)[tc].sum()
        tp.columns = [c.replace("tag_", "").replace("_", " ").title() for c in tp.columns]
        if vl3.startswith("M"):
            tp = tp.loc[tp.sum(axis=1).nlargest(25).index]

        fig3 = px.imshow(
            tp,
            labels=dict(x="Tag", y=gc3, color="Count"),
            color_continuous_scale="Viridis",
            aspect="auto",
            text_auto=True,
        )
        fig3.update_layout(height=max(400, len(tp) * 28))
        st.plotly_chart(fig3, width='stretch')

        st.subheader("Regression Bug Rate [Side Effect]")
        se = [c for c in tc if "side_effect" in c.lower()]
        if se:
            total_per_mod = df.groupby(gc3).size()
            se_count = df[df[se[0]] == True].groupby(gc3).size()
            se_rate = (
                (se_count / total_per_mod * 100)
                .fillna(0).sort_values(ascending=False).head(15)
            )
            fig_se = px.bar(
                se_rate.reset_index(), x=gc3, y=0,
                labels={gc3: "Module/Category", "0": "Regression Bug Rate (%)"},
                title="Top 15 by Regression Bug Rate (%)",
            )
            fig_se.update_layout(height=350, xaxis_tickangle=-30)
            st.plotly_chart(fig_se, width='stretch')

        st.subheader("Automation Coverage [AT Found Rate]")
        at = [c for c in tc if "at_found" in c.lower()]
        if at:
            total_per_mod = df.groupby(gc3).size()
            at_count = (
                df[df[at[0]] == True].groupby(gc3).size()
                .reindex(total_per_mod.index, fill_value=0)
            )
            at_rate = (at_count / total_per_mod * 100).fillna(0)
            at_df = pd.DataFrame({
                "Module": at_rate.index,
                "AT Found Rate (%)": at_rate.values,
                "Total Bugs": total_per_mod.values,
                "AT Found Bugs": at_count.values,
            }).sort_values("AT Found Rate (%)", ascending=False)

            fig_at = px.bar(
                at_df, x="Module", y="AT Found Rate (%)",
                color="AT Found Rate (%)",
                color_continuous_scale=["red", "orange", "green"],
                range_color=[0, 50],
                hover_data=["Total Bugs", "AT Found Bugs"],
                title="AT Found Rate by Module (Green ≥30%, Orange 10–29%, Red <10%)",
            )
            fig_at.update_layout(height=400, xaxis_tickangle=-30)
            st.plotly_chart(fig_at, width='stretch')

            blind_spots = at_df[at_df["AT Found Rate (%)"] == 0]
            st.subheader(f"🔴 Automation Blind Spots ({len(blind_spots)} modules with 0% AT coverage)")
            if len(blind_spots):
                st.dataframe(blind_spots, width='stretch', hide_index=True)
            else:
                st.success("All modules have AT coverage!")
    else:
        st.warning("No tag columns found — re-run parse_ecl_export.py")


# =====================================================================
# TAB 4 – Priority vs Severity Alignment
# =====================================================================

elif active_tab == "⚖️ P/S Alignment":
    st.header("⚖️ Priority vs Severity Alignment")

    with st.expander("📖 How to read this chart", expanded=True):
        st.markdown("""
**QA assigns Severity (S1–S4). RD assigns Priority (Fix Now → N/A).**

The chart has **RD Priority on the Y axis (rows)** and **QA Severity on the X axis (columns)**.

| RD Priority (rows) ↓ \\ QA Severity (columns) → | **S1–S2 (Critical/Major)** | **S3–S4 (Normal/Minor)** |
|---|---|---|
| **Fix Now / Must Fix** | ✅ Aligned | 🟡 **Inverse** — RD fast-tracked something QA rates minor; verify scope with RD |
| **Better Fix / No Matter** | 🔴 **Mismatch** — high-severity bug deprioritised by RD; escalate | ✅ Aligned |
| **N/A (not yet triaged)** | 🔴 **Urgent Gap** — critical bug not yet triaged by RD | ⚪ Expected — low-severity bugs often untriaged |

**Priority N/A** means RD has not assigned a priority yet. Bugs on the **diagonal** (top-left and bottom-right) are well-aligned. Bugs in the **top-right or bottom-left** cells are mismatches worth investigating.

---
### 📊 The Three Mismatch Metrics (below the chart)

| Metric | Definition | What to do |
|--------|-----------|-----------|
| **🔴 Critical Mismatch** | S1 or S2 bugs with RD Priority 4 (No Matter) or N/A | Escalate directly to RD — a confirmed crash or major functional failure must not be deprioritised without a written justification |
| **🟡 Inverse Mismatch** | S3 or S4 bugs that RD has marked Fix Now or Must Fix | Verify scope: RD may know about a wider business impact that QA's severity rating didn't capture. If the higher priority is justified, update the severity. If not, discuss recalibration with RD. |
| **⚪ Untriaged Critical** | S1 or S2 bugs where RD has not set any priority (N/A) | The most urgent gap — a crash- or data-loss-level bug with no triage decision is an uncontrolled release risk. Ping RD for a priority decision immediately. |

**Critical Mismatch drill-down** (auto-expands when mismatches exist): lists every S1/S2 bug with a low or missing priority, with ECL links for direct follow-up.

---
**Where this data comes from:**
- `priority_label` — mapped from the ECL `Priority` column by `parse_ecl_export.py` using the priority label map (1 = Fix Now → 2 = Must Fix → 3 = Better Fix → 4 = No Matter → 5 = N/A)
- `severity_num` — extracted from the ECL `Severity` column and mapped S1=1 (Critical) → S4=4 (Minor)
""")

    if "priority_label" in df.columns and "severity_num" in df.columns:
        mm = df.pivot_table(
            index="priority_label", columns="severity_num",
            aggfunc="size", fill_value=0,
        )
        mm.columns = [
            f"S{int(c)}-{'Critical' if c==1 else 'Major' if c==2 else 'Normal' if c==3 else 'Minor'}"
            for c in mm.columns
        ]
        present_priorities = [p for p in PRIORITY_ORDER if p in mm.index]
        mm = mm.reindex(present_priorities)

        if mm.empty:
            st.warning("No bugs with matching priority/severity in current filters.")
        else:
            fig4 = px.imshow(
                mm, color_continuous_scale="Blues", aspect="auto", text_auto=True,
                labels={"x": "QA Severity", "y": "RD Priority", "color": "Bug Count"},
            )
            fig4.update_layout(height=350)
            st.plotly_chart(fig4, width='stretch')

            st.markdown("---")
            col1, col2, col3 = st.columns(3)
            high_sev = df["severity_num"].isin([1, 2])
            high_pri = df["priority_label"].isin(["1-Fix Now", "2-Must Fix"])
            low_pri  = df["priority_label"].isin(["4-No Matter", "5-N/A"])

            critical_mismatch  = df[high_sev & low_pri]
            inverse_mismatch   = df[~high_sev & high_pri]
            untriaged_critical = df[high_sev & (df["priority_label"] == "5-N/A")]

            col1.metric("🔴 Critical Mismatch",  f"{len(critical_mismatch):,}",
                        help="S1/S2 bugs with low priority or N/A — escalate to RD")
            col2.metric("🟡 Inverse Mismatch",   f"{len(inverse_mismatch):,}",
                        help="S3/S4 bugs with Fix Now/Must Fix — verify scope with RD")
            col3.metric("⚪ Untriaged Critical", f"{len(untriaged_critical):,}",
                        help="S1/S2 bugs where RD has not set priority (N/A)")

            if len(critical_mismatch) > 0:
                with st.expander(f"🔴 View Critical Mismatches ({len(critical_mismatch)} bugs)"):
                    render_bug_table(critical_mismatch)
    else:
        st.warning(
            "Need `priority_label` (or `Priority`) and `severity_num` columns. "
            "Re-run parse_ecl_export.py v2.3."
        )


# =====================================================================
# TAB 5 – Team Coverage
# =====================================================================

elif active_tab == "👥 Team Coverage":
    st.header("Tester × Module Coverage")

    with st.expander("📖 How to read this chart", expanded=False):
        st.markdown("""
**What this shows:** How many bugs each tester (row) has filed per module category (column). A darker cell = more bugs filed by that tester in that category — a proxy for hands-on test experience in that area.

**Important caveat:** This counts bugs *filed*, not simply bugs *tested*. A thorough tester who actively investigates will naturally file more bugs. A light cell does not necessarily mean low coverage — the module may have genuinely been stable or another tester already opened duplicates. Treat this as a rough experience signal, not an exact coverage map.

**Knowledge Silos** (flagged below the chart) = a category where only **one** tester has ever filed bugs.
This is a **bus-factor risk**: if that person is unavailable — on holiday, leaves the team, or is reassigned — that entire area of the product has zero team members with direct hands-on experience. In a release crunch, this creates an undetected blind spot.

**How to act on silos:**
- **Pair the sole expert** with a second tester for 1–2 sprints of shadowed testing — the fastest, cheapest knowledge transfer.
- **Document test runbooks** for silo categories: what to test, where the known edge cases are, which failure modes recur most often. If the expert leaves, the runbook preserves the knowledge.
- **Cross-train during low-pressure sprints** — not mid-sprint before a release. The right time to address bus-factor risk is before it becomes urgent.
- **Prioritise silo categories that also appear in the P1 list (Risk Heatmap tab)** — these are the most dangerous combination: high risk AND single point of failure for testing.

---
**Where this data comes from:** `Creator` is read directly from the ECL `Creator` field by `parse_ecl_export.py`. `module_category` is the normalised category assigned at parse time. Only bugs in the current sidebar filter (version, status, product) are counted — change filters to compare coverage across different release windows.
""")

    if "Creator" in df.columns and "module_category" in df.columns:
        cv = df.pivot_table(
            index="Creator", columns="module_category",
            aggfunc="size", fill_value=0,
        )
        fig5 = px.imshow(cv, color_continuous_scale="Greens", aspect="auto", text_auto=True)
        fig5.update_layout(height=max(400, len(cv) * 35))
        st.plotly_chart(fig5, width='stretch')

        st.subheader("Knowledge Silos")
        silos = [cat for cat in cv.columns if (cv[cat] > 0).sum() <= 1]
        if silos:
            for s in silos:
                st.warning(f"⚠️ **{s}**: only 1 tester")
        else:
            st.success("No knowledge silos detected.")
    else:
        st.warning("Need `Creator` and `module_category` columns.")


# =====================================================================
# TAB 6 – KPI Dashboard
# =====================================================================

elif active_tab == "📊 KPI Dashboard":
    st.header("Quality KPIs")

    with st.expander("📖 What these KPIs mean", expanded=False):
        st.markdown("""
| KPI | What it measures | Why it matters |
|-----|-----------------|----------------|
| **Total Bugs** | All bugs matching the current sidebar filters (version, status, product) | Filtered baseline — change sidebar version/status filters to slice by release or bug state |
| **Critical Bugs (S1)** | Count of Severity-1 (crash / data loss) bugs in the current filter | Any S1 in the release candidate is a potential showstopper. Zero S1s is the minimum gate criterion before releasing. |
| **Avg Days to Close** | Mean calendar days from bug `Create Date` to `Closed Date` | Measures RD fix velocity. Rising trend = backlog is accumulating faster than it is being resolved. Compare across releases to detect if fix rate is deteriorating. |
| **Regression Bug Rate** | % of bugs tagged `[Side Effect]` in their Short Description | Side-effect bugs are regressions — features that previously worked and broke after a code change. A rate above 20% indicates insufficient regression test coverage for the complexity of changes being made. |
| **Active vs Inactive** | Active = Open / In-Progress; Inactive = Closed / NAB / Won't Fix | Active count = live unresolved risk in the current filter. |
| **P1 Modules** | Modules with I×P×D risk score > 90 (from loaded risk register) | Every P1 module must be tested every single build — no exceptions. |
| **P2 Modules** | Modules with I×P×D risk score 70–90 | Test every sprint or major build. |
| **Avg Risk Score** | Mean I×P×D risk score across all modules in the current filter | Overall risk health signal. A rising trend = cumulative risk is growing. A declining trend = risk is being managed and reduced. |

---
### 📈 Weekly Bug Trend

Counts how many bugs were **created per calendar week** by resampling the `Create Date` field into 7-day buckets. The line chart lets you see whether bug creation is accelerating, stable, or declining.

Healthy patterns:
- **Declining trend late in a release cycle** — RD is closing bugs faster than new ones are being found. A good sign before RC sign-off.
- **Flat trend mid-cycle** — Normal steady state during active development.
- **Spike mid-cycle** — Often signals a large merge, SDK update, or new feature drop that introduced regressions. Cross-reference with Tab 2 (Version Timeline) to pinpoint which module spiked.
- **Spike late in cycle** — Warning: regression testing uncovered a deep problem. Consider delaying release until the spike resolves.

### 🥧 Severity Distribution

Pie chart of all currently filtered bugs broken down by severity. A healthy end-of-cycle snapshot should show a large S3/S4 slice. A large S1 or S2 slice at release time is a risk signal requiring immediate RD escalation.

---
**Where these numbers come from:**
- Bug counts, severity, and weekly trend: `ecl_parsed.csv` from `parse_ecl_export.py`
- P1/P2 module counts and Avg Risk Score: `risk_register_scored.csv` from `ai_risk_scorer.py`
- Regression Bug Rate: mean of the `tag_side_effect` boolean column, parsed from `[Side Effect]` tags in ECL
- Avg Days to Close: computed at parse time from `Create Date` and `Closed Date` in the ECL export
""")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Bugs (filtered)", f"{len(df):,}")
    c2.metric("Critical Bugs (S1)", f"{int((df['severity_num'] == 1).sum()):,}")

    if "days_to_close" in df.columns:
        ac = df["days_to_close"].dropna().mean()
        c3.metric("Avg Days to Close", f"{ac:.1f}" if not np.isnan(ac) else "N/A")
    else:
        c3.metric("Avg Days to Close", "N/A")

    se2 = [c for c in df.columns if "side_effect" in c.lower() and c.startswith("tag_")]
    c4.metric("Regression Bug Rate", f"{df[se2[0]].mean() * 100:.1f}%" if se2 else "N/A")

    if "status_active" in df.columns:
        st.markdown("---")
        sa1, sa2 = st.columns(2)
        sa1.metric("Active Bugs (in filter)", f"{df['status_active'].sum():,}")
        sa2.metric("Inactive Bugs (in filter)", f"{(~df['status_active']).sum():,}",
                   help="Close, NAB, Won't Fix, etc.")

    if risk_available:
        st.markdown("---")
        cr1, cr2, cr3 = st.columns(3)
        q_counts = df.groupby("quadrant").size()
        cr1.metric("P1 Modules (Critical)",  int(q_counts.get("P1 - Critical", 0)))
        cr2.metric("P2 Modules (High)", int(q_counts.get("P2 - High", 0)))
        cr3.metric("Avg Risk Score", f"{df['risk_score_final'].mean():.1f}")

    st.markdown("---")
    if "Create Date" in df.columns:
        st.subheader("Weekly Bug Trend")
        vd = df[df["Create Date"].notna()].copy()
        if len(vd):
            wk = vd.set_index("Create Date").resample("W").size().reset_index()
            wk.columns = ["Week", "Count"]
            st.plotly_chart(
                px.line(wk, x="Week", y="Count", markers=True).update_layout(height=300),
                width='stretch',
            )

    st.subheader("Severity Distribution")
    sd = df["severity_num"].value_counts().sort_index()
    sd.index = sd.index.map({1: "S1-Critical", 2: "S2-Major", 3: "S3-Normal", 4: "S4-Minor"})
    st.plotly_chart(
        px.pie(
            values=sd.values, names=sd.index,
            color_discrete_sequence=["#f44336", "#f57c00", "#fbc02d", "#4caf50"],
        ).update_layout(height=350),
        width='stretch',
    )


# =====================================================================
# TAB 7 – Risk Heatmap  (always left/right [6,4] split)
# =====================================================================

elif active_tab == "🔥 Risk Heatmap":

    if not risk_available:
        st.warning("""
**Risk data not loaded.** To unlock this tab:

1. Run: `python scripts/ai_risk_scorer.py data/risk_register.csv data/risk_register_scored.csv --provider heuristic`
2. In the **sidebar → Step 2**, upload `data/risk_register_scored.csv`
""")
        st.stop()

    # Build aggregation
    tm_agg = df.groupby(["module_category", "parsed_module"]).agg(
        bug_count=("parsed_module", "size"),
        sev_weight=("severity_weight", "sum"),
        critical_count=("severity_num", lambda x: (x == 1).sum()),
        risk_score=("risk_score_final", "mean"),
    ).reset_index()
    tm_agg = tm_agg[tm_agg["parsed_module"].notna() & (tm_agg["bug_count"] > 0)]
    tm_agg["quadrant"] = tm_agg["parsed_module"].map(
        df.groupby("parsed_module")["quadrant"].first().to_dict()
    ).fillna("P4 - Low")

    all_modules    = sorted(tm_agg["parsed_module"].dropna().unique().tolist())
    all_categories = sorted(tm_agg["module_category"].dropna().unique().tolist())

    with st.expander("📖 How the Risk Score works — full pipeline explained", expanded=False):
        st.markdown("""
The risk scores shown in this tab are produced by a **3-step pipeline** run before launching the
dashboard. Here is exactly what each step does and how every number was calculated.

---

### Step 1 — Parse ECL bugs → `ecl_parsed.csv`
**Script:** `parse_ecl_export.py`

Reads the raw ECL Excel export and extracts structured fields from each bug's Short Description.
Example: `PDR-I 16.2.5 - [EDF][UX] AI Storytelling: subtitle misplaced`

Key outputs per bug row:
- `parsed_module` — normalised module name (64 typo aliases resolved, e.g. *HQ Auido Denoise* → *HQ Audio Denoise*)
- `module_category` — one of 20 top-level categories (222 flat overrides + partial matching)
- `severity_num` / `severity_weight` — S1=10 pts, S2=5 pts, S3=2 pts, S4=1 pt
- `tag_side_effect`, `tag_at_found`, `tag_edf`, `tag_ux` etc. — boolean columns from `[TAG]` prefixes
- `days_to_close`, `builds_to_fix`, `repro_rate` — computed from ECL date and build fields

---

### Step 2 — Aggregate per-module metrics → `risk_register.csv`
**Script:** `compute_risk_scores.py`

Groups all bugs by module and computes:

| Column | How it is calculated |
|--------|---------------------|
| `total_bugs` | Count of all bugs for this module |
| `severity_weighted_total` | Sum of per-bug weights (S1x10 + S2x5 + S3x2 + S4x1) |
| `critical_count` / `major_count` | Count of S1 / S2 severity bugs |
| `regression_rate` | side_effect_count / total_bugs — fraction that are regressions |
| `automation_catch_rate` | at_found_count / total_bugs — fraction caught by automated tests |
| `avg_repro_rate` | Mean reproduce probability across all bugs in this module |
| `avg_days_to_close` | Mean calendar days from bug creation to Close status |
| `unique_reporters` | Number of distinct testers who have filed bugs here |
| **`probability_score_auto`** | **Quintile rank of total_bugs across all modules, scored 1–5.** Top 20% most bug-prone = 5, bottom 20% = 1. This is the P in I x P x D. |

Also produces one `risk_register_<version>.csv` per ECL version for per-build analysis.

---

### Step 3 — Score Impact and Detectability → `risk_register_scored.csv`
**Script:** `ai_risk_scorer.py`   Provider: heuristic / Ollama LLM / OpenAI

#### I — Impact (1–5): How severe is it when this module breaks?

Assigned from a domain knowledge table built by the QA team:

| Score | Example modules | Rationale |
|-------|----------------|-----------|
| 5 | Export, AI Storytelling, Auto Edit, Project, Produced Video | Core output — failure = complete feature loss for user |
| 4 | Image/Text to Video, AI Music Generator, Auto Captions, Camera, Trim | Key features that directly affect the deliverable |
| 3 | Most mid-tier features | Notable impact but a workaround exists |
| 2 | Trending, peripheral UI | Low user-visible impact |
| 1 | Settings, Preferences, Tutorials, Credit pages | Cosmetic or internal only |

**Auto-boost rules applied on top of the table:**
- `critical_count >= 10` → Impact +1 (capped at 5)
- `critical_count >= 5` and Impact < 4 → Impact raised to 4

With **Ollama or OpenAI**, the LLM receives module name, category, bug counts, and regression
rate, reasons about business impact, and returns a score 1–5 with written justification.

#### P — Probability (1–5): How likely is this module to have bugs in the next build?

This is `probability_score_auto` from Step 2 — the quintile rank of historical bug density.
It is **never overridden by AI**; it is always purely data-driven from ECL history.

#### D — Detectability (1–5): How hard is it to catch bugs before release?

Heuristic calculation (higher = harder to catch = more dangerous to miss):

| Condition | Effect on D |
|-----------|-------------|
| Base value | 3 |
| Automation catch rate > 10% | -1 (AT is finding these bugs) |
| Automation catch rate > 30% | -1 again (strong AT coverage) |
| Automation catch rate = 0% | +1 (no automated safety net at all) |
| Avg reproduce rate < 50% | +1 (intermittent bugs are hard to confirm) |
| Regression rate > 15% | +1 (side effects are unpredictable) |
| Final value | clamped to [1, 5] |

With Ollama or OpenAI, the LLM reasons about module complexity and test coverage instead.

---

### Final formula

```
Risk Score = I x P x D        (maximum = 5 x 5 x 5 = 125)
```

| Priority | Score threshold | Testing cadence |
|----------|----------------|-----------------|
| P1 — Critical  | > 90  | Every build, every sprint |
| P2 — High      | 70-90 | Every sprint / major build |
| P3 — Medium    | 50-69 | Every release candidate |
| P4 — Low       | < 50  | Full release cycle only |

---

### What each visual element shows

| Element | Data source | What it represents |
|---------|-------------|-------------------|
| **Block size** | `ecl_parsed.csv` | Bug count for this module |
| **Colour — Risk Score** | `risk_register_scored.csv` | Final I×P×D value |
| **Colour — Priority** | `risk_register_scored.csv` | P1–P4 assignment |
| **Colour — Critical Count** | `ecl_parsed.csv` | Number of S1 (crash-level) bugs |
| **Colour — Severity Weight** | `ecl_parsed.csv` | Weighted bug sum S1×10 + S2×5 + S3×2 + S4×1 |
| **Right detail panel** | Both files joined | Total bugs, critical count, risk score, and full bug list for the selected module. Before clicking a block, shows the top 5 modules by bug count as a quick reference. |
| **🌞 Sunburst View** (expander below treemap) | Both files | Same data in a nested pie chart. Use it to compare the proportion of bugs across categories — the outer ring is modules, the inner ring is categories. |
| **📋 Category Summary** (expander below treemap) | Both files | Aggregate table: one row per category showing total modules, total bugs, severity weight, average risk score, and count of P1 modules. Sort by Avg Risk Score to quickly identify the riskiest category. |
| **🔴 P1 Modules bar chart** | `risk_register_scored.csv` | All P1-priority modules ranked by risk score, highest first. These are the mandatory test-every-build targets. Each bar is coloured by category. |
| **Risk Score vs Probability scatter** | `risk_register_scored.csv` | I×P×D score on the Y axis vs historical defect probability rank (1–5) on the X axis. X values are slightly jittered (±0.35) so overlapping dots separate visually — hover for the true integer value. Threshold lines at 50, 70, 90 define the four risk zones. Look for modules in the top-right corner: both high-risk AND historically bug-prone — these are the highest-priority targets. |

> **Tip:** Heuristic scores are a strong starting point but should be reviewed with the team.
> Open `risk_register_scored.csv` in a spreadsheet, adjust any `impact_score` or
> `detectability_score` values the team disagrees with, then re-run
> `ai_risk_scorer.py --provider heuristic` to recompute final scores and priorities.
""")

    # Version context banner
    if len(sel_v) == 1:
        st.info(
            f"Showing version **{sel_v[0]}** only. "
            "Risk scores loaded from per-version file where available. "
            "Clear the filter or select multiple versions to see all-version combined scores."
        )
    elif sel_v:
        st.info(
            f"Showing {len(sel_v)} versions: {chr(44).join(str(v) for v in sel_v)}. "
            "Risk scores are from the combined all-versions file."
        )
    else:
        st.info("Showing all versions combined. Risk scores are from the combined all-versions file.")

    color_opt = st.radio(
        "Color by",
        ["Risk Score (LLM)", "Priority", "Critical Count", "Severity Weight"],
        horizontal=True, key="tmc",
    )

    dynamic_height = min(1100, max(700, len(all_modules) * 12))

    col_map, col_detail = st.columns([6, 4], gap="medium")

    # ── LEFT: Treemap ────────────────────────────────────────────────
    with col_map:
        current_sel = st.session_state["tm_selected_module"]
        if current_sel:
            st.caption(f"🔍 **{current_sel}** selected · click another module to switch")
        else:
            st.caption("Click a module block to view its bugs →")

        if color_opt == "Risk Score (LLM)":
            fig_tm = px.treemap(
                tm_agg, path=["module_category", "parsed_module"],
                values="bug_count", color="risk_score",
                color_continuous_scale="YlOrRd",
                labels={"bug_count": "Bugs", "risk_score": "Risk Score",
                        "module_category": "Category", "parsed_module": "Module"},
                hover_data={"bug_count": True, "risk_score": ":.1f",
                            "critical_count": True, "quadrant": True},
            )
        elif color_opt == "Priority":
            fig_tm = px.treemap(
                tm_agg, path=["module_category", "parsed_module"],
                values="bug_count", color="quadrant",
                color_discrete_map=QUADRANT_COLORS,
                labels={"bug_count": "Bugs", "quadrant": "Priority",
                        "module_category": "Category", "parsed_module": "Module"},
                hover_data={"bug_count": True, "risk_score": ":.1f", "critical_count": True},
            )
        elif color_opt == "Critical Count":
            fig_tm = px.treemap(
                tm_agg, path=["module_category", "parsed_module"],
                values="bug_count", color="critical_count",
                color_continuous_scale="Reds",
                labels={"bug_count": "Bugs", "critical_count": "Critical Bugs",
                        "module_category": "Category", "parsed_module": "Module"},
            )
        else:
            fig_tm = px.treemap(
                tm_agg, path=["module_category", "parsed_module"],
                values="bug_count", color="sev_weight",
                color_continuous_scale="YlOrRd",
                labels={"bug_count": "Bugs", "sev_weight": "Severity Weight",
                        "module_category": "Category", "parsed_module": "Module"},
            )

        fig_tm.update_layout(
            height=dynamic_height,
            margin=dict(t=50, l=5, r=5, b=5),
            transition_duration=0,
        )
        fig_tm.update_traces(
            root_color="rgba(0,0,0,0)",
            textinfo="label+value",
        )

        tm_event = st.plotly_chart(
            fig_tm,
            width='stretch',
            on_select="rerun",
            selection_mode=["points"],
            key="treemap_click",
        )

        if tm_event and hasattr(tm_event, "selection"):
            sel = tm_event.selection
            pts = sel.points if hasattr(sel, "points") else sel.get("points", [])
            if pts:
                pt = pts[0]
                clicked_label = (
                    pt.get("label") if isinstance(pt, dict)
                    else getattr(pt, "label", None)
                )
                # Use the Plotly `parent` field to distinguish a module node from a
                # category node. Category nodes have an empty parent (they sit at the
                # root level), while module nodes have a non-empty parent that is the
                # category name. This correctly handles the edge case where a module
                # shares the same name as its category (e.g. "Shortcut" module inside
                # the "Shortcut" category) — the old `not in all_categories` check
                # would silently drop the click in that situation.
                clicked_parent = (
                    pt.get("parent") if isinstance(pt, dict)
                    else getattr(pt, "parent", None)
                )
                is_module_node = bool(clicked_parent)  # empty string / None = category root
                if (clicked_label
                        and clicked_label in all_modules
                        and is_module_node):
                    cat_row = tm_agg[tm_agg["parsed_module"] == clicked_label]
                    st.session_state["tm_selected_module"] = clicked_label
                    st.session_state["tm_selected_category"] = (
                        cat_row["module_category"].values[0] if len(cat_row) else ""
                    )

    # ── RIGHT: Bug detail panel ──────────────────────────────────────
    with col_detail:
        st.markdown(
            f"<div style='max-height:{dynamic_height}px; overflow-y:auto; padding-right:6px;'>",
            unsafe_allow_html=True,
        )

        selected     = st.session_state["tm_selected_module"]
        selected_cat = st.session_state.get("tm_selected_category", "")

        if not selected:
            st.markdown("### 📋 Bug Details")
            st.info("👈 Click a module in the treemap to see its bugs here.")
            st.markdown("**Top 5 modules by bug count:**")
            top5 = tm_agg.nlargest(5, "bug_count")[
                ["parsed_module", "bug_count", "critical_count", "risk_score", "quadrant"]
            ].copy()
            top5.columns = ["Module", "Bugs", "Critical", "Risk Score", "Priority"]
            st.dataframe(top5, width='stretch', hide_index=True)

        else:
            risk_row    = tm_agg[tm_agg["parsed_module"] == selected]
            module_bugs = df[df["parsed_module"] == selected].copy()

            st.markdown(f"### 📋 {selected}")
            if selected_cat:
                st.caption(f"Category: **{selected_cat}**")

            mm1, mm2, mm3 = st.columns(3)
            mm1.metric("Total",    len(module_bugs))
            mm2.metric("Critical", int((module_bugs["severity_num"] == 1).sum()))
            mm3.metric(
                "Risk Score",
                f"{risk_row['risk_score'].values[0]:.1f}" if len(risk_row) else "N/A"
            )

            quadrant_val = risk_row["quadrant"].values[0] if len(risk_row) else "—"
            qcolor = {"P1 - Critical": "🔴", "P2 - High": "🟠", "P3 - Medium": "🟡", "P4 - Low": "🟢"}.get(quadrant_val, "⚪")
            st.markdown(f"Priority: {qcolor} **{quadrant_val}**")

            st.markdown("---")

            detail_sev = st.radio(
                "Severity filter",
                options=SEV_OPTIONS,
                horizontal=True,
                key="detail_sev_filter",
            )
            if detail_sev != "All":
                module_bugs = module_bugs[
                    module_bugs["severity_num"] == SEV_NUM_MAP[detail_sev]
                ]

            if len(module_bugs) == 0:
                st.info("No bugs match this filter.")
            else:
                render_bug_table(module_bugs)
                st.caption(f"{len(module_bugs)} bug(s) · severity: **{detail_sev}**")

        st.markdown("</div>", unsafe_allow_html=True)

    # ── Supporting charts below ───────────────────────────────────────
    st.markdown("---")
    with st.expander("🌞 Sunburst View"):
        if color_opt == "Priority":
            fig_sb = px.sunburst(
                tm_agg, path=["module_category", "parsed_module"],
                values="bug_count", color="quadrant",
                color_discrete_map=QUADRANT_COLORS,
            )
        else:
            fig_sb = px.sunburst(
                tm_agg, path=["module_category", "parsed_module"],
                values="bug_count", color="risk_score",
                color_continuous_scale="YlOrRd",
            )
        fig_sb.update_layout(height=600, margin=dict(t=10, l=10, r=10, b=10))
        st.plotly_chart(fig_sb, width='stretch')

    with st.expander("📋 Category Summary"):
        cats = (
            tm_agg.groupby("module_category").agg(
                Modules=("parsed_module", "nunique"),
                Total_Bugs=("bug_count", "sum"),
                Severity_Weight=("sev_weight", "sum"),
                Avg_Risk=("risk_score", "mean"),
                P1_Modules=("quadrant", lambda x: (x == "P1 - Critical").sum()),
            )
            .sort_values("Avg_Risk", ascending=False).reset_index()
        )
        cats.columns = ["Category", "Modules", "Total Bugs",
                        "Severity Weight", "Avg Risk Score", "P1 Modules"]
        st.dataframe(cats, width='stretch', hide_index=True)

    st.subheader("🔴 P1 Modules — Test Every Build")
    q4 = tm_agg[tm_agg["quadrant"] == "P1 - Critical"].sort_values("risk_score", ascending=False)
    if len(q4):
        fig_q4 = px.bar(
            q4.head(20), x="parsed_module", y="risk_score", color="module_category",
            labels={"parsed_module": "Module", "risk_score": "Risk Score",
                    "module_category": "Category"},
            title="P1 Modules by Risk Score",
        )
        fig_q4.update_layout(height=400, xaxis_tickangle=-30)
        st.plotly_chart(fig_q4, width='stretch')
    else:
        st.info("No P1 modules in current filter.")

    if risk_df is not None and "probability_score_auto" in risk_df.columns:
        st.subheader("Risk Score vs Probability")
        scatter_df = tm_agg.copy()
        prob_map = risk_df.set_index("parsed_module")["probability_score_auto"].to_dict()
        scatter_df["probability"] = scatter_df["parsed_module"].map(prob_map).fillna(2.5)

        def assign_zone(score: float) -> str:
            if score > 90: return "Critical Risk (>90)"
            if score >= 70: return "High Risk (70-90)"
            if score >= 50: return "Medium Risk (50-69)"
            return "Low Risk (<50)"

        scatter_df["risk_zone"] = scatter_df["risk_score"].apply(assign_zone)

        # Add small random jitter to x (probability) so dots at the same
        # integer score separate from each other and can be hovered individually.
        rng = np.random.default_rng(42)
        scatter_df["probability_jittered"] = (
            scatter_df["probability"]
            + rng.uniform(-0.35, 0.35, size=len(scatter_df))
        )

        zone_colors = {
            "Critical Risk (>90)": "#D62728",
            "High Risk (70-90)":   "#FF7F0E",
            "Medium Risk (50-69)": "#BCBD22",
            "Low Risk (<50)":      "#2CA02C",
        }
        fig_scatter = px.scatter(
            scatter_df,
            x="probability_jittered",
            y="risk_score",
            color="risk_zone",
            color_discrete_map=zone_colors,
            size="bug_count",
            hover_name="parsed_module",
            hover_data={
                "probability_jittered": False,        # hide the jittered value
                "probability": True,                  # show the real integer
                "risk_score": ":.1f",
                "bug_count": True,
                "risk_zone": False,
            },
            labels={
                "probability_jittered": "Probability Score (1–5, jittered for readability)",
                "risk_score": "Risk Score (I×P×D)",
                "bug_count": "Bug Count",
            },
            size_max=18,          # smaller max bubble so dense clusters separate
            opacity=0.75,         # transparency lets overlapping dots show through
        )
        # Overlay true integer tick labels on x-axis so jitter doesn't confuse
        fig_scatter.update_xaxes(
            tickvals=[1, 2, 3, 4, 5],
            ticktext=["1", "2", "3", "4", "5"],
            range=[0.3, 5.7],
        )
        for y_val, label in [
            (50, "Medium threshold"), (70, "High threshold"), (90, "Critical threshold")
        ]:
            fig_scatter.add_hline(
                y=y_val, line_dash="dash", line_color="gray", line_width=1,
                annotation_text=label, annotation_position="right",
            )
        fig_scatter.update_layout(height=500)
        st.caption(
            "ℹ️ X-axis values are slightly jittered (±0.35) so dots at the same probability "
            "level separate visually. Hover any dot for the true score."
        )
        st.plotly_chart(fig_scatter, width='stretch')

# =====================================================================
# TAB 8 – Bug Clusters
# =====================================================================

elif active_tab == "🔬 Bug Clusters":
    st.header("🔬 Bug Clusters — What Kinds of Problems Exist?")

    with st.expander("📖 How to read this tab — full guide", expanded=False):
        st.markdown("""
## 🔬 Bug Clusters — Complete Guide

This tab groups all bugs into **themes** using natural language analysis of their short descriptions.
Instead of reviewing hundreds of individual bugs, it immediately answers:
**"What categories of problems keep recurring, and how serious are they?"**

Each cluster is named by the 2–3 keywords that appear most often together in the bugs it contains
(e.g. `ai storytelling | subtitle | timing`). When run with Ollama, the names are richer plain-English
labels generated by an LLM (e.g. "subtitle rendering delay"). Either way, think of each cluster as a
*recurring complaint type* — not a single bug, but a pattern of bugs.

---

### 📊 Headline Metrics (top row)

| Metric | What it tells you |
|--------|------------------|
| **Total Bugs Analysed** | All bugs in the loaded clustered file |
| **Distinct Themes Found** | Number of named clusters (the noise/unclustered bucket is excluded) |
| **Bugs Grouped into Themes** | Count and % of bugs that belong to a named cluster — higher % means the algorithm found strong patterns |
| **Uncategorised Bugs** | Bugs whose descriptions were too short or too unique to match any cluster — normal to have some |

---

### 🎨 Colour Coding

Each theme bar in the overview chart is coloured by the **average severity** of the bugs it contains:

| Colour | Average severity | Meaning |
|--------|-----------------|---------|
| 🔴 Red | ≤ 1.5 | Mostly Critical bugs (crashes, data loss) |
| 🟠 Orange | 1.5 – 2.5 | Mostly Major functional issues |
| 🟡 Yellow | 2.5 – 3.5 | Mostly Normal bugs |
| 🟢 Green | > 3.5 | Mostly Minor / cosmetic issues |

---

### 📏 How to Read the Overview Chart

- **Bar length** = number of bugs in that theme. Longer = more bugs share this pattern.
- **Colour** = how severe those bugs are on average (see above).
- A **short red bar** (few critical bugs on a single theme) may be more urgent than a **long yellow bar** (many minor bugs on a popular theme). Use both dimensions together.

---

### 🃏 Theme Detail Cards

Expand any card to see:
- **Bug count** — how many bugs belong to this theme
- **Avg severity** — 1 (Critical) to 4 (Minor)
- **Share of clustered bugs** — this theme's proportion of all grouped bugs
- **Velocity (recent vs prior 3 builds)** — the acceleration ratio; >1.5 = growing, <0.67 = declining
- **Recurrence rate** — fraction of recent bugs from modules that also appeared in the prior 3-build window (high = same modules keep re-introducing this bug type)
- **Modules affected** — which modules contribute bugs to this theme
- **Sample bug descriptions** — up to 6 real ECL examples so you can judge the pattern yourself
- **Action line** — a plain-English recommendation based on severity, amplified if the velocity or recurrence signals are also firing:
  - 🚨 **Immediate attention** (avg sev ≤ 1.5) — crash-level or data-loss bugs. Escalate to RD.
  - ⚠️ **High priority** (avg sev ≤ 2.5) — major functional issues. Add to next sprint regression.
  - 📋 **Standard priority** (avg sev ≤ 3.5) — normal issues. Cover in release-candidate testing.
  - ✅ **Low priority** (avg sev > 3.5) — cosmetic or minor issues. Full release cycle.

---

### 🚨 Alert Banners (shown before the chart when thresholds are hit)

**🔺 Growing theme alerts** fire when `cluster_velocity_ratio` ≥ 1.5.
> **Velocity ratio** = (bugs in last 3 builds) ÷ (bugs in prior 3 builds). A ratio of 1.5 means 50% more bugs than the preceding window — a strong signal that something changed recently.

**🔁 Fix-not-holding alerts** fire when `recurrence_rate` ≥ 0.6.
> **Recurrence rate** = fraction of recent bugs (last 3 builds) from modules that also contributed to this theme in the prior 3-build window. 60%+ means the same modules keep re-introducing the same type of bug — a root-cause fix, not another patch, is needed.

---

### 📈 Cluster Velocity Chart

Ranks all themes by velocity ratio. The **red dashed threshold line at 1.5×** marks the growing boundary.
- Bars to the right at 1.5 or beyond need immediate investigative attention regardless of total bug count.
- Green bars (declining) indicate themes that are resolving — the fix is holding.

---

### 🔀 Module Cluster Entropy Chart

**Shannon entropy** measures how spread out a module's bugs are across different themes.
The formula: H = −Σ(pᵢ × log₂(pᵢ)) where pᵢ = the fraction of bugs belonging to theme i.

| Entropy | Meaning | What to do |
|---------|---------|-----------|
| **< 1 (✅ Focused)** | Bugs concentrated in one theme — single well-defined failure mode | One root-cause fix likely resolves the majority of bugs |
| **1–2 (🔶 Spreading)** | Bugs spread across a few themes | Investigate whether the themes share a common component or API |
| **> 2 (⚠️ Broad instability)** | Bugs across many themes — systemic module instability | Comprehensive regression pass needed; no single fix will adequately cover this |

Orange dashed line at 1.0 and red dashed line at 2.0 mark the thresholds on the chart.

---

### 🔬 Severity-Stratified Tabs (S1/S2 and S3/S4)

When clustering was run with `--stratify-severity`, two additional sub-views appear:
- **🔴 S1/S2 tier** — clusters built only from Critical and Major bugs. These show the most dangerous patterns without minor-bug noise diluting the keyword weighting.
- **🟡 S3/S4 tier** — clusters built only from Normal and Minor bugs. Useful for UX and polish issues independently.

Each tier has its own overview bar chart and detail cards with velocity and recurrence metrics.

---

### 🔍 How to Investigate a Pattern

1. Check alert banners first — growing or fix-not-holding themes are highest priority.
2. Find the largest red/orange bars — combine severity and count.
3. Open the card and check which modules are affected.
   - **1 module** → isolated bug area. Add it to the current sprint focus.
   - **3+ modules** → shared root cause likely. Investigate common infrastructure (API, SDK version, shared component).
4. Read the sample descriptions — do they describe the same underlying failure? If yes, this is systematic.
5. Cross-check with **Tab 7 (Risk Heatmap)** — if the affected modules are P1/P2, the theme is high-priority regardless of bug count.

---

### 📋 Full Theme Table

The "Full theme table (raw data)" expander at the bottom provides a sortable table with all numeric columns: velocity ratio, trend, recurrence rate. Export or copy to a spreadsheet for offline analysis.

---

### 🛠️ Where Does This Data Come From?

Run the clustering script, then point the sidebar to the output files:

```bash
# Default (TF-IDF, fast, no Ollama required):
python scripts/cluster_bugs.py data/ecl_parsed.csv data/ecl_parsed_clustered.csv

# With severity stratification (v3.0 — adds S1/S2 and S3/S4 sub-clusters):
python scripts/cluster_bugs.py data/ecl_parsed.csv data/ecl_parsed_clustered.csv \\
  --stratify-severity

# Ollama mode (richer labels, recommended when RAM is available):
python scripts/cluster_bugs.py data/ecl_parsed.csv data/ecl_parsed_clustered.csv \\
  --provider ollama --model qwen3:7b --stratify-severity
```

v3.0 produces additional output files automatically:
- `clusters/ecl_parsed_cluster_summary.csv` — includes velocity, trend, recurrence
- `clusters/ecl_parsed_module_entropy.csv` — per-module Shannon entropy
- `clusters/ecl_parsed_cluster_summary_s12.csv` — critical/major tier (requires `--stratify-severity`)
- `clusters/ecl_parsed_cluster_summary_s34.csv` — normal/minor tier (requires `--stratify-severity`)

In the sidebar: **Step 3** → set paths to the above files. The module entropy and stratified summaries load automatically from their default paths.

Recommend re-running clustering **every Friday** or whenever a new batch of builds has been parsed.
""")

    if cluster_df is None:
        st.warning(
            "**Cluster data not loaded.** "
            "Run `python scripts/cluster_bugs.py data/ecl_parsed.csv` then set the path in **Sidebar → Step 3**."
        )
        st.stop()

    # ── Guard: need cluster columns ──────────────────────────────────────
    if "cluster_id" not in cluster_df.columns or "cluster_label" not in cluster_df.columns:
        st.error("Cluster file is missing `cluster_id` / `cluster_label` columns. Re-run cluster_bugs.py.")
        st.stop()

    # ── Headline metrics ─────────────────────────────────────────────────
    n_total    = len(cluster_df)
    n_clustered = (cluster_df["cluster_id"] != -1).sum()
    n_clusters  = cluster_df[cluster_df["cluster_id"] != -1]["cluster_id"].nunique()
    n_unclustered = n_total - n_clustered

    hm1, hm2, hm3, hm4 = st.columns(4)
    hm1.metric("Total Bugs Analysed", f"{n_total:,}")
    hm2.metric("Distinct Themes Found", f"{n_clusters}")
    hm3.metric("Bugs Grouped into Themes", f"{n_clustered:,}",
               help=f"{n_clustered/n_total*100:.0f}% of all bugs belong to a named theme")
    hm4.metric("Uncategorised Bugs", f"{n_unclustered:,}",
               help="Bugs whose descriptions were too short or too unique to cluster")

    st.markdown("---")
    if cluster_sum_df is not None and not cluster_sum_df.empty:
        summary = cluster_sum_df.copy()
        # normalise column names that come from the CSV vs computed inline
        summary = summary.reset_index(drop=True)
    else:
        clustered_only = cluster_df[cluster_df["cluster_id"] != -1]
        summary = (
            clustered_only
            .groupby(["cluster_id", "cluster_label"])
            .agg(
                count=("cluster_id", "size"),
                modules=("parsed_module",
                         lambda x: ", ".join(sorted(x.dropna().unique())[:5])),
                avg_sev=("severity_num", "mean"),
            )
            .sort_values("count", ascending=False)
            .reset_index()
        )
        # When built inline, velocity/trend/recurrence are not in the summary CSV.
        # Compute velocity from the raw clustered bugs if Build# is available.
        if "Build#" in cluster_df.columns:
            _build_num = pd.to_numeric(cluster_df["Build#"], errors="coerce")
            _max_build = _build_num.max()
            _vel_rows = []
            for _cid in summary["cluster_id"].unique():
                _cmask = (cluster_df["cluster_id"] == _cid)
                _cb = _build_num[_cmask]
                _recent = int((_cb >= _max_build - 2).sum())
                _prior  = int(((_cb >= _max_build - 5) & (_cb < _max_build - 2)).sum())
                _ratio  = round(_recent / max(_prior, 1), 2)
                _trend  = "growing" if _ratio >= 1.5 else "declining" if _ratio <= 0.67 else "stable"
                # Recurrence: fraction of recent bugs from modules that also appeared in prior window
                _r_mods = set(cluster_df.loc[_cmask & (_build_num >= _max_build - 2), "parsed_module"].dropna())
                _p_mods = set(cluster_df.loc[_cmask & (_build_num >= _max_build - 5) & (_build_num < _max_build - 2), "parsed_module"].dropna())
                _recur  = round(len(_r_mods & _p_mods) / max(len(_r_mods), 1), 3)
                _vel_rows.append({"cluster_id": _cid, "cluster_velocity_ratio": _ratio,
                                  "cluster_trend": _trend, "recurrence_rate": _recur})
            _vel_df = pd.DataFrame(_vel_rows)
            summary = summary.merge(_vel_df, on="cluster_id", how="left")
        else:
            summary["cluster_velocity_ratio"] = 1.0
            summary["cluster_trend"]          = "stable"
            summary["recurrence_rate"]         = 0.0

    if summary.empty:
        st.info("No clusters found in the loaded data.")
        st.stop()

    # Ensure all required display columns exist
    for col, default in [("count", 0), ("avg_sev", 3.0), ("modules", ""),
                         ("cluster_velocity_ratio", 1.0), ("cluster_trend", "stable"),
                         ("recurrence_rate", 0.0)]:
        if col not in summary.columns:
            summary[col] = default

    summary["avg_sev"] = pd.to_numeric(summary["avg_sev"], errors="coerce").fillna(3.0)
    summary["count"]   = pd.to_numeric(summary["count"],   errors="coerce").fillna(0).astype(int)

    # Human-readable severity label for the bar chart colour
    def _sev_band(v):
        if v <= 1.5: return "🔴 Mostly Critical"
        if v <= 2.5: return "🟠 Mostly Major"
        if v <= 3.5: return "🟡 Mostly Normal"
        return "🟢 Mostly Minor"

    summary["severity_band"] = summary["avg_sev"].apply(_sev_band)

    SEV_BAND_COLORS = {
        "🔴 Mostly Critical": "#ef4444",
        "🟠 Mostly Major":    "#f97316",
        "🟡 Mostly Normal":   "#eab308",
        "🟢 Mostly Minor":    "#22c55e",
    }

    # Truncate very long cluster labels for display
    summary["label_short"] = summary["cluster_label"].apply(
        lambda s: s[:45] + "…" if isinstance(s, str) and len(s) > 45 else s
    )

    # ── Growing-cluster alert banners ────────────────────────────────────
    # Show immediately visible alerts for any theme growing ≥1.5× so users
    # don't have to scroll through cards to find the urgent signals.
    summary["cluster_velocity_ratio"] = pd.to_numeric(
        summary["cluster_velocity_ratio"], errors="coerce").fillna(1.0)
    _growing = summary[
        (summary["cluster_trend"] == "growing") &
        (summary["cluster_velocity_ratio"] >= 1.5)
    ].sort_values("cluster_velocity_ratio", ascending=False)
    _high_recur = summary[summary["recurrence_rate"] >= 0.6].sort_values(
        "recurrence_rate", ascending=False)

    if not _growing.empty:
        for _, _gr in _growing.head(3).iterrows():
            st.warning(
                f"🔺 **Growing theme:** _{_gr['label_short']}_ — "
                f"{_gr['cluster_velocity_ratio']:.1f}× increase in recent builds "
                f"({int(_gr['count'])} total bugs, avg sev {_gr['avg_sev']:.1f}). "
                f"Modules: {_gr['modules']}"
            )
    if not _high_recur.empty:
        for _, _rr in _high_recur.head(2).iterrows():
            st.error(
                f"🔁 **Fix not holding:** _{_rr['label_short']}_ — "
                f"{_rr['recurrence_rate'] * 100:.0f}% recurrence rate. "
                f"The same modules keep producing this type of bug. Escalate root-cause review."
            )

    # ── Overview bar chart ───────────────────────────────────────────────
    st.subheader("📊 Theme Overview — How Many Bugs per Theme?")
    st.caption(
        "Each bar is a recurring bug theme. Colour shows average severity. "
        "Longer bar = more bugs share that theme."
    )

    top_n_bar = st.slider("Show top N themes", min_value=5, max_value=min(30, len(summary)),
                          value=min(15, len(summary)), key="cluster_bar_n")
    bar_data = summary.head(top_n_bar).copy()

    fig_cl = px.bar(
        bar_data,
        x="count",
        y="label_short",
        orientation="h",
        color="severity_band",
        color_discrete_map=SEV_BAND_COLORS,
        hover_data={"count": True, "avg_sev": ":.2f", "modules": True,
                    "severity_band": False, "label_short": False},
        labels={"count": "Number of Bugs", "label_short": "Theme (keywords)",
                "avg_sev": "Avg Severity (1=Critical, 4=Minor)",
                "modules": "Modules affected"},
        text="count",
    )
    fig_cl.update_layout(
        height=max(380, top_n_bar * 30),
        yaxis={"categoryorder": "total ascending"},
        showlegend=True,
        legend_title_text="Severity",
        margin=dict(l=10, r=10, t=10, b=10),
    )
    fig_cl.update_traces(textposition="outside")
    st.plotly_chart(fig_cl, width='stretch')

    # ── Per-cluster detail cards ─────────────────────────────────────────
    st.markdown("---")
    st.subheader("🃏 Theme Detail Cards")
    st.caption(
        "Each card shows one recurring bug theme. "
        "Expand a card to see which modules are affected and example bugs."
    )

    for _, row in summary.head(top_n_bar).iterrows():
        cid    = row.get("cluster_id", -1)
        label  = str(row.get("cluster_label", "Unknown"))
        count  = int(row.get("count", 0))
        avg_sv = float(row.get("avg_sev", 3.0))
        mods   = str(row.get("modules", ""))
        sband  = row.get("severity_band", "🟡 Mostly Normal")
        rank   = summary.index[summary["cluster_label"] == label].tolist()
        rank_n = (rank[0] + 1) if rank else "?"

        # Pre-compute velocity/trend/recurrence so they can appear in the header
        vel_val    = float(row.get("cluster_velocity_ratio", 1.0))
        trend_val  = str(row.get("cluster_trend", "stable"))
        recur_val  = float(row.get("recurrence_rate", 0.0))
        trend_icon = {"growing": "🔺", "declining": "✅", "stable": "➡️"}.get(trend_val, "➡️")
        sev_icon   = {"🔴 Mostly Critical": "🔴", "🟠 Mostly Major": "🟠",
                      "🟡 Mostly Normal": "🟡", "🟢 Mostly Minor": "🟢"}.get(sband, "⚪")

        card_title = (
            f"{sev_icon} Theme #{rank_n} · **{count} bugs** "
            f"· {trend_icon} {trend_val} ({vel_val:.1f}×) · _{label}_"
        )

        with st.expander(card_title, expanded=(rank_n == 1)):
            cc1, cc2, cc3 = st.columns(3)
            cc1.metric("Bugs in theme", count)
            cc2.metric("Avg severity", f"{avg_sv:.1f}",
                       help="1=Critical  2=Major  3=Normal  4=Minor")
            pct = f"{count / n_clustered * 100:.1f}%" if n_clustered else "—"
            cc3.metric("Share of clustered bugs", pct)

            # Velocity / trend / recurrence row
            vc1, vc2, vc3 = st.columns(3)
            vel_delta_color = "inverse" if trend_val == "declining" else ("off" if trend_val == "stable" else "normal")
            vc1.metric("Velocity (recent vs prior 3 builds)", f"{vel_val:.2f}×",
                       delta=f"{trend_icon} {trend_val.capitalize()}",
                       delta_color=vel_delta_color,
                       help="Ratio of bugs in the last 3 builds vs the 3 builds before that. Above 1.5 = growing.")
            vc2.metric("Recurrence rate", f"{recur_val * 100:.0f}%",
                       help="Fraction of recent bugs from modules that also contributed to this theme in the prior window. High = fix not holding.")
            recur_label = "⚠️ Fix not holding" if recur_val > 0.5 else ("🔶 Moderate" if recur_val > 0.25 else "✅ Low")
            vc3.metric("Recurrence risk", recur_label)

            if mods:
                st.markdown(f"**Modules affected:** {mods}")

            # Sample bugs from this cluster
            if cluster_df is not None and "cluster_id" in cluster_df.columns:
                cl_bugs = cluster_df[cluster_df["cluster_id"] == cid]
                if "parsed_description" in cl_bugs.columns:
                    samples = (
                        cl_bugs[cl_bugs["parsed_description"].notna()]
                        [["parsed_module", "parsed_description", "severity_label"]]
                        .head(6)
                        .rename(columns={
                            "parsed_module": "Module",
                            "parsed_description": "Bug description",
                            "severity_label": "Severity",
                        })
                    )
                    if not samples.empty:
                        st.markdown("**Sample bugs in this theme:**")
                        st.dataframe(samples, hide_index=True, width='stretch')

            # Plain-English action — severity-based baseline
            if avg_sv <= 1.5:
                action = "🚨 **Immediate attention** — crash-level or data-loss bugs. Escalate to RD."
            elif avg_sv <= 2.5:
                action = "⚠️ **High priority** — major functionality issues. Add to next sprint regression."
            elif avg_sv <= 3.5:
                action = "📋 **Standard priority** — normal-severity issues. Cover in release-candidate testing."
            else:
                action = "✅ **Low priority** — mostly cosmetic or minor issues. Full release cycle."
            # Amplify when velocity or recurrence signals override the severity baseline
            if trend_val == "growing" and vel_val >= 1.5:
                action += f"  🔺 **Growing fast ({vel_val:.1f}×)** — prioritise this theme in the next build regardless of total count."
            if recur_val >= 0.5:
                action += "  🔁 **High recurrence** — same modules keep appearing; underlying root cause may not be resolved."
            st.info(action)

    # ── Raw summary table ────────────────────────────────────────────────
    with st.expander("📋 Full theme table (raw data)"):
        display_cols_sum = [c for c in ["cluster_label", "count", "avg_sev", "modules",
                                         "severity_band", "cluster_velocity_ratio",
                                         "cluster_trend", "recurrence_rate"]
                            if c in summary.columns]
        disp_names = {
            "cluster_label": "Theme keywords", "count": "Bug count",
            "avg_sev": "Avg severity (1–4)", "modules": "Modules affected",
            "severity_band": "Severity band",
            "cluster_velocity_ratio": "Velocity ratio",
            "cluster_trend": "Trend",
            "recurrence_rate": "Recurrence rate",
        }
        display_sum = summary[display_cols_sum].rename(columns=disp_names)
        st.dataframe(display_sum, hide_index=True, width='stretch')

    # ── NEW v3.0 — Cluster velocity chart ───────────────────────────────
    if "cluster_velocity_ratio" in summary.columns and "cluster_trend" in summary.columns:
        st.markdown("---")
        st.subheader("📈 Cluster Velocity — Which Themes Are Growing?")
        st.caption(
            "Velocity ratio compares bug count in the most recent 3 builds vs the prior 3 builds. "
            "**Above 1.5** = theme is growing (🔺 alert). **Below 0.67** = theme is declining (✅ improving). "
            "Focus testing effort on growing themes."
        )

        vel_df = summary.copy()
        vel_df["cluster_velocity_ratio"] = pd.to_numeric(
            vel_df["cluster_velocity_ratio"], errors="coerce").fillna(1.0)
        # Show all clusters sorted by velocity descending — most urgent at top
        vel_df = vel_df.sort_values("cluster_velocity_ratio", ascending=False).head(20)

        vel_df["trend_color"] = vel_df["cluster_trend"].map({
            "growing":   "🔺 Growing",
            "stable":    "➡️ Stable",
            "declining": "✅ Declining",
        }).fillna("➡️ Stable")

        TREND_COLORS = {
            "🔺 Growing":   "#ef4444",
            "➡️ Stable":    "#94a3b8",
            "✅ Declining": "#22c55e",
        }
        fig_vel = px.bar(
            vel_df,
            x="cluster_velocity_ratio",
            y="label_short",
            orientation="h",
            color="trend_color",
            color_discrete_map=TREND_COLORS,
            hover_data={"cluster_velocity_ratio": ":.2f",
                        "count": True, "modules": True,
                        "label_short": False, "trend_color": False},
            labels={"cluster_velocity_ratio": "Velocity ratio (recent / prior 3 builds)",
                    "label_short": "Theme",
                    "count": "Total bugs",
                    "modules": "Modules"},
            text="cluster_velocity_ratio",
        )
        fig_vel.update_traces(texttemplate="%{text:.2f}×", textposition="outside")
        fig_vel.add_vline(x=1.0, line_color="gray", line_width=1, line_dash="dash")
        fig_vel.add_vline(x=1.5, line_color="#ef4444", line_width=1, line_dash="dot",
                          annotation_text="Growing threshold", annotation_position="top right")
        fig_vel.update_layout(
            height=max(320, len(vel_df) * 28),
            yaxis={"categoryorder": "total ascending"},
            showlegend=True, legend_title_text="Trend",
            margin=dict(l=10, r=120, t=20, b=10),
        )
        st.plotly_chart(fig_vel, width='stretch')

    # ── NEW v3.0 — Module cluster entropy table ──────────────────────────
    # Use loaded entropy CSV if available; otherwise compute directly from cluster_df
    _ent_source_df = cluster_ent_df
    if _ent_source_df is None and cluster_df is not None and "cluster_id" in cluster_df.columns:
        _clustered_only = cluster_df[(cluster_df["cluster_id"] != -1)].dropna(
            subset=["parsed_module", "cluster_id"])
        _ent_rows = []
        for _mod, _grp in _clustered_only.groupby("parsed_module"):
            _counts = _grp["cluster_id"].value_counts().values.astype(float)
            _total  = _counts.sum()
            if _total > 0:
                _p   = _counts / _total
                _ent = float(-np.sum(_p * np.log2(_p + 1e-12)))
                _ent_rows.append({"module": _mod, "cluster_entropy": round(_ent, 3)})
        if _ent_rows:
            _ent_source_df = pd.DataFrame(_ent_rows)

    if _ent_source_df is not None and not _ent_source_df.empty:
        st.markdown("---")
        st.subheader("🔀 Module Cluster Entropy — Breadth of Bug Themes")
        with st.expander("ℹ️ What is cluster entropy?", expanded=False):
            st.markdown("""
**Entropy** measures how spread out a module's bugs are across different themes.

| Entropy | Meaning | Action |
|---------|---------|--------|
| **Low (< 1)** | Bugs concentrated in one theme — focused failure mode | Easy to fix: one root cause to address |
| **Medium (1–2)** | Bugs spread across a few themes | Review whether themes share a common component |
| **High (> 2)** | Bugs spread across many themes — broad instability | Module needs comprehensive regression testing |
""")
        ent_display = _ent_source_df.copy()
        ent_display["cluster_entropy"] = pd.to_numeric(
            ent_display["cluster_entropy"], errors="coerce").fillna(0.0)
        ent_display = ent_display.sort_values("cluster_entropy", ascending=False)
        ent_display["stability"] = ent_display["cluster_entropy"].apply(
            lambda v: "⚠️ Broad instability" if v > 2 else
                      "🔶 Spreading" if v > 1 else
                      "✅ Focused"
        )
        fig_ent = px.bar(
            ent_display.head(20),
            x="cluster_entropy", y="module", orientation="h",
            color="stability",
            color_discrete_map={
                "⚠️ Broad instability": "#ef4444",
                "🔶 Spreading":         "#f97316",
                "✅ Focused":           "#22c55e",
            },
            labels={"cluster_entropy": "Entropy (higher = more spread)",
                    "module": "Module", "stability": "Theme breadth"},
            text="cluster_entropy",
        )
        fig_ent.update_traces(texttemplate="%{text:.2f}", textposition="outside")
        fig_ent.add_vline(x=1.0, line_color="#f97316", line_width=1, line_dash="dash")
        fig_ent.add_vline(x=2.0, line_color="#ef4444", line_width=1, line_dash="dash")
        fig_ent.update_layout(
            height=max(300, min(20, len(ent_display)) * 30),
            yaxis={"categoryorder": "total ascending"},
            showlegend=True, legend_title_text="Theme breadth",
            margin=dict(l=10, r=80, t=10, b=10),
        )
        st.plotly_chart(fig_ent, width='stretch')

    # ── NEW v3.0 — Severity-stratified cluster views ─────────────────────
    if cluster_s12_df is not None or cluster_s34_df is not None:
        st.markdown("---")
        st.subheader("🔬 Severity-Stratified Themes")
        st.caption(
            "Clusters run separately for S1/S2 (crash/major) and S3/S4 (normal/minor) bugs. "
            "The global view above mixes all severities; these tabs show each tier in isolation."
        )
        tier_tabs = st.tabs(["🔴 Critical/Major themes (S1–S2)", "🟡 Normal/Minor themes (S3–S4)"])

        for tab_widget, tier_df, tier_name in [
            (tier_tabs[0], cluster_s12_df, "S1/S2"),
            (tier_tabs[1], cluster_s34_df, "S3/S4"),
        ]:
            with tab_widget:
                if tier_df is None or tier_df.empty:
                    st.info(f"No stratified cluster data for {tier_name}. "
                            f"Re-run with `--stratify-severity` flag.")
                    continue

                for col, default in [("count", 0), ("avg_sev", 3.0), ("modules", ""),
                                     ("cluster_velocity_ratio", 1.0), ("cluster_trend", "stable"),
                                     ("recurrence_rate", 0.0)]:
                    if col not in tier_df.columns:
                        tier_df[col] = default

                tier_df = tier_df.copy()
                tier_df["label_short"] = tier_df["cluster_label"].apply(
                    lambda s: s[:45] + "…" if isinstance(s, str) and len(s) > 45 else s)
                tier_df["avg_sev"]   = pd.to_numeric(tier_df["avg_sev"], errors="coerce").fillna(3.0)
                tier_df["count"]     = pd.to_numeric(tier_df["count"],   errors="coerce").fillna(0).astype(int)
                tier_df["cluster_velocity_ratio"] = pd.to_numeric(
                    tier_df["cluster_velocity_ratio"], errors="coerce").fillna(1.0)
                tier_df = tier_df.sort_values("count", ascending=False)

                # Overview bar
                fig_tier = px.bar(
                    tier_df.head(15),
                    x="count", y="label_short", orientation="h",
                    color="cluster_trend",
                    color_discrete_map={"growing": "#ef4444", "stable": "#94a3b8",
                                        "declining": "#22c55e"},
                    hover_data={"count": True, "avg_sev": ":.2f", "modules": True,
                                "cluster_velocity_ratio": ":.2f", "recurrence_rate": ":.0%"},
                    labels={"count": "Bugs", "label_short": "Theme",
                            "cluster_trend": "Trend",
                            "cluster_velocity_ratio": "Velocity",
                            "recurrence_rate": "Recurrence"},
                    text="count",
                )
                fig_tier.update_traces(textposition="outside")
                fig_tier.update_layout(
                    height=max(300, min(15, len(tier_df)) * 30),
                    yaxis={"categoryorder": "total ascending"},
                    showlegend=True, legend_title_text="Trend",
                    margin=dict(l=10, r=40, t=10, b=10),
                )
                st.plotly_chart(fig_tier, width='stretch')

                # Detail cards for top 10
                st.markdown(f"**Theme detail cards — {tier_name} tier**")
                tier_n_clustered = tier_df["count"].sum()
                for _ti, _tr in tier_df.head(10).iterrows():
                    _cid   = _tr.get("cluster_id", -1)
                    _label = str(_tr.get("cluster_label", "Unknown"))
                    _count = int(_tr.get("count", 0))
                    _avsev = float(_tr.get("avg_sev", 3.0))
                    _mods  = str(_tr.get("modules", ""))
                    _vel   = float(_tr.get("cluster_velocity_ratio", 1.0))
                    _trend = str(_tr.get("cluster_trend", "stable"))
                    _recur = float(_tr.get("recurrence_rate", 0.0))
                    _sev_icon = "🔴" if _avsev <= 1.5 else "🟠" if _avsev <= 2.5 else "🟡" if _avsev <= 3.5 else "🟢"
                    _trend_icon = {"growing": "🔺", "declining": "✅", "stable": "➡️"}.get(_trend, "➡️")
                    _title = f"{_sev_icon} **{_count} bugs** · {_trend_icon} {_trend} · _{_label}_"
                    with st.expander(_title, expanded=False):
                        _c1, _c2, _c3 = st.columns(3)
                        _c1.metric("Bugs", _count)
                        _c2.metric("Avg severity", f"{_avsev:.1f}")
                        _c3.metric("Share of tier", f"{_count / max(tier_n_clustered, 1) * 100:.1f}%")
                        _v1, _v2 = st.columns(2)
                        _v1.metric("Velocity", f"{_vel:.2f}×",
                                   delta=f"{_trend_icon} {_trend.capitalize()}",
                                   delta_color="inverse" if _trend == "declining" else
                                              ("off" if _trend == "stable" else "normal"))
                        _v2.metric("Recurrence", f"{_recur * 100:.0f}%",
                                   help="Fraction of recent bugs from modules that contributed in prior window")
                        if _mods:
                            st.markdown(f"**Modules:** {_mods}")
                        # Sample bugs for this tier cluster
                        _id_col = "cluster_id_s12" if tier_name == "S1/S2" else "cluster_id_s34"
                        if cluster_df is not None and _id_col in cluster_df.columns:
                            _cl_bugs = cluster_df[cluster_df[_id_col] == _cid]
                        elif cluster_df is not None and "cluster_id" in cluster_df.columns:
                            _cl_bugs = cluster_df[cluster_df["cluster_id"] == _cid]
                        else:
                            _cl_bugs = pd.DataFrame()
                        if not _cl_bugs.empty and "parsed_description" in _cl_bugs.columns:
                            _samples = (
                                _cl_bugs[_cl_bugs["parsed_description"].notna()]
                                [["parsed_module", "parsed_description", "severity_label"]]
                                .head(5)
                                .rename(columns={"parsed_module": "Module",
                                                 "parsed_description": "Bug description",
                                                 "severity_label": "Severity"})
                            )
                            if not _samples.empty:
                                st.dataframe(_samples, hide_index=True, width='stretch')


# =====================================================================
# TAB 9 – Defect Forecast (v2.21 — scenario-based layout)
# =====================================================================

elif active_tab == "🔮 Defect Forecast":
    st.header("🔮 Defect Forecast — What's Likely to Break")

    with st.expander("📖 How to read this tab — full guide", expanded=False):
        st.markdown("""
## 🔮 Defect Forecast — Complete Guide

This tab shows you **what is likely to break in the next build** and **what concrete scenarios to test for**, driven by a machine-learning model trained on all historical ECL bug data.

---

### 📊 Headline Metrics (Top Row)

| Metric | What it means |
|--------|--------------|
| **🔴 Critical modules** | Modules with composite risk score > 90 — must be tested every build |
| **🟠 High-risk modules** | Modules with composite risk score 70–90 — test every sprint |
| **🎯 Predicted scenarios** | Total count of concrete, testable bug scenario predictions across all modules |
| **Total modules forecast** | Number of modules for which the model generated a prediction |

---

### 🎯 What to Test Next Build (Primary Section)

The top section shows the core output: human-readable bug scenarios grouped by risk level, listed from most to least urgent (Critical → High → Medium). Each scenario directly answers the question: *"What specific thing should I test?"*

**Confidence levels** are based on how many times a similar bug has recurred in history:
- **🟢 High** — 3 or more recurring bugs with similar descriptions (well-established recurring pattern)
- **🟡 Medium** — 2 similar bugs (emerging pattern, include in testing)
- **🔵 Low** — 1 occurrence (speculative, add to exploratory pass for Critical/High modules)

**How scenarios are generated (the algorithm):**
For each high-risk module, `predict_defects.py` runs these steps:
1. Collects all bug Short Descriptions from the last 5 builds for that module
2. Vectorises them using TF-IDF and clusters similar descriptions with `AgglomerativeClustering` (cosine distance threshold)
3. Picks the most representative description from each cluster as the predicted scenario text
4. Assigns confidence based on the cluster size (bugs in cluster → High/Medium/Low)
5. When running with `--provider ollama` or `--provider claude`, an AI model receives the clusters and historical context to produce more specific, actionable scenario text grounded in real patterns

---

### 📋 AI Risk Briefing — Next Build Focus Summary

A collapsible plain-English executive summary generated by `predict_defects.py`. Lists the top-risk modules with:
- Why they are at risk (which signals are currently elevated)
- What specifically to test based on historical patterns

Generated with Ollama or Claude when `--provider` is set; a heuristic text summary is produced even without AI.

---

### ⚠️ Severity Escalation Alerts

Shown automatically for modules where bugs are getting **more severe** in recent builds.

**How `severity_escalation` is calculated:**
```
severity_escalation = avg_severity(last build) − avg_severity(prior 3 builds)
```
Since severity is numbered 1 (Critical) → 4 (Minor), a **negative value** means severity is worsening toward S1. Modules with `severity_escalation < −0.3` appear here; a value of −0.8 or worse gets the 🚨 icon.

This signal can detect instability **before** bug counts spike — a pre-emptive warning that regression testing should be intensified immediately.

---

### 🕒 Overdue for a Critical Bug

Shown for modules that historically produced S1 (crash-level) bugs regularly, but have been quiet for 5–20 recent builds. These modules are **not** fixed — they may be accumulating risk silently.

**How `builds_since_last_crit` is calculated:** For each module's training history, the feature records how many build slots have elapsed since the most recent row where `critical_count > 0`. If no critical bugs exist at all in history, the value equals the total length of that module's history (maximum caution). Modules quiet for >20 builds are excluded (likely genuinely resolved).

---

### 📋 Predicted Bug Categories — What Will Break?

A stacked bar chart (one bar per module, stacked by category) showing which **types** of bugs are expected based on the category distribution of recent bugs.

**The 6 QA categories** are assigned by keyword scoring on each bug's Short Description. Whichever category accumulates the most keyword hits wins:

| Category | Example trigger keywords |
|----------|------------------------|
| Crash / Stability | crash, exception, fatal, force close, hang, freeze |
| Feature not working as intended | not work, incorrect behavior, broken, regression |
| UI / Display problem | layout, alignment, render, icon, overlap, truncat |
| UX / Usability problem | confusing, navigation, slow, hard to find, unclear |
| Translation / Localization | translation, missing string, language, zh-tw, locale |
| Data / File / Sync issue | corrupt, import/export, sync, cannot save, wrong data |

`historical_pct` = this category's share of bugs in recent builds.
`expected_next_build` = `historical_pct × predicted_total` — how many bugs of this type are expected.

---

### 🔍 Predicted Bug Scenarios by Module

Expandable per-module section showing every predicted scenario with confidence badge, supporting QA categories, and the real source bug descriptions it was derived from. Critical-risk modules auto-expand; others are collapsed by default.

---

### 🃏 Module Forecast Cards

One card per Critical or High-risk module (top 10). Each card contains:

| Field | Explanation |
|-------|-------------|
| **Risk level** | Composite risk category (Critical/High/Medium/Low) |
| **Bug categories expected** | Count of distinct QA categories predicted for this module next build |
| **Actual last build** | Real observed bug count from the most recent build in training data — compare to the forecast for calibration |
| **Severity trend** | `severity_escalation` — see Alerts section above |
| **Builds since last critical** | `builds_since_last_crit` — see Overdue section above |
| **Theme breadth (entropy)** | Shannon entropy of bug-theme distribution. Higher = bugs spread across many themes (broad instability). Lower = concentrated single failure mode. |
| **Leading signal** | The single feature with the highest Pearson correlation to future bug count for this module |
| **What types of bugs to expect** | Per-category breakdown from `_predictions_by_category.csv` showing historical % and expected count |
| **Predicted bug scenarios** | Top 3 scenarios from `_predictions_by_scenario.csv` |
| **AI risk briefing** | Narrative from Ollama/Claude when `--provider` is set |
| **What to test** | Concise test instruction based on risk level |

---

### 📡 Leading Indicators — What Predicts Future Bugs?

A horizontal bar chart ranking features by **Pearson correlation coefficient (r)** with future bug counts.

**How to read the chart:**
- **r close to +1 (red bar, right)** — when this signal goes up, more bugs follow in the next build
- **r close to −1 (green bar, left)** — when this signal goes up, fewer bugs follow (protective signal)
- **r near 0** — no consistent predictive relationship

The correlations are computed across all (module, build) rows in the training dataset by comparing each feature column against the `target` (actual bug count in the next build).

**Features ranked here include:**
- `crit_1/3/5` — critical bug momentum over last 1/3/5 builds
- `bugs_1/3/5` — total bug count momentum over last 1/3/5 builds
- `sev_1/3/5` — severity-weighted momentum
- `trend` — last build minus 3 builds ago (upward slope)
- `severity_escalation` — worsening severity signal
- `builds_since_last_crit` — how long since the last S1
- `cluster_entropy_3/5` — bug-theme diversity index (when cluster data is loaded)
- `top_cluster_velocity` — growth rate of the dominant bug theme
- TF-IDF text features — keyword loadings from module descriptions
- Risk features (when loaded) — impact, detectability, probability scores from `ai_risk_scorer.py`

---

### 🔧 Advanced / Model Diagnostics (collapsed by default)

- **Predicted Bug Count bar chart** — raw predicted count per module coloured by risk level. Use the slider to control how many modules are shown.
- **Actual vs Predicted** — side-by-side bars comparing the model's last-build forecast against the real observed count. Useful for building trust in the model's accuracy before acting on it.
- **Module Signals Table** — sortable table of all numeric signals for every module.
- **Full Predictions Table** — complete output of `_predictions.csv`.

---

### 🛠️ How the ML Model Works

**Algorithm:** `GradientBoostingRegressor` (scikit-learn, 200 trees, max_depth=4, learning_rate=0.1, random_state=42). Trained to predict the bug count in the next build for each module.

**Training validation:** 3-fold `TimeSeriesSplit` cross-validation (respects time ordering — no data leakage). The CV MAE (Mean Absolute Error) is printed at run time: it tells you how many bugs off the forecast is on average.

**Feature matrix:** Built by `build_features()` in `predict_defects.py`. Each row is a (module, build) pair. Features are computed as rolling window statistics from the prior 1/3/5 builds. Requires at least **5 builds of history** per module; modules with less are excluded.

**Risk level assignment:** Risk levels are **not directly from the model output**. They are assigned from a composite risk score that weights: predicted bug count, severity escalation trend, domain impact score (from `ai_risk_scorer.py` if loaded), and historical probability score.

Thresholds: Critical > 90, High 70–90, Medium 50–69, Low < 50.

---

### 🛠️ Where Does This Data Come From?

```bash
# Basic run (heuristic scenarios, no AI needed):
python scripts/predict_defects.py data/ecl_parsed.csv

# With cluster features (enables entropy signals and better accuracy):
python scripts/predict_defects.py data/ecl_parsed.csv \\
  --cluster-csv data/clusters/ecl_parsed_clustered.csv

# Full AI-powered scenarios:
python scripts/predict_defects.py data/ecl_parsed.csv \\
  --cluster-csv data/clusters/ecl_parsed_clustered.csv \\
  --provider ollama --model llama3.1

# Also load I×P×D risk features:
python scripts/predict_defects.py data/ecl_parsed.csv \\
  --scored-csv data/risk_register_scored.csv
```

Output files saved to `data/predictions/`:

| File | Contents |
|------|---------|
| `_predictions.csv` | Main forecast — predicted count, risk level, all signals |
| `_predictions_by_category.csv` | Per-module per-category historical % and expected count |
| `_predictions_by_scenario.csv` | Concrete bug scenario predictions with confidence |
| `_predictions_by_cluster.csv` | Per-module per-cluster breakdown (requires `--cluster-csv`) |
| `_predictions_importance.csv` | Feature importance ranking from the trained model |
| `_predictions_leading_indicators.csv` | Pearson r correlation of each feature with future bugs |
| `_predictions_focus_summary.txt` | Plain-English risk briefing text |

**Recommend re-running every Friday** alongside `cluster_bugs.py` so Tabs 8 and 9 stay in sync.
""")

    if pred_df is None:
        st.warning(
            "**Prediction data not loaded.** "
            "Run `python scripts/predict_defects.py data/ecl_parsed.csv` then set the path in **Sidebar → Step 4**."
        )
        st.stop()

    required_pred_cols = {"module", "predicted", "risk_level"}
    if not required_pred_cols.issubset(pred_df.columns):
        st.error(f"Predictions file missing columns: {required_pred_cols - set(pred_df.columns)}")
        st.stop()

    # Build _ent_source_df for module entropy lookup (same fallback logic as Tab 8)
    _ent_source_df = cluster_ent_df
    if _ent_source_df is None and cluster_df is not None and "cluster_id" in cluster_df.columns:
        _ec_only = cluster_df[(cluster_df["cluster_id"] != -1)].dropna(
            subset=["parsed_module", "cluster_id"])
        _ent_rows_t9 = []
        for _mod, _grp in _ec_only.groupby("parsed_module"):
            _c = _grp["cluster_id"].value_counts().values.astype(float)
            if _c.sum() > 0:
                _p = _c / _c.sum()
                _ent_rows_t9.append({"module": _mod,
                                     "cluster_entropy": round(float(-np.sum(_p * np.log2(_p + 1e-12))), 3)})
        if _ent_rows_t9:
            _ent_source_df = pd.DataFrame(_ent_rows_t9)

    pred_df["predicted"] = pd.to_numeric(pred_df["predicted"], errors="coerce").fillna(0)
    pred_df = pred_df.sort_values("predicted", ascending=False).reset_index(drop=True)

    FORECAST_COLORS = {
        "Critical": "#ef4444",
        "High":     "#f97316",
        "Medium":   "#eab308",
        "Low":      "#22c55e",
    }
    RISK_ORDER = ["Critical", "High", "Medium", "Low"]
    RISK_ICONS   = {"Critical": "🔴", "High": "🟠", "Medium": "🟡", "Low": "🟢"}
    RISK_ADVICE  = {
        "Critical": "Test **every build**. Focus on crash scenarios, data loss, and any recently changed functionality.",
        "High":     "Test **every sprint**. Run full regression for this module and check for side effects in related areas.",
        "Medium":   "Include in **release-candidate** testing. Spot-check changed areas.",
        "Low":      "Cover in the full **release cycle** pass. No special urgency.",
    }
    _CONF_BADGES = {"high": "🟢 High", "medium": "🟡 Medium", "low": "🔵 Low"}

    # ── Headline metrics ─────────────────────────────────────────────────
    rl_counts = pred_df["risk_level"].value_counts()
    _scenario_count = len(pred_scenario_df) if pred_scenario_df is not None else 0
    pm1, pm2, pm3, pm4 = st.columns(4)
    pm1.metric("🔴 Critical modules",  int(rl_counts.get("Critical", 0)),
               help="Composite risk >90 (domain risk + predicted count + severity trend + momentum)")
    pm2.metric("🟠 High-risk modules", int(rl_counts.get("High", 0)),
               help="Composite risk 70–90 (domain risk + predicted count + severity trend + momentum)")
    pm3.metric("🎯 Predicted scenarios", _scenario_count,
               help="Total concrete bug scenario predictions across all modules")
    pm4.metric("Total modules forecast", len(pred_df))

    # ── "What to Test Next Build" — PRIMARY SECTION ──────────────────────
    st.markdown("---")
    st.subheader("🎯 What to Test Next Build")

    if pred_scenario_df is not None and not pred_scenario_df.empty:
        st.caption(
            "Concrete bug scenarios predicted for the next build, grouped by risk level. "
            "Each scenario is grounded in recurring historical patterns."
        )
        # Group scenarios by risk level
        for _rl in ["Critical", "High", "Medium"]:
            _rl_scenarios = pred_scenario_df[pred_scenario_df["risk_level"] == _rl]
            if _rl_scenarios.empty:
                continue
            _rl_icon = RISK_ICONS.get(_rl, "⚪")
            st.markdown(f"#### {_rl_icon} {_rl} Risk")
            for _mod, _mod_sc in _rl_scenarios.groupby("module", sort=False):
                st.markdown(f"**{_mod}**")
                for _, _sc in _mod_sc.head(3).iterrows():
                    _conf = _CONF_BADGES.get(str(_sc.get("confidence", "medium")), "🔵 Low")
                    _text = str(_sc.get("scenario_text", ""))
                    st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;{_conf} — {_text}",
                                unsafe_allow_html=True)
            st.markdown("")
    else:
        # Fallback to AI Focus Summary if no scenarios
        if pred_summary_txt and pred_summary_txt.strip():
            st.caption(
                "Scenario predictions not available. Showing AI Focus Summary instead. "
                "Re-run predict_defects.py v5.0 to generate scenario predictions."
            )
            st.code(pred_summary_txt, language=None)
        else:
            st.info(
                "No scenario predictions available. Run `predict_defects.py` v5.0 "
                "to generate concrete bug scenario predictions."
            )

    # ── AI Focus Summary ─────────────────────────────────────────────────
    if pred_summary_txt and pred_summary_txt.strip():
        with st.expander("📋 AI Risk Briefing — Next Build Focus Summary", expanded=False):
            st.caption(
                "Plain-English summary generated by predict_defects.py. "
                "Lists the highest-risk modules with why they are at risk and what to test."
            )
            st.code(pred_summary_txt, language=None)

    st.markdown("---")

    # ── Severity escalation alerts ───────────────────────────────────────
    if "severity_escalation" in pred_df.columns:
        _esc = pred_df[["module", "predicted", "risk_level", "severity_escalation"]].copy()
        _esc["severity_escalation"] = pd.to_numeric(_esc["severity_escalation"], errors="coerce")
        _worsening = _esc[_esc["severity_escalation"] < -0.3].sort_values(
            "severity_escalation").head(8)
        if not _worsening.empty:
            st.subheader("⚠️ Severity Escalation Alerts")
            st.caption(
                "These modules have bugs getting **more severe** in recent builds — "
                "the average severity is trending toward Critical (S1). "
                "Flag for immediate investigation even if raw bug counts look low."
            )
            for _, _er in _worsening.iterrows():
                _esc_val = float(_er["severity_escalation"])
                _esc_icon = "🚨" if _esc_val < -0.8 else "⚠️"
                st.warning(
                    f"{_esc_icon} **{_er['module']}** — severity worsening by "
                    f"{abs(_esc_val):.2f} points toward S1 "
                    f"(predicted {_er['predicted']:.0f} bugs, {_er['risk_level']} risk)"
                )
            st.markdown("---")

    # ── Builds since last critical summary ────────────────────────────────
    if "builds_since_last_crit" in pred_df.columns:
        _bslc = pred_df[["module", "predicted", "risk_level", "builds_since_last_crit"]].copy()
        _bslc["builds_since_last_crit"] = pd.to_numeric(
            _bslc["builds_since_last_crit"], errors="coerce")
        _overdue = _bslc[
            (_bslc["builds_since_last_crit"] >= 5) &
            (_bslc["builds_since_last_crit"] <= 20) &
            (~_bslc["risk_level"].isin(["Low"]))
        ].sort_values("builds_since_last_crit").head(6)
        if not _overdue.empty:
            with st.expander(
                f"🕒 {len(_overdue)} module(s) overdue for a critical bug — had S1s historically, none recently",
                expanded=True,
            ):
                st.caption(
                    "Modules that have produced critical bugs in the past but have been quiet for 5–20 builds. "
                    "They are not 'fixed' — they may be accumulating risk. Monitor closely."
                )
                for _, _od in _overdue.iterrows():
                    st.info(
                        f"**{_od['module']}** — {int(_od['builds_since_last_crit'])} builds "
                        f"since last critical · predicted {_od['predicted']:.0f} bugs · {_od['risk_level']} risk"
                    )
            st.markdown("---")

    # ── Bug Categories stacked bar chart (promoted from old position) ─────
    # Compute top_n_pred for charts
    _pred_n = len(pred_df)
    if _pred_n <= 1:
        top_n_pred = _pred_n
    else:
        top_n_pred = min(20, _pred_n)

    if pred_category_df is not None and not pred_category_df.empty:
        st.subheader("📋 Predicted Bug Categories — What Will Break?")
        st.caption(
            "Each bar shows which types of bugs are expected per module, based on "
            "recent bug descriptions. This tells you WHAT to test for, not just how many."
        )
        _top_mods = pred_df.head(top_n_pred)["module"].tolist()
        _cat_chart = pred_category_df[pred_category_df["module"].isin(_top_mods)].copy()
        if not _cat_chart.empty:
            _CAT_COLORS = {
                "Crash / Stability":              "#ef4444",
                "Feature not working as intended": "#f97316",
                "UI / Display problem":            "#eab308",
                "UX / Usability problem":          "#a855f7",
                "Translation / Localization":      "#3b82f6",
                "Data / File / Sync issue":        "#06b6d4",
            }
            _mod_order = [m for m in _top_mods if m in _cat_chart["module"].values]
            _cat_chart["module"] = pd.Categorical(
                _cat_chart["module"], categories=_mod_order, ordered=True)
            _cat_chart = _cat_chart.sort_values(["module", "expected_next_build"],
                                                ascending=[True, False])
            fig_cat = px.bar(
                _cat_chart,
                x="module", y="historical_pct", color="category",
                color_discrete_map=_CAT_COLORS,
                labels={"module": "Module", "historical_pct": "Proportion of recent bugs",
                        "category": "Bug Category"},
                hover_data={"historical_count": True, "expected_next_build": ":.1f"},
                barmode="stack",
            )
            fig_cat.update_traces(texttemplate="", textposition="inside")
            fig_cat.update_layout(
                height=460,
                xaxis_tickangle=-35,
                yaxis_tickformat=".0%",
                showlegend=True,
                legend_title_text="Bug Category",
                margin=dict(t=20, b=10),
            )
            st.plotly_chart(fig_cat, width='stretch')
        st.markdown("---")

    # ── Predicted Bug Scenarios by Module (NEW detail section) ────────────
    if pred_scenario_df is not None and not pred_scenario_df.empty:
        st.subheader("🔍 Predicted Bug Scenarios by Module")
        st.caption(
            "Expand each module to see all predicted scenarios with confidence levels, "
            "supporting categories, and source bug examples."
        )
        _scenario_modules = pred_scenario_df["module"].unique()
        for _sc_mod in _scenario_modules:
            _sc_mod_data = pred_scenario_df[pred_scenario_df["module"] == _sc_mod]
            _sc_rl = str(_sc_mod_data.iloc[0].get("risk_level", "Medium"))
            _sc_icon = RISK_ICONS.get(_sc_rl, "⚪")
            _sc_count = len(_sc_mod_data)
            with st.expander(
                f"{_sc_icon} **{_sc_mod}** — {_sc_rl} risk · {_sc_count} scenario(s)",
                expanded=(_sc_rl == "Critical"),
            ):
                for _, _sc_row in _sc_mod_data.iterrows():
                    _rank = int(_sc_row.get("scenario_rank", 0))
                    _text = str(_sc_row.get("scenario_text", ""))
                    _conf = str(_sc_row.get("confidence", "medium"))
                    _conf_badge = _CONF_BADGES.get(_conf, "🔵 Low")
                    _cats = str(_sc_row.get("supporting_categories", ""))
                    _examples = str(_sc_row.get("source_bug_examples", ""))

                    st.markdown(f"**#{_rank}** {_conf_badge} — {_text}")
                    _detail_parts = []
                    if _cats and _cats != "nan":
                        _detail_parts.append(f"Categories: {_cats}")
                    if _examples and _examples != "nan" and len(_examples) > 5:
                        _detail_parts.append(f"Based on: _{_examples[:200]}_")
                    if _detail_parts:
                        st.caption(" · ".join(_detail_parts))
                    st.markdown("")
        st.markdown("---")

    # ── Module forecast cards ─────────────────────────────────────────────
    st.subheader("🃏 Module Forecast Cards")
    st.caption(
        "One card per high-risk module. Written for anyone on the team — "
        "no data science background needed."
    )

    cards_df = pred_df[pred_df["risk_level"].isin(["Critical", "High"])].head(10)
    if cards_df.empty:
        cards_df = pred_df.head(5)

    for _, row in cards_df.iterrows():
        mod       = str(row.get("module", "Unknown"))
        pred_val  = float(row.get("predicted", 0))
        rl        = str(row.get("risk_level", "Medium"))
        dom_type  = str(row.get("dominant_bug_type", "mixed"))
        lead_sig  = str(row.get("leading_signal", "recent bug momentum"))
        target_v  = row.get("target", None)
        icon      = RISK_ICONS.get(rl, "⚪")

        # v4.0 — build a short category summary for the card header
        _card_cat_hint = ""
        if pred_category_df is not None and not pred_category_df.empty:
            _mc = pred_category_df[pred_category_df["module"] == mod].head(2)
            if not _mc.empty:
                _card_cat_hint = " · ".join(_mc["category"].tolist())

        if _card_cat_hint:
            card_header = f"{icon} **{mod}** — {rl} risk · likely: {_card_cat_hint}"
        else:
            card_header = f"{icon} **{mod}** — {rl} risk"
        with st.expander(card_header, expanded=(rl == "Critical")):
            # Row 1 — risk + context metrics
            fc1, fc2, fc3 = st.columns(3)
            fc1.metric("Risk level", rl)
            if pred_category_df is not None and not pred_category_df.empty:
                _mod_cats = pred_category_df[pred_category_df["module"] == mod]
                fc2.metric("Bug categories expected", f"{len(_mod_cats)}",
                           help="Number of distinct QA bug categories predicted for next build")
            else:
                fc2.metric("Predicted bugs", f"{pred_val:.0f}")
            if target_v is not None:
                fc3.metric("Actual last build", f"{float(target_v):.0f}")

            # Row 2 — v3.0 signals
            sev_esc   = row.get("severity_escalation", None)
            bslc      = row.get("builds_since_last_crit", None)
            mod_ent   = None
            if _ent_source_df is not None and not _ent_source_df.empty:
                _ent_row = _ent_source_df[_ent_source_df["module"] == mod]
                if not _ent_row.empty:
                    mod_ent = float(_ent_row.iloc[0]["cluster_entropy"])

            if sev_esc is not None or bslc is not None or mod_ent is not None:
                sc1, sc2, sc3 = st.columns(3)
                if sev_esc is not None:
                    _sev_esc_f = float(sev_esc)
                    _esc_label = (
                        "🚨 Worsening toward S1" if _sev_esc_f < -0.5 else
                        "⚠️ Slight worsening"    if _sev_esc_f < -0.1 else
                        "✅ Stable / improving"
                    )
                    sc1.metric("Severity trend", _esc_label,
                               help="Negative = severity worsening toward S1 in recent builds")
                if bslc is not None:
                    sc2.metric("Builds since last critical", f"{int(float(bslc))}",
                               help="How many builds have passed since the last S1 bug in this module")
                if mod_ent is not None:
                    _ent_label = (
                        "⚠️ Broad instability" if mod_ent > 2.0 else
                        "🔶 Spreading themes"  if mod_ent > 1.0 else
                        "✅ Focused theme"
                    )
                    sc3.metric("Theme breadth (entropy)", f"{mod_ent:.2f} — {_ent_label}",
                               help="High entropy = bugs spread across many themes; low = concentrated failure mode")

            st.markdown(f"**Leading signal:** _{lead_sig}_")

            # Per-category bug-type breakdown
            _showed_categories = False
            if pred_category_df is not None and not pred_category_df.empty:
                mod_cats = pred_category_df[pred_category_df["module"] == mod].head(6)
                if not mod_cats.empty:
                    _showed_categories = True
                    st.markdown("**What types of bugs to expect:**")
                    for _, cr in mod_cats.iterrows():
                        pct_str = f"{cr['historical_pct'] * 100:.0f}%"
                        cat_name = str(cr.get("category", "Unknown"))
                        exp_cnt  = cr.get("expected_next_build", 0)
                        st.markdown(
                            f"&nbsp;&nbsp;• **{cat_name}** — {pct_str} of recent bugs"
                            + (f" (~{exp_cnt:.0f} expected)" if exp_cnt >= 1 else ""),
                            unsafe_allow_html=True,
                        )

            # Fall back to cluster breakdown if no categories
            if not _showed_categories and pred_cluster_df is not None and not pred_cluster_df.empty:
                mod_clusters = pred_cluster_df[pred_cluster_df["module"] == mod].head(6)
                if not mod_clusters.empty:
                    st.markdown("**What to expect (by bug theme):**")
                    for _, cr in mod_clusters.iterrows():
                        pct_str = f"{cr['historical_pct'] * 100:.0f}%"
                        label   = str(cr.get("cluster_label", f"Cluster {cr['cluster_id']}"))
                        if label not in ("No cluster data", "Unclustered"):
                            st.markdown(
                                f"&nbsp;&nbsp;• _{label}_ ({pct_str})",
                                unsafe_allow_html=True,
                            )

            # v5.0 — show top predicted scenarios in the card
            if pred_scenario_df is not None and not pred_scenario_df.empty:
                _card_scenarios = pred_scenario_df[pred_scenario_df["module"] == mod].head(3)
                if not _card_scenarios.empty:
                    st.markdown("**Predicted bug scenarios:**")
                    for _, _cs in _card_scenarios.iterrows():
                        _cs_conf = _CONF_BADGES.get(str(_cs.get("confidence", "medium")), "🔵 Low")
                        _cs_text = str(_cs.get("scenario_text", ""))
                        st.markdown(f"&nbsp;&nbsp;→ {_cs_conf} — {_cs_text}",
                                    unsafe_allow_html=True)

            # AI narrative
            _narrative = str(row.get("ai_narrative", "")).strip()
            if _narrative and _narrative not in ("", "nan"):
                st.markdown("**AI risk briefing:**")
                st.info(_narrative)

            st.info(f"**What to test:** {RISK_ADVICE.get(rl, '')}")

    # ── Cross-tab cluster callout ─────────────────────────────────────────
    if cluster_sum_df is not None and not cluster_sum_df.empty and "cluster_trend" in cluster_sum_df.columns:
        _growing_mods = set()
        for _, _cr in cluster_sum_df[cluster_sum_df["cluster_trend"] == "growing"].iterrows():
            _mod_str = str(_cr.get("modules", ""))
            for _m in _mod_str.split(","):
                _growing_mods.add(_m.strip())
        _high_risk_mods = set(cards_df["module"].tolist())
        _overlap = _high_risk_mods & _growing_mods
        if _overlap:
            st.info(
                f"📡 **Tab 8 cross-reference:** {len(_overlap)} high-risk module(s) also have "
                f"**growing bug themes** in the cluster analysis — "
                f"{', '.join(sorted(_overlap))}. "
                f"Open **Tab 8 → Bug Clusters** to see which specific themes are accelerating."
            )

    # ── Leading indicators ────────────────────────────────────────────────
    if pred_leading_df is not None and not pred_leading_df.empty:
        st.markdown("---")
        st.subheader("📡 Leading Indicators — What Predicts Future Bugs?")
        st.caption(
            "These are the current bug signals most strongly correlated with future bug counts. "
            "A high positive value means: 'when this signal is elevated now, more bugs tend to follow next build.'"
        )

        li = pred_leading_df.copy()
        li["pearson_r"] = pd.to_numeric(li["pearson_r"], errors="coerce").fillna(0)
        li["abs_r"]     = li["pearson_r"].abs()
        li = li.sort_values("abs_r", ascending=False).head(12)

        li["display_label"] = li.get("label", li.get("feature", li.index)).fillna(li.get("feature", ""))

        li["direction"] = li["pearson_r"].apply(
            lambda r: "📈 More now → more bugs later" if r > 0 else "📉 More now → fewer bugs later"
        )
        li["strength"]  = li["abs_r"].apply(
            lambda r: "Strong" if r >= 0.5 else "Moderate" if r >= 0.3 else "Weak"
        )

        fig_li = px.bar(
            li,
            x="pearson_r",
            y="display_label",
            orientation="h",
            color="pearson_r",
            color_continuous_scale=["#22c55e", "#f3f4f6", "#ef4444"],
            range_color=[-1, 1],
            hover_data={"pearson_r": ":.3f", "direction": True, "strength": True,
                        "display_label": False},
            labels={"pearson_r": "Correlation (−1 to +1)",
                    "display_label": "Signal",
                    "direction": "Direction",
                    "strength": "Strength"},
            text="pearson_r",
        )
        fig_li.update_traces(texttemplate="%{text:.2f}", textposition="outside")
        fig_li.update_layout(
            height=max(320, len(li) * 36),
            yaxis={"categoryorder": "total ascending"},
            coloraxis_showscale=False,
            margin=dict(l=10, r=60, t=10, b=10),
        )
        fig_li.add_vline(x=0, line_color="gray", line_width=1)
        st.plotly_chart(fig_li, width='stretch')

        with st.expander("📋 Full leading indicators table"):
            li_display = li[["display_label", "pearson_r", "direction", "strength"]].copy()
            li_display.columns = ["Signal", "Correlation", "Direction", "Strength"]
            st.dataframe(li_display, hide_index=True, width='stretch')

    # ── Advanced / Model Diagnostics (DEMOTED) ───────────────────────────
    st.markdown("---")
    with st.expander("🔧 Advanced / Model Diagnostics", expanded=False):
        st.caption(
            "Count-based model outputs and diagnostic tables. "
            "These are useful for model validation but not the primary view."
        )

        # Predicted Bug Count bar chart
        st.markdown("#### 📊 Predicted Bug Count — Next Build")
        _pred_n = len(pred_df)
        if _pred_n <= 1:
            _adv_top_n = _pred_n
        else:
            _slider_min = min(1, _pred_n - 1)
            _slider_max = min(40, _pred_n)
            _slider_val = min(20, _pred_n)
            _adv_top_n = st.slider(
                "Show top N modules",
                min_value=_slider_min,
                max_value=_slider_max,
                value=_slider_val,
                key="pred_bar_n_adv",
            )
        bar_pred = pred_df.head(_adv_top_n).copy()
        bar_pred["risk_level"] = pd.Categorical(
            bar_pred["risk_level"].astype(str), categories=RISK_ORDER, ordered=True
        )
        bar_pred = bar_pred.sort_values(["risk_level", "predicted"], ascending=[True, False])

        hover_extra = {}
        if "target" in bar_pred.columns:
            hover_extra["target"] = True
        if "dominant_bug_type" in bar_pred.columns:
            hover_extra["dominant_bug_type"] = True

        fig_pred = px.bar(
            bar_pred,
            x="module",
            y="predicted",
            color="risk_level",
            color_discrete_map=FORECAST_COLORS,
            category_orders={"risk_level": RISK_ORDER},
            hover_data={"predicted": ":.1f", "risk_level": True, **hover_extra},
            labels={"module": "Module", "predicted": "Predicted bugs (next build)",
                    "risk_level": "Risk level", "target": "Actual (last build)",
                    "dominant_bug_type": "Typical bug type"},
            text="predicted",
        )
        fig_pred.update_traces(texttemplate="%{text:.0f}", textposition="outside")
        fig_pred.update_layout(
            height=460,
            xaxis_tickangle=-35,
            showlegend=True,
            legend_title_text="Risk level",
            margin=dict(t=20, b=10),
        )
        st.plotly_chart(fig_pred, width='stretch')

        # Actual vs Predicted comparison
        if "target" in pred_df.columns:
            st.markdown("#### 📈 Actual vs Predicted (last known build)")
            avp = pred_df.head(_adv_top_n)[["module", "target", "predicted", "risk_level"]].copy()
            avp["target"]    = pd.to_numeric(avp["target"],    errors="coerce").fillna(0)
            avp["predicted"] = pd.to_numeric(avp["predicted"], errors="coerce").fillna(0)
            avp_melt = avp.melt(id_vars="module", value_vars=["target", "predicted"],
                                var_name="Type", value_name="Bugs")
            avp_melt["Type"] = avp_melt["Type"].map(
                {"target": "Actual (last build)", "predicted": "Forecast (next build)"}
            )
            fig_avp = px.bar(
                avp_melt, x="module", y="Bugs", color="Type", barmode="group",
                color_discrete_map={
                    "Actual (last build)":     "#6366f1",
                    "Forecast (next build)":   "#f97316",
                },
                labels={"module": "Module", "Bugs": "Bug count"},
            )
            fig_avp.update_layout(height=380, xaxis_tickangle=-35,
                                  legend_title_text="", margin=dict(t=10, b=10))
            st.plotly_chart(fig_avp, width='stretch')

        # Module Signals Table
        _sig_cols = [c for c in ["module", "predicted", "risk_level",
                                  "severity_escalation", "builds_since_last_crit",
                                  "dominant_bug_type", "leading_signal"]
                     if c in pred_df.columns]
        _have_new_signals = any(c in pred_df.columns
                                for c in ["severity_escalation", "builds_since_last_crit"])
        if _have_new_signals:
            st.markdown("#### 📊 Module Signals Table")
            _sig_df = pred_df[_sig_cols].copy()
            if _ent_source_df is not None and not _ent_source_df.empty:
                _sig_df = _sig_df.merge(
                    _ent_source_df[["module", "cluster_entropy"]], on="module", how="left")
            _col_labels = {
                "module": "Module",
                "predicted": "Forecast",
                "risk_level": "Risk",
                "severity_escalation": "Sev. escalation",
                "builds_since_last_crit": "Builds since S1",
                "dominant_bug_type": "Typical bug type",
                "leading_signal": "Leading signal",
                "cluster_entropy": "Theme breadth",
            }
            _sig_df = _sig_df.rename(columns=_col_labels)
            st.dataframe(_sig_df, hide_index=True, width='stretch')

        # Full predictions table
        st.markdown("#### 📋 Full Predictions Table")
        _all_pred_cols = [c for c in [
            "module", "predicted", "target", "risk_level",
            "dominant_bug_type", "leading_signal",
            "severity_escalation", "builds_since_last_crit",
        ] if c in pred_df.columns]
        disp_pred = pred_df[_all_pred_cols].copy()
        _pred_col_labels = {
            "module": "Module",
            "predicted": "Forecast (next build)",
            "target": "Actual (last build)",
            "risk_level": "Risk level",
            "dominant_bug_type": "Typical bug type",
            "leading_signal": "Leading signal",
            "severity_escalation": "Sev. escalation",
            "builds_since_last_crit": "Builds since S1",
        }
        disp_pred = disp_pred.rename(columns=_pred_col_labels)
        st.dataframe(disp_pred, hide_index=True, width='stretch')

        if pred_cluster_df is not None and not pred_cluster_df.empty:
            st.markdown("**Bug-type predictions by cluster:**")
            st.dataframe(
                pred_cluster_df[["module", "cluster_label", "historical_pct", "predicted_count"]]
                .rename(columns={
                    "module": "Module",
                    "cluster_label": "Bug theme",
                    "historical_pct": "Historical %",
                    "predicted_count": "Predicted bugs",
                }),
                hide_index=True, width='stretch',
            )