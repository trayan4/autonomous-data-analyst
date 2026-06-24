"""
test_sql_generator.py - verify the generator pipeline WITHOUT a live model by
mocking model_client.chat. Proves: fence stripping, validator integration, and
the repair retry loop. No API key needed.

Run:  python agent/test_sql_generator.py
"""
from __future__ import annotations

import sys

from ada.agent import sql_generator as G

GOOD = ("SELECT region, SUM(line_gmv) AS gmv FROM dbo.order_items "
        "WHERE order_date >= '2026-05-01' AND order_date < '2026-06-01' GROUP BY region")
FENCED = "```sql\n" + GOOD + "\n```"
BAD = "DELETE FROM order_items"


def run_with(replies):
    """Patch chat() to return canned replies in sequence."""
    state = {"i": 0}

    def fake_chat(messages, **kw):
        out = replies[min(state["i"], len(replies) - 1)]
        state["i"] += 1
        return out

    G.chat = fake_chat
    return G.generate_sql("Why did sales drop last month?")


ok = True


def check(name, cond):
    global ok
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    ok = ok and cond


# 1) clean good SQL -> valid on first attempt
r = run_with([GOOD])
check("plain good SQL valid on attempt 1", r.valid and r.attempts == 1)

# 2) fenced SQL -> fences stripped, still valid
r = run_with([FENCED])
check("fenced SQL stripped + valid", r.valid and "```" not in r.sql and r.sql.upper().startswith("SELECT"))

# 3) bad then good -> repair loop produces valid on attempt 2
r = run_with([BAD, GOOD])
check("repair loop recovers on attempt 2", r.valid and r.attempts == 2)

# 4) always bad -> invalid with reasons
r = run_with([BAD, BAD])
check("persistently bad SQL rejected with reasons", (not r.valid) and bool(r.reasons))

# PII request is terminal: blocked + tagged, no retry even if a "fix" is queued next
r = run_with(["SELECT full_name, email, phone FROM customers", GOOD])
check("PII request blocked terminally (no retry)", (not r.valid) and r.pii_blocked and r.attempts == 1)

# execution-repair: accept a corrected safe query, reject a PII "fix"
G.chat = lambda messages, **k: "SELECT SUM(oi.line_gmv) AS g FROM dbo.order_items oi"
fixed = G.repair_sql_for_execution("q", "SELECT SUM(line_gmv) ...", "Ambiguous column name 'order_date'")
check("execution-repair returns corrected safe SQL", fixed is not None and "order_items" in fixed)

G.chat = lambda messages, **k: "SELECT email FROM customers"
check("execution-repair rejects a PII fix", G.repair_sql_for_execution("q", "bad", "err") is None)

print("\nRESULT:", "PASS - generator wiring correct" if ok else "FAIL")
sys.exit(0 if ok else 1)
