"""demand_plan v1 step registry.

Each step is ``def step(ctx: RunContext) -> StepResult`` registered with
``@step(name, no, reads, writes, config_keys, version, section)``. The runner enforces the
declared reads/writes, timeouts, fingerprints and delete-then-insert durable writes.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

WORKFLOW_ID = "demand_plan"

# MVP-12 sections, in order, with the steps each one ticks.
SECTIONS: list[tuple[str, list[int]]] = [
    ("Reading imported data", [1]),
    ("Cleaning demand data", [2]),
    ("Correcting for availability", [3]),
    ("Classifying demand patterns", [4]),
    ("Fixing accuracy baseline", [5]),
    ("Generating the forecast", [6, 7, 8]),
    ("Building the plan", [9, 10, 11]),
]


def section_for(step_no: int) -> str:
    return next(name for name, steps in SECTIONS if step_no in steps)


@dataclass
class StepResult:
    status: str = "succeeded"  # succeeded | skipped
    rows_in: int = 0
    rows_out: int = 0
    summary: str = ""
    warnings: list[str] = field(default_factory=list)
    artifacts: dict[str, pd.DataFrame] = field(default_factory=dict)
    writes: dict[str, pd.DataFrame] = field(default_factory=dict)
    run_updates: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StepSpec:
    name: str
    no: int
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    config_keys: tuple[str, ...]
    version: str
    fn: Callable[..., StepResult]
    uses_llm: bool = False
    timeout_s: int | None = None

    @property
    def section(self) -> str:
        return section_for(self.no)


_REGISTRY: dict[int, StepSpec] = {}


def step(name: str, no: int, reads: list[str], writes: list[str], config_keys: list[str],
         version: str, uses_llm: bool = False, timeout_s: int | None = None
         ) -> Callable[[Callable[..., StepResult]], Callable[..., StepResult]]:
    def deco(fn: Callable[..., StepResult]) -> Callable[..., StepResult]:
        _REGISTRY[no] = StepSpec(name, no, tuple(reads), tuple(writes), tuple(config_keys), version, fn,
                                 uses_llm, timeout_s)
        return fn
    return deco


STEP_MODULES = [
    "01_validate_inputs", "02_build_history", "03_correct_oos", "04_classify_skus",
    "05_base_forecast", "06_derive_factors", "07_build_evidence_pack", "08_agent_plan",
    "09_validate_agent_output", "10_net_and_round", "11_build_report",
]


def all_steps() -> list[StepSpec]:
    for m in STEP_MODULES:
        importlib.import_module(f"worker.steps.{m}")
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


def steps_manifest() -> list[dict[str, Any]]:
    return [{"no": s.no, "name": s.name, "version": s.version, "reads": list(s.reads),
             "writes": list(s.writes), "config_keys": list(s.config_keys), "section": s.section}
            for s in all_steps()]
