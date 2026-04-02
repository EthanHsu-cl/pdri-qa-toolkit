#!/usr/bin/env python3
"""PDR-I Defect Prediction v5.0

Changes from v4.0
─────────────────
Change 18 — Scenario-first prediction output (primary forecast artifact)
• generate_bug_scenarios_heuristic() extracts recurring bug patterns per module
  and formats them as concrete, testable scenario statements without AI.
• generate_bug_scenarios_ollama() uses a local Ollama model (e.g. llama3.1) to
  generate specific, feature-level bug scenarios from recent descriptions.
• generate_bug_scenarios() orchestrates both paths: Ollama when provider=ollama,
  heuristic otherwise.
• Output: data/predictions/<stem>_predictions_by_scenario.csv with columns:
      module, risk_level, predicted_build, scenario_rank, scenario_text,
      source_bug_examples, supporting_categories, leading_signal, confidence
• The ML count prediction is retained as an internal ranking/prioritisation
  signal but is no longer the primary user-facing output.

All v4.0 behaviour (category breakdown, focus summary, narratives) unchanged.

Output paths:
  data/predictions/<stem>_predictions.csv
  data/predictions/<stem>_predictions_by_scenario.csv  ← NEW (always, primary)
  data/predictions/<stem>_predictions_by_category.csv
  data/predictions/<stem>_predictions_by_cluster.csv   (when --cluster-csv given)
  data/predictions/<stem>_predictions_importance.csv
  data/predictions/<stem>_predictions_leading_indicators.csv
  data/predictions/<stem>_predictions_focus_summary.txt

Usage:
  python predict_defects.py <input_csv>
  python predict_defects.py <input_csv> --cluster-csv data/clusters/ecl_parsed_clustered.csv
  python predict_defects.py <input_csv> --provider ollama --model llama3.1
  python predict_defects.py <input_csv> --provider claude --api-key <key>
  python predict_defects.py <input_csv> --no-ai
  python predict_defects.py <input_csv> --scored-csv <path>
"""
import os
import sys
import json
import urllib.request
import warnings
import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.model_selection import TimeSeriesSplit, cross_val_score
warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

