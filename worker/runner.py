"""Worker-side run execution: load the frozen snapshot, run steps 1–11 with Postgres/Storage
hooks, record run_step rows, reuse finished steps by fingerprint on retries, and never
leave a partial plan."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any

import pandas as pd
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from core.config import Settings
from core.llm import LLMClient
from core.logging import get_logger, run_id_var
from core.plan_config import PlanConfig
from core.storage import KeyExistsError, Store, run_key
from worker import snapshots
from worker.context import RunContext
from worker.executor import Hooks, Stopped, run_steps
from worker.repo import DISCRIMINATOR, copy_durable, write_durable
from worker.steps import StepResult, StepSpec, all_steps

log = get_logger(__name__)


class RunFailed(RuntimeError):
    def __init__(self, step: str | None, message: str):
        super().__init__(message)
        self.step = step


def agent_recorder(pool: Any, company_id: str) -> Callable[[dict[str, Any]], None]:
    """Write every LLM call to meta.agent_call (no API key ever in the request body)."""
    def record(rec: dict[str, Any]) -> None:
        with pool.connection() as conn, conn.transaction():
            conn.execute(
                """INSERT INTO meta.agent_call (company_id, run_id, session_id, role, sku_code, attempt,
                   prompt_version, model, request, response, valid, failed_checks, tokens_in, tokens_out,
                   cost_usd, latency_ms) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (rec.get("company_id") or company_id, rec.get("run_id"), _uuid_or_none(rec.get("session_id")),
                 rec["role"], rec.get("sku_code"), rec.get("attempt") or 1, rec.get("prompt_version"),
                 rec.get("model"), Jsonb(rec.get("request")), Jsonb(rec.get("response")), rec.get("valid"),
                 Jsonb(rec.get("failed_checks")), rec.get("tokens_in"), rec.get("tokens_out"),
                 rec.get("cost_usd"), rec.get("latency_ms")))
    return record


def _uuid_or_none(v: Any) -> Any:
    try:
        import uuid

        return str(uuid.UUID(str(v))) if v else None
    except ValueError:
        return None


def replay_source(conn: psycopg.Connection, run_id: str) -> Callable[[str, int, int], dict[str, Any] | None]:
    """Stored planning responses of this run, keyed by (sku, attempt)."""
    rows = conn.execute(
        "SELECT sku_code, attempt, response FROM meta.agent_call WHERE run_id=%s AND role='planning' "
        "AND response IS NOT NULL ORDER BY created_at", (run_id,)).fetchall()
    stored: dict[tuple[str, int], Any] = {}
    for r in rows:
        resp = r["response"]
        try:
            content = resp["choices"][0]["message"]["content"]
            stored[(r["sku_code"], int(r["attempt"]))] = json.loads(content)
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    lock = threading.Lock()

    def get(sku: str, attempt: int, _: int) -> dict[str, Any] | None:
        with lock:
            return stored.get((sku, attempt))
    return get


