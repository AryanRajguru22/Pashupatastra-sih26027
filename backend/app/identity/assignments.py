"""Railway role assignment and per-request role selection (Slice 10.1B).

    Person  --(person_id)-->  RoleAssignment(person_id, role)

A RoleAssignment is plain data: "this person holds this human railway
role". It is NOT an Actor, carries no scope, permission, credential,
assurance or session state, and confers nothing by itself. Railway scope
is a separate later concept; enforcement, authentication and Actor
construction are later slices. Nothing here persists anything.

ASSIGNABLE ROLES
    Only HUMAN_ROLES (WORKER, ENGINEER, AUTHORITY, ADMIN). SYSTEM is the
    application acting for itself and UNIDENTIFIED is the marker for "no
    identity supplied"; neither is a role a person can hold. ADMIN is the
    Pashupatastra administrative role and is never derived from an
    identity provider's role.

IDENTITY
    The pair (person_id, role) is the identity of an assignment: value
    equality, no generated id. A collection may hold each pair at most
    once. There is no temporal validity yet; removing an assignment is a
    change to whatever supplies the collection.

ONE ROLE PER REQUEST
    A person may hold several assignments, but a request acts in exactly
    one role. select_role picks that assignment for one request. The
    requested role is a selector among assignments the person already
    holds, never a claim: the result always comes from the collection.
    Ambiguity is refused, never resolved by "first", "highest" or "most
    privileged". No state is kept between calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

from backend.app.identity.actor import HUMAN_ROLES, ActorRole
from backend.app.identity.person import Person, PersonError, _validate_person_id

INVALID_ROLE = "INVALID_ROLE"
INVALID_PERSON_ID = "INVALID_PERSON_ID"
DUPLICATE_ASSIGNMENT = "DUPLICATE_ASSIGNMENT"
INVALID_ASSIGNMENT = "INVALID_ASSIGNMENT"
ROLE_NOT_ASSIGNED = "ROLE_NOT_ASSIGNED"
NO_ASSIGNMENT = "NO_ASSIGNMENT"
ROLE_SELECTION_REQUIRED = "ROLE_SELECTION_REQUIRED"


class AssignmentError(ValueError):
    """A role assignment or role selection was refused; see .reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(f"{reason}: {message}")
        self.reason = reason


def _human_role(value: object) -> ActorRole:
    """Resolve value to an assignable role, or raise INVALID_ROLE."""

    try:
        role = ActorRole(value)
    except (ValueError, TypeError) as exc:
        raise AssignmentError(
            INVALID_ROLE,
            f"Unknown role {value!r}. Permitted roles are "
            f"{sorted(r.value for r in HUMAN_ROLES)}.",
        ) from exc

    if role not in HUMAN_ROLES:
        raise AssignmentError(
            INVALID_ROLE,
            f"Role {role.value} is not a human railway role and cannot be "
            f"assigned. Permitted roles are {sorted(r.value for r in HUMAN_ROLES)}.",
        )

    return role


@dataclass(frozen=True)
class RoleAssignment:
    """One person holds one human railway role. Nothing more."""

    person_id: str
    role: ActorRole

    def __post_init__(self) -> None:
        try:
            _validate_person_id(self.person_id)
        except PersonError as exc:
            raise AssignmentError(INVALID_PERSON_ID, str(exc)) from exc

        object.__setattr__(self, "role", _human_role(self.role))


def _checked(assignments: Iterable[RoleAssignment]) -> Tuple[RoleAssignment, ...]:
    """Validate a whole collection: members are assignments, no duplicates."""

    members = tuple(assignments)
    seen = set()

    for member in members:
        if not isinstance(member, RoleAssignment):
            raise AssignmentError(
                INVALID_ASSIGNMENT,
                f"Expected a RoleAssignment; got {type(member).__name__}.",
            )
        if member in seen:
            raise AssignmentError(
                DUPLICATE_ASSIGNMENT,
                f"{member.person_id} is assigned {member.role.value} more than once.",
            )
        seen.add(member)

    return members


def _require_person(person: object) -> Person:
    if not isinstance(person, Person):
        raise AssignmentError(
            INVALID_ASSIGNMENT, f"Expected a Person; got {type(person).__name__}."
        )
    return person


def assignments_for(
    person: Person, assignments: Iterable[RoleAssignment]
) -> Tuple[RoleAssignment, ...]:
    """The person's assignments, ordered by role name.

    The whole collection is validated, so a duplicate anywhere in it is
    refused. The order (alphabetical by role) is only for determinism; it
    implies no ranking of roles.
    """

    person = _require_person(person)
    members = _checked(assignments)

    return tuple(
        sorted(
            (m for m in members if m.person_id == person.person_id),
            key=lambda m: m.role.value,
        )
    )


def select_role(
    person: Person,
    assignments: Iterable[RoleAssignment],
    requested_role: Optional[ActorRole | str] = None,
) -> RoleAssignment:
    """Choose the single assignment the person acts under for one request.

    requested_role given:
        not an assignable role        -> INVALID_ROLE
        not held by the person        -> ROLE_NOT_ASSIGNED
        held                          -> that assignment
    requested_role None:
        exactly one assignment        -> that assignment
        none                          -> NO_ASSIGNMENT
        several                       -> ROLE_SELECTION_REQUIRED
    """

    held = assignments_for(person, assignments)

    if requested_role is not None:
        wanted = _human_role(requested_role)

        for assignment in held:
            if assignment.role is wanted:
                return assignment

        raise AssignmentError(
            ROLE_NOT_ASSIGNED,
            f"{person.person_id} does not hold the {wanted.value} role.",
        )

    if not held:
        raise AssignmentError(
            NO_ASSIGNMENT, f"{person.person_id} has no role assignment."
        )

    if len(held) > 1:
        raise AssignmentError(
            ROLE_SELECTION_REQUIRED,
            f"{person.person_id} holds {len(held)} roles; a role must be "
            "requested.",
        )

    return held[0]


__all__ = [
    "AssignmentError",
    "RoleAssignment",
    "assignments_for",
    "select_role",
]
