"""Postgres job queue on the worker's session-mode connection.

Claim = one short transaction with FOR UPDATE SKIP LOCKED that skips jobs whose chat
already has a running job; then a session-level advisory lock per chat held until the
job ends. Heartbeat every 15 s on its own connection, checking cancel_requested."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row

from core.logging import get_logger

log = get_logger(__name__)

CLAIM_SQL = """
UPDATE meta.job_queue j
SET status = 'running', locked_by = %(worker_id)s, heartbeat_at = now(), attempt = attempt + 1
WHERE j.id = (
    SELECT q.id FROM meta.job_queue q
    WHERE q.status = 'queued'
      AND NOT EXISTS (
          SELECT 1 FROM meta.job_queue q2
          WHERE q2.status = 'running' AND q2.session_id = q.session_id)
    ORDER BY q.created_at
    FOR UPDATE OF q SKIP LOCKED
    LIMIT 1)
RETURNING j.id, j.run_id, j.kind, j.session_id, j.company_id, j.dataset_id, j.payload, j.attempt
"""

LOCK_KEY_SQL = "SELECT hashtextextended(%s::text, 0) AS k"


@dataclass
class Job:
    id: str
    run_id: str | None
    kind: str
    session_id: str
    company_id: str
    dataset_id: str | None
    payload: dict[str, Any]
    attempt: int


def claim(conn: psycopg.Connection, worker_id: str) -> Job | None:
    with conn.transaction():
        row = conn.execute(CLAIM_SQL, {"worker_id": worker_id}).fetchone()
    if not row:
        return None
    r = dict(row) if not isinstance(row, dict) else row
    return Job(id=str(r["id"]), run_id=str(r["run_id"]) if r["run_id"] else None, kind=r["kind"],
               session_id=str(r["session_id"]), company_id=str(r["company_id"]),
               dataset_id=str(r["dataset_id"]) if r["dataset_id"] else None,
               payload=r["payload"] or {}, attempt=int(r["attempt"]))


def take_chat_lock(conn: psycopg.Connection, session_id: str) -> bool:
    """Session-level advisory lock; requires the session-mode pooler."""
    row = conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s::text, 0)) AS ok",
                       (session_id,)).fetchone()
    conn.commit()
    return bool(row["ok"] if isinstance(row, dict) else row[0])


def release_chat_lock(conn: psycopg.Connection, session_id: str) -> None:
    conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s::text, 0))", (session_id,))
    conn.commit()


def requeue(conn: psycopg.Connection, job_id: str) -> None:
    with conn.transaction():
        conn.execute("UPDATE meta.job_queue SET status='queued', locked_by=NULL, attempt=greatest(attempt-1,0) "
                     "WHERE id=%s", (job_id,))


def finish(conn: psycopg.Connection, job_id: str, status: str, error: str | None = None) -> None:
    with conn.transaction():
        conn.execute("UPDATE meta.job_queue SET status=%s, error=%s, heartbeat_at=now() WHERE id=%s",
                     (status, error, job_id))


def sweep(conn: psycopg.Connection, stale_seconds: int = 120, max_attempts: int = 3) -> dict[str, int]:
    """Re-queue running jobs with a stale heartbeat; fail them after max_attempts."""
    with conn.transaction():
        failed = conn.execute(
            """UPDATE meta.job_queue SET status='failed', error='worker heartbeat lost after max attempts'
               WHERE status='running' AND heartbeat_at < now() - make_interval(secs => %s) AND attempt >= %s
               RETURNING run_id""", (stale_seconds, max_attempts)).fetchall()
        for r in failed:
            rid = r["run_id"] if isinstance(r, dict) else r[0]
            if rid:
                conn.execute("UPDATE meta.workflow_run SET status='failed', finished_at=now(), "
                             "error='The worker stopped responding three times; retry the run.' "
                             "WHERE id=%s AND status IN ('queued','running')", (rid,))
        requeued = conn.execute(
            """UPDATE meta.job_queue SET status='queued', locked_by=NULL
               WHERE status='running' AND heartbeat_at < now() - make_interval(secs => %s) AND attempt < %s
               RETURNING id""", (stale_seconds, max_attempts)).fetchall()
    return {"requeued": len(requeued), "failed": len(failed)}


class Heartbeat:
    """Background thread: heartbeat_at every ``interval`` s; reads cancel_requested."""

    def __init__(self, pool: Any, job_id: str, interval: float = 15.0):
        self.pool = pool
        self.job_id = job_id
        self.interval = interval
        self.cancel_requested = False
        self.last_beat = time.monotonic()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="heartbeat", daemon=True)

    def start(self) -> Heartbeat:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def beat(self) -> None:
        with self.pool.connection() as conn:
            conn.row_factory = dict_row
            with conn.transaction():
                row = conn.execute(
                    "UPDATE meta.job_queue SET heartbeat_at = now() WHERE id = %s RETURNING cancel_requested",
                    (self.job_id,)).fetchone()
        if row and row["cancel_requested"]:
            self.cancel_requested = True
        self.last_beat = time.monotonic()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.beat()
            except Exception:  # noqa: BLE001
                log.warning("heartbeat failed")
