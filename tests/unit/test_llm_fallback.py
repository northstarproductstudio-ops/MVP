"""Gaps 1–2: primary → fallback model; rate limits are OpenRouter's, not ours."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

import core.llm as llm_mod
from core.llm import LLMClient, LLMUnavailable

PRIMARY = "nvidia/nemotron-3-super-120b-a12b:free"
FALLBACK = "z-ai/glm-4.5-air:free"


def _ok(model: str) -> httpx.Response:
    return httpx.Response(200, json={
        "model": model,
        "choices": [{"message": {"content": json.dumps({"ok": True})}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2},
    })


def _client(handler: Any, records: list[dict[str, Any]], fallback: str | None = FALLBACK) -> tuple[LLMClient, list[str]]:
    calls: list[str] = []

    def wrapped(req: httpx.Request) -> httpx.Response:
        model = json.loads(req.content)["model"]
        calls.append(model)
        return handler(model, len(calls))

    c = LLMClient(api_key="k", model=PRIMARY, fallback_model=fallback, recorder=records.append)
    c._http = httpx.Client(transport=httpx.MockTransport(wrapped))
    return c, calls


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_mod.time, "sleep", lambda _s: None)


def _call(c: LLMClient) -> Any:
    return c.complete(role="planning", messages=[{"role": "user", "content": "x"}],
                      json_schema={"type": "object"}, run_id="r", sku_code="cat:Home Baking#0")


def test_429_goes_straight_to_fallback() -> None:
    records: list[dict[str, Any]] = []
    c, calls = _client(lambda m, n: httpx.Response(429, json={"error": "rate"}) if m == PRIMARY else _ok(m), records)
    res = _call(c)
    assert res.parsed == {"ok": True}
    assert res.model == FALLBACK
    assert calls == [PRIMARY, FALLBACK]  # no same-model retry on 429
    assert [r["model"] for r in records] == [PRIMARY, FALLBACK]
    assert records[0]["response"] is None and records[0]["valid"] is False
    assert records[1]["model"] == FALLBACK and records[1]["response"] is not None


def test_5xx_retries_same_model_once_then_falls_back() -> None:
    records: list[dict[str, Any]] = []
    c, calls = _client(lambda m, n: httpx.Response(503) if m == PRIMARY else _ok(m), records)
    res = _call(c)
    assert res.model == FALLBACK
    assert calls == [PRIMARY, PRIMARY, FALLBACK]
    assert len(records) == 2  # one agent_call row per model attempt


@pytest.mark.parametrize("status,body", [
    (404, "No endpoints found matching your data policy"),
    (400, "nvidia/foo is not a valid model ID"),
])
def test_model_or_provider_errors_fall_through(status: int, body: str) -> None:
    records: list[dict[str, Any]] = []
    c, calls = _client(lambda m, n: httpx.Response(status, text=body) if m == PRIMARY else _ok(m), records)
    assert _call(c).model == FALLBACK
    assert calls == [PRIMARY, FALLBACK]


def test_bad_request_is_not_retried_on_fallback() -> None:
    records: list[dict[str, Any]] = []
    c, calls = _client(lambda m, n: httpx.Response(400, text="messages: field required"), records)
    with pytest.raises(LLMUnavailable):
        _call(c)
    assert calls == [PRIMARY]


def test_transport_error_falls_through() -> None:
    records: list[dict[str, Any]] = []

    def handler(m: str, n: int) -> httpx.Response:
        if m == PRIMARY:
            raise httpx.ConnectError("down")
        return _ok(m)

    c, calls = _client(handler, records)
    assert _call(c).model == FALLBACK
    assert calls == [PRIMARY, PRIMARY, FALLBACK]


def test_both_models_down_raises_unavailable() -> None:
    records: list[dict[str, Any]] = []
    c, calls = _client(lambda m, n: httpx.Response(429), records)
    with pytest.raises(LLMUnavailable):
        _call(c)
    assert calls == [PRIMARY, FALLBACK]
    assert [r["model"] for r in records] == [PRIMARY, FALLBACK]


def test_no_fallback_configured() -> None:
    records: list[dict[str, Any]] = []
    c, calls = _client(lambda m, n: httpx.Response(429), records, fallback=None)
    with pytest.raises(LLMUnavailable):
        _call(c)
    assert calls == [PRIMARY]


def test_data_collection_deny_is_always_sent() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        bodies.append(body)
        return httpx.Response(429) if body["model"] == PRIMARY else _ok(body["model"])

    c = LLMClient(api_key="k", model=PRIMARY, fallback_model=FALLBACK)
    c._http = httpx.Client(transport=httpx.MockTransport(handler))
    _call(c)
    assert all(b["provider"] == {"data_collection": "deny"} for b in bodies)


def test_no_rate_limiting_logic_in_code() -> None:
    import inspect

    src = inspect.getsource(llm_mod).lower()
    for word in ("daily_cap", "requests_per_minute", "ratelimiter", "tokenbucket"):
        assert word not in src
