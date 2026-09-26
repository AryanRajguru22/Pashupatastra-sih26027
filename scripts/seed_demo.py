"""Deterministic demo seed / reset for the SIH walkthrough (P0-3).

    python scripts/seed_demo.py            wipe the demo DB, seed the queue
    python scripts/seed_demo.py --verify   rehearse the whole demo on a
                                           TEMPORARY database and check it

Both need, in the environment of this command AND of the backend server:

    PASHUPAT_CORRIDOR_ID=CORR-NDLS-AGC
    PASHUPAT_JOBS_DB=<absolute path of the dedicated demo database>
                     (suggested: <repo>/demo/jobs.db, already gitignored)

and no PASHUPAT_AUTH_* / PASHUPAT_IDENTITY_* variables (demo mode).

SYNTHETIC, ILLUSTRATIVE SCENARIO (default)
    Every job below is invented for the demonstration. The corridor is the
    checked-in NDLS-AGC dataset, which uses real station CODES but
    synthetic topology, timetable, asset condition and possession windows;
    the optimizer already labels every run's provenance SYNTHETIC. Nothing
    here is Indian Railways operational data, and the evidence references
    are placeholders under synthetic-demo/.

REAL PUBLIC SNAPSHOT MODE (PASHUPAT_RAILWAY_DATA=real, in the environment of
this command AND of the backend server)
    The corridor is the offline public-railway-data snapshot
    (data/railway/ndls_agc): published stations, chainage, TAG-2026
    timetable and dated infrastructure. Possession windows are CANDIDATES
    derived from that public passenger timetable. The jobs are still
    DEMO MAINTENANCE INPUTS - real railway work categories on real
    sections (see works.json), not observed defects - so every description
    starts "DEMO INPUT:" and the evidence placeholders sit under
    demo-input/. Placement is decided by the solver's search, so the real
    mode does not promise that the postponement target starts on day 1;
    it checks that the authority's not-before date is honoured.

WHAT THE SEED DOES
    Wipes ONLY the dedicated demo database, then reports the seed jobs
    through JobService.create_job - the same validation, scoring,
    duplicate detection, asset association and event history the HTTP
    API uses. It leaves every job 'reported'. It does not optimize:
    obligation (SLA) clocks start when a proposal exists, so optimizing at
    seed time would let them run out between seeding and recording.

WHAT THE DEMO THEN DOES (rehearse() performs exactly this)
    1. a worker reports LIVE_INTAKE_JOB (critical rail fracture)
    2. optimize                  -> every job placed; the ballast tamping
                                    job lands on day 1 (2026-09-10)
    3. authority approves the critical job (commits its block)
    4. authority postpones the ballast tamping job to 2026-09-11
    5. authority rejects the routine inspection
    6. optimize again            -> ballast tamping moves to day 2, the
                                    approved block is preserved
    7. the crew starts and completes the critical job with evidence

WHY THE SCENARIO IS SHAPED THIS WAY
    The solver's objective rewards only WHETHER a job is scheduled, never
    where (backend/app/optimizer/solver.py); with its fixed seed and single
    worker the placement is reproducible for an identical request, and the
    candidate order is creation order. The seed order below is therefore
    part of the scenario: the postponement target is reported first. No
    job type is used whose duration exceeds the possession windows of its
    own section and track (TRACK_RENEWAL's 240 minutes fits no window
    on either day, so it is not used).

    Placement is guaranteed only for the same OR-Tools version and solver
    configuration. --verify checks the scenario's invariants on the
    machine that will record the demo and exits non-zero if any fails.

Job ids are random. Nothing here hardcodes one: seed() and rehearse()
return them, and find_demo_job() looks a job up by what it IS.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Deliberately NOT imported: backend.app.api.main and backend.app.jobs.router.
# The router builds a JobService at import against whatever PASHUPAT_JOBS_DB
# names - or the repository-root jobs.db when it is unset.
from contracts import DEFAULT_HORIZON_START  # noqa: E402

from backend.app.data.dataset_selection import (  # noqa: E402
    MODE_REAL,
    load_configured_dataset,
    railway_data_mode,
)
from backend.app.identity.actor import ActorRole, human_actor  # noqa: E402
from backend.app.jobs.field_location import convert_field_location  # noqa: E402
from backend.app.jobs.lifecycle import proposal_run_id_of  # noqa: E402
from backend.app.jobs.models import JobCreateRequest  # noqa: E402
from backend.app.jobs.optimization import JobOptimizationService  # noqa: E402
from backend.app.jobs.repository import JobRepository  # noqa: E402
from backend.app.jobs.service import JobService  # noqa: E402

DEMO_CORRIDOR_ID = "CORR-NDLS-AGC"
ROOT_JOBS_DB = REPO_ROOT / "jobs.db"
SUGGESTED_DEMO_DB = REPO_ROOT / "demo" / "jobs.db"

DAY_MINUTES = 1440
POSTPONE_DATE = "2026-09-11"  # day 2 of the fixed 2026-09-10 horizon

AUTHORITY_ID = "AUTHORITY-017"
CREW_ID = "WORKER-042"

# Which dataset this process was started for. Read once at import, from the same
# variable the backend reads, so the seed and the server cannot disagree unless
# the two environments do (the runbook sets both).
REAL_DATA_MODE = railway_data_mode() == MODE_REAL

PLACEHOLDER_ROOT = "demo-input" if REAL_DATA_MODE else "synthetic-demo"

POSTPONE_REASON = (
    "Authority defers the work to the next planning day (demo decision)"
    if REAL_DATA_MODE
    else "Festival special trains on the RKM-AGC down line on 10 Sep"
)
REJECT_REASON = "Covered by the scheduled trolley inspection this week"


@dataclass(frozen=True)
class DemoJob:
    """One field report, in the terms a field worker has."""

    key: str
    job_type: str
    severity: str
    from_station_id: str
    toward_station_id: str
    offset_start_m: float
    offset_end_m: float
    track_id: str
    description: str
    reporter_id: str

    @property
    def evidence_reference(self) -> str:
        return f"{PLACEHOLDER_ROOT}/inspection/{self.key}.jpg"

    @property
    def idempotency_key(self) -> str:
        return f"demo-{self.key}"


# ORDER MATTERS (see the module docstring): the postponement target first.
SEED_JOBS: Tuple[DemoJob, ...] = (
    DemoJob(
        key="ballast-rkm-agc",
        job_type="BALLAST_TAMPING",
        severity="MODERATE",
        from_station_id="RKM",
        toward_station_id="AGC",
        offset_start_m=3000,
        offset_end_m=3600,
        track_id="DOWN-1",
        description="Ballast deficiency and loss of top level over 600 m; "
        "cross-level readings out of tolerance.",
        reporter_id="WORKER-021",
    ),
    DemoJob(
        key="ohe-rkm-agc",
        job_type="OHE_MAINTENANCE",
        severity="MODERATE",
        from_station_id="RKM",
        toward_station_id="AGC",
        offset_start_m=1000,
        offset_end_m=1400,
        track_id="UP-1",
        description="OHE dropper wires damaged over two spans; contact wire "
        "sag observed.",
        reporter_id="WORKER-033",
    ),
    DemoJob(
        key="inspection-mtj-rkm",
        job_type="ROUTINE_INSPECTION",
        severity="MINOR",
        from_station_id="MTJ",
        toward_station_id="RKM",
        offset_start_m=500,
        offset_end_m=900,
        track_id="UP-1",
        description="Routine fastening and sleeper inspection due.",
        reporter_id="WORKER-042",
    ),
    DemoJob(
        key="signal-fdb-pwl",
        job_type="SIGNALLING_INTERLOCKING",
        severity="MODERATE",
        from_station_id="FDB",
        toward_station_id="PWL",
        offset_start_m=4000,
        offset_end_m=4300,
        track_id="DOWN-1",
        description="Intermittent track-circuit failure reported by the "
        "station master.",
        reporter_id="WORKER-058",
    ),
)

# Reported live, in front of the audience, AFTER the seed.
LIVE_INTAKE_JOB = DemoJob(
    key="fracture-mtj-rkm",
    job_type="EMERGENCY_REPAIR",
    severity="CRITICAL",
    from_station_id="MTJ",
    toward_station_id="RKM",
    offset_start_m=5700,
    offset_end_m=6000,
    track_id="DOWN-1",
    description="Rail fracture found on patrol; caution order issued.",
    reporter_id="WORKER-042",
)

if REAL_DATA_MODE:
    # The observation is a controlled demo input; only its work category is
    # grounded in a real sanctioned work (data/railway/ndls_agc/works.json).
    SEED_JOBS = tuple(
        replace(job, description=f"DEMO INPUT: {job.description}") for job in SEED_JOBS
    )
    LIVE_INTAKE_JOB = replace(
        LIVE_INTAKE_JOB, description=f"DEMO INPUT: {LIVE_INTAKE_JOB.description}"
    )

POSTPONE_TARGET = SEED_JOBS[0].key
REJECT_TARGET = SEED_JOBS[2].key
APPROVE_TARGET = LIVE_INTAKE_JOB.key


class DemoResetRefused(RuntimeError):
    """The reset would have touched something other than a demo database."""


class DemoEnvironmentError(RuntimeError):
    """The environment does not select the demo corridor and database."""


# ----------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------


def job_request(job: DemoJob, service: JobService) -> JobCreateRequest:
    """The HTTP request body a field app would send for this report.

    Distances come from the canonical field-location conversion, so the
    request carries both forms and they agree by construction.
    """

    resolved = convert_field_location(
        service.registry,
        job.from_station_id,
        job.toward_station_id,
        job.offset_start_m,
        job.offset_end_m,
    )

    return JobCreateRequest(
        track_id=job.track_id,
        job_type=job.job_type,
        distance_start=resolved.distance_start_m,
        distance_end=resolved.distance_end_m,
        workers_min=2,
        workers_max=4,
        description=job.description,
        severity=job.severity,
        evidence_reference=job.evidence_reference,
        field_location={
            "from_station_id": job.from_station_id,
            "toward_station_id": job.toward_station_id,
            "offset_start_m": job.offset_start_m,
            "offset_end_m": job.offset_end_m,
        },
        idempotency_key=job.idempotency_key,
    )


def build_demo_service(db_path: str | Path) -> JobService:
    """A JobService for the demo corridor over exactly this database file.

    The same construction the server performs with
    PASHUPAT_CORRIDOR_ID=CORR-NDLS-AGC in demo mode, with the database
    passed explicitly instead of read from the environment.
    """

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    service = JobService(
        repository=JobRepository(db_path),
        dataset=load_configured_dataset(),
    )

    if service.corridor.corridor_id != DEMO_CORRIDOR_ID:
        raise DemoEnvironmentError(
            f"The checked-in dataset is {service.corridor.corridor_id!r}, "
            f"not {DEMO_CORRIDOR_ID!r}."
        )

    return service


# ----------------------------------------------------------------------
# Reset
# ----------------------------------------------------------------------


def _same_file(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(
        os.path.realpath(b)
    )


def _is_jobs_database(path: Path) -> bool:
    """True only for a SQLite file that holds a maintenance_jobs table."""

    with open(path, "rb") as handle:
        if handle.read(16) != b"SQLite format 3\x00":
            return False

    # immutable=1: read the main file only. A leftover -journal or -wal
    # (a crashed server) must neither be replayed nor block the check.
    try:
        uri = f"file:{path.as_posix()}?mode=ro&immutable=1"
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='maintenance_jobs'"
            ).fetchone()
    except sqlite3.Error:
        return False

    return row is not None


def reset_database(db_path: str | Path) -> Path:
    """Delete and recreate the demo database. Refuses anything else.

    Refused: the repository-root jobs.db (by resolved path), a relative
    path, a directory, and any existing file that is not a Pashupatastra
    jobs database. An empty file is treated as a fresh database.
    """

    path = Path(db_path)

    if not path.is_absolute():
        raise DemoResetRefused(f"The demo database path must be absolute: {path}")

    if _same_file(path, ROOT_JOBS_DB):
        raise DemoResetRefused(
            "Refusing to reset the repository-root jobs.db. Point "
            "PASHUPAT_JOBS_DB at a dedicated demo database, e.g. "
            f"{SUGGESTED_DEMO_DB}."
        )

    if path.exists():
        if not path.is_file():
            raise DemoResetRefused(f"Not a file: {path}")
        if path.stat().st_size > 0 and not _is_jobs_database(path):
            raise DemoResetRefused(
                f"Refusing to delete {path}: it is not a Pashupatastra "
                "jobs database."
            )

    targets = [path] + [Path(f"{path}{suffix}") for suffix in ("-wal", "-shm", "-journal")]

    for target in targets:
        try:
            target.unlink(missing_ok=True)
        except PermissionError as exc:
            raise DemoResetRefused(
                f"Could not delete {target}: it is in use. Stop the backend "
                "server first, then reset."
            ) from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    JobRepository(path)  # creates the schema
    return path


# ----------------------------------------------------------------------
# Seed and lookup
# ----------------------------------------------------------------------


def report(service: JobService, job: DemoJob) -> Dict[str, Any]:
    reporter = human_actor(job.reporter_id, ActorRole.WORKER)
    return service.create_job(job_request(job, service), actor=reporter)


def seed(service: JobService) -> Dict[str, str]:
    """Report every seed job, in order. Returns {demo key: job_id}."""

    if service.corridor.corridor_id != DEMO_CORRIDOR_ID:
        raise DemoEnvironmentError(
            f"The service is for {service.corridor.corridor_id!r}, not "
            f"{DEMO_CORRIDOR_ID!r}."
        )

    if service.repository.list_all():
        raise DemoResetRefused("Seed expects an empty database; reset it first.")

    ids: Dict[str, str] = {}

    for job in SEED_JOBS:
        created = report(service, job)
        if created["status"] != "reported":
            raise RuntimeError(f"{job.key} was not left 'reported'.")
        ids[job.key] = created["job_id"]

    return ids


def find_demo_job(service: JobService, job: DemoJob) -> Optional[Dict[str, Any]]:
    """The stored job this report became, looked up by what it is."""

    expected = job_request(job, service)

    for stored in service.repository.list_all():
        if (
            stored["track_id"] == expected.track_id
            and stored["work_type"] == expected.job_type.value
            and stored["distance_start"] == expected.distance_start
            and stored["distance_end"] == expected.distance_end
        ):
            return stored

    return None


# ----------------------------------------------------------------------
# Rehearsal
# ----------------------------------------------------------------------


def _observed_at(minute: float) -> str:
    from datetime import datetime, timedelta

    return (
        datetime.fromisoformat(DEFAULT_HORIZON_START) + timedelta(minutes=minute)
    ).isoformat()


def _placement(service: JobService, job_id: str) -> Dict[str, Any]:
    stored = service.repository.get(job_id)
    return {
        "status": stored["status"],
        "start_minute": stored["schedule_start_minute"],
        "end_minute": stored["schedule_end_minute"],
    }


def _run_summary(outcome: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "solver_status": outcome["solver_status"],
        "counts": dict(outcome["counts"]),
        "unscheduled_reasons": sorted(u["reason"] for u in outcome["unscheduled"]),
        "provenance": dict(outcome["provenance"]),
    }


@dataclass
class Rehearsal:
    """What rehearse() did. ids are random; everything else is business."""

    ids: Dict[str, str]
    first_run: Dict[str, Any]
    second_run: Dict[str, Any]
    after_first: Dict[str, Dict[str, Any]]
    after_second: Dict[str, Dict[str, Any]]
    final: Dict[str, Dict[str, Any]]
    first_run_id: str
    second_run_id: str


def rehearse(service: JobService, seed_ids: Mapping[str, str]) -> Rehearsal:
    """Perform the demo, exactly as the walkthrough does, on this service."""

    authority = human_actor(AUTHORITY_ID, ActorRole.AUTHORITY)
    crew = human_actor(CREW_ID, ActorRole.WORKER)
    optimizer = JobOptimizationService(service)

    ids = dict(seed_ids)
    ids[LIVE_INTAKE_JOB.key] = report(service, LIVE_INTAKE_JOB)["job_id"]

    def stored(key: str) -> Dict[str, Any]:
        return service.repository.get(ids[key])

    first = optimizer.optimize_corridor(DEMO_CORRIDOR_ID, actor=authority)
    after_first = {key: _placement(service, job_id) for key, job_id in ids.items()}

    service.approve_proposal(
        ids[APPROVE_TARGET],
        authority,
        expected_proposal_run_id=proposal_run_id_of(stored(APPROVE_TARGET)),
    )
    service.postpone_proposal(
        ids[POSTPONE_TARGET],
        authority,
        expected_proposal_run_id=proposal_run_id_of(stored(POSTPONE_TARGET)),
        reason=POSTPONE_REASON,
        selected_date=POSTPONE_DATE,
    )
    service.reject_proposal(
        ids[REJECT_TARGET],
        authority,
        expected_proposal_run_id=proposal_run_id_of(stored(REJECT_TARGET)),
        reason=REJECT_REASON,
    )

    second = optimizer.optimize_corridor(DEMO_CORRIDOR_ID, actor=authority)
    after_second = {key: _placement(service, job_id) for key, job_id in ids.items()}

    committed = stored(APPROVE_TARGET)
    start_minute = committed["schedule_start_minute"] + 1
    _, execution = service.start_execution(
        ids[APPROVE_TARGET],
        crew,
        expected_proposal_run_id=proposal_run_id_of(committed),
        actual_start_at=_observed_at(start_minute),
        before_work_evidence=[
            {
                "evidence_reference": f"{PLACEHOLDER_ROOT}/execution/before-1.jpg",
                "evidence_kind": "PHOTO",
                "captured_at": _observed_at(start_minute),
            }
        ],
    )
    end_minute = committed["schedule_end_minute"]
    service.complete_execution(
        ids[APPROVE_TARGET],
        crew,
        execution_id=execution.execution_id,
        actual_end_at=_observed_at(end_minute),
        after_work_evidence=[
            {
                "evidence_reference": f"{PLACEHOLDER_ROOT}/execution/after-1.jpg",
                "evidence_kind": "PHOTO",
                "captured_at": _observed_at(end_minute),
            }
        ],
    )

    final = {key: _placement(service, job_id) for key, job_id in ids.items()}

    return Rehearsal(
        ids=ids,
        first_run=_run_summary(first),
        second_run=_run_summary(second),
        after_first=after_first,
        after_second=after_second,
        final=final,
        first_run_id=first["optimization_run_id"],
        second_run_id=second["optimization_run_id"],
    )


def business_summary(service: JobService, rehearsal: Rehearsal) -> Dict[str, Any]:
    """Everything the demo shows, with random ids and timestamps removed."""

    authority = human_actor(AUTHORITY_ID, ActorRole.AUTHORITY)
    jobs: Dict[str, Any] = {}

    for key, job_id in rehearsal.ids.items():
        stored = service.repository.get(job_id)
        candidate = stored["block_candidate"]
        obligation = service.job_obligation(job_id, authority)
        jobs[key] = {
            "work_type": stored["work_type"],
            "track_id": stored["track_id"],
            "section_id": candidate["section_id"],
            "distance_start": stored["distance_start"],
            "distance_end": stored["distance_end"],
            "duration_minutes": candidate["duration_minutes"],
            "priority_score": stored["priority_score"],
            "risk_score": stored["risk_score"],
            "after_first_run": rehearsal.after_first[key],
            "after_second_run": rehearsal.after_second[key],
            "final": rehearsal.final[key],
            "obligation": {
                "type": obligation.obligation_type.value,
                "state": obligation.state.value,
                "owed_role": obligation.owed_role.value if obligation.owed_role else None,
            },
            "events": [
                entry.event.event_type.value
                for entry in service.job_history(job_id, authority)
            ],
        }

    return {
        "first_run": rehearsal.first_run,
        "second_run": rehearsal.second_run,
        "jobs": jobs,
    }


# ----------------------------------------------------------------------
# Invariants (checked by the tests and by --verify)
# ----------------------------------------------------------------------


def _windows_by_resource(service: JobService) -> Dict[Tuple[str, str], List[Tuple[int, int]]]:
    by_resource: Dict[Tuple[str, str], List[Tuple[int, int]]] = {}
    for window in service.possession_inputs().windows:
        by_resource.setdefault((window.section_id, window.track_id), []).append(
            (int(window.start_minute), int(window.end_minute))
        )
    return by_resource


def check_invariants(service: JobService, rehearsal: Rehearsal) -> List[str]:
    """Every property the walkthrough relies on. [] means all hold.

    Placement is checked against the possession windows independently of
    the solver's own report.
    """

    failures: List[str] = []
    windows = _windows_by_resource(service)

    for label, run in (("first", rehearsal.first_run), ("second", rehearsal.second_run)):
        if run["solver_status"] not in {"OPTIMAL", "FEASIBLE"}:
            failures.append(f"{label} optimization status is {run['solver_status']}")
        if run["counts"]["unscheduled"] != 0:
            failures.append(
                f"{label} optimization left jobs unscheduled: {run['unscheduled_reasons']}"
            )

    for key, job_id in rehearsal.ids.items():
        candidate = service.repository.get(job_id)["block_candidate"]
        resource = (candidate["section_id"], candidate["track_id"])
        duration = candidate["duration_minutes"]
        available = windows.get(resource, [])

        if not any(end - start >= duration for start, end in available):
            failures.append(f"{key}: no possession window on {resource} fits {duration} min")

        for label, placements in (
            ("first", rehearsal.after_first),
            ("second", rehearsal.after_second),
        ):
            placed = placements[key]
            start, end = placed["start_minute"], placed["end_minute"]
            if start is None or end is None:
                if not (label == "second" and key == REJECT_TARGET and placed["status"] == "reported"):
                    failures.append(f"{key}: not placed after the {label} run")
                continue
            if end - start != duration:
                failures.append(f"{key}: {label} placement is not {duration} min long")
            if not any(ws <= start and end <= we for ws, we in available):
                failures.append(f"{key}: {label} placement {start}-{end} is outside every window")

    target_first = rehearsal.after_first[POSTPONE_TARGET]
    target_second = rehearsal.after_second[POSTPONE_TARGET]
    if target_first["status"] != "scheduled" or target_first["end_minute"] is None:
        failures.append(f"postpone target has no proposal before postponing: {target_first}")
    elif not REAL_DATA_MODE and target_first["end_minute"] > DAY_MINUTES:
        # Synthetic scenario only: the search happens to put this job on day 1.
        # In real mode placement over the derived windows is decided by the
        # solver's search, so only the not-before date is checked (below).
        failures.append(f"postpone target is not on day 1 before postponing: {target_first}")
    if target_second["status"] != "scheduled" or target_second["start_minute"] is None or (
        target_second["start_minute"] < DAY_MINUTES
    ):
        failures.append(f"postpone target did not move to day 2: {target_second}")

    approved_first = rehearsal.after_first[APPROVE_TARGET]
    approved_second = rehearsal.after_second[APPROVE_TARGET]
    if approved_second["status"] != "notified" or (
        approved_second["start_minute"],
        approved_second["end_minute"],
    ) != (approved_first["start_minute"], approved_first["end_minute"]):
        failures.append(
            f"approved block moved or lost its commitment: {approved_first} -> {approved_second}"
        )

    approved_events = [
        entry.event
        for entry in service.history.list_for_job(rehearsal.ids[APPROVE_TARGET])
    ]
    if not any(
        event.event_type.value == "COMMITTED_BLOCK_PRESERVED"
        and event.optimization_run_id == rehearsal.second_run_id
        for event in approved_events
    ):
        failures.append("re-optimization recorded no COMMITTED_BLOCK_PRESERVED for the approved block")

    if rehearsal.final[APPROVE_TARGET]["status"] != "completed":
        failures.append("the approved job was not completed")

    if rehearsal.after_first[REJECT_TARGET]["status"] != "scheduled":
        failures.append("the reject target had no proposal to reject")

    return failures


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def _print_live_intake(service: JobService) -> None:
    request = job_request(LIVE_INTAKE_JOB, service)
    print("Live intake to report during the demo (use exactly these values):")
    print(f"  job_type        {request.job_type.value}")
    print(f"  severity        {LIVE_INTAKE_JOB.severity}")
    print(f"  track_id        {request.track_id}")
    print(
        f"  field_location  {LIVE_INTAKE_JOB.from_station_id} -> "
        f"{LIVE_INTAKE_JOB.toward_station_id}, "
        f"{LIVE_INTAKE_JOB.offset_start_m:g}-{LIVE_INTAKE_JOB.offset_end_m:g} m"
    )
    print(f"  distance        {request.distance_start:g}-{request.distance_end:g} m")
    print(f"  description     {request.description}")


def _require_demo_environment(environ: Mapping[str, str]) -> Path:
    from backend.app.api.composition import (
        AuthConfigurationError,
        AuthMode,
        resolve_auth_mode,
    )

    corridor = environ.get("PASHUPAT_CORRIDOR_ID")
    if corridor != DEMO_CORRIDOR_ID:
        raise DemoEnvironmentError(
            f"PASHUPAT_CORRIDOR_ID must be {DEMO_CORRIDOR_ID!r} (got {corridor!r}). "
            "Set it here AND for the backend server."
        )

    raw_path = environ.get("PASHUPAT_JOBS_DB")
    if not raw_path:
        raise DemoEnvironmentError(
            "PASHUPAT_JOBS_DB must name the dedicated demo database, e.g. "
            f"{SUGGESTED_DEMO_DB}. Set it here AND for the backend server."
        )

    try:
        mode = resolve_auth_mode(environ)
    except AuthConfigurationError as exc:
        raise DemoEnvironmentError(f"The backend would refuse to start: {exc}") from exc
    if mode is not AuthMode.DEMO:
        raise DemoEnvironmentError("The demo runs in demo auth mode; unset PASHUPAT_AUTH_MODE.")

    return Path(raw_path)


def _ortools_version() -> str:
    try:
        import ortools

        return ortools.__version__
    except Exception:  # noqa: BLE001 - informational only
        return "unknown"


def verify() -> int:
    """Rehearse the full demo on a temporary database. Never the demo DB."""

    with tempfile.TemporaryDirectory(prefix="pashupatastra-demo-verify-") as scratch:
        service = build_demo_service(Path(scratch) / "jobs.db")
        rehearsal = rehearse(service, seed(service))
        summary = business_summary(service, rehearsal)
        failures = check_invariants(service, rehearsal)

    print(f"OR-Tools {_ortools_version()}, corridor {DEMO_CORRIDOR_ID}")
    print(f"first run:  {summary['first_run']['solver_status']} {summary['first_run']['counts']}")
    print(f"second run: {summary['second_run']['solver_status']} {summary['second_run']['counts']}")
    for key, job in summary["jobs"].items():
        first, second, final = job["after_first_run"], job["after_second_run"], job["final"]
        print(
            f"  {key:20s} {job['work_type']:24s} {job['section_id']:8s} {job['track_id']:6s} "
            f"p={job['priority_score']:.3f} "
            f"run1 {first['status']}@{first['start_minute']}-{first['end_minute']} "
            f"run2 {second['status']}@{second['start_minute']}-{second['end_minute']} "
            f"final {final['status']}"
        )

    if failures:
        print("VERIFY FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("VERIFY PASS")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--verify",
        action="store_true",
        help="rehearse the whole demo on a temporary database and check it",
    )
    args = parser.parse_args(argv)

    if REAL_DATA_MODE:
        print(
            "REAL PUBLIC SNAPSHOT (offline, dated) + DEMO MAINTENANCE INPUTS - "
            "possession windows are derived candidates; no live Indian Railways data."
        )
    else:
        print("SYNTHETIC ILLUSTRATIVE SCENARIO - not Indian Railways operational data.")

    try:
        db_path = _require_demo_environment(os.environ)
    except DemoEnvironmentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.verify:
        return verify()

    try:
        reset_database(db_path)
    except DemoResetRefused as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    service = build_demo_service(db_path)
    ids = seed(service)

    print(f"Reset and seeded {db_path}")
    for job in SEED_JOBS:
        stored = service.repository.get(ids[job.key])
        print(
            f"  {stored['job_id']}  {job.job_type:24s} {job.severity:9s} "
            f"{job.from_station_id}->{job.toward_station_id} {job.track_id:6s} "
            f"priority {stored['priority_score']:.3f}  {stored['status']}"
        )
    _print_live_intake(service)
    extra = " (and PASHUPAT_RAILWAY_DATA=real)" if REAL_DATA_MODE else ""
    print(f"Start the backend with the SAME environment variables{extra}:")
    print("  python -m uvicorn backend.app.api.main:app --port 8000")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
