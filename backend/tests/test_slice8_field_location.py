"""Sprint 3 Slice 8, item 3: human-friendly location intake.

field location (from station, toward station, offset)
    -> section-relative -> corridor-absolute metres
    -> the UNCHANGED resolve_job_resource() -> (track, section)

The converter is not a second location model: only corridor-absolute
metres are stored, distance_start/distance_end stay REQUIRED on the frozen
v1 request, and a field location is a cross-check on them.
"""

from __future__ import annotations

import math
import sqlite3
import uuid
from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.api.main import app
from backend.app.data.corridor_dataset import load_corridor_dataset
from backend.app.data.models import Section
from backend.app.data.section_registry import SectionRegistry
from backend.app.jobs.field_location import (
    FieldLocationError,
    LocationInputConflictError,
    convert_field_location,
)
from backend.app.jobs.models import FieldLocation, JobCreateRequest
from backend.app.jobs.repository import JobRepository
from backend.app.jobs.router import service as router_service
from backend.app.jobs.service import JobService
from backend.tests.slice8_helpers import (
    CORRIDOR_ID,
    STATIONS,
    WORKER,
    created_event,
    job_count,
    make_asset,
    make_registry,
    make_request,
    make_service,
)


@pytest.fixture
def registry():
    return make_registry(("T1",))


def fl(frm, toward, start, end):
    return FieldLocation(
        from_station_id=frm,
        toward_station_id=toward,
        offset_start_m=start,
        offset_end_m=end,
    )


# ----------------------------------------------------------------------
# Conversion: deterministic and direction-aware
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "frm, toward, start, end, expected_start, expected_end, section",
    [
        # measured from the section's low-km station
        ("A", "B", 1_000.0, 1_400.0, 1_000.0, 1_400.0, "A-B"),
        ("B", "C", 400.0, 800.0, 50_400.0, 50_800.0, "B-C"),
        # measured from the high-km station: larger offset = lower chainage
        ("B", "A", 1_000.0, 1_400.0, 48_600.0, 49_000.0, "A-B"),
        ("C", "B", 400.0, 800.0, 99_200.0, 99_600.0, "B-C"),
        # either order of the pair names the same section
        ("A", "B", 0.0, 100.0, 0.0, 100.0, "A-B"),
    ],
)
def test_conversion_to_corridor_absolute_metres(
    registry, frm, toward, start, end, expected_start, expected_end, section
):
    converted = convert_field_location(registry, frm, toward, start, end)

    assert converted.distance_start_m == expected_start
    assert converted.distance_end_m == expected_end
    assert converted.section_id == section
    assert converted.section_length_m == 50_000.0


def test_conversion_is_deterministic(registry):
    results = {
        convert_field_location(registry, "B", "A", 1_000.0, 1_400.0)
        for _ in range(5)
    }

    assert len(results) == 1


def test_conversion_on_the_checked_in_dataset_corridor():
    dataset = load_corridor_dataset()

    # NZM-FDB spans km 7-32; 500-900 m from FDB toward NZM is km 31.1-31.5.
    converted = convert_field_location(
        dataset.registry, "FDB", "NZM", 500.0, 900.0
    )

    assert converted.section_id == "NZM-FDB"
    assert converted.distance_start_m == 31_100.0
    assert converted.distance_end_m == 31_500.0

    # And the same span from the other end agrees.
    assert convert_field_location(
        dataset.registry, "NZM", "FDB", 24_100.0, 24_500.0
    ).distance_start_m == 31_100.0


# ----------------------------------------------------------------------
# Fail closed
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "frm, toward, start, end, message",
    [
        ("ZZZ", "B", 100.0, 200.0, "No section joins"),  # unknown station
        ("A", "ZZZ", 100.0, 200.0, "No section joins"),
        ("A", "C", 100.0, 200.0, "No section joins"),  # not adjacent
        ("A", "A", 100.0, 200.0, "same station"),
        ("", "B", 100.0, 200.0, "both required"),
        ("A", "B", -1.0, 200.0, "offset_start_m must be >= 0"),  # negative
        ("A", "B", 200.0, 200.0, "greater than offset_start_m"),
        ("A", "B", 300.0, 200.0, "greater than offset_start_m"),
        ("A", "B", 100.0, 50_000.001, "beyond section"),  # past the far station
        ("A", "B", 100.0, 99_000.0, "beyond section"),
        ("A", "B", math.nan, 200.0, "finite"),
        ("A", "B", 100.0, math.inf, "finite"),
    ],
)
def test_invalid_locations_fail_closed(registry, frm, toward, start, end, message):
    with pytest.raises(FieldLocationError, match=message):
        convert_field_location(registry, frm, toward, start, end)


