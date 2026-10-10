"""Read plan lines for a run at a report version (version 0 + edits along the parent chain)."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import psycopg

from core.domain.reports import current_lines

DP_SELECT = """SELECT line_id, sku_code, item_name, plan_category, city, platform, abc, sb_class, base::float AS base,
               reference_dp, final_dp, applied_factors, bridge, reason, confidence, flag_for_review, flags,
               safety_stock, order_qty, case_size FROM core.dp_output WHERE run_id=%s ORDER BY line_id"""


async def versions(cur: psycopg.AsyncCursor[Any], run_id: str) -> list[dict[str, Any]]:
    await cur.execute("SELECT id::text, version, parent_version_id::text AS parent, edit, note, overview_text, "
                      "created_at, is_current FROM core.report_version WHERE run_id=%s ORDER BY version", (run_id,))
    return [dict(r) for r in await cur.fetchall()]


def chain_to(vs: list[dict[str, Any]], version: int | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    by_id = {v["id"]: v for v in vs}
    if version is None:
        target = next((v for v in vs if v["is_current"]), vs[0] if vs else None)
    else:
        target = next((v for v in vs if v["version"] == version), None)
    if target is None:
        raise LookupError("report version not found")
    chain = []
    v = target
    while v is not None:
        chain.append(v)
        v = by_id.get(v["parent"]) if v["parent"] else None
    return target, list(reversed(chain))


async def load_plan(cur: psycopg.AsyncCursor[Any], run_id: str, version: int | None = None
                    ) -> tuple[pd.DataFrame, dict[str, Any], list[dict[str, Any]]]:
    vs = await versions(cur, run_id)
    if not vs:
        raise LookupError("this run has no report yet")
    target, chain = chain_to(vs, version)
    await cur.execute(DP_SELECT, (run_id,))
    dp = pd.DataFrame([dict(r) for r in await cur.fetchall()])
    for c in ("applied_factors", "bridge"):
        if c in dp:
            dp[c] = dp[c].map(lambda v: json.dumps(v) if not isinstance(v, str) else v)
    ids = [v["id"] for v in chain if v["version"] > 0]
    edits = pd.DataFrame()
    if ids:
        await cur.execute("""SELECT rv.version, l.line_id, l.dp_after, l.order_qty_after, rv.note
                             FROM core.dp_line_version l JOIN core.report_version rv ON rv.id = l.report_version_id
                             WHERE l.report_version_id = ANY(%s::uuid[]) ORDER BY rv.version""", (ids,))
        edits = pd.DataFrame([dict(r) for r in await cur.fetchall()])
    lines = current_lines(dp, edits) if not dp.empty else dp
    return lines, target, vs
