#!/usr/bin/env python3
"""PDR-I Auto-Tagger v3.0

Changes from v2.1:
  - --cluster-predictions <csv>: accepts the _predictions_by_cluster.csv
    produced by predict_defects.py v3.0.  When provided, generate_skeleton_tests()
    emits one targeted test method per predicted bug theme in addition to the
    three standard methods.  Theme methods are named from the cluster label
    (e.g. "login crash timeout" → test_login_crash_timeout), marked with the
    appropriate allure severity, and include a TODO comment showing the expected
    bug count so engineers know how many bugs to expect in that area.

  - generate_cluster_test_plan(): new function that writes a standalone
    data/cluster_test_plan.md summarising, for every high-risk module, which
    specific test scenarios to prioritise based on the predicted bug-theme
    breakdown.  No UI changes required — run with --cluster-plan.

  - generate_summary() extended: when the cluster predictions are loaded, the
    markdown summary table gains a "Expected bug themes" column for P1/P2
    modules showing the top 2 predicted themes inline.

  - All v2.1 behaviour (--generate-skeletons, --tag-existing, --summary)
    is fully backward-compatible.  The --cluster-predictions argument is
    optional; omitting it gives identical output to v2.1.

Usage:
  python auto_tag_tests.py scored.csv --generate-skeletons tests/generated/
  python auto_tag_tests.py scored.csv --generate-skeletons tests/generated/ \\
      --cluster-predictions data/predictions/ecl_parsed_predictions_by_cluster.csv
  python auto_tag_tests.py scored.csv --summary --cluster-plan
  python auto_tag_tests.py scored.csv --tag-existing tests/
"""
import os, re, sys, argparse, pandas as pd
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# Helpers  (unchanged from v2.1)
# ─────────────────────────────────────────────────────────────────────────────

