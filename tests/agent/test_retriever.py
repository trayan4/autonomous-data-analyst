"""
test_retriever.py - few-shot retrieval backends, offline.

Hybrid is exercised with a MOCKED embedder (deterministic vectors, no API), so the
BM25 + FAISS + RRF wiring is verified end to end without Azure.
"""
from __future__ import annotations

import sys

import numpy as np

from ada.agent import retriever as R

ok = True


def check(name, cond):
    global ok
    ok = ok and cond
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")


SPEC = {
    "metrics": {"gmv": {"synonyms": ["sales", "revenue"]}},
    "query_examples": [
        {"q": "What was total GMV last month?", "sql": "SELECT SUM(line_gmv) FROM dbo.order_items"},
        {"q": "Which region and category drove the decline?",
         "sql": "SELECT region, category FROM dbo.order_items GROUP BY region, category"},
        {"q": "ROAS by channel", "sql": "SELECT channel FROM dbo.marketing_spend"},
        {"q": "Average discount by category", "sql": "SELECT category, AVG(discount) FROM dbo.order_items"},
    ],
}


def fake_embed(texts):
    """Deterministic 'semantic' vectors: a diagnosis axis, a roas axis, a gmv axis."""
    out = []
    for t in texts:
        tl = t.lower()
        diag = 1.0 if any(w in tl for w in ["drove", "decline", "drop", "why", "sales"]) else 0.0
        roas = 1.0 if "roas" in tl else 0.0
        gmv = 1.0 if ("gmv" in tl or "total" in tl) else 0.0
        out.append([diag, roas, gmv, 0.1])
    return np.asarray(out, dtype="float32")


# 1) keyword (default) returns the lexically-matching example first
kw = R.select_examples("total GMV last month", SPEC, k=2, mode="keyword")
check("keyword returns the GMV example first", len(kw) >= 1 and kw[0]["q"].startswith("What was total GMV"))

# 2) bm25 ranks lexically, no API
bm = R.select_examples("average discount by category", SPEC, k=1, mode="bm25")
check("bm25 finds the discount example", bm[0]["q"].startswith("Average discount"))

# 3) hybrid: vector side pulls the semantically-matching example for a paraphrase
hy = R.select_examples("Why did sales drop?", SPEC, k=2, mode="hybrid",
                       embedder=fake_embed, cache_dir=None)
hy_qs = [e["q"] for e in hy]
check("hybrid surfaces the decomposition example", "Which region and category drove the decline?" in hy_qs)

# 4) RRF fusion math: an item ranked high by both lists wins
fused = R._rrf([[2, 0, 1], [2, 1, 0]])
check("rrf ranks the jointly-top item first", fused[0] == 2)

# 5) graceful degradation: a broken embedder -> hybrid falls back, still returns k
def broken_embed(texts):
    raise RuntimeError("no embedding service")

deg = R.select_examples("average discount by category", SPEC, k=2, mode="hybrid",
                        embedder=broken_embed, cache_dir=None)
check("hybrid degrades to lexical when embedding fails", len(deg) == 2)

# 6) empty bank never explodes
check("empty bank returns []", R.select_examples("x", {"query_examples": []}, k=3, mode="hybrid") == [])

print("\nRESULT:", "PASS - retrieval backends + RRF + degradation work" if ok else "FAIL")
sys.exit(0 if ok else 1)
