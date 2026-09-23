"""Who performed an action (Sprint 3 Slice 1).

This is the domain-level actor seam that every state-changing job
service method receives, and that every job lifecycle event records.
It is deliberately NOT authentication: nothing here verifies an
identity. It gives a later authentication slice exactly one thing to
populate - an Actor - without the job service, the event model or the
history API changing shape.

THREE INDEPENDENT FACTS, NEVER INFERRED FROM ONE ANOTHER
    actor_id   - a stable identifier such as "WORKER-042" or
                 "SYSTEM:OPTIMIZER".
    role       - what the actor is acting as (ActorRole).
    assurance  - how much the system actually knows about that claim
                 (IdentityAssurance). This is what keeps the audit
                 trail honest today: an HTTP caller that sends
                 "X-Actor-Id: WORKER-042" is recorded as
                 DECLARED_UNVERIFIED, never as a verified worker.

    kind (HUMAN / SYSTEM) is DERIVED from role, not stored separately,
    so a record cannot claim role=SYSTEM while kind=HUMAN.

HUMAN VS SYSTEM
    SYSTEM actors are constructed only inside the application
    (system_actor below) and always carry SYSTEM_INTERNAL assurance.
    A human role can never carry SYSTEM_INTERNAL, and a human actor_id
    may not start with "SYSTEM", so an automated decision can never be
    attributed to a person and a person can never be recorded as the
    system. The HTTP layer additionally refuses a caller-declared
    SYSTEM role (see backend.app.api.deps).

AUTHENTICATED (Slice 10.2c)
    IdentityAssurance.AUTHENTICATED exists, but no ordinary construction
    path can produce it. A human Actor may carry it only if its
    constructor is handed the module-private capability below, which
    only backend.app.identity.authenticated_actor imports. That factory
    derives the Actor from a cryptographically verified assertion, an
    enrolled Person and a role that person explicitly holds. A caller
    can never supply the assurance: human_actor has no assurance
    parameter, and enum coercion, dataclasses.replace and deserialization
    (JobEvent.from_dict) all run through the same check and are refused.

    The capability protects against request-borne data and accidental
    code paths. It is not a defence against hostile code already running
    in this process (object.__new__, object.__setattr__, pickle).
"""

from __future__ import annotations

import re
from dataclasses import InitVar, dataclass
from enum import Enum
from typing import Dict


class ActorKind(str, Enum):
    """Whether an action was taken by a person or by the application."""

    HUMAN = "HUMAN"
    SYSTEM = "SYSTEM"


class ActorRole(str, Enum):
    """What an actor is acting as.

    WORKER, ENGINEER, AUTHORITY, ADMIN are the human operational roles
    the product distinguishes. SYSTEM is the application itself
    (scoring, optimization, automatic state processing).

    UNIDENTIFIED is not a permission role. It records the honest current
    reality that a caller supplied no identity at all - authentication
    does not exist yet. A future authorization policy is expected to
    deny it; today nothing is enforced (see
    backend.app.identity.authorization).
    """

    WORKER = "WORKER"
    ENGINEER = "ENGINEER"
    AUTHORITY = "AUTHORITY"
    ADMIN = "ADMIN"
    SYSTEM = "SYSTEM"
    UNIDENTIFIED = "UNIDENTIFIED"


HUMAN_ROLES = frozenset(
    {
        ActorRole.WORKER,
        ActorRole.ENGINEER,
        ActorRole.AUTHORITY,
        ActorRole.ADMIN,
    }
)


class IdentityAssurance(str, Enum):
    """How much the system actually knows about an actor's identity.

    SYSTEM_INTERNAL      - the application acting on its own behalf.
    DECLARED_UNVERIFIED  - the caller asserted an identity and role.
                           Nothing verified it.
    NONE                 - the caller asserted no identity at all.
    AUTHENTICATED        - a verified external identity, resolved through
                           the identity directory to an enrolled person
                           and a role that person holds. Constructible
                           only through the private capability in this
                           module (see Actor and authenticated_actor).
    """

    SYSTEM_INTERNAL = "SYSTEM_INTERNAL"
    DECLARED_UNVERIFIED = "DECLARED_UNVERIFIED"
    NONE = "NONE"
    AUTHENTICATED = "AUTHENTICATED"


