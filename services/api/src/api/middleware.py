"""One log line per request, and a request id on everything it logs (D121).

A plain ASGI middleware rather than FastAPI's `@app.middleware("http")`:
that one buffers through Starlette's BaseHTTPMiddleware, which is awkward for
event streams (SSE). This only watches the messages go by.
"""

import logging
import re
import time
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.logs import log_context

logger = logging.getLogger("api.access")

HEADER = "x-request-id"
# A caller's own id is kept (to follow one request through the dashboard and
# the API) only if it's short and plain: it ends up in log lines.
_VALID_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


class RequestLog:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = _incoming_id(scope) or uuid4().hex
        token = log_context.set({"request_id": request_id})
        start = time.perf_counter()
        status = 500  # if the app fails before it answers

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = [
                    *message.get("headers", []),
                    (HEADER.encode(), request_id.encode()),
                ]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            path = scope["path"]
            # Health probes come every few seconds (compose, the demo's cron);
            # a passing one says nothing worth a line.
            if not (path == "/health" and status == 200):
                logger.info(
                    "%s %s %s",
                    scope["method"],
                    path,
                    status,
                    extra={
                        "method": scope["method"],
                        "path": path,
                        "status": status,
                        "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                    },
                )
            log_context.reset(token)


def _incoming_id(scope: Scope) -> str | None:
    for name, value in scope["headers"]:
        if name == HEADER.encode():
            candidate = value.decode("latin-1")
            return candidate if _VALID_ID.fullmatch(candidate) else None
    return None
