"""Slice 10.2d.1: authenticated actor rehydration (P1-A).

A persisted AUTHENTICATED actor is read back as a record-only
RecordedActor, never as a live authenticated Actor. The database can
remember an authenticated identity; it can never become a way to
authenticate.

Hygiene: every service and database here is a tmp one; neither `main` nor
`router` is re-imported. AUTHENTICATED actors are built with the private
capability exactly as the 10.2c/10.2d tests do, to stand in for the
factory's output.
"""

from __future__ import annotations

import ast
import dataclasses
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.app.identity as identity_package
from backend.app.api.composition import AuthMode, compose_jobs
from backend.app.identity import actor as actor_module
from backend.app.identity.actor import (
    Actor,
    ActorRole,
    IdentityAssurance,
    InvalidActorError,
    human_actor,
    system_actor,
    unidentified_actor,
)
from backend.app.identity.assignments import RoleAssignment
from backend.app.identity.authenticated_policy import AuthenticatedEnforcingPolicy
from backend.app.identity.authorization import AuthorizationDenied, JobAction
from backend.app.identity.directory import IdentityDirectory
from backend.app.identity.person import ExternalIdentity, Person
from backend.app.identity.recorded_actor import RecordedActor, rehydrate_actor
from backend.app.identity.resource import ResourceLocation
from backend.app.identity.scope import RailwayScope
from backend.app.identity.scope_assignment import RoleScopeAssignment
from backend.app.jobs.events import JobEvent, JobEventType, make_event
from backend.app.jobs.history import JobHistoryRepository, append_events
from backend.app.jobs.lifecycle import InvalidTransitionError, proposal_run_id_of
from backend.app.jobs.models import JobCreateRequest
from backend.app.jobs.repository import JobRepository
from backend.app.jobs.service import JobService
from backend.tests.execution_helpers import (
    complete_execution_body,
    observed_at,
    start_execution_body,
)
from backend.tests.test_slice10_2c_authenticated_assurance import (
    ALLOWED_CAPABILITY_MODULES,
    constructs_actor_as_authenticated,
    mentions_authenticated_value,
    references_capability,
)

W, E, A, ADM = (
    ActorRole.WORKER,
    ActorRole.ENGINEER,
    ActorRole.AUTHORITY,
    ActorRole.ADMIN,
)
AUTH = IdentityAssurance.AUTHENTICATED
CAP = actor_module._AUTHENTICATION_CAPABILITY
APP_ROOT = Path(__file__).resolve().parents[1] / "app"
E_TYPES = JobEventType


def authed(role: ActorRole = W, actor_id: str = "P-W") -> Actor:
    return Actor(actor_id, role, AUTH, CAP)


def recorded(role: ActorRole = W, actor_id: str = "P-W") -> RecordedActor:
    return RecordedActor(actor_id, role, AUTH)


def event_dict(actor: Actor, job_id: str = "JOB-1", **kwargs) -> dict:
    return make_event(job_id, E_TYPES.JOB_CREATED, actor, **kwargs).to_dict()


def rehydrated_event(actor: Actor | None = None) -> JobEvent:
    return JobEvent.from_dict(event_dict(actor or authed()))


# --------------------------------------------------------------- the rig


def make_directory(corridor: str, sections: tuple[str, ...], *, roles=(W, E, A)):
    scope = RailwayScope(corridor, list(sections))
    ids = {W: "P-W", E: "P-E", A: "P-A"}

    return IdentityDirectory(
        people=[Person(pid, ExternalIdentity("nf", f"s-{pid}")) for pid in ids.values()],
        role_assignments=[RoleAssignment(ids[r], r) for r in roles],
        scope_assignments=[RoleScopeAssignment(ids[r], r, scope) for r in roles],
    )


@pytest.fixture()
def rig(tmp_path):
    probe = JobService(repository=JobRepository(tmp_path / "probe.db"))
    corridor = probe.registry.corridor_id
    sections = probe.registry.section_ids()
    db = tmp_path / "novaforge.db"
    composed = compose_jobs(
        AuthMode.NOVAFORGE,
        directory=make_directory(corridor, sections),
        db_path=db,
    )

    return SimpleNamespace(
        svc=composed.service,
        opt=composed.optimization_service,
        db=db,
        corridor=corridor,
        sections=sections,
        W=authed(W, "P-W"),
        E=authed(E, "P-E"),
        A=authed(A, "P-A"),
    )


