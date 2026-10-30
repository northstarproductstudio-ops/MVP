"""The three run outputs: demand plan report (formatRef layout, numbers only), reasoning
report (one row per plan line) and the plan overview text (chat only)."""

from __future__ import annotations

import io
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from core.domain.categories import UNCATEGORISED, template_layout
from core.domain.plan_math import bridge_units

REASONING_COLUMNS = [
    "line_id", "sku_code", "item_name", "plan_category", "city", "platform", "abc", "sb_class", "base",
    "trend_m", "trend_units", "sell_through_m", "sell_through_units", "fill_rate_m", "fill_rate_units",
    "stock_cover_units", "agent_factors", "judgment_m", "judgment_units", "reference_dp", "pipeline_dp",
    "user_change", "final_dp", "order_qty", "reason", "confidence", "flags", "report_version",
]


# --------------------------------------------------------------------------- current version

def current_lines(dp: pd.DataFrame, edits: pd.DataFrame | None) -> pd.DataFrame:
    """Version-0 lines with every edit up to the current version applied.

    ``edits``: rows (version, line_id, dp_after, order_qty_after, note, edit) for versions
    1..current, ascending."""
    out = dp.copy()
    out["pipeline_dp"] = out["final_dp"]
    out["user_change"] = None
    if edits is None or edits.empty:
        return out
    idx = {lid: i for i, lid in zip(out.index, out["line_id"], strict=False)}
    for e in edits.sort_values("version").itertuples():
        i = idx.get(e.line_id)
        if i is None:
            continue
        before = out.at[i, "final_dp"]
        out.at[i, "final_dp"] = int(e.dp_after)
        if pd.notna(getattr(e, "order_qty_after", None)):
            out.at[i, "order_qty"] = int(e.order_qty_after)
        pct = (int(e.dp_after) / before - 1) * 100 if before else None
        change = f"v{e.version}: {before}→{int(e.dp_after)}" + (f" ({pct:+.0f}%)" if pct is not None else "")
        if e.note:
            change += f" — {e.note}"
        prev = out.at[i, "user_change"]
        out.at[i, "user_change"] = f"{prev}; {change}" if prev else change
    return out


# --------------------------------------------------------------------------- reasoning report

def reasoning_rows(lines: pd.DataFrame, version: int) -> pd.DataFrame:
    rows = []
    for r in lines.itertuples():
        applied = json.loads(r.applied_factors) if isinstance(r.applied_factors, str) else (r.applied_factors or [])
        bridge = json.loads(r.bridge) if isinstance(r.bridge, str) else (r.bridge or [])
        val = {f["name"]: f["value"] for f in applied}
        agents = [f for f in applied if f["name"].startswith("agent:")]
        agent_txt = ", ".join(f"{f['name']} {f['value'] * 100:+.0f}% ({bridge_units(bridge, f['name']):+d})"
                              for f in agents)
        flags = r.flags if isinstance(r.flags, (list, tuple)) else (json.loads(r.flags) if r.flags else [])
        rows.append({
            "line_id": r.line_id, "sku_code": r.sku_code, "item_name": r.item_name,
            "plan_category": r.plan_category, "city": r.city, "platform": r.platform, "abc": r.abc,
            "sb_class": r.sb_class, "base": round(float(r.base), 1),
            "trend_m": val.get("trend"), "trend_units": bridge_units(bridge, "trend"),
            "sell_through_m": val.get("sell_through"), "sell_through_units": bridge_units(bridge, "sell_through"),
            "fill_rate_m": val.get("fill_rate"), "fill_rate_units": bridge_units(bridge, "fill_rate"),
            "stock_cover_units": bridge_units(bridge, "stock_cover"), "agent_factors": agent_txt or None,
            "judgment_m": val.get("judgment"), "judgment_units": bridge_units(bridge, "judgment"),
            "reference_dp": r.reference_dp, "pipeline_dp": r.pipeline_dp, "user_change": r.user_change,
            "final_dp": int(r.final_dp), "order_qty": None if pd.isna(r.order_qty) else int(r.order_qty),
            "reason": r.reason, "confidence": r.confidence, "flags": ", ".join(flags) or None,
            "report_version": version,
        })
    return pd.DataFrame(rows, columns=REASONING_COLUMNS)


