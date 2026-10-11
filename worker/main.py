"""fmcg-worker: claim loop + tiny health server.

Order (Deployment-Readiness Spec, Worker process lifecycle):
1. load Settings, configure logging and Sentry, clean TMP_DIR
2. health server on X_ZOHO_CATALYST_LISTEN_PORT (GET /health)
3. SIGTERM/SIGINT set shutting_down
4. loop: exit if shutting_down; claim a job; sleep 1 s when idle
5. for a job: chat lock, heartbeat thread, execute, stop heartbeat, release lock, clean TMP_DIR
"""

from __future__ import annotations

import json
import os
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from psycopg.rows import dict_row

from core.config import load_settings
from core.db import connect_with_backoff, get_pool
from core.llm import LLMClient
from core.logging import configure_logging, get_logger, init_sentry
from core.plan_config import PlanConfig
from core.storage import Store, clean_tmp
from worker import importer, queue
from worker.expiry import expire_removed
from worker.runner import DbRunner, agent_recorder

log = get_logger("worker")


class State:
    shutting_down = False
    current_run: str | None = None
    heartbeat: queue.Heartbeat | None = None
    last_sweep = 0.0
    last_expiry = float("-inf")  # first idle moment after start, then every 24 h


def serve_health(port: int, worker_id: str) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path.rstrip("/") != "/health":
                self.send_response(404)
                self.end_headers()
                return
            hb = State.heartbeat
            body = json.dumps({"status": "ok", "worker_id": worker_id, "run_id": State.current_run,
                               "seconds_since_heartbeat": round(time.monotonic() - hb.last_beat, 1) if hb else None,
                               "shutting_down": State.shutting_down}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            return

    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=srv.serve_forever, name="health", daemon=True).start()
    return srv


def main() -> None:
    settings = load_settings("worker")
    configure_logging("fmcg-worker", settings.app_env)
    init_sentry(settings.sentry_dsn.get_secret_value() if settings.sentry_dsn else None, "fmcg-worker",
                settings.app_env)
    clean_tmp(settings.tmp_dir)
    serve_health(settings.listen_port, settings.worker_id)

    def on_signal(signum: int, _frame: Any) -> None:
        log.info("signal %s: finishing the current step, then exiting", signum)
        State.shutting_down = True

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    pool = connect_with_backoff(get_pool("worker", settings), settings)
    store = Store(settings.supabase_url, settings.supabase_service_role_key.get_secret_value(),
                  settings.storage_bucket, settings.tmp_dir)
    log.info("worker %s ready", settings.worker_id)

    while not State.shutting_down:
        try:
            with pool.connection() as conn:
                conn.row_factory = dict_row
                if time.monotonic() - State.last_sweep > 120:
                    # Fallback sweeper in case Job Scheduling cannot call the API with a header.
                    queue.sweep(conn)
                    State.last_sweep = time.monotonic()
                job = queue.claim(conn, settings.worker_id)
                if job is None:
                    if time.monotonic() - State.last_expiry > 24 * 3600:
                        # Gap 12: nightly expiry of removed raw files, when idle
                        State.last_expiry = time.monotonic()
                        days = int(PlanConfig.load().get("data.removed_retention_days", 30))
                        log.info("expiry: %s", expire_removed(conn, store, days))
                    time.sleep(1)
                    continue
                if not queue.take_chat_lock(conn, job.session_id):
                    queue.requeue(conn, job.id)
                    time.sleep(1)
                    continue
                try:
                    handle(job, conn, pool, store, settings)
                finally:
                    queue.release_chat_lock(conn, job.session_id)
                    clean_tmp(settings.tmp_dir)
        except Exception:  # noqa: BLE001 - reconnect with backoff, never crash
            log.exception("worker loop error; reconnecting")
            time.sleep(1)
            pool = connect_with_backoff(pool, settings)
    log.info("worker exiting")


class KeepAlive:
    """While a job runs, GET our own public /health every minute so AppSail counts the
    instance as active and does not scale it down mid-run (WORKER_URL; no-op if unset)."""

    def __init__(self, interval: float = 60.0):
        self.url = os.environ.get("WORKER_URL")
        self.interval = interval
        self._stop = threading.Event()

    def start(self) -> KeepAlive:
        if self.url:
            threading.Thread(target=self._loop, name="keepalive", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        import httpx

        while not self._stop.wait(self.interval):
            try:
                httpx.get(self.url.rstrip("/") + "/health", timeout=10)  # type: ignore[union-attr]
            except httpx.HTTPError:
                log.warning("keep-alive ping failed")


def handle(job: queue.Job, conn: Any, pool: Any, store: Store, settings: Any) -> None:
    keepalive = KeepAlive().start()
    hb = queue.Heartbeat(pool, job.id).start()
    State.heartbeat = hb
    State.current_run = job.run_id
    try:
        if job.kind in ("plan", "backtest"):
            def should_stop() -> str | None:
                if hb.cancel_requested:
                    return "cancel"
                return "shutdown" if State.shutting_down else None

            status = DbRunner(pool, store, settings, should_stop).execute(job.run_id)  # type: ignore[arg-type]
            if status == "shutdown":
                return  # leave job running; the sweeper re-queues it and finished steps are reused
            queue.finish(conn, job.id, "done" if status == "succeeded" else
                         ("cancelled" if status == "cancelled" else "failed"))
        elif job.kind in ("import", "materialize"):
            cfg = PlanConfig.load()
            try:
                if job.kind == "import":
                    llm = LLMClient(api_key=settings.openrouter_api_key.get_secret_value(), model=settings.llm_model,
                                    fallback_model=settings.llm_fallback_model,
                                    base_url=settings.openrouter_base_url,
                                    recorder=agent_recorder(pool, job.company_id))
                    importer.run_import(conn, store, job.company_id, job.dataset_id, cfg, llm)  # type: ignore[arg-type]
                else:
                    importer.run_materialize(conn, store, job.company_id, job.dataset_id, cfg)  # type: ignore[arg-type]
                queue.finish(conn, job.id, "done")
            except Exception as exc:  # noqa: BLE001
                log.exception("import job failed")
                conn.rollback()
                with conn.transaction():
                    conn.execute("UPDATE raw.dataset SET status='failed', error=%s WHERE id=%s",
                                 (str(exc)[:1000], job.dataset_id))
                queue.finish(conn, job.id, "failed", str(exc)[:1000])
    finally:
        hb.stop()
        keepalive.stop()
        State.heartbeat = None
        State.current_run = None


if __name__ == "__main__":
    main()
