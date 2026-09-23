"""Identity directory: an immutable in-memory people/role/scope store (Slice 10.2b).

    ExternalIdentity --(exact match)--> Person --> RoleAssignment(s)
                                                --> RoleScopeAssignment(s)

The directory is the server-side answer to "who is this external
identity, which roles does that person hold and over which scopes". It
is pure data behind a few lookups. It is NOT authentication: it never
verifies an assertion, builds an Actor, assigns assurance, reads a file,
touches persistence or infers a role from anything a provider says.
Configuration loading and reload are a later slice; this one is built
from in-memory collections only.

WHOLE-DATASET VALIDATION
    Construction validates the complete dataset and fails as a whole on
    the first violated invariant. There is no partially loaded directory.

        person_id unique; ExternalIdentity unique across persons
        every assignment / scope assignment references an enrolled person
        every scope assignment's role is actually held by that person
        no duplicate assignments (validated by the existing collection
        checks, not a competing copy of them)
        ADMIN is only ever a RoleAssignment and never receives scope

    An empty directory is valid and grants nothing.

MATCHING IS EXACT
    ExternalIdentity lookup is strict and case-sensitive: (provider,
    subject) is the key, exactly as Slice 10.1A defines it. No stripping,
    folding or normalisation happens here.

DETERMINISM AND IMMUTABILITY
    Inputs are copied and stored in canonical order, so the same dataset
    behaves identically whatever order it was supplied in. The instance
    is read-only after construction.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Iterable, Optional, Tuple

from backend.app.identity.actor import HUMAN_ROLES, ActorRole
from backend.app.identity.assignments import (
    RoleAssignment,
    _checked as _checked_role_assignments,
    assignments_for as _assignments_for,
    select_role as _select_role,
)
from backend.app.identity.person import ExternalIdentity, Person
from backend.app.identity.scope import RailwayScope
from backend.app.identity.scope_assignment import (
    ADMIN_HAS_NO_SCOPE,
    RoleScopeAssignment,
    _checked as _checked_scope_assignments,
    _order as _scope_order,
)

IDENTITY_NOT_ENROLLED = "IDENTITY_NOT_ENROLLED"
PERSON_NOT_ENROLLED = "PERSON_NOT_ENROLLED"
INVALID_IDENTITY_ARGUMENT = "INVALID_IDENTITY_ARGUMENT"
INVALID_DIRECTORY_INPUT = "INVALID_DIRECTORY_INPUT"
INVALID_PERSON = "INVALID_PERSON"
DUPLICATE_PERSON = "DUPLICATE_PERSON"
DUPLICATE_PERSON_ID = "DUPLICATE_PERSON_ID"
DUPLICATE_EXTERNAL_IDENTITY = "DUPLICATE_EXTERNAL_IDENTITY"
UNKNOWN_PERSON = "UNKNOWN_PERSON"
ROLE_NOT_HELD = "ROLE_NOT_HELD"


class DirectoryError(ValueError):
    """A directory could not be built or a lookup was refused; see .reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(f"{reason}: {message}")
        self.reason = reason


def _as_tuple(label: str, values: object) -> Tuple[Any, ...]:
    """Defensively copy an iterable input; a non-iterable is refused."""

    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise DirectoryError(
            INVALID_DIRECTORY_INPUT,
            f"{label} must be a collection; got {type(values).__name__}.",
        )
    return tuple(values)


def _identity_key(identity: ExternalIdentity) -> Tuple[str, str]:
    return (identity.identity_provider, identity.subject)


