#!/usr/bin/env python3
"""PDR-I NLP Defect Clustering v2.2 - Fixed all edge cases
Usage: python cluster_bugs.py <parsed_csv> [output_csv]"""
import sys, pandas as pd, numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import DBSCAN, KMeans
from sklearn.metrics.pairwise import cosine_distances
from pathlib import Path

def cluster_bugs(df, text_col="parsed_description", method="kmeans", n_clusters=25, eps=0.7, min_samples=3):
    df = df.copy()
    mask = df[text_col].notna() & (df[text_col].str.len() > 5)
    valid_idx = df[mask].index.tolist()

    if len(valid_idx) < 10:
        df["cluster_id"] = -1
        df["cluster_label"] = "Unclustered"
        return df

    texts = df.loc[valid_idx, text_col].tolist()

    try:
        vec = TfidfVectorizer(max_features=3000, stop_words="english", ngram_range=(1,2), min_df=2, max_df=0.8)
        X = vec.fit_transform(texts)
    except ValueError:
        try:
            vec = TfidfVectorizer(max_features=3000, stop_words="english", ngram_range=(1,1), min_df=1, max_df=0.95)
            X = vec.fit_transform(texts)
        except ValueError:
            df["cluster_id"] = -1
            df["cluster_label"] = "Unclustered"
            return df

    if method == "dbscan":
        labels = DBSCAN(eps=eps, min_samples=min_samples, metric="precomputed").fit_predict(cosine_distances(X))
    else:
        labels = KMeans(n_clusters=min(n_clusters, len(valid_idx)), random_state=42, n_init=10).fit_predict(X)

    fnames = vec.get_feature_names_out()
    clabels = {}
    for cid in sorted(set(labels)):
        if cid == -1:
            clabels[-1] = "Noise/Unclustered"
            continue
        tfidf_mean = X[np.array(labels)==cid].mean(axis=0).A1
        top = tfidf_mean.argsort()[-3:][::-1]
        clabels[cid] = " | ".join(fnames[i] for i in top)

    df["cluster_id"] = -1
    df["cluster_label"] = "Unclustered"
    for i, idx in enumerate(valid_idx):
        df.at[idx, "cluster_id"] = int(labels[i])
        df.at[idx, "cluster_label"] = clabels.get(int(labels[i]), "Unknown")

    return df

def summarize(df):
    if "cluster_id" not in df.columns:
        print("No cluster_id column."); return pd.DataFrame()
    clustered = df[df["cluster_id"] != -1]
    if len(clustered) == 0:
        print("No clusters found."); return pd.DataFrame()
    cl = clustered.groupby(["cluster_id","cluster_label"]).agg(
        count=("cluster_id","size"),
        modules=("parsed_module", lambda x: ", ".join(x.dropna().unique()[:5])),
        avg_sev=("severity_num","mean")
    ).sort_values("count", ascending=False)
    n_c = df["cluster_id"].nunique() - (1 if -1 in df["cluster_id"].values else 0)
    n_in = (df["cluster_id"] != -1).sum()
    print(f"Clusters: {n_c}, In clusters: {n_in}/{len(df)}")
    print(cl.head(15).to_string())
    return cl

def main():
    if len(sys.argv) < 2:
        print("Usage: python cluster_bugs.py <csv>"); sys.exit(1)
    inp = sys.argv[1]
    inp_dir = Path(inp).parent
    out_dir = inp_dir / "clusters"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = str(out_dir / (Path(inp).stem + "_clustered.csv"))
    df = pd.read_csv(inp)
    print(f"Loaded {len(df)} bugs")

    df = cluster_bugs(df, method="kmeans", n_clusters=25)
    summary = summarize(df)
    if len(summary) > 0:
        summary.to_csv(str(out_dir / (Path(inp).stem + "_cluster_summary.csv")), encoding="utf-8-sig")

    if "parsed_module" in df.columns:
        for mod in df["parsed_module"].value_counts().head(5).index.tolist():
            mdf = df[df["parsed_module"] == mod].copy()
            if len(mdf) >= 20:
                print(f"\nClustering within: {mod} ({len(mdf)} bugs)")
                mdf = cluster_bugs(mdf, method="dbscan", eps=0.6, min_samples=2)
                summarize(mdf)

    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\nSaved to: {out}")

if __name__ == "__main__":
    main()