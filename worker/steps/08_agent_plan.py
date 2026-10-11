"""Step 8 — agent_plan: one structured planning-agent call per category batch (Gaps 3–5),
agent.parallel_calls at once.

Batches come from step 7 (``agent_batches``): one per plan category, split into sub-batches
of whole SKUs over agent.max_lines_per_call, plus general batches for uncategorised SKUs.
The user message holds the category name, the category signals and every SKU pack in the
batch. The agent declares category-wide effects once (``category_factors``) and returns its
own ``final_dp`` for every line (prompt v2, ``AGENT_OUTPUT_JSON_SCHEMA_V2``).

The only step with LLM access. A batch is retried once, resending only the SKUs whose lines
failed, with their failed checks; a SKU failing twice falls back to the reference DP in step
9. An OpenRouter outage (both models down) leaves the batch without an answer, so step 9
falls back too — never a failed process. ``meta.agent_call.sku_code`` holds the batch key;
re-opening a run replays stored responses by (batch_key, attempt) and never calls the model.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import pandas as pd

from core.domain.agent_checks import check_batch_output
from core.llm import LLMError, load_prompt
from core.models.planning import AGENT_OUTPUT_JSON_SCHEMA_V2
from worker.context import RunContext
from worker.steps import StepResult, step

COLS = ["batch_key", "sku_code", "attempt", "response_json", "valid", "failed_checks", "source", "model"]


def user_message(batch: dict[str, Any], packs: list[dict[str, Any]],
                 failed: dict[str, dict[str, list[str]]] | None) -> str:
    body = {"category": batch.get("category"), "category_signals": batch.get("category_signals") or {},
            "skus": packs}
    msg = "Category batch:\n" + json.dumps(body, sort_keys=True, default=str)
    if failed:
        msg += ("\n\nYour previous answer failed these checks for the SKUs above (by SKU, then line). "
                "Return a corrected answer for every line of these SKUs, with the category factors "
                "they use:\n" + json.dumps(failed, sort_keys=True))
    return msg


@step(name="agent_plan", no=8, reads=["evidence_pack", "agent_batches"], writes=["agent_raw"],
      config_keys=["agent.enabled", "agent.prompt_version", "agent.temperature", "agent.max_lines_per_call",
                   "agent.parallel_calls", "agent.factor_bounds", "agent.judgment_cap",
                   "agent.explain_threshold", "agent.band", "agent.arith_tolerance", "agent.timeout_s",
                   "agent.batch_by", "agent.uncategorised_batches"],
      version="2.0", uses_llm=True, timeout_s=1800)
def agent_plan(ctx: RunContext) -> StepResult:
    cfg = ctx.config
    empty = pd.DataFrame(columns=COLS)
    if not cfg.get("agent.enabled"):
        return StepResult(status="skipped", summary="Planning agent off (agent.enabled = false); "
                                                    "plan uses the reference DP",
                          artifacts={"agent_raw": empty})
    if ctx.llm is None and ctx.replay is None:
        return StepResult(status="skipped", warnings=["Planning agent unavailable; reference DP used"],
                          artifacts={"agent_raw": empty})
    ev = ctx.artifact("evidence_pack")
    batches = ctx.artifact("agent_batches")
    packs = {r.sku_code: json.loads(r.pack_json) for r in ev.itertuples()}
    version = cfg.get("agent.prompt_version")
    if version == "v1":  # the v1 prompt is per-SKU; batches need v2 or later
        version = "v2"
    system = load_prompt("demand_plan", version)
    total = len(batches)

    def run_batch(b: dict[str, Any]) -> list[dict[str, Any]]:
        key = b["batch_key"]
        todo = list(json.loads(b["sku_codes"]))
        out: list[dict[str, Any]] = []
        failed_by_sku: dict[str, dict[str, list[str]]] | None = None
        for attempt in (1, 2):
            sent = [packs[s] for s in todo if s in packs]
            if not sent:
                break
            parsed, source, model = None, "model", None
            stored = ctx.replay(key, attempt, 0) if ctx.replay else None
            if stored is not None:
                parsed, source = stored, "replay"
            elif ctx.llm is not None:
                try:
                    res = ctx.llm.complete(
                        role="planning", run_id=ctx.run_id, company_id=ctx.company_id, sku_code=key,
                        attempt=attempt, prompt_version=version,
                        temperature=float(cfg.get("agent.temperature")),
                        json_schema=AGENT_OUTPUT_JSON_SCHEMA_V2, schema_name="demand_plan_batch",
                        messages=[{"role": "system", "content": system},
                                  {"role": "user", "content": user_message(b, sent, failed_by_sku)}])
                    parsed, model = res.parsed, res.model
                except LLMError as exc:
                    for s in todo:
                        out.append({"batch_key": key, "sku_code": s, "attempt": attempt, "response_json": None,
                                    "valid": False, "failed_checks": json.dumps({"*": [f"llm: {exc}"]}),
                                    "source": "error", "model": None})
                    return out
            else:
                return out
            text = json.dumps(parsed, sort_keys=True) if parsed is not None else None
            failed_by_sku = {}
            for s in todo:
                pack = packs[s]
                if parsed is None:
                    good, failed = {}, {"*": ["schema: no parseable JSON"]}
                else:
                    good, failed = check_batch_output(pack, parsed, cfg)
                ok = not failed and len(good) == len(pack["lines"])
                out.append({"batch_key": key, "sku_code": s, "attempt": attempt, "response_json": text,
                            "valid": ok, "failed_checks": json.dumps(failed or {}), "source": source,
                            "model": model})
                if not ok:
                    failed_by_sku[s] = failed
            todo = [s for s in todo if s in failed_by_sku]  # retry resends only the failing SKUs
            if not todo:
                break
        return out

    rows: list[dict[str, Any]] = []
    done = 0
    with ThreadPoolExecutor(max_workers=int(cfg.get("agent.parallel_calls"))) as pool:
        futures = [pool.submit(run_batch, b) for b in batches.to_dict("records")]
        for fut in as_completed(futures):
            rows.extend(fut.result())
            done += 1
            if ctx.progress:
                ctx.progress({"type": "agent_progress", "done": done, "total": total, "unit": "batches",
                              "label": f"Planning agent: {done} of {total} batches"})
    raw = pd.DataFrame(rows, columns=COLS).sort_values(["batch_key", "sku_code", "attempt"]).reset_index(drop=True)
    final = raw.groupby(["batch_key", "sku_code"]).tail(1)
    valid_skus = int(final["valid"].sum())
    calls = int(raw.drop_duplicates(["batch_key", "attempt"])["source"].isin(["model", "replay"]).sum())
    errors = int((raw.drop_duplicates(["batch_key", "attempt"])["source"] == "error").sum())
    warn = [f"Planning agent unreachable for {errors} batches; those SKUs use the reference DP"] if errors else []
    return StepResult(rows_in=total, rows_out=len(raw), warnings=warn,
                      summary=f"Planning agent: {calls} calls over {total} batches; {valid_skus} of {len(packs)} "
                              f"SKUs answered ({int((raw['attempt'] == 2).sum())} SKU retries)",
                      artifacts={"agent_raw": raw})
