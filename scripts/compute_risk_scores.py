#!/usr/bin/env python3
"""PDR-I Risk Register Generator v2.4

Changes from v2.3:
  - Per-version risk registers are now generated in recency order (newest
    first, by max Create Date) rather than lexicographic version-string order.
    This is robust against version strings that don't sort numerically and
    against typo/sparse versions (< VERSION_SPARSE_THRESHOLD bugs) that would
    appear at the wrong position in a string-sorted list.
  - Sparse versions (< VERSION_SPARSE_THRESHOLD bugs) are skipped for
    per-version risk register generation.  They are still included in the
    combined all-versions register.  A warning is printed for each skipped
    version so the operator knows it was intentional.
  - Reads version_catalogue.csv (produced by parse_ecl_export.py) when
    available to reuse the pre-computed recency ordering and sparse flags.
    Falls back gracefully to re-computing the same logic from the CSV data
    when the catalogue file is not present.

Behavior:
- Single call:
    python compute_risk_scores.py data/ecl_parsed.csv data/risk_register_all.csv

  Produces:
    - data/risk_register_all.csv        ← combined (all versions)
    - data/risk_register_<ver>.csv      ← one per non-sparse parsed_version,
                                          newest first

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


def _version_order(df: pd.DataFrame, cat_path: str = None):
    """Return (version_string, is_sparse) tuples ordered newest → oldest.

    Resolution priority:
      1. version_catalogue.csv produced by parse_ecl_export.py (most accurate)
      2. Re-derive from Create Date in df (same algorithm, no catalogue needed)

    Sparse = fewer than VERSION_SPARSE_THRESHOLD bugs.  Sparse versions are
    returned last so callers can easily skip them for per-version registers
    while still having the full list for reference.
    """
    VERSION_SPARSE_THRESHOLD = 5

    # ── Try reading pre-built catalogue ─────────────────────────────────────
    if cat_path and Path(cat_path).exists():
        try:
            cat = pd.read_csv(cat_path)
            if {"parsed_version", "version_rank", "version_is_sparse"}.issubset(cat.columns):
                cat = cat.sort_values("version_rank")
                return [
                    (str(r["parsed_version"]), bool(r["version_is_sparse"]))
                    for _, r in cat.iterrows()
                    if pd.notna(r["parsed_version"])
                ]
        except Exception as e:
            print(f"  Note: could not read version catalogue ({e}) — re-deriving from data.")

    # ── Fallback: derive from Create Date ────────────────────────────────────
    rows = []
    for ver, grp in df.groupby("parsed_version", dropna=True):
        n = len(grp)
        if "Create Date" in grp.columns:
            latest = pd.to_datetime(grp["Create Date"], errors="coerce").max()
        else:
            latest = pd.NaT
        rows.append({
            "parsed_version":     ver,
            "bug_count":          n,
            "latest_create_date": latest,
            "version_is_sparse":  n < VERSION_SPARSE_THRESHOLD,
        })
    if not rows:
        return []

    cat = pd.DataFrame(rows)
    cat["_sort_key"] = cat["latest_create_date"].fillna(pd.Timestamp.min)
    real   = cat[~cat["version_is_sparse"]].sort_values("_sort_key", ascending=False)
    sparse = cat[ cat["version_is_sparse"]].sort_values("_sort_key", ascending=False)
    cat = pd.concat([real, sparse], ignore_index=True)
    return [
        (str(r["parsed_version"]), bool(r["version_is_sparse"]))
        for _, r in cat.iterrows()
    ]


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

    # 2) Per-version risk registers, newest first, sparse versions skipped
    out_path = Path(output_csv_all)
    base_dir = out_path.parent
    cat_path = str(base_dir / "version_catalogue.csv")
    ver_dir  = base_dir / "risk_register_versions"
    ver_dir.mkdir(parents=True, exist_ok=True)

    version_order = _version_order(df, cat_path=cat_path)

    print(f"\nPer-version risk registers -> {ver_dir}/")
    n_written = n_skipped = 0
    for ver, is_sparse in version_order:
        df_ver = df[df["parsed_version"] == ver].copy()
        if df_ver.empty:
            continue
        if is_sparse:
            print(f"  ⚠️  SKIP  {ver:<18s} ({len(df_ver)} bugs) — sparse/typo version, "
                  f"excluded from per-version registers")
            n_skipped += 1
            continue
        agg_ver = _compute_risk_core(df_ver)
        ver_safe = str(ver).replace(" ", "_").replace(".", "_")
        ver_out = ver_dir / f"risk_register_{ver_safe}.csv"
        agg_ver["parsed_version"] = ver
        agg_ver.to_csv(ver_out, index=False, encoding="utf-8-sig")
        print(
            f"  ✅  {ver:<18s} -> {len(agg_ver):4d} modules  -> "
            f"risk_register_versions/{ver_out.name}"
        )
        n_written += 1

    print(f"\n  Written: {n_written} per-version files  |  Skipped (sparse): {n_skipped}")


def main():
    if len(sys.argv) < 3:
        print("Usage: python compute_risk_scores.py <ecl_parsed.csv> <risk_register_all.csv>")
        sys.exit(1)
    compute_risk_all_and_per_version(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    main()