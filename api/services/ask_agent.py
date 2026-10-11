"""Ask agent: a tool loop over the chat's data and plans, streamed as SSE events.

Context per turn: system prompt, workspace summary, current run id/status/factor list,
the last 20 messages. Plan lines are never pasted into the prompt; tools fetch them.
No DB connection is held while waiting on the model: each tool opens its own short
tenant transaction."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from psycopg.types.json import Jsonb

from api.services import edits as edit_svc
from api.services import runs as run_svc
from api.services.plans import load_plan
from api.services.readiness import dataset_states
from api.services.workspace import Workspace, open_workspace, snapshot_row
from core.db import tenant_transaction
from core.domain.edits import EditError
from core.domain.fenced import FencedQueryError, run_fenced
from core.domain.known_queries import QUERIES, QueryParamError, SourceMissing, run_known
from core.domain.readiness import coverage, evaluate
from core.llm import LLMClient, LLMError, load_prompt
from core.logging import get_logger
from core.models.edit import Edit
from worker.steps import section_for

log = get_logger(__name__)
MAX_TOOL_ROUNDS = 6

TOOLS = [
    {"type": "function", "function": {
        "name": "run_demand_plan",
        "description": "Start the demand-plan pipeline for a target month and wait up to 90 s for it.",
        "parameters": {"type": "object", "properties": {
            "target_month": {"type": "string", "description": "'next month', a month name or YYYY-MM"},
            "scope": {"type": "object", "description": "optional scope, e.g. {\"platforms\": [\"blinkit\"]}"}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "get_run", "description": "Summary of a run: steps, factors, totals, top lines, warnings, versions.",
        "parameters": {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": []}}},
    {"type": "function", "function": {
        "name": "query_known",
        "description": "Run one known parameterised query. Known queries: "
                       + "; ".join(f"{k}: {v[1]}" for k, v in QUERIES.items()),
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "enum": list(QUERIES)}, "params": {"type": "object"}},
            "required": ["name", "params"]}}},
    {"type": "function", "function": {
        "name": "query_workspace",
        "description": "Fenced read-only lookup: one SELECT over sellin_line, po_line, stock_snapshot, market_daily, "
                       "extra_value, sku_master, sku_crosswalk, customer_map, city_map. Use only when no known query fits.",
        "parameters": {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]}}},
    {"type": "function", "function": {
        "name": "apply_edit",
        "description": "Directly edit stored plan numbers (no re-run). op 'pct' (value −90..300) or 'set' (whole units, "
                       "one line). Empty target fields mean all.",
        "parameters": {"type": "object", "properties": {
            "run_id": {"type": "string"},
            "target": {"type": "object", "properties": {
                "sku_code": {"type": ["string", "null"]}, "plan_category": {"type": ["string", "null"]},
                "city": {"type": ["string", "null"]}, "platform": {"type": ["string", "null"]}}},
            "op": {"type": "string", "enum": ["pct", "set"]}, "value": {"type": "number"},
            "note": {"type": ["string", "null"]}}, "required": ["target", "op", "value"]}}},
    {"type": "function", "function": {
        "name": "undo_edit", "description": "Move the current plan version back one.",
        "parameters": {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": []}}},
]

TOOL_LABELS = {"run_demand_plan": "Starting the demand plan", "get_run": "Reading the plan run",
               "query_known": "Looking up the data", "query_workspace": "Running a direct lookup",
               "apply_edit": "Applying your change to the plan", "undo_edit": "Undoing the last change"}


PLAN_CODES_NEEDING_SALES = {"no_sales_file", "no_sales_history", "sales_fields_unmapped"}
GROUP_LABEL = {"sellin": "Zoho sales-order export", "po": "platform PO export", "stock": "Blinkit DOH export",
               "market": "Dcluttr category insight export"}


def missing_file(result: dict[str, Any]) -> dict[str, Any] | None:
    """The file type a tool result says is missing, for the chat's "Add file" action."""
    code = result.get("error_code")
    group = None
    if code in PLAN_CODES_NEEDING_SALES:
        group = "sellin"
    elif code == "source_missing":
        group = result.get("file_type")
    elif result.get("missing_sources"):
        group = result["missing_sources"][0].get("file_type")
    if not group:
        return None
    return {"file_group": group, "label": GROUP_LABEL.get(group, group),
            "action": "map" if code == "sales_fields_unmapped" else "add"}


def event(kind: str, session_id: str, **payload: Any) -> dict[str, Any]:
    return {"id": uuid.uuid4().hex, "type": kind, "session_id": session_id,
            "ts": datetime.now(UTC).isoformat(timespec="seconds"), **payload}


