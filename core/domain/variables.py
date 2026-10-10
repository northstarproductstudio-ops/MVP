"""Plan variable catalogue — the contract between mapping and the pipeline."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache

from core.plan_config import SEED_DIR


@dataclass(frozen=True)
class Variable:
    name: str
    category: str
    data_type: str
    unit: str | None
    tier: int
    level: str  # hard | soft | key | none
    file_group: str | None
    definition: str | None
    origin: str = "seed"


@lru_cache(maxsize=1)
def catalogue() -> dict[str, Variable]:
    out: dict[str, Variable] = {}
    with open(SEED_DIR / "variables.csv", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            out[row["name"]] = Variable(
                name=row["name"], category=row["category"], data_type=row["data_type"],
                unit=row["unit"] or None, tier=int(row["tier"]), level=row["level"],
                file_group=row["file_group"] or None, definition=row["definition"] or None,
            )
    return out


def hard_variables(group: str = "sellin") -> list[str]:
    return [v.name for v in catalogue().values() if v.level == "hard" and v.file_group == group]


def categories() -> list[str]:
    return sorted({v.category for v in catalogue().values()})


def compatible(var: Variable, inferred: str) -> bool:
    """Does an inferred column type fit a tier-1 variable's type?"""
    if inferred == "empty":
        return False
    if var.data_type in ("integer", "decimal", "money"):
        return inferred == "number"
    if var.data_type == "date":
        return inferred in ("date", "text")
    return True
