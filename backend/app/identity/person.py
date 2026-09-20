"""Person and external identity (Slice 10.1A).

A Person is the stable, role-neutral Pashupatastra-side anchor for one
human. It is NOT an Actor, NOT a railway role, NOT an authenticated
request and NOT a copy of an identity provider's user record.

    ExternalIdentity   (identity_provider, subject)   case-sensitive key
            |  1:1
            v
    Person             person_id                      opaque, role-neutral

Roles, railway scope and authenticated Actor construction are later
slices. Nothing here verifies an identity, stores a credential or
profile field, or touches persistence.

person_id is supplied by the caller (the later directory allocates it);
it is never derived from the external identity, a name, an email or a
role. It must be a legal actor_id so an authenticated Actor can carry it
unchanged, and it is validated through the existing actor rule rather
than a competing copy of it.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.identity.actor import ActorRole, InvalidActorError, human_actor

MAX_IDENTITY_PROVIDER_LENGTH = 64
MAX_SUBJECT_LENGTH = 256


class PersonError(ValueError):
    """A person or external identity could not be constructed."""


def _validate_identity_part(label: str, value: object, max_length: int) -> str:
    """Return value unchanged, or raise. Never strips or re-cases it."""

    if not isinstance(value, str):
        raise PersonError(f"{label} must be a string; got {type(value).__name__}.")

    if not value.strip():
        raise PersonError(f"{label} must not be empty or whitespace only.")

    if value != value.strip():
        raise PersonError(
            f"{label} {value!r} has leading or trailing whitespace; it is "
            "rejected rather than silently changed."
        )

    if any(not ch.isprintable() for ch in value):
        raise PersonError(f"{label} {value!r} contains control characters.")

    if len(value) > max_length:
        raise PersonError(
            f"{label} is {len(value)} characters; the maximum is {max_length}."
        )

    return value


def _validate_person_id(person_id: object) -> str:
    """Apply the existing actor_id rule (charset, length, SYSTEM* reserved).

    human_actor is used only as a validator; the Actor it builds is
    discarded and never returned or stored.
    """

    try:
        human_actor(person_id, ActorRole.WORKER)  # type: ignore[arg-type]
    except InvalidActorError as exc:
        raise PersonError(f"Invalid person_id: {exc}") from exc

    return person_id  # type: ignore[return-value]


@dataclass(frozen=True)
class ExternalIdentity:
    """An identity at an external provider, keyed by (provider, subject).

    identity_provider is a logical alias such as "novaforge", not a URL
    and not a role. subject is the provider's stable user identifier.
    Both are case-sensitive and kept exactly as given.
    """

    identity_provider: str
    subject: str

    def __post_init__(self) -> None:
        _validate_identity_part(
            "identity_provider", self.identity_provider, MAX_IDENTITY_PROVIDER_LENGTH
        )
        _validate_identity_part("subject", self.subject, MAX_SUBJECT_LENGTH)


@dataclass(frozen=True)
class Person:
    """A role-neutral person: an opaque person_id and its external identity."""

    person_id: str
    identity: ExternalIdentity

    def __post_init__(self) -> None:
        _validate_person_id(self.person_id)

        if not isinstance(self.identity, ExternalIdentity):
            raise PersonError(
                "identity must be an ExternalIdentity; got "
                f"{type(self.identity).__name__}."
            )


__all__ = [
    "ExternalIdentity",
    "MAX_IDENTITY_PROVIDER_LENGTH",
    "MAX_SUBJECT_LENGTH",
    "Person",
    "PersonError",
]
