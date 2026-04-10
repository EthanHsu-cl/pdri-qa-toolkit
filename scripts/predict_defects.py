#!/usr/bin/env python3
"""PDR-I Defect Prediction v5.0

Changes from v4.0
─────────────────
Change 18 — Scenario-based bug forecasting
• NEW generate_bug_scenarios() orchestrator produces concrete, testable bug
  scenario predictions per module (e.g. "Crash when importing large .mov files
  on macOS Sonoma") instead of just counts.
• _extract_description_patterns() — heuristic (non-AI) scenario extractor that
  clusters recent bug descriptions with AgglomerativeClustering and picks
  representative descriptions as predicted scenarios.
• _generate_scenarios_ai_ollama() / _generate_scenarios_ai_claude() — AI-powered
  scenario generators that prompt the model for 3-5 concrete bug scenarios per
  module, grounded in historical patterns.
• Output: data/predictions/<stem>_predictions_by_scenario.csv with columns:
      module, risk_level, predicted_build, scenario_rank, scenario_text,
      confidence, source_bug_examples, supporting_categories, leading_signal
• generate_focus_summary() now appends top scenario predictions per module.

All v4.0 behaviour is unchanged.

Output paths:
  data/predictions/<stem>_predictions.csv
  data/predictions/<stem>_predictions_by_category.csv  (always)
  data/predictions/<stem>_predictions_by_scenario.csv  ← NEW (always)
  data/predictions/<stem>_predictions_by_cluster.csv   (when --cluster-csv given)
  data/predictions/<stem>_predictions_importance.csv
  data/predictions/<stem>_predictions_leading_indicators.csv
  data/predictions/<stem>_predictions_focus_summary.txt

Usage:
  python predict_defects.py <input_csv>
  python predict_defects.py <input_csv> --cluster-csv data/clusters/ecl_parsed_clustered.csv
  python predict_defects.py <input_csv> --provider ollama --model gemma4
  python predict_defects.py <input_csv> --provider claude --api-key <key>
  python predict_defects.py <input_csv> --no-ai
  python predict_defects.py <input_csv> --scored-csv <path>
"""
import os
import re
import sys
import json
import urllib.request
import warnings
import pandas as pd
import numpy as np
from tqdm import tqdm
from pathlib import Path
from sklearn.cluster import AgglomerativeClustering
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_distances
from sklearn.model_selection import TimeSeriesSplit, cross_val_score
warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

