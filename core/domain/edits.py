"""Direct edits on stored plan lines (no pipeline re-run).

Pure part: resolve the target to lines of the current version and compute new DP (and
order quantity when step 10 ran). The API service persists a new report version."""

from __future__ import annotations

import math
from dataclasses import dataclass
from difflib import get_close_matches
from typing import Any

import pandas as pd

from core.domain.plan_math import round_half_up
from core.models.edit import Edit


class EditError(ValueError):
    pass


@dataclass
class EditPlan:
    edit: Edit
    changes: pd.DataFrame  # line_id, dp_before, dp_after, order_qty_after
    lines_changed: int
    units_before: int
    units_after: int
    suggestion: str | None = None


def match_name(value: str, options: list[str]) -> tuple[str | None, list[str]]:
    """Exact (case-insensitive) match, else close candidates for the agent to confirm."""
    low = {o.lower(): o for o in options if o}
    if value.lower() in low:
        return low[value.lower()], []
    return None, get_close_matches(value, options, n=3, cutoff=0.5)


def resolve(lines: pd.DataFrame, edit: Edit) -> pd.DataFrame:
    t = edit.target
    m = pd.Series(True, index=lines.index)
    for col, val, label in (("sku_code", t.sku_code, "SKU"), ("plan_category", t.plan_category, "category"),
                            ("city", t.city, "city"), ("platform", t.platform, "platform")):
        if not val:
            continue
        exact, cands = match_name(val, sorted(lines[col].dropna().unique().tolist()))
        if exact is None:
            hint = f" Did you mean {', '.join(cands)}?" if cands else ""
            raise EditError(f"No {label} named '{val}' in this plan.{hint}")
        m &= lines[col] == exact
    sel = lines[m]
    if sel.empty:
        raise EditError("That edit matches no plan lines; please restate it.")
    if edit.op == "set" and len(sel) != 1:
        raise EditError("A 'set' edit must target exactly one line (SKU, city and platform).")
    return sel


def plan_edit(lines: pd.DataFrame, edit: Edit, days_in_month: int, cover_days: dict[str, Any],
              netting_ran: bool, soh: dict[tuple, float] | None = None,
              open_po: dict[tuple, float] | None = None, moq: dict[str, int] | None = None) -> EditPlan:
    sel = resolve(lines, edit)
    rows = []
    for r in sel.itertuples():
        before = int(r.final_dp)
        after = round_half_up(before * (1 + edit.value / 100)) if edit.op == "pct" else int(edit.value)
        after = max(0, after)
        oq = None
        if netting_ran:
            dc = (cover_days.get(r.abc) or {}).get(r.sb_class)
            if dc is not None:
                ss = after / days_in_month * float(dc)
                k = (r.sku_code, r.city, r.platform)
                need = after + ss - float((soh or {}).get(k, 0)) - float((open_po or {}).get(k, 0))
                case = max(1, int(r.case_size or 1))
                oq = math.ceil(max(0.0, need) / case) * case
                mq = (moq or {}).get(r.sku_code)
                if mq and 0 < oq < mq:
                    oq = math.ceil(mq / case) * case
        rows.append({"line_id": r.line_id, "dp_before": before, "dp_after": after, "order_qty_after": oq})
    ch = pd.DataFrame(rows)
    changed = ch[ch["dp_before"] != ch["dp_after"]]
    return EditPlan(edit=edit, changes=changed, lines_changed=len(changed),
                    units_before=int(changed["dp_before"].sum()), units_after=int(changed["dp_after"].sum()))
