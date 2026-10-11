"""Step 6 — derive_factors (Python factors) and per-line signals.

Multipliers are written as m (effect × (1 + m)); additives in units. Signals are facts
for the planning agent and are never multiplied in.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from core.domain.factor_sources import FACTOR_GROUPS, dataset_ids, source_label
from worker.context import RunContext
from worker.steps import StepResult, step

FACTOR_COLS = ["name", "kind", "origin", "scope", "derived_value", "value", "enabled", "basis", "note",
               "sku_code", "city", "platform"]


def _fmt_pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def market_units(md: pd.DataFrame) -> pd.Series:
    """Daily units: sales_sp ÷ sp, or sales_mrp ÷ mrp where SP sales are absent (Zepto)."""
    sp_units = md["sales_sp"] / md["sp"].replace(0, np.nan)
    mrp_units = md["sales_mrp"] / md["mrp"].replace(0, np.nan)
    return sp_units.where(md["sales_sp"].notna(), mrp_units).fillna(0.0)


def market_value(md: pd.DataFrame) -> pd.Series:
    return md["sales_sp"].where(md["sales_sp"].notna(), md["sales_mrp"]).fillna(0.0)


@step(name="derive_factors", no=6,
      reads=["history", "classes", "base", "oos", "inputs", "po_line", "stock_snapshot", "market_daily",
             "sellin_line"],
      writes=["factors", "signals", "factor", "variable_value"],
      config_keys=["trend.cap", "sellthrough.cap", "sellthrough.osa_floor", "fill.lookback_days",
                   "fill.threshold", "fill.cap", "stock.target_doh", "stock.stale_days",
                   "plan.quick_commerce", "market.price_window_days", "history.months",
                   "base.weights"],
      version="1.1")
def derive_factors(ctx: RunContext) -> StepResult:
    cfg = ctx.config
    hist = ctx.artifact("history")
    classes = ctx.artifact("classes").set_index("sku_code")
    base = ctx.artifact("base")
    oos = ctx.artifact("oos")
    po = ctx.table("po_line")
    stock = ctx.table("stock_snapshot")
    md = ctx.table("market_daily")
    md = md[md["market_date"] <= ctx.as_of]
    months = ctx.history_months()
    m1, m3 = months[0], months[-1]
    h = ctx.horizon
    factors: list[dict] = []
    warnings: list[str] = []
    flags: dict[tuple[str, str], list[str]] = {}

    nat = hist.groupby(["sku_code", "platform", "month"])["q"].sum()
    pairs = base[["sku_code", "platform"]].drop_duplicates()

    def add(name, kind, value, basis, sku=None, city=None, platform=None, enabled=True, note=None):
        scope = {k: v for k, v in (("sku", sku), ("city", city), ("platform", platform)) if v}
        factors.append({"name": name, "kind": kind, "origin": "system", "scope": json.dumps(scope, sort_keys=True),
                        "derived_value": float(value), "value": float(value), "enabled": enabled,
                        "basis": basis, "note": note, "sku_code": sku, "city": city, "platform": platform})

    # trend ------------------------------------------------------------------------
    cap = float(cfg.get("trend.cap"))
    for r in pairs.itertuples():
        cls = classes["sb_class"].get(r.sku_code, "smooth")
        if cls in ("intermittent", "lumpy", "new"):
            continue
        q1 = float(nat.get((r.sku_code, r.platform, m1), 0.0))
        q3 = float(nat.get((r.sku_code, r.platform, m3), 0.0))
        if q3 <= 0:
            continue
        span = len(months) - 1 or 1
        g = (q1 / q3) ** (1 / span) - 1
        m = float(np.clip((1 + g) ** h - 1, -cap, cap))
        add("trend", "multiplier", round(m, 6),
            f"{m3:%b} {q3:,.0f} → {m1:%b} {q1:,.0f}, {h} months forward, cap {cap:.0%}",
            sku=r.sku_code, platform=r.platform)

    # sell-through -----------------------------------------------------------------
    qc = set(cfg.get("plan.quick_commerce"))
    m1_end = m1 + pd.offsets.MonthEnd(0)
    md_m1 = md[(md["market_date"] >= m1) & (md["market_date"] <= m1_end) & md["is_own_brand"]
               & md["sku_code"].notna()].copy()
    st_cap = float(cfg.get("sellthrough.cap"))
    floor = float(cfg.get("sellthrough.osa_floor"))
    if not md_m1.empty:
        md_m1["u"] = market_units(md_m1)
        osa = (md_m1["osa_pct"].fillna(100.0) / 100.0).clip(lower=floor)
        md_m1["u_corr"] = md_m1["u"] / osa
        S = md_m1.groupby(["sku_code", "platform"])["u_corr"].sum()
        for (sku, plat), s in S.items():
            if plat not in qc:
                continue
            p = float(nat.get((sku, plat, m1), 0.0))
            if p <= 0:
                continue
            m = float(np.clip(s / p - 1, -st_cap, st_cap))
            add("sell_through", "multiplier", round(m, 6),
                f"{m1:%b} secondary {s:,.0f} (OSA-corrected) vs primary {p:,.0f}, cap {st_cap:.0%}",
                sku=sku, platform=plat)
    elif not md.empty:
        warnings.append(f"Market data has no {m1:%b %Y} rows: sell-through factor off")

    # fill rate --------------------------------------------------------------------
    fr_rates: dict[tuple[str, str], float] = {}
    if not po.empty:
        lb = ctx.as_of - pd.Timedelta(days=int(cfg.get("fill.lookback_days")))
        win = po[(po["po_date"] >= lb) & (po["po_date"] <= ctx.as_of) & po["sku_code"].notna()]
        comp = win[win["status"] == "completed"]
        agg = comp.groupby(["sku_code", "platform"])[["qty_received", "qty_ordered"]].sum()
        thr, fcap = float(cfg.get("fill.threshold")), float(cfg.get("fill.cap"))
        for (sku, plat), r in agg.iterrows():
            if r.qty_ordered <= 0:
                continue
            fr = float(r.qty_received) / float(r.qty_ordered)
            fr_rates[(sku, plat)] = fr
            m = min(fcap, (1 / fr - 1) if fr > 0 else fcap) if fr < thr else 0.0
            add("fill_rate", "multiplier", round(m, 6),
                f"Fill rate {fr:.0%} on {int(r.qty_ordered):,} ordered over {cfg.get('fill.lookback_days')} days "
                f"(threshold {thr:.0%}, cap {fcap:.0%})", sku=sku, platform=plat)
        exp = win[(win["status"] == "expired") & (win["qty_received"].fillna(0) == 0)]
        for (sku, plat) in exp.groupby(["sku_code", "platform"]).groups:
            flags.setdefault((sku, plat), []).append("po_expiry_shortfall")

    # stock cover (Blinkit, additive) ------------------------------------------------
    stock_info: dict[tuple[str, str], dict] = {}
    if not stock.empty:
        snap = stock["snapshot_date"].max()
        age = int((ctx.as_of - snap).days) if pd.notna(snap) else None
        stale = age is not None and age > int(cfg.get("stock.stale_days"))
        target = float(cfg.get("stock.target_doh"))
        s = stock[stock["sku_code"].notna() & (stock["snapshot_date"] == snap)]
        g = s.groupby(["sku_code", "city"]).agg(rr=("run_rate", "sum"), soh=("soh_units", "sum"),
                                                rr_n=("run_rate", "count"))
        bl = base[base["platform"] == "blinkit"].set_index(["sku_code", "city"])["base"]
        for (sku, city), r in g.iterrows():
            stock_info[(sku, city)] = {"soh": float(r.soh), "rr": float(r.rr) if r.rr_n else None,
                                       "age": age, "stale": stale}
            if (sku, city) not in bl.index or not r.rr_n or r.rr <= 0:
                continue
            doh = float(r.soh) / float(r.rr)
            stock_info[(sku, city)]["doh"] = doh
            a = max((target - doh) * float(r.rr), -float(bl[(sku, city)]))
            add("stock_cover", "additive", round(a, 4),
                f"DOH {doh:.0f} vs target {target:.0f} at run rate {r.rr:.1f}/day"
                + (f"; snapshot {age} days old (stale)" if stale else ""),
                sku=sku, city=city, platform="blinkit", note="stale_stock" if stale else None)

    # seasonal (history index) — needs 12 months; written disabled in v1 ------------
    add("seasonal", "multiplier", 0.0, "Seasonal index needs 12 months of history; off in v1 "
        "(the planning agent supplies seasonality)", enabled=False)
    warnings.append("Seasonal index off: needs 12 months of history")

    # signals per line -------------------------------------------------------------
    pw = int(cfg.get("market.price_window_days"))
    own = md[md["is_own_brand"] & md["sku_code"].notna()]
    end = pd.Timestamp(md["market_date"].max()) if not md.empty else None
    price_sig: dict[tuple[str, str], dict] = {}
    if end is not None and not own.empty:
        now = own[own["market_date"] > end - pd.Timedelta(days=pw)]
        prior = own[(own["market_date"] <= end - pd.Timedelta(days=pw)) &
                    (own["market_date"] > end - pd.Timedelta(days=2 * pw))]
        for key, grp in now.groupby(["sku_code", "platform"]):
            pr = prior[(prior.sku_code == key[0]) & (prior.platform == key[1])]
            sp_now = grp["sp"].mean()
            sp_pr = pr["sp"].mean() if len(pr) else np.nan
            price_sig[key] = {
                "discount_now_vs_prior": _r(grp["discount_pct"].mean() - pr["discount_pct"].mean()) if len(pr) else None,
                "price_change_pct": _r((sp_now / sp_pr - 1) * 100) if len(pr) and sp_pr else None,
                "category_share_now": _r(grp.groupby("market_date")["cat_share_sp"].sum().mean()),
                "current_sp": _r(sp_now),
            }
    open_po = {}
    if not po.empty:
        op = po[(po["status"] == "open") & po["sku_code"].notna()]
        open_po = op.groupby(["sku_code", "platform", "city"])["qty_open"].sum().to_dict()
    lh = hist[hist["month"].isin(months)].groupby(["sku_code", "city", "platform", "month"])["q"].sum()
    oos_i = oos.set_index(["sku_code", "platform"]) if not oos.empty else None
    sig_rows = []
    for r in base.itertuples():
        q1 = float(lh.get((r.sku_code, r.city, r.platform, m1), 0.0))
        avg3 = float(np.mean([lh.get((r.sku_code, r.city, r.platform, m), 0.0) for m in months]))
        sig: dict = {"last_month_sellin": round(q1), "avg_3m_sellin": round(avg3, 1)}
        if oos_i is not None and (r.sku_code, r.platform) in oos_i.index:
            o = oos_i.loc[(r.sku_code, r.platform)]
            sig["osa_28d"] = _r(o["osa_28d"])
            sig["competitor_osa"] = _r(o["competitor_osa"])
        sig.update({k: v for k, v in price_sig.get((r.sku_code, r.platform), {}).items() if v is not None})
        if (r.sku_code, r.platform) in fr_rates:
            sig["fill_rate"] = round(fr_rates[(r.sku_code, r.platform)], 3)
        opq = open_po.get((r.sku_code, r.platform, r.city))
        if opq:
            sig["open_po_qty"] = int(opq)
        if r.platform == "blinkit" and (r.sku_code, r.city) in stock_info:
            si = stock_info[(r.sku_code, r.city)]
            sig["stock_age_days"] = si["age"]
            sig["soh"] = int(si["soh"])
            if si.get("doh") is not None:
                sig["doh"] = round(si["doh"], 1)
        fl = list(flags.get((r.sku_code, r.platform), []))
        if r.platform == "blinkit" and stock_info.get((r.sku_code, r.city), {}).get("stale"):
            fl.append("stale_stock")
        sig_rows.append({"line_id": r.line_id, "signals": json.dumps(sig, sort_keys=True),
                         "flags": json.dumps(sorted(set(fl)))})
    signals = pd.DataFrame(sig_rows, columns=["line_id", "signals", "flags"])
    fdf = pd.DataFrame(factors, columns=FACTOR_COLS)
    # Gap 8b: every factor row names the data it came from
    tables = {t: ctx.table(t) for t in ("sellin_line", "po_line", "stock_snapshot", "market_daily")}
    src_ids = {n: _uuids(dataset_ids(tables, FACTOR_GROUPS.get(n, ("sellin",)))) for n in fdf["name"].unique()}
    fdf["source_label"] = fdf["name"].map(source_label)
    fdf["source_dataset_ids"] = fdf["name"].map(lambda n: src_ids.get(n, []))
    vv = [{"line_id": s["line_id"], "name": f"signal:{k}", "value_num": v if isinstance(v, (int, float)) else None,
           "value_text": None if isinstance(v, (int, float)) else str(v)}
          for s in sig_rows for k, v in json.loads(s["signals"]).items()]
    counts = fdf[fdf["enabled"]].groupby("name").size().to_dict()
    return StepResult(
        rows_in=len(base), rows_out=len(fdf),
        summary="Python factors: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
                + f"; signals for {len(signals)} lines",
        warnings=warnings,
        artifacts={"factors": fdf, "signals": signals},
        writes={"factor": fdf.drop(columns=["sku_code", "city", "platform"]),
                "variable_value": pd.DataFrame(vv, columns=["line_id", "name", "value_num", "value_text"])})


def _uuids(ids: list[str]) -> list[str]:
    import uuid

    out = []
    for i in ids:
        try:
            out.append(str(uuid.UUID(str(i))))
        except ValueError:
            continue  # local runs key datasets by file name
    return out


def _r(x) -> float | None:
    try:
        if x is None or pd.isna(x):
            return None
        return round(float(x), 3)
    except (TypeError, ValueError):
        return None
