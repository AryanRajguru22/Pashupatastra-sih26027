"""API tests for the Phase 1 FastAPI backend."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.api.main import app
from contracts import SolverStatus


FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "app"
    / "data"
    / "fixtures"
    / "corridor_a_blocks.json"
)


client = TestClient(app)


def _fixture_payload() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


def test_health_returns_healthy_status():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_optimize_valid_request_returns_200():
    response = client.post(
        "/optimize",
        json=_fixture_payload(),
    )

    assert response.status_code == 200


def test_optimize_result_structure_matches_contract():
    response = client.post(
        "/optimize",
        json=_fixture_payload(),
    )

    body = response.json()

    assert body["corridor_id"] == "CORRIDOR_A"
    assert body["status"] in [status.value for status in SolverStatus]

    assert "scheduled_blocks" in body
    assert "unscheduled_blocks" in body
    assert "total_priority_scheduled" in body
    assert "total_risk_mitigated" in body
    assert "solve_time_seconds" in body
    assert "infeasibility_reasons" in body
    assert "rejection_reasons" in body


def test_optimize_propagates_solver_status_and_counts():
    response = client.post(
        "/optimize",
        json=_fixture_payload(),
    )

    body = response.json()

    assert body["status"] in (
        SolverStatus.OPTIMAL.value,
        SolverStatus.FEASIBLE.value,
    )

    total_blocks = (
        len(body["scheduled_blocks"])
        + len(body["unscheduled_blocks"])
    )

    assert total_blocks == len(
        _fixture_payload()["candidates"]
    )

    assert (
        body["total_priority_scheduled"]
        >= 0
    )

    assert (
        body["total_risk_mitigated"]
        >= 0
    )

    for scheduled_block in body["scheduled_blocks"]:
        assert {
            "block_id",
            "track_id",
            "start_minute",
            "end_minute",
            "work_type",
            "priority_score",
            "risk_score",
            "is_committed",
            "status",
        } <= scheduled_block.keys()


def test_optimize_invalid_request_returns_422():
    response = client.post(
        "/optimize",
        json={
            "corridor_id": "CORRIDOR_A"
        },
    )

    assert response.status_code == 422