class InvalidActorError(ValueError):
    """An actor could not be constructed from the values given."""


# Bounded, whitespace-free and punctuation-limited so an actor_id is
# safe to echo in an audit record, stable under canonical serialization
# and cannot smuggle separators or control characters into a future
# hash-chained record.
_ACTOR_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")

SYSTEM_ACTOR_ID_PREFIX = "SYSTEM"

# Module-private capability for AUTHENTICATED assurance. Deliberately not
# in __all__. Only backend.app.identity.authenticated_actor may import it
# (an AST test enforces that). It is passed through Actor's trailing
# InitVar, so it is never stored, never part of dataclasses.fields(),
# equality, hashing, repr or to_dict.
_AUTHENTICATION_CAPABILITY = object()

UNIDENTIFIED_ACTOR_ID = "UNIDENTIFIED"


@dataclass(frozen=True)
class Actor:
    """One actor, validated at construction.

    Prefer the constructors below (human_actor, system_actor,
    unidentified_actor) over calling this directly: they pick the only
    assurance value each role is allowed to carry.

    AUTHENTICATED assurance additionally requires the module-private
    capability as the trailing `_capability` InitVar (see
    _AUTHENTICATION_CAPABILITY). It is not a field: it is checked and
    discarded, so the three-field shape, equality, hashing, repr and
    to_dict are unchanged. dataclasses.replace re-runs this check with
    the default (None), so an AUTHENTICATED actor cannot be re-roled or
    re-identified, and nothing can be upgraded to AUTHENTICATED. A
    downgrade to DECLARED_UNVERIFIED through replace is allowed; it
    claims less and escalates nothing.
    """

    actor_id: str
    role: ActorRole
    assurance: IdentityAssurance
    _capability: InitVar[object] = None

    def __post_init__(self, _capability: object) -> None:
        try:
            role = ActorRole(self.role)
        except ValueError as exc:
            raise InvalidActorError(
                f"Unknown actor role {self.role!r}. Permitted values are "
                f"{[member.value for member in ActorRole]}."
            ) from exc

        try:
            assurance = IdentityAssurance(self.assurance)
        except ValueError as exc:
            raise InvalidActorError(
                f"Unknown identity assurance {self.assurance!r}. Permitted "
                f"values are {[m.value for m in IdentityAssurance]}."
            ) from exc

        object.__setattr__(self, "role", role)
        object.__setattr__(self, "assurance", assurance)

        if not isinstance(self.actor_id, str) or not _ACTOR_ID_PATTERN.match(
            self.actor_id
        ):
            raise InvalidActorError(
                f"Invalid actor_id {self.actor_id!r}: expected 1-64 "
                "characters of letters, digits, '.', '_', ':' or '-', "
                "starting with a letter or digit."
            )

        looks_like_system = self.actor_id.upper().startswith(
            SYSTEM_ACTOR_ID_PREFIX
        )

        if role is ActorRole.SYSTEM:
            if assurance is not IdentityAssurance.SYSTEM_INTERNAL:
                raise InvalidActorError(
                    "A SYSTEM actor must carry SYSTEM_INTERNAL assurance; "
                    f"got {assurance.value}."
                )
            if not looks_like_system:
                raise InvalidActorError(
                    "A SYSTEM actor_id must start with "
                    f"{SYSTEM_ACTOR_ID_PREFIX!r}; got {self.actor_id!r}."
                )
            return

        if looks_like_system:
            raise InvalidActorError(
                f"actor_id {self.actor_id!r} is reserved for SYSTEM actors "
                f"and cannot be used with role {role.value}."
            )

        if role is ActorRole.UNIDENTIFIED:
            if assurance is not IdentityAssurance.NONE:
                raise InvalidActorError(
                    "An UNIDENTIFIED actor must carry NONE assurance; "
                    f"got {assurance.value}."
                )
            return

        # A human operational role: DECLARED_UNVERIFIED, or AUTHENTICATED
        # with the private capability. SYSTEM and UNIDENTIFIED returned
        # above, so AUTHENTICATED can never reach them.
        if assurance is IdentityAssurance.AUTHENTICATED:
            if _capability is not _AUTHENTICATION_CAPABILITY:
                raise InvalidActorError(
                    "AUTHENTICATED assurance cannot be constructed directly; "
                    "it is produced only by the authenticated actor factory."
                )
            return

        if assurance is not IdentityAssurance.DECLARED_UNVERIFIED:
            raise InvalidActorError(
                f"A {role.value} actor must carry DECLARED_UNVERIFIED "
                f"assurance; got {assurance.value}."
            )

    @property
    def kind(self) -> ActorKind:
        """HUMAN or SYSTEM, derived from role and never stored apart from it."""

        if self.role is ActorRole.SYSTEM:
            return ActorKind.SYSTEM

        return ActorKind.HUMAN

    @property
    def is_system(self) -> bool:
        return self.kind is ActorKind.SYSTEM

    def to_dict(self) -> Dict[str, str]:
        return {
            "actor_id": self.actor_id,
            "role": self.role.value,
            "kind": self.kind.value,
            "assurance": self.assurance.value,
        }


