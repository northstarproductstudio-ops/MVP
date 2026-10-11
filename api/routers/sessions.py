"""Chat sessions, Ask messages (SSE) and suggested questions."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.deps import CurrentUser, plan_config, pool, settings, store
from api.services.ask_agent import AskTurn
from api.services.plans import load_plan
from api.services.suggestions import candidate_facts, phrase, present_tables
from api.services.workspace import open_workspace, snapshot_row
from api.sse import sse_response
from core.db import tenant_transaction
from core.llm import LLMClient

router = APIRouter(tags=["sessions"])


class NewSession(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class NewMessage(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


@router.post("/sessions")
async def create_session(body: NewSession, user: CurrentUser) -> dict[str, Any]:
    async with tenant_transaction(pool(), user.company_id) as cur:
        await cur.execute("INSERT INTO core.chat_session (company_id, title, created_by) VALUES (%s,%s,%s) "
                          "RETURNING id::text, title, created_at", (user.company_id, body.title, user.user_id))
        return dict(await cur.fetchone())


@router.get("/sessions")
async def list_sessions(user: CurrentUser) -> list[dict[str, Any]]:
    async with tenant_transaction(pool(), user.company_id) as cur:
        await cur.execute("SELECT id::text, title, created_at, current_run_id::text AS current_run_id "
                          "FROM core.chat_session ORDER BY created_at DESC LIMIT 50")
        return [dict(r) for r in await cur.fetchall()]


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, user: CurrentUser) -> dict[str, Any]:
    async with tenant_transaction(pool(), user.company_id) as cur:
        await cur.execute("SELECT id::text, title, created_at, current_run_id::text AS current_run_id, "
                          "current_snapshot_id::text AS current_snapshot_id FROM core.chat_session WHERE id=%s",
                          (session_id,))
        s = await cur.fetchone()
        if not s:
            raise HTTPException(404, "chat not found")
        await cur.execute("""SELECT id::text, file_name, file_size, file_type, type_score, type_source, status,
                             rows_read, warnings, progress, error, created_at, data_from, data_to FROM raw.dataset
                             WHERE session_id=%s AND status <> 'removed' ORDER BY created_at""", (session_id,))
        datasets = [dict(r) for r in await cur.fetchall()]
        await cur.execute("SELECT id::text, role, content, evidence, tool_calls, created_at FROM core.chat_message "
                          "WHERE session_id=%s AND role <> 'tool' ORDER BY created_at", (session_id,))
        messages = [dict(r) for r in await cur.fetchall()]
        await cur.execute("SELECT id::text, status, target_month, created_at, dp_total FROM meta.workflow_run "
                          "WHERE session_id=%s ORDER BY created_at", (session_id,))
        runs = [dict(r) for r in await cur.fetchall()]
    return {**dict(s), "datasets": datasets, "messages": messages, "runs": runs}


@router.post("/sessions/{session_id}/messages")
async def post_message(session_id: str, body: NewMessage, user: CurrentUser):  # noqa: ANN201
    turn = AskTurn(pool(), store(), settings(), plan_config(), user.company_id, user.user_id, session_id)
    try:
        async with tenant_transaction(pool(), user.company_id) as cur:
            await cur.execute("SELECT 1 FROM core.chat_session WHERE id=%s", (session_id,))
            if not await cur.fetchone():
                raise HTTPException(404, "chat not found")
    except LookupError:
        raise HTTPException(404, "chat not found") from None
    return sse_response(turn.run(body.content))


@router.get("/sessions/{session_id}/suggestions")
async def suggestions(session_id: str, user: CurrentUser) -> dict[str, Any]:
    cfg = plan_config()
    async with tenant_transaction(pool(), user.company_id) as cur:
        snap = await snapshot_row(cur, session_id)
        await cur.execute("SELECT current_run_id::text AS r FROM core.chat_session WHERE id=%s", (session_id,))
        row = await cur.fetchone()
        lines = None
        if row and row["r"]:
            try:
                lines, _, _ = await load_plan(cur, row["r"])
            except LookupError:
                lines = None
    if snap is None:
        return {"plan": [], "why": [], "data": [], "has_data": False}
    ws = await open_workspace(snap, store(), user.company_id, lines, settings().tmp_dir,
                              settings().duckdb_memory_limit)
    try:
        facts = candidate_facts(ws.con, lines is not None, cfg.get("suggest.move_pct"), cfg.get("suggest.osa_alert"),
                                cfg.get("fill.threshold"))
        has_sales = "sellin_line" in present_tables(ws.con)
    finally:
        ws.con.close()
    s = settings()
    llm = LLMClient(api_key=s.openrouter_api_key.get_secret_value(), model=s.llm_model,
                    fallback_model=s.llm_fallback_model, base_url=s.openrouter_base_url, timeout_s=20)
    out = await asyncio.to_thread(phrase, llm, facts, lines is not None, int(cfg.get("suggest.max_questions")),
                                  session_id, user.company_id, has_sales, cfg.get("ask.prompt_version", "v1"))
    return {**out, "has_data": True, "workspace": ws.summary}