def test_an_ambiguous_station_pair_is_refused_never_guessed():
    """Parallel routes between the same two stations: the pair does not
    say which section is meant, so nothing is chosen."""

    sections = [
        Section("A-B-MAIN", "S8", "A", "B", 0.0, 50.0, track_ids=("T1",)),
        Section("A-B-LOOP", "S8", "A", "B", 0.0, 52.0, track_ids=("T2",)),
    ]
    ambiguous = SectionRegistry("S8", sections)

    with pytest.raises(FieldLocationError, match="do not identify one section"):
        convert_field_location(ambiguous, "A", "B", 100.0, 200.0)


def test_the_request_model_rejects_malformed_field_locations():
    for bad in (
        dict(offset_start_m=-1, offset_end_m=10),
        dict(offset_start_m=10, offset_end_m=10),
        dict(offset_start_m=10, offset_end_m=5),
        dict(offset_start_m=0, offset_end_m=math.inf),
        dict(offset_start_m=math.nan, offset_end_m=10),
        dict(offset_start_m=0, offset_end_m=10, extra_field="x"),
    ):
        with pytest.raises(ValidationError):
            FieldLocation(from_station_id="A", toward_station_id="B", **bad)

    with pytest.raises(ValidationError):
        FieldLocation(
            from_station_id="",
            toward_station_id="B",
            offset_start_m=0,
            offset_end_m=10,
        )


# ----------------------------------------------------------------------
# Through create_job: the existing resolver stays the only authority
# ----------------------------------------------------------------------


def _assets():
    return [make_asset("AST-T1-OHE-001", 1.2), make_asset("AST-T1-OHE-002", 50.6)]


def test_a_field_location_matches_the_equivalent_absolute_submission(tmp_path):
    with_field = make_service(tmp_path, _assets(), db_name="a.db").create_job(
        make_request(
            50_400.0,
            50_800.0,
            field_location=fl("B", "C", 400.0, 800.0),
        ),
        actor=WORKER,
    )
    absolute_only = make_service(tmp_path, _assets(), db_name="b.db").create_job(
        make_request(50_400.0, 50_800.0),
        actor=WORKER,
    )

    for key in ("track_id", "distance_start", "distance_end", "work_type"):
        assert with_field[key] == absolute_only[key]

    assert (
        with_field["block_candidate"]["section_id"]
        == absolute_only["block_candidate"]["section_id"]
        == "B-C"
    )
    assert with_field["block_candidate"]["track_id"] == absolute_only["block_candidate"]["track_id"]
    assert with_field["priority_score"] == absolute_only["priority_score"]
    assert with_field["risk_score"] == absolute_only["risk_score"]

    assert with_field["block_candidate"]["metadata"]["location_source"] == "FIELD_LOCATION_VERIFIED"
    assert absolute_only["block_candidate"]["metadata"]["location_source"] == "ABSOLUTE_CHAINAGE"
    assert absolute_only["block_candidate"]["metadata"]["field_location"] is None


def test_the_field_location_is_recorded_in_job_created(tmp_path):
    service = make_service(tmp_path, _assets())

    job = service.create_job(
        make_request(48_600.0, 49_000.0, field_location=fl("B", "A", 1_000.0, 1_400.0)),
        actor=WORKER,
    )

    metadata = created_event(service, job["job_id"]).metadata

    assert metadata["location_source"] == "FIELD_LOCATION_VERIFIED"
    assert metadata["field_location"] == {
        "section_id": "A-B",
        "from_station_id": "B",
        "toward_station_id": "A",
        "offset_start_m": 1_000.0,
        "offset_end_m": 1_400.0,
        "derived_distance_start_m": 48_600.0,
        "derived_distance_end_m": 49_000.0,
    }
    # Only corridor-absolute metres are the stored location.
    assert job["distance_start"] == 48_600.0
    assert job["distance_end"] == 49_000.0


