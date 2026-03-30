#!/usr/bin/env python3
"""PDR-I Bug Heatmap Dashboard v2.16

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
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from pathlib import Path


st.set_page_config(page_title="PDR-I QA Dashboard", layout="wide", page_icon="🔥")
st.sidebar.title("🔥 PDR-I QA Dashboard v2.16")


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


@st.cache_data
def load_csv(fp: str) -> pd.DataFrame:
    df = pd.read_csv(fp, low_memory=False)
    for dc in ["Create Date", "Closed Date"]:
        if dc in df.columns:
            df[dc] = pd.to_datetime(df[dc], errors="coerce")
    return df


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
# Sidebar – load data
# ---------------------------------------------------------------------

st.sidebar.markdown("**Step 1 — Bug data** (required)")

ds = st.sidebar.radio("Bug data source", ["Upload CSV", "File Path"], key="ds_bugs", index=1)

if ds == "Upload CSV":
    up = st.sidebar.file_uploader("Upload ecl_parsed.csv", type="csv", key="up_bugs")
    if up:
        df = pd.read_csv(up, low_memory=False)
        for dc in ["Create Date", "Closed Date"]:
            if dc in df.columns:
                df[dc] = pd.to_datetime(df[dc], errors="coerce")
    else:
        st.info("📂 Upload **ecl_parsed.csv** (from `parse_ecl_export.py`) to begin.")
        st.stop()
else:
    fp = st.sidebar.text_input("CSV path", value="data/ecl_parsed.csv", key="fp_bugs")
    if Path(fp).exists():
        df = load_csv(fp)
    else:
        st.error(f"File not found: {fp}")
        st.stop()

required = ["parsed_module", "severity_num"]
missing = [c for c in required if c not in df.columns]
if missing:
    st.error("❌ Wrong file! Upload **ecl_parsed.csv** (bug-level), not risk_register_scored.csv")
    st.warning(f"Missing columns: {', '.join(missing)}")
    st.stop()

if "risk_score_final" in df.columns and "quadrant" in df.columns and "bug_count" in df.columns:
    st.error("❌ This looks like risk_register_scored.csv. Please upload ecl_parsed.csv instead.")
    st.stop()

# Derived fields
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

# Normalise module names — strip sub-variant parentheticals
# e.g. "Auto Edit(Pet 02)" -> "Auto Edit"
if "parsed_module" in df.columns:
    df["parsed_module"] = df["parsed_module"].apply(
        lambda x: normalise_module(x) if pd.notna(x) else x
    )

sl_map = {1: "1-Critical", 2: "2-Major", 3: "3-Normal", 4: "4-Minor"}
if "severity_label" not in df.columns:
    df["severity_label"] = df["severity_num"].map(sl_map)


# ---------------------------------------------------------------------
# ---------------------------------------------------------------------
# Sidebar – risk data
# ---------------------------------------------------------------------
# Source info is stored here; the actual merge happens AFTER the version
# filter so we can pick the right scored file dynamically.

st.sidebar.markdown("---")
st.sidebar.markdown("**Step 2 — Risk scores** (optional, enriches all charts)")

rds = st.sidebar.radio(
    "Risk data source", ["Upload CSV", "File Path", "None"], key="ds_risk", index=1
)

risk_base_path: str = ""
risk_ver_dir:   str = ""
risk_uploaded       = None

if rds == "Upload CSV":
    up_r = st.sidebar.file_uploader(
        "Upload risk_register_scored_all.csv", type="csv", key="up_risk"
    )
    if up_r:
        risk_uploaded = up_r
elif rds == "File Path":
    rfp = st.sidebar.text_input(
        "Risk CSV path (combined all-versions file)",
        value="data/risk_register_scored_all.csv", key="fp_risk",
    )
    if Path(rfp).exists():
        risk_base_path = rfp
        risk_ver_dir   = str(Path(rfp).parent / "risk_register_versions")
    elif rfp:
        st.sidebar.caption("File not found.")

if rds != "None" and not risk_base_path and risk_uploaded is None:
    st.sidebar.info("No risk file found yet — Tab 7 will unlock once loaded.")

# ── Step 3 — Cluster file (optional) ──────────────────────────────────────
st.sidebar.markdown("---")
st.sidebar.markdown("**Step 3 — Bug clusters** (optional, unlocks Tab 8)")

cds = st.sidebar.radio(
    "Cluster data source", ["File Path", "None"], key="ds_cluster", index=0
)
cluster_df       = None
cluster_sum_df   = None

if cds == "File Path":
    cfp = st.sidebar.text_input(
        "Clustered CSV path",
        value="data/clusters/ecl_parsed_clustered.csv", key="fp_cluster",
    )
    csfp = st.sidebar.text_input(
        "Cluster summary CSV",
        value="data/clusters/ecl_parsed_cluster_summary.csv", key="fp_cluster_sum",
    )
    if Path(cfp).exists():
        cluster_df = load_csv(cfp)
        st.sidebar.caption(f"✅ Clustered bugs loaded ({len(cluster_df):,} rows)")
    elif cfp:
        st.sidebar.caption("Clustered file not found — run cluster_bugs.py first.")
    if Path(csfp).exists():
        cluster_sum_df = load_csv(csfp)

# ── Step 4 — Prediction file (optional) ───────────────────────────────────
st.sidebar.markdown("---")
st.sidebar.markdown("**Step 4 — Defect forecast** (optional, unlocks Tab 9)")

pds = st.sidebar.radio(
    "Prediction data source", ["File Path", "None"], key="ds_pred", index=0
)
pred_df          = None
pred_summary_txt = ""
pred_leading_df  = None

if pds == "File Path":
    pfp = st.sidebar.text_input(
        "Predictions CSV path",
        value="data/predictions/ecl_parsed_predictions.csv", key="fp_pred",
    )
    psfp = st.sidebar.text_input(
        "Focus summary .txt",
        value="data/predictions/ecl_parsed_predictions_focus_summary.txt", key="fp_pred_sum",
    )
    plfp = st.sidebar.text_input(
        "Leading indicators CSV",
        value="data/predictions/ecl_parsed_predictions_leading_indicators.csv", key="fp_pred_li",
    )
    if Path(pfp).exists():
        pred_df = load_csv(pfp)
        st.sidebar.caption(f"✅ Predictions loaded ({len(pred_df):,} modules)")
    elif pfp:
        st.sidebar.caption("Predictions file not found — run predict_defects.py first.")
    if Path(psfp).exists():
        pred_summary_txt = Path(psfp).read_text(encoding="utf-8")
    if Path(plfp).exists():
        pred_leading_df = load_csv(plfp)


# ---------------------------------------------------------------------
# Sidebar – filters
# ---------------------------------------------------------------------

st.sidebar.markdown("---")
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

    # Default: 3 most recent non-sparse versions; fall back to top 3 overall
    default_vers = vers_real[:3] if vers_real else vers_all[:3]

    sel_v = st.sidebar.multiselect("Version", vers_all, default=default_vers)
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
    st.sidebar.success(f"Risk scores: {n_unique} modules ({ver_context})")
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

**`[P1]` / `[P2]` badges** appear next to module names when risk data is loaded (sidebar Step 2).
They show the module's test priority — see the Risk Heatmap tab for full detail.

**Click any cell** to instantly filter the drill-down table below to that module + severity.

---
**Where this data comes from:** Bugs are parsed from the ECL Excel export by `parse_ecl_export.py`. Severity is extracted from the ECL `Severity` column and mapped to S1-S4, with weights S1=10, S2=5, S3=2, S4=1 applied at parse time. The `[P1]`/`[P2]` priority badges come from `risk_register_scored.csv` loaded in sidebar Step 2 — produced by the I x P x D scoring pipeline described in the Risk Heatmap tab.
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

**How to use it:**
- A column that suddenly turns dark red = a bad release for that module — likely a regression.
- A row that stays consistently warm across all versions = a chronic problem area.
- Compare the last 3 versions (default filter) to spot recent regressions quickly.

**Severity weighting** is the same as Tab 1: Critical×10, Major×5, Normal×2, Minor×1.

**Tip:** Switch the filter in the sidebar to "All versions" to see the full history.

---
**Where this data comes from:** `parsed_version` is extracted from the ECL `Version` field by `parse_ecl_export.py`. Severity weighting (S1=10, S2=5, S3=2, S4=1) is stored as `severity_weight` per bug at parse time. `compute_risk_scores.py` also produces one `risk_register_<version>.csv` per version so the full I x P x D scoring can be run per-release if needed.
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

**Regression Bug Rate (Side Effect %):** Modules with a high rate need extra regression coverage.
A module at 30%+ side-effect rate means nearly 1 in 3 bugs is a regression — test it every build.

**AT Found Rate:** Percentage of bugs caught by automated tests.
- Green (≥30%) — good automation coverage
- Orange (10–29%) — partial coverage, room to improve
- Red (<10%) — automation blind spot, bugs are slipping through to manual

**Automation Blind Spots** at the bottom lists modules with 0% AT coverage — highest priority for new automation.

---
**Where tags come from:** `parse_ecl_export.py` reads `[TAG]` prefixes in each bug's Short Description and creates boolean columns: `tag_side_effect`, `tag_at_found`, `tag_edf`, `tag_ux`, `tag_mui`, etc.

These feed directly into the risk scoring pipeline:
- `regression_rate` = `side_effect_count / total_bugs` — computed in `compute_risk_scores.py`
- `automation_catch_rate` = `at_found_count / total_bugs` — also from `compute_risk_scores.py`

Both rates feed into **Detectability (D)** in `ai_risk_scorer.py`: a module with 0% AT coverage gets D+1 (harder to detect), pushing its overall I x P x D risk score up.
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
**What this shows:** How many bugs each tester has filed per module category.
A cell with a high number means that tester has deep experience in that area.

**Knowledge Silos** (flagged below the chart) = a category where only **one** tester has filed bugs.
This is a bus-factor risk — if that person is unavailable, test coverage for that area drops to zero.

**How to act on this:**
- Pair the sole expert with a second tester for knowledge transfer.
- Prioritise those categories for test documentation / runbook creation.
- Consider cross-training during lower-pressure sprints.
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
| **Total Bugs** | Active bugs matching current sidebar filters | Filtered baseline — change version/status filters to slice |
| **Critical Bugs (S1)** | Severity-1 (crash / data loss) open bugs | Any S1 in the release is a potential showstopper |
| **Avg Days to Close** | Mean calendar days from bug creation to Close status | Measures RD fix velocity; rising trend = backlog pressure |
| **Regression Bug Rate** | % of bugs tagged `[Side Effect]` | How often new code breaks existing functionality |
| **P1 Modules** | Modules scored ≥60 on I×P×D risk scale | Must be tested every single build |
| **P2 Modules** | Modules scored 30–59 | Test every sprint / major build |
| **Avg Risk Score** | Mean I×P×D across all filtered modules | Overall health signal; rising = increasing risk exposure |

**Weekly Bug Trend:** A healthy project shows a declining or flat trend late in a release cycle.
A spike mid-cycle usually indicates a large merge or new feature landing.

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
| P1 — Critical  | >= 60 | Every build, every sprint |
| P2 — High      | 30-59 | Every sprint / major build |
| P3 — Medium    | 10-29 | Every release candidate |
| P4 — Low       | < 10  | Full release cycle only |

---

### What each visual element shows

| Element | Data source | What it represents |
|---------|-------------|-------------------|
| **Block size** | `ecl_parsed.csv` | Bug count for this module |
| **Colour — Risk Score** | `risk_register_scored.csv` | Final I x P x D value |
| **Colour — Priority** | `risk_register_scored.csv` | P1–P4 assignment |
| **Colour — Critical Count** | `ecl_parsed.csv` | Number of S1 (crash-level) bugs |
| **Colour — Severity Weight** | `ecl_parsed.csv` | Weighted bug sum S1x10 + S2x5 + S3x2 + S4x1 |
| **Right detail panel** | Both files joined | Total bugs, critical count, risk score for selected module |
| **P1 bar chart** | `risk_register_scored.csv` | P1 modules ranked by risk score, highest first |
| **Risk vs Probability scatter** | `risk_register_scored.csv` | I x P x D vs P — shows which are both high-risk AND historically bug-prone |

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
            if score >= 60: return "Critical Risk (≥60)"
            if score >= 30: return "High Risk (30-59)"
            if score >= 10: return "Medium Risk (10-29)"
            return "Low Risk (<10)"

        scatter_df["risk_zone"] = scatter_df["risk_score"].apply(assign_zone)

        # Add small random jitter to x (probability) so dots at the same
        # integer score separate from each other and can be hovered individually.
        rng = np.random.default_rng(42)
        scatter_df["probability_jittered"] = (
            scatter_df["probability"]
            + rng.uniform(-0.35, 0.35, size=len(scatter_df))
        )

        zone_colors = {
            "Critical Risk (≥60)": "#D62728",
            "High Risk (30-59)":   "#FF7F0E",
            "Medium Risk (10-29)": "#BCBD22",
            "Low Risk (<10)":      "#2CA02C",
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
            (10, "Medium threshold"), (30, "High threshold"), (60, "Critical threshold")
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
- **Modules affected** — which modules contribute bugs to this theme
- **Sample bug descriptions** — 6 real examples from ECL so you can judge for yourself
- **Action line** — a plain-English recommendation based on severity:
  - 🚨 **Immediate attention** (avg sev ≤ 1.5) — crash-level or data-loss bugs. Escalate to RD.
  - ⚠️ **High priority** (avg sev ≤ 2.5) — major functional issues. Add to next sprint regression.
  - 📋 **Standard priority** (avg sev ≤ 3.5) — normal issues. Cover in release-candidate testing.
  - ✅ **Low priority** (avg sev > 3.5) — cosmetic or minor issues. Full release cycle.

---

### 🔍 How to Investigate a Pattern

1. Find the largest red/orange bars — these are the highest-priority themes.
2. Open the card and check which modules are affected.
   - If it's 1 module → isolated bug area. Add module to current sprint focus.
   - If it's 3+ modules → shared root cause likely. Investigate common infrastructure (API, shared component, SDK version).
3. Read the sample descriptions — do they describe the same underlying failure? If yes, this is a systematic problem, not random noise.
4. Cross-check with **Tab 7 (Risk Heatmap)** — if the affected modules are P1/P2, the theme is high-priority regardless of bug count.

---

### 🛠️ Where Does This Data Come From?

Run the clustering script, then point the sidebar to the output files:

```bash
# Default (TF-IDF, fast, no Ollama required):
python scripts/cluster_bugs.py data/ecl_parsed.csv data/ecl_parsed_clustered.csv

# Ollama mode (richer labels, recommended when RAM is available):
python scripts/cluster_bugs.py data/ecl_parsed.csv data/ecl_parsed_clustered.csv \\
  --provider ollama --model qwen3:7b
```

Then in the sidebar: **Step 3** → set paths to `ecl_parsed_clustered.csv` and `ecl_parsed_cluster_summary.csv`.

Recommend re-running clustering **every Friday** or whenever a new batch of builds has been parsed, so the themes reflect the latest patterns.
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

    # ── Build summary table if not pre-loaded from CSV ───────────────────
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

    if summary.empty:
        st.info("No clusters found in the loaded data.")
        st.stop()

    # Ensure required columns exist
    for col, default in [("count", 0), ("avg_sev", 3.0), ("modules", "")]:
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

        # Severity emoji for card header
        sev_icon = {"🔴 Mostly Critical": "🔴", "🟠 Mostly Major": "🟠",
                    "🟡 Mostly Normal": "🟡", "🟢 Mostly Minor": "🟢"}.get(sband, "⚪")

        card_title = f"{sev_icon} Theme #{rank_n} · **{count} bugs** · _{label}_"

        with st.expander(card_title, expanded=(rank_n == 1)):
            cc1, cc2, cc3 = st.columns(3)
            cc1.metric("Bugs in theme", count)
            cc2.metric("Avg severity", f"{avg_sv:.1f}",
                       help="1=Critical  2=Major  3=Normal  4=Minor")
            pct = f"{count / n_clustered * 100:.1f}%" if n_clustered else "—"
            cc3.metric("Share of clustered bugs", pct)

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

            # Plain-English interpretation for non-QA readers
            if avg_sv <= 1.5:
                action = "🚨 **Immediate attention** — this theme contains crash-level or data-loss bugs. Escalate to RD."
            elif avg_sv <= 2.5:
                action = "⚠️ **High priority** — major functionality issues. Add these modules to the next sprint's regression list."
            elif avg_sv <= 3.5:
                action = "📋 **Standard priority** — normal-severity issues. Cover in release-candidate testing."
            else:
                action = "✅ **Low priority** — mostly cosmetic or minor issues. Include in full release cycle testing."
            st.info(action)

    # ── Raw summary table ────────────────────────────────────────────────
    with st.expander("📋 Full theme table (raw data)"):
        display_sum = summary[["cluster_label", "count", "avg_sev", "modules",
                                "severity_band"]].copy()
        display_sum.columns = ["Theme keywords", "Bug count", "Avg severity (1–4)",
                                "Modules affected", "Severity band"]
        st.dataframe(display_sum, hide_index=True, width='stretch')


# =====================================================================
# TAB 9 – Defect Forecast
# =====================================================================

elif active_tab == "🔮 Defect Forecast":
    st.header("🔮 Defect Forecast — What Will Break Next Build?")

    with st.expander("📖 How to read this tab — full guide", expanded=False):
        st.markdown("""
