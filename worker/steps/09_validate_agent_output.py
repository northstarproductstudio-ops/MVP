"""Step 9 — validate_agent_output: accept checked agent lines, fall back to the reference
DP (flag agent_fallback) otherwise, write the factor bridge, agent factors and the
market-ceiling flag. Flags never change a number."""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd

from core.domain.agent_checks import check_batch_output
from core.domain.factor_sources import source_label
from core.domain.plan_math import build_bridge
from worker.context import RunContext
from worker.steps import StepResult, step


def _reason_from_factors(base: float, applied: list[dict[str, Any]]) -> str:
    parts = [f"Base {base:,.0f}"]
    for f in applied:
        if f["kind"] == "multiplier" and f["value"]:
            parts.append(f"{f['name']} {f['value'] * 100:+.1f}%")
        elif f["kind"] == "additive" and f["value"]:
            parts.append(f"{f['name']} {f['value']:+,.0f} units")
    return "Reference DP: " + "; ".join(parts)


@step(name="validate_agent_output", no=9,
      reads=["evidence_pack", "reference", "agent_raw", "classes", "base", "market_daily", "factors",
             "agent_batches"],
      writes=["plan_lines", "factor"],
      config_keys=["agent.enabled", "agent.factor_bounds", "agent.judgment_cap", "agent.arith_tolerance",
                   "agent.band", "agent.explain_threshold", "market.ceiling_ratio", "market.price_window_days"],
      version="2.0")
def validate_agent_output(ctx: RunContext) -> StepResult:
    cfg = ctx.config
    ev = ctx.artifact("evidence_pack")
    ref = ctx.artifact("reference").set_index("line_id")
    raw = ctx.artifact("agent_raw")
    classes = ctx.artifact("classes").set_index("sku_code")
    base = ctx.artifact("base").set_index("line_id")
    agent_on = bool(cfg.get("agent.enabled")) and not raw.empty
    accepted: dict[str, dict[str, Any]] = {}
    batch_of: dict[str, str] = {}
    batches = ctx.artifact("agent_batches") if ctx.has_artifact("agent_batches") else pd.DataFrame()
    for b in batches.itertuples():
        for s in json.loads(b.sku_codes):
            batch_of[s] = b.batch_key
    for r in ev.itertuples():
        pack = json.loads(r.pack_json)
        answers = raw[(raw["sku_code"] == r.sku_code) & raw["valid"] & raw["response_json"].notna()] \
            if agent_on else raw.iloc[0:0]
        for a in answers.itertuples():
            parsed = json.loads(a.response_json)
            # expand category factors into each line, then re-check deterministically
            good, _ = check_batch_output(pack, parsed, cfg)
            accepted.update(good)

    lines, agent_factor_rows = [], []
    fallback = review = 0
    for lid, rr in ref.iterrows():
        b = base.loc[lid]
        flags = set(json.loads(rr["flags"]))
        py = json.loads(rr["python_factors"])
        if lid in accepted:
            g = accepted[lid]
            applied = g["applied"]
            ln = g["line"]
            reason, confidence, ffr = ln["reason"], ln["confidence"], bool(ln["flag_for_review"])
            for a in applied:
                if a["name"].startswith("agent:") or a["name"] == "judgment":
                    agent_factor_rows.append({"name": a["name"], "kind": "multiplier", "origin": "agent",
                                              "scope": json.dumps({"sku": rr["sku_code"], "city": rr["city"],
                                                                   "platform": rr["platform"]}, sort_keys=True),
                                              "derived_value": a["value"], "value": a["value"], "enabled": True,
                                              "basis": a.get("reason"), "note": lid,
                                              "source_label": source_label(a["name"], "agent"),
                                              "source_dataset_ids": []})
        else:
            applied = [{**f, "reason": None} for f in py]
            reason = _reason_from_factors(rr["base"], applied)
            confidence, ffr = None, False
            if agent_on:
                flags.add("agent_fallback")
                fallback += 1
        bridge, final_dp = build_bridge(rr["base"], applied)
        if ffr:
            review += 1
        lines.append({
            "line_id": lid, "sku_code": rr["sku_code"], "city": rr["city"], "platform": rr["platform"],
            "item_name": b["item_name"], "plan_category": b["plan_category"],
            "batch_key": batch_of.get(rr["sku_code"]),
            "abc": classes["abc"].get(rr["sku_code"]), "sb_class": classes["sb_class"].get(rr["sku_code"]),
            "base": float(rr["base"]), "reference_dp": int(rr["reference_dp"]), "final_dp": int(final_dp),
            "applied_factors": json.dumps(applied, sort_keys=True), "bridge": json.dumps(bridge),
            "reason": reason, "confidence": confidence, "flag_for_review": ffr, "flags": sorted(flags),
        })
    plan = pd.DataFrame(lines)

    # Market-ceiling flag per SKU × platform
    md = ctx.table("market_daily")
    ceiling = 0
    if not md.empty and not plan.empty:
        ratio = float(cfg.get("market.ceiling_ratio"))
        m1 = ctx.m1
        m1_end = m1 + pd.offsets.MonthEnd(0)
        mm = md[(md["market_date"] >= m1) & (md["market_date"] <= m1_end)]
        val = mm["sales_sp"].where(mm["sales_sp"].notna(), mm["sales_mrp"]).fillna(0)
        mm = mm.assign(val=val)
        cat_val = mm.groupby(["platform", "category"])["val"].sum()
        own = mm[mm["is_own_brand"] & mm["sku_code"].notna()]
        own_val = own.groupby(["sku_code", "platform"])["val"].sum()
        own_cat = own.groupby(["sku_code", "platform"])["category"].first()
        pw = int(cfg.get("market.price_window_days"))
        end = pd.Timestamp(md["market_date"].max())
        recent = md[(md["market_date"] > end - pd.Timedelta(days=pw)) & md["is_own_brand"] & md["sku_code"].notna()]
        price = recent.assign(p=recent["sp"].where(recent["sales_sp"].notna(), recent["mrp"])) \
            .groupby(["sku_code", "platform"])["p"].mean()
        dp = plan.groupby(["sku_code", "platform"])["final_dp"].sum()
        for (sku, plat), units in dp.items():
            if (sku, plat) not in own_cat.index or (sku, plat) not in price.index:
                continue
            cv = float(cat_val.get((plat, own_cat[(sku, plat)]), 0.0))
            cur = float(own_val.get((sku, plat), 0.0))
            if cv <= 0 or cur <= 0 or not np.isfinite(price[(sku, plat)]):
                continue
            implied = units * float(price[(sku, plat)]) / cv
            if implied > ratio * (cur / cv):
                ceiling += 1
                mask = (plan["sku_code"] == sku) & (plan["platform"] == plat)
                plan.loc[mask, "flags"] = plan.loc[mask, "flags"].map(lambda f: sorted(set(f) | {"market_ceiling"}))
    af = pd.DataFrame(agent_factor_rows, columns=["name", "kind", "origin", "scope", "derived_value", "value",
                                                  "enabled", "basis", "note", "source_label", "source_dataset_ids"])
    total = int(plan["final_dp"].sum()) if len(plan) else 0
    return StepResult(
        rows_in=len(ref), rows_out=len(plan),
        summary=(f"{len(accepted)} lines from the planning agent, {fallback} fell back to reference; "
                 if agent_on else "Plan lines from the reference DP; ")
                + f"total DP {total:,}; {ceiling} SKU × platform pairs flagged market ceiling",
        warnings=[f"{fallback} lines fell back to the reference DP"] if fallback else [],
        artifacts={"plan_lines": plan},
        writes={"factor": af})