def request(start: float = 1000.0, key: str | None = None) -> JobCreateRequest:
    return JobCreateRequest(
        track_id="UP-1",
        job_type="BALLAST_TAMPING",
        distance_start=start,
        distance_end=start + 400,
        workers_min=2,
        workers_max=4,
        description="slice 10.2d.1",
        idempotency_key=key,
    )


def scheduled(rig, start: float) -> dict:
    job = rig.svc.create_job(request(start), actor=rig.W)
    rig.opt.optimize_corridor(actor=rig.E)
    job = rig.svc.repository.get(job["job_id"])
    assert job["status"] == "scheduled"
    return job


def notified(rig, start: float) -> dict:
    job = scheduled(rig, start)
    return rig.svc.notify(
        job["job_id"],
        actor=rig.A,
        expected_proposal_run_id=proposal_run_id_of(job),
    )


def event_count(db: Path) -> int:
    with closing(sqlite3.connect(db)) as conn:
        return conn.execute("SELECT COUNT(*) FROM job_events").fetchone()[0]


def actor_types(events) -> set[type]:
    return {type(e.event.actor if hasattr(e, "event") else e.actor) for e in events}


def raw_insert(db: Path, event_id: str, **actor_overrides) -> None:
    """A crafted row written straight to the table, as an attacker could."""

    record = event_dict(human_actor("P-W", W), job_id="JOB-CRAFTED", event_id=event_id)
    actor = {**record["actor"], **actor_overrides}

    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute(
            """
            INSERT INTO job_events (
                event_id, schema_version, entity_type, job_id, event_type,
                occurred_at, actor_id, actor_role, actor_kind, actor_assurance,
                reason, optimization_run_id, before_state_json,
                after_state_json, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, '{}')
            """,
            (
                event_id,
                record["schema_version"],
                record["entity_type"],
                record["job_id"],
                record["event_type"],
                record["occurred_at"],
                actor["actor_id"],
                actor["role"],
                actor["kind"],
                actor["assurance"],
            ),
        )


@pytest.fixture()
def plain_db(tmp_path) -> Path:
    db = tmp_path / "plain.db"
    JobRepository(db)
    return db


# ------------------------------------------ A. RecordedActor, the value


def test_recorded_actor_is_a_frozen_value_that_is_not_an_actor():
    rec = recorded()

    assert type(rec) is RecordedActor
    assert not isinstance(rec, Actor)
    assert not issubclass(RecordedActor, Actor)
    assert [f.name for f in dataclasses.fields(rec)] == ["actor_id", "role", "assurance"]

    with pytest.raises(dataclasses.FrozenInstanceError):
        rec.actor_id = "P-2"  # type: ignore[misc]


def test_recorded_actor_to_dict_has_the_same_shape_and_values_as_actor():
    for role in (W, E, A, ADM):
        assert recorded(role).to_dict() == authed(role).to_dict()
        assert list(recorded(role).to_dict()) == list(authed(role).to_dict())


def test_recorded_actor_kind_and_is_system_are_derived():
    assert recorded().kind.value == "HUMAN"
    assert recorded().is_system is False


def test_recorded_actor_never_equals_an_actor_and_is_hashable():
    assert recorded() != authed()
    assert authed() != recorded()
    assert recorded() == recorded()
    assert len({recorded(), recorded()}) == 1


def test_an_admin_identity_can_be_recorded():
    assert recorded(ADM, "P-ADMIN").role is ADM


@pytest.mark.parametrize(
    "actor_id,role",
    [
        ("SYSTEM", ActorRole.SYSTEM),
        ("SYSTEM:OPTIMIZER", ActorRole.SYSTEM),
        ("UNIDENTIFIED", ActorRole.UNIDENTIFIED),
        ("P-W", ActorRole.SYSTEM),
        ("P-W", ActorRole.UNIDENTIFIED),
        ("SYSTEM-1", W),
        ("system:x", W),
        ("", W),
        (" P-W", W),
        ("P W", W),
        ("-lead", W),
        ("x" * 65, W),
        (None, W),
        (7, W),
        ("P-W", "PILOT"),
    ],
)
def test_invalid_authenticated_records_fail_closed_by_both_constructors(actor_id, role):
    with pytest.raises(InvalidActorError):
        RecordedActor(actor_id, role, AUTH)

    with pytest.raises(InvalidActorError):
        rehydrate_actor(actor_id, role, "AUTHENTICATED")


