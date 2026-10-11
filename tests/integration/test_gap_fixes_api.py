"""Gaps 7, 10 and 12 against Postgres + the FastAPI app (fake Storage, HS256 dev tokens).

* readiness API and preconditions() agree on 10 scripted mapping states; saving is never blocked
* precondition error codes: no_sales_file, sales_fields_unmapped, no_sales_history, scope_has_no_history
* data management: list, remove, replace, clear; 409 while a plan is queued or running;
  removal changes the next snapshot only; finished runs keep their snapshot; system notes
* nightly expiry (worker) deletes raw files of long-removed datasets unless a run's snapshot uses them

Needs DATABASE_URL_TEST (superuser on an empty database). Skipped otherwise.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row

URL = os.environ.get("DATABASE_URL_TEST")
pytestmark = pytest.mark.skipif(not URL, reason="DATABASE_URL_TEST not set")
ROOT = Path(__file__).resolve().parents[2]
SECRET = "test-jwt-secret-for-gap-fixes-0123456789"
HARD = ["sku_code", "order_date", "so_status", "customer_name", "ship_city", "qty_invoiced"]


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def api(tmp_path_factory: pytest.TempPathFactory):
    env = {**os.environ, "DATABASE_URL_SESSION": URL, "APP_API_PASSWORD": "test-app-api"}
    subprocess.run([sys.executable, "scripts/migrate.py"], cwd=ROOT, env=env, check=True)
    subprocess.run([sys.executable, "scripts/seed.py", "global"], cwd=ROOT, env=env, check=True)
    from tests.fakes import storage_server

    port = _port()
    srv = storage_server.serve(port)
    time.sleep(0.1)
    api_url = psycopg.conninfo.make_conninfo(URL, user="app_api", password="test-app-api")
    mp = pytest.MonkeyPatch()
    for k, v in {"APP_ENV": "test", "SERVICE_NAME": "api", "DATABASE_URL_POOLED": api_url,
                 "SUPABASE_URL": f"http://127.0.0.1:{port}", "SUPABASE_SERVICE_ROLE_KEY": "k",
                 "SUPABASE_JWT_SECRET": SECRET, "STORAGE_BUCKET": "b", "OPENROUTER_API_KEY": "x",
                 "LLM_MODEL": "m", "JOB_TOKEN": "job-token", "WORKER_URL": "",
                 "TMP_DIR": str(tmp_path_factory.mktemp("api-tmp"))}.items():
        mp.setenv(k, v)
    from api import deps

    for f in (deps.settings, deps.store, deps.plan_config):
        f.cache_clear()
    from fastapi.testclient import TestClient

    from api.main import app

    with TestClient(app) as client:
        yield client
    mp.undo()
    srv.shutdown()
    for f in (deps.settings, deps.store, deps.plan_config):
        f.cache_clear()
    import core.db as dbmod

    dbmod._pools.clear()


def _token(company_id: str) -> dict[str, str]:
    import jwt

    now = int(time.time())
    tok = jwt.encode({"sub": str(uuid.uuid4()), "aud": "authenticated", "iat": now, "exp": now + 3600,
                      "app_metadata": {"company_id": company_id}}, SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {tok}"}


def _su() -> psycopg.Connection:
    return psycopg.connect(URL, autocommit=True, row_factory=dict_row)


def _company() -> tuple[str, str]:
    cid = str(uuid.uuid4())
    with _su() as c:
        c.execute("INSERT INTO core.company (id, company_id, name) VALUES (%s,%s,'t')", (cid, cid))
        sid = c.execute("INSERT INTO core.chat_session (company_id, title) VALUES (%s,'c') RETURNING id::text",
                        (cid,)).fetchone()["id"]
    return cid, sid


def _dataset(cid: str, sid: str, *, file_type: str = "erp_sales_orders", status: str = "ready",
             mapped: dict[str, str] | None = None, proposal: dict[str, Any] | None = None,
             sellin_status: str = "invoiced", rows: int = 3, name: str | None = None) -> str:
    """A dataset with a saved mapping (variable → state) and a few sell-in rows."""
    with _su() as c:
        mv = None
        if mapped is not None:
            mv = c.execute("INSERT INTO core.field_mapping_version (company_id, file_type, version) "
                           "SELECT %s, %s, coalesce(max(version), 0) + 1 FROM core.field_mapping_version "
                           "WHERE company_id=%s AND file_type=%s RETURNING id",
                           (cid, file_type, cid, file_type)).fetchone()["id"]
            for var, st in mapped.items():
                c.execute("INSERT INTO core.field_mapping (company_id, version_id, source_sheet, source_field, variable, "
                          "state) VALUES (%s,%s,'s',%s,%s,%s)", (cid, mv, var.upper(), var, st))
        ds = c.execute(
            "INSERT INTO raw.dataset (company_id, session_id, file_name, content_sha256, storage_key, file_type, status, "
            "mapping_version_id, proposal, rows_read) VALUES (%s,%s,%s,%s,'k',%s,%s,%s,%s,%s) RETURNING id::text",
            (cid, sid, name or f"{file_type}-{uuid.uuid4().hex[:6]}.xlsx", uuid.uuid4().hex, file_type, status, mv,
             psycopg.types.json.Jsonb(proposal) if proposal else None, rows)).fetchone()["id"]
        if file_type == "erp_sales_orders":
            for i in range(rows):
                c.execute("INSERT INTO core.sellin_line (company_id, dataset_id, row_no, line_key, order_date, status, "
                          "sku_code, item_name, qty_invoiced, qty_cancelled, is_internal, platform, city) "
                          "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,0,false,'blinkit','Chennai')",
                          (cid, ds, i, f"{ds}-{i}", f"2026-09-{10 + i:02d}", sellin_status, f"SKU{i}",
                           f"Product {i}", 10))
    return ds


async def _pre(cid: str, sid: str, scope: dict[str, Any] | None = None) -> tuple[bool, str | None]:
    from api.services import runs as run_svc

    async with await psycopg.AsyncConnection.connect(URL, row_factory=dict_row) as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT set_config('app.company_id', %s, false)", (cid,))
            try:
                await run_svc.preconditions(cur, sid, {"history": {"months": 3,
                                                                   "sellin_statuses": ["invoiced", "confirmed"]}},
                                            scope)
                return True, None
            except run_svc.PreconditionError as exc:
                return False, exc.code


ALL = dict.fromkeys(HARD, "confirmed")


@pytest.mark.parametrize("case", [
    "none", "parsed_only", "ready_all_confirmed", "ready_missing_order_date", "ready_order_date_proposed",
    "ready_order_date_new_category", "ready_all_recognised", "ready_all_custom", "two_files_one_complete",
    "complete_file_removed",
])
def test_readiness_and_preconditions_agree(api, case):
    cid, sid = _company()
    if case == "parsed_only":
        _dataset(cid, sid, status="parsed", proposal={"fields": [{"source_field": v.upper(), "variable": v,
                                                                 "state": "recognised"} for v in HARD]})
    elif case == "ready_all_confirmed":
        _dataset(cid, sid, mapped=ALL)
    elif case == "ready_missing_order_date":
        _dataset(cid, sid, mapped={k: v for k, v in ALL.items() if k != "order_date"})
    elif case == "ready_order_date_proposed":
        _dataset(cid, sid, mapped={**ALL, "order_date": "proposed"})
    elif case == "ready_order_date_new_category":
        _dataset(cid, sid, mapped={**ALL, "order_date": "new_category"})
    elif case == "ready_all_recognised":
        _dataset(cid, sid, mapped=dict.fromkeys(HARD, "recognised"))
    elif case == "ready_all_custom":
        _dataset(cid, sid, mapped=dict.fromkeys(HARD, "custom"))
    elif case == "two_files_one_complete":
        _dataset(cid, sid, mapped={k: v for k, v in ALL.items() if k != "qty_invoiced"})
        _dataset(cid, sid, mapped=ALL)
    elif case == "complete_file_removed":
        _dataset(cid, sid, mapped=ALL, status="removed")
    r = api.get(f"/sessions/{sid}/readiness", headers=_token(cid))
    assert r.status_code == 200, r.text
    body = r.json()
    ok, code = asyncio.run(_pre(cid, sid))
    assert body["plan_ready"] == ok, (case, body["blocking"], code)
    if not ok:
        assert code == body["code"], case
    expected_ready = case in ("ready_all_confirmed", "ready_all_recognised", "ready_all_custom",
                              "two_files_one_complete")
    assert ok == expected_ready, case
    # display names only, never raw variable names, in what the planner sees
    assert all(v["display_name"] and v["display_name"] != v["name"] or v["name"] in ("ean",)
               for v in body["variables"] if v["needed_for"] == "plan")
    if case == "ready_missing_order_date":
        assert body["blocking_display"] == ["Order date"]


def test_no_sales_history_and_scope(api):
    cid, sid = _company()
    _dataset(cid, sid, mapped=ALL, sellin_status="void")
    assert asyncio.run(_pre(cid, sid)) == (False, "no_sales_history")
    cid, sid = _company()
    _dataset(cid, sid, mapped=ALL)
    assert asyncio.run(_pre(cid, sid, {"skus": ["NOPE1", "NOPE2"]})) == (False, "scope_has_no_history")
    assert asyncio.run(_pre(cid, sid, {"skus": ["SKU0", "NOPE"]})) == (True, None)


def test_saving_a_mapping_is_never_blocked(api):
    cid, sid = _company()
    ds = _dataset(cid, sid, status="parsed", proposal={"file_type": "erp_sales_orders", "fields": [
        {"sheet": "s", "source_field": "Order Date", "variable": None, "state": "needs_decision"},
        {"sheet": "s", "source_field": "SKU", "variable": "sku_code", "state": "recognised"}]})
    r = api.put(f"/imports/{ds}/mapping", headers=_token(cid), json={"fields": [
        {"sheet": "s", "source_field": "Order Date", "variable": None, "state": "needs_decision"},
        {"sheet": "s", "source_field": "SKU", "variable": "sku_code", "state": "recognised"}]})
    assert r.status_code == 200, r.text
    with _su() as c:
        st = {x["source_field"]: x["state"] for x in c.execute(
            "SELECT source_field, state FROM core.field_mapping m JOIN raw.dataset d ON d.mapping_version_id = m.version_id "
            "WHERE d.id=%s", (ds,)).fetchall()}
    assert st == {"Order Date": "needs_decision", "SKU": "confirmed"}


def test_data_management(api):
    cid, sid = _company()
    h = _token(cid)
    a = _dataset(cid, sid, mapped=ALL, name="SOs - Jul-Sep.xlsx")
    b = _dataset(cid, sid, file_type="platform_po_zepto", mapped={}, name="Zepto PO Export - Aug-2026.csv")
    with _su() as c:
        snap = c.execute("INSERT INTO meta.workspace_snapshot (company_id, session_id, dataset_ids, table_hashes, "
                         "storage_prefix) VALUES (%s,%s,%s,'{}','p') RETURNING id", (cid, sid, [a, b])).fetchone()["id"]
        c.execute("UPDATE core.chat_session SET current_snapshot_id=%s WHERE id=%s", (snap, sid))
        run = c.execute("INSERT INTO meta.workflow_run (company_id, session_id, target_month, \"trigger\", status, "
                        "snapshot_id) VALUES (%s,%s,'2026-11-01','chat','succeeded',%s) RETURNING id",
                        (cid, sid, snap)).fetchone()["id"]

    listed = api.get(f"/sessions/{sid}/datasets", headers=h).json()
    assert {d["id"] for d in listed["datasets"]} == {a, b} and not listed["plan_running"]

    r = api.delete(f"/datasets/{b}", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "removed"
    with _su() as c:
        assert c.execute("SELECT status FROM raw.dataset WHERE id=%s", (b,)).fetchone()["status"] == "removed"
        assert c.execute("SELECT current_snapshot_id FROM core.chat_session WHERE id=%s",
                         (sid,)).fetchone()["current_snapshot_id"] is None
        note = c.execute("SELECT content FROM core.chat_message WHERE session_id=%s AND role='system'",
                         (sid,)).fetchone()["content"]
        assert note == "Removed Zepto PO Export - Aug-2026.csv"
        # finished runs never change: the run keeps the snapshot it was built from
        assert str(c.execute("SELECT snapshot_id FROM meta.workflow_run WHERE id=%s", (run,)).fetchone()["snapshot_id"]) \
            == str(snap)
    with _su() as c:
        from worker import snapshots

        assert [d["id"] for d in snapshots.ready_datasets(c, cid, sid)] == [a]  # the next snapshot only
    assert {d["id"] for d in api.get(f"/sessions/{sid}/datasets", headers=h).json()["datasets"]} == {a}
    assert b in {d["id"] for d in api.get(f"/sessions/{sid}/datasets?include_removed=true", headers=h).json()["datasets"]}

    # replace: imports as a new dataset; the old one stays until the new one is ready
    r = api.post(f"/datasets/{a}/replace", headers=h, files={"file": ("SOs - Jul-Oct.csv", b"a,b\n1,2\n", "text/csv")})
    assert r.status_code == 200, r.text
    new = r.json()["dataset_id"]
    with _su() as c:
        rows = {x["id"]: x for x in c.execute("SELECT id::text, status, replaces_dataset_id::text, removed_by "
                                              "FROM raw.dataset WHERE session_id=%s", (sid,)).fetchall()}
    assert rows[new]["replaces_dataset_id"] == a and rows[new]["status"] == "uploaded"
    assert rows[a]["status"] == "ready" and rows[a]["removed_by"] is not None

    # no file change while a plan in the same chat is queued or running
    with _su() as c:
        c.execute("INSERT INTO meta.workflow_run (company_id, session_id, target_month, \"trigger\", status) "
                  "VALUES (%s,%s,'2026-11-01','chat','running')", (cid, sid))
    assert api.delete(f"/datasets/{a}", headers=h).status_code == 409
    assert api.post(f"/sessions/{sid}/datasets/clear", headers=h).status_code == 409
    assert api.post(f"/datasets/{a}/replace", headers=h,
                    files={"file": ("x.csv", b"c\n3\n", "text/csv")}).status_code == 409
    up = api.post(f"/sessions/{sid}/imports", headers=h, files={"files": ("y.csv", b"d\n4\n", "text/csv")})
    assert up.status_code == 409 and "A plan is running" in up.json()["detail"]
    assert api.get(f"/sessions/{sid}/datasets", headers=h).json()["plan_running"]
    with _su() as c:
        c.execute("UPDATE meta.workflow_run SET status='succeeded' WHERE session_id=%s AND status='running'", (sid,))

    r = api.post(f"/sessions/{sid}/datasets/clear", headers=h)
    assert r.status_code == 200 and r.json()["removed"] == 2
    assert api.get(f"/sessions/{sid}/datasets", headers=h).json()["datasets"] == []
    # a removed file can be added again (it no longer blocks its content hash)
    again = api.post(f"/sessions/{sid}/imports", headers=h, files={"files": ("SOs - Jul-Oct.csv", b"a,b\n1,2\n", "text/csv")})
    assert again.status_code == 200 and again.json()["files"][0]["status"] == "uploaded"


def test_expiry_job_keeps_files_a_run_uses(api):
    cid, sid = _company()
    used = _dataset(cid, sid, mapped=ALL, status="removed")
    gone = _dataset(cid, sid, mapped=ALL, status="removed")
    fresh = _dataset(cid, sid, mapped=ALL, status="removed")
    with _su() as c:
        c.execute("UPDATE raw.dataset SET removed_at = now() - interval '40 days', "
                  "storage_key = 'uploads/' || company_id || '/' || session_id || '/x-' || id WHERE id = ANY(%s::uuid[])",
                  ([used, gone],))
        c.execute("UPDATE raw.dataset SET removed_at = now() - interval '2 days' WHERE id=%s", (fresh,))
        snap = c.execute("INSERT INTO meta.workspace_snapshot (company_id, session_id, dataset_ids, table_hashes, "
                         "storage_prefix) VALUES (%s,%s,%s,'{}','p') RETURNING id", (cid, sid, [used])).fetchone()["id"]
        c.execute("INSERT INTO meta.workflow_run (company_id, session_id, target_month, \"trigger\", status, snapshot_id) "
                  "VALUES (%s,%s,'2026-11-01','chat','succeeded',%s)", (cid, sid, snap))
    from worker.expiry import expire_removed

    deleted: list[str] = []

    class Store:
        def delete_key(self, key: str) -> None:
            deleted.append(key)

    with _su() as c:
        out = expire_removed(c, Store(), 30)
    assert out["deleted"] >= 1
    with _su() as c:
        got = {x["id"]: x["raw_deleted_at"] for x in c.execute(
            "SELECT id::text, raw_deleted_at FROM raw.dataset WHERE id = ANY(%s::uuid[])", ([used, gone, fresh],))}
    assert got[gone] is not None and got[used] is None and got[fresh] is None
    assert any(k.endswith(gone) for k in deleted) and not any(k.endswith(used) for k in deleted)
