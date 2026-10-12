"""One error shape for every API error (planning.md §7):

    {"error": {"code": "not_found", "message": "...", "details": {...}}}

`code` is stable and machine-readable; `message` is for humans.
"""

import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from api.queue import QueueUnavailable

logger = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        self.headers = headers


def not_found(what: str, id_: int) -> ApiError:
    return ApiError(404, "not_found", f"{what} {id_} not found")


def _body(code: str, message: str, details: dict[str, Any]) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            _body(exc.code, exc.message, exc.details), exc.status_code, headers=exc.headers
        )

    # Framework errors (unknown route, wrong method) get the same shape, with
    # the status's own name as the code: 404 -> "not_found".
    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
        code = HTTPStatus(exc.status_code).phrase.lower().replace(" ", "_")
        return JSONResponse(_body(code, str(exc.detail), {}), exc.status_code, headers=exc.headers)

    @app.exception_handler(QueueUnavailable)
    async def _queue_down(_: Request, exc: QueueUnavailable) -> JSONResponse:
        # The broker's message can name hosts; it goes to the log, not the client.
        logger.warning("job queue unavailable: %s", exc)
        return JSONResponse(
            _body("queue_unavailable", "the job queue is unavailable; try again", {}), 503
        )

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        # jsonable_encoder: pydantic's error entries can hold exception objects.
        details = {"errors": jsonable_encoder(exc.errors())}
        return JSONResponse(_body("invalid_request", "invalid request", details), 422)
