"""Per-request DuckDB workspace over the chat snapshot (+ current plan lines).

The API stays stateless: snapshot Parquet is downloaded to TMP_DIR (cached by key, hash
verified) and loaded into an in-memory DuckDB; external access is disabled afterwards."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import duckdb
import pandas as pd
import psycopg

from core import duck
from core.domain.snapshot import DATA_TABLES, MAP_TABLES
from core.storage import Store, read_parquet_file, snapshot_key


@dataclass
class Workspace:
    con: duckdb.DuckDBPyConnection
    snapshot_id: str | None
    summary: dict[str, Any]


async def snapshot_row(cur: psycopg.AsyncCursor[Any], session_id: str) -> dict[str, Any] | None:
    await cur.execute("""SELECT s.id::text AS id, s.session_id::text AS session_id, s.table_hashes, s.row_counts,
                         s.as_of, s.date_from, s.date_to, s.dataset_ids
                         FROM core.chat_session c JOIN meta.workspace_snapshot s ON s.id = c.current_snapshot_id
                         WHERE c.id=%s""", (session_id,))
    r = await cur.fetchone()
    return dict(r) if r else None


def _load_frames(store: Store, company_id: str, snap: dict[str, Any]) -> dict[str, pd.DataFrame]:
    frames = {}
    for name in DATA_TABLES + MAP_TABLES:
        key = snapshot_key(company_id, snap["session_id"], snap["id"], name)
        frames[name] = read_parquet_file(store.get_parquet(key, snap["table_hashes"].get(name)))
    return frames


async def open_workspace(snap: dict[str, Any] | None, store: Store, company_id: str,
                         plan_lines: pd.DataFrame | None, tmp_dir: Any, memory_limit: str) -> Workspace:
    """Call with no DB connection held: the snapshot row is read first in its own transaction."""
    con = duck.connect(tmp_dir, memory_limit)
    summary: dict[str, Any] = {"has_data": False}
    if snap:
        frames = await asyncio.to_thread(_load_frames, store, company_id, snap)
        duck.register_frames(con, frames)
        s = frames["sellin_line"]
        summary = {
            "has_data": True, "as_of": str(snap["as_of"]), "date_from": str(snap["date_from"]),
            "date_to": str(snap["date_to"]), "datasets": len(snap["dataset_ids"] or []),
            "rows": snap["row_counts"], "skus": int(s["sku_code"].nunique()) if len(s) else 0,
            "cities": sorted(s["city"].dropna().unique().tolist()) if len(s) else [],
            "platforms": sorted(s["platform"].dropna().unique().tolist()) if len(s) else [],
        }
    if plan_lines is not None and not plan_lines.empty and snap:
        pl = plan_lines.copy()
        last = con.execute("""SELECT sku_code || '|' || city || '|' || platform AS line_id,
                   sum(coalesce(qty_invoiced,0)+coalesce(qty_cancelled,0)) AS q FROM sellin_line
                   WHERE status IN ('invoiced','confirmed') AND NOT is_internal
                   AND date_trunc('month', order_date) = (SELECT date_trunc('month', max(order_date)) FROM sellin_line)
                   GROUP BY 1""").df()
        pl["last_month_sellin"] = pl["line_id"].map(dict(zip(last["line_id"], last["q"], strict=False))).fillna(0)
        pl["flags"] = pl["flags"].map(lambda f: ", ".join(f) if isinstance(f, (list, tuple)) else (f or ""))
        duck.register_frames(con, {"plan_lines": pl})
    duck.lock_down(con)
    return Workspace(con=con, snapshot_id=snap["id"] if snap else None, summary=summary)
