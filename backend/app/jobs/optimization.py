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

from dataclasses import replace
from typing import Any

from contracts import (
    BlockCandidate,
    OptimizationRequest,
    OptimizationResult,
    PossessionWindow,
)

from backend.app.audit.repository import AuditRepository
from backend.app.audit.service import AuditService, new_run_id

from backend.app.identity.actor import Actor, unidentified_actor
from backend.app.identity.authorization import JobAction

from backend.app.jobs.events import event_timestamp

from backend.app.jobs.lifecycle import (
    OUTCOME_ERROR,
    OUTCOME_NOT_RUN,
    CommittedStateIntegrityError,
    OptimizationAttempt,
    plan_optimization_failure,
    plan_optimization_outcome,
)

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

# Reused, not re-implemented (Sprint 3 Slice 2): these are the exact
# predicates the solver itself used to decide resource identity and
# possession coverage. Explaining a placement with anything else risks
# the explanation silently drifting from what the solver actually
# enforced.
from backend.app.optimizer.solver import (
    _possession_window_covers_block as _window_covers_block,
)
from backend.app.optimizer.solver import _resource_key


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


def _proposal_explanations(
    scheduled: list[dict[str, Any]],
    candidates: list[BlockCandidate],
    committed: list[BlockCandidate],
    possession: PossessionInputs,
) -> dict[str, dict[str, Any]]:
    """Per-job structured facts to attach to its placement event (Slice 2).

    Computed here, once, from data this call already has - never
    recomputed later by a reader - so a BlockProposal built from history
    reflects exactly what was true of THIS run, even if corridor data
    changes afterwards. See backend.app.jobs.proposal.

    Every value is plain JSON (str/int/float/bool/list/dict) - required
    by JobEvent's metadata contract - and every list is sorted, since
    JobEvent round-trips metadata through canonical (sort_keys) JSON.
    """

    by_id = {block.block_id: block for block in candidates}
    explanations: dict[str, dict[str, Any]] = {}

    # The pre-solve `committed` snapshot is what was FED to the solver as
    # pinned work - it is not proof of what the solver actually kept
    # scheduled. Cross-checking against the post-solve `scheduled` result
    # (each entry's own is_committed, set from result.scheduled_blocks -
    # see summarize_outcome / backend.app.optimizer.solver.solve) is what
    # keeps COMMITTED_BLOCKS_RESPECTED honest: a mate is named only if it
    # actually remained scheduled-and-committed in THIS run's result, not
    # merely because it was committed going in.
    actually_committed_ids = {
        entry["job_id"] for entry in scheduled if entry.get("is_committed")
    }

    for entry in scheduled:
        job_id = entry["job_id"]
        block = by_id.get(job_id)

        if block is None:
            continue

        start = int(entry["start_minute"])
        end = int(entry["end_minute"])

        window = next(
            (
                w
                for w in possession.windows
                if _window_covers_block(w, block)
                and w.start_minute <= start
                and end <= w.end_minute
            ),
            None,
        )

        resource = _resource_key(block)
        mates = sorted(
            {
                mate.block_id
                for mate in committed
                if mate.block_id != job_id
                and _resource_key(mate) == resource
                and mate.block_id in actually_committed_ids
            }
        )

        explanation: dict[str, Any] = {
            "objective_score": round(block.risk_score + block.priority_score, 4),
            "committed_resource_mates": mates,
        }

        if window is not None:
            explanation["possession_window_start_minute"] = int(window.start_minute)
            explanation["possession_window_end_minute"] = int(window.end_minute)

        if possession.snapshot is None:
            # GENERATED_STATIC_SLOTS: no timetable was consulted at all,
            # so "zero conflicts" would misrepresent "never checked".
            explanation["train_data_available"] = False
            explanation["trains_avoided"] = []
        else:
            explanation["train_data_available"] = True
            explanation["trains_avoided"] = sorted(
                rejection.train_number
                for rejection in possession.rejections
                if not rejection.affected_section_ids
                or block.section_id in rejection.affected_section_ids
            )

        explanations[job_id] = explanation

    return explanations


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

    CONCURRENCY (honest statement of scope):
    optimization holds JobService.lifecycle_lock - the SAME lock that
    commit (notify), completion and schedule assignment hold - for the
    whole snapshot -> solve -> write sequence. Within ONE application
    process (the current deployment shape: a single uvicorn process
    backed by a single SQLite file) that means an optimization can never
    interleave with another optimization or with a commit.

    The lock provides NO protection across processes, workers or hosts.
    What does hold across processes is fail-closed rather than
    serializing: the outcome is written in one BEGIN IMMEDIATE
    transaction that re-checks every considered job's status against
    the snapshot the solver saw, and refuses the whole batch
    (ConcurrentJobModificationError) if any changed. Multi-instance
    serialization needs a database or external lock and remains out of
    scope.

    LIFECYCLE HISTORY
    Every attempt that considers at least one job records, per job, who
    requested it and what the system decided - including attempts that
    fail. Any attempt that does not (re)place a scheduled job withdraws
    that job's uncommitted proposal (see backend.app.jobs.lifecycle).
    """

    def __init__(self, service: JobService | None = None):
        self.service = service or JobService()

        # Shared with every JobService transition. Kept under the old
        # attribute name as well so existing references still resolve
        # to the one lock rather than a second, private one.
        self._lock = self.service.lifecycle_lock

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
        actor: Actor | None = None,
    ) -> dict[str, Any]:

        requester = actor if actor is not None else unidentified_actor()

        self.service.authorization.authorize(
            requester,
            JobAction.REQUEST_OPTIMIZATION,
        )

        target = corridor_id or self.service.corridor.corridor_id

        if target != self.service.corridor.corridor_id:
            raise KeyError(
                f"Unknown corridor '{target}'. This deployment serves "
                f"corridor '{self.service.corridor.corridor_id}'."
            )

        # Single-flight across every lifecycle transition: two
        # authorities pressing "optimize", or one optimizing while
        # another commits, must not read the same active set and
        # interleave their writes.
        with self._lock:
            try:
                candidates, committed, expected_statuses = (
                    self.service.optimization_snapshot()
                )

            except CommittedStateIntegrityError as exc:
                # Refused before any job was considered: no solver run, no
                # optimization_runs row, no proposal written or withdrawn.
                # The refusal is recorded on each inconsistent job.
                self.service.record_rejected_transition(
                    list(exc.job_ids), requester, "optimize", exc
                )
                raise

            if not candidates:
                raise NoEligibleJobsError(
                    "No active maintenance jobs to optimize."
                )

            job_ids = [block.block_id for block in candidates]

            # ONE horizon for this run, resolved once here and passed to
            # BOTH the possession boundary and the request builder. The
            # canonical path anchors every traversal to this exact
            # horizon_start (Sprint 3 Step 8), so windows, traversals
            # and the OptimizationRequest cannot drift onto different
            # anchors - which is why it is not left to two independent
            # defaults.
            #
            # horizon_minutes is read from self.service.horizon_minutes
            # (Slice 4 Step 2), not from the OPTIMIZATION_HORIZON_MINUTES
            # module constant directly - that constant remains only the
            # DEFAULT a plain JobService() starts with. Reading the
            # instance attribute here is what makes
            # JobService(horizon_minutes=...) actually take effect for
            # optimization, not just for possession_inputs/create_job.
            horizon_start = OPTIMIZATION_HORIZON_START
            horizon_minutes = self.service.horizon_minutes

            attempt = OptimizationAttempt(
                corridor_id=target,
                requester=requester,
                requested_at=event_timestamp(),
                run_id=new_run_id(),
                horizon_start=horizon_start,
            )

            try:
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

            except Exception as exc:
                # Refused before the solver ran (no possession data, or
                # possession inputs that could not be built safely).
                # No optimization_runs row exists, so the job events
                # carry no run id.
                self._record_failure(
                    job_ids,
                    replace(attempt, run_id=None),
                    exc,
                    OUTCOME_NOT_RUN,
                )
                raise

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
            # The run's actor column records who caused the run, the
            # same actor as each job's OPTIMIZATION_REQUESTED event. The
            # system decisions that follow are recorded under
            # SYSTEM:OPTIMIZER with this run id.
            try:
                result = self._audit_service.record_run(
                    request,
                    solve,
                    trigger=attempt.trigger,
                    actor=requester.actor_id,
                    run_id=attempt.run_id,
                    provenance_snapshot={
                        "possession_source": possession_source,
                        "possession_derivation": possession.derivation,
                        "possession_window_count": len(windows),
                        "uncovered_tracks": sorted(uncovered),
                        "provenance": provenance_profile.to_dict(),
                        **_rejection_summary(possession),
                    },
                )

            except Exception as exc:
                # record_run already wrote the ERROR optimization_runs
                # row; job history references it by run id.
                self._record_failure(job_ids, attempt, exc, OUTCOME_ERROR)
                raise

            scheduled, refused = summarize_outcome(
                result,
                candidates,
            )

            explanations = _proposal_explanations(
                scheduled, candidates, committed, possession
            )

            generated_at = event_timestamp()

            # One transaction for the whole batch: every job's new state
            # and every lifecycle event, or none of them.
            try:
                self.service.repository.mutate_jobs(
                    job_ids,
                    plan_optimization_outcome(
                        job_ids,
                        attempt=attempt,
                        completed_at=generated_at,
                        solver_status=result.status,
                        placements={entry["job_id"]: entry for entry in scheduled},
                        refusals={entry["job_id"]: entry["reason"] for entry in refused},
                        expected_statuses=expected_statuses,
                        explanations=explanations,
                    ),
                    reject_terminal=True,
                )

            except CommittedStateIntegrityError as exc:
                # Stored state became inconsistent after the snapshot (a
                # writer outside this process). The batch was rolled back.
                self.service.record_rejected_transition(
                    list(exc.job_ids), requester, "optimize", exc
                )
                raise

        return {
            "corridor_id": target,
            "optimization_run_id": attempt.run_id,
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

    def _record_failure(
        self,
        job_ids: list[str],
        attempt: OptimizationAttempt,
        error: BaseException,
        outcome_label: str,
    ) -> None:
        """Record a failed attempt in job history, then let the caller re-raise.

        Withdraws every uncommitted proposal the attempt considered, so a
        proposal can never outlive an attempt that did not reaffirm it.
        Committed work is untouched. If recording itself fails, the
        ORIGINAL error still propagates, with a note - history problems
        must never replace the real cause.
        """

        try:
            self.service.repository.mutate_jobs(
                job_ids,
                plan_optimization_failure(
                    job_ids,
                    attempt=attempt,
                    failed_at=event_timestamp(),
                    reason=f"{type(error).__name__}: {error}",
                    outcome_label=outcome_label,
                ),
            )

        except Exception as history_error:  # noqa: BLE001
            error.add_note(
                "Job lifecycle history for this failed optimization could "
                f"not be recorded: {type(history_error).__name__}: "
                f"{history_error}"
            )
