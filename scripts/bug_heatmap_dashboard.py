#!/usr/bin/env python3
"""PDR-I Bug Heatmap Dashboard v2.4

Changes from v2.3:
  - Issue 9: Fixed tab reset-to-first on widget interaction — replaced st.tabs()
             with sidebar radio navigation persisted via session_state.

Changes from v2.2:
  - Issue 2: Status filter defaults to ACTIVE bugs only (sidebar toggle to include inactive)
  - Issue 3: Heatmap cells clickable → shows filtered bug table with ECL links (Option A)
  - Issue 6: Fixed KeyError: 5 in Tab 1/2 Module nlargest (was misusing Series as column key)
  - Issue 8: Priority vs Severity now uses human-readable labels (Fix Now/Must Fix/etc, N/A for 5)
             plus clearer P/S interpretation and mismatch metrics.

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


# ---------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------

ECL_BUG_URL = "https://ecl.cyberlink.com/Ebug/EbugHandle/HandleMainEbug.asp?BugCode={bug_code}"

QUADRANT_COLORS = {
    "Q4": "#ef4444",
    "Q3": "#f97316",
    "Q2": "#eab308",
    "Q1": "#22c55e",
}

PRIORITY_LABEL_MAP = {
    1: "1-Fix Now",
    2: "2-Must Fix",
    3: "3-Better Fix",
    4: "4-No Matter",
    5: "5-N/A",
}
PRIORITY_ORDER = ["1-Fix Now", "2-Must Fix", "3-Better Fix", "4-No Matter", "5-N/A"]

INACTIVE_STATUSES = {
    "close",
    "need more info",
    "nab",
    "propose nab",
    "wont fix",
    "won't fix",
    "propose wont fix",
    "qa propose wont fix",
    "not reproducible",
    "notreproducible",
    "not a bug",
    "new feature",
    "external issue",
    "hqqa close",
    "fae close",
}

TAB_NAMES = [
    "🗺️ Module × Severity",
    "📅 Version Timeline",
    "🏷️ Tag Analysis",
    "⚖️ P/S Alignment",
    "👥 Team Coverage",
    "📊 KPI Dashboard",
    "🔥 Risk Heatmap",
]


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def is_active(status_val) -> bool:
    if pd.isna(status_val):
        return True
    return str(status_val).strip().lower() not in INACTIVE_STATUSES


@st.cache_data
def load_csv(fp: str) -> pd.DataFrame:
    df = pd.read_csv(fp)
    for dc in ["Create Date", "Closed Date"]:
        if dc in df.columns:
            df[dc] = pd.to_datetime(df[dc], errors="coerce")
    return df


# ---------------------------------------------------------------------
# Sidebar – Navigation (Issue 9: replaces st.tabs for persistent state)
# ---------------------------------------------------------------------

st.sidebar.subheader("📑 Navigation")

active_tab = st.sidebar.radio(
    "View",
    TAB_NAMES,
    key="active_tab",
    label_visibility="collapsed",
)

# ---------------------------------------------------------------------
# Sidebar – load data
# ---------------------------------------------------------------------

st.sidebar.title("🔥 PDR-I QA Dashboard v2.4")
st.sidebar.markdown("**Step 1 — Bug data** (required)")

ds = st.sidebar.radio("Bug data source", ["Upload CSV", "File Path"], key="ds_bugs", index=1)

if ds == "Upload CSV":
    up = st.sidebar.file_uploader("Upload ecl_parsed.csv", type="csv", key="up_bugs")
    if up:
        df = pd.read_csv(up)
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

# Validate basic columns
required = ["parsed_module", "severity_num"]
missing = [c for c in required if c not in df.columns]
if missing:
    st.error("❌ Wrong file! Upload **ecl_parsed.csv** (bug-level), not risk_register_scored.csv")
    st.warning(f"Missing columns: {', '.join(missing)}")
    st.stop()

if (
    "risk_score_final" in df.columns
    and "quadrant" in df.columns
    and "bug_count" in df.columns
):
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

if "status_active" not in df.columns:
    if "Status" in df.columns:
        df["status_active"] = df["Status"].apply(is_active)
    else:
        df["status_active"] = True


# ---------------------------------------------------------------------
# Sidebar – risk data
# ---------------------------------------------------------------------

st.sidebar.markdown("---")
st.sidebar.markdown("**Step 2 — Risk scores** (optional, enriches all charts)")

rds = st.sidebar.radio(
    "Risk data source", ["Upload CSV", "File Path", "None"], key="ds_risk", index=1
)
risk_df = None
if rds == "Upload CSV":
    up_r = st.sidebar.file_uploader(
        "Upload risk_register_scored.csv", type="csv", key="up_risk"
    )
    if up_r:
        risk_df = pd.read_csv(up_r)
elif rds == "File Path":
    rfp = st.sidebar.text_input(
        "Risk CSV path", value="data/risk_register_scored.csv", key="fp_risk"
    )
    if Path(rfp).exists():
        risk_df = load_csv(rfp)

if risk_df is not None:
    risk_ok = all(
        c in risk_df.columns for c in ["parsed_module", "risk_score_final", "quadrant"]
    )
    if not risk_ok:
        st.sidebar.warning("⚠️ Risk CSV missing required columns — ignoring.")
        risk_df = None
    else:
        st.sidebar.success(f"✅ Risk data loaded: {len(risk_df)} modules")

if risk_df is not None:
    risk_cols = [
        c
        for c in [
            "parsed_module",
            "quadrant",
            "risk_score_final",
            "impact_score_ai",
            "detectability_score_ai",
        ]
        if c in risk_df.columns
    ]
    df = df.merge(risk_df[risk_cols], on="parsed_module", how="left")
    df["quadrant"] = df.get("quadrant", pd.Series(["Q1"] * len(df))).fillna("Q1")
    df["risk_score_final"] = df.get("risk_score_final", pd.Series([0.0] * len(df))).fillna(0.0)
    risk_available = True
else:
    df["quadrant"] = "Unknown"
    df["risk_score_final"] = 0.0
    risk_available = False


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
    vers = sorted(df["parsed_version"].dropna().unique(), reverse=True)
    default_vers = vers[:3] if len(vers) >= 3 else vers
    sel_v = st.sidebar.multiselect("Version", vers, default=default_vers)
    if sel_v:
        df = df[df["parsed_version"].isin(sel_v)]

if "Status" in df.columns:
    stats = sorted(df["Status"].dropna().unique().tolist())
    sel_s = st.sidebar.multiselect("Status", stats, default=stats)
    if sel_s:
        df = df[df["Status"].isin(sel_s)]

if "severity_num" in df.columns:
    sel_sev = st.sidebar.multiselect(
        "Severity",
        [1, 2, 3, 4],
        default=[1, 2, 3, 4],
        format_func=lambda x: {
            1: "S1-Critical",
            2: "S2-Major",
            3: "S3-Normal",
            4: "S4-Minor",
        }[x],
    )
    if sel_sev:
        df = df[df["severity_num"].isin(sel_sev)]

if risk_available:
    all_q = sorted(df["quadrant"].dropna().unique().tolist())
    sel_q = st.sidebar.multiselect("Quadrant", all_q, default=all_q)
    if sel_q:
        df = df[df["quadrant"].isin(sel_q)]

active_label = "active " if not include_inactive else ""
st.sidebar.markdown(f"**Showing {len(df):,} {active_label}bugs**")



# =====================================================================
# TAB 1 – Module × Severity
# =====================================================================

if active_tab == "🗺️ Module × Severity":
    st.header("Module × Severity Heatmap")

    vl = st.radio("View", ["Category", "Module (top 30)"], horizontal=True, key="t1v")
    gc = "module_category" if vl.startswith("C") else "parsed_module"

    sl = {1: "1-Critical", 2: "2-Major", 3: "3-Normal", 4: "4-Minor"}
    df["severity_label"] = df["severity_num"].map(sl)

    pv = df.pivot_table(
        index=gc,
        columns="severity_label",
        values="severity_weight",
        aggfunc="sum",
        fill_value=0,
    )

    for lbl in ["1-Critical", "2-Major", "3-Normal", "4-Minor"]:
        if lbl not in pv.columns:
            pv[lbl] = 0
    pv = pv[["1-Critical", "2-Major", "3-Normal", "4-Minor"]]

    if vl.startswith("M"):
        total_weight = pv.sum(axis=1)
        top_modules = total_weight.nlargest(30).index
        pv = pv.loc[top_modules]

    pv = pv.sort_values("1-Critical", ascending=True)

    y_labels = list(pv.index)
    if risk_available:
        q_map = df.groupby(gc)["quadrant"].first().to_dict()
        y_labels = [f"{y} [{q_map.get(y, '—')}]" for y in pv.index]

    fig = px.imshow(
        pv,
        labels=dict(x="Severity", y=gc, color="Weighted Count"),
        color_continuous_scale="YlOrRd",
        aspect="auto",
        text_auto=True,
    )
    fig.update_layout(
        height=max(400, len(pv) * 28),
        yaxis=dict(ticktext=y_labels, tickvals=list(range(len(pv.index)))),
    )
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")
    st.subheader("🔗 Bug Drill-Down")
    st.caption("Select a module/category and severity to list bugs with ECL links.")

    col_a, col_b = st.columns(2)
    with col_a:
        drill_group = st.selectbox(
            "Module / Category", options=sorted(df[gc].dropna().unique()), key="dd_group"
        )
    with col_b:
        drill_sev = st.selectbox(
            "Severity",
            options=["All", "1-Critical", "2-Major", "3-Normal", "4-Minor"],
            key="dd_sev",
        )

    drill_df = df[df[gc] == drill_group].copy()
    if drill_sev != "All":
        sev_num_map = {"1-Critical": 1, "2-Major": 2, "3-Normal": 3, "4-Minor": 4}
        drill_df = drill_df[drill_df["severity_num"] == sev_num_map[drill_sev]]

    if len(drill_df) == 0:
        st.info("No bugs match this selection.")
    else:
        display_cols_wanted = [
            "BugCode", "Short Description", "Status", "severity_label",
            "priority_label", "parsed_version", "Creator", "parsed_module",
        ]
        display_cols = [c for c in display_cols_wanted if c in drill_df.columns]
        drill_display = drill_df[display_cols].copy()

        bug_code_col = next(
            (c for c in drill_df.columns if "bugcode" in c.lower()), None
        )
        if bug_code_col:
            drill_display["ECL Link"] = drill_df[bug_code_col].apply(
                lambda x: ECL_BUG_URL.format(bug_code=x) if pd.notna(x) else ""
            )
            st.dataframe(
                drill_display,
                column_config={
                    "ECL Link": st.column_config.LinkColumn("ECL Link", display_text="Open 🔗")
                },
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.dataframe(drill_display, use_container_width=True, hide_index=True)

        st.caption(f"Showing {len(drill_display)} bugs for **{drill_group}** / **{drill_sev}**")

    with st.expander("Raw pivot data"):
        st.dataframe(pv, use_container_width=True)


# =====================================================================
# TAB 2 – Version Timeline
# =====================================================================

elif active_tab == "📅 Version Timeline":
    st.header("Module × Version Timeline")

    if "parsed_version" in df.columns:
        vl2 = st.radio("View", ["Category", "Module (top 25)"], horizontal=True, key="t2v")
        gc2 = "module_category" if vl2.startswith("C") else "parsed_module"

        tl = df.pivot_table(
            index=gc2,
            columns="parsed_version",
            values="severity_weight",
            aggfunc="sum",
            fill_value=0,
        )
        tl = tl[sorted(tl.columns)]

        if vl2.startswith("M"):
            total_weight2 = tl.sum(axis=1)
            top_mods2 = total_weight2.nlargest(25).index
            tl = tl.loc[top_mods2]

        fig2 = px.imshow(
            tl,
            labels=dict(x="Version", y=gc2, color="Weighted"),
            color_continuous_scale="YlOrRd",
            aspect="auto",
            text_auto=True,
        )
        fig2.update_layout(height=max(400, len(tl) * 28))
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.warning("No `parsed_version` column found.")


# =====================================================================
# TAB 3 – Tag Analysis
# =====================================================================

elif active_tab == "🏷️ Tag Analysis":
    st.header("Tag Distribution")

    tc = [c for c in df.columns if c.startswith("tag_")]
    if tc:
        vl3 = st.radio("View", ["Category", "Module (top 25)"], horizontal=True, key="t3v")
        gc3 = "module_category" if vl3.startswith("C") else "parsed_module"

        tp = df.groupby(gc3)[tc].sum()
        tp.columns = [c.replace("tag_", "").replace("_", " ").title() for c in tp.columns]

        if vl3.startswith("M"):
            total_tags = tp.sum(axis=1)
            top_mods3 = total_tags.nlargest(25).index
            tp = tp.loc[top_mods3]

        fig3 = px.imshow(
            tp,
            labels=dict(x="Tag", y=gc3, color="Count"),
            color_continuous_scale="Viridis",
            aspect="auto",
            text_auto=True,
        )
        fig3.update_layout(height=max(400, len(tp) * 28))
        st.plotly_chart(fig3, use_container_width=True)

        st.subheader("Regression Bug Rate [Side Effect]")
        se = [c for c in tc if "side_effect" in c.lower()]
        if se:
            total_per_mod = df.groupby(gc3).size()
            se_count = df[df[se[0]] == True].groupby(gc3).size()
            se_rate = (
                (se_count / total_per_mod * 100)
                .fillna(0)
                .sort_values(ascending=False)
                .head(15)
            )
            fig_se = px.bar(
                se_rate.reset_index(),
                x=gc3,
                y=0,
                labels={gc3: "Module/Category", "0": "Regression Bug Rate (%)"},
                title="Top 15 by Regression Bug Rate (%)",
            )
            fig_se.update_layout(height=350, xaxis_tickangle=-30)
            st.plotly_chart(fig_se, use_container_width=True)

        st.subheader("Automation Coverage [AT Found Rate]")
        at = [c for c in tc if "at_found" in c.lower()]
        if at:
            total_per_mod = df.groupby(gc3).size()
            at_count = (
                df[df[at[0]] == True]
                .groupby(gc3)
                .size()
                .reindex(total_per_mod.index, fill_value=0)
            )
            at_rate = (at_count / total_per_mod * 100).fillna(0)

            at_df = (
                pd.DataFrame(
                    {
                        "Module": at_rate.index,
                        "AT Found Rate (%)": at_rate.values,
                        "Total Bugs": total_per_mod.values,
                        "AT Found Bugs": at_count.values,
                    }
                )
                .sort_values("AT Found Rate (%)", ascending=False)
            )

            fig_at = px.bar(
                at_df,
                x="Module",
                y="AT Found Rate (%)",
                color="AT Found Rate (%)",
                color_continuous_scale=["red", "orange", "green"],
                range_color=[0, 50],
                hover_data=["Total Bugs", "AT Found Bugs"],
                title="AT Found Rate by Module (Green ≥30%, Orange 10–29%, Red <10%)",
            )
            fig_at.update_layout(height=400, xaxis_tickangle=-30)
            st.plotly_chart(fig_at, use_container_width=True)

            blind_spots = at_df[at_df["AT Found Rate (%)"] == 0]
            st.subheader(
                f"🔴 Automation Blind Spots ({len(blind_spots)} modules with 0% AT coverage)"
            )
            if len(blind_spots):
                st.dataframe(blind_spots, use_container_width=True, hide_index=True)
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
        st.markdown(
            """
