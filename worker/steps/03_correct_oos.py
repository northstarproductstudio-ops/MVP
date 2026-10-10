"""Step 3 — correct_oos. v1 has no in-stock store-days, so sell-in is not corrected; this
step writes availability signals per SKU × platform from own-brand market data."""

from __future__ import annotations

import pandas as pd

from worker.context import RunContext
from worker.steps import StepResult, step


@step(name="correct_oos", no=3, reads=["market_daily"], writes=["oos", "variable_value"],
      config_keys=["base.method", "market.osa_window_days", "market.osa_min_window_days"], version="1.0")
def correct_oos(ctx: RunContext) -> StepResult:
    md = ctx.table("market_daily")
    cols = ["sku_code", "platform", "osa_28d", "osa_min_7d", "competitor_osa", "category"]
    if md.empty:
        return StepResult(status="skipped", warnings=["No market data: OSA signals off"],
                          artifacts={"oos": pd.DataFrame(columns=cols)})
    if ctx.config.get("base.method") == "ros":
        raise RuntimeError("base.method = ros needs in-stock store-days, which no source provides in v1")
    md = md[md["market_date"] <= ctx.as_of]
    end = pd.Timestamp(md["market_date"].max())
    w = int(ctx.config.get("market.osa_window_days"))
    w7 = int(ctx.config.get("market.osa_min_window_days"))
    win = md[md["market_date"] > end - pd.Timedelta(days=w)]
    own = win[win["is_own_brand"] & win["sku_code"].notna()]
    comp = win[~win["is_own_brand"]]
    osa = own.groupby(["sku_code", "platform"], as_index=False).agg(
        osa_28d=("osa_pct", "mean"), category=("category", "first"))
    last7 = own[own["market_date"] > end - pd.Timedelta(days=w7)]
    daily = last7.groupby(["sku_code", "platform", "market_date"])["osa_pct"].mean()
    mn = daily.groupby(level=[0, 1]).min().rename("osa_min_7d").reset_index()
    comp_med = comp.groupby(["platform", "category"])["osa_pct"].median().rename("competitor_osa").reset_index()
    out = osa.merge(mn, on=["sku_code", "platform"], how="left").merge(
        comp_med, on=["platform", "category"], how="left")
    vv = []
    for r in out.itertuples():
        for n in ("osa_28d", "osa_min_7d", "competitor_osa"):
            val = getattr(r, n)
            if pd.notna(val):
                vv.append({"line_id": f"{r.sku_code}|ALL|{r.platform}", "name": n, "value_num": float(val),
                           "value_text": None})
    return StepResult(rows_in=len(md), rows_out=len(out),
                      summary=f"OSA signals for {len(out)} SKU × platform pairs over the last {w} days "
                              f"of market data (signal only; sell-in not corrected in v1)",
                      artifacts={"oos": out[cols]},
                      writes={"variable_value": pd.DataFrame(vv, columns=["line_id", "name", "value_num",
                                                                          "value_text"])})
