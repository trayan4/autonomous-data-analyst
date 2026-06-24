"""
context_builder.py - assemble the schema-RAG briefing the SQL model reads.

build_context(question) returns chat messages (system + user) containing the rules,
the schema, the canonical metric definitions, and the few-shot examples most
relevant to the question. Examples are ranked by keyword overlap with the question
after expanding query words via the catalog's metric synonyms (e.g. "sales" -> gmv).

The ranking is a deterministic stand-in for vector retrieval and needs no extra
infrastructure - it runs purely in-process, so NO Azure AI Search (or any vector
store) is currentlyrequired to run this project. On the next deployment, I'm considering
upgrading to using FAISS / Azure AI Search & an embedding model to index the full set
of query examples, and retrieve the top-k based on the question embedding;
that would allow scaling to a much larger set of data (& at the same time,
a higher number of supported queries); then rank_examples() would be swapped for the vector retriever.
P.S: since I'm poor, I don't know whether I'll actually do that, but the code is structured to allow it if I want to.
Update: Now I'm using FAISS for vector retrieval, but the code is still structured to allow swapping it out for Azure AI Search if required.
"""
from __future__ import annotations

import datetime
import importlib.resources as resources
import re

import yaml

SEMANTIC = resources.files("ada.semantic") / "semantic_layer.yaml"

SYSTEM_RULES = """You are a careful data analyst for an e-commerce warehouse on Azure SQL (T-SQL).
Write exactly ONE read-only SELECT statement that answers the user's question.

Rules:
- Use ONLY the tables and columns in the schema below. Never write or modify data.
- Use the canonical metric definitions exactly; do not invent your own formulas.
- Prefer aggregates and GROUP BY; add TOP when returning rows so results stay bounded.
- When comparing one period against a longer baseline, normalize for length: compare the period against the baseline's per-period AVERAGE (divide the baseline total by the number of periods it spans). Never compare a single-period total against a multi-period total.
- Never select PII columns (full_name, email, phone) unless the request is explicitly entitled.
- Return ONLY the SQL - no explanation, no markdown code fences."""

DIAGNOSIS_DIRECTIVE = """\
DIAGNOSIS MODE - the user is asking WHY a metric changed, not merely by how much.
A top-line total alone is NOT an acceptable answer; you must localize the cause.
Write ONE read-only SELECT that decomposes the change:
- Compute the metric for the focal period next to the per-period baseline AVERAGE,
  broken down BY region AND BY product category, including the percent change between
  them. Keep region and category as explicit columns and ORDER BY the largest decline
  first, so the worst-hit segment is the top row.
- Where it helps explain a sharp drop, you may LEFT JOIN inventory_snapshots to flag
  stockouts (stock_level = 0) in the focal period for those segments.
Return only that SELECT."""


def _load():
    return yaml.safe_load(SEMANTIC.read_text())


def _words(text):
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _add_months(first_of_month: datetime.date, n: int) -> datetime.date:
    m = first_of_month.month - 1 + n
    return datetime.date(first_of_month.year + m // 12, m % 12 + 1, 1)


def date_context(today: datetime.date | None = None) -> str:
    """Tell the model what 'today' is so it can resolve relative dates itself.
    Uses the live system clock; the `today` arg exists only for tests."""
    today = today or datetime.date.today()
    this_m = today.replace(day=1)
    last_m = _add_months(this_m, -1)
    trailing = _add_months(last_m, -3)
    return "\n".join([
        "DATE CONTEXT (resolve relative dates against this; do not guess):",
        f"- Today is {today.isoformat()}.",
        f"- 'last month' = order_date >= '{last_m}' AND order_date < '{this_m}'.",
        f"- 'the three months before last month' = order_date >= '{trailing}' AND order_date < '{last_m}'.",
        "- For any other relative period, compute from today using the same "
        "half-open [first-of-period, first-of-next-period) pattern shown in the examples.",
    ])


def format_schema(spec):
    lines = ["SCHEMA (schema: dbo):"]
    for tname, t in spec["tables"].items():
        lines.append(f"\n- {tname} - {t.get('grain', '')}: {t.get('description', '')}")
        for col, c in t["columns"].items():
            c = c or {}
            enum = f" enum={c['enum']}" if c.get("enum") else ""
            pii = " [PII]" if c.get("pii") else ""
            lines.append(f"    {col} ({c.get('type', '')}){pii}: {c.get('description', '')}{enum}")
    rels = [r for r in spec.get("relationships", []) if isinstance(r, dict) and r.get("from")]
    if rels:
        lines.append("\nJOINS:")
        for r in rels:
            lines.append(f"    {r['from']} -> {r.get('to', '')}")
    return "\n".join(lines)


def format_metrics(spec):
    lines = ["METRICS (use these exact definitions):"]
    for m, d in spec.get("metrics", {}).items():
        syn = f"  (aka {', '.join(d['synonyms'])})" if d.get("synonyms") else ""
        lines.append(f"- {m} = {d['expression']}   [{d.get('table', 'cross-table')}]{syn}")
    return "\n".join(lines)


def _synonym_index(spec):
    idx = {}
    for m, d in spec.get("metrics", {}).items():
        for term in [m] + d.get("synonyms", []):
            for w in _words(term):
                idx.setdefault(w, set()).add(m)
    return idx


def rank_examples(question, spec, k=3):
    examples = spec.get("query_examples", [])
    q = _words(question)
    syn = _synonym_index(spec)
    expanded = set(q)
    for w in q:
        expanded |= syn.get(w, set())
    scored = [(len(expanded & _words(ex["q"] + " " + ex["sql"])), ex) for ex in examples]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [ex for score, ex in scored[:k] if score > 0] or examples[:k]


def format_examples(examples):
    lines = ["EXAMPLES (question -> SQL):"]
    for ex in examples:
        lines.append(f"\nQ: {ex['q']}\n{ex['sql'].strip()}")
    return "\n".join(lines)


def build_context(question, k_examples=3, mode="answer"):
    from ada.agent.retriever import select_examples
    spec = _load()
    context = "\n\n".join([
        date_context(),
        format_schema(spec),
        format_metrics(spec),
        format_examples(select_examples(question, spec, k_examples)),
    ])
    system = SYSTEM_RULES + ("\n\n" + DIAGNOSIS_DIRECTIVE if mode == "diagnose" else "")
    return [
        {"role": "system", "content": system + "\n\n" + context},
        {"role": "user", "content": question},
    ]


if __name__ == "__main__":
    Q = "Why did sales drop last month?"
    spec = _load()

    print("RANKED EXAMPLES for:", Q)
    for ex in rank_examples(Q, spec):
        print("  -", ex["q"])

    msgs = build_context(Q)
    print("\n" + "=" * 70)
    print("[system]\n" + msgs[0]["content"])
    print("\n[user]\n" + msgs[1]["content"])
    print("=" * 70)

    sys_text = msgs[0]["content"]
    assert all(t in sys_text for t in spec["tables"]), "missing a table"
    assert "gmv = SUM" in sys_text, "missing gmv metric"
    assert "[PII]" in sys_text, "PII not flagged"
    assert "read-only" in sys_text.lower(), "rules missing"
    assert "Today is" in sys_text, "date context missing"
    print("\ncontext checks: PASS")
