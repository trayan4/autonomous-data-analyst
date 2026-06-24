"""
data_agent.py - the Data Retrieval agent, end to end (standalone, no orchestrator).

answer_question(question):
    generate_sql -> (validate inside generator) -> execute -> synthesize

Returns an AgentResult with the question, the SQL, a row preview, the answer, and
status flags - short-circuiting with a clear message if generation or execution
fails. This is one agent's full loop; the Phase-4 orchestrator will call it as the
data-retrieval worker alongside the research and analysis agents.

CLI:  python -m ada.agent.data_agent "Why did sales drop last month?"
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field


from ada.agent.sql_generator import generate_sql, repair_sql_for_execution
from ada.agent.executor import run_sql
from ada.agent.synthesizer import synthesize_answer


@dataclass
class AgentResult:
    question: str
    answer: str
    sql: str = ""
    valid_sql: bool = False
    row_count: int = 0
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    error: str | None = None
    seconds: float = 0.0


def answer_question(question: str, max_rows: int = 1000, max_exec_retries: int = 2,
                    allow_pii: bool = False, tier: str = "strong", mode: str = "answer",
                    synthesize_fn=None) -> AgentResult:
    t0 = time.time()
    synthesize_fn = synthesize_fn or synthesize_answer

    gen = generate_sql(question, allow_pii=allow_pii, tier=tier, mode=mode)
    if gen.pii_blocked:
        return AgentResult(
            question,
            answer=("This request asks for customer contact details, which aren't permitted "
                    "for this role due to access restrictions. I can provide aggregated or "
                    "non-identifying results instead - for example, customer counts by segment "
                    "or region."),
            sql=gen.sql, valid_sql=False, error="blocked: PII not permitted for this role",
            seconds=time.time() - t0)
    if not gen.valid:
        return AgentResult(
            question, answer="I couldn't produce a safe, valid query for that question.",
            sql=gen.sql, valid_sql=False,
            error="invalid SQL: " + "; ".join(gen.reasons), seconds=time.time() - t0)

    sql = gen.sql
    res = run_sql(sql, max_rows=max_rows)

    # execution-repair: feed DB errors back to the model for a corrected query
    exec_retries = 0
    while not res.ok and exec_retries < max_exec_retries:
        fixed = repair_sql_for_execution(question, sql, res.error or "", allow_pii=allow_pii, tier=tier, mode=mode)
        if not fixed:
            break                       # repair was unsafe/PII or empty - stop
        sql = fixed
        res = run_sql(sql, max_rows=max_rows)
        exec_retries += 1

    if not res.ok:
        return AgentResult(
            question, answer="The query failed to run against the database.",
            sql=sql, valid_sql=True, error=res.error, seconds=time.time() - t0)

    answer = synthesize_fn(question, res.columns, res.rows)
    return AgentResult(
        question, answer=answer, sql=sql, valid_sql=True,
        row_count=res.row_count, columns=res.columns, rows=res.rows,
        seconds=time.time() - t0)


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "Why did sales drop last month?"
    r = answer_question(q)
    print("Q:", r.question)
    print(f"\n--- SQL (valid={r.valid_sql}) ---\n{r.sql}")
    if r.error:
        print("\nERROR:", r.error)
    print(f"\n--- rows ({r.row_count}) ---")
    print("cols:", r.columns)
    for row in r.rows[:12]:
        print("  ", row)
    print("\n--- ANSWER ---\n" + r.answer)
    print(f"\n({r.seconds:.1f}s)")