def export_reasoning_xlsx(rows: pd.DataFrame, meta: dict[str, Any]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Reasoning"
    ws.append([f"Reasoning report · target {meta['target_month']} · report version {meta['version']} · "
               f"run {meta['run_id']}"])
    ws.append(list(rows.columns))
    for c in ws[2]:
        c.font = Font(bold=True)
    for rec in rows.itertuples(index=False):
        ws.append([None if (isinstance(v, float) and pd.isna(v)) else v for v in rec])
    for i, col in enumerate(rows.columns, 1):
        ws.column_dimensions[get_column_letter(i)].width = min(40, max(10, len(col) + 2))
    ws.freeze_panes = "C3"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# --------------------------------------------------------------------------- plan report (formatRef)

def history_actuals(sellin: pd.DataFrame, target_month: pd.Timestamp, months: int,
                    statuses: list[str]) -> pd.DataFrame:
    """Primary sales per SKU × city × month for the plan report, from a run's snapshot
    sell-in, where cities and internal customers are already resolved as the pipeline saw
    them (core.sellin_line stores neither). The last ``months`` months before the target."""
    cols = ["sku_code", "city", "month", "q", "mrp"]
    if sellin is None or sellin.empty:
        return pd.DataFrame(columns=cols)
    internal = sellin["is_internal"].map(lambda v: str(v).strip().lower() in ("true", "1"))
    s = sellin[sellin["status"].isin(statuses) & ~internal].copy()
    s["month"] = pd.to_datetime(s["order_date"]).dt.to_period("M").dt.to_timestamp()
    s = s[s["month"] < target_month]
    s = s[s["month"].isin(sorted(s["month"].unique())[-months:])]
    s["q"] = pd.to_numeric(s["qty_invoiced"], errors="coerce").fillna(0) \
        + pd.to_numeric(s["qty_cancelled"], errors="coerce").fillna(0)
    s["mrp"] = pd.to_numeric(s["mrp"], errors="coerce")
    out = s.groupby(["sku_code", "city", "month"], as_index=False).agg(q=("q", "sum"), mrp=("mrp", "max"))
    return out[cols]


def export_plan_xlsx(template: Path, lines: pd.DataFrame, actuals: pd.DataFrame, factors: pd.DataFrame,
                     meta: dict[str, Any], cities: list[str], mrp: dict[str, float] | None = None) -> bytes:
    """Write the plan into the formatRef template, numbers only.

    * Overall DP: national DP per SKU (all platforms and cities, incl. Other) in a target
      column added after the last month column, grouped under template categories.
    * <City> DP tabs: city DP per SKU next to Primary Sales actuals per history month.
    * City shares, Factors used for Forecast.
    SKUs missing from the template are appended under their plan_category; SKUs with no
    plan show "-". Formulas and helper sheets from the template are replaced by values."""
    import openpyxl
    from openpyxl.styles import Font

    wb = openpyxl.load_workbook(template)
    layout = list(template_layout(str(template)))
    target = pd.Timestamp(meta["target_month"])
    nat = lines.groupby("sku_code")["final_dp"].sum()
    city_dp = lines.groupby(["sku_code", "city"])["final_dp"].sum()
    names = lines.groupby("sku_code")["item_name"].first()
    cats = lines.groupby("sku_code")["plan_category"].first()
    known = {s for _, s, _ in layout}
    extra = [(cats.get(s) or UNCATEGORISED, s, names.get(s) or s) for s in sorted(nat.index) if s not in known]
    months = sorted(actuals["month"].unique()) if not actuals.empty else []
    act = actuals.groupby(["sku_code", "city", "month"])["q"].sum() if not actuals.empty else pd.Series(dtype=float)
    act_nat = actuals.groupby(["sku_code", "month"])["q"].sum() if not actuals.empty else pd.Series(dtype=float)
    bold = Font(bold=True)

    def write_sheet(ws, value_for, actual_for) -> None:
        ws.delete_rows(1, ws.max_row)
        ws.append([f"Demand plan · {target:%b %Y} · report version {meta['version']}"])
        header = ["Product", "SKU Code", target.to_pydatetime()] + [pd.Timestamp(m).to_pydatetime() for m in months]
        ws.append(header)
        ws.append([None, None, "Demand Plan (pcs)"] + ["Primary Sales (pcs)"] * len(months))
        for c in list(ws[2]) + list(ws[3]):
            c.font = bold
        for c in ws[2][2:]:
            c.number_format = "mmm-yy"
        cur_cat = None
        for cat, sku, nm in layout + extra:
            if cat != cur_cat:
                ws.append([cat])
                ws.cell(ws.max_row, 1).font = bold
                cur_cat = cat
            v = value_for(sku)
            ws.append([names.get(sku) or nm, sku, v if v is not None else "-"]
                      + [actual_for(sku, m) for m in months])

    for name in list(wb.sheetnames):
        if name not in [f"{c} DP" for c in cities] + ["Overall DP", "Factors used for Forecast"]:
            del wb[name]
    ws = wb["Overall DP"] if "Overall DP" in wb.sheetnames else wb.create_sheet("Overall DP")
    write_sheet(ws, lambda s: int(nat[s]) if s in nat.index else None,
                lambda s, m: int(act_nat.get((s, m), 0)))
    for c in cities:
        title = f"{c} DP"
        ws = wb[title] if title in wb.sheetnames else wb.create_sheet(title)
        write_sheet(ws, lambda s, c=c: int(city_dp[(s, c)]) if (s, c) in city_dp.index else None,
                    lambda s, m, c=c: int(act.get((s, c, m), 0)))

    # City shares
    ws = wb.create_sheet("City shares")
    ws.append(["SKU Code", "Product", "MRP"] + cities + ["Other", "Share basis"])
    for c in ws[1]:
        c.font = bold
    basis = lines.groupby("sku_code")["share_basis"].first() if "share_basis" in lines else pd.Series(dtype=str)
    for s in sorted(nat.index):
        tot = float(nat[s]) or 1.0
        ws.append([s, names.get(s), (mrp or {}).get(s)]
                  + [round(float(city_dp.get((s, c), 0)) / tot, 4) for c in cities + ["Other"]]
                  + [basis.get(s)])

    # Factors used
    ws = wb["Factors used for Forecast"] if "Factors used for Forecast" in wb.sheetnames \
        else wb.create_sheet("Factors used for Forecast")
    ws.delete_rows(1, ws.max_row)
    ws.append(["Factor", "Source", "Applied this run", "Scopes applied", "Min value", "Max value", "Basis example"])
    for c in ws[1]:
        c.font = bold
    sources = {"trend": "Zoho sell-in", "sell_through": "Dcluttr secondary vs primary",
               "fill_rate": "Platform POs", "stock_cover": "Blinkit DOH", "seasonal": "History index (12 m)",
               "judgment": "Planning agent"}
    if not factors.empty:
        for name, g in factors.groupby("name", sort=True):
            enabled = g[g["enabled"]]
            src = sources.get(name, "Planning agent" if name.startswith("agent:") else "")
            ws.append([name, src, "yes" if len(enabled) else "no", int(len(enabled)),
                       float(enabled["value"].min()) if len(enabled) else None,
                       float(enabled["value"].max()) if len(enabled) else None,
                       str(g["basis"].iloc[0])[:200] if g["basis"].notna().any() else None])
    order = ["Overall DP"] + [f"{c} DP" for c in cities] + ["City shares", "Factors used for Forecast"]
    wb._sheets = [wb[n] for n in order if n in wb.sheetnames]  # noqa: SLF001
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# --------------------------------------------------------------------------- overview

def overview_facts(lines: pd.DataFrame, factors: pd.DataFrame, last_month_total: float,
                   target_month: str, version: int, warnings: list[str]) -> dict[str, Any]:
    flags = Counter(f for fl in lines["flags"] for f in (fl if isinstance(fl, (list, tuple)) else []))
    applied = Counter()
    agent_cats: dict[str, set[str]] = {}
    for r in lines.itertuples():
        for f in json.loads(r.applied_factors) if isinstance(r.applied_factors, str) else []:
            if f.get("value"):
                applied[f["name"]] += 1
                if f["name"].startswith("agent:"):
                    agent_cats.setdefault(f["name"], set()).add(r.plan_category or "")
    total = int(lines["final_dp"].sum())
    return {
        "target_month": target_month, "version": version, "total_dp": total,
        "last_month_sellin": int(last_month_total),
        "change_pct": round((total / last_month_total - 1) * 100, 1) if last_month_total else None,
        "lines": int(len(lines)), "skus": int(lines["sku_code"].nunique()),
        "by_city": {k: int(v) for k, v in lines.groupby("city")["final_dp"].sum().sort_values(ascending=False).items()},
        "by_platform": {k: int(v) for k, v in lines.groupby("platform")["final_dp"].sum().sort_values(ascending=False).items()},
        "factor_lines": dict(applied.most_common(12)),
        "agent_factor_categories": {k: sorted(v) for k, v in agent_cats.items()},
        "flags": dict(flags), "review": int(lines["flag_for_review"].sum()) if "flag_for_review" in lines else 0,
        "warnings": warnings[:6],
        "user_changes": int(lines["user_change"].notna().sum()) if "user_change" in lines else 0,
    }


def template_overview(f: dict[str, Any]) -> str:
    parts = [f"The {pd.Timestamp(f['target_month']):%B %Y} plan (version {f['version']}) totals "
             f"{f['total_dp']:,} units across {f['skus']} SKUs and {f['lines']} lines"
             + (f", {f['change_pct']:+.1f}% against last month's sell-in of {f['last_month_sellin']:,}."
                if f.get("change_pct") is not None else ".")]
    if f["by_city"]:
        top = list(f["by_city"].items())[:3]
        parts.append("Largest cities: " + ", ".join(f"{c} {v:,}" for c, v in top) + ".")
    if f["factor_lines"]:
        parts.append("Factors applied: " + ", ".join(f"{k} on {v} lines" for k, v in f["factor_lines"].items()) + ".")
    if f["agent_factor_categories"]:
        parts.append("Planning-agent factors: " + "; ".join(
            f"{k} for {', '.join(c for c in v if c) or 'some SKUs'}" for k, v in f["agent_factor_categories"].items()) + ".")
    fl = {k: v for k, v in f["flags"].items() if v}
    if fl or f.get("user_changes"):
        bits = [f"{v} {k.replace('_', ' ')}" for k, v in fl.items()]
        if f.get("user_changes"):
            bits.append(f"{f['user_changes']} lines changed by you")
        parts.append("Flags: " + ", ".join(bits) + ".")
    parts.append("See the reasoning report for the per-SKU detail.")
    return " ".join(parts)
