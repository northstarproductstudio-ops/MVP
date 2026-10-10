"""Fenced read-only lookups for the Ask agent.

One SELECT over the snapshot tables only, parsed with sqlglot and rejected if it is
anything else; DuckDB opened in memory with external access disabled; LIMIT forced to
ask.row_cap; timeout ask.timeout_s. Answers are labelled as direct lookups."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import duckdb
import pandas as pd
import sqlglot
from sqlglot import exp

ALLOWED_TABLES = {"sellin_line", "po_line", "stock_snapshot", "market_daily", "extra_value", "sku_master",
                  "sku_crosswalk", "customer_map", "city_map"}
FORBIDDEN_FUNCS = {"read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto", "glob",
                   "read_text", "read_blob", "parquet_scan", "csv_scan", "sniff_csv", "query", "query_table",
                   "getenv", "current_setting"}
LOOKUP_LABEL = "Direct lookup from imported data, not a pipeline figure"


class FencedQueryError(ValueError):
    pass


@dataclass
class LookupResult:
    rows: list[dict[str, Any]]
    row_count: int
    sql: str
    truncated: bool
    latency_ms: int
    label: str = LOOKUP_LABEL


def validate_sql(sql: str, row_cap: int) -> str:
    try:
        stmts = sqlglot.parse(sql, read="duckdb")
    except sqlglot.errors.ParseError as exc:
        raise FencedQueryError(f"could not parse SQL: {exc}") from None
    stmts = [s for s in stmts if s is not None]
    if len(stmts) != 1:
        raise FencedQueryError("exactly one statement is allowed")
    tree = stmts[0]
    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)) and not (
            isinstance(tree, exp.With) or tree.find(exp.Select) is tree):
        raise FencedQueryError("only SELECT queries are allowed")
    for name in ("Insert", "Update", "Delete", "Create", "Drop", "Alter", "Command", "Merge", "Copy",
                 "Pragma", "Set", "Use", "Attach", "Detach", "Install", "Export", "LoadData"):
        bad_cls = getattr(exp, name, None)
        if isinstance(bad_cls, type) and tree.find(bad_cls) is not None:
            raise FencedQueryError(f"{name} is not allowed")
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    for t in tree.find_all(exp.Table):
        name = t.name.lower()
        if t.args.get("db") or t.args.get("catalog"):
            raise FencedQueryError("schema-qualified tables are not allowed")
        if name not in ALLOWED_TABLES and name not in ctes:
            raise FencedQueryError(f"table not allowed: {t.name}")
        if isinstance(t.this, exp.Anonymous) or t.find(exp.Anonymous):
            raise FencedQueryError("table functions are not allowed")
    for f in tree.find_all(exp.Func):
        fname = (f.sql_name() if not isinstance(f, exp.Anonymous) else f.name).lower()
        if fname in FORBIDDEN_FUNCS or (isinstance(f, exp.Anonymous) and f.name.lower() in FORBIDDEN_FUNCS):
            raise FencedQueryError(f"function not allowed: {fname}")
    inner = tree.sql(dialect="duckdb")
    return f"SELECT * FROM ({inner}) AS q LIMIT {int(row_cap) + 1}"


def run_fenced(con: duckdb.DuckDBPyConnection, sql: str, row_cap: int, timeout_s: float) -> LookupResult:
    wrapped = validate_sql(sql, row_cap)
    started = time.monotonic()
    timer = threading.Timer(timeout_s, con.interrupt)
    timer.start()
    try:
        df = con.execute(wrapped).df()
    except duckdb.InterruptException:
        raise FencedQueryError(f"query took longer than {timeout_s}s") from None
    except duckdb.Error as exc:
        raise FencedQueryError(f"query failed: {str(exc).splitlines()[0]}") from None
    finally:
        timer.cancel()
    truncated = len(df) > row_cap
    df = df.head(row_cap)
    return LookupResult(rows=frame_records(df), row_count=len(df), sql=wrapped, truncated=truncated,
                        latency_ms=int((time.monotonic() - started) * 1000))


def frame_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    for rec in df.to_dict("records"):
        clean = {}
        for k, v in rec.items():
            if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NaT:
                clean[k] = None
            elif hasattr(v, "isoformat"):
                clean[k] = v.isoformat()[:10]
            elif hasattr(v, "item"):
                clean[k] = v.item()
            else:
                clean[k] = v
        out.append(clean)
    return out
