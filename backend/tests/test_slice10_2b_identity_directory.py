"""Slice 10.2b: the immutable in-memory identity directory."""

from __future__ import annotations

import ast
import inspect
import itertools
import random
import re
import types
from pathlib import Path

import pytest

import backend.app.identity as identity_package
from backend.app.identity import directory as directory_module
from backend.app.identity.actor import ActorRole, human_actor
from backend.app.identity.assignments import (
    DUPLICATE_ASSIGNMENT,
    INVALID_ASSIGNMENT,
    INVALID_ROLE,
    NO_ASSIGNMENT,
    ROLE_NOT_ASSIGNED,
    ROLE_SELECTION_REQUIRED,
    AssignmentError,
    RoleAssignment,
    select_role,
)
from backend.app.identity.authorization import AuthorizationDenied, JobAction
from backend.app.identity.directory import (
    DUPLICATE_EXTERNAL_IDENTITY,
    DUPLICATE_PERSON,
    DUPLICATE_PERSON_ID,
    IDENTITY_NOT_ENROLLED,
    INVALID_DIRECTORY_INPUT,
    INVALID_IDENTITY_ARGUMENT,
    INVALID_PERSON,
    PERSON_NOT_ENROLLED,
    ROLE_NOT_HELD,
    UNKNOWN_PERSON,
    DirectoryError,
    IdentityDirectory,
)
from backend.app.identity.person import ExternalIdentity, Person
from backend.app.identity.policy import EnforcingPolicy, StaticScopeDirectory
from backend.app.identity.scope import RailwayScope
from backend.app.identity.scope_assignment import (
    ADMIN_HAS_NO_SCOPE,
    RoleScopeAssignment,
)

W, E, A, ADM = ActorRole.WORKER, ActorRole.ENGINEER, ActorRole.AUTHORITY, ActorRole.ADMIN
CORRIDOR = "CORR-1"


def ident(subject: str, provider: str = "novaforge") -> ExternalIdentity:
    return ExternalIdentity(provider, subject)


def per(person_id: str, subject: str | None = None, provider: str = "novaforge") -> Person:
    return Person(person_id, ident(subject or f"sub-{person_id}", provider))


def scope(*sections: str, corridor: str = CORRIDOR) -> RailwayScope:
    return RailwayScope(corridor, list(sections))


def rsa(person_id: str, role: ActorRole, *sections: str) -> RoleScopeAssignment:
    return RoleScopeAssignment(person_id, role, scope(*sections))


def dataset():
    people = [per("P-1"), per("P-2"), per("P-3"), per("P-ADMIN")]
    roles = [
        RoleAssignment("P-1", W),
        RoleAssignment("P-2", W),
        RoleAssignment("P-2", E),
        RoleAssignment("P-3", A),
        RoleAssignment("P-ADMIN", ADM),
    ]
    scopes = [
        rsa("P-1", W, "S1", "S2"),
        rsa("P-2", W, "S1"),
        rsa("P-2", E, "S9"),
        rsa("P-2", E, "S8"),
        rsa("P-3", A, "S1", "S2", "S3"),
    ]
    return people, roles, scopes


def built() -> IdentityDirectory:
    return IdentityDirectory(*dataset())


def bypass_admin_scope() -> RoleScopeAssignment:
    """An ADMIN RoleScopeAssignment that skips its own constructor guard."""

    obj = object.__new__(RoleScopeAssignment)
    object.__setattr__(obj, "person_id", "P-ADMIN")
    object.__setattr__(obj, "role", ADM)
    object.__setattr__(obj, "scope", scope("S1"))
    return obj


# ---------------------------------------------------------------- lookup


def test_known_identity_resolves_to_its_person():
    d = built()
    assert d.person_for(ident("sub-P-1")) == per("P-1")
    assert d.person_for(ident("sub-P-3")).person_id == "P-3"


def test_unknown_identity_is_not_enrolled():
    with pytest.raises(DirectoryError) as info:
        built().person_for(ident("nobody"))
    assert info.value.reason == IDENTITY_NOT_ENROLLED


def test_unknown_identity_error_does_not_echo_subject_or_provider():
    with pytest.raises(DirectoryError) as info:
        built().person_for(ident("very-secret-subject", "secret-provider"))
    text = str(info.value) + repr(info.value)
    assert "very-secret-subject" not in text
    assert "secret-provider" not in text


