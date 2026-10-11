"""Step 5 — base_forecast: national base per SKU × platform, split to cities.

    B(s,p) = w1·q(M1) + w2·q(M2) + w3·q(M3)                  smooth / erratic
    B(s,p) = mean of months sold                              new SKU
    B(s,p) = Croston-SBA(weekly) × days_in_target / 7         intermittent / lumpy
    share(c|s,p) = weighted sell-in in c ÷ weighted national  (category share on p when
                                                               national < split.min_units)
    base(s,c,p) = B(s,p) × share(c|s,p)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.domain.categories import UNCATEGORISED, resolve_plan_categories
from worker.context import RunContext, line_id
from worker.steps import StepResult, step


def croston_sba(series: np.ndarray, alpha: float = 0.1) -> float:
    """Croston with Syntetos-Boylan bias correction; per-period forecast.

    Same recursion as statsforecast's CrostonSBA (SES on non-zero sizes and on intervals,
    alpha fixed, × (1 − alpha/2))."""
    y = np.asarray(series, dtype=float)
    nz = np.flatnonzero(y > 0)
    if len(nz) == 0:
        return 0.0
    sizes = y[nz]
    intervals = np.diff(np.concatenate([[-1], nz])).astype(float)
    z, p = sizes[0], intervals[0]
    for s, i in zip(sizes[1:], intervals[1:], strict=False):
        z = z + alpha * (s - z)
        p = p + alpha * (i - p)
    return float((1 - alpha / 2) * z / p)


@step(name="base_forecast", no=5,
      reads=["history", "weekly", "classes", "sku_master", "sellin_line", "po_line", "market_daily",
             "extra_value"], writes=["base", "variable_value"],
      config_keys=["base.method", "base.weights", "base.method_intermittent", "base.croston_alpha",
                   "split.min_units", "history.months", "plan.cities"],
      version="1.1")
def base_forecast(ctx: RunContext) -> StepResult:
    cfg = ctx.config
    hist = ctx.artifact("history")
    weekly = ctx.artifact("weekly")
    classes = ctx.artifact("classes").set_index("sku_code")
    months = ctx.history_months()
    weights = list(cfg.get("base.weights"))[: len(months)]
    present = set(hist["month"].unique())
    pairs = [(m, w) for m, w in zip(months, weights, strict=False) if m in present]
    wsum = sum(w for _, w in pairs) or 1.0
    wmap = {m: w / wsum for m, w in pairs}  # renormalised over months present
    h = hist[hist["month"].isin(wmap)].copy()
    h["wq"] = h["q"] * h["month"].map(wmap)

    # National per SKU × platform
    nat_m = h.groupby(["sku_code", "platform", "month"])["q"].sum().reset_index()
    nat_w = h.groupby(["sku_code", "platform"])["wq"].sum()
    alpha = float(cfg.get("base.croston_alpha"))
    all_weeks = pd.date_range(min(wmap).to_period("W-SUN").start_time,
                              ctx.as_of.to_period("W-SUN").start_time, freq="7D") if wmap else []
    rows = []
    for (sku, plat), wq in nat_w.items():
        cls = classes["sb_class"].get(sku, "smooth")
        method = "weighted"
        if cls == "new":
            sold = nat_m[(nat_m.sku_code == sku) & (nat_m.platform == plat) & (nat_m.q > 0)]["q"]
            b = float(sold.mean()) if len(sold) else 0.0
            method = "new_mean"
        elif cls in ("intermittent", "lumpy") and cfg.get("base.method_intermittent") == "croston_sba":
            s = weekly[(weekly.sku_code == sku) & (weekly.platform == plat)].set_index("week")["q"]
            s = s.reindex(all_weeks, fill_value=0)
            b = croston_sba(s.to_numpy(), alpha) * ctx.days_in_target / 7.0
            method = "croston_sba"
        else:
            b = float(wq)
        rows.append({"sku_code": sku, "platform": plat, "B_np": b, "base_method": method, "nat_w": float(wq)})
    nat = pd.DataFrame(rows)

    # City shares
    skus = sorted(set(h["sku_code"]))
    resolved = resolve_plan_categories(
        skus, {t: ctx.table(t) for t in ("sku_master", "sellin_line", "po_line", "market_daily", "extra_value")
               if t in ctx.tables}, ctx.template_path)
    cats = {r.sku_code: (r.plan_category or UNCATEGORISED) for r in resolved.itertuples()}
    cat_src = dict(zip(resolved["sku_code"], resolved["category_source"], strict=False))
    h["plan_category"] = h["sku_code"].map(cats)
    city_w = h.groupby(["sku_code", "platform", "city"])["wq"].sum().reset_index()
    cat_city = h.groupby(["plan_category", "platform", "city"])["wq"].sum()
    cat_tot = h.groupby(["plan_category", "platform"])["wq"].sum()
    plat_city = h.groupby(["platform", "city"])["wq"].sum()
    plat_tot = h.groupby("platform")["wq"].sum()
    min_units = float(cfg.get("split.min_units"))
    out = []
    for r in nat.itertuples():
        cat = cats.get(r.sku_code)
        if r.nat_w >= min_units:
            sub = city_w[(city_w.sku_code == r.sku_code) & (city_w.platform == r.platform)]
            shares = dict(zip(sub.city, sub.wq / r.nat_w, strict=False))
            basis = "sku"
        elif cat_tot.get((cat, r.platform), 0) > 0:
            tot = cat_tot[(cat, r.platform)]
            shares = {c: v / tot for (cc, p, c), v in cat_city.items() if cc == cat and p == r.platform}
            basis = "category"
        elif plat_tot.get(r.platform, 0) > 0:
            tot = plat_tot[r.platform]
            shares = {c: v / tot for (p, c), v in plat_city.items() if p == r.platform}
            basis = "platform"
        else:
            shares, basis = {"Other": 1.0}, "none"
        for city, sh in shares.items():
            if sh <= 0:
                continue
            out.append({"line_id": line_id(r.sku_code, city, r.platform), "sku_code": r.sku_code,
                        "city": city, "platform": r.platform, "plan_category": cat,
                        "category_source": cat_src.get(r.sku_code, "none"),
                        "B_np": r.B_np, "base_method": r.base_method, "share": sh,
                        "share_basis": basis, "base": r.B_np * sh})
    base = pd.DataFrame(out)
    names = hist.groupby("sku_code")["item_name"].last()
    base["item_name"] = base["sku_code"].map(names)
    vv = pd.concat([
        base[["line_id"]].assign(name="base_np", value_num=base["B_np"], value_text=None),
        base[["line_id"]].assign(name="city_share", value_num=base["share"], value_text=base["share_basis"]),
    ], ignore_index=True)
    fallback = int((base.drop_duplicates(["sku_code", "platform"])["share_basis"] != "sku").sum()) if len(base) else 0
    return StepResult(
        rows_in=len(h), rows_out=len(base),
        summary=f"Base demand for {len(nat)} SKU × platform pairs split into {len(base)} plan lines "
                f"(weights {', '.join(f'{m:%b} {w:.2f}' for m, w in wmap.items())}); "
                f"{fallback} pairs use a category city split",
        artifacts={"base": base}, writes={"variable_value": vv})