@pytest.mark.parametrize(
    "assurance",
    [
        IdentityAssurance.DECLARED_UNVERIFIED,
        IdentityAssurance.SYSTEM_INTERNAL,
        IdentityAssurance.NONE,
        "nonsense",
    ],
)
def test_recorded_actor_holds_only_authenticated(assurance):
    with pytest.raises(InvalidActorError):
        RecordedActor("P-W", W, assurance)


# ------------------------------------------ B. rehydrate_actor


def test_only_authenticated_becomes_a_recorded_actor():
    assert type(rehydrate_actor("P-W", W, "AUTHENTICATED")) is RecordedActor
    assert type(rehydrate_actor("P-W", W, AUTH)) is RecordedActor


def test_every_other_assurance_keeps_its_current_actor_behaviour():
    assert rehydrate_actor("P-W", W, "DECLARED_UNVERIFIED") == human_actor("P-W", W)
    assert type(rehydrate_actor("P-W", W, "DECLARED_UNVERIFIED")) is Actor
    assert rehydrate_actor("SYSTEM:X", ActorRole.SYSTEM, "SYSTEM_INTERNAL") == system_actor("X")
    assert rehydrate_actor("UNIDENTIFIED", ActorRole.UNIDENTIFIED, "NONE") == unidentified_actor()


def test_unknown_assurance_fails_as_before():
    with pytest.raises(InvalidActorError):
        rehydrate_actor("P-W", W, "TRUSTED")

    with pytest.raises(ValueError):
        rehydrate_actor("P-W", W, None)  # type: ignore[arg-type]


def test_rehydrate_actor_still_applies_actors_own_rules_to_other_assurances():
    with pytest.raises(InvalidActorError):
        rehydrate_actor("SYSTEM-1", W, "DECLARED_UNVERIFIED")

    with pytest.raises(InvalidActorError):
        rehydrate_actor("SYSTEM:X", ActorRole.SYSTEM, "DECLARED_UNVERIFIED")


def test_an_authenticated_actor_still_cannot_be_rebuilt_from_its_record():
    """Retained from 10.2c: a record is never a credential."""

    data = authed().to_dict()

    with pytest.raises(InvalidActorError):
        Actor(data["actor_id"], ActorRole(data["role"]), IdentityAssurance(data["assurance"]))

    with pytest.raises(InvalidActorError):
        Actor(**{k: v for k, v in data.items() if k != "kind"})


# ------------------------------------------ C. the JobEvent boundary


def test_authenticated_event_round_trip_is_byte_identical():
    original = make_event("JOB-1", E_TYPES.JOB_CREATED, authed())
    rebuilt = JobEvent.from_dict(original.to_dict())

    assert type(rebuilt.actor) is RecordedActor
    assert rebuilt.to_dict() == original.to_dict()
    assert rebuilt.canonical_bytes() == original.canonical_bytes()
    # A record is deliberately not equal to the live Actor that produced it.
    assert rebuilt.actor != original.actor
    assert rebuilt.actor.to_dict() == original.actor.to_dict()


def test_from_dict_never_produces_a_live_authenticated_actor():
    for role in (W, E, A, ADM):
        actor = JobEvent.from_dict(event_dict(authed(role))).actor
        assert not isinstance(actor, Actor)


def test_direct_construction_with_a_recorded_actor_fails():
    kwargs = dict(
        event_id="EV-1",
        job_id="JOB-1",
        event_type=E_TYPES.JOB_CREATED,
        occurred_at=make_event("J", E_TYPES.JOB_CREATED, human_actor("P-W", W)).occurred_at,
        actor=recorded(),
    )

    with pytest.raises(TypeError):
        JobEvent(**kwargs)

    with pytest.raises(TypeError):
        JobEvent(**kwargs, _rehydration=object())

    with pytest.raises(TypeError):
        JobEvent(**kwargs, _rehydration=None)


