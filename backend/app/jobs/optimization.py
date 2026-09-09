"""Bridge between persisted maintenance jobs and the existing CP-SAT solver.

This module is the only place where an OptimizationRequest is assembled
from internal state. Everything it produces is fed to the existing
backend.app.optimizer.solver.solve - there is no second optimizer, no
alternative scheduling logic and no solver bypass here.

The safety boundary it adds is deliberate and narrow:

    Sprint 1 made the solver fail CLOSED when a possession window
    exists but does not cover a block's track. It does NOT protect
    against possession windows being absent altogether - an empty
    possession_windows list means "this request does not model
    possessions", which switches possession control off completely and
    schedules every block unconstrained by train occupation.

    That is correct for the legacy fixture flow, which is why the
    solver keeps the behaviour. It is NOT acceptable for a jobs
    pipeline, so this assembly layer refuses to build a request at all
    when no possession data is available.

Per-track gaps are a different case and are deliberately allowed
through: the solver refuses those blocks individually with an explicit
safety reason, which is honest partial success rather than a reason to
abandon the whole batch.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from contracts import (
    BlockCandidate,
    OptimizationRequest,
    OptimizationResult,
    PossessionWindow,
)

from backend.app.jobs.service import (
    DEFAULT_MIN_HEADWAY_MINUTES,
    OPTIMIZATION_HORIZON_MINUTES,
    POSSESSION_SOURCE_GENERATED_STATIC,
    JobService,
)

from backend.app.optimizer.solver import (
    solve,
    uncovered_possession_tracks,
)


class PossessionDataUnavailableError(RuntimeError):
    """No possession data at all was available for the optimization.

    Distinct from "a track has no window", which the solver handles per
    block. This means the request could not be built safely, so nothing
    was solved and no schedule was produced.
    """


class NoEligibleJobsError(RuntimeError):
    """There is no active job to optimize."""


def build_jobs_optimization_request(
    corridor_id: str,
    tracks: list[str],
    candidates: list[BlockCandidate],
    possession_windows: list[PossessionWindow],
    committed: list[BlockCandidate] | None = None,
    horizon_minutes: int = OPTIMIZATION_HORIZON_MINUTES,
    min_headway_minutes: int = DEFAULT_MIN_HEADWAY_MINUTES,
) -> OptimizationRequest:
    """Assemble a canonical OptimizationRequest from persisted jobs.

    Pure: no I/O, no persistence, no solving. Raises rather than
    returning a request that would be solved without possession
    protection.
    """

    if not candidates:
        raise NoEligibleJobsError(
            "No active maintenance jobs to optimize."
        )

    # FAIL CLOSED on absent possession data. An empty list here would
    # reach the solver as "possession control inactive" and every job
    # would be scheduled with its train occupation never checked.
    if not possession_windows:
        raise PossessionDataUnavailableError(
            "No possession windows are available for corridor "
            f"'{corridor_id}'. Refusing to optimize: scheduling "
            "maintenance without possession data would place work on "
            "track whose train occupation has not been verified."
        )

    return OptimizationRequest(
        corridor_id=corridor_id,
        horizon_minutes=horizon_minutes,
        tracks=tracks,
        candidates=candidates,
        possession_windows=possession_windows,
        existing_committed_blocks=list(committed or []),
        min_headway_minutes=min_headway_minutes,
    )


def summarize_outcome(
    result: OptimizationResult,
    considered: list[BlockCandidate],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Map a solver result back onto the jobs that were considered.

    block_id IS job_id for jobs created through JobService, so the
    mapping is by identity and needs no join table.

    Reasons are taken verbatim from the solver's rejection_reasons and
    are never invented here. A job the solver did not explain falls
    back to the solver's own infeasibility reasons, or to a plainly
    labelled default - an unscheduled job is never reported as success.
    """

    scheduled_ids = {
        block.block_id for block in result.scheduled_blocks
    }

    scheduled = [
        {
            "job_id": block.block_id,
            "track_id": block.track_id,
            "start_minute": block.start_minute,
            "end_minute": block.end_minute,
            "is_committed": block.is_committed,
        }
        for block in result.scheduled_blocks
    ]

    fallback = (
        "; ".join(result.infeasibility_reasons)
        if result.infeasibility_reasons
        else "not scheduled by the optimizer (no reason reported)"
    )

    refused = [
        {
            "job_id": block.block_id,
            "track_id": block.track_id,
            "reason": result.rejection_reasons.get(
                block.block_id,
                fallback,
            ),
        }
        for block in considered
        if block.block_id not in scheduled_ids
    ]

    return scheduled, refused


class JobOptimizationService:
    """Runs the real solver over persisted jobs and records the outcome.

    CONCURRENCY LIMITATION (honest statement of scope):
    the lock below is an in-process threading.Lock. It serializes
    concurrent optimize calls inside ONE application process, which is
    the current deployment shape - a single uvicorn process backed by a
    single SQLite file. It provides NO protection across multiple
    processes, workers or hosts. Multi-instance safety needs a database
    or external lock and is deliberately out of Sprint 2 scope.
    """

    def __init__(self, service: JobService | None = None):
        self.service = service or JobService()
        self._lock = threading.Lock()

    def optimize_corridor(
        self,
        corridor_id: str | None = None,
    ) -> dict[str, Any]:

        target = corridor_id or self.service.corridor.corridor_id

        if target != self.service.corridor.corridor_id:
            raise KeyError(
                f"Unknown corridor '{target}'. This deployment serves "
                f"corridor '{self.service.corridor.corridor_id}'."
            )

        # Single-flight: two authorities pressing "optimize" at the
        # same moment must not read the same active set, solve
        # independently and interleave their writes.
        with self._lock:
            candidates, committed = (
                self.service.classify_for_optimization()
            )

            windows = self.service.possession_windows()

            request = build_jobs_optimization_request(
                corridor_id=target,
                tracks=[
                    track.track_id
                    for track in self.service.corridor.tracks
                ],
                candidates=candidates,
                possession_windows=windows,
                committed=committed,
            )

            uncovered = uncovered_possession_tracks(request)

            # The existing CP-SAT solver. Not wrapped, not replaced.
            result = solve(request)

            scheduled, refused = summarize_outcome(
                result,
                candidates,
            )

            generated_at = datetime.now(timezone.utc).isoformat()

            # One transaction for the whole batch.
            self.service.repository.apply_optimization_outcome(
                scheduled=scheduled,
                refused=refused,
                updated_at=generated_at,
                solver_status=result.status,
            )

        return {
            "corridor_id": target,
            "solver_status": result.status,
            "solve_time_seconds": result.solve_time_seconds,
            "possession_source": (
                POSSESSION_SOURCE_GENERATED_STATIC
            ),
            "possession_window_count": len(windows),
            "uncovered_tracks": sorted(uncovered),
            "generated_at": generated_at,
            "counts": {
                "considered": len(candidates),
                "committed": len(committed),
                "scheduled": len(scheduled),
                "unscheduled": len(refused),
            },
            "scheduled": scheduled,
            "unscheduled": refused,
            "infeasibility_reasons": list(
                result.infeasibility_reasons
            ),
        }
