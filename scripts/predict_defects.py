#!/usr/bin/env python3
"""PDR-I Defect Prediction v2.1
Usage: python predict_defects.py <parsed_csv> [output_csv]"""
import sys, pandas as pd, numpy as np, warnings
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.model_selection import TimeSeriesSplit, cross_val_score
from pathlib import Path
warnings.filterwarnings("ignore")

def build_features(df, build_col="Build#", mod_col="parsed_module"):
    df = df.copy()
    df[build_col] = pd.to_numeric(df[build_col], errors="coerce")
    df = df.dropna(subset=[build_col, mod_col])
    df[build_col] = df[build_col].astype(int)
    agg = df.groupby([mod_col,build_col]).agg(
        bug_count=("severity_weight","size"), sev_w=("severity_weight","sum"),
        crit=("severity_num",lambda x:(x==1).sum()), major=("severity_num",lambda x:(x==2).sum())
    ).reset_index()
    if "repro_rate" in df.columns:
        repro = df.groupby([mod_col,build_col])["repro_rate"].mean().reset_index()
        agg = agg.merge(repro, on=[mod_col,build_col], how="left")
    agg = agg.sort_values([mod_col,build_col])
    rows = []
    for mod in agg[mod_col].unique():
        md = agg[agg[mod_col]==mod].sort_values(build_col).reset_index(drop=True)
        for i in range(5, len(md)):
            r = {"module":mod,"build":md.loc[i,build_col],"target":md.loc[i,"bug_count"]}
            for lag in [1,3,5]:
                w = md.loc[max(0,i-lag):i-1]
                r[f"bugs_{lag}"] = w["bug_count"].sum()
                r[f"sev_{lag}"] = w["sev_w"].sum()
                r[f"crit_{lag}"] = w["crit"].sum()
            l3 = md.loc[max(0,i-3):i-1,"bug_count"].values
            r["trend"] = l3[-1]-l3[0] if len(l3)>=2 else 0
            rows.append(r)
    return pd.DataFrame(rows)

def train_predict(fdf):
    fcols = [c for c in fdf.columns if c not in ["module","build","target"]]
    X = fdf[fcols].fillna(0); y = fdf["target"]
    print(f"Training: {len(X)} samples, {len(fcols)} features")
    model = GradientBoostingRegressor(n_estimators=200,max_depth=4,learning_rate=0.1,random_state=42)
    scores = cross_val_score(model, X, y, cv=TimeSeriesSplit(n_splits=3), scoring="neg_mean_absolute_error")
    print(f"CV MAE: {-scores.mean():.2f} (+/- {scores.std():.2f})")
    model.fit(X, y)
    imp = pd.Series(model.feature_importances_, index=fcols).sort_values(ascending=False)
    print(f"\nTop features:\n{imp.head(10)}")
    latest = fdf.groupby("module").tail(1).copy()
    latest["predicted"] = model.predict(latest[fcols].fillna(0)).round(1)
    return model, latest, imp

def main():
    if len(sys.argv)<2: print("Usage: python predict_defects.py <csv> [output]"); sys.exit(1)
    inp = sys.argv[1]; out = sys.argv[2] if len(sys.argv)>2 else Path(inp).stem+"_predictions.csv"
    df = pd.read_csv(inp); print(f"Loaded {len(df)} bugs")
    fdf = build_features(df); print(f"Feature matrix: {fdf.shape}")
    if len(fdf)<20: print("Not enough data for prediction. Need more build history."); sys.exit(1)
    model, preds, imp = train_predict(fdf)
    preds = preds.sort_values("predicted",ascending=False)
    preds["risk_level"] = pd.cut(preds["predicted"],bins=[-1,2,5,10,float("inf")],labels=["Low","Medium","High","Critical"])
    print(f"\nPREDICTED BUG COUNT:")
    print(preds[["module","target","predicted","risk_level"]].head(20).to_string(index=False))
    preds.to_csv(out, index=False, encoding="utf-8-sig")
    imp.to_csv(Path(out).stem+"_importance.csv", encoding="utf-8-sig")
    print(f"Saved to: {out}")
if __name__=="__main__": main()
