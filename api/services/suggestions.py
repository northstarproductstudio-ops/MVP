"""Suggested questions: Python computes candidate facts from the snapshot and current run;
one Ask-agent call phrases up to six questions in three groups from those facts only.
Before a plan exists only data questions show. Falls back to templates without the LLM."""

from __future__ import annotations

import json
from typing import Any

import duckdb

from core.llm import LLMClient, LLMError, load_prompt


def candidate_facts(con: duckdb.DuckDBPyConnection, has_plan: bool, move_pct: float, osa_alert: float,
                    fill_threshold: float) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    if "sellin_line" not in tables:
        return facts
    moves = con.execute("""
        WITH m AS (SELECT date_trunc('month', order_date) AS m, city, platform,
                     sum(coalesce(qty_invoiced,0)+coalesce(qty_cancelled,0)) AS q
                   FROM sellin_line WHERE status IN ('invoiced','confirmed') AND NOT is_internal GROUP BY ALL),
             last AS (SELECT max(m) AS lm FROM m)
        SELECT 'city' AS kind, city AS name, sum(q) FILTER (WHERE m = lm) AS last_q,
               sum(q) FILTER (WHERE m < lm AND m >= lm - INTERVAL 3 MONTH) / 3.0 AS avg_q
        FROM m, last GROUP BY city
        UNION ALL
        SELECT 'platform', platform, sum(q) FILTER (WHERE m = lm),
               sum(q) FILTER (WHERE m < lm AND m >= lm - INTERVAL 3 MONTH) / 3.0
        FROM m, last GROUP BY platform""").fetchall()
    for kind, name, last_q, avg_q in moves:
        if avg_q and last_q is not None:
            ch = last_q / avg_q - 1
            if abs(ch) > move_pct and name not in (None, "Other", "other"):
                facts.append({"fact": f"{kind}_moved", "name": name, "change_pct": round(ch * 100, 1),
                              "direction": "down" if ch < 0 else "up"})
    top = con.execute("""
        WITH m AS (SELECT sku_code, any_value(item_name) AS item, date_trunc('month', order_date) AS m,
                   sum(coalesce(qty_invoiced,0)+coalesce(qty_cancelled,0)) AS q FROM sellin_line
                   WHERE status IN ('invoiced','confirmed') AND NOT is_internal GROUP BY sku_code, m),
             last AS (SELECT max(m) lm FROM m)
        SELECT a.item, a.q - coalesce(b.q, 0) AS d FROM m a JOIN last ON a.m = last.lm
        LEFT JOIN m b ON b.sku_code = a.sku_code AND b.m = last.lm - INTERVAL 1 MONTH
        ORDER BY abs(d) DESC LIMIT 3""").fetchall()
    for item, d in top:
        facts.append({"fact": "top_mover", "name": item, "change_units": int(d or 0)})
    if "market_daily" in tables:
        low = con.execute(f"""SELECT platform, count(DISTINCT sku_code) FROM market_daily
            WHERE is_own_brand AND sku_code IS NOT NULL
              AND market_date > (SELECT max(market_date) FROM market_daily) - INTERVAL 28 DAY
            GROUP BY platform HAVING avg(osa_pct) < {float(osa_alert)}""").fetchall()
        for plat, n in low:
            facts.append({"fact": "low_osa", "platform": plat, "skus": int(n)})
    if "po_line" in tables:
        fr = con.execute("""SELECT platform, sum(qty_received)/nullif(sum(qty_ordered),0) FROM po_line
                            WHERE status='completed' GROUP BY platform""").fetchall()
        for plat, rate in fr:
            if rate is not None and rate < fill_threshold:
                facts.append({"fact": "low_fill_rate", "platform": plat, "fill_rate": round(float(rate), 2)})
    if "stock_snapshot" in tables:
        age = con.execute("SELECT date_diff('day', max(snapshot_date), (SELECT max(order_date) FROM sellin_line)) "
                          "FROM stock_snapshot").fetchone()[0]
        if age is not None and age > 14:
            facts.append({"fact": "stale_stock", "days_old": int(age)})
    if has_plan and "plan_lines" in tables:
        rev, ceil = con.execute("""SELECT count(*) FILTER (WHERE flag_for_review),
                                   count(*) FILTER (WHERE flags LIKE '%market_ceiling%') FROM plan_lines""").fetchone()
        facts.append({"fact": "plan_flags", "review": int(rev or 0), "market_ceiling": int(ceil or 0)})
    return facts


def template_questions(facts: list[dict[str, Any]], has_plan: bool, n: int) -> dict[str, list[str]]:
    plan = ["Create a demand plan for next month"] if not has_plan else [
        "How is next month's plan split by city?", "Which products have the largest plan?"]
    why, data = [], []
    for f in facts:
        if f["fact"] == "city_moved" and f["direction"] == "down":
            why.append(f"Why is demand lower in {f['name']}?")
        elif f["fact"] == "city_moved":
            why.append(f"Why did sales rise in {f['name']} last month?")
        elif f["fact"] == "platform_moved":
            why.append(f"What changed on {f['name']} last month?")
        elif f["fact"] == "top_mover":
            data.append(f"How did {f['name']} sell over the last three months?")
        elif f["fact"] == "low_osa":
            data.append(f"Which products have low availability on {f['platform']}?")
        elif f["fact"] == "low_fill_rate":
            data.append(f"What is the fill rate on {f['platform']} by SKU?")
        elif f["fact"] == "stale_stock":
            data.append("When was the stock data last updated?")
        elif f["fact"] == "plan_flags" and f["review"]:
            plan.append("Which plan lines are flagged for review?")
    if not has_plan:
        why = []
    out = {"plan": plan[:2], "why": why[:2], "data": data[:2] or ["Which products sold most last month?"]}
    return out if has_plan else {"plan": out["plan"][:1], "why": [], "data": out["data"]}


def phrase(llm: LLMClient | None, facts: list[dict[str, Any]], has_plan: bool, n: int,
           session_id: str, company_id: str) -> dict[str, list[str]]:
    if llm is None or not facts:
        return template_questions(facts, has_plan, n)
    try:
        res = llm.complete(role="ask", session_id=session_id, company_id=company_id, temperature=0.0,
                           max_tokens=400, messages=[
                               {"role": "system", "content": load_prompt("ask_agent", "v1", "suggestions.md")
                                .replace("{max_questions}", str(n))},
                               {"role": "user", "content": json.dumps({"has_plan": has_plan, "facts": facts})}])
        data = json.loads((res.content or "").strip().strip("`").removeprefix("json"))
        out = {k: [q for q in data.get(k, []) if isinstance(q, str)][:3] for k in ("plan", "why", "data")}
        if not has_plan:
            out = {"plan": ["Create a demand plan for next month"], "why": [], "data": out["data"]}
        return out
    except (LLMError, ValueError, AttributeError):
        return template_questions(facts, has_plan, n)
