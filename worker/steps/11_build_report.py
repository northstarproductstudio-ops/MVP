"""Step 11 — build_report: report version 0, run totals and the plan overview (one
Ask-agent call that sees only totals, factor counts and flag counts; template fallback)."""

from __future__ import annotations

import json

import pandas as pd

from core.domain.factor_sources import GROUP_SHORT, factors_off_text, group_period, sources_used_text
from core.domain.reports import current_lines, overview_facts, template_overview
from core.llm import LLMError, load_prompt
from worker.context import RunContext
from worker.steps import StepResult, step

DP_COLS = ["line_id", "sku_code", "item_name", "plan_category", "city", "platform", "abc", "sb_class", "base",
           "reference_dp", "final_dp", "applied_factors", "bridge", "reason", "confidence", "flag_for_review",
           "flags", "safety_stock", "order_qty", "case_size", "batch_key", "sources_used", "factors_off"]


def write_overview(ctx: RunContext, facts: dict) -> str:
    if ctx.llm is None:
        return template_overview(facts)
    try:
        res = ctx.llm.complete(
            role="ask", run_id=ctx.run_id, session_id=ctx.session_id, company_id=ctx.company_id,
            prompt_version=ctx.config.get("agent.prompt_version"), temperature=0.0, max_tokens=400,
            messages=[{"role": "system", "content": load_prompt("ask_agent", ctx.config.get("ask.prompt_version", "v1"), "overview.md")},
                      {"role": "user", "content": json.dumps(facts, sort_keys=True)}])
        text = (res.content or "").strip()
        return text or template_overview(facts)
    except LLMError:
        return template_overview(facts)


FILE_TABLES = ("sellin_line", "po_line", "stock_snapshot", "market_daily")


def run_sources(tables: dict[str, pd.DataFrame]) -> tuple[dict[str, bool], dict[str, list[str]]]:
    """Which file groups were present, and the overview's "used / missing" lists (Gap 8b)."""
    present = {g: tables.get(t) is not None and not tables[t].empty
               for g, t in (("sellin", "sellin_line"), ("po", "po_line"), ("stock", "stock_snapshot"),
                            ("market", "market_daily"))}
    used, missing = [], []
    for g, ok in present.items():
        per = group_period(tables, g) if ok else None
        if ok:
            used.append(f"{GROUP_SHORT[g]} {per}" if per else GROUP_SHORT[g])
        else:
            missing.append({"po": "no PO file, so fill rate is off", "stock": "no stock file, so stock cover is off",
                            "market": "no Dcluttr file, so sell-through is off",
                            "sellin": "no sales history"}[g])
    return present, {"used": used, "missing": missing}


@step(name="build_report", no=11,
      reads=["netted", "history", "factors", "base", "inputs", "sellin_line", "po_line", "stock_snapshot",
             "market_daily"],
      writes=["dp_output", "report", "report_version"], config_keys=["plan.cities", "ask.prompt_version"],
      version="1.1", uses_llm=True)
def build_report(ctx: RunContext) -> StepResult:
    plan = ctx.artifact("netted").copy()
    hist = ctx.artifact("history")
    factors = ctx.artifact("factors")
    base = ctx.artifact("base").set_index("line_id")
    inputs = ctx.artifact("inputs")
    tables = {t: ctx.table(t) for t in FILE_TABLES}
    present, sources = run_sources(tables)
    not_planned = json.loads(inputs["no_history_skus"].iloc[0]) if "no_history_skus" in inputs and len(inputs) else []
    off_extra = ["seasonal: needs 12 months of history"]
    plan["sources_used"] = [sources_used_text(json.loads(a) if isinstance(a, str) else (a or []), p, tables)
                            for a, p in zip(plan["applied_factors"], plan["platform"], strict=False)]
    plan["factors_off"] = factors_off_text(present, off_extra)
    plan["share_basis"] = plan["line_id"].map(base["share_basis"])
    last_month = float(hist.loc[hist["month"] == ctx.m1, "q"].sum())
    lines = current_lines(plan, None)
    facts = overview_facts(lines, factors, last_month, f"{ctx.target_month:%Y-%m-%d}", 0, [], sources)
    overview = write_overview(ctx, facts)
    order_total = int(pd.to_numeric(plan["order_qty"], errors="coerce").fillna(0).sum())
    dp_total = int(plan["final_dp"].sum())
    payload = {
        "target_month": f"{ctx.target_month:%Y-%m-%d}", "as_of": f"{ctx.as_of:%Y-%m-%d}",
        "dp_total": dp_total, "order_total": order_total if plan["order_qty"].notna().any() else None,
        "last_month_sellin": int(last_month), "change_pct": facts["change_pct"],
        "lines": int(len(plan)), "skus": int(plan["sku_code"].nunique()),
        "cities": sorted(plan["city"].unique().tolist()), "platforms": sorted(plan["platform"].unique().tolist()),
        "agent_fallback": int(plan["flags"].map(lambda f: "agent_fallback" in f).sum()),
        "review": int(plan["flag_for_review"].sum()), "flags": facts["flags"],
        "factor_lines": facts["factor_lines"], "by_city": facts["by_city"], "by_platform": facts["by_platform"],
        "overview": overview, "sources": sources,
        "not_planned": not_planned,  # Gap 7: SKUs left out because they have no sales history
    }
    dp = plan.reindex(columns=DP_COLS)
    dp["flags"] = dp["flags"].map(lambda f: list(f) if isinstance(f, (list, tuple)) else [])
    rv = pd.DataFrame([{"version": 0, "parent_version_id": None, "edit": None, "note": "pipeline output",
                        "overview_text": overview, "created_by": None, "is_current": True}])
    return StepResult(
        rows_in=len(plan), rows_out=len(dp),
        summary=f"Plan built: {dp_total:,} units over {payload['skus']} SKUs, {len(plan)} lines"
                + (f"; order quantity {order_total:,}" if payload["order_total"] is not None
                   else "; order quantity needs stock data"),
        writes={"dp_output": dp, "report": pd.DataFrame([{"payload": json.dumps(payload, default=str)}]),
                "report_version": rv},
        run_updates={"dp_total": dp_total, "order_total": payload["order_total"]})
