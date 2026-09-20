"""Sprint 3 Slice 9: the accountability query service and its read APIs.

Where test_slice9_obligations.py pins the pure derivation over
hand-built histories, this drives the REAL service, repository,
lifecycle and HTTP API, with an injected clock so elapsed time is
chosen rather than waited for.

WHAT IS PINNED HERE
  QUERY     the eleven questions an accountability view must answer:
            what is owed, by which role, since when, until when, which
            event started the clock, which run it belongs to, and under
            which SLA policy version.
  CROSS-JOB one page of obligations over many jobs, one evaluation
            moment, one history read, with the existing keyset cursor.
  FILTERS   state, type, role, past-due and attention.
  EMPTY     a job that owes nothing answers 200/NONE, never 404.
  RUN       GET /v1/optimization-runs/{run_id}, including its own 404.
  HONESTY   every response states that its SLA values are ASSUMED.

Every test uses an isolated temporary database.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from contracts import DEFAULT_HORIZON_START

from backend.app.identity.actor import ActorRole, human_actor
from backend.app.identity.authorization import AuthorizationDenied, JobAction
from backend.app.jobs.events import JobEventType
from backend.app.jobs.lifecycle import proposal_run_id_of
from backend.app.jobs.models import JobCreateRequest
from backend.app.jobs.obligations import (
    ObligationReason,
    ObligationState,
    ObligationType,
)
from backend.app.jobs.optimization import JobOptimizationService
from backend.app.jobs.repository import JobRepository
from backend.app.jobs.service import JobService
from backend.app.jobs.sla_policy import ASSUMED_DEMO_SLA_VERSION
from backend.tests.execution_helpers import start_execution_body


WORKER = human_actor("WORKER-042", ActorRole.WORKER)
ENGINEER = human_actor("ENGINEER-003", ActorRole.ENGINEER)
AUTHORITY = human_actor("AUTHORITY-017", ActorRole.AUTHORITY)

WORKER_HEADERS = {"X-Actor-Id": "WORKER-042", "X-Actor-Role": "WORKER"}
AUTHORITY_HEADERS = {"X-Actor-Id": "AUTHORITY-017", "X-Actor-Role": "AUTHORITY"}
ENGINEER_HEADERS = {"X-Actor-Id": "ENGINEER-003", "X-Actor-Role": "ENGINEER"}

CORRIDOR = "CORRIDOR_A"
E = JobEventType

# Far enough past the fixed planning horizon that every committed block's
# planned window is historical - the situation the deployment is actually
# in, and the reason the execution SLA anchors on BLOCK_COMMITTED.
START = datetime.fromisoformat(DEFAULT_HORIZON_START) + timedelta(days=3650)


class MovableClock:
    """A clock a test drives. The whole point of finishing clock injection."""

    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> datetime:
        self.now = self.now + timedelta(**kwargs)
        return self.now


@pytest.fixture()
def clock() -> MovableClock:
    return MovableClock(START)


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "jobs.db"


@pytest.fixture()
def service(db_path: Path, clock: MovableClock) -> JobService:
    return JobService(repository=JobRepository(db_path), clock=clock)


@pytest.fixture()
def optimizer(service: JobService) -> JobOptimizationService:
    return JobOptimizationService(service)


def report(service, track_id="UP-1", distance_start=1000.0, severity="CRITICAL"):
    return service.create_job(
        JobCreateRequest(
            track_id=track_id,
            job_type="BALLAST_TAMPING",
            distance_start=distance_start,
            distance_end=distance_start + 400,
            workers_min=2,
            workers_max=4,
            description="Rail head crack observed during foot patrol",
            severity=severity,
        ),
        actor=WORKER,
    )


def optimize(optimizer):
    return optimizer.optimize_corridor(CORRIDOR, actor=ENGINEER)["optimization_run_id"]


def scheduled_job(service, optimizer, **kwargs):
    job = report(service, **kwargs)
    optimize(optimizer)
    stored = service.repository.get(job["job_id"])
    assert stored["status"] == "scheduled"
    return stored


def approve(service, job_id):
    run_id = proposal_run_id_of(service.repository.get(job_id))
    service.approve_proposal(job_id, actor=AUTHORITY, expected_proposal_run_id=run_id)
    return service.repository.get(job_id)


def notified_job(service, optimizer, **kwargs):
    job = scheduled_job(service, optimizer, **kwargs)
    return approve(service, job["job_id"])


def in_progress_job(service, optimizer, **kwargs):
    job = notified_job(service, optimizer, **kwargs)
    service.start_execution(job["job_id"], actor=WORKER, **start_execution_body(job))
    return service.repository.get(job["job_id"])


# ----------------------------------------------------------------------
# The eleven questions, over the real stack
# ----------------------------------------------------------------------


def test_a_scheduled_job_answers_every_accountability_question(
    service, optimizer, clock
):
    job = scheduled_job(service, optimizer)
    run_id = proposal_run_id_of(job)
    placement = [
        item.event
        for item in service.history.list_for_job(job["job_id"])
        if item.event.event_type in (E.BLOCK_PROPOSED, E.BLOCK_REPROPOSED)
    ][-1]

    clock.advance(hours=1)
    obligation = service.job_obligation(job["job_id"])

    # 1 what is owed, 6 which role owes it
    assert obligation.obligation_type is ObligationType.APPROVAL_PENDING
    assert obligation.owed_role is ActorRole.AUTHORITY
    # 7 since when, 9 which event started the clock
    assert obligation.clock_started_at == placement.occurred_at
    assert obligation.anchor_event_id == placement.event_id
    # 8 when it is due (CRITICAL approval SLA: 2h)
    assert obligation.due_at is not None
    assert obligation.elapsed_seconds == 3600
    # 10 which run it belongs to
    assert obligation.optimization_run_id == run_id
    # 11 which policy version produced it
    assert obligation.policy_version == ASSUMED_DEMO_SLA_VERSION
    assert obligation.policy_assumed is True


def test_elapsed_time_moves_the_obligation_up_the_ladder(service, optimizer, clock):
    job = scheduled_job(service, optimizer)
    job_id = job["job_id"]

    assert service.job_obligation(job_id).state is ObligationState.WITHIN_SLA

    clock.advance(hours=1, minutes=45)
    assert service.job_obligation(job_id).state is ObligationState.DUE_SOON

    clock.advance(hours=1)
    assert service.job_obligation(job_id).state is ObligationState.ESCALATED_L1

    clock.advance(hours=48)
    assert service.job_obligation(job_id).state is ObligationState.ESCALATED_L3


def test_approving_ends_the_approval_obligation_and_starts_the_execution_one(
    service, optimizer, clock
):
    job = scheduled_job(service, optimizer)
    job_id = job["job_id"]

    clock.advance(hours=10)
    assert service.job_obligation(job_id).is_past_due is True

    approve(service, job_id)
    obligation = service.job_obligation(job_id)

    assert obligation.obligation_type is ObligationType.EXECUTION_START_PENDING
    assert obligation.owed_role is ActorRole.WORKER
    # A brand-new clock, anchored on the commit that just happened.
    assert obligation.state is ObligationState.WITHIN_SLA
    assert obligation.elapsed_seconds == 0


def test_starting_execution_replaces_the_start_obligation_with_completion(
    service, optimizer, clock
):
    job = in_progress_job(service, optimizer)
    obligation = service.job_obligation(job["job_id"])

    assert obligation.obligation_type is ObligationType.COMPLETION_PENDING
    assert obligation.owed_role is ActorRole.WORKER
    assert obligation.reason_code is ObligationReason.COMPLETION_AWAITING_OUTCOME


def test_a_released_block_needs_replanning_and_is_never_reported_overdue(
    service, optimizer, clock
):
    job = notified_job(service, optimizer)
    job_id = job["job_id"]

    service.release_committed_block(
        job_id,
        actor=AUTHORITY,
        expected_proposal_run_id=proposal_run_id_of(job),
        reason="possession not granted by section controller",
    )

    clock.advance(days=400)
    obligation = service.job_obligation(job_id)

    assert obligation.obligation_type is ObligationType.REPLANNING_PENDING
    assert obligation.owed_role is ActorRole.ENGINEER
    assert obligation.is_past_due is False
    assert obligation.state is ObligationState.NO_SLA_DEFINED


def test_a_reproposal_resets_the_clock_over_the_real_optimizer(
    service, optimizer, clock
):
    job = scheduled_job(service, optimizer)
    job_id = job["job_id"]
    first_run = proposal_run_id_of(job)

    clock.advance(hours=20)
    assert service.job_obligation(job_id).is_past_due is True

    # A second run re-places the same job under a new run id.
    second_run = optimize(optimizer)
    obligation = service.job_obligation(job_id)

    assert second_run != first_run
    assert obligation.optimization_run_id == second_run
    assert obligation.state is ObligationState.WITHIN_SLA
    assert obligation.elapsed_seconds == 0


def test_a_job_that_owes_nothing_says_so_rather_than_refusing(service, optimizer):
    job = report(service)

    obligation = service.job_obligation(job["job_id"])

    assert obligation.obligation_type is ObligationType.NONE
    assert obligation.state is ObligationState.NONE
    assert obligation.owed_role is None
    assert (
        obligation.reason_code is ObligationReason.NO_OBLIGATION_AWAITING_OPTIMIZATION
    )


def test_an_unknown_job_is_a_key_error_not_an_empty_obligation(service):
    with pytest.raises(KeyError):
        service.job_obligation("JOB-DOES-NOT-EXIST")


# ----------------------------------------------------------------------
# Cross-job query
# ----------------------------------------------------------------------


def test_a_cross_job_page_evaluates_every_job_at_one_moment(
    service, optimizer, clock
):
    for offset in range(3):
        report(service, distance_start=1000.0 + offset * 1000)

    optimize(optimizer)
    clock.advance(hours=3)

    obligations, next_row, evaluated_at = service.obligations_page(limit=50)

    assert len(obligations) == 3
    assert next_row is None
    assert len({o.evaluated_at for o in obligations}) == 1
    assert all(o.obligation_type is ObligationType.APPROVAL_PENDING for o in obligations)


def test_the_cross_job_read_uses_one_history_query_not_one_per_job(
    service, optimizer, clock, monkeypatch
):
    """The N+1 this slice exists to avoid - asserted, not assumed."""

    for offset in range(4):
        report(service, distance_start=1000.0 + offset * 1000)

    optimize(optimizer)

    calls = {"per_job": 0, "batched": 0}
    real_batched = service.history.list_for_jobs
    real_single = service.history.list_for_job

    def counted_batched(job_ids):
        calls["batched"] += 1
        return real_batched(job_ids)

    def counted_single(job_id):
        calls["per_job"] += 1
        return real_single(job_id)

    monkeypatch.setattr(service.history, "list_for_jobs", counted_batched)
    monkeypatch.setattr(service.history, "list_for_job", counted_single)

    obligations, _, _ = service.obligations_page(limit=50)

    assert len(obligations) == 4
    assert calls == {"per_job": 0, "batched": 1}


def test_the_page_follows_the_existing_keyset_cursor(service, optimizer, clock):
    for offset in range(5):
        report(service, distance_start=1000.0 + offset * 1000)

    optimize(optimizer)

    seen = []
    after = None

    while True:
        page, next_row, _ = service.obligations_page(limit=2, after=after)
        seen.extend(o.job_id for o in page)

        if next_row is None:
            break

        after = (next_row["created_at"], next_row["job_id"])

    assert len(seen) == 5
    assert len(set(seen)) == 5


def test_completed_jobs_are_not_scanned_as_obligation_candidates(service):
    from backend.app.jobs.obligations import OBLIGATION_CANDIDATE_STATUSES

    assert "completed" not in OBLIGATION_CANDIDATE_STATUSES
    assert set(OBLIGATION_CANDIDATE_STATUSES) == {
        "reported",
        "scheduled",
        "notified",
        "in_progress",
    }


def test_an_empty_statuses_filter_is_an_empty_page_not_an_unfiltered_one(
    service, optimizer
):
    report(service)
    optimize(optimizer)

    obligations, _, _ = service.obligations_page(limit=50, statuses=[])

    assert obligations == []


# ----------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------


@pytest.fixture()
def client(db_path, clock, monkeypatch):
    """A TestClient whose router service is pinned to this test's database."""

    import backend.app.jobs.router as router_module
    from backend.app.api.main import app

    isolated = JobService(repository=JobRepository(db_path), clock=clock)
    monkeypatch.setattr(router_module, "service", isolated)

    return TestClient(app), isolated