def test_make_event_still_requires_an_exact_actor():
    with pytest.raises(TypeError):
        make_event("JOB-1", E_TYPES.JOB_CREATED, recorded())  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        make_event("JOB-1", E_TYPES.JOB_CREATED, "P-W")  # type: ignore[arg-type]

    assert make_event("JOB-1", E_TYPES.JOB_CREATED, authed()).actor == authed()


def test_a_rehydrated_event_cannot_be_copied_into_a_new_event():
    event = rehydrated_event()

    with pytest.raises(TypeError):
        dataclasses.replace(event, reason="edited")


def test_the_rehydration_token_is_not_a_field_and_not_serialized():
    names = {f.name for f in dataclasses.fields(JobEvent)}

    assert "_rehydration" not in names
    assert "_rehydration" not in rehydrated_event().to_dict()


def test_event_actor_field_contracts_are_unchanged():
    event = make_event("JOB-1", E_TYPES.JOB_CREATED, human_actor("P-W", W))

    assert JobEvent.from_dict(event.to_dict()) == event
    assert type(JobEvent.from_dict(event.to_dict()).actor) is Actor


def test_from_dict_refuses_a_kind_that_contradicts_the_role():
    data = event_dict(authed())
    data["actor"] = {**data["actor"], "kind": "SYSTEM"}

    with pytest.raises(ValueError, match="contradicts"):
        JobEvent.from_dict(data)


@pytest.mark.parametrize(
    "override",
    [
        {"role": "SYSTEM", "kind": "SYSTEM"},
        {"role": "UNIDENTIFIED"},
        {"actor_id": "SYSTEM:OPTIMIZER"},
        {"actor_id": "bad id"},
        {"assurance": "TRUSTED"},
    ],
)
def test_from_dict_refuses_forged_authenticated_records(override):
    data = event_dict(authed())
    data["actor"] = {**data["actor"], **override}

    with pytest.raises(ValueError):
        JobEvent.from_dict(data)


# ------------------------------------------ D. the history boundary


def test_append_events_refuses_a_recorded_actor_and_writes_nothing(plain_db):
    good = make_event("JOB-1", E_TYPES.JOB_CREATED, human_actor("P-W", W))
    historical = rehydrated_event()

    with closing(sqlite3.connect(plain_db)) as conn, conn:
        with pytest.raises(TypeError):
            append_events(conn, [good, historical])

    assert event_count(plain_db) == 0

    with pytest.raises(TypeError):
        JobRepository(plain_db).append_events([historical])

    assert event_count(plain_db) == 0


def test_append_events_refuses_an_actor_subclass(plain_db):
    class Sub(Actor):
        pass

    event = make_event("JOB-1", E_TYPES.JOB_CREATED, Sub("P-W", W, IdentityAssurance.DECLARED_UNVERIFIED))

    with pytest.raises(TypeError):
        JobRepository(plain_db).append_events([event])

    assert event_count(plain_db) == 0


def test_append_events_still_accepts_every_real_actor(plain_db):
    JobRepository(plain_db).append_events(
        [
            make_event("JOB-1", E_TYPES.JOB_CREATED, human_actor("P-W", W)),
            make_event("JOB-1", E_TYPES.JOB_CREATED, system_actor("X")),
            make_event("JOB-1", E_TYPES.JOB_CREATED, unidentified_actor()),
            make_event("JOB-1", E_TYPES.JOB_CREATED, authed()),
        ]
    )

    assert event_count(plain_db) == 4


def test_history_round_trips_an_authenticated_event_through_every_read(plain_db):
    original = make_event("JOB-1", E_TYPES.JOB_CREATED, authed(), optimization_run_id="RUN-1")
    JobRepository(plain_db).append_events([original])
    history = JobHistoryRepository(plain_db)

    for stored in (
        history.list_for_job("JOB-1")[0],
        history.get_by_event_id(original.event_id),
        history.list_for_run("RUN-1")[0],
        history.list_for_jobs(["JOB-1", "JOB-NONE"])["JOB-1"][0],
    ):
        assert type(stored.event.actor) is RecordedActor
        assert stored.event.to_dict() == original.to_dict()
        assert stored.event.canonical_bytes() == original.canonical_bytes()

    assert history.list_for_jobs(["JOB-1", "JOB-NONE"])["JOB-NONE"] == []


