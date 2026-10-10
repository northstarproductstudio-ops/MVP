"""Health checks and scheduled-job endpoints (X-Job-Token, constant-time compare)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api.deps import plan_config, pool, require_job_token, store
from api.services import runs as run_svc
from core.db import system_transaction

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> dict[str, Any]:
    try:
        async with system_transaction(pool()) as cur:
            await cur.execute("SELECT 1 AS ok")
            await cur.fetchone()
    except Exception:  # noqa: BLE001
        raise HTTPException(503, "database unreachable") from None
    if not store().ping():
        raise HTTPException(503, "storage unreachable")
    return {"status": "ready"}


@router.post("/jobs/sweep", dependencies=[Depends(require_job_token)])
async def sweep() -> dict[str, int]:
    async with system_transaction(pool()) as cur:
        await cur.execute(
            """UPDATE meta.job_queue SET status='failed', error='worker heartbeat lost after max attempts'
               WHERE status='running' AND heartbeat_at < now() - interval '2 minutes' AND attempt >= 3
               RETURNING run_id""")
        failed = [r["run_id"] for r in await cur.fetchall()]
        for rid in failed:
            if rid:
                await cur.execute("UPDATE meta.workflow_run SET status='failed', finished_at=now(), "
                                  "error='The worker stopped responding three times; retry the run.' "
                                  "WHERE id=%s AND status IN ('queued','running')", (rid,))
        await cur.execute("""UPDATE meta.job_queue SET status='queued', locked_by=NULL
                             WHERE status='running' AND heartbeat_at < now() - interval '2 minutes' AND attempt < 3
                             RETURNING id""")
        requeued = len(await cur.fetchall())
    return {"requeued": requeued, "failed": len(failed)}


@router.post("/jobs/runs/baseline", dependencies=[Depends(require_job_token)])
async def baseline() -> dict[str, Any]:
    """Scheduled baseline run (built, off by default via config schedule.baseline_enabled)."""
    if not plan_config().get("schedule.baseline_enabled", False):
        return {"status": "disabled"}
    started = []
    async with system_transaction(pool()) as cur:
        await cur.execute("""SELECT DISTINCT ON (company_id) id::text, company_id::text FROM core.chat_session
                             WHERE current_snapshot_id IS NOT NULL ORDER BY company_id, created_at DESC""")
        for s in await cur.fetchall():
            await cur.execute("SELECT set_config('app.company_id', %s, true)", (s["company_id"],))
            try:
                started.append(await run_svc.enqueue_run(cur, s["company_id"], s["id"], None, {}, "schedule", None))
            except run_svc.PreconditionError:
                continue
    return {"status": "queued", "runs": started}
