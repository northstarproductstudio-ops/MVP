"""Server-sent events helpers: id/type framing and 15 s keep-alive comments."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi.responses import StreamingResponse

KEEPALIVE_S = 15.0


def frame(ev: dict[str, Any]) -> str:
    return f"id: {ev.get('id', '')}\nevent: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"


async def with_keepalive(source: AsyncIterator[dict[str, Any]]) -> AsyncIterator[str]:
    it = source.__aiter__()
    pending: asyncio.Task | None = None
    while True:
        if pending is None:
            pending = asyncio.ensure_future(it.__anext__())
        done, _ = await asyncio.wait({pending}, timeout=KEEPALIVE_S)
        if not done:
            yield ": keep-alive\n\n"
            continue
        try:
            ev = pending.result()
        except StopAsyncIteration:
            return
        pending = None
        yield frame(ev)


def sse_response(source: AsyncIterator[dict[str, Any]]) -> StreamingResponse:
    return StreamingResponse(with_keepalive(source), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
