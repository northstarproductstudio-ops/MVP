"""One OpenRouter client shared by the import, planning and Ask agents.

* primary model from LLM_MODEL, optional fallback from LLM_FALLBACK_MODEL (Gap 1)
* temperature from config, ``provider.data_collection = deny`` (never removed)
* JSON-schema structured output for the import and planning agents
* rate limits are OpenRouter's (Gap 2): a 429 is never retried on the same model, it goes
  straight to the fallback. Only transport errors and 5xx get one short same-model retry.
  There is no call counter, token bucket or daily-cap logic anywhere.
* one ``meta.agent_call`` row per model attempt, with the model actually called
* the API key never appears in the recorded request
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from core.logging import get_logger

log = get_logger(__name__)

Recorder = Callable[[dict[str, Any]], None]


class LLMError(RuntimeError):
    pass


class LLMUnavailable(LLMError):
    """Transport failure or provider outage after retries."""


@dataclass
class LLMResult:
    content: str | None
    tool_calls: list[dict[str, Any]]
    parsed: Any
    tokens_in: int
    tokens_out: int
    cost_usd: float | None
    latency_ms: int
    raw: dict[str, Any] = field(repr=False)
    model: str = ""


# A 400/404 whose body names the model or provider means "this model can't serve it":
# move to the fallback. Any other 400 is caused by the request itself and is not retried.
_MODEL_ERROR_MARKERS = ("no endpoints", "model not found", "not a valid model", "no allowed providers",
                        "provider", "model", "data policy")


def _is_model_error(status: int, text: str) -> bool:
    if status == 404:
        return True
    if status != 400:
        return False
    low = text.lower()
    return any(m in low for m in _MODEL_ERROR_MARKERS)


@dataclass
class LLMClient:
    api_key: str
    model: str
    fallback_model: str | None = None
    base_url: str = "https://openrouter.ai/api/v1"
    timeout_s: float = 60.0
    recorder: Recorder | None = None
    transport_retries: int = 1
    _http: httpx.Client | None = None

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=self.timeout_s, headers={
                "Authorization": f"Bearer {self.api_key}",
                "HTTP-Referer": "https://fmcg-demand-planning.app",
                "X-Title": "FMCG Demand Planning MVP",
            })
        return self._http

    def complete(
        self,
        *,
        role: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.0,
        json_schema: dict[str, Any] | None = None,
        schema_name: str = "output",
        tools: list[dict[str, Any]] | None = None,
        prompt_version: str = "v1",
        run_id: str | None = None,
        session_id: str | None = None,
        company_id: str | None = None,
        sku_code: str | None = None,
        attempt: int = 1,
        max_tokens: int | None = None,
    ) -> LLMResult:
        base: dict[str, Any] = {
            "messages": messages,
            "temperature": temperature,
            "provider": {"data_collection": "deny"},
            "usage": {"include": True},
        }
        if max_tokens:
            base["max_tokens"] = max_tokens
        if json_schema is not None:
            base["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": json_schema},
            }
        if tools:
            base["tools"] = tools
        meta = {"role": role, "run_id": run_id, "session_id": session_id, "company_id": company_id,
                "sku_code": sku_code, "attempt": attempt, "prompt_version": prompt_version}

        last_error = "no model configured"
        for model in self.models():
            outcome, payload = self._try_model(model, base, meta, json_schema is not None)
            if outcome == "ok":
                return payload  # type: ignore[return-value]
            last_error = payload  # type: ignore[assignment]
            if outcome == "stop":  # the request itself is bad; another model won't help
                break
            log.warning("llm model failed, trying fallback", extra={"model": model, "error": last_error})
        raise LLMUnavailable(last_error)

    def models(self) -> list[str]:
        out = [self.model]
        if self.fallback_model and self.fallback_model != self.model:
            out.append(self.fallback_model)
        return out

    def _try_model(self, model: str, base: dict[str, Any], meta: dict[str, Any],
                   want_json: bool) -> tuple[str, Any]:
        """One model attempt (with at most one same-model retry for transport/5xx).

        Returns ("ok", LLMResult), ("next", error) to fall through, or ("stop", error).
        Exactly one agent_call row is recorded per call of this method.
        """
        body = {"model": model, **base}
        started = time.monotonic()
        data: dict[str, Any] | None = None
        error: str | None = None
        outcome = "next"
        for i in range(self.transport_retries + 1):
            last_try = i == self.transport_retries
            try:
                resp = self._client().post(f"{self.base_url}/chat/completions", json=body)
            except httpx.TransportError as exc:
                error = f"transport: {type(exc).__name__}"
                if not last_try:
                    time.sleep(1 + i)
                continue
            if resp.status_code == 429:
                error = "provider status 429 (rate limited)"
                break  # Gap 2: never retry 429 on the same model
            if resp.status_code >= 500:
                error = f"provider status {resp.status_code}"
                if not last_try:
                    time.sleep(1 + i)
                continue
            if resp.status_code >= 400:
                error = f"provider status {resp.status_code}: {resp.text[:200]}"
                if not _is_model_error(resp.status_code, resp.text):
                    outcome = "stop"
                break
            try:
                data = resp.json()
            except ValueError:
                error = "transport: invalid JSON body"
                if not last_try:
                    time.sleep(1 + i)
                continue
            if isinstance(data, dict) and data.get("error") and not data.get("choices"):
                # OpenRouter can return 200 with an error object (provider failure)
                err = data.get("error") or {}
                error = f"provider error: {str(err.get('message') if isinstance(err, dict) else err)[:200]}"
                data = None
                break
            error = None
            break
        latency_ms = int((time.monotonic() - started) * 1000)

        record: dict[str, Any] = {
            **meta, "model": model, "request": body, "response": data, "latency_ms": latency_ms,
            "tokens_in": None, "tokens_out": None, "cost_usd": None, "valid": None,
            "failed_checks": [error] if error else None,
        }
        if data is None:
            record["valid"] = False
            self._record(record)
            return outcome, error or "no response"

        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        usage = data.get("usage") or {}
        content = msg.get("content")
        parsed: Any = None
        if want_json and content:
            try:
                parsed = json.loads(_strip_fences(content))
            except json.JSONDecodeError:
                parsed = None
        record.update(tokens_in=usage.get("prompt_tokens"), tokens_out=usage.get("completion_tokens"),
                      cost_usd=usage.get("cost"))
        self._record(record)
        return "ok", LLMResult(content=content, tool_calls=msg.get("tool_calls") or [], parsed=parsed,
                               tokens_in=usage.get("prompt_tokens") or 0,
                               tokens_out=usage.get("completion_tokens") or 0,
                               cost_usd=usage.get("cost"), latency_ms=latency_ms, raw=data, model=model)

    def _record(self, record: dict[str, Any]) -> None:
        if self.recorder is None:
            return
        try:
            self.recorder(record)
        except Exception:  # noqa: BLE001 - recording must not break the call path
            log.exception("failed to record agent_call")


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t[: -3]
    return t.strip()


def load_prompt(agent: str, version: str, name: str = "system.md") -> str:
    from core.config import REPO_ROOT

    path = REPO_ROOT / "prompts" / agent / version / name
    return path.read_text(encoding="utf-8")
