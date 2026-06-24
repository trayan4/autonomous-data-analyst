"""
test_executor.py - verify executor logic WITHOUT a database by faking _connect.
Proves: validate-before-connect, column/row return, and row-cap truncation.

Run:  python agent/test_executor.py
"""
from __future__ import annotations

import sys

from ada.agent import executor as E

GOOD = "SELECT region, SUM(line_gmv) AS gmv FROM dbo.order_items GROUP BY region"
BAD = "DELETE FROM order_items"
DESC = [("region",), ("gmv",)]


def rows_n(n):
    return [(f"R{i}", float(i)) for i in range(n)]


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.description = DESC

    def execute(self, sql):
        pass

    def fetchmany(self, n):
        return self._rows[:n]

    def close(self):
        pass


class FakeConn:
    def __init__(self, rows):
        self._cur = FakeCursor(rows)

    def cursor(self):
        return self._cur

    def close(self):
        pass


ok = True


def check(name, cond):
    global ok
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    ok = ok and cond


# 1) bad SQL must be rejected BEFORE any connection attempt
def _explode(_timeout):
    raise AssertionError("connect() should not be called for invalid SQL")


E._connect = _explode
r = E.run_sql(BAD)
check("bad SQL rejected before connecting", (not r.ok) and "validation" in (r.error or ""))

# 2) good SQL returns columns + rows, not truncated
E._connect = lambda t: FakeConn(rows_n(4))
r = E.run_sql(GOOD, max_rows=1000)
check("returns columns + rows", r.ok and r.columns == ["region", "gmv"] and r.row_count == 4 and not r.truncated)

# 3) more rows than cap -> truncated, capped count
E._connect = lambda t: FakeConn(rows_n(11))
r = E.run_sql(GOOD, max_rows=10)
check("row cap truncates", r.ok and r.row_count == 10 and r.truncated)

print("\nRESULT:", "PASS - executor logic correct" if ok else "FAIL")
sys.exit(0 if ok else 1)
