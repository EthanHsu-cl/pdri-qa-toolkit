#!/usr/bin/env python3
"""PDR-I Defect Prediction v2.6

Changes from v2.5
─────────────────
Change 9 — Per-module leading_signal (fixes all modules showing the same signal)
• train_predict() now computes leading_signal independently for each module by
  correlating each lag feature against that module's own build history.
  Only core lag features (bugs_N, sev_N, crit_N, trend) are candidates —
  risk-score and TF-IDF columns are excluded because they are module-constant
  and would produce misleading correlations.
  Falls back to the global top-Pearson-r signal when a module has fewer than
  4 build rows (not enough history for a per-module correlation).
• generate_focus_summary() reads leading_signal from the preds dataframe
  (already computed above) rather than re-deriving it independently.

Change 10 — Recency-weighted dominant_bug_type (fixes all modules showing "Major")
• dominant_bug_type is now weighted so recent builds count more:
  last build ×5, last third of builds ×3, earlier builds ×1.
  A module that accumulated many Normal bugs historically but is now producing
  Critical bugs will correctly show "crash/Critical (S1)" rather than its
  all-time mode.
• generate_focus_summary() reads dominant_bug_type from the preds dataframe
  rather than recomputing it with all-history mode, ensuring the focus summary
  and the CSV always agree.

All other behaviour is unchanged from v2.5.

Output paths (unchanged):
  data/predictions/ecl_parsed_predictions.csv
  data/predictions/ecl_parsed_predictions_importance.csv
  data/predictions/ecl_parsed_predictions_leading_indicators.csv
  data/predictions/ecl_parsed_predictions_focus_summary.txt

Usage:
  python predict_defects.py <input_csv>
  python predict_defects.py <input_csv> --provider ollama
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
║           PDR-I DEFECT PREDICTION — HOW TO READ THIS OUTPUT                ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  TARGET VARIABLE                                                            ║
║  ─────────────                                                              ║
║  The model predicts 'bug_count' for each module in the NEXT build.         ║
║  bug_count = number of new bugs filed against that module in one build.    ║
║  It is NOT severity-weighted; it is a raw head-count of new defects.       ║
║                                                                             ║
║  FEATURES USED                                                              ║
║  ─────────────                                                              ║
║  bugs_1 / bugs_3 / bugs_5        Bugs filed in last 1 / 3 / 5 builds      ║
║  sev_1  / sev_3  / sev_5         Severity-weighted sum over same windows   ║
║  crit_1 / crit_3 / crit_5        Critical (S1) count over same windows     ║
║  trend                            bugs in last build minus bugs 3 ago      ║
║  repro_rate       (if present)    mean reproduce probability                ║
║  tfidf_*          (if present)    top TF-IDF terms from descriptions        ║
║  impact_score     (if scored)     domain impact 1-5                        ║
║  detectability_score              test difficulty 1-5                      ║
║  probability_score_auto           historical defect probability            ║
║  risk_score_final                 I × P × D composite score                ║
║                                                                             ║
║  OUTPUT COLUMNS                                                             ║
║  ──────────────                                                             ║
║  target           Actual bug count in most recent build (ground truth)     ║
║  predicted        Model's forecast for the NEXT build                      ║
║  risk_level       Low(<3) / Medium(3-5) / High(6-10) / Critical(>10)      ║
║  dominant_bug_type  Most common severity in recent history                 ║
║  leading_signal   Feature most predictive of this module's future bugs     ║
║  ai_narrative     Plain-English AI explanation of risk and what to test    ║
║                                                                             ║
║  HOW TO ACT ON IT                                                           ║
║  ────────────────                                                           ║
║  Critical/High modules → add to mandatory test suite for the next build.  ║
║  Read 'ai_narrative' for a plain-English explanation of why.               ║
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
    "repro_rate":             "high reproduce rate (consistently reproducible bugs)",
    "impact_score":           "domain impact score (I×P×D scorer)",
    "detectability_score":    "test detectability score (I×P×D scorer)",
    "probability_score_auto": "historical defect probability (I×P×D scorer)",
    "risk_score_final":       "composite risk score I×P×D",
}

_RISK_ADVICE = {
    "Critical": "Mandatory — add to test suite for every build. Focus on crash and data-loss scenarios.",
    "High":     "Priority — run core functional tests every build and watch for regressions.",
    "Medium":   "Standard — include in sprint regression pass.",
    "Low":      "Monitor — include in release-candidate pass.",
}

# ─────────────────────────────────────────────────────────────────────────────
# Risk feature loader
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
# TF-IDF text features
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
# Feature matrix builder
# ─────────────────────────────────────────────────────────────────────────────

def build_features(df, build_col="Build#", mod_col="parsed_module",
                   risk_features=None, tfidf_features=None):
    df = df.copy()
    total_before = len(df)
    df[build_col] = pd.to_numeric(df[build_col], errors="coerce")
    non_numeric = df[build_col].isna().sum()
    df = df.dropna(subset=[build_col, mod_col])
    df[build_col] = df[build_col].astype(int)
    if non_numeric > 0:
        print(f"  WARNING: {non_numeric}/{total_before} rows dropped — "
              f"'{build_col}' could not be parsed as numbers.")

    agg = df.groupby([mod_col, build_col]).agg(
        bug_count=("severity_weight", "size"),
        sev_w=("severity_weight", "sum"),
        crit=("severity_num", lambda x: (x == 1).sum()),
        major=("severity_num", lambda x: (x == 2).sum()),
    ).reset_index()

    if "repro_rate" in df.columns:
        repro = df.groupby([mod_col, build_col])["repro_rate"].mean().reset_index()
        agg = agg.merge(repro, on=[mod_col, build_col], how="left")
    agg = agg.sort_values([mod_col, build_col])

    rows = []
    for mod in agg[mod_col].unique():
        md = agg[agg[mod_col] == mod].sort_values(build_col).reset_index(drop=True)
        for i in range(5, len(md)):
            r = {"module": mod, "build": md.loc[i, build_col], "target": md.loc[i, "bug_count"]}
            for lag in [1, 3, 5]:
                w = md.loc[max(0, i - lag):i - 1]
                r[f"bugs_{lag}"] = w["bug_count"].sum()
                r[f"sev_{lag}"]  = w["sev_w"].sum()
                r[f"crit_{lag}"] = w["crit"].sum()
            l3 = md.loc[max(0, i - 3):i - 1, "bug_count"].values
            r["trend"] = l3[-1] - l3[0] if len(l3) >= 2 else 0
            rows.append(r)

    fdf = pd.DataFrame(rows)

    if tfidf_features is not None and not fdf.empty:
        fdf = fdf.merge(tfidf_features, on="module", how="left")
        tfidf_cols = [c for c in fdf.columns if c.startswith(TFIDF_PREFIX)]
        fdf[tfidf_cols] = fdf[tfidf_cols].fillna(0.0)
        print(f"  Text features merged: {len(tfidf_cols)} TF-IDF columns added.")

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

    return fdf


# ─────────────────────────────────────────────────────────────────────────────
# Leading-indicator correlation
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
# Model training
# ─────────────────────────────────────────────────────────────────────────────

def train_predict(fdf: pd.DataFrame, orig_df: pd.DataFrame):
    fcols = [c for c in fdf.columns if c not in ["module", "build", "target"]]
    n_text    = sum(1 for c in fcols if c.startswith(TFIDF_PREFIX))
    n_numeric = len(fcols) - n_text
    X = fdf[fcols].fillna(0)
    y = fdf["target"]
    print(f"Training: {len(X)} samples, {len(fcols)} features "
          f"({n_text} text TF-IDF, {n_numeric} numeric)")

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
        print(f"  {feat:<20s} r = {r:+.3f}  ({_FEATURE_LABELS.get(feat, feat)})")

    latest = fdf.groupby("module").tail(1).copy()
    latest["predicted"] = model.predict(latest[fcols].fillna(0)).round(1)

    # ── dominant_bug_type: recency-weighted, per module ───────────────────────
    # Weight recent bugs more heavily (last 3 builds × 3, last build × 5) so a
    # module that used to have many Normal bugs but is now producing Critical
    # bugs correctly shows "crash/Critical" rather than its all-time mode.
    sev_label_map = {1: "crash/Critical (S1)", 2: "Major functional (S2)",
                     3: "Normal (S3)", 4: "Minor/cosmetic (S4)"}
    dom_type = {}
    if "severity_num" in orig_df.columns and "parsed_module" in orig_df.columns:
        if "Build#" in orig_df.columns:
            # Use numeric Build# for recency ordering when available
            orig_sorted = orig_df.copy()
            orig_sorted["_build_num"] = pd.to_numeric(
                orig_sorted["Build#"], errors="coerce"
            )
        else:
            orig_sorted = orig_df.copy()
            orig_sorted["_build_num"] = np.nan

        for mod, grp in orig_sorted.groupby("parsed_module"):
            grp = grp.dropna(subset=["severity_num"])
            if grp.empty:
                dom_type[mod] = "mixed"
                continue
            # If we have build numbers, weight recent builds more heavily
            if grp["_build_num"].notna().sum() >= 3:
                grp = grp.sort_values("_build_num")
                n = len(grp)
                # Last build: weight 5, second-last third: weight 3, rest: weight 1
                weights = np.ones(n)
                cutoff_1 = grp["_build_num"].nlargest(1).min()
                cutoff_3 = grp["_build_num"].nlargest(
                    max(1, int(n * 0.33))
                ).min()
                weights[grp["_build_num"] >= cutoff_3]  = 3
                weights[grp["_build_num"] >= cutoff_1]  = 5
                sev_vals = grp["severity_num"].astype(int).values
                # Weighted mode: sum weights per severity level
                sev_weights = {}
                for sv, w in zip(sev_vals, weights):
                    sev_weights[sv] = sev_weights.get(sv, 0.0) + w
                best_sev = min(sev_weights, key=lambda s: (-sev_weights[s], s))
            else:
                mode_sev = grp["severity_num"].mode()
                best_sev = int(mode_sev.iloc[0]) if len(mode_sev) else 3
            dom_type[mod] = sev_label_map.get(int(best_sev), "mixed")

    latest["dominant_bug_type"] = latest["module"].map(dom_type).fillna("mixed")

    # ── leading_signal: per-module, not global ────────────────────────────────
    # For each module, find the feature that has the highest absolute Pearson
    # correlation with that module's own target values across its build history.
    # Falls back to the global top signal when a module has too few rows for
    # a meaningful per-module correlation (< 4 build rows).
    global_top_signal = leading.index[0] if len(leading) else ""
    global_top_label  = _FEATURE_LABELS.get(global_top_signal, global_top_signal)

    # Only consider the core numeric lag features for per-module signal —
    # tfidf and risk score columns are module-constant and would mislead.
    signal_candidates = [
        c for c in fcols
        if not c.startswith(TFIDF_PREFIX)
        and c not in ("impact_score", "detectability_score",
                      "probability_score_auto", "risk_score_final")
    ]

    def _per_module_signal(mod: str) -> str:
        mod_rows = fdf[fdf["module"] == mod]
        if len(mod_rows) < 4 or not signal_candidates:
            return global_top_label
        y_mod = mod_rows["target"].fillna(0)
        if y_mod.std() == 0:
            # All target values identical — no signal is meaningful
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
# Description sampler
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
# Claude narrative (unchanged from v2.4)
# ─────────────────────────────────────────────────────────────────────────────

def generate_ai_narrative(module, predicted, risk_level, dominant_bug_type,
                           leading_signal, descriptions, api_key) -> str:
    desc_block = "\n".join(f" - {d}" for d in descriptions[:12]) if descriptions \
        else " (no description samples available)"
    prompt = (
        f"You are a QA lead writing a pre-build risk briefing for your team.\n\n"
        f"Module: {module}\n"
        f"Predicted bugs next build: {predicted:.0f}\n"
        f"Risk level: {risk_level}\n"
        f"Dominant bug type historically: {dominant_bug_type}\n"
        f"Strongest predictive signal: {leading_signal}\n\n"
        f"Sample of recent bug descriptions for this module:\n{desc_block}\n\n"
        f"Write a concise 3-5 sentence paragraph that:\n"
        f"1. States clearly why this module is high risk next build.\n"
        f"2. Describes the specific kinds of issues to expect based on the descriptions above.\n"
        f"3. Gives concrete, actionable testing advice (what flows/scenarios to prioritise).\n\n"
        f"Be specific and direct. Plain prose only. No bullet points. No headers."
    )
    payload = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 300,
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
# Ollama narrative (NEW — Change 8)
# ─────────────────────────────────────────────────────────────────────────────

def generate_ai_narrative_ollama(module, predicted, risk_level, dominant_bug_type,
                                  leading_signal, descriptions,
                                  model="llama3.1") -> str:
    desc_block = "\n".join(f" - {d}" for d in descriptions[:12]) if descriptions \
        else " (no description samples available)"
    prompt = (
        f"You are a QA lead writing a pre-build risk briefing for your team.\n\n"
        f"Module: {module}\n"
        f"Predicted bugs next build: {predicted:.0f}\n"
        f"Risk level: {risk_level}\n"
        f"Dominant bug type historically: {dominant_bug_type}\n"
        f"Strongest predictive signal: {leading_signal}\n\n"
        f"Sample of recent bug descriptions for this module:\n{desc_block}\n\n"
        f"Write a concise 3-5 sentence paragraph that:\n"
        f"1. States clearly why this module is high risk next build.\n"
        f"2. Describes the specific kinds of issues to expect based on the descriptions above.\n"
        f"3. Gives concrete, actionable testing advice (what flows/scenarios to prioritise).\n\n"
        f"Be specific and direct. Plain prose only. No bullet points. No headers."
    )
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 300},
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
# Focus summary — routes to ollama, claude, or template
# ─────────────────────────────────────────────────────────────────────────────

def generate_focus_summary(preds, leading, orig_df,
                            mod_col="parsed_module", top_n=8,
                            api_key="", provider="none",
                            model="llama3.1") -> str:
    lines = [
        "=" * 78,
        " PDR-I DEFECT PREDICTION — FOCUS SUMMARY FOR NEXT BUILD",
        "=" * 78, "",
        " Modules ranked by predicted bug count.", "",
    ]

    sev_label_map = {1: "crash/Critical (S1)", 2: "Major functional (S2)",
                     3: "Normal (S3)", 4: "Minor/cosmetic (S4)"}

    # dominant_bug_type is already computed per-module with recency weighting
    # in train_predict() and stored in the preds dataframe — read it from there
    # rather than recomputing from all-history mode here.
    dom_type_series = preds.set_index("module").get("dominant_bug_type", pd.Series(dtype=str))

    top_feat = leading.index[0] if len(leading) else "bug_count"
    top_feat_label = _FEATURE_LABELS.get(top_feat, top_feat)

    high_risk = preds[preds["risk_level"].isin(["Critical", "High"])].head(top_n)
    if high_risk.empty:
        high_risk = preds.head(min(top_n, len(preds)))

    for _, row in high_risk.iterrows():
        mod   = row["module"]
        pred  = row["predicted"]
        rl    = str(row["risk_level"])
        dtype = str(row.get("dominant_bug_type", dom_type_series.get(mod, "mixed")))

        # leading_signal is already computed per-module in train_predict();
        # read it from the preds row. Only fall back to per-summary correlation
        # if the column is absent (e.g. older predictions file).
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

        lines.append(f"  {'─' * 74}")
        lines.append(f"  📍 {mod} → predicted {pred:.0f} bugs [{rl}]")

        if provider == "ollama":
            descriptions = _sample_descriptions(orig_df, mod, mod_col=mod_col)
            narrative = generate_ai_narrative_ollama(
                module=mod, predicted=pred, risk_level=rl,
                dominant_bug_type=dtype, leading_signal=mod_feat_label,
                descriptions=descriptions, model=model,
            )
            for narrative_line in narrative.splitlines():
                lines.append(f"  {narrative_line}")

        elif provider == "claude" and api_key:
            descriptions = _sample_descriptions(orig_df, mod, mod_col=mod_col)
            narrative = generate_ai_narrative(
                module=mod, predicted=pred, risk_level=rl,
                dominant_bug_type=dtype, leading_signal=mod_feat_label,
                descriptions=descriptions, api_key=api_key,
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
              "[--provider claude|ollama|none] [--model <name>] "
              "[--no-ai] [--scored-csv <path>] [--api-key <key>]")
        sys.exit(1)

    args = sys.argv[1:]
    scored_csv_path = None
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

    inp = args[0]
    inp_dir = Path(inp).parent
    out_dir = inp_dir / "predictions"
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

    risk_features = None
    if scored_csv_path:
        print(f"\nLoading risk scores from: {scored_csv_path}")
        risk_features = load_risk_features(scored_csv_path)
    else:
        auto_path = inp_dir / "risk_register_scored_all.csv"
        if auto_path.exists():
            print(f"\nAuto-detected risk scores: {auto_path}")
            risk_features = load_risk_features(str(auto_path))

    print("\nBuilding TF-IDF text features from parsed_description...")
    tfidf_features = build_tfidf_features(orig_df)

    fdf = build_features(orig_df, risk_features=risk_features, tfidf_features=tfidf_features)
    print(f"Feature matrix: {fdf.shape}")

    if len(fdf) < 20:
        print("Not enough data for prediction. Need ≥5 builds per module.")
        sys.exit(1)

    model_obj, preds, imp, leading = train_predict(fdf, orig_df)

    preds = preds.sort_values("predicted", ascending=False)
    preds["risk_level"] = pd.cut(
        preds["predicted"],
        bins=[-1, 2, 5, 10, float("inf")],
        labels=["Low", "Medium", "High", "Critical"],
    )

    print("\n" + "─" * 78)
    print(" PREDICTED BUG COUNT — next build estimate per module")
    print(" target = actual bugs in most recent build | predicted = next build forecast")
    print(" risk_level thresholds: Low <3 | Medium 3-5 | High 6-10 | Critical >10")
    print("─" * 78)
    display_cols = ["module", "target", "predicted", "risk_level",
                    "dominant_bug_type", "leading_signal"]
    display_cols = [c for c in display_cols if c in preds.columns]
    print(preds[display_cols].head(20).to_string(index=False))

    use_ai = provider in ("claude", "ollama")
    print(f"\nGenerating focus summary (provider={provider})...")
    summary_text = generate_focus_summary(
        preds, leading, orig_df,
        top_n=8, api_key=api_key,
        provider=provider, model=model,
    )
    print("\n" + summary_text)

    preds["ai_narrative"] = ""
    if use_ai:
        high_risk_mods = (
            preds[preds["risk_level"].isin(["Critical", "High"])]
            .head(8)["module"].tolist()
        )
        for mod in high_risk_mods:
            row = preds[preds["module"] == mod].iloc[0]
            descriptions = _sample_descriptions(orig_df, mod)
            kwargs = dict(
                module=mod,
                predicted=float(row["predicted"]),
                risk_level=str(row["risk_level"]),
                dominant_bug_type=str(row.get("dominant_bug_type", "mixed")),
                leading_signal=str(row.get("leading_signal", "")),
                descriptions=descriptions,
            )
            if provider == "ollama":
                narrative = generate_ai_narrative_ollama(**kwargs, model=model)
            else:
                narrative = generate_ai_narrative(**kwargs, api_key=api_key)
            preds.loc[preds["module"] == mod, "ai_narrative"] = narrative

    summary_path = str(out_stem) + "_focus_summary.txt"
    Path(summary_path).write_text(summary_text, encoding="utf-8")

    save_cols = [c for c in preds.columns if not c.startswith(TFIDF_PREFIX)]
    preds[save_cols].to_csv(out, index=False, encoding="utf-8-sig")

    imp_path     = str(out_stem) + "_importance.csv"
    leading_path = str(out_stem) + "_leading_indicators.csv"
    imp.to_csv(imp_path, encoding="utf-8-sig")

    leading_df = leading.reset_index()
    leading_df.columns = ["feature", "pearson_r"]
    leading_df["label"] = leading_df["feature"].map(_FEATURE_LABELS).fillna(leading_df["feature"])
    leading_df.to_csv(leading_path, index=False, encoding="utf-8-sig")

    print(f"\nOutputs:")
    print(f"  {out}           ← main predictions (includes ai_narrative column)")
    print(f"  {imp_path}      ← model feature importances")
    print(f"  {leading_path}  ← leading indicator correlations")
    print(f"  {summary_path}  ← plain-English focus summary")


if __name__ == "__main__":
    main()