class DbRunner:
    def __init__(self, pool: Any, store: Store, settings: Settings,
                 should_stop: Callable[[], str | None]):
        self.pool = pool
        self.store = store
        self.settings = settings
        self.should_stop = should_stop

    # ------------------------------------------------------------------ helpers
    def _q(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            conn.row_factory = dict_row
            with conn.transaction():
                return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def _x(self, sql: str, params: tuple = ()) -> None:
        with self.pool.connection() as conn, conn.transaction():
            conn.execute(sql, params)

    # ------------------------------------------------------------------ execute
    def execute(self, run_id: str) -> str:
        """Returns final status: succeeded | failed | cancelled | shutdown."""
        token = run_id_var.set(run_id)
        try:
            return self._execute(run_id)
        finally:
            run_id_var.reset(token)

    def _execute(self, run_id: str) -> str:
        run = self._q("SELECT r.*, r.id::text AS rid, r.company_id::text AS cid, r.session_id::text AS sid, "
                      "r.retry_of::text AS retry FROM meta.workflow_run r WHERE r.id=%s", (run_id,))
        if not run:
            raise LookupError(f"run {run_id} not found")
        run = run[0]
        if run["status"] in ("succeeded", "failed", "cancelled"):
            return run["status"]
        company_id, session_id = run["cid"], run["sid"]
        cfg_row = self._q("SELECT config FROM meta.config_version WHERE id=%s", (run["config_version_id"],))
        cfg = PlanConfig(cfg_row[0]["config"]) if cfg_row else PlanConfig.load()
        self._x("UPDATE meta.workflow_run SET status='running', started_at=coalesce(started_at, now()) "
                "WHERE id=%s AND company_id=%s", (run_id, company_id))
        steps = all_steps()
        self._ensure_step_rows(run_id, company_id, steps)
        current: dict[str, StepSpec | None] = {"spec": None}
        try:
            with self.pool.connection() as conn:
                conn.row_factory = dict_row
                snapshot_id = run["snapshot_id"]
                if snapshot_id is None or run.get("scope", {}).get("rebuild_snapshot"):
                    snapshot_id = snapshots.snapshot_is_current(conn, company_id, session_id)
                    if snapshot_id is None:
                        snapshot_id, _ = snapshots.build_and_store(conn, self.store, company_id, session_id, cfg)
                    with conn.transaction():
                        conn.execute("UPDATE meta.workflow_run SET snapshot_id=%s WHERE id=%s", (snapshot_id, run_id))
                tables, as_of = snapshots.load(conn, self.store, company_id, str(snapshot_id))
                replay = replay_source(conn, run_id)
            as_of = pd.Timestamp(run["as_of"] or as_of)
            llm = LLMClient(api_key=self.settings.openrouter_api_key.get_secret_value(),
                            model=run["model"] or self.settings.llm_model,
                            base_url=self.settings.openrouter_base_url,
                            timeout_s=float(cfg.get("agent.timeout_s")),
                            recorder=agent_recorder(self.pool, company_id))
            ctx = RunContext(run_id=run_id, company_id=company_id, session_id=session_id, as_of=as_of,
                             target_month=pd.Timestamp(run["target_month"]), config=cfg, tables=tables,
                             llm=llm, replay=replay, progress=lambda p: self._progress(run_id, 8, p),
                             prompt_version=run["prompt_version"] or cfg.get("agent.prompt_version"),
                             scope={k: v for k, v in (run.get("scope") or {}).items() if k in ("platforms", "cities", "skus")})
            hooks = self._hooks(run, current)
            elog = run_steps(ctx, hooks, steps)
            warnings = sorted(set(elog.warnings))
            self._x("UPDATE meta.workflow_run SET status='succeeded', finished_at=now(), dp_total=%s, "
                    "order_total=%s, warnings=%s, as_of=%s WHERE id=%s AND company_id=%s",
                    (elog.run_updates.get("dp_total"), elog.run_updates.get("order_total"), Jsonb(warnings),
                     as_of.date(), run_id, company_id))
            if run["trigger"] == "backtest":
                self._score_backtest(run_id, company_id, ctx)
            else:
                self._x("UPDATE core.chat_session SET current_run_id=%s WHERE id=%s AND company_id=%s",
                        (run_id, session_id, company_id))
            return "succeeded"
        except Stopped as stop:
            spec = current["spec"]
            if spec is not None:
                self._x("UPDATE meta.run_step SET status='pending' WHERE run_id=%s AND step_no=%s "
                        "AND status='running'", (run_id, spec.no))
            if stop.reason == "cancel":
                self._fail_cleanup(run_id, company_id)
                self._x("UPDATE meta.workflow_run SET status='cancelled', finished_at=now() WHERE id=%s", (run_id,))
                return "cancelled"
            return "shutdown"
        except Exception as exc:  # noqa: BLE001 - a step failure must never crash the worker
            spec = current["spec"]
            msg = str(exc) or type(exc).__name__
            log.exception("run failed at step %s", spec.name if spec else "setup")
            self._capture(exc, run_id, spec)
            if spec is not None:
                self._x("UPDATE meta.run_step SET status='failed', error=%s, finished_at=now() "
                        "WHERE run_id=%s AND step_no=%s", (msg[:2000], run_id, spec.no))
            self._fail_cleanup(run_id, company_id)
            self._x("UPDATE meta.workflow_run SET status='failed', finished_at=now(), error=%s WHERE id=%s",
                    (msg[:2000], run_id))
            return "failed"

    def _score_backtest(self, run_id: str, company_id: str, ctx: RunContext) -> None:
        """Score reference and agent DP against actual sell-in of the target month."""
        from core.domain.backtest import score
        from worker.steps._actuals import line_actuals

        plan = ctx.artifacts["plan_lines"].merge(
            ctx.artifacts["reference"][["line_id", "python_factors"]], on="line_id", how="left")
        actual = line_actuals(ctx.tables["sellin_line"], ctx.target_month, ctx.config)
        prev = ctx.m1
        naive = line_actuals(ctx.tables["sellin_line"], prev, ctx.config)
        metrics = score(plan, actual, naive)
        with self.pool.connection() as conn, conn.transaction():
            conn.execute("DELETE FROM meta.run_metric WHERE run_id=%s", (run_id,))
            for m in metrics:
                conn.execute("INSERT INTO meta.run_metric (company_id, run_id, metric, class, value) "
                             "VALUES (%s,%s,%s,%s,%s)", (company_id, run_id, m["metric"], m["class"], m["value"]))

    def _fail_cleanup(self, run_id: str, company_id: str) -> None:
        """No partial plan: remove this run's plan outputs in one transaction."""
        with self.pool.connection() as conn, conn.transaction():
            for t in ("dp_output", "report_version", "report"):
                conn.execute(f"DELETE FROM core.{t} WHERE run_id=%s AND company_id=%s", (run_id, company_id))

    def _capture(self, exc: Exception, run_id: str, spec: StepSpec | None) -> None:
        try:
            import sentry_sdk

            with sentry_sdk.push_scope() as scope:
                scope.set_tag("run_id", run_id)
                scope.set_tag("step", spec.name if spec else "setup")
                sentry_sdk.capture_exception(exc)
        except Exception:  # noqa: BLE001
            pass

    def _ensure_step_rows(self, run_id: str, company_id: str, steps: list[StepSpec]) -> None:
        with self.pool.connection() as conn, conn.transaction():
            for s in steps:
                conn.execute("INSERT INTO meta.run_step (company_id, run_id, step_no, name) VALUES (%s,%s,%s,%s) "
                             "ON CONFLICT (run_id, step_no) DO NOTHING", (company_id, run_id, s.no, s.name))

    def _progress(self, run_id: str, step_no: int, payload: dict[str, Any]) -> None:
        try:
            self._x("UPDATE meta.run_step SET progress=%s WHERE run_id=%s AND step_no=%s",
                    (Jsonb(payload), run_id, step_no))
        except Exception:  # noqa: BLE001
            pass

    def _hooks(self, run: dict[str, Any], current: dict[str, StepSpec | None]) -> Hooks:
        run_id, company_id = run["rid"], run["cid"]
        chain = self._retry_chain(run)

        def on_start(spec: StepSpec, fp: str) -> None:
            current["spec"] = spec
            self._x("UPDATE meta.run_step SET status='running', attempt=attempt+1, fingerprint=%s, "
                    "started_at=now(), error=NULL WHERE run_id=%s AND step_no=%s", (fp, run_id, spec.no))

        def save_artifacts(spec: StepSpec, arts: dict[str, pd.DataFrame]) -> dict[str, str]:
            keys = {}
            attempt = self._q("SELECT attempt FROM meta.run_step WHERE run_id=%s AND step_no=%s",
                              (run_id, spec.no))[0]["attempt"]
            for name, df in arts.items():
                key = run_key(company_id, run_id, f"{spec.no:02d}_{name}_a{attempt}")
                try:
                    self.store.put_parquet(key, _parquet_safe(df))
                except KeyExistsError:
                    pass  # same attempt already uploaded before a crash; content is identical
                keys[name] = key
            return keys

        def on_done(spec: StepSpec, res: StepResult, fp: str, keys: dict[str, str]) -> None:
            with self.pool.connection() as conn:
                for table, frames in _group_writes(res).items():
                    write_durable(conn, company_id, run_id, table, frames)
            self._x("UPDATE meta.run_step SET status=%s, rows_in=%s, rows_out=%s, artifact_key=%s, summary=%s, "
                    "warnings=%s, finished_at=now() WHERE run_id=%s AND step_no=%s",
                    (res.status, res.rows_in, res.rows_out, Jsonb(keys), res.summary[:2000],
                     Jsonb(res.warnings), run_id, spec.no))
            current["spec"] = None

        def find_reusable(spec: StepSpec, fp: str) -> dict[str, Any] | None:
            if spec.uses_llm and spec.no == 11:
                return None
            rows = self._q(
                "SELECT run_id::text AS run_id, artifact_key, summary, rows_in, rows_out, warnings, status "
                "FROM meta.run_step WHERE run_id = ANY(%s::uuid[]) AND step_no=%s AND fingerprint=%s "
                "AND status IN ('succeeded','skipped','reused') ORDER BY finished_at DESC LIMIT 1",
                (chain, spec.no, fp))
            if not rows:
                return None
            r = rows[0]
            return {"run_id": r["run_id"], "artifact_keys": r["artifact_key"] or {}, "row": r}

        def load_artifacts(keys: dict[str, str]) -> dict[str, pd.DataFrame]:
            out = {}
            for name, key in keys.items():
                df = self.store.read_parquet(key)
                for c in df.columns:
                    if c in ("month", "week", "first_sale", "stock_snapshot_date"):
                        df[c] = pd.to_datetime(df[c])
                if name == "plan_lines" or name == "netted":
                    df["flags"] = df["flags"].map(lambda f: list(f) if f is not None else [])
                out[name] = df
            return out

        def on_reused(spec: StepSpec, fp: str, prev: dict[str, Any]) -> None:
            src = prev["run_id"]
            if src != run_id:
                with self.pool.connection() as conn:
                    for table in [w for w in spec.writes if w in ("factor", "variable_value")]:
                        disc_vals = _disc_values(spec, table)
                        copy_durable(conn, company_id, src, run_id, table, disc_vals)
            row = prev["row"]
            self._x("UPDATE meta.run_step SET status='reused', fingerprint=%s, reused_from_run_id=%s, "
                    "artifact_key=%s, summary=%s, rows_in=%s, rows_out=%s, warnings=%s, finished_at=now() "
                    "WHERE run_id=%s AND step_no=%s",
                    (fp, src, Jsonb(prev["artifact_keys"]), row["summary"], row["rows_in"], row["rows_out"],
                     Jsonb(row["warnings"] or []), run_id, spec.no))

        return Hooks(on_start=on_start, on_done=on_done, on_reused=on_reused, find_reusable=find_reusable,
                     load_artifacts=load_artifacts, save_artifacts=save_artifacts, should_stop=self.should_stop)

    def _retry_chain(self, run: dict[str, Any]) -> list[str]:
        chain = [run["rid"]]
        prev = run.get("retry")
        while prev and len(chain) < 10:
            chain.append(prev)
            r = self._q("SELECT retry_of::text AS p FROM meta.workflow_run WHERE id=%s", (prev,))
            prev = r[0]["p"] if r else None
        return chain


def _group_writes(res: StepResult) -> dict[str, list[pd.DataFrame]]:
    return {t: [df] for t, df in res.writes.items()}


_DISC_BY_STEP = {
    ("derive_factors", "factor"): ["system"], ("validate_agent_output", "factor"): ["agent"],
    ("correct_oos", "variable_value"): ["osa_28d", "osa_min_7d", "competitor_osa"],
    ("classify_skus", "variable_value"): ["abc", "sb_class"],
    ("base_forecast", "variable_value"): ["base_np", "city_share"],
}


def _disc_values(spec: StepSpec, table: str) -> list[str] | None:
    if table not in DISCRIMINATOR:
        return None
    vals = _DISC_BY_STEP.get((spec.name, table))
    if vals is None and table == "variable_value":
        return None
    return vals


def _parquet_safe(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if out[c].dtype == object:
            sample = out[c].dropna().head(5).tolist()
            if sample and all(isinstance(v, list) for v in sample):
                continue
            out[c] = out[c].map(lambda v: None if v is None or (isinstance(v, float) and pd.isna(v)) else
                                (v if isinstance(v, (str, bool, int, float)) else str(v)))
    return out


_ = time
