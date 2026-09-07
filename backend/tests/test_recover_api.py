"""API tests for the disruption recovery endpoint (POST /recover).

Mirrors the request/event construction style already used in
backend/tests/test_simulation.py, but exercises the endpoint over HTTP
via TestClient rather than calling apply_disruption()/simulate_disruption()
directly.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.api.main import app
from contracts import BlockCandidate, DisruptionType, SolverStatus

client = TestClient(app)


def make_request_payload() -> dict:
    """A small deterministic OptimizationRequest body for recovery tests."""

    return {
        "corridor_id": "CORRIDOR_A",
        "horizon_minutes": 600,
        "tracks": ["UP", "DOWN"],
        "candidates": [
            {
                "block_id": "BLOCK_UP_1",
                "asset_id": "ASSET_UP",
                "track_id": "UP",
                "work_type": "ROUTINE_INSPECTION",
                "duration_minutes": 60,
                "earliest_start_minute": 0,
                "latest_end_minute": 600,
                "priority_score": 0.8,
                "risk_score": 0.7,
            },
            {
                "block_id": "BLOCK_DOWN_1",
                "asset_id": "ASSET_DOWN",
                "track_id": "DOWN",
                "work_type": "EMERGENCY_REPAIR",
                "duration_minutes": 60,
                "earliest_start_minute": 0,
                "latest_end_minute": 600,
                "priority_score": 0.6,
                "risk_score": 0.5,
            },
        ],
        "possession_windows": [
            {
                "window_id": "POS-001",
                "track_id": "UP",
                "start_minute": 0,
                "end_minute": 600,
            },
            {
                "window_id": "POS-002",
                "track_id": "DOWN",
                "start_minute": 0,
                "end_minute": 600,
            },
        ],
        "existing_committed_blocks": [],
        "min_headway_minutes": 10,
    }


def make_event_payload(
    disruption_id: str,
    disruption_type: str,
    *,
    track_id: str | None = None,
    affected_asset_id: str | None = None,
    new_candidate: dict | None = None,
    start_minute: int = 0,
    end_minute: int = 600,
    description: str = "",
) -> dict:
    return {
        "disruption_id": disruption_id,
        "disruption_type": disruption_type,
        "corridor_id": "CORRIDOR_A",
        "track_id": track_id,
        "start_minute": start_minute,
        "end_minute": end_minute,
        "affected_asset_id": affected_asset_id,
        "new_candidate": new_candidate,
        "description": description,
    }


def test_recover_asset_breakdown_returns_200_and_recovers():
    payload = {
        "request": make_request_payload(),
        "disruption": make_event_payload(
            "DISR-AB-001",
            DisruptionType.ASSET_BREAKDOWN.value,
            affected_asset_id="ASSET_UP",
            description="Asset ASSET_UP becomes unavailable.",
        ),
    }

    response = client.post("/recover", json=payload)
    assert response.status_code == 200

    body = response.json()
    assert body["disruption"]["disruption_id"] == "DISR-AB-001"

    updated_ids = {c["block_id"] for c in body["updated_request"]["candidates"]}
    assert "BLOCK_UP_1" not in updated_ids
    assert "BLOCK_DOWN_1" in updated_ids

    recovery_result = body["recovery_result"]
    assert recovery_result["status"] in [s.value for s in SolverStatus]
    assert all(
        b["block_id"] != "BLOCK_UP_1"
        for b in recovery_result["scheduled_blocks"]
    )


def test_recover_track_unavailable_returns_200_and_recovers():
    payload = {
        "request": make_request_payload(),
        "disruption": make_event_payload(
            "DISR-TU-001",
            DisruptionType.TRACK_UNAVAILABLE.value,
            track_id="UP",
            description="UP track becomes unavailable.",
        ),
    }

    response = client.post("/recover", json=payload)
    assert response.status_code == 200

    body = response.json()
    updated_ids = {c["block_id"] for c in body["updated_request"]["candidates"]}
    assert "BLOCK_UP_1" not in updated_ids
    assert "BLOCK_DOWN_1" in updated_ids


def test_recover_emergency_work_returns_200_and_adds_candidate():
    emergency_candidate = BlockCandidate(
        block_id="EMERGENCY_001",
        asset_id="ASSET_EMERGENCY",
        track_id="UP",
        work_type="EMERGENCY_REPAIR",
        duration_minutes=30,
        earliest_start_minute=100,
        latest_end_minute=200,
        priority_score=1.0,
        risk_score=1.0,
    ).to_dict()

    payload = {
        "request": make_request_payload(),
        "disruption": make_event_payload(
            "DISR-EW-001",
            DisruptionType.EMERGENCY_WORK.value,
            new_candidate=emergency_candidate,
            description="Emergency maintenance required.",
        ),
    }

    response = client.post("/recover", json=payload)
    assert response.status_code == 200

    body = response.json()
    updated_ids = {c["block_id"] for c in body["updated_request"]["candidates"]}
    assert "EMERGENCY_001" in updated_ids


def test_recover_possession_curtailment_returns_200_and_removes_window():
    payload = {
        "request": make_request_payload(),
        "disruption": make_event_payload(
            "DISR-PC-001",
            DisruptionType.POSSESSION_CURTAILMENT.value,
            track_id="UP",
            start_minute=150,
            end_minute=200,
            description="UP possession window is curtailed.",
        ),
    }

    response = client.post("/recover", json=payload)
    assert response.status_code == 200

    body = response.json()
    remaining_window_ids = {
        w["window_id"] for w in body["updated_request"]["possession_windows"]
    }
    assert "POS-001" not in remaining_window_ids
    assert "POS-002" in remaining_window_ids


def test_recover_unsupported_disruption_type_returns_400():
    payload = {
        "request": make_request_payload(),
        "disruption": make_event_payload(
            "DISR-BAD-001",
            "ASSET_CONDITION_DETERIORATION",
            description="Not a supported disruption type.",
        ),
    }

    response = client.post("/recover", json=payload)
    assert response.status_code == 400
    assert "ASSET_CONDITION_DETERIORATION" in response.json()["detail"]


def test_recover_malformed_request_returns_422():
    response = client.post(
        "/recover",
        json={"request": {"corridor_id": "CORRIDOR_A"}, "disruption": {}},
    )
    assert response.status_code == 422


def test_recover_missing_disruption_returns_422():
    response = client.post(
        "/recover",
        json={"request": make_request_payload()},
    )
    assert response.status_code == 422


def test_optimize_still_works_after_recover_router_added():
    """Adding the /recover router must not change /optimize behavior."""

    response = client.post("/optimize", json=make_request_payload())
    assert response.status_code == 200

    body = response.json()
    assert body["corridor_id"] == "CORRIDOR_A"
    assert body["status"] in [s.value for s in SolverStatus]
