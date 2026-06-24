"""
test_sql_validator.py - prove the gate lets honest queries through and blocks
everything dangerous: writes, DDL, stacked statements, and off-limits tables.

Run:  python agent/test_sql_validator.py     (exit 0 = all correct)
"""
from __future__ import annotations

import sys

from ada.agent.sql_validator import validate_sql

SAFE = [
    "SELECT SUM(line_gmv) FROM order_items WHERE order_date >= '2026-05-01' AND order_date < '2026-06-01'",
    "SELECT TOP 10 region, SUM(line_gmv) AS g FROM dbo.order_items GROUP BY region ORDER BY g DESC",
    """WITH may AS (SELECT region, SUM(line_gmv) g FROM order_items
                    WHERE order_date >= '2026-05-01' AND order_date < '2026-06-01' GROUP BY region),
            tr  AS (SELECT region, SUM(line_gmv)/3.0 g FROM order_items
                    WHERE order_date >= '2026-02-01' AND order_date < '2026-05-01' GROUP BY region)
       SELECT m.region, (m.g - t.g) AS delta FROM may m JOIN tr t ON m.region = t.region ORDER BY delta""",
    "SELECT region, segment, COUNT(*) AS customers FROM customers GROUP BY region, segment",
]

UNSAFE = [
    ("DELETE FROM order_items",                                   "write"),
    ("DROP TABLE customers",                                      "DDL"),
    ("UPDATE customers SET email = 'x@y.com'",                    "write"),
    ("INSERT INTO orders (order_id) VALUES (1)",                  "write"),
    ("SELECT * FROM order_items; DROP TABLE customers",           "stacked statements"),
    ("SELECT * FROM sys.tables",                                  "system schema"),
    ("SELECT * FROM secret_payroll",                              "off-limits table"),
    ("SELECT * FROM customers; EXEC xp_cmdshell 'whoami'",        "stacked + command"),
]

print(f"{'query':<60} expect  result")
print("-" * 80)
ok = True

for q in SAFE:
    r = validate_sql(q)
    status = "PASS" if r.ok else "FAIL"
    if not r.ok:
        ok = False
    print(f"{q[:57].replace(chr(10),' '):<60} SAFE    {status}  {r.reasons}")

for q, label in UNSAFE:
    r = validate_sql(q)
    status = "BLOCKED" if not r.ok else "LEAKED!"
    if r.ok:
        ok = False
    print(f"{(q[:57]):<60} UNSAFE  {status}  ({label})")

# PII guard: PII references blocked + tagged; entitlement override permits; non-PII unaffected
PII_BLOCKED = [
    "SELECT full_name, email, phone FROM customers",
    "SELECT c.email FROM dbo.customers c WHERE c.segment = 'VIP'",
    "SELECT * FROM customers",
]
for q in PII_BLOCKED:
    r = validate_sql(q)
    good = (not r.ok) and r.pii_blocked
    if not good:
        ok = False
    print(f"{q[:57]:<60} PII     {'BLOCKED+tagged' if good else 'LEAKED!'}")

# entitled caller may pass PII; COUNT(*) over a PII table is not a star-leak
if not validate_sql("SELECT email FROM customers", allow_pii=True).ok:
    ok = False
    print("entitlement override FAILED")
if validate_sql("SELECT COUNT(*) FROM customers").pii_blocked:
    ok = False
    print("COUNT(*) wrongly flagged as PII star")

print("-" * 80)
print("RESULT:", "PASS - gate accepts safe, blocks unsafe + PII" if ok else "FAIL - gate misclassified a query")
sys.exit(0 if ok else 1)
