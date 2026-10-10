from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.plan_config import PlanConfig  # noqa: E402
from tests.fixtures.synthetic import write_all  # noqa: E402


@pytest.fixture(scope="session")
def cfg() -> PlanConfig:
    return PlanConfig.load()


@pytest.fixture(scope="session")
def sample_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("synthetic")
    write_all(d)
    return d


@pytest.fixture(scope="session")
def snapshot(sample_dir: Path, cfg: PlanConfig):
    from worker.local import import_folder

    snap, _ = import_folder(sorted(sample_dir.iterdir()), cfg)
    return snap


@pytest.fixture(scope="session")
def local_run(snapshot, cfg: PlanConfig):
    from worker.local import run_local

    return run_local(snapshot, cfg.with_overrides({"agent.enabled": False,
                                                   "crosswalk.hard_fail_unmapped": False}), "2026-11")
