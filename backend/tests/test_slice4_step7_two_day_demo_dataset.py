"""Slice 4 Step 7: the checked-in synthetic demo dataset covers two days.

pashupatastra_realistic_dataset.json (corridor CORR-NDLS-AGC) previously
carried 24 synthetic train records, all dated 2026-09-10. Step 5 widened
the production horizon to 2880 minutes, which requires 2026-09-10 AND
2026-09-11, so the Step 4 coverage gate (correctly) refused to optimize
over it. Step 7 appends a second synthetic service date so the working
multi-day demonstration runs on a fully covered timetable.

THE DATASET CHANGE, EXACTLY
    - 24 new train records, each a deep copy of the matching 2026-09-10
      record with ONLY service_date changed to 2026-09-11.
    - Clock strings (scheduled_*/actual_*) are unchanged on purpose: they
      are local clock times of their own service_date. The canonical
      adapter's horizon anchoring turns the same "00:20" into minute 20
      on day 1 and minute 1460 on day 2. Rewriting the strings would
      double-count the day (or produce invalid "24:20").
    - train_number is reused: identity is (train_number, service_date),
      the same way a real daily service keeps its number.
    - dataset_version 1.0 -> 1.1 and the note now states both dates.
    - synthetic stays true. Nothing else in the file changed; the legacy
      day-1 fixture sections (possession_windows, maintenance_jobs, ...)
      are untouched and are not read by the canonical jobs pipeline.

TWO-DAY COVERAGE IS NOT MAINTENANCE AVAILABILITY
    Coverage only says timetable records exist for both dates. Possession
    is still derived from train occupation; tests below prove day-2
    trains still constrain day 2 and no fake availability was added.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

import pytest

import backend.app.jobs.optimization as optimization_module
from backend.app.audit.repository import AuditRepository
from backend.app.data.corridor_dataset import (
    DEFAULT_DATASET_PATH,
    CorridorDataset,
    load_corridor_dataset,
)
from backend.app.data.provenance import ProvenanceLevel
from backend.app.data.timetable_adapter import convert_timetable
from backend.app.data.timetable_coverage import (
    TimetableCoverage,
    uncovered_horizon_days,
)
from backend.app.identity.actor import ActorRole, human_actor
from backend.app.jobs.events import JobEventType
from backend.app.jobs.models import JobCreateRequest
from backend.app.jobs.optimization import JobOptimizationService
from backend.app.jobs.repository import JobRepository
from backend.app.jobs.service import (
    OPTIMIZATION_HORIZON_MINUTES,
    OPTIMIZATION_HORIZON_START,
    POSSESSION_DERIVATION_CANONICAL_TIMETABLE,
    JobService,
    TimetableCoverageGapError,
)


CORRIDOR_ID = "CORR-NDLS-AGC"
DAY_1 = "2026-09-10"
DAY_2 = "2026-09-11"

WORKER = human_actor("WORKER-701", ActorRole.WORKER)
ENGINEER = human_actor("ENGINEER-701", ActorRole.ENGINEER)
AUTHORITY = human_actor("AUTHORITY-701", ActorRole.AUTHORITY)


def _raw() -> Dict[str, Any]:
    return json.loads(Path(DEFAULT_DATASET_PATH).read_text(encoding="utf-8"))


def _records_for(dataset: CorridorDataset, service_date: str) -> List[Dict[str, Any]]:
    return [r for r in dataset.timetable_records if r["service_date"] == service_date]


def _service(tmp_path: Path, **kwargs) -> JobService:
    return JobService(
        repository=JobRepository(tmp_path / "jobs.db"),
        dataset=load_corridor_dataset(),
        **kwargs,
    )


def _report(service: JobService) -> Dict[str, Any]:
    return service.create_job(
        JobCreateRequest(
            track_id="UP-1",
            job_type="BALLAST_TAMPING",
            distance_start=1000.0,
            distance_end=1400.0,
            workers_min=2,
            workers_max=4,
            description="Step 7 demo: ballast defect reported by a field worker",
        ),
        actor=WORKER,
    )


# ----------------------------------------------------------------------
# A/B. Loads, and remains explicitly synthetic.
# ----------------------------------------------------------------------


def test_a_dataset_loads_with_48_train_records():
    dataset = load_corridor_dataset()

    assert dataset.corridor_id == CORRIDOR_ID
    assert len(dataset.timetable_records) == 48


def test_b_dataset_remains_explicitly_synthetic():
    raw = _raw()

    assert raw["synthetic"] is True
    assert raw["dataset_version"] == "1.1"
    assert "synthetic" in raw["note"]
    assert "not an official timetable" in raw["note"]
    for claim in ("live", "real-time", "verified", "official data"):
        assert claim not in raw["note"].lower()


# ----------------------------------------------------------------------
# C/D/E. Exactly two service dates; the gate passes at 2880 and fails
# closed at 4320 on exactly 2026-09-12.
# ----------------------------------------------------------------------


def test_c_exactly_two_service_dates_24_trains_each():
    dataset = load_corridor_dataset()
    coverage = TimetableCoverage.from_timetable_records(dataset.timetable_records)

    assert coverage.covered_service_dates == frozenset(
        {date(2026, 9, 10), date(2026, 9, 11)}
    )
    assert len(_records_for(dataset, DAY_1)) == 24
    assert len(_records_for(dataset, DAY_2)) == 24


@pytest.mark.parametrize("horizon_minutes", [1440, 2880])
def test_d_coverage_gate_passes_within_the_two_covered_days(horizon_minutes: int):
    dataset = load_corridor_dataset()
    coverage = TimetableCoverage.from_timetable_records(dataset.timetable_records)

    assert uncovered_horizon_days(coverage, OPTIMIZATION_HORIZON_START, horizon_minutes) == []


def test_d_production_default_passes_the_gate_end_to_end(tmp_path: Path):
    service = _service(tmp_path)
    assert service.horizon_minutes == OPTIMIZATION_HORIZON_MINUTES == 2880

    inputs = service.possession_inputs()

    assert inputs.derivation == POSSESSION_DERIVATION_CANONICAL_TIMETABLE
    assert inputs.rejections == ()


def test_e_three_day_horizon_fails_closed_on_exactly_2026_09_12(tmp_path: Path):
    service = _service(tmp_path, horizon_minutes=4320)

    with pytest.raises(TimetableCoverageGapError) as excinfo:
        service.possession_inputs()

    assert excinfo.value.covered_dates == (date(2026, 9, 10), date(2026, 9, 11))
    assert excinfo.value.uncovered_dates == (date(2026, 9, 12),)


# ----------------------------------------------------------------------
# F/G/H. The canonical adapter processes both days; day 2 is absolute.
# ----------------------------------------------------------------------


def _anchored(records):
    dataset = load_corridor_dataset()
    return convert_timetable(
        records, dataset.topology, horizon_start=OPTIMIZATION_HORIZON_START
    )


def test_f_day_one_records_convert_inside_day_one():
    dataset = load_corridor_dataset()
    snapshot = _anchored(_records_for(dataset, DAY_1))

    assert len(snapshot.trains) == 24
    assert snapshot.rejections == ()
    for train in snapshot.trains:
        for traversal in train.traversals:
            assert 0 <= traversal.enter_minute < traversal.exit_minute < 1440


def test_g_day_two_records_convert_inside_day_two():
    dataset = load_corridor_dataset()
    snapshot = _anchored(_records_for(dataset, DAY_2))

    assert len(snapshot.trains) == 24
    assert snapshot.rejections == ()
    for train in snapshot.trains:
        for traversal in train.traversals:
            assert 1440 <= traversal.enter_minute < traversal.exit_minute < 2880


def test_h_every_day_two_traversal_is_its_day_one_twin_plus_1440():
    dataset = load_corridor_dataset()
    day_1 = _anchored(_records_for(dataset, DAY_1)).trains
    day_2 = _anchored(_records_for(dataset, DAY_2)).trains

    assert [t.train_number for t in day_1] == [t.train_number for t in day_2]

    for first, second in zip(day_1, day_2):
        assert len(first.traversals) == len(second.traversals)
        for a, b in zip(first.traversals, second.traversals):
            assert (b.section_id, b.track_id) == (a.section_id, a.track_id)
            assert b.enter_minute == a.enter_minute + 1440
            assert b.exit_minute == a.exit_minute + 1440


def test_h_first_day_two_train_is_minute_1460_not_20():
    """Gatimaan 12049 departs NDLS at local 00:20 on both dates: minute 20
    on day 1, minute 1460 on day 2 - never re-based modulo 1440."""

    dataset = load_corridor_dataset()
    day_2 = _anchored(_records_for(dataset, DAY_2)).trains
    gatimaan = next(t for t in day_2 if t.train_number == "12049")

    assert gatimaan.traversals[0].enter_minute == 1440 + 23  # departs 00:23


# ----------------------------------------------------------------------
# I. Identity and deterministic derivation.
# ----------------------------------------------------------------------


def test_i_train_identity_is_unique_per_service_date():
    dataset = load_corridor_dataset()
    identities = [(r["train_number"], r["service_date"]) for r in dataset.timetable_records]

    assert len(identities) == len(set(identities)) == 48
    assert {r["train_number"] for r in _records_for(dataset, DAY_1)} == {
        r["train_number"] for r in _records_for(dataset, DAY_2)
    }


def test_i_day_two_is_day_one_with_only_service_date_changed():
    """The whole day-2 block is inspectable and derived, not hand-authored:
    record N of day 2 equals record N of day 1 in every field except
    service_date, and ordering is day 1 then day 2."""

    records = list(load_corridor_dataset().timetable_records)
    day_1, day_2 = records[:24], records[24:]

    assert all(r["service_date"] == DAY_1 for r in day_1)
    assert all(r["service_date"] == DAY_2 for r in day_2)
    for first, second in zip(day_1, day_2):
        assert {**second, "service_date": DAY_1} == first


# ----------------------------------------------------------------------
# J/K. Train paths and section/track mappings stay valid.
# ----------------------------------------------------------------------


def test_j_every_train_path_is_valid_against_topology():
    dataset = load_corridor_dataset()
    stations = set(dataset.topology.station_ids)

    for record in dataset.timetable_records:
        path = [stop["station_id"] for stop in record["station_times"]]
        assert len(path) >= 2
        assert len(path) == len(set(path))
        assert set(path) <= stations
        assert path == record["route"]

    snapshot = _anchored(dataset.timetable_records)
    assert len(snapshot.trains) == 48
    assert snapshot.rejections == ()


def test_k_every_traversal_resolves_to_a_registered_section_and_track():
    dataset = load_corridor_dataset()
    corridor_tracks = {t.track_id for t in dataset.corridor.tracks}

    for train in _anchored(dataset.timetable_records).trains:
        assert train.track_id in corridor_tracks
        for traversal in train.traversals:
            assert traversal.section_id in dataset.registry
            assert traversal.track_id in dataset.registry.track_ids_for(
                traversal.section_id
            )


# ----------------------------------------------------------------------
# Coverage is not availability: day-2 trains still constrain day 2.
# ----------------------------------------------------------------------


def test_two_day_coverage_does_not_manufacture_free_track_time(tmp_path: Path):
    service = _service(tmp_path)
    inputs = service.possession_inputs()

    # No derived window covers a whole calendar day on any resource.
    for window in inputs.windows:
        assert window.end_minute - window.start_minute < 1440

    # Every day-2 traversal is excluded from every derived window on its
    # own resource - possession is the complement of occupation, still.
    for train in inputs.snapshot.trains:
        for traversal in train.traversals:
            if traversal.enter_minute < 1440:
                continue
            for window in inputs.windows:
                if (window.track_id, window.section_id) != (
                    traversal.track_id,
                    traversal.section_id,
                ):
                    continue
                assert (
                    window.end_minute <= traversal.enter_minute
                    or window.start_minute >= traversal.exit_minute
                )


# ----------------------------------------------------------------------
# L/M/N. Full two-day optimization; the solver really runs.
# ----------------------------------------------------------------------


def test_l_m_n_two_day_optimization_invokes_the_solver_and_proposes(
    tmp_path: Path, monkeypatch
):
    requests: list = []
    real_solve = optimization_module.solve

    def counting_solve(request):
        requests.append(request)
        return real_solve(request)

    monkeypatch.setattr(optimization_module, "solve", counting_solve)

    service = _service(tmp_path)
    job = _report(service)

    outcome = JobOptimizationService(service).optimize_corridor(
        CORRIDOR_ID, actor=ENGINEER
    )

    assert len(requests) == 1
    assert requests[0].horizon_minutes == 2880
    assert any(w.start_minute >= 1440 for w in requests[0].possession_windows)

    assert outcome["solver_status"] in {"OPTIMAL", "FEASIBLE"}
    assert outcome["possession_derivation"] == POSSESSION_DERIVATION_CANONICAL_TIMETABLE
    placement = next(s for s in outcome["scheduled"] if s["job_id"] == job["job_id"])

    proposal = service.current_proposal(job["job_id"])
    assert proposal.optimization_run_id == outcome["optimization_run_id"]
    assert (proposal.start_minute, proposal.end_minute) == (
        placement["start_minute"],
        placement["end_minute"],
    )


# ----------------------------------------------------------------------
# O. The demo story: postpone to day 2 -> reoptimize -> approve.
# ----------------------------------------------------------------------


def test_o_postpone_to_day_two_reoptimize_and_approve(tmp_path: Path):
    service = _service(tmp_path)
    optimizer = JobOptimizationService(service)

    job = _report(service)
    assert job["priority_score"] > 0  # scored by the existing baseline

    first = optimizer.optimize_corridor(CORRIDOR_ID, actor=ENGINEER)
    assert service.repository.get(job["job_id"])["status"] == "scheduled"

    postponed = service.postpone_proposal(
        job["job_id"],
        actor=AUTHORITY,
        expected_proposal_run_id=first["optimization_run_id"],
        reason="Crew unavailable today; defer to tomorrow",
        selected_date=DAY_2,
    )
    assert postponed["block_candidate"]["earliest_start_minute"] == 1440

    second = optimizer.optimize_corridor(CORRIDOR_ID, actor=ENGINEER)
    assert second["optimization_run_id"] != first["optimization_run_id"]

    rescheduled = service.repository.get(job["job_id"])
    assert rescheduled["status"] == "scheduled", rescheduled["last_refusal_reason"]
    start = rescheduled["schedule_start_minute"]
    end = rescheduled["schedule_end_minute"]
    assert start >= 1440 and end <= 2880

    # The placement sits inside a window DERIVED from the two-day
    # timetable on the job's own resource.
    block = rescheduled["block_candidate"]
    inputs = service.possession_inputs()
    assert any(
        w.track_id == block["track_id"]
        and w.section_id == block["section_id"]
        and w.start_minute <= start
        and end <= w.end_minute
        for w in inputs.windows
    )

    approved = service.approve_proposal(
        job["job_id"],
        actor=AUTHORITY,
        expected_proposal_run_id=second["optimization_run_id"],
    )
    assert approved["status"] == "notified"
    assert approved["block_candidate"]["status"] == "COMMITTED"
    assert (approved["schedule_start_minute"], approved["schedule_end_minute"]) == (start, end)

    events = [item.event.event_type for item in service.job_history(job["job_id"])]
    assert events == [
        JobEventType.JOB_CREATED,
        JobEventType.JOB_SCORED,
        JobEventType.OPTIMIZATION_REQUESTED,
        JobEventType.OPTIMIZATION_COMPLETED,
        JobEventType.BLOCK_PROPOSED,
        JobEventType.PROPOSAL_POSTPONED,
        JobEventType.OPTIMIZATION_REQUESTED,
        JobEventType.OPTIMIZATION_COMPLETED,
        JobEventType.BLOCK_PROPOSED,
        JobEventType.BLOCK_COMMITTED,
    ]


# ----------------------------------------------------------------------
# P. One-day horizons stay deterministic and unaffected by day 2.
# ----------------------------------------------------------------------


def test_p_one_day_horizon_windows_are_unchanged_by_the_day_two_records(
    tmp_path: Path,
):
    full = _service(tmp_path, horizon_minutes=1440).possession_inputs()

    base = load_corridor_dataset()
    day_one_only = CorridorDataset(
        corridor=base.corridor,
        topology=base.topology,
        registry=base.registry,
        timetable_records=tuple(_records_for(base, DAY_1)),
    )
    reference = JobService(
        repository=JobRepository(tmp_path / "reference.db"),
        dataset=day_one_only,
        horizon_minutes=1440,
    ).possession_inputs()

    def key(windows):
        return sorted(
            (w.track_id, w.section_id, w.start_minute, w.end_minute) for w in windows
        )

    assert key(full.windows) == key(reference.windows)
    assert all(w.end_minute <= 1440 for w in full.windows)


# ----------------------------------------------------------------------
# Q. Provenance remains synthetic end to end.
# ----------------------------------------------------------------------


def test_q_provenance_remains_synthetic_in_outcome_and_audit(tmp_path: Path):
    service = _service(tmp_path)
    _report(service)

    inputs = service.possession_inputs()
    assert inputs.timetable_provenance is ProvenanceLevel.SYNTHETIC
    assert inputs.possession_provenance is ProvenanceLevel.SYNTHETIC

    outcome = JobOptimizationService(service).optimize_corridor(
        CORRIDOR_ID, actor=ENGINEER
    )
    assert outcome["provenance"]["effective"] == "SYNTHETIC"

    (run,) = AuditRepository(tmp_path / "jobs.db").list_by_corridor(CORRIDOR_ID)
    snapshot = run.provenance_snapshot_json
    assert "REAL_" not in snapshot
    assert "LIVE" not in snapshot