def test_changed_subject_is_not_enrolled():
    with pytest.raises(DirectoryError) as info:
        built().person_for(ident("sub-P-1-new"))
    assert info.value.reason == IDENTITY_NOT_ENROLLED


def test_changed_provider_is_not_enrolled():
    with pytest.raises(DirectoryError) as info:
        built().person_for(ident("sub-P-1", "other-provider"))
    assert info.value.reason == IDENTITY_NOT_ENROLLED


def test_subject_matching_is_case_sensitive():
    d = IdentityDirectory([per("P-1", "Alice")], [], [])
    assert d.person_for(ident("Alice")).person_id == "P-1"
    for variant in ("alice", "ALICE"):
        with pytest.raises(DirectoryError) as info:
            d.person_for(ident(variant))
        assert info.value.reason == IDENTITY_NOT_ENROLLED


def test_provider_matching_is_case_sensitive():
    d = IdentityDirectory([per("P-1", "s", "NovaForge")], [], [])
    with pytest.raises(DirectoryError):
        d.person_for(ident("s", "novaforge"))


def test_case_variant_identities_may_coexist():
    """Exact matching means case variants are distinct identities (approved)."""

    d = IdentityDirectory([per("P-1", "Alice"), per("P-2", "alice")], [], [])
    assert d.person_for(ident("Alice")).person_id == "P-1"
    assert d.person_for(ident("alice")).person_id == "P-2"


@pytest.mark.parametrize(
    "bad", [None, "novaforge", ("novaforge", "sub-P-1"), per("P-1"), {"a": 1}]
)
def test_person_for_rejects_non_external_identity(bad):
    with pytest.raises(DirectoryError) as info:
        built().person_for(bad)
    assert info.value.reason == INVALID_IDENTITY_ARGUMENT


def test_person_by_id():
    assert built().person("P-2") == per("P-2")


@pytest.mark.parametrize("bad", ["P-404", "", None, 7, "p-1", ["P-1"]])
def test_unknown_person_id(bad):
    with pytest.raises(DirectoryError) as info:
        built().person(bad)
    assert info.value.reason == PERSON_NOT_ENROLLED


# ------------------------------------------------- load-time invariants


def refusal(people=(), roles=(), scopes=()):
    with pytest.raises((DirectoryError, AssignmentError)) as info:
        IdentityDirectory(people, roles, scopes)
    return info.value


def test_duplicate_external_identity_refused():
    err = refusal([per("P-1", "same"), per("P-2", "same")])
    assert err.reason == DUPLICATE_EXTERNAL_IDENTITY
    assert "same" not in str(err)


def test_duplicate_person_id_refused():
    assert refusal([per("P-1", "a"), per("P-1", "b")]).reason == DUPLICATE_PERSON_ID


def test_duplicate_person_entry_refused():
    assert refusal([per("P-1"), per("P-1")]).reason == DUPLICATE_PERSON


def test_unknown_person_in_role_assignment_refused():
    err = refusal([per("P-1")], [RoleAssignment("P-404", W)])
    assert err.reason == UNKNOWN_PERSON


def test_unknown_person_in_scope_assignment_refused():
    err = refusal([per("P-1")], [RoleAssignment("P-1", W)], [rsa("P-404", W, "S1")])
    assert err.reason == UNKNOWN_PERSON


def test_scope_without_held_role_refused():
    assert refusal([per("P-1")], [], [rsa("P-1", W, "S1")]).reason == ROLE_NOT_HELD


def test_scope_under_a_different_held_role_refused():
    err = refusal([per("P-1")], [RoleAssignment("P-1", W)], [rsa("P-1", E, "S1")])
    assert err.reason == ROLE_NOT_HELD


def test_scope_for_role_held_by_someone_else_refused():
    err = refusal(
        [per("P-1"), per("P-2")],
        [RoleAssignment("P-2", E)],
        [rsa("P-1", E, "S1")],
    )
    assert err.reason == ROLE_NOT_HELD


