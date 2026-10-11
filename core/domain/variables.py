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
    file_group: str | None  # primary file group (first of a "|"-separated list)
    definition: str | None
    origin: str = "seed"
    display_name: str = ""  # Gap 11: what planners see; ``name`` stays the stored mapping target
    help: str | None = None
    file_groups: tuple[str, ...] = ()  # every file group the variable may be mapped from

    @property
    def label(self) -> str:
        return self.display_name or humanize(self.name)


@lru_cache(maxsize=1)
def catalogue() -> dict[str, Variable]:
    out: dict[str, Variable] = {}
    with open(SEED_DIR / "variables.csv", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            groups = tuple(g.strip() for g in (row["file_group"] or "").split("|") if g.strip())
            out[row["name"]] = Variable(
                name=row["name"], category=row["category"], data_type=row["data_type"],
                unit=row["unit"] or None, tier=int(row["tier"]), level=row["level"],
                file_group=groups[0] if groups else None, definition=row["definition"] or None,
                display_name=(row.get("display_name") or "").strip(), help=(row.get("help") or "").strip() or None,
                file_groups=groups,
            )
    return out


def humanize(name: str) -> str:
    """Fallback display name for variables without one (e.g. agent-proposed tier-2 names)."""
    words = name.replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else name


def display_name(name: str) -> str:
    v = catalogue().get(name)
    return v.label if v is not None else humanize(name)


def display_names(names: list[str]) -> list[str]:
    return [display_name(n) for n in names]


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
