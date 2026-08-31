"""The error envelope. Details always carry integer bytes."""

from typing import Any

from stashd.domain.errors import ErrorCode
from stashd.schemas.base import Wire


class ErrorBody(Wire):
    code: ErrorCode
    message: str
    details: dict[str, Any]
    request_id: str


class ErrorEnvelope(Wire):
    error: ErrorBody
