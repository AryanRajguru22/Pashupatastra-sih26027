"""The only trusted path to an AUTHENTICATED Actor (Slice 10.2c).

    VerifiedIdentityAssertion
        -> verified.identity                       (an ExternalIdentity)
        -> IdentityDirectory.person_for()          (an ENROLLED Person)
        -> IdentityDirectory.select_role()         (a role the person HOLDS)
        -> Actor(person_id, role, AUTHENTICATED, private capability)

Nothing in that chain is caller-supplied. The factory accepts a verified
assertion (a type only the 10.2a verifier can construct), the identity
directory and, optionally, the role to act in. It never accepts a Person,
an ExternalIdentity, a person_id, an Actor or an assurance value, so a
caller cannot manufacture AUTHENTICATED by supplying any of them.

WHAT IS TRUSTED FROM THE ASSERTION
    Only `verified.identity`. Issuer, audience, session id, assertion id,
    auth_time and expiry are verification and session facts; they carry
    no railway authority and are not read here. NovaForge roles, trust
    levels, scopes and actor ids never reach a VerifiedIdentityAssertion
    at all, and could not grant anything if they did: the role comes from
    the directory's RoleAssignments, the actor_id from the enrolled
    Person, and railway scope stays with the directory.

ROLE SELECTION IS NOT ROLE GRANTING
    requested_role only picks among roles the directory already holds for
    this person, through the existing select_role: one role is selected,
    several without a request is ROLE_SELECTION_REQUIRED, an unheld role
    is ROLE_NOT_ASSIGNED, none is NO_ASSIGNMENT, and SYSTEM,
    UNIDENTIFIED or garbage is INVALID_ROLE. It is not case-normalised.

EXACT TYPES
    A subclass could override the behaviour this depends on (a forged
    VerifiedIdentityAssertion that skips the verifier's construction
    token, or a directory whose person_for answers whatever it likes),
    and a duck-typed directory such as StaticScopeDirectory has no
    role-held invariant. Both are therefore checked with `type(x) is`,
    not isinstance.

NOT IN THIS SLICE
    No expiry, replay or single-use check (the verifier already spent the
    jti; freshness and session creation are 10.2e), no session, no
    request_actor change, no policy wiring. This module is not exported
    from backend.app.identity.

The capability guards against request-borne data and accidental code
paths, not against hostile code running in this process.
"""

from __future__ import annotations

from typing import Optional

from backend.app.identity.actor import (
    _AUTHENTICATION_CAPABILITY,
    Actor,
    ActorRole,
    IdentityAssurance,
)
from backend.app.identity.directory import IdentityDirectory
from backend.app.identity.identity_assertion import VerifiedIdentityAssertion

UNTRUSTED_ASSERTION = "UNTRUSTED_ASSERTION"
UNTRUSTED_DIRECTORY = "UNTRUSTED_DIRECTORY"


class AuthenticationContextError(ValueError):
    """The factory was given something other than trusted inputs."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(f"{reason}: {message}")
        self.reason = reason


def authenticated_actor(
    verified: VerifiedIdentityAssertion,
    directory: IdentityDirectory,
    requested_role: Optional[ActorRole | str] = None,
) -> Actor:
    """The AUTHENTICATED Actor for a verified identity, or raise."""

    if type(verified) is not VerifiedIdentityAssertion:
        raise AuthenticationContextError(
            UNTRUSTED_ASSERTION,
            "Expected a VerifiedIdentityAssertion produced by the verifier; "
            f"got {type(verified).__name__}.",
        )

    if type(directory) is not IdentityDirectory:
        raise AuthenticationContextError(
            UNTRUSTED_DIRECTORY,
            f"Expected an IdentityDirectory; got {type(directory).__name__}.",
        )

    person = directory.person_for(verified.identity)
    assignment = directory.select_role(person, requested_role)

    return Actor(
        person.person_id,
        assignment.role,
        IdentityAssurance.AUTHENTICATED,
        _AUTHENTICATION_CAPABILITY,
    )


__all__ = [
    "AuthenticationContextError",
    "UNTRUSTED_ASSERTION",
    "UNTRUSTED_DIRECTORY",
    "authenticated_actor",
]
