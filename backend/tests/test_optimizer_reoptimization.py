from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


from backend.app.optimizer.solver import solve
from contracts import OptimizationRequest, OptimizationStatus, ScheduledBlock


FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "app"
    / "data"
    / "fixtures"
    / "corridor_a_blocks.json"
)


def load_request() -> OptimizationRequest:
    raw = json.loads(FIXTURE_PATH.read_text())
    return OptimizationRequest.model_validate(raw)


def test_committed_block_remains_at_its_existing_slot():
    request = load_request()

    request.existing_committed_blocks = [
        ScheduledBlock(
            block_id="BLK-001",
            track_id="UP",
            start=datetime.fromisoformat("2026-09-01T01:00:00"),
            end=datetime.fromisoformat("2026-09-01T04:00:00"),
        )
    ]

    # Verify committed track matches the candidate track.
    candidate = next(
        block
        for block in request.block_candidates
        if block.block_id == "BLK-001"
    )

    committed_input = request.existing_committed_blocks[0]

    assert committed_input.track_id == candidate.track_id

    result = solve(request)

    assert result.status in (
        OptimizationStatus.OPTIMAL,
        OptimizationStatus.FEASIBLE,
    )

    scheduled = {
        block.block_id: block
        for block in result.scheduled_blocks
    }

    assert "BLK-001" in scheduled

    committed = scheduled["BLK-001"]

    assert committed.track_id == candidate.track_id

    assert committed.start == datetime.fromisoformat(
        "2026-09-01T01:00:00"
    )

    assert committed.end == datetime.fromisoformat(
        "2026-09-01T04:00:00"
    )

def test_non_committed_block_can_move_around_committed_block():
    request = load_request()

    blocks_by_id = {
        block.block_id: block
        for block in request.block_candidates
    }

    # Use a small controlled scenario.
    request.block_candidates = [
        blocks_by_id["BLK-003"],
        blocks_by_id["BLK-012"],
    ]

    # Commit BLK-003 to a valid position on the UP track.
    request.existing_committed_blocks = [
        ScheduledBlock(
            block_id="BLK-003",
            track_id="UP",
            start=datetime.fromisoformat(
                "2026-09-01T01:00:00"
            ),
            end=datetime.fromisoformat(
                "2026-09-01T03:00:00"
            ),
        )
    ]

    result = solve(request)

    assert result.status in (
        OptimizationStatus.OPTIMAL,
        OptimizationStatus.FEASIBLE,
    )

    scheduled = {
        block.block_id: block
        for block in result.scheduled_blocks
    }

    # The committed block must remain exactly where it was committed.
    assert "BLK-003" in scheduled

    committed = scheduled["BLK-003"]

    assert committed.track_id == "UP"
    assert committed.start == datetime.fromisoformat(
        "2026-09-01T01:00:00"
    )
    assert committed.end == datetime.fromisoformat(
        "2026-09-01T03:00:00"
    )

    # BLK-012 is NOT committed.
    # CP-SAT should still be able to place it around BLK-003.
    assert "BLK-012" in scheduled

    other = scheduled["BLK-012"]

    assert other.track_id == "UP"

    # BLK-012 cannot overlap the committed BLK-003.
    # The configured headway is 10 minutes.
    assert other.start >= datetime.fromisoformat(
        "2026-09-01T03:10:00"
    )

    assert other.end <= datetime.fromisoformat(
        "2026-09-01T05:00:00"
    )

def test_committed_block_still_respects_track_no_overlap():
    request = load_request()

    blocks_by_id = {
        block.block_id: block
        for block in request.block_candidates
    }

    # Use two blocks on the same UP track.
    request.block_candidates = [
        blocks_by_id["BLK-003"],
        blocks_by_id["BLK-012"],
    ]

    # BLK-003 is committed from 01:00 to 03:00.
    request.existing_committed_blocks = [
        ScheduledBlock(
            block_id="BLK-003",
            track_id="UP",
            start=datetime.fromisoformat(
                "2026-09-01T01:00:00"
            ),
            end=datetime.fromisoformat(
                "2026-09-01T03:00:00"
            ),
        )
    ]

    result = solve(request)

    assert result.status in (
        OptimizationStatus.OPTIMAL,
        OptimizationStatus.FEASIBLE,
    )

    scheduled = {
        block.block_id: block
        for block in result.scheduled_blocks
    }

    assert "BLK-003" in scheduled
    assert "BLK-012" in scheduled

    committed = scheduled["BLK-003"]
    other = scheduled["BLK-012"]

    # Verify the committed block stayed fixed.
    assert committed.start == datetime.fromisoformat(
        "2026-09-01T01:00:00"
    )
    assert committed.end == datetime.fromisoformat(
        "2026-09-01T03:00:00"
    )

    # Both are on UP. Therefore BLK-012 must respect the
    # configured 10-minute headway after BLK-003.
    assert other.start >= datetime.fromisoformat(
        "2026-09-01T03:10:00"
    )

def test_non_committed_block_moves_when_old_slot_becomes_unavailable():
    request = load_request()

    blocks_by_id = {
        block.block_id: block
        for block in request.block_candidates
    }

    blk003 = blocks_by_id["BLK-003"]
    blk012 = blocks_by_id["BLK-012"]

    # Keep the scenario small and deterministic.
    request.block_candidates = [blk003, blk012]

    # BLK-003 is already committed at 01:00-03:00.
    request.existing_committed_blocks = [
        ScheduledBlock(
            block_id="BLK-003",
            track_id="UP",
            start=datetime.fromisoformat(
                "2026-09-01T01:00:00"
            ),
            end=datetime.fromisoformat(
                "2026-09-01T03:00:00"
            ),
        )
    ]

    # BLK-012 remains non-committed and is allowed to use the
    # remaining part of its window after the committed block.
    blk012.earliest_start = datetime.fromisoformat(
        "2026-09-01T00:00:00"
    )
    blk012.latest_finish = datetime.fromisoformat(
        "2026-09-01T05:00:00"
    )

    result = solve(request)

    assert result.status in (
        OptimizationStatus.OPTIMAL,
        OptimizationStatus.FEASIBLE,
    )

    scheduled = {
        block.block_id: block
        for block in result.scheduled_blocks
    }

    # Committed block must remain fixed.
    assert "BLK-003" in scheduled

    committed = scheduled["BLK-003"]

    assert committed.start == datetime.fromisoformat(
        "2026-09-01T01:00:00"
    )
    assert committed.end == datetime.fromisoformat(
        "2026-09-01T03:00:00"
    )
    assert committed.track_id == "UP"

    # The non-committed block must still be solver-controlled.
    assert "BLK-012" in scheduled

    other = scheduled["BLK-012"]

    # BLK-012 is on the same UP track and must respect the
    # 10-minute headway after the committed block.
    assert other.start >= datetime.fromisoformat(
        "2026-09-01T03:10:00"
    )

    assert other.end <= datetime.fromisoformat(
        "2026-09-01T05:00:00"
    )