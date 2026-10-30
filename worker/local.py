"""Run the pipeline in memory over local files (no Postgres, no Storage).

Used by ``scripts/plan_offline.py`` for the November plan (Python steps 1–7 + reference
DP, agent off) and by the golden/determinism tests.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from core.domain import entity_resolution as er
from core.domain.importing import import_file
from core.domain.snapshot import Snapshot, build_snapshot
from core.plan_config import PlanConfig
from worker.context import RunContext, add_months, month_start
from worker.executor import ExecutionLog, run_steps


@dataclass
class LocalRun:
    ctx: RunContext
    log: ExecutionLog
    snapshot: Snapshot
    import_warnings: dict[str, list[str]]

    def write(self, table: str) -> pd.DataFrame:
        frames = self.log.writes.get(table, [])
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def import_folder(files: list[Path], cfg: PlanConfig, crosswalk: pd.DataFrame | None = None
                  ) -> tuple[Snapshot, dict[str, list[str]]]:
    data: dict[str, list[pd.DataFrame]] = {}
    sku_master = None
    warns: dict[str, list[str]] = {}
    for f in files:
        parsed, res = import_file(f, f.name, cfg, dataset_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f.name)))
        warns[f.name] = [f"type: {parsed.proposal.file_type}"] + res.warnings + parsed.proposal.warnings
        if res.table == "sku_master":
            sku_master = res.frame
        else:
            data.setdefault(res.table, []).append(res.frame)
        if res.extra is not None and not res.extra.empty:
            data.setdefault("extra_value", []).append(res.extra)
    snap = build_snapshot(
        data, sku_master, crosswalk, er.seed_customer_rules(), er.seed_city_aliases(),
        unmatched_share=cfg.get("city_map.unmatched_share_alert"),
        strip_tokens=cfg.get("brand.strip_tokens"), auto_accept=cfg.get("crosswalk.auto_accept"),
        review_min=cfg.get("crosswalk.review_min"), tie_margin=cfg.get("crosswalk.tie_margin", 0.01))
    return snap, warns


def resolve_target(as_of: pd.Timestamp, target: str | None) -> pd.Timestamp:
    nxt = add_months(month_start(as_of), 1)
    if not target:
        return nxt
    t = month_start(pd.Timestamp(target))
    if t <= month_start(as_of):
        raise ValueError(f"target month {t:%b %Y} must be after the data's as-of month {as_of:%b %Y}")
    return t


def run_local(snapshot: Snapshot, cfg: PlanConfig, target: str | None = None, llm: Any = None,
              as_of: pd.Timestamp | None = None, scope: dict[str, Any] | None = None) -> LocalRun:
    as_of = as_of or snapshot.as_of
    if as_of is None:
        raise ValueError("snapshot has no sell-in")
    ctx = RunContext(run_id=str(uuid.uuid4()), company_id="00000000-0000-0000-0000-000000000000",
                     session_id="local", as_of=pd.Timestamp(as_of), target_month=resolve_target(as_of, target),
                     config=cfg, tables=snapshot.tables, llm=llm,
                     prompt_version=cfg.get("agent.prompt_version"), scope=scope or {})
    elog = run_steps(ctx)
    return LocalRun(ctx=ctx, log=elog, snapshot=snapshot, import_warnings={})