def test_duplicate_role_assignment_refused_by_existing_validation():
    err = refusal([per("P-1")], [RoleAssignment("P-1", W), RoleAssignment("P-1", W)])
    assert isinstance(err, AssignmentError)
    assert err.reason == DUPLICATE_ASSIGNMENT


def test_duplicate_scope_assignment_refused_by_existing_validation():
    err = refusal(
        [per("P-1")],
        [RoleAssignment("P-1", W)],
        [rsa("P-1", W, "S1", "S2"), rsa("P-1", W, "S2", "S1")],
    )
    assert isinstance(err, AssignmentError)
    assert err.reason == DUPLICATE_ASSIGNMENT


@pytest.mark.parametrize("bad", [None, "P-1", ("P-1", W), per("P-1"), 3])
def test_invalid_role_assignment_entry_refused(bad):
    err = refusal([per("P-1")], [bad])
    assert isinstance(err, AssignmentError)
    assert err.reason == INVALID_ASSIGNMENT


@pytest.mark.parametrize("bad", [None, RoleAssignment("P-1", W), "x", 1])
def test_invalid_scope_assignment_entry_refused(bad):
    err = refusal([per("P-1")], [RoleAssignment("P-1", W)], [bad])
    assert isinstance(err, AssignmentError)
    assert err.reason == INVALID_ASSIGNMENT


@pytest.mark.parametrize("bad", [None, "P-1", ident("x"), RoleAssignment("P-1", W), 5])
def test_invalid_person_entry_refused(bad):
    assert refusal([bad]).reason == INVALID_PERSON


@pytest.mark.parametrize("field", ["people", "role_assignments", "scope_assignments"])
@pytest.mark.parametrize("bad", [None, 5, "abc", b"abc"])
def test_non_collection_input_refused(field, bad):
    with pytest.raises(DirectoryError) as info:
        IdentityDirectory(**{field: bad})
    assert info.value.reason == INVALID_DIRECTORY_INPUT


def test_one_bad_entry_fails_the_entire_construction():
    people, roles, scopes = dataset()
    with pytest.raises(DirectoryError):
        IdentityDirectory(people + [per("P-9", "sub-P-1")], roles, scopes)


def test_empty_directory_is_valid_and_grants_nothing():
    d = IdentityDirectory()
    assert d.people == () and d.role_assignments == () and d.scope_assignments == ()
    assert IdentityDirectory([], [], []).people == ()
    with pytest.raises(DirectoryError) as info:
        d.person_for(ident("x"))
    assert info.value.reason == IDENTITY_NOT_ENROLLED
    with pytest.raises(DirectoryError):
        d.person("P-1")
    assert d.scopes_for_actor("P-1", W) == ()


def test_people_without_assignments_are_valid_and_hold_nothing():
    d = IdentityDirectory([per("P-1")])
    with pytest.raises(AssignmentError) as info:
        d.select_role(per("P-1"))
    assert info.value.reason == NO_ASSIGNMENT
    assert d.assignments_for(per("P-1")) == ()


def test_accepts_generators_and_other_iterables():
    people, roles, scopes = dataset()
    d = IdentityDirectory(iter(people), (r for r in roles), tuple(scopes))
    assert len(d.people) == 4


# ------------------------------------------------- mutation-style tests
#
# Each test builds a copy of directory.py with one refusal neutralised and
# proves (a) the real module refuses the bad dataset with that reason and
# (b) the mutant does not, so removing the invariant fails the suite.


def mutant(reason_const: str, occurrence: int = 0) -> types.ModuleType:
    source = Path(inspect.getsourcefile(directory_module)).read_text(encoding="utf-8")
    pattern = re.compile(r"raise DirectoryError\(\s*" + reason_const + r"\b")
    matches = list(pattern.finditer(source))
    assert len(matches) > occurrence, f"no raise site {occurrence} for {reason_const}"
    m = matches[occurrence]
    neutralised = source[m.start() : m.end()].replace(
        "raise DirectoryError(", "_neutralised = DirectoryError(", 1
    )
    mutated = source[: m.start()] + neutralised + source[m.end() :]
    module = types.ModuleType("mutant_directory")
    module.__file__ = "<mutant>"
    exec(compile(mutated, "<mutant>", "exec"), module.__dict__)
    return module


