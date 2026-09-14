"""Small shared plumbing helpers for the API layer.

Not domain modeling - the fixture-loading pattern already used by
scripts/run_milestone1.py and backend/tests/test_api.py, and the request
actor dependency, each given one canonical home so future routers don't
reinvent them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import Header, HTTPException

from backend.app.identity.actor import (
    Actor,
    ActorRole,
    InvalidActorError,
    human_actor,
    unidentified_actor,
)
from contracts import OptimizationRequest


ACTOR_ID_HEADER = "X-Actor-Id"
ACTOR_ROLE_HEADER = "X-Actor-Role"


def request_actor(
    actor_id: Optional[str] = Header(default=None, alias=ACTOR_ID_HEADER),
    actor_role: Optional[str] = Header(default=None, alias=ACTOR_ROLE_HEADER),
) -> Actor:
    """The actor a request is recorded under. NOT authentication.

    This is the seam a later authentication slice replaces: it will
    derive the Actor from a verified credential instead of from these
    headers. Until then the identity is only DECLARED by the caller and
    is recorded with DECLARED_UNVERIFIED assurance, so no history entry
    can read as a verified person.

      - both headers absent  -> the UNIDENTIFIED actor (assurance NONE)
      - both present         -> a human actor for WORKER / ENGINEER /
                                AUTHORITY / ADMIN
      - only one present     -> 400
      - role SYSTEM          -> 403: an external caller can never act as
                                the system, so a human request cannot be
                                recorded as an automated decision
      - role UNIDENTIFIED    -> 400: send no headers instead
      - malformed id / role  -> 400
    """

    if actor_id is None and actor_role is None:
        return unidentified_actor()

    if actor_id is None or actor_role is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{ACTOR_ID_HEADER} and {ACTOR_ROLE_HEADER} must be sent "
                "together, or not at all."
            ),
        )

    role = actor_role.strip().upper()

    if role == ActorRole.SYSTEM.value:
        raise HTTPException(
            status_code=403,
            detail="External callers cannot act as the SYSTEM actor.",
        )

    try:
        return human_actor(actor_id.strip(), role)
    except InvalidActorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def load_optimization_request(path: Path) -> OptimizationRequest:
    # `from contracts import OptimizationRequest` resolves to the
    # canonical dataclass in contracts/schemas.py, which deserializes
    # with from_dict(). This previously called Pydantic's
    # model_validate() and raised AttributeError on every call - the
    # helper simply had no callers, so nobody noticed. It is the
    # clearest evidence of why contracts/schemas.py must stay the one
    # canonical contract path (see the banner in contracts/common.py).
    raw = json.loads(path.read_text(encoding="utf-8"))
    return OptimizationRequest.from_dict(raw)
