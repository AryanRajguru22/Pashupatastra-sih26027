"""Slice 10.1C: role-scoped assignment (Person + Role + RailwayScope).

Pins that scope belongs to a role assignment context (never to a person
alone); that RoleAssignment is untouched and scope-free; that one person
can hold different roles with different scopes; that equivalent
assignments cannot coexist; and that ADMIN carries NO scope - represented
by absence, never by a wildcard.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import itertools

import pytest

from backend.app.identity import assignments as assignments_module
from backend.app.identity import scope_assignment as scope_assignment_module
from backend.app.identity.actor import HUMAN_ROLES, ActorRole
from backend.app.identity.assignments import AssignmentError, RoleAssignment
from backend.app.identity.person import ExternalIdentity, Person
from backend.app.identity.resource import ResourceLocation, ScopeMatch, match_scope
from backend.app.identity.scope import RailwayScope
from backend.app.identity.scope_assignment import (
    RoleScopeAssignment,
    scopes_for,
)

CORRIDOR = "CORR-NDLS-AGC"
S1, S2, S3, S4, S5, S6 = (
    "NDLS-NZM",
    "NZM-FDB",
    "FDB-PWL",
    "PWL-MTJ",
    "MTJ-RKM",
    "RKM-AGC",
)
OPERATIONAL = (ActorRole.WORKER, ActorRole.ENGINEER, ActorRole.AUTHORITY)


def _person(person_id: str = "P-000123") -> Person:
    return Person(
        person_id=person_id,
        identity=ExternalIdentity("novaforge", f"subject-{person_id}"),
    )


def _rsa(role, sections, person_id: str = "P-000123", corridor: str = CORRIDOR):
    return RoleScopeAssignment(
        person_id=person_id, role=role, scope=RailwayScope(corridor, sections)
    )


def _reason(excinfo) -> str:
    return excinfo.value.reason


# ----------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------


def test_person_role_scope_assignment_preserves_values():
    scope = RailwayScope(CORRIDOR, [S2, S3])
    assignment = RoleScopeAssignment("P-000123", ActorRole.AUTHORITY, scope)

    assert assignment.person_id == "P-000123"
    assert assignment.role is ActorRole.AUTHORITY
    assert assignment.scope == scope


@pytest.mark.parametrize("role", OPERATIONAL)
def test_every_operational_role_can_carry_a_scope(role):
    assert _rsa(role, [S3]).role is role


def test_role_may_be_given_by_value_string():
    assert _rsa("ENGINEER", [S3]).role is ActorRole.ENGINEER


def test_assignment_is_immutable_and_hashable():
    assignment = _rsa(ActorRole.WORKER, [S3])

    assert RoleScopeAssignment.__dataclass_params__.frozen
    with pytest.raises(dataclasses.FrozenInstanceError):
        assignment.role = ActorRole.AUTHORITY  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        assignment.scope = RailwayScope(CORRIDOR, [S1])  # type: ignore[misc]
    assert len({assignment, _rsa(ActorRole.WORKER, [S3])}) == 1


def test_assignment_fields_are_exactly_person_role_scope():
    assert [f.name for f in dataclasses.fields(RoleScopeAssignment)] == [
        "person_id",
        "role",
        "scope",
    ]


@pytest.mark.parametrize("bad", ["", " P", "P ", "SYSTEM", "SYSTEM-1", 5, None, "a b"])
def test_invalid_person_id_is_rejected_with_the_role_assignment_reason(bad):
    with pytest.raises(AssignmentError) as excinfo:
        RoleScopeAssignment(bad, ActorRole.WORKER, RailwayScope(CORRIDOR, [S3]))

    assert _reason(excinfo) == "INVALID_PERSON_ID"


@pytest.mark.parametrize(
    "bad", [ActorRole.SYSTEM, ActorRole.UNIDENTIFIED, "SYSTEM", "ROOT", None, 3]
)
def test_non_assignable_roles_are_rejected(bad):
    with pytest.raises(AssignmentError) as excinfo:
        RoleScopeAssignment("P-000123", bad, RailwayScope(CORRIDOR, [S3]))

    assert _reason(excinfo) == "INVALID_ROLE"


@pytest.mark.parametrize(
    "bad",
    [None, "*", "ALL", CORRIDOR, {"corridor_id": CORRIDOR}, [S3], frozenset({S3})],
)
def test_scope_must_be_a_railway_scope(bad):
    with pytest.raises(AssignmentError) as excinfo:
        RoleScopeAssignment("P-000123", ActorRole.WORKER, bad)  # type: ignore[arg-type]

    assert _reason(excinfo) == "INVALID_SCOPE"


@pytest.mark.parametrize("role", OPERATIONAL)
def test_operational_role_without_a_scope_is_rejected_not_made_global(role):
    with pytest.raises(AssignmentError) as excinfo:
        RoleScopeAssignment("P-000123", role, None)  # type: ignore[arg-type]

    assert _reason(excinfo) == "INVALID_SCOPE"


# ----------------------------------------------------------------------
# ADMIN carries no scope
# ----------------------------------------------------------------------


def test_admin_cannot_be_given_a_scope_assignment():
    with pytest.raises(AssignmentError) as excinfo:
        RoleScopeAssignment(
            "P-000123", ActorRole.ADMIN, RailwayScope(CORRIDOR, [S1, S2, S3])
        )

    assert _reason(excinfo) == "ADMIN_HAS_NO_SCOPE"


def test_admin_cannot_be_given_a_scope_covering_every_section():
    everything = [S1, S2, S3, S4, S5, S6]

    with pytest.raises(AssignmentError) as excinfo:
        RoleScopeAssignment(
            "P-000123", ActorRole.ADMIN, RailwayScope(CORRIDOR, everything)
        )

    assert _reason(excinfo) == "ADMIN_HAS_NO_SCOPE"


def test_admin_cannot_be_given_a_wildcard_scope():
    from backend.app.identity.scope import ScopeError

    with pytest.raises(ScopeError):
        RailwayScope(CORRIDOR, ["*"])
    with pytest.raises(ScopeError):
        RailwayScope(CORRIDOR, ["ALL"])
    with pytest.raises(AssignmentError):
        RoleScopeAssignment("P-000123", ActorRole.ADMIN, "*")  # type: ignore[arg-type]


def test_admin_is_still_a_valid_role_assignment_without_scope():
    assignment = RoleAssignment("P-000123", ActorRole.ADMIN)

    assert assignment.role is ActorRole.ADMIN
    assert not hasattr(assignment, "scope")


def test_admin_has_no_scopes_even_with_other_roles_present():
    person = _person()
    collection = [_rsa(ActorRole.AUTHORITY, [S3]), _rsa(ActorRole.ENGINEER, [S2, S3])]

    assert scopes_for(person, ActorRole.ADMIN, collection) == ()


def test_admin_has_no_scope_so_the_matcher_can_only_say_no_match():
    scopes = scopes_for(_person(), ActorRole.ADMIN, [])

    assert scopes == ()
    for section in (S1, S3, S6, "*", "ALL"):
        assert (
            match_scope(None, ResourceLocation(CORRIDOR, section))
            is ScopeMatch.NO_MATCH
        )


def test_module_exposes_no_global_wildcard_or_admin_scope_names():
    public = {n.upper() for n in dir(scope_assignment_module) if not n.startswith("_")}

    assert not public & {"ALL_SECTIONS", "GLOBAL_SCOPE", "ADMIN_SCOPE", "WILDCARD"}


# ----------------------------------------------------------------------
# Role and scope stay separate concepts
# ----------------------------------------------------------------------


def test_role_assignment_remains_scope_free_and_unchanged():
    assert [f.name for f in dataclasses.fields(RoleAssignment)] == [
        "person_id",
        "role",
    ]
    assert RoleAssignment.__dataclass_params__.frozen
    assert not hasattr(RoleAssignment("P-000123", ActorRole.WORKER), "scope")


def test_role_scope_assignment_is_not_a_role_assignment_subclass():
    assert not issubclass(RoleScopeAssignment, RoleAssignment)
    assert RoleScopeAssignment("P-000123", ActorRole.WORKER, RailwayScope(CORRIDOR, [S3])) != (
        RoleAssignment("P-000123", ActorRole.WORKER)
    )


def test_role_assignment_view_carries_only_person_and_role():
    assignment = _rsa(ActorRole.AUTHORITY, [S2, S3])

    view = assignment.role_assignment

    assert view == RoleAssignment("P-000123", ActorRole.AUTHORITY)
    assert type(view) is RoleAssignment


def test_scope_is_not_a_person_level_concept():
    assert not hasattr(Person, "scope")
    assert "scope" not in {f.name for f in dataclasses.fields(Person)}
    public = {n for n in dir(scope_assignment_module) if not n.startswith("_")}
    assert "PersonScope" not in public


def test_same_person_holds_different_roles_with_different_scopes():
    person = _person()
    worker = _rsa(ActorRole.WORKER, [S3])
    engineer = _rsa(ActorRole.ENGINEER, [S2, S3, S4])
    authority = _rsa(ActorRole.AUTHORITY, [S3])
    collection = [engineer, worker, authority]

    assert scopes_for(person, ActorRole.WORKER, collection) == (
        RailwayScope(CORRIDOR, [S3]),
    )
    assert scopes_for(person, ActorRole.ENGINEER, collection) == (
        RailwayScope(CORRIDOR, [S2, S3, S4]),
    )
    assert scopes_for(person, ActorRole.AUTHORITY, collection) == (
        RailwayScope(CORRIDOR, [S3]),
    )


def test_role_scopes_are_never_merged_or_inherited_across_roles():
    person = _person()
    collection = [_rsa(ActorRole.WORKER, [S3]), _rsa(ActorRole.ENGINEER, [S2, S4])]

    worker_scopes = scopes_for(person, ActorRole.WORKER, collection)

    assert worker_scopes == (RailwayScope(CORRIDOR, [S3]),)
    assert all(S2 not in s.section_ids for s in worker_scopes)


def test_scopes_are_isolated_between_people():
    collection = [
        _rsa(ActorRole.WORKER, [S3], person_id="P-000001"),
        _rsa(ActorRole.WORKER, [S5], person_id="P-000002"),
    ]

    assert scopes_for(_person("P-000001"), ActorRole.WORKER, collection) == (
        RailwayScope(CORRIDOR, [S3]),
    )
    assert scopes_for(_person("P-000002"), ActorRole.WORKER, collection) == (
        RailwayScope(CORRIDOR, [S5]),
    )
    assert scopes_for(_person("P-000003"), ActorRole.WORKER, collection) == ()


def test_a_role_the_person_has_no_scope_for_yields_no_scope_never_a_default():
    person = _person()
    collection = [_rsa(ActorRole.WORKER, [S3])]

    assert scopes_for(person, ActorRole.AUTHORITY, collection) == ()


def test_scopes_for_string_role_and_invalid_role():
    person = _person()
    collection = [_rsa(ActorRole.ENGINEER, [S2])]

    assert scopes_for(person, "ENGINEER", collection) == (RailwayScope(CORRIDOR, [S2]),)
    with pytest.raises(AssignmentError) as excinfo:
        scopes_for(person, ActorRole.SYSTEM, collection)
    assert _reason(excinfo) == "INVALID_ROLE"


def test_scopes_for_requires_a_person():
    with pytest.raises(AssignmentError) as excinfo:
        scopes_for("P-000123", ActorRole.WORKER, [])  # type: ignore[arg-type]

    assert _reason(excinfo) == "INVALID_ASSIGNMENT"


def test_same_person_and_role_in_two_corridors_are_two_distinct_scopes():
    person = _person()
    a = _rsa(ActorRole.ENGINEER, [S2])
    b = _rsa(ActorRole.ENGINEER, [S2], corridor="CORR-OTHER")

    scopes = scopes_for(person, ActorRole.ENGINEER, [a, b])

    assert set(scopes) == {a.scope, b.scope}
    assert len(scopes) == 2


def test_scopes_for_returns_a_deterministic_order_independent_of_input_order():
    person = _person()
    a = _rsa(ActorRole.ENGINEER, [S2])
    b = _rsa(ActorRole.ENGINEER, [S4, S5])
    c = _rsa(ActorRole.ENGINEER, [S2], corridor="CORR-OTHER")

    results = {
        scopes_for(person, ActorRole.ENGINEER, list(order))
        for order in itertools.permutations([a, b, c])
    }

    assert len(results) == 1


# ----------------------------------------------------------------------
# Duplicates and canonical equality
# ----------------------------------------------------------------------


def test_section_order_does_not_change_assignment_equality():
    assert _rsa(ActorRole.AUTHORITY, [S3, S2]) == _rsa(ActorRole.AUTHORITY, [S2, S3])
    assert hash(_rsa(ActorRole.AUTHORITY, [S3, S2])) == hash(
        _rsa(ActorRole.AUTHORITY, [S2, S3])
    )


def test_equivalent_duplicate_assignment_is_rejected():
    person = _person()

    with pytest.raises(AssignmentError) as excinfo:
        scopes_for(
            person,
            ActorRole.AUTHORITY,
            [_rsa(ActorRole.AUTHORITY, [S2, S3]), _rsa(ActorRole.AUTHORITY, [S3, S2])],
        )

    assert _reason(excinfo) == "DUPLICATE_ASSIGNMENT"


def test_duplicate_anywhere_in_the_collection_is_rejected_even_for_other_people():
    duplicate = _rsa(ActorRole.WORKER, [S3], person_id="P-000009")

    with pytest.raises(AssignmentError) as excinfo:
        scopes_for(_person(), ActorRole.AUTHORITY, [duplicate, duplicate])

    assert _reason(excinfo) == "DUPLICATE_ASSIGNMENT"


def test_same_role_with_different_scopes_is_not_a_duplicate():
    person = _person()

    scopes = scopes_for(
        person,
        ActorRole.AUTHORITY,
        [_rsa(ActorRole.AUTHORITY, [S2]), _rsa(ActorRole.AUTHORITY, [S2, S3])],
    )

    assert len(scopes) == 2


def test_same_scope_under_different_roles_is_not_a_duplicate():
    person = _person()
    collection = [_rsa(ActorRole.WORKER, [S3]), _rsa(ActorRole.AUTHORITY, [S3])]

    assert len(scopes_for(person, ActorRole.WORKER, collection)) == 1
    assert len(scopes_for(person, ActorRole.AUTHORITY, collection)) == 1


def test_collection_members_must_be_role_scope_assignments():
    person = _person()

    for bad in (RoleAssignment("P-000123", ActorRole.WORKER), "x", None, object()):
        with pytest.raises(AssignmentError) as excinfo:
            scopes_for(person, ActorRole.WORKER, [bad])  # type: ignore[list-item]
        assert _reason(excinfo) == "INVALID_ASSIGNMENT"


def test_scopes_for_accepts_any_iterable_and_does_not_consume_state():
    person = _person()
    collection = [_rsa(ActorRole.WORKER, [S3])]

    assert scopes_for(person, ActorRole.WORKER, iter(collection)) == (
        RailwayScope(CORRIDOR, [S3]),
    )
    assert scopes_for(person, ActorRole.WORKER, tuple(collection)) == (
        RailwayScope(CORRIDOR, [S3]),
    )
    assert collection == [_rsa(ActorRole.WORKER, [S3])]


# ----------------------------------------------------------------------
# Scope from the assignment feeds the pure matcher (no authorization)
# ----------------------------------------------------------------------


def test_assignment_scope_drives_matching_only_inside_its_own_role_context():
    person = _person()
    collection = [
        _rsa(ActorRole.AUTHORITY, [S3]),
        _rsa(ActorRole.ENGINEER, [S2, S3, S4]),
    ]

    (authority_scope,) = scopes_for(person, ActorRole.AUTHORITY, collection)
    (engineer_scope,) = scopes_for(person, ActorRole.ENGINEER, collection)
    at_s2 = ResourceLocation(CORRIDOR, S2)

    assert match_scope(authority_scope, at_s2) is ScopeMatch.NO_MATCH
    assert match_scope(engineer_scope, at_s2) is ScopeMatch.MATCH


# ----------------------------------------------------------------------
# Isolation
# ----------------------------------------------------------------------


def _imports(module) -> set:
    tree = ast.parse(inspect.getsource(module))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _names(module) -> set:
    tree = ast.parse(inspect.getsource(module))
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }


def test_module_never_references_actor_construction_assurance_or_job_action():
    assert not _names(scope_assignment_module) & {
        "Actor",
        "human_actor",
        "system_actor",
        "unidentified_actor",
        "IdentityAssurance",
        "JobAction",
        "AuthorizationPolicy",
    }


def test_module_imports_no_authorization_authentication_persistence_or_novaforge():
    imported = _imports(scope_assignment_module)
    top = {name.split(".")[0] for name in imported}

    assert "backend.app.identity.authorization" not in imported
    assert not any(n.startswith("backend.app.jobs") for n in imported)
    assert not any(n.startswith("backend.app.api") for n in imported)
    assert not any(n.startswith("backend.app.persistence") for n in imported)
    assert not any("novaforge" in n.lower() for n in imported)
    assert not top & {
        "sqlite3",
        "jwt",
        "jose",
        "requests",
        "httpx",
        "fastapi",
        "authlib",
        "sqlalchemy",
    }


def test_module_holds_no_global_mutable_state():
    mutable = [
        name
        for name, value in vars(scope_assignment_module).items()
        if not name.startswith("__") and isinstance(value, (list, dict, set))
    ]

    assert mutable == []


def test_role_assignments_module_is_still_scope_free():
    tree = ast.parse(inspect.getsource(assignments_module))
    imported = {
        n.module
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and n.module
    }

    assert "backend.app.identity.scope" not in imported
    assert "backend.app.identity.scope_assignment" not in imported
    assert "backend.app.identity.resource" not in imported


def test_actor_role_and_human_roles_are_unchanged():
    assert [r.value for r in ActorRole] == [
        "WORKER",
        "ENGINEER",
        "AUTHORITY",
        "ADMIN",
        "SYSTEM",
        "UNIDENTIFIED",
    ]
    assert HUMAN_ROLES == frozenset(
        {ActorRole.WORKER, ActorRole.ENGINEER, ActorRole.AUTHORITY, ActorRole.ADMIN}
    )


def test_identity_package_public_surface_does_not_export_scope_types():
    import backend.app.identity as identity

    for name in ("RoleScopeAssignment", "RailwayScope", "ScopeMatch", "match_scope"):
        assert name not in identity.__all__
        assert not hasattr(identity, name)