def bad_datasets():
    """(reason, occurrence, people, roles, scopes)."""

    p1 = per("P-1")
    return [
        (DUPLICATE_EXTERNAL_IDENTITY, 0, [per("P-1", "s"), per("P-2", "s")], [], []),
        (DUPLICATE_PERSON_ID, 0, [per("P-1", "a"), per("P-1", "b")], [], []),
        (DUPLICATE_PERSON, 0, [p1, p1], [], []),
        (INVALID_PERSON, 0, [None], [], []),
        (INVALID_DIRECTORY_INPUT, 0, None, [], []),
        (UNKNOWN_PERSON, 0, [p1], [RoleAssignment("P-9", W)], []),
        (UNKNOWN_PERSON, 1, [p1], [RoleAssignment("P-1", W)], [rsa("P-9", W, "S1")]),
        (ROLE_NOT_HELD, 0, [p1], [], [rsa("P-1", W, "S1")]),
        (ROLE_NOT_HELD, 0, [p1], [RoleAssignment("P-1", W)], [rsa("P-1", E, "S1")]),
        (
            ADMIN_HAS_NO_SCOPE,
            0,
            [per("P-ADMIN")],
            [RoleAssignment("P-ADMIN", ADM)],
            [bypass_admin_scope()],
        ),
    ]


_CASES = bad_datasets()


@pytest.mark.parametrize(
    "reason,occurrence,people,roles,scopes",
    _CASES,
    ids=[f"{c[0]}-{c[1]}-{i}" for i, c in enumerate(_CASES)],
)
def test_each_load_time_refusal_is_load_bearing(reason, occurrence, people, roles, scopes):
    with pytest.raises(DirectoryError) as info:
        IdentityDirectory(people, roles, scopes)
    assert info.value.reason == reason

    mutated = mutant(reason, occurrence)
    try:
        mutated.IdentityDirectory(people, roles, scopes)
    except Exception as exc:  # noqa: BLE001 - only the *same* refusal is disallowed
        assert getattr(exc, "reason", None) != reason


def test_role_not_held_mutant_accepts_what_the_real_directory_refuses():
    args = ([per("P-1")], [RoleAssignment("P-1", W)], [rsa("P-1", E, "S1")])
    leaked = mutant(ROLE_NOT_HELD).IdentityDirectory(*args)
    assert leaked.scope_assignments  # the mutant loaded the unheld-role scope
    with pytest.raises(DirectoryError) as info:
        IdentityDirectory(*args)
    assert info.value.reason == ROLE_NOT_HELD


def test_lookup_time_guard_never_reports_scope_for_an_unheld_role():
    """Defence in depth: even a mutant that loaded the bad row reports ()."""

    args = ([per("P-1")], [RoleAssignment("P-1", W)], [rsa("P-1", E, "S1")])
    leaked = mutant(ROLE_NOT_HELD).IdentityDirectory(*args)
    assert leaked.scopes_for_actor("P-1", E) == ()


# ------------------------------------------------------- role selection


def test_assignments_for_returns_the_directorys_assignments():
    d = built()
    assert d.assignments_for(per("P-2")) == (
        RoleAssignment("P-2", E),
        RoleAssignment("P-2", W),
    )
    assert d.assignments_for(per("P-1")) == (RoleAssignment("P-1", W),)


def test_assignments_for_unenrolled_person_refused():
    with pytest.raises(DirectoryError) as info:
        built().assignments_for(per("P-404"))
    assert info.value.reason == PERSON_NOT_ENROLLED


def test_person_sharing_an_id_but_not_the_identity_is_not_enrolled():
    impostor = Person("P-3", ident("someone-else"))
    d = built()
    with pytest.raises(DirectoryError) as info:
        d.select_role(impostor)
    assert info.value.reason == PERSON_NOT_ENROLLED
    with pytest.raises(DirectoryError):
        d.assignments_for(impostor)


def test_assignments_for_non_person_refused_by_existing_validation():
    with pytest.raises(AssignmentError) as info:
        built().assignments_for("P-1")
    assert info.value.reason == INVALID_ASSIGNMENT


def test_select_role_single_assignment():
    assert built().select_role(per("P-1")) == RoleAssignment("P-1", W)


