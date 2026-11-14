"""The enforcing policy for the NovaForge-backed deployment.

    NovaForgePolicy = AuthenticatedEnforcingPolicy  (unchanged rules)
                    + the NovaForge permission for the action

Every decision is still made by the existing machinery: authentication floor,
role/action table, railway scope. This subclass only ADDS a refusal: the actor
must also hold the NovaForge permission naming the action (job.<action>)
under the role it is acting in. It can therefore only ever narrow what the
existing policy would allow, so an administrator removing a permission in
NovaForge takes effect on the next liveness check, never widens anything,
and a missing or unknown permission set denies (fail closed).
"""

from __future__ import annotations

from typing import Iterable, Tuple

from backend.app.auth.live_directory import NovaForgeDirectory, permission_key
from backend.app.identity.actor import Actor
from backend.app.identity.authenticated_policy import AuthenticatedEnforcingPolicy
from backend.app.identity.authorization import AuthorizationDenied, JobAction
from backend.app.identity.directory import IdentityDirectory
from backend.app.identity.resource import ResourceLocation

_DENIAL = "the identity provider has not granted this permission"


class NovaForgePolicy(AuthenticatedEnforcingPolicy):
    def __init__(
        self,
        live: NovaForgeDirectory,
        *,
        topology: Iterable[Tuple[str, Iterable[str]]] = (),
    ) -> None:
        # The parent insists on exactly an IdentityDirectory at construction;
        # an empty one satisfies it, and is immediately replaced by a view
        # that always consults the live snapshot.
        super().__init__(IdentityDirectory(), topology=topology)
        self._directory = live.scope_view()
        self._live = live

    def _require_permission(self, actor: Actor, action: JobAction) -> None:
        granted = self._live.permissions_for(actor.actor_id, actor.role)
        if granted is None or permission_key(action) not in granted:
            raise AuthorizationDenied(actor, action, _DENIAL)

    def authorize(self, actor: Actor, action: JobAction) -> None:
        super().authorize(actor, action)
        self._require_permission(actor, action)

    def authorize_resource(
        self,
        actor: Actor,
        action: JobAction,
        location: ResourceLocation,
    ) -> None:
        super().authorize_resource(actor, action, location)
        self._require_permission(actor, action)


__all__ = ["NovaForgePolicy"]
