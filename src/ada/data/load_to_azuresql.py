"""
applies schema.sql and bulk-loads data/raw/*.csv into Azure SQL.

Connection comes from the AZSQL_CONNSTR environment variable, e.g.:
  export AZSQL_CONNSTR="$(az keyvault secret show --vault-name <kv> \
      -n ada-sql-connstr --query value -o tsv)"

Requires: pip install pyodbc pandas   (and the ODBC Driver 18 for SQL Server)
Run:      python -m ada.data.load_to_azuresql
"""
from __future__ import annotations

import importlib.resources as resources
import os
import pathlib
import sys

import pandas as pd
import pyodbc

RAW = pathlib.Path("data/raw")
SCHEMA = resources.files("ada.data") / "schema.sql"

# load order respects foreign keys (parents before children)
LOAD_ORDER = ["customers", "products", "orders", "order_items",
              "marketing_spend", "inventory_snapshots"]
CHUNK = 10_000


def connect() -> pyodbc.Connection:
    cs = os.environ.get("AZSQL_CONNSTR")
    if not cs:
        sys.exit("Set AZSQL_CONNSTR (see the header of this file).")
    return pyodbc.connect(cs, autocommit=False)


def apply_schema(con: pyodbc.Connection) -> None:
    statements = [s.strip() for s in SCHEMA.read_text().split(";")]
    cur = con.cursor()
    applied = 0
    for s in statements:
        # skip blank or comment-only fragments
        if not s or all(not ln.strip() or ln.strip().startswith("--")
                        for ln in s.splitlines()):
            continue
        cur.execute(s)
        applied += 1
    con.commit()
    print(f"schema applied ({applied} statements)")


def load_table(con: pyodbc.Connection, table: str) -> None:
    df = pd.read_csv(RAW / f"{table}.csv")
    df = df.astype(object).where(pd.notnull(df), None)   # NaN -> None, keep ints as ints
    cols = list(df.columns)
    sql = (f"INSERT INTO dbo.{table} ({','.join(cols)}) "
           f"VALUES ({','.join('?' * len(cols))})")
    rows = df.values.tolist()
    cur = con.cursor()
    cur.fast_executemany = True
    for i in range(0, len(rows), CHUNK):
        cur.executemany(sql, rows[i:i + CHUNK])
    con.commit()
    n = con.cursor().execute(f"SELECT COUNT(*) FROM dbo.{table}").fetchone()[0]
    print(f"  {table:<20} {n:>9,} rows")


def main() -> None:
    if not RAW.exists() or not any(RAW.glob("*.csv")):
        sys.exit(f"No CSVs in {RAW}. Run generate_data.py first.")
    con = connect()
    apply_schema(con)
    print("loading tables:")
    for t in LOAD_ORDER:
        load_table(con, t)
    con.close()
    print("done.")


if __name__ == "__main__":
    main()
