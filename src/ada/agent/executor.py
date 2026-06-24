"""
executor.py - run validated SQL against Azure SQL: read-only, bounded, timed.

run_sql(sql):
  1. re-validate through the safety gate (never execute unvalidated SQL)
  2. open a connection (pyodbc, imported lazily so the module loads without it)
  3. execute with a query timeout
  4. fetch at most max_rows (+1 to detect truncation)
  5. return columns + rows + flags, never raising

Connection settings come from .env (AZURE_SQL_*), SQL auth over ODBC Driver 18.
The connection only ever runs the SELECT and never commits - effectively read-only.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import find_dotenv, load_dotenv

from ada.agent.sql_validator import validate_sql

load_dotenv(find_dotenv(usecwd=True))

DRIVER = "{ODBC Driver 18 for SQL Server}"


def _require(name: str) -> str:
    v = os.getenv(name, "")
    if not v or v.startswith("<"):
        raise RuntimeError(f"missing env var {name} - set it in .env")
    return v


def _conn_str() -> str:
    return (
        f"DRIVER={DRIVER};"
        f"SERVER={_require('AZURE_SQL_SERVER')},1433;"
        f"DATABASE={_require('AZURE_SQL_DATABASE')};"
        f"UID={_require('AZURE_SQL_USER')};"
        f"PWD={_require('AZURE_SQL_PASSWORD')};"
        f"Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
    )


def _connect(timeout_s: int):
    import pyodbc  # lazy: keeps the module importable where the driver isn't installed
    conn = pyodbc.connect(_conn_str(), timeout=timeout_s)
    conn.timeout = timeout_s            # per-query timeout
    return conn


@dataclass
class QueryResult:
    ok: bool
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    error: str | None = None


def run_sql(sql: str, max_rows: int = 1000, timeout_s: int = 30) -> QueryResult:
    gate = validate_sql(sql)
    if not gate.ok:
        return QueryResult(ok=False, error="validation failed: " + "; ".join(gate.reasons))

    try:
        conn = _connect(timeout_s)
    except Exception as e:  # noqa: BLE001
        return QueryResult(ok=False, error=f"connection error: {e}")

    try:
        cur = conn.cursor()
        cur.execute(sql)
        columns = [d[0] for d in cur.description] if cur.description else []
        fetched = cur.fetchmany(max_rows + 1)          # one extra to detect truncation
        truncated = len(fetched) > max_rows
        rows = [tuple(r) for r in fetched[:max_rows]]
        return QueryResult(ok=True, columns=columns, rows=rows,
                           row_count=len(rows), truncated=truncated)
    except Exception as e:  # noqa: BLE001
        return QueryResult(ok=False, error=f"query error: {e}")
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    sql = ("SELECT TOP 5 region, SUM(line_gmv) AS gmv FROM dbo.order_items "
           "WHERE order_date >= '2026-05-01' AND order_date < '2026-06-01' "
           "GROUP BY region ORDER BY gmv DESC")
    res = run_sql(sql)
    if not res.ok:
        print("FAILED:", res.error)
        raise SystemExit(1)
    print("columns:", res.columns)
    for r in res.rows:
        print("  ", r)
    print(f"rows: {res.row_count} | truncated: {res.truncated}")
    print("executor OK")