PREDICTION_GUIDE = """
╔══════════════════════════════════════════════════════════════════════════════╗
║           PDR-I DEFECT PREDICTION v4.0 — HOW TO READ THIS OUTPUT           ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                             ║
║  WHAT THIS TOOL PREDICTS                                                    ║
║  ───────────────────────                                                    ║
║  For each module, the model predicts:                                       ║
║   1. A risk level (Low / Medium / High / Critical) for the NEXT build.     ║
║   2. WHAT TYPES of bugs are expected (by QA category), based on recent     ║
║      bug descriptions — not just "how many" but "what kind."               ║
║                                                                             ║
║  QA BUG CATEGORIES (auto-classified from descriptions)                     ║
║  ────────────────────────────────────────────────────                       ║
║  • Crash / Stability              • Feature not working as intended        ║
║  • UI / Display problem           • UX / Usability problem                 ║
║  • Translation / Localization     • Data / File / Sync issue               ║
║                                                                             ║
║  CATEGORY PREDICTION OUTPUT (_predictions_by_category.csv)                 ║
║  ─────────────────────────────────────────────────────────                  ║
║  module, category          Which bug category is expected                   ║
║  historical_count          How many recent bugs fell in this category       ║
║  historical_pct            Fraction of recent bugs in this category         ║
║  expected_next_build       Scaled count for next build                      ║
║                                                                             ║
║  HOW TO ACT ON IT                                                           ║
║  ────────────────                                                           ║
║  Critical/High modules → add to mandatory test suite for the next build.  ║
║  Read '_by_category.csv' to know WHAT TYPES of bugs to test for.           ║
║  Read 'ai_narrative' for a plain-English explanation of what to expect.    ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

TFIDF_TOP_N  = 40
TFIDF_PREFIX = "tfidf_"
OLLAMA_BASE  = "http://localhost:11434"

_FEATURE_LABELS = {
    "crit_1":  "critical-bug momentum (last build)",
    "crit_3":  "critical-bug momentum (last 3 builds)",
    "crit_5":  "critical-bug momentum (last 5 builds)",
    "bugs_1":  "recent bug-count momentum (last build)",
    "bugs_3":  "recent bug-count momentum (last 3 builds)",
    "bugs_5":  "recent bug-count momentum (last 5 builds)",
    "sev_1":   "severity-weighted momentum (last build)",
    "sev_3":   "severity-weighted momentum (last 3 builds)",
    "sev_5":   "severity-weighted momentum (last 5 builds)",
    "trend":   "upward bug-count trend",
    # Change 11 — new features
    "severity_escalation":     "severity escalation (negative = worsening toward S1)",
    "builds_since_last_crit":  "builds elapsed since last critical bug",
    # Change 12 — cluster features
    "cluster_entropy_3":       "bug-theme diversity index (last 3 builds)",
    "cluster_entropy_5":       "bug-theme diversity index (last 5 builds)",
    "top_cluster_velocity":    "fastest-growing bug theme velocity",
    # existing optional features
    "repro_rate":             "high reproduce rate (consistently reproducible bugs)",
    "impact_score":           "domain impact score (I×P×D scorer)",
    "detectability_score":    "test detectability score (I×P×D scorer)",
    "probability_score_auto": "historical defect probability (I×P×D scorer)",
    "risk_score_final":       "composite risk score I×P×D",
}

# Cluster feature columns — excluded from per-module leading_signal search
# because they are more structural signals than per-build time-series signals.
_CLUSTER_FEATURE_COLS = {"cluster_entropy_3", "cluster_entropy_5", "top_cluster_velocity"}

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
# TF-IDF text features  (unchanged from v2.6)
# ─────────────────────────────────────────────────────────────────────────────

def build_tfidf_features(orig_df: pd.DataFrame,
                          mod_col: str = "parsed_module",
                          text_col: str = "parsed_description",
                          top_n: int = TFIDF_TOP_N) -> "pd.DataFrame | None":
    if text_col not in orig_df.columns or mod_col not in orig_df.columns:
        print(f"  NOTE: '{text_col}' column not found — skipping text features.")
        return None

    mod_docs = (
        orig_df[[mod_col, text_col]]
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

      cluster_entropy_3   Shannon entropy of bug-theme distribution, last 3 builds
      cluster_entropy_5   Shannon entropy of bug-theme distribution, last 5 builds
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

            for lag in [3, 5]:
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
          f"(cluster_entropy_3/5, top_cluster_velocity)")
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

    # Aggregate per (module, build) — added avg_sev for severity_escalation
    agg = df.groupby([mod_col, build_col]).agg(
        bug_count=("severity_weight", "size"),
        sev_w=("severity_weight", "sum"),
        crit=("severity_num", lambda x: (x == 1).sum()),
        major=("severity_num", lambda x: (x == 2).sum()),
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
            for lag in [1, 3, 5]:
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

            rows.append(r)

    fdf = pd.DataFrame(rows)

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
        for col in ["cluster_entropy_3", "cluster_entropy_5", "top_cluster_velocity"]:
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
# Change 18 — Bug scenario generation (second-stage, scenario-first output)
# ─────────────────────────────────────────────────────────────────────────────

def _dedupe_descriptions(descs: list, max_n: int = 5, min_len: int = 20) -> list:
    """Return up to max_n descriptions with near-duplicates removed.

    Deduplication uses word-overlap ratio: two descriptions sharing > 65% of
    their words (Jaccard on split tokens) are considered duplicates; only the
    first one is kept.
    """
    seen_tokens: list = []
    out: list = []
    for d in descs:
        d = str(d).strip()
        if len(d) < min_len:
            continue
        tokens = set(d.lower().split())
        is_dup = any(
            len(tokens & s) / max(len(tokens | s), 1) > 0.65
            for s in seen_tokens
        )
        if not is_dup:
            seen_tokens.append(tokens)
            out.append(d)
        if len(out) >= max_n:
            break
    return out


def generate_bug_scenarios_heuristic(
    module: str,
    risk_level: str,
    leading_signal: str,
    orig_df: pd.DataFrame,
    category_forecast: "dict | None" = None,
    mod_col: str = "parsed_module",
    text_col: str = "parsed_description",
    build_col: str = "Build#",
    history_builds: int = 8,
    max_scenarios: int = 5,
) -> list:
    """Generate scenario predictions by surfacing recurring bug patterns.

    For each QA category (ordered by recent frequency), finds the most
    recurring description pattern within that category and formats it as a
    concrete, testable scenario statement.

    Returns a list of dicts matching predictions_by_scenario.csv columns.
    """
    if text_col not in orig_df.columns:
        return []
    working = orig_df[orig_df[mod_col] == module].copy()
    if len(working) < 3:
        return []
    working[build_col] = pd.to_numeric(working[build_col], errors="coerce")
    working = working.dropna(subset=[build_col, text_col])
    working[build_col] = working[build_col].astype(int)
    working["_cat"] = working[text_col].apply(classify_bug_category)

    recent_builds = sorted(working[build_col].unique())[-history_builds:]
    recent = working[working[build_col].isin(recent_builds)]
    if recent.empty:
        return []

    scenarios: list = []
    rank = 1
    for cat, total_in_cat in recent["_cat"].value_counts().items():
        if rank > max_scenarios:
            break
        cat_bugs_raw = recent[recent["_cat"] == cat][text_col].dropna().tolist()
        if not cat_bugs_raw:
            continue

        # Group by first-12-words key to find the most recurring description.
        freq: dict = {}
        canonical: dict = {}
        for desc in cat_bugs_raw:
            d = str(desc).strip()
            if len(d) < 15:
                continue
            key = " ".join(d.lower().split()[:12])
            if key not in canonical:
                canonical[key] = d
            freq[key] = freq.get(key, 0) + 1

        if freq:
            top_key = max(freq, key=lambda k: freq[k])
            representative = canonical[top_key]
            top_freq = freq[top_key]
        else:
            representative = cat_bugs_raw[0]
            top_freq = 1

        source_examples = _dedupe_descriptions(cat_bugs_raw, max_n=2)

        if top_freq >= 5 or total_in_cat >= 12:
            confidence = "High"
        elif top_freq >= 2 or total_in_cat >= 4:
            confidence = "Medium"
        else:
            confidence = "Low"

        scenarios.append({
            "module":                module,
            "risk_level":            risk_level,
            "predicted_build":       "next",
            "scenario_rank":         rank,
            "scenario_text":         f"[{cat}] {module}: {representative}",
            "source_bug_examples":   " | ".join(source_examples),
            "supporting_categories": cat,
            "leading_signal":        leading_signal,
            "confidence":            confidence,
        })
        rank += 1

    return scenarios


def generate_bug_scenarios_ollama(
    module: str,
    risk_level: str,
    leading_signal: str,
    descriptions: list,
    category_forecast: "dict | None",
    model: str = "llama3.1",
    max_scenarios: int = 5,
) -> list:
    """Use a local Ollama model to generate concrete, testable bug scenarios.

    The prompt explicitly forbids vague/count-based language and requires
    scenarios to be specific to a feature, user action, and visible symptom.

    Returns a list of scenario text strings (not dicts); caller wraps in dicts.
    """
    if not descriptions:
        return []

    desc_block = "\n".join(f"  {i+1}. {d}" for i, d in enumerate(descriptions[:15]))
    if category_forecast:
        cat_block = "\n".join(
            f"  • {cat} ({pct * 100:.0f}% of recent bugs)"
            for cat, pct in list(category_forecast.items())[:6]
        )
    else:
        cat_block = "  (not available)"

    prompt = (
        f"You are a QA engineer predicting the most likely bugs for the next release.\n\n"
        f"Module: {module}\n"
        f"Risk level: {risk_level}\n"
        f"Leading risk signal: {leading_signal}\n"
        f"Recent bug category distribution:\n{cat_block}\n\n"
        f"Recent bug descriptions from this module (actual past bugs):\n{desc_block}\n\n"
        f"Task: Write exactly {max_scenarios} numbered bug scenario statements that are "
        f"most likely to occur in the next build, based on recurring patterns above.\n\n"
        f"Requirements:\n"
        f"- Each statement must follow this format exactly: "
        f"[Category] {module}: <specific feature or flow> <specific failure mode or symptom>\n"
        f"- Base ONLY on patterns visible in the bug descriptions provided above.\n"
        f"- Be specific: name the feature, the user action, and the visible failure.\n"
        f"- Do NOT say 'may', 'might', 'could', or other uncertain language.\n"
        f"- Do NOT mention bug counts or numbers.\n"
        f"- Do NOT add explanations or commentary — output ONLY the numbered list.\n\n"
        f"Valid categories: Crash / Stability | Feature not working as intended | "
        f"UI / Display problem | UX / Usability problem | Translation / Localization | "
        f"Data / File / Sync issue\n\n"
        f"Example output format:\n"
        f"1. [UI / Display problem] {module}: Text label is truncated with '...' "
        f"when the string exceeds the container width\n"
        f"2. [Feature not working as intended] {module}: Export fails silently when "
        f"project contains clips longer than 30 seconds\n"
    )
    payload = json.dumps({
        "model":   model,
        "prompt":  prompt,
        "stream":  False,
        "options": {"temperature": 0.2, "num_predict": 700},
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw  = json.loads(resp.read().decode())
            text = raw.get("response", "").strip()
            scenarios = []
            for line in text.splitlines():
                line = line.strip()
                m = re.match(r"^\d+[.)]\s+(.+)$", line)
                if m:
                    scenarios.append(m.group(1).strip())
            return scenarios[:max_scenarios]
    except Exception as e:
        print(f"    Ollama scenario generation failed for {module} (model={model}): {e}")
        return []


def generate_bug_scenarios(
    preds: pd.DataFrame,
    orig_df: pd.DataFrame,
    provider: str = "none",
    model: str = "llama3.1",
    category_preds_df: "pd.DataFrame | None" = None,
    mod_col: str = "parsed_module",
    top_n: int = 10,
) -> "pd.DataFrame | None":
    """Second-stage scenario generation: produce a ranked list of likely bug
    scenarios for each high-risk module.

    Uses Ollama when provider='ollama', heuristic pattern extraction otherwise.
    The ML count prediction (preds["predicted"]) is used only to prioritise
    which modules receive scenario generation — not as the final output.

    Returns a DataFrame with columns:
        module, risk_level, predicted_build, scenario_rank, scenario_text,
        source_bug_examples, supporting_categories, leading_signal, confidence
    Or None if no scenarios could be generated.
    """
    target = preds[preds["risk_level"].isin(["Critical", "High"])].head(top_n)
    if target.empty:
        target = preds.head(min(top_n, len(preds)))

    all_rows: list = []
    for _, row in target.iterrows():
        mod            = str(row["module"])
        rl             = str(row.get("risk_level", "Medium"))
        leading_signal = str(row.get("leading_signal", "recent bug momentum"))

        category_forecast: "dict | None" = None
        if category_preds_df is not None and not category_preds_df.empty:
            mc = category_preds_df[category_preds_df["module"] == mod].head(6)
            if not mc.empty:
                category_forecast = dict(zip(mc["category"], mc["historical_pct"]))

        if provider == "ollama":
            descriptions = _sample_descriptions(orig_df, mod, mod_col=mod_col, n=15)
            ai_texts = generate_bug_scenarios_ollama(
                module=mod, risk_level=rl, leading_signal=leading_signal,
                descriptions=descriptions, category_forecast=category_forecast,
                model=model,
            )
            if ai_texts:
                working = orig_df[orig_df[mod_col] == mod].copy()
                text_col = "parsed_description" if "parsed_description" in working.columns else None
                if text_col and "Build#" in working.columns:
                    working["_b"] = pd.to_numeric(working["Build#"], errors="coerce")
                    working = working.dropna(subset=["_b"])
                    working["_b"] = working["_b"].astype(int)
                    recent_builds = sorted(working["_b"].unique())[-8:]
                    working = working[working["_b"].isin(recent_builds)]
                    working["_cat"] = working[text_col].apply(classify_bug_category)

                for rank, scenario_text in enumerate(ai_texts, start=1):
                    inferred_cat = classify_bug_category(scenario_text)
                    source_str = ""
                    if text_col and "_cat" in working.columns:
                        matching = working[working["_cat"] == inferred_cat][text_col].dropna().tolist()
                        sources  = _dedupe_descriptions(matching, max_n=2)
                        source_str = " | ".join(sources)
                    all_rows.append({
                        "module":                mod,
                        "risk_level":            rl,
                        "predicted_build":       "next",
                        "scenario_rank":         rank,
                        "scenario_text":         scenario_text,
                        "source_bug_examples":   source_str,
                        "supporting_categories": inferred_cat,
                        "leading_signal":        leading_signal,
                        "confidence":            "Medium",
                    })
                continue  # Skip heuristic for this module

        # Heuristic fallback
        heuristic_rows = generate_bug_scenarios_heuristic(
            module=mod, risk_level=rl, leading_signal=leading_signal,
            orig_df=orig_df, category_forecast=category_forecast,
            mod_col=mod_col,
        )
        all_rows.extend(heuristic_rows)

    if not all_rows:
        print("  NOTE: No bug scenarios could be generated (insufficient description data).")
        return None

    out = pd.DataFrame(all_rows)
    out = out.sort_values(["module", "scenario_rank"]).reset_index(drop=True)
    print(
        f"  Bug scenario predictions: {len(out)} scenarios across "
        f"{out['module'].nunique()} modules."
    )
    return out


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
                                  leading_signal, descriptions, model="llama3.1",
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
                            api_key="", provider="none", model="llama3.1",
                            cluster_preds_df: "pd.DataFrame | None" = None,
                            category_preds_df: "pd.DataFrame | None" = None) -> str:
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

    for _, row in high_risk.iterrows():
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
    model    = "llama3.1"
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
        sys.exit(1)

    model_obj, preds, imp, leading = train_predict(fdf, orig_df)

    preds = preds.sort_values("predicted", ascending=False)

    # Percentile-based risk levels so the distribution is always meaningful
    # regardless of whether average bug counts are 1-5 or 10-50 per build.
    #   Critical : top 5%  (~1-7 modules in a 131-module dataset)
    #   High     : next 10% (85th–95th percentile)
    #   Medium   : next 25% (60th–85th percentile)
    #   Low      : bottom 60%
    # Absolute-count floors ensure a module never reaches Critical/High on
    # noise (predicted < 1 is always Low regardless of percentile rank).
    _p95 = float(preds["predicted"].quantile(0.95))
    _p85 = float(preds["predicted"].quantile(0.85))
    _p60 = float(preds["predicted"].quantile(0.60))

    def _assign_risk(val: float) -> str:
        if val < 1.0:
            return "Low"
        if val >= _p95:
            return "Critical"
        if val >= _p85:
            return "High"
        if val >= _p60:
            return "Medium"
        return "Low"

    preds["risk_level"] = preds["predicted"].apply(_assign_risk)

    print("\n" + "─" * 78)
    print(" PREDICTED BUG COUNT — next build estimate per module")
    print(" target = actual bugs in most recent build | predicted = next build forecast")
    print(f" risk_level thresholds (percentile): "
          f"Critical ≥p95 ({_p95:.1f}), High ≥p85 ({_p85:.1f}), "
          f"Medium ≥p60 ({_p60:.1f}), Low <p60")
    print("─" * 78)
    display_cols = ["module", "target", "predicted", "risk_level",
                    "dominant_bug_type", "leading_signal"]
    display_cols = [c for c in display_cols if c in preds.columns]
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

    # ── Change 18 — Bug scenario generation (second-stage, primary output) ───
    print("\nGenerating bug scenario predictions …")
    scenario_preds_df = generate_bug_scenarios(
        preds, orig_df,
        provider=provider, model=model,
        category_preds_df=category_preds_df,
    )
    if scenario_preds_df is not None:
        by_scenario_path = str(out_stem) + "_predictions_by_scenario.csv"
        scenario_preds_df.to_csv(by_scenario_path, index=False, encoding="utf-8-sig")
        print(f"  Scenario predictions saved → {by_scenario_path}")
        print(f"  ({len(scenario_preds_df)} scenarios across "
              f"{scenario_preds_df['module'].nunique()} modules)")

    # ── Focus summary ─────────────────────────────────────────────────────────
    use_ai = provider in ("claude", "ollama")
    print(f"\nGenerating focus summary (provider={provider})...")
    summary_text = generate_focus_summary(
        preds, leading, orig_df,
        top_n=8, api_key=api_key,
        provider=provider, model=model,
        cluster_preds_df=cluster_preds_df,
        category_preds_df=category_preds_df,
    )
    print("\n" + summary_text)

    # ── Per-module AI narratives ───────────────────────────────────────────────
    preds["ai_narrative"] = ""
    if use_ai:
        high_risk_mods = (
            preds[preds["risk_level"].isin(["Critical", "High"])]
            .head(8)["module"].tolist()
        )
        for mod in high_risk_mods:
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
    if scenario_preds_df is not None:
        print(f"  {out_stem}_predictions_by_scenario.csv ← ranked bug scenarios per module  [PRIMARY]")
    if category_preds_df is not None:
        print(f"  {out_stem}_predictions_by_category.csv ← expected bug categories per module")
    if cluster_preds_df is not None:
        print(f"  {out_stem}_predictions_by_cluster.csv  ← bug-type forecast per cluster")
    print(f"  {imp_path}         ← model feature importances")
    print(f"  {leading_path}     ← leading indicator correlations")
    print(f"  {summary_path}     ← plain-English focus summary")


if __name__ == "__main__":
    main()