"""Structured error envelope for the v1 API contract (Slice 7).

Every 4xx the v1 routes raise carries a stable machine-readable `code`
beside the human-readable `detail`:

    {"code": "STALE_PROPOSAL", "detail": "<prose>"}

A client switches on `code` and displays `detail`. The prose is NOT part
of the contract and may be reworded; the codes are. Statuses are exactly
the ones the routes already returned - this module adds a code, it does
not change a status.

Scope: the jobs lifecycle routes under /v1 and the actor-header
dependency they share. The legacy /optimize and /recover routes keep
FastAPI's default {"detail": ...} body.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse


V1_PREFIX = "/v1"


class ErrorCode(str, Enum):
    """Every `code` a v1 route can return. Additions are compatible;
    renames and removals require a new API version."""

    # 400 / 422 - the request itself
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INVALID_REQUEST = "INVALID_REQUEST"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    # An evidence item or an observation time (actual_start_at /
    # actual_end_at / captured_at) failed validation.
    EVIDENCE_INVALID = "EVIDENCE_INVALID"
    INVALID_CURSOR = "INVALID_CURSOR"
    ACTOR_HEADERS_INCOMPLETE = "ACTOR_HEADERS_INCOMPLETE"
    ACTOR_INVALID = "ACTOR_INVALID"
    # Slice 8 field intake. No asset lies close enough to the reported span
    # to attach the job to (or none is usable); the report is refused, not
    # attached to a distant asset.
    ASSET_ASSOCIATION_FAILED = "ASSET_ASSOCIATION_FAILED"
    # The field_location could not be converted: unknown/non-adjacent
    # station, negative or out-of-section offset, ambiguous section.
    FIELD_LOCATION_INVALID = "FIELD_LOCATION_INVALID"
    # field_location and distance_start/distance_end describe different
    # spans; neither is preferred.
    LOCATION_INPUT_CONFLICT = "LOCATION_INPUT_CONFLICT"

    # 403
    ACTOR_SYSTEM_ROLE_FORBIDDEN = "ACTOR_SYSTEM_ROLE_FORBIDDEN"
    AUTHORIZATION_DENIED = "AUTHORIZATION_DENIED"

    # 404
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    CORRIDOR_NOT_FOUND = "CORRIDOR_NOT_FOUND"

    # 409 - conflicts with the job's (or the data's) current state
    JOB_TERMINAL = "JOB_TERMINAL"
    JOB_COMMITTED = "JOB_COMMITTED"
    STALE_PROPOSAL = "STALE_PROPOSAL"
    STALE_EXECUTION = "STALE_EXECUTION"
    CONCURRENT_MODIFICATION = "CONCURRENT_MODIFICATION"
    NO_CURRENT_PROPOSAL = "NO_CURRENT_PROPOSAL"
    NO_ELIGIBLE_JOBS = "NO_ELIGIBLE_JOBS"
    POSSESSION_DATA_UNAVAILABLE = "POSSESSION_DATA_UNAVAILABLE"
    TIMETABLE_COVERAGE_GAP = "TIMETABLE_COVERAGE_GAP"
    COMMITTED_STATE_INCONSISTENT = "COMMITTED_STATE_INCONSISTENT"
    EXECUTION_HISTORY_INCONSISTENT = "EXECUTION_HISTORY_INCONSISTENT"
    # Slice 8. The idempotency_key was already used for a different
    # request (or its job is gone): the key is not reused.
    IDEMPOTENCY_KEY_CONFLICT = "IDEMPOTENCY_KEY_CONFLICT"
    # Slice 8. A stored asset_id no longer resolves to the asset it named
    # in the active asset set. Not reachable from a v1 route today (no
    # route re-trusts a stored asset_id); carried so any that does maps to
    # a stable code.
    ASSET_REFERENCE_INVALID = "ASSET_REFERENCE_INVALID"


class ApiError(HTTPException):
    """An HTTPException that carries a stable ErrorCode."""

    def __init__(self, status_code: int, code: ErrorCode, detail: str):
        super().__init__(status_code=status_code, detail=detail)
        self.code = ErrorCode(code)


def _api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code.value, "detail": exc.detail},
        headers=getattr(exc, "headers", None),
    )


async def _validation_error_handler(
    request: Request, exc: RequestValidationError
) -> Any:
    # Only the v1 contract gets the envelope; the legacy routes keep
    # FastAPI's default 422 body byte-for-byte.
    if request.url.path.startswith(V1_PREFIX + "/"):
        return JSONResponse(
            status_code=422,
            content={
                "code": ErrorCode.VALIDATION_ERROR.value,
                "detail": jsonable_encoder(exc.errors()),
            },
        )

    from fastapi.exception_handlers import request_validation_exception_handler

    return await request_validation_exception_handler(request, exc)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)


__all__ = [
    "V1_PREFIX",
    "ApiError",
    "ErrorCode",
    "install_error_handlers",
]
