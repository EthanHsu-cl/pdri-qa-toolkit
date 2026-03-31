#!/usr/bin/env python3
"""PDR-I NLP Defect Clustering v2.4 - Resume support + embedding cache

Changes from v2.3:
  - Input fingerprint check: hashes row count + newest Create Date from the
    input CSV. If both match the last run's fingerprint (stored in the cluster
    cache file), the script exits immediately with "Nothing changed" and
    returns without touching any output files. This makes daily cron runs
    essentially free when the bug data hasn't been refreshed.
  - Embedding cache: Ollama embedding vectors are persisted to
    <cluster_dir>/embedding_cache.json, keyed by BugCode. On subsequent runs
    only NEW bugs (BugCode not in the cache) are sent to Ollama for embedding;
    all existing bugs reuse their cached vectors. This is the main time-saver:
    a corpus of 10,000 bugs where 200 are new takes the same time as embedding
    200 bugs from scratch rather than all 10,000.
  - Cache is written every EMBED_CHECKPOINT_EVERY embeddings (default 50) so
    a crash mid-run preserves all work done so far.
  - --force flag overrides the fingerprint check and runs a full re-cluster
    even when the input is unchanged. Use this after updating the Ollama model
    or when you want fresh cluster assignments regardless.
  - --no-cache flag skips loading AND saving the embedding cache (runs exactly
    like v2.3, useful for debugging or one-off runs on a different machine).
  - Dropped bugs warning: if BugCodes present in the embedding cache are
    absent from the current input, a count is printed (same pattern as the
    scorer's module-dropped warning). The stale entries are retained in the
    cache file so they don't need to be re-embedded if the bug reappears.

Usage:
  python cluster_bugs.py <input_csv> <output_csv>                          # TF-IDF (default)
  python cluster_bugs.py <input_csv> <output_csv> --provider ollama        # Ollama embeddings
  python cluster_bugs.py <input_csv> <output_csv> --provider ollama --model llama3.1
  python cluster_bugs.py <input_csv> <output_csv> --provider ollama --force  # ignore cache
  python cluster_bugs.py <input_csv> <output_csv> --provider ollama --no-cache
"""
import sys, json, hashlib, argparse, urllib.request, time
import pandas as pd, numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import DBSCAN, KMeans
from sklearn.metrics.pairwise import cosine_distances
from sklearn.preprocessing import normalize
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

OLLAMA_BASE            = "http://localhost:11434"
EMBED_CHECKPOINT_EVERY = 50   # save cache to disk this often during embedding


# ─────────────────────────────────────────────────────────────────────────────
# Input fingerprint helpers
# ─────────────────────────────────────────────────────────────────────────────

def _compute_fingerprint(df: pd.DataFrame) -> str:
    """Return a short hash that changes when bug data is added or removed.

    Uses row count + max(Create Date) rather than hashing all cell values —
    fast enough on 10k+ rows, stable across column reordering.
    """
    n = len(df)
    latest = ""
    if "Create Date" in df.columns:
        latest = str(pd.to_datetime(df["Create Date"], errors="coerce").max())
    raw = f"{n}|{latest}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _load_fingerprint(cache_path: Path) -> str:
    """Read the fingerprint stored in the embedding cache file, or ''."""
    if not cache_path.exists():
        return ""
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("_fingerprint", "")
    except Exception:
        return ""


def _save_cache(cache_path: Path, embeddings: dict, fingerprint: str) -> None:
    """Persist embedding cache + fingerprint atomically (write-then-rename)."""
    tmp = cache_path.with_suffix(".tmp")
    payload = {"_fingerprint": fingerprint, "embeddings": embeddings}
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    tmp.replace(cache_path)


def _load_cache(cache_path: Path) -> dict:
    """Load embedding cache dict (keyed by BugCode → list[float]).
    Returns empty dict on any error.
    """
    if not cache_path.exists():
        return {}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("embeddings", {})
    except Exception as e:
        print(f"  Warning: could not load embedding cache ({e}) — starting fresh.")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Ollama helpers
# ─────────────────────────────────────────────────────────────────────────────

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