**QA assigns Severity (S1–S4). RD assigns Priority (Fix Now → N/A).**

| | RD: N/A (not triaged) | RD: Low priority (No Matter) | RD: High priority (Fix Now/Must Fix) |
|---|---|---|---|
| **QA: S1–S2 (Critical/Major)** | 🔴 **Urgent Gap** — critical bug not yet triaged by RD | 🔴 **Mismatch** — escalate to RD | ✅ Aligned |
| **QA: S3–S4 (Normal/Minor)** | ⚪ Expected — low priority bugs often untriaged | ✅ Aligned | 🟡 **Inverse** — RD fast-tracked something QA rates minor, verify scope |

**Priority 5 = N/A** means RD has not assigned a priority yet. A high count of S1/S2 bugs with Priority N/A is a triage backlog signal.
"""
        )

    if "priority_label" in df.columns and "severity_num" in df.columns:
        mm = df.pivot_table(
            index="priority_label",
            columns="severity_num",
            aggfunc="size",
            fill_value=0,
        )
        mm.columns = [
            f"S{int(c)}-{'Critical' if c==1 else 'Major' if c==2 else 'Normal' if c==3 else 'Minor'}"
            for c in mm.columns
        ]
        present_priorities = [p for p in PRIORITY_ORDER if p in mm.index]
        mm = mm.reindex(present_priorities)

        fig4 = px.imshow(
            mm,
            color_continuous_scale="Blues",
            aspect="auto",
            text_auto=True,
            labels={"x": "QA Severity", "y": "RD Priority", "color": "Bug Count"},
        )
        fig4.update_layout(height=350)
        st.plotly_chart(fig4, use_container_width=True)

        st.markdown("---")
        col1, col2, col3 = st.columns(3)
        high_sev = df["severity_num"].isin([1, 2])
        high_pri = df["priority_label"].isin(["1-Fix Now", "2-Must Fix"])
        low_pri = df["priority_label"].isin(["4-No Matter", "5-N/A"])

        critical_mismatch = df[high_sev & low_pri]
        inverse_mismatch = df[~high_sev & high_pri]
        untriaged_critical = df[high_sev & (df["priority_label"] == "5-N/A")]

        col1.metric(
            "🔴 Critical Mismatch",
            f"{len(critical_mismatch):,}",
            help="S1/S2 bugs with low priority or N/A — escalate to RD",
        )
        col2.metric(
            "🟡 Inverse Mismatch",
            f"{len(inverse_mismatch):,}",
            help="S3/S4 bugs with Fix Now/Must Fix — verify scope with RD",
        )
        col3.metric(
            "⚪ Untriaged Critical",
            f"{len(untriaged_critical):,}",
            help="S1/S2 bugs where RD has not set priority (N/A)",
        )

        if len(critical_mismatch) > 0:
            with st.expander(f"🔴 View Critical Mismatches ({len(critical_mismatch)} bugs)"):
                show_cols = [
                    c for c in [
                        "BugCode", "Short Description", "severity_label", "priority_label",
                        "Status", "parsed_module", "parsed_version",
                    ]
                    if c in critical_mismatch.columns
                ]
                bug_code_col = next(
                    (c for c in critical_mismatch.columns if "bugcode" in c.lower()), None
                )
                disp = critical_mismatch[show_cols].copy()
                if bug_code_col:
                    disp["ECL Link"] = critical_mismatch[bug_code_col].apply(
                        lambda x: ECL_BUG_URL.format(bug_code=x) if pd.notna(x) else ""
                    )
                    st.dataframe(
                        disp,
                        column_config={
                            "ECL Link": st.column_config.LinkColumn(
                                "ECL Link", display_text="Open 🔗"
                            )
                        },
                        use_container_width=True,
                        hide_index=True,
                    )
                else:
                    st.dataframe(disp, use_container_width=True, hide_index=True)
    else:
        st.warning(
            "Need `priority_label` (or `Priority`) and `severity_num` columns. Re-run parse_ecl_export.py v2.3."
        )


# =====================================================================
# TAB 5 – Team Coverage
# =====================================================================

elif active_tab == "👥 Team Coverage":
    st.header("Tester × Module Coverage")

    if "Creator" in df.columns and "module_category" in df.columns:
        cv = df.pivot_table(
            index="Creator",
            columns="module_category",
            aggfunc="size",
            fill_value=0,
        )
        fig5 = px.imshow(
            cv,
            color_continuous_scale="Greens",
            aspect="auto",
            text_auto=True,
        )
        fig5.update_layout(height=max(400, len(cv) * 35))
        st.plotly_chart(fig5, use_container_width=True)

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

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Bugs (filtered)", f"{len(df):,}")
    c2.metric("Critical Bugs (S1)", f"{int((df['severity_num'] == 1).sum()):,}")

    if "days_to_close" in df.columns:
        ac = df["days_to_close"].dropna().mean()
        c3.metric("Avg Days to Close", f"{ac:.1f}" if not np.isnan(ac) else "N/A")
    else:
        c3.metric("Avg Days to Close", "N/A")

    se2 = [c for c in df.columns if "side_effect" in c.lower() and c.startswith("tag_")]
    if se2:
        reg_rate = df[se2[0]].mean() * 100
        c4.metric("Regression Bug Rate", f"{reg_rate:.1f}%")
    else:
        c4.metric("Regression Bug Rate", "N/A")

    if "status_active" in df.columns:
        st.markdown("---")
        sa1, sa2 = st.columns(2)
        sa1.metric("Active Bugs (in filter)", f"{df['status_active'].sum():,}")
        sa2.metric(
            "Inactive Bugs (in filter)",
            f"{(~df['status_active']).sum():,}",
            help="Close, NAB, Won't Fix, etc.",
        )

    if risk_available:
        st.markdown("---")
        cr1, cr2, cr3 = st.columns(3)
        q_counts = df.groupby("quadrant").size()
        cr1.metric("Q4 Modules (Test First)", int(q_counts.get("Q4", 0)))
        cr2.metric("Q3 Modules (Test Second)", int(q_counts.get("Q3", 0)))
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
                use_container_width=True,
            )

    st.subheader("Severity Distribution")
    sd = df["severity_num"].value_counts().sort_index()
    sd.index = sd.index.map(
        {1: "S1-Critical", 2: "S2-Major", 3: "S3-Normal", 4: "S4-Minor"}
    )
    st.plotly_chart(
        px.pie(
            values=sd.values,
            names=sd.index,
            color_discrete_sequence=["#f44336", "#f57c00", "#fbc02d", "#4caf50"],
        ).update_layout(height=350),
        use_container_width=True,
    )


# =====================================================================
# TAB 7 – Risk Heatmap
# =====================================================================

elif active_tab == "🔥 Risk Heatmap":
    st.header("🔥 Risk-Based Bug Heatmap")

    if not risk_available:
        st.warning(
            """
