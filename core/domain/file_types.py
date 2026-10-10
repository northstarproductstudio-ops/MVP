"""File-type detection against the library (content first, filename as a weak hint).

For every sheet, try header rows 1..N and score each adapter as matched signature
columns ÷ signature size. Best ≥ accept picks the type. The filename only breaks ties:
if the top two are within ``tie`` or nothing reaches ``accept`` and the filename names a
type, the import agent decides and may ask the user. Below ``custom_below`` with no hint
the file is Custom.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from core.domain.adapters import REGISTRY
from core.domain.file_reader import RawSheet
from core.plan_config import SEED_DIR


@dataclass
class TypeScore:
    key: str
    score: float
    sheet: str
    header_row: int


@dataclass
class Detection:
    file_type: str
    score: float
    source: str  # adapter | agent | user
    decided: bool  # False → the import agent (or user) must decide
    reason: str
    scores: list[TypeScore] = field(default_factory=list)
    hint: str | None = None


def library() -> list[dict]:
    with open(SEED_DIR / "file_types.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def filename_hint(file_name: str) -> str | None:
    name = Path(file_name).stem.lower().replace("_", " ").replace("-", " ")
    # most specific hints first
    order = ["platform_po_zepto", "platform_po_instamart", "platform_stock_blinkit",
             "market_category_insight", "platform_po_bigbasket", "erp_sales_orders", "sku_master"]
    if any(w in name for w in ("insight", "dcluttr", "categories")):
        return "market_category_insight"
    if any(w in name for w in ("doh", "soh")) and "blinkit" in name:
        return "platform_stock_blinkit"
    for key in order:
        for h in REGISTRY[key].filename_hints:
            if f" {h} " in f" {name} ":
                return key
    return None


def detect(sheets: list[RawSheet], file_name: str, accept: float = 0.8, tie: float = 0.1,
           custom_below: float = 0.5, rows_to_try: int = 5) -> Detection:
    best_by_type: dict[str, TypeScore] = {}
    for key, cls in REGISTRY.items():
        if key == "custom":
            continue
        for sh in sheets:
            s, hdr = cls.detect(sh, rows_to_try)
            cur = best_by_type.get(key)
            if cur is None or s > cur.score:
                best_by_type[key] = TypeScore(key, round(s, 4), sh.name, hdr)
    ranked = sorted(best_by_type.values(), key=lambda t: -t.score)
    hint = filename_hint(file_name)
    top = ranked[0] if ranked else TypeScore("custom", 0.0, "", 1)
    second = ranked[1] if len(ranked) > 1 else TypeScore("custom", 0.0, "", 1)

    if top.score >= accept and (top.score - second.score) > tie:
        conflict = hint is not None and hint != top.key
        return Detection(top.key, top.score, "adapter", not conflict,
                         "content matched" + (f"; file name suggests {hint}" if conflict else ""),
                         ranked, hint)
    if top.score >= accept:
        # tie between types: filename breaks it
        if hint in (top.key, second.key):
            return Detection(hint, best_by_type[hint].score, "adapter", True,
                             "tie broken by file name", ranked, hint)
        return Detection(top.key, top.score, "adapter", False, "two types fit equally", ranked, hint)
    if hint is not None:
        return Detection(hint, best_by_type.get(hint, top).score, "adapter", False,
                         "weak content match; file name names a type", ranked, hint)
    if top.score < custom_below:
        return Detection("custom", top.score, "adapter", True, "no type matched", ranked, hint)
    return Detection(top.key, top.score, "adapter", False, "partial content match", ranked, hint)
