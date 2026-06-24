"""
test_data_agent.py - verify the end-to-end wiring WITHOUT a model or DB by mocking
the three stages. Proves: happy path returns the synthesized answer, and failures
short-circuit (later stages are NOT called).

Run:  python agent/test_data_agent.py
"""
from __future__ import annotations

import sys

from ada.agent import data_agent as A
from ada.agent.sql_generator import GenResult
from ada.agent.executor import QueryResult

ok = True


def check(name, cond):
    global ok
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    ok = ok and cond


# 1) happy path
A.generate_sql = lambda q, **k: GenResult(q, "SELECT 1", True, 1, [], "raw")
A.run_sql = lambda sql, max_rows=1000: QueryResult(ok=True, columns=["a"], rows=[(1,)], row_count=1)
A.synthesize_answer = lambda q, c, r: "synthesized answer"
res = A.answer_question("Q")
check("happy path returns synthesized answer", res.answer == "synthesized answer" and res.valid_sql and res.row_count == 1 and not res.error)

# 2) invalid SQL short-circuits: run_sql and synthesize must NOT be called
flags = {"run": False, "synth": False}
A.generate_sql = lambda q, **k: GenResult(q, "DELETE", False, 2, ["only read-only allowed"], "raw")
A.run_sql = lambda *a, **k: flags.__setitem__("run", True) or QueryResult(ok=True)
A.synthesize_answer = lambda *a, **k: flags.__setitem__("synth", True) or "x"
res = A.answer_question("Q")
check("invalid SQL stops before execute/synthesize", (not res.valid_sql) and res.error and not flags["run"] and not flags["synth"])

# 3) query error with no viable repair -> stops before synthesize
flags2 = {"synth": False}
A.generate_sql = lambda q, **k: GenResult(q, "SELECT 1", True, 1, [], "raw")
A.run_sql = lambda *a, **k: QueryResult(ok=False, error="boom")
A.repair_sql_for_execution = lambda *a, **k: None          # repair gives up
A.synthesize_answer = lambda *a, **k: flags2.__setitem__("synth", True) or "x"
res = A.answer_question("Q")
check("unrepairable query failure stops before synthesize", res.valid_sql and res.error == "boom" and not flags2["synth"])

# 4) PII block short-circuits to a restriction message, no execute/synthesize
flags3 = {"run": False, "synth": False}
A.generate_sql = lambda q, **k: GenResult(q, "SELECT email FROM customers", False, 1, ["pii"], "raw", pii_blocked=True)
A.run_sql = lambda *a, **k: flags3.__setitem__("run", True) or QueryResult(ok=True)
A.synthesize_answer = lambda *a, **k: flags3.__setitem__("synth", True) or "x"
res = A.answer_question("Q")
check("PII block returns restriction, no execute/synthesize",
      (not res.valid_sql) and ("permitted" in res.answer.lower() or "restrictions" in res.answer.lower())
      and not flags3["run"] and not flags3["synth"])

# 5) execution error repaired, then succeeds -> synthesize runs, sql updated to the fix
state = {"calls": 0}


def flaky_run(sql, max_rows=1000):
    state["calls"] += 1
    if state["calls"] == 1:
        return QueryResult(ok=False, error="Ambiguous column name 'order_date'")
    return QueryResult(ok=True, columns=["g"], rows=[(123,)], row_count=1)


synth = {"called": False}
A.generate_sql = lambda q, **k: GenResult(q, "BROKEN", True, 1, [], "raw")
A.run_sql = flaky_run
A.repair_sql_for_execution = lambda question, sql, err, **k: "SELECT SUM(oi.line_gmv) g FROM dbo.order_items oi"
A.synthesize_answer = lambda q, c, r: synth.__setitem__("called", True) or "fixed answer"
res = A.answer_question("Q")
check("execution-repair recovers then synthesizes",
      res.valid_sql and res.answer == "fixed answer" and synth["called"]
      and res.sql == "SELECT SUM(oi.line_gmv) g FROM dbo.order_items oi" and res.row_count == 1)

print("\nRESULT:", "PASS - end-to-end wiring correct" if ok else "FAIL")
sys.exit(0 if ok else 1)
