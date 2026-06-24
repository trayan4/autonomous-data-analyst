# ADR-0007: Hybrid few-shot retrieval (BM25 + dense vectors, fused with RRF)

- **Status:** Ongoing
- **Date:** 2026-06-24

## Context

The SQL prompt is taught with few-shot examples (`semantic_layer.yaml → query_examples`)
selected per question. The original selector (`rank_examples`) scored examples by
synonym-expanded **word overlap** — adequate for the initial bank of 6, where almost any
question lexically matched its intended example.

Growing the bank to ~50 diverse, individually-validated examples (broader pattern
coverage) exposed the limit of pure lexical scoring. For the headline diagnostic
question *"Why did sales drop last month?"*, overlap now retrieves the total-GMV and ROAS
examples and **drops** the region × category decomposition example — because the best
example (*"…which region and category drove the decline"*) shares **meaning** but not
**words** with the question. With 6 examples this never surfaced; with 50 competing for 3
slots, literal overlap mis-ranks. This is a retrieval-quality problem: semantic proximity
matters, not just shared tokens.

## Decision

- Introduce a single **`select_examples()`** entry point with pluggable backends chosen by
  **`ADA_RETRIEVER`** (or an explicit `mode` arg): `keyword` / `bm25` / `hybrid`.
- **`hybrid`** runs two retrievers and fuses them: **BM25** (lexical, via `rank_bm25`) for
  exact-term precision on metric names and dimension values, and **dense vectors** (Foundry
  `text-embedding-3-small`, cosine over a FAISS `IndexFlatIP`) for paraphrase recall. The
  two ranked lists are combined with **Reciprocal Rank Fusion** (RRF, k=60) — the same
  BM25 + vector + RRF pattern Azure AI Search uses, run in-process.
- **RRF over a weighted score blend**: fusion is *rank*-based, so it needs no score
  normalization across two scorers on different scales — which is exactly why it's robust.
- **`keyword` stays the default.** The offline test suite and the default eval path pull in
  no embedding API and no extra dependencies; hybrid is strictly opt-in.
- **Graceful degradation is structural**: heavy imports (`numpy`, `faiss`, `rank_bm25`) and
  the embedding call are lazy and wrapped, so `hybrid` degrades to `bm25` and `bm25` to
  `keyword` rather than ever hard-failing — the same soft-dependency posture as tracing.
- **The bank is embedded once and cached** on disk (keyed by a hash of the examples + the
  embedding deployment); only the user's question is embedded per call. The embedder is
  injectable, so the FAISS + RRF wiring is tested offline with deterministic mock vectors.

## Consequences

- For the paraphrased diagnostic query, hybrid surfaces the decomposition example that
  keyword drops — strictly better teaching context for the SQL model.
- **Default path is unchanged** (`keyword`), so there is no regression risk until a caller
  opts in, and the offline suite stays API-free (17/17, including a new `test_retriever`).
- Eval-driven (ADR-0004): retrieval is a prompt input, so a switch is observable via the
  golden set; enabling hybrid **requires** a fresh `ada-eval`. Confirmed 5/5 holds.
- Cost is one embedding call per question plus a one-time, cached embedding of the bank —
  negligible against the existing generation calls.
- **Trade-offs:**
  - Hybrid adds dependencies (`faiss-cpu`, `rank-bm25`, `numpy`) and needs an embedding
    deployment. Keeping it behind an opt-in extra (`pip install -e ".[retrieval]"`) keeps
    the default install and the offline path lean.
  - FAISS over ~50 vectors is **overkill** — a NumPy dot-product would rank just as well.
    It's chosen deliberately to mirror the production vector-store pattern and to scale to a
    much larger bank without rework, not because the current size demands it.
  - The most interesting consequence is a *non*-consequence: because ADR-0006 makes root
    cause a **structural property of the diagnostic path**, better retrieval does **not**
    change the `ucA` outcome — the forced decomposition holds regardless of which example is
    retrieved. So the headline eval can't see this win; its real value is breadth and
    correctness on *simple* and ad-hoc SQL, where the retrieved example does shape output. A
    retrieval-specific eval (recall@k over labelled question→example pairs) would measure it
    directly; I'll add one under a new ADR if retrieval quality needs to be a gate rather
    than an enhancement.
  - The disk cache invalidates automatically when example text changes, but **not** if an
    embedding deployment is swapped while keeping the same name. Acceptable given the
    deployment name is part of the cache key and rarely reused for a different model.
