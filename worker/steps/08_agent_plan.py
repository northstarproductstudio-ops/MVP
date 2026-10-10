"""Step 8 — agent_plan: one structured planning-agent call per SKU (lines over
agent.max_lines_per_call split across calls), agent.parallel_calls at once.

The only step with LLM access. A failing SKU is retried once with the failed checks
listed (validator from step 9). An OpenRouter outage leaves the SKU without an answer,
so step 9 falls back to the reference DP — never a failed process. Re-opening a run
replays stored ``agent_call`` responses and never calls the model.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import pandas as pd

from core.domain.agent_checks import check_output
from core.llm import LLMError, load_prompt
from core.models.planning import AGENT_OUTPUT_JSON_SCHEMA
from worker.context import RunContext
from worker.steps import StepResult, step

COLS = ["sku_code", "chunk", "attempt", "response_json", "valid", "failed_checks", "source"]


def chunk_pack(pack: dict[str, Any], size: int) -> list[dict[str, Any]]:
    lines = pack["lines"]
    if len(lines) <= size:
        return [pack]
    return [{**pack, "lines": lines[i:i + size]} for i in range(0, len(lines), size)]


def _user_message(pack: dict[str, Any], failed: dict[str, list[str]] | None) -> str:
    msg = "Evidence pack:\n" + json.dumps(pack, sort_keys=True)
    if failed:
        msg += ("\n\nYour previous answer failed these checks; return a corrected answer for every line:\n"
                + json.dumps(failed, sort_keys=True))
    return msg


@step(name="agent_plan", no=8, reads=["evidence_pack"], writes=["agent_raw"],
      config_keys=["agent.enabled", "agent.prompt_version", "agent.temperature", "agent.max_lines_per_call",
                   "agent.parallel_calls", "agent.factor_bounds", "agent.judgment_cap",
                   "agent.explain_threshold", "agent.band", "agent.arith_tolerance", "agent.timeout_s"],
      version="1.0", uses_llm=True, timeout_s=1800)
def agent_plan(ctx: RunContext) -> StepResult:
    cfg = ctx.config
    if not cfg.get("agent.enabled"):
        return StepResult(status="skipped", summary="Planning agent off (agent.enabled = false); "
                                                    "plan uses the reference DP",
                          artifacts={"agent_raw": pd.DataFrame(columns=COLS)})
    if ctx.llm is None and ctx.replay is None:
        return StepResult(status="skipped", warnings=["Planning agent unavailable; reference DP used"],
                          artifacts={"agent_raw": pd.DataFrame(columns=COLS)})
    ev = ctx.artifact("evidence_pack")
    version = cfg.get("agent.prompt_version")
    system = load_prompt("demand_plan", version)
    size = int(cfg.get("agent.max_lines_per_call"))
    jobs = []
    for r in ev.itertuples():
        pack = json.loads(r.pack_json)
        for i, part in enumerate(chunk_pack(pack, size)):
            jobs.append((r.sku_code, i, part))
    total_skus = len(ev)
    done_skus: set[str] = set()
    rows: list[dict[str, Any]] = []

    def run_one(sku: str, chunk: int, pack: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        failed: dict[str, list[str]] | None = None
        for attempt in (1, 2):
            call_key = chunk * 10 + attempt
            parsed, source = None, "model"
            stored = ctx.replay(sku, call_key, 0) if ctx.replay else None
            if stored is not None:
                parsed, source = stored, "replay"
            elif ctx.llm is not None:
                try:
                    res = ctx.llm.complete(
                        role="planning", run_id=ctx.run_id, company_id=ctx.company_id, sku_code=sku,
                        attempt=call_key, prompt_version=version,
                        temperature=float(cfg.get("agent.temperature")),
                        json_schema=AGENT_OUTPUT_JSON_SCHEMA, schema_name="demand_plan_lines",
                        messages=[{"role": "system", "content": system},
                                  {"role": "user", "content": _user_message(pack, failed)}])
                    parsed = res.parsed
                except LLMError as exc:
                    out.append({"sku_code": sku, "chunk": chunk, "attempt": attempt, "response_json": None,
                                "valid": False, "failed_checks": json.dumps({"*": [f"llm: {exc}"]}),
                                "source": "error"})
                    return out
            else:
                return out
            good, failed = check_output(pack, parsed, cfg)
            ok = not failed and len(good) == len(pack["lines"])
            out.append({"sku_code": sku, "chunk": chunk, "attempt": attempt,
                        "response_json": json.dumps(parsed, sort_keys=True) if parsed is not None else None,
                        "valid": ok, "failed_checks": json.dumps(failed or {}), "source": source})
            if ok:
                break
        return out

    with ThreadPoolExecutor(max_workers=int(cfg.get("agent.parallel_calls"))) as pool:
        futures = {pool.submit(run_one, sku, ch, p): sku for sku, ch, p in jobs}
        for fut in as_completed(futures):
            rows.extend(fut.result())
            done_skus.add(futures[fut])
            if ctx.progress:
                ctx.progress({"type": "agent_progress", "done": len(done_skus), "total": total_skus,
                              "label": f"Planning agent: {len(done_skus)} of {total_skus} SKUs"})
    raw = pd.DataFrame(rows, columns=COLS).sort_values(["sku_code", "chunk", "attempt"]).reset_index(drop=True)
    final = raw.groupby(["sku_code", "chunk"]).tail(1)
    valid = int(final["valid"].sum())
    errors = int((final["source"] == "error").sum())
    warn = [f"Planning agent unreachable for {errors} calls; those SKUs use the reference DP"] if errors else []
    return StepResult(rows_in=len(jobs), rows_out=len(raw), warnings=warn,
                      summary=f"Planning agent answered {valid} of {len(jobs)} calls for {total_skus} SKUs "
                              f"({int((raw['attempt'] == 2).sum())} retried)",
                      artifacts={"agent_raw": raw})
