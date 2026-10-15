"""Log setup shared by the API, the workers and the CLIs (D121).

Two formats, chosen by LOG_FORMAT:
- `text` (the default): one readable line, for a terminal or `docker compose logs`;
- `json`: one JSON object per line, for a log service to search and filter
  (the hosted demo's API on Vercel uses it).

Every line logged while handling a request or running a job also carries that
request's or job's id (`log_context`), so its lines can be found together.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Literal

# Fields for every line logged in the current request or job, e.g.
# {"request_id": ...} or {"task_id": ..., "task": ...}. A ContextVar is per
# asyncio task and per thread, so concurrent requests never mix them up.
log_context: ContextVar[dict[str, str] | None] = ContextVar("log_context", default=None)

TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# The attributes every LogRecord has. Anything else on a record was passed as
# `extra=` (or by ContextFilter) and becomes a field of its JSON line.
_STANDARD = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class ContextFilter(logging.Filter):
    """Copies `log_context` onto each record."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in (log_context.get() or {}).items():
            setattr(record, key, value)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD and not key.startswith("_"):
                line[key] = value
        if record.exc_info:
            line["exc"] = self.formatException(record.exc_info)
        # default=str: an `extra` value that isn't JSON (a Path, a datetime)
        # must not lose the line.
        return json.dumps(line, default=str)


# The handler configure_logging installed last, so calling it again (each test
# builds an app) replaces it instead of adding a second one.
_installed: logging.Handler | None = None


def configure_logging(level: str, fmt: Literal["text", "json"]) -> None:
    """Send every log line to stdout in one format.

    Other handlers on the root logger are left alone (pytest's capture is
    one). Uvicorn configures its own loggers before it imports the app, with
    their own handlers and no propagation; they are routed through ours
    instead, so the server's lines look like everyone else's. Its access log
    is replaced by the API's own (api/middleware.py), which has the duration
    and the request id.
    """
    global _installed
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if fmt == "json" else logging.Formatter(TEXT_FORMAT))
    handler.addFilter(ContextFilter())
    root = logging.getLogger()
    if _installed is not None:
        root.removeHandler(_installed)
    root.addHandler(handler)
    root.setLevel(level)
    _installed = handler
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
