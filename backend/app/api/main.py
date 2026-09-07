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

from backend.app.api.routers import health, optimize, recover

app = FastAPI(title="Pashupatastra API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(optimize.router)
app.include_router(recover.router)
