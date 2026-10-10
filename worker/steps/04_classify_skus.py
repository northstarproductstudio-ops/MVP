"""Step 4 — classify_skus: ABC on national sell-in value; Syntetos-Boylan on weekly sell-in."""

from __future__ import annotations

import numpy as np
import pandas as pd

from worker.context import RunContext, add_months
from worker.steps import StepResult, step


def sb_class(adi: float, cv2: float, adi_cut: float, cv2_cut: float) -> str:
    if adi < adi_cut and cv2 < cv2_cut:
        return "smooth"
    if adi < adi_cut:
        return "erratic"
    if cv2 < cv2_cut:
        return "intermittent"
    return "lumpy"


@step(name="classify_skus", no=4, reads=["history", "weekly"], writes=["classes", "variable_value"],
      config_keys=["abc.cutoffs", "sb.adi", "sb.cv2", "history.new_weeks", "history.months"],
      version="1.0")
def classify_skus(ctx: RunContext) -> StepResult:
    cfg = ctx.config
    hist = ctx.artifact("history")
    weekly = ctx.artifact("weekly")
    months = ctx.history_months()
    window_start = months[-1]
    h = hist[(hist["month"] >= window_start) & (hist["month"] <= months[0])]
    nat = h.groupby("sku_code").agg(value=("value", "sum"), q=("q", "sum"))
    use_value = nat["value"].sum() > 0
    basis = nat["value"] if use_value else nat["q"]
    basis = basis.sort_values(ascending=False)
    share = basis.cumsum() / (basis.sum() or 1)
    a_cut, b_cut = cfg.get("abc.cutoffs")
    abc = pd.Series(np.where(share.shift(fill_value=0) < a_cut, "A",
                             np.where(share.shift(fill_value=0) < b_cut, "B", "C")), index=share.index)

    # Weekly national series over the history window, zero-filled.
    wk = weekly.groupby(["sku_code", "week"])["q"].sum().reset_index()
    first_week = pd.Timestamp(window_start).to_period("W-SUN").start_time
    last_week = ctx.as_of.to_period("W-SUN").start_time
    all_weeks = pd.date_range(first_week, last_week, freq="7D")
    first_sale = weekly[weekly["q"] > 0].groupby("sku_code")["week"].min()
    new_cut = ctx.as_of - pd.Timedelta(weeks=int(cfg.get("history.new_weeks")))
    rows = []
    for sku in sorted(set(nat.index) | set(wk["sku_code"])):
        s = wk[wk["sku_code"] == sku].set_index("week")["q"].reindex(all_weeks, fill_value=0)
        nz = s[s > 0]
        adi = len(s) / len(nz) if len(nz) else float("inf")
        cv2 = float((nz.std(ddof=0) / nz.mean()) ** 2) if len(nz) > 1 and nz.mean() > 0 else 0.0
        cls = sb_class(adi, cv2, cfg.get("sb.adi"), cfg.get("sb.cv2"))
        fs = first_sale.get(sku)
        is_new = bool(fs is not None and fs > new_cut)
        rows.append({"sku_code": sku, "abc": abc.get(sku, "C"), "sb_class": "new" if is_new else cls,
                     "sb_pattern": cls, "adi": round(adi, 4) if np.isfinite(adi) else None,
                     "cv2": round(cv2, 4), "value": float(nat["value"].get(sku, 0.0)),
                     "first_sale": fs, "is_new": is_new})
    classes = pd.DataFrame(rows)
    vv = [{"line_id": f"{r['sku_code']}|ALL|ALL", "name": n, "value_num": None, "value_text": r[k]}
          for r in rows for n, k in (("abc", "abc"), ("sb_class", "sb_class"))]
    counts = classes["sb_class"].value_counts().to_dict() if not classes.empty else {}
    _ = add_months
    return StepResult(rows_in=len(weekly), rows_out=len(classes),
                      summary=f"{len(classes)} SKUs classified: ABC by "
                              f"{'sell-in value' if use_value else 'units'}; patterns "
                              + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())),
                      artifacts={"classes": classes},
                      writes={"variable_value": pd.DataFrame(vv)})
