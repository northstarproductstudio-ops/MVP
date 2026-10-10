"""Step 10 — net_and_round. Runs only when netting.enabled, cover days are set and stock
data exists; otherwise skipped with "needs stock data" and order quantity stays empty.

    safety_stock = final_dp ÷ days_in_month × safety.cover_days[abc][sb_class]
    order_qty    = ceil(max(0, final_dp + safety_stock − soh − in_transit − open_po) ÷ case) × case
"""

from __future__ import annotations

import json
import math

import pandas as pd

from worker.context import RunContext
from worker.steps import StepResult, step


@step(name="net_and_round", no=10,
      reads=["plan_lines", "stock_snapshot", "po_line", "sku_master", "inputs"], writes=["netted"],
      config_keys=["netting.enabled", "safety.cover_days"], version="1.0")
def net_and_round(ctx: RunContext) -> StepResult:
    cfg = ctx.config
    plan = ctx.artifact("plan_lines").copy()
    cover = cfg.get("safety.cover_days") or {}
    stock = ctx.table("stock_snapshot")
    plan["safety_stock"] = pd.NA
    plan["order_qty"] = pd.NA
    sm = ctx.table("sku_master").set_index("sku_code") if not ctx.table("sku_master").empty else None
    plan["case_size"] = [int(sm["case_size"].get(s)) if sm is not None and pd.notna(sm["case_size"].get(s))
                         else 1 for s in plan["sku_code"]]
    if not cfg.get("netting.enabled") or not cover or stock.empty:
        return StepResult(status="skipped", warnings=["Order quantity needs stock data (netting off)"],
                          summary="Netting skipped: order quantity needs stock data",
                          artifacts={"netted": plan})
    snap = stock["snapshot_date"].max()
    soh = stock[(stock["snapshot_date"] == snap) & stock["sku_code"].notna()] \
        .groupby(["sku_code", "city", "platform"])["soh_units"].sum()
    po = ctx.table("po_line")
    open_po = po[(po["status"] == "open") & po["sku_code"].notna()] \
        .groupby(["sku_code", "city", "platform"])["qty_open"].sum() if not po.empty else pd.Series(dtype=float)
    days = ctx.days_in_target
    moq_raised = 0
    ss_list, oq_list = [], []
    for r in plan.itertuples():
        days_cover = (cover.get(r.abc) or {}).get(r.sb_class)
        if days_cover is None:
            ss_list.append(None)
            oq_list.append(None)
            continue
        ss = r.final_dp / days * float(days_cover)
        k = (r.sku_code, r.city, r.platform)
        need = r.final_dp + ss - float(soh.get(k, 0)) - 0.0 - float(open_po.get(k, 0))
        case = max(1, int(r.case_size))
        oq = math.ceil(max(0.0, need) / case) * case
        moq = sm["moq"].get(r.sku_code) if sm is not None else None
        if moq is not None and pd.notna(moq) and 0 < oq < int(moq):
            oq = int(math.ceil(int(moq) / case) * case)
            plan.at[r.Index, "flags"] = sorted(set(r.flags) | {"moq_raised"})
            moq_raised += 1
        ss_list.append(int(round(ss)))
        oq_list.append(int(oq))
    plan["safety_stock"] = ss_list
    plan["order_qty"] = oq_list
    _ = json
    total = int(pd.Series([x for x in oq_list if x is not None]).sum()) if oq_list else 0
    return StepResult(rows_in=len(plan), rows_out=len(plan),
                      summary=f"Order quantity {total:,} units after netting stock and open POs; "
                              f"{moq_raised} lines raised to MOQ",
                      artifacts={"netted": plan})