def test_a_field_location_that_disagrees_with_the_distances_is_refused(tmp_path):
    service = make_service(tmp_path, _assets())

    with pytest.raises(LocationInputConflictError, match="Neither is preferred"):
        service.create_job(
            make_request(
                2_000.0,
                2_400.0,
                field_location=fl("A", "B", 1_000.0, 1_400.0),
            ),
            actor=WORKER,
        )

    assert job_count(service) == 0


def test_a_field_location_naming_a_different_span_is_refused(tmp_path):
    """400-800 m from A toward B is 0.4-0.8 km; the declared distances are
    50.4-50.8 km (section B-C). Different spans: refused."""

    service = make_service(tmp_path, _assets())

    with pytest.raises(LocationInputConflictError):
        service.create_job(
            make_request(50_400.0, 50_800.0, field_location=fl("A", "B", 400.0, 800.0)),
            actor=WORKER,
        )

    assert job_count(service) == 0


def test_same_metres_in_a_different_section_is_refused(tmp_path):
    """Two sections cover the same chainage on different tracks. The
    metres agree, but the field location names the section serving T2
    while the job is on T1: refused, not reconciled."""

    registry = SectionRegistry(
        CORRIDOR_ID,
        [
            Section("P", CORRIDOR_ID, "A", "B", 0.0, 50.0, track_ids=("T1",)),
            Section("Q", CORRIDOR_ID, "A", "D", 0.0, 50.0, track_ids=("T2",)),
        ],
    )
    service = make_service(
        tmp_path,
        [make_asset("AST-T1-OHE-001", 1.2)],
        track_ids=("T1", "T2"),
        registry=registry,
    )

    with pytest.raises(FieldLocationError, match="is in section 'Q'"):
        service.create_job(
            make_request(1_000.0, 1_400.0, track_id="T1", field_location=fl("A", "D", 1_000.0, 1_400.0)),
            actor=WORKER,
        )

    assert job_count(service) == 0

    # The consistent spelling of the same job is accepted.
    job = service.create_job(
        make_request(1_000.0, 1_400.0, track_id="T1", field_location=fl("A", "B", 1_000.0, 1_400.0)),
        actor=WORKER,
    )
    assert job["block_candidate"]["section_id"] == "P"


def test_a_span_ending_on_the_far_station_is_refused_by_the_existing_resolver(
    tmp_path,
):
    """Half-open [km_start, km_end): the station belongs to the NEXT
    section, so the resolver refuses a span ending exactly on it - the
    same as an absolute-metre request would be. Inherited, not re-decided."""

    service = make_service(tmp_path, _assets())

    with pytest.raises(ValueError, match="crosses a section boundary"):
        service.create_job(
            make_request(49_000.0, 50_000.0, field_location=fl("A", "B", 49_000.0, 50_000.0)),
            actor=WORKER,
        )

    assert job_count(service) == 0


def test_a_track_that_does_not_serve_the_section_is_refused_by_the_resolver(
    tmp_path,
):
    """The field location is valid; the track is not one of the section's
    running lines. The existing resolver, unchanged, refuses it."""

    service = make_service(
        tmp_path,
        [
            make_asset("AST-T1-OHE-001", 1.2),
            make_asset("AST-T2-OHE-001", 1.2, track_id="T2"),
        ],
        track_ids=("T1", "T2"),
        registry=make_registry(("T1",)),  # T2 exists but serves no section
    )

    with pytest.raises(ValueError, match="none of which list"):
        service.create_job(
            make_request(
                1_000.0, 1_400.0, track_id="T2", field_location=fl("A", "B", 1_000.0, 1_400.0)
            ),
            actor=WORKER,
        )


