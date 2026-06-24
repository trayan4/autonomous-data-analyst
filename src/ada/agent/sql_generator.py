"""
turns a question into validated SQL (the data-retrieval spine).

generate_sql(question):
  1. build the schema-RAG briefing            (context_builder.build_context)
  2. ask the model for SQL                     (model_client.chat)
  3. strip stray ```sql fences                 (extract_sql)
  4. run the safety gate                       (sql_validator.validate_sql)
  5. if rejected, hand the model the reason and retry once (repair loop)

Returns a GenResult recording the SQL, whether it passed, and how many attempts
it took - so downstream nodes and evals can see what happened.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


from ada.agent.context_builder import build_context
from ada.agent.model_client import chat
from ada.agent.sql_validator import validate_sql

_FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.S | re.I)


def extract_sql(text: str) -> str:
    text = (text or "").strip()
    m = _FENCE.search(text)
    return m.group(1).strip() if m else text


@dataclass
class GenResult:
    question: str
    sql: str
    valid: bool
    attempts: int
    reasons: list = field(default_factory=list)
    raw: str = ""
    pii_blocked: bool = False


def generate_sql(question: str, max_retries: int = 1, allow_pii: bool = False,
                 tier: str = "strong", mode: str = "answer") -> GenResult:
    messages = build_context(question, mode=mode)
    sql, raw, result = "", "", None

    for attempt in range(1, max_retries + 2):
        raw = chat(messages, tier=tier)
        sql = extract_sql(raw)
        result = validate_sql(sql, allow_pii=allow_pii)
        if result.ok:
            return GenResult(question, sql, True, attempt, [], raw)
        if result.pii_blocked:
            # terminal: a PII request cannot be "repaired" - stop and surface it
            return GenResult(question, sql, False, attempt, result.reasons, raw, pii_blocked=True)
        # repair: show the model exactly why it was rejected and ask again
        messages = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content":
                "The SQL was rejected by the safety validator for: "
                + "; ".join(result.reasons)
                + ". Return ONE corrected read-only SELECT. SQL only, no prose."},
        ]

    return GenResult(question, sql, False, attempt, result.reasons if result else [], raw)


def repair_sql_for_execution(question: str, failed_sql: str, db_error: str,
                             allow_pii: bool = False, tier: str = "strong",
                             mode: str = "answer") -> str | None:
    """Ask the model to fix SQL that the database rejected at runtime. Returns
    corrected SQL only if it re-passes the safety + PII gate, else None (give up)."""
    messages = build_context(question, mode=mode) + [
        {"role": "assistant", "content": failed_sql},
        {"role": "user", "content":
            f"The database rejected that SQL with this error:\n{db_error}\n"
            "Return ONE corrected read-only SELECT that fixes the error. "
            "Qualify every column with its table alias to avoid ambiguity. SQL only, no prose."},
    ]
    sql = extract_sql(chat(messages, tier=tier))
    return sql if validate_sql(sql, allow_pii=allow_pii).ok else None


if __name__ == "__main__":
    q = "Why did sales drop last month?"
    r = generate_sql(q)
    print("Q:", q)
    print("valid:", r.valid, "| attempts:", r.attempts)
    if r.reasons:
        print("reasons:", r.reasons)
    print("SQL:\n" + r.sql)
