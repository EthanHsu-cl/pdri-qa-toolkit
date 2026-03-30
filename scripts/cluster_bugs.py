#!/usr/bin/env python3
"""PDR-I NLP Defect Clustering v2.3 - Ollama semantic embeddings + LLM cluster labels
Usage:
  python cluster_bugs.py <input_csv> <output_csv>                          # TF-IDF (default)
  python cluster_bugs.py <input_csv> <output_csv> --provider ollama        # Ollama embeddings
  python cluster_bugs.py <input_csv> <output_csv> --provider ollama --model llama3.1
"""
import sys, json, argparse, urllib.request, time
import pandas as pd, numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import DBSCAN, KMeans
from sklearn.metrics.pairwise import cosine_distances
from sklearn.preprocessing import normalize
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Ollama helpers
# ─────────────────────────────────────────────────────────────────────────────

OLLAMA_BASE = "http://localhost:11434"

def _ollama_embed(text: str, model: str = "llama3.1") -> "list[float] | None":
    """Return embedding vector for a single text string via Ollama /api/embeddings."""
    payload = json.dumps({"model": model, "prompt": text}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/embeddings",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode()).get("embedding")
    except Exception as e:
        print(f"  [embed] Ollama error: {e}", file=sys.stderr)
        return None


def get_ollama_embeddings(texts: "list[str]", model: str = "llama3.1",
                          batch_delay: float = 0.1) -> "np.ndarray | None":
    """Embed a list of texts. Returns (N, D) array or None on failure."""
    print(f"  Getting Ollama embeddings for {len(texts)} texts (model={model})...")
    vecs = []
    for i, t in enumerate(texts):
        v = _ollama_embed(t[:512], model=model)  # truncate to avoid slow long prompts
        if v is None:
            print(f"  Embedding failed at index {i} — falling back to TF-IDF.")
            return None
        vecs.append(v)
        if batch_delay and i % 20 == 0 and i > 0:
            print(f"    {i}/{len(texts)} embedded...")
            time.sleep(batch_delay)
    arr = np.array(vecs, dtype=np.float32)
    print(f"  Embeddings ready: shape={arr.shape}")
    return arr


def _ollama_label_cluster(samples: "list[str]", model: str = "llama3.1") -> str:
    """Ask Ollama to produce a short (3-6 word) label summarising sample bug descriptions."""
    joined = "\n".join(f"- {s}" for s in samples[:8])
    prompt = (
        "You are a QA analyst. Given these bug descriptions from the same cluster, "
        "respond with ONLY a concise 3-6 word label (no punctuation, no quotes) that "
        "best captures the common theme.\n\n"
        f"Bug descriptions:\n{joined}\n\nLabel:"
    )
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 20},
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = json.loads(resp.read().decode()).get("response", "").strip()
            label = raw.splitlines()[0].strip().strip('"\'')
            return label if label else "unlabelled"
    except Exception as e:
        print(f"  [label] Ollama error: {e}", file=sys.stderr)
        return "unlabelled"


# ─────────────────────────────────────────────────────────────────────────────
# Core clustering
# ─────────────────────────────────────────────────────────────────────────────

def _tfidf_matrix(texts: "list[str]"):
    """Return (TF-IDF sparse matrix, vectorizer). Falls back to unigrams on sparse data."""
    for kwargs in [
        dict(max_features=3000, stop_words="english", ngram_range=(1, 2), min_df=2, max_df=0.8),
        dict(max_features=3000, stop_words="english", ngram_range=(1, 1), min_df=1, max_df=0.95),
    ]:
        try:
            vec = TfidfVectorizer(**kwargs)
            X = vec.fit_transform(texts)
            return X, vec
        except ValueError:
            continue
    return None, None


