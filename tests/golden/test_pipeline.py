"""Pipeline tests on the synthetic workspace: invariants, determinism and hand-checked
factor arithmetic (golden-factors in miniature; the planner-built 10-line sheet plugs in
via tests/golden/golden_lines.csv when provided)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.domain.plan_math import build_bridge
from worker.local import run_local


def test_all_steps_ran(local_run):
    st = {n: r.status for n, r in local_run.log.results.items()}
    assert st[8] == "skipped" and st[10] == "skipped"
    assert all(st[n] == "succeeded" for n in (1, 2, 3, 4, 5, 6, 7, 9, 11))


def test_invariants(local_run):
    dp = local_run.write("dp_output")
    assert len(dp) > 0
    assert (dp["final_dp"] >= 0).all()
    for r in dp.itertuples():
        bridge = json.loads(r.bridge)
        assert sum(b["units"] for b in bridge) == r.final_dp, r.line_id
    # shares for the five cities plus Other sum to 1 for every SKU × platform
    base = local_run.ctx.artifacts["base"]
    sums = base.groupby(["sku_code", "platform"])["share"].sum()
    assert np.allclose(sums.values, 1.0)


def test_internal_customers_excluded(local_run):
    hist = local_run.ctx.artifacts["history"]
    assert "internal" not in set(hist["platform"])


def test_target_and_horizon(local_run):
    assert local_run.ctx.as_of == pd.Timestamp("2026-09-28")
    assert local_run.ctx.m1 == pd.Timestamp("2026-08-01")  # September is not complete at 28 Sep
    assert local_run.ctx.target_month == pd.Timestamp("2026-11-01")
    assert local_run.ctx.horizon == 3


def test_determinism(snapshot, cfg):
    c = cfg.with_overrides({"agent.enabled": False, "crosswalk.hard_fail_unmapped": False})
    a = run_local(snapshot, c, "2026-11")
    b = run_local(snapshot, c, "2026-11")
    ha = a.ctx.artifacts["evidence_pack"]["pack_hash"].tolist()
    hb = b.ctx.artifacts["evidence_pack"]["pack_hash"].tolist()
    assert ha == hb
    assert a.write("dp_output")["final_dp"].tolist() == b.write("dp_output")["final_dp"].tolist()
    assert a.log.fingerprints == b.log.fingerprints


def test_trend_factor_hand_check(local_run):
    """trend m = clip((1+g)^h − 1, ±cap) with g = (q(M1)/q(M3))^(1/2) − 1, per SKU × platform."""
    ctx = local_run.ctx
    hist = ctx.artifacts["history"]
    f = ctx.artifacts["factors"]
    m1, _, m3 = ctx.history_months()
    nat = hist.groupby(["sku_code", "platform", "month"])["q"].sum()
    cap = ctx.config.get("trend.cap")
    for r in f[f["name"] == "trend"].itertuples():
        q1, q3 = nat[(r.sku_code, r.platform, m1)], nat[(r.sku_code, r.platform, m3)]
        g = (q1 / q3) ** 0.5 - 1
        assert r.value == pytest.approx(float(np.clip((1 + g) ** ctx.horizon - 1, -cap, cap)), abs=1e-6)


def test_base_weights_hand_check(local_run):
    ctx = local_run.ctx
    hist = ctx.artifacts["history"]
    base = ctx.artifacts["base"]
    classes = ctx.artifacts["classes"].set_index("sku_code")
    w = ctx.config.get("base.weights")
    months = ctx.history_months()
    nat = hist.groupby(["sku_code", "platform", "month"])["q"].sum()
    present = set(hist["month"])
    pairs = [(wi, m) for wi, m in zip(w, months, strict=False) if m in present]
    wsum = sum(wi for wi, _ in pairs)  # weights renormalised over the months present
    for r in base.drop_duplicates(["sku_code", "platform"]).itertuples():
        if classes.loc[r.sku_code, "sb_class"] not in ("smooth", "erratic"):
            continue
        expect = sum(wi / wsum * nat.get((r.sku_code, r.platform, m), 0.0) for wi, m in pairs)
        assert r.B_np == pytest.approx(expect, rel=1e-9)


def test_reference_dp_matches_bridge(local_run):
    ref = local_run.ctx.artifacts["reference"]
    for r in ref.itertuples():
        _, final = build_bridge(r.base, json.loads(r.python_factors))
        assert final == r.reference_dp


GOLDEN = Path(__file__).with_name("golden_lines.csv")


@pytest.mark.skipif(not GOLDEN.exists(), reason="planner golden sheet not provided yet")
def test_golden_lines_from_planner_sheet(local_run):  # pragma: no cover - activated by the golden sheet
    gold = pd.read_csv(GOLDEN)
    dp = local_run.write("dp_output").set_index("line_id")
    for g in gold.itertuples():
        assert dp.loc[g.line_id, "reference_dp"] == g.reference_dp


def test_scope_limits_platforms(snapshot, cfg):
    c = cfg.with_overrides({"agent.enabled": False, "crosswalk.hard_fail_unmapped": False})
    r = run_local(snapshot, c, "2026-11", scope={"platforms": ["blinkit"]})
    assert set(r.write("dp_output")["platform"]) == {"blinkit"}
