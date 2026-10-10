"""Runs: summary, SSE progress, retry/cancel/back-test, plan and reasoning rows, versions,
undo and exports."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

import pandas as pd
from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import Response

from api.deps import CurrentUser, plan_config, pool
from api.services import runs as run_svc
from api.services.ask_agent import event
from api.services.edits import undo_edit
from api.services.plans import load_plan
from api.sse import sse_response
from core.config import REPO_ROOT
from core.db import tenant_transaction
from core.domain.edits import EditError
from core.domain.reports import export_plan_xlsx, export_reasoning_xlsx, reasoning_rows
from worker.steps import section_for

router = APIRouter(tags=["runs"])


@router.get("/runs/{run_id}")
async def get_run(run_id: str, user: CurrentUser) -> dict[str, Any]:
    async with tenant_transaction(pool(), user.company_id) as cur:
        try:
            s = await run_svc.run_summary(cur, run_id)
        except LookupError:
            raise HTTPException(404, "run not found") from None
        from api.services.plans import versions

        vs = await versions(cur, run_id)
    cur_v = next((v for v in vs if v["is_current"]), None)
    s["report_card"] = run_svc.report_card(s, cur_v) if s["status"] == "succeeded" else None
    s["versions"] = [{"version": v["version"], "note": v["note"], "is_current": v["is_current"],
                      "created_at": v["created_at"]} for v in vs]
    return s


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, user: CurrentUser,
                     last_event_id: str | None = Header(default=None, alias="Last-Event-ID")):  # noqa: ANN201
    """Polls run_step once a second with a fresh short transaction; holds no connection
    between polls. Reconnects resume from Last-Event-ID (a monotonically increasing seq)."""
    start_seq = int(last_event_id.split("-")[-1]) if last_event_id and last_event_id.split("-")[-1].isdigit() else 0

    async def gen() -> AsyncIterator[dict[str, Any]]:
        seq = 0
        seen: dict[int, tuple] = {}
        session_id = ""
        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline:
            async with tenant_transaction(pool(), user.company_id) as cur:
                try:
                    s = await run_svc.run_summary(cur, run_id)
                except LookupError:
                    return
                from api.services.plans import versions

                vs = await versions(cur, run_id) if s["status"] == "succeeded" else []
            session_id = s["session_id"]
            out = []
            for st in s["steps"]:
                key = (st["status"], json.dumps(st.get("progress"), default=str))
                if seen.get(st["step_no"]) != key:
                    seen[st["step_no"]] = key
                    out.append(event("step", session_id, run_id=run_id, step_no=st["step_no"], name=st["name"],
                                     section=section_for(st["step_no"]), status=st["status"], rows=st["rows_out"],
                                     summary=st["summary"], warnings=st["warnings"]))
                    if st.get("progress") and st["status"] == "running":
                        out.append(event("agent_progress", session_id, run_id=run_id, **st["progress"]))
            if s["status"] == "succeeded":
                cur_v = next((v for v in vs if v["is_current"]), None)
                out.append(event("report_card", session_id, **run_svc.report_card(s, cur_v)))
            elif s["status"] in ("failed", "cancelled"):
                failed = next((st for st in s["steps"] if st["status"] == "failed"), None)
                out.append(event("run_failed", session_id, run_id=run_id, step=failed["name"] if failed else None,
                                 message=s["error"] or s["status"], retry_allowed=s["status"] == "failed"))
            for ev in out:
                seq += 1
                ev["id"] = f"{run_id}-{seq}"
                if seq > start_seq:
                    yield ev
            if s["status"] in ("succeeded", "failed", "cancelled"):
                return
            await asyncio.sleep(1)

    return sse_response(gen())


@router.post("/runs/{run_id}/retry")
async def retry(run_id: str, user: CurrentUser) -> dict[str, Any]:
    async with tenant_transaction(pool(), user.company_id) as cur:
        await cur.execute("SELECT session_id::text, status, target_month, scope FROM meta.workflow_run WHERE id=%s",
                          (run_id,))
        r = await cur.fetchone()
        if not r:
            raise HTTPException(404, "run not found")
        if r["status"] not in ("failed", "cancelled"):
            raise HTTPException(409, "Only failed or cancelled runs can be retried")
        try:
            return await run_svc.enqueue_run(cur, user.company_id, r["session_id"], f"{r['target_month']:%Y-%m}",
                                             r["scope"], "retry", user.user_id, retry_of=run_id)
        except run_svc.PreconditionError as exc:
            raise HTTPException(409, str(exc)) from None


@router.post("/runs/{run_id}/cancel")
async def cancel(run_id: str, user: CurrentUser) -> dict[str, Any]:
    async with tenant_transaction(pool(), user.company_id) as cur:
        await cur.execute("UPDATE meta.job_queue SET cancel_requested=true WHERE run_id=%s AND status='running'",
                          (run_id,))
        await cur.execute("UPDATE meta.job_queue SET status='cancelled' WHERE run_id=%s AND status='queued' "
                          "RETURNING id", (run_id,))
        if await cur.fetchone():
            await cur.execute("UPDATE meta.workflow_run SET status='cancelled', finished_at=now() WHERE id=%s",
                              (run_id,))
    return {"run_id": run_id, "cancel_requested": True}


@router.post("/runs/{run_id}/backtest")
async def backtest(run_id: str, user: CurrentUser) -> dict[str, Any]:
    """Re-run steps 1–9 with as_of at the end of M2 to plan M1, scored against actual M1."""
    async with tenant_transaction(pool(), user.company_id) as cur:
        await cur.execute("SELECT session_id::text, as_of FROM meta.workflow_run WHERE id=%s", (run_id,))
        r = await cur.fetchone()
        if not r:
            raise HTTPException(404, "run not found")
        try:
            as_of = await run_svc.preconditions(cur, r["session_id"])
        except run_svc.PreconditionError as exc:
            raise HTTPException(409, str(exc)) from None
        ts = pd.Timestamp(as_of)
        m1 = ts.to_period("M").to_timestamp() if (ts + pd.Timedelta(days=1)).month != ts.month \
            else (ts.to_period("M") - 1).to_timestamp()
        bt_as_of = (m1 - pd.Timedelta(days=1)).date()
        await cur.execute("UPDATE meta.workflow_run SET status=status WHERE id=%s", (run_id,))
        try:
            out = await run_svc.enqueue_run(cur, user.company_id, r["session_id"], f"{m1:%Y-%m}",
                                            {"backtest_of": run_id}, "backtest", user.user_id,
                                            as_of_override=bt_as_of)
        except run_svc.PreconditionError as exc:
            raise HTTPException(409, str(exc)) from None
    return out


@router.get("/runs/{run_id}/plan")
async def plan_rows(run_id: str, user: CurrentUser, version: int | None = None,
                    page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
    lines, ver = await _lines(run_id, user, version)
    plan = (lines.groupby(["sku_code", "item_name", "plan_category", "city"], dropna=False, as_index=False)
            .agg(final_dp=("final_dp", "sum"), pipeline_dp=("pipeline_dp", "sum"),
                 order_qty=("order_qty", lambda s: None if s.isna().all() else int(s.fillna(0).sum())),
                 sb_class=("sb_class", "first"))
            .sort_values(["plan_category", "sku_code", "city"]))
    return _page(plan, page, page_size, ver)


@router.get("/runs/{run_id}/reasoning")
async def reasoning(run_id: str, user: CurrentUser, version: int | None = None,
                    page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
    lines, ver = await _lines(run_id, user, version)
    rows = reasoning_rows(lines, ver["version"])
    return _page(rows, page, page_size, ver)


@router.get("/runs/{run_id}/versions")
async def list_versions(run_id: str, user: CurrentUser) -> list[dict[str, Any]]:
    from api.services.plans import versions

    async with tenant_transaction(pool(), user.company_id) as cur:
        return [{k: v for k, v in x.items() if k != "parent"} for x in await versions(cur, run_id)]


@router.post("/runs/{run_id}/undo")
async def undo(run_id: str, user: CurrentUser) -> dict[str, Any]:
    async with tenant_transaction(pool(), user.company_id) as cur:
        try:
            return await undo_edit(cur, run_id)
        except EditError as exc:
            raise HTTPException(409, str(exc)) from None


@router.get("/runs/{run_id}/export/plan.xlsx")
async def export_plan(run_id: str, user: CurrentUser) -> Response:
    lines, ver = await _lines(run_id, user, None)
    async with tenant_transaction(pool(), user.company_id) as cur:
        await cur.execute("SELECT target_month, snapshot_id::text AS sid, session_id::text FROM meta.workflow_run "
                          "WHERE id=%s", (run_id,))
        run = await cur.fetchone()
        await cur.execute("SELECT name, kind, origin, value::float AS value, enabled, basis FROM core.factor "
                          "WHERE run_id=%s", (run_id,))
        factors = pd.DataFrame([dict(r) for r in await cur.fetchall()])
        await cur.execute("""SELECT s.sku_code, s.city, date_trunc('month', s.order_date)::date AS month,
                             sum(coalesce(s.qty_invoiced,0)+coalesce(s.qty_cancelled,0))::float AS q, max(s.mrp)::float AS mrp
                             FROM core.sellin_line s JOIN raw.dataset d ON d.id = s.dataset_id
                             WHERE d.session_id=%s AND d.status='ready' AND s.status IN ('invoiced','confirmed')
                               AND NOT s.is_internal GROUP BY 1,2,3""", (run["session_id"],))
        actuals = pd.DataFrame([dict(r) for r in await cur.fetchall()])
        await cur.execute("SELECT line_id, value_text AS share_basis FROM core.variable_value "
                          "WHERE run_id=%s AND name='city_share'", (run_id,))
        basis = {r["line_id"]: r["share_basis"] for r in await cur.fetchall()}
    cfg = plan_config()
    tm = pd.Timestamp(run["target_month"])
    if not actuals.empty:
        actuals["month"] = pd.to_datetime(actuals["month"])
        actuals = actuals[actuals["month"] < tm].copy()
        months = sorted(actuals["month"].unique())[-int(cfg.get("history.months")):]
        actuals = actuals[actuals["month"].isin(months)]
    lines["share_basis"] = lines["line_id"].map(basis)
    mrp = actuals.groupby("sku_code")["mrp"].max().to_dict() if not actuals.empty else {}
    meta = {"target_month": f"{tm:%Y-%m-%d}", "version": ver["version"], "run_id": run_id}
    data = await asyncio.to_thread(export_plan_xlsx, REPO_ROOT / "templates" / "formatRef.xlsx", lines,
                                   actuals, factors, meta, cfg.cities, mrp)
    return _xlsx(data, f"demand-plan-{tm:%Y-%m}-v{ver['version']}.xlsx")


@router.get("/runs/{run_id}/export/reasoning.xlsx")
async def export_reasoning(run_id: str, user: CurrentUser) -> Response:
    lines, ver = await _lines(run_id, user, None)
    tm = await _target(run_id, user)
    meta = {"target_month": f"{tm:%Y-%m-%d}", "version": ver["version"], "run_id": run_id}
    data = export_reasoning_xlsx(reasoning_rows(lines, ver["version"]), meta)
    return _xlsx(data, f"reasoning-{tm:%Y-%m}-v{ver['version']}.xlsx")


@router.get("/runs/{run_id}/export/reasoning.csv")
async def export_reasoning_csv(run_id: str, user: CurrentUser) -> Response:
    lines, ver = await _lines(run_id, user, None)
    tm = await _target(run_id, user)
    csv = reasoning_rows(lines, ver["version"]).to_csv(index=False)
    return Response(csv, media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="reasoning-{tm:%Y-%m}-v{ver["version"]}.csv"'})


async def _lines(run_id: str, user: Any, version: int | None) -> tuple[pd.DataFrame, dict[str, Any]]:
    async with tenant_transaction(pool(), user.company_id) as cur:
        try:
            lines, ver, _ = await load_plan(cur, run_id, version)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
    return lines, ver


async def _target(run_id: str, user: Any) -> pd.Timestamp:
    async with tenant_transaction(pool(), user.company_id) as cur:
        await cur.execute("SELECT target_month FROM meta.workflow_run WHERE id=%s", (run_id,))
        return pd.Timestamp((await cur.fetchone())["target_month"])


def _page(df: pd.DataFrame, page: int, size: int, ver: dict[str, Any]) -> dict[str, Any]:
    total = len(df)
    part = df.iloc[(page - 1) * size: page * size]
    rows = json.loads(part.to_json(orient="records", date_format="iso"))
    return {"version": ver["version"], "total": total, "page": page, "page_size": size, "rows": rows}


def _xlsx(data: bytes, name: str) -> Response:
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
