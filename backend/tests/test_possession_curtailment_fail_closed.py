"""Curtailing a possession must never lift possession protection.

The solver treats an empty possession_windows list as "this request does
not model possessions", which is correct for a request that never had
any. But POSSESSION_CURTAILMENT removes windows, so a wide enough
curtailment could empty the list and thereby switch protection OFF - the
exact opposite of what curtailing a possession means operationally.

apply_possession_curtailment now refuses that case. The /recover router
already maps ValueError to HTTP 400, so the API reports it honestly
instead of returning an unprotected plan.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.api.main import app
from backend.app.optimizer.solver import solve
from backend.app.simulation.disruptions import apply_disruption
from contracts import (
    BlockCandidate,
    DisruptionEvent,
    DisruptionType,
    OptimizationRequest,
    PossessionWindow,
)


client = TestClient(app)


def make_request() -> OptimizationRequest:
    return OptimizationRequest(
        corridor_id="TEST_CORRIDOR",
        horizon_minutes=1440,
        tracks=["UP-1", "DOWN-1"],
        candidates=[
            BlockCandidate(
                block_id="B1",
                asset_id="A1",
                track_id="UP-1",
                work_type="TRACK_RENEWAL",
                duration_minutes=60,
                earliest_start_minute=0,
                latest_end_minute=1440,
                priority_score=1.0,
                risk_score=1.0,
            ),
            BlockCandidate(
                block_id="B2",
                asset_id="A2",
                track_id="DOWN-1",
                work_type="TRACK_RENEWAL",
                duration_minutes=60,
                earliest_start_minute=0,
                latest_end_minute=1440,
                priority_score=1.0,
                risk_score=1.0,
            ),
        ],
        possession_windows=[
            PossessionWindow(
                window_id="POS-UP",
                track_id="UP-1",
                start_minute=0,
                end_minute=300,
            ),
            PossessionWindow(
                window_id="POS-DOWN",
                track_id="DOWN-1",
                start_minute=0,
                end_minute=300,
            ),
        ],
    )


def curtailment(
    track_id: str | None,
    start_minute: int,
    end_minute: int,
) -> DisruptionEvent:
    return DisruptionEvent(
        disruption_id="DIS-CURTAIL",
        disruption_type=DisruptionType.POSSESSION_CURTAILMENT.value,
        corridor_id="TEST_CORRIDOR",
        track_id=track_id,
        start_minute=start_minute,
        end_minute=end_minute,
        description="Possession curtailed.",
    )


def test_partial_curtailment_still_works():
    """Curtailing one track's window is normal and must keep working."""

    request = make_request()

    updated = apply_disruption(
        request,
        curtailment("UP-1", 0, 300),
    )

    remaining = {
        window.window_id for window in updated.possession_windows
    }

    assert remaining == {"POS-DOWN"}


def test_partial_curtailment_refuses_work_on_the_curtailed_track():
    """After curtailment the affected track has no window, so work on
    it must be refused rather than scheduled unprotected."""

    request = make_request()

    updated = apply_disruption(
        request,
        curtailment("UP-1", 0, 300),
    )

    result = solve(updated)
    scheduled = {block.block_id for block in result.scheduled_blocks}

    assert "B2" in scheduled
    assert "B1" not in scheduled
    assert "refused for safety" in result.rejection_reasons["B1"]


def test_curtailing_every_window_is_refused():
    """The fail-open hole: emptying the window list would have turned
    possession protection off entirely."""

    request = make_request()

    with pytest.raises(ValueError, match="every possession window"):
        apply_disruption(
            request,
            curtailment(None, 0, 1440),
        )


def test_curtailing_every_window_returns_400_not_an_unprotected_plan():
    """End-to-end through the real API."""

    body = {
        "request": make_request().to_dict(),
        "disruption": curtailment(None, 0, 1440).to_dict(),
    }

    response = client.post("/recover", json=body)

    assert response.status_code == 400
    assert "possession window" in response.json()["detail"]


def test_curtailment_on_a_request_without_windows_is_still_allowed():
    """A request that never modelled possessions is unaffected."""

    request = make_request()
    request.possession_windows = []

    updated = apply_disruption(
        request,
        curtailment(None, 0, 1440),
    )

    assert updated.possession_windows == []
