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
    they ask a different question instead. Both fail closed, and neither
    invents a wildcard: an action classified as neither is denied.

    CORRIDOR-SCOPED MEANS CORRIDOR-COMPLETE (Slice 10.1D.3, answering OQ-2)
        The question a corridor-scoped action asks is NOT "does this
        actor hold any scope in this corridor" - that let one held
        section stand in for the whole corridor, which is exactly the
        wildcard-by-accident this module elsewhere refuses. It is "do
        this actor's OWN-ROLE scopes, taken together, name EVERY section
        of this corridor" - see
        backend.app.identity.resource.covers_corridor. `_require_corridor`
        answers it against this policy's own `topology` (server-side
        configuration, exactly like the scope directory below - never the
        request), and denies when no topology is known for a corridor:
        absence is never a grant. See
        docs/SLICE10_1D3_CORRIDOR_AUTHORIZATION_ARCHITECTURE.md.

NO STATE, NO HISTORY, NO IDENTITY COMPARISON
    Nothing here reads a job status, an execution, evidence or the event
    history. Lifecycle owns state (backend.app.jobs.lifecycle).
    Segregation of duties is deferred entirely to 10.3, because today's
    actor ids are role-prefixed by convention ("WORKER-042",
    "AUTHORITY-017"), so one person acting in two roles presents two
    different ids and an actor_id comparison would silently never fire.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Iterable, Mapping, Protocol, Tuple

from backend.app.identity.actor import HUMAN_ROLES, Actor, ActorRole
from backend.app.identity.authorization import (
    AuthorizationDenied,
    JobAction,
    ResourceAwarePolicy,
)
from backend.app.identity.person import Person
from backend.app.identity.resource import (
    ResourceLocation,
    ScopeMatch,
    covers_corridor,
    match_scope,
)
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

    def __init__(
        self,
        directory: ScopeDirectory,
        *,
        topology: Iterable[Tuple[str, Iterable[str]]] = (),
    ) -> None:
        if not callable(getattr(directory, "scopes_for_actor", None)):
            raise TypeError(
                "EnforcingPolicy needs a ScopeDirectory exposing "
                f"scopes_for_actor; got {type(directory).__name__}."
            )

        self._directory = directory
        self._topology = self._build_topology(topology)

    @staticmethod
    def _build_topology(
        topology: Iterable[Tuple[str, Iterable[str]]],
    ) -> Mapping[str, FrozenSet[str]]:
        """Server-side corridor -> section-set data (Slice 10.1D.3).

        The ONLY source `_require_corridor` may consult for "which
        sections make up this corridor" - never the resource, the
        request, a header or a scope. Each entry is a (corridor_id,
        section_ids) pair, e.g. (registry.corridor_id,
        registry.section_ids()) for a deployment's own SectionRegistry.
        A duplicate corridor_id is refused rather than silently
        overwritten: last-write-wins would let a later entry silently
        redefine an earlier one's completeness. A corridor with no entry
        here has no PROVEN topology, and every corridor-scoped action
        against it fails closed - see covers_corridor's UNRESOLVED case.
        Frozen (a plain dict of frozensets) so nothing can mutate a
        policy's topology after construction.
        """

        by_corridor: Dict[str, FrozenSet[str]] = {}

        for entry in topology:
            try:
                corridor_id, section_ids = entry
            except (TypeError, ValueError) as exc:
                raise TypeError(
                    "topology entries must be (corridor_id, section_ids) "
                    f"pairs; got {entry!r}."
                ) from exc

            if not isinstance(corridor_id, str) or not corridor_id.strip():
                raise ValueError(
                    "topology corridor_id must be a non-blank string; got "
                    f"{corridor_id!r}."
                )

            if corridor_id in by_corridor:
                raise ValueError(
                    f"Duplicate topology for corridor {corridor_id!r}."
                )

            by_corridor[corridor_id] = frozenset(section_ids)

        return by_corridor

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

    def _require_corridor(
        self,
        actor: Actor,
        action: JobAction,
        location: ResourceLocation,
        scopes: Tuple[RailwayScope, ...],
    ) -> None:
        """Corridor-COMPLETE membership only (Slice 10.1D.3, OQ-2).

        Holding one section of the corridor is never enough - see the
        module docstring's "CORRIDOR-SCOPED MEANS CORRIDOR-COMPLETE".
        Only covers_corridor's MATCH permits; NO_MATCH and UNRESOLVED
        (no proven topology, blank corridor, empty section set) both
        deny, with the SAME message, so a caller cannot distinguish
        "no topology configured" from "topology known but incomplete".
        """

        corridor_id = location.corridor_id

        if not isinstance(corridor_id, str) or not corridor_id.strip():
            raise AuthorizationDenied(
                actor,
                action,
                "the resource names no corridor, so corridor-wide "
                "authority cannot be shown",
            )

        section_ids = self._topology.get(corridor_id)

        if covers_corridor(scopes, corridor_id, section_ids) is ScopeMatch.MATCH:
            return

        # Deliberately silent about which sections are missing or held:
        # this message reaches an HTTP caller verbatim as a 403 body (see
        # backend.app.jobs.router._api_error), and naming the gap would
        # hand a partially-scoped actor the map of the corridor it is not
        # authorized to have.
        raise AuthorizationDenied(
            actor,
            action,
            f"{actor.actor_id!r} does not hold corridor-wide authority "
            f"for corridor {corridor_id!r}",
        )


__all__ = [
    "EnforcingPolicy",
    "ScopeDirectory",
    "StaticScopeDirectory",
]