def cluster_bugs(df, text_col="parsed_description", method="kmeans",
                 n_clusters=25, eps=0.7, min_samples=3,
                 provider="tfidf", model="llama3.1"):
    df = df.copy()
    mask = df[text_col].notna() & (df[text_col].str.len() > 5)
    valid_idx = df[mask].index.tolist()

    if len(valid_idx) < 10:
        df["cluster_id"] = -1
        df["cluster_label"] = "Unclustered"
        return df

    texts = df.loc[valid_idx, text_col].tolist()
    X_dense = None
    embed_source = "tfidf"

    # ── Ollama semantic embeddings ──────────────────────────────────────────
    if provider == "ollama":
        arr = get_ollama_embeddings(texts, model=model)
        if arr is not None:
            X_dense = normalize(arr)
            embed_source = "ollama"
        else:
            print("  Ollama embedding failed — falling back to TF-IDF.")

    # ── TF-IDF fallback ──────────────────────────────────────────────────────
    if X_dense is None:
        X_sparse, vec = _tfidf_matrix(texts)
        if X_sparse is None:
            df["cluster_id"] = -1
            df["cluster_label"] = "Unclustered"
            return df
        X_dense = X_sparse.toarray()
        vec_ref = vec
    else:
        vec_ref = None

    # ── Fit clusters ─────────────────────────────────────────────────────────
    if method == "dbscan":
        dist = cosine_distances(X_dense)
        labels = DBSCAN(eps=eps, min_samples=min_samples,
                        metric="precomputed").fit_predict(dist)
    else:
        labels = KMeans(
            n_clusters=min(n_clusters, len(valid_idx)),
            random_state=42, n_init=10
        ).fit_predict(X_dense)

    # ── Cluster labels ───────────────────────────────────────────────────────
    unique_ids = sorted(set(labels))
    clabels = {}
    for cid in unique_ids:
        if cid == -1:
            clabels[-1] = "Noise/Unclustered"
            continue
        cluster_texts = [texts[i] for i, l in enumerate(labels) if l == cid]

        if embed_source == "ollama":
            clabels[cid] = _ollama_label_cluster(cluster_texts[:8], model=model)
        else:
            mask_c = np.array(labels) == cid
            X_sp, vec_inner = _tfidf_matrix(texts)
            tfidf_mean = X_sp[mask_c].mean(axis=0)
            if hasattr(tfidf_mean, "A1"):
                tfidf_mean = tfidf_mean.A1
            else:
                tfidf_mean = np.asarray(tfidf_mean).flatten()
            fnames = vec_ref.get_feature_names_out()
            top = tfidf_mean.argsort()[-3:][::-1]
            clabels[cid] = " | ".join(fnames[i] for i in top)

    df["cluster_id"] = -1
    df["cluster_label"] = "Unclustered"
    df["embed_source"] = embed_source
    for i, idx in enumerate(valid_idx):
        df.at[idx, "cluster_id"] = int(labels[i])
        df.at[idx, "cluster_label"] = clabels.get(int(labels[i]), "Unknown")

    return df


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

def summarize(df):
    if "cluster_id" not in df.columns:
        print("No cluster_id column.")
        return pd.DataFrame()
    clustered = df[df["cluster_id"] != -1]
    if len(clustered) == 0:
        print("No clusters found.")
        return pd.DataFrame()
    agg_dict = dict(
        count=("cluster_id", "size"),
        modules=("parsed_module", lambda x: ", ".join(x.dropna().unique()[:5])),
        avg_sev=("severity_num", "mean"),
    )
    cl = clustered.groupby(["cluster_id", "cluster_label"]).agg(**agg_dict)\
                  .sort_values("count", ascending=False)
    n_c = df["cluster_id"].nunique() - (1 if -1 in df["cluster_id"].values else 0)
    n_in = (df["cluster_id"] != -1).sum()
    src = df["embed_source"].iloc[0] if "embed_source" in df.columns else "tfidf"
    print(f"Clusters: {n_c}, In clusters: {n_in}/{len(df)}, Embed source: {src}")
    print(cl.head(15).to_string())
    return cl


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="PDR-I Defect Clustering v2.3")
    parser.add_argument("input_csv")
    parser.add_argument("output_csv")
    parser.add_argument("--provider", choices=["tfidf", "ollama"], default="tfidf",
                        help="Embedding source: tfidf (default) or ollama")
    parser.add_argument("--model", default="llama3.1",
                        help="Ollama model for embeddings and labels (default: llama3.1)")
    args = parser.parse_args()

    inp = args.input_csv
    out = args.output_csv
    inp_dir = Path(inp).parent
    out_dir = Path(out).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    cluster_dir = inp_dir / "clusters"
    cluster_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(inp)
    print(f"Loaded {len(df)} bugs | provider={args.provider} model={args.model}")

    df = cluster_bugs(df, method="kmeans", n_clusters=25,
                      provider=args.provider, model=args.model)
    summary = summarize(df)
    if len(summary) > 0:
        summary.to_csv(
            str(cluster_dir / (Path(inp).stem + "_cluster_summary.csv")),
            encoding="utf-8-sig"
        )

    if "parsed_module" in df.columns:
        for mod in df["parsed_module"].value_counts().head(5).index.tolist():
            mdf = df[df["parsed_module"] == mod].copy()
            if len(mdf) >= 20:
                print(f"\nClustering within: {mod} ({len(mdf)} bugs)")
                mdf = cluster_bugs(mdf, method="dbscan", eps=0.6, min_samples=2,
                                   provider=args.provider, model=args.model)
                summarize(mdf)

    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\nSaved to: {out}")


if __name__ == "__main__":
    main()