## 🔮 Defect Forecast — Complete Guide

This tab shows a **machine-learning forecast** of how many bugs each module is likely to produce
in the **next build**, based on its recent history. No data-science background needed.

---

### 🎯 What Is Being Predicted?

The model counts how many new bugs were filed against each module in each past build,
learns the pattern over time, and extrapolates to the next build.

> **Target variable:** raw bug count per module per build — not severity-weighted.
> A module that consistently produces 12 minor bugs scores higher than one that produced 1 critical bug once.
> Use **Tab 7 (Risk Heatmap)** alongside this tab to catch modules with low count but high severity.

---

### 🔢 Features the Model Uses

| Signal | Plain-English meaning |
|--------|-----------------------|
| **Bug count (last 1 build)** | Most recent data point — captures sudden spikes |
| **Bug count (last 3 builds avg)** | Short-term trend — smooths out one-off outliers |
| **Bug count (last 5 builds avg)** | Medium-term baseline — captures chronic instability |
| **Critical bug count (last 3 builds)** | Crash/data-loss momentum — strongest predictor for high-impact modules |
| **Trend (last 3 builds)** | Is the count rising, falling, or flat? |
| **Severity-weighted count (last 3 builds)** | Weights Critical (×10) and Major (×5) bugs more heavily |

---

