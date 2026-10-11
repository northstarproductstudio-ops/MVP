"""Load the session's datasets and mappings for the shared readiness rule (Gaps 7, 8, 10)."""

from __future__ import annotations

from typing import Any

import psycopg

from core.domain.readiness import DatasetState, Readiness, coverage, evaluate


async def dataset_states(cur: psycopg.AsyncCursor[Any], session_id: str) -> list[DatasetState]:
    """Every non-removed dataset of the chat with its fields: the saved mapping when there is
    one, else the import proposal."""
    await cur.execute("""SELECT id::text, file_name, file_type, status, rows_read, data_from, data_to, proposal,
                         mapping_version_id::text AS mv FROM raw.dataset
                         WHERE session_id=%s AND status NOT IN ('removed', 'duplicate', 'failed')
                         ORDER BY created_at""", (session_id,))
    rows = [dict(r) for r in await cur.fetchall()]
    mvs = [r["mv"] for r in rows if r["mv"]]
    saved: dict[str, list[tuple[str, str | None, str]]] = {}
    if mvs:
        await cur.execute("SELECT version_id::text AS mv, source_field, variable, state FROM core.field_mapping "
                          "WHERE version_id = ANY(%s::uuid[])", (mvs,))
        for m in await cur.fetchall():
            saved.setdefault(m["mv"], []).append((m["source_field"], m["variable"], m["state"]))
    out = []
    for r in rows:
        if r["mv"]:
            fields = saved.get(r["mv"], [])
        else:
            fields = [(f.get("source_field"), f.get("variable"), f.get("state") or "needs_decision")
                      for f in ((r["proposal"] or {}).get("fields") or [])]
        out.append(DatasetState(id=r["id"], file_name=r["file_name"], file_type=r["file_type"], status=r["status"],
                                rows=r["rows_read"], data_from=r["data_from"], data_to=r["data_to"], fields=fields))
    return out


async def company_variables(cur: psycopg.AsyncCursor[Any]) -> list[dict[str, Any]]:
    await cur.execute("SELECT name, display_name, help FROM core.variable WHERE company_id IS NOT NULL")
    return [dict(r) for r in await cur.fetchall()]


async def session_readiness(cur: psycopg.AsyncCursor[Any], session_id: str) -> Readiness:
    return evaluate(await dataset_states(cur, session_id), await company_variables(cur))


async def session_coverage(cur: psycopg.AsyncCursor[Any], session_id: str) -> list[dict[str, Any]]:
    return coverage(await dataset_states(cur, session_id))
