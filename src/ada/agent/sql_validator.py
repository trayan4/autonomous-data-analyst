"""
the hard safety gate for agent-generated SQL.

Before any query touches the warehouse it must pass validate_sql():
  * parses as exactly ONE statement (no stacked / injected queries),
  * is read-only - a SELECT, with no INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/EXEC/MERGE/TRUNCATE,
  * references only allow-listed tables in the dbo schema (from the semantic layer),
  * reports whether it carries a row limit (the executor caps fetches regardless).

Parsing uses sqlglot's AST, not regex, so tricks like comments or stacked
statements can't slip through.
"""
from __future__ import annotations

import importlib.resources as resources
from dataclasses import dataclass, field

import sqlglot
import yaml
from sqlglot import exp

SEMANTIC = resources.files("ada.semantic") / "semantic_layer.yaml"
ALLOWED_SCHEMAS = {"dbo", ""}        # "" = unqualified table name

# expression types that must never appear anywhere in the tree
_FORBIDDEN = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create,
              exp.Alter, exp.Command, exp.Merge, exp.TruncateTable)


@dataclass
class ValidationResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    has_row_limit: bool = False
    pii_blocked: bool = False


def allowed_tables(path=SEMANTIC) -> set[str]:
    spec = yaml.safe_load(path.read_text())
    return {t.lower() for t in spec["tables"].keys()}


def pii_columns(path=SEMANTIC) -> tuple[set[str], set[str]]:
    """Return (pii column names, tables containing PII) from the semantic layer."""
    spec = yaml.safe_load(path.read_text())
    cols, tables = set(), set()
    for tname, t in spec["tables"].items():
        for col, meta in (t.get("columns") or {}).items():
            if isinstance(meta, dict) and meta.get("pii"):
                cols.add(col.lower())
                tables.add(tname.lower())
    return cols, tables


def _has_forbidden(tree: exp.Expression) -> bool:
    if isinstance(tree, _FORBIDDEN):
        return True
    for ftype in _FORBIDDEN:
        for _ in tree.find_all(ftype):
            return True
    return False


def validate_sql(sql: str, allowed: set[str] | None = None,
                 max_rows: int = 10000, allow_pii: bool = False) -> ValidationResult:
    allowed = allowed if allowed is not None else allowed_tables()

    # 1) parse; reject unparseable or multi-statement input
    try:
        statements = [s for s in sqlglot.parse(sql, read="tsql") if s is not None]
    except Exception as e:  # noqa: BLE001
        return ValidationResult(ok=False, reasons=[f"unparseable SQL: {e}"])
    if len(statements) != 1:
        return ValidationResult(
            ok=False, reasons=[f"expected exactly 1 statement, found {len(statements)}"])
    tree = statements[0]

    res = ValidationResult(ok=True)

    # 2) read-only
    if _has_forbidden(tree):
        res.ok = False
        res.reasons.append("only read-only SELECT statements are allowed")
    if tree.find(exp.Select) is None:
        res.ok = False
        res.reasons.append("statement is not a SELECT")

    # 3) allow-listed tables in the dbo schema only (skip CTE names - they aren't tables)
    cte_names = {(c.alias or c.alias_or_name or "").lower() for c in tree.find_all(exp.CTE)}
    cte_names.discard("")
    tables = []
    for t in tree.find_all(exp.Table):
        name = t.name.lower()
        if name in cte_names:
            continue                      # reference to a CTE, not a real table
        schema = (t.db or "").lower()
        tables.append(name)
        if schema not in ALLOWED_SCHEMAS:
            res.ok = False
            res.reasons.append(f"schema not allowed: {schema}")
        if name not in allowed:
            res.ok = False
            res.reasons.append(f"table not in allow-list: {name}")
    res.tables = sorted(set(tables))

    # 4) PII guard: block PII columns (and SELECT * over a PII table) unless entitled
    if not allow_pii:
        pii_cols, pii_tables = pii_columns()
        referenced = sorted({c.name.lower() for c in tree.find_all(exp.Column)
                             if c.name.lower() in pii_cols})
        has_star = _selects_star(tree)
        if referenced:
            res.ok = False
            res.pii_blocked = True
            res.reasons.append(f"requires PII column(s) not permitted for this role: {referenced}")
        elif has_star and (set(res.tables) & pii_tables):
            res.ok = False
            res.pii_blocked = True
            res.reasons.append("SELECT * over a table containing PII is not permitted")

    # 5) row limit present? (TOP or LIMIT) - reported, not required; executor caps anyway
    res.has_row_limit = _has_limit(tree)

    return res


def _selects_star(tree: exp.Expression) -> bool:
    """True only for projection-level SELECT * or t.* (not COUNT(*) etc.)."""
    for sel in tree.find_all(exp.Select):
        for proj in sel.expressions:
            if isinstance(proj, exp.Star):
                return True
            if isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star):
                return True
    return False


def _has_limit(tree: exp.Expression) -> bool:
    if tree.find(exp.Limit) is not None:
        return True
    top_cls = getattr(exp, "Top", None)           # not present in all sqlglot versions
    if top_cls is not None and tree.find(top_cls) is not None:
        return True
    for sel in tree.find_all(exp.Select):         # fallback: select-level limit/top args
        if sel.args.get("limit") or sel.args.get("top"):
            return True
    return False
