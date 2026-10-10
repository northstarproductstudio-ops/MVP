"""Plan arithmetic shared by steps 7–11, the validator and direct edits.

    reference_dp = max(0, round(base × Π(1 + m_i) + Σ a_j))
    final_dp     = base × Π(1 + applied multipliers) + Σ applied additives (rounded)

Bridge order: base, trend, sell_through, fill_rate, agent factors (as returned),
judgment, then additives. Each unit effect is computed on the running total and rounded
cumulatively, so the bridge sums exactly to final_dp.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

PY_MULT_ORDER = ["trend", "sell_through", "fill_rate"]


def line_factors(factors: pd.DataFrame, sku: str, city: str, platform: str) -> list[dict[str, Any]]:
    """Enabled Python factors whose scope covers this line, in bridge order."""
    if factors.empty:
        return []
    f = factors[factors["enabled"]]
    m = f[(f["sku_code"] == sku) & ((f["platform"] == platform) | f["platform"].isna())
          & ((f["city"] == city) | f["city"].isna())]
    out = [{"name": r.name, "kind": r.kind, "value": float(r.value), "basis": r.basis}
           for r in m.itertuples(index=False)]
    order = {n: i for i, n in enumerate(PY_MULT_ORDER)}
    return sorted(out, key=lambda x: (x["kind"] == "additive", order.get(x["name"], 99), x["name"]))


def compute_dp(base: float, applied: list[dict[str, Any]]) -> float:
    total = float(base)
    for f in applied:
        if f["kind"] == "multiplier":
            total *= 1 + float(f["value"])
    for f in applied:
        if f["kind"] == "additive":
            total += float(f["value"])
    return total


def reference_dp(base: float, factors: list[dict[str, Any]]) -> int:
    return max(0, int(round_half_up(compute_dp(base, factors))))


def round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def bridge_order(applied: list[dict[str, Any]]) -> list[dict[str, Any]]:
    py = [f for f in applied if f["kind"] == "multiplier" and f["name"] in PY_MULT_ORDER]
    py.sort(key=lambda f: PY_MULT_ORDER.index(f["name"]))
    agent = [f for f in applied if f["kind"] == "multiplier" and f["name"].startswith("agent:")]
    judgment = [f for f in applied if f["name"] == "judgment"]
    other_mult = [f for f in applied if f["kind"] == "multiplier" and f not in py + agent + judgment]
    adds = [f for f in applied if f["kind"] == "additive"]
    return py + other_mult + agent + judgment + adds


def build_bridge(base: float, applied: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Return (bridge rows, final_dp). Rows: name, kind, value, units."""
    ordered = bridge_order(applied)
    running = float(base)
    prev_rounded = round_half_up(running)
    rows = [{"name": "base", "kind": "base", "value": None, "units": prev_rounded}]
    for f in ordered:
        if f["kind"] == "multiplier":
            running = running * (1 + float(f["value"]))
        else:
            running = running + float(f["value"])
        cur = round_half_up(running)
        rows.append({"name": f["name"], "kind": f["kind"], "value": float(f["value"]),
                     "units": cur - prev_rounded})
        prev_rounded = cur
    final = prev_rounded
    if final < 0:
        rows.append({"name": "floor_at_zero", "kind": "floor", "value": None, "units": -final})
        final = 0
    return rows, final


def bridge_units(bridge: list[dict[str, Any]], name: str) -> int:
    return sum(int(b["units"]) for b in bridge if b["name"] == name)