def test_get_one_jobs_obligation_over_http(client, optimizer):
    http, service = client
    optimizer = JobOptimizationService(service)
    job = scheduled_job(service, optimizer)

    response = http.get(
        f"/v1/jobs/{job['job_id']}/obligation", headers=AUTHORITY_HEADERS
    )
    body = response.json()

    assert response.status_code == 200
    assert body["obligation_type"] == "APPROVAL_PENDING"
    assert body["owed_role"] == "AUTHORITY"
    assert body["policy_version"] == ASSUMED_DEMO_SLA_VERSION
    assert body["policy_assumed"] is True
    assert body["anchor_event_id"].startswith("EVT-")


def test_a_job_with_no_obligation_is_200_not_404_over_http(client):
    http, service = client
    job = report(service)

    response = http.get(
        f"/v1/jobs/{job['job_id']}/obligation", headers=AUTHORITY_HEADERS
    )

    assert response.status_code == 200
    assert response.json()["obligation_type"] == "NONE"


def test_an_unknown_job_obligation_is_404_with_its_own_code(client):
    http, _ = client

    response = http.get("/v1/jobs/JOB-NOPE/obligation", headers=AUTHORITY_HEADERS)

    assert (response.status_code, response.json()["code"]) == (404, "JOB_NOT_FOUND")


