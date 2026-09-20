"""Small hand-built corridors for the Slice 8 field-intake tests.

Every test here needs to control exactly where assets sit and where
station chainage falls, which the generated/dataset corridors do not
offer. Corridor "S8": stations A(0 km) - B(50 km) - C(100 km), so two
sections A-B [0, 50) and B-C [50, 100), served by the tracks given.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from backend.app.data.models import Asset, Corridor, Section, TrackSegment
from backend.app.data.section_registry import SectionRegistry
from backend.app.identity.actor import ActorRole, human_actor
from backend.app.jobs.models import JobCreateRequest
from backend.app.jobs.repository import JobRepository
from backend.app.jobs.service import JobService


CORRIDOR_ID = "S8"

STATIONS = [
    {"station_id": "A", "name": "Alpha", "km": 0.0},
    {"station_id": "B", "name": "Bravo", "km": 50.0},
    {"station_id": "C", "name": "Charlie", "km": 100.0},
]

WORKER = human_actor("WORKER-042", ActorRole.WORKER)
OTHER_WORKER = human_actor("WORKER-777", ActorRole.WORKER)


def make_asset(
    asset_id: str,
    km: float,
    track_id: str = "T1",
    asset_type: str = "OHE_MAST",
    **overrides,
) -> Asset:
    return Asset(
        asset_id=asset_id,
        name=f"{asset_id} @ KM {km}",
        asset_type=asset_type,
        track_id=track_id,
        km_location=km,
        **overrides,
    )


def make_registry(track_ids: Sequence[str] = ("T1",)) -> SectionRegistry:
    return SectionRegistry.from_stations(
        CORRIDOR_ID, STATIONS, track_ids=list(track_ids)
    )


def make_corridor(
    assets: Iterable[Asset],
    track_ids: Sequence[str] = ("T1",),
) -> Corridor:
    return Corridor(
        corridor_id=CORRIDOR_ID,
        name="Slice 8 test corridor",
        tracks=[
            TrackSegment(
                track_id=track_id,
                corridor_id=CORRIDOR_ID,
                segment_name=f"SEG-{track_id}",
                section_name=f"{track_id} main",
                direction="UP",
                km_start=0.0,
                km_end=100.0,
            )
            for track_id in track_ids
        ],
        assets=list(assets),
    )


def make_service(
    tmp_path: Path,
    assets: Iterable[Asset] | None = None,
    *,
    track_ids: Sequence[str] = ("T1",),
    registry: SectionRegistry | None = None,
    db_name: str = "jobs.db",
    **service_kwargs,
) -> JobService:
    if assets is None:
        assets = [make_asset("AST-T1-OHE-001", 1.2)]

    return JobService(
        repository=JobRepository(tmp_path / db_name),
        corridor=make_corridor(assets, track_ids),
        registry=registry or make_registry(track_ids),
        **service_kwargs,
    )


def make_request(
    start: float = 1_000.0,
    end: float = 1_400.0,
    *,
    track_id: str = "T1",
    job_type: str = "BALLAST_TAMPING",
    description: str = "field-reported defect",
    **extra,
) -> JobCreateRequest:
    return JobCreateRequest(
        track_id=track_id,
        job_type=job_type,
        distance_start=start,
        distance_end=end,
        workers_min=2,
        workers_max=4,
        description=description,
        **extra,
    )


def job_count(service: JobService) -> int:
    return len(service.repository.list_all())


def created_event(service: JobService, job_id: str):
    history = service.job_history(job_id)
    return next(
        stored.event
        for stored in history
        if stored.event.event_type.value == "JOB_CREATED"
    )
