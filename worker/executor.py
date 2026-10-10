"""Generic step executor enforcing the runner contract.

* reads/writes only the declared tables and artifacts
* no LLM except in steps declaring ``uses_llm`` (8 and 11)
* timeout per step (``step.timeout_s`` or the step's own)
* fingerprint = sha256(step version ‖ config subset ‖ input fingerprints ‖ as_of/target)
* persistence and reuse are delegated to hooks, so the same executor runs offline
  (in memory) and in the worker (Postgres + Storage)
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutTimeout
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from core.domain.snapshot import table_hash
from core.logging import get_logger, step_var
from worker.context import RunContext
from worker.steps import StepResult, StepSpec, all_steps

log = get_logger(__name__)


class StepTimeout(RuntimeError):
    pass


class ContractViolation(RuntimeError):
    pass


class Stopped(RuntimeError):
    """Shutdown or cancel requested between steps."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class Hooks:
    on_start: Callable[[StepSpec, str], None] = lambda s, fp: None
    on_done: Callable[[StepSpec, StepResult, str, dict[str, str]], None] = lambda s, r, fp, keys: None
    on_reused: Callable[[StepSpec, str, dict[str, Any]], None] = lambda s, fp, prev: None
    find_reusable: Callable[[StepSpec, str], dict[str, Any] | None] = lambda s, fp: None
    load_artifacts: Callable[[dict[str, str]], dict[str, pd.DataFrame]] = lambda keys: {}
    save_artifacts: Callable[[StepSpec, dict[str, pd.DataFrame]], dict[str, str]] = lambda s, a: {}
    should_stop: Callable[[], str | None] = lambda: None


@dataclass
class ExecutionLog:
    results: dict[int, StepResult] = field(default_factory=dict)
    fingerprints: dict[int, str] = field(default_factory=dict)
    writes: dict[str, list[pd.DataFrame]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    run_updates: dict[str, Any] = field(default_factory=dict)
    durations: dict[int, float] = field(default_factory=dict)


def fingerprint(spec: StepSpec, ctx: RunContext, input_fps: dict[str, str]) -> str:
    payload = {
        "step": spec.name, "version": spec.version,
        "config": ctx.config.subset(list(spec.config_keys)),
        "inputs": {k: input_fps.get(k) for k in sorted(spec.reads)},
        "as_of": f"{ctx.as_of:%Y-%m-%d}", "target": f"{ctx.target_month:%Y-%m-%d}",
        "prompt_version": ctx.prompt_version if spec.uses_llm else None,
        "scope": ctx.scope,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def run_steps(ctx: RunContext, hooks: Hooks | None = None, steps: list[StepSpec] | None = None,
              default_timeout: int | None = None) -> ExecutionLog:
    hooks = hooks or Hooks()
    steps = steps or all_steps()
    timeout_default = default_timeout or int(ctx.config.get("step.timeout_s"))
    elog = ExecutionLog()
    input_fps: dict[str, str] = {name: table_hash(df) for name, df in ctx.tables.items()}
    for spec in steps:
        reason = hooks.should_stop()
        if reason:
            raise Stopped(reason)
        token = step_var.set(f"{spec.no:02d}_{spec.name}")
        try:
            fp = fingerprint(spec, ctx, input_fps)
            elog.fingerprints[spec.no] = fp
            prev = hooks.find_reusable(spec, fp)
            if prev is not None:
                arts = hooks.load_artifacts(prev.get("artifact_keys") or {})
                if set(arts) >= {w for w in spec.writes if w in (prev.get("artifact_keys") or {})}:
                    ctx.artifacts.update(arts)
                    for name, df in arts.items():
                        input_fps[name] = table_hash(df)
                    hooks.on_reused(spec, fp, prev)
                    log.info("step reused from run %s", prev.get("run_id"))
                    continue
            hooks.on_start(spec, fp)
            started = time.monotonic()
            result = _call(spec, ctx, spec.timeout_s or timeout_default)
            elog.durations[spec.no] = time.monotonic() - started
            _check_contract(spec, result)
            ctx.artifacts.update(result.artifacts)
            for name, df in result.artifacts.items():
                input_fps[name] = table_hash(df)
            keys = hooks.save_artifacts(spec, result.artifacts)
            for table, df in result.writes.items():
                elog.writes.setdefault(table, []).append(df)
            elog.results[spec.no] = result
            elog.warnings.extend(result.warnings)
            elog.run_updates.update(result.run_updates)
            hooks.on_done(spec, result, fp, keys)
            log.info("step %s %s: %s", spec.no, result.status, result.summary)
        finally:
            step_var.reset(token)
    return elog


def _call(spec: StepSpec, ctx: RunContext, timeout_s: int) -> StepResult:
    sctx = ctx.restricted(spec.reads)
    if not spec.uses_llm:
        sctx.llm = None
        sctx.replay = None
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"step{spec.no}") as ex:
        fut = ex.submit(spec.fn, sctx)
        try:
            return fut.result(timeout=timeout_s)
        except FutTimeout as exc:
            raise StepTimeout(f"step {spec.no} {spec.name} exceeded {timeout_s}s") from exc


DURABLE_TABLES = {"factor", "variable_value", "dp_output", "report", "report_version"}


def _check_contract(spec: StepSpec, result: StepResult) -> None:
    declared = set(spec.writes)
    undeclared = (set(result.artifacts) | set(result.writes)) - declared
    if undeclared:
        raise ContractViolation(f"step {spec.no} wrote undeclared outputs: {sorted(undeclared)}")
    bad = set(result.writes) - DURABLE_TABLES
    if bad:
        raise ContractViolation(f"step {spec.no} durable writes to non-plan tables: {sorted(bad)}")
