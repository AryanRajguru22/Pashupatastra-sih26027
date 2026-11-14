"""Request guards that exist only when authentication is enforced.

CSRF: the session cookie is SameSite=Lax, which already withholds it from
cross-site POSTs. This adds two independent layers for state-changing
requests: they must carry the custom header `X-PP-CSRF: 1` (which a
cross-origin page cannot send without a CORS preflight that this server only
grants to the console's own origin), and any `Origin` they carry must be the
console's origin.

Plus the standard hardening headers on every response.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

CSRF_HEADER = "x-pp-csrf"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class EnforcedModeMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, frontend_origin: str) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self._origin = frontend_origin

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if request.headers.get(CSRF_HEADER) != "1" or (origin is not None and origin != self._origin):
                return JSONResponse(
                    {"code": "CSRF_REJECTED", "detail": "Cross-site request refused."},
                    status_code=403,
                )
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        return response
