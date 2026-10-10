"""Build and load frozen workspace snapshots (Parquet in Storage, hashes in Postgres)."""

from __future__ import annotations

import uuid
from typing import Any

import pandas as pd
import psycopg
from psycopg.types.json import Jsonb

from core.domain import entity_resolution as er
from core.domain.snapshot import DATA_TABLES, MAP_TABLES, Snapshot, build_snapshot
from core.plan_config import PlanConfig
from core.storage import Store, snapshot_key
from worker.repo import load_table


def company_rules(conn: psycopg.Connection, company_id: str) -> tuple[list[er.CustomerRule], dict[str, str]]:
    rules = [er.CustomerRule(r["ord"], r["match"], r["pattern"].lower(), r["platform"]) for r in conn.execute(
        "SELECT ord, match, pattern, platform FROM core.customer_rule WHERE company_id=%s ORDER BY ord",
        (company_id,)).fetchall()] or er.seed_customer_rules()
    aliases = {r["raw_city"].lower(): r["city"] for r in conn.execute(
        "SELECT raw_city, city FROM core.city_map WHERE company_id=%s", (company_id,)).fetchall()}
    return rules, aliases or er.seed_city_aliases()


def ready_datasets(conn: psycopg.Connection, company_id: str, session_id: str) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT id::text, file_type, created_at FROM raw.dataset WHERE company_id=%s AND session_id=%s "
        "AND status='ready' ORDER BY created_at", (company_id, session_id)).fetchall()]


def snapshot_is_current(conn: psycopg.Connection, company_id: str, session_id: str) -> str | None:
    row = conn.execute("SELECT current_snapshot_id::text AS sid FROM core.chat_session WHERE id=%s AND company_id=%s",
                       (session_id, company_id)).fetchone()
    sid = row["sid"] if row else None
    if not sid:
        return None
    snap = conn.execute("SELECT dataset_ids FROM meta.workspace_snapshot WHERE id=%s", (sid,)).fetchone()
    ready = {d["id"] for d in ready_datasets(conn, company_id, session_id)}
    have = {str(x) for x in (snap["dataset_ids"] if snap else [])}
    return sid if ready == have else None


def build_and_store(conn: psycopg.Connection, store: Store, company_id: str, session_id: str,
                    cfg: PlanConfig) -> tuple[str, Snapshot]:
    ds = ready_datasets(conn, company_id, session_id)
    ids = [d["id"] for d in ds]
    data = {t: [load_table(conn, t, company_id, ids)] for t in DATA_TABLES}
    sku_master = load_table(conn, "sku_master", company_id)
    crosswalk = load_table(conn, "sku_crosswalk", company_id)
    cmap = load_table(conn, "customer_map", company_id)
    rules, aliases = company_rules(conn, company_id)
    snap = build_snapshot(data, sku_master, crosswalk, rules, aliases, customer_map_saved=cmap,
                          unmatched_share=cfg.get("city_map.unmatched_share_alert"),
                          strip_tokens=cfg.get("brand.strip_tokens"),
                          auto_accept=cfg.get("crosswalk.auto_accept"), review_min=cfg.get("crosswalk.review_min"),
                          draft_crosswalk=False)
    sid = str(uuid.uuid4())
    prefix = f"workspaces/{company_id}/{session_id}/{sid}"
    hashes = {}
    for name, df in snap.tables.items():
        hashes[name] = store.put_parquet(snapshot_key(company_id, session_id, sid, name), _parquet_safe(df))
    with conn.transaction():
        conn.execute(
            """INSERT INTO meta.workspace_snapshot (id, company_id, session_id, dataset_ids, as_of, date_from,
               date_to, table_hashes, row_counts, storage_prefix) VALUES (%s,%s,%s,%s::uuid[],%s,%s,%s,%s,%s,%s)""",
            (sid, company_id, session_id, ids, _d(snap.as_of), _d(snap.date_from), _d(snap.date_to),
             Jsonb(hashes), Jsonb(snap.row_counts()), prefix))
        conn.execute("UPDATE core.chat_session SET current_snapshot_id=%s WHERE id=%s AND company_id=%s",
                     (sid, session_id, company_id))
    return sid, snap


def load(conn: psycopg.Connection, store: Store, company_id: str, snapshot_id: str) -> tuple[dict[str, pd.DataFrame], Any]:
    row = conn.execute("SELECT session_id::text, table_hashes, as_of FROM meta.workspace_snapshot "
                       "WHERE id=%s AND company_id=%s", (snapshot_id, company_id)).fetchone()
    if not row:
        raise LookupError("snapshot not found")
    tables = {}
    for name in DATA_TABLES + MAP_TABLES:
        key = snapshot_key(company_id, row["session_id"], snapshot_id, name)
        df = store.read_parquet(key, row["table_hashes"].get(name))
        for c in df.columns:
            if c in ("order_date", "po_date", "expected_date", "expiry_date", "snapshot_date", "market_date",
                     "value_date"):
                df[c] = pd.to_datetime(df[c])
        tables[name] = df
    return tables, row["as_of"]


def _d(ts: Any) -> Any:
    return None if ts is None or pd.isna(ts) else pd.Timestamp(ts).date()


def _parquet_safe(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if out[c].dtype == object:
            out[c] = out[c].map(lambda v: None if v is None else str(v)).astype("string")
    return out