def test_legacy_declared_system_and_none_events_are_unchanged(plain_db):
    events = [
        make_event("JOB-1", E_TYPES.JOB_CREATED, human_actor("WORKER-042", W)),
        make_event("JOB-1", E_TYPES.JOB_CREATED, system_actor("SCORER")),
        make_event("JOB-1", E_TYPES.JOB_CREATED, unidentified_actor()),
    ]
    JobRepository(plain_db).append_events(events)

    stored = JobHistoryRepository(plain_db).list_for_job("JOB-1")

    assert [s.event.actor for s in stored] == [e.actor for e in events]
    assert {type(s.event.actor) for s in stored} == {Actor}
    assert [s.event.canonical_bytes() for s in stored] == [e.canonical_bytes() for e in events]


# ------------------------------------------ E. forged rows


def test_a_forged_declared_to_authenticated_row_is_only_a_record(plain_db):
    raw_insert(plain_db, "EV-FORGED", actor_id="P-NOT-ENROLLED", assurance="AUTHENTICATED")

    stored = JobHistoryRepository(plain_db).get_by_event_id("EV-FORGED")

    assert type(stored.event.actor) is RecordedActor
    assert not isinstance(stored.event.actor, Actor)


def test_a_forged_row_cannot_pass_authorization_or_be_written_back(plain_db, rig):
    raw_insert(plain_db, "EV-FORGED", assurance="AUTHENTICATED")
    forged = JobHistoryRepository(plain_db).get_by_event_id("EV-FORGED").event.actor

    policy = rig.svc.authorization
    location = ResourceLocation(rig.corridor, rig.sections[0])

    for action in JobAction:
        with pytest.raises(AuthorizationDenied):
            policy.authorize(forged, action)

        with pytest.raises(AuthorizationDenied):
            policy.authorize_resource(forged, action, location)

    with pytest.raises(TypeError):
        make_event("JOB-1", E_TYPES.JOB_CREATED, forged)


@pytest.mark.parametrize(
    "override",
    [
        {"role": "SYSTEM"},
        {"role": "UNIDENTIFIED"},
        {"actor_id": "SYSTEM"},
        {"actor_id": "SYSTEM:X"},
        {"actor_id": "bad id!"},
        {"actor_id": ""},
        {"kind": "SYSTEM"},
        {"assurance": "TRUSTED"},
    ],
)
def test_malformed_forged_rows_fail_closed_on_read(plain_db, override):
    raw_insert(plain_db, "EV-BAD", **{"assurance": "AUTHENTICATED", **override})

    with pytest.raises(ValueError):
        JobHistoryRepository(plain_db).get_by_event_id("EV-BAD")


# ------------------------------------------ F. P1-A, end to end


def test_create_then_history_read_succeeds(rig):
    job = rig.svc.create_job(request(), actor=rig.W)

    events = rig.svc.job_history(job["job_id"], actor=rig.W)

    assert type(events[0].event.actor) is RecordedActor
    assert events[0].event.actor.to_dict() == rig.W.to_dict()


def test_idempotent_replay_succeeds_after_an_authenticated_create(rig):
    first = rig.svc.create_job_with_outcome(request(key="key-1"), rig.W)
    second = rig.svc.create_job_with_outcome(request(key="key-1"), rig.W)

    assert first.replayed is False
    assert second.replayed is True
    assert second.job["job_id"] == first.job["job_id"]


def test_concurrent_winner_replay_succeeds(rig, monkeypatch):
    winner = rig.svc.create_job_with_outcome(request(key="key-2"), rig.W)
    real_replay = rig.svc._idempotent_replay
    calls = []

    def replay(*args):
        calls.append(args)
        return None if len(calls) == 1 else real_replay(*args)

    def lose_the_race(job, events):
        raise sqlite3.IntegrityError("UNIQUE constraint failed: job_events.event_id")

    monkeypatch.setattr(rig.svc, "_idempotent_replay", replay)
    monkeypatch.setattr(rig.svc.repository, "create", lose_the_race)

    outcome = rig.svc.create_job_with_outcome(request(key="key-2"), rig.W)

    assert len(calls) == 2
    assert outcome.replayed is True
    assert outcome.job["job_id"] == winner.job["job_id"]