def test_list_obligations_over_http_states_its_policy_is_assumed(client, clock):
    http, service = client
    optimizer = JobOptimizationService(service)
    report(service)
    optimize(optimizer)
    clock.advance(hours=5)

    response = http.get("/v1/obligations", headers=AUTHORITY_HEADERS)
    body = response.json()

    assert response.status_code == 200
    assert body["policy_assumed"] is True
    assert body["policy_version"] == ASSUMED_DEMO_SLA_VERSION
    assert len(body["items"]) == 1
    assert body["items"][0]["policy_assumed"] is True
    assert set(body) == {"items", "next_cursor", "evaluated_at", "policy_version",
                         "policy_assumed"}


@pytest.mark.parametrize(
    "query,expected",
    [
        ("role=AUTHORITY", 1),
        ("role=WORKER", 0),
        ("obligation_type=APPROVAL_PENDING", 1),
        ("obligation_type=COMPLETION_PENDING", 0),
        ("state=ESCALATED_L1", 1),
        ("state=WITHIN_SLA", 0),
        ("past_due=true", 1),
        ("past_due=false", 0),
        ("attention=true", 0),
        ("attention=false", 1),
    ],
)
def test_obligation_filters_over_http(client, clock, query, expected):
    http, service = client
    optimizer = JobOptimizationService(service)
    report(service)
    optimize(optimizer)
    # CRITICAL approval SLA is 2h; L1 begins at the deadline, L2 at 4h.
    clock.advance(hours=3)

    response = http.get(f"/v1/obligations?{query}", headers=AUTHORITY_HEADERS)

    assert response.status_code == 200
    assert len(response.json()["items"]) == expected


