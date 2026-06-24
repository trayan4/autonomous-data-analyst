"""
the Analysis agent.

It reuses the Data Retrieval agent's chain (generate -> validate -> execute -> repair)
but with two changes that ensures RCA, instead of it being accidental:

  1. mode="diagnose": the SQL briefing forbids a bare top-line total and REQUIRES a
     decomposition of the metric change by region x product category (plus an optional
     stockout signal). The single data agent only *sometimes* chose to decompose; this
     path always does.
  2. a causal synthesizer (synthesize_diagnosis) that names the driving region/category
     and the likely cause - run on the strong tier, since this is the reasoning step.

The orchestrator routes diagnostic ("why did X change") questions here and simple
look-ups to the cheaper data agent. Same AgentResult shape, so the graph treats them
interchangeably.

CLI:  python -m ada.agent.analysis_agent "Why did sales drop last month?"
"""
from __future__ import annotations

import sys


from ada.agent.data_agent import AgentResult, answer_question
from ada.agent.synthesizer import synthesize_diagnosis


def analyze_question(question: str, max_rows: int = 1000, max_exec_retries: int = 2,
                     allow_pii: bool = False, synthesize_fn=None) -> AgentResult:
    """Diagnostic path: force a decomposed query on the strong tier and narrate the
    cause. A thin specialization of answer_question - no duplicated execution logic."""
    return answer_question(
        question,
        max_rows=max_rows,
        max_exec_retries=max_exec_retries,
        allow_pii=allow_pii,
        tier="strong",
        mode="diagnose",
        synthesize_fn=synthesize_fn or synthesize_diagnosis,
    )


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "Why did sales drop last month?"
    r = analyze_question(q)
    print("Q:", r.question)
    print(f"\n--- SQL (valid={r.valid_sql}) ---\n{r.sql}")
    if r.error:
        print("\nERROR:", r.error)
    print(f"\n--- rows ({r.row_count}) ---")
    for row in r.rows[:12]:
        print("  ", row)
    print("\n--- diagnosis ---\n" + r.answer)
