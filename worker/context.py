"""RunContext: everything a step may touch. Time comes from ``as_of``; snapshot tables and
earlier artifacts are read through ``table``/``artifact``, which the runner restricts to
the step's declared reads."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from core.config import REPO_ROOT
from core.plan_config import PlanConfig


class UndeclaredAccess(RuntimeError):
    pass


def month_start(ts: pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(year=ts.year, month=ts.month, day=1)


def add_months(ts: pd.Timestamp, n: int) -> pd.Timestamp:
    m = ts.year * 12 + (ts.month - 1) + n
    return pd.Timestamp(year=m // 12, month=m % 12 + 1, day=1)


def months_between(a: pd.Timestamp, b: pd.Timestamp) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month)


@dataclass
class RunContext:
    run_id: str
    company_id: str
    session_id: str
    as_of: pd.Timestamp
    target_month: pd.Timestamp
    config: PlanConfig
    tables: dict[str, pd.DataFrame]
    artifacts: dict[str, pd.DataFrame] = field(default_factory=dict)
    llm: Any = None  # core.llm.LLMClient, only for steps declaring uses_llm
    replay: Callable[[str, int, int], dict[str, Any] | None] | None = None
    progress: Callable[[dict[str, Any]], None] | None = None
    template_path: Path = REPO_ROOT / "templates" / "formatRef.xlsx"
    prompt_version: str = "v1"
    scope: dict[str, Any] = field(default_factory=dict)  # e.g. {"platforms": ["blinkit"]}
    _allowed: set[str] | None = None

    # -- declared access ---------------------------------------------------------
    def table(self, name: str) -> pd.DataFrame:
        self._check(name)
        if name not in self.tables:
            raise KeyError(f"snapshot table missing: {name}")
        return self.tables[name]

    def artifact(self, name: str) -> pd.DataFrame:
        self._check(name)
        if name not in self.artifacts:
            raise KeyError(f"artifact missing: {name}")
        return self.artifacts[name]

    def has_artifact(self, name: str) -> bool:
        return name in self.artifacts

    def _check(self, name: str) -> None:
        if self._allowed is not None and name not in self._allowed:
            raise UndeclaredAccess(f"step read undeclared input: {name}")

    def restricted(self, reads: tuple[str, ...]) -> RunContext:
        import copy

        c = copy.copy(self)
        c._allowed = set(reads)
        return c

    # -- months ------------------------------------------------------------------
    @property
    def m1(self) -> pd.Timestamp:
        """Last complete month before or at as_of."""
        nxt = self.as_of + pd.Timedelta(days=1)
        return month_start(self.as_of) if nxt.month != self.as_of.month else add_months(month_start(self.as_of), -1)

    def history_months(self) -> list[pd.Timestamp]:
        """[M1, M2, M3, …] most recent first."""
        n = int(self.config.get("history.months"))
        return [add_months(self.m1, -i) for i in range(n)]

    @property
    def horizon(self) -> int:
        return months_between(self.m1, self.target_month)

    @property
    def days_in_target(self) -> int:
        return int(add_months(self.target_month, 1).__sub__(self.target_month).days)


def line_id(sku: str, city: str, platform: str) -> str:
    return f"{sku}|{city}|{platform}"