def test_start_and_complete_execution_succeed_after_the_commit(rig):
    job = notified(rig, 1000.0)

    running, execution = rig.svc.start_execution(
        job["job_id"], actor=rig.W, **start_execution_body(job)
    )
    assert running["status"] == "in_progress"
    assert execution.started_by == rig.W.to_dict()

    done, finished = rig.svc.complete_execution(
        job["job_id"], actor=rig.W, **complete_execution_body(job, execution.execution_id)
    )
    assert done["status"] == "completed"
    assert finished.execution_id == execution.execution_id

    assert rig.svc.get_execution(job["job_id"], actor=rig.W)[0].ended_by == rig.W.to_dict()


def test_not_completed_succeeds_after_the_commit(rig):
    job = notified(rig, 1000.0)
    _, execution = rig.svc.start_execution(
        job["job_id"], actor=rig.W, **start_execution_body(job)
    )

    released, record = rig.svc.report_execution_not_completed(
        job["job_id"],
        actor=rig.W,
        execution_id=execution.execution_id,
        actual_end_at=observed_at(job["schedule_start_minute"] + 30),
        reason="rail temperature too high",
    )

    assert released["status"] == "reported"
    assert record.execution_id == execution.execution_id


def test_postpone_and_its_proposal_digest_succeed(rig):
    job = scheduled(rig, 1000.0)

    assert isinstance(rig.svc._current_proposal_digest(job["job_id"]), str)

    postponed = rig.svc.postpone_proposal(
        job["job_id"],
        actor=rig.A,
        expected_proposal_run_id=proposal_run_id_of(job),
        reason="wait for possession",
        selected_date="2026-09-10",
    )

    assert postponed["status"] == "reported"


def test_a_no_proposal_reason_can_be_derived_for_a_reported_job(rig):
    job = rig.svc.create_job(request(), actor=rig.W)

    with pytest.raises(Exception) as info:
        rig.svc.current_proposal(job["job_id"], actor=rig.A)

    assert type(info.value).__name__ == "NoBlockProposalError"


def test_obligation_reads_succeed(rig):
    job = scheduled(rig, 1000.0)

    obligation = rig.svc.job_obligation(job["job_id"], actor=rig.A)
    page, _, _ = rig.svc.obligations_page(limit=50, actor=rig.A)

    assert obligation.job_id == job["job_id"]
    assert [o.job_id for o in page] == [job["job_id"]]


def test_a_mixed_authenticated_and_declared_page_is_readable_by_everyone(rig):
    authenticated_job = rig.svc.create_job(request(1000.0), actor=rig.W)
    demo = JobService(repository=JobRepository(rig.db))
    declared_job = demo.create_job(request(3000.0), actor=human_actor("WORKER-042", W))

    page, _, _ = rig.svc.obligations_page(limit=50, actor=rig.A)
    demo_page, _, _ = demo.obligations_page(limit=50)

    expected = {authenticated_job["job_id"], declared_job["job_id"]}
    assert {o.job_id for o in page} == expected
    assert {o.job_id for o in demo_page} == expected

    assert {type(s.event.actor) for s in rig.svc.history.list_for_jobs(list(expected))[declared_job["job_id"]]} == {Actor}


# ------------------------------------------ G. a RecordedActor cannot act


def test_a_recorded_actor_is_denied_by_the_enforcing_service_and_writes_nothing(rig):
    before = event_count(rig.db)
    forged = recorded(W, "P-W")

    with pytest.raises(AuthorizationDenied):
        rig.svc.create_job(request(), actor=forged)  # type: ignore[arg-type]

    with pytest.raises(AuthorizationDenied):
        rig.svc.list_jobs(actor=forged)  # type: ignore[arg-type]

    with pytest.raises(AuthorizationDenied):
        rig.opt.optimize_corridor(actor=forged)  # type: ignore[arg-type]

    assert event_count(rig.db) == before
    assert rig.svc.repository.list_all() == []


def test_a_recorded_actor_is_denied_by_the_policy_directly(rig):
    policy = rig.svc.authorization
    location = ResourceLocation(rig.corridor, rig.sections[0])

    for role in (W, E, A, ADM):
        for action in JobAction:
            with pytest.raises(AuthorizationDenied, match="authenticated identity is required"):
                policy.authorize(recorded(role), action)  # type: ignore[arg-type]

            with pytest.raises(AuthorizationDenied, match="authenticated identity is required"):
                policy.authorize_resource(recorded(role), action, location)  # type: ignore[arg-type]