### 🚦 Risk Level Thresholds

| Level | Predicted bugs | What to do |
|-------|---------------|------------|
| 🔴 **Critical** | > 10 | Test **every build**. High instability. Focus on crash scenarios, data loss, and recently changed areas. |
| 🟠 **High** | 6–10 | Test **every sprint**. Run full regression for this module and check side effects in adjacent modules. |
| 🟡 **Medium** | 3–5 | Include in **release-candidate** pass. Spot-check changed areas. |
| 🟢 **Low** | < 3 | **Standard release cycle** coverage. No special urgency. |

---

### 📊 Headline Metrics (top row)

| Metric | What it tells you |
|--------|------------------|
| 🔴 Critical modules | Modules predicted to produce > 10 bugs next build |
| 🟠 High-risk modules | Modules predicted to produce 6–10 bugs |
| Total modules forecast | All modules with sufficient history for the model (≥ 20 data points) |
| Model accuracy (MAE) | Mean absolute error vs the last known build — ±2 bugs is excellent, ±5 is acceptable |

---

### 🃏 Module Forecast Cards

Each card for a Critical or High-risk module shows:
- **Predicted bugs** — the model's next-build estimate
- **Actual last build** — what actually happened (when available) — compare these to judge model quality
- **Typical bug type** — the most common historical severity for this module (e.g. "crash/Critical (S1)"), so the team knows *what kind* of bugs to expect, not just how many
- **Why high risk** — the leading signal driving this score. Examples:
  - *"critical-bug momentum (last 3 builds)"* → the module has had critical bugs recently and historically they persist
  - *"sustained high volume (last 5 builds)"* → the module has consistently produced many bugs over time
  - *"sharp upward trend (last 3 builds)"* → bug count is accelerating — new code changes may be destabilising it
