"""FastAPI dependencies used only when authentication is enforced.

The demo-mode seam (backend.app.api.deps.request_actor) is left exactly as it
was. In enforced mode main.py replaces it, through FastAPI's
dependency_overrides, with enforced_request_actor below, so every route that
asks for an Actor gets one derived from the server-side session and from
nothing the caller sent.
"""

from __future__ import annotations

from typing import Optional

from fastapi import Header, Request

from backend.app.api.errors import ApiError, ErrorCode
from backend.app.auth.router import api_error_for
from backend.app.auth.runtime import get_auth_service
from backend.app.auth.service import AuthError
from backend.app.identity.actor import Actor
from backend.app.identity.authorization import AuthorizationDenied, JobAction


def enforced_request_actor(
    request: Request,
    actor_id: Optional[str] = Header(default=None, alias="X-Actor-Id", include_in_schema=False),
    actor_role: Optional[str] = Header(default=None, alias="X-Actor-Role", include_in_schema=False),
) -> Actor:
    service = get_auth_service()
    if service is None:  # defensive: only installed when enforced
        raise ApiError(401, ErrorCode.UNAUTHENTICATED, "Authentication is not configured.")
    if actor_id is not None or actor_role is not None:
        # Refused, not ignored: a client still sending declared identities is misconfigured.
        raise ApiError(
            400,
            ErrorCode.ACTOR_HEADERS_NOT_ACCEPTED,
            "X-Actor-Id and X-Actor-Role are not accepted when authentication is enforced.",
        )
    try:
        return service.authenticate(request.cookies.get(service.settings.session_cookie)).actor
    except AuthError as exc:
        raise api_error_for(exc) from None


def require_solver_access(
    request: Request,
    actor_id: Optional[str] = Header(default=None, alias="X-Actor-Id", include_in_schema=False),
    actor_role: Optional[str] = Header(default=None, alias="X-Actor-Role", include_in_schema=False),
) -> None:
    """Gate for the stateless what-if solver routes (/optimize, /recover).

    No-op in demo mode. When enforced, the caller must hold a live session
    whose role (and NovaForge permission) allows requesting an optimization.
    """

    service = get_auth_service()
    if service is None:
        return
    actor = enforced_request_actor(request, actor_id, actor_role)
    try:
        service.require_action(actor, JobAction.REQUEST_OPTIMIZATION)
    except AuthorizationDenied as exc:
        raise ApiError(403, ErrorCode.AUTHORIZATION_DENIED, str(exc)) from None
