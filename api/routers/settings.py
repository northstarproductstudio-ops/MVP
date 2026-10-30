"""Workspace settings: the planning-agent switch (pipeline step 8) and the model in use."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.deps import CurrentUser, pool, settings
from api.services import settings as settings_svc
from core.db import tenant_transaction

router = APIRouter(tags=["settings"])


class PlanningAgent(BaseModel):
    enabled: bool


@router.get("/settings")
async def get_settings(user: CurrentUser) -> dict[str, Any]:
    async with tenant_transaction(pool(), user.company_id) as cur:
        try:
            row = await settings_svc.effective_config(cur, user.company_id)
        except LookupError as exc:
            raise HTTPException(409, str(exc)) from None
    return settings_svc.settings_view(row, settings())


@router.put("/settings/planning-agent")
async def put_planning_agent(body: PlanningAgent, user: CurrentUser) -> dict[str, Any]:
    async with tenant_transaction(pool(), user.company_id) as cur:
        try:
            row = await settings_svc.set_planning_agent(cur, user.company_id, user.user_id, body.enabled)
        except LookupError as exc:
            raise HTTPException(409, str(exc)) from None
    return settings_svc.settings_view(row, settings())
