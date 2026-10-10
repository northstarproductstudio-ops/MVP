"""Actual sell-in per plan line for a month (back-test scoring)."""

from __future__ import annotations

import pandas as pd


def line_actuals(sellin: pd.DataFrame, month: pd.Timestamp, cfg) -> dict[str, float]:
    statuses = cfg.get("history.sellin_statuses")
    s = sellin[sellin["status"].isin(statuses) & ~sellin["is_internal"]]
    s = s[s["order_date"].dt.to_period("M").dt.to_timestamp() == pd.Timestamp(month)]
    q = (s["qty_invoiced"].fillna(0) + s["qty_cancelled"].fillna(0)).astype(float)
    key = s["sku_code"] + "|" + s["city"] + "|" + s["platform"]
    return q.groupby(key).sum().to_dict()
