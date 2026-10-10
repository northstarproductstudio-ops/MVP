"""Validator test: every injected bad answer is caught (falls back to reference)."""

from __future__ import annotations

import numpy as np
import pytest

from core.domain.agent_checks import check_output
from core.domain.plan_math import build_bridge, compute_dp, reference_dp
from worker.steps import all_steps

PACK = {
    "sku": "CDTFSA01", "plan_category": "Cake Décor", "lines": [{
        "line_id": "CDTFSA01|Chennai|blinkit", "base": 1840.0, "reference_dp": 2012,
        "factors": [{"name": "trend", "kind": "multiplier", "value": 0.061, "basis": ""},
                    {"name": "sell_through", "kind": "multiplier", "value": 0.03, "basis": ""},
                    {"name": "stock_cover", "kind": "additive", "value": -6.0, "basis": ""}],
        "signals": {"osa_28d": 71.4}}]}


def good_answer(extra: list[dict] | None = None) -> dict:
    applied = [{"name": "trend", "value": 0.061, "reason": None},
               {"name": "sell_through", "value": 0.03, "reason": None},
               {"name": "stock_cover", "value": -6.0, "reason": None}] + (extra or [])
    kinds = {"stock_cover": "additive"}
    dp = compute_dp(1840.0, [{"name": a["name"], "kind": kinds.get(a["name"], "multiplier"), "value": a["value"]}
                             for a in applied])
    return {"lines": [{"line_id": "CDTFSA01|Chennai|blinkit", "final_dp": round(dp), "applied_factors": applied,
                       "reason": "trend and agent:diwali", "confidence": "medium", "flag_for_review": False}]}


def test_reference_dp_formula():
    fs = [{"name": "trend", "kind": "multiplier", "value": 0.061},
          {"name": "sell_through", "kind": "multiplier", "value": 0.03},
          {"name": "stock_cover", "kind": "additive", "value": -6.0}]
    assert reference_dp(1840.0, fs) == round(1840 * 1.061 * 1.03 - 6)
    assert reference_dp(10.0, [{"name": "stock_cover", "kind": "additive", "value": -50.0}]) == 0


@pytest.mark.parametrize("seed", range(25))
def test_bridge_sums_to_final(seed):
    rng = np.random.default_rng(seed)
    base = float(rng.uniform(0, 5000))
    applied = [{"name": n, "kind": "multiplier", "value": float(rng.uniform(-0.3, 0.5))}
               for n in ("trend", "sell_through", "fill_rate", "agent:diwali", "judgment")]
    applied.append({"name": "stock_cover", "kind": "additive", "value": float(rng.uniform(-base, 500))})
    bridge, final = build_bridge(base, applied)
    assert sum(b["units"] for b in bridge) == final
    assert final >= 0
    assert [b["name"] for b in bridge][:4] == ["base", "trend", "sell_through", "fill_rate"]


def test_good_answer_passes(cfg):
    good, failed = check_output(PACK, good_answer([{"name": "agent:diwali", "value": 0.08,
                                                    "reason": "Diwali lifts baking garnish demand"}]), cfg)
    assert not failed and list(good) == ["CDTFSA01|Chennai|blinkit"]


def _mutate(fn):
    a = good_answer([{"name": "agent:diwali", "value": 0.08, "reason": "Diwali lifts baking"}])
    fn(a["lines"][0])
    return a


BAD = {
    "unknown_factor": lambda ln: ln["applied_factors"].append({"name": "magic", "value": 0.0, "reason": "x"}),
    "agent_out_of_bounds": lambda ln: ln["applied_factors"][3].update(value=0.9),
    "agent_no_reason": lambda ln: ln["applied_factors"][3].update(reason=""),
    "two_judgments": lambda ln: ln["applied_factors"].extend([{"name": "judgment", "value": 0.01, "reason": "a"},
                                                              {"name": "judgment", "value": 0.01, "reason": "b"}]),
    "judgment_over_cap": lambda ln: ln["applied_factors"].append({"name": "judgment", "value": 0.2, "reason": "a"}),
    "arithmetic": lambda ln: ln.update(final_dp=ln["final_dp"] + 50),
    "band": lambda ln: (ln["applied_factors"].append({"name": "agent:huge", "value": 0.5, "reason": "x"}),
                        ln["applied_factors"].append({"name": "agent:huge2", "value": 0.5, "reason": "y"}),
                        ln.update(final_dp=round(compute_dp(1840, [
                            {"name": "a", "kind": "multiplier", "value": 0.061},
                            {"name": "b", "kind": "multiplier", "value": 0.03},
                            {"name": "c", "kind": "multiplier", "value": 0.08},
                            {"name": "d", "kind": "multiplier", "value": 0.5},
                            {"name": "e", "kind": "multiplier", "value": 0.5},
                            {"name": "s", "kind": "additive", "value": -6}])))),
    "unexplained_gap": lambda ln: (ln["applied_factors"][3].update(value=0.3),
                                   ln.update(reason="looks fine",
                                             final_dp=round(1840 * 1.061 * 1.03 * 1.3 - 6))),
}


@pytest.mark.parametrize("name", sorted(BAD))
def test_validator_catches_bad_answers(name, cfg):
    answer = _mutate(BAD[name])
    if name == "unexplained_gap":
        answer["lines"][0]["applied_factors"][3]["reason"] = "x"
        answer["lines"][0]["applied_factors"][3]["name"] = "agent:x"
    good, failed = check_output(PACK, answer, cfg)
    assert failed, name
    assert not good


def test_missing_line_and_bad_schema(cfg):
    _, failed = check_output(PACK, {"lines": []}, cfg)
    assert "*" in failed
    _, failed = check_output(PACK, {"nope": 1}, cfg)
    assert failed["*"][0].startswith("schema")


def test_only_llm_steps_declare_llm():
    assert {s.no for s in all_steps() if s.uses_llm} == {8, 11}
    assert [s.no for s in all_steps()] == list(range(1, 12))
