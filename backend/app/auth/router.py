"""HTTP surface of the NovaForge integration: /auth/*.

    GET  /auth/config         which mode the server runs in (always public)
    GET  /auth/login          start sign-in (redirects to NovaForge)
    GET  /auth/callback       finish sign-in (NovaForge redirects here)
    GET  /auth/me             who am I, which roles, which permissions
    POST /auth/session/role   choose which granted role to act in
    POST /auth/logout         end the session here and at NovaForge

In the web console these are reached through its same-origin proxy
(/api/pp/auth/...), so the cookies below belong to the console's own origin.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Cookie, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from backend.app.api.errors import ApiError, ErrorCode
from backend.app.auth.runtime import get_auth_service
from backend.app.auth.service import AuthError, AuthService

# Not part of the frozen v1 OpenAPI contract (docs/openapi-v1.json); documented in
# docs/NOVAFORGE_INTEGRATION.md instead.
router = APIRouter(prefix="/auth", tags=["auth"], include_in_schema=False)

_NO_STORE = {"Cache-Control": "no-store"}


def _service() -> AuthService:
    service = get_auth_service()
    if service is None:
        raise ApiError(404, ErrorCode.INVALID_REQUEST, "Authentication is not enabled on this server.")
    return service


def api_error_for(exc: AuthError) -> ApiError:
    if exc.status == 503:
        return ApiError(503, ErrorCode.AUTH_PROVIDER_UNAVAILABLE, exc.message)
    if exc.code == "role_selection_required":
        return ApiError(409, ErrorCode.ROLE_SELECTION_REQUIRED, exc.message)
    if exc.status == 401:
        return ApiError(401, ErrorCode.UNAUTHENTICATED, exc.message)
    if exc.status == 403:
        return ApiError(403, ErrorCode.AUTHORIZATION_DENIED, exc.message)
    return ApiError(exc.status, ErrorCode.INVALID_REQUEST, exc.message)


def _set_session_cookie(response: Response, service: AuthService, handle: str) -> None:
    s = service.settings
    response.set_cookie(
        s.session_cookie,
        handle,
        max_age=s.session_seconds,
        httponly=True,
        secure=s.cookie_secure,
        samesite="lax",
        path="/",
    )


def _clear_cookie(response: Response, service: AuthService, name: str) -> None:
    s = service.settings
    response.delete_cookie(name, path="/", secure=s.cookie_secure, httponly=True, samesite="lax")


@router.get("/config")
def auth_config() -> JSONResponse:
    service = get_auth_service()
    body = (
        {"mode": "demo"}
        if service is None
        else {"mode": "novaforge", "login_path": "/auth/login", "issuer": service.settings.issuer}
    )
    return JSONResponse(body, headers=_NO_STORE)


@router.get("/login")
def login(return_to: Optional[str] = Query(default=None, max_length=300)) -> RedirectResponse:
    service = _service()
    try:
        txn_id, url = service.begin_login(return_to)
    except AuthError as exc:
        raise api_error_for(exc) from None
    response = RedirectResponse(url, status_code=302, headers=_NO_STORE)
    response.set_cookie(
        service.settings.login_cookie,
        txn_id,
        max_age=600,
        httponly=True,
        secure=service.settings.cookie_secure,
        samesite="lax",  # the callback is a cross-site top-level navigation
        path="/",
    )
    return response


@router.get("/callback")
def callback(request: Request) -> RedirectResponse:
    service = _service()
    s = service.settings
    # Only these four, each a single plain string: nothing else is read.
    params = {
        k: v
        for k, v in request.query_params.items()
        if k in ("code", "state", "iss", "error") and len(v) <= 600
    }
    if len(request.query_params.multi_items()) != len(request.query_params.keys()):
        params = {}  # a parameter supplied twice is not a response we trust
    try:
        handle, path = service.complete_login(request.cookies.get(s.login_cookie), params)
    except AuthError as exc:
        response = RedirectResponse(f"{s.frontend_origin}/auth/error?code={exc.code}", status_code=302, headers=_NO_STORE)
        _clear_cookie(response, service, s.login_cookie)
        return response
    response = RedirectResponse(f"{s.frontend_origin}{path}", status_code=302, headers=_NO_STORE)
    _clear_cookie(response, service, s.login_cookie)
    # A fresh handle is minted on every successful sign-in: no session fixation.
    _set_session_cookie(response, service, handle)
    return response


@router.get("/me")
def me(request: Request) -> JSONResponse:
    service = _service()
    try:
        body = service.session_summary(request.cookies.get(service.settings.session_cookie))
    except AuthError as exc:
        raise api_error_for(exc) from None
    return JSONResponse(body, headers=_NO_STORE)


class RoleChoice(BaseModel):
    role: str = Field(min_length=1, max_length=32)


@router.post("/session/role")
def choose_role(body: RoleChoice, request: Request) -> JSONResponse:
    service = _service()
    try:
        handle = service.select_role(request.cookies.get(service.settings.session_cookie), body.role)
    except AuthError as exc:
        raise api_error_for(exc) from None
    response = JSONResponse(service.session_summary(handle), headers=_NO_STORE)
    _set_session_cookie(response, service, handle)  # the handle rotates with the role
    return response


@router.post("/logout")
def logout(request: Request) -> JSONResponse:
    service = _service()
    url = service.logout(request.cookies.get(service.settings.session_cookie))
    response = JSONResponse({"logout_url": url}, headers=_NO_STORE)
    _clear_cookie(response, service, service.settings.session_cookie)
    return response


__all__ = ["api_error_for", "router"]