PREDICTION_GUIDE = """
╔══════════════════════════════════════════════════════════════════════════════╗
║           PDR-I DEFECT PREDICTION v5.0 — HOW TO READ THIS OUTPUT           ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                             ║
║  WHAT THIS TOOL PREDICTS                                                    ║
║  ───────────────────────                                                    ║
║  For each module, the model predicts:                                       ║
║   1. A risk level (Low / Medium / High / Critical) for the NEXT build.     ║
║   2. WHAT TYPES of bugs are expected (by QA category).                     ║
║   3. CONCRETE BUG SCENARIOS — specific, testable predictions like real     ║
║      bug titles, grounded in recurring historical patterns.                 ║
║                                                                             ║
║  SCENARIO PREDICTION OUTPUT (_predictions_by_scenario.csv)                 ║
║  ─────────────────────────────────────────────────────────                  ║
║  module              Which module this scenario applies to                   ║
║  scenario_text       A concrete, testable bug prediction                    ║
║  confidence          high (3+ recurring bugs) / medium (2) / low (1)       ║
║  source_bug_examples Real bug descriptions this scenario is based on        ║
║  supporting_categories  QA categories that support this scenario            ║
║                                                                             ║
║  HOW TO ACT ON IT                                                           ║
║  ────────────────                                                           ║
║  Critical/High modules → add predicted scenarios to your test plan.        ║
║  Read '_by_scenario.csv' for WHAT SPECIFIC BUGS to test for.               ║
║  Read '_by_category.csv' for the category-level distribution.              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

TFIDF_TOP_N  = 40
TFIDF_PREFIX = "tfidf_"
OLLAMA_BASE  = "http://localhost:11434"

SCENARIO_TOP_MODULES    = 10
SCENARIOS_PER_MODULE    = 5
SCENARIO_HISTORY_BUILDS = 5

_FEATURE_LABELS = {
    "crit_1":  "critical-bug momentum (last build)",
    "crit_2":  "critical-bug momentum (last 2 builds)",
    "crit_3":  "critical-bug momentum (last 3 builds)",
    "bugs_1":  "recent bug-count momentum (last build)",
    "bugs_2":  "recent bug-count momentum (last 2 builds)",
    "bugs_3":  "recent bug-count momentum (last 3 builds)",
    "sev_1":   "severity-weighted momentum (last build)",
    "sev_2":   "severity-weighted momentum (last 2 builds)",
    "sev_3":   "severity-weighted momentum (last 3 builds)",
    "trend":   "upward bug-count trend",
    # Change 11 — new features
    "severity_escalation":     "severity escalation (negative = worsening toward S1)",
    "builds_since_last_crit":  "builds elapsed since last critical bug",
    # Change 12 — cluster features
    "cluster_entropy_2":       "bug-theme diversity index (last 2 builds)",
    "cluster_entropy_3":       "bug-theme diversity index (last 3 builds)",
    "top_cluster_velocity":    "fastest-growing bug theme velocity",
    # Change 19 — new features
    "crit_ratio":             "proportion of critical bugs (S1 / total)",
    "new_module":             "new module (first appeared in recent builds)",
    "cross_module_spike":     "correlated spike — related modules also spiking",
    "total_historical_bugs":  "total historical bug count (module maturity)",
    # existing optional features
    "repro_rate":             "high reproduce rate (consistently reproducible bugs)",
    "impact_score":           "domain impact score (I×P×D scorer)",
    "detectability_score":    "test detectability score (I×P×D scorer)",
    "probability_score_auto": "historical defect probability (I×P×D scorer)",
    "risk_score_final":       "composite risk score I×P×D",
}

# Cluster feature columns — excluded from per-module leading_signal search
# because they are more structural signals than per-build time-series signals.
_CLUSTER_FEATURE_COLS = {"cluster_entropy_2", "cluster_entropy_3", "top_cluster_velocity"}

_RISK_ADVICE = {
    "Critical": "Mandatory — add to test suite for every build. Focus on crash and data-loss scenarios.",
    "High":     "Priority — run core functional tests every build and watch for regressions.",
    "Medium":   "Standard — include in sprint regression pass.",
    "Low":      "Monitor — include in release-candidate pass.",
}

# ─────────────────────────────────────────────────────────────────────────────
# Change 15 — QA bug category taxonomy
# ─────────────────────────────────────────────────────────────────────────────

# Each key is a human-readable QA category; values are lowercase keyword fragments
# that, when found in a bug description, score a point toward that category.
# Priority order (highest first) matters for tie-breaking.
BUG_CATEGORIES = {
    "Crash / Stability": [
        "crash", "exception", "fatal", "abort", "force close", "forcibly closed",
        "terminate", "stop working", "out of memory", "oom", "memory leak",
        "deadlock", "unresponsive", "hang", "freeze", "hung", "blue screen",
        "application error", "access violation",
    ],
    "Feature not working as intended": [
        "not work", "doesn't work", "does not work", "no longer work",
        "incorrect behavior", "wrong result", "not function", "broken",
        "fail", "failure", "unexpected", "not as expected", "supposed to",
        "incorrect", "mismatch", "should be", "should not", "not correct",
        "behaves", "regression", "stopped working",
    ],
    "UI / Display problem": [
        "display", "layout", "alignment", "visual", "render", "style",
        "color", "colour", "font", "icon", "button", "overlap", "cut off",
        "cutoff", "truncat", "clipping", "overflow", "ui ", " ui,", "interface",
        "appearance", "look and feel", "dark mode", "light mode", "theme",
        "blank screen", "blank page", "not show", "missing icon", "wrong icon",
        "z-order", "z order", "overlapping", "misaligned", "pixel",
    ],
    "UX / Usability problem": [
        "usability", "confusing", "confus", "navigation", "workflow",
        "hard to find", "not intuitive", "difficult to use", "frustrat",
        "response time", "slow", "lag ", "no feedback", "unclear", "unintuitive",
        "too many clicks", "hard to", "not obvious", "awkward",
    ],
    "Translation / Localization": [
        "translation", "localization", "localisation", "missing string",
        "wrong string", "language", "missing text", "garbled", "encoding",
        "locale", "region", "l10n", "i18n", "chinese", "japanese", "korean",
        "german", "french", "spanish", "traditional chinese", "simplified chinese",
        "zh-tw", "zh-cn", " de ", " fr ", " ja ", " ko ",
    ],
    "Data / File / Sync issue": [
        "data", "corrupt", "import", "export", "sync", "cannot save",
        "cannot load", "cannot open", "wrong data", "missing data",
        "incorrect data", "file", "read error", "write error", "database",
        "cloud", "upload", "download", "transfer", "backup",
    ],
}

_CATEGORY_ORDER = list(BUG_CATEGORIES.keys())  # priority: first wins on tie


def classify_bug_category(text: str) -> str:
    """Assign a bug description to the highest-scoring QA category.

    Scoring: count keyword hits per category; if tied, first in _CATEGORY_ORDER wins.
    Falls back to 'Feature not working as intended' when no keywords match.
    """
    if not isinstance(text, str) or len(text.strip()) < 5:
        return "Feature not working as intended"
    t = text.lower()
    scores: dict = {}
    for cat, keywords in BUG_CATEGORIES.items():
        scores[cat] = sum(1 for kw in keywords if kw in t)

    best_score = max(scores.values())
    if best_score == 0:
        return "Feature not working as intended"

    # Among tied categories keep the one that appears first in _CATEGORY_ORDER
    for cat in _CATEGORY_ORDER:
        if scores[cat] == best_score:
            return cat
    return "Feature not working as intended"


# ─────────────────────────────────────────────────────────────────────────────
# Risk feature loader  (unchanged from v2.6)
# ─────────────────────────────────────────────────────────────────────────────

def load_risk_features(scored_csv: str) -> "pd.DataFrame | None":
    try:
        rdf = pd.read_csv(scored_csv)
    except Exception as e:
        print(f"  WARNING: could not load scored CSV ({e}) — running without risk features.")
        return None

    mod_col = ("parsed_module" if "parsed_module" in rdf.columns
               else "module" if "module" in rdf.columns else None)
    if mod_col is None:
        print("  WARNING: scored CSV has no module/parsed_module column — skipping.")
        return None

    risk_cols = [c for c in [
        "impact_score", "detectability_score",
        "probability_score_auto", "risk_score_final",
    ] if c in rdf.columns]

    if not risk_cols:
        print("  WARNING: scored CSV has none of the expected risk columns — skipping.")
        return None

    out = rdf[[mod_col] + risk_cols].copy()
    out = out.rename(columns={mod_col: "module"})
    out = out.drop_duplicates(subset=["module"]).set_index("module")
    print(f"  Loaded risk features for {len(out)} modules from {scored_csv}")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# TF-IDF text features  (updated v6.0 — rolling version window)
# ─────────────────────────────────────────────────────────────────────────────

def build_tfidf_features(orig_df: pd.DataFrame,
                          mod_col: str = "parsed_module",
                          text_col: str = "parsed_description",
                          version_col: str = "parsed_version",
                          top_n: int = TFIDF_TOP_N,
                          version_window: int = 3) -> "pd.DataFrame | None":
    """Build TF-IDF text features using only the last `version_window` versions.

    Each version spans 1-2 months, so 3 versions ≈ 3-6 months of recent bug
    descriptions.  This makes the text signal temporal: it captures what a
    module's bugs *currently* look like rather than aggregating all-time history.
    Falls back to all-time aggregation if version data is absent.
    """
    if text_col not in orig_df.columns or mod_col not in orig_df.columns:
        print(f"  NOTE: '{text_col}' column not found — skipping text features.")
        return None

    working = orig_df.copy()

    # Filter to recent versions if version column exists.
    # Sort by semantic version number (numeric tuple), filtering out
    # versions with <10 bugs to exclude typos/outliers (e.g. "152.0").
    if version_col in working.columns:
        ver_bug_counts = working[version_col].value_counts()
        valid_versions = ver_bug_counts[ver_bug_counts >= 10].index.tolist()

        def _version_key(v):
            try:
                return tuple(int(p) for p in str(v).split("."))
            except (ValueError, TypeError):
                return (0,)

        sorted_versions = sorted(valid_versions, key=_version_key)
        recent_versions = sorted_versions[-version_window:]
        before = len(working)
        working = working[working[version_col].isin(recent_versions)]
        print(f"  TF-IDF: windowed to last {version_window} versions "
              f"({', '.join(str(v) for v in recent_versions)}) — "
              f"{len(working)}/{before} bugs retained.")
    else:
        print(f"  TF-IDF: '{version_col}' not found — using all-time descriptions.")

    mod_docs = (
        working[[mod_col, text_col]]
        .dropna(subset=[mod_col, text_col])
        .groupby(mod_col)[text_col]
        .apply(lambda texts: " ".join(str(t) for t in texts if len(str(t)) > 5))
        .reset_index()
    )
    mod_docs = mod_docs[mod_docs[text_col].str.len() > 20]

    if len(mod_docs) < 5:
        print("  WARNING: too few modules with descriptions for TF-IDF — skipping.")
        return None

    for kwargs in [
        dict(max_features=top_n, stop_words="english", ngram_range=(1, 2),
             min_df=2, max_df=0.85, sublinear_tf=True),
        dict(max_features=top_n, stop_words="english", ngram_range=(1, 1),
             min_df=1, max_df=0.95, sublinear_tf=True),
    ]:
        try:
            vec = TfidfVectorizer(**kwargs)
            X = vec.fit_transform(mod_docs[text_col])
            terms = [f"{TFIDF_PREFIX}{t}" for t in vec.get_feature_names_out()]
            tfidf_df = pd.DataFrame(X.toarray(), columns=terms)
            tfidf_df.insert(0, "module", mod_docs[mod_col].values)
            print(f"  TF-IDF: {len(terms)} text features across {len(mod_docs)} modules.")
            return tfidf_df
        except ValueError:
            continue

    print("  WARNING: TF-IDF vectorization failed — skipping text features.")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# NEW v3.0 — Cluster feature builder
# ─────────────────────────────────────────────────────────────────────────────

def load_cluster_features(cluster_csv: str,
                           orig_df: pd.DataFrame,
                           build_col: str = "Build#",
                           mod_col: str = "parsed_module") -> "pd.DataFrame | None":
    """
    Read the clustered bug CSV produced by cluster_bugs.py and build per-module
    per-build cluster-derived features:

      cluster_entropy_2   Shannon entropy of bug-theme distribution, last 2 builds
      cluster_entropy_3   Shannon entropy of bug-theme distribution, last 3 builds
      top_cluster_velocity  Growth ratio of the dominant theme over last 3 builds

    Returns a DataFrame keyed on (module, build) for merging into fdf,
    or None if the cluster CSV cannot be used.
    """
    try:
        cdf = pd.read_csv(cluster_csv)
    except Exception as e:
        print(f"  WARNING: could not load cluster CSV ({e}) — skipping cluster features.")
        return None

    if "cluster_id" not in cdf.columns:
        print("  NOTE: cluster CSV has no cluster_id column — skipping cluster features.")
        return None

    # Join cluster assignments back onto the raw bug data
    if "BugCode" in cdf.columns and "BugCode" in orig_df.columns:
        bug_cols = [c for c in [build_col, mod_col, "BugCode"] if c in orig_df.columns]
        merged = orig_df[bug_cols].merge(
            cdf[["BugCode", "cluster_id"]], on="BugCode", how="left"
        )
    elif mod_col in cdf.columns and build_col in cdf.columns:
        merged = orig_df[[build_col, mod_col]].merge(
            cdf[[build_col, mod_col, "cluster_id"]], on=[build_col, mod_col], how="left"
        )
    else:
        print("  NOTE: No shared key between cluster CSV and bug data — skipping cluster features.")
        return None

    merged[build_col] = pd.to_numeric(merged[build_col], errors="coerce")
    merged = merged.dropna(subset=[build_col, mod_col])
    merged[build_col] = merged[build_col].astype(int)
    merged["cluster_id"] = merged["cluster_id"].fillna(-1).astype(int)

    rows = []
    for mod, mod_bugs in merged.groupby(mod_col):
        builds_sorted = sorted(mod_bugs[build_col].unique())
        if len(builds_sorted) < 5:
            continue

        for idx in range(5, len(builds_sorted)):
            build_num = builds_sorted[idx]
            r = {"module": mod, "build": build_num}

            for lag in [2, 3]:
                prev_builds = builds_sorted[max(0, idx - lag):idx]
                window_bugs = mod_bugs[
                    mod_bugs[build_col].isin(prev_builds) & (mod_bugs["cluster_id"] != -1)
                ]
                if len(window_bugs) == 0:
                    r[f"cluster_entropy_{lag}"] = 0.0
                else:
                    counts = window_bugs["cluster_id"].value_counts().values.astype(float)
                    probs  = counts / counts.sum()
                    r[f"cluster_entropy_{lag}"] = round(
                        float(-np.sum(probs * np.log2(probs + 1e-12))), 3
                    )

            # Velocity of the dominant cluster: recent 3 builds vs prior 3
            if idx >= 6:
                recent_b = builds_sorted[idx - 3:idx]
                prior_b  = builds_sorted[idx - 6:idx - 3]
                r_bugs = mod_bugs[mod_bugs[build_col].isin(recent_b) & (mod_bugs["cluster_id"] != -1)]
                p_bugs = mod_bugs[mod_bugs[build_col].isin(prior_b)  & (mod_bugs["cluster_id"] != -1)]
                if len(r_bugs) > 0:
                    top_cid = r_bugs["cluster_id"].value_counts().index[0]
                    rc_n    = int((r_bugs["cluster_id"] == top_cid).sum())
                    pc_n    = int((p_bugs["cluster_id"] == top_cid).sum()) if len(p_bugs) > 0 else 0
                    r["top_cluster_velocity"] = round(rc_n / max(pc_n, 1), 2)
                else:
                    r["top_cluster_velocity"] = 1.0
            else:
                r["top_cluster_velocity"] = 1.0

            rows.append(r)

    if not rows:
        print("  NOTE: No cluster features could be built (insufficient history per module).")
        return None

    cf = pd.DataFrame(rows)
    print(f"  Cluster features: {len(cf)} rows  "
          f"(cluster_entropy_2/3, top_cluster_velocity)")
    return cf


# ─────────────────────────────────────────────────────────────────────────────
# Feature matrix builder  (updated for v3.0)
# ─────────────────────────────────────────────────────────────────────────────

def build_features(df, build_col="Build#", mod_col="parsed_module",
                   risk_features=None, tfidf_features=None,
                   cluster_features=None):
    df = df.copy()
    total_before = len(df)
    df[build_col] = pd.to_numeric(df[build_col], errors="coerce")
    non_numeric = df[build_col].isna().sum()
    df = df.dropna(subset=[build_col, mod_col])
    df[build_col] = df[build_col].astype(int)
    if non_numeric > 0:
        print(f"  WARNING: {non_numeric}/{total_before} rows dropped — "
              f"'{build_col}' could not be parsed as numbers.")

    # Apply status weighting if available:
    #   1.0 = open/active  → full contribution
    #   0.5 = closed/fixed → real bug but resolved; counts at half weight
    #   0.0 = invalid (NAB, Won't Fix, etc.) → excluded entirely
    if "status_weight" in df.columns:
        df["status_weight"] = pd.to_numeric(df["status_weight"], errors="coerce").fillna(1.0)
        invalid_count = (df["status_weight"] == 0.0).sum()
        closed_count  = (df["status_weight"] == 0.5).sum()
        df = df[df["status_weight"] > 0].copy()
        print(f"  Status weighting: {invalid_count} invalid bugs excluded, "
              f"{closed_count} closed bugs weighted at 0.5")
        df["_bug_weight"] = df["status_weight"]
        df["severity_weight"] = df["severity_weight"] * df["status_weight"]
        df["_crit_w"] = (df["severity_num"] == 1).astype(float) * df["status_weight"]
        df["_major_w"] = (df["severity_num"] == 2).astype(float) * df["status_weight"]
    else:
        df["_bug_weight"] = 1.0
        df["_crit_w"] = (df["severity_num"] == 1).astype(float)
        df["_major_w"] = (df["severity_num"] == 2).astype(float)

    # Aggregate per (module, build) — added avg_sev for severity_escalation
    agg = df.groupby([mod_col, build_col]).agg(
        bug_count=("_bug_weight", "sum"),
        sev_w=("severity_weight", "sum"),
        crit=("_crit_w", "sum"),
        major=("_major_w", "sum"),
        avg_sev=("severity_num", "mean"),    # NEW v3.0
    ).reset_index()

    if "repro_rate" in df.columns:
        repro = df.groupby([mod_col, build_col])["repro_rate"].mean().reset_index()
        agg = agg.merge(repro, on=[mod_col, build_col], how="left")
    agg = agg.sort_values([mod_col, build_col])

    rows = []
    for mod in agg[mod_col].unique():
        md = agg[agg[mod_col] == mod].sort_values(build_col).reset_index(drop=True)
        for i in range(5, len(md)):
            r = {"module": mod,
                 "build": md.loc[i, build_col],
                 "target": md.loc[i, "bug_count"]}

            # Existing lag windows
            for lag in [1, 2, 3]:
                w = md.loc[max(0, i - lag):i - 1]
                r[f"bugs_{lag}"] = w["bug_count"].sum()
                r[f"sev_{lag}"]  = w["sev_w"].sum()
                r[f"crit_{lag}"] = w["crit"].sum()
            l3 = md.loc[max(0, i - 3):i - 1, "bug_count"].values
            r["trend"] = l3[-1] - l3[0] if len(l3) >= 2 else 0

            # Change 11 — severity_escalation
            # Mean severity in the last build vs the mean in the 3 builds before that.
            # Negative = severity is worsening (moving toward S1).
            sv_last  = md.loc[i - 1, "avg_sev"] if i >= 1 else 3.0
            sv_prior_slice = md.loc[max(0, i - 4):i - 2, "avg_sev"]
            sv_prior = float(sv_prior_slice.mean()) if len(sv_prior_slice) > 0 else float(sv_last)
            r["severity_escalation"] = round(float(sv_last - sv_prior), 3)

            # Change 11 — builds_since_last_crit
            crit_hist = md.loc[:i - 1, "crit"]
            crit_rows = crit_hist.index[crit_hist > 0].tolist()
            r["builds_since_last_crit"] = int(i - crit_rows[-1] - 1) if crit_rows else i

            # Change 19 — crit_ratio: proportion of S1 bugs in last 3 builds
            w3 = md.loc[max(0, i - 3):i - 1]
            total_w3 = w3["bug_count"].sum()
            r["crit_ratio"] = round(float(w3["crit"].sum() / max(total_w3, 1)), 3)

            # Change 19 — new_module: 1 if module first appeared in the
            # most recent 20% of builds (i.e. a newly-added feature)
            r["new_module"] = 1 if len(md) <= max(5, int(len(agg[build_col].unique()) * 0.2)) else 0

            # Change 19 — total_historical_bugs (module maturity / size)
            r["total_historical_bugs"] = int(md.loc[:i - 1, "bug_count"].sum())

            rows.append(r)

    fdf = pd.DataFrame(rows)

    # Change 19 — cross_module_spike: for each (module, build), compute
    # how many OTHER modules also spiked in that build.  A "spike" is a
    # build where bug_count > module's rolling 3-build mean.
    if not fdf.empty:
        # Pre-compute per-module rolling mean bug count
        fdf = fdf.sort_values(["module", "build"])
        fdf["_rolling_mean"] = (
            fdf.groupby("module")["target"]
            .transform(lambda s: s.rolling(3, min_periods=1).mean().shift(1))
        )
        fdf["_is_spike"] = (fdf["target"] > fdf["_rolling_mean"]).astype(int)
        # Count how many modules spiked per build (excluding self)
        spikes_per_build = fdf.groupby("build")["_is_spike"].sum()
        fdf["cross_module_spike"] = (
            fdf["build"].map(spikes_per_build) - fdf["_is_spike"]
        ).clip(0).astype(int)
        fdf.drop(columns=["_rolling_mean", "_is_spike"], inplace=True)
        print(f"  New features: crit_ratio, new_module, total_historical_bugs, cross_module_spike")

    # Merge TF-IDF text features
    if tfidf_features is not None and not fdf.empty:
        fdf = fdf.merge(tfidf_features, on="module", how="left")
        tfidf_cols = [c for c in fdf.columns if c.startswith(TFIDF_PREFIX)]
        fdf[tfidf_cols] = fdf[tfidf_cols].fillna(0.0)
        print(f"  Text features merged: {len(tfidf_cols)} TF-IDF columns added.")

    # Merge risk features
    if risk_features is not None and not fdf.empty:
        fdf = fdf.merge(risk_features, on="module", how="left")
        for col, default in [
            ("impact_score", 3.0), ("detectability_score", 3.0),
            ("probability_score_auto", 3.0), ("risk_score_final", 0.0),
        ]:
            if col in fdf.columns:
                fdf[col] = fdf[col].fillna(default)
        n_matched = fdf["impact_score"].notna().sum() if "impact_score" in fdf.columns else 0
        print(f"  Risk features merged: {n_matched}/{len(fdf)} rows have scores.")

    # Change 12 — merge cluster features
    if cluster_features is not None and not fdf.empty:
        fdf = fdf.merge(cluster_features, on=["module", "build"], how="left")
        for col in ["cluster_entropy_2", "cluster_entropy_3", "top_cluster_velocity"]:
            if col in fdf.columns:
                fdf[col] = fdf[col].fillna(0.0)
        added = [c for c in _CLUSTER_FEATURE_COLS if c in fdf.columns]
        print(f"  Cluster features merged: {len(added)} columns added.")

    return fdf


# ─────────────────────────────────────────────────────────────────────────────
# Leading-indicator correlation  (unchanged from v2.6)
# ─────────────────────────────────────────────────────────────────────────────

def compute_leading_indicators(fdf: pd.DataFrame) -> pd.Series:
    fcols = [c for c in fdf.columns
             if c not in ["module", "build", "target"]
             and not c.startswith(TFIDF_PREFIX)]
    corr = {}
    for col in fcols:
        x = fdf[col].fillna(0)
        y = fdf["target"]
        corr[col] = float(np.corrcoef(x, y)[0, 1]) if x.std() > 0 else 0.0
    return pd.Series(corr).sort_values(key=abs, ascending=False)


# ─────────────────────────────────────────────────────────────────────────────
# Model training  (updated for v3.0)
# ─────────────────────────────────────────────────────────────────────────────

def train_predict(fdf: pd.DataFrame, orig_df: pd.DataFrame):
    fcols = [c for c in fdf.columns if c not in ["module", "build", "target"]]
    n_text    = sum(1 for c in fcols if c.startswith(TFIDF_PREFIX))
    n_cluster = sum(1 for c in fcols if c in _CLUSTER_FEATURE_COLS)
    n_numeric = len(fcols) - n_text - n_cluster
    X = fdf[fcols].fillna(0)
    y = fdf["target"]
    print(f"Training: {len(X)} samples, {len(fcols)} features "
          f"({n_text} text TF-IDF, {n_cluster} cluster, {n_numeric} numeric)")

    model = GradientBoostingRegressor(
        n_estimators=200, max_depth=4, learning_rate=0.1, random_state=42
    )
    scores = cross_val_score(model, X, y,
                             cv=TimeSeriesSplit(n_splits=3),
                             scoring="neg_mean_absolute_error")
    print(f"CV MAE: {-scores.mean():.2f} (+/- {scores.std():.2f})")
    model.fit(X, y)

    imp = pd.Series(model.feature_importances_, index=fcols).sort_values(ascending=False)
    print(f"\nTop features:\n{imp.head(10)}")

    leading = compute_leading_indicators(fdf)
    print(f"\nLeading indicators (Pearson r):")
    for feat, r in leading.head(8).items():
        print(f"  {feat:<25s} r = {r:+.3f}  ({_FEATURE_LABELS.get(feat, feat)})")

    latest = fdf.groupby("module").tail(1).copy()
    latest["predicted"] = model.predict(latest[fcols].fillna(0)).round(1)

    # Recency-weighted dominant_bug_type (unchanged from v2.6)
    sev_label_map = {1: "crash/Critical (S1)", 2: "Major functional (S2)",
                     3: "Normal (S3)", 4: "Minor/cosmetic (S4)"}
    dom_type = {}
    if "severity_num" in orig_df.columns and "parsed_module" in orig_df.columns:
        if "Build#" in orig_df.columns:
            orig_sorted = orig_df.copy()
            orig_sorted["_build_num"] = pd.to_numeric(orig_sorted["Build#"], errors="coerce")
        else:
            orig_sorted = orig_df.copy()
            orig_sorted["_build_num"] = np.nan

        for mod, grp in orig_sorted.groupby("parsed_module"):
            grp = grp.dropna(subset=["severity_num"])
            if grp.empty:
                dom_type[mod] = "mixed"
                continue
            if grp["_build_num"].notna().sum() >= 3:
                grp = grp.sort_values("_build_num")
                n = len(grp)
                weights = np.ones(n)
                cutoff_1 = grp["_build_num"].nlargest(1).min()
                cutoff_3 = grp["_build_num"].nlargest(max(1, int(n * 0.33))).min()
                weights[grp["_build_num"].values >= cutoff_3] = 3
                weights[grp["_build_num"].values >= cutoff_1] = 5
                sev_vals = grp["severity_num"].astype(int).values
                sev_weights: dict = {}
                for sv, w in zip(sev_vals, weights):
                    sev_weights[sv] = sev_weights.get(sv, 0.0) + w
                best_sev = min(sev_weights, key=lambda s: (-sev_weights[s], s))
            else:
                mode_sev = grp["severity_num"].mode()
                best_sev = int(mode_sev.iloc[0]) if len(mode_sev) else 3
            dom_type[mod] = sev_label_map.get(int(best_sev), "mixed")

    latest["dominant_bug_type"] = latest["module"].map(dom_type).fillna("mixed")

    # Per-module leading_signal (unchanged from v2.6)
    global_top_signal = leading.index[0] if len(leading) else ""
    global_top_label  = _FEATURE_LABELS.get(global_top_signal, global_top_signal)

    # Exclude TF-IDF, risk-score, and cluster-structural features from per-module signal
    signal_candidates = [
        c for c in fcols
        if not c.startswith(TFIDF_PREFIX)
        and c not in ("impact_score", "detectability_score",
                      "probability_score_auto", "risk_score_final")
        and c not in _CLUSTER_FEATURE_COLS
    ]

    def _per_module_signal(mod: str) -> str:
        mod_rows = fdf[fdf["module"] == mod]
        if len(mod_rows) < 4 or not signal_candidates:
            return global_top_label
        y_mod = mod_rows["target"].fillna(0)
        if y_mod.std() == 0:
            return global_top_label
        best_r, best_col = 0.0, global_top_signal
        for fc in signal_candidates:
            x_mod = mod_rows[fc].fillna(0)
            if x_mod.std() > 0:
                r = abs(float(np.corrcoef(x_mod, y_mod)[0, 1]))
                if r > best_r:
                    best_r, best_col = r, fc
        return _FEATURE_LABELS.get(best_col, best_col)

    latest["leading_signal"] = latest["module"].apply(_per_module_signal)

    return model, latest, imp, leading


# ─────────────────────────────────────────────────────────────────────────────
# Change 19 — Learned risk classifier (replaces hand-tuned composite)
# ─────────────────────────────────────────────────────────────────────────────

def train_risk_classifier(fdf: pd.DataFrame, orig_df: pd.DataFrame,
                          build_col: str = "Build#",
                          mod_col: str = "parsed_module") -> "tuple | None":
    """Train a calibrated GradientBoostingClassifier to predict probability
    of at least one Critical (S1) bug in the NEXT build for each module.

    The binary target is: did this module have any severity 1 (Critical) bug
    in this build?  S2 bugs are too common (~89% of all bugs) to be a useful
    signal — S1 is rare enough (~3.6%) to produce meaningful risk separation.

    Returns (classifier, latest_proba) or None if not enough data.
    """
    df = orig_df.copy()
    df[build_col] = pd.to_numeric(df[build_col], errors="coerce")
    df = df.dropna(subset=[build_col, mod_col])
    df[build_col] = df[build_col].astype(int)

    if "severity_num" not in df.columns:
        print("  Risk classifier: severity_num not found — skipping.")
        return None

    # Per (module, build): 1 if any S1 (Critical) bug, else 0
    has_severe = (
        df[df["severity_num"] == 1]
        .groupby([mod_col, build_col])
        .size()
        .reset_index(name="_severe_count")
    )
    has_severe["_had_severe"] = 1

    # Merge onto feature matrix
    clf_df = fdf.copy()
    clf_df = clf_df.merge(
        has_severe[[mod_col, build_col, "_had_severe"]].rename(
            columns={mod_col: "module", build_col: "build"}),
        on=["module", "build"], how="left"
    )
    clf_df["_had_severe"] = clf_df["_had_severe"].fillna(0).astype(int)

    fcols = [c for c in clf_df.columns
             if c not in ["module", "build", "target", "_had_severe"]]
    X = clf_df[fcols].fillna(0)
    y = clf_df["_had_severe"]

    pos_rate = y.mean()
    print(f"\n  Risk classifier: {len(X)} samples, {y.sum():.0f} positive "
          f"({pos_rate:.1%} had S1 critical bug)")

    if y.sum() < 10 or (1 - pos_rate) < 0.05:
        print("  Risk classifier: too few positive/negative samples — skipping.")
        return None

    base_clf = GradientBoostingClassifier(
        n_estimators=150, max_depth=3, learning_rate=0.1,
        random_state=42, min_samples_leaf=5,
    )
    # Calibrate probabilities using isotonic regression on CV folds
    tscv = TimeSeriesSplit(n_splits=3)
    try:
        cal_clf = CalibratedClassifierCV(base_clf, cv=tscv, method="isotonic")
        cal_clf.fit(X, y)
    except Exception as e:
        print(f"  Risk classifier: calibration failed ({e}), using uncalibrated.")
        base_clf.fit(X, y)
        cal_clf = base_clf

    # CV score for reporting
    from sklearn.metrics import brier_score_loss
    cv_proba = np.zeros(len(X))
    for train_idx, test_idx in tscv.split(X):
        fold_clf = GradientBoostingClassifier(
            n_estimators=150, max_depth=3, learning_rate=0.1,
            random_state=42, min_samples_leaf=5,
        )
        fold_clf.fit(X.iloc[train_idx], y.iloc[train_idx])
        cv_proba[test_idx] = fold_clf.predict_proba(X.iloc[test_idx])[:, 1]
    # Only score on test folds (non-zero entries)
    mask = cv_proba > 0
    if mask.sum() > 10:
        brier = brier_score_loss(y[mask], cv_proba[mask])
        print(f"  Risk classifier CV Brier score: {brier:.4f} "
              f"(lower is better, 0 = perfect, 0.25 = random)")

    # Predict on latest rows
    latest = fdf.groupby("module").tail(1).copy()
    proba = cal_clf.predict_proba(latest[fcols].fillna(0))
    # Handle binary vs single-class output
    if proba.shape[1] == 2:
        latest["risk_proba"] = proba[:, 1]
    else:
        latest["risk_proba"] = proba[:, 0]

    print(f"  Risk classifier: predicted probabilities for {len(latest)} modules")
    print(f"    Probability range: {latest['risk_proba'].min():.3f} – "
          f"{latest['risk_proba'].max():.3f}")

    return cal_clf, latest[["module", "risk_proba"]]


# ─────────────────────────────────────────────────────────────────────────────
# Change 19 — Stratified training (separate models for high/low activity)
# ─────────────────────────────────────────────────────────────────────────────

def train_predict_stratified(fdf: pd.DataFrame, orig_df: pd.DataFrame):
    """Train separate GBR models for high-activity and low-activity modules.

    Split point: modules with total historical bugs above the median get the
    high-activity model; below median get the low-activity model.
    Returns the same shape output as train_predict().
    """
    fcols = [c for c in fdf.columns if c not in ["module", "build", "target"]]
    X_all = fdf[fcols].fillna(0)
    y_all = fdf["target"]

    # Split by module historical activity
    mod_totals = fdf.groupby("module")["target"].sum()
    median_bugs = mod_totals.median()
    high_mods = set(mod_totals[mod_totals >= median_bugs].index)
    low_mods = set(mod_totals[mod_totals < median_bugs].index)

    print(f"\n  Stratified training: {len(high_mods)} high-activity modules "
          f"(≥{median_bugs:.0f} total bugs), {len(low_mods)} low-activity")

    all_latest = []
    all_imp = pd.Series(dtype=float)

    for label, mod_set in [("high-activity", high_mods), ("low-activity", low_mods)]:
        mask = fdf["module"].isin(mod_set)
        subset = fdf[mask]
        if len(subset) < 20:
            print(f"  {label}: too few samples ({len(subset)}) — skipping stratum.")
            continue

        X = subset[fcols].fillna(0)
        y = subset["target"]

        model = GradientBoostingRegressor(
            n_estimators=200, max_depth=4, learning_rate=0.1, random_state=42
        )
        scores = cross_val_score(model, X, y,
                                 cv=TimeSeriesSplit(n_splits=3),
                                 scoring="neg_mean_absolute_error")
        print(f"  {label} CV MAE: {-scores.mean():.2f} (+/- {scores.std():.2f})")
        model.fit(X, y)

        latest = subset.groupby("module").tail(1).copy()
        latest["predicted_stratified"] = model.predict(latest[fcols].fillna(0)).round(1)
        all_latest.append(latest[["module", "predicted_stratified"]])

        imp = pd.Series(model.feature_importances_, index=fcols)
        all_imp = all_imp.add(imp * len(subset), fill_value=0)

    if not all_latest:
        return None

    stratified_preds = pd.concat(all_latest, ignore_index=True)
    # Normalize importance by total samples
    all_imp = (all_imp / len(fdf)).sort_values(ascending=False)

    return stratified_preds, all_imp


# ─────────────────────────────────────────────────────────────────────────────
# NEW v3.0 — Bug-type (per-cluster) prediction
# ─────────────────────────────────────────────────────────────────────────────

def predict_bug_type(latest_preds: pd.DataFrame,
                     orig_df: pd.DataFrame,
                     cluster_csv: str,
                     build_col: str = "Build#",
                     mod_col: str = "parsed_module",
                     history_builds: int = 5) -> "pd.DataFrame | None":
    """
    For each module, predict the expected count per bug theme (cluster) for
    the next build. Strategy: scale the recent historical cluster distribution
    by the total predicted count from train_predict().

    Parameters
    ----------
    latest_preds   : DataFrame with [module, predicted] from train_predict()
    orig_df        : raw bug-level DataFrame
    cluster_csv    : path to the clustered output CSV from cluster_bugs.py
    history_builds : number of most recent builds to use for the distribution

    Returns
    -------
    DataFrame: module, cluster_id, cluster_label, historical_pct, predicted_count
    Sorted by module, then predicted_count descending.
    Returns None if the cluster CSV cannot be read or there is insufficient data.
    """
    try:
        cdf = pd.read_csv(cluster_csv)
    except Exception as e:
        print(f"  WARNING: cannot load cluster CSV for type prediction ({e}).")
        return None

    if "cluster_id" not in cdf.columns or "predicted" not in latest_preds.columns:
        return None

    # Join cluster assignments onto the raw bug data
    if "BugCode" in cdf.columns and "BugCode" in orig_df.columns:
        bug_cols = [c for c in [build_col, mod_col, "BugCode"] if c in orig_df.columns]
        merged = orig_df[bug_cols].merge(
            cdf[["BugCode", "cluster_id", "cluster_label"]], on="BugCode", how="left"
        )
    elif mod_col in cdf.columns and build_col in cdf.columns:
        merged = orig_df[[build_col, mod_col]].merge(
            cdf[[build_col, mod_col, "cluster_id", "cluster_label"]],
            on=[build_col, mod_col], how="left"
        )
    else:
        print("  NOTE: Cannot join cluster CSV for type prediction — no shared key.")
        return None

    merged[build_col] = pd.to_numeric(merged[build_col], errors="coerce")
    merged = merged.dropna(subset=[build_col, mod_col])
    merged[build_col] = merged[build_col].astype(int)
    merged["cluster_id"] = merged["cluster_id"].fillna(-1).astype(int)

    # Build cluster label lookup
    label_map = (
        cdf[cdf["cluster_id"] != -1][["cluster_id", "cluster_label"]]
        .drop_duplicates("cluster_id")
        .set_index("cluster_id")["cluster_label"]
        .to_dict()
        if "cluster_label" in cdf.columns else {}
    )

    results = []
    for _, pred_row in latest_preds.iterrows():
        mod        = pred_row["module"]
        pred_total = float(pred_row.get("predicted", 0))

        mod_data = merged[merged[mod_col] == mod]
        if len(mod_data) < 5:
            continue

        recent_builds = sorted(mod_data[build_col].unique())[-history_builds:]
        recent = mod_data[
            mod_data[build_col].isin(recent_builds) & (mod_data["cluster_id"] != -1)
        ]

        if len(recent) == 0:
            results.append({
                "module": mod, "cluster_id": -1,
                "cluster_label": "No cluster data",
                "historical_pct": 1.0,
                "predicted_count": round(pred_total, 1),
            })
            continue

        total_recent = len(recent)
        for cid, cnt in recent["cluster_id"].value_counts().items():
            pct = cnt / total_recent
            results.append({
                "module": mod,
                "cluster_id": int(cid),
                "cluster_label": label_map.get(int(cid), f"Cluster {cid}"),
                "historical_pct": round(float(pct), 3),
                "predicted_count": round(pred_total * pct, 1),
            })

    if not results:
        return None

    out = pd.DataFrame(results)
    out = out.sort_values(["module", "predicted_count"], ascending=[True, False])
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Change 15 — Category breakdown prediction (no cluster CSV required)
# ─────────────────────────────────────────────────────────────────────────────

def predict_category_breakdown(latest_preds: pd.DataFrame,
                               orig_df: pd.DataFrame,
                               mod_col: str = "parsed_module",
                               text_col: str = "parsed_description",
                               build_col: str = "Build#",
                               history_builds: int = 5) -> "pd.DataFrame | None":
    """
    Classify each recent bug into a QA category (BUG_CATEGORIES) and scale
    the distribution by the ML-model total prediction to produce expected
    per-category counts for the next build.

    Parameters
    ----------
    latest_preds   : DataFrame with [module, predicted] from train_predict()
    orig_df        : raw bug-level DataFrame with parsed_description
    history_builds : number of most recent builds to use for the distribution

    Returns
    -------
    DataFrame: module, category, historical_count, historical_pct, expected_next_build
    Sorted by module, then expected_next_build descending.
    Returns None if parsed_description is absent or no data can be built.
    """
    if text_col not in orig_df.columns or mod_col not in orig_df.columns:
        print(f"  NOTE: '{text_col}' not found — skipping category breakdown prediction.")
        return None

    working = orig_df.copy()
    working[build_col] = pd.to_numeric(working[build_col], errors="coerce")
    working = working.dropna(subset=[build_col, mod_col])
    working = working[working[text_col].notna()]
    working[build_col] = working[build_col].astype(int)
    working["_bug_category"] = working[text_col].apply(classify_bug_category)

    results = []
    for _, pred_row in latest_preds.iterrows():
        mod        = pred_row["module"]
        pred_total = float(pred_row.get("predicted", 0))

        mod_data = working[working[mod_col] == mod]
        if len(mod_data) < 3:
            continue

        recent_builds = sorted(mod_data[build_col].unique())[-history_builds:]
        recent = mod_data[mod_data[build_col].isin(recent_builds)]
        if len(recent) == 0:
            continue

        total_recent = len(recent)
        for cat, cnt in recent["_bug_category"].value_counts().items():
            pct = cnt / total_recent
            results.append({
                "module": mod,
                "category": cat,
                "historical_count": int(cnt),
                "historical_pct": round(float(pct), 3),
                "expected_next_build": round(pred_total * pct, 1),
            })

    if not results:
        print("  NOTE: No category breakdown could be built (insufficient description data).")
        return None

    out = pd.DataFrame(results)
    out = out.sort_values(["module", "expected_next_build"], ascending=[True, False])
    print(f"  Category breakdown: {len(out)} module×category rows across "
          f"{out['module'].nunique()} modules.")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Description sampler  (unchanged from v2.6)
# ─────────────────────────────────────────────────────────────────────────────

def _sample_descriptions(orig_df, module, mod_col="parsed_module",
                          text_col="parsed_description", n=12):
    if text_col not in orig_df.columns:
        return []
    rows = orig_df[orig_df[mod_col] == module][text_col].dropna()
    seen, out = set(), []
    for s in rows.tail(n * 2).tolist():
        s = str(s).strip()
        if s and s not in seen and len(s) > 10:
            seen.add(s); out.append(s)
        if len(out) >= n:
            break
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Change 18 — Scenario generation pipeline (v5.0)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_description_patterns(
    orig_df: pd.DataFrame,
    module: str,
    mod_col: str = "parsed_module",
    text_col: str = "parsed_description",
    build_col: str = "Build#",
    history_builds: int = SCENARIO_HISTORY_BUILDS,
    top_n: int = SCENARIOS_PER_MODULE,
) -> list:
    """Heuristic (non-AI) scenario extractor.

    Clusters recent bug descriptions for a module using AgglomerativeClustering
    on TF-IDF cosine distances, then picks the description closest to each
    cluster centroid as a representative scenario.

    Returns list of dicts: {scenario_text, confidence, source_count, source_examples}
    """
    if text_col not in orig_df.columns or mod_col not in orig_df.columns:
        return []

    working = orig_df.copy()
    working[build_col] = pd.to_numeric(working[build_col], errors="coerce")
    working = working.dropna(subset=[build_col, mod_col])
    working[build_col] = working[build_col].astype(int)

    mod_data = working[working[mod_col] == module]
    if len(mod_data) < 2:
        return []

    recent_builds = sorted(mod_data[build_col].unique())[-history_builds:]
    recent = mod_data[mod_data[build_col].isin(recent_builds)]

    descriptions = recent[text_col].dropna().astype(str).tolist()
    # Deduplicate while preserving order
    seen = set()
    unique_descs = []
    for d in descriptions:
        d_stripped = d.strip()
        if d_stripped and len(d_stripped) > 10 and d_stripped not in seen:
            seen.add(d_stripped)
            unique_descs.append(d_stripped)

    if len(unique_descs) < 2:
        if unique_descs:
            return [{
                "scenario_text": unique_descs[0],
                "confidence": "low",
                "source_count": 1,
                "source_examples": unique_descs[0],
            }]
        return []

    # Build TF-IDF matrix
    try:
        vec = TfidfVectorizer(
            max_features=100, stop_words="english",
            ngram_range=(1, 2), min_df=1, max_df=0.95, sublinear_tf=True,
        )
        X = vec.fit_transform(unique_descs)
    except ValueError:
        return [{
            "scenario_text": unique_descs[0],
            "confidence": "low",
            "source_count": 1,
            "source_examples": unique_descs[0],
        }]

    # Cosine distance matrix
    dist_matrix = cosine_distances(X)

    # Cluster
    n_samples = len(unique_descs)
    if n_samples <= 2:
        # Too few for clustering — return as-is
        results = []
        for d in unique_descs[:top_n]:
            results.append({
                "scenario_text": d,
                "confidence": "low",
                "source_count": 1,
                "source_examples": d,
            })
        return results

    try:
        clustering = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=0.7,
            metric="precomputed",
            linkage="average",
        )
        labels = clustering.fit_predict(dist_matrix)
    except Exception:
        return [{
            "scenario_text": unique_descs[0],
            "confidence": "low",
            "source_count": 1,
            "source_examples": unique_descs[0],
        }]

    # Group descriptions by cluster, pick representative (closest to centroid)
    from collections import defaultdict
    clusters: dict = defaultdict(list)
    for idx, label in enumerate(labels):
        clusters[label].append(idx)

    # Sort clusters by size descending
    sorted_clusters = sorted(clusters.items(), key=lambda x: -len(x[1]))

    results = []
    for _, member_indices in sorted_clusters[:top_n]:
        member_descs = [unique_descs[i] for i in member_indices]
        cluster_size = len(member_indices)

        # Pick description closest to cluster centroid
        if cluster_size == 1:
            representative = member_descs[0]
        else:
            sub_dist = dist_matrix[np.ix_(member_indices, member_indices)]
            avg_dist = sub_dist.mean(axis=1)
            best_local_idx = int(np.argmin(avg_dist))
            representative = member_descs[best_local_idx]

        # Confidence based on cluster size
        if cluster_size >= 3:
            confidence = "high"
        elif cluster_size == 2:
            confidence = "medium"
        else:
            confidence = "low"

        # Source examples: up to 3 other descriptions from the cluster
        examples = [d for d in member_descs if d != representative][:3]
        example_str = " | ".join(examples) if examples else representative

        results.append({
            "scenario_text": representative,
            "confidence": confidence,
            "source_count": cluster_size,
            "source_examples": example_str,
        })

    return results


def _generate_scenarios_ai_ollama(
    module: str,
    risk_level: str,
    descriptions: list,
    category_distribution: "dict | None" = None,
    cluster_labels: "list | None" = None,
    leading_signal: str = "",
    model: str = "gemma4",
) -> list:
    """AI scenario generator using Ollama. Returns list of scenario dicts."""
    desc_block = "\n".join(f" - {d}" for d in descriptions[:15]) if descriptions \
        else " (no description samples available)"

    cat_block = ""
    if category_distribution:
        lines = [f"  • {cat} ({pct*100:.0f}%)" for cat, pct in list(category_distribution.items())[:6]]
        cat_block = "\nCategory distribution:\n" + "\n".join(lines)

    cluster_block = ""
    if cluster_labels:
        cluster_block = "\nBug theme clusters: " + ", ".join(cluster_labels[:5])

    prompt = (
        f"You are a QA lead predicting specific bugs for the next build.\n\n"
        f"Module: {module}\n"
        f"Risk level: {risk_level}\n"
        f"Strongest predictive signal: {leading_signal}"
        f"{cat_block}\n{cluster_block}\n\n"
        f"Recent bug descriptions for this module:\n{desc_block}\n\n"
        f"Predict 3-5 specific bug scenarios likely to occur next build.\n"
        f"Each must be a concrete, testable statement like real bug titles.\n"
        f"Do NOT write vague summaries. Do NOT mention counts.\n"
        f'Return ONLY a JSON array: [{{"scenario": "...", "confidence": "high|medium|low", "based_on": "..."}}]'
    )

    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.3, "num_predict": 800},
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = json.loads(resp.read().decode())
            text = raw.get("response", "").strip()
            # Try parsing as JSON directly
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return parsed
                # Some models wrap in a dict
                if isinstance(parsed, dict):
                    for key in ("scenarios", "predictions", "bugs", "results"):
                        if key in parsed and isinstance(parsed[key], list):
                            return parsed[key]
            except json.JSONDecodeError:
                pass
            # Fallback: extract JSON array from text
            match = re.search(r'\[.*\]', text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
    except Exception:
        pass
    return []


def _generate_scenarios_ai_claude(
    module: str,
    risk_level: str,
    descriptions: list,
    category_distribution: "dict | None" = None,
    cluster_labels: "list | None" = None,
    leading_signal: str = "",
    api_key: str = "",
) -> list:
    """AI scenario generator using Claude API. Returns list of scenario dicts."""
    desc_block = "\n".join(f" - {d}" for d in descriptions[:15]) if descriptions \
        else " (no description samples available)"

    cat_block = ""
    if category_distribution:
        lines = [f"  • {cat} ({pct*100:.0f}%)" for cat, pct in list(category_distribution.items())[:6]]
        cat_block = "\nCategory distribution:\n" + "\n".join(lines)

    cluster_block = ""
    if cluster_labels:
        cluster_block = "\nBug theme clusters: " + ", ".join(cluster_labels[:5])

    prompt = (
        f"You are a QA lead predicting specific bugs for the next build.\n\n"
        f"Module: {module}\n"
        f"Risk level: {risk_level}\n"
        f"Strongest predictive signal: {leading_signal}"
        f"{cat_block}\n{cluster_block}\n\n"
        f"Recent bug descriptions for this module:\n{desc_block}\n\n"
        f"Predict 3-5 specific bug scenarios likely to occur next build.\n"
        f"Each must be a concrete, testable statement like real bug titles.\n"
        f"Do NOT write vague summaries. Do NOT mention counts.\n"
        f'Return ONLY a JSON array: [{{"scenario": "...", "confidence": "high|medium|low", "based_on": "..."}}]'
    )

    payload = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 800,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            text = data["content"][0]["text"].strip()
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                pass
            match = re.search(r'\[.*\]', text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
    except Exception:
        pass
    return []


def generate_bug_scenarios(
    preds: pd.DataFrame,
    orig_df: pd.DataFrame,
    category_preds_df: "pd.DataFrame | None",
    cluster_preds_df: "pd.DataFrame | None",
    provider: str = "none",
    model: str = "gemma4",
    api_key: str = "",
    mod_col: str = "parsed_module",
    text_col: str = "parsed_description",
    build_col: str = "Build#",
) -> "pd.DataFrame | None":
    """Orchestrate scenario generation for the top risk-ranked modules.

    For each module: gather context, call AI or heuristic scenario generator,
    build a DataFrame of predicted bug scenarios.
    """
    # Select top modules by risk
    risk_order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    ranked = preds.copy()
    ranked["_risk_rank"] = ranked["risk_level"].astype(str).map(risk_order).fillna(3).astype(int)
    ranked = ranked.sort_values(["_risk_rank", "predicted"], ascending=[True, False])
    top_modules = ranked.head(SCENARIO_TOP_MODULES)

    # Determine predicted build number
    if build_col in orig_df.columns:
        build_nums = pd.to_numeric(orig_df[build_col], errors="coerce").dropna()
        predicted_build = int(build_nums.max()) + 1 if len(build_nums) > 0 else 0
    else:
        predicted_build = 0

    all_rows = []

    for _, mod_row in tqdm(top_modules.iterrows(), total=len(top_modules),
                           desc="Generating scenarios", unit="mod",
                           file=sys.stderr, dynamic_ncols=True):
        mod = mod_row["module"]
        risk_level = str(mod_row.get("risk_level", "Medium"))
        leading_signal = str(mod_row.get("leading_signal", ""))

        # Gather descriptions
        descriptions = _sample_descriptions(orig_df, mod, mod_col=mod_col,
                                            text_col=text_col, n=15)

        # Category distribution
        category_distribution = None
        if category_preds_df is not None and not category_preds_df.empty:
            mod_cats = category_preds_df[category_preds_df["module"] == mod].head(6)
            if not mod_cats.empty:
                category_distribution = dict(
                    zip(mod_cats["category"], mod_cats["historical_pct"]))

        # Cluster labels
        cluster_labels = None
        if cluster_preds_df is not None and not cluster_preds_df.empty:
            mod_cl = cluster_preds_df[cluster_preds_df["module"] == mod].head(5)
            if not mod_cl.empty and "cluster_label" in mod_cl.columns:
                cluster_labels = mod_cl["cluster_label"].tolist()

        # Supporting categories string
        supporting_cats = ""
        if category_distribution:
            supporting_cats = ", ".join(list(category_distribution.keys())[:3])

        # Try AI scenario generation
        scenarios = []
        if provider == "ollama":
            scenarios = _generate_scenarios_ai_ollama(
                module=mod, risk_level=risk_level,
                descriptions=descriptions,
                category_distribution=category_distribution,
                cluster_labels=cluster_labels,
                leading_signal=leading_signal,
                model=model,
            )
        elif provider == "claude" and api_key:
            scenarios = _generate_scenarios_ai_claude(
                module=mod, risk_level=risk_level,
                descriptions=descriptions,
                category_distribution=category_distribution,
                cluster_labels=cluster_labels,
                leading_signal=leading_signal,
                api_key=api_key,
            )

        # Fallback to heuristic if AI returned nothing or provider is "none"
        if not scenarios:
            heuristic = _extract_description_patterns(
                orig_df, mod, mod_col=mod_col, text_col=text_col,
                build_col=build_col,
            )
            for i, h in enumerate(heuristic):
                all_rows.append({
                    "module": mod,
                    "risk_level": risk_level,
                    "predicted_build": predicted_build,
                    "scenario_rank": i + 1,
                    "scenario_text": h["scenario_text"],
                    "confidence": h["confidence"],
                    "source_bug_examples": h["source_examples"],
                    "supporting_categories": supporting_cats,
                    "leading_signal": leading_signal,
                })
        else:
            for i, s in enumerate(scenarios[:SCENARIOS_PER_MODULE]):
                scenario_text = s.get("scenario", s.get("scenario_text", ""))
                confidence = s.get("confidence", "medium")
                if confidence not in ("high", "medium", "low"):
                    confidence = "medium"
                based_on = s.get("based_on", "")
                all_rows.append({
                    "module": mod,
                    "risk_level": risk_level,
                    "predicted_build": predicted_build,
                    "scenario_rank": i + 1,
                    "scenario_text": scenario_text,
                    "confidence": confidence,
                    "source_bug_examples": based_on,
                    "supporting_categories": supporting_cats,
                    "leading_signal": leading_signal,
                })

    if not all_rows:
        return None

    return pd.DataFrame(all_rows)


# ─────────────────────────────────────────────────────────────────────────────
# AI narrative — Claude  (updated for v3.0 cluster_forecast param)
# ─────────────────────────────────────────────────────────────────────────────

def generate_ai_narrative(module, predicted, risk_level, dominant_bug_type,
                           leading_signal, descriptions, api_key,
                           cluster_forecast: "dict | None" = None,
                           category_forecast: "dict | None" = None) -> str:
    desc_block = "\n".join(f" - {d}" for d in descriptions[:12]) if descriptions \
        else " (no description samples available)"

    # Build category forecast block (preferred over cluster)
    type_block = ""
    if category_forecast:
        lines = []
        for cat, pct in list(category_forecast.items())[:6]:
            lines.append(f"  • {cat} ({pct*100:.0f}% of recent bugs)")
        type_block = (
            "\n\nExpected bug category distribution for next build "
            f"(risk level: {risk_level}):\n" + "\n".join(lines)
        )
    elif cluster_forecast:
        lines = []
        for label, count in list(cluster_forecast.items())[:5]:
            lines.append(f"  • '{label}'")
        type_block = (
            "\n\nExpected bug themes for next build:\n" + "\n".join(lines)
        )

    prompt = (
        f"You are a QA lead writing a pre-build risk briefing for your team.\n\n"
        f"Module: {module}\n"
        f"Risk level: {risk_level}\n"
        f"Dominant bug type historically: {dominant_bug_type}\n"
        f"Strongest predictive signal: {leading_signal}"
        f"{type_block}\n\n"
        f"Sample of recent bug descriptions for this module:\n{desc_block}\n\n"
        f"Write a concise 3-5 sentence paragraph that:\n"
        f"1. Describes WHAT specific types of bugs are most likely to appear "
        f"(use the category distribution above — name the categories).\n"
        f"2. Identifies which features, flows, or scenarios to prioritise in testing.\n"
        f"3. Highlights any recurring patterns visible in the recent descriptions.\n\n"
        f"Do NOT state or estimate a total bug count. Focus on WHAT will break, "
        f"not how many bugs. Be specific and direct. Plain prose only. No bullet points. No headers."
    )
    payload = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 350,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            return data["content"][0]["text"].strip()
    except Exception as e:
        return f"[Claude narrative unavailable: {e}]"


# ─────────────────────────────────────────────────────────────────────────────
# AI narrative — Ollama  (updated for v3.0 cluster_forecast param)
# ─────────────────────────────────────────────────────────────────────────────

def generate_ai_narrative_ollama(module, predicted, risk_level, dominant_bug_type,
                                  leading_signal, descriptions, model="gemma4",
                                  cluster_forecast: "dict | None" = None,
                                  category_forecast: "dict | None" = None) -> str:
    desc_block = "\n".join(f" - {d}" for d in descriptions[:12]) if descriptions \
        else " (no description samples available)"

    type_block = ""
    if category_forecast:
        lines = []
        for cat, pct in list(category_forecast.items())[:6]:
            lines.append(f"  • {cat} ({pct*100:.0f}% of recent bugs)")
        type_block = (
            "\n\nExpected bug category distribution for next build "
            f"(risk level: {risk_level}):\n" + "\n".join(lines)
        )
    elif cluster_forecast:
        lines = []
        for label, count in list(cluster_forecast.items())[:5]:
            lines.append(f"  • '{label}'")
        type_block = (
            "\n\nExpected bug themes for next build:\n" + "\n".join(lines)
        )

    prompt = (
        f"You are a QA lead writing a pre-build risk briefing for your team.\n\n"
        f"Module: {module}\n"
        f"Risk level: {risk_level}\n"
        f"Dominant bug type historically: {dominant_bug_type}\n"
        f"Strongest predictive signal: {leading_signal}"
        f"{type_block}\n\n"
        f"Sample of recent bug descriptions for this module:\n{desc_block}\n\n"
        f"Write a concise 3-5 sentence paragraph that:\n"
        f"1. Describes WHAT specific types of bugs are most likely to appear "
        f"(use the category distribution above — name the categories).\n"
        f"2. Identifies which features, flows, or scenarios to prioritise in testing.\n"
        f"3. Highlights any recurring patterns visible in the recent descriptions.\n\n"
        f"Do NOT state or estimate a total bug count. Focus on WHAT will break, "
        f"not how many bugs. Be specific and direct. Plain prose only. No bullet points. No headers."
    )
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 350},
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = json.loads(resp.read().decode())
            return raw.get("response", "").strip()
    except Exception as e:
        return f"[Ollama narrative unavailable: {e}]"


# ─────────────────────────────────────────────────────────────────────────────
# Focus summary  (updated to pass cluster_forecast to narratives)
# ─────────────────────────────────────────────────────────────────────────────

def generate_focus_summary(preds, leading, orig_df,
                            mod_col="parsed_module", top_n=8,
                            api_key="", provider="none", model="gemma4",
                            cluster_preds_df: "pd.DataFrame | None" = None,
                            category_preds_df: "pd.DataFrame | None" = None,
                            scenario_df: "pd.DataFrame | None" = None) -> str:
    lines = [
        "=" * 78,
        " PDR-I DEFECT PREDICTION — FOCUS SUMMARY FOR NEXT BUILD",
        "=" * 78, "",
        " Modules ranked by risk level. Category breakdown shows WHAT bugs to expect.", "",
    ]

    dom_type_series = preds.set_index("module").get(
        "dominant_bug_type", pd.Series(dtype=str))

    top_feat       = leading.index[0] if len(leading) else "bug_count"
    top_feat_label = _FEATURE_LABELS.get(top_feat, top_feat)

    high_risk = preds[preds["risk_level"].isin(["Critical", "High"])].head(top_n)
    if high_risk.empty:
        high_risk = preds.head(min(top_n, len(preds)))

    for _, row in tqdm(high_risk.iterrows(), total=len(high_risk),
                       desc="Building focus summary", unit="mod",
                       file=sys.stderr, dynamic_ncols=True):
        mod   = row["module"]
        pred  = row["predicted"]
        rl    = str(row["risk_level"])
        dtype = str(row.get("dominant_bug_type",
                             dom_type_series.get(mod, "mixed")))

        if "leading_signal" in row and str(row["leading_signal"]) not in ("", "nan"):
            mod_feat_label = str(row["leading_signal"])
        else:
            fcols = [c for c in preds.columns
                     if c not in ["module", "build", "target", "predicted", "risk_level",
                                   "dominant_bug_type", "leading_signal", "ai_narrative"]
                     and not c.startswith(TFIDF_PREFIX)]
            mod_rows = preds[preds["module"] == mod]
            mod_feat_label = top_feat_label
            if fcols and len(mod_rows) > 1:
                best_corr, best_col = 0.0, fcols[0]
                y_mod = mod_rows["target"].fillna(0)
                for fc in fcols:
                    x_mod = mod_rows[fc].fillna(0)
                    if x_mod.std() > 0 and y_mod.std() > 0:
                        r = abs(float(np.corrcoef(x_mod, y_mod)[0, 1]))
                        if r > best_corr:
                            best_corr, best_col = r, fc
                mod_feat_label = _FEATURE_LABELS.get(best_col, best_col)

        # Build category_forecast dict for this module (preferred)
        category_forecast = None
        if category_preds_df is not None and not category_preds_df.empty:
            mod_cats = category_preds_df[category_preds_df["module"] == mod].head(6)
            if not mod_cats.empty:
                category_forecast = dict(
                    zip(mod_cats["category"], mod_cats["historical_pct"])
                )

        # Fall back to cluster_forecast if no category data
        cluster_forecast = None
        if category_forecast is None and cluster_preds_df is not None and not cluster_preds_df.empty:
            mod_clusters = cluster_preds_df[cluster_preds_df["module"] == mod].head(5)
            if not mod_clusters.empty:
                cluster_forecast = dict(
                    zip(mod_clusters["cluster_label"],
                        mod_clusters["predicted_count"])
                )

        lines.append(f"  {'─' * 74}")
        lines.append(f"  📍 {mod}  [{rl} risk]")
        lines.append(f"     Leading signal : {mod_feat_label}")

        if category_forecast:
            lines.append(f"     Expected bug categories (from recent history):")
            for cat, pct in list(category_forecast.items())[:4]:
                lines.append(f"       • {cat} ({pct*100:.0f}%)")
        elif cluster_forecast:
            lines.append(f"     Expected bug themes:")
            for lbl, cnt in list(cluster_forecast.items())[:4]:
                lines.append(f"       • {lbl}")

        # v5.0 — append top predicted scenarios
        if scenario_df is not None and not scenario_df.empty:
            mod_scenarios = scenario_df[scenario_df["module"] == mod].head(3)
            if not mod_scenarios.empty:
                lines.append(f"     Predicted bug scenarios:")
                for _, sc in mod_scenarios.iterrows():
                    conf = str(sc.get("confidence", ""))
                    text = str(sc.get("scenario_text", ""))
                    lines.append(f"       → [{conf}] {text}")

        if provider == "ollama":
            descriptions = _sample_descriptions(orig_df, mod, mod_col=mod_col)
            narrative = generate_ai_narrative_ollama(
                module=mod, predicted=pred, risk_level=rl,
                dominant_bug_type=dtype, leading_signal=mod_feat_label,
                descriptions=descriptions, model=model,
                cluster_forecast=cluster_forecast,
                category_forecast=category_forecast,
            )
            for narrative_line in narrative.splitlines():
                lines.append(f"  {narrative_line}")

        elif provider == "claude" and api_key:
            descriptions = _sample_descriptions(orig_df, mod, mod_col=mod_col)
            narrative = generate_ai_narrative(
                module=mod, predicted=pred, risk_level=rl,
                dominant_bug_type=dtype, leading_signal=mod_feat_label,
                descriptions=descriptions, api_key=api_key,
                cluster_forecast=cluster_forecast,
                category_forecast=category_forecast,
            )
            for narrative_line in narrative.splitlines():
                lines.append(f"  {narrative_line}")

        else:
            lines.append(f"  Bug type to expect : {dtype}")
            lines.append(f"  Leading signal     : {mod_feat_label}")
            lines.append(f"  Testing advice     : {_RISK_ADVICE.get(rl, 'Review.')}")

    lines += ["", "=" * 78, ""]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print(PREDICTION_GUIDE)

    if len(sys.argv) < 2:
        print("Usage: python predict_defects.py <input_csv> "
              "[--provider claude|ollama|none] [--model <n>] "
              "[--no-ai] [--scored-csv <path>] [--api-key <key>] "
              "[--cluster-csv <path>]")
        sys.exit(1)

    args = sys.argv[1:]
    scored_csv_path  = None
    cluster_csv_path = None
    provider = "claude"
    model    = "gemma4"
    api_key  = os.environ.get("ANTHROPIC_API_KEY", "")

    if "--no-ai" in args:
        provider = "none"
        args.remove("--no-ai")

    if "--provider" in args:
        idx = args.index("--provider")
        provider = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    if "--model" in args:
        idx = args.index("--model")
        model = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    if "--scored-csv" in args:
        idx = args.index("--scored-csv")
        scored_csv_path = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    if "--api-key" in args:
        idx = args.index("--api-key")
        api_key = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    # Change 12 — new --cluster-csv argument
    if "--cluster-csv" in args:
        idx = args.index("--cluster-csv")
        cluster_csv_path = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    inp = args[0]
    inp_dir  = Path(inp).parent
    out_dir  = inp_dir / "predictions"
    out_dir.mkdir(parents=True, exist_ok=True)
    out      = str(out_dir / (Path(inp).stem + "_predictions.csv"))
    out_stem = out_dir / Path(inp).stem

    if provider == "claude" and not api_key:
        print("  NOTE: --provider claude but ANTHROPIC_API_KEY not set — falling back to none.")
        provider = "none"

    if provider not in ("claude", "ollama", "none"):
        print(f"  ERROR: unknown --provider '{provider}'. Choose: claude, ollama, none")
        sys.exit(1)

    print(f"AI provider: {provider}" + (f" (model={model})" if provider == "ollama" else ""))

    orig_df = pd.read_csv(inp)
    print(f"Loaded {len(orig_df)} bugs")

    # ── Load optional risk scores ─────────────────────────────────────────────
    risk_features = None
    if scored_csv_path:
        print(f"\nLoading risk scores from: {scored_csv_path}")
        risk_features = load_risk_features(scored_csv_path)
    else:
        auto_path = inp_dir / "risk_register_scored_all.csv"
        if auto_path.exists():
            print(f"\nAuto-detected risk scores: {auto_path}")
            risk_features = load_risk_features(str(auto_path))

    # ── TF-IDF text features ──────────────────────────────────────────────────
    print("\nBuilding TF-IDF text features from parsed_description...")
    tfidf_features = build_tfidf_features(orig_df)

    # ── Cluster features (Change 12) ──────────────────────────────────────────
    cluster_features = None
    if cluster_csv_path:
        print(f"\nBuilding cluster features from: {cluster_csv_path}")
        cluster_features = load_cluster_features(cluster_csv_path, orig_df)
    else:
        # Auto-detect: look in data/clusters/ next to the input CSV
        auto_cluster = inp_dir / "clusters" / (Path(inp).stem + "_clustered.csv")
        if auto_cluster.exists():
            print(f"\nAuto-detected cluster CSV: {auto_cluster}")
            cluster_features = load_cluster_features(str(auto_cluster), orig_df)

    # ── Feature matrix ────────────────────────────────────────────────────────
    fdf = build_features(orig_df,
                         risk_features=risk_features,
                         tfidf_features=tfidf_features,
                         cluster_features=cluster_features)
    print(f"Feature matrix: {fdf.shape}")

    if len(fdf) < 20:
        print("Not enough data for prediction. Need ≥5 builds per module.")
        sys.exit(0)

    model_obj, preds, imp, leading = train_predict(fdf, orig_df)

    preds = preds.sort_values("predicted", ascending=False)

    # ── Change 19 — Stratified training (show both global and stratified) ─
    print("\n" + "─" * 78)
    print(" STRATIFIED TRAINING (separate models for high/low activity modules)")
    print("─" * 78)
    strat_result = train_predict_stratified(fdf, orig_df)
    if strat_result is not None:
        strat_preds, strat_imp = strat_result
        preds = preds.merge(strat_preds, on="module", how="left")
        # Where stratified prediction is missing, fall back to global
        preds["predicted_stratified"] = preds["predicted_stratified"].fillna(preds["predicted"])
        print(f"\n  Stratified predictions merged for {strat_preds['module'].nunique()} modules.")
        print(f"  Compare: predicted (global) vs predicted_stratified in output CSV.")
    else:
        preds["predicted_stratified"] = preds["predicted"]
        print("  Stratified training could not run — using global model only.")

    # ── Change 19 — Learned risk classifier (replaces hand-tuned composite) ─
    print("\n" + "─" * 78)
    print(" RISK CLASSIFIER (learned probability of S1 critical bug next build)")
    print("─" * 78)
    clf_result = train_risk_classifier(fdf, orig_df)

    if clf_result is not None:
        risk_clf, risk_proba = clf_result
        preds = preds.merge(risk_proba, on="module", how="left")
        preds["risk_proba"] = preds["risk_proba"].fillna(0.0)

        # Use learned probability as the primary risk score (0–100)
        preds["composite_risk"] = (preds["risk_proba"] * 100).round(2)

        # Thresholds on calibrated probability of S1 (critical) bug.
        # S1 bugs are ~3.6% of all bugs, so even a 10% predicted
        # probability represents a 3× baseline elevation.
        #   >20% chance of S1 = Critical (6× baseline)
        #   10–20% = High (3× baseline)
        #   5–10% = Medium (above baseline)
        #   <5% = Low (near or below baseline)
        preds["risk_level"] = pd.cut(
            preds["composite_risk"],
            bins=[-1, 5, 10, 20, float("inf")],
            labels=["Low", "Medium", "High", "Critical"],
        )
        risk_method = "learned classifier (P(S1 critical bug))"
        risk_thresholds = "Critical >20% | High 10–20% | Medium 5–10% | Low <5%"
    else:
        # Fallback to weighted composite when classifier cannot train
        print("  Falling back to weighted composite scoring.")
        _rs = pd.to_numeric(preds.get("risk_score_final", 0), errors="coerce").fillna(0.0)
        _pred = pd.to_numeric(preds["predicted"], errors="coerce").fillna(0.0)
        _sev = pd.to_numeric(preds.get("severity_escalation", 0), errors="coerce").fillna(0.0)
        _trend = pd.to_numeric(preds.get("trend", 0), errors="coerce").fillna(0.0)

        rs_max = max(_rs.max(), 1.0)
        norm_rs = (_rs / rs_max).clip(0, 1)
        pred_max = max(_pred.max(), 1.0)
        norm_pred = (_pred / pred_max).clip(0, 1)
        norm_sev = (-_sev).clip(0, 1)
        trend_max = max(_trend.max(), 1.0)
        norm_trend = (_trend.clip(0, None) / trend_max).clip(0, 1)

        raw_composite = (
            0.50 * norm_rs +
            0.20 * norm_pred +
            0.20 * norm_sev +
            0.10 * norm_trend
        )
        preds["composite_risk"] = (raw_composite * 100).round(2)
        preds["risk_level"] = pd.cut(
            preds["composite_risk"],
            bins=[-1, 25, 50, 75, float("inf")],
            labels=["Low", "Medium", "High", "Critical"],
        )
        risk_method = "weighted composite (fallback)"
        risk_thresholds = "Critical >75 | High 50–75 | Medium 25–50 | Low <25"

    # Log the breakdown
    rl_counts = preds["risk_level"].value_counts()
    print(f"\n  Risk scoring method: {risk_method}")
    print(f"    Thresholds — {risk_thresholds}")
    for lvl in ["Critical", "High", "Medium", "Low"]:
        print(f"    {lvl}: {rl_counts.get(lvl, 0)} module(s)")

    print("\n" + "─" * 78)
    print(" PREDICTED BUG COUNT — next build estimate per module")
    print(" target = actual bugs in most recent build | predicted = next build forecast")
    print(f" risk scoring: {risk_method}")
    print(f" {risk_thresholds}")
    print("─" * 78)
    display_cols = ["module", "target", "predicted", "predicted_stratified",
                    "composite_risk", "risk_level",
                    "dominant_bug_type", "leading_signal"]
    display_cols = [c for c in display_cols if c in preds.columns]
    preds = preds.sort_values("composite_risk", ascending=False)
    print(preds[display_cols].head(20).to_string(index=False))

    # ── Bug-type prediction (Change 13 — cluster-based) ──────────────────────
    cluster_preds_df = None
    cluster_csv_for_type = cluster_csv_path
    if cluster_csv_for_type is None:
        auto_cluster = inp_dir / "clusters" / (Path(inp).stem + "_clustered.csv")
        if auto_cluster.exists():
            cluster_csv_for_type = str(auto_cluster)

    if cluster_csv_for_type:
        print("\nBuilding per-cluster bug-type predictions …")
        cluster_preds_df = predict_bug_type(preds, orig_df, cluster_csv_for_type)
        if cluster_preds_df is not None:
            by_cluster_path = str(out_stem) + "_predictions_by_cluster.csv"
            cluster_preds_df.to_csv(by_cluster_path, index=False, encoding="utf-8-sig")
            print(f"  Bug-type predictions saved → {by_cluster_path}")
            print(f"  ({len(cluster_preds_df)} module×cluster rows)")

    # ── Change 15 — QA category breakdown prediction (always runs) ───────────
    print("\nBuilding QA category breakdown predictions …")
    category_preds_df = predict_category_breakdown(preds, orig_df)
    if category_preds_df is not None:
        by_category_path = str(out_stem) + "_predictions_by_category.csv"
        category_preds_df.to_csv(by_category_path, index=False, encoding="utf-8-sig")
        print(f"  Category predictions saved → {by_category_path}")
        print(f"  ({len(category_preds_df)} module×category rows)")

    # ── Change 18 — Bug scenario predictions (always runs) ──────────────────
    print("\nGenerating bug scenario predictions ...")
    scenario_df = generate_bug_scenarios(
        preds, orig_df, category_preds_df, cluster_preds_df,
        provider=provider, model=model, api_key=api_key,
    )
    if scenario_df is not None:
        scenario_path = str(out_stem) + "_predictions_by_scenario.csv"
        scenario_df.to_csv(scenario_path, index=False, encoding="utf-8-sig")
        print(f"  Scenario predictions saved → {scenario_path}")
        print(f"  ({len(scenario_df)} scenario rows across {scenario_df['module'].nunique()} modules)")
    else:
        print("  NOTE: No scenario predictions could be generated.")

    # ── Focus summary ─────────────────────────────────────────────────────────
    use_ai = provider in ("claude", "ollama")
    print(f"\nGenerating focus summary (provider={provider})...")
    summary_text = generate_focus_summary(
        preds, leading, orig_df,
        top_n=8, api_key=api_key,
        provider=provider, model=model,
        cluster_preds_df=cluster_preds_df,
        category_preds_df=category_preds_df,
        scenario_df=scenario_df,
    )
    print("\n" + summary_text)

    # ── Per-module AI narratives ───────────────────────────────────────────────
    preds["ai_narrative"] = ""
    if use_ai:
        high_risk_mods = (
            preds[preds["risk_level"].isin(["Critical", "High"])]
            .head(8)["module"].tolist()
        )
        for mod in tqdm(high_risk_mods, desc="Generating narratives", unit="mod",
                        file=sys.stderr, dynamic_ncols=True):
            row = preds[preds["module"] == mod].iloc[0]
            descriptions = _sample_descriptions(orig_df, mod)

            # Build category_forecast for this module (preferred)
            category_forecast = None
            if category_preds_df is not None and not category_preds_df.empty:
                mod_cats = category_preds_df[category_preds_df["module"] == mod].head(6)
                if not mod_cats.empty:
                    category_forecast = dict(
                        zip(mod_cats["category"], mod_cats["historical_pct"]))

            # Fall back to cluster_forecast
            cluster_forecast = None
            if category_forecast is None and cluster_preds_df is not None and not cluster_preds_df.empty:
                mod_c = cluster_preds_df[cluster_preds_df["module"] == mod].head(5)
                if not mod_c.empty:
                    cluster_forecast = dict(
                        zip(mod_c["cluster_label"], mod_c["predicted_count"]))

            kwargs = dict(
                module=mod,
                predicted=float(row["predicted"]),
                risk_level=str(row["risk_level"]),
                dominant_bug_type=str(row.get("dominant_bug_type", "mixed")),
                leading_signal=str(row.get("leading_signal", "")),
                descriptions=descriptions,
                cluster_forecast=cluster_forecast,
                category_forecast=category_forecast,
            )
            if provider == "ollama":
                narrative = generate_ai_narrative_ollama(**kwargs, model=model)
            else:
                narrative = generate_ai_narrative(**kwargs, api_key=api_key)
            preds.loc[preds["module"] == mod, "ai_narrative"] = narrative

    # ── Save outputs ──────────────────────────────────────────────────────────
    summary_path = str(out_stem) + "_predictions_focus_summary.txt"
    Path(summary_path).write_text(summary_text, encoding="utf-8")

    save_cols = [c for c in preds.columns if not c.startswith(TFIDF_PREFIX)]
    preds[save_cols].to_csv(out, index=False, encoding="utf-8-sig")

    imp_path     = str(out_stem) + "_predictions_importance.csv"
    leading_path = str(out_stem) + "_predictions_leading_indicators.csv"
    imp.to_csv(imp_path, encoding="utf-8-sig")

    leading_df = leading.reset_index()
    leading_df.columns = ["feature", "pearson_r"]
    leading_df["label"] = leading_df["feature"].map(_FEATURE_LABELS).fillna(
        leading_df["feature"])
    leading_df.to_csv(leading_path, index=False, encoding="utf-8-sig")

    print(f"\nOutputs:")
    print(f"  {out}              ← main predictions (includes ai_narrative)")
    if category_preds_df is not None:
        print(f"  {out_stem}_predictions_by_category.csv ← expected bug categories per module")
    if scenario_df is not None:
        print(f"  {out_stem}_predictions_by_scenario.csv ← concrete bug scenario predictions")
    if cluster_preds_df is not None:
        print(f"  {out_stem}_predictions_by_cluster.csv  ← bug-type forecast per cluster")
    print(f"  {imp_path}         ← model feature importances")
    print(f"  {leading_path}     ← leading indicator correlations")
    print(f"  {summary_path}     ← plain-English focus summary")


if __name__ == "__main__":
    main()