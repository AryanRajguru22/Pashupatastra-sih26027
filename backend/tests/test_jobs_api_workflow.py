"""Sprint 2 jobs workflow over the real HTTP API.

Exercises the endpoints an authority actually uses: report, review the
queue, retrieve one job, run optimization, notify, complete - including
the lifecycle guards that must reject illegal transitions.

The app's router builds its JobService at import time against
PASHUPAT_JOBS_DB, which backend/tests/conftest.py points at a temporary
file, so these tests never touch the operational database.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.api.main import app
from backend.app.jobs.router import service as router_service


client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_jobs_table():
    """Each test starts from an empty table in the temporary test DB."""

    import sqlite3
    from contextlib import closing

    def wipe():
        with closing(
            sqlite3.connect(router_service.repository.db_path)
        ) as conn, conn:
            conn.execute("DELETE FROM maintenance_jobs")
            conn.commit()

    wipe()
    yield
    wipe()


def payload(
    track_id: str = "UP-1",
    job_type: str = "BALLAST_TAMPING",
    distance_start: float = 1000.0,
) -> dict:
    return {
        "track_id": track_id,
        "job_type": job_type,
        "distance_start": distance_start,
        "distance_end": distance_start + 400,
        "workers_min": 2,
        "workers_max": 4,
        "description": "Field-reported defect",
    }


def create(**kwargs) -> dict:
    response = client.post("/jobs", json=payload(**kwargs))
    assert response.status_code == 201, response.text
    return response.json()


# ----------------------------------------------------------------------
# Reporting and validation
# ----------------------------------------------------------------------


def test_report_job_returns_201_and_persists():
    job = create()

    assert job["status"] == "reported"
    assert job["updated_at"]
    assert job["last_solver_status"] is None
    assert job["last_refusal_reason"] is None


def test_unknown_track_returns_400():
    response = client.post("/jobs", json=payload(track_id="NOPE"))

    assert response.status_code == 400
    assert "Unknown track_id" in response.json()["detail"]


def test_malformed_payload_returns_422():
    bad = payload()
    bad["distance_end"] = bad["distance_start"] - 1

    assert client.post("/jobs", json=bad).status_code == 422

    unknown_field = payload()
    unknown_field["not_a_field"] = 1

    assert client.post("/jobs", json=unknown_field).status_code == 422


# ----------------------------------------------------------------------
# Authority retrieval
# ----------------------------------------------------------------------


def test_list_jobs_and_filter_by_status():
    create()
    create(track_id="DOWN-1", distance_start=2000)

    assert len(client.get("/jobs").json()) == 2

    reported = client.get("/jobs", params={"status": "reported"})
    assert reported.status_code == 200
    assert len(reported.json()) == 2

    scheduled = client.get("/jobs", params={"status": "scheduled"})
    assert scheduled.json() == []


def test_invalid_status_filter_is_rejected():
    assert (
        client.get("/jobs", params={"status": "not-a-status"}).status_code
        == 422
    )


def test_get_single_job():
    job = create()

    found = client.get(f"/jobs/{job['job_id']}")
    assert found.status_code == 200
    assert found.json()["job_id"] == job["job_id"]

    assert client.get("/jobs/JOB-DOES-NOT-EXIST").status_code == 404


# ----------------------------------------------------------------------
# Optimization endpoint
# ----------------------------------------------------------------------


def test_optimize_corridor_schedules_reported_jobs():
    job = create()

    response = client.post("/corridors/CORRIDOR_A/optimize-jobs")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["corridor_id"] == "CORRIDOR_A"
    assert body["solver_status"] in {"OPTIMAL", "FEASIBLE"}
    assert body["possession_source"] == "GENERATED_STATIC"
    assert body["possession_window_count"] > 0
    assert body["counts"]["considered"] == 1
    assert body["counts"]["scheduled"] == 1

    reloaded = client.get(f"/jobs/{job['job_id']}").json()
    assert reloaded["status"] == "scheduled"
    assert reloaded["schedule_start_minute"] is not None
    assert reloaded["last_solver_status"] == body["solver_status"]


def test_optimize_with_no_active_jobs_returns_409():
    response = client.post("/corridors/CORRIDOR_A/optimize-jobs")

    assert response.status_code == 409
    assert "No active maintenance jobs" in response.json()["detail"]


def test_optimize_unknown_corridor_returns_404():
    create()

    response = client.post("/corridors/CORRIDOR_ZZZ/optimize-jobs")
    assert response.status_code == 404


def test_optimize_response_reports_partial_success_honestly():
    for i in range(8):
        create(job_type="TRACK_RENEWAL", distance_start=1000 + 400 * i)

    body = client.post("/corridors/CORRIDOR_A/optimize-jobs").json()

    assert body["counts"]["scheduled"] > 0
    assert body["counts"]["unscheduled"] > 0
    assert len(body["unscheduled"]) == body["counts"]["unscheduled"]

    for refusal in body["unscheduled"]:
        assert refusal["reason"]
        reloaded = client.get(f"/jobs/{refusal['job_id']}").json()
        assert reloaded["status"] == "reported"
        assert reloaded["last_refusal_reason"] == refusal["reason"]


# ----------------------------------------------------------------------
# Lifecycle transitions
# ----------------------------------------------------------------------


def test_full_lifecycle_report_optimize_notify_complete():
    job = create()

    client.post("/corridors/CORRIDOR_A/optimize-jobs")

    notified = client.post(f"/jobs/{job['job_id']}/notify")
    assert notified.status_code == 200
    assert notified.json()["job"]["status"] == "notified"

    completed = client.post(f"/jobs/{job['job_id']}/complete")
    assert completed.status_code == 200
    assert completed.json()["job"]["status"] == "completed"


def test_illegal_transitions_are_rejected():
    job = create()

    # reported -> notified is illegal (must be scheduled first)
    assert (
        client.post(f"/jobs/{job['job_id']}/notify").status_code == 400
    )

    # reported -> completed is illegal
    assert (
        client.post(f"/jobs/{job['job_id']}/complete").status_code == 400
    )

    client.post("/corridors/CORRIDOR_A/optimize-jobs")

    # scheduled -> completed is illegal (must be notified first)
    assert (
        client.post(f"/jobs/{job['job_id']}/complete").status_code == 400
    )


def test_transitions_on_missing_job_return_404():
    assert client.post("/jobs/NOPE/notify").status_code == 404
    assert client.post("/jobs/NOPE/complete").status_code == 404


def test_completed_job_is_excluded_from_later_optimization():
    done = create()
    client.post("/corridors/CORRIDOR_A/optimize-jobs")
    client.post(f"/jobs/{done['job_id']}/notify")
    client.post(f"/jobs/{done['job_id']}/complete")

    frozen = client.get(f"/jobs/{done['job_id']}").json()

    create(track_id="DOWN-1", distance_start=2000)
    body = client.post("/corridors/CORRIDOR_A/optimize-jobs").json()

    touched = {s["job_id"] for s in body["scheduled"]}
    touched |= {u["job_id"] for u in body["unscheduled"]}
    assert done["job_id"] not in touched

    after = client.get(f"/jobs/{done['job_id']}").json()
    assert after["status"] == "completed"
    assert after["schedule_start_minute"] == frozen["schedule_start_minute"]


def test_notified_work_survives_a_later_optimization_over_http():
    job = create(job_type="TRACK_RENEWAL")
    client.post("/corridors/CORRIDOR_A/optimize-jobs")
    client.post(f"/jobs/{job['job_id']}/notify")

    pinned = client.get(f"/jobs/{job['job_id']}").json()
    original = (
        pinned["schedule_start_minute"],
        pinned["schedule_end_minute"],
    )

    for i in range(6):
        create(job_type="TRACK_RENEWAL", distance_start=4000 + 400 * i)

    body = client.post("/corridors/CORRIDOR_A/optimize-jobs").json()

    kept = [s for s in body["scheduled"] if s["job_id"] == job["job_id"]]
    assert kept, "notified work must not be dropped"
    assert (kept[0]["start_minute"], kept[0]["end_minute"]) == original
    assert kept[0]["is_committed"] is True

    after = client.get(f"/jobs/{job['job_id']}").json()
    assert after["status"] == "notified"
