"""Shared fixtures for the Slice 10.1D resource-authorization tests.

A three-section corridor is the smallest topology that can tell the
scope rules apart: one section inside a scope, one outside it, and a
third to prove nothing adjacent is inferred. The deployment default
corridor (CORRIDOR_A) has a single section, so on it "outside my scope"
is unreachable and every scope test would pass vacuously.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from backend.app.data.generator import CorridorDataGenerator
from backend.app.data.models import Corridor, TrackSegment
from backend.app.data.section_registry import SectionRegistry
from backend.app.identity.actor import ActorRole, human_actor
from backend.app.identity.person import ExternalIdentity, Person
from backend.app.identity.policy import EnforcingPolicy, StaticScopeDirectory
from backend.app.identity.scope import RailwayScope
from backend.app.identity.scope_assignment import RoleScopeAssignment
from backend.app.jobs.models import JobCreateRequest
from backend.app.jobs.repository import JobRepository
from backend.app.jobs.service import JobService

CORRIDOR = "THREE_SECTION_CORRIDOR"
OTHER_CORRIDOR = "OTHER_CORRIDOR"
TRACK = "T1"

STATIONS = [
    {"station_id": "X", "name": "X", "km": 0.0},
    {"station_id": "Y", "name": "Y", "km": 100.0},
    {"station_id": "Z", "name": "Z", "km": 200.0},
    {"station_id": "W", "name": "W", "km": 300.0},
]

# Section ids follow the one naming convention (format_section_id).
S_XY = "X-Y"
S_YZ = "Y-Z"
S_ZW = "Z-W"

# A job span wholly inside each section, in METRES (JobCreateRequest's unit).
IN_XY = (49_500.0, 50_500.0)
IN_YZ = (149_500.0, 150_500.0)
IN_ZW = (249_500.0, 250_500.0)

WORKER_ID = "WORKER-042"
ENGINEER_ID = "ENGINEER-003"
AUTHORITY_ID = "AUTHORITY-017"
ADMIN_ID = "ADMIN-001"

WORKER = human_actor(WORKER_ID, ActorRole.WORKER)
ENGINEER = human_actor(ENGINEER_ID, ActorRole.ENGINEER)
AUTHORITY = human_actor(AUTHORITY_ID, ActorRole.AUTHORITY)
ADMIN = human_actor(ADMIN_ID, ActorRole.ADMIN)


def corridor_and_registry(
    corridor_id: str = CORRIDOR,
) -> tuple[Corridor, SectionRegistry]:
    tracks = [
        TrackSegment(
            track_id=TRACK,
            corridor_id=corridor_id,
            segment_name=f"SEG-{TRACK}",
            section_name=f"{TRACK} main",
            direction="UP",
            km_start=0.0,
            km_end=300.0,
        )
    ]

    assets = CorridorDataGenerator(seed=7).generate_assets(
        tracks, num_assets_per_track=12
    )

    corridor = Corridor(
        corridor_id=corridor_id,
        name=corridor_id.replace("_", " ").title(),
        tracks=tracks,
        assets=assets,
    )

    registry = SectionRegistry.from_stations(
        corridor_id, STATIONS, track_ids=[TRACK]
    )

    return corridor, registry


def build_service(
    tmp_path: Path,
    authorization: Any = None,
    corridor_id: str = CORRIDOR,
) -> JobService:
    corridor, registry = corridor_and_registry(corridor_id)

    return JobService(
        repository=JobRepository(tmp_path / "jobs.db"),
        corridor=corridor,
        registry=registry,
        authorization=authorization,
    )


def person(person_id: str) -> Person:
    return Person(
        person_id=person_id,
        identity=ExternalIdentity("novaforge", f"subject-{person_id}"),
    )


def scope(*section_ids: str, corridor_id: str = CORRIDOR) -> RailwayScope:
    return RailwayScope(corridor_id, list(section_ids))


def directory(
    *assignments: RoleScopeAssignment,
    people: Iterable[str] = (WORKER_ID, ENGINEER_ID, AUTHORITY_ID, ADMIN_ID),
) -> StaticScopeDirectory:
    return StaticScopeDirectory(
        people=[person(pid) for pid in people],
        assignments=assignments,
    )


def assignment(
    person_id: str,
    role: ActorRole,
    *section_ids: str,
    corridor_id: str = CORRIDOR,
) -> RoleScopeAssignment:
    return RoleScopeAssignment(
        person_id=person_id,
        role=role,
        scope=scope(*section_ids, corridor_id=corridor_id),
    )


def enforcing(*assignments: RoleScopeAssignment) -> EnforcingPolicy:
    """A real enforcing policy over an in-memory directory."""

    return EnforcingPolicy(directory(*assignments))


def fully_scoped() -> EnforcingPolicy:
    """Every operational role scoped to every section of the corridor."""

    return enforcing(
        assignment(WORKER_ID, ActorRole.WORKER, S_XY, S_YZ, S_ZW),
        assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY, S_YZ, S_ZW),
        assignment(AUTHORITY_ID, ActorRole.AUTHORITY, S_XY, S_YZ, S_ZW),
    )


def job_request(
    span: tuple[float, float] = IN_XY,
    description: str = "slice 10.1D job",
    track_id: str = TRACK,
) -> JobCreateRequest:
    start, end = span

    return JobCreateRequest(
        track_id=track_id,
        job_type="BALLAST_TAMPING",
        distance_start=start,
        distance_end=end,
        workers_min=2,
        workers_max=4,
        description=description,
    )


def report(
    service: JobService,
    span: tuple[float, float] = IN_XY,
    actor: Any = WORKER,
    description: str = "slice 10.1D job",
) -> dict[str, Any]:
    return service.create_job(
        job_request(span, description=description), actor=actor
    )


def section_of(job: dict[str, Any]) -> str | None:
    return (job.get("block_candidate") or {}).get("section_id")


def count_events(db_path: Path) -> int:
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM job_events").fetchone()[0]


def event_types(db_path: Path) -> list[str]:
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(db_path)) as conn:
        return [
            row[0]
            for row in conn.execute(
                "SELECT event_type FROM job_events ORDER BY sequence"
            )
        ]
