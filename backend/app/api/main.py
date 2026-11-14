"""FastAPI application entrypoint.

Builds the app, wires up CORS, and registers routers. Route handlers
themselves live in backend/app/api/routers/ - this file should stay
small; adding a new endpoint means adding a router module and one
include_router() call here, not editing existing handlers. See
docs/integration.md for the full pattern.

Run the dev server from the repo root:
    python -m uvicorn backend.app.api.main:app --reload
"""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.deps import request_actor
from backend.app.auth import runtime as auth_runtime
from backend.app.auth.guards import enforced_request_actor, require_solver_access
from backend.app.auth.middleware import EnforcedModeMiddleware
from backend.app.auth.router import router as auth_router

from backend.app.api.errors import install_error_handlers
from backend.app.api.routers import health, optimize, recover

from backend.app.jobs.router import (
    _composition as jobs_composition,
    router as jobs_router,
)

# API CONTRACT (Slice 7)
#   /v1/...            the frozen frontend-facing jobs-lifecycle contract
#                      (backend.app.jobs.router). Structured {code, detail}
#                      errors; see backend.app.api.errors.
#   /optimize,/recover LEGACY Milestone-1 demo routes. Kept exactly as
#                      they were (unversioned, default error body) because
#                      the existing dashboard calls them. NOT part of the
#                      v1 contract; do not build lifecycle features on them.
#   /health            unversioned liveness check.
def build_app(auth: object = None) -> FastAPI:
    """The application for one authentication mode.

    auth is None for DEMO mode (declared X-Actor-* identities, open CORS,
    exactly as before) or the AuthService for ENFORCED mode. Production builds
    the module-level `app` below from the composition root; tests build
    enforced apps directly.
    """

    application = FastAPI(title="Pashupatastra API")
    install_error_handlers(application)
    # Authentication mode is decided once, by the composition root
    # (backend.app.api.composition). None means demo mode, unchanged.
    auth_runtime.set_auth_service(auth)  # type: ignore[arg-type]

    if auth is None:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
    else:
        # ENFORCED mode: identity only from the server-side session; CORS only
        # for the console's own origin, with credentials; CSRF + hardening headers.
        frontend = auth.settings.frontend_origin  # type: ignore[attr-defined]
        application.add_middleware(EnforcedModeMiddleware, frontend_origin=frontend)
        application.add_middleware(
            CORSMiddleware,
            allow_origins=[frontend],
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Content-Type", "X-PP-CSRF", "Idempotency-Key"],
        )
        application.dependency_overrides[request_actor] = enforced_request_actor

    application.include_router(health.router)
    application.include_router(auth_router)
    application.include_router(optimize.router, dependencies=[Depends(require_solver_access)])
    application.include_router(recover.router, dependencies=[Depends(require_solver_access)])
    application.include_router(jobs_router)
    return application


app = build_app(jobs_composition.auth)
