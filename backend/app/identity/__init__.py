"""Actor identity and the authorization seam (no authentication yet)."""

from backend.app.identity.actor import (
    OPTIMIZER,
    SCORER,
    SYSTEM,
    Actor,
    ActorKind,
    ActorRole,
    IdentityAssurance,
    InvalidActorError,
    human_actor,
    system_actor,
    unidentified_actor,
)
from backend.app.identity.authorization import (
    AuthorizationDenied,
    AuthorizationPolicy,
    JobAction,
    UnenforcedPolicy,
)

__all__ = [
    "Actor",
    "ActorKind",
    "ActorRole",
    "AuthorizationDenied",
    "AuthorizationPolicy",
    "IdentityAssurance",
    "InvalidActorError",
    "JobAction",
    "OPTIMIZER",
    "SCORER",
    "SYSTEM",
    "UnenforcedPolicy",
    "human_actor",
    "system_actor",
    "unidentified_actor",
]
