"""Slice 10.1B: railway role assignment (Person -> assigned role(s)).

Pins that a RoleAssignment is plain, immutable data linking a person_id
to one human railway role; that a person may hold several roles as
several assignments (never a role list on Person); that duplicates are
rejected; and that a request-time selection can only ever return an
assignment the person already holds. No scope, permission, credential,
session, persistence or authentication exists here.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import itertools

import pytest

from backend.app.identity import assignments as assignments_module
from backend.app.identity.actor import (
    HUMAN_ROLES,
    Actor,
    ActorRole,
    IdentityAssurance,
)
from backend.app.identity.assignments import (
    AssignmentError,
    RoleAssignment,
    assignments_for,
    select_role,
)
from backend.app.identity.person import ExternalIdentity, Person

SUBJECT = "0b7d3a52-4c1e-4f0a-9d6e-2f1c8a7b5e90"


def _person(person_id: str = "P-000123", subject: str = SUBJECT) -> Person:
    return Person(
        person_id=person_id,
        identity=ExternalIdentity(identity_provider="novaforge", subject=subject),
    )


def _ra(role, person_id: str = "P-000123") -> RoleAssignment:
    return RoleAssignment(person_id=person_id, role=role)


def _reason(excinfo) -> str:
    return excinfo.value.reason


# ----------------------------------------------------------------------
# RoleAssignment: construction and validity
# ----------------------------------------------------------------------


def test_valid_role_assignment_preserves_values():
    assignment = _ra(ActorRole.AUTHORITY)

    assert assignment.person_id == "P-000123"
    assert assignment.role is ActorRole.AUTHORITY


def test_assignment_for_a_person_uses_the_person_id():
    person = _person()
    assignment = RoleAssignment(person_id=person.person_id, role=ActorRole.WORKER)

    assert assignment.person_id == person.person_id


@pytest.mark.parametrize("role", sorted(HUMAN_ROLES, key=lambda r: r.value))
def test_every_human_role_is_assignable(role):
    assert _ra(role).role is role


def test_admin_is_assignable_as_a_railway_domain_role():
    # ADMIN is "administrative" in the readiness report and is assignable;
    # this is the Pashupatastra ADMIN role, never a NovaForge mapping.
    assert _ra(ActorRole.ADMIN).role is ActorRole.ADMIN


@pytest.mark.parametrize("role", [ActorRole.SYSTEM, ActorRole.UNIDENTIFIED])
def test_system_and_unidentified_are_never_assignable(role):
    with pytest.raises(AssignmentError) as excinfo:
        _ra(role)

    assert _reason(excinfo) == "INVALID_ROLE"


def test_assignable_roles_are_exactly_the_existing_human_roles():
    assignable = {
        role
        for role in ActorRole
        if _try_assign(role)
    }

    assert assignable == set(HUMAN_ROLES)
    assert assignable == {
        ActorRole.WORKER,
        ActorRole.ENGINEER,
        ActorRole.AUTHORITY,
        ActorRole.ADMIN,
    }


def _try_assign(role) -> bool:
    try:
        _ra(role)
    except AssignmentError:
        return False
    return True


def test_role_value_strings_are_coerced_to_the_enum_like_actor_does():
    assignment = _ra("AUTHORITY")

    assert assignment.role is ActorRole.AUTHORITY
    assert assignment == _ra(ActorRole.AUTHORITY)


@pytest.mark.parametrize(
    "bad", ["authority", "Authority", "SUPER_ADMIN", "", " ", "NOVAFORGE_ADMIN", None, 3]
)
def test_unknown_roles_are_rejected(bad):
    with pytest.raises(AssignmentError) as excinfo:
        _ra(bad)

    assert _reason(excinfo) == "INVALID_ROLE"


@pytest.mark.parametrize("bad", ["", " ", "P 1", "-P1", "SYSTEM", "SYSTEM:X", None, 5])
def test_invalid_person_ids_are_rejected(bad):
    with pytest.raises(AssignmentError) as excinfo:
        _ra(ActorRole.WORKER, person_id=bad)

    assert _reason(excinfo) == "INVALID_PERSON_ID"


# ----------------------------------------------------------------------
# RoleAssignment: value semantics and immutability
# ----------------------------------------------------------------------


def test_equality_and_hash_are_value_based_and_deterministic():
    a = _ra(ActorRole.ENGINEER)
    b = _ra(ActorRole.ENGINEER)

    assert a == b
    assert hash(a) == hash(b)
    assert len({a, b}) == 1


def test_assignments_differing_in_person_or_role_are_distinct():
    base = _ra(ActorRole.ENGINEER)

    assert base != _ra(ActorRole.AUTHORITY)
    assert base != _ra(ActorRole.ENGINEER, person_id="P-000124")
    assert len({base, _ra(ActorRole.AUTHORITY)}) == 2


def test_role_assignment_is_immutable():
    assignment = _ra(ActorRole.WORKER)

    with pytest.raises(dataclasses.FrozenInstanceError):
        assignment.role = ActorRole.ADMIN  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        assignment.person_id = "P-999999"  # type: ignore[misc]


def test_assignment_needs_no_generated_id_the_pair_is_the_identity():
    assert [f.name for f in dataclasses.fields(RoleAssignment)] == [
        "person_id",
        "role",
    ]


# ----------------------------------------------------------------------
# Multi-role and duplicate prevention
# ----------------------------------------------------------------------


def test_same_person_can_hold_multiple_roles():
    person = _person()
    roles = [ActorRole.WORKER, ActorRole.ENGINEER, ActorRole.AUTHORITY]
    collection = [_ra(role, person.person_id) for role in roles]

    held = assignments_for(person, collection)

    assert {a.role for a in held} == set(roles)
    assert len(held) == 3


def test_duplicate_same_role_assignment_is_rejected():
    person = _person()
    collection = [_ra(ActorRole.AUTHORITY), _ra(ActorRole.AUTHORITY)]

    with pytest.raises(AssignmentError) as excinfo:
        assignments_for(person, collection)

    assert _reason(excinfo) == "DUPLICATE_ASSIGNMENT"


def test_duplicate_is_rejected_even_when_it_belongs_to_another_person():
    collection = [
        _ra(ActorRole.WORKER, "P-000200"),
        _ra(ActorRole.WORKER, "P-000200"),
        _ra(ActorRole.WORKER, "P-000123"),
    ]

    with pytest.raises(AssignmentError) as excinfo:
        assignments_for(_person(), collection)

    assert _reason(excinfo) == "DUPLICATE_ASSIGNMENT"


def test_duplicate_is_detected_whether_role_was_given_as_enum_or_string():
    with pytest.raises(AssignmentError) as excinfo:
        assignments_for(_person(), [_ra("AUTHORITY"), _ra(ActorRole.AUTHORITY)])

    assert _reason(excinfo) == "DUPLICATE_ASSIGNMENT"


def test_the_same_role_for_different_people_is_not_a_duplicate():
    collection = [
        _ra(ActorRole.WORKER, "P-000123"),
        _ra(ActorRole.WORKER, "P-000124"),
    ]

    assert len(assignments_for(_person("P-000123"), collection)) == 1
    assert len(assignments_for(_person("P-000124", "s2"), collection)) == 1


def test_non_assignment_members_are_rejected():
    with pytest.raises(AssignmentError):
        assignments_for(_person(), [("P-000123", ActorRole.WORKER)])  # type: ignore[list-item]


def test_assignments_for_requires_a_person():
    with pytest.raises(AssignmentError):
        assignments_for("P-000123", [])  # type: ignore[arg-type]


def test_assignments_for_only_returns_that_persons_assignments():
    collection = [
        _ra(ActorRole.WORKER, "P-000123"),
        _ra(ActorRole.AUTHORITY, "P-000200"),
    ]

    held = assignments_for(_person("P-000123"), collection)

    assert held == (_ra(ActorRole.WORKER, "P-000123"),)
    assert assignments_for(_person("P-000999", "s9"), collection) == ()


def test_assignments_for_accepts_any_iterable():
    person = _person()

    held = assignments_for(person, (_ra(r) for r in (ActorRole.WORKER,)))

    assert held == (_ra(ActorRole.WORKER),)


# ----------------------------------------------------------------------
# Deterministic ordering (no privilege ordering implied)
# ----------------------------------------------------------------------


def test_role_ordering_is_deterministic_regardless_of_input_order():
    person = _person()
    roles = [ActorRole.WORKER, ActorRole.AUTHORITY, ActorRole.ENGINEER, ActorRole.ADMIN]

    results = {
        assignments_for(person, [_ra(r) for r in perm])
        for perm in itertools.permutations(roles)
    }

    assert len(results) == 1
    (ordered,) = results
    assert [a.role.value for a in ordered] == sorted(r.value for r in roles)


def test_result_is_an_immutable_tuple():
    assert isinstance(assignments_for(_person(), [_ra(ActorRole.WORKER)]), tuple)


# ----------------------------------------------------------------------
# Selecting the one role for a request
# ----------------------------------------------------------------------


def _multi_role_collection():
    return [
        _ra(ActorRole.WORKER),
        _ra(ActorRole.ENGINEER),
        _ra(ActorRole.AUTHORITY),
    ]


def test_selecting_an_assigned_role_succeeds():
    selected = select_role(_person(), _multi_role_collection(), ActorRole.AUTHORITY)

    assert selected == _ra(ActorRole.AUTHORITY)
    assert isinstance(selected, RoleAssignment)


def test_selection_returns_exactly_one_assignment():
    selected = select_role(_person(), _multi_role_collection(), "ENGINEER")

    assert isinstance(selected, RoleAssignment)
    assert selected.role is ActorRole.ENGINEER


def test_selecting_an_unassigned_role_fails():
    collection = [_ra(ActorRole.WORKER)]

    with pytest.raises(AssignmentError) as excinfo:
        select_role(_person(), collection, ActorRole.AUTHORITY)

    assert _reason(excinfo) == "ROLE_NOT_ASSIGNED"


def test_a_role_held_only_by_someone_else_cannot_be_selected():
    collection = [
        _ra(ActorRole.WORKER, "P-000123"),
        _ra(ActorRole.AUTHORITY, "P-000200"),
    ]

    with pytest.raises(AssignmentError) as excinfo:
        select_role(_person("P-000123"), collection, ActorRole.AUTHORITY)

    assert _reason(excinfo) == "ROLE_NOT_ASSIGNED"


@pytest.mark.parametrize(
    "requested", [ActorRole.SYSTEM, ActorRole.UNIDENTIFIED, "SUPER_ADMIN", "", "worker"]
)
def test_invalid_requested_roles_are_rejected_even_if_present_elsewhere(requested):
    with pytest.raises(AssignmentError) as excinfo:
        select_role(_person(), _multi_role_collection(), requested)

    assert _reason(excinfo) == "INVALID_ROLE"


def test_empty_string_is_an_invalid_request_not_an_absent_one():
    with pytest.raises(AssignmentError) as excinfo:
        select_role(_person(), [_ra(ActorRole.WORKER)], "")

    assert _reason(excinfo) == "INVALID_ROLE"


def test_no_requested_role_selects_the_only_assignment():
    selected = select_role(_person(), [_ra(ActorRole.ENGINEER)])

    assert selected == _ra(ActorRole.ENGINEER)


def test_no_requested_role_and_no_assignment_is_rejected():
    with pytest.raises(AssignmentError) as excinfo:
        select_role(_person(), [_ra(ActorRole.WORKER, "P-000200")])

    assert _reason(excinfo) == "NO_ASSIGNMENT"


def test_no_requested_role_with_several_assignments_is_rejected_not_guessed():
    # Never "first", "highest" or "most privileged": ambiguity is refused.
    for perm in itertools.permutations(_multi_role_collection()):
        with pytest.raises(AssignmentError) as excinfo:
            select_role(_person(), list(perm))

        assert _reason(excinfo) == "ROLE_SELECTION_REQUIRED"


def test_requested_role_with_no_assignments_is_not_assigned():
    with pytest.raises(AssignmentError) as excinfo:
        select_role(_person(), [], ActorRole.WORKER)

    assert _reason(excinfo) == "ROLE_NOT_ASSIGNED"


def test_selection_is_independent_of_input_order():
    person = _person()
    for perm in itertools.permutations(_multi_role_collection()):
        assert select_role(person, list(perm), ActorRole.ENGINEER) == _ra(
            ActorRole.ENGINEER
        )


def test_selection_validates_the_collection_and_rejects_duplicates():
    with pytest.raises(AssignmentError) as excinfo:
        select_role(
            _person(),
            [_ra(ActorRole.WORKER), _ra(ActorRole.WORKER)],
            ActorRole.WORKER,
        )

    assert _reason(excinfo) == "DUPLICATE_ASSIGNMENT"


def test_select_role_requires_a_person():
    with pytest.raises(AssignmentError):
        select_role("P-000123", [_ra(ActorRole.WORKER)], ActorRole.WORKER)  # type: ignore[arg-type]


def test_selection_is_per_request_and_leaves_no_state_behind():
    person = _person()
    collection = _multi_role_collection()

    first = select_role(person, collection, ActorRole.WORKER)
    second = select_role(person, collection, ActorRole.AUTHORITY)
    third = select_role(person, collection, ActorRole.WORKER)

    assert first.role is ActorRole.WORKER
    assert second.role is ActorRole.AUTHORITY
    assert third == first
    assert not hasattr(person, "active_role")
    assert not hasattr(Person, "active_role")
    assert collection == _multi_role_collection()


def test_the_returned_role_comes_from_the_assignment_not_the_input():
    # A string request is only a selector; what comes back is the stored
    # enum-valued assignment.
    selected = select_role(_person(), _multi_role_collection(), "AUTHORITY")

    assert selected.role is ActorRole.AUTHORITY
    assert isinstance(selected.role, ActorRole)


# ----------------------------------------------------------------------
# Person stays role-neutral
# ----------------------------------------------------------------------


def test_person_is_unchanged_and_role_neutral():
    assert [f.name for f in dataclasses.fields(Person)] == ["person_id", "identity"]
    assert not hasattr(Person, "roles")
    assert not hasattr(Person, "role")


def test_multiple_assignments_do_not_change_person_identity():
    person = _person()
    before = (person, hash(person), person.person_id, person.identity)

    assignments_for(person, _multi_role_collection())
    select_role(person, _multi_role_collection(), ActorRole.AUTHORITY)

    assert (person, hash(person), person.person_id, person.identity) == before
    assert person == _person()


def test_person_id_does_not_depend_on_assigned_roles():
    person = _person()

    for role in HUMAN_ROLES:
        assert _ra(role, person.person_id).person_id == person.person_id


# ----------------------------------------------------------------------
# What a role assignment must not contain
# ----------------------------------------------------------------------

FORBIDDEN_FIELD_NAMES = {
    # scope (10.1C)
    "scope",
    "scopes",
    "section_id",
    "section_ids",
    "sections",
    "corridor_id",
    "track_id",
    "asset_id",
    # authentication / credentials
    "token",
    "access_token",
    "id_token",
    "refresh_token",
    "password",
    "secret",
    "cookie",
    "assurance",
    "identity_assurance",
    # permissions
    "permission",
    "permissions",
    "allowed_actions",
    # request / session state
    "session",
    "session_id",
    "active",
    "active_role",
    "request",
    # profile / provider role
    "email",
    "name",
    "display_name",
    "novaforge_role",
    # temporal validity is deferred
    "effective_from",
    "effective_until",
    "expires_at",
}


def test_role_assignment_has_no_scope_auth_permission_or_session_fields():
    names = {f.name for f in dataclasses.fields(RoleAssignment)}

    assert not names & FORBIDDEN_FIELD_NAMES


def test_role_assignment_is_a_frozen_dataclass():
    assert dataclasses.is_dataclass(RoleAssignment)
    assert RoleAssignment.__dataclass_params__.frozen


def test_role_assignment_holds_no_actor_assurance_or_person_object():
    assignment = _ra(ActorRole.WORKER)

    for value in vars(assignment).values():
        assert not isinstance(value, (Actor, IdentityAssurance, Person))

    assert not isinstance(assignment, Actor)
    assert not hasattr(assignment, "to_dict")


def test_module_never_references_actor_construction():
    tree = ast.parse(inspect.getsource(assignments_module))

    referenced = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}

    assert not referenced & {
        "Actor",
        "human_actor",
        "system_actor",
        "unidentified_actor",
        "IdentityAssurance",
        "JobAction",
    }


def test_module_imports_no_persistence_authentication_or_http_machinery():
    tree = ast.parse(inspect.getsource(assignments_module))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    top_level = {name.split(".")[0] for name in imported}
    assert not top_level & {
        "sqlite3",
        "jwt",
        "jose",
        "requests",
        "httpx",
        "fastapi",
        "authlib",
    }
    assert not any(name.startswith("backend.app.jobs") for name in imported)
    assert not any(name.startswith("backend.app.api") for name in imported)
    assert "backend.app.identity.authorization" not in imported


def test_module_holds_no_global_mutable_state():
    mutable = [
        name
        for name, value in vars(assignments_module).items()
        if not name.startswith("__") and isinstance(value, (list, dict, set))
    ]

    assert mutable == []


# ----------------------------------------------------------------------
# Existing identity model is untouched
# ----------------------------------------------------------------------


def test_actor_role_members_are_unchanged():
    assert [role.value for role in ActorRole] == [
        "WORKER",
        "ENGINEER",
        "AUTHORITY",
        "ADMIN",
        "SYSTEM",
        "UNIDENTIFIED",
    ]
    assert HUMAN_ROLES == frozenset(
        {
            ActorRole.WORKER,
            ActorRole.ENGINEER,
            ActorRole.AUTHORITY,
            ActorRole.ADMIN,
        }
    )


def test_actor_and_identity_assurance_are_unchanged():
    assert [f.name for f in dataclasses.fields(Actor)] == [
        "actor_id",
        "role",
        "assurance",
    ]
    assert {m.value for m in IdentityAssurance} == {
        "SYSTEM_INTERNAL",
        "DECLARED_UNVERIFIED",
        "NONE",
    }
