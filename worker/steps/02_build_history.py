"""Step 2 — build_history: sell-in units per line and month, plus weekly national sell-in."""

from __future__ import annotations

import pandas as pd

from worker.context import RunContext
from worker.steps import StepResult, step


def sellin_units(df: pd.DataFrame) -> pd.Series:
    """q = qty_invoiced + qty_cancelled (ordered); qty_ordered if invoiced is unmapped."""
    if df["qty_invoiced"].isna().all():
        return df["qty_ordered"].fillna(0).astype(float)
    return (df["qty_invoiced"].fillna(0) + df["qty_cancelled"].fillna(0)).astype(float)


@step(name="build_history", no=2, reads=["sellin_line", "sku_master"], writes=["history", "weekly"],
      config_keys=["history.sellin_statuses"], version="1.1")
def build_history(ctx: RunContext) -> StepResult:
    sellin = ctx.table("sellin_line")
    statuses = ctx.config.get("history.sellin_statuses")
    live = sellin[sellin["status"].isin(statuses) & ~sellin["is_internal"]
                  & (sellin["order_date"] <= ctx.as_of)].copy()
    dropped_internal = int(sellin["is_internal"].sum())
    scope = ctx.scope or {}
    for key, col in (("platforms", "platform"), ("cities", "city"), ("skus", "sku_code")):
        if scope.get(key):
            live = live[live[col].isin([str(v) for v in scope[key]])]
    live["q"] = sellin_units(live)
    price = live["item_price"].where(live["item_price"] > 0, live["mrp"])
    live["value"] = live["q"] * price.fillna(0)
    live["month"] = live["order_date"].dt.to_period("M").dt.to_timestamp()
    hist = (live.groupby(["sku_code", "city", "platform", "month"], as_index=False)
            .agg(q=("q", "sum"), value=("value", "sum")))
    names = live.sort_values("order_date").groupby("sku_code")["item_name"].last()
    hist["item_name"] = hist["sku_code"].map(names)
    live["week"] = live["order_date"].dt.to_period("W-SUN").dt.start_time
    weekly = live.groupby(["sku_code", "platform", "week"], as_index=False).agg(q=("q", "sum"))
    return StepResult(
        rows_in=len(sellin), rows_out=len(hist),
        summary=f"{len(live):,} invoiced/confirmed lines → {len(hist):,} SKU × city × platform × month "
                f"rows; {dropped_internal:,} internal (Symbiate) lines excluded",
        artifacts={"history": hist, "weekly": weekly})
