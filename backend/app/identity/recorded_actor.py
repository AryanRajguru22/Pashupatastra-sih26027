"""A historical, record-only authenticated actor (Slice 10.2d.1).

    persisted event row --(rehydrate_actor)--> Actor | RecordedActor

An authenticated mutation is persisted with its actor's assurance, and
reading that history back must not fail. But a database row must never
become a way to AUTHENTICATE: anyone able to write a row could otherwise
claim to be an enrolled person. So a stored AUTHENTICATED actor is
rebuilt as a RecordedActor, a value that says "this event was recorded
under this identity" and nothing more.

RECORD, NOT CREDENTIAL
    RecordedActor is deliberately NOT an Actor and not a subclass of one.
    Every guard that demands an exact Actor (the authorization policies,
    the lifecycle accountability guard, event construction and
    append_events) therefore refuses it. It holds no capability, cannot be
    passed where a live caller is expected, and cannot be written back as
    a new event.

HISTORY IS NOT CURRENT AUTHORIZATION
    The stored role means "acted as this role, with this assurance, at
    that time". Rehydration never consults the identity directory: the
    person may since have been removed, lost the role or changed scope,
    and history must read the same regardless. Current authorization is
    decided from a live Actor and the current directory, never from here.

FORGED ROWS
    A row claiming AUTHENTICATED with a SYSTEM or UNIDENTIFIED role, a
    SYSTEM-prefixed id or a malformed id is refused (InvalidActorError),
    by the same rules a human Actor is held to. A well-formed forged row
    yields a record that grants nothing. That it was forged is a record
    integrity question for the future audit hash chain, not something
    rehydration can decide.

This module only READS the assurance value. It never mints an
authenticated Actor and never touches the private capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from backend.app.identity.actor import (
    Actor,
    ActorKind,
    ActorRole,
    IdentityAssurance,
    InvalidActorError,
    human_actor,
)


@dataclass(frozen=True)
class RecordedActor:
    """A stored authenticated identity, as history records it."""

    actor_id: str
    role: ActorRole
    assurance: IdentityAssurance

    def __post_init__(self) -> None:
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

        if assurance is not IdentityAssurance.AUTHENTICATED:
            raise InvalidActorError(
                "A RecordedActor records only an AUTHENTICATED identity; "
                f"got {assurance.value}."
            )

        # The identity rules (a human operational role, the actor_id
        # pattern, no SYSTEM prefix) are Actor's, not re-implemented here.
        # The probe is a DECLARED actor that is discarded: nothing
        # authenticated is built.
        human_actor(self.actor_id, role)

        object.__setattr__(self, "role", role)
        object.__setattr__(self, "assurance", assurance)

    @property
    def kind(self) -> ActorKind:
        """HUMAN or SYSTEM, derived from role, exactly as Actor derives it."""

        if self.role is ActorRole.SYSTEM:
            return ActorKind.SYSTEM

        return ActorKind.HUMAN

    @property
    def is_system(self) -> bool:
        return self.kind is ActorKind.SYSTEM

    def to_dict(self) -> Dict[str, str]:
        """The same shape as Actor.to_dict, so serialization round-trips."""

        return {
            "actor_id": self.actor_id,
            "role": self.role.value,
            "kind": self.kind.value,
            "assurance": self.assurance.value,
        }


def rehydrate_actor(
    actor_id: str,
    role: ActorRole | str,
    assurance: IdentityAssurance | str,
) -> Actor | RecordedActor:
    """The actor a persisted record describes. Never a live authenticated one.

    AUTHENTICATED becomes a RecordedActor. Every other assurance is built
    as an ordinary Actor with exactly the checks it has always had, and an
    unknown assurance fails as it always has.
    """

    try:
        resolved = IdentityAssurance(assurance)
    except ValueError as exc:
        raise InvalidActorError(
            f"Unknown identity assurance {assurance!r}. Permitted "
            f"values are {[m.value for m in IdentityAssurance]}."
        ) from exc

    if resolved is IdentityAssurance.AUTHENTICATED:
        return RecordedActor(actor_id, role, resolved)  # type: ignore[arg-type]

    return Actor(actor_id, role, resolved)  # type: ignore[arg-type]


__all__ = ["RecordedActor", "rehydrate_actor"]