def test_select_role_requires_selection_when_several():
    with pytest.raises(AssignmentError) as info:
        built().select_role(per("P-2"))
    assert info.value.reason == ROLE_SELECTION_REQUIRED


def test_select_role_requested_role_held():
    d = built()
    assert d.select_role(per("P-2"), E) == RoleAssignment("P-2", E)
    assert d.select_role(per("P-2"), "WORKER") == RoleAssignment("P-2", W)


def test_select_role_not_assigned():
    with pytest.raises(AssignmentError) as info:
        built().select_role(per("P-1"), E)
    assert info.value.reason == ROLE_NOT_ASSIGNED


def test_select_role_no_assignment():
    d = IdentityDirectory([per("P-1")])
    with pytest.raises(AssignmentError) as info:
        d.select_role(per("P-1"))
    assert info.value.reason == NO_ASSIGNMENT


@pytest.mark.parametrize("bad", ["NOPE", ActorRole.SYSTEM, ActorRole.UNIDENTIFIED, 5])
def test_select_role_invalid_role(bad):
    with pytest.raises(AssignmentError) as info:
        built().select_role(per("P-1"), bad)
    assert info.value.reason == INVALID_ROLE


def test_select_role_delegates_to_the_existing_implementation(monkeypatch):
    calls = []

    def spy(person, assignments, requested_role=None):
        calls.append((person, tuple(assignments), requested_role))
        return select_role(person, assignments, requested_role)

    monkeypatch.setattr(directory_module, "_select_role", spy)
    d = built()
    result = d.select_role(per("P-2"), E)
    assert result == RoleAssignment("P-2", E)
    assert len(calls) == 1
    assert calls[0][0] == per("P-2") and calls[0][2] is E
    assert calls[0][1] == d.role_assignments


def test_select_role_method_raises_no_assignment_errors_itself():
    tree = ast.parse(inspect.getsource(directory_module))
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "IdentityDirectory")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "select_role")
    for raised in (n for n in ast.walk(method) if isinstance(n, ast.Raise)):
        assert not (
            isinstance(raised.exc, ast.Call)
            and getattr(raised.exc.func, "id", "") == "AssignmentError"
        )


# ---------------------------------------------------------------- scope


def test_scopes_for_actor_returns_role_scopes_in_canonical_order():
    d = built()
    assert d.scopes_for_actor("P-1", W) == (scope("S1", "S2"),)
    assert d.scopes_for_actor("P-2", E) == (scope("S8"), scope("S9"))


def test_scope_isolation_between_people():
    d = built()
    assert d.scopes_for_actor("P-1", W) == (scope("S1", "S2"),)
    assert d.scopes_for_actor("P-2", W) == (scope("S1"),)


def test_no_cross_role_scope_leakage():
    d = built()
    assert d.scopes_for_actor("P-2", W) == (scope("S1"),)
    assert d.scopes_for_actor("P-2", E) == (scope("S8"), scope("S9"))
    assert d.scopes_for_actor("P-2", A) == ()
    assert d.scopes_for_actor("P-1", E) == ()
    assert d.scopes_for_actor("P-3", W) == ()


def test_role_held_without_scope_yields_empty_never_wildcard():
    d = IdentityDirectory([per("P-1")], [RoleAssignment("P-1", W)])
    assert d.scopes_for_actor("P-1", W) == ()


@pytest.mark.parametrize(
    "actor_id,role",
    [
        ("P-404", W),
        ("", W),
        (None, W),
        (7, W),
        (["P-1"], W),
        ("P-1", ActorRole.SYSTEM),
        ("P-1", ActorRole.UNIDENTIFIED),
        ("P-1", "WORKER"),
        ("P-1", None),
        ("P-1", E),
    ],
)
def test_scopes_for_actor_fails_closed(actor_id, role):
    assert built().scopes_for_actor(actor_id, role) == ()


def test_satisfies_the_scope_directory_protocol():
    d = built()
    policy = EnforcingPolicy(d, topology=[(CORRIDOR, ["S1", "S2", "S3"])])
    assert policy.enforcing is True


# ---------------------------------------------------------------- ADMIN


def test_admin_may_be_held_as_a_role_assignment_only():
    d = built()
    assert d.select_role(per("P-ADMIN")) == RoleAssignment("P-ADMIN", ADM)
    assert d.scopes_for_actor("P-ADMIN", ADM) == ()


