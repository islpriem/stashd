"""One place where a failure becomes the error envelope.

Routes raise domain exceptions; nothing builds an envelope by hand.
"""

from typing import Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from stashd.domain.errors import ErrorCode, StashError
from stashd.schemas.errors import ErrorBody, ErrorEnvelope

_STATUS: dict[ErrorCode, int] = {
    ErrorCode.UNAUTHENTICATED: status.HTTP_401_UNAUTHORIZED,
    ErrorCode.FORBIDDEN: status.HTTP_403_FORBIDDEN,
    ErrorCode.PERMISSION_DENIED: status.HTTP_403_FORBIDDEN,
    ErrorCode.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    ErrorCode.PATH_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    ErrorCode.INVALID_REQUEST: status.HTTP_400_BAD_REQUEST,
    ErrorCode.INVALID_NAME: status.HTTP_400_BAD_REQUEST,
    ErrorCode.INVALID_PATH: status.HTTP_400_BAD_REQUEST,
    ErrorCode.NOT_A_SOURCE_STORAGE: status.HTTP_400_BAD_REQUEST,
    ErrorCode.NOT_A_CACHE_STORAGE: status.HTTP_400_BAD_REQUEST,
    ErrorCode.FLUSH_TARGET_REQUIRED: status.HTTP_400_BAD_REQUEST,
    ErrorCode.CONFLICT: status.HTTP_409_CONFLICT,
    ErrorCode.FILESET_EXISTS: status.HTTP_409_CONFLICT,
    ErrorCode.FILESET_NOT_READY: status.HTTP_409_CONFLICT,
    ErrorCode.SOURCE_MISMATCH: status.HTTP_409_CONFLICT,
    ErrorCode.ALLOCATION_LIMIT_EXCEEDED: status.HTTP_409_CONFLICT,
    ErrorCode.TOTAL_ALLOCATION_LIMIT_EXCEEDED: status.HTTP_409_CONFLICT,
    ErrorCode.STORAGE_FULL: status.HTTP_409_CONFLICT,
    ErrorCode.OVER_ALLOCATION: status.HTTP_409_CONFLICT,
    ErrorCode.TOO_MANY_FILESETS: status.HTTP_409_CONFLICT,
    ErrorCode.TOO_MANY_QUEUED: status.HTTP_409_CONFLICT,
    ErrorCode.STORAGE_DRAINED: status.HTTP_503_SERVICE_UNAVAILABLE,
    ErrorCode.DAEMON_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    ErrorCode.CONFIG_STALE: status.HTTP_503_SERVICE_UNAVAILABLE,
    ErrorCode.INTERNAL: status.HTTP_500_INTERNAL_SERVER_ERROR,
}

_BY_STATUS = {
    status.HTTP_401_UNAUTHORIZED: ErrorCode.UNAUTHENTICATED,
    status.HTTP_403_FORBIDDEN: ErrorCode.FORBIDDEN,
    status.HTTP_404_NOT_FOUND: ErrorCode.NOT_FOUND,
    status.HTTP_405_METHOD_NOT_ALLOWED: ErrorCode.NOT_FOUND,
    status.HTTP_409_CONFLICT: ErrorCode.CONFLICT,
}


def envelope(
    request: Request, code: ErrorCode, message: str, details: dict[str, Any], http_status: int
) -> JSONResponse:
    request_id = str(getattr(request.state, "request_id", ""))
    body = ErrorEnvelope(
        error=ErrorBody(code=code, message=message, details=details, request_id=request_id)
    )
    return JSONResponse(
        status_code=http_status,
        content=body.model_dump(mode="json"),
        headers={"X-Request-Id": request_id},
    )


def install_error_handlers(app: FastAPI) -> None:
    logger = structlog.get_logger()

    @app.exception_handler(StashError)
    async def _domain_error(request: Request, error: StashError) -> JSONResponse:
        http_status = _STATUS[error.code]
        return envelope(request, error.code, error.message, error.details, http_status)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        problems = [
            {"field": ".".join(str(part) for part in item["loc"]), "problem": item["msg"]}
            for item in error.errors()
        ]
        return envelope(
            request,
            ErrorCode.INVALID_REQUEST,
            "the request could not be understood",
            {"problems": problems},
            status.HTTP_400_BAD_REQUEST,
        )

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, error: HTTPException) -> JSONResponse:
        code = _BY_STATUS.get(error.status_code, ErrorCode.INTERNAL)
        return envelope(request, code, str(error.detail), {}, error.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, error: Exception) -> JSONResponse:
        # The detail goes to the log with the request id, never to the client.
        await logger.aexception(
            "request.failed", path=request.url.path, error=type(error).__name__
        )
        return envelope(
            request,
            ErrorCode.INTERNAL,
            "the request failed unexpectedly",
            {},
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
