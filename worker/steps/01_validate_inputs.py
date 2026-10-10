"""Step 1 — validate_inputs. Hard checks fail the run; soft checks warn and switch the
named steps off."""

from __future__ import annotations

import pandas as pd

from worker.context import RunContext
from worker.steps import StepResult, step


class InputError(RuntimeError):
    """Hard validation failure; message lists the problems for the user."""


@step(name="validate_inputs", no=1,
      reads=["sellin_line", "po_line", "stock_snapshot", "market_daily", "sku_crosswalk", "sku_master"],
      writes=["inputs"],
      config_keys=["history.months", "stock.stale_days", "crosswalk.hard_fail_unmapped",
                   "history.sellin_statuses"],
      version="1.0")
def validate_inputs(ctx: RunContext) -> StepResult:
    cfg = ctx.config
    sellin = ctx.table("sellin_line")
    po = ctx.table("po_line")
    stock = ctx.table("stock_snapshot")
    market = ctx.table("market_daily")
    cw = ctx.table("sku_crosswalk")
    hard: list[str] = []
    warn: list[str] = []

    if sellin.empty:
        raise InputError("No ready sales-order dataset. Import a Zoho sales-order export first.")
    for col, label in (("order_date", "order date"), ("sku_code", "SKU"), ("customer_name", "customer"),
                       ("qty_invoiced", "quantity invoiced")):
        if sellin[col].isna().all():
            hard.append(f"Required field {label} is unmapped or empty")
    bad_dates = int(sellin["order_date"].isna().sum())
    if bad_dates:
        hard.append(f"{bad_dates} sales-order lines have dates that do not parse")
    dupes = int(sellin["line_key"].duplicated().sum())
    if dupes:
        hard.append(f"{dupes} duplicate sales-order lines after de-duplication")
    neg = int(((sellin["qty_invoiced"] < 0) | (sellin["qty_ordered"] < 0) | (sellin["qty_cancelled"] < 0)).sum())
    if neg:
        hard.append(f"{neg} sales-order lines have negative quantities")
    for name, df, key in (("PO", po, ["platform", "po_number", "platform_item_id"]),
                          ("stock", stock, ["platform", "snapshot_date", "facility", "platform_item_id"])):
        if not df.empty and int(df.duplicated(subset=key).sum()):
            hard.append(f"Duplicate {name} rows on their natural key")
        if not df.empty and name == "PO" and int((df[["qty_ordered", "qty_received"]] < 0).any(axis=1).sum()):
            hard.append("PO lines with negative quantities")

    # Crosswalk rule: a SKU with sell-in that appears in a platform file (a platform item
    # proposes it) but has no confirmed or auto crosswalk row fails the run hard. Platform
    # rows whose ID resolves to no SKU at all are excluded from factors with a warning.
    statuses = cfg.get("history.sellin_statuses")
    live = sellin[sellin["status"].isin(statuses) & ~sellin["is_internal"]]
    sold = set(live["sku_code"].dropna())
    pending: dict[str, set[str]] = {}
    if not cw.empty:
        proposed = cw["proposed_sku"] if "proposed_sku" in cw else pd.Series([None] * len(cw), index=cw.index)
        review = cw[cw["match_method"] == "review"]
        accepted = set(zip(cw.loc[cw["match_method"].isin(["ean", "auto", "manual"]), "platform"],
                           cw.loc[cw["match_method"].isin(["ean", "auto", "manual"]), "sku_code"], strict=False))
        for idx, r in review.iterrows():
            sku = proposed.get(idx) if pd.notna(proposed.get(idx)) else r["sku_code"]
            if sku in sold and (r["platform"], sku) not in accepted:
                pending.setdefault(r["platform"], set()).add(sku)
    if pending:
        parts = [f"{p}: {', '.join(sorted(s)[:15])}{' …' if len(s) > 15 else ''} ({len(s)})"
                 for p, s in sorted(pending.items())]
        msg = ("SKUs with sell-in appear in platform files but their crosswalk rows await review — "
               + "; ".join(parts))
        if cfg.get("crosswalk.hard_fail_unmapped"):
            hard.append(msg + ". Confirm them in the crosswalk review (MVP-09) and run again.")
        else:
            warn.append(msg + ". Platform factors for these SKUs are skipped.")
    for name, df in (("PO", po), ("stock", stock), ("market", market)):
        if not df.empty:
            orphan = int(df["sku_code"].isna().sum())
            if orphan:
                warn.append(f"{orphan} {name} rows resolve to no SKU and are excluded from factors")

    if hard:
        raise InputError("Run stopped: " + " | ".join(hard))

    if po.empty:
        warn.append("No PO files: fill-rate factor off")
    if stock.empty:
        warn.append("No stock file: stock-cover factor and netting off")
    else:
        snap = stock["snapshot_date"].max()
        age = int((ctx.as_of - snap).days) if pd.notna(snap) else None
        if age is not None and age > int(cfg.get("stock.stale_days")):
            warn.append(f"Stock snapshot is {age} days old (> {cfg.get('stock.stale_days')}): stock cover flagged stale")
    if market.empty:
        warn.append("No market file: sell-through, OSA signal and market ceiling off")
    months_present = sorted(live["order_date"].dt.to_period("M").unique())
    if len(months_present) < int(cfg.get("history.months")):
        warn.append(f"Only {len(months_present)} months of sell-in (< {cfg.get('history.months')}); "
                    "base uses the months present")

    inputs = pd.DataFrame([{
        "has_po": not po.empty, "has_stock": not stock.empty, "has_market": not market.empty,
        "sellin_rows": len(live), "months_present": len(months_present),
        "stock_snapshot_date": stock["snapshot_date"].max() if not stock.empty else pd.NaT,
    }])
    total_rows = sum(len(t) for t in (sellin, po, stock, market))
    return StepResult(rows_in=total_rows, rows_out=len(live), warnings=warn,
                      summary=f"Read {total_rows:,} rows; {len(live):,} sell-in lines usable "
                              f"across {live['sku_code'].nunique()} SKUs",
                      artifacts={"inputs": inputs})
