"""SQL used by the worker. Every query filters by company_id taken from the job/run row
(the worker is trusted and enforces tenancy in code)."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

import pandas as pd
import psycopg
from psycopg.types.json import Jsonb

from core.domain.tables import TABLE_COLUMNS

JSON_COLS = {
    "factor": {"scope"},
    "dp_output": {"applied_factors", "bridge"},
    "report": {"payload"},
    "report_version": {"edit"},
    "run_step": {"warnings", "artifact_key", "progress"},
}
# Durable-write discriminators: delete-then-insert by run_id plus these columns.
DISCRIMINATOR = {"factor": "origin", "variable_value": "name"}
DURABLE_COLUMNS = {
    "factor": ["name", "kind", "origin", "scope", "derived_value", "value", "enabled", "basis", "note",
               "source_label", "source_dataset_ids"],
    "variable_value": ["line_id", "name", "value_num", "value_text"],
    "dp_output": ["line_id", "sku_code", "item_name", "plan_category", "city", "platform", "abc", "sb_class",
                  "base", "reference_dp", "final_dp", "applied_factors", "bridge", "reason", "confidence",
                  "flag_for_review", "flags", "safety_stock", "order_qty", "case_size", "batch_key", "sources_used", "factors_off"],
    "report": ["payload"],
    "report_version": ["version", "parent_version_id", "edit", "note", "overview_text", "created_by",
                       "is_current"],
}


def _py(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    if v is pd.NA or v is pd.NaT:
        return None
    if hasattr(v, "item") and not isinstance(v, (list, tuple, str, bytes)):
        try:
            return v.item()
        except (ValueError, AttributeError):
            pass
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    return v


def rows_for(df: pd.DataFrame, cols: list[str], json_cols: set[str]) -> list[tuple]:
    out = []
    for rec in df.reindex(columns=cols).itertuples(index=False):
        row = []
        for c, v in zip(cols, rec, strict=True):
            if c in json_cols:
                if isinstance(v, str):
                    v = json.loads(v)
                row.append(None if v is None or (isinstance(v, float) and pd.isna(v)) else Jsonb(v))
            elif isinstance(v, (list, tuple)):
                row.append(list(v))
            else:
                row.append(_py(v))
        out.append(tuple(row))
    return out


def write_durable(conn: psycopg.Connection, company_id: str, run_id: str, table: str, frames: Iterable[pd.DataFrame]) -> int:
    """Delete-then-insert keyed by run_id (+ discriminator) in one transaction."""
    df = pd.concat(list(frames), ignore_index=True) if frames else pd.DataFrame()
    cols = DURABLE_COLUMNS[table]
    with conn.transaction():
        disc = DISCRIMINATOR.get(table)
        if disc and not df.empty:
            conn.execute(f"DELETE FROM core.{table} WHERE run_id = %s AND company_id = %s AND {disc} = ANY(%s)",
                         (run_id, company_id, sorted(df[disc].dropna().unique().tolist())))
        elif not disc:
            conn.execute(f"DELETE FROM core.{table} WHERE run_id = %s AND company_id = %s", (run_id, company_id))
        if df.empty:
            return 0
        rows = rows_for(df, cols, JSON_COLS.get(table, set()))
        placeholders = ", ".join(["%s"] * (len(cols) + 2))
        with conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO core.{table} (company_id, run_id, {', '.join(cols)}) VALUES ({placeholders})",
                [(company_id, run_id, *r) for r in rows])
    return len(df)


def copy_durable(conn: psycopg.Connection, company_id: str, from_run: str, to_run: str, table: str,
                 values: list[str] | None = None) -> None:
    cols = DURABLE_COLUMNS[table]
    disc = DISCRIMINATOR.get(table)
    cl = ", ".join(cols)
    with conn.transaction():
        if disc and values:
            conn.execute(f"DELETE FROM core.{table} WHERE run_id=%s AND company_id=%s AND {disc}=ANY(%s)",
                         (to_run, company_id, values))
            conn.execute(f"INSERT INTO core.{table} (company_id, run_id, {cl}) SELECT company_id, %s, {cl} "
                         f"FROM core.{table} WHERE run_id=%s AND company_id=%s AND {disc}=ANY(%s)",
                         (to_run, from_run, company_id, values))
        elif disc:
            # dynamic discriminator values (e.g. signal:*): copy those the target run lacks
            conn.execute(f"INSERT INTO core.{table} (company_id, run_id, {cl}) SELECT company_id, %s, {cl} "
                         f"FROM core.{table} s WHERE s.run_id=%s AND s.company_id=%s AND NOT EXISTS ("
                         f"SELECT 1 FROM core.{table} t WHERE t.run_id=%s AND t.{disc}=s.{disc})",
                         (to_run, from_run, company_id, to_run))
        else:
            conn.execute(f"DELETE FROM core.{table} WHERE run_id=%s AND company_id=%s", (to_run, company_id))
            conn.execute(f"INSERT INTO core.{table} (company_id, run_id, {cl}) SELECT company_id, %s, {cl} "
                         f"FROM core.{table} WHERE run_id=%s AND company_id=%s", (to_run, from_run, company_id))


def load_table(conn: psycopg.Connection, table: str, company_id: str, dataset_ids: list[str] | None = None
               ) -> pd.DataFrame:
    cols = list(TABLE_COLUMNS[table])
    if table == "sku_crosswalk":
        cols = [c for c in cols if c != "candidates"] + ["candidates"]
    sel = ", ".join(f"{c}::text" if c in ("dataset_id", "confirmed_by", "confirmed_at", "candidates") else c
                    for c in cols)
    if dataset_ids is not None:
        rows = conn.execute(
            f"SELECT {sel} FROM core.{table} t WHERE company_id=%s AND dataset_id = ANY(%s::uuid[]) "
            f"ORDER BY (SELECT created_at FROM raw.dataset d WHERE d.id = t.dataset_id), row_no",
            (company_id, dataset_ids)).fetchall()
    else:
        rows = conn.execute(f"SELECT {sel} FROM core.{table} WHERE company_id=%s", (company_id,)).fetchall()
    return pd.DataFrame([dict(r) for r in rows], columns=cols)


def jsonb(v: Any) -> Jsonb:
    return Jsonb(v)
