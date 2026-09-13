"""Loads the checked-in synthetic corridor dataset (Sprint 3 Step 10).

This is the ONE place that knows the on-disk shape of
pashupatastra_realistic_dataset.json. It exists so the jobs pipeline can
obtain a corridor that actually HAS a timetable without JobService
learning anything about file layout, JSON keys, or how timetable records
are spelled - JobService receives already-separated pieces (a Corridor, a
CorridorTopology, a SectionRegistry and an opaque sequence of timetable
records) and hands the records straight to the provider boundary.

WHAT THIS DATASET IS, EXACTLY
    Synthetic demo data. The file declares `"synthetic": true` and
    describes itself as an "operationally realistic synthetic demo
    dataset". It uses real Indian Railways station CODES (NDLS, NZM,
    FDB, PWL, MTJ, RKM, AGC) and plausible chainages, but that does not
    make it real railway data: nothing in it was transcribed from a
    published timetable or an official topology source, and no live feed
    exists. Its provenance on every axis is SYNTHETIC - see
    backend.app.data.provenance, and note that this is precisely the
    trap the Step 9 weakest-link rule exists to prevent: realistic-
    looking identifiers must never be allowed to read as REAL_*.

    load_corridor_dataset FAILS CLOSED if that `synthetic` flag is
    missing or false. No approved path in this repository ingests real
    railway data, so a file claiming to be real is refused rather than
    loaded and silently labelled.

WHAT IT IS NOT
    It is not the default deployment corridor. The default jobs corridor
    (CORRIDOR_A) is built by CorridorDataGenerator and has no timetable
    at all - see backend.app.jobs.service.JobService.possession_inputs
    for how that corridor is handled instead.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

from backend.app.data.canonical_train import CorridorTopology
from backend.app.data.generator import CorridorDataGenerator
from backend.app.data.models import Corridor, RouteClassification, TrackSegment
from backend.app.data.section_registry import SectionRegistry


# Repository root / pashupatastra_realistic_dataset.json. Resolved from
# this file rather than the process working directory so the loader
# behaves identically under pytest, uvicorn and a shell in any folder.
DEFAULT_DATASET_PATH = (
    Path(__file__).resolve().parents[3] / "pashupatastra_realistic_dataset.json"
)


class CorridorDatasetError(ValueError):
    """The dataset is missing, malformed, or not declared synthetic."""


@dataclass(frozen=True)
class CorridorDataset:
    """One loaded corridor: structure, section vocabulary and timetable.

    timetable_records stay OPAQUE dicts. They are not parsed here - the
    canonical adapter (backend.app.data.timetable_adapter, reached
    through backend.app.data.train_provider) is the only thing that
    reads a clock value, resolves a section or applies chronology rules.
    """

    corridor: Corridor
    topology: CorridorTopology
    registry: SectionRegistry
    timetable_records: Tuple[Dict[str, Any], ...]

    @property
    def corridor_id(self) -> str:
        return self.corridor.corridor_id


def _build_tracks(
    corridor_id: str,
    track_records: Sequence[Dict[str, Any]],
) -> list[TrackSegment]:
    """TrackSegments from the dataset's own track records.

    Built explicitly rather than through TrackSegment.from_dict because
    the dataset's track entries carry no corridor_id or segment_name -
    supplying those here keeps the missing-field handling visible
    instead of relying on from_dict's defaults.
    """

    tracks: list[TrackSegment] = []

    for record in track_records:
        track_id = str(record["track_id"])

        tracks.append(
            TrackSegment(
                track_id=track_id,
                corridor_id=corridor_id,
                segment_name=f"SEG-{corridor_id}-{track_id}",
                section_name=str(record.get("section_name", track_id)),
                direction=str(record.get("direction", "UP")),
                km_start=float(record.get("km_start", 0.0)),
                km_end=float(record.get("km_end", 0.0)),
                speed_limit_kmh=int(record.get("speed_limit_kmh", 130)),
                electrified=bool(record.get("electrified", True)),
                daily_train_density=int(
                    record.get("daily_train_density", 110)
                ),
                route_classification=str(
                    record.get(
                        "route_classification",
                        RouteClassification.GROUP_A.value,
                    )
                ),
            )
        )

    return tracks


def load_corridor_dataset(
    path: Optional[Path] = None,
    asset_seed: int = 42,
    assets_per_track: int = 8,
) -> CorridorDataset:
    """Load the checked-in synthetic corridor dataset.

    Assets are GENERATED (CorridorDataGenerator.generate_assets) over
    the dataset's own tracks rather than read from the file, because the
    dataset carries maintenance jobs but no asset inventory. That keeps
    the asset_condition provenance axis honestly SYNTHETIC and uses the
    repository's existing deterministic generator rather than inventing
    a second asset model here.
    """

    dataset_path = path or DEFAULT_DATASET_PATH

    try:
        with Path(dataset_path).open(encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise CorridorDatasetError(
            f"Corridor dataset not found at {dataset_path}."
        ) from exc
    except json.JSONDecodeError as exc:
        raise CorridorDatasetError(
            f"Corridor dataset at {dataset_path} is not valid JSON: {exc}"
        ) from exc

    # Fail closed on anything not explicitly declared synthetic. No
    # approved path in this repository ingests real railway data, so a
    # dataset that does not say it is synthetic is refused rather than
    # loaded and labelled by assumption.
    if data.get("synthetic") is not True:
        raise CorridorDatasetError(
            f"Dataset at {dataset_path} does not declare "
            '"synthetic": true. This loader refuses to load data that '
            "does not state it is synthetic - no real railway data "
            "source is approved or connected, and provenance must "
            "never be assumed."
        )

    corridor_record = data.get("corridor") or {}
    corridor_id = str(corridor_record.get("corridor_id") or "")

    if not corridor_id:
        raise CorridorDatasetError("Dataset corridor has no corridor_id.")

    stations = corridor_record.get("stations") or []
    track_records = corridor_record.get("tracks") or []

    if len(stations) < 2:
        raise CorridorDatasetError(
            f"Corridor {corridor_id!r} declares {len(stations)} "
            "station(s); at least two are needed to define a section."
        )

    if not track_records:
        raise CorridorDatasetError(
            f"Corridor {corridor_id!r} declares no tracks."
        )

    tracks = _build_tracks(corridor_id, track_records)

    # Both the section vocabulary and the traversal topology are built
    # from the SAME station list, through the same single section-naming
    # construction site (section_registry.format_section_id). That is
    # what makes every section_id the timetable adapter produces
    # resolvable in this registry - see Sprint 3 Step 7.
    registry = SectionRegistry.from_stations(
        corridor_id,
        stations,
        track_ids=[track.track_id for track in tracks],
    )

    topology = CorridorTopology.from_stations(stations)

    assets = CorridorDataGenerator(seed=asset_seed).generate_assets(
        tracks,
        num_assets_per_track=assets_per_track,
    )

    corridor = Corridor(
        corridor_id=corridor_id,
        name=str(corridor_record.get("name", corridor_id)),
        zone=str(corridor_record.get("zone", "Northern Railway")),
        division=str(corridor_record.get("division", "Delhi")),
        tracks=tracks,
        assets=assets,
    )

    return CorridorDataset(
        corridor=corridor,
        topology=topology,
        registry=registry,
        timetable_records=tuple(data.get("trains") or ()),
    )


__all__ = [
    "DEFAULT_DATASET_PATH",
    "CorridorDataset",
    "CorridorDatasetError",
    "load_corridor_dataset",
]
