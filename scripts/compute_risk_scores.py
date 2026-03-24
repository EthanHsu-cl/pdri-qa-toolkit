#!/usr/bin/env python3
"""PDR-I Risk Register Generator v2.3

Behavior:

- Single call:
    python compute_risk_scores.py data/ecl_parsed.csv data/risk_register_all.csv

  Produces:
    - data/risk_register_all.csv        ← combined (all versions)
    - data/risk_register_<ver>.csv      ← one per parsed_version value

No extra arguments required.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

def _compute_risk_core(df: pd.DataFrame) -> pd.DataFrame:
    df = df[df["parsed_module"].notna()].copy()
    tag_cols = [c for c in df.columns if c.startswith("tag_")]

    agg = (
        df.groupby("parsed_module")
        .agg(
            category=("module_category", "first"),
            total_bugs=("parsed_module", "size"),
            severity_weighted_total=("severity_weight", "sum"),
            critical_count=("severity_num", lambda x: (x == 1).sum()),
            major_count=("severity_num", lambda x: (x == 2).sum()),
            normal_count=("severity_num", lambda x: (x == 3).sum()),
            minor_count=("severity_num", lambda x: (x == 4).sum()),
        )
        .reset_index()
        .rename(columns={"parsed_module": "module"})
    )

    for tc in tag_cols:
        t = df.groupby("parsed_module")[tc].sum().reset_index()
        t.columns = ["module", tc.replace("tag_", "") + "_count"]
        agg = agg.merge(t, on="module", how="left")

    se_col = [c for c in agg.columns if "side_effect" in c.lower()]
    if se_col:
        agg["regression_rate"] = (agg[se_col[0]] / agg["total_bugs"]).round(4)

    at_col = [c for c in agg.columns if "at_found" in c.lower()]
    if at_col:
        agg["automation_catch_rate"] = (agg[at_col[0]] / agg["total_bugs"]).round(4)

    for metric in ["repro_rate", "days_to_close", "builds_to_fix"]:
        if metric in df.columns:
            m = (
                df.groupby("parsed_module")[metric]
                .agg(["mean", "median"])
                .reset_index()
            )
            m.columns = ["module", f"avg_{metric}", f"median_{metric}"]
            agg = agg.merge(m, on="module", how="left")

    if "Status" in df.columns:
        for status in ["Open", "Closed", "Postpone"]:
            s = (
                df[df["Status"].str.contains(status, case=False, na=False)]
                .groupby("parsed_module")
                .size()
                .reset_index()
            )
            s.columns = ["module", f"{status.lower()}_count"]
            agg = agg.merge(s, on="module", how="left")

    if "Creator" in df.columns:
        r = df.groupby("parsed_module")["Creator"].nunique().reset_index()
        r.columns = ["module", "unique_reporters"]
        agg = agg.merge(r, on="module", how="left")

    agg = agg.fillna(0)

    # Probability score: quintile rank, safe for small slices
    n = len(agg)
    if n >= 5:
        agg["probability_score_auto"] = pd.qcut(
            agg["total_bugs"].rank(method="first"),
            q=5,
            labels=[1, 2, 3, 4, 5],
            duplicates="drop",
        ).astype(float).fillna(1).astype(int)
    else:
        agg["probability_score_auto"] = (
            agg["total_bugs"]
            .rank(method="first")
            .apply(lambda r: max(1, min(5, int(np.ceil(r / n * 5)))))
            .astype(int)
        )

    agg["impact_score"] = 0
    agg["detectability_score"] = 0
    agg["risk_score_final"] = 0
    agg["quadrant"] = ""

    agg = agg.sort_values("severity_weighted_total", ascending=False)
    return agg


def compute_risk_all_and_per_version(input_csv: str, output_csv_all: str):
    df = pd.read_csv(input_csv, low_memory=False)
    print(f"Loaded {len(df)} bugs from {input_csv}")

    if "parsed_version" not in df.columns:
        print("ERROR: `parsed_version` column not found in ecl_parsed.csv")
        sys.exit(1)

    # 1) Combined risk register (all versions)
    agg_all = _compute_risk_core(df)
    agg_all.to_csv(output_csv_all, index=False, encoding="utf-8-sig")
    print(f"\n[ALL] RISK REGISTER: {len(agg_all)} modules")
    for _, r in agg_all.head(10).iterrows():
        print(
            f" {r['module']:30s} wt={r['severity_weighted_total']:>6.0f} "
            f"crit={r['critical_count']:>3.0f}"
        )
    print(f"Saved combined register to: {output_csv_all}")

    # 2) Per-version risk registers -- stored under data/risk_register_versions/
    versions = sorted(df["parsed_version"].dropna().unique())
    out_path = Path(output_csv_all)
    base_dir = out_path.parent
    ver_dir  = base_dir / "risk_register_versions"
    ver_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nPer-version risk registers -> {ver_dir}/")
    for ver in versions:
        df_ver = df[df["parsed_version"] == ver].copy()
        if df_ver.empty:
            continue
        agg_ver = _compute_risk_core(df_ver)
        ver_safe = str(ver).replace(" ", "_")
        ver_out = ver_dir / f"risk_register_{ver_safe}.csv"
        agg_ver["parsed_version"] = ver  # keep version for later AI scoring
        agg_ver.to_csv(ver_out, index=False, encoding="utf-8-sig")
        print(
            f"  {ver:15s} -> {len(agg_ver):4d} modules  -> risk_register_versions/{ver_out.name}"
        )


def main():
    if len(sys.argv) < 3:
        print("Usage: python compute_risk_scores.py <ecl_parsed.csv> <risk_register_all.csv>")
        sys.exit(1)
    compute_risk_all_and_per_version(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    main()