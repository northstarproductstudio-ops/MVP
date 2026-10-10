"""Structured JSON logging: one object per line with ts, level, service, run_id, step,
company_id and msg. Never log prompts, Parquet contents or secrets."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

run_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("run_id", default=None)
step_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("step", default=None)
company_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("company_id", default=None)

_SERVICE = "fmcg"
_REDACT_KEYS = ("key", "secret", "token", "password", "authorization", "dsn")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "service": _SERVICE,
            "run_id": run_id_var.get(),
            "step": step_var.get(),
            "company_id": company_var.get(),
            "msg": record.getMessage(),
            "logger": record.name,
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            for k, v in extra.items():
                if any(r in k.lower() for r in _REDACT_KEYS):
                    v = "[redacted]"
                payload[k] = v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class PlainFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ctx = " ".join(f"{k}={v}" for k, v in (("run", run_id_var.get()), ("step", step_var.get())) if v)
        base = f"{record.levelname[0]} {record.name}: {record.getMessage()}"
        return f"{base} [{ctx}]" if ctx else base


def configure_logging(service: str, app_env: str = "production", level: int = logging.INFO) -> None:
    global _SERVICE
    _SERVICE = service
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(PlainFormatter() if app_env == "local" else JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log(logger: logging.Logger, level: int, msg: str, **fields: Any) -> None:
    logger.log(level, msg, extra={"fields": fields})


def init_sentry(dsn: str | None, service: str, env: str) -> None:
    """Initialise Sentry when a DSN is set; strip Authorization headers and env values."""
    if not dsn:
        return
    import sentry_sdk

    def before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
        req = event.get("request") or {}
        headers = req.get("headers") or {}
        for h in list(headers):
            if h.lower() in ("authorization", "x-job-token", "cookie"):
                headers[h] = "[redacted]"
        req.pop("env", None)
        event.setdefault("tags", {}).update(
            {"service": service, "run_id": run_id_var.get(), "step": step_var.get()}
        )
        return event

    sentry_sdk.init(dsn=dsn, environment=env, before_send=before_send, send_default_pii=False,
                    traces_sample_rate=0.0)
