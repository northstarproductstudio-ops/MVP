"""Data management (Gap 12) and plan readiness (Gap 10).

Files can be added, replaced, removed and cleared at any time — except while a plan in the
same chat is queued or running (409). Storage stays write-once: removing a file only changes
its dataset's status, never its stored bytes, so finished runs keep the snapshot they were
built from. Every change clears the chat's current snapshot and adds a short system note.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile

from api.deps import CurrentUser, pool
from api.routers.imports import data_changed, guard_no_run, ingest_file
from api.services.readiness import session_coverage, session_readiness
from api.services.wake import wake_worker
from core.db import tenant_transaction
from core.domain.adapters import REGISTRY
from core.domain.readiness import file_type_roles

router = APIRouter(tags=["data"])


@router.get("/sessions/{session_id}/datasets")
async def list_datasets(session_id: str, user: CurrentUser, include_removed: bool = False) -> dict[str, Any]:
    async with tenant_transaction(pool(), user.company_id) as cur:
        await cur.execute("SELECT 1 FROM core.chat_session WHERE id=%s", (session_id,))
        if not await cur.fetchone():
            raise HTTPException(404, "chat not found")
        await cur.execute(
            """SELECT d.id::text, d.file_name, d.file_type, d.status, d.rows_read, d.data_from, d.data_to,
                      d.created_at AS uploaded_at, d.removed_at, d.replaces_dataset_id::text,
                      d.warnings, d.error,
                      (SELECT n.id::text FROM raw.dataset n WHERE n.replaces_dataset_id = d.id
                       ORDER BY n.created_at DESC LIMIT 1) AS replaced_by
               FROM raw.dataset d WHERE d.session_id=%s
                 AND (%s OR d.status NOT IN ('removed', 'duplicate'))
               ORDER BY d.created_at""", (session_id, include_removed))
        rows = [dict(r) for r in await cur.fetchall()]
        await cur.execute("SELECT 1 FROM meta.workflow_run WHERE session_id=%s AND status IN ('queued','running')",
                          (session_id,))
        running = bool(await cur.fetchone())
    roles = file_type_roles()
    for r in rows:
        ft = r["file_type"]
        r["type_name"] = REGISTRY[ft].name if ft in REGISTRY else ft
        r["plan_role"] = roles.get(ft, {}).get("plan_role")
    return {"datasets": rows, "plan_running": running}


@router.post("/datasets/{dataset_id}/replace")
async def replace_dataset(dataset_id: str, user: CurrentUser, file: UploadFile = File(...)) -> dict[str, Any]:
    """Upload a new version. It imports as a new dataset; the old one becomes removed only
    when the new one reaches ready (the worker does that), so the plan never loses data."""
    async with tenant_transaction(pool(), user.company_id) as cur:
        old = await _dataset(cur, dataset_id)
        if old["status"] == "removed":
            raise HTTPException(409, "That file was already removed; add the new file instead.")
        await guard_no_run(cur, old["session_id"])
        await cur.execute("UPDATE raw.dataset SET removed_by=%s WHERE id=%s", (user.user_id, dataset_id))
    out = await ingest_file(file, user, old["session_id"], replaces=dataset_id)
    if out["status"] != "uploaded":
        raise HTTPException(409 if out["status"] == "duplicate" else 400, out.get("error") or "upload failed")
    async with tenant_transaction(pool(), user.company_id) as cur:
        await data_changed(cur, user.company_id, old["session_id"],
                           f"Replacing {old['file_name']} with {out['file_name']}")
    wake_worker()
    return out


@router.delete("/datasets/{dataset_id}")
async def remove_dataset(dataset_id: str, user: CurrentUser) -> dict[str, Any]:
    async with tenant_transaction(pool(), user.company_id) as cur:
        d = await _dataset(cur, dataset_id)
        await guard_no_run(cur, d["session_id"])
        if d["status"] == "removed":
            return {"dataset_id": dataset_id, "status": "removed"}
        await cur.execute("UPDATE raw.dataset SET status='removed', removed_at=now(), removed_by=%s WHERE id=%s",
                          (user.user_id, dataset_id))
        await cur.execute("UPDATE meta.job_queue SET status='cancelled' WHERE dataset_id=%s AND status='queued'",
                          (dataset_id,))
        await data_changed(cur, user.company_id, d["session_id"], f"Removed {d['file_name']}")
    return {"dataset_id": dataset_id, "status": "removed"}


@router.post("/sessions/{session_id}/datasets/clear")
async def clear_datasets(session_id: str, user: CurrentUser) -> dict[str, Any]:
    """Mark every dataset in the chat removed (the UI asks for confirmation first)."""
    async with tenant_transaction(pool(), user.company_id) as cur:
        await cur.execute("SELECT 1 FROM core.chat_session WHERE id=%s", (session_id,))
        if not await cur.fetchone():
            raise HTTPException(404, "chat not found")
        await guard_no_run(cur, session_id)
        await cur.execute("UPDATE raw.dataset SET status='removed', removed_at=now(), removed_by=%s "
                          "WHERE session_id=%s AND status NOT IN ('removed', 'duplicate') RETURNING id",
                          (user.user_id, session_id))
        n = len(await cur.fetchall())
        await cur.execute("UPDATE meta.job_queue SET status='cancelled' WHERE session_id=%s AND status='queued' "
                          "AND kind IN ('import', 'materialize')", (session_id,))
        if n:
            await data_changed(cur, user.company_id, session_id, f"Cleared all data ({n} files removed)")
    return {"removed": n}


@router.get("/sessions/{session_id}/readiness")
async def readiness(session_id: str, user: CurrentUser) -> dict[str, Any]:
    """What the plan needs and what is mapped. Uses exactly the rule ``preconditions()`` uses."""
    async with tenant_transaction(pool(), user.company_id) as cur:
        await cur.execute("SELECT 1 FROM core.chat_session WHERE id=%s", (session_id,))
        if not await cur.fetchone():
            raise HTTPException(404, "chat not found")
        r = await session_readiness(cur, session_id)
        cov = await session_coverage(cur, session_id)
    return {**r.as_dict(), "coverage": cov}


async def _dataset(cur: Any, dataset_id: str) -> dict[str, Any]:
    await cur.execute("SELECT id::text, session_id::text, file_name, status FROM raw.dataset WHERE id=%s",
                      (dataset_id,))
    d = await cur.fetchone()
    if not d:
        raise HTTPException(404, "file not found")
    return dict(d)
