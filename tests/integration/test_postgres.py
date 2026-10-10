"""Postgres integration: migrations, RLS isolation, queue claims, chat lock, sweeper.

Needs DATABASE_URL_TEST (superuser on an empty database) — CI provides a Postgres service.
Skipped otherwise."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

URL = os.environ.get("DATABASE_URL_TEST")
pytestmark = pytest.mark.skipif(not URL, reason="DATABASE_URL_TEST not set")
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def db() -> str:
    env = {**os.environ, "DATABASE_URL_SESSION": URL, "APP_API_PASSWORD": "test-app-api"}
    subprocess.run([sys.executable, "scripts/migrate.py"], cwd=ROOT, env=env, check=True)
    subprocess.run([sys.executable, "scripts/seed.py", "global"], cwd=ROOT, env=env, check=True)
    return URL  # type: ignore[return-value]


def _company(conn: psycopg.Connection) -> tuple[str, str]:
    cid = str(uuid.uuid4())
    conn.execute("INSERT INTO core.company (id, company_id, name) VALUES (%s,%s,'t')", (cid, cid))
    sid = conn.execute("INSERT INTO core.chat_session (company_id, title) VALUES (%s,'c') RETURNING id",
                       (cid,)).fetchone()[0]
    ds = conn.execute("INSERT INTO raw.dataset (company_id, session_id, file_name, content_sha256, storage_key, "
                      "status) VALUES (%s,%s,'f',%s,'k','ready') RETURNING id", (cid, sid, uuid.uuid4().hex)).fetchone()[0]
    conn.execute("INSERT INTO core.sellin_line (company_id, dataset_id, row_no, order_date, sku_code, line_key) "
                 "VALUES (%s,%s,1,'2026-09-01','SKU1','k')", (cid, ds))
    run = conn.execute("INSERT INTO meta.workflow_run (company_id, session_id, target_month, \"trigger\") "
                       "VALUES (%s,%s,'2026-11-01','chat') RETURNING id", (cid, sid)).fetchone()[0]
    conn.execute("INSERT INTO meta.job_queue (company_id, run_id, session_id, kind) VALUES (%s,%s,%s,'plan')",
                 (cid, run, sid))
    return cid, str(sid)


def test_rls_isolates_every_table(db):
    with psycopg.connect(db, autocommit=True) as su:
        a, _ = _company(su)
        b, _ = _company(su)
        tables = [f"{r[0]}.{r[1]}" for r in su.execute(
            "SELECT schemaname, tablename FROM pg_tables WHERE schemaname IN ('raw','core','meta') "
            "AND tablename <> 'schema_migration'")]
    api_url = psycopg.conninfo.make_conninfo(db, user="app_api", password="test-app-api")
    with psycopg.connect(api_url) as conn:
        with conn.transaction():
            conn.execute("SELECT set_config('app.company_id', %s, true)", (a,))
            for t in tables:
                n = conn.execute(f"SELECT count(*) FROM {t} WHERE company_id = %s", (b,)).fetchone()[0]  # noqa: S608
                assert n == 0, t
            assert conn.execute("SELECT count(*) FROM core.sellin_line").fetchone()[0] >= 1
        with conn.transaction():  # no tenant set → nothing visible
            assert conn.execute("SELECT count(*) FROM core.sellin_line").fetchone()[0] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute("SELECT set_config('app.company_id', %s, true)", (a,))
                conn.execute("INSERT INTO core.chat_session (company_id) VALUES (%s)", (b,))


def test_two_workers_never_claim_the_same_job(db):
    from worker import queue

    with psycopg.connect(db, autocommit=True) as su:
        su.execute("UPDATE meta.job_queue SET status='done' WHERE status IN ('queued','running')")
        _company(su)
        _company(su)
    with psycopg.connect(db, row_factory=dict_row) as c1, psycopg.connect(db, row_factory=dict_row) as c2:
        j1 = queue.claim(c1, "w1")
        j2 = queue.claim(c2, "w2")
        assert j1 and j2 and j1.id != j2.id
        assert queue.take_chat_lock(c1, j1.session_id)
        assert not queue.take_chat_lock(c2, j1.session_id)  # same chat: second worker backs off
        queue.release_chat_lock(c1, j1.session_id)


def test_claim_skips_chats_with_a_running_job(db):
    from worker import queue

    with psycopg.connect(db, autocommit=True) as su:
        su.execute("UPDATE meta.job_queue SET status='done' WHERE status IN ('queued','running')")
        cid, sid = _company(su)
        su.execute("INSERT INTO meta.job_queue (company_id, session_id, kind, status) VALUES (%s,%s,'import','running')",
                   (cid, sid))
    with psycopg.connect(db, row_factory=dict_row) as c:
        assert queue.claim(c, "w") is None


def test_sweeper_requeues_then_fails(db):
    from worker import queue

    with psycopg.connect(db, autocommit=True) as plain:
        plain.execute("UPDATE meta.job_queue SET status='done' WHERE status IN ('queued','running')")
        cid, sid = _company(plain)
    with psycopg.connect(db, autocommit=True, row_factory=dict_row) as su:
        su.execute("UPDATE meta.job_queue SET status='running', heartbeat_at=now() - interval '5 minutes', attempt=1 "
                   "WHERE session_id=%s", (sid,))
        assert queue.sweep(su) == {"requeued": 1, "failed": 0}
        su.execute("UPDATE meta.job_queue SET status='running', heartbeat_at=now() - interval '5 minutes', attempt=3 "
                   "WHERE session_id=%s", (sid,))
        assert queue.sweep(su) == {"requeued": 0, "failed": 1}
        st = su.execute("SELECT r.status FROM meta.workflow_run r JOIN meta.job_queue q ON q.run_id = r.id "
                        "WHERE q.session_id=%s", (sid,)).fetchone()["status"]
        assert st == "failed"