def test_admin_scope_assignment_cannot_even_be_constructed():
    with pytest.raises(AssignmentError) as info:
        RoleScopeAssignment("P-ADMIN", ADM, scope("S1"))
    assert info.value.reason == ADMIN_HAS_NO_SCOPE


def test_admin_scope_refused_at_load_even_if_constructor_guard_bypassed():
    err = refusal([per("P-ADMIN")], [RoleAssignment("P-ADMIN", ADM)], [bypass_admin_scope()])
    assert err.reason == ADMIN_HAS_NO_SCOPE


def test_admin_holder_with_other_role_keeps_scope_only_for_that_role():
    d = IdentityDirectory(
        [per("P-1")],
        [RoleAssignment("P-1", ADM), RoleAssignment("P-1", W)],
        [rsa("P-1", W, "S1")],
    )
    assert d.scopes_for_actor("P-1", ADM) == ()
    assert d.scopes_for_actor("P-1", W) == (scope("S1"),)


@pytest.mark.parametrize("action", list(JobAction))
def test_admin_is_denied_every_job_action_under_enforcing_policy(action):
    policy = EnforcingPolicy(built(), topology=[(CORRIDOR, ["S1", "S2", "S3"])])
    with pytest.raises(AuthorizationDenied):
        policy.authorize(human_actor("P-ADMIN", ADM), action)


def test_directory_exposes_no_management_surface():
    public = {n for n in dir(IdentityDirectory) if not n.startswith("_")}
    assert public == {
        "people",
        "role_assignments",
        "scope_assignments",
        "person_for",
        "person",
        "assignments_for",
        "select_role",
        "scopes_for_actor",
    }


# ------------------------------------- determinism / copy / immutability


def test_construction_is_deterministic_regardless_of_input_order():
    people, roles, scopes = dataset()
    reference = IdentityDirectory(people, roles, scopes)
    rng = random.Random(1234)

    for _ in range(25):
        p, r, s = list(people), list(roles), list(scopes)
        rng.shuffle(p)
        rng.shuffle(r)
        rng.shuffle(s)
        other = IdentityDirectory(p, r, s)
        assert other.people == reference.people
        assert other.role_assignments == reference.role_assignments
        assert other.scope_assignments == reference.scope_assignments
        for pid, role in itertools.product(["P-1", "P-2", "P-3", "P-ADMIN"], list(ActorRole)):
            assert other.scopes_for_actor(pid, role) == reference.scopes_for_actor(pid, role)


def test_refusal_is_order_independent():
    a, b = per("P-1", "same"), per("P-2", "same")
    for order in ([a, b], [b, a]):
        with pytest.raises(DirectoryError) as info:
            IdentityDirectory(order)
        assert info.value.reason == DUPLICATE_EXTERNAL_IDENTITY


def test_inputs_are_defensively_copied():
    people, roles, scopes = dataset()
    d = IdentityDirectory(people, roles, scopes)
    people.clear()
    roles.clear()
    scopes.clear()
    assert len(d.people) == 4
    assert d.person_for(ident("sub-P-1")).person_id == "P-1"
    assert d.select_role(per("P-1")) == RoleAssignment("P-1", W)
    assert d.scopes_for_actor("P-1", W) == (scope("S1", "S2"),)


def test_exposed_state_is_immutable_tuples():
    d = built()
    for value in (d.people, d.role_assignments, d.scope_assignments):
        assert isinstance(value, tuple)
    with pytest.raises(AttributeError):
        d.people.append(per("P-9"))  # type: ignore[attr-defined]


def test_instance_is_read_only():
    d = built()
    with pytest.raises(AttributeError):
        d.people = ()  # type: ignore[misc]
    with pytest.raises(AttributeError):
        d._people = ()
    with pytest.raises(AttributeError):
        d.new_attribute = 1  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        del d._people


def test_internal_mappings_are_read_only():
    d = built()
    with pytest.raises(TypeError):
        d._by_identity[("x", "y")] = per("P-9")  # type: ignore[index]
    with pytest.raises(TypeError):
        d._by_id["P-9"] = per("P-9")  # type: ignore[index]
    with pytest.raises(TypeError):
        d._scopes[("P-1", W)] = ()  # type: ignore[index]