def test_a_filtered_page_may_be_short_while_the_cursor_is_still_set(client, clock):
    """Documented pagination semantics: filters apply within a page of jobs."""

    http, service = client
    optimizer = JobOptimizationService(service)

    for offset in range(3):
        report(service, distance_start=1000.0 + offset * 1000)

    optimize(optimizer)

    response = http.get(
        "/v1/obligations?limit=1&role=WORKER", headers=AUTHORITY_HEADERS
    )
    body = response.json()

    assert body["items"] == []
    assert body["next_cursor"] is not None


def test_a_malformed_cursor_is_refused_with_the_existing_code(client):
    http, _ = client

    response = http.get("/v1/obligations?cursor=not-a-cursor", headers=AUTHORITY_HEADERS)

    assert (response.status_code, response.json()["code"]) == (400, "INVALID_CURSOR")


# ----------------------------------------------------------------------
# Optimization run lookup
# ----------------------------------------------------------------------


def test_an_optimization_run_is_readable_in_process(service, optimizer):
    report(service)
    run_id = optimize(optimizer)

    run = service.optimization_run(run_id)

    assert run.run_id == run_id
    assert run.corridor_id == CORRIDOR
    assert run.request_json


def test_an_unknown_optimization_run_is_a_key_error(service):
    with pytest.raises(KeyError):
        service.optimization_run("RUN-NOPE")


def test_an_optimization_run_is_readable_over_http(client):
    http, service = client
    optimizer = JobOptimizationService(service)
    report(service)
    run_id = optimize(optimizer)

    response = http.get(f"/v1/optimization-runs/{run_id}", headers=ENGINEER_HEADERS)
    body = response.json()

    assert response.status_code == 200
    assert body["run_id"] == run_id
    assert body["solver_status"]
    assert body["provenance_snapshot_json"]


def test_an_unknown_optimization_run_is_404_with_its_own_code(client):
    http, _ = client

    response = http.get("/v1/optimization-runs/RUN-NOPE", headers=ENGINEER_HEADERS)

    assert (response.status_code, response.json()["code"]) == (
        404,
        "OPTIMIZATION_RUN_NOT_FOUND",
    )


def test_the_obligation_names_a_run_that_actually_resolves(client, clock):
    """The two reads compose: an obligation's run id is a real lookup."""

    http, service = client
    optimizer = JobOptimizationService(service)
    job = scheduled_job(service, optimizer)

    obligation = http.get(
        f"/v1/jobs/{job['job_id']}/obligation", headers=AUTHORITY_HEADERS
    ).json()
    run = http.get(
        f"/v1/optimization-runs/{obligation['optimization_run_id']}",
        headers=ENGINEER_HEADERS,
    ).json()

    assert run["run_id"] == obligation["optimization_run_id"]


