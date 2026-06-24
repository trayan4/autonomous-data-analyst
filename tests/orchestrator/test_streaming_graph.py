"""
test_streaming_graph.py - stream_run() streams narration tokens through LangGraph's
custom channel and still returns the full final state.

Fully offline: a fake agent supplies fake columns/rows (no SQL/DB) and calls the
injected synthesize_fn, while the streaming synthesizers are monkeypatched to yield
canned deltas (no model). This exercises the real graph routing + custom-stream
plumbing without any Azure dependency.
"""
from __future__ import annotations

import sys

from ada.orchestrator import graph as G
from ada.agent.data_agent import AgentResult
import ada.agent.synthesizer as SY

ok = True


def check(name, cond):
    global ok
    ok = ok and cond
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")


def fake_agent(question, allow_pii=False, synthesize_fn=None):
    cols, rows = ["region", "gmv"], [("South", 511358.98)]
    answer = synthesize_fn(question, cols, rows) if synthesize_fn else "(blocking path)"
    return AgentResult(question=question, answer=answer, sql="SELECT 1", valid_sql=True,
                       row_count=1, columns=cols, rows=rows)


# canned streaming synthesizers (no model)
SY.synthesize_answer_stream = lambda q, c, r, max_rows=50: iter(["South ", "Electronics ", "stockout."])
SY.synthesize_diagnosis_stream = lambda q, c, r, max_rows=50: iter(["Cause: ", "South ", "stockout."])


def drain(gen):
    tokens, final = [], {}
    while True:
        try:
            tokens.append(next(gen))
        except StopIteration as stop:
            final = stop.value or {}
            break
    return tokens, final


# 1) simple path streams answer deltas, then returns full state
toks, final = drain(G.stream_run("how much gmv in may", agent_fn=fake_agent,
                                 classify_fn=lambda q: "simple"))
check("simple: tokens streamed in order", toks == ["South ", "Electronics ", "stockout."])
check("simple: final answer = joined deltas", final.get("answer") == "South Electronics stockout.")
check("simple: routed to data_retrieval", final.get("route") == "data_retrieval")
check("simple: final state carries sql", final.get("sql") == "SELECT 1")

# 2) diagnostic path streams via the diagnosis synthesizer + strong analysis node
toks2, final2 = drain(G.stream_run("why did sales drop", analysis_fn=fake_agent,
                                   classify_fn=lambda q: "diagnostic"))
check("diagnostic: tokens streamed", "".join(toks2) == "Cause: South stockout.")
check("diagnostic: routed to analysis", final2.get("route") == "analysis")

# 3) out-of-scope refusal: nothing streams, static answer in final state
toks3, final3 = drain(G.stream_run("which sales reps to fire", classify_fn=lambda q: "out_of_scope"))
check("refusal: no tokens streamed", toks3 == [])
check("refusal: routed to refuse", final3.get("route") == "refuse")
check("refusal: refusal text in final state", "outside what this assistant can answer" in final3.get("answer", ""))

# 4) the non-streaming run() is unchanged (no _stream, blocking agent path)
out = G.run("how much gmv in may", agent_fn=lambda q, allow_pii=False: fake_agent(q, allow_pii),
            classify_fn=lambda q: "simple")
check("blocking run() still returns blocking answer", out.get("answer") == "(blocking path)")

print("\nRESULT:", "PASS - graph streams narration and preserves final state" if ok else "FAIL")
sys.exit(0 if ok else 1)
