"""Milestone 1: prove CP-SAT can solve the block-scheduling problem shape.

Loads the single-corridor synthetic fixture (~10-15 BlockCandidates),
solves it with the CP-SAT engine in backend/app/optimizer/solver.py, and
prints the resulting OptimizationResult. Asserts the hard constraints
(no two scheduled blocks overlap on the same track) actually hold -
this script is meant to be runnable with no FastAPI/frontend involved.

Run from the repo root:
    python scripts/run_milestone1.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from contracts import OptimizationRequest  # noqa: E402
from backend.app.optimizer.solver import solve  # noqa: E402

FIXTURE_PATH = REPO_ROOT / "backend" / "app" / "data" / "fixtures" / "corridor_a_blocks.json"


def _assert_no_overlaps(result) -> None:
    by_track: dict[str, list] = {}
    for sb in result.scheduled_blocks:
        by_track.setdefault(sb.track_id, []).append(sb)

    for track_id, blocks in by_track.items():
        blocks.sort(key=lambda b: b.start)
        for a, b in zip(blocks, blocks[1:]):
            assert a.end <= b.start, (
                f"Hard constraint violated: {a.block_id} and {b.block_id} "
                f"overlap on track {track_id}"
            )


def main() -> None:
    raw = json.loads(FIXTURE_PATH.read_text())
    request = OptimizationRequest.model_validate(raw)

    result = solve(request)

    print(f"status: {result.status.value}")
    print(f"solve_time_ms: {result.solve_time_ms}")
    print(f"scheduled: {len(result.scheduled_blocks)} / {len(request.block_candidates)} blocks")
    print()
    for sb in sorted(result.scheduled_blocks, key=lambda b: (b.track_id, b.start)):
        print(f"  [{sb.track_id}] {sb.block_id}: {sb.start.isoformat()} -> {sb.end.isoformat()}")
    print()
    for ub in result.unscheduled_blocks:
        print(f"  UNSCHEDULED {ub.block_id}: {ub.reason}")
    print()
    print(f"kpis: {result.kpis.model_dump()}")

    _assert_no_overlaps(result)
    print("\nOK: no hard-constraint violations detected.")

    out_path = REPO_ROOT / "scripts" / "milestone1_result.json"
    out_path.write_text(result.model_dump_json(indent=2))
    print(f"Full OptimizationResult written to {out_path}")


if __name__ == "__main__":
    main()