# ----------------------------------------------------------------------
# The reads pass the same authorization seam as every other read
# ----------------------------------------------------------------------


class DenyPolicy:
    enforcing = True

    def __init__(self, *denied):
        self.denied = set(denied)

    def authorize(self, actor, action):
        if action in self.denied:
            raise AuthorizationDenied(actor, action)


@pytest.mark.parametrize(
    "action,call",
    [
        (
            JobAction.READ_JOB_OBLIGATIONS,
            lambda s: s.job_obligation("JOB-ANY"),
        ),
        (
            JobAction.READ_JOB_OBLIGATIONS,
            lambda s: s.obligations_page(limit=10),
        ),
        (
            JobAction.READ_OPTIMIZATION_RUN,
            lambda s: s.optimization_run("RUN-ANY"),
        ),
    ],
)
def test_a_denied_read_is_refused_before_anything_is_read(db_path, action, call):
    service = JobService(
        repository=JobRepository(db_path),
        authorization=DenyPolicy(action),
    )

    with pytest.raises(AuthorizationDenied):
        call(service)


# ----------------------------------------------------------------------
# Fail closed: an incoherent history is refused, never papered over
# ----------------------------------------------------------------------


def _repoint_proposal_run(db_path, job_id, run_id):
    """Point a scheduled job at a run its history knows nothing about."""

    import json
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(db_path)) as conn, conn:
        raw = conn.execute(
            "SELECT block_candidate_json FROM maintenance_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()[0]

        block = json.loads(raw)
        block.setdefault("metadata", {})["proposal_run_id"] = run_id

        conn.execute(
            "UPDATE maintenance_jobs SET block_candidate_json = ? WHERE job_id = ?",
            (json.dumps(block), job_id),
        )


def test_a_job_whose_row_and_history_disagree_is_refused_not_guessed(
    service, optimizer, db_path
):
    job = scheduled_job(service, optimizer)
    _repoint_proposal_run(db_path, job["job_id"], "RUN-THAT-NEVER-RAN")

    from backend.app.jobs.obligations import ObligationIntegrityError

    with pytest.raises(ObligationIntegrityError):
        service.job_obligation(job["job_id"])


def test_an_incoherent_history_is_409_over_http_never_a_fabricated_deadline(
    client, db_path
):
    http, service = client
    optimizer = JobOptimizationService(service)
    job = scheduled_job(service, optimizer)
    _repoint_proposal_run(db_path, job["job_id"], "RUN-THAT-NEVER-RAN")

    response = http.get(
        f"/v1/jobs/{job['job_id']}/obligation", headers=AUTHORITY_HEADERS
    )

    assert response.status_code == 409
    assert response.json()["code"] == "OBLIGATION_STATE_INCONSISTENT"
    # A refusal, not a stack trace.
    assert "Traceback" not in response.json()["detail"]


def test_a_cross_job_page_fails_closed_rather_than_skipping_a_broken_job(
    client, db_path
):
    """One incoherent job refuses the whole page: silently omitting it
    would under-report accountability, which is the failure this view
    exists to prevent."""

    http, service = client
    optimizer = JobOptimizationService(service)
    good = scheduled_job(service, optimizer, distance_start=1000.0)
    bad = scheduled_job(service, optimizer, distance_start=5000.0)
    _repoint_proposal_run(db_path, bad["job_id"], "RUN-THAT-NEVER-RAN")

    response = http.get("/v1/obligations", headers=AUTHORITY_HEADERS)

    assert response.status_code == 409
    assert response.json()["code"] == "OBLIGATION_STATE_INCONSISTENT"
    assert good["job_id"]


def test_an_unresolvable_stored_severity_is_refused_with_its_own_code(
    client, db_path
):
    import json
    import sqlite3
    from contextlib import closing

    http, service = client
    optimizer = JobOptimizationService(service)
    job = scheduled_job(service, optimizer)

    with closing(sqlite3.connect(db_path)) as conn, conn:
        raw = conn.execute(
            "SELECT block_candidate_json FROM maintenance_jobs WHERE job_id = ?",
            (job["job_id"],),
        ).fetchone()[0]
        block = json.loads(raw)
        block["metadata"]["reported_severity"] = "CATASTROPHIC"
        conn.execute(
            "UPDATE maintenance_jobs SET block_candidate_json = ? WHERE job_id = ?",
            (json.dumps(block), job["job_id"]),
        )

    response = http.get(
        f"/v1/jobs/{job['job_id']}/obligation", headers=AUTHORITY_HEADERS
    )

    assert (response.status_code, response.json()["code"]) == (
        409,
        "SLA_POLICY_UNRESOLVABLE",
    )
