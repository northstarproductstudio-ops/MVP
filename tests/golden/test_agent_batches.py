"""Gaps 1 and 3–6 on the synthetic workspace, with the scripted OpenRouter stand-in.

* call count: one planning call per category batch on the happy path
* every SKU in exactly one batch; no SKU's lines split across batches; every line planned
* category factors are declared once and expanded into each line (step 9 accepts them)
* no categories → general batches, run succeeds with a warning
* both models down → reference DP with agent_fallback; run succeeds
* determinism still holds with batching
"""

from __future__ import annotations

import json
import socket
from typing import Any

import pytest

import core.llm as llm_mod
from core.llm import LLMClient
from tests.fakes import openrouter_server as fake
from worker.local import run_local


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def fake_url():
    port = _port()
    srv = fake.serve(port)
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_mod.time, "sleep", lambda _s: None)


def _llm(url: str, records: list[dict[str, Any]], fallback: str | None = "z-ai/glm-4.5-air:free") -> LLMClient:
    return LLMClient(api_key="k", model="nvidia/nemotron-3-super-120b-a12b:free", fallback_model=fallback,
                     base_url=url, recorder=records.append)


def _cfg(cfg, **extra):
    return cfg.with_overrides({"agent.enabled": True, "crosswalk.hard_fail_unmapped": False, **extra})


def test_batches_cover_every_sku_once(snapshot, cfg, fake_url):
    records: list[dict[str, Any]] = []
    run = run_local(snapshot, _cfg(cfg), "2026-11", llm=_llm(fake_url, records))
    a = run.ctx.artifacts
    batches, ev, ref = a["agent_batches"], a["evidence_pack"], a["reference"]
    seen: list[str] = [s for b in batches.itertuples() for s in json.loads(b.sku_codes)]
    assert sorted(seen) == sorted(ev["sku_code"]) and len(seen) == len(set(seen))
    # line_count is the sum of whole SKUs: no SKU's lines split across batches
    n_lines = dict(zip(ev["sku_code"], ev["n_lines"], strict=False))
    for b in batches.itertuples():
        assert b.line_count == sum(n_lines[s] for s in json.loads(b.sku_codes))
    assert all(k.startswith(("cat:", "uncat#")) for k in batches["batch_key"])

    planning = [r for r in records if r["role"] == "planning"]
    assert len(planning) == len(batches)  # happy path: one call per batch, no retries
    assert len(planning) <= 25
    assert {r["sku_code"] for r in planning} == set(batches["batch_key"])  # agent_call.sku_code = batch key

    dp = run.write("dp_output")
    assert set(dp["line_id"]) == set(ref["line_id"])  # every line gets a final_dp
    assert not dp["flags"].map(lambda f: "agent_fallback" in f).any()
    assert dp["batch_key"].notna().all()
    festive = dp[dp["plan_category"].isin(fake.FESTIVE)]
    if len(festive):
        applied = festive["applied_factors"].map(json.loads)
        assert applied.map(lambda fs: any(f["name"] == "agent:diwali" and f["value"] == 0.1 for f in fs)).all()


def test_progress_counts_batches(snapshot, cfg, fake_url):
    events: list[dict[str, Any]] = []
    run = run_local(snapshot, _cfg(cfg), "2026-11", llm=_llm(fake_url, []))
    run.ctx.progress = events.append  # progress is wired by the DB runner; check the label shape
    from worker.steps import all_steps

    step8 = next(s for s in all_steps() if s.no == 8)
    step8.fn(run.ctx.restricted(step8.reads))
    assert events and events[-1]["label"].endswith("batches")
    assert events[-1]["done"] == events[-1]["total"] == len(run.ctx.artifacts["agent_batches"])


def test_no_categories_uses_general_batches(snapshot, cfg, fake_url, monkeypatch):
    import core.domain.categories as cats

    monkeypatch.setattr(cats, "template_categories", lambda _p: {})
    tables = dict(snapshot.tables)
    md = tables["market_daily"].copy()
    md["category"] = None
    tables["market_daily"] = md
    sm = tables["sku_master"].copy()
    sm["plan_category"] = None
    tables["sku_master"] = sm
    snap = type(snapshot)(**{**snapshot.__dict__, "tables": tables})
    records: list[dict[str, Any]] = []
    run = run_local(snap, _cfg(cfg, **{"agent.uncategorised_batches": 3}), "2026-11", llm=_llm(fake_url, records))
    batches = run.ctx.artifacts["agent_batches"]
    assert all(k.startswith("uncat#") for k in batches["batch_key"])
    assert len(batches) == min(3, len(run.ctx.artifacts["evidence_pack"]))
    sizes = batches["line_count"].tolist()
    assert max(sizes) - min(sizes) <= max(run.ctx.artifacts["evidence_pack"]["n_lines"])
    assert any("have no category; planned in general batches" in w for w in run.log.warnings)
    dp = run.write("dp_output")
    assert len(dp) == len(run.ctx.artifacts["reference"])


