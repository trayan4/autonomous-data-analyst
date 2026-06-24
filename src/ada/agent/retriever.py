"""
few-shot example retrieval for the SQL prompt.

Backends (choose with ADA_RETRIEVER, or the `mode` argument):
  keyword  synonym-expanded lexical overlap (default; no deps, fully offline)
  bm25     BM25Okapi lexical ranking (better than overlap; still no API call)
  hybrid   BM25 + dense-vector similarity (Foundry embeddings + FAISS), fused with
           Reciprocal Rank Fusion (RRF) - the Azure AI Search pattern, in-process

Why hybrid: with a large, diverse example bank, pure word-overlap misses examples that
mean the same thing in different words ("revenue cratered" vs "GMV decline"). Dense
vectors catch that; BM25 keeps exact-term precision; RRF blends both without score
normalization.

Robustness: heavy deps (numpy, faiss, rank_bm25) and the embedding API are imported
lazily and wrapped, so hybrid degrades to BM25, and BM25 to keyword, rather than ever
failing. The default path (keyword) pulls in none of them. Example embeddings are cached
on disk (keyed by a hash of the examples + the embedding deployment), so only the user's
question is embedded per call.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import re

from ada.agent.context_builder import rank_examples, _synonym_index

RRF_K = 60 # RRF damping constant (Azure AI Search uses 60)
CACHE_DIR = pathlib.Path("data/index") # CWD-relative; gitignored like data/raw


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _expanded_query_tokens(question: str, spec: dict) -> list[str]:
    """Question tokens plus the metric names their synonyms map to (sales -> gmv), so the
    lexical side speaks the same vocabulary as the SQL examples."""
    syn = _synonym_index(spec)
    toks = _tokens(question)
    out = list(toks)
    for w in set(toks):
        out.extend(syn.get(w, ()))
    return out


def _bm25_ranking(question: str, examples: list[dict], spec: dict) -> list[int]:
    """Return example indices ranked best-first by BM25 over (question + SQL)."""
    from rank_bm25 import BM25Okapi
    corpus = [_tokens(ex["q"] + " " + ex["sql"]) for ex in examples]
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(_expanded_query_tokens(question, spec))
    return sorted(range(len(examples)), key=lambda i: scores[i], reverse=True)


def embed_texts(texts: list[str]):
    """Embed texts with the Foundry embedding deployment. Returns a float32 ndarray."""
    import numpy as np
    from ada.agent.config import get_settings
    from ada.agent.model_client import _ensure
    client, _ = _ensure()
    resp = client.embeddings.create(model=get_settings().deployment_embed, input=list(texts))
    return np.asarray([d.embedding for d in resp.data], dtype="float32")


def _normalize(matrix):
    import numpy as np
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.clip(norms, 1e-12, None)


def _example_embeddings(examples, embedder, deployment, cache_dir):
    """Embed the example questions once and cache to disk (keyed by examples+deployment),
    so subsequent runs/queries don't re-embed the bank."""
    import numpy as np
    texts = [ex["q"] for ex in examples]
    if cache_dir is not None:
        key = hashlib.sha256(("\u241f".join(texts) + "|" + str(deployment)).encode()).hexdigest()[:16]
        cache_file = pathlib.Path(cache_dir) / f"fewshot_{key}.npz"
        if cache_file.exists():
            try:
                return np.load(cache_file)["vecs"]
            except Exception:
                pass
    vecs = np.asarray(embedder(texts), dtype="float32")
    if cache_dir is not None:
        try:
            pathlib.Path(cache_dir).mkdir(parents=True, exist_ok=True)
            np.savez(cache_file, vecs=vecs)
        except Exception:
            pass
    return vecs


def _vector_ranking(question, examples, embedder, deployment, cache_dir) -> list[int]:
    """Return example indices ranked best-first by cosine similarity (FAISS inner product
    over L2-normalized vectors)."""
    import faiss
    ex_vecs = _normalize(_example_embeddings(examples, embedder, deployment, cache_dir))
    q_vec = _normalize(embedder([question]))
    index = faiss.IndexFlatIP(ex_vecs.shape[1])
    index.add(ex_vecs)
    _, idx = index.search(q_vec, len(examples))
    return list(int(i) for i in idx[0])


def _rrf(rankings: list[list[int]], k: int = RRF_K) -> list[int]:
    """Reciprocal Rank Fusion: combine ranked lists by summing 1/(k + rank). No score
    normalization needed, which is why it's robust across different scorers."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, idx in enumerate(ranking):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda i: scores[i], reverse=True)


def select_examples(question, spec, k: int = 3, mode: str | None = None,
                    embedder=None, cache_dir=CACHE_DIR):
    """Pick the k most relevant few-shot examples for `question`.

    Dispatches on `mode` (or ADA_RETRIEVER, default 'keyword'). Hybrid fuses BM25 + dense
    vectors with RRF and degrades to BM25 then keyword if anything is unavailable, so the
    agent always gets examples.
    """
    mode = (mode or os.getenv("ADA_RETRIEVER", "keyword")).lower()
    examples = spec.get("query_examples", [])
    if not examples or mode == "keyword":
        return rank_examples(question, spec, k)

    rankings: list[list[int]] = []
    try:
        rankings.append(_bm25_ranking(question, examples, spec))
    except Exception:
        pass

    if mode == "hybrid":
        try:
            emb = embedder or embed_texts
            deployment = None if embedder is not None else _embed_deployment()
            cdir = cache_dir if embedder is None else None   # don't disk-cache mocked vecs
            rankings.append(_vector_ranking(question, examples, emb, deployment, cdir))
        except Exception:
            pass   # no API / faiss / numpy -> fall through to BM25-only (or keyword)

    if not rankings:
        return rank_examples(question, spec, k)
    fused = _rrf(rankings)
    return [examples[i] for i in fused[:k]] or examples[:k]


def _embed_deployment():
    try:
        from ada.agent.config import get_settings
        return get_settings().deployment_embed
    except Exception:
        return os.getenv("AZURE_OPENAI_EMBED_DEPLOYMENT", "text-embedding-3-small")