- **What to test** — a plain-English instruction matched to the risk level

---

### 📈 Actual vs Predicted Comparison

The grouped bar chart compares the model's most-recent prediction against what actually happened.
- Bars that are **close in height** = the model was accurate for that build
- A **much taller actual bar** = the module had a surprise spike — review what changed in that build
- A **much taller predicted bar** = the model over-estimated — may reflect a recent improvement in that module

---

### 📡 Leading Indicators Chart

Shows which current bug signals are most strongly correlated (Pearson r) with future bug counts
across all modules. Think of this as: "what should I be watching *now* to predict problems *next build*?"

- **Positive (red) bar** → more of this signal now = more bugs next build. High positive = reliable early warning.
- **Negative (green) bar** → more of this signal now = fewer bugs next build (e.g. a build that closed many bugs may be followed by a quieter one).
- **Bar length** = strength of correlation. Only bars with |r| ≥ 0.3 are practically meaningful.

The signals are labelled in plain English, not variable names.

---

### ⚠️ Limitations and What to Watch For

- **Needs numeric Build#**: version strings (e.g. "16.3.5") are automatically dropped. If many rows are dropped, forecasts will be less accurate.
- **Needs history**: modules with fewer than 20 build-module data points are excluded. Run predictions again after more build data has been captured.
- **Counts, not severity**: a module producing 12 minor bugs scores higher than one that produced 3 critical bugs. Combine with Tab 7 (risk scores) for a complete picture.
- **Model retrains each run**: re-run `predict_defects.py` weekly or after each major parse update to keep forecasts fresh.

