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

from backend.app.audit.models import SYSTEM_ACTOR
from backend.app.audit.repository import AuditRepository
from backend.app.audit.service import AuditService

from backend.app.data.provenance import (
    ProvenanceLevel,
    ProvenanceProfile,
    to_possession_source,
)

from backend.app.jobs.service import (
    DEFAULT_MIN_HEADWAY_MINUTES,
    OPTIMIZATION_HORIZON_MINUTES,
    OPTIMIZATION_HORIZON_START,
    JobService,
    PossessionInputs,
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
    horizon_start: str = OPTIMIZATION_HORIZON_START,
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
        horizon_start=horizon_start,
        horizon_minutes=horizon_minutes,
        tracks=tracks,
        candidates=candidates,
        possession_windows=possession_windows,
        existing_committed_blocks=list(committed or []),
        min_headway_minutes=min_headway_minutes,
    )


def _build_provenance_profile(
    inputs: PossessionInputs,
) -> ProvenanceProfile:
    """Canonical four-axis provenance for one jobs-pipeline optimization.

    Built from the inputs ACTUALLY used (Sprint 3 Step 10): the
    timetable and possession axes come from the resolved
    PossessionInputs, so whichever possession source ran is what gets
    reported. They are not restated independently here, which is what
    keeps them from ever disagreeing with the windows the solver saw.

    topology and asset_condition remain SYNTHETIC on every path
    available today:

      - a generated corridor's topology is CorridorDataGenerator's two
        abstract endpoints;
      - the checked-in dataset corridor's topology comes from a file
        that declares itself synthetic. Its station codes (NDLS, NZM,
        ...) are real Indian Railways codes and its chainages are
        plausible, but nothing in it was transcribed from an official
        topology source - realistic-looking identifiers are exactly what
        the Step 9 weakest-link rule exists to stop reading as REAL_*.
      - assets are generated by CorridorDataGenerator.generate_assets on
        both paths.

    effective is never set here: it is computed by ProvenanceProfile
    from these four axes.
    """

    return ProvenanceProfile(
        topology=ProvenanceLevel.SYNTHETIC,
        timetable=inputs.timetable_provenance,
        asset_condition=ProvenanceLevel.SYNTHETIC,
        possession=inputs.possession_provenance,
    )


#: Most rejected train identifiers recorded on one audit row. A
#: rejection summary must stay a fixed-size fact about the run - the
#: audit record is not a place to mirror a timetable payload of
#: unbounded size. The count is always exact even when the list is
#: truncated, so nothing is silently under-reported.
_MAX_AUDITED_REJECTIONS = 20


def _rejection_summary(inputs: PossessionInputs) -> dict[str, Any]:
    """Bounded record of trains the canonical adapter refused.

    Empty on the generator path, which converts no trains at all. On the
    canonical path a rejection is safety-relevant in a specific way:
    possession derivation withholds every section the rejected train
    might have occupied, so a rejection EXPLAINS a missing maintenance
    window. Recording only the count and a capped list of identifiers
    keeps that explanation available without copying timetable content
    into the audit trail.
    """

    rejections = inputs.rejections

    if not rejections:
        return {}

    return {
        "train_rejection_count": len(rejections),
        "rejected_train_numbers": sorted(
            rejection.train_number for rejection in rejections
        )[:_MAX_AUDITED_REJECTIONS],
    }


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

        # The audit repository is pinned to this exact JobService's own
        # repository.db_path, not to any independently-resolved
        # default. That is what keeps a test constructing
        # JobService(repository=JobRepository(tmp_path / "jobs.db"))
        # isolated: its audit rows land in that same tmp_path file
        # instead of a shared/default database.
        self._audit_service = AuditService(
            AuditRepository(self.service.repository.db_path)
        )

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

            # ONE horizon for this run, resolved once here and passed to
            # BOTH the possession boundary and the request builder. The
            # canonical path anchors every traversal to this exact
            # horizon_start (Sprint 3 Step 8), so windows, traversals
            # and the OptimizationRequest cannot drift onto different
            # anchors - which is why it is not left to two independent
            # defaults.
            horizon_start = OPTIMIZATION_HORIZON_START
            horizon_minutes = OPTIMIZATION_HORIZON_MINUTES

            possession = self.service.possession_inputs(
                horizon_start,
                horizon_minutes,
            )

            windows = possession.windows

            request = build_jobs_optimization_request(
                corridor_id=target,
                tracks=[
                    track.track_id
                    for track in self.service.corridor.tracks
                ],
                candidates=candidates,
                possession_windows=windows,
                committed=committed,
                horizon_start=horizon_start,
                horizon_minutes=horizon_minutes,
            )

            uncovered = uncovered_possession_tracks(request)

            provenance_profile = _build_provenance_profile(possession)

            # possession_source is computed FROM provenance_profile.possession
            # via to_possession_source, not assigned a second time from
            # POSSESSION_SOURCE_GENERATED_STATIC directly - this is what
            # keeps it a mechanically-derived view rather than a field a
            # caller could set inconsistently with the canonical axis.
            # The literal originating signal is still the legacy
            # POSSESSION_SOURCE_GENERATED_STATIC constant in
            # backend.app.jobs.service - read by _build_provenance_profile
            # via from_possession_source - not the other way around; see
            # backend/app/data/provenance.py's to_possession_source
            # docstring for why a true canonical-first direction needs a
            # real possession-provenance signal this pipeline does not
            # have yet.
            possession_source = to_possession_source(
                provenance_profile.possession
            )

            # The existing CP-SAT solver, unmodified: record_run calls
            # solve(request) exactly once and returns its result
            # unchanged. The wrapping only adds an immutable
            # optimization_runs audit record - on success, on a
            # refused/infeasible outcome, or (via re-raise) on a
            # solver exception, which reaches the caller exactly as it
            # would without auditing.
            result = self._audit_service.record_run(
                request,
                solve,
                trigger="jobs_optimize",
                actor=SYSTEM_ACTOR,
                provenance_snapshot={
                    "possession_source": possession_source,
                    "possession_derivation": possession.derivation,
                    "possession_window_count": len(windows),
                    "uncovered_tracks": sorted(uncovered),
                    "provenance": provenance_profile.to_dict(),
                    **_rejection_summary(possession),
                },
            )

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
            "possession_source": possession_source,
            "possession_derivation": possession.derivation,
            "possession_window_count": len(windows),
            "provenance": provenance_profile.to_dict(),
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
