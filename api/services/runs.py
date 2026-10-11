"""enqueue_run and run reads. All four triggers (chat, button, retry, schedule) call
enqueue_run; it checks preconditions, then inserts the run and its job in one
transaction. The API never takes advisory locks."""

from __future__ import annotations

import os
import re
from datetime import date
from typing import Any

import pandas as pd
import psycopg
from psycopg.types.json import Jsonb

from core.domain.variables import hard_variables
from worker.steps import SECTIONS, steps_manifest


class PreconditionError(ValueError):
    pass


MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
     "November", "December"], 1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})


def parse_target(text: str | None, as_of: date) -> date:
    """'next month' → month after the as_of month; a named month is used if after as_of."""
    nxt = (pd.Timestamp(as_of) + pd.offsets.MonthBegin(1)).date().replace(day=1)
    if not text or "next" in text.lower():
        return nxt
    m = re.search(r"(\d{4})-(\d{2})", text)
    if m:
        t = date(int(m.group(1)), int(m.group(2)), 1)
    else:
        name = next((k for k in MONTHS if re.search(rf"\b{k}\b", text.lower())), None)
        if name is None:
            return nxt
        mon = MONTHS[name]
        yr = as_of.year + (1 if mon <= as_of.month else 0)
        t = date(yr, mon, 1)
    if t <= as_of.replace(day=1):
        raise PreconditionError(f"{t:%B %Y} is not after the data's last month ({as_of:%B %Y}).")
    return t


async def _workflow_version(cur: psycopg.AsyncCursor[Any], company_id: str) -> str:
    """Global row seeded on deploy (scripts/seed.py); a company row is created if the deploy
    step was skipped, so a run always pins the code version it ran."""
    sha = os.environ.get("GIT_SHA", "dev")
    await cur.execute("SELECT id::text FROM meta.workflow_version WHERE workflow_id='demand_plan' AND git_sha=%s "
                      "ORDER BY (company_id IS NULL) DESC LIMIT 1", (sha,))
    r = await cur.fetchone()
    if r:
        return r["id"]
    await cur.execute("INSERT INTO meta.workflow_version (company_id, workflow_id, git_sha, steps) "
                      "VALUES (%s, 'demand_plan', %s, %s) RETURNING id::text",
                      (company_id, sha, Jsonb(steps_manifest())))
    return (await cur.fetchone())["id"]


async def _config_version(cur: psycopg.AsyncCursor[Any], company_id: str) -> tuple[str, dict[str, Any]]:
    await cur.execute("SELECT id::text, config FROM meta.config_version WHERE company_id = %s OR company_id IS NULL "
                      "ORDER BY (company_id IS NULL), created_at DESC LIMIT 1", (company_id,))
    r = await cur.fetchone()
    if not r:
        raise PreconditionError("No business configuration has been seeded; run scripts/seed.py.")
    return r["id"], r["config"]


async def preconditions(cur: psycopg.AsyncCursor[Any], session_id: str) -> date:
    await cur.execute("SELECT id::text, file_type, mapping_version_id::text AS mv FROM raw.dataset "
                      "WHERE session_id=%s AND status='ready'", (session_id,))
    ready = await cur.fetchall()
    sales = [r for r in ready if r["file_type"] == "erp_sales_orders"]
    if not sales:
        raise PreconditionError("A plan needs at least one ready sales-order file (Zoho). Import and map one first.")
    await cur.execute("SELECT DISTINCT variable FROM core.field_mapping WHERE version_id = ANY(%s::uuid[]) "
                      "AND state IN ('confirmed','recognised','proposed','new_category','custom')",
                      ([r["mv"] for r in sales if r["mv"]],))
    mapped = {r["variable"] for r in await cur.fetchall()}
    missing = [v for v in hard_variables("sellin") if v not in mapped]
    if missing:
        raise PreconditionError("These required fields are not mapped: " + ", ".join(missing))
    await cur.execute("SELECT 1 FROM meta.workflow_run WHERE session_id=%s AND status IN ('queued','running')",
                      (session_id,))
    if await cur.fetchone():
        raise PreconditionError("A plan is already running in this chat; wait for it to finish.")
    await cur.execute("SELECT max(order_date) AS d FROM core.sellin_line s JOIN raw.dataset d ON d.id = s.dataset_id "
                      "WHERE d.session_id=%s AND d.status='ready'", (session_id,))
    r = await cur.fetchone()
    if not r or not r["d"]:
        raise PreconditionError("The sales-order data has no dates.")
    return r["d"]


