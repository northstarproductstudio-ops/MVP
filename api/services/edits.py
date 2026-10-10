"""apply_edit / undo_edit as service functions (the Ask agent's tools call these; a future
endpoint would call the same functions). One transaction each."""

from __future__ import annotations

from typing import Any

import pandas as pd
import psycopg
from psycopg.types.json import Jsonb

from api.services.plans import load_plan
from core.domain.edits import EditError, plan_edit
from core.domain.reports import overview_facts, template_overview
from core.models.edit import Edit


async def apply_edit(cur: psycopg.AsyncCursor[Any], company_id: str, run_id: str, edit: Edit,
                     user_id: str | None, cfg: dict[str, Any]) -> dict[str, Any]:
    await cur.execute("SELECT status, target_month FROM meta.workflow_run WHERE id=%s FOR UPDATE", (run_id,))
    run = await cur.fetchone()
    if not run or run["status"] != "succeeded":
        raise EditError("Edits apply only to a finished plan.")
    lines, current, vs = await load_plan(cur, run_id)
    await cur.execute("SELECT status FROM meta.run_step WHERE run_id=%s AND step_no=10", (run_id,))
    netting_ran = (await cur.fetchone() or {}).get("status") == "succeeded"
    tm = pd.Timestamp(run["target_month"])
    days = (tm + pd.offsets.MonthBegin(1) - tm).days
    soh, open_po, moq = {}, {}, {}
    if netting_ran:
        await cur.execute("""SELECT sku_code, city, platform, sum(soh_units) AS s FROM core.stock_snapshot
                             WHERE sku_code IS NOT NULL GROUP BY 1,2,3""")
        soh = {(r["sku_code"], r["city"], r["platform"]): float(r["s"] or 0) for r in await cur.fetchall()}
        await cur.execute("""SELECT sku_code, city, platform, sum(qty_open) AS q FROM core.po_line
                             WHERE status='open' AND sku_code IS NOT NULL GROUP BY 1,2,3""")
        open_po = {(r["sku_code"], r["city"], r["platform"]): float(r["q"] or 0) for r in await cur.fetchall()}
        await cur.execute("SELECT sku_code, moq FROM core.sku_master WHERE moq IS NOT NULL")
        moq = {r["sku_code"]: int(r["moq"]) for r in await cur.fetchall()}
    plan = plan_edit(lines, edit, days, (cfg.get("safety") or {}).get("cover_days") or {}, netting_ran,
                     soh, open_po, moq)
    if plan.lines_changed == 0:
        raise EditError("That edit does not change any line (the result rounds to the same numbers).")
    new_version = max(v["version"] for v in vs) + 1
    # overview for the new version
    after = lines.set_index("line_id")
    for r in plan.changes.itertuples():
        after.at[r.line_id, "final_dp"] = r.dp_after
        prev = after.at[r.line_id, "user_change"]
        after.at[r.line_id, "user_change"] = (prev + "; " if prev else "") + f"v{new_version}"
    after = after.reset_index()
    await cur.execute("SELECT payload FROM core.report WHERE run_id=%s", (run_id,))
    rep = (await cur.fetchone())["payload"]
    await cur.execute("SELECT name, value, enabled FROM core.factor WHERE run_id=%s", (run_id,))
    factors = pd.DataFrame([dict(r) for r in await cur.fetchall()])
    facts = overview_facts(after, factors, rep.get("last_month_sellin") or 0, str(run["target_month"]),
                           new_version, [])
    overview = template_overview(facts)
    await cur.execute("UPDATE core.report_version SET is_current=false WHERE run_id=%s AND is_current", (run_id,))
    await cur.execute(
        """INSERT INTO core.report_version (company_id, run_id, version, parent_version_id, edit, note, overview_text,
           created_by, is_current) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,true) RETURNING id::text""",
        (company_id, run_id, new_version, current["id"],
         Jsonb({**edit.model_dump(), "plan_total_after": int(after["final_dp"].sum()),
                "lines_changed": plan.lines_changed}), edit.note or edit.describe(), overview, user_id))
    vid = (await cur.fetchone())["id"]
    # (COPY is not allowed under row-level security for the API role, so a batched INSERT)
    await cur.executemany(
        "INSERT INTO core.dp_line_version (company_id, report_version_id, line_id, dp_before, dp_after, "
        "order_qty_after) VALUES (%s,%s,%s,%s,%s,%s)",
        [(company_id, vid, r.line_id, int(r.dp_before), int(r.dp_after),
          None if pd.isna(r.order_qty_after) else int(r.order_qty_after)) for r in plan.changes.itertuples()])
    total_after = int(after["final_dp"].sum())
    return {"version": new_version, "edit": edit.describe(), "lines_changed": plan.lines_changed,
            "units_before": plan.units_before, "units_after": plan.units_after,
            "plan_total_before": int(lines["final_dp"].sum()), "plan_total_after": total_after,
            "overview": overview}


async def undo_edit(cur: psycopg.AsyncCursor[Any], run_id: str) -> dict[str, Any]:
    await cur.execute("SELECT id::text, version, parent_version_id::text AS parent FROM core.report_version "
                      "WHERE run_id=%s AND is_current FOR UPDATE", (run_id,))
    cur_v = await cur.fetchone()
    if not cur_v or cur_v["parent"] is None:
        raise EditError("Nothing to undo: this is the pipeline's original plan (version 0).")
    await cur.execute("UPDATE core.report_version SET is_current=false WHERE id=%s", (cur_v["id"],))
    await cur.execute("UPDATE core.report_version SET is_current=true WHERE id=%s RETURNING version",
                      (cur_v["parent"],))
    restored = (await cur.fetchone())["version"]
    return {"undone_version": cur_v["version"], "current_version": restored}