class IdentityDirectory:
    """Immutable people, role assignments and scope assignments.

    Satisfies the ScopeDirectory protocol (scopes_for_actor), so it can
    back an EnforcingPolicy directly.
    """

    __slots__ = (
        "_people",
        "_by_id",
        "_by_identity",
        "_role_assignments",
        "_scope_assignments",
        "_held",
        "_scopes",
    )

    def __init__(
        self,
        people: Iterable[Person] = (),
        role_assignments: Iterable[RoleAssignment] = (),
        scope_assignments: Iterable[RoleScopeAssignment] = (),
    ) -> None:
        person_list = _as_tuple("people", people)
        role_list = _as_tuple("role_assignments", role_assignments)
        scope_list = _as_tuple("scope_assignments", scope_assignments)

        by_id: dict[str, Person] = {}
        by_identity: dict[Tuple[str, str], Person] = {}

        for person in person_list:
            if not isinstance(person, Person):
                raise DirectoryError(
                    INVALID_PERSON,
                    f"Expected a Person; got {type(person).__name__}.",
                )

            existing = by_id.get(person.person_id)
            if existing is not None:
                if existing == person:
                    raise DirectoryError(
                        DUPLICATE_PERSON,
                        f"Person {person.person_id!r} is listed more than once.",
                    )
                raise DirectoryError(
                    DUPLICATE_PERSON_ID,
                    f"person_id {person.person_id!r} is used by more than one person.",
                )

            key = _identity_key(person.identity)
            if key in by_identity:
                # Neither subject nor provider is echoed.
                raise DirectoryError(
                    DUPLICATE_EXTERNAL_IDENTITY,
                    f"An external identity is bound to more than one person "
                    f"({by_identity[key].person_id!r} and {person.person_id!r}).",
                )

            by_id[person.person_id] = person
            by_identity[key] = person

        # Existing collection validation: member types and duplicates.
        roles = _checked_role_assignments(role_list)
        scopes = _checked_scope_assignments(scope_list)

        for assignment in roles:
            if assignment.person_id not in by_id:
                raise DirectoryError(
                    UNKNOWN_PERSON,
                    f"Role assignment references unknown person "
                    f"{assignment.person_id!r}.",
                )

        held = frozenset((a.person_id, a.role) for a in roles)

        for scoped in scopes:
            if scoped.person_id not in by_id:
                raise DirectoryError(
                    UNKNOWN_PERSON,
                    f"Scope assignment references unknown person "
                    f"{scoped.person_id!r}.",
                )
            if scoped.role is ActorRole.ADMIN:
                raise DirectoryError(
                    ADMIN_HAS_NO_SCOPE,
                    "ADMIN carries no scope; it is never given a wildcard or "
                    "an all-sections scope.",
                )
            if (scoped.person_id, scoped.role) not in held:
                raise DirectoryError(
                    ROLE_NOT_HELD,
                    f"{scoped.person_id!r} holds no {scoped.role.value} role "
                    "assignment, so it cannot hold scope under that role.",
                )

        ordered_roles = tuple(
            sorted(roles, key=lambda a: (a.person_id, a.role.value))
        )
        ordered_scopes = tuple(
            sorted(
                scopes,
                key=lambda s: (s.person_id, s.role.value, _scope_order(s.scope)),
            )
        )

        scope_index: dict[Tuple[str, ActorRole], list[RailwayScope]] = {}
        for scoped in ordered_scopes:
            scope_index.setdefault((scoped.person_id, scoped.role), []).append(
                scoped.scope
            )

        set_ = object.__setattr__
        set_(self, "_people", tuple(by_id[k] for k in sorted(by_id)))
        set_(self, "_by_id", MappingProxyType(dict(by_id)))
        set_(self, "_by_identity", MappingProxyType(dict(by_identity)))
        set_(self, "_role_assignments", ordered_roles)
        set_(self, "_scope_assignments", ordered_scopes)
        set_(self, "_held", held)
        set_(
            self,
            "_scopes",
            MappingProxyType({k: tuple(v) for k, v in scope_index.items()}),
        )

    # ------------------------------------------------------------------
    # Read-only state.
    # ------------------------------------------------------------------

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("IdentityDirectory is immutable.")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("IdentityDirectory is immutable.")

    @property
    def people(self) -> Tuple[Person, ...]:
        return self._people

    @property
    def role_assignments(self) -> Tuple[RoleAssignment, ...]:
        return self._role_assignments

    @property
    def scope_assignments(self) -> Tuple[RoleScopeAssignment, ...]:
        return self._scope_assignments

    # ------------------------------------------------------------------
    # Lookups.
    # ------------------------------------------------------------------

    def person_for(self, identity: ExternalIdentity) -> Person:
        """The person bound to exactly this external identity."""

        if not isinstance(identity, ExternalIdentity):
            raise DirectoryError(
                INVALID_IDENTITY_ARGUMENT,
                f"Expected an ExternalIdentity; got {type(identity).__name__}.",
            )

        person = self._by_identity.get(_identity_key(identity))

        if person is None:
            raise DirectoryError(
                IDENTITY_NOT_ENROLLED,
                "The external identity is not enrolled in the directory.",
            )

        return person

    def person(self, person_id: str) -> Person:
        """The enrolled person with this person_id."""

        found = self._by_id.get(person_id) if isinstance(person_id, str) else None

        if found is not None:
            return found

        raise DirectoryError(
            PERSON_NOT_ENROLLED, "No such person is enrolled in the directory."
        )

    def _enrolled(self, person: Person) -> Person:
        """The directory's own Person for this one, or raise.

        A Person that merely shares a person_id but carries a different
        identity is not the enrolled person.
        """

        if self.person(person.person_id) != person:
            raise DirectoryError(
                PERSON_NOT_ENROLLED, "That person is not enrolled in the directory."
            )
        return person

    def assignments_for(self, person: Person) -> Tuple[RoleAssignment, ...]:
        """The directory's explicit role assignments for this person."""

        if isinstance(person, Person):
            self._enrolled(person)
        return _assignments_for(person, self._role_assignments)

    def select_role(
        self,
        person: Person,
        requested_role: Optional[ActorRole | str] = None,
    ) -> RoleAssignment:
        """Delegates to the existing select_role over the directory's data."""

        if isinstance(person, Person):
            self._enrolled(person)
        return _select_role(person, self._role_assignments, requested_role)

    def scopes_for_actor(
        self, actor_id: str, role: ActorRole
    ) -> Tuple[RailwayScope, ...]:
        """Scopes held by this person under exactly this role, or ().

        () means "no scope" and covers nothing; it is never a wildcard.
        """

        if not isinstance(actor_id, str) or not isinstance(role, ActorRole):
            return ()

        if role not in HUMAN_ROLES or (actor_id, role) not in self._held:
            return ()

        return self._scopes.get((actor_id, role), ())


__all__ = [
    "DirectoryError",
    "IdentityDirectory",
]
