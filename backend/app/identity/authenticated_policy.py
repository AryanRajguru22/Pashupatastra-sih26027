"""An enforcing policy that also requires an AUTHENTICATED identity (Slice 10.2d).

    Actor --(assurance floor)--> role/action --> railway scope --> permit

EnforcingPolicy decides what a given Actor may do; it has never asked
whether the Actor is who it claims to be, so with a directory installed a
DECLARED_UNVERIFIED actor holding an enrolled person_id in an X-Actor
header would receive that person's real scope. This subclass closes that
gap by refusing every Actor that is not exactly an authenticated one,
BEFORE any role or scope is examined.

THE FLOOR NEVER GRANTS
    It only removes. An actor that passes it is then judged by exactly the
    same role/action table and scope rules as before (ADMIN still holds no
    action). Nothing here mints, upgrades or infers an Actor: this module
    only READS the assurance value and never constructs an Actor.

BOTH ENTRY POINTS ENFORCE IT INDEPENDENTLY
    authorize() and authorize_resource() each run the floor first. The
    resource method does not rely on the parent reaching self.authorize.

ONE MESSAGE FOR EVERY DENIAL
    Every floor denial carries the same detail regardless of the actor's
    role, so a denial reveals nothing about which roles may do what.
"""

from __future__ import annotations

from typing import Iterable, Tuple

from backend.app.identity.actor import Actor, IdentityAssurance, unidentified_actor
from backend.app.identity.authorization import (
    AuthorizationDenied,
    JobAction,
    PolicyConfigurationError,
)
from backend.app.identity.directory import IdentityDirectory
from backend.app.identity.policy import EnforcingPolicy
from backend.app.identity.resource import ResourceLocation

_FLOOR_DETAIL = "an authenticated identity is required"


class AuthenticatedEnforcingPolicy(EnforcingPolicy):
    """EnforcingPolicy that denies every non-AUTHENTICATED actor first."""

    def __init__(
        self,
        directory: IdentityDirectory,
        *,
        topology: Iterable[Tuple[str, Iterable[str]]] = (),
    ) -> None:
        # Exact type: a StaticScopeDirectory, a subclass or a duck type
        # is refused. Its scopes are keyed by whatever id an actor
        # presents; only IdentityDirectory is keyed by an enrolled person.
        if type(directory) is not IdentityDirectory:
            raise PolicyConfigurationError(
                "AuthenticatedEnforcingPolicy requires exactly an "
                f"IdentityDirectory; got {type(directory).__name__}."
            )

        super().__init__(directory, topology=topology)

    @staticmethod
    def _require_authenticated(actor: object, action: JobAction) -> None:
        if (
            type(actor) is Actor
            and actor.assurance is IdentityAssurance.AUTHENTICATED
        ):
            return

        # A non-Actor (or subclass) cannot be formatted safely, so the
        # denial is attributed to the unidentified caller instead.
        denied_actor = actor if type(actor) is Actor else unidentified_actor()

        raise AuthorizationDenied(denied_actor, action, _FLOOR_DETAIL)

    def authorize(self, actor: Actor, action: JobAction) -> None:
        self._require_authenticated(actor, action)
        super().authorize(actor, action)

    def authorize_resource(
        self,
        actor: Actor,
        action: JobAction,
        location: ResourceLocation,
    ) -> None:
        self._require_authenticated(actor, action)
        super().authorize_resource(actor, action, location)


__all__ = ["AuthenticatedEnforcingPolicy"]