class AskTurn:
    def __init__(self, pool: Any, store: Any, settings: Any, cfg: Any, company_id: str, user_id: str,
                 session_id: str):
        self.pool, self.store, self.settings, self.cfg = pool, store, settings, cfg
        self.company_id, self.user_id, self.session_id = company_id, user_id, session_id
        self.records: list[dict[str, Any]] = []
        self.llm = LLMClient(api_key=settings.openrouter_api_key.get_secret_value(), model=settings.llm_model,
                             fallback_model=settings.llm_fallback_model, base_url=settings.openrouter_base_url, recorder=self.records.append)
        self.evidence: list[dict[str, Any]] = []
        self.current_run: str | None = None

    def tx(self) -> Any:
        return tenant_transaction(self.pool, self.company_id)

    async def _flush_records(self) -> None:
        if not self.records:
            return
        recs, self.records = self.records, []
        async with self.tx() as cur:
            for r in recs:
                await cur.execute(
                    """INSERT INTO meta.agent_call (company_id, run_id, session_id, role, attempt, prompt_version,
                       model, request, response, failed_checks, tokens_in, tokens_out, cost_usd, latency_ms)
                       VALUES (%s,%s,%s,'ask',1,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (self.company_id, r.get("run_id"), self.session_id, r.get("prompt_version"), r.get("model"),
                     Jsonb(r.get("request")), Jsonb(r.get("response")), Jsonb(r.get("failed_checks")),
                     r.get("tokens_in"), r.get("tokens_out"), r.get("cost_usd"), r.get("latency_ms")))

    async def _workspace(self) -> Workspace:
        async with self.tx() as cur:
            snap = await snapshot_row(cur, self.session_id)
            lines = None
            if self.current_run:
                try:
                    lines, _, _ = await load_plan(cur, self.current_run)
                except LookupError:
                    lines = None
        return await open_workspace(snap, self.store, self.company_id, lines, self.settings.tmp_dir,
                                    self.settings.duckdb_memory_limit)

    async def context(self) -> list[dict[str, Any]]:
        async with self.tx() as cur:
            await cur.execute("SELECT current_run_id::text AS run FROM core.chat_session WHERE id=%s",
                              (self.session_id,))
            row = await cur.fetchone()
            if row is None:
                raise LookupError("chat not found")
            self.current_run = row["run"]
            await cur.execute("SELECT role, content FROM core.chat_message WHERE session_id=%s AND role IN "
                              "('user','assistant') ORDER BY created_at DESC LIMIT %s",
                              (self.session_id, int(self.cfg.get("ask.history_messages"))))
            history = list(reversed([dict(r) for r in await cur.fetchall()]))
            states = await dataset_states(cur, self.session_id)
            cov = coverage(states)
            ready = evaluate(states)
            run_info: dict[str, Any] | None = None
            if self.current_run:
                s = await run_svc.run_summary(cur, self.current_run)
                run_info = {"run_id": s["id"], "status": s["status"], "target_month": str(s["target_month"]),
                            "dp_total": s["dp_total"], "factors": s["factors"]}
        ws = await self._workspace()
        ws.con.close()
        summary = {**ws.summary, "coverage": cov}
        ctx = {"workspace": summary, "plan_readiness": {"plan_ready": ready.plan_ready, "code": ready.code,
                                                         "missing_for_plan": ready.as_dict()["blocking_display"]},
               "current_run": run_info, "today": f"{datetime.now(UTC):%Y-%m-%d}"}
        msgs = [{"role": "system", "content": load_prompt("ask_agent", self.cfg.get("ask.prompt_version", "v1"))},
                {"role": "system", "content": "Context: " + json.dumps(ctx, default=str)}]
        msgs += [{"role": h["role"], "content": h["content"] or ""} for h in history]
        return msgs

    # ------------------------------------------------------------------ tools
    async def tool(self, name: str, args: dict[str, Any], emit: Any) -> dict[str, Any]:
        started = time.monotonic()
        if name == "run_demand_plan":
            return await self._run_plan(args, emit)
        if name == "get_run":
            rid = args.get("run_id") or self.current_run
            if not rid:
                return {"error": "no plan exists in this chat yet"}
            async with self.tx() as cur:
                s = await run_svc.run_summary(cur, rid)
                try:
                    lines, ver, vs = await load_plan(cur, rid)
                    top = lines.sort_values("final_dp", ascending=False).head(20)
                    s["top_lines"] = top[["line_id", "base", "final_dp", "reason", "bridge"]].to_dict("records")
                    s["versions"] = [{"version": v["version"], "note": v["note"], "current": v["is_current"]} for v in vs]
                except LookupError:
                    pass
            self.evidence.append({"label": "From the demand plan, step 11", "query": "get_run",
                                  "rows": s.get("top_lines", [])[:20], "row_count": len(s.get("top_lines", []))})
            return json.loads(json.dumps(s, default=str))
        if name == "query_known":
            ws = await self._workspace()
            try:
                res = run_known(ws.con, args.get("name", ""), args.get("params") or {})
            except SourceMissing as exc:
                await self._log(None, "known", args.get("name"), None, 0, started, str(exc))
                return exc.as_dict()
            except QueryParamError as exc:
                await self._log(args.get("question", ""), "known", args.get("name"), None, 0, started, str(exc))
                return {"error": str(exc)}
            finally:
                ws.con.close()
            label = res.label + (f" · Section · {res.section}" if res.section else "")
            self.evidence.append({"label": label, "query": res.name, "rows": res.rows[:50], "row_count": res.row_count})
            await self._log(None, "known", res.name, None, res.row_count, started, None)
            return {"query": res.name, "label": res.label, "row_count": res.row_count, "rows": res.rows[:200]}
        if name == "query_workspace":
            ws = await self._workspace()
            try:
                lk = await asyncio.to_thread(run_fenced, ws.con, args.get("sql", ""),
                                              int(self.cfg.get("ask.row_cap")), float(self.cfg.get("ask.timeout_s")))
            except FencedQueryError as exc:
                await self._log(None, "fenced", None, args.get("sql"), 0, started, str(exc))
                return {"error": str(exc)}
            finally:
                ws.con.close()
            self.evidence.append({"label": lk.label, "query": lk.sql, "rows": lk.rows[:50],
                                  "row_count": lk.row_count})
            await self._log(None, "fenced", None, lk.sql, lk.row_count, started, None)
            out = {"label": lk.label, "row_count": lk.row_count, "truncated": lk.truncated, "rows": lk.rows[:200]}
            if lk.empty_tables:
                missing = [SourceMissing(t) for t in lk.empty_tables]
                out["missing_sources"] = [m.as_dict() for m in missing]
                out["note"] = ("; ".join(str(m) for m in missing)
                               + ". Say so instead of answering 0.")
            return out
        if name == "apply_edit":
            rid = args.get("run_id") or self.current_run
            if not rid:
                return {"error": "no plan exists in this chat yet"}
            try:
                edit = Edit.model_validate({k: args.get(k) for k in ("target", "op", "value", "note")})
                async with self.tx() as cur:
                    out = await edit_svc.apply_edit(cur, self.company_id, rid, edit, self.user_id, self.cfg.as_dict())
            except (EditError, ValueError) as exc:
                return {"error": str(exc)}
            self.evidence.append({"label": f"Plan edit · report version {out['version']}", "query": "apply_edit",
                                  "rows": [out], "row_count": out["lines_changed"]})
            emit(event("report_card", self.session_id, **(await self._card(rid))))
            return out
        if name == "undo_edit":
            rid = args.get("run_id") or self.current_run
            if not rid:
                return {"error": "no plan exists in this chat yet"}
            try:
                async with self.tx() as cur:
                    out = await edit_svc.undo_edit(cur, rid)
            except EditError as exc:
                return {"error": str(exc)}
            emit(event("report_card", self.session_id, **(await self._card(rid))))
            return out
        return {"error": f"unknown tool {name}"}

    async def _card(self, run_id: str) -> dict[str, Any]:
        async with self.tx() as cur:
            s = await run_svc.run_summary(cur, run_id)
            vs = [v for v in (await _versions(cur, run_id)) if v["is_current"]]
        return run_svc.report_card(s, vs[0] if vs else None)

    async def _run_plan(self, args: dict[str, Any], emit: Any) -> dict[str, Any]:
        try:
            async with self.tx() as cur:
                q = await run_svc.enqueue_run(cur, self.company_id, self.session_id, args.get("target_month"),
                                              args.get("scope"), "chat", self.user_id)
        except run_svc.PreconditionError as exc:
            # Gap 7: structured, so the agent explains what is missing and what to do next
            return exc.as_dict()
        rid = q["run_id"]
        self.current_run = rid
        emit(event("run_queued", self.session_id, run_id=rid, target_month=q["target_month"]))
        deadline = time.monotonic() + float(self.cfg.get("ask.run_wait_s"))
        seen: dict[int, str] = {}
        while time.monotonic() < deadline:
            await asyncio.sleep(1)
            async with self.tx() as cur:
                s = await run_svc.run_summary(cur, rid)
            for st in s["steps"]:
                if seen.get(st["step_no"]) != st["status"]:
                    seen[st["step_no"]] = st["status"]
                    emit(event("step", self.session_id, run_id=rid, step_no=st["step_no"], name=st["name"],
                               section=section_for(st["step_no"]),
                               status=st["status"], rows=st["rows_out"], summary=st["summary"]))
            if s["status"] in ("succeeded", "failed", "cancelled"):
                if s["status"] == "succeeded":
                    card = await self._card(rid)
                    emit(event("report_card", self.session_id, **card))
                    return {"run_id": rid, "status": "succeeded", "summary": card}
                failed = next((st for st in s["steps"] if st["status"] == "failed"), None)
                emit(event("run_failed", self.session_id, run_id=rid, step=failed["name"] if failed else None,
                           message=s["error"], retry_allowed=True))
                return {"run_id": rid, "status": s["status"], "error": s["error"]}
        return {"run_id": rid, "status": "running",
                "note": "Still running; the report card is posted in this chat when it finishes."}

    async def _log(self, question: str | None, path: str, qname: str | None, sql: str | None, rows: int,
                   started: float, error: str | None) -> None:
        async with self.tx() as cur:
            await cur.execute("""INSERT INTO core.ask_log (company_id, session_id, question, path, query_name, sql_text,
                                 row_count, latency_ms, error) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                              (self.company_id, self.session_id, question or self._question, path, qname, sql, rows,
                               int((time.monotonic() - started) * 1000), error))

    # ------------------------------------------------------------------ turn
    async def run(self, question: str) -> AsyncIterator[dict[str, Any]]:
        self._question = question
        async with self.tx() as cur:
            await cur.execute("INSERT INTO core.chat_message (company_id, session_id, role, content) "
                              "VALUES (%s,%s,'user',%s)", (self.company_id, self.session_id, question))
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        def emit(ev: dict[str, Any]) -> None:
            queue.put_nowait(ev)

        async def drain() -> AsyncIterator[dict[str, Any]]:
            while not queue.empty():
                yield queue.get_nowait()

        messages = await self.context()
        messages.append({"role": "user", "content": question})
        answer = ""
        tool_log: list[dict[str, Any]] = []
        for _ in range(MAX_TOOL_ROUNDS):
            try:
                res = await asyncio.to_thread(self.llm.complete, role="ask", messages=messages, tools=TOOLS,
                                              temperature=0.0, session_id=self.session_id,
                                              company_id=self.company_id)
            except LLMError:
                answer = "I can't reach the language model right now. Please try again in a minute."
                break
            finally:
                await self._flush_records()
            if not res.tool_calls:
                answer = res.content or ""
                break
            messages.append({"role": "assistant", "content": res.content or "", "tool_calls": res.tool_calls})
            for tc in res.tool_calls:
                fn = tc.get("function") or {}
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                yield event("tool_call", self.session_id, name=name, label=TOOL_LABELS.get(name, name))
                task = asyncio.create_task(self.tool(name, args, emit))
                while not task.done():
                    await asyncio.sleep(0.25)
                    async for ev in drain():
                        yield ev
                try:
                    result = task.result()
                except Exception as exc:  # noqa: BLE001 - a tool error must not break the stream
                    log.exception("tool %s failed", name)
                    result = {"error": f"{name} failed: {type(exc).__name__}"}
                async for ev in drain():
                    yield ev
                missing = missing_file(result) if isinstance(result, dict) else None
                if missing:  # Gap 12: the chat shows an "Add file" action under the reply
                    yield event("missing_file", self.session_id, **missing)
                tool_log.append({"name": name, "args": args,
                                 "ok": isinstance(result, dict) and "error" not in result and "error_code" not in result,
                                 "run_id": result.get("run_id") if isinstance(result, dict) else None,
                                 "missing_file": missing})
                messages.append({"role": "tool", "tool_call_id": tc.get("id"), "name": name,
                                 "content": json.dumps(result, default=str)[:60_000]})
        for ev in self.evidence:
            yield event("evidence", self.session_id, **ev)
        for chunk in _chunks(answer):
            yield event("message_delta", self.session_id, text=chunk)
        async with self.tx() as cur:
            await cur.execute("INSERT INTO core.chat_message (company_id, session_id, role, content, tool_calls, "
                              "evidence) VALUES (%s,%s,'assistant',%s,%s,%s) RETURNING id::text",
                              (self.company_id, self.session_id, answer, Jsonb(tool_log), Jsonb(self.evidence)))
            mid = (await cur.fetchone())["id"]
            if self.current_run:
                await cur.execute("UPDATE core.chat_session SET current_run_id = coalesce(current_run_id, %s) "
                                  "WHERE id=%s", (self.current_run, self.session_id))
        yield event("message_done", self.session_id, message_id=mid)


async def _versions(cur: Any, run_id: str) -> list[dict[str, Any]]:
    from api.services.plans import versions

    return await versions(cur, run_id)


def _chunks(text: str, size: int = 120) -> list[str]:
    if not text:
        return []
    words, out, buf = text.split(" "), [], ""
    for w in words:
        if len(buf) + len(w) + 1 > size and buf:
            out.append(buf + " ")
            buf = w
        else:
            buf = f"{buf} {w}" if buf else w
    if buf:
        out.append(buf)
    return out