def get_ollama_embeddings(
    texts: "list[str]",
    bug_codes: "list[str]",
    model: str = "llama3.1",
    cache: "dict | None" = None,
    cache_path: "Path | None" = None,
    fingerprint: str = "",
    batch_delay: float = 0.1,
) -> "np.ndarray | None":
    """Embed a list of texts, reusing cached vectors by BugCode.

    Args:
        texts:       One description per bug (parallel to bug_codes).
        bug_codes:   Unique identifier per bug — used as cache key.
        model:       Ollama model name.
        cache:       Mutable dict of previously computed vectors (modified in-place).
        cache_path:  Path to persist the cache; checkpointed every
                     EMBED_CHECKPOINT_EVERY new embeddings.
        fingerprint: Written back to the cache file so the next run can
                     detect unchanged input.
        batch_delay: Seconds to sleep every 20 requests (avoids hammering Ollama).

    Returns:
        (N, D) float32 array of embedding vectors, or None on failure.
    """
    if cache is None:
        cache = {}

    # Identify which bugs are new (not in cache)
    new_indices = [i for i, code in enumerate(bug_codes) if str(code) not in cache]
    cached_count = len(texts) - len(new_indices)

    if cached_count:
        print(f"  Embedding cache: {cached_count:,} reused, {len(new_indices):,} new bugs to embed.")
    else:
        print(f"  No cache hits — embedding all {len(texts):,} bugs (model={model}).")

    if new_indices:
        print(f"  Sending {len(new_indices):,} new bugs to Ollama...")

    new_done = 0
    for i in new_indices:
        code = str(bug_codes[i])
        vec = _ollama_embed(texts[i][:512], model=model)
        if vec is None:
            print(f"  Embedding failed at bug index {i} (BugCode={code}) — falling back to TF-IDF.")
            return None
        cache[code] = vec
        new_done += 1

        # Progress + checkpoint
        if batch_delay and new_done % 20 == 0:
            print(f"    {new_done}/{len(new_indices)} new embeddings done...")
            time.sleep(batch_delay)
        if cache_path and new_done % EMBED_CHECKPOINT_EVERY == 0:
            _save_cache(cache_path, cache, fingerprint)
            print(f"    Cache checkpointed at {new_done} new embeddings.")

    # Assemble full array in original order
    vecs = []
    for i, code in enumerate(bug_codes):
        v = cache.get(str(code))
        if v is None:
            print(f"  BugCode {code} still missing from cache after embedding — aborting Ollama path.")
            return None
        vecs.append(v)

    arr = np.array(vecs, dtype=np.float32)
    print(f"  Embeddings ready: shape={arr.shape}  (new={len(new_indices)}, cached={cached_count})")
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


