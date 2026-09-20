"""Sprint 3 Slice 7: the frozen frontend-facing API contract.

WHAT IS PINNED HERE
  VERSIONING  every jobs-lifecycle route lives under /v1 and nowhere
              else; the legacy /optimize, /recover and /health routes are
              untouched, unversioned, and keep their default error body.
  ERRORS      every v1 4xx is {code, detail}; each domain exception has
              its own stable code; statuses are unchanged.
  PAGINATION  GET /v1/jobs is {items, next_cursor}, stable and complete.
  NO-PROPOSAL the reason a job has no current proposal names WHO decided
              (authority reject/postpone/release, worker not-completed,
              optimizer refusal) - an authority's decision is never
              reported as an optimizer outcome.
  PINNED      the v1 contract has no unpinned commit path: /notify needs
              expected_proposal_run_id exactly as /proposal/approve does.
  ACTOR       the X-Actor-Id / X-Actor-Role identity contract.

Nothing here touches the solver, the schema or the lifecycle.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from contracts import DEFAULT_HORIZON_START

from backend.app.api.errors import ApiError, ErrorCode
from backend.app.api.main import app
from backend.app.identity.actor import ActorRole, human_actor
from backend.app.jobs import router as jobs_router_module
from backend.app.jobs.events import JobEventType, make_event
from backend.app.jobs.history import StoredJobEvent
from backend.app.jobs.router import _TRANSLATIONS, _api_error
from backend.app.jobs.router import service as router_service
from backend.app.jobs.service import _no_proposal_reason
from backend.tests.execution_helpers import start_execution_body


client = TestClient(app)

WORKER = {"X-Actor-Id": "WORKER-042", "X-Actor-Role": "WORKER"}
ENGINEER = {"X-Actor-Id": "ENGINEER-003", "X-Actor-Role": "ENGINEER"}
AUTHORITY = {"X-Actor-Id": "AUTHORITY-017", "X-Actor-Role": "AUTHORITY"}

E = JobEventType


@pytest.fixture(autouse=True)
def clean_jobs_table():
    def wipe():
        with closing(
            sqlite3.connect(router_service.repository.db_path)
        ) as conn, conn:
            conn.execute("DELETE FROM maintenance_jobs")
            conn.commit()

    wipe()
    yield
    wipe()


def create(track_id="UP-1", job_type="BALLAST_TAMPING", distance_start=1000.0):
    response = client.post(
        "/v1/jobs",
        json={
            "track_id": track_id,
            "job_type": job_type,
            "distance_start": distance_start,
            "distance_end": distance_start + 400,
            "workers_min": 2,
            "workers_max": 4,
            "description": "Field-reported defect",
        },
        headers=WORKER,
    )
    assert response.status_code == 201, response.text
    return response.json()


def get_job(job_id):
    return client.get(f"/v1/jobs/{job_id}").json()


def optimized(job=None):
    """A job that has been optimized into a 'scheduled' proposal."""

    job = job or create()
    assert client.post(
        "/v1/corridors/CORRIDOR_A/optimize-jobs", headers=ENGINEER
    ).status_code == 200
    stored = get_job(job["job_id"])
    assert stored["status"] == "scheduled"
    return stored


def approved(job=None):
    stored = optimized(job)
    response = client.post(
        f"/v1/jobs/{stored['job_id']}/proposal/approve",
        json={"expected_proposal_run_id": stored["proposal_run_id"]},
        headers=AUTHORITY,
    )
    assert response.status_code == 200, response.text
    return get_job(stored["job_id"])


def no_proposal(job_id):
    response = client.get(f"/v1/jobs/{job_id}/proposal", headers=WORKER)
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "NO_CURRENT_PROPOSAL"
    return body["detail"]


# ----------------------------------------------------------------------
# 7.1 Versioning: v1 for the lifecycle, legacy untouched
# ----------------------------------------------------------------------


def _operations():
    return {
        (method.upper(), path)
        for path, item in app.openapi()["paths"].items()
        for method in item
    }


V1_OPERATIONS = {
    ("POST", "/v1/jobs"),
    ("GET", "/v1/jobs"),
    ("GET", "/v1/jobs/{job_id}"),
    ("GET", "/v1/jobs/{job_id}/history"),
    ("GET", "/v1/jobs/{job_id}/proposal"),
    ("GET", "/v1/jobs/{job_id}/execution"),
    ("POST", "/v1/corridors/{corridor_id}/optimize-jobs"),
    ("POST", "/v1/jobs/{job_id}/notify"),
    ("POST", "/v1/jobs/{job_id}/proposal/approve"),
    ("POST", "/v1/jobs/{job_id}/proposal/reject"),
    ("POST", "/v1/jobs/{job_id}/proposal/postpone"),
    ("POST", "/v1/jobs/{job_id}/proposal/release"),
    ("POST", "/v1/jobs/{job_id}/execution/start"),
    ("POST", "/v1/jobs/{job_id}/execution/complete"),
    ("POST", "/v1/jobs/{job_id}/execution/not-completed"),
}

LEGACY_OPERATIONS = {
    ("POST", "/optimize"),
    ("POST", "/recover"),
    ("GET", "/health"),
}


def test_exact_v1_and_legacy_route_inventory():
    assert _operations() == V1_OPERATIONS | LEGACY_OPERATIONS


def test_unversioned_jobs_routes_no_longer_exist():
    assert client.get("/jobs").status_code == 404
    assert client.post("/corridors/CORRIDOR_A/optimize-jobs").status_code == 404


def test_legacy_routes_keep_their_default_error_body():
    """/optimize's 422 is FastAPI's own {"detail": [...]}, with no `code`."""

    response = client.post("/optimize", json={"not": "a request"})

    assert response.status_code == 422
    assert set(response.json()) == {"detail"}
    assert isinstance(response.json()["detail"], list)


def test_health_is_unversioned_and_unchanged():
    assert client.get("/health").json() == {"status": "healthy"}


# ----------------------------------------------------------------------
# 7.2 Structured error codes
# ----------------------------------------------------------------------


def test_every_translated_exception_has_its_own_code():
    codes = [code for _, _, code in _TRANSLATIONS]

    assert len(codes) == len(set(codes))

    # The seven exceptions the old single 409 collapsed, plus the
    # evidence/transition split of the old generic 400.
    by_name = {exc_type.__name__: code for exc_type, _, code in _TRANSLATIONS}

    for name, code in {
        "TerminalJobError": ErrorCode.JOB_TERMINAL,
        "CommittedJobError": ErrorCode.JOB_COMMITTED,
        "CommittedStateIntegrityError": ErrorCode.COMMITTED_STATE_INCONSISTENT,
        "StaleProposalError": ErrorCode.STALE_PROPOSAL,
        "StaleExecutionError": ErrorCode.STALE_EXECUTION,
        "ExecutionIntegrityError": ErrorCode.EXECUTION_HISTORY_INCONSISTENT,
        "ConcurrentJobModificationError": ErrorCode.CONCURRENT_MODIFICATION,
        "EvidenceValidationError": ErrorCode.EVIDENCE_INVALID,
        "InvalidTransitionError": ErrorCode.INVALID_TRANSITION,
    }.items():
        assert by_name[name] is code


def test_translation_order_puts_subclasses_before_bases():
    types = [exc_type for exc_type, _, _ in _TRANSLATIONS]

    for i, base in enumerate(types):
        for subclass in types[i + 1 :]:
            assert not issubclass(subclass, base), (
                f"{subclass.__name__} is shadowed by earlier {base.__name__}"
            )


def test_api_error_keeps_status_and_code_per_exception():
    for exc_type, status, code in _TRANSLATIONS:
        error = _api_error(exc_type.__new__(exc_type))
        assert isinstance(error, ApiError)
        assert (error.status_code, error.code) == (status, code)

    missing = _api_error(KeyError("Job 'X' not found"))
    assert (missing.status_code, missing.code) == (404, ErrorCode.JOB_NOT_FOUND)
    assert missing.detail == "Job 'X' not found"

    corridor = _api_error(KeyError("nope"), not_found=ErrorCode.CORRIDOR_NOT_FOUND)
    assert corridor.code is ErrorCode.CORRIDOR_NOT_FOUND


def test_error_body_is_code_and_detail_only():
    response = client.get("/v1/jobs/JOB-DOES-NOT-EXIST")

    assert response.status_code == 404
    assert response.json() == {
        "code": "JOB_NOT_FOUND",
        "detail": "Job 'JOB-DOES-NOT-EXIST' not found",
    }


def test_missing_job_and_unknown_corridor_have_distinct_codes():
    job = client.get("/v1/jobs/NOPE/history", headers=WORKER)
    corridor = client.post("/v1/corridors/CORRIDOR_ZZZ/optimize-jobs", headers=ENGINEER)

    assert (job.status_code, job.json()["code"]) == (404, "JOB_NOT_FOUND")
    assert (corridor.status_code, corridor.json()["code"]) == (404, "CORRIDOR_NOT_FOUND")


def test_stale_and_state_conflicts_are_told_apart():
    job = optimized()
    run_id = job["proposal_run_id"]

    stale = client.post(
        f"/v1/jobs/{job['job_id']}/proposal/approve",
        json={"expected_proposal_run_id": "RUN-STALE"},
        headers=AUTHORITY,
    )
    assert (stale.status_code, stale.json()["code"]) == (409, "STALE_PROPOSAL")

    approve = client.post(
        f"/v1/jobs/{job['job_id']}/proposal/approve",
        json={"expected_proposal_run_id": run_id},
        headers=AUTHORITY,
    )
    assert approve.status_code == 200

    # A committed job has no proposal any more: a different code and cause.
    again = client.get(f"/v1/jobs/{job['job_id']}/proposal", headers=WORKER)
    assert (again.status_code, again.json()["code"]) == (409, "NO_CURRENT_PROPOSAL")


def test_no_eligible_jobs_has_its_own_code():
    response = client.post("/v1/corridors/CORRIDOR_A/optimize-jobs", headers=ENGINEER)

    assert response.status_code == 409
    assert response.json()["code"] == "NO_ELIGIBLE_JOBS"


def _start_body(run_id, **overrides):
    body = {
        "expected_proposal_run_id": run_id,
        "actual_start_at": "2026-09-10T10:00:00+05:30",
        "before_work_evidence": [
            {
                "evidence_reference": "before/photo-1.jpg",
                "evidence_kind": "PHOTO",
                "captured_at": "2026-09-10T09:59:00+05:30",
            }
        ],
    }
    body.update(overrides)
    return body


def test_evidence_and_transition_400s_are_told_apart():
    job = approved()
    stored = get_job(job["job_id"])
    url = f"/v1/jobs/{job['job_id']}/execution/start"

    # Semantic evidence problem (future capture time), raised inside the
    # lifecycle plan as a refused transition.
    future = start_execution_body(stored)
    future["before_work_evidence"][0]["captured_at"] = "2999-01-01T00:00:00+05:30"
    response = client.post(url, json=future, headers=WORKER)
    assert (response.status_code, response.json()["code"]) == (400, "EVIDENCE_INVALID")

    # Shape-level evidence problem (no UTC offset), raised at EvidenceItem
    # construction before the lifecycle is consulted.
    naive = start_execution_body(stored)
    naive["before_work_evidence"][0]["captured_at"] = "2026-09-10T09:59:00"
    response = client.post(url, json=naive, headers=WORKER)
    assert (response.status_code, response.json()["code"]) == (400, "EVIDENCE_INVALID")

    # A genuinely illegal transition is a different code.
    fresh = create(track_id="DOWN-1", distance_start=2000.0)
    response = client.post(
        f"/v1/jobs/{fresh['job_id']}/execution/start",
        json=_start_body("RUN-NONE"),
        headers=WORKER,
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_TRANSITION"


def test_v1_validation_errors_use_the_envelope_and_legacy_do_not():
    response = client.post("/v1/jobs", json={"track_id": "UP-1"}, headers=WORKER)

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert isinstance(body["detail"], list)


# ----------------------------------------------------------------------
# 7.3 Pagination
# ----------------------------------------------------------------------


def test_pagination_is_complete_stable_and_duplicate_free():
    created = {
        create(track_id="UP-1", distance_start=1000.0 + 500 * i)["job_id"]
        for i in range(5)
    }

    seen, cursor, pages = [], None, 0

    while True:
        params = {"limit": 2}

        if cursor:
            params["cursor"] = cursor

        page = client.get("/v1/jobs", params=params).json()
        assert set(page) == {"items", "next_cursor"}
        assert len(page["items"]) <= 2
        seen.extend(item["job_id"] for item in page["items"])
        pages += 1
        cursor = page["next_cursor"]

        if cursor is None:
            break

    assert pages == 3
    assert len(seen) == len(set(seen)) == 5
    assert set(seen) == created

    unpaged = [
        item["job_id"] for item in client.get("/v1/jobs", params={"limit": 200}).json()["items"]
    ]
    assert seen == unpaged


def test_exact_page_boundary_has_no_trailing_cursor():
    for i in range(4):
        create(distance_start=1000.0 + 500 * i)

    page = client.get("/v1/jobs", params={"limit": 4}).json()

    assert len(page["items"]) == 4
    assert page["next_cursor"] is None


def test_pagination_respects_the_status_filter():
    for i in range(3):
        create(distance_start=1000.0 + 500 * i)

    page = client.get("/v1/jobs", params={"status": "reported", "limit": 2}).json()
    assert len(page["items"]) == 2 and page["next_cursor"]

    rest = client.get(
        "/v1/jobs",
        params={"status": "reported", "limit": 2, "cursor": page["next_cursor"]},
    ).json()
    assert len(rest["items"]) == 1 and rest["next_cursor"] is None

    assert client.get("/v1/jobs", params={"status": "scheduled"}).json() == {
        "items": [],
        "next_cursor": None,
    }


def test_bad_cursor_and_bad_limits_are_refused_with_codes():
    bad = client.get("/v1/jobs", params={"cursor": "not-a-cursor"})
    assert (bad.status_code, bad.json()["code"]) == (400, "INVALID_CURSOR")

    for limit in (0, 201, -1):
        response = client.get("/v1/jobs", params={"limit": limit})
        assert (response.status_code, response.json()["code"]) == (422, "VALIDATION_ERROR")


# ----------------------------------------------------------------------
# 7.4 History-aware "no current proposal" reason
# ----------------------------------------------------------------------


def test_never_optimized_job_says_so():
    job = create()

    assert "has not been through optimization yet" in no_proposal(job["job_id"])


def test_genuine_optimizer_refusal_is_still_reported_as_the_optimizers():
    for i in range(8):
        create(job_type="TRACK_RENEWAL", distance_start=1000 + 400 * i)

    body = client.post("/v1/corridors/CORRIDOR_A/optimize-jobs", headers=ENGINEER).json()
    refused = body["unscheduled"][0]

    detail = no_proposal(refused["job_id"])

    assert "considered and left UNSCHEDULED" in detail
    assert refused["reason"] in detail
    assert "AUTHORITY" not in detail


def test_authority_reject_is_an_authority_decision_not_an_optimizer_outcome():
    job = optimized()

    response = client.post(
        f"/v1/jobs/{job['job_id']}/proposal/reject",
        json={"expected_proposal_run_id": job["proposal_run_id"], "reason": "unsafe window"},
        headers=AUTHORITY,
    )
    assert response.status_code == 200, response.text

    detail = no_proposal(job["job_id"])

    assert "REJECTED" in detail
    assert "AUTHORITY 'AUTHORITY-017'" in detail
    assert "unsafe window" in detail
    assert "considered and left UNSCHEDULED" not in detail


def test_authority_postpone_is_reported_with_its_date():
    job = optimized()
    selected = (datetime.fromisoformat(DEFAULT_HORIZON_START) + timedelta(days=1)).date().isoformat()

    response = client.post(
        f"/v1/jobs/{job['job_id']}/proposal/postpone",
        json={
            "expected_proposal_run_id": job["proposal_run_id"],
            "reason": "line blocked for a VIP movement",
            "selected_date": selected,
        },
        headers=AUTHORITY,
    )
    assert response.status_code == 200, response.text

    detail = no_proposal(job["job_id"])

    assert "POSTPONED" in detail
    assert f"until {selected}" in detail
    assert "AUTHORITY 'AUTHORITY-017'" in detail
    assert "line blocked for a VIP movement" in detail
    assert "considered and left UNSCHEDULED" not in detail


def test_authority_release_is_reported_as_the_release():
    job = approved()

    response = client.post(
        f"/v1/jobs/{job['job_id']}/proposal/release",
        json={
            "expected_proposal_run_id": job["proposal_run_id"],
            "reason": "possession not granted",
        },
        headers=AUTHORITY,
    )
    assert response.status_code == 200, response.text
    assert get_job(job["job_id"])["status"] == "reported"

    detail = no_proposal(job["job_id"])

    assert "RELEASED" in detail
    assert "AUTHORITY 'AUTHORITY-017'" in detail
    assert "possession not granted" in detail
    assert "considered and left UNSCHEDULED" not in detail


def test_worker_not_completed_is_reported_as_a_field_decision():
    job = approved()
    stored = get_job(job["job_id"])

    started = client.post(
        f"/v1/jobs/{job['job_id']}/execution/start",
        json=start_execution_body(stored),
        headers=WORKER,
    )
    assert started.status_code == 200, started.text
    execution = started.json()["execution"]

    ended = client.post(
        f"/v1/jobs/{job['job_id']}/execution/not-completed",
        json={
            "execution_id": execution["execution_id"],
            "actual_end_at": execution["actual_start_at"],
            "reason": "rail temperature too high",
        },
        headers=WORKER,
    )
    assert ended.status_code == 200, ended.text

    detail = no_proposal(job["job_id"])

    assert "NOT COMPLETED" in detail
    assert "WORKER 'WORKER-042'" in detail
    assert "rail temperature too high" in detail
    assert "considered and left UNSCHEDULED" not in detail


def _stored(job_id, event_type, actor, reason):
    return StoredJobEvent(sequence=0, event=make_event(job_id, event_type, actor, reason=reason))


def test_latest_decision_wins_when_the_optimizer_acts_after_a_human():
    from backend.app.identity.actor import OPTIMIZER

    authority = human_actor("AUTHORITY-017", ActorRole.AUTHORITY)
    job = {"job_id": "J", "status": "reported", "last_refusal_reason": "solver: no window"}

    rejected_then_refused = [
        _stored("J", E.PROPOSAL_REJECTED, authority, "unsafe"),
        _stored("J", E.OPTIMIZATION_REFUSED, OPTIMIZER, "solver: no window"),
    ]
    refused_then_postponed = [
        _stored("J", E.OPTIMIZATION_REFUSED, OPTIMIZER, "solver: no window"),
        _stored("J", E.PROPOSAL_POSTPONED, authority, "later"),
    ]
    noise_after_decision = [
        _stored("J", E.PROPOSAL_REJECTED, authority, "unsafe"),
        _stored("J", E.OPTIMIZATION_REQUESTED, OPTIMIZER, None),
    ]

    assert "considered and left UNSCHEDULED" in _no_proposal_reason(job, rejected_then_refused)
    assert "POSTPONED" in _no_proposal_reason(job, refused_then_postponed)
    assert "REJECTED" in _no_proposal_reason(job, noise_after_decision)

    # No decision event at all (legacy row): the original wording.
    assert "considered and left UNSCHEDULED" in _no_proposal_reason(job, [])


def test_other_statuses_keep_their_reasons():
    for status, fragment in (
        ("notified", "already committed"),
        ("in_progress", "being executed"),
        ("completed", "completed"),
    ):
        assert fragment in _no_proposal_reason({"status": status})


def test_no_proposal_reason_changes_no_stored_state():
    job = optimized()
    client.post(
        f"/v1/jobs/{job['job_id']}/proposal/reject",
        json={"expected_proposal_run_id": job["proposal_run_id"], "reason": "unsafe window"},
        headers=AUTHORITY,
    )
    before = get_job(job["job_id"])
    history_before = client.get(f"/v1/jobs/{job['job_id']}/history", headers=WORKER).json()

    no_proposal(job["job_id"])

    assert get_job(job["job_id"]) == before
    assert client.get(f"/v1/jobs/{job['job_id']}/history", headers=WORKER).json() == history_before


# ----------------------------------------------------------------------
# 7.6 No unpinned commit path in v1
# ----------------------------------------------------------------------


def _events(job_id):
    return client.get(f"/v1/jobs/{job_id}/history", headers=WORKER).json()["events"]


def test_notify_without_a_body_is_refused_and_commits_nothing():
    job = optimized()
    events_before = len(_events(job["job_id"]))

    for kwargs in ({}, {"json": {}}, {"json": {"expected_proposal_run_id": ""}}):
        response = client.post(
            f"/v1/jobs/{job['job_id']}/notify", headers=AUTHORITY, **kwargs
        )
        assert (response.status_code, response.json()["code"]) == (422, "VALIDATION_ERROR")

    assert get_job(job["job_id"])["status"] == "scheduled"
    assert len(_events(job["job_id"])) == events_before


def test_notify_with_a_stale_run_is_409_and_recorded():
    job = optimized()

    response = client.post(
        f"/v1/jobs/{job['job_id']}/notify",
        json={"expected_proposal_run_id": "RUN-STALE"},
        headers=AUTHORITY,
    )

    assert (response.status_code, response.json()["code"]) == (409, "STALE_PROPOSAL")
    assert get_job(job["job_id"])["status"] == "scheduled"
    assert _events(job["job_id"])[-1]["event_type"] == "TRANSITION_REJECTED"


def test_notify_with_the_current_run_commits():
    job = optimized()

    response = client.post(
        f"/v1/jobs/{job['job_id']}/notify",
        json={"expected_proposal_run_id": job["proposal_run_id"]},
        headers=AUTHORITY,
    )

    assert response.status_code == 200, response.text
    assert response.json()["job"]["status"] == "notified"


def test_every_v1_commit_route_requires_the_run_pin():
    schema = app.openapi()

    for path in ("/v1/jobs/{job_id}/notify", "/v1/jobs/{job_id}/proposal/approve"):
        body = schema["paths"][path]["post"]["requestBody"]
        assert body["required"] is True

        ref = body["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
        assert "expected_proposal_run_id" in schema["components"]["schemas"][ref]["required"]


def test_in_process_notify_keeps_its_optional_pin_for_direct_callers():
    import inspect

    from backend.app.jobs.service import JobService

    parameter = inspect.signature(JobService.notify).parameters["expected_proposal_run_id"]
    assert parameter.default is None


# ----------------------------------------------------------------------
# 7.5 Actor-header identity contract
# ----------------------------------------------------------------------


def test_actor_headers_are_documented_on_every_actor_aware_v1_route():
    schema = app.openapi()

    for path in (
        "/v1/jobs",
        "/v1/jobs/{job_id}/proposal/approve",
        "/v1/jobs/{job_id}/execution/start",
    ):
        for method, operation in schema["paths"][path].items():
            if method != "post":
                continue

            names = {
                p["name"].lower(): p for p in operation.get("parameters", [])
                if p["in"] == "header"
            }
            assert {"x-actor-id", "x-actor-role"} <= set(names), path
            assert "NOT verified" in names["x-actor-role"]["description"]


@pytest.mark.parametrize(
    "headers",
    [{"X-Actor-Id": "WORKER-042"}, {"X-Actor-Role": "WORKER"}],
)
def test_actor_headers_are_both_or_neither(headers):
    response = client.get("/v1/jobs/JOB-X/history", headers=headers)

    assert (response.status_code, response.json()["code"]) == (400, "ACTOR_HEADERS_INCOMPLETE")


@pytest.mark.parametrize("role", ["SYSTEM", "system", " System "])
def test_a_caller_can_never_declare_the_system_role(role):
    response = client.get(
        "/v1/jobs/JOB-X/history",
        headers={"X-Actor-Id": "SYSTEM:OPTIMIZER", "X-Actor-Role": role},
    )

    assert (response.status_code, response.json()["code"]) == (
        403,
        "ACTOR_SYSTEM_ROLE_FORBIDDEN",
    )


def test_malformed_actor_is_400_with_its_own_code():
    response = client.get(
        "/v1/jobs/JOB-X/history",
        headers={"X-Actor-Id": "bad id!", "X-Actor-Role": "WORKER"},
    )

    assert (response.status_code, response.json()["code"]) == (400, "ACTOR_INVALID")


def test_declared_actors_are_recorded_unverified_and_no_headers_as_unidentified():
    declared = client.post(
        "/v1/jobs",
        json={
            "track_id": "UP-1",
            "job_type": "BALLAST_TAMPING",
            "distance_start": 1000.0,
            "distance_end": 1400.0,
            "workers_min": 2,
            "workers_max": 4,
            "description": "Actor contract check",
        },
        headers=WORKER,
    ).json()

    actor = _events(declared["job_id"])[0]["actor"]
    assert actor["assurance"] == "DECLARED_UNVERIFIED"
    assert actor["role"] == "WORKER"

    anonymous = client.get(f"/v1/jobs/{declared['job_id']}/history").json()
    assert anonymous["events"]  # readable without identity today (unenforced policy)


def test_no_authenticated_assurance_exists():
    from backend.app.identity.actor import IdentityAssurance

    assert {member.value for member in IdentityAssurance} == {
        "SYSTEM_INTERNAL",
        "DECLARED_UNVERIFIED",
        "NONE",
    }


# ----------------------------------------------------------------------
# What Slice 7 must NOT have changed
# ----------------------------------------------------------------------


def test_lifecycle_statuses_and_transitions_are_unchanged():
    from backend.app.jobs.lifecycle import ALLOWED_TRANSITIONS
    from backend.app.jobs.models import JobStatus

    assert [s.value for s in JobStatus] == [
        "reported",
        "scheduled",
        "notified",
        "in_progress",
        "completed",
    ]
    assert ALLOWED_TRANSITIONS == {
        "reported": frozenset({"reported", "scheduled"}),
        "scheduled": frozenset({"scheduled", "reported", "notified"}),
        "notified": frozenset({"notified", "in_progress", "reported"}),
        "in_progress": frozenset({"in_progress", "completed", "reported"}),
        "completed": frozenset(),
    }


def test_the_jobs_router_is_the_only_v1_router():
    assert jobs_router_module.router.prefix == "/v1"


# ----------------------------------------------------------------------
# The exported contract artifact cannot drift silently
# ----------------------------------------------------------------------


def _contract_shape(schema):
    """The parts of an OpenAPI document a client depends on."""

    shape = {}

    for path, item in schema["paths"].items():
        for method, op in item.items():
            body = op.get("requestBody")
            shape[(method.upper(), path)] = {
                "params": sorted((p["in"], p["name"]) for p in op.get("parameters", [])),
                "body": None
                if body is None
                else (
                    body.get("required", False),
                    body["content"]["application/json"]["schema"].get("$ref"),
                ),
                "responses": sorted(op["responses"]),
            }

    required = {
        name: sorted(model.get("required", []))
        for name, model in schema["components"]["schemas"].items()
    }

    return shape, required


def test_committed_openapi_snapshot_matches_the_live_contract():
    import json
    from pathlib import Path

    snapshot = json.loads(
        (Path(__file__).resolve().parents[2] / "docs" / "openapi-v1.json").read_text(
            encoding="utf-8"
        )
    )

    assert _contract_shape(snapshot) == _contract_shape(app.openapi()), (
        "docs/openapi-v1.json is the frozen frontend contract. If this "
        "change is intended, it is a contract change: regenerate the "
        "snapshot deliberately and version it."
    )


def test_error_code_vocabulary_is_frozen():
    assert {code.value for code in ErrorCode} == {
        "VALIDATION_ERROR",
        "INVALID_REQUEST",
        "INVALID_TRANSITION",
        "EVIDENCE_INVALID",
        "INVALID_CURSOR",
        "ACTOR_HEADERS_INCOMPLETE",
        "ACTOR_INVALID",
        "ACTOR_SYSTEM_ROLE_FORBIDDEN",
        "AUTHORIZATION_DENIED",
        "JOB_NOT_FOUND",
        "CORRIDOR_NOT_FOUND",
        "JOB_TERMINAL",
        "JOB_COMMITTED",
        "STALE_PROPOSAL",
        "STALE_EXECUTION",
        "CONCURRENT_MODIFICATION",
        "NO_CURRENT_PROPOSAL",
        "NO_ELIGIBLE_JOBS",
        "POSSESSION_DATA_UNAVAILABLE",
        "TIMETABLE_COVERAGE_GAP",
        "COMMITTED_STATE_INCONSISTENT",
        "EXECUTION_HISTORY_INCONSISTENT",
        # Slice 8 (field intake) - additive, added deliberately. No
        # existing code was renamed or removed.
        "ASSET_ASSOCIATION_FAILED",
        "FIELD_LOCATION_INVALID",
        "LOCATION_INPUT_CONFLICT",
        "IDEMPOTENCY_KEY_CONFLICT",
        "ASSET_REFERENCE_INVALID",
    }
