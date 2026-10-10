"""One OpenRouter client shared by the import, planning and Ask agents.

* model from LLM_MODEL, temperature from config, ``provider.data_collection = deny``
* JSON-schema structured output for the import and planning agents
* one transport retry; every call is handed to a recorder that writes ``meta.agent_call``
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


@dataclass
class LLMClient:
    api_key: str
    model: str
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
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "provider": {"data_collection": "deny"},
            "usage": {"include": True},
        }
        if max_tokens:
            body["max_tokens"] = max_tokens
        if json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": json_schema},
            }
        if tools:
            body["tools"] = tools
        started = time.monotonic()
        data: dict[str, Any] | None = None
        error: str | None = None
        for i in range(self.transport_retries + 1):
            try:
                resp = self._client().post(f"{self.base_url}/chat/completions", json=body)
                if resp.status_code >= 500 or resp.status_code == 429:
                    error = f"provider status {resp.status_code}"
                    time.sleep(1 + i)
                    continue
                if resp.status_code >= 400:
                    error = f"provider status {resp.status_code}: {resp.text[:200]}"
                    break
                data = resp.json()
                error = None
                break
            except (httpx.TransportError, ValueError) as exc:
                error = f"transport: {type(exc).__name__}"
                time.sleep(1 + i)
        latency_ms = int((time.monotonic() - started) * 1000)

        record: dict[str, Any] = {
            "role": role, "run_id": run_id, "session_id": session_id, "company_id": company_id,
            "sku_code": sku_code, "attempt": attempt, "prompt_version": prompt_version,
            "model": self.model, "request": body, "response": data, "latency_ms": latency_ms,
            "tokens_in": None, "tokens_out": None, "cost_usd": None, "valid": None,
            "failed_checks": [error] if error else None,
        }
        if data is None:
            self._record(record)
            raise LLMUnavailable(error or "no response")

        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        usage = data.get("usage") or {}
        content = msg.get("content")
        parsed: Any = None
        if json_schema is not None and content:
            try:
                parsed = json.loads(_strip_fences(content))
            except json.JSONDecodeError:
                parsed = None
        record.update(tokens_in=usage.get("prompt_tokens"), tokens_out=usage.get("completion_tokens"),
                      cost_usd=usage.get("cost"))
        self._record(record)
        return LLMResult(content=content, tool_calls=msg.get("tool_calls") or [], parsed=parsed,
                         tokens_in=usage.get("prompt_tokens") or 0,
                         tokens_out=usage.get("completion_tokens") or 0,
                         cost_usd=usage.get("cost"), latency_ms=latency_ms, raw=data)

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