def test_unknown_stations_through_create_job_are_field_location_errors(tmp_path):
    service = make_service(tmp_path, _assets())

    with pytest.raises(FieldLocationError, match="No section joins"):
        service.create_job(
            make_request(1_000.0, 1_400.0, field_location=fl("A", "NOPE", 1_000.0, 1_400.0)),
            actor=WORKER,
        )

    assert job_count(service) == 0


def test_no_field_location_leaves_absolute_intake_unchanged(tmp_path):
    service = make_service(tmp_path, _assets())

    job = service.create_job(make_request(1_000.0, 1_400.0), actor=WORKER)

    assert job["block_candidate"]["section_id"] == "A-B"
    assert job["block_candidate"]["metadata"]["location_source"] == "ABSOLUTE_CHAINAGE"


# ----------------------------------------------------------------------
# API contract impact
# ----------------------------------------------------------------------

client = TestClient(app)
WORKER_HEADERS = {"X-Actor-Id": "WORKER-042", "X-Actor-Role": "WORKER"}


@pytest.fixture(autouse=True)
def clean_jobs_table():
    def wipe():
        with closing(sqlite3.connect(router_service.repository.db_path)) as conn, conn:
            conn.execute("DELETE FROM maintenance_jobs")
            conn.commit()

    wipe()
    yield
    wipe()


def _body(**extra):
    return {
        "track_id": "UP-1",
        "job_type": "BALLAST_TAMPING",
        "distance_start": 1000.0,
        "distance_end": 1400.0,
        "workers_min": 2,
        "workers_max": 4,
        "description": "field-reported defect",
        **extra,
    }


def test_the_field_location_fields_are_optional_in_the_v1_request():
    schema = JobCreateRequest.model_json_schema()

    assert sorted(schema["required"]) == [
        "description",
        "distance_end",
        "distance_start",
        "job_type",
        "track_id",
        "workers_max",
        "workers_min",
    ]
    assert "field_location" in schema["properties"]
    assert "idempotency_key" in schema["properties"]


def test_a_pre_slice8_request_body_is_still_accepted():
    response = client.post("/v1/jobs", json=_body(), headers=WORKER_HEADERS)

    assert response.status_code == 201, response.text


def test_http_accepts_a_matching_field_location():
    stations = router_service.registry.get(router_service.registry.section_ids()[0])

    response = client.post(
        "/v1/jobs",
        json=_body(
            field_location={
                "from_station_id": stations.start_station_id,
                "toward_station_id": stations.end_station_id,
                "offset_start_m": 1000.0,
                "offset_end_m": 1400.0,
            }
        ),
        headers=WORKER_HEADERS,
    )

    assert response.status_code == 201, response.text
    assert (
        response.json()["block_candidate"]["metadata"]["location_source"]
        == "FIELD_LOCATION_VERIFIED"
    )


def test_http_unknown_station_is_400_not_a_404_job_not_found():
    """SectionRegistry raises UnknownSectionError, a KeyError. Left alone
    the router would answer 404 JOB_NOT_FOUND for a bad station."""

    response = client.post(
        "/v1/jobs",
        json=_body(
            field_location={
                "from_station_id": "NOPE",
                "toward_station_id": "NADA",
                "offset_start_m": 1000.0,
                "offset_end_m": 1400.0,
            }
        ),
        headers=WORKER_HEADERS,
    )

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "FIELD_LOCATION_INVALID"


def test_http_conflicting_locations_are_400_location_input_conflict():
    section = router_service.registry.get(router_service.registry.section_ids()[0])

    response = client.post(
        "/v1/jobs",
        json=_body(
            field_location={
                "from_station_id": section.start_station_id,
                "toward_station_id": section.end_station_id,
                "offset_start_m": 3000.0,
                "offset_end_m": 3400.0,
            }
        ),
        headers=WORKER_HEADERS,
    )

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "LOCATION_INPUT_CONFLICT"


def test_http_negative_offset_is_a_422_validation_error():
    response = client.post(
        "/v1/jobs",
        json=_body(
            field_location={
                "from_station_id": "A",
                "toward_station_id": "B",
                "offset_start_m": -5.0,
                "offset_end_m": 10.0,
            }
        ),
        headers=WORKER_HEADERS,
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