**Risk data not loaded.** To unlock this tab:

1. Run: `python scripts/ai_risk_scorer.py data/risk_register.csv data/risk_register_scored.csv --provider heuristic`
2. In the **sidebar → Step 2**, upload `data/risk_register_scored.csv`
"""
        )
        st.stop()

    st.caption(
        "Rectangle **size** = bug count | **Color** = selected metric | Click category to drill down"
    )

    color_opt = st.radio(
        "Color by",
        ["Risk Score (LLM)", "Quadrant", "Critical Count", "Severity Weight"],
        horizontal=True,
        key="tmc",
    )

    tm_agg = df.groupby(["module_category", "parsed_module"]).agg(
        bug_count=("parsed_module", "size"),
        sev_weight=("severity_weight", "sum"),
        critical_count=("severity_num", lambda x: (x == 1).sum()),
        risk_score=("risk_score_final", "mean"),
    ).reset_index()

    tm_agg = tm_agg[tm_agg["parsed_module"].notna() & (tm_agg["bug_count"] > 0)]
    q_lookup = df.groupby("parsed_module")["quadrant"].first().to_dict()
    tm_agg["quadrant"] = tm_agg["parsed_module"].map(q_lookup).fillna("Q1")

    if color_opt == "Risk Score (LLM)":
        fig_tm = px.treemap(
            tm_agg,
            path=["module_category", "parsed_module"],
            values="bug_count",
            color="risk_score",
            color_continuous_scale="YlOrRd",
            labels={
                "bug_count": "Bugs", "risk_score": "Risk Score",
                "module_category": "Category", "parsed_module": "Module",
            },
            hover_data={
                "bug_count": True, "risk_score": ":.1f",
                "critical_count": True, "quadrant": True,
            },
        )
    elif color_opt == "Quadrant":
        fig_tm = px.treemap(
            tm_agg,
            path=["module_category", "parsed_module"],
            values="bug_count",
            color="quadrant",
            color_discrete_map=QUADRANT_COLORS,
            labels={
                "bug_count": "Bugs", "quadrant": "Quadrant",
                "module_category": "Category", "parsed_module": "Module",
            },
            hover_data={"bug_count": True, "risk_score": ":.1f", "critical_count": True},
        )
    elif color_opt == "Critical Count":
        fig_tm = px.treemap(
            tm_agg,
            path=["module_category", "parsed_module"],
            values="bug_count",
            color="critical_count",
            color_continuous_scale="Reds",
            labels={
                "bug_count": "Bugs", "critical_count": "Critical Bugs",
                "module_category": "Category", "parsed_module": "Module",
            },
        )
    else:
        fig_tm = px.treemap(
            tm_agg,
            path=["module_category", "parsed_module"],
            values="bug_count",
            color="sev_weight",
            color_continuous_scale="YlOrRd",
            labels={
                "bug_count": "Bugs", "sev_weight": "Severity Weight",
                "module_category": "Category", "parsed_module": "Module",
            },
        )

    fig_tm.update_layout(height=720, margin=dict(t=30, l=10, r=10, b=10))
    st.plotly_chart(fig_tm, use_container_width=True)

    with st.expander("🌞 Sunburst View"):
        if color_opt == "Quadrant":
            fig_sb = px.sunburst(
                tm_agg,
                path=["module_category", "parsed_module"],
                values="bug_count",
                color="quadrant",
                color_discrete_map=QUADRANT_COLORS,
            )
        else:
            fig_sb = px.sunburst(
                tm_agg,
                path=["module_category", "parsed_module"],
                values="bug_count",
                color="risk_score",
                color_continuous_scale="YlOrRd",
            )
        fig_sb.update_layout(height=600, margin=dict(t=10, l=10, r=10, b=10))
        st.plotly_chart(fig_sb, use_container_width=True)

    with st.expander("📋 Category Summary"):
        cats = (
            tm_agg.groupby("module_category")
            .agg(
                Modules=("parsed_module", "nunique"),
                Total_Bugs=("bug_count", "sum"),
                Severity_Weight=("sev_weight", "sum"),
                Avg_Risk=("risk_score", "mean"),
                Q4_Modules=("quadrant", lambda x: (x == "Q4").sum()),
            )
            .sort_values("Avg_Risk", ascending=False)
            .reset_index()
        )
        cats.columns = [
            "Category", "Modules", "Total Bugs",
            "Severity Weight", "Avg Risk Score", "Q4 Modules",
        ]
        st.dataframe(cats, use_container_width=True, hide_index=True)

    st.subheader("🔴 Q4 Modules — Test Every Build")
    q4 = tm_agg[tm_agg["quadrant"] == "Q4"].sort_values("risk_score", ascending=False)
    if len(q4):
        fig_q4 = px.bar(
            q4.head(20),
            x="parsed_module",
            y="risk_score",
            color="module_category",
            labels={
                "parsed_module": "Module",
                "risk_score": "Risk Score",
                "module_category": "Category",
            },
            title="Q4 Modules by Risk Score",
        )
        fig_q4.update_layout(height=400, xaxis_tickangle=-30)
        st.plotly_chart(fig_q4, use_container_width=True)
    else:
        st.info("No Q4 modules in current filter.")

    if risk_df is not None and "probability_score_auto" in risk_df.columns:
        st.subheader("Risk Score vs Probability")
        scatter_df = tm_agg.copy()
        prob_map = risk_df.set_index("parsed_module")["probability_score_auto"].to_dict()
        scatter_df["probability"] = scatter_df["parsed_module"].map(prob_map).fillna(2.5)

        def assign_zone(score: float) -> str:
            if score >= 60:
                return "Critical Risk (≥60)"
            if score >= 30:
                return "High Risk (30-59)"
            if score >= 10:
                return "Medium Risk (10-29)"
            return "Low Risk (<10)"

        scatter_df["risk_zone"] = scatter_df["risk_score"].apply(assign_zone)
        zone_colors = {
            "Critical Risk (≥60)": "#D62728",
            "High Risk (30-59)": "#FF7F0E",
            "Medium Risk (10-29)": "#BCBD22",
            "Low Risk (<10)": "#2CA02C",
        }

        fig_scatter = px.scatter(
            scatter_df,
            x="probability",
            y="risk_score",
            color="risk_zone",
            color_discrete_map=zone_colors,
            size="bug_count",
            hover_name="parsed_module",
            hover_data={
                "probability": True,
                "risk_score": ":.1f",
                "bug_count": True,
                "risk_zone": False,
            },
            labels={
                "probability": "Probability Score (1–5)",
                "risk_score": "Risk Score (I×P×D)",
                "bug_count": "Bug Count",
            },
            size_max=30,
        )

        for y_val, label in [
            (10, "Medium threshold"),
            (30, "High threshold"),
            (60, "Critical threshold"),
        ]:
            fig_scatter.add_hline(
                y=y_val,
                line_dash="dash",
                line_color="gray",
                line_width=1,
                annotation_text=label,
                annotation_position="right",
            )

        fig_scatter.update_layout(height=500)
        st.plotly_chart(fig_scatter, use_container_width=True)
