"""Per-request correlation: one id in the log, the response header and the envelope."""

import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from stashd import __version__

Handler = Callable[[Request], Awaitable[Response]]


class RequestContextMiddleware(BaseHTTPMiddleware):
    api_version = "v1"

    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        request_id = uuid.uuid4().hex
        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id, path=request.url.path)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        response.headers["X-Request-Id"] = request_id
        # The client compares these on every response: it warns on a differing server
        # version and refuses a differing API version.
        response.headers["X-Stash-Api-Version"] = self.api_version
        response.headers["X-Stash-Server-Version"] = __version__
        return response
