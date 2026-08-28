"""API tests for the Phase 1 FastAPI backend.

These exercise the HTTP layer only - correctness of the optimizer
itself is covered by backend/tests/test_solver.py. The point of these
tests is to confirm the API faithfully wraps solve() (using the
existing contracts as-is) rather than reshaping or duplicating it.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.api.main import app
from contracts import OptimizationStatus

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
    response = client.post("/optimize", json=_fixture_payload())
    assert response.status_code == 200


def test_optimize_result_structure_matches_contract():
    response = client.post("/optimize", json=_fixture_payload())
    body = response.json()

    assert body["request_id"] == "REQ-MILESTONE-1"
    assert body["status"] in [s.value for s in OptimizationStatus]
    assert "scheduled_blocks" in body
    assert "unscheduled_blocks" in body
    assert "kpis" in body
    assert "explainability" in body
    assert "solve_time_ms" in body
    assert "generated_at" in body


def test_optimize_propagates_solver_status_and_counts():
    response = client.post("/optimize", json=_fixture_payload())
    body = response.json()

    # The fixture is a fixed, protected input - the exact schedule the
    # solver picks is its own business (covered by test_solver.py), but
    # the API must faithfully propagate whatever it returns.
    assert body["status"] in ("OPTIMAL", "FEASIBLE")
    total_blocks = len(body["scheduled_blocks"]) + len(body["unscheduled_blocks"])
    assert total_blocks == len(_fixture_payload()["block_candidates"])
    assert body["kpis"]["blocks_scheduled"] == len(body["scheduled_blocks"])
    for scheduled_block in body["scheduled_blocks"]:
        assert {"block_id", "track_id", "start", "end"} <= scheduled_block.keys()


def test_optimize_invalid_request_returns_422():
    response = client.post("/optimize", json={"request_id": "missing-required-fields"})
    assert response.status_code == 422
