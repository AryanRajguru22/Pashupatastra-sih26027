"""Slice 1: the actor model and how an HTTP request becomes an actor.

Covers backend.app.identity.actor and backend.app.api.deps.request_actor.
Nothing here is authentication: these tests pin that identity is always
recorded with an honest assurance level, that human and system actors
cannot be confused, and that an external caller can never act as SYSTEM.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.identity.actor import (
    OPTIMIZER,
    SCORER,
    SYSTEM,
    Actor,
    ActorKind,
    ActorRole,
    IdentityAssurance,
    InvalidActorError,
    human_actor,
    system_actor,
    unidentified_actor,
)
from backend.app.identity.authorization import JobAction, UnenforcedPolicy


# ----------------------------------------------------------------------
# 1-6. Actor representation per role
# ----------------------------------------------------------------------


def test_actor_carries_id_role_kind_and_assurance():
    actor = human_actor("WORKER-042", ActorRole.WORKER)

    assert actor.to_dict() == {
        "actor_id": "WORKER-042",
        "role": "WORKER",
        "kind": "HUMAN",
        "assurance": "DECLARED_UNVERIFIED",
    }


@pytest.mark.parametrize(
    "role",
    [ActorRole.WORKER, ActorRole.ENGINEER, ActorRole.AUTHORITY, ActorRole.ADMIN],
)
def test_each_human_role_is_a_declared_unverified_human(role):
    actor = human_actor(f"{role.value}-001", role)

    assert actor.role is role
    assert actor.kind is ActorKind.HUMAN
    assert actor.is_system is False
    assert actor.assurance is IdentityAssurance.DECLARED_UNVERIFIED


def test_human_actor_accepts_role_strings():
    assert human_actor("ENGINEER-7", "ENGINEER").role is ActorRole.ENGINEER


def test_system_actor_is_system_internal():
    for actor, expected_id in (
        (SYSTEM, "SYSTEM"),
        (SCORER, "SYSTEM:SCORER"),
        (OPTIMIZER, "SYSTEM:OPTIMIZER"),
        (system_actor("NOTIFIER"), "SYSTEM:NOTIFIER"),
    ):
        assert actor.actor_id == expected_id
        assert actor.role is ActorRole.SYSTEM
        assert actor.kind is ActorKind.SYSTEM
        assert actor.is_system is True
        assert actor.assurance is IdentityAssurance.SYSTEM_INTERNAL


def test_unidentified_actor_claims_no_identity():
    actor = unidentified_actor()

    assert actor.actor_id == "UNIDENTIFIED"
    assert actor.role is ActorRole.UNIDENTIFIED
    assert actor.kind is ActorKind.HUMAN
    assert actor.assurance is IdentityAssurance.NONE


def test_no_authenticated_assurance_exists_yet():
    """Nothing can claim a verified identity while authentication does not exist."""

    assert {member.value for member in IdentityAssurance} == {
        "SYSTEM_INTERNAL",
        "DECLARED_UNVERIFIED",
        "NONE",
    }


# ----------------------------------------------------------------------
# 7. Human vs system cannot be confused
# ----------------------------------------------------------------------


def test_system_role_can_only_be_system_internal():
    with pytest.raises(InvalidActorError):
        Actor("SYSTEM", ActorRole.SYSTEM, IdentityAssurance.DECLARED_UNVERIFIED)


def test_human_role_can_never_be_system_internal():
    with pytest.raises(InvalidActorError):
        Actor("WORKER-042", ActorRole.WORKER, IdentityAssurance.SYSTEM_INTERNAL)


def test_human_actor_cannot_use_a_system_looking_id():
    with pytest.raises(InvalidActorError):
        human_actor("SYSTEM:OPTIMIZER", ActorRole.AUTHORITY)

    with pytest.raises(InvalidActorError):
        human_actor("system-admin", ActorRole.ADMIN)


def test_system_actor_id_must_look_like_system():
    with pytest.raises(InvalidActorError):
        Actor("WORKER-042", ActorRole.SYSTEM, IdentityAssurance.SYSTEM_INTERNAL)


def test_human_actor_rejects_non_human_roles():
    with pytest.raises(InvalidActorError):
        human_actor("X-1", ActorRole.SYSTEM)

    with pytest.raises(InvalidActorError):
        human_actor("X-1", ActorRole.UNIDENTIFIED)

    with pytest.raises(InvalidActorError):
        human_actor("X-1", "SUPERVISOR")


@pytest.mark.parametrize(
    "bad_id",
    ["", " WORKER-1", "WORKER 1", "-WORKER", "W" * 65, "WORKER\n1", "WORKER;DROP"],
)
def test_malformed_actor_ids_are_rejected(bad_id):
    with pytest.raises(InvalidActorError):
        human_actor(bad_id, ActorRole.WORKER)


def test_actor_is_immutable():
    actor = human_actor("WORKER-042", ActorRole.WORKER)

    with pytest.raises(Exception):
        actor.role = ActorRole.AUTHORITY  # type: ignore[misc]


def test_shipped_authorization_policy_does_not_claim_enforcement():
    policy = UnenforcedPolicy()

    assert policy.enforcing is False

    for action in JobAction:
        assert policy.authorize(unidentified_actor(), action) is None


# ----------------------------------------------------------------------
# HTTP actor resolution (backend.app.api.deps.request_actor)
# ----------------------------------------------------------------------


@pytest.fixture()
def client():
    from backend.app.api.main import app

    return TestClient(app)


def _payload() -> dict:
    return {
        "track_id": "UP-1",
        "job_type": "ROUTINE_INSPECTION",
        "distance_start": 3000.0,
        "distance_end": 3200.0,
        "workers_min": 1,
        "workers_max": 2,
        "description": "Actor header resolution check",
    }


def _created_actor(client, job_id: str) -> dict:
    history = client.get(f"/jobs/{job_id}/history")
    assert history.status_code == 200, history.text
    return history.json()["events"][0]["actor"]


def test_declared_headers_are_recorded_as_unverified_human(client):
    response = client.post(
        "/jobs",
        json=_payload(),
        headers={"X-Actor-Id": "WORKER-042", "X-Actor-Role": "WORKER"},
    )
    assert response.status_code == 201, response.text

    assert _created_actor(client, response.json()["job_id"]) == {
        "actor_id": "WORKER-042",
        "role": "WORKER",
        "kind": "HUMAN",
        "assurance": "DECLARED_UNVERIFIED",
    }


def test_role_header_is_case_insensitive(client):
    response = client.post(
        "/jobs",
        json=_payload(),
        headers={"X-Actor-Id": "ENGINEER-9", "X-Actor-Role": "engineer"},
    )
    assert response.status_code == 201, response.text
    assert _created_actor(client, response.json()["job_id"])["role"] == "ENGINEER"


def test_no_headers_records_the_unidentified_actor(client):
    response = client.post("/jobs", json=_payload())
    assert response.status_code == 201, response.text

    actor = _created_actor(client, response.json()["job_id"])
    assert actor["actor_id"] == "UNIDENTIFIED"
    assert actor["role"] == "UNIDENTIFIED"
    assert actor["assurance"] == "NONE"


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Actor-Id": "WORKER-042"},
        {"X-Actor-Role": "WORKER"},
    ],
)
def test_half_an_identity_is_rejected(client, headers):
    response = client.post("/jobs", json=_payload(), headers=headers)
    assert response.status_code == 400


def test_external_caller_cannot_act_as_system(client):
    response = client.post(
        "/jobs",
        json=_payload(),
        headers={"X-Actor-Id": "SYSTEM", "X-Actor-Role": "SYSTEM"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Actor-Id": "WORKER-042", "X-Actor-Role": "SUPERVISOR"},
        {"X-Actor-Id": "WORKER-042", "X-Actor-Role": "UNIDENTIFIED"},
        {"X-Actor-Id": "SYSTEM:OPTIMIZER", "X-Actor-Role": "AUTHORITY"},
        {"X-Actor-Id": "bad id", "X-Actor-Role": "WORKER"},
    ],
)
def test_invalid_declared_identity_is_rejected(client, headers):
    response = client.post("/jobs", json=_payload(), headers=headers)
    assert response.status_code == 400, response.text
