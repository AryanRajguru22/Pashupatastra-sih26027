"""Loads the offline NDLS -> AGC public-railway-data snapshot (real-data path).

This is the ADDITIVE counterpart of backend.app.data.corridor_dataset. That
loader refuses anything not declared synthetic and stays exactly as it is -
flipping its flag for real data would defeat its fail-closed guard. This
module is the one place that loads the real snapshot, and it produces the same
CorridorDataset the rest of the jobs pipeline already consumes (Corridor,
CorridorTopology, SectionRegistry, opaque timetable records), so the optimizer
sees its canonical inputs unchanged.

WHAT THE SNAPSHOT IS
    data/railway/ndls_agc/ : manifest.json, topology.json, timetable.json,
    infrastructure.json, works.json, blocks.json, rules.json and sources/.
    It is produced once, offline, by scripts/real_data/build_ndls_agc_snapshot.py
    from public documents that were downloaded, hashed and stored. NOTHING here
    (or anywhere in the runtime) fetches from the network.

FAIL-CLOSED
    load_real_snapshot raises RealSnapshotError - it never repairs and never
    falls back - if:
      - the manifest is missing or names a different corridor;
      - a dataset file or a stored source file is missing or its SHA-256 differs
        from the manifest;
      - any provenance record carries an unknown class, an unknown source id, or
        is DERIVED_FROM_REAL without derived_from / derivation_method;
      - the topology is not strictly increasing / contiguous, or a timetable
        record names an unknown station, track or direction.

WHAT IT CLAIMS (see DataBasis below)
    topology    REAL_STATIC-tier: dated public snapshot (the per-field classes,
                including DERIVED_FROM_REAL chainage, live in the provenance
                records - the coarse enum cannot express them).
    timetable   REAL_SCHEDULED: the TAG-2026 published schedule.
    possession  SYNTHETIC tier on the coarse enum: windows DERIVED by rule from the
                public timetable are a computed artifact, never a real or scheduled
                possession. The true class (DERIVED_FROM_REAL) lives in the
                provenance records and in possession_source
                ("DERIVED_FROM_PUBLIC_TIMETABLE").
    asset       SYNTHETIC: asset condition is a demo input; no public source.
    The weakest-link rule therefore still reports the effective provenance of
    every optimization as SYNTHETIC.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.app.data.canonical_train import CorridorTopology
from backend.app.data.corridor_dataset import CorridorDataset, DataBasis
from backend.app.data.generator import CorridorDataGenerator
from backend.app.data.models import Corridor, TrackSegment
from backend.app.data.provenance import ProvenanceLevel
from backend.app.data.section_registry import SectionRegistry


DEFAULT_SNAPSHOT_DIR = (
    Path(__file__).resolve().parents[3] / "data" / "railway" / "ndls_agc"
)

SUPPORTED_SCHEMA_VERSION = 1

DATA_CLASSES = (
    "REAL",
    "REAL_DATED_SNAPSHOT",
    "DERIVED_FROM_REAL",
    "ENGINEERING_ASSUMPTION",
    "DEMO_MAINTENANCE_INPUT",
    "UNAVAILABLE_PUBLICLY",
)

REQUIRED_DATASETS = (
    "topology",
    "timetable",
    "infrastructure",
    "works",
    "blocks",
    "rules",
)

# The label shown wherever these windows appear. Never "real possession".
CANDIDATE_WINDOW_LABEL = (
    "CANDIDATE POSSESSION WINDOW - DERIVED FROM PUBLIC PASSENGER TIMETABLE"
)


class RealSnapshotError(ValueError):
    """The real-data snapshot is missing, altered or internally inconsistent."""


@dataclass(frozen=True)
class RealSnapshot:
    """The verified, in-memory snapshot. Plain dicts; nothing is interpreted."""

    directory: Path
    manifest: Dict[str, Any]
    topology: Dict[str, Any]
    timetable: Dict[str, Any]
    infrastructure: Dict[str, Any]
    works: Dict[str, Any]
    blocks: Dict[str, Any]
    rules: Dict[str, Any]

    @property
    def snapshot_id(self) -> str:
        return str(self.manifest["snapshot_id"])

    @property
    def corridor_id(self) -> str:
        return str(self.manifest["corridor_id"])

    def source(self, source_id: str) -> Dict[str, Any]:
        for src in self.manifest["sources"]:
            if src["source_id"] == source_id:
                return src
        raise KeyError(source_id)

    def all_provenance_records(self) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for name in REQUIRED_DATASETS:
            records.extend(getattr(self, name).get("provenance", []))
        return records


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise RealSnapshotError(f"Snapshot file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RealSnapshotError(f"Snapshot file {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RealSnapshotError(f"Snapshot file {path} must hold a JSON object.")
    return data


def _validate_provenance(records: List[Dict[str, Any]], source_ids: set) -> None:
    for record in records:
        field_name = record.get("field", "<unnamed>")
        cls = record.get("class")

        if cls not in DATA_CLASSES:
            raise RealSnapshotError(
                f"Provenance record {field_name!r} has unknown class {cls!r}; "
                f"permitted: {list(DATA_CLASSES)}."
            )

        for sid in record.get("source_ids") or []:
            if sid not in source_ids:
                raise RealSnapshotError(
                    f"Provenance record {field_name!r} cites unknown source {sid!r}."
                )

        if cls == "DERIVED_FROM_REAL" and not (
            record.get("derived_from") and record.get("derivation_method")
        ):
            raise RealSnapshotError(
                f"Provenance record {field_name!r} is DERIVED_FROM_REAL but does "
                "not say what it was derived from and how."
            )

        if cls in ("REAL", "REAL_DATED_SNAPSHOT") and not record.get("source_ids"):
            raise RealSnapshotError(
                f"Provenance record {field_name!r} is {cls} but cites no source."
            )

        if not record.get("confidence"):
            raise RealSnapshotError(
                f"Provenance record {field_name!r} states no confidence."
            )


def load_real_snapshot(directory: Optional[Path] = None) -> RealSnapshot:
    """Load and VERIFY the snapshot. Raises RealSnapshotError on any doubt."""

    root = Path(directory) if directory is not None else DEFAULT_SNAPSHOT_DIR

    manifest = _read_json(root / "manifest.json")

    if manifest.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
        raise RealSnapshotError(
            f"Unsupported snapshot schema_version {manifest.get('schema_version')!r}."
        )

    if manifest.get("offline") is not True:
        raise RealSnapshotError("The snapshot manifest must declare offline: true.")

    if list(manifest.get("data_classes", [])) != list(DATA_CLASSES):
        raise RealSnapshotError("The manifest's data_classes differ from the supported vocabulary.")

    # Integrity: every stored source and every dataset file matches its hash.
    for src in manifest.get("sources", []):
        path = root / "sources" / src["file"]
        if not path.is_file():
            raise RealSnapshotError(f"Stored source missing: {path}")
        if _sha256(path) != src["sha256"]:
            raise RealSnapshotError(
                f"Stored source {src['file']} does not match the manifest hash "
                f"({src['source_id']}). The snapshot has been altered."
            )

    loaded: Dict[str, Dict[str, Any]] = {}
    for name in REQUIRED_DATASETS:
        entry = manifest.get("datasets", {}).get(name)
        if not entry:
            raise RealSnapshotError(f"Manifest lists no {name!r} dataset.")
        path = root / entry["file"]
        if not path.is_file():
            raise RealSnapshotError(f"Snapshot dataset missing: {path}")
        if _sha256(path) != entry["sha256"]:
            raise RealSnapshotError(
                f"Snapshot dataset {entry['file']} does not match the manifest hash. "
                "The snapshot has been altered; rebuild it with "
                "scripts/real_data/build_ndls_agc_snapshot.py."
            )
        loaded[name] = _read_json(path)
        if loaded[name].get("snapshot_id") != manifest.get("snapshot_id"):
            raise RealSnapshotError(f"{entry['file']} belongs to a different snapshot.")

    snapshot = RealSnapshot(directory=root, manifest=manifest, **loaded)

    source_ids = {s["source_id"] for s in manifest["sources"]}
    _validate_provenance(snapshot.all_provenance_records(), source_ids)
    _validate_structure(snapshot)

    return snapshot


def _validate_structure(snapshot: RealSnapshot) -> None:
    topology = snapshot.topology

    if topology.get("corridor_id") != snapshot.corridor_id:
        raise RealSnapshotError("topology.json names a different corridor than the manifest.")

    if topology.get("distance_unit") != "km":
        raise RealSnapshotError("The snapshot's domain distance unit must be km.")

    stations = topology.get("stations") or []
    if len(stations) < 2:
        raise RealSnapshotError("The snapshot declares fewer than two stations.")

    km = [float(s["km"]) for s in stations]
    if any(b <= a for a, b in zip(km, km[1:])):
        raise RealSnapshotError("Station chainage must be strictly increasing.")

    ids = [s["station_id"] for s in stations]
    sections = topology.get("sections") or []
    if [s["section_id"] for s in sections] != [f"{a}-{b}" for a, b in zip(ids, ids[1:])]:
        raise RealSnapshotError("Sections must be exactly the adjacent station pairs, in order.")

    for section, a, b in zip(sections, stations, stations[1:]):
        if float(section["km_start"]) != float(a["km"]) or float(section["km_end"]) != float(b["km"]):
            raise RealSnapshotError(
                f"Section {section['section_id']} chainage disagrees with its stations."
            )
        if not section.get("zone") or not section.get("division"):
            raise RealSnapshotError(
                f"Section {section['section_id']} carries no zone/division."
            )

    tracks = {t["track_id"]: t for t in topology.get("tracks") or []}
    if not tracks:
        raise RealSnapshotError("The snapshot declares no tracks.")

    for record in snapshot.timetable.get("service_records") or []:
        number = record.get("train_number")
        if record.get("track_assignment") not in tracks:
            raise RealSnapshotError(f"Train {number} names an unknown track.")
        if tracks[record["track_assignment"]]["direction"] != record.get("direction"):
            raise RealSnapshotError(f"Train {number}: direction disagrees with its track.")
        for stop in record.get("station_times") or []:
            if stop["station_id"] not in ids:
                raise RealSnapshotError(
                    f"Train {number} names unknown station {stop['station_id']!r}."
                )
        if not record.get("service_date"):
            raise RealSnapshotError(f"Train {number} has no service_date.")


def _build_tracks(corridor_id: str, records: List[Dict[str, Any]]) -> List[TrackSegment]:
    return [
        TrackSegment(
            track_id=str(t["track_id"]),
            corridor_id=corridor_id,
            segment_name=f"SEG-{corridor_id}-{t['track_id']}",
            section_name=str(t["section_name"]),
            direction=str(t["direction"]),
            km_start=float(t["km_start"]),
            km_end=float(t["km_end"]),
            speed_limit_kmh=int(t["speed_limit_kmh"]),
            electrified=bool(t["electrified"]),
            daily_train_density=int(t["daily_train_density"]),
            route_classification=str(t["route_classification"]),
        )
        for t in records
    ]


def build_real_corridor_dataset(
    snapshot: RealSnapshot,
    asset_seed: Optional[int] = None,
    assets_per_track: Optional[int] = None,
) -> CorridorDataset:
    """The canonical CorridorDataset over a verified snapshot.

    asset_seed / assets_per_track default to the snapshot's own declared demo
    inputs (rules.json demo_inputs.asset_condition), so the demo assets are
    part of the reviewed data rather than a literal here.
    """

    demo_assets = snapshot.rules["demo_inputs"]["asset_condition"]
    if asset_seed is None:
        asset_seed = int(demo_assets["asset_seed"])
    if assets_per_track is None:
        assets_per_track = int(demo_assets["assets_per_track"])

    topology = snapshot.topology
    corridor_id = snapshot.corridor_id

    stations = [
        {"station_id": s["station_id"], "name": s["name"], "km": float(s["km"])}
        for s in topology["stations"]
    ]

    tracks = _build_tracks(corridor_id, topology["tracks"])

    registry = SectionRegistry.from_stations(
        corridor_id,
        stations,
        track_ids=[t.track_id for t in tracks],
    )
    canonical_topology = CorridorTopology.from_stations(stations)

    # Assets are DEMO inputs: the same deterministic generator the synthetic
    # path uses. No public source gives defect-level asset condition, so this
    # axis stays SYNTHETIC (DataBasis.asset_condition) on purpose.
    assets = CorridorDataGenerator(seed=asset_seed).generate_assets(
        tracks, num_assets_per_track=assets_per_track
    )

    corridor = Corridor(
        corridor_id=corridor_id,
        name=str(topology["name"]),
        zone=str(topology["zone"]),
        division=str(topology["division"]),
        tracks=tracks,
        assets=assets,
    )

    rules = snapshot.rules["engineering_rules"]

    basis = DataBasis(
        label="REAL_PUBLIC_SNAPSHOT",
        topology=ProvenanceLevel.REAL_STATIC,
        timetable_train_provenance="REAL_SCHEDULED",
        asset_condition=ProvenanceLevel.SYNTHETIC,
        # Candidate windows derived by rule from a real timetable are NOT a
        # real scheduled possession, so the coarse axis is the conservative
        # SYNTHETIC tier (never above the timetable, and never "real"). The
        # accurate class - DERIVED_FROM_REAL - is carried by possession_source
        # below, the rules.json provenance and the UI label.
        derived_possession=ProvenanceLevel.SYNTHETIC,
        possession_source_label="DERIVED_FROM_PUBLIC_TIMETABLE",
        safety_buffer_minutes=int(rules["safety_buffer_minutes"]),
        minimum_window_minutes=int(rules["minimum_window_minutes"]),
        snapshot_id=snapshot.snapshot_id,
    )

    records = tuple(
        {k: v for k, v in record.items()}
        for record in snapshot.timetable["service_records"]
    )

    return CorridorDataset(
        corridor=corridor,
        topology=canonical_topology,
        registry=registry,
        timetable_records=records,
        basis=basis,
        snapshot=snapshot,
    )


def load_real_corridor_dataset(
    directory: Optional[Path] = None,
    asset_seed: Optional[int] = None,
    assets_per_track: Optional[int] = None,
) -> CorridorDataset:
    return build_real_corridor_dataset(
        load_real_snapshot(directory),
        asset_seed=asset_seed,
        assets_per_track=assets_per_track,
    )


__all__ = [
    "CANDIDATE_WINDOW_LABEL",
    "DEFAULT_SNAPSHOT_DIR",
    "RealSnapshot",
    "RealSnapshotError",
    "build_real_corridor_dataset",
    "load_real_corridor_dataset",
    "load_real_snapshot",
]
