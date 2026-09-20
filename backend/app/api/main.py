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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.errors import install_error_handlers
from backend.app.api.routers import health, optimize, recover

from backend.app.jobs.router import (
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
app = FastAPI(title="Pashupatastra API")

install_error_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(optimize.router)
app.include_router(recover.router)
app.include_router(jobs_router)
