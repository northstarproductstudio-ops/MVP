"""Business configuration (config/demand_plan.yaml, pinned per run via meta.config_version)."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from core.config import REPO_ROOT

CONFIG_PATH = REPO_ROOT / "config" / "demand_plan.yaml"
SEED_DIR = REPO_ROOT / "config" / "seed"


class PlanConfig:
    """Read-only view over the config dict with dotted-key access."""

    def __init__(self, data: dict[str, Any]):
        self._data = copy.deepcopy(data)

    @classmethod
    def load(cls, path: Path | None = None) -> PlanConfig:
        with open(path or CONFIG_PATH, encoding="utf-8") as fh:
            return cls(yaml.safe_load(fh))

    def get(self, key: str, default: Any = ...) -> Any:
        node: Any = self._data
        for part in key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                if default is ...:
                    raise KeyError(f"config key missing: {key}")
                return default
        return copy.deepcopy(node)

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def with_overrides(self, overrides: dict[str, Any]) -> PlanConfig:
        data = copy.deepcopy(self._data)
        for key, value in overrides.items():
            node = data
            parts = key.split(".")
            for p in parts[:-1]:
                node = node.setdefault(p, {})
            node[parts[-1]] = value
        return PlanConfig(data)

    def subset(self, keys: list[str]) -> dict[str, Any]:
        return {k: self.get(k, None) for k in sorted(keys)}

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(self._data, sort_keys=True, default=str).encode()).hexdigest()

    @property
    def cities(self) -> list[str]:
        return list(self.get("plan.cities"))

    @property
    def cities_with_other(self) -> list[str]:
        return self.cities + ["Other"]
