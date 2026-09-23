"""Slice 10.1A: Person and ExternalIdentity (identity-domain foundation).

Pins that an external identity is the case-sensitive pair
(identity_provider, subject), that a Person is a role-neutral,
immutable anchor pairing an opaque Pashupatastra person_id with that
identity, and that neither type can carry a role, assurance, token,
credential or profile field. No database, API or authentication is
involved.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect

import pytest

from backend.app.identity import person as person_module
from backend.app.identity.actor import (
    Actor,
    ActorRole,
    IdentityAssurance,
    human_actor,
)
from backend.app.identity.person import ExternalIdentity, Person, PersonError

SUBJECT = "0b7d3a52-4c1e-4f0a-9d6e-2f1c8a7b5e90"


def _identity(provider: str = "novaforge", subject: str = SUBJECT):
    return ExternalIdentity(identity_provider=provider, subject=subject)


# ----------------------------------------------------------------------
# ExternalIdentity
# ----------------------------------------------------------------------


def test_valid_external_identity_preserves_values():
    identity = _identity()

    assert identity.identity_provider == "novaforge"
    assert identity.subject == SUBJECT


@pytest.mark.parametrize("field", ["identity_provider", "subject"])
@pytest.mark.parametrize("bad", ["", " ", "   ", "\t", "\n"])
def test_empty_or_whitespace_only_values_are_rejected(field, bad):
    kwargs = {"identity_provider": "novaforge", "subject": SUBJECT}
    kwargs[field] = bad

    with pytest.raises(PersonError):
        ExternalIdentity(**kwargs)


@pytest.mark.parametrize("field", ["identity_provider", "subject"])
@pytest.mark.parametrize("bad", [None, 7, b"novaforge", ["a"]])
def test_non_string_values_are_rejected(field, bad):
    kwargs = {"identity_provider": "novaforge", "subject": SUBJECT}
    kwargs[field] = bad

    with pytest.raises(PersonError):
        ExternalIdentity(**kwargs)


@pytest.mark.parametrize("field", ["identity_provider", "subject"])
@pytest.mark.parametrize("bad", [" novaforge", "novaforge ", "nova\x00forge"])
def test_surrounding_whitespace_and_control_characters_are_rejected_not_stripped(
    field, bad
):
    kwargs = {"identity_provider": "novaforge", "subject": SUBJECT}
    kwargs[field] = bad

    with pytest.raises(PersonError):
        ExternalIdentity(**kwargs)


def test_values_are_never_silently_normalized():
    identity = ExternalIdentity(identity_provider="NovaForge", subject="AbC-123")

    assert identity.identity_provider == "NovaForge"
    assert identity.subject == "AbC-123"


def test_length_bounds_are_enforced():
    with pytest.raises(PersonError):
        ExternalIdentity(identity_provider="p" * 65, subject=SUBJECT)
    with pytest.raises(PersonError):
        ExternalIdentity(identity_provider="novaforge", subject="s" * 257)

    ExternalIdentity(identity_provider="p" * 64, subject="s" * 256)


def test_same_provider_and_subject_are_equal_and_hash_equal():
    a = _identity()
    b = _identity()

    assert a == b
    assert hash(a) == hash(b)
    assert len({a, b}) == 1


def test_different_providers_are_distinct():
    a = _identity(provider="novaforge")
    b = _identity(provider="otherprovider")

    assert a != b
    assert len({a, b}) == 2


def test_different_subjects_are_distinct():
    a = _identity(subject="user-1")
    b = _identity(subject="user-2")

    assert a != b
    assert len({a, b}) == 2


def test_identity_is_case_sensitive():
    assert _identity(provider="novaforge") != _identity(provider="NovaForge")
    assert _identity(subject="abc") != _identity(subject="ABC")


def test_provider_is_not_hardcoded_to_novaforge():
    assert _identity(provider="futureidp").identity_provider == "futureidp"


def test_external_identity_is_immutable():
    identity = _identity()

    with pytest.raises(dataclasses.FrozenInstanceError):
        identity.subject = "other"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        identity.identity_provider = "other"  # type: ignore[misc]


# ----------------------------------------------------------------------
# Person
# ----------------------------------------------------------------------


def test_valid_person_preserves_person_id_and_identity():
    identity = _identity()
    person = Person(person_id="P-000123", identity=identity)

    assert person.person_id == "P-000123"
    assert person.identity is identity
    assert person.identity.identity_provider == "novaforge"
    assert person.identity.subject == SUBJECT


def test_person_equality_is_deterministic_and_value_based():
    a = Person(person_id="P-000123", identity=_identity())
    b = Person(person_id="P-000123", identity=_identity())

    assert a == b
    assert hash(a) == hash(b)
    assert len({a, b}) == 1


def test_persons_differing_in_id_or_identity_are_distinct():
    base = Person(person_id="P-000123", identity=_identity())

    assert base != Person(person_id="P-000124", identity=_identity())
    assert base != Person(person_id="P-000123", identity=_identity(subject="x"))


def test_same_subject_under_different_provider_is_a_different_identity():
    a = Person(person_id="P-000001", identity=_identity(provider="novaforge"))
    b = Person(person_id="P-000002", identity=_identity(provider="futureidp"))

    assert a.identity != b.identity
    assert a != b


def test_person_is_immutable():
    person = Person(person_id="P-000123", identity=_identity())

    with pytest.raises(dataclasses.FrozenInstanceError):
        person.person_id = "P-999999"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        person.identity = _identity(subject="x")  # type: ignore[misc]


def test_person_requires_an_external_identity_instance():
    with pytest.raises(PersonError):
        Person(person_id="P-000123", identity=None)  # type: ignore[arg-type]
    with pytest.raises(PersonError):
        Person(
            person_id="P-000123",
            identity=("novaforge", SUBJECT),  # type: ignore[arg-type]
        )


# ----------------------------------------------------------------------
# person_id validity: exactly the existing actor_id rule
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "",
        " ",
        "P 1",
        "-P1",
        ".P1",
        "P" * 65,
        "SYSTEM",
        "SYSTEM:OPTIMIZER",
        "system-1",
        "P/1",
        None,
        123,
    ],
)
def test_person_id_rejected_exactly_as_actor_id_is(bad):
    with pytest.raises(PersonError):
        Person(person_id=bad, identity=_identity())


@pytest.mark.parametrize("good", ["P-000123", "p1", "0", "P" * 64, "A.b_c:d-e"])
def test_person_id_accepted_exactly_as_actor_id_is(good):
    # The same value must be a legal actor_id, so a later authenticated
    # Actor can carry it unchanged.
    human_actor(good, ActorRole.WORKER)

    assert Person(person_id=good, identity=_identity()).person_id == good


def test_person_id_is_opaque_and_independent_of_identity():
    # person_id is supplied opaque; it is never derived from the identity
    # or from any role, so the same id with any identity is preserved.
    a = Person(person_id="P-000123", identity=_identity(subject="x"))
    b = Person(person_id="P-000123", identity=_identity(subject="y"))

    assert a.person_id == b.person_id == "P-000123"


def test_person_id_does_not_change_with_role_concepts():
    # Role-neutral: the id is identical whichever role a later slice
    # assigns, because Person has no role input at all.
    person = Person(person_id="P-000123", identity=_identity())

    for role in ActorRole:
        assert human_actor(person.person_id, ActorRole.WORKER).actor_id == (
            person.person_id
        )
        assert role.value not in person.person_id

    assert "role" not in inspect.signature(Person).parameters


# ----------------------------------------------------------------------
# Role-neutrality and absence of credentials/profile data
# ----------------------------------------------------------------------

FORBIDDEN_FIELD_NAMES = {
    "role",
    "roles",
    "actor_role",
    "assurance",
    "identity_assurance",
    "scope",
    "sections",
    "permission",
    "permissions",
    "token",
    "access_token",
    "id_token",
    "refresh_token",
    "password",
    "secret",
    "cookie",
    "session",
    "email",
    "name",
    "display_name",
}


@pytest.mark.parametrize("cls", [ExternalIdentity, Person])
def test_types_are_frozen_dataclasses_without_forbidden_fields(cls):
    assert dataclasses.is_dataclass(cls)
    assert cls.__dataclass_params__.frozen

    names = {f.name for f in dataclasses.fields(cls)}

    assert not names & FORBIDDEN_FIELD_NAMES


def test_exact_field_sets():
    assert [f.name for f in dataclasses.fields(ExternalIdentity)] == [
        "identity_provider",
        "subject",
    ]
    assert [f.name for f in dataclasses.fields(Person)] == [
        "person_id",
        "identity",
    ]


def test_person_holds_no_role_or_assurance_or_actor_values():
    person = Person(person_id="P-000123", identity=_identity())

    for value in (person.person_id, person.identity, *vars(person).values()):
        assert not isinstance(value, (ActorRole, IdentityAssurance, Actor))

    for field in dataclasses.fields(Person):
        assert "Actor" not in str(field.type)
        assert "Assurance" not in str(field.type)


def test_person_is_not_an_actor_and_exposes_no_role_state():
    person = Person(person_id="P-1", identity=_identity())

    assert not isinstance(person, Actor)
    assert not hasattr(person, "to_dict")
    assert not hasattr(person, "role")
    assert not hasattr(person, "assurance")


def test_actor_is_referenced_only_inside_the_id_validation_helper():
    """The one permitted Actor use is construct-and-discard id validation."""

    tree = ast.parse(inspect.getsource(person_module))

    helper_nodes = {
        id(n)
        for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef) and fn.name == "_validate_person_id"
        for n in ast.walk(fn)
    }

    offenders = []
    for node in ast.walk(tree):
        if id(node) in helper_nodes:
            continue
        if isinstance(node, ast.Name) and node.id in {"Actor", "human_actor"}:
            offenders.append(node.id)

    assert offenders == []


def test_module_imports_no_authentication_or_persistence_machinery():
    tree = ast.parse(inspect.getsource(person_module))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert not imported & {
        "sqlite3",
        "jwt",
        "jose",
        "requests",
        "httpx",
        "fastapi",
        "authlib",
    }


def test_actor_model_is_unchanged_by_this_slice():
    assert [f.name for f in dataclasses.fields(Actor)] == [
        "actor_id",
        "role",
        "assurance",
    ]
    assert {m.value for m in IdentityAssurance} == {
        "SYSTEM_INTERNAL",
        "DECLARED_UNVERIFIED",
        "NONE",
        "AUTHENTICATED",
    }
