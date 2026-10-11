"""Wake the worker after queueing a job.

AppSail scales an instance down after a few idle minutes and starts one on the next
request. The worker does its work in a background loop, so the API sends a cheap GET to
the worker's /health (WORKER_URL) whenever it queues a job. Fire-and-forget; the queue row
is already committed, and the worker's own sweep picks the job up once it runs."""

from __future__ import annotations

import asyncio
import os

import httpx

from core.logging import get_logger

log = get_logger(__name__)
_tasks: set[asyncio.Task] = set()


async def _ping(url: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.get(url.rstrip("/") + "/health")
    except httpx.HTTPError:
        log.warning("could not wake the worker at WORKER_URL")


def wake_worker() -> None:
    url = os.environ.get("WORKER_URL")
    if not url:
        return
    t = asyncio.get_event_loop().create_task(_ping(url))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
