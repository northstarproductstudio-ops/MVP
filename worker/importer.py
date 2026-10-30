"""Import jobs on the worker: ``import`` parses a stored upload and proposes a mapping;
``materialize`` writes typed core rows after the user saves the mapping, drafts new
crosswalk rows and rebuilds the chat's snapshot."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from core.domain import entity_resolution as er
from core.domain.import_agent import make_import_agent
from core.domain.importing import mapping_from_rows, materialize, parse_file
from core.domain.tables import TABLE_COLUMNS
from core.llm import LLMClient
from core.logging import get_logger
from core.plan_config import PlanConfig
from core.storage import Store
from worker import snapshots
from worker.repo import load_table, rows_for

log = get_logger(__name__)
RAW_BATCH = 2000


def _dataset(conn: psycopg.Connection, dataset_id: str, company_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT *, id::text AS did, session_id::text AS sid FROM raw.dataset "
                       "WHERE id=%s AND company_id=%s", (dataset_id, company_id)).fetchone()
    if not row:
        raise LookupError("dataset not found")
    return dict(row)


def _company_brands(conn: psycopg.Connection, company_id: str, cfg: PlanConfig) -> list[str]:
    row = conn.execute("SELECT own_brand_names FROM core.company WHERE company_id=%s LIMIT 1", (company_id,)).fetchone()
    return list(row["own_brand_names"]) if row and row["own_brand_names"] else list(cfg.get("brand.own_names"))


def saved_mappings(conn: psycopg.Connection, company_id: str) -> dict[str, list[dict[str, Any]]]:
    rows = conn.execute(
        """SELECT v.file_type, m.source_sheet, m.source_field, m.variable, m.category, m.state
           FROM core.field_mapping m JOIN core.field_mapping_version v ON v.id = m.version_id
           WHERE v.company_id=%s AND v.version = (SELECT max(version) FROM core.field_mapping_version v2
                 WHERE v2.company_id=v.company_id AND v2.file_type=v.file_type)""", (company_id,)).fetchall()
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r["file_type"], []).append(dict(r))
    return out


def _progress(conn: psycopg.Connection, dataset_id: str, **fields: Any) -> None:
    with conn.transaction():
        conn.execute("UPDATE raw.dataset SET progress = progress || %s WHERE id=%s", (Jsonb(fields), dataset_id))


def run_import(conn: psycopg.Connection, store: Store, company_id: str, dataset_id: str, cfg: PlanConfig,
               llm: LLMClient | None) -> None:
    conn.row_factory = dict_row
    ds = _dataset(conn, dataset_id, company_id)
    with conn.transaction():
        conn.execute("UPDATE raw.dataset SET status='parsing' WHERE id=%s", (dataset_id,))
    path = store.download_to(ds["storage_key"])
    _progress(conn, dataset_id, stage="reading")
    agent = make_import_agent(llm, company_id=company_id, session_id=ds["sid"]) if llm else None
    parsed = parse_file(Path(path), ds["file_name"], cfg, saved=saved_mappings(conn, company_id), agent=agent,
                        forced_type=ds["file_type"] if ds["type_source"] == "user" else None)
    p = parsed.proposal
    with conn.transaction():
        conn.execute("DELETE FROM raw.raw_record WHERE dataset_id=%s", (dataset_id,))
        for src in parsed.sources:
            batch = []
            for row_no, rec in src.frame.iterrows():
                batch.append((company_id, dataset_id, src.sheet, int(row_no),
                              json.dumps({k: _jsonable(v) for k, v in rec.items()}, default=str)))
                if len(batch) >= RAW_BATCH:
                    _copy_raw(conn, batch)
                    batch = []
            _copy_raw(conn, batch)
        conn.execute(
            """UPDATE raw.dataset SET status='parsed', file_type=%s, type_score=%s, type_source=%s, rows_read=%s,
               proposal=%s, warnings=%s, progress = progress || %s WHERE id=%s""",
            (p.file_type, p.type_confidence, p.type_source, parsed.rows_read, Jsonb(p.model_dump()),
             Jsonb(p.warnings), Jsonb({"stage": "proposed", "header_found": True,
                                       "fields_detected": len(p.fields),
                                       "fields_proposed": sum(1 for f in p.fields if f.state != "needs_decision"),
                                       "sheets": [s.sheet for s in parsed.sources]}), dataset_id))


def _copy_raw(conn: psycopg.Connection, rows: list[tuple]) -> None:
    if not rows:
        return
    with conn.cursor() as cur, cur.copy(
            "COPY raw.raw_record (company_id, dataset_id, sheet, row_no, data) FROM STDIN") as cp:
        for r in rows:
            cp.write_row(r)


def _jsonable(v: Any) -> Any:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, (int, float, str, bool)):
        return v
    return str(v)


def run_materialize(conn: psycopg.Connection, store: Store, company_id: str, dataset_id: str,
                    cfg: PlanConfig) -> None:
    conn.row_factory = dict_row
    ds = _dataset(conn, dataset_id, company_id)
    path = store.download_to(ds["storage_key"])
    parsed = parse_file(Path(path), ds["file_name"], cfg, forced_type=ds["file_type"])
    rows = [dict(r) for r in conn.execute(
        "SELECT source_sheet, source_field, variable, state FROM core.field_mapping WHERE version_id=%s",
        (ds["mapping_version_id"],)).fetchall()]
    res = materialize(parsed.sources, ds["file_type"], mapping_from_rows(rows), {
        "dataset_id": dataset_id, "file_name": ds["file_name"],
        "own_brand_names": _company_brands(conn, company_id, cfg)})
    table = res.table
    with conn.transaction():
        for t in ("sellin_line", "po_line", "stock_snapshot", "market_daily", "extra_value"):
            conn.execute(f"DELETE FROM core.{t} WHERE dataset_id=%s AND company_id=%s", (dataset_id, company_id))
        if table == "sku_master":
            for r in res.frame.to_dict("records"):
                conn.execute(
                    """INSERT INTO core.sku_master (company_id, dataset_id, sku_code, item_name, plan_category, mrp,
                       ean, pack_size, case_size, moq) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (company_id, sku_code) DO UPDATE SET item_name=coalesce(excluded.item_name,
                       core.sku_master.item_name), plan_category=coalesce(excluded.plan_category,
                       core.sku_master.plan_category), mrp=coalesce(excluded.mrp, core.sku_master.mrp),
                       ean=coalesce(excluded.ean, core.sku_master.ean), case_size=coalesce(excluded.case_size,
                       core.sku_master.case_size), moq=coalesce(excluded.moq, core.sku_master.moq)""",
                    (company_id, dataset_id, *rows_for(pd.DataFrame([r]), ["sku_code", "item_name", "plan_category",
                                                                            "mrp", "ean", "pack_size", "case_size",
                                                                            "moq"], set())[0]))
        elif table != "extra_value" and not res.frame.empty:
            cols = [c for c in TABLE_COLUMNS[table] if c != "dataset_id"]
            if table == "sellin_line":
                res.frame["is_internal"] = res.frame["is_internal"].fillna(False)
            data = rows_for(res.frame, cols, set())
            _copy_rows(conn, f"core.{table}", ["company_id", "dataset_id"] + cols,
                       [(company_id, dataset_id, *r) for r in data])
        if res.extra is not None and not res.extra.empty:
            ecols = ["row_no", "variable", "value_text", "value_num", "value_date"]
            _copy_rows(conn, "core.extra_value", ["company_id", "dataset_id"] + ecols,
                       [(company_id, dataset_id, *r) for r in rows_for(res.extra, ecols, set())])
        warnings = list(ds["warnings"] or []) + res.warnings
        conn.execute("UPDATE raw.dataset SET status='ready', warnings=%s WHERE id=%s",
                     (Jsonb(sorted(set(warnings), key=warnings.index)), dataset_id))
    draft_crosswalk(conn, company_id, cfg)
    snapshots.build_and_store(conn, store, company_id, ds["sid"], cfg)


def _copy_rows(conn: psycopg.Connection, table: str, cols: list[str], rows: list[tuple]) -> None:
    from psycopg import sql

    schema, name = table.split(".")
    stmt = sql.SQL("COPY {}.{} ({}) FROM STDIN").format(
        sql.Identifier(schema), sql.Identifier(name), sql.SQL(", ").join(map(sql.Identifier, cols)))
    with conn.cursor() as cur, cur.copy(stmt) as cp:
        for r in rows:
            cp.write_row(r)


def draft_crosswalk(conn: psycopg.Connection, company_id: str, cfg: PlanConfig) -> int:
    """Draft crosswalk rows for new platform IDs; confirmed rows are never overwritten."""
    po = load_table(conn, "po_line", company_id)
    stock = load_table(conn, "stock_snapshot", company_id)
    market = load_table(conn, "market_daily", company_id)
    sellin = conn.execute("SELECT DISTINCT sku_code, item_name, max(order_date) AS order_date FROM core.sellin_line "
                          "WHERE company_id=%s GROUP BY sku_code, item_name", (company_id,)).fetchall()
    sellin_df = pd.DataFrame([dict(r) for r in sellin], columns=["sku_code", "item_name", "order_date"])
    sm = load_table(conn, "sku_master", company_id)
    existing = load_table(conn, "sku_crosswalk", company_id)
    items = er.platform_items(po, stock, market)
    # re-score every row that no one has confirmed (new IDs and earlier drafts alike)
    confirmed = set()
    if not existing.empty:
        ex = existing[existing["confirmed_at"].notna() | existing["match_method"].isin(["manual", "ean"])]
        confirmed = set(zip(ex["platform"], ex["platform_item_id"], strict=False))
    todo = items[[k not in confirmed for k in zip(items["platform"], items["platform_item_id"], strict=False)]]
    if todo.empty:
        return 0
    strip = cfg.get("brand.strip_tokens")
    drafted = er.build_crosswalk(todo, er.sku_refs(sellin_df, sm, strip), None, strip,
                                 cfg.get("crosswalk.auto_accept"), cfg.get("crosswalk.review_min"),
                                 cfg.get("crosswalk.tie_margin", 0.01))
    with conn.transaction():
        for r in drafted.to_dict("records"):
            cands = json.loads(r["candidates"]) if isinstance(r.get("candidates"), str) else r.get("candidates")
            conn.execute(
                """INSERT INTO core.sku_crosswalk (company_id, platform, platform_item_id, platform_name, sku_code,
                   match_method, score, candidates) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (company_id, platform, platform_item_id) DO UPDATE SET
                   platform_name=excluded.platform_name, sku_code=excluded.sku_code,
                   match_method=excluded.match_method, score=excluded.score, candidates=excluded.candidates
                   WHERE core.sku_crosswalk.confirmed_at IS NULL
                     AND core.sku_crosswalk.match_method NOT IN ('manual','ean')""",
                (company_id, r["platform"], r["platform_item_id"], r.get("platform_name"),
                 r.get("sku_code") or (r.get("proposed_sku") if r["match_method"] == "review" else None),
                 r["match_method"], r.get("score"), Jsonb(cands)))
    return len(drafted)