async def enqueue_run(cur: psycopg.AsyncCursor[Any], company_id: str, session_id: str, target: str | None,
                      scope: dict[str, Any] | None, trigger: str, requested_by: str | None,
                      retry_of: str | None = None, as_of_override: date | None = None,
                      model: str | None = None) -> dict[str, Any]:
    as_of = await preconditions(cur, session_id)
    if as_of_override:
        as_of = as_of_override
    target_month = parse_target(target, as_of)
    wf = await _workflow_version(cur, company_id)
    cv, cfg = await _config_version(cur, company_id)
    await cur.execute(
        """INSERT INTO meta.workflow_run (company_id, session_id, workflow_version_id, config_version_id,
           prompt_version, model, target_month, scope, "trigger", retry_of, requested_by, as_of)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id::text, target_month""",
        (company_id, session_id, wf, cv, cfg.get("agent", {}).get("prompt_version", "v1"),
         model or os.environ.get("LLM_MODEL"), target_month, Jsonb(scope or {}), trigger, retry_of,
         requested_by, as_of if as_of_override else None))
    run = await cur.fetchone()
    await cur.execute("INSERT INTO meta.job_queue (company_id, run_id, session_id, kind) VALUES (%s,%s,%s,%s)",
                      (company_id, run["id"], session_id, "backtest" if trigger == "backtest" else "plan"))
    from api.services.wake import wake_worker

    wake_worker()  # the job row commits with the caller's transaction; the worker polls every second
    return {"run_id": run["id"], "target_month": str(run["target_month"]), "as_of": str(as_of)}


async def run_summary(cur: psycopg.AsyncCursor[Any], run_id: str) -> dict[str, Any]:
    await cur.execute("""SELECT id::text, session_id::text, status, target_month, as_of, "trigger", retry_of::text,
                         created_at, started_at, finished_at, dp_total, order_total, warnings, error
                         FROM meta.workflow_run WHERE id=%s""", (run_id,))
    run = await cur.fetchone()
    if not run:
        raise LookupError("run not found")
    await cur.execute("SELECT step_no, name, status, rows_in, rows_out, summary, warnings, progress, error, "
                      "started_at, finished_at FROM meta.run_step WHERE run_id=%s ORDER BY step_no", (run_id,))
    steps = [dict(s) for s in await cur.fetchall()]
    out = dict(run)
    out["steps"] = steps
    out["sections"] = sections_view(steps)
    await cur.execute("SELECT payload FROM core.report WHERE run_id=%s", (run_id,))
    rep = await cur.fetchone()
    out["report"] = rep["payload"] if rep else None
    await cur.execute("SELECT name, origin, count(*) FILTER (WHERE enabled) AS lines, min(value) AS min, max(value) AS max "
                      "FROM core.factor WHERE run_id=%s GROUP BY 1, 2 ORDER BY 1", (run_id,))
    out["factors"] = [dict(f) for f in await cur.fetchall()]
    return out


def sections_view(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_no = {s["step_no"]: s for s in steps}
    out = []
    for name, nos in SECTIONS:
        st = [by_no.get(n, {"status": "pending"}) for n in nos]
        statuses = {s["status"] for s in st}
        if "failed" in statuses:
            status = "failed"
        elif statuses <= {"succeeded", "reused", "skipped"}:
            status = "done"
        elif statuses & {"running", "succeeded", "reused", "skipped"}:
            status = "running"
        else:
            status = "pending"
        summaries = [s.get("summary") for s in st if s.get("summary")]
        progress = next((s.get("progress") for s in st if s.get("progress") and s["status"] == "running"), None)
        out.append({"section": name, "steps": nos, "status": status, "summary": " · ".join(summaries) or None,
                    "warnings": sorted({w for s in st for w in (s.get("warnings") or [])}), "progress": progress})
    return out


def report_card(run: dict[str, Any], version: dict[str, Any] | None) -> dict[str, Any]:
    rep = run.get("report") or {}
    edit = (version or {}).get("edit") or {}
    dp_total = edit.get("plan_total_after", rep.get("dp_total")) if (version or {}).get("version") else rep.get("dp_total")
    last = rep.get("last_month_sellin")
    change = round((dp_total / last - 1) * 100, 1) if dp_total is not None and last else rep.get("change_pct")
    return {
        "run_id": run["id"], "target_month": str(run["target_month"]),
        "version": version["version"] if version else 0, "dp_total": dp_total,
        "last_month_sellin": last, "change_pct": change,
        "lines": rep.get("lines"), "skus": rep.get("skus"), "agent_fallback": rep.get("agent_fallback"),
        "review": rep.get("review"), "warnings": run.get("warnings") or [],
        "overview": (version or {}).get("overview_text") or rep.get("overview"),
        "order_total": rep.get("order_total"),
    }
