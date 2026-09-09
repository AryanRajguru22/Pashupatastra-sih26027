"""Sprint 2: persisted maintenance jobs become real CP-SAT solver input.

Covers the operational workflow end to end - report, persist, optimize
with the existing solver, honest partial success, authority retrieval,
notify/commit, complete - plus the three safety defects the Sprint 2
architecture audit verified before implementation:

  P0-A  absent possession data must fail closed. Sprint 1 protects
        against a window that does not COVER a track; it does not
        protect against there being no windows at all, which the solver
        reads as "possession control inactive" and schedules everything
        unprotected.
  P0-B  completed work is terminal and can never re-enter optimization.
  P0-C  a solver-assigned schedule must be written back into
        block_candidate_json, or a notified job cannot be rebuilt as a
        committed block and re-optimization becomes INFEASIBLE.

Every test uses an isolated temporary database.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from backend.app.jobs.models import JobCreateRequest, JobStatus
from backend.app.jobs.optimization import (
    JobOptimizationService,
    NoEligibleJobsError,
    PossessionDataUnavailableError,
    build_jobs_optimization_request,
    summarize_outcome,
)
from backend.app.jobs.repository import (
    JobRepository,
    TerminalJobError,
)
from backend.app.jobs.service import (
    POSSESSION_SOURCE_GENERATED_STATIC,
    JobService,
)
from contracts import BlockCandidate, OptimizationResult, ScheduledBlock


SAFETY_REFUSAL = "refused for safety"


@pytest.fixture()
def service(tmp_path: Path) -> JobService:
    return JobService(
        repository=JobRepository(tmp_path / "jobs.db")
    )


@pytest.fixture()
def optimizer(service: JobService) -> JobOptimizationService:
    return JobOptimizationService(service)


def report(
    service: JobService,
    track_id: str = "UP-1",
    job_type: str = "BALLAST_TAMPING",
    distance_start: float = 1000.0,
    description: str = "Reported by field patrol",
) -> dict:
    return service.create_job(
        JobCreateRequest(
            track_id=track_id,
            job_type=job_type,
            distance_start=distance_start,
            distance_end=distance_start + 400,
            workers_min=2,
            workers_max=4,
            description=description,
        )
    )


# ----------------------------------------------------------------------
# 1-2. Reporting and validation
# ----------------------------------------------------------------------


def test_valid_job_is_reported_scored_and_persisted(service: JobService):
    job = report(service)

    assert job["status"] == JobStatus.REPORTED.value
    assert 0.0 <= job["priority_score"] <= 1.0
    assert 0.0 <= job["risk_score"] <= 1.0
    assert job["created_at"]
    assert job["updated_at"]

    # Never optimized yet - distinguishable from "considered and refused".
    assert job["last_solver_status"] is None
    assert job["last_refusal_reason"] is None

    stored = service.repository.get(job["job_id"])
    assert stored is not None
    assert stored["block_candidate"]["block_id"] == job["job_id"]


@pytest.mark.parametrize(
    "kwargs, problem",
    [
        ({"track_id": "NO-SUCH-TRACK"}, "unknown track"),
        ({"distance_start": 5000.0}, "still valid range"),
    ],
)
def test_unknown_track_is_rejected(
    service: JobService,
    kwargs: dict,
    problem: str,
):
    if kwargs.get("track_id") == "NO-SUCH-TRACK":
        with pytest.raises(ValueError, match="Unknown track_id"):
            report(service, **kwargs)
    else:
        assert report(service, **kwargs)["status"] == "reported"


def test_invalid_job_payload_is_rejected_by_the_contract():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        JobCreateRequest(
            track_id="UP-1",
            job_type="BALLAST_TAMPING",
            distance_start=1000,
            distance_end=900,  # end before start
            workers_min=2,
            workers_max=4,
            description="bad range",
        )

    with pytest.raises(ValidationError):
        JobCreateRequest(
            track_id="UP-1",
            job_type="BALLAST_TAMPING",
            distance_start=1000,
            distance_end=1400,
            workers_min=5,
            workers_max=2,  # max below min
            description="bad workers",
        )


# ----------------------------------------------------------------------
# 3-5. Jobs become real solver input and the schedule is persisted
# ----------------------------------------------------------------------


def test_active_jobs_become_valid_block_candidates(service: JobService):
    job = report(service)

    candidates, committed = service.classify_for_optimization()

    assert [c.block_id for c in candidates] == [job["job_id"]]
    assert committed == []
    assert isinstance(candidates[0], BlockCandidate)
    assert candidates[0].duration_minutes > 0


def test_optimization_runs_the_real_solver_and_persists_the_schedule(
    service: JobService,
    optimizer: JobOptimizationService,
):
    job = report(service)

    outcome = optimizer.optimize_corridor("CORRIDOR_A")

    assert outcome["solver_status"] in {"OPTIMAL", "FEASIBLE"}
    assert outcome["counts"]["considered"] == 1
    assert outcome["counts"]["scheduled"] == 1
    assert outcome["solve_time_seconds"] >= 0

    placement = outcome["scheduled"][0]
    assert placement["job_id"] == job["job_id"]
    assert placement["end_minute"] > placement["start_minute"]

    stored = service.repository.get(job["job_id"])
    assert stored["status"] == JobStatus.SCHEDULED.value
    assert stored["schedule_start_minute"] == placement["start_minute"]
    assert stored["schedule_end_minute"] == placement["end_minute"]
    assert stored["last_solver_status"] == outcome["solver_status"]
    assert stored["last_refusal_reason"] is None


def test_scheduled_work_lies_inside_a_possession_window(
    service: JobService,
    optimizer: JobOptimizationService,
):
    """The whole point of possession control, asserted end to end."""

    for i in range(3):
        report(service, distance_start=1000 + 600 * i)

    outcome = optimizer.optimize_corridor("CORRIDOR_A")
    windows = service.possession_windows()

    for placement in outcome["scheduled"]:
        containing = [
            w
            for w in windows
            if w.track_id == placement["track_id"]
            and w.start_minute <= placement["start_minute"]
            and w.end_minute >= placement["end_minute"]
        ]
        assert containing, (
            f"{placement['job_id']} scheduled outside every "
            "possession window"
        )


def test_optimization_response_states_possession_provenance(
    service: JobService,
    optimizer: JobOptimizationService,
):
    """Sprint 2 possession data is generated, never live."""

    report(service)

    outcome = optimizer.optimize_corridor("CORRIDOR_A")

    assert outcome["possession_source"] == "GENERATED_STATIC"
    assert POSSESSION_SOURCE_GENERATED_STATIC == "GENERATED_STATIC"
    assert outcome["possession_window_count"] > 0

    serialized = json.dumps(outcome).upper()
    for forbidden in ("LIVE", "REAL-TIME", "REALTIME"):
        assert forbidden not in serialized, (
            f"optimization outcome must never claim {forbidden} data"
        )


# ----------------------------------------------------------------------
# 6-7. Honest partial success
# ----------------------------------------------------------------------


def test_partial_success_reports_both_outcomes_honestly(
    service: JobService,
    optimizer: JobOptimizationService,
):
    """More work than the possession windows can hold.

    UP-1 offers 270 + 180 + 150 minutes. Eight 240-minute renewals
    cannot all fit, so some must be refused - and the batch must still
    succeed for the rest rather than collapsing.
    """

    for i in range(8):
        report(
            service,
            track_id="UP-1",
            job_type="TRACK_RENEWAL",
            distance_start=1000 + 400 * i,
        )

    outcome = optimizer.optimize_corridor("CORRIDOR_A")

    counts = outcome["counts"]
    assert counts["considered"] == 8
    assert counts["scheduled"] > 0, "safe partial success expected"
    assert counts["unscheduled"] > 0, "not everything can fit"
    assert counts["scheduled"] + counts["unscheduled"] == 8

    # Scheduled work persisted; refused work left eligible with a reason.
    for placement in outcome["scheduled"]:
        stored = service.repository.get(placement["job_id"])
        assert stored["status"] == JobStatus.SCHEDULED.value
        assert stored["schedule_start_minute"] is not None

    for refusal in outcome["unscheduled"]:
        stored = service.repository.get(refusal["job_id"])
        assert stored["status"] == JobStatus.REPORTED.value, (
            "an unscheduled job must stay eligible for the next run"
        )
        assert stored["schedule_start_minute"] is None
        assert stored["last_refusal_reason"] == refusal["reason"]


def test_unscheduled_job_reason_comes_from_the_solver(
    service: JobService,
    optimizer: JobOptimizationService,
):
    for i in range(8):
        report(
            service,
            track_id="UP-1",
            job_type="TRACK_RENEWAL",
            distance_start=1000 + 400 * i,
        )

    outcome = optimizer.optimize_corridor("CORRIDOR_A")

    assert outcome["unscheduled"]

    for refusal in outcome["unscheduled"]:
        assert refusal["reason"]
        assert refusal["reason"] != "not scheduled by the optimizer (no reason reported)"


def test_summarize_outcome_never_invents_a_reason():
    """A block the solver did not explain still gets an honest label."""

    considered = [
        BlockCandidate(
            block_id="B1",
            asset_id="A1",
            track_id="UP-1",
            work_type="ROUTINE_INSPECTION",
            duration_minutes=45,
        )
    ]

    result = OptimizationResult(
        corridor_id="C",
        status="INFEASIBLE",
        scheduled_blocks=[],
        unscheduled_blocks=list(considered),
        infeasibility_reasons=["solver found no feasible solution"],
        rejection_reasons={},
    )

    scheduled, refused = summarize_outcome(result, considered)

    assert scheduled == []
    assert refused[0]["reason"] == "solver found no feasible solution"


# ----------------------------------------------------------------------
# 8-9. P0-A: possession safety
# ----------------------------------------------------------------------


def test_absent_possession_data_fails_closed(service: JobService):
    """The Sprint 2 gap: no windows at all must refuse to solve.

    An empty possession_windows list reaches the solver as "possession
    control inactive" and schedules every block with its train
    occupation never checked. Assembly must refuse instead.
    """

    report(service)
    candidates, _ = service.classify_for_optimization()

    with pytest.raises(PossessionDataUnavailableError) as excinfo:
        build_jobs_optimization_request(
            corridor_id="CORRIDOR_A",
            tracks=["UP-1", "DOWN-1"],
            candidates=candidates,
            possession_windows=[],
        )

    assert "Refusing to optimize" in str(excinfo.value)


def test_absent_possession_data_would_otherwise_schedule_unprotected(
    service: JobService,
):
    """Proves the guard is load-bearing, not decorative.

    Bypassing assembly and handing the solver an empty window list
    schedules the work - which is exactly what the guard prevents.
    """

    from backend.app.optimizer.solver import solve
    from contracts import OptimizationRequest

    report(service)
    candidates, _ = service.classify_for_optimization()

    unguarded = OptimizationRequest(
        corridor_id="CORRIDOR_A",
        horizon_minutes=1440,
        tracks=["UP-1", "DOWN-1"],
        candidates=candidates,
        possession_windows=[],
    )

    assert solve(unguarded).scheduled_blocks, (
        "baseline: without windows the solver schedules freely, which "
        "is why build_jobs_optimization_request must refuse"
    )


def test_track_without_a_window_remains_fail_closed_per_block(
    service: JobService,
):
    """Sprint 1 behaviour must survive: a covered track proceeds, an
    uncovered one is refused individually rather than collapsing the
    batch or being scheduled unprotected."""

    from backend.app.optimizer.solver import solve

    covered = report(service, track_id="UP-1")
    uncovered = report(service, track_id="DOWN-1")

    candidates, committed = service.classify_for_optimization()

    windows = [
        w for w in service.possession_windows() if w.track_id == "UP-1"
    ]

    request = build_jobs_optimization_request(
        corridor_id="CORRIDOR_A",
        tracks=["UP-1", "DOWN-1"],
        candidates=candidates,
        possession_windows=windows,
        committed=committed,
    )

    result = solve(request)
    scheduled = {b.block_id for b in result.scheduled_blocks}

    assert covered["job_id"] in scheduled
    assert uncovered["job_id"] not in scheduled
    assert SAFETY_REFUSAL in result.rejection_reasons[uncovered["job_id"]]


def test_optimizing_with_no_active_jobs_is_refused(
    optimizer: JobOptimizationService,
):
    with pytest.raises(NoEligibleJobsError):
        optimizer.optimize_corridor("CORRIDOR_A")


def test_unknown_corridor_is_rejected(optimizer: JobOptimizationService):
    with pytest.raises(KeyError):
        optimizer.optimize_corridor("CORRIDOR_DOES_NOT_EXIST")


# ----------------------------------------------------------------------
# 10-11. P0-B: completed is terminal
# ----------------------------------------------------------------------


def complete_a_job(service: JobService, optimizer) -> dict:
    job = report(service)
    optimizer.optimize_corridor("CORRIDOR_A")
    service.notify(job["job_id"])
    service.complete(job["job_id"])
    return job


def test_completed_job_cannot_re_enter_optimization(
    service: JobService,
    optimizer: JobOptimizationService,
):
    job = complete_a_job(service, optimizer)

    candidates, committed = service.classify_for_optimization()

    assert job["job_id"] not in {c.block_id for c in candidates}
    assert job["job_id"] not in {c.block_id for c in committed}


def test_completed_job_cannot_be_resurrected_by_set_schedule(
    service: JobService,
    optimizer: JobOptimizationService,
):
    """The verified defect: set_schedule used to force a completed job
    back to 'scheduled', returning it to the candidate set."""

    job = complete_a_job(service, optimizer)

    with pytest.raises(TerminalJobError):
        service.set_schedule(job["job_id"], 100, 160)

    assert (
        service.repository.get(job["job_id"])["status"]
        == JobStatus.COMPLETED.value
    )

    candidates, _ = service.classify_for_optimization()
    assert job["job_id"] not in {c.block_id for c in candidates}


def test_repository_also_refuses_to_reschedule_terminal_work(
    service: JobService,
    optimizer: JobOptimizationService,
):
    """Defence in depth: the guard holds even below the service layer."""

    job = complete_a_job(service, optimizer)

    with pytest.raises(TerminalJobError):
        service.repository.update_schedule(
            job["job_id"], 100, 160, updated_at="now"
        )


def test_completed_job_is_never_touched_by_a_later_batch(
    service: JobService,
    optimizer: JobOptimizationService,
):
    done = complete_a_job(service, optimizer)
    before = service.repository.get(done["job_id"])

    report(service, track_id="DOWN-1", distance_start=2000)
    outcome = optimizer.optimize_corridor("CORRIDOR_A")

    assert done["job_id"] not in {
        s["job_id"] for s in outcome["scheduled"]
    }
    assert done["job_id"] not in {
        u["job_id"] for u in outcome["unscheduled"]
    }

    after = service.repository.get(done["job_id"])
    assert after["status"] == before["status"]
    assert after["schedule_start_minute"] == before["schedule_start_minute"]


# ----------------------------------------------------------------------
# 12-14. P0-C + committed work
# ----------------------------------------------------------------------


def test_scheduling_writes_the_placement_into_block_candidate_json(
    service: JobService,
    optimizer: JobOptimizationService,
):
    """P0-C. Writing only the schedule_* columns left
    block_candidate_json claiming earliest_start_minute=0, so pinning
    the job later placed it at minute 0 - outside every possession
    window - and re-optimization came back INFEASIBLE."""

    job = report(service)
    outcome = optimizer.optimize_corridor("CORRIDOR_A")
    placement = outcome["scheduled"][0]

    stored = service.repository.get(job["job_id"])
    metadata = stored["block_candidate"]["metadata"]

    assert metadata["committed_start_minute"] == placement["start_minute"]
    assert metadata["committed_end_minute"] == placement["end_minute"]
    assert stored["block_candidate"]["status"] == "SCHEDULED"


def test_notifying_marks_the_persisted_block_committed(
    service: JobService,
    optimizer: JobOptimizationService,
):
    job = report(service)
    optimizer.optimize_corridor("CORRIDOR_A")

    service.notify(job["job_id"])

    stored = service.repository.get(job["job_id"])
    assert stored["status"] == JobStatus.NOTIFIED.value
    assert stored["block_candidate"]["is_committed"] is True
    assert stored["block_candidate"]["status"] == "COMMITTED"

    candidates, committed = service.classify_for_optimization()
    assert job["job_id"] in {c.block_id for c in committed}
    assert job["job_id"] in {c.block_id for c in candidates}, (
        "a committed block must also appear in candidates or the "
        "solver never models it"
    )


def test_future_optimization_preserves_notified_work(
    service: JobService,
    optimizer: JobOptimizationService,
):
    """Decision 1: once field personnel are notified, the placement is
    committed and must survive later optimization runs - even under
    heavy contention that would otherwise displace it."""

    job = report(
        service, track_id="UP-1", job_type="TRACK_RENEWAL"
    )
    optimizer.optimize_corridor("CORRIDOR_A")
    service.notify(job["job_id"])

    pinned = service.repository.get(job["job_id"])
    original = (
        pinned["schedule_start_minute"],
        pinned["schedule_end_minute"],
    )

    # Flood the same track with competing work.
    for i in range(6):
        report(
            service,
            track_id="UP-1",
            job_type="TRACK_RENEWAL",
            distance_start=4000 + 400 * i,
        )

    outcome = optimizer.optimize_corridor("CORRIDOR_A")

    kept = [
        s for s in outcome["scheduled"] if s["job_id"] == job["job_id"]
    ]

    assert kept, "notified work was dropped by a later optimization"
    assert (kept[0]["start_minute"], kept[0]["end_minute"]) == original
    assert kept[0]["is_committed"] is True

    after = service.repository.get(job["job_id"])
    assert after["status"] == JobStatus.NOTIFIED.value, (
        "a notified job must not be demoted back to 'scheduled'"
    )
    assert (
        after["schedule_start_minute"],
        after["schedule_end_minute"],
    ) == original


def test_possession_uncovered_committed_work_uses_sprint1_safety(
    service: JobService,
):
    """Sprint 1 Option A, reached through the jobs workflow.

    A committed job whose track has lost possession coverage must be
    refused individually - not pinned onto an unprotected track, and
    not allowed to collapse the whole plan to INFEASIBLE.
    """

    from backend.app.optimizer.solver import solve

    committed_job = report(service, track_id="UP-1")
    other = report(service, track_id="DOWN-1", distance_start=2000)

    # Schedule and notify, so it becomes committed work.
    candidates, _ = service.classify_for_optimization()
    first = solve(
        build_jobs_optimization_request(
            corridor_id="CORRIDOR_A",
            tracks=["UP-1", "DOWN-1"],
            candidates=candidates,
            possession_windows=service.possession_windows(),
        )
    )
    for block in first.scheduled_blocks:
        service.set_schedule(
            block.block_id, block.start_minute, block.end_minute
        )
    service.notify(committed_job["job_id"])

    # Now UP-1 loses every possession window.
    candidates, committed = service.classify_for_optimization()
    windows = [
        w for w in service.possession_windows() if w.track_id != "UP-1"
    ]

    result = solve(
        build_jobs_optimization_request(
            corridor_id="CORRIDOR_A",
            tracks=["UP-1", "DOWN-1"],
            candidates=candidates,
            possession_windows=windows,
            committed=committed,
        )
    )

    assert result.status in {"OPTIMAL", "FEASIBLE"}, (
        "an uncovered committed block must not collapse the plan"
    )

    scheduled = {b.block_id for b in result.scheduled_blocks}
    assert committed_job["job_id"] not in scheduled
    assert other["job_id"] in scheduled
    assert SAFETY_REFUSAL in result.rejection_reasons[
        committed_job["job_id"]
    ]


# ----------------------------------------------------------------------
# 15-16. Batch atomicity and concurrency
# ----------------------------------------------------------------------


def test_batch_persistence_is_atomic(service: JobService):
    """A batch containing a terminal job writes nothing at all."""

    good = report(service)
    doomed = report(service, track_id="DOWN-1", distance_start=2000)

    service.repository.update_status(
        doomed["job_id"], JobStatus.COMPLETED.value
    )

    before = service.repository.get(good["job_id"])

    with pytest.raises(TerminalJobError):
        service.repository.apply_optimization_outcome(
            scheduled=[
                {
                    "job_id": good["job_id"],
                    "start_minute": 100,
                    "end_minute": 160,
                },
                {
                    "job_id": doomed["job_id"],
                    "start_minute": 200,
                    "end_minute": 260,
                },
            ],
            refused=[],
            updated_at="now",
            solver_status="OPTIMAL",
        )

    after = service.repository.get(good["job_id"])

    assert after["status"] == before["status"]
    assert after["schedule_start_minute"] is None, (
        "a rejected batch must not leave a half-written schedule"
    )


def test_concurrent_optimize_calls_do_not_interleave(
    service: JobService,
    optimizer: JobOptimizationService,
):
    """The single-flight lock serializes runs within this process.

    This is an in-process guard only; it provides no protection across
    multiple processes or hosts (see JobOptimizationService).
    """

    for i in range(4):
        report(service, distance_start=1000 + 500 * i)

    outcomes: list[dict] = []
    errors: list[BaseException] = []

    def run():
        try:
            outcomes.append(
                optimizer.optimize_corridor("CORRIDOR_A")
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert len(outcomes) == 4

    # Every job ends up in exactly one consistent final state.
    for job in service.repository.list_all():
        if job["status"] == JobStatus.SCHEDULED.value:
            assert job["schedule_start_minute"] is not None
            assert job["schedule_end_minute"] is not None
        else:
            assert job["status"] == JobStatus.REPORTED.value


# ----------------------------------------------------------------------
# 17. Migration
# ----------------------------------------------------------------------


PRE_SPRINT2_SCHEMA = """
CREATE TABLE maintenance_jobs (
    job_id TEXT PRIMARY KEY,
    track_id TEXT NOT NULL,
    work_type TEXT NOT NULL,
    distance_start REAL NOT NULL,
    distance_end REAL NOT NULL,
    workers_min INTEGER NOT NULL,
    workers_max INTEGER NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL,
    priority_score REAL NOT NULL DEFAULT 0.0,
    risk_score REAL NOT NULL DEFAULT 0.0,
    schedule_start_minute INTEGER,
    schedule_end_minute INTEGER,
    created_at TEXT NOT NULL,
    block_candidate_json TEXT NOT NULL
)
"""


def make_legacy_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(PRE_SPRINT2_SCHEMA)
        conn.execute(
            "INSERT INTO maintenance_jobs VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "JOB-LEGACY01",
                "UP-1",
                "BALLAST_TAMPING",
                1000.0,
                1400.0,
                2,
                4,
                "pre-Sprint-2 row",
                "reported",
                0.5,
                0.4,
                None,
                None,
                "2026-09-01T00:00:00+00:00",
                json.dumps(
                    {
                        "block_id": "JOB-LEGACY01",
                        "asset_id": "AST-UP-1-OHE-001",
                        "track_id": "UP-1",
                        "work_type": "BALLAST_TAMPING",
                        "duration_minutes": 120,
                        "earliest_start_minute": 0,
                        "latest_end_minute": 1440,
                        "priority_score": 0.5,
                        "risk_score": 0.4,
                        "dependencies": [],
                        "mutual_exclusion_group": None,
                        "is_committed": False,
                        "status": "PLANNED",
                        "metadata": {},
                    }
                ),
            ),
        )
        conn.commit()


def test_migration_preserves_existing_rows(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    make_legacy_db(db_path)

    with sqlite3.connect(db_path) as conn:
        before = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(maintenance_jobs)"
            )
        }
    assert "updated_at" not in before

    repository = JobRepository(db_path)

    with sqlite3.connect(db_path) as conn:
        after = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(maintenance_jobs)"
            )
        }

    assert {"updated_at", "last_solver_status", "last_refusal_reason"} <= after

    legacy = repository.get("JOB-LEGACY01")
    assert legacy is not None
    assert legacy["description"] == "pre-Sprint-2 row"
    assert legacy["status"] == "reported"
    assert legacy["updated_at"] is None
    assert legacy["block_candidate"]["block_id"] == "JOB-LEGACY01"


def test_migration_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    make_legacy_db(db_path)

    JobRepository(db_path)
    JobRepository(db_path)
    repository = JobRepository(db_path)

    assert repository.get("JOB-LEGACY01") is not None
    assert len(repository.list_all()) == 1


def test_migrated_legacy_job_can_be_optimized(tmp_path: Path):
    """An upgraded row is a first-class citizen, not a second-class one."""

    db_path = tmp_path / "legacy.db"
    make_legacy_db(db_path)

    service = JobService(repository=JobRepository(db_path))
    outcome = JobOptimizationService(service).optimize_corridor(
        "CORRIDOR_A"
    )

    assert outcome["counts"]["considered"] == 1
    assert outcome["counts"]["scheduled"] == 1
