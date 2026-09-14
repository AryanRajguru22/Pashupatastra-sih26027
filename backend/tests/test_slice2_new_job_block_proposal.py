"""Sprint 3 Slice 2: NEW maintenance job -> AI/ML scoring -> NEW BlockProposal.

Covers the product invariant end to end: a newly reported job is NEW
scheduling demand, existing blocks/possessions are constraints, and the
optimizer must produce a NEW BlockProposal for the new job without ever
reinterpreting an existing (especially committed) block as that job's
proposal.

Every test uses an isolated temporary database, exactly like Slice 1's
test_job_lifecycle_accountability.py.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from contracts import BlockCandidate

from backend.app.data.models import DefectSeverity
from backend.app.data.provenance import ProvenanceLevel
from backend.app.identity.actor import ActorRole, human_actor
from backend.app.jobs.events import JobEventType
from backend.app.jobs.models import JobCreateRequest
from backend.app.jobs.optimization import (
    JobOptimizationService,
    _proposal_explanations,
)
from backend.app.jobs.proposal import NoBlockProposalError, build_block_proposal
from backend.app.jobs.repository import JobRepository
from backend.app.jobs.service import (
    POSSESSION_DERIVATION_GENERATED_SLOTS,
    JobService,
    PossessionInputs,
)


WORKER = human_actor("WORKER-101", ActorRole.WORKER)
ENGINEER = human_actor("ENGINEER-101", ActorRole.ENGINEER)
AUTHORITY = human_actor("AUTHORITY-101", ActorRole.AUTHORITY)

E = JobEventType


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "jobs.db"


@pytest.fixture()
def service(db_path: Path) -> JobService:
    return JobService(repository=JobRepository(db_path))


@pytest.fixture()
def optimizer(service: JobService) -> JobOptimizationService:
    return JobOptimizationService(service)


def report(
    service: JobService,
    actor=WORKER,
    track_id: str = "UP-1",
    job_type: str = "BALLAST_TAMPING",
    distance_start: float = 1000.0,
    severity: DefectSeverity | None = None,
    evidence_reference: str | None = None,
) -> dict:
    return service.create_job(
        JobCreateRequest(
            track_id=track_id,
            job_type=job_type,
            distance_start=distance_start,
            distance_end=distance_start + 400,
            workers_min=2,
            workers_max=4,
            description="Field-reported defect during patrol",
            severity=severity,
            evidence_reference=evidence_reference,
        ),
        actor=actor,
    )


def event_types(service: JobService, job_id: str) -> list[str]:
    return [
        item.event.event_type.value
        for item in service.history.list_for_job(job_id)
    ]


# ----------------------------------------------------------------------
# 1. New maintenance-job workflow
# ----------------------------------------------------------------------


def test_new_job_resolves_section_via_existing_registry(service: JobService):
    job = report(service, severity=DefectSeverity.CRITICAL, evidence_reference="PHOTO-001")

    block = job["block_candidate"]
    assert block["section_id"] is not None
    assert block["track_id"] == "UP-1"
    assert block["metadata"]["reported_severity"] == "CRITICAL"
    assert block["metadata"]["evidence_reference"] == "PHOTO-001"


def test_unknown_track_is_rejected_fail_closed(service: JobService):
    with pytest.raises(ValueError, match="Unknown track_id"):
        report(service, track_id="NO-SUCH-TRACK")


def test_out_of_corridor_location_is_rejected_fail_closed(
    service: JobService, db_path: Path
):
    """Same fail-closed contract Slice 1 already proved; re-asserted
    here as the Slice 2 "invalid resource" negative test."""

    with pytest.raises(ValueError):
        report(service, distance_start=999_000.0)

    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM maintenance_jobs").fetchone()[0] == 0


def test_mismatched_corridor_id_is_rejected(service: JobService):
    with pytest.raises(ValueError, match="corridor_id"):
        service.create_job(
            JobCreateRequest(
                track_id="UP-1",
                job_type="BALLAST_TAMPING",
                distance_start=1000.0,
                distance_end=1400.0,
                workers_min=2,
                workers_max=4,
                description="wrong corridor",
                corridor_id="SOME_OTHER_CORRIDOR",
            ),
            actor=WORKER,
        )


# ----------------------------------------------------------------------
# 2. AI/ML-compatible scoring
# ----------------------------------------------------------------------


def test_scoring_is_deterministic_and_labelled_as_a_baseline(service: JobService):
    job_a = report(service, severity=DefectSeverity.CRITICAL, distance_start=1000.0)
    job_b = report(service, severity=DefectSeverity.CRITICAL, distance_start=1000.0)

    assert job_a["job_id"] != job_b["job_id"]
    assert job_a["priority_score"] == job_b["priority_score"]
    assert job_a["risk_score"] == job_b["risk_score"]

    metadata = job_a["block_candidate"]["metadata"]
    assert metadata["scoring_model_type"] == "BASELINE_DETERMINISTIC"
    assert metadata["scoring_model_version"]
    assert isinstance(metadata["scoring_explanation"], list)
    assert metadata["scoring_explanation"]


def test_reported_severity_changes_the_score_deterministically(service: JobService):
    low = report(service, severity=DefectSeverity.NONE, distance_start=1000.0)
    high = report(service, severity=DefectSeverity.CRITICAL, distance_start=1000.0)

    assert high["risk_score"] > low["risk_score"]


def test_omitted_severity_falls_back_to_pre_slice_2_behavior(service: JobService):
    """No severity given -> scores off the asset record, exactly as Slice 1 did."""

    job = report(service, severity=None)
    assert 0.0 <= job["priority_score"] <= 1.0
    assert 0.0 <= job["risk_score"] <= 1.0


# ----------------------------------------------------------------------
# 3-4-6-7. NEW BlockProposal: creation, explainability, provenance,
#          committed-block protection, run linkage
# ----------------------------------------------------------------------


def test_golden_new_job_scoring_and_optimization_produce_a_distinct_new_proposal(
    service: JobService, optimizer: JobOptimizationService
):
    # --- an EXISTING committed job, from before this new demand existed ---
    existing = report(service, actor=WORKER, distance_start=200.0)
    first_run = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)
    service.notify(existing["job_id"], actor=AUTHORITY, expected_proposal_run_id=first_run["optimization_run_id"])

    committed_before = service.repository.get(existing["job_id"])
    assert committed_before["status"] == "notified"
    assert committed_before["block_candidate"]["status"] == "COMMITTED"

    # --- the NEW maintenance job (this slice's subject) ---
    new_job = report(
        service,
        actor=WORKER,
        distance_start=20_000.0,
        severity=DefectSeverity.MODERATE,
        evidence_reference="PHOTO-DRONE-42",
    )
    assert new_job["job_id"] != existing["job_id"]
    assert new_job["status"] == "reported"

    outcome = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)
    run_id = outcome["optimization_run_id"]
    assert run_id != first_run["optimization_run_id"]

    # --- existing committed block is UNCHANGED ---
    committed_after = service.repository.get(existing["job_id"])
    assert committed_after["status"] == "notified"
    assert committed_after["schedule_start_minute"] == committed_before["schedule_start_minute"]
    assert committed_after["schedule_end_minute"] == committed_before["schedule_end_minute"]
    assert committed_after["block_candidate"]["status"] == "COMMITTED"

    # --- the NEW job got its OWN new proposal, never the existing block ---
    stored_new = service.repository.get(new_job["job_id"])
    assert stored_new["status"] == "scheduled"
    assert stored_new["schedule_start_minute"] is not None

    proposal = service.current_proposal(new_job["job_id"], actor=ENGINEER)

    assert proposal.job_id == new_job["job_id"]
    assert proposal.job_id != existing["job_id"]
    assert proposal.optimization_run_id == run_id
    assert proposal.corridor_id == "CORRIDOR_A"
    assert proposal.track_id == "UP-1"
    assert proposal.is_committed is False
    assert proposal.start_minute == stored_new["schedule_start_minute"]
    assert proposal.end_minute == stored_new["schedule_end_minute"]
    assert proposal.duration_minutes == proposal.end_minute - proposal.start_minute
    assert proposal.proposal_id == f"PROP-{run_id}-{new_job['job_id']}"

    # never overlaps the committed placement on the same resource
    committed_start = committed_after["schedule_start_minute"]
    committed_end = committed_after["schedule_end_minute"]
    assert proposal.end_minute <= committed_start or proposal.start_minute >= committed_end

    # --- explainability: structured, not free text ---
    codes = {item.code for item in proposal.explanation}
    assert "PLACEMENT" in codes
    assert "RESOURCE_USED" in codes
    assert "COMMITTED_BLOCKS_RESPECTED" in codes

    placement_item = next(i for i in proposal.explanation if i.code == "PLACEMENT")
    assert "earliest" not in placement_item.detail.lower()
    assert "optimal" not in placement_item.detail.lower()

    mates_item = next(i for i in proposal.explanation if i.code == "COMMITTED_BLOCKS_RESPECTED")
    assert existing["job_id"] in mates_item.detail

    # --- provenance: canonical, synthetic (never claimed real/live) ---
    assert proposal.provenance.effective.value == "SYNTHETIC"

    # --- optimization-run linkage + job history ---
    types = event_types(service, new_job["job_id"])
    assert types == [
        "JOB_CREATED",
        "JOB_SCORED",
        "OPTIMIZATION_REQUESTED",
        "OPTIMIZATION_COMPLETED",
        "BLOCK_PROPOSED",
    ]

    proposed_event = [
        item.event
        for item in service.history.list_for_job(new_job["job_id"])
        if item.event.event_type is E.BLOCK_PROPOSED
    ][0]
    assert proposed_event.optimization_run_id == run_id


# ----------------------------------------------------------------------
# Negative: infeasible scheduling -> no proposal, explicit unscheduled state
# ----------------------------------------------------------------------


def test_infeasible_scheduling_leaves_no_proposal_and_records_unscheduled(
    service: JobService, optimizer: JobOptimizationService, monkeypatch
):
    job = report(service, distance_start=3000.0)

    original = service.possession_inputs

    def no_windows_on_up1(*args, **kwargs):
        from dataclasses import replace as dc_replace

        inputs = original(*args, **kwargs)
        return dc_replace(
            inputs, windows=[w for w in inputs.windows if w.track_id != "UP-1"]
        )

    monkeypatch.setattr(service, "possession_inputs", no_windows_on_up1)

    optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    stored = service.repository.get(job["job_id"])
    assert stored["status"] == "reported"
    assert stored["schedule_start_minute"] is None
    assert stored["schedule_end_minute"] is None
    assert stored["last_refusal_reason"] is not None

    with pytest.raises(NoBlockProposalError) as excinfo:
        service.current_proposal(job["job_id"], actor=ENGINEER)

    assert "UNSCHEDULED" in str(excinfo.value) or stored["last_refusal_reason"] in str(
        excinfo.value
    )

    assert "OPTIMIZATION_REFUSED" in event_types(service, job["job_id"])
    assert "BLOCK_PROPOSED" not in event_types(service, job["job_id"])


def test_no_proposal_before_any_optimization(service: JobService):
    job = report(service)

    with pytest.raises(NoBlockProposalError):
        service.current_proposal(job["job_id"], actor=ENGINEER)


def test_no_proposal_unknown_job_raises_key_error(service: JobService):
    with pytest.raises(KeyError):
        service.current_proposal("JOB-DOES-NOT-EXIST", actor=ENGINEER)


def test_no_proposal_once_committed(
    service: JobService, optimizer: JobOptimizationService
):
    """A proposal is not a commitment: once notified, it is no longer
    read through the "current proposal" lens."""

    job = report(service)
    outcome = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)
    service.notify(
        job["job_id"],
        actor=AUTHORITY,
        expected_proposal_run_id=outcome["optimization_run_id"],
    )

    with pytest.raises(NoBlockProposalError, match="committed"):
        service.current_proposal(job["job_id"], actor=ENGINEER)


# ----------------------------------------------------------------------
# Negative: stale/invalid proposal handling across a NEW job's re-optimization
# ----------------------------------------------------------------------


def test_stale_proposal_is_rejected_after_new_job_is_reoptimized(
    service: JobService, optimizer: JobOptimizationService
):
    job = report(service)

    first = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)
    stale_run_id = first["optimization_run_id"]

    second = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)
    current_run_id = second["optimization_run_id"]
    assert current_run_id != stale_run_id

    from backend.app.jobs.lifecycle import StaleProposalError

    with pytest.raises(StaleProposalError):
        service.notify(
            job["job_id"],
            actor=AUTHORITY,
            expected_proposal_run_id=stale_run_id,
        )

    # the CURRENT run id still commits successfully
    committed = service.notify(
        job["job_id"],
        actor=AUTHORITY,
        expected_proposal_run_id=current_run_id,
    )
    assert committed["status"] == "notified"


# ----------------------------------------------------------------------
# HTTP surface: GET /jobs/{job_id}/proposal
# ----------------------------------------------------------------------


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from backend.app.api.main import app
    from backend.app.jobs.router import service as router_service

    def wipe_jobs():
        with closing(sqlite3.connect(router_service.repository.db_path)) as conn, conn:
            conn.execute("DELETE FROM maintenance_jobs")

    wipe_jobs()
    yield TestClient(app)
    wipe_jobs()


WORKER_HEADERS = {"X-Actor-Id": "WORKER-101", "X-Actor-Role": "WORKER"}
ENGINEER_HEADERS = {"X-Actor-Id": "ENGINEER-101", "X-Actor-Role": "ENGINEER"}


def _create(client, **overrides) -> dict:
    payload = {
        "track_id": "UP-1",
        "job_type": "BALLAST_TAMPING",
        "distance_start": 1000.0,
        "distance_end": 1400.0,
        "workers_min": 2,
        "workers_max": 4,
        "description": "Field-reported defect",
        "severity": "MODERATE",
    }
    payload.update(overrides)

    response = client.post("/jobs", json=payload, headers=WORKER_HEADERS)
    assert response.status_code == 201, response.text
    return response.json()


def test_http_proposal_endpoint_404_before_creation(client):
    response = client.get("/jobs/JOB-DOES-NOT-EXIST/proposal")
    assert response.status_code == 404


def test_http_proposal_endpoint_409_before_optimization(client):
    job = _create(client)

    response = client.get(f"/jobs/{job['job_id']}/proposal")
    assert response.status_code == 409


def test_http_proposal_endpoint_returns_the_new_proposal(client):
    job = _create(client, evidence_reference="PHOTO-777")

    optimized = client.post(
        "/corridors/CORRIDOR_A/optimize-jobs", headers=ENGINEER_HEADERS
    )
    assert optimized.status_code == 200, optimized.text
    run_id = optimized.json()["optimization_run_id"]

    response = client.get(f"/jobs/{job['job_id']}/proposal")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["job_id"] == job["job_id"]
    assert body["optimization_run_id"] == run_id
    assert body["is_committed"] is False
    assert body["end_minute"] > body["start_minute"]
    assert body["duration_minutes"] == body["end_minute"] - body["start_minute"]
    assert body["provenance"]["effective"] == "SYNTHETIC"
    assert isinstance(body["explanation"], list) and body["explanation"]
    assert all({"code", "detail"} <= set(item) for item in body["explanation"])


# ----------------------------------------------------------------------
# Defense-in-depth: build_block_proposal must not misreport a committed
# job as an open proposal even outside JobService.current_proposal's own
# status == 'scheduled' enforcement
# ----------------------------------------------------------------------


def test_build_block_proposal_reports_committed_job_as_committed(
    service: JobService, optimizer: JobOptimizationService
):
    """JobService.current_proposal only ever calls build_block_proposal for
    a job in status 'scheduled'; that caller-side check must not be the
    ONLY thing keeping is_committed accurate. Called directly against a
    job that has since been notified (committed), it must derive
    is_committed=True from the job's own stored state, not hardcode
    False."""

    job = report(service)
    outcome = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)
    run_id = outcome["optimization_run_id"]
    service.notify(job["job_id"], actor=AUTHORITY, expected_proposal_run_id=run_id)

    committed_job = service.repository.get(job["job_id"])
    assert committed_job["status"] == "notified"
    assert committed_job["block_candidate"]["status"] == "COMMITTED"

    from backend.app.audit.repository import AuditRepository

    run = AuditRepository(service.repository.db_path).get(run_id)
    events = service.history.list_for_job(job["job_id"])

    proposal = build_block_proposal(
        committed_job, service.corridor.corridor_id, events, run
    )

    assert proposal.is_committed is True


# ----------------------------------------------------------------------
# Explanation hardening: COMMITTED_BLOCKS_RESPECTED must reflect the
# actual post-solve scheduled set, not just the pre-solve committed
# snapshot that was fed to the solver
# ----------------------------------------------------------------------


def _synthetic_block(
    block_id: str,
    track_id: str = "UP-1",
    section_id: str = "SEC-1",
    is_committed: bool = False,
) -> BlockCandidate:
    return BlockCandidate(
        block_id=block_id,
        asset_id=f"ASSET-{block_id}",
        track_id=track_id,
        work_type="BALLAST_TAMPING",
        duration_minutes=60,
        section_id=section_id,
        is_committed=is_committed,
    )


def _empty_possession() -> PossessionInputs:
    return PossessionInputs(
        windows=[],
        derivation=POSSESSION_DERIVATION_GENERATED_SLOTS,
        timetable_provenance=ProvenanceLevel.SYNTHETIC,
        possession_provenance=ProvenanceLevel.SYNTHETIC,
    )


def test_committed_resource_mates_excludes_a_mate_that_did_not_survive_the_solve():
    """The pre-solve `committed` list says JOB-MATE was pinned on the same
    resource as JOB-NEW, but the post-solve `scheduled` result does not
    carry JOB-MATE back as committed at all. The explanation must not
    claim committed work was respected in that case."""

    mate = _synthetic_block("JOB-MATE", is_committed=True)
    new = _synthetic_block("JOB-NEW")

    scheduled = [
        {
            "job_id": "JOB-NEW",
            "start_minute": 0,
            "end_minute": 60,
            "is_committed": False,
        },
    ]

    explanations = _proposal_explanations(
        scheduled, [mate, new], [mate], _empty_possession()
    )

    assert explanations["JOB-NEW"]["committed_resource_mates"] == []


def test_committed_resource_mates_includes_a_mate_that_did_survive_the_solve():
    """The positive case: JOB-MATE is both pre-solve committed AND present
    in the post-solve scheduled result as committed, so it is legitimately
    named."""

    mate = _synthetic_block("JOB-MATE", is_committed=True)
    new = _synthetic_block("JOB-NEW")

    scheduled = [
        {
            "job_id": "JOB-MATE",
            "start_minute": 0,
            "end_minute": 60,
            "is_committed": True,
        },
        {
            "job_id": "JOB-NEW",
            "start_minute": 60,
            "end_minute": 120,
            "is_committed": False,
        },
    ]

    explanations = _proposal_explanations(
        scheduled, [mate, new], [mate], _empty_possession()
    )

    assert explanations["JOB-NEW"]["committed_resource_mates"] == ["JOB-MATE"]


# ----------------------------------------------------------------------
# Golden: cross-job proposal contamination when both jobs are mutable
# ----------------------------------------------------------------------


def test_reoptimization_does_not_contaminate_proposals_across_two_mutable_jobs(
    service: JobService, optimizer: JobOptimizationService
):
    """Job A is already scheduled (NOT committed) when Job B - a second,
    independent maintenance job on the SAME scheduling resource - is
    reported and the corridor is reoptimized together. Neither job's
    proposal may be derived from the other's placement, and both must
    keep correct, independent attribution after the shared run."""

    job_a = report(service, actor=WORKER, distance_start=200.0)
    optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)

    stored_a_before = service.repository.get(job_a["job_id"])
    assert stored_a_before["status"] == "scheduled"

    job_b = report(service, actor=WORKER, distance_start=20_000.0)

    outcome = optimizer.optimize_corridor("CORRIDOR_A", actor=ENGINEER)
    run_id = outcome["optimization_run_id"]

    stored_a = service.repository.get(job_a["job_id"])
    stored_b = service.repository.get(job_b["job_id"])
    assert stored_a["status"] == "scheduled"
    assert stored_b["status"] == "scheduled"

    proposal_a = service.current_proposal(job_a["job_id"], actor=ENGINEER)
    proposal_b = service.current_proposal(job_b["job_id"], actor=ENGINEER)

    # correct attribution: each proposal names its OWN job, run and placement
    assert proposal_a.job_id == job_a["job_id"]
    assert proposal_b.job_id == job_b["job_id"]
    assert proposal_a.proposal_id == f"PROP-{run_id}-{job_a['job_id']}"
    assert proposal_b.proposal_id == f"PROP-{run_id}-{job_b['job_id']}"

    assert proposal_a.start_minute == stored_a["schedule_start_minute"]
    assert proposal_a.end_minute == stored_a["schedule_end_minute"]
    assert proposal_b.start_minute == stored_b["schedule_start_minute"]
    assert proposal_b.end_minute == stored_b["schedule_end_minute"]

    # B's proposal cannot be derived from A's placement: distinct windows,
    # non-overlapping on the shared resource
    assert (proposal_a.start_minute, proposal_a.end_minute) != (
        proposal_b.start_minute,
        proposal_b.end_minute,
    )
    assert (
        proposal_a.end_minute <= proposal_b.start_minute
        or proposal_b.end_minute <= proposal_a.start_minute
    )

    # both job IDs remain correctly associated: each job's OWN history
    # never names the other job
    for job_id, other_id in (
        (job_a["job_id"], job_b["job_id"]),
        (job_b["job_id"], job_a["job_id"]),
    ):
        for item in service.history.list_for_job(job_id):
            assert item.event.job_id == job_id
            assert item.event.job_id != other_id

    # A was reoptimized alongside B (BLOCK_REPROPOSED, since it already
    # had a placement); B gets its first-ever BLOCK_PROPOSED
    assert "BLOCK_REPROPOSED" in event_types(service, job_a["job_id"])
    assert "BLOCK_PROPOSED" in event_types(service, job_b["job_id"])

    reproposed_a = [
        item.event
        for item in service.history.list_for_job(job_a["job_id"])
        if item.event.event_type is E.BLOCK_REPROPOSED
    ][0]
    proposed_b = [
        item.event
        for item in service.history.list_for_job(job_b["job_id"])
        if item.event.event_type is E.BLOCK_PROPOSED
    ][0]
    assert reproposed_a.optimization_run_id == run_id
    assert proposed_b.optimization_run_id == run_id

    # each proposal's own PLACEMENT explanation names ITS OWN minutes,
    # never the other job's
    placement_a = next(i for i in proposal_a.explanation if i.code == "PLACEMENT")
    placement_b = next(i for i in proposal_b.explanation if i.code == "PLACEMENT")

    assert (
        placement_a.detail
        == f"Placed at minute {proposal_a.start_minute}-{proposal_a.end_minute}."
    )
    assert (
        placement_b.detail
        == f"Placed at minute {proposal_b.start_minute}-{proposal_b.end_minute}."
    )
    assert placement_a.detail != placement_b.detail