def human_actor(actor_id: str, role: ActorRole | str) -> Actor:
    """A person acting in an operational role, identity not verified."""

    try:
        resolved = ActorRole(role)
    except ValueError as exc:
        raise InvalidActorError(
            f"Unknown actor role {role!r}. Permitted human roles are "
            f"{sorted(r.value for r in HUMAN_ROLES)}."
        ) from exc

    if resolved not in HUMAN_ROLES:
        raise InvalidActorError(
            f"Role {resolved.value} is not a human operational role. "
            f"Permitted human roles are {sorted(r.value for r in HUMAN_ROLES)}."
        )

    return Actor(
        actor_id=actor_id,
        role=resolved,
        assurance=IdentityAssurance.DECLARED_UNVERIFIED,
    )


def system_actor(component: str | None = None) -> Actor:
    """The application acting on its own behalf.

    component names the subsystem ("OPTIMIZER", "SCORER") so a history
    can tell an automated scoring decision from an automated scheduling
    one. The actor_id is "SYSTEM" or "SYSTEM:<COMPONENT>".
    """

    actor_id = (
        SYSTEM_ACTOR_ID_PREFIX
        if not component
        else f"{SYSTEM_ACTOR_ID_PREFIX}:{component}"
    )

    return Actor(
        actor_id=actor_id,
        role=ActorRole.SYSTEM,
        assurance=IdentityAssurance.SYSTEM_INTERNAL,
    )


def unidentified_actor() -> Actor:
    """A caller that supplied no identity.

    Used by in-process callers that pass no actor and by HTTP requests
    that send no identity headers. Recording this - rather than a
    made-up worker id - is what keeps an unauthenticated action from
    reading as if a known person performed it.
    """

    return Actor(
        actor_id=UNIDENTIFIED_ACTOR_ID,
        role=ActorRole.UNIDENTIFIED,
        assurance=IdentityAssurance.NONE,
    )


SYSTEM = system_actor()
SCORER = system_actor("SCORER")
OPTIMIZER = system_actor("OPTIMIZER")


__all__ = [
    "Actor",
    "ActorKind",
    "ActorRole",
    "HUMAN_ROLES",
    "IdentityAssurance",
    "InvalidActorError",
    "OPTIMIZER",
    "SCORER",
    "SYSTEM",
    "UNIDENTIFIED_ACTOR_ID",
    "human_actor",
    "system_actor",
    "unidentified_actor",
]
