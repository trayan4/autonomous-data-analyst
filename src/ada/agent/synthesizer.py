"""
turns retrieved rows into a grounded answer (Data Retrieval agent).

synthesize_answer(question, columns, rows) asks the model to answer using ONLY the
result table - cite the numbers, invent nothing, and if the data shows WHERE a
change happened but not WHY, say so. This is the data agent stating what its own
data shows; deeper multi-source diagnosis is the Phase-4 Analysis agent's job.

Each function has a streaming duplicate (*_stream) that yields text deltas for the
user-facing narration. Prompt construction is shared via _messages(), so the
streamed and blocking outputs are identical - eval keeps using the blocking ones.
"""
from __future__ import annotations

from ada.agent.model_client import chat, chat_stream

SYNTH_RULES = """You are a data analyst. Answer the user's question using ONLY the result table provided.
- First describe what the data DOES show: the key figures, the largest and smallest values, and any clear outlier or pattern, citing specific numbers.
- Do not invent values, rows, or causes the data does not show.
- Do not treat the data as representing an entity the question named unless the columns actually correspond to it. If the question asks about X (e.g. sales reps) but the columns are a different entity (e.g. customers), say the data does not contain X rather than relabeling one as the other.
- If the table shows WHERE/WHAT but not WHY, give those observable findings first, then state plainly that the cause cannot be determined from this data and what further data would be needed.
- Be concise: a few sentences, no preamble."""


def _format_table(columns, rows, max_rows):
    header = " | ".join(map(str, columns))
    sep = " | ".join("---" for _ in columns)
    body = "\n".join(" | ".join(str(c) for c in r) for r in rows[:max_rows])
    extra = "" if len(rows) <= max_rows else f"\n...(+{len(rows) - max_rows} more rows)"
    return f"{header}\n{sep}\n{body}{extra}"


def _messages(rules: str, question: str, columns: list, rows: list, max_rows: int) -> list[dict]:
    """Build the synth chat messages. Single source of truth for both the blocking
    and streaming variants so their prompts (and therefore outputs) stay identical."""
    table = _format_table(columns, rows, max_rows) if rows else "(no rows returned)"
    return [
        {"role": "system", "content": rules},
        {"role": "user", "content": f"Question: {question}\n\nResult table:\n{table}"},
    ]


def synthesize_answer(question: str, columns: list, rows: list, max_rows: int = 50) -> str:
    # narrating a correct result set is easy work -> cheap tier
    return chat(_messages(SYNTH_RULES, question, columns, rows, max_rows), tier="cheap")


def synthesize_answer_stream(question: str, columns: list, rows: list, max_rows: int = 50):
    """Streaming twin of synthesize_answer: yields text deltas (cheap tier)."""
    yield from chat_stream(_messages(SYNTH_RULES, question, columns, rows, max_rows), tier="cheap")


DIAGNOSIS_SYNTH_RULES = """You are a data analyst explaining WHY a metric changed, using ONLY the result table.
- Lead with the root cause: name the region AND product category that fell the most and by how much, citing the focal vs baseline figures and the percent change.
- If the table flags a stockout or other signal aligned with the worst-hit segment, state that it is the likely cause of the collapse there.
- Then note the secondary contributors briefly.
- Do not invent causes the data does not show. If the data localizes WHERE the change happened but not WHY, say so and what further data is needed.
- Be concise: a few sentences, no preamble."""


def synthesize_diagnosis(question: str, columns: list, rows: list, max_rows: int = 50) -> str:
    """Causal narration for the analysis agent: name the driving region/category and
    the likely cause. Runs on the strong tier - this is the reasoning-heavy step."""
    return chat(_messages(DIAGNOSIS_SYNTH_RULES, question, columns, rows, max_rows), tier="strong")


def synthesize_diagnosis_stream(question: str, columns: list, rows: list, max_rows: int = 50):
    """Streaming twin of synthesize_diagnosis: yields text deltas (strong tier)."""
    yield from chat_stream(_messages(DIAGNOSIS_SYNTH_RULES, question, columns, rows, max_rows), tier="strong")


if __name__ == "__main__":
    cols = ["region", "gmv"]
    rows = [("North", 1372904.94), ("East", 1006788.71),
            ("West", 905423.23), ("South", 511358.98)]
    print("Q: Why did sales drop last month?\n")
    print(synthesize_answer("Why did sales drop last month?", cols, rows))
