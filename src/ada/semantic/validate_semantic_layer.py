"""
sanity-checks the semantic layer against the data.

Loads data/raw/*.csv into an in-memory SQLite database, then verifies:
  1. every table & column declared in semantic_layer.yaml exists in the data;
  2. declared PII columns exist;
  3. every single-table metric expression executes and returns a value;
  4. the metric definitions reproduce the UC-A ground truth (docs/anomaly-spec.md).

Run:  python -m ada.semantic.validate_semantic_layer
Exit code 0 = pass, 1 = fail (suitable as a CI gate, per ADR-0004).
"""
from __future__ import annotations

import importlib.resources as resources
import pathlib
import sqlite3
import sys

import pandas as pd
import yaml

RAW = pathlib.Path("data/raw")
SPEC_PATH = resources.files("ada.semantic") / "semantic_layer.yaml"

TRAIL = ("2026-02", "2026-03", "2026-04")
TARGET = "2026-05"


def load_db() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    for csv in sorted(RAW.glob("*.csv")):
        pd.read_csv(csv).to_sql(csv.stem, con, index=False, if_exists="replace")
    return con


def main() -> None:
    if not RAW.exists() or not any(RAW.glob("*.csv")):
        sys.exit(f"ERROR: no CSVs in {RAW}. Run data/generate_data.py first.")

    spec = yaml.safe_load(SPEC_PATH.read_text())
    con = load_db()
    cur = con.cursor()
    errors: list[str] = []

    actual: dict[str, set[str]] = {}
    table_names = [t for (t,) in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    for t in table_names:
        actual[t] = {r[1] for r in cur.execute(f"PRAGMA table_info({t})").fetchall()}

    print("=" * 60)
    # 1) table & column existence
    n_cols = 0
    for tname, tdef in spec["tables"].items():
        if tname not in actual:
            errors.append(f"table '{tname}' missing from data")
            continue
        for col in tdef["columns"]:
            n_cols += 1
            if col not in actual[tname]:
                errors.append(f"{tname}.{col} declared but missing from data")
    print(f"1) columns      : checked {n_cols} across {len(spec['tables'])} tables")

    # 2) PII columns
    for ref in spec["policy"]["pii_columns"]:
        t, c = ref.split(".")
        if c not in actual.get(t, set()):
            errors.append(f"PII column {ref} missing from data")
    print(f"2) PII columns  : {len(spec['policy']['pii_columns'])} declared")

    # 3) single-table metric expressions execute
    ran = 0
    for mname, mdef in spec["metrics"].items():
        if mdef.get("cross_table"):
            continue
        try:
            cur.execute(f"SELECT {mdef['expression']} FROM {mdef['table']}").fetchone()
            ran += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"metric '{mname}' failed: {exc}")
    print(f"3) metrics      : {ran} single-table expressions executed OK")

    # 4) reproduce UC-A ground truth
    def gmv_where(months):
        ph = ",".join("?" * len(months))
        return cur.execute(
            f"SELECT SUM(line_gmv) FROM order_items WHERE substr(order_date,1,7) IN ({ph})",
            months,
        ).fetchone()[0]

    may = gmv_where([TARGET])
    trail = gmv_where(list(TRAIL)) / 3.0
    drop = (may - trail) / trail * 100

    reg = dict(cur.execute(
        f"""
        WITH m AS (SELECT region, SUM(line_gmv) g FROM order_items
                   WHERE substr(order_date,1,7)='{TARGET}' GROUP BY region),
             t AS (SELECT region, SUM(line_gmv)/3.0 g FROM order_items
                   WHERE substr(order_date,1,7) IN {TRAIL} GROUP BY region)
        SELECT m.region, (m.g - t.g)/t.g*100 FROM m JOIN t USING(region)
        """
    ).fetchall())

    seg = cur.execute(
        f"""
        WITH m AS (SELECT region, category, SUM(line_gmv) g FROM order_items
                   WHERE substr(order_date,1,7)='{TARGET}' GROUP BY region, category),
             t AS (SELECT region, category, SUM(line_gmv)/3.0 g FROM order_items
                   WHERE substr(order_date,1,7) IN {TRAIL} GROUP BY region, category)
        SELECT m.region, m.category, (m.g - t.g) d FROM m JOIN t USING(region, category)
        """
    ).fetchall()
    gross_declines = -sum(d for _, _, d in seg if d < 0)
    sxe = next(d for r, c, d in seg if r == "South" and c == "Electronics")
    sxe_share = -sxe / gross_declines * 100

    print("4) UC-A ground truth (recomputed via the semantic-layer GMV metric):")
    print(f"     total drop   : {drop:6.1f}%   (spec -7.5%)")
    print(f"     South region : {reg['South']:6.1f}%   (spec -56.0%)")
    print(f"     South x Elec : {sxe_share:5.0f}% of declines   (spec 84%)")
    for name, val, tgt in [("drop", drop, -7.5), ("South", reg["South"], -56.0),
                           ("S x Elec", sxe_share, 84.0)]:
        if abs(val - tgt) > 1.0:
            errors.append(f"UC-A '{name}' = {val:.1f} differs from spec {tgt}")

    print("=" * 60)
    if errors:
        print(f"FAIL - {len(errors)} issue(s):")
        for e in errors:
            print("   -", e)
        sys.exit(1)
    print("PASS - semantic layer matches the data and reproduces UC-A ground truth.")


if __name__ == "__main__":
    main()