def test_a_recorded_actor_cannot_create_a_job_even_when_nothing_enforces(tmp_path):
    demo = JobService(repository=JobRepository(tmp_path / "demo.db"))

    with pytest.raises(TypeError):
        demo.create_job(request(), actor=recorded())  # type: ignore[arg-type]

    assert demo.repository.list_all() == []
    assert event_count(tmp_path / "demo.db") == 0


def test_a_recorded_actor_cannot_transition_a_job(rig, tmp_path):
    job = scheduled(rig, 1000.0)
    demo = JobService(repository=JobRepository(rig.db))
    before_events = event_count(rig.db)
    before_state = rig.svc.repository.get(job["job_id"])["status"]

    with pytest.raises((InvalidTransitionError, TypeError)):
        demo.reject_proposal(
            job["job_id"],
            actor=recorded(A, "P-A"),  # type: ignore[arg-type]
            expected_proposal_run_id=proposal_run_id_of(job),
            reason="not allowed",
        )

    assert event_count(rig.db) == before_events
    assert rig.svc.repository.get(job["job_id"])["status"] == before_state


# ------------------------------------------ H. stale identities, no directory


def _compose_with(rig, tmp_path, directory) -> SimpleNamespace:
    composed = compose_jobs(AuthMode.NOVAFORGE, directory=directory, db_path=rig.db)
    return SimpleNamespace(svc=composed.service, opt=composed.optimization_service)


CHANGED_DIRECTORIES = {
    "person_removed": lambda c, s: IdentityDirectory(),
    "role_removed": lambda c, s: make_directory(c, s, roles=(E, A)),
    "scope_changed": lambda c, s: IdentityDirectory(
        people=[Person("P-W", ExternalIdentity("nf", "s-P-W"))],
        role_assignments=[RoleAssignment("P-W", W)],
        scope_assignments=[RoleScopeAssignment("P-W", W, RailwayScope("OTHER-CORRIDOR", ["X-Y"]))],
    ),
    "multiple_roles": lambda c, s: IdentityDirectory(
        people=[Person("P-W", ExternalIdentity("nf", "s-P-W"))],
        role_assignments=[RoleAssignment("P-W", W), RoleAssignment("P-W", E), RoleAssignment("P-W", A)],
        scope_assignments=[
            RoleScopeAssignment("P-W", r, RailwayScope(c, list(s))) for r in (W, E, A)
        ],
    ),
}


@pytest.mark.parametrize("change", sorted(CHANGED_DIRECTORIES))
def test_history_reads_identically_whatever_the_directory_becomes(rig, tmp_path, change):
    job = notified(rig, 1000.0)
    rig.svc.start_execution(job["job_id"], actor=rig.W, **start_execution_body(job))
    before = [
        (s.sequence, s.event.canonical_bytes())
        for s in JobHistoryRepository(rig.db).list_for_job(job["job_id"])
    ]

    changed = _compose_with(
        rig, tmp_path, CHANGED_DIRECTORIES[change](rig.corridor, rig.sections)
    )

    after = [
        (s.sequence, s.event.canonical_bytes())
        for s in changed.svc.history.list_for_job(job["job_id"])
    ]

    assert after == before
    assert {
        type(s.event.actor) for s in changed.svc.history.list_for_job(job["job_id"])
    } >= {RecordedActor, Actor}


def test_current_authorization_follows_the_directory_while_history_does_not(rig, tmp_path):
    job = rig.svc.create_job(request(), actor=rig.W)
    changed = _compose_with(rig, tmp_path, IdentityDirectory())

    with pytest.raises(AuthorizationDenied):
        changed.svc.job_history(job["job_id"], actor=rig.W)

    stored = changed.svc.history.list_for_job(job["job_id"])
    assert type(stored[0].event.actor) is RecordedActor
    assert stored[0].event.actor.actor_id == "P-W"


