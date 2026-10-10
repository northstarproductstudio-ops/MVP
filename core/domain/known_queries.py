"""The eight parameterised Ask queries. Each runs on DuckDB over the chat snapshot plus the
current plan version (registered as ``plan_lines``). Parameters are bound, never formatted;
optional filters are composed from fixed SQL fragments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import duckdb
import pandas as pd

from core.domain.fenced import frame_records


class QueryParamError(ValueError):
    pass


@dataclass
class QueryResult:
    name: str
    rows: list[dict[str, Any]]
    row_count: int
    label: str
    section: str | None = None


def _where(filters: list[tuple[str, Any]]) -> tuple[str, list[Any]]:
    """filters: (fixed SQL fragment with one '?', value) — skipped when value is None."""
    parts, params = [], []
    for frag, val in filters:
        if val is None or val == "":
            continue
        parts.append(frag)
        params.append(val)
    return (" AND ".join(parts) if parts else "TRUE"), params


def _month(v: Any, name: str) -> pd.Timestamp | None:
    if v in (None, ""):
        return None
    try:
        return pd.Timestamp(v)
    except (ValueError, TypeError):
        raise QueryParamError(f"{name} must be a date (YYYY-MM-DD)") from None


def _sku_or_category(p: dict[str, Any]) -> list[tuple[str, Any]]:
    return [("s.sku_code = ?", p.get("sku")),
            ("coalesce(m.plan_category, 'Other') = ?", p.get("category"))]


SALES_BASE = """
FROM sellin_line s LEFT JOIN sku_master m USING (sku_code)
WHERE s.status IN ('invoiced','confirmed') AND NOT s.is_internal
"""


def sales_history(con: duckdb.DuckDBPyConnection, p: dict[str, Any]) -> QueryResult:
    grain = p.get("grain") or "month"
    if grain not in ("month", "week"):
        raise QueryParamError("grain must be month or week")
    trunc = "month" if grain == "month" else "week"
    filt, params = _where(_sku_or_category(p) + [
        ("s.city = ?", p.get("city")), ("s.platform = ?", p.get("platform")),
        ("s.order_date >= ?", _month(p.get("from"), "from")), ("s.order_date <= ?", _month(p.get("to"), "to"))])
    by = p.get("by") or "platform"
    if by not in ("platform", "city", "sku", "none"):
        raise QueryParamError("by must be platform, city, sku or none")
    group = {"platform": "s.platform", "city": "s.city", "sku": "s.sku_code", "none": "'all'"}[by]
    sql = f"""SELECT date_trunc('{trunc}', s.order_date)::DATE AS period, {group} AS {by if by != 'none' else 'total'},
              sum(coalesce(s.qty_invoiced,0) + coalesce(s.qty_cancelled,0))::BIGINT AS units
              {SALES_BASE} AND {filt} GROUP BY 1, 2 ORDER BY 1, 3 DESC"""
    df = con.execute(sql, params).df()
    return QueryResult("sales_history", frame_records(df), len(df), "Known query: sales_history")


def top_movers(con: duckdb.DuckDBPyConnection, p: dict[str, Any]) -> QueryResult:
    period = _month(p.get("period"), "period")
    compare = _month(p.get("compare_to"), "compare_to")
    if period is None:
        period = pd.Timestamp(con.execute("SELECT date_trunc('month', max(order_date)) FROM sellin_line").fetchone()[0])
    if compare is None:
        compare = period - pd.offsets.MonthBegin(1)
    n = int(p.get("n") or 10)
    direction = (p.get("direction") or "up").lower()
    if direction not in ("up", "down"):
        raise QueryParamError("direction must be up or down")
    metric = p.get("metric") or "sell-in"
    if metric == "secondary":
        sql = """WITH x AS (SELECT sku_code, platform, date_trunc('month', market_date) AS m,
                   sum(coalesce(sales_sp, sales_mrp)) AS v FROM market_daily WHERE is_own_brand AND sku_code IS NOT NULL
                   GROUP BY ALL)
                 SELECT a.sku_code, a.platform, round(a.v) AS now_value, round(coalesce(b.v,0)) AS prior_value,
                   round(a.v - coalesce(b.v,0)) AS change FROM x a LEFT JOIN x b
                   ON a.sku_code=b.sku_code AND a.platform=b.platform AND b.m = ?
                 WHERE a.m = ? ORDER BY change """ + ("DESC" if direction == "up" else "ASC") + " LIMIT ?"
        params = [compare, period, n]
    else:
        sql = """WITH x AS (SELECT s.sku_code, any_value(s.item_name) AS item_name, date_trunc('month', s.order_date) AS m,
                   sum(coalesce(s.qty_invoiced,0)+coalesce(s.qty_cancelled,0)) AS q """ + SALES_BASE + """ GROUP BY 1, 3)
                 SELECT a.sku_code, a.item_name, a.q::BIGINT AS now_units, coalesce(b.q,0)::BIGINT AS prior_units,
                   (a.q - coalesce(b.q,0))::BIGINT AS change,
                   CASE WHEN coalesce(b.q,0) > 0 THEN round((a.q / b.q - 1) * 100, 1) END AS change_pct
                 FROM x a LEFT JOIN x b ON a.sku_code=b.sku_code AND b.m = ?
                 WHERE a.m = ? ORDER BY change """ + ("DESC" if direction == "up" else "ASC") + " LIMIT ?"
        params = [compare, period, n]
    df = con.execute(sql, params).df()
    return QueryResult("top_movers", frame_records(df), len(df), "Known query: top_movers")


def plan_vs_history(con: duckdb.DuckDBPyConnection, p: dict[str, Any]) -> QueryResult:
    _need_plan(con)
    by = p.get("by") or "city"
    if by not in ("city", "platform", "sku", "category"):
        raise QueryParamError("by must be city, platform, sku or category")
    col = {"city": "city", "platform": "platform", "sku": "sku_code", "category": "plan_category"}[by]
    filt, params = _where([("sku_code = ?", p.get("sku")), ("plan_category = ?", p.get("category")),
                           ("city = ?", p.get("city")), ("platform = ?", p.get("platform"))])
    sql = f"""WITH plan AS (SELECT {col} AS k, sum(final_dp)::BIGINT AS plan_units FROM plan_lines WHERE {filt} GROUP BY 1),
              last AS (SELECT {col} AS k, sum(last_month_sellin)::BIGINT AS last_month_units FROM plan_lines WHERE {filt} GROUP BY 1)
              SELECT plan.k AS {by}, plan_units, last_month_units,
                CASE WHEN last_month_units > 0 THEN round((plan_units / last_month_units - 1) * 100, 1) END AS change_pct
              FROM plan LEFT JOIN last USING (k) ORDER BY plan_units DESC"""
    df = con.execute(sql, params + params).df()
    return QueryResult("plan_vs_history", frame_records(df), len(df),
                       "From the demand plan, step 11", section="Building the plan")


def line_reasoning(con: duckdb.DuckDBPyConnection, p: dict[str, Any]) -> QueryResult:
    _need_plan(con)
    if not p.get("sku") and not p.get("category"):
        raise QueryParamError("sku or category is required")
    filt, params = _where([("sku_code = ?", p.get("sku")), ("plan_category = ?", p.get("category")),
                           ("city = ?", p.get("city")), ("platform = ?", p.get("platform"))])
    sql = f"""SELECT line_id, sku_code, item_name, city, platform, round(base, 1) AS base, reference_dp, pipeline_dp,
              final_dp, applied_factors, bridge, reason, confidence, flags, user_change
              FROM plan_lines WHERE {filt} ORDER BY final_dp DESC LIMIT 50"""
    df = con.execute(sql, params).df()
    return QueryResult("line_reasoning", frame_records(df), len(df),
                       "From the demand plan, step 9", section="Building the plan")


def stock_cover(con: duckdb.DuckDBPyConnection, p: dict[str, Any]) -> QueryResult:
    filt, params = _where([("sku_code = ?", p.get("sku")), ("city = ?", p.get("city"))])
    sql = f"""SELECT sku_code, city, any_value(platform_name) AS item, max(snapshot_date)::DATE AS snapshot_date,
              sum(soh_units)::BIGINT AS soh, round(sum(run_rate), 2) AS run_rate,
              CASE WHEN sum(run_rate) > 0 THEN round(sum(soh_units) / sum(run_rate), 1) END AS doh
              FROM stock_snapshot WHERE sku_code IS NOT NULL AND {filt}
                AND snapshot_date = (SELECT max(snapshot_date) FROM stock_snapshot)
              GROUP BY sku_code, city ORDER BY doh NULLS LAST"""
    df = con.execute(sql, params).df()
    return QueryResult("stock_cover", frame_records(df), len(df), "Known query: stock_cover")


def fill_rate(con: duckdb.DuckDBPyConnection, p: dict[str, Any]) -> QueryResult:
    filt, params = _where([("platform = ?", p.get("platform")), ("sku_code = ?", p.get("sku")),
                           ("city = ?", p.get("city")), ("po_date >= ?", _month(p.get("from"), "from")),
                           ("po_date <= ?", _month(p.get("to"), "to"))])
    by = p.get("by") or "platform"
    if by not in ("platform", "sku", "city", "facility"):
        raise QueryParamError("by must be platform, sku, city or facility")
    col = {"platform": "platform", "sku": "sku_code", "city": "city", "facility": "facility"}[by]
    sql = f"""SELECT {col} AS {by}, sum(qty_ordered) FILTER (WHERE status='completed')::BIGINT AS ordered,
              sum(qty_received) FILTER (WHERE status='completed')::BIGINT AS received,
              round(100.0 * sum(qty_received) FILTER (WHERE status='completed') /
                    nullif(sum(qty_ordered) FILTER (WHERE status='completed'), 0), 1) AS fill_pct,
              count(DISTINCT po_number) FILTER (WHERE status='expired' AND coalesce(qty_received,0)=0) AS expired_pos
              FROM po_line WHERE {filt} GROUP BY 1 ORDER BY ordered DESC NULLS LAST"""
    df = con.execute(sql, params).df()
    return QueryResult("fill_rate", frame_records(df), len(df), "Known query: fill_rate")


def market_position(con: duckdb.DuckDBPyConnection, p: dict[str, Any]) -> QueryResult:
    filt, params = _where([("category = ?", p.get("category")), ("platform = ?", p.get("platform")),
                           ("market_date >= ?", _month(p.get("from"), "from")),
                           ("market_date <= ?", _month(p.get("to"), "to"))])
    sql = f"""SELECT platform, category,
              round(sum(coalesce(sales_sp, sales_mrp))) AS category_value,
              round(sum(coalesce(sales_sp, sales_mrp)) FILTER (WHERE is_own_brand)) AS own_value,
              round(100.0 * sum(coalesce(sales_sp, sales_mrp)) FILTER (WHERE is_own_brand) /
                    nullif(sum(coalesce(sales_sp, sales_mrp)), 0), 2) AS own_share_pct,
              round(avg(osa_pct) FILTER (WHERE is_own_brand), 1) AS own_osa,
              round(avg(osa_pct) FILTER (WHERE NOT is_own_brand), 1) AS competitor_osa,
              round(avg(discount_pct) FILTER (WHERE is_own_brand), 1) AS own_discount,
              round(avg(discount_pct) FILTER (WHERE NOT is_own_brand), 1) AS competitor_discount
              FROM market_daily WHERE {filt} GROUP BY 1, 2 ORDER BY category_value DESC"""
    df = con.execute(sql, params).df()
    return QueryResult("market_position", frame_records(df), len(df), "Known query: market_position")


def open_pos(con: duckdb.DuckDBPyConnection, p: dict[str, Any]) -> QueryResult:
    filt, params = _where([("platform = ?", p.get("platform")), ("sku_code = ?", p.get("sku"))])
    sql = f"""SELECT platform, sku_code, city, sum(qty_open)::BIGINT AS open_qty, count(DISTINCT po_number) AS pos,
              min(expected_date)::DATE AS earliest_expected, max(expiry_date)::DATE AS latest_expiry
              FROM po_line WHERE status = 'open' AND {filt} GROUP BY ALL ORDER BY open_qty DESC"""
    df = con.execute(sql, params).df()
    return QueryResult("open_pos", frame_records(df), len(df), "Known query: open_pos")


def _need_plan(con: duckdb.DuckDBPyConnection) -> None:
    tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    if "plan_lines" not in tables:
        raise QueryParamError("no demand plan exists in this chat yet")


QUERIES = {
    "sales_history": (sales_history, "Sell-in units by period for a SKU or category, city, platform, date range; "
                      "params: sku, category, city, platform, from, to, grain (month|week), by (platform|city|sku|none)"),
    "top_movers": (top_movers, "SKUs that grew or fell most; params: metric (sell-in|secondary), period (YYYY-MM-01), "
                   "compare_to, n, direction (up|down)"),
    "plan_vs_history": (plan_vs_history, "Plan vs last month's sell-in; params: by (city|platform|sku|category), sku, "
                        "category, city, platform"),
    "line_reasoning": (line_reasoning, "Factors, bridge and reason behind plan lines; params: sku or category, city, "
                       "platform"),
    "stock_cover": (stock_cover, "Blinkit stock on hand, run rate, days on hand; params: sku, city"),
    "fill_rate": (fill_rate, "PO ordered, received, fill %, expired POs; params: platform, sku, city, from, to, "
                  "by (platform|sku|city|facility)"),
    "market_position": (market_position, "Own share, category size, OSA and discount vs competitors; params: "
                        "category, platform, from, to"),
    "open_pos": (open_pos, "Open PO quantity and expected dates; params: platform, sku"),
}


def run_known(con: duckdb.DuckDBPyConnection, name: str, params: dict[str, Any]) -> QueryResult:
    if name not in QUERIES:
        raise QueryParamError(f"unknown query {name}; known: {', '.join(QUERIES)}")
    return QUERIES[name][0](con, params or {})