def cluster_bugs(
    df,
    text_col="parsed_description",
    method="kmeans",
    n_clusters=25,
    eps=0.7,
    min_samples=3,
    provider="tfidf",
    model="llama3.1",
    embed_cache: "dict | None" = None,
    cache_path: "Path | None" = None,
    fingerprint: str = "",
):
    """Cluster bugs by description similarity.

    embed_cache, cache_path, fingerprint are passed through to
    get_ollama_embeddings() and are only used in Ollama mode.
    """
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
        # Build a stable per-row key: prefer BugCode, fall back to row index
        if "BugCode" in df.columns:
            bug_codes = df.loc[valid_idx, "BugCode"].fillna("").astype(str).tolist()
        else:
            bug_codes = [str(i) for i in valid_idx]

        arr = get_ollama_embeddings(
            texts,
            bug_codes=bug_codes,
            model=model,
            cache=embed_cache,
            cache_path=cache_path,
            fingerprint=fingerprint,
        )
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
    parser = argparse.ArgumentParser(description="PDR-I Defect Clustering v2.4")
    parser.add_argument("input_csv")
    parser.add_argument("output_csv")
    parser.add_argument("--provider", choices=["tfidf", "ollama"], default="tfidf",
                        help="Embedding source: tfidf (default) or ollama")
    parser.add_argument("--model", default="llama3.1",
                        help="Ollama model for embeddings and labels (default: llama3.1)")
    parser.add_argument("--force", action="store_true",
                        help="Ignore fingerprint check and re-cluster even if input is unchanged")
    parser.add_argument("--no-cache", action="store_true",
                        help="Skip loading and saving the embedding cache (always embeds from scratch)")
    args = parser.parse_args()

    inp = Path(args.input_csv)
    out = Path(args.output_csv)
    out.parent.mkdir(parents=True, exist_ok=True)

    cluster_dir = inp.parent / "clusters"
    cluster_dir.mkdir(parents=True, exist_ok=True)

    cache_path = cluster_dir / "embedding_cache.json"

    # ── Load input ────────────────────────────────────────────────────────────
    df = pd.read_csv(str(inp))
    print(f"Loaded {len(df):,} bugs | provider={args.provider} model={args.model}")

    # ── Fingerprint check ─────────────────────────────────────────────────────
    fingerprint = _compute_fingerprint(df)

    if not args.force and not args.no_cache:
        cached_fp = _load_fingerprint(cache_path)
        if cached_fp == fingerprint and out.exists():
            print(
                f"\n  Input fingerprint unchanged ({fingerprint}) and output already exists.\n"
                f"  Nothing to do. Use --force to re-cluster regardless.\n"
                f"  Output: {out}"
            )
            return

    if args.force:
        print(f"  --force: skipping fingerprint check, running full re-cluster.")

    # ── Load embedding cache (Ollama mode only) ───────────────────────────────
    embed_cache: dict = {}
    if args.provider == "ollama" and not args.no_cache:
        embed_cache = _load_cache(cache_path)
        if embed_cache:
            # Warn about BugCodes in cache that are no longer in the input
            if "BugCode" in df.columns:
                current_codes  = set(df["BugCode"].dropna().astype(str))
                cached_codes   = set(embed_cache.keys()) - {"_fingerprint"}
                stale_codes    = cached_codes - current_codes
                if stale_codes:
                    print(
                        f"  Note: {len(stale_codes):,} BugCodes in embedding cache are absent from "
                        f"current input (bugs closed/removed). They are retained in the cache in case "
                        f"they reappear, but will not affect this run's output."
                    )
            print(f"  Embedding cache loaded: {len(embed_cache):,} existing vectors.")
        else:
            print(f"  No embedding cache found — all bugs will be embedded from scratch.")

    # ── Global clustering ─────────────────────────────────────────────────────
    df = cluster_bugs(
        df,
        method="kmeans",
        n_clusters=25,
        provider=args.provider,
        model=args.model,
        embed_cache=embed_cache,
        cache_path=cache_path if not args.no_cache else None,
        fingerprint=fingerprint,
    )

    # ── Save final embedding cache + fingerprint ──────────────────────────────
    if args.provider == "ollama" and not args.no_cache:
        _save_cache(cache_path, embed_cache, fingerprint)
        print(f"  Embedding cache saved: {len(embed_cache):,} vectors → {cache_path}")

    # ── Summary + summary CSV ─────────────────────────────────────────────────
    summary = summarize(df)
    if len(summary) > 0:
        summary_path = cluster_dir / (inp.stem + "_cluster_summary.csv")
        summary.to_csv(str(summary_path), encoding="utf-8-sig")
        print(f"  Cluster summary → {summary_path}")

    # ── Per-module DBSCAN clustering (top 5 modules) ──────────────────────────
    if "parsed_module" in df.columns:
        for mod in df["parsed_module"].value_counts().head(5).index.tolist():
            mdf = df[df["parsed_module"] == mod].copy()
            if len(mdf) >= 20:
                print(f"\nClustering within module: {mod} ({len(mdf):,} bugs)")
                # Per-module clustering reuses the same embed_cache that was
                # populated during the global pass, so no new Ollama calls needed.
                mdf = cluster_bugs(
                    mdf,
                    method="dbscan",
                    eps=0.6,
                    min_samples=2,
                    provider=args.provider,
                    model=args.model,
                    embed_cache=embed_cache,
                    cache_path=None,  # don't re-checkpoint for per-module passes
                    fingerprint=fingerprint,
                )
                summarize(mdf)

    # ── Write output ──────────────────────────────────────────────────────────
    df.to_csv(str(out), index=False, encoding="utf-8-sig")
    print(f"\nSaved to: {out}")


if __name__ == "__main__":
    main()