def test_rehydration_never_consults_the_identity_directory(rig, monkeypatch):
    job = notified(rig, 1000.0)
    rig.svc.start_execution(job["job_id"], actor=rig.W, **start_execution_body(job))

    def forbidden(*args, **kwargs):
        raise AssertionError("rehydration consulted the identity directory")

    for name in (
        "person_for",
        "select_role",
        "scopes_for_actor",
        "roles_for",
        "people",
        "role_assignments",
        "scope_assignments",
    ):
        if hasattr(IdentityDirectory, name):
            attribute = getattr(IdentityDirectory, name)
            if isinstance(attribute, property):
                monkeypatch.setattr(IdentityDirectory, name, property(forbidden))
            else:
                monkeypatch.setattr(IdentityDirectory, name, forbidden)

    history = JobHistoryRepository(rig.db)

    assert history.list_for_job(job["job_id"])
    assert history.list_for_jobs([job["job_id"]])[job["job_id"]]
    assert rehydrated_event().actor.actor_id == "P-W"


def test_rehydration_does_not_depend_on_the_auth_mode(rig):
    job = rig.svc.create_job(request(), actor=rig.W)
    demo = JobService(repository=JobRepository(rig.db))

    demo_events = demo.job_history(job["job_id"])
    enforced_events = rig.svc.job_history(job["job_id"], actor=rig.W)

    assert type(demo.authorization).__name__ == "UnenforcedPolicy"
    assert type(rig.svc.authorization) is AuthenticatedEnforcingPolicy
    assert [e.event.canonical_bytes() for e in demo_events] == [
        e.event.canonical_bytes() for e in enforced_events
    ]
    assert type(demo_events[0].event.actor) is RecordedActor


# ------------------------------------------ I. AST capability boundaries


def _source(rel: str) -> str:
    return (APP_ROOT / rel).read_text(encoding="utf-8")


def _imports(source: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_recorded_actor_module_reads_but_never_mints():
    source = _source("identity/recorded_actor.py")

    assert not references_capability(source)
    assert not constructs_actor_as_authenticated(source)
    assert "_AUTHENTICATION_CAPABILITY" not in source
    assert "authenticated_actor" not in source
    assert _imports(source) <= {
        "__future__",
        "dataclasses",
        "typing",
        "backend.app.identity.actor",
    }


def test_recorded_actor_never_subclasses_or_wraps_actor():
    tree = ast.parse(_source("identity/recorded_actor.py"))

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            assert node.bases == []

    calls = [
        getattr(n.func, "id", None)
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
    ]
    assert calls.count("Actor") == 1  # the non-authenticated branch only


@pytest.mark.parametrize("rel", ["jobs/events.py", "jobs/history.py"])
def test_events_and_history_do_not_name_the_value_or_the_factory(rel):
    source = _source(rel)

    assert not mentions_authenticated_value(source)
    assert not references_capability(source)
    assert not constructs_actor_as_authenticated(source)
    assert "authenticated_actor" not in source


def test_events_rehydrates_through_the_sanctioned_function_only():
    tree = ast.parse(_source("jobs/events.py"))
    from_dict = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "from_dict"
        and any(getattr(a, "arg", None) == "data" for a in n.args.args)
        and "JobEvent" in ast.dump(n.returns or ast.Constant(value=""))
    )
    called = {
        getattr(n.func, "id", None) for n in ast.walk(from_dict) if isinstance(n, ast.Call)
    }

    assert "rehydrate_actor" in called
    assert "Actor" not in called


def test_the_capability_and_naming_allowlists_are_narrowly_extended():
    assert ALLOWED_CAPABILITY_MODULES == {
        "identity/actor.py",
        "identity/authenticated_actor.py",
    }

    from backend.tests.test_slice10_2c_authenticated_assurance import (
        ALLOWED_VALUE_NAMING_MODULES,
        production_sources,
    )

    assert ALLOWED_VALUE_NAMING_MODULES - ALLOWED_CAPABILITY_MODULES == {
        "identity/authenticated_policy.py",
        "identity/recorded_actor.py",
    }
    assert {
        rel for rel, src in production_sources() if mentions_authenticated_value(src)
    } == set(ALLOWED_VALUE_NAMING_MODULES)


def test_recorded_actor_is_not_exported_from_the_identity_package():
    assert "RecordedActor" not in identity_package.__all__
    assert "rehydrate_actor" not in identity_package.__all__
    assert not hasattr(identity_package, "RecordedActor")
