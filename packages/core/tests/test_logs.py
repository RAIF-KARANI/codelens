import json
import logging
import sys
from pathlib import PurePosixPath

import pytest

from core import logs
from core.logs import JsonFormatter, configure_logging, log_context


def record(message: str = "hello %s", *args: object, **extra: object) -> logging.LogRecord:
    rec = logging.makeLogRecord(
        {"name": "core.test", "levelname": "INFO", "msg": message, "args": args or ("world",)}
    )
    for key, value in extra.items():
        setattr(rec, key, value)
    return rec


def test_a_json_line_has_the_basics_and_every_extra() -> None:
    line = json.loads(JsonFormatter().format(record(status=200, path="/x")))

    assert line["level"] == "INFO"
    assert line["logger"] == "core.test"
    assert line["message"] == "hello world"
    assert line["ts"].endswith("+00:00")
    assert (line["status"], line["path"]) == (200, "/x")
    # The record's own bookkeeping stays out.
    assert "args" not in line and "msecs" not in line


def test_values_that_are_not_json_dont_lose_the_line() -> None:
    line = json.loads(JsonFormatter().format(record(where=PurePosixPath("data/repos"))))
    assert line["where"] == "data/repos"


def test_an_exception_is_included() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        rec = record("failed for %s")
        rec.exc_info = sys.exc_info()
    line = json.loads(JsonFormatter().format(rec))
    assert "ValueError: boom" in line["exc"]


def test_the_context_reaches_every_line(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", "json")
    token = log_context.set({"request_id": "abc123"})
    try:
        logging.getLogger("core.test").info("inside")
    finally:
        log_context.reset(token)
    logging.getLogger("core.test").info("outside")

    inside, outside = (json.loads(x) for x in capsys.readouterr().out.strip().splitlines()[-2:])
    assert inside["request_id"] == "abc123"
    assert "request_id" not in outside


def test_configuring_twice_keeps_one_handler(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", "text")
    configure_logging("INFO", "text")
    logging.getLogger("core.test").info("once")
    assert capsys.readouterr().out.count("once") == 1
    assert sum(h is logs._installed for h in logging.getLogger().handlers) == 1


def test_uvicorn_lines_come_through_ours() -> None:
    uvicorn = logging.getLogger("uvicorn.error")
    uvicorn.addHandler(logging.NullHandler())
    uvicorn.propagate = False

    configure_logging("INFO", "json")

    assert uvicorn.handlers == [] and uvicorn.propagate
    # Replaced by the API's own access line (duration, request id).
    assert logging.getLogger("uvicorn.access").disabled
