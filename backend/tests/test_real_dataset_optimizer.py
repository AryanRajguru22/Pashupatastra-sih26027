import json

from backend.app.data.train_adapter import (
    generate_section_possession_windows_from_trains,
    qualified_track_for_job,
)
from contracts.schemas import BlockCandidate, OptimizationRequest
from backend.app.optimizer.solver import solve


def test_real_dataset_section_windows_reach_optimizer():
    with open(
        "pashupatastra_realistic_dataset.json",
        encoding="utf-8",
    ) as f:
        data = json.load(f)

    stations = data["corridor"]["stations"]

    possession_windows = (
        generate_section_possession_windows_from_trains(
            data["trains"],
            stations,
            horizon_minutes=1440,
            safety_buffer_minutes=10,
            minimum_window_minutes=20,
        )
    )

    candidates = []

    for job in data["maintenance_jobs"]:
        qualified_track = qualified_track_for_job(
            job,
            stations,
        )

        candidates.append(
            BlockCandidate(
                block_id=job["block_id"],
                asset_id=job["asset_id"],
                track_id=qualified_track,
                work_type=job["work_type"],
                duration_minutes=job["duration_minutes"],
                earliest_start_minute=job["earliest_start_minute"],
                latest_end_minute=job["latest_end_minute"],
                priority_score=job["metadata"]["scoring_features"][
                    "baseline_priority_score"
                ],
                risk_score=job["metadata"]["scoring_features"][
                    "baseline_risk_score"
                ],
                mutual_exclusion_group=job.get(
                    "mutual_exclusion_group"
                ),
                is_committed=job.get(
                    "is_committed",
                    False,
                ),
                status=job.get(
                    "status",
                    "PLANNED",
                ),
                metadata=job.get(
                    "metadata",
                    {},
                ),
            )
        )

    request = OptimizationRequest(
        corridor_id=data["corridor"]["corridor_id"],
        horizon_minutes=1440,
        tracks=sorted(
            {
                window.track_id
                for window in possession_windows
            }
        ),
        candidates=candidates,
        possession_windows=possession_windows,
        min_headway_minutes=15,
    )

    result = solve(request)

    assert result.status in {"OPTIMAL", "FEASIBLE"}

    for block in result.scheduled_blocks:
        matching = [
            window
            for window in possession_windows
            if window.track_id == block.track_id
            and window.start_minute <= block.start_minute
            and window.end_minute >= block.end_minute
        ]

        assert matching, (
            f"{block.block_id} scheduled outside "
            f"a valid possession window"
        )