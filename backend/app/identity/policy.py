"""A policy that actually enforces role and railway scope (Slice 10.1D).

    Actor  --(directory)-->  RailwayScope(s)  --(match)-->  permit / deny

NOT WIRED. JobService still constructs UnenforcedPolicy by default
(backend.app.jobs.service), so installing this class is a deliberate act
by a deployment. It exists now so the seam it uses is exercised by real
enforcement in tests rather than only by no-ops.

ENFORCEMENT IS NOT AUTHENTICATION
    This decides what a given Actor may do. It does not decide whether
    the Actor is who it claims to be, and it must not be read as doing
    so. Until authentication exists (Slice 10.3) every HTTP-borne actor
    carries DECLARED_UNVERIFIED assurance, so a deployment installing
    this policy today is enforcing authorization over a DECLARED
    identity - meaningful for a demo, and not a production trust
    boundary. The remedy is 10.3, not a flag here.

SCOPE NEVER COMES FROM THE REQUEST
    The only source of a RailwayScope is the injected directory, which is
    server-side configuration. No request field, header, query parameter
    or body value reaches it, and none may be added. A caller can name a
    job; it can never name the section it would like that job to be in.

TWO SCOPE RULES, CHOSEN BY THE ACTION
    Section-scoped actions match the job's own (corridor, section) pair
    through backend.app.identity.resource.match_scope. Corridor-scoped
    actions (optimization request and run read) have no section at all,
    so section matching would report them UNRESOLVED and deny everything;
    they ask the narrower question "does this actor hold any scope in
    this corridor?" instead. Both fail closed, and neither invents a
    wildcard: an action classified as neither is denied.

NO STATE, NO HISTORY, NO IDENTITY COMPARISON
    Nothing here reads a job status, an execution, evidence or the event
    history. Lifecycle owns state (backend.app.jobs.lifecycle).
    Segregation of duties is deferred entirely to 10.3, because today's
    actor ids are role-prefixed by convention ("WORKER-042",
    "AUTHORITY-017"), so one person acting in two roles presents two
    different ids and an actor_id comparison would silently never fire.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Protocol, Tuple

from backend.app.identity.actor import HUMAN_ROLES, Actor, ActorRole
from backend.app.identity.authorization import (
    AuthorizationDenied,
    JobAction,
    ResourceAwarePolicy,
)
from backend.app.identity.person import Person
from backend.app.identity.resource import ResourceLocation, ScopeMatch, match_scope
from backend.app.identity.role_actions import (
    CORRIDOR_SCOPED_ACTIONS,
    SECTION_SCOPED_ACTIONS,
    role_permits,
)
from backend.app.identity.scope import RailwayScope
from backend.app.identity.scope_assignment import RoleScopeAssignment, scopes_for


class ScopeDirectory(Protocol):
    """Read-only lookup of the railway scopes an actor holds in one role.

    The deployment's server-side configuration, never request input.
    Returning an empty tuple is the normal answer for "this actor holds
    no scope under that role", and it covers nothing.
    """

    def scopes_for_actor(
        self, actor_id: str, role: ActorRole
    ) -> Tuple[RailwayScope, ...]: ...


class StaticScopeDirectory:
    """An in-memory directory built from Persons and their scoped roles.

    Deliberately not a database (Slice 10.1D adds no schema, no table and
    no migration): assignments are configuration, and the identity
    foundation is directory-oriented by design.

    The lookup key is actor_id == Person.person_id. That equality is what
    Slice 10.1A built person_id to satisfy - it is a legal actor_id, so an
    authenticated Actor can one day carry it unchanged. Until then the
    directory is keyed by the DECLARED actor_id, which is exactly why this
    is demo enforcement and not a production trust boundary.
    """

    def __init__(
        self,
        people: Iterable[Person] = (),
        assignments: Iterable[RoleScopeAssignment] = (),
    ) -> None:
        by_id: dict[str, Person] = {}

        for person in people:
            if not isinstance(person, Person):
                raise TypeError(
                    f"Expected a Person; got {type(person).__name__}."
                )
            if person.person_id in by_id:
                raise ValueError(
                    f"Duplicate person_id {person.person_id!r} in the directory."
                )
            by_id[person.person_id] = person

        self._people: Mapping[str, Person] = by_id
        # Validated eagerly by scopes_for on every lookup, so a duplicate
        # role+scope assignment is refused rather than quietly deduplicated.
        self._assignments: Tuple[RoleScopeAssignment, ...] = tuple(assignments)

    def scopes_for_actor(
        self, actor_id: str, role: ActorRole
    ) -> Tuple[RailwayScope, ...]:
        """The scopes this actor holds under this role, or ()."""

        if role not in HUMAN_ROLES:
            # SYSTEM and UNIDENTIFIED are not roles a person can hold, so
            # there is nothing to look up. Never an error, never a grant.
            return ()

        person = self._people.get(actor_id)

        if person is None:
            return ()

        return scopes_for(person, role, self._assignments)


class EnforcingPolicy(ResourceAwarePolicy):
    """Enforces the role/action table and railway scope. Fails closed.

    Subclasses ResourceAwarePolicy, so it cannot exist without
    authorize_resource: omitting it is a TypeError at construction, not a
    silently skipped scope check at decision time.
    """

    enforcing = True

    def __init__(self, directory: ScopeDirectory) -> None:
        if not callable(getattr(directory, "scopes_for_actor", None)):
            raise TypeError(
                "EnforcingPolicy needs a ScopeDirectory exposing "
                f"scopes_for_actor; got {type(directory).__name__}."
            )

        self._directory = directory

    # ------------------------------------------------------------------
    # 1. Role / action. No repository read, no resource, no state.
    # ------------------------------------------------------------------

    def authorize(self, actor: Actor, action: JobAction) -> None:
        if not role_permits(actor.role, action):
            raise AuthorizationDenied(
                actor,
                action,
                f"the {actor.role.value} role holds no {action.value} permission",
            )

    # ------------------------------------------------------------------
    # 2. Resource / scope. Runs only after the resource is resolved.
    # ------------------------------------------------------------------

    def authorize_resource(
        self,
        actor: Actor,
        action: JobAction,
        location: ResourceLocation,
    ) -> None:
        if not isinstance(location, ResourceLocation):
            raise TypeError(
                "location must be a ResourceLocation; got "
                f"{type(location).__name__}."
            )

        # Defence in depth: this method is always reached through
        # authorize(), but it must never be weaker on its own.
        self.authorize(actor, action)

        scopes = self._directory.scopes_for_actor(actor.actor_id, actor.role)

        if not scopes:
            # No scope is the absence of a grant, never a global one.
            # ADMIN always lands here: it holds no scope by construction.
            raise AuthorizationDenied(
                actor,
                action,
                f"{actor.actor_id!r} holds no railway scope under the "
                f"{actor.role.value} role",
            )

        if action in CORRIDOR_SCOPED_ACTIONS:
            self._require_corridor(actor, action, location, scopes)
            return

        if action in SECTION_SCOPED_ACTIONS:
            self._require_section(actor, action, location, scopes)
            return

        # An action classified as neither is a gap in the table, not a
        # permission. Refuse rather than guess which rule applies.
        raise AuthorizationDenied(
            actor,
            action,
            f"{action.value} has no railway scope rule, so it cannot be "
            "authorized against a resource",
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _require_section(
        actor: Actor,
        action: JobAction,
        location: ResourceLocation,
        scopes: Tuple[RailwayScope, ...],
    ) -> None:
        """Exact (corridor, section) membership. Only MATCH permits."""

        results = tuple(match_scope(scope, location) for scope in scopes)

        if ScopeMatch.MATCH in results:
            return

        if ScopeMatch.UNRESOLVED in results:
            # A resource whose section is absent, blank or spanning several
            # sections. Unknown is not authorized.
            raise AuthorizationDenied(
                actor,
                action,
                "the resource could not be resolved to exactly one railway "
                "section, so it cannot be matched against a scope",
            )

        raise AuthorizationDenied(
            actor,
            action,
            f"{location.section_id!r} in corridor {location.corridor_id!r} is "
            f"outside every section {actor.actor_id!r} is scoped to",
        )

    @staticmethod
    def _require_corridor(
        actor: Actor,
        action: JobAction,
        location: ResourceLocation,
        scopes: Tuple[RailwayScope, ...],
    ) -> None:
        """Corridor membership only. Never a claim over any section in it."""

        corridor_id = location.corridor_id

        if not isinstance(corridor_id, str) or not corridor_id.strip():
            raise AuthorizationDenied(
                actor,
                action,
                "the resource names no corridor, so it cannot be matched "
                "against a scope",
            )

        if any(scope.corridor_id == corridor_id for scope in scopes):
            return

        raise AuthorizationDenied(
            actor,
            action,
            f"{actor.actor_id!r} holds no scope in corridor {corridor_id!r}",
        )


__all__ = [
    "EnforcingPolicy",
    "ScopeDirectory",
    "StaticScopeDirectory",
]