def module_to_filename(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", name.lower())
    return f"test_{re.sub(r'_+', '_', safe).strip('_')}.py"


def module_to_classname(name: str) -> str:
    words = re.sub(r"[^a-zA-Z0-9 ]", " ", name).split()
    return "Test" + "".join(w.capitalize() for w in words)


def _label_to_method_name(label: str) -> str:
    """Convert a cluster label string to a valid Python method name suffix.

    e.g. "login crash | timeout"  →  "login_crash_timeout"
         "UI button misalignment" →  "ui_button_misalignment"
    """
    # Replace separators (pipe, slash, dash) with spaces first
    label = re.sub(r"[|/\-–]", " ", label)
    # Keep only alphanumeric and spaces, collapse, lowercase
    safe = re.sub(r"[^a-zA-Z0-9 ]", "", label).lower()
    safe = re.sub(r"\s+", "_", safe.strip())
    safe = re.sub(r"_+", "_", safe).strip("_")
    return safe[:60] or "theme"


def _label_to_allure_title(label: str) -> str:
    """Convert a cluster label to a short human-readable Allure title."""
    # Strip TF-IDF pipe separators for readability
    label = re.sub(r"\s*\|\s*", " / ", label).strip()
    return label[:80]


# ─────────────────────────────────────────────────────────────────────────────
# Cluster predictions loader
# ─────────────────────────────────────────────────────────────────────────────

def load_cluster_predictions(path: str) -> "pd.DataFrame | None":
    """Load _predictions_by_cluster.csv and validate required columns.

    Returns a DataFrame with at least [module, cluster_label, predicted_count,
    historical_pct], or None if the file cannot be loaded or is missing columns.
    """
    if not path or not Path(path).exists():
        return None
    try:
        df = pd.read_csv(path)
    except Exception as e:
        print(f"  WARNING: could not load cluster predictions ({e}) — skipping theme tests.")
        return None

    required = {"module", "cluster_label", "predicted_count"}
    if not required.issubset(df.columns):
        missing = required - set(df.columns)
        print(f"  WARNING: cluster predictions missing columns {missing} — skipping theme tests.")
        return None

    df["predicted_count"] = pd.to_numeric(df["predicted_count"], errors="coerce").fillna(0.0)
    df["historical_pct"]  = pd.to_numeric(df.get("historical_pct", 0), errors="coerce").fillna(0.0)

    # Drop noise / unclustered rows — they don't produce useful test names
    df = df[~df["cluster_label"].str.lower().str.contains(
        r"unclustered|no cluster|noise", na=False)]

    print(f"  Loaded {len(df)} module×cluster rows from {path}")
    return df


def _get_module_clusters(cluster_df: "pd.DataFrame | None",
                          module: str,
                          top_n: int = 5) -> list:
    """Return a list of dicts [{label, predicted_count, historical_pct}, ...]
    for the given module, sorted by predicted_count descending."""
    if cluster_df is None:
        return []
    mdf = cluster_df[cluster_df["module"] == module].sort_values(
        "predicted_count", ascending=False).head(top_n)
    return mdf[["cluster_label", "predicted_count", "historical_pct"]].to_dict("records")


# ─────────────────────────────────────────────────────────────────────────────
# Skeleton generator  (updated for v3.0)
# ─────────────────────────────────────────────────────────────────────────────

def generate_skeleton_tests(df: pd.DataFrame,
                             output_dir: str,
                             cluster_df: "pd.DataFrame | None" = None) -> None:
    """Generate one pytest file per module.

    When cluster_df is provided, each file gains one additional test method
    per predicted bug theme (cluster label), named after the theme and annotated
    with the expected bug count as a TODO comment.
    """
    os.makedirs(output_dir, exist_ok=True)
    count = 0
    sev_map = {
        "p1 - critical": "CRITICAL",
        "p2 - high":     "NORMAL",
        "p3 - medium":   "MINOR",
        "p4 - low":      "TRIVIAL",
    }

    for _, row in df.iterrows():
        module   = row["module"]
        category = row.get("category", "Unknown")
        quadrant = row.get("quadrant", "P3 - Medium")
        q        = quadrant.split(" ")[0].lower()
        risk     = row.get("risk_score_final", 0)
        iv       = int(row.get("impact_score", 3))
        pv       = int(row.get("probability_score_auto", 3)) if "probability_score_auto" in row else 3
        dv       = int(row.get("detectability_score", 3))
        sev      = sev_map.get(q, "NORMAL")
        cls      = module_to_classname(module)
        fn       = module_to_filename(module)
        vn       = fn.replace(".py", "").replace("test_", "")

        # Cluster themes for this module
        themes = _get_module_clusters(cluster_df, module)

        # ── File header ──────────────────────────────────────────────────────
        lines = [
            f'#!/usr/bin/env python3',
            f'"""Auto-generated test for: {module}',
            f'Category: {category} | Quadrant: {quadrant} | Risk: {risk:.0f} (I:{iv} x P:{pv} x D:{dv})',
        ]
        if themes:
            lines.append(f'Predicted bug themes (next build):')
            for t in themes:
                lines.append(
                    f'  - {t["cluster_label"]} '
                    f'(~{t["predicted_count"]:.0f} bugs, {t["historical_pct"] * 100:.0f}%)'
                )
        lines += [
            f'TODO: Replace placeholder selectors."""',
            f'import pytest',
            f'import allure',
            f'',
            f'@allure.suite("{category}")',
            f'@allure.sub_suite("{module}")',
            f'@allure.tag("{q.upper()}")',
            f'@pytest.mark.{q}',
            f'class {cls}:',
            f'    """{quadrant} tests for {module}.',
        ]
        if themes:
            lines.append(f'')
            lines.append(f'    Predicted themes for next build:')
            for t in themes:
                lines.append(
                    f'    - {t["cluster_label"]} '
                    f'(~{t["predicted_count"]:.0f} bugs)'
                )
        lines += [f'    """', f'']

        # ── Standard smoke tests (always present) ─────────────────────────────
        lines += [
            f'    @allure.title("{module} - Screen Launch")',
            f'    @allure.severity(allure.severity_level.{sev})',
            f'    def test_screen_launches(self, driver):',
            f'        pass',
            f'',
            f'    @allure.title("{module} - Basic Functionality")',
            f'    @allure.severity(allure.severity_level.{sev})',
            f'    def test_basic_functionality(self, driver):',
            f'        pass',
            f'',
            f'    @allure.title("{module} - Visual Regression")',
            f'    @allure.severity(allure.severity_level.NORMAL)',
            f'    @pytest.mark.visual',
            f'    def test_visual_regression(self, driver, visual_check):',
            f'        # TODO: Navigate then call visual_check("{vn}")',
            f'        pass',
        ]

        # ── Per-theme targeted tests (new in v3.0) ────────────────────────────
        if themes:
            lines += [f'', f'    # ── Predicted bug-theme tests (auto-generated from cluster forecast) ──']
            for t in themes:
                theme_label  = t["cluster_label"]
                theme_method = _label_to_method_name(theme_label)
                theme_title  = _label_to_allure_title(theme_label)
                exp_count    = t["predicted_count"]
                exp_pct      = t["historical_pct"] * 100

                # Severity based on cluster avg_sev if available, else inherit module sev
                # Use CRITICAL if label contains known crash/data-loss keywords
                if re.search(r"\b(crash|data.?loss|corrupt|freeze|force.?close|unrespons)\b",
                             theme_label, re.I):
                    theme_sev = "CRITICAL"
                elif re.search(r"\b(fail|error|unable|broken|wrong|incorrect)\b",
                               theme_label, re.I):
                    theme_sev = "NORMAL"
                else:
                    theme_sev = "MINOR"

                lines += [
                    f'',
                    f'    @allure.title("{module} - {theme_title}")',
                    f'    @allure.severity(allure.severity_level.{theme_sev})',
                    f'    @pytest.mark.predicted_theme',
                    f'    def test_{theme_method}(self, driver):',
                    f'        # Cluster forecast: ~{exp_count:.0f} bugs expected ({exp_pct:.0f}% of module bugs)',
                    f'        # Theme: {theme_label}',
                    f'        # TODO: Write scenario that exercises this failure pattern.',
                    f'        pass',
                ]

        with open(os.path.join(output_dir, fn), "w", encoding="utf-8") as fout:
            fout.write("\n".join(lines) + "\n")
        count += 1

    theme_count = sum(len(_get_module_clusters(cluster_df, r["module"])) for _, r in df.iterrows())
    print(f"Generated {count} test skeletons in {output_dir}/")
    if cluster_df is not None:
        print(f"  Includes {theme_count} predicted-theme test methods across all files.")


# ─────────────────────────────────────────────────────────────────────────────
# Tag existing  (unchanged from v2.1)
# ─────────────────────────────────────────────────────────────────────────────

def tag_existing_tests(df: pd.DataFrame, test_dir: str) -> None:
    mm = {
        r["module"].lower(): r.get("quadrant", "P3 - Medium").split(" ")[0].lower()
        for _, r in df.iterrows()
    }
    updated = 0
    for pf in Path(test_dir).glob("test_*.py"):
        content = pf.read_text(encoding="utf-8")
        for mn, q in mm.items():
            if mn in content.lower():
                for old in re.findall(r"@pytest\.mark\.(p[1-4])", content):
                    if old != q:
                        content = content.replace(
                            f"@pytest.mark.{old}", f"@pytest.mark.{q}")
                        updated += 1
                break
        pf.write_text(content, encoding="utf-8")
    print(f"Updated {updated} markers in {test_dir}/")


# ─────────────────────────────────────────────────────────────────────────────
# Summary  (updated for v3.0 — adds theme column for P1/P2 rows)
# ─────────────────────────────────────────────────────────────────────────────

def generate_summary(df: pd.DataFrame,
                     path: str = "data/quadrant_summary.md",
                     cluster_df: "pd.DataFrame | None" = None) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    lines = [
        "# PDR-I Risk-Based Testing: Priority Assignment\n",
        f"**Modules: {len(df)}**\n",
    ]

    has_themes = cluster_df is not None and not cluster_df.empty

    for ql, desc in [
        ("P1 - Critical", "Every build"),
        ("P2 - High",     "Daily"),
        ("P3 - Medium",   "RC only"),
        ("P4 - Low",      "RC if time"),
    ]:
        qd = df[df["quadrant"] == ql].sort_values("risk_score_final", ascending=False)
        lines.append(f"\n## {ql} ({len(qd)} modules) — {desc}\n")

        # For P1/P2, include predicted themes column when available
        show_themes = has_themes and ql in ("P1 - Critical", "P2 - High")

        if show_themes:
            lines.append("| Module | Category | Score | IxPxD | Predicted bug themes |")
            lines.append("|--------|----------|-------|-------|----------------------|")
        else:
            lines.append("| Module | Category | Score | IxPxD |")
            lines.append("|--------|----------|-------|-------|")

        for _, r in qd.iterrows():
            p = int(r.get("probability_score_auto", 3)) if "probability_score_auto" in r else 3
            base = (f"| {r['module']} | {r.get('category', '')} | "
                    f"{r['risk_score_final']:.0f} | "
                    f"{int(r['impact_score'])}x{p}x{int(r['detectability_score'])}")
            if show_themes:
                themes = _get_module_clusters(cluster_df, r["module"], top_n=2)
                if themes:
                    theme_str = "; ".join(
                        f"{t['cluster_label']} (~{t['predicted_count']:.0f})"
                        for t in themes
                    )
                else:
                    theme_str = "—"
                lines.append(base + f" | {theme_str} |")
            else:
                lines.append(base + " |")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Summary saved to: {path}")


# ─────────────────────────────────────────────────────────────────────────────
# NEW v3.0 — Cluster test plan
# ─────────────────────────────────────────────────────────────────────────────

def generate_cluster_test_plan(df: pd.DataFrame,
                                cluster_df: pd.DataFrame,
                                path: str = "data/cluster_test_plan.md") -> None:
    """Write a plain-English test plan driven by the per-cluster bug-type forecast.

    For each P1/P2 module, lists:
      - The expected total bug count
      - Each predicted bug theme with its expected count
      - A suggested test scenario description for each theme

    The scenario descriptions are generated by pattern-matching the cluster
    label against common QA terminology, so they read naturally without an
    AI call.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    # Join risk quadrant onto cluster predictions
    risk_cols = ["module", "quadrant", "risk_score_final", "predicted"]
    risk_cols = [c for c in risk_cols if c in df.columns]
    merged = cluster_df.merge(
        df[risk_cols], on="module", how="left")
    merged["quadrant"] = merged["quadrant"].fillna("P4 - Low")

    high_risk = merged[merged["quadrant"].isin(["P1 - Critical", "P2 - High"])]
    high_risk = high_risk.sort_values(
        ["risk_score_final", "predicted_count"],
        ascending=[False, False],
    )

    # Scenario verb mapping driven by cluster label keywords
    def _scenario_hint(label: str) -> str:
        l = label.lower()
        if re.search(r"\b(crash|freeze|force.?close|unrespons)\b", l):
            return "Trigger the action repeatedly. Verify app does not crash or become unresponsive."
        if re.search(r"\b(data.?loss|corrupt|overwrite|delete)\b", l):
            return "Create and save data through this flow. Verify integrity after the action completes."
        if re.search(r"\b(login|auth|sign.?in|token|session)\b", l):
            return "Test login with valid and invalid credentials. Verify session state after expiry."
        if re.search(r"\b(export|save|output|render|convert)\b", l):
            return "Run the full export/save flow. Verify the output file is valid and uncorrupted."
        if re.search(r"\b(ui|display|layout|button|icon|text|label)\b", l):
            return "Visually inspect all states (enabled, disabled, loading, error). Run visual regression."
        if re.search(r"\b(network|timeout|connect|offline|retry)\b", l):
            return "Simulate poor connectivity and timeout conditions. Verify graceful degradation."
        if re.search(r"\b(permission|access|restrict|deny)\b", l):
            return "Test with accounts that have and do not have the required permission."
        if re.search(r"\b(memory|leak|perf|slow|lag|hang)\b", l):
            return "Run the flow 5–10 times in sequence. Monitor memory/CPU. Verify no accumulation."
        return "Exercise the full user flow for this area. Check for regressions after recent code changes."

    lines = [
        "# PDR-I Cluster-Based Test Plan\n",
        "> Generated by `auto_tag_tests.py --cluster-plan`  \n",
        "> Covers **P1 and P2 modules only**. Ordered by risk score.  \n",
        "> Each theme row shows the predicted bug count and a suggested scenario.\n",
    ]

    seen_modules = []
    for mod, mod_rows in high_risk.groupby("module", sort=False):
        if mod in seen_modules:
            continue
        seen_modules.append(mod)

        q_val   = mod_rows["quadrant"].iloc[0]
        rs_val  = mod_rows["risk_score_final"].iloc[0] if "risk_score_final" in mod_rows.columns else 0
        pred_v  = mod_rows["predicted"].iloc[0] if "predicted" in mod_rows.columns else "?"

        q_icon  = "🔴" if q_val == "P1 - Critical" else "🟠"
        lines += [
            f"\n---\n",
            f"## {q_icon} {mod}  ({q_val} — risk score {rs_val:.0f})\n",
            f"**Predicted bugs next build:** {pred_v}  \n",
            f"**Themes to prioritise:**\n",
            f"| # | Predicted bugs | Theme | Scenario |",
            f"|---|---------------|-------|---------|",
        ]

        for i, (_, tr) in enumerate(
            mod_rows.sort_values("predicted_count", ascending=False).iterrows(), 1
        ):
            label  = str(tr["cluster_label"])
            cnt    = tr["predicted_count"]
            pct    = tr.get("historical_pct", 0) * 100
            hint   = _scenario_hint(label)
            lines.append(f"| {i} | ~{cnt:.0f} ({pct:.0f}%) | {label} | {hint} |")

    if not seen_modules:
        lines.append("\n_No P1/P2 modules found — all modules are medium or low risk._\n")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Cluster test plan saved to: {path}  ({len(seen_modules)} modules)")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="PDR-I Auto-Tagger v3.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic (same as v2.1):
  python auto_tag_tests.py data/risk_register_scored_all.csv \\
      --generate-skeletons tests/generated/

  # With cluster-aware theme tests (v3.0):
  python auto_tag_tests.py data/risk_register_scored_all.csv \\
      --generate-skeletons tests/generated/ \\
      --cluster-predictions data/predictions/ecl_parsed_predictions_by_cluster.csv

  # Cluster test plan only:
  python auto_tag_tests.py data/risk_register_scored_all.csv \\
      --cluster-predictions data/predictions/ecl_parsed_predictions_by_cluster.csv \\
      --cluster-plan

  # Full run:
  python auto_tag_tests.py data/risk_register_scored_all.csv \\
      --generate-skeletons tests/generated/ \\
      --cluster-predictions data/predictions/ecl_parsed_predictions_by_cluster.csv \\
      --summary --cluster-plan
""",
    )
    p.add_argument("input_csv",
                   help="Scored risk register CSV (risk_register_scored_all.csv)")
    p.add_argument("--generate-skeletons", metavar="DIR",
                   help="Output directory for generated test files")
    p.add_argument("--tag-existing", metavar="DIR",
                   help="Directory of existing test files to re-tag with current quadrant markers")
    p.add_argument("--summary", action="store_true",
                   help="Write data/quadrant_summary.md")
    p.add_argument("--cluster-predictions", metavar="CSV",
                   default="",
                   help="Path to _predictions_by_cluster.csv (from predict_defects.py v3.0). "
                        "Enables per-theme test methods and the cluster test plan.")
    p.add_argument("--cluster-plan", action="store_true",
                   help="Write data/cluster_test_plan.md (requires --cluster-predictions)")
    p.add_argument("--summary-path", default="data/quadrant_summary.md",
                   help="Output path for --summary (default: data/quadrant_summary.md)")
    p.add_argument("--plan-path", default="data/cluster_test_plan.md",
                   help="Output path for --cluster-plan (default: data/cluster_test_plan.md)")

    a = p.parse_args()
    df = pd.read_csv(a.input_csv)
    print(f"Loaded {len(df)} modules from {a.input_csv}")

    # Auto-detect cluster predictions if not provided but default path exists
    cluster_pred_path = a.cluster_predictions
    if not cluster_pred_path:
        auto = Path(a.input_csv).parent / "predictions" / (
            Path(a.input_csv).stem.replace("risk_register_scored_all", "ecl_parsed")
            + "_predictions_by_cluster.csv"
        )
        if auto.exists():
            cluster_pred_path = str(auto)
            print(f"  Auto-detected cluster predictions: {auto}")

    cluster_df = load_cluster_predictions(cluster_pred_path) if cluster_pred_path else None

    did_something = False

    if a.generate_skeletons:
        generate_skeleton_tests(df, a.generate_skeletons, cluster_df=cluster_df)
        did_something = True

    if a.tag_existing:
        tag_existing_tests(df, a.tag_existing)
        did_something = True

    if a.summary:
        generate_summary(df, path=a.summary_path, cluster_df=cluster_df)
        did_something = True

    if a.cluster_plan:
        if cluster_df is None:
            print("ERROR: --cluster-plan requires --cluster-predictions (or auto-detectable file).")
            sys.exit(1)
        generate_cluster_test_plan(df, cluster_df, path=a.plan_path)
        did_something = True

    if not did_something:
        p.print_help()


if __name__ == "__main__":
    main()