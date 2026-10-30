"""Workspace settings kept as company config versions (meta.config_version).

The planning-agent switch writes a new company-scoped version copied from the one runs use
now, so every run still pins the exact config it started with and the change is reversible."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import psycopg
from psycopg.types.json import Jsonb

from core.config import Settings
from core.llm import data_collection_policy
from core.plan_config import PlanConfig


def with_agent(config: dict[str, Any], enabled: bool) -> PlanConfig:
    """``config`` with ``agent.enabled`` set; every other key unchanged."""
    data = PlanConfig(config).as_dict()
    data.setdefault("agent", {})["enabled"] = bool(enabled)
    return PlanConfig(data)


async def effective_config(cur: psycopg.AsyncCursor[Any], company_id: str) -> dict[str, Any]:
    """The config version new runs use: the company's own if it has one, else the global one
    (same order as runs._config_version)."""
    await cur.execute("""SELECT id::text, company_id::text, created_at, note, config FROM meta.config_version
                         WHERE company_id = %s OR company_id IS NULL
                         ORDER BY (company_id IS NULL), created_at DESC LIMIT 1""", (company_id,))
    r = await cur.fetchone()
    if not r:
        raise LookupError("No business configuration has been seeded; run scripts/seed.py.")
    return dict(r)


async def set_planning_agent(cur: psycopg.AsyncCursor[Any], company_id: str, user_id: str,
                             enabled: bool) -> dict[str, Any]:
    cur_cfg = await effective_config(cur, company_id)
    if bool(PlanConfig(cur_cfg["config"]).get("agent.enabled")) == enabled:
        return cur_cfg
    new = with_agent(cur_cfg["config"], enabled)
    await cur.execute(
        """INSERT INTO meta.config_version (company_id, parent_id, config, sha256, created_by, note)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (company_id, cur_cfg["id"], Jsonb(new.as_dict()), new.sha256, user_id,
         f"planning agent {'on' if enabled else 'off'} (Settings)"))
    return await effective_config(cur, company_id)


def settings_view(cfg_row: dict[str, Any], settings: Settings) -> dict[str, Any]:
    return {
        "planning_agent": {
            "enabled": bool(PlanConfig(cfg_row["config"]).get("agent.enabled")),
            "config_version_id": cfg_row["id"],
            "scope": "workspace" if cfg_row["company_id"] else "default",
            "updated_at": cfg_row["created_at"].isoformat() if cfg_row["created_at"] else None,
            "note": cfg_row["note"],
        },
        "model": {
            "id": settings.llm_model,
            "provider": urlparse(settings.openrouter_base_url).hostname or settings.openrouter_base_url,
            "data_collection": data_collection_policy(),
        },
    }
