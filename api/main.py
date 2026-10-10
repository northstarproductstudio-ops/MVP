"""fmcg-api: FastAPI app. Stateless; queues runs and talks to the user, runs no steps."""

from __future__ import annotations

import tempfile
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.deps import pool, settings
from api.routers import crosswalk, imports, runs, sessions, system
from core.db import close_pools
from core.logging import configure_logging, get_logger, init_sentry

log = get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    s = settings()  # fails fast with the names of missing variables
    configure_logging("fmcg-api", s.app_env)
    init_sentry(s.sentry_dsn.get_secret_value() if s.sentry_dsn else None, "fmcg-api", s.app_env)
    s.tmp_dir.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(s.tmp_dir)  # spooled uploads go under TMP_DIR, never the app directory
    await pool().open()
    log.info("api ready")
    yield
    await close_pools()


app = FastAPI(title="Demand Planning MVP API", version="0.1.0", lifespan=lifespan)


def _cors() -> list[str]:
    try:
        return settings().cors_origin_list
    except Exception:  # noqa: BLE001 - settings errors surface in lifespan
        return []


app.add_middleware(CORSMiddleware, allow_origins=_cors(), allow_credentials=True,
                   allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                   allow_headers=["Authorization", "Content-Type", "Last-Event-ID"])


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse({"detail": "Something went wrong; it has been logged."}, status_code=500)


for r in (system.router, sessions.router, imports.router, crosswalk.router, runs.router):
    app.include_router(r)
