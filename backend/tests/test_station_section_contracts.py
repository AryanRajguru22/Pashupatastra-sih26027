"""Sprint 3 Step 2: Station/Section domain models and section_id on contracts.

Covers:
  - Station and Section domain model construction (backend/app/data/models.py).
  - section_id on the canonical contracts BlockCandidate, PossessionWindow,
    and ScheduledBlock (contracts/schemas.py), alongside their existing
    bare track_id.
  - Compound track identifiers ("UP-1:7-32") are not required anywhere in
    the canonical contract shapes - section_id and track_id are always two
    separate fields.
  - Existing checked-in fixtures still load through contracts.from_dict.
  - maintenance_jobs remains a job_id/.../block_candidate_json blob table -
    adding section_id to BlockCandidate did not add or rename a column.

This file adds new coverage; it does not modify or weaken any existing
test.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.app.data.models import Section, Station
from backend.app.jobs.repository import JobRepository
from contracts import BlockCandidate, OptimizationRequest, PossessionWindow, ScheduledBlock


REPO_ROOT = Path(__file__).resolve().parents[2]

FIXTURES_DIR = REPO_ROOT / "backend" / "app" / "data" / "fixtures"

CANONICAL_FIXTURES = [
    "corridor_a_blocks.json",
    "corridor_b_dense.json",
    "corridor_c_disrupted.json",
]


# --------------------------------------------------------------------------
# Task 7.1 - Station model construction
# --------------------------------------------------------------------------


def test_station_constructs_with_required_fields():
    station = Station(
        station_id="NDLS",
        corridor_id="CORRIDOR_A",
        name="New Delhi",
        km=0.0,
    )

    assert station.station_id == "NDLS"
    assert station.corridor_id == "CORRIDOR_A"
    assert station.name == "New Delhi"
    assert station.km == 0.0


def test_station_round_trips_through_dict():
    station = Station(station_id="NZM", corridor_id="CORRIDOR_A", name="Hazrat Nizamuddin", km=7.0)

    restored = Station.from_dict(station.to_dict())

    assert restored == station


def test_station_from_dict_accepts_realistic_dataset_shape():
    # backend.app.data.train_adapter.section_for_km consumes dicts shaped
    # exactly like pashupatastra_realistic_dataset.json's corridor.stations
    # entries: station_id/name/km, no corridor_id (it's implied by nesting).
    raw = {"station_id": "FDB", "name": "Faridabad", "km": 32.0, "platforms": 6}

    station = Station.from_dict(raw)

    assert station.station_id == "FDB"
    assert station.name == "Faridabad"
    assert station.km == 32.0


# --------------------------------------------------------------------------
# Task 7.2 - Section model construction
# --------------------------------------------------------------------------


def test_section_constructs_with_station_pair_identity():
    section = Section(
        section_id="NDLS-NZM",
        corridor_id="CORRIDOR_A",
        track_id="UP-1",
        start_station_id="NDLS",
        end_station_id="NZM",
        km_start=0.0,
        km_end=7.0,
    )

    assert section.section_id == "NDLS-NZM"
    assert section.track_id == "UP-1"
    assert section.start_station_id == "NDLS"
    assert section.end_station_id == "NZM"
    assert section.km_start == 0.0
    assert section.km_end == 7.0


def test_section_id_is_not_a_compound_track_id():
    section = Section(
        section_id="NDLS-NZM",
        corridor_id="CORRIDOR_A",
        track_id="UP-1",
        start_station_id="NDLS",
        end_station_id="NZM",
        km_start=0.0,
        km_end=7.0,
    )

    # The architecture decision this Step implements: track_id must stay
    # bare. section_id is a separate identifier, not a "track_id:km-km"
    # compound baked into the track field.
    assert ":" not in section.track_id
    assert section.track_id == "UP-1"
    assert section.section_id != f"{section.track_id}:{section.km_start:g}-{section.km_end:g}"


def test_section_round_trips_through_dict():
    section = Section(
        section_id="NZM-FDB",
        corridor_id="CORRIDOR_A",
        track_id="DOWN-1",
        start_station_id="NZM",
        end_station_id="FDB",
        km_start=7.0,
        km_end=32.0,
    )

    restored = Section.from_dict(section.to_dict())

    assert restored == section


# --------------------------------------------------------------------------
# Task 7.3-7.5 - contracts carry track_id + section_id
# --------------------------------------------------------------------------


def test_block_candidate_carries_track_id_and_section_id():
    candidate = BlockCandidate(
        block_id="BLK-001",
        asset_id="AST-001",
        track_id="UP-1",
        work_type="BALLAST_TAMPING",
        duration_minutes=90,
        section_id="NDLS-NZM",
    )

    assert candidate.track_id == "UP-1"
    assert candidate.section_id == "NDLS-NZM"
    assert candidate.to_dict()["section_id"] == "NDLS-NZM"

    restored = BlockCandidate.from_dict(candidate.to_dict())
    assert restored.section_id == "NDLS-NZM"


def test_block_candidate_section_id_defaults_to_none():
    # Existing construction sites across the codebase that predate Step 2
    # must keep working unchanged.
    candidate = BlockCandidate(
        block_id="BLK-002",
        asset_id="AST-002",
        track_id="DOWN-1",
        work_type="ROUTINE_INSPECTION",
        duration_minutes=45,
    )

    assert candidate.section_id is None
    assert BlockCandidate.from_dict({
        "block_id": "BLK-003",
        "asset_id": "AST-003",
        "track_id": "UP-1",
        "work_type": "ROUTINE_INSPECTION",
        "duration_minutes": 45,
    }).section_id is None


def test_possession_window_carries_track_id_and_section_id():
    window = PossessionWindow(
        window_id="POS-UP1-01",
        track_id="UP-1",
        start_minute=30,
        end_minute=300,
        section_id="NDLS-NZM",
    )

    assert window.track_id == "UP-1"
    assert window.section_id == "NDLS-NZM"

    restored = PossessionWindow.from_dict(window.to_dict())
    assert restored.section_id == "NDLS-NZM"


def test_scheduled_block_carries_track_id_and_section_id():
    scheduled = ScheduledBlock(
        block_id="BLK-001",
        track_id="UP-1",
        start_minute=30,
        end_minute=120,
        work_type="BALLAST_TAMPING",
        section_id="NDLS-NZM",
    )

    assert scheduled.track_id == "UP-1"
    assert scheduled.section_id == "NDLS-NZM"

    restored = ScheduledBlock.from_dict(scheduled.to_dict())
    assert restored.section_id == "NDLS-NZM"


# --------------------------------------------------------------------------
# Task 7.6 - compound IDs are not required by the canonical contracts
# --------------------------------------------------------------------------


def test_canonical_contracts_never_require_a_compound_track_id():
    """track_id and section_id are independently settable, bare strings.

    Nothing in the canonical dataclasses forces or derives a
    "track_id:km-km" style compound - that pattern exists only in the
    (untouched, Step-3-scope) train adapter's section-qualified track
    helpers, never in the contract shapes themselves.
    """

    candidate = BlockCandidate(
        block_id="BLK-001",
        asset_id="AST-001",
        track_id="UP-1",
        work_type="BALLAST_TAMPING",
        duration_minutes=90,
    )
    window = PossessionWindow(
        window_id="POS-UP1-01", track_id="UP-1", start_minute=30, end_minute=300
    )
    scheduled = ScheduledBlock(
        block_id="BLK-001", track_id="UP-1", start_minute=30, end_minute=120, work_type="BALLAST_TAMPING"
    )

    for obj in (candidate, window, scheduled):
        assert ":" not in obj.track_id
        assert obj.section_id is None


# --------------------------------------------------------------------------
# Task 7.7 - existing fixtures still load
# --------------------------------------------------------------------------


def test_existing_fixtures_still_load_through_contracts():
    for filename in CANONICAL_FIXTURES:
        with open(FIXTURES_DIR / filename, encoding="utf-8") as f:
            data = json.load(f)

        request = OptimizationRequest.from_dict(data)

        assert request.candidates
        assert all(isinstance(c.section_id, (str, type(None))) for c in request.candidates)


def test_golden_scenario_fixture_predates_section_id_and_still_loads():
    # golden_scenario.json is hand-authored and nested (initial_request /
    # initial_expected_solution), unlike the generator-produced fixtures
    # above. It has no section_id anywhere - from_dict must not require it.
    with open(FIXTURES_DIR / "golden_scenario.json", encoding="utf-8") as f:
        data = json.load(f)

    request = OptimizationRequest.from_dict(data["initial_request"])

    assert request.candidates
    assert all(c.section_id is None for c in request.candidates)


# --------------------------------------------------------------------------
# Task 7.9 - maintenance_jobs schema is unchanged
# --------------------------------------------------------------------------

_EXPECTED_MAINTENANCE_JOBS_COLUMNS = {
    "job_id",
    "track_id",
    "work_type",
    "distance_start",
    "distance_end",
    "workers_min",
    "workers_max",
    "description",
    "status",
    "priority_score",
    "risk_score",
    "schedule_start_minute",
    "schedule_end_minute",
    "created_at",
    "block_candidate_json",
    "updated_at",
    "last_solver_status",
    "last_refusal_reason",
}


def test_maintenance_jobs_table_columns_are_unchanged(tmp_path: Path):
    """block_candidate is stored as one JSON blob column.

    Adding section_id to BlockCandidate changes what ends up inside
    block_candidate_json, not the table's column list - there is no
    per-contract-field column to add or migrate.
    """

    repository = JobRepository(tmp_path / "jobs.db")

    import sqlite3

    with sqlite3.connect(repository.db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(maintenance_jobs)")}

    assert columns == _EXPECTED_MAINTENANCE_JOBS_COLUMNS
    assert "section_id" not in columns