def test_lookups_do_not_alter_the_directory():
    d = built()
    d.scopes_for_actor("P-2", E)
    d.assignments_for(per("P-2"))
    assert d.people == built().people
    assert d.scope_assignments == built().scope_assignments


# ------------------------------------------------------ AST boundary


FORBIDDEN_MODULE_PARTS = {
    "identity_assertion",
    "trusted_keys",
    "cryptography",
    "jwt",
    "sqlite3",
    "persistence",
    "jobs",
    "api",
    "_jose",
    "os",
    "pathlib",
    "io",
    "json",
    "yaml",
    "tomllib",
    "http",
    "requests",
    "urllib",
    "fastapi",
}
FORBIDDEN_NAMES = {
    "Actor",
    "human_actor",
    "system_actor",
    "unidentified_actor",
    "IdentityAssurance",
    "AUTHENTICATED",
    "open",
    "identity_assertion",
    "trusted_keys",
    "sqlite3",
}
ALLOWED_IMPORTS = {
    "__future__",
    "types",
    "typing",
    "backend.app.identity.actor",
    "backend.app.identity.assignments",
    "backend.app.identity.person",
    "backend.app.identity.scope",
    "backend.app.identity.scope_assignment",
}


def _tree():
    return ast.parse(inspect.getsource(directory_module))


def _imported_modules():
    mods = []
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods.append(node.module or "")
    return mods


def test_directory_imports_no_forbidden_module():
    for mod in _imported_modules():
        assert not (set(mod.split(".")) & FORBIDDEN_MODULE_PARTS), mod


def test_directory_only_imports_the_identity_data_modules():
    for mod in _imported_modules():
        assert mod in ALLOWED_IMPORTS, mod


def test_directory_references_no_forbidden_name():
    tree = _tree()
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    imported = {
        a.asname or a.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in n.names
    }
    assert not (FORBIDDEN_NAMES & (names | attrs | imported))


def test_directory_module_has_no_top_level_side_effects():
    for node in _tree().body:
        assert isinstance(
            node,
            (ast.Expr, ast.Import, ast.ImportFrom, ast.Assign, ast.ClassDef, ast.FunctionDef),
        )
        if isinstance(node, ast.Expr):
            assert isinstance(node.value, ast.Constant)  # docstring only


def test_boundary_test_would_catch_a_forbidden_import():
    """The AST check itself works: a synthetic forbidden import is detected."""

    tree = ast.parse("from backend.app.identity.identity_assertion import X\nimport sqlite3")
    mods = [
        (n.module if isinstance(n, ast.ImportFrom) else n.names[0].name)
        for n in ast.walk(tree)
        if isinstance(n, (ast.Import, ast.ImportFrom))
    ]
    assert all(set(m.split(".")) & FORBIDDEN_MODULE_PARTS for m in mods)


# ------------------------------------------- unchanged neighbours


def test_static_scope_directory_is_unchanged():
    d = StaticScopeDirectory(people=[per("P-1")], assignments=[rsa("P-1", W, "S1")])
    assert d.scopes_for_actor("P-1", W) == (scope("S1"),)
    assert d.scopes_for_actor("P-404", W) == ()
    assert d.scopes_for_actor("P-1", ActorRole.SYSTEM) == ()
    with pytest.raises(ValueError):
        StaticScopeDirectory(people=[per("P-1"), per("P-1")])
    assert not hasattr(d, "person_for")


def test_package_exports_are_unchanged():
    assert sorted(identity_package.__all__) == sorted(
        [
            "Actor",
            "ActorKind",
            "ActorRole",
            "AuthorizationDenied",
            "AuthorizationPolicy",
            "IdentityAssurance",
            "InvalidActorError",
            "JobAction",
            "OPTIMIZER",
            "SCORER",
            "SYSTEM",
            "UnenforcedPolicy",
            "human_actor",
            "system_actor",
            "unidentified_actor",
        ]
    )
    assert not hasattr(identity_package, "IdentityDirectory")
    assert not hasattr(identity_package, "DirectoryError")
    assert directory_module.__all__ == ["DirectoryError", "IdentityDirectory"]
