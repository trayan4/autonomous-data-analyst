"""
test_graph.py - verify routing with stub classifier + agents (no model/DB).
Proves: out_of_scope -> refuse (no agent called); simple -> cheap data agent;
diagnostic -> analysis agent; and the 3-way classifier parser.

Run:  python orchestrator/test_graph.py
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

from ada.orchestrator import graph as G

ok = True


def check(name, cond):
    global ok
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    ok = ok and cond


def res(answer):
    return SimpleNamespace(answer=answer, sql="SELECT 1", columns=["c"],
                           rows=[(1,)], error=None, seconds=0.1)


calls = {"data": 0, "analysis": 0}


def fake_data(q, allow_pii=False):
    calls["data"] += 1
    return res("DATA_ANSWER")


def fake_analysis(q, allow_pii=False):
    calls["analysis"] += 1
    return res("ANALYSIS_ANSWER")


# simple -> cheap data agent
out = G.run("What was total GMV in May 2026?", agent_fn=fake_data,
            analysis_fn=fake_analysis, classify_fn=lambda q: "simple")
check("simple routes to data agent", out.get("route") == "data_retrieval" and out.get("answer") == "DATA_ANSWER")
check("data agent invoked", calls["data"] == 1)
check("analysis NOT invoked on simple", calls["analysis"] == 0)

# diagnostic -> analysis agent
calls["data"] = calls["analysis"] = 0
out = G.run("Why did sales drop last month?", agent_fn=fake_data,
            analysis_fn=fake_analysis, classify_fn=lambda q: "diagnostic")
check("diagnostic routes to analysis agent", out.get("route") == "analysis" and out.get("answer") == "ANALYSIS_ANSWER")
check("analysis agent invoked", calls["analysis"] == 1)
check("data agent NOT invoked on diagnostic", calls["data"] == 0)

# out_of_scope -> refuse, neither agent called
calls["data"] = calls["analysis"] = 0
out = G.run("Which sales reps should we fire?", agent_fn=fake_data,
            analysis_fn=fake_analysis, classify_fn=lambda q: "out_of_scope")
check("out_of_scope routes to refuse", out.get("route") == "refuse")
check("refusal explains scope", "outside" in out.get("answer", "").lower())
check("no agent called on refusal", calls["data"] == 0 and calls["analysis"] == 0)

# 3-way parser
check("parser reads out_of_scope", G.classify_scope("q", chat_fn=lambda m, **k: "out_of_scope") == "out_of_scope")
check("parser reads diagnostic", G.classify_scope("q", chat_fn=lambda m, **k: "diagnostic") == "diagnostic")
check("parser reads simple", G.classify_scope("q", chat_fn=lambda m, **k: "simple") == "simple")
check("parser defaults to simple on junk", G.classify_scope("q", chat_fn=lambda m, **k: "maybe?") == "simple")
check("parser reads first word", G.classify_scope("q", chat_fn=lambda m, **k: "diagnostic - why") == "diagnostic")

print("\nRESULT:", "PASS - router wired correctly" if ok else "FAIL")
sys.exit(0 if ok else 1)
