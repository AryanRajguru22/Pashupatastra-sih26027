"""Role-scoped assignment: Person + Role + RailwayScope (Slice 10.1C).

    RoleScopeAssignment(person_id, role, scope)

Scope belongs to a role context, never to a person alone: the same person
may hold WORKER on one set of sections and ENGINEER on another, and the
two never merge or inherit. RoleAssignment(person_id, role) is untouched
and stays scope-free; RoleScopeAssignment is a separate, parallel value
that a later directory can hold beside it (this slice builds no directory,
persistence, temporal validity or authorization).

ADMIN CARRIES NO SCOPE
    ADMIN is a valid RoleAssignment but has no RoleScopeAssignment: it is
    represented by ABSENCE, never by a wildcard, an "all sections" scope
    or a scope that happens to list every section. Constructing a
    RoleScopeAssignment for ADMIN is refused. Whether ADMIN may do
    anything is a later authorization decision; the data cannot make
    ADMIN operationally scoped.

IDENTITY
    Value equality over (person_id, role, scope); scope equality is
    canonical because section ids are a frozenset. A collection may hold
    each such value at most once. The same person and role may hold
    several DIFFERENT scopes (for example two corridors); nothing merges
    them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

from backend.app.identity.actor import ActorRole
from backend.app.identity.assignments import (
    DUPLICATE_ASSIGNMENT,
    INVALID_ASSIGNMENT,
    AssignmentError,
    RoleAssignment,
    _human_role,
    _require_person,
)
from backend.app.identity.person import Person
from backend.app.identity.scope import RailwayScope

INVALID_SCOPE = "INVALID_SCOPE"
ADMIN_HAS_NO_SCOPE = "ADMIN_HAS_NO_SCOPE"


@dataclass(frozen=True)
class RoleScopeAssignment:
    """One person holds one operational role over one explicit scope."""

    person_id: str
    role: ActorRole
    scope: RailwayScope

    def __post_init__(self) -> None:
        # RoleAssignment owns person_id and role validation.
        validated = RoleAssignment(self.person_id, self.role)

        if validated.role is ActorRole.ADMIN:
            raise AssignmentError(
                ADMIN_HAS_NO_SCOPE,
                "ADMIN carries no scope and has no role-scope assignment; "
                "it is never given a wildcard or an all-sections scope.",
            )

        if not isinstance(self.scope, RailwayScope):
            raise AssignmentError(
                INVALID_SCOPE,
                f"scope must be a RailwayScope; got {type(self.scope).__name__}.",
            )

        object.__setattr__(self, "role", validated.role)

    @property
    def role_assignment(self) -> RoleAssignment:
        """The scope-free role assignment this scope belongs to."""

        return RoleAssignment(self.person_id, self.role)


def _checked(
    assignments: Iterable[RoleScopeAssignment],
) -> Tuple[RoleScopeAssignment, ...]:
    """Validate a whole collection: members are role-scope assignments, no duplicates."""

    members = tuple(assignments)
    seen = set()

    for member in members:
        if not isinstance(member, RoleScopeAssignment):
            raise AssignmentError(
                INVALID_ASSIGNMENT,
                f"Expected a RoleScopeAssignment; got {type(member).__name__}.",
            )
        if member in seen:
            raise AssignmentError(
                DUPLICATE_ASSIGNMENT,
                f"{member.person_id} holds {member.role.value} over the same "
                f"scope ({member.scope.corridor_id}: "
                f"{list(member.scope.sorted_section_ids())}) more than once.",
            )
        seen.add(member)

    return members


def _order(scope: RailwayScope) -> Tuple[str, Tuple[str, ...]]:
    return (scope.corridor_id, scope.sorted_section_ids())


def scopes_for(
    person: Person,
    role: ActorRole | str,
    assignments: Iterable[RoleScopeAssignment],
) -> Tuple[RailwayScope, ...]:
    """The scopes this person holds under exactly this role.

    The whole collection is validated, so a duplicate anywhere in it is
    refused. Scopes of other roles are never included or inherited. A role
    the person has no scope for (always the case for ADMIN) yields an empty
    tuple: absence of scope, never a default or a wildcard. Order (by
    corridor, then section ids) is only for determinism and implies no
    ranking.
    """

    person = _require_person(person)
    wanted = _human_role(role)
    members = _checked(assignments)

    return tuple(
        sorted(
            (
                m.scope
                for m in members
                if m.person_id == person.person_id and m.role is wanted
            ),
            key=_order,
        )
    )


__all__ = [
    "RoleScopeAssignment",
    "scopes_for",
]