def test_both_models_down_falls_back_to_reference(snapshot, cfg, fake_url):
    records: list[dict[str, Any]] = []
    fake._H.fail = True
    try:
        run = run_local(snapshot, _cfg(cfg), "2026-11", llm=_llm(fake_url, records))
    finally:
        fake._H.fail = False
    dp = run.write("dp_output")
    assert dp["flags"].map(lambda f: "agent_fallback" in f).all()
    assert (dp["final_dp"] == dp["reference_dp"]).all()
    models = {r["model"] for r in records if r["role"] == "planning"}
    assert models == {"nvidia/nemotron-3-super-120b-a12b:free", "z-ai/glm-4.5-air:free"}


def test_sku_mode_is_one_batch_per_sku(snapshot, cfg, fake_url):
    run = run_local(snapshot, _cfg(cfg, **{"agent.batch_by": "sku"}), "2026-11", llm=_llm(fake_url, []))
    b = run.ctx.artifacts["agent_batches"]
    assert len(b) == len(run.ctx.artifacts["evidence_pack"])
    assert all(len(json.loads(s)) == 1 for s in b["sku_codes"])


def test_category_split_keeps_skus_whole(snapshot, cfg, fake_url):
    run = run_local(snapshot, _cfg(cfg, **{"agent.max_lines_per_call": 5}), "2026-11", llm=_llm(fake_url, []))
    b = run.ctx.artifacts["agent_batches"]
    ev = run.ctx.artifacts["evidence_pack"].set_index("sku_code")
    for r in b.itertuples():
        skus = json.loads(r.sku_codes)
        assert r.line_count <= 5 or len(skus) == 1  # over the cap only when one SKU alone exceeds it
        assert r.line_count == int(ev.loc[skus, "n_lines"].sum())


def test_batching_is_deterministic(snapshot, cfg):
    c = cfg.with_overrides({"agent.enabled": False, "crosswalk.hard_fail_unmapped": False})
    a = run_local(snapshot, c, "2026-11").ctx.artifacts
    b = run_local(snapshot, c, "2026-11").ctx.artifacts
    assert a["agent_batches"].equals(b["agent_batches"])
    assert a["evidence_pack"]["pack_hash"].tolist() == b["evidence_pack"]["pack_hash"].tolist()


class _ScriptedLLM:
    """Answers like the fake server, but breaks the first SKU's lines on attempt 1."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def complete(self, **kw: Any) -> Any:
        if kw["role"] != "planning":  # step 11 overview
            return type("R", (), {"content": "Plan overview.", "parsed": None, "model": "scripted"})()
        user = kw["messages"][-1]["content"]
        batch = json.loads(user.split("Category batch:\n", 1)[1].split("\n\n", 1)[0])
        self.calls.append({"key": kw["sku_code"], "attempt": kw["attempt"],
                           "skus": [p["sku"] for p in batch["skus"]], "retry": "failed these checks" in user})
        ans = fake.batch_answer(batch)
        if kw["attempt"] == 1:
            first = batch["skus"][0]["sku"]
            for ln in ans["lines"]:
                if ln["line_id"].startswith(first + "|"):
                    ln["final_dp"] = ln["final_dp"] + 999  # arithmetic check fails
        return type("R", (), {"parsed": ans, "model": "scripted"})()


def test_retry_resends_only_failing_skus(snapshot, cfg):
    llm = _ScriptedLLM()
    run = run_local(snapshot, _cfg(cfg, **{"agent.max_lines_per_call": 1000, "agent.batch_by": "category"}),
                    "2026-11", llm=llm)
    first = [c for c in llm.calls if c["attempt"] == 1]
    retries = [c for c in llm.calls if c["attempt"] == 2]
    assert len(retries) == len(first)  # one retry per batch (each batch's first SKU was broken)
    for r in retries:
        orig = next(c for c in first if c["key"] == r["key"])
        assert r["skus"] == orig["skus"][:1] and r["retry"]
    dp = run.write("dp_output")
    assert not dp["flags"].map(lambda f: "agent_fallback" in f).any()
