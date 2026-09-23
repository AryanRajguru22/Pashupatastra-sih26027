"""Slice 10.2c: AUTHENTICATED assurance and its single trusted factory.

The only legitimate path is

    VerifiedIdentityAssertion -> verified.identity -> IdentityDirectory
        .person_for -> .select_role -> Actor(..., AUTHENTICATED, capability)

The capability protects against request-borne data and accidental code
paths, not against hostile in-process code (object.__new__ and friends).
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import pickle
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import backend.app.identity as identity_package
from backend.app.api.deps import request_actor
from backend.app.identity import actor as actor_module
from backend.app.identity import authenticated_actor as factory_module
from backend.app.identity.actor import (
    Actor,
    ActorRole,
    IdentityAssurance,
    InvalidActorError,
    human_actor,
    system_actor,
    unidentified_actor,
)
from backend.app.identity.assignments import (
    INVALID_ROLE,
    NO_ASSIGNMENT,
    ROLE_NOT_ASSIGNED,
    ROLE_SELECTION_REQUIRED,
    AssignmentError,
    RoleAssignment,
)
from backend.app.identity.authenticated_actor import (
    UNTRUSTED_ASSERTION,
    UNTRUSTED_DIRECTORY,
    AuthenticationContextError,
    authenticated_actor,
)
from backend.app.identity.authorization import AuthorizationDenied, JobAction
from backend.app.identity.directory import (
    IDENTITY_NOT_ENROLLED,
    DirectoryError,
    IdentityDirectory,
)
from backend.app.identity.identity_assertion import (
    IdentityAssertionVerifier,
    InMemoryReplayGuard,
    VerifiedIdentityAssertion,
)
from backend.app.identity.person import ExternalIdentity, Person
from backend.app.identity.policy import EnforcingPolicy, StaticScopeDirectory
from backend.app.identity.resource import ResourceLocation
from backend.app.identity.scope import RailwayScope
from backend.app.identity.scope_assignment import RoleScopeAssignment
from backend.app.jobs.events import JobEvent, JobEventType, make_event
from backend.tests.test_slice10_2a_identity_assertion import (
    ALIAS,
    FixedClock,
    SigningKey,
    base_claims,
    config,
    jwks,
    mint,
)
from backend.app.identity.trusted_keys import StaticKeyProvider

W, E, A, ADM = ActorRole.WORKER, ActorRole.ENGINEER, ActorRole.AUTHORITY, ActorRole.ADMIN
AUTH = IdentityAssurance.AUTHENTICATED
CORRIDOR = "CORR-1"
CAP = actor_module._AUTHENTICATION_CAPABILITY


# ---------------------------------------------------------------- helpers


@pytest.fixture()
def key() -> SigningKey:
    return SigningKey("key-current")


@pytest.fixture()
def verifier(key) -> IdentityAssertionVerifier:
    return IdentityAssertionVerifier(
        config(), StaticKeyProvider(jwks(key)), InMemoryReplayGuard(), FixedClock()
    )


def verified_for(key, verifier, subject: str, **claims: Any) -> VerifiedIdentityAssertion:
    return verifier.verify(mint(key, base_claims(sub=subject, **claims)))


def scope(*sections: str) -> RailwayScope:
    return RailwayScope(CORRIDOR, list(sections))


def person(person_id: str, subject: str) -> Person:
    return Person(person_id, ExternalIdentity(ALIAS, subject))


SUB_ONE, SUB_MULTI, SUB_NONE, SUB_ADMIN = (str(uuid.uuid4()) for _ in range(4))


def directory() -> IdentityDirectory:
    return IdentityDirectory(
        people=[
            person("P-ONE", SUB_ONE),
            person("P-MULTI", SUB_MULTI),
            person("P-NONE", SUB_NONE),
            person("P-ADMIN", SUB_ADMIN),
        ],
        role_assignments=[
            RoleAssignment("P-ONE", W),
            RoleAssignment("P-MULTI", W),
            RoleAssignment("P-MULTI", E),
            RoleAssignment("P-ADMIN", ADM),
        ],
        scope_assignments=[
            RoleScopeAssignment("P-ONE", W, scope("S1", "S2")),
            RoleScopeAssignment("P-MULTI", W, scope("S1")),
            RoleScopeAssignment("P-MULTI", E, scope("S9")),
        ],
    )


def refused_actor(**kwargs: Any) -> InvalidActorError:
    with pytest.raises(InvalidActorError) as info:
        Actor(**kwargs)
    return info.value


def authed(role: ActorRole = W, actor_id: str = "P-1") -> Actor:
    return Actor(actor_id, role, AUTH, CAP)


# ------------------------------------------------ A. the trusted path


def test_valid_assertion_enrolled_person_and_role_yield_authenticated_actor(key, verifier):
    d = directory()
    actor = authenticated_actor(verified_for(key, verifier, SUB_ONE), d)

    assert actor == Actor("P-ONE", W, AUTH, CAP)
    assert actor.actor_id == "P-ONE"
    assert actor.role is W
    assert actor.assurance is AUTH
    assert actor.kind.value == "HUMAN"


def test_actor_id_is_the_enrolled_person_id_not_the_subject(key, verifier):
    actor = authenticated_actor(verified_for(key, verifier, SUB_ONE), directory())
    assert actor.actor_id == "P-ONE"
    assert SUB_ONE not in actor.actor_id


def test_requested_role_selects_among_held_roles(key, verifier):
    d = directory()
    assert authenticated_actor(verified_for(key, verifier, SUB_MULTI), d, E).role is E
    assert authenticated_actor(verified_for(key, verifier, SUB_MULTI), d, "WORKER").role is W


def test_admin_role_can_be_authenticated(key, verifier):
    actor = authenticated_actor(verified_for(key, verifier, SUB_ADMIN), directory())
    assert actor.role is ADM and actor.assurance is AUTH


# ------------------------------------------------ B-G. fail closed


def test_unknown_identity_is_not_enrolled_and_subject_not_echoed(key, verifier):
    subject = f"stranger-{uuid.uuid4()}"
    with pytest.raises(DirectoryError) as info:
        authenticated_actor(verified_for(key, verifier, subject), directory())
    assert info.value.reason == IDENTITY_NOT_ENROLLED
    assert subject not in str(info.value)


def test_enrolled_person_with_no_role_is_refused(key, verifier):
    with pytest.raises(AssignmentError) as info:
        authenticated_actor(verified_for(key, verifier, SUB_NONE), directory())
    assert info.value.reason == NO_ASSIGNMENT


def test_ambiguous_roles_without_a_request_are_refused(key, verifier):
    with pytest.raises(AssignmentError) as info:
        authenticated_actor(verified_for(key, verifier, SUB_MULTI), directory())
    assert info.value.reason == ROLE_SELECTION_REQUIRED


def test_requested_role_not_held_is_refused(key, verifier):
    with pytest.raises(AssignmentError) as info:
        authenticated_actor(verified_for(key, verifier, SUB_ONE), directory(), E)
    assert info.value.reason == ROLE_NOT_ASSIGNED


@pytest.mark.parametrize(
    "bad",
    [ActorRole.SYSTEM, ActorRole.UNIDENTIFIED, "SYSTEM", "UNIDENTIFIED", "NOPE", "worker", "Worker", 7],
)
def test_system_unidentified_invalid_and_case_variant_roles_are_refused(key, verifier, bad):
    with pytest.raises(AssignmentError) as info:
        authenticated_actor(verified_for(key, verifier, SUB_ONE), directory(), bad)
    assert info.value.reason == INVALID_ROLE


# ------------------------------------- H/I. only trusted inputs accepted


@pytest.mark.parametrize(
    "bad",
    [
        person("P-ONE", SUB_ONE),
        {"identity": ExternalIdentity(ALIAS, SUB_ONE)},
        (ExternalIdentity(ALIAS, SUB_ONE),),
        ExternalIdentity(ALIAS, SUB_ONE),
        "P-ONE",
        None,
        authed(),
    ],
)
def test_untrusted_objects_are_refused_as_the_assertion(bad):
    with pytest.raises(AuthenticationContextError) as info:
        authenticated_actor(bad, directory())
    assert info.value.reason == UNTRUSTED_ASSERTION


def test_subclass_forged_verified_assertion_is_refused(key, verifier):
    real = verified_for(key, verifier, SUB_ONE)

    @dataclasses.dataclass(frozen=True)
    class Forged(VerifiedIdentityAssertion):
        def __post_init__(self, _construction_token):  # skips the token check
            pass

    forged = Forged(
        identity=ExternalIdentity(ALIAS, SUB_ONE),
        issuer=real.issuer,
        audience=real.audience,
        session_id="sid",
        assertion_id="jti",
        key_id="kid",
        issued_at=1,
        expires_at=2,
        auth_time=1,
    )
    assert isinstance(forged, VerifiedIdentityAssertion)  # isinstance would let it through

    with pytest.raises(AuthenticationContextError) as info:
        authenticated_actor(forged, directory())
    assert info.value.reason == UNTRUSTED_ASSERTION


def test_static_scope_directory_is_refused(key, verifier):
    static = StaticScopeDirectory(people=[person("P-ONE", SUB_ONE)])
    with pytest.raises(AuthenticationContextError) as info:
        authenticated_actor(verified_for(key, verifier, SUB_ONE), static)  # type: ignore[arg-type]
    assert info.value.reason == UNTRUSTED_DIRECTORY


def test_identity_directory_subclass_is_refused(key, verifier):
    class Liar(IdentityDirectory):
        def person_for(self, identity):
            return person("P-ADMIN", SUB_ADMIN)

    with pytest.raises(AuthenticationContextError) as info:
        authenticated_actor(verified_for(key, verifier, SUB_ONE), Liar())
    assert info.value.reason == UNTRUSTED_DIRECTORY


def test_duck_typed_directory_is_refused(key, verifier):
    class Duck:
        def person_for(self, identity):
            return person("P-ADMIN", SUB_ADMIN)

        def select_role(self, p, requested_role=None):
            return RoleAssignment("P-ADMIN", ADM)

    with pytest.raises(AuthenticationContextError) as info:
        authenticated_actor(verified_for(key, verifier, SUB_ONE), Duck())  # type: ignore[arg-type]
    assert info.value.reason == UNTRUSTED_DIRECTORY


def test_factory_signature_accepts_no_person_identity_id_actor_or_assurance():
    params = list(inspect.signature(authenticated_actor).parameters)
    assert params == ["verified", "directory", "requested_role"]
    for forbidden in ("person", "identity", "person_id", "actor", "actor_id", "assurance", "role"):
        assert forbidden not in params
    with pytest.raises(TypeError):
        authenticated_actor(  # type: ignore[call-arg]
            verified=None, directory=directory(), assurance=AUTH
        )


def test_context_error_carries_a_reason():
    with pytest.raises(AuthenticationContextError) as info:
        authenticated_actor(None, directory())  # type: ignore[arg-type]
    assert isinstance(info.value, ValueError)
    assert str(info.value).startswith(UNTRUSTED_ASSERTION)


# ------------------------- J/K. NovaForge claims carry no authority


def test_novaforge_role_scope_and_actor_id_claims_cannot_alter_identity(key, verifier):
    d = directory()
    scopes_before = {(p, r): d.scopes_for_actor(p, r) for p in ("P-ONE", "P-ADMIN") for r in ActorRole}

    claims = dict(
        role="SUPER_ADMIN",
        trust_level=100,
        railway_role="ADMIN",
        scope=["S1", "S2", "S3", "*"],
        scopes=["ALL"],
        actor_id="P-ADMIN",
        person_id="P-ADMIN",
    )
    actor = authenticated_actor(verified_for(key, verifier, SUB_ONE, **claims), d)

    assert actor.actor_id == "P-ONE"
    assert actor.role is W  # never ADMIN / AUTHORITY
    assert {(p, r): d.scopes_for_actor(p, r) for p in ("P-ONE", "P-ADMIN") for r in ActorRole} == scopes_before
    assert d.scopes_for_actor("P-ONE", W) == (scope("S1", "S2"),)


def test_novaforge_admin_claim_does_not_help_a_person_without_roles(key, verifier):
    with pytest.raises(AssignmentError) as info:
        authenticated_actor(
            verified_for(key, verifier, SUB_NONE, role="SUPER_ADMIN", railway_role="ADMIN"),
            directory(),
        )
    assert info.value.reason == NO_ASSIGNMENT


def test_a_claimed_role_the_person_does_not_hold_cannot_be_selected_through_claims(key, verifier):
    actor = authenticated_actor(
        verified_for(key, verifier, SUB_MULTI, role="AUTHORITY", railway_role="AUTHORITY"),
        directory(),
        E,
    )
    assert actor.role is E


def test_factory_reads_only_the_identity_from_the_assertion():
    tree = ast.parse(inspect.getsource(factory_module))
    used = {
        n.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "verified"
    }
    assert used == {"identity"}


# ------------------------------------------- L. no manufacturing


@pytest.mark.parametrize("role", [W, E, A, ADM])
def test_direct_authenticated_construction_is_rejected(role):
    assert "AUTHENTICATED" in str(refused_actor(actor_id="P-1", role=role, assurance=AUTH))


def test_string_authenticated_is_rejected():
    refused_actor(actor_id="P-1", role=W, assurance="AUTHENTICATED")
    refused_actor(actor_id="P-1", role="WORKER", assurance="AUTHENTICATED")


def test_enum_coercion_of_authenticated_is_rejected_without_capability():
    coerced = IdentityAssurance("AUTHENTICATED")
    assert coerced is AUTH
    refused_actor(actor_id="P-1", role=W, assurance=coerced)


@pytest.mark.parametrize("wrong", [None, object(), "capability", 1, True, ()])
def test_wrong_capability_is_rejected(wrong):
    with pytest.raises(InvalidActorError):
        Actor("P-1", W, AUTH, wrong)


def test_a_second_object_of_the_same_kind_is_not_the_capability():
    with pytest.raises(InvalidActorError):
        Actor("P-1", W, AUTH, type(CAP)())


def test_class_level_default_cannot_be_used_as_the_capability():
    assert Actor._capability is None
    with pytest.raises(InvalidActorError):
        Actor("P-1", W, AUTH, Actor._capability)
    with pytest.raises(InvalidActorError):
        Actor("P-1", W, AUTH, authed()._capability)


def test_capability_is_private_and_never_exposed():
    assert not hasattr(actor_module, "AUTHENTICATION_CAPABILITY")
    assert "_AUTHENTICATION_CAPABILITY" not in actor_module.__all__
    namespace: dict = {}
    exec("from backend.app.identity.actor import *", namespace)
    assert "_AUTHENTICATION_CAPABILITY" not in namespace
    assert "_capability" not in dataclasses.asdict(authed())
    assert "_capability" not in repr(authed())
    assert CAP is not None and repr(CAP) not in repr(authed())


@pytest.mark.parametrize("role", [ActorRole.SYSTEM, ActorRole.UNIDENTIFIED])
def test_authenticated_is_never_valid_for_system_or_unidentified_even_with_capability(role):
    for actor_id in ("SYSTEM:X", "UNIDENTIFIED", "P-1"):
        with pytest.raises(InvalidActorError):
            Actor(actor_id, role, AUTH, CAP)


def test_system_and_unidentified_keep_their_own_assurance_only():
    with pytest.raises(InvalidActorError):
        Actor("SYSTEM", ActorRole.SYSTEM, IdentityAssurance.NONE)
    with pytest.raises(InvalidActorError):
        Actor("UNIDENTIFIED", ActorRole.UNIDENTIFIED, IdentityAssurance.DECLARED_UNVERIFIED)
    with pytest.raises(InvalidActorError):
        Actor("P-1", W, IdentityAssurance.SYSTEM_INTERNAL, CAP)
    with pytest.raises(InvalidActorError):
        Actor("P-1", W, IdentityAssurance.NONE, CAP)


def test_the_capability_does_not_relax_other_actor_rules():
    with pytest.raises(InvalidActorError):
        Actor("SYSTEM-1", W, AUTH, CAP)  # SYSTEM* ids stay reserved
    with pytest.raises(InvalidActorError):
        Actor("bad id!", W, AUTH, CAP)
    with pytest.raises(InvalidActorError):
        Actor("P-1", "NOPE", AUTH, CAP)


def test_declared_assurance_ignores_the_capability():
    actor = Actor("P-1", W, IdentityAssurance.DECLARED_UNVERIFIED, CAP)
    assert actor.assurance is IdentityAssurance.DECLARED_UNVERIFIED


# ------------------------------------------ M. demo compatibility


def test_human_actor_is_still_declared_unverified_and_takes_no_assurance():
    assert human_actor("WORKER-042", W).assurance is IdentityAssurance.DECLARED_UNVERIFIED
    assert list(inspect.signature(human_actor).parameters) == ["actor_id", "role"]
    with pytest.raises(TypeError):
        human_actor("WORKER-042", W, AUTH)  # type: ignore[call-arg]


def test_system_and_unidentified_constructors_are_unchanged():
    assert system_actor().assurance is IdentityAssurance.SYSTEM_INTERNAL
    assert system_actor("OPTIMIZER").actor_id == "SYSTEM:OPTIMIZER"
    assert unidentified_actor().assurance is IdentityAssurance.NONE


def test_actor_shape_equality_hash_repr_and_serialization_are_unchanged():
    assert [f.name for f in dataclasses.fields(Actor)] == ["actor_id", "role", "assurance"]
    a, b = Actor("P-1", W, AUTH, CAP), Actor("P-1", W, AUTH, CAP)
    assert a == b and hash(a) == hash(b)
    assert a != human_actor("P-1", W)
    assert repr(a) == "Actor(actor_id='P-1', role=<ActorRole.WORKER: 'WORKER'>, assurance=<IdentityAssurance.AUTHENTICATED: 'AUTHENTICATED'>)"
    positional = human_actor("P-1", W)
    assert Actor("P-1", W, IdentityAssurance.DECLARED_UNVERIFIED) == positional
    assert positional.to_dict() == {
        "actor_id": "P-1",
        "role": "WORKER",
        "kind": "HUMAN",
        "assurance": "DECLARED_UNVERIFIED",
    }


def test_authenticated_actor_serializes_with_to_dict():
    assert authed().to_dict() == {
        "actor_id": "P-1",
        "role": "WORKER",
        "kind": "HUMAN",
        "assurance": "AUTHENTICATED",
    }


def test_request_actor_behaviour_is_unchanged():
    assert request_actor(actor_id="WORKER-042", actor_role="WORKER").assurance is (
        IdentityAssurance.DECLARED_UNVERIFIED
    )
    assert request_actor(actor_id=None, actor_role=None).assurance is IdentityAssurance.NONE


def _echo_client() -> TestClient:
    app = FastAPI()

    @app.get("/who")
    def who(actor: Actor = Depends(request_actor)):
        return actor.to_dict()

    return TestClient(app)


@pytest.mark.parametrize(
    "extra",
    [
        {"X-Actor-Assurance": "AUTHENTICATED"},
        {"X-Assurance": "AUTHENTICATED"},
        {"X-Actor-Authenticated": "true"},
    ],
)
def test_no_header_can_produce_authenticated(extra):
    client = _echo_client()
    response = client.get(
        "/who", headers={"X-Actor-Id": "WORKER-042", "X-Actor-Role": "WORKER", **extra}
    )
    assert response.status_code == 200
    assert response.json()["assurance"] == "DECLARED_UNVERIFIED"

    anonymous = client.get("/who", headers=extra)
    assert anonymous.json()["assurance"] == "NONE"


def test_authenticated_as_a_role_header_value_is_not_a_role():
    response = _echo_client().get(
        "/who", headers={"X-Actor-Id": "WORKER-042", "X-Actor-Role": "AUTHENTICATED"}
    )
    assert response.status_code == 400


def test_query_and_body_cannot_reach_assurance():
    response = _echo_client().get(
        "/who?assurance=AUTHENTICATED&actor_assurance=AUTHENTICATED",
        headers={"X-Actor-Id": "WORKER-042", "X-Actor-Role": "WORKER"},
    )
    assert response.json()["assurance"] == "DECLARED_UNVERIFIED"


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
    # The submodule is importable, but the factory itself is not re-exported.
    assert "authenticated_actor" not in identity_package.__all__
    assert not callable(getattr(identity_package, "authenticated_actor", None))
    assert not hasattr(identity_package, "AuthenticationContextError")
    assert "_AUTHENTICATION_CAPABILITY" not in actor_module.__all__


# ------------------------------------------- N. replace / mutation


def test_replace_cannot_upgrade_declared_to_authenticated():
    declared = human_actor("P-1", W)
    with pytest.raises(InvalidActorError):
        dataclasses.replace(declared, assurance=AUTH)
    with pytest.raises(InvalidActorError):
        dataclasses.replace(declared, assurance="AUTHENTICATED")


@pytest.mark.parametrize("change", [{"actor_id": "P-2"}, {"role": E}, {"role": ADM}, {"role": "AUTHORITY"}])
def test_replace_cannot_change_id_or_role_of_an_authenticated_actor(change):
    with pytest.raises(InvalidActorError):
        dataclasses.replace(authed(), **change)


def test_replace_with_no_change_of_an_authenticated_actor_is_refused_too():
    """replace() cannot re-supply the capability, so it cannot clone AUTHENTICATED at all."""

    with pytest.raises(InvalidActorError):
        dataclasses.replace(authed())


def test_replace_downgrade_to_declared_is_allowed_and_non_escalating():
    """Documented behaviour: a downgrade claims LESS. It escalates nothing."""

    down = dataclasses.replace(authed(), assurance=IdentityAssurance.DECLARED_UNVERIFIED)
    assert down.assurance is IdentityAssurance.DECLARED_UNVERIFIED
    assert down == human_actor("P-1", W)
    # ... and the downgraded actor cannot be upgraded again.
    with pytest.raises(InvalidActorError):
        dataclasses.replace(down, assurance=AUTH)


def test_direct_mutation_is_rejected():
    actor = authed()
    for name, value in (("assurance", IdentityAssurance.DECLARED_UNVERIFIED), ("role", ADM), ("actor_id", "P-2")):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(actor, name, value)
    declared = human_actor("P-1", W)
    with pytest.raises(dataclasses.FrozenInstanceError):
        declared.assurance = AUTH  # type: ignore[misc]


def test_copies_of_a_declared_actor_stay_declared():
    import copy

    declared = human_actor("P-1", W)
    assert copy.copy(declared).assurance is IdentityAssurance.DECLARED_UNVERIFIED
    assert copy.deepcopy(declared).assurance is IdentityAssurance.DECLARED_UNVERIFIED


# ---------------------------------- O. deserialization fails closed


def _event_dict(assurance: str) -> dict:
    event = make_event("job-1", JobEventType.JOB_CREATED, human_actor("P-1", W))
    data = event.to_dict()
    data["actor"] = {**data["actor"], "assurance": assurance}
    return data


def test_job_event_from_dict_still_rebuilds_declared_actors():
    rebuilt = JobEvent.from_dict(_event_dict("DECLARED_UNVERIFIED"))
    assert rebuilt.actor == human_actor("P-1", W)


def test_job_event_from_dict_fails_closed_for_an_authenticated_record():
    """Sanctioned authenticated rehydration is deferred to 10.2f (blocking there)."""

    with pytest.raises(InvalidActorError):
        JobEvent.from_dict(_event_dict("AUTHENTICATED"))


def test_an_authenticated_actor_cannot_be_rebuilt_from_its_dict():
    data = authed().to_dict()
    with pytest.raises(InvalidActorError):
        Actor(data["actor_id"], ActorRole(data["role"]), IdentityAssurance(data["assurance"]))


def test_pickle_is_a_documented_in_process_limit_and_never_an_upgrade():
    """Documented limit (gate P3-1), asserted so a change is noticed.

    Dataclass unpickling rebuilds via object.__new__ and skips __init__,
    so the capability check cannot run on it: like object.__new__ and
    object.__setattr__, crafted pickle bytes are hostile in-process code
    and out of scope. What IS pinned: nothing in production pickles an
    Actor, and pickling never turns a DECLARED actor into AUTHENTICATED.
    """

    declared = pickle.loads(pickle.dumps(human_actor("P-1", W)))
    assert declared.assurance is IdentityAssurance.DECLARED_UNVERIFIED
    for rel, src in production_sources():
        assert "pickle" not in {
            a.name.split(".")[0]
            for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Import)
            for a in n.names
        }, rel


# ------------------------------------------- authorization behaviour


def _policy(d: IdentityDirectory) -> EnforcingPolicy:
    return EnforcingPolicy(d, topology=[(CORRIDOR, ["S1", "S2", "S3"])])


@pytest.mark.parametrize("action", list(JobAction))
def test_authenticated_admin_is_denied_every_job_action(key, verifier, action):
    d = directory()
    admin = authenticated_actor(verified_for(key, verifier, SUB_ADMIN), d)
    assert admin.role is ADM and admin.assurance is AUTH
    with pytest.raises(AuthorizationDenied):
        _policy(d).authorize(admin, action)


def test_authenticated_worker_is_authorized_in_scope_and_denied_out_of_scope(key, verifier):
    d = directory()
    worker = authenticated_actor(verified_for(key, verifier, SUB_ONE), d)
    policy = _policy(d)

    policy.authorize_resource(worker, JobAction.READ_JOB, ResourceLocation(CORRIDOR, "S1"))
    policy.authorize_resource(worker, JobAction.READ_JOB, ResourceLocation(CORRIDOR, "S2"))

    with pytest.raises(AuthorizationDenied):
        policy.authorize_resource(worker, JobAction.READ_JOB, ResourceLocation(CORRIDOR, "S3"))
    with pytest.raises(AuthorizationDenied):
        policy.authorize_resource(worker, JobAction.READ_JOB, ResourceLocation("OTHER", "S1"))


def test_selected_role_governs_scope_for_a_multi_role_person(key, verifier):
    d = directory()
    policy = _policy(d)
    v1 = verified_for(key, verifier, SUB_MULTI)
    as_worker = authenticated_actor(v1, d, W)
    as_engineer = authenticated_actor(v1, d, E)

    policy.authorize_resource(as_worker, JobAction.READ_JOB, ResourceLocation(CORRIDOR, "S1"))
    with pytest.raises(AuthorizationDenied):
        policy.authorize_resource(as_worker, JobAction.READ_JOB, ResourceLocation(CORRIDOR, "S9"))
    policy.authorize_resource(as_engineer, JobAction.READ_JOB, ResourceLocation(CORRIDOR, "S9"))


def test_factory_result_is_not_an_authorization_decision():
    """An AUTHENTICATED actor with no scope is denied like any other."""

    d = IdentityDirectory([person("P-1", "sub-1")], [RoleAssignment("P-1", W)])
    with pytest.raises(AuthorizationDenied):
        _policy(d).authorize_resource(authed(actor_id="P-1"), JobAction.READ_JOB, ResourceLocation(CORRIDOR, "S1"))


def test_directory_changes_take_effect_because_the_factory_holds_no_state(key, verifier):
    """Revocation: re-resolving through a directory without the person is refused."""

    v1 = verified_for(key, verifier, SUB_ONE)
    assert authenticated_actor(v1, directory()).actor_id == "P-ONE"
    empty = IdentityDirectory()
    with pytest.raises(DirectoryError) as info:
        authenticated_actor(v1, empty)
    assert info.value.reason == IDENTITY_NOT_ENROLLED


# ------------------------------------------- P. AST boundary

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
ALLOWED_CAPABILITY_MODULES = {"identity/actor.py", "identity/authenticated_actor.py"}


def references_capability(source: str) -> bool:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "_AUTHENTICATION_CAPABILITY":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "_AUTHENTICATION_CAPABILITY":
            return True
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if any(a.name.split(".")[-1] == "_AUTHENTICATION_CAPABILITY" for a in node.names):
                return True
        if isinstance(node, ast.Constant) and node.value == "_AUTHENTICATION_CAPABILITY":
            return True  # getattr(module, "_AUTHENTICATION_CAPABILITY")
    return False


def constructs_actor_as_authenticated(source: str) -> bool:
    """A call to Actor(...) whose arguments mention AUTHENTICATED in any form."""

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name != "Actor":
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Attribute) and inner.attr == "AUTHENTICATED":
                return True
            if isinstance(inner, ast.Name) and inner.id in {"AUTHENTICATED", "_AUTHENTICATION_CAPABILITY"}:
                return True
            if isinstance(inner, ast.Constant) and inner.value == "AUTHENTICATED":
                return True
    return False


def mentions_authenticated_value(source: str) -> bool:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "AUTHENTICATED":
            return True
        if isinstance(node, ast.Constant) and node.value == "AUTHENTICATED":
            return True
    return False


def production_sources():
    for path in sorted(APP_ROOT.rglob("*.py")):
        yield path.relative_to(APP_ROOT).as_posix(), path.read_text(encoding="utf-8")


def test_capability_is_referenced_only_by_actor_and_the_factory():
    offenders = [
        rel
        for rel, src in production_sources()
        if rel not in ALLOWED_CAPABILITY_MODULES and references_capability(src)
    ]
    assert offenders == []
    referencing = {rel for rel, src in production_sources() if references_capability(src)}
    assert referencing == ALLOWED_CAPABILITY_MODULES


def test_only_the_factory_constructs_an_actor_as_authenticated():
    offenders = [
        rel
        for rel, src in production_sources()
        if rel != "identity/authenticated_actor.py" and constructs_actor_as_authenticated(src)
    ]
    assert offenders == []
    factory_src = (APP_ROOT / "identity" / "authenticated_actor.py").read_text(encoding="utf-8")
    assert constructs_actor_as_authenticated(factory_src)


def test_no_other_production_module_names_the_authenticated_value():
    offenders = [
        rel
        for rel, src in production_sources()
        if rel not in ALLOWED_CAPABILITY_MODULES and mentions_authenticated_value(src)
    ]
    assert offenders == []


def test_scanners_detect_planted_offenders():
    assert references_capability(
        "from backend.app.identity.actor import _AUTHENTICATION_CAPABILITY\nx = _AUTHENTICATION_CAPABILITY\n"
    )
    assert references_capability(
        "from backend.app.identity import actor as m\nx = m._AUTHENTICATION_CAPABILITY\n"
    )
    assert references_capability("from backend.app.identity.actor import _AUTHENTICATION_CAPABILITY as c\n")
    assert references_capability("x = getattr(m, '_AUTHENTICATION_CAPABILITY')\n")
    assert not references_capability("x = 1\n")

    assert constructs_actor_as_authenticated(
        "from backend.app.identity.actor import Actor, IdentityAssurance\n"
        "Actor('a', 'WORKER', IdentityAssurance.AUTHENTICATED, cap)\n"
    )
    assert constructs_actor_as_authenticated("m.Actor('a', 'WORKER', 'AUTHENTICATED')\n")
    assert constructs_actor_as_authenticated("Actor(actor_id='a', role=r, assurance=AUTHENTICATED)\n")
    assert not constructs_actor_as_authenticated("Actor('a', 'WORKER', IdentityAssurance.DECLARED_UNVERIFIED)\n")

    assert mentions_authenticated_value("x = IdentityAssurance.AUTHENTICATED\n")
    assert mentions_authenticated_value("x = 'AUTHENTICATED'\n")
    assert not mentions_authenticated_value("x = 'DECLARED_UNVERIFIED'\n")


def _imports(module) -> set:
    tree = ast.parse(inspect.getsource(module))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_factory_imports_no_api_jobs_persistence_sqlite_or_os():
    imported = _imports(factory_module)
    tops = {m.split(".")[0] for m in imported}
    assert not tops & {"sqlite3", "os", "pathlib", "io", "json", "fastapi", "starlette", "requests", "httpx", "cryptography", "jwt"}
    for forbidden in ("backend.app.api", "backend.app.jobs", "backend.app.persistence", "backend.app.identity._jose", "backend.app.identity.trusted_keys"):
        assert not any(m == forbidden or m.startswith(forbidden + ".") for m in imported), forbidden
    assert imported <= {
        "__future__",
        "typing",
        "backend.app.identity.actor",
        "backend.app.identity.directory",
        "backend.app.identity.identity_assertion",
    }


def test_identity_assertion_and_directory_do_not_import_the_factory():
    from backend.app.identity import directory as directory_module
    from backend.app.identity import identity_assertion as assertion_module

    for module in (assertion_module, directory_module):
        assert not any("authenticated_actor" in m for m in _imports(module)), module.__name__
        names = {n.id for n in ast.walk(ast.parse(inspect.getsource(module))) if isinstance(n, ast.Name)}
        assert "authenticated_actor" not in names and "_AUTHENTICATION_CAPABILITY" not in names


def test_deferred_modules_were_not_touched_by_this_slice():
    """10.2d/e/f work must not leak in: none of these modules know AUTHENTICATED."""

    for rel in (
        "api/deps.py",
        "jobs/events.py",
        "jobs/history.py",
        "jobs/service.py",
        "jobs/router.py",
        "identity/policy.py",
        "identity/authorization.py",
        "identity/directory.py",
        "identity/identity_assertion.py",
        "identity/__init__.py",
    ):
        source = (APP_ROOT / rel).read_text(encoding="utf-8")
        assert not mentions_authenticated_value(source), rel
        assert "authenticated_actor" not in source, rel