---

### 🛠️ Where Does This Data Come From?

```bash
python scripts/predict_defects.py data/ecl_parsed.csv data/ecl_parsed_predictions.csv
```

Then in the sidebar: **Step 4** → set paths to `ecl_parsed_predictions.csv`, `_focus_summary.txt`,
and `_leading_indicators.csv`. All four output files are stored under `data/predictions/` by default.

Recommend re-running **every Friday** alongside clustering so Tab 8 and Tab 9 stay in sync.
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

    pred_df["predicted"] = pd.to_numeric(pred_df["predicted"], errors="coerce").fillna(0)
    pred_df = pred_df.sort_values("predicted", ascending=False).reset_index(drop=True)

    # ── Headline metrics ─────────────────────────────────────────────────
    rl_counts = pred_df["risk_level"].value_counts()
    pm1, pm2, pm3, pm4 = st.columns(4)
    pm1.metric("🔴 Critical modules",  int(rl_counts.get("Critical", 0)),
               help="Predicted > 10 bugs next build")
    pm2.metric("🟠 High-risk modules", int(rl_counts.get("High", 0)),
               help="Predicted 6–10 bugs next build")
    pm3.metric("Total modules forecast", len(pred_df))
    if "target" in pred_df.columns:
        mae = abs(pred_df["predicted"] - pd.to_numeric(pred_df["target"], errors="coerce")).mean()
        pm4.metric("Model accuracy (MAE)", f"±{mae:.1f} bugs",
                   help="Mean absolute error on the most recent known build — lower is better")
    else:
        pm4.metric("Model accuracy (MAE)", "N/A")

    st.markdown("---")

    # ── Forecast bar chart ────────────────────────────────────────────────
    st.subheader("📊 Predicted Bug Count — Next Build")
    st.caption(
        "Bar height = predicted number of new bugs. "
        "Colour = risk level. Hover for current (actual) vs predicted."
    )

    FORECAST_COLORS = {
        "Critical": "#ef4444",
        "High":     "#f97316",
        "Medium":   "#eab308",
        "Low":      "#22c55e",
    }
    RISK_ORDER = ["Critical", "High", "Medium", "Low"]

    top_n_pred = st.slider("Show top N modules", min_value=5,
                           max_value=min(40, len(pred_df)),
                           value=min(20, len(pred_df)), key="pred_bar_n")
    bar_pred = pred_df.head(top_n_pred).copy()
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

    # ── Actual vs Predicted comparison ───────────────────────────────────
    if "target" in pred_df.columns:
        with st.expander("📈 Actual vs Predicted (last known build)"):
            st.caption(
                "Compares the model's prediction against what actually happened in the most recent build. "
                "Bars close to the line = accurate; bars far above = module had a surprise spike."
            )
            avp = pred_df.head(top_n_pred)[["module", "target", "predicted", "risk_level"]].copy()
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

    # ── Module forecast cards ─────────────────────────────────────────────
    st.markdown("---")
    st.subheader("🃏 Module Forecast Cards")
    st.caption(
        "One card per high-risk module. Written for anyone on the team — "
        "no data science background needed."
    )

    cards_df = pred_df[pred_df["risk_level"].isin(["Critical", "High"])].head(10)
    if cards_df.empty:
        cards_df = pred_df.head(5)

    RISK_ICONS   = {"Critical": "🔴", "High": "🟠", "Medium": "🟡", "Low": "🟢"}
    RISK_ADVICE  = {
        "Critical": "Test **every build**. Focus on crash scenarios, data loss, and any recently changed functionality.",
        "High":     "Test **every sprint**. Run full regression for this module and check for side effects in related areas.",
        "Medium":   "Include in **release-candidate** testing. Spot-check changed areas.",
        "Low":      "Cover in the full **release cycle** pass. No special urgency.",
    }

    for _, row in cards_df.iterrows():
        mod       = str(row.get("module", "Unknown"))
        pred_val  = float(row.get("predicted", 0))
        rl        = str(row.get("risk_level", "Medium"))
        dom_type  = str(row.get("dominant_bug_type", "mixed"))
        lead_sig  = str(row.get("leading_signal", "recent bug momentum"))
        target_v  = row.get("target", None)
        icon      = RISK_ICONS.get(rl, "⚪")

        card_header = f"{icon} **{mod}** — forecast {pred_val:.0f} bugs · {rl} risk"
        with st.expander(card_header, expanded=(rl == "Critical")):
            fc1, fc2, fc3 = st.columns(3)
            fc1.metric("Predicted bugs", f"{pred_val:.0f}")
            if target_v is not None:
                fc2.metric("Actual last build", f"{float(target_v):.0f}")
            fc3.metric("Risk level", rl)

            st.markdown(f"**Typical bug type:** {dom_type}")
            st.markdown(f"**Why high risk:** driven by _{lead_sig}_")
            st.info(f"**What to test:** {RISK_ADVICE.get(rl, '')}")

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

        # Use label column if present, else feature name
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

    # ── Raw predictions table ──────────────────────────────────────────────
    with st.expander("📋 Full predictions table"):
        show_cols = [c for c in ["module", "predicted", "target", "risk_level",
                                 "dominant_bug_type", "leading_signal"] if c in pred_df.columns]
        disp_pred = pred_df[show_cols].copy()
        disp_pred.columns = [
            {"module": "Module", "predicted": "Forecast (next build)",
             "target": "Actual (last build)", "risk_level": "Risk level",
             "dominant_bug_type": "Typical bug type",
             "leading_signal": "Leading signal"}.get(c, c)
            for c in show_cols
        ]
        st.dataframe(disp_pred, hide_index=True, width='stretch')