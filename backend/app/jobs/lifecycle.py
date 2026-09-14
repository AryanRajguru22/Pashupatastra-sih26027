"""Job lifecycle rules: transitions, protected state, and their events (Slice 1).

This module is pure: no I/O, no locking, no persistence. It decides
WHAT a transition does to a job and WHICH events describe it.
backend.app.jobs.repository decides HOW that is written - atomically,
with the events in the same transaction as the state change. The split
is what lets a later authorization or hash-chain slice wrap the write
path without re-deriving the rules.

STATES (unchanged - no new status was added)
    reported   -> no current proposal; eligible for optimization
    scheduled  -> has a CURRENT, uncommitted proposal
    notified   -> committed/pinned; the placement is protected
    completed  -> terminal

THE TWO STEP 16 DEFECTS THIS FIXES
    Stale proposal (P0). A scheduled job that a later optimization
    attempt did not (re)place used to keep status 'scheduled' and its old
    window, so it could still be committed. Now ANY attempt that
    considered the job and did not produce a placement for it - a solver
    refusal, an INFEASIBLE model, a solver exception, or a refusal to
    solve at all - withdraws the uncommitted proposal: the job returns
    to 'reported', its window is cleared, and PROPOSAL_INVALIDATED is
    recorded with the reason. 'scheduled' therefore always means "the
    latest attempt that considered this job proposed this window".

    Committed-state regression. Re-optimization used to rewrite a
    notified job's persisted block status to SCHEDULED. A notified job's
    block now stays COMMITTED, and protect_committed_and_terminal_state
    rejects any write - from any repository path - that would demote a
    committed job, drop its COMMITTED block status, move its placement,
    or touch a terminal job at all.

INCONSISTENT COMMITTED STATE IS DETECTED, NEVER REPAIRED
    A notified job whose STORED block is not COMMITTED / is_committed, or
    which has no valid placement, is an integrity violation - whether the
    old bug or a future one produced it. Every write path refuses such a
    job with CommittedStateIntegrityError and leaves the row exactly as
    stored; the service records the refused attempt (TRANSITION_REJECTED)
    and optimization refuses the whole corridor before solving, because
    work cannot be scheduled around a commitment whose integrity is
    unknown. Correcting the row is an explicit reconciliation decision
    outside this module. No write may produce such a state either.

COMMITTED WORK IS NEVER INVALIDATED BY A FAILED ATTEMPT
    A notified job the solver does not re-place keeps its status,
    placement and COMMITTED block; the refusal is recorded as
    COMMITTED_BLOCK_CONFLICT so the conflict is visible rather than
    silently absorbed.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from contracts import BlockStatus

from backend.app.identity.actor import OPTIMIZER, Actor
from backend.app.jobs.events import (
    JobEvent,
    JobEventType,
    JobStateSnapshot,
    make_event,
)
from backend.app.jobs.models import JobStatus


REPORTED = JobStatus.REPORTED.value
SCHEDULED = JobStatus.SCHEDULED.value
NOTIFIED = JobStatus.NOTIFIED.value
COMPLETED = JobStatus.COMPLETED.value

# Statuses from which a job can never return. See
# backend.app.jobs.repository, which re-exports this for existing callers.
TERMINAL_STATUSES = (COMPLETED,)

# Job statuses whose work is committed and must be pinned.
COMMITTED_STATUSES = (NOTIFIED,)

# The only lifecycle moves a job may make through the service path.
ALLOWED_TRANSITIONS: Dict[str, frozenset] = {
    REPORTED: frozenset({REPORTED, SCHEDULED}),
    SCHEDULED: frozenset({SCHEDULED, REPORTED, NOTIFIED}),
    NOTIFIED: frozenset({NOTIFIED, COMPLETED}),
    COMPLETED: frozenset(),
}

PROPOSAL_RUN_ID_KEY = "proposal_run_id"
COMMITTED_START_KEY = "committed_start_minute"
COMMITTED_END_KEY = "committed_end_minute"

# last_solver_status values for attempts that produced no solver status.
# ERROR matches backend.app.audit.models.ERROR_STATUS (solve() raised);
# NOT_RUN means the attempt was refused before the solver was called.
OUTCOME_ERROR = "ERROR"
OUTCOME_NOT_RUN = "NOT_RUN"


class TerminalJobError(RuntimeError):
    """Raised when a mutation would alter a job in a terminal status."""


class CommittedJobError(RuntimeError):
    """Raised when a write would weaken committed (pinned) work."""


class CommittedStateIntegrityError(RuntimeError):
    """STORED committed state is already inconsistent; nothing was written.

    Distinct from CommittedJobError, which means "this write would weaken
    committed work". This means the persisted row itself violates the
    committed-state invariant (e.g. a notified job whose block is not
    COMMITTED), so no transition can be trusted to act on it. It is never
    repaired automatically: correcting it is an explicit, separate
    reconciliation decision, not a side effect of another operation.
    """

    def __init__(self, message: str, job_ids: Sequence[str]):
        super().__init__(message)
        self.job_ids = tuple(job_ids)


class InvalidTransitionError(ValueError):
    """A requested lifecycle transition is not allowed from the current state.

    A ValueError subclass so the existing HTTP mapping (400) is unchanged.
    """


class StaleProposalError(RuntimeError):
    """The caller acted on a proposal that is no longer the current one."""


class ConcurrentJobModificationError(RuntimeError):
    """A job changed between an optimization snapshot and its write.

    The in-process lifecycle lock makes this unreachable within one
    process; it is the fail-closed guard for writers the lock cannot see
    (a second process on the same database).
    """


@dataclass(frozen=True)
class JobMutation:
    """The complete new value of every mutable job column.

    Total rather than partial: the repository writes exactly these
    values, so no column is left to a default a caller forgot about.
    """

    job_id: str
    status: str
    schedule_start_minute: Optional[int]
    schedule_end_minute: Optional[int]
    updated_at: Optional[str]
    last_solver_status: Optional[str]
    last_refusal_reason: Optional[str]
    block_candidate: Dict[str, Any]

    @classmethod
    def unchanged(cls, job: Mapping[str, Any]) -> "JobMutation":
        """The job's current values, exactly as stored, as the starting point of a plan.

        No normalization is applied. In particular a committed job whose
        stored block is not COMMITTED is NOT repaired here: that state is
        an integrity violation, and protect_committed_and_terminal_state
        refuses every write to such a job (CommittedStateIntegrityError)
        so the inconsistency is surfaced instead of being rewritten into
        valid-looking state during an unrelated transition.
        """

        block = copy.deepcopy(job["block_candidate"])

        return cls(
            job_id=job["job_id"],
            status=job["status"],
            schedule_start_minute=job.get("schedule_start_minute"),
            schedule_end_minute=job.get("schedule_end_minute"),
            updated_at=job.get("updated_at"),
            last_solver_status=job.get("last_solver_status"),
            last_refusal_reason=job.get("last_refusal_reason"),
            block_candidate=block,
        )

    def as_job(self, current: Mapping[str, Any]) -> Dict[str, Any]:
        """The job as it will read after this mutation is written."""

        job = dict(current)
        job.update(
            status=self.status,
            schedule_start_minute=self.schedule_start_minute,
            schedule_end_minute=self.schedule_end_minute,
            updated_at=self.updated_at,
            last_solver_status=self.last_solver_status,
            last_refusal_reason=self.last_refusal_reason,
            block_candidate=self.block_candidate,
        )
        return job


Plan = Callable[
    [Dict[str, Dict[str, Any]]],
    Tuple[List[JobMutation], List[JobEvent]],
]


# ----------------------------------------------------------------------
# Invariants
# ----------------------------------------------------------------------


def committed_state_problems(
    status: str,
    block: Mapping[str, Any],
    schedule: Tuple[Optional[int], Optional[int]],
) -> List[str]:
    """Why a job in `status` with this block/schedule is not validly committed.

    Empty for any non-committed status, and for a consistent committed
    job: block status COMMITTED, is_committed True, and a real placement.
    """

    if status not in COMMITTED_STATUSES:
        return []

    problems = []

    if block.get("status") != BlockStatus.COMMITTED.value:
        problems.append(
            f"block status is {block.get('status')!r}, expected "
            f"{BlockStatus.COMMITTED.value!r}"
        )

    if block.get("is_committed") is not True:
        problems.append(
            f"block is_committed is {block.get('is_committed')!r}, expected True"
        )

    start, end = schedule

    if start is None or end is None or end <= start:
        problems.append(f"placement {(start, end)} is not a valid window")

    return problems


def assert_committed_state_consistent(jobs: Iterable[Mapping[str, Any]]) -> None:
    """Raise CommittedStateIntegrityError naming EVERY inconsistent stored job."""

    findings = []

    for job in jobs:
        problems = committed_state_problems(
            job["status"],
            job.get("block_candidate") or {},
            (job.get("schedule_start_minute"), job.get("schedule_end_minute")),
        )

        if problems:
            findings.append((job["job_id"], job["status"], problems))

    if findings:
        detail = "; ".join(
            f"job '{job_id}' is '{status}' but " + ", ".join(problems)
            for job_id, status, problems in findings
        )
        raise CommittedStateIntegrityError(
            "Stored committed state is inconsistent and was not modified: "
            f"{detail}. Explicit reconciliation is required.",
            [job_id for job_id, _, _ in findings],
        )


def protect_committed_and_terminal_state(
    current: Mapping[str, Any],
    new_status: str,
    new_block: Mapping[str, Any],
    new_schedule: Tuple[Optional[int], Optional[int]],
) -> None:
    """Invariants EVERY write path must respect, including low-level ones.

    - A terminal job is never written.
    - A committed job whose STORED state is already inconsistent is never
      written (CommittedStateIntegrityError) - detected, not repaired.
    - A committed (notified) job may only stay committed or complete; it
      keeps a COMMITTED, is_committed block and its exact placement.
    - No write may PRODUCE an inconsistent committed job, so a future
      application bug cannot create the state the check above refuses.
    """

    job_id = current["job_id"]
    status = current["status"]

    if status in TERMINAL_STATUSES:
        raise TerminalJobError(
            f"Job '{job_id}' is in terminal status '{status}' and cannot "
            "be modified"
        )

    assert_committed_state_consistent([current])

    resulting = committed_state_problems(new_status, new_block, tuple(new_schedule))

    if resulting:
        raise CommittedJobError(
            f"Job '{job_id}' would become '{new_status}' with inconsistent "
            f"committed state: {', '.join(resulting)}"
        )

    if status not in COMMITTED_STATUSES:
        return

    if new_status not in (NOTIFIED, COMPLETED):
        raise CommittedJobError(
            f"Job '{job_id}' is committed ('{status}') and cannot be moved "
            f"back to '{new_status}'"
        )

    if (
        new_block.get("status") != BlockStatus.COMMITTED.value
        or new_block.get("is_committed") is not True
    ):
        raise CommittedJobError(
            f"Job '{job_id}' is committed; its block must remain "
            "COMMITTED and is_committed"
        )

    current_schedule = (
        current.get("schedule_start_minute"),
        current.get("schedule_end_minute"),
    )

    if tuple(new_schedule) != current_schedule:
        raise CommittedJobError(
            f"Job '{job_id}' is committed at {current_schedule}; its "
            f"placement cannot be changed to {tuple(new_schedule)}"
        )


def validate_mutation(current: Mapping[str, Any], mutation: JobMutation) -> None:
    """Full service-path validation: protected state plus the transition graph."""

    protect_committed_and_terminal_state(
        current,
        mutation.status,
        mutation.block_candidate,
        (mutation.schedule_start_minute, mutation.schedule_end_minute),
    )

    allowed = ALLOWED_TRANSITIONS.get(current["status"])

    if allowed is None or mutation.status not in allowed:
        raise InvalidTransitionError(
            f"Job '{current['job_id']}' cannot move from "
            f"'{current['status']}' to '{mutation.status}'"
        )

    start = mutation.schedule_start_minute
    end = mutation.schedule_end_minute

    if mutation.status == REPORTED and (start is not None or end is not None):
        raise InvalidTransitionError(
            f"Job '{current['job_id']}' would be 'reported' while still "
            "carrying a proposed window"
        )

    if mutation.status in (SCHEDULED, NOTIFIED) and (
        start is None or end is None or end <= start
    ):
        raise InvalidTransitionError(
            f"Job '{current['job_id']}' would be '{mutation.status}' "
            "without a valid placement"
        )


# ----------------------------------------------------------------------
# Block helpers
# ----------------------------------------------------------------------


def _with_placement(
    block: Mapping[str, Any],
    start: int,
    end: int,
    proposal_run_id: Optional[str],
) -> Dict[str, Any]:
    updated = copy.deepcopy(dict(block))
    metadata = dict(updated.get("metadata") or {})
    metadata[COMMITTED_START_KEY] = int(start)
    metadata[COMMITTED_END_KEY] = int(end)

    if proposal_run_id is None:
        metadata.pop(PROPOSAL_RUN_ID_KEY, None)
    else:
        metadata[PROPOSAL_RUN_ID_KEY] = proposal_run_id

    updated["metadata"] = metadata
    updated["status"] = BlockStatus.SCHEDULED.value
    updated["is_committed"] = False
    return updated


def _without_placement(block: Mapping[str, Any]) -> Dict[str, Any]:
    updated = copy.deepcopy(dict(block))
    metadata = dict(updated.get("metadata") or {})

    for key in (COMMITTED_START_KEY, COMMITTED_END_KEY, PROPOSAL_RUN_ID_KEY):
        metadata.pop(key, None)

    updated["metadata"] = metadata
    updated["status"] = BlockStatus.PLANNED.value
    updated["is_committed"] = False
    return updated


def _committed(block: Mapping[str, Any]) -> Dict[str, Any]:
    updated = copy.deepcopy(dict(block))
    updated["status"] = BlockStatus.COMMITTED.value
    updated["is_committed"] = True
    return updated


def proposal_run_id_of(job: Mapping[str, Any]) -> Optional[str]:
    block = job.get("block_candidate") or {}
    return (block.get("metadata") or {}).get(PROPOSAL_RUN_ID_KEY)


def _snapshot(job: Mapping[str, Any]) -> JobStateSnapshot:
    return JobStateSnapshot.from_job(job)


# ----------------------------------------------------------------------
# Optimization outcome
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class OptimizationAttempt:
    """What one optimization attempt was, shared by every job it considered.

    requester is None only for the history-less compatibility wrapper
    JobRepository.apply_optimization_outcome; every path that records
    history passes the actor who requested the run.
    """

    corridor_id: str
    requester: Optional[Actor]
    requested_at: str
    run_id: Optional[str]
    horizon_start: str
    trigger: str = "jobs_optimize"


def _requested_event(job, attempt: OptimizationAttempt) -> JobEvent:
    state = _snapshot(job)
    return make_event(
        job["job_id"],
        JobEventType.OPTIMIZATION_REQUESTED,
        attempt.requester,
        occurred_at=attempt.requested_at,
        optimization_run_id=attempt.run_id,
        before_state=state,
        after_state=state,
        metadata={
            "corridor_id": attempt.corridor_id,
            "trigger": attempt.trigger,
        },
    )


def plan_optimization_outcome(
    job_ids: Sequence[str],
    *,
    attempt: OptimizationAttempt,
    completed_at: str,
    solver_status: str,
    placements: Mapping[str, Mapping[str, Any]],
    refusals: Mapping[str, str],
    expected_statuses: Optional[Mapping[str, str]] = None,
    record_history: bool = True,
    explanations: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> Plan:
    """Plan the writes and events for a solver result.

    placements: job_id -> {start_minute, end_minute, track_id, section_id?}
    refusals:   job_id -> the solver's verbatim reason

    expected_statuses, when given, is the status each job had when the
    candidate set was classified. Any difference means another writer
    changed the job underneath this run, and the whole batch is refused
    (ConcurrentJobModificationError) rather than applied to state the
    solver never saw.

    explanations (Sprint 3 Slice 2), when given, is
    job_id -> additional structured facts about a PLACED job's outcome
    (possession window used, which currently-committed jobs share its
    resource, its objective contribution, train-conflict data). Merged
    into the placement event's metadata so backend.app.jobs.proposal can
    build a BlockProposal straight from stored history without
    recomputing anything the solver already decided. Absent for a
    refusal - there is no placement to explain.
    """

    if record_history and attempt.requester is None:
        raise ValueError("Recording optimization history requires a requester.")

    def plan(rows: Dict[str, Dict[str, Any]]):
        mutations: List[JobMutation] = []
        events: List[JobEvent] = []

        counts = {
            "considered": len(job_ids),
            "scheduled": len(placements),
            "unscheduled": len(refusals),
        }

        for job_id in job_ids:
            job = rows[job_id]
            status = job["status"]

            if expected_statuses is not None and expected_statuses.get(job_id) != status:
                raise ConcurrentJobModificationError(
                    f"Job '{job_id}' changed from "
                    f"'{expected_statuses.get(job_id)}' to '{status}' while "
                    "optimization was running; the outcome was not applied"
                )

            before = _snapshot(job)
            base = replace(
                JobMutation.unchanged(job),
                updated_at=completed_at,
                last_solver_status=solver_status,
            )

            if job_id in placements:
                mutation, outcome = _plan_placement(
                    job,
                    base,
                    placements[job_id],
                    attempt,
                    completed_at,
                    solver_status,
                    (explanations or {}).get(job_id),
                )
            elif job_id in refusals:
                mutation, outcome = _plan_refusal(
                    job, base, refusals[job_id], attempt, completed_at, solver_status
                )
            else:
                raise ValueError(
                    f"Job '{job_id}' was considered but the outcome neither "
                    "placed nor refused it"
                )

            mutations.append(mutation)

            if record_history:
                events.append(_requested_event(job, attempt))
                events.append(
                    make_event(
                        job_id,
                        JobEventType.OPTIMIZATION_COMPLETED,
                        OPTIMIZER,
                        occurred_at=completed_at,
                        optimization_run_id=attempt.run_id,
                        before_state=before,
                        after_state=before,
                        metadata={"solver_status": solver_status, **counts},
                    )
                )
                events.append(outcome(_snapshot(mutation.as_job(job))))

        return mutations, events

    return plan


def _plan_placement(
    job, base, placement, attempt, completed_at, solver_status, explanation=None
):
    job_id = job["job_id"]
    status = job["status"]
    before = _snapshot(job)
    start = int(placement["start_minute"])
    end = int(placement["end_minute"])

    location = {
        "track_id": placement.get("track_id"),
        "section_id": (job.get("block_candidate") or {}).get("section_id"),
        "start_minute": start,
        "end_minute": end,
        "horizon_start": attempt.horizon_start,
        "solver_status": solver_status,
        **(explanation or {}),
    }

    if status in COMMITTED_STATUSES:
        current = (job.get("schedule_start_minute"), job.get("schedule_end_minute"))

        if (start, end) != current:
            # The solver pins committed work to its exact placement; a
            # different placement means the pin did not hold. Refuse the
            # batch rather than move committed work.
            raise CommittedJobError(
                f"Optimization placed committed job '{job_id}' at "
                f"{(start, end)} instead of its committed {current}"
            )

        mutation = replace(base, last_refusal_reason=None)

        def outcome(after):
            return make_event(
                job_id,
                JobEventType.COMMITTED_BLOCK_PRESERVED,
                OPTIMIZER,
                occurred_at=completed_at,
                optimization_run_id=attempt.run_id,
                before_state=before,
                after_state=after,
                metadata=location,
            )

        return mutation, outcome

    mutation = replace(
        base,
        status=SCHEDULED,
        schedule_start_minute=start,
        schedule_end_minute=end,
        last_refusal_reason=None,
        block_candidate=_with_placement(
            job["block_candidate"], start, end, attempt.run_id
        ),
    )

    if status == SCHEDULED:
        previous = {
            "previous_start_minute": job.get("schedule_start_minute"),
            "previous_end_minute": job.get("schedule_end_minute"),
            "previous_proposal_run_id": proposal_run_id_of(job),
            "placement_changed": (
                (job.get("schedule_start_minute"), job.get("schedule_end_minute"))
                != (start, end)
            ),
        }
        event_type = JobEventType.BLOCK_REPROPOSED
    else:
        previous = {}
        event_type = JobEventType.BLOCK_PROPOSED

    def outcome(after):
        return make_event(
            job_id,
            event_type,
            OPTIMIZER,
            occurred_at=completed_at,
            optimization_run_id=attempt.run_id,
            before_state=before,
            after_state=after,
            metadata={**location, **previous},
        )

    return mutation, outcome


def _plan_refusal(job, base, reason, attempt, completed_at, solver_status):
    job_id = job["job_id"]
    status = job["status"]
    before = _snapshot(job)
    metadata = {"solver_status": solver_status}

    if status in COMMITTED_STATUSES:
        mutation = replace(base, last_refusal_reason=reason)
        event_type = JobEventType.COMMITTED_BLOCK_CONFLICT
        metadata["commitment_preserved"] = True

    elif status == SCHEDULED:
        mutation = _withdrawn(base, job, reason)
        event_type = JobEventType.PROPOSAL_INVALIDATED
        metadata.update(_withdrawn_metadata(job))

    else:
        mutation = replace(base, last_refusal_reason=reason)
        event_type = JobEventType.OPTIMIZATION_REFUSED

    def outcome(after):
        return make_event(
            job_id,
            event_type,
            OPTIMIZER,
            occurred_at=completed_at,
            reason=reason,
            optimization_run_id=attempt.run_id,
            before_state=before,
            after_state=after,
            metadata=metadata,
        )

    return mutation, outcome


def _withdrawn(base: JobMutation, job, reason: str) -> JobMutation:
    return replace(
        base,
        status=REPORTED,
        schedule_start_minute=None,
        schedule_end_minute=None,
        last_refusal_reason=reason,
        block_candidate=_without_placement(job["block_candidate"]),
    )


def _withdrawn_metadata(job) -> Dict[str, Any]:
    return {
        "withdrawn_start_minute": job.get("schedule_start_minute"),
        "withdrawn_end_minute": job.get("schedule_end_minute"),
        "withdrawn_proposal_run_id": proposal_run_id_of(job),
    }


def plan_optimization_failure(
    job_ids: Sequence[str],
    *,
    attempt: OptimizationAttempt,
    failed_at: str,
    reason: str,
    outcome_label: str,
) -> Plan:
    """Plan the writes and events for an attempt that produced no outcome.

    Uses each job's CURRENT state rather than a snapshot check: the only
    writes it makes are withdrawing uncommitted proposals and recording
    the failure, both of which are safe whatever else happened. Jobs that
    became terminal meanwhile are left alone.
    """

    def plan(rows: Dict[str, Dict[str, Any]]):
        mutations: List[JobMutation] = []
        events: List[JobEvent] = []

        for job_id in job_ids:
            job = rows[job_id]
            status = job["status"]

            if status in TERMINAL_STATUSES:
                continue

            before = _snapshot(job)
            base = replace(
                JobMutation.unchanged(job),
                updated_at=failed_at,
                last_solver_status=outcome_label,
                last_refusal_reason=reason,
            )

            mutation = _withdrawn(base, job, reason) if status == SCHEDULED else base
            mutations.append(mutation)
            after = _snapshot(mutation.as_job(job))

            events.append(_requested_event(job, attempt))
            events.append(
                make_event(
                    job_id,
                    JobEventType.OPTIMIZATION_FAILED,
                    OPTIMIZER,
                    occurred_at=failed_at,
                    reason=reason,
                    optimization_run_id=attempt.run_id,
                    before_state=before,
                    after_state=before,
                    metadata={
                        "outcome": outcome_label,
                        "commitment_preserved": status in COMMITTED_STATUSES,
                    },
                )
            )

            if status == SCHEDULED:
                events.append(
                    make_event(
                        job_id,
                        JobEventType.PROPOSAL_INVALIDATED,
                        OPTIMIZER,
                        occurred_at=failed_at,
                        reason=reason,
                        optimization_run_id=attempt.run_id,
                        before_state=before,
                        after_state=after,
                        metadata={"outcome": outcome_label, **_withdrawn_metadata(job)},
                    )
                )

        return mutations, events

    return plan


# ----------------------------------------------------------------------
# Human-initiated transitions
# ----------------------------------------------------------------------


def plan_commit(
    job_id: str,
    *,
    actor: Actor,
    at: str,
    expected_proposal_run_id: Optional[str] = None,
) -> Plan:
    """scheduled -> notified: commit/pin the CURRENT proposal."""

    def plan(rows):
        job = rows[job_id]
        status = job["status"]

        if status != SCHEDULED:
            raise InvalidTransitionError(
                f"Job '{job_id}' cannot be notified from status '{status}'"
            )

        if job.get("schedule_start_minute") is None or job.get("schedule_end_minute") is None:
            raise InvalidTransitionError(
                f"Job '{job_id}' is 'scheduled' but has no current proposal"
            )

        current_run = proposal_run_id_of(job)

        if expected_proposal_run_id is not None and expected_proposal_run_id != current_run:
            raise StaleProposalError(
                f"Job '{job_id}' proposal is from run {current_run!r}, not "
                f"the expected {expected_proposal_run_id!r}; the proposal "
                "being committed is not the one that was reviewed"
            )

        mutation = replace(
            JobMutation.unchanged(job),
            status=NOTIFIED,
            updated_at=at,
            block_candidate=_committed(job["block_candidate"]),
        )

        event = make_event(
            job_id,
            JobEventType.BLOCK_COMMITTED,
            actor,
            occurred_at=at,
            optimization_run_id=current_run,
            before_state=_snapshot(job),
            after_state=_snapshot(mutation.as_job(job)),
            metadata={
                "transition": "notify",
                "start_minute": job.get("schedule_start_minute"),
                "end_minute": job.get("schedule_end_minute"),
                "expected_proposal_run_id": expected_proposal_run_id,
            },
        )

        return [mutation], [event]

    return plan


def _require_nonblank(value: Optional[str], what: str) -> None:
    if not value or not str(value).strip():
        raise InvalidTransitionError(f"{what} is required")


def plan_reject(
    job_id: str,
    *,
    actor: Actor,
    at: str,
    reason: str,
    expected_proposal_run_id: str,
) -> Plan:
    """scheduled -> reported: refuse the CURRENT proposal outright.

    Unlike plan_commit, expected_proposal_run_id is MANDATORY here - a
    human rejection decision must always be pinned to the specific
    proposal that was reviewed, never "whatever proposal is current".
    reason is mandatory too, and becomes the job's last_refusal_reason
    (the same field a solver refusal writes - see _withdrawn), so
    _no_proposal_reason reports it identically either way.

    Reuses _withdrawn(), the exact same "clear placement, return to
    reported" mutation a solver's own refusal applies to a stale
    'scheduled' job (see _plan_refusal) - a human rejection and a
    solver withdrawal are the same state change, described by a
    different event.
    """

    _require_nonblank(expected_proposal_run_id, "expected_proposal_run_id")
    _require_nonblank(reason, "reason")

    def plan(rows):
        job = rows[job_id]
        status = job["status"]

        if status != SCHEDULED:
            raise InvalidTransitionError(
                f"Job '{job_id}' cannot reject a proposal from status "
                f"'{status}'"
            )

        if job.get("schedule_start_minute") is None or job.get("schedule_end_minute") is None:
            raise InvalidTransitionError(
                f"Job '{job_id}' is 'scheduled' but has no current "
                "proposal to reject"
            )

        current_run = proposal_run_id_of(job)

        if expected_proposal_run_id != current_run:
            raise StaleProposalError(
                f"Job '{job_id}' proposal is from run {current_run!r}, not "
                f"the expected {expected_proposal_run_id!r}; the proposal "
                "being rejected is not the one that was reviewed"
            )

        before = _snapshot(job)
        rejected_start = job.get("schedule_start_minute")
        rejected_end = job.get("schedule_end_minute")

        mutation = _withdrawn(
            replace(JobMutation.unchanged(job), updated_at=at), job, reason
        )

        event = make_event(
            job_id,
            JobEventType.PROPOSAL_REJECTED,
            actor,
            occurred_at=at,
            reason=reason,
            optimization_run_id=current_run,
            before_state=before,
            after_state=_snapshot(mutation.as_job(job)),
            metadata={
                "transition": "reject",
                "rejected_start_minute": rejected_start,
                "rejected_end_minute": rejected_end,
                "expected_proposal_run_id": expected_proposal_run_id,
            },
        )

        return [mutation], [event]

    return plan


def plan_postpone(
    job_id: str,
    *,
    actor: Actor,
    at: str,
    reason: str,
    selected_date: str,
    not_before_minute: int,
    expected_proposal_run_id: str,
    proposal_digest: Optional[str] = None,
) -> Plan:
    """scheduled -> reported: defer the CURRENT proposal to a not-before date.

    not_before_minute is the ALREADY-CONVERTED horizon-relative minute -
    see backend.app.data.horizon_anchor.horizon_relative_minutes, the
    one authoritative conversion. This function does not compute it and
    does not re-derive it from selected_date; a caller that has not
    converted selected_date through that path has not honoured the
    horizon contract, and selected_date is recorded here only as
    traceability metadata, never re-parsed.

    Fails closed (InvalidTransitionError) if not_before_minute falls
    outside [0, latest_end_minute) of the job's own stored block: a
    postponement can never silently create schedulable availability
    beyond what this deployment's optimization horizon supports, and a
    "postpone into the past" is rejected the same way a "postpone
    beyond the horizon" is - both are an invalid target, not a stale
    proposal.

    proposal_digest is audit traceability only (see
    backend.app.jobs.proposal.BlockProposal.digest) - expected_proposal_
    run_id, checked above, is what actually makes a stale postpone fail.
    """

    _require_nonblank(expected_proposal_run_id, "expected_proposal_run_id")
    _require_nonblank(reason, "reason")

    def plan(rows):
        job = rows[job_id]
        status = job["status"]

        if status != SCHEDULED:
            raise InvalidTransitionError(
                f"Job '{job_id}' cannot postpone a proposal from status "
                f"'{status}'"
            )

        if job.get("schedule_start_minute") is None or job.get("schedule_end_minute") is None:
            raise InvalidTransitionError(
                f"Job '{job_id}' is 'scheduled' but has no current "
                "proposal to postpone"
            )

        current_run = proposal_run_id_of(job)

        if expected_proposal_run_id != current_run:
            raise StaleProposalError(
                f"Job '{job_id}' proposal is from run {current_run!r}, not "
                f"the expected {expected_proposal_run_id!r}; the proposal "
                "being postponed is not the one that was reviewed"
            )

        block = job["block_candidate"]
        latest_end = int(block.get("latest_end_minute", 1440))

        if not_before_minute < 0 or not_before_minute >= latest_end:
            raise InvalidTransitionError(
                f"Job '{job_id}' cannot be postponed to {selected_date!r}: "
                f"it resolves to minute {not_before_minute} relative to "
                "this deployment's optimization horizon, which only "
                f"covers [0, {latest_end}); choose a date inside the "
                "supported horizon"
            )

        before = _snapshot(job)
        original_start = job.get("schedule_start_minute")
        original_end = job.get("schedule_end_minute")

        withdrawn = _withdrawn(
            replace(JobMutation.unchanged(job), updated_at=at), job, reason
        )
        postponed_block = copy.deepcopy(withdrawn.block_candidate)
        postponed_block["earliest_start_minute"] = int(not_before_minute)
        mutation = replace(withdrawn, block_candidate=postponed_block)

        event = make_event(
            job_id,
            JobEventType.PROPOSAL_POSTPONED,
            actor,
            occurred_at=at,
            reason=reason,
            optimization_run_id=current_run,
            before_state=before,
            after_state=_snapshot(mutation.as_job(job)),
            metadata={
                "transition": "postpone",
                "selected_date": selected_date,
                "not_before_minute": int(not_before_minute),
                "original_proposal_run_id": current_run,
                "original_start_minute": original_start,
                "original_end_minute": original_end,
                "expected_proposal_run_id": expected_proposal_run_id,
                "proposal_digest": proposal_digest,
            },
        )

        return [mutation], [event]

    return plan


def plan_completion(job_id: str, *, actor: Actor, at: str) -> Plan:
    """notified -> completed."""

    def plan(rows):
        job = rows[job_id]
        status = job["status"]

        if status != NOTIFIED:
            raise InvalidTransitionError(
                f"Job '{job_id}' cannot be completed from status '{status}'"
            )

        mutation = replace(JobMutation.unchanged(job), status=COMPLETED, updated_at=at)

        event = make_event(
            job_id,
            JobEventType.JOB_COMPLETED,
            actor,
            occurred_at=at,
            before_state=_snapshot(job),
            after_state=_snapshot(mutation.as_job(job)),
        )

        return [mutation], [event]

    return plan


def plan_schedule_assignment(
    job_id: str,
    start_minute: int,
    end_minute: int,
    *,
    actor: Actor,
    at: str,
) -> Plan:
    """Write a placement directly, bypassing the optimizer.

    Not called by any production path (JobService.set_schedule has no
    HTTP route). The resulting schedule carries no proposal_run_id,
    because no optimization run proposed it.
    """

    def plan(rows):
        job = rows[job_id]
        status = job["status"]

        if status in TERMINAL_STATUSES:
            raise TerminalJobError(
                f"Job '{job_id}' is in terminal status '{status}' and cannot "
                "be rescheduled"
            )

        if status in COMMITTED_STATUSES:
            raise CommittedJobError(
                f"Job '{job_id}' is committed ('{status}'); its placement "
                "cannot be reassigned"
            )

        mutation = replace(
            JobMutation.unchanged(job),
            status=SCHEDULED,
            schedule_start_minute=int(start_minute),
            schedule_end_minute=int(end_minute),
            updated_at=at,
            last_refusal_reason=None,
            block_candidate=_with_placement(
                job["block_candidate"], start_minute, end_minute, None
            ),
        )

        event = make_event(
            job_id,
            JobEventType.SCHEDULE_ASSIGNED,
            actor,
            occurred_at=at,
            before_state=_snapshot(job),
            after_state=_snapshot(mutation.as_job(job)),
            metadata={
                "source": "direct_assignment",
                "start_minute": int(start_minute),
                "end_minute": int(end_minute),
            },
        )

        return [mutation], [event]

    return plan


def rejected_transition_event(
    job: Mapping[str, Any],
    *,
    actor: Actor,
    attempted: str,
    error: BaseException,
    at: str,
) -> JobEvent:
    """Record a refused transition attempt. The job's state is unchanged."""

    state = _snapshot(job)

    return make_event(
        job["job_id"],
        JobEventType.TRANSITION_REJECTED,
        actor,
        occurred_at=at,
        reason=str(error),
        before_state=state,
        after_state=state,
        metadata={
            "attempted_transition": attempted,
            "error_type": type(error).__name__,
        },
    )


def creation_events(
    job: Mapping[str, Any],
    *,
    reporter: Actor,
    scorer: Actor,
    at: str,
) -> List[JobEvent]:
    """JOB_CREATED by the reporter, then JOB_SCORED by the system.

    Scoring runs synchronously inside report intake, but it is an
    automated decision, so it is recorded under the scorer's SYSTEM
    identity - never the reporter's.
    """

    block = job["block_candidate"]
    metadata = block.get("metadata") or {}
    state = _snapshot(job)

    created = make_event(
        job["job_id"],
        JobEventType.JOB_CREATED,
        reporter,
        occurred_at=at,
        before_state=None,
        after_state=state,
        metadata={
            "track_id": job["track_id"],
            "section_id": block.get("section_id"),
            "work_type": job["work_type"],
            "distance_start": job["distance_start"],
            "distance_end": job["distance_end"],
            "workers_min": job["workers_min"],
            "workers_max": job["workers_max"],
            "description": job["description"],
            "duration_minutes": block.get("duration_minutes"),
            "asset_id": block.get("asset_id"),
        },
    )

    scored = make_event(
        job["job_id"],
        JobEventType.JOB_SCORED,
        scorer,
        occurred_at=at,
        before_state=state,
        after_state=state,
        metadata={
            "priority_score": job["priority_score"],
            "risk_score": job["risk_score"],
            "scoring_explanation": metadata.get("scoring_explanation", []),
            "scoring_features": metadata.get("scoring_features", {}),
            "scorer": "backend.app.ml.scorer.score_block",
            "scoring_model_type": metadata.get("scoring_model_type"),
            "scoring_model_version": metadata.get("scoring_model_version"),
            "reported_severity": metadata.get("reported_severity"),
        },
    )

    return [created, scored]


__all__ = [
    "ALLOWED_TRANSITIONS",
    "COMMITTED_STATUSES",
    "CommittedJobError",
    "CommittedStateIntegrityError",
    "ConcurrentJobModificationError",
    "InvalidTransitionError",
    "JobMutation",
    "OUTCOME_ERROR",
    "OUTCOME_NOT_RUN",
    "OptimizationAttempt",
    "PROPOSAL_RUN_ID_KEY",
    "Plan",
    "StaleProposalError",
    "TERMINAL_STATUSES",
    "TerminalJobError",
    "assert_committed_state_consistent",
    "committed_state_problems",
    "creation_events",
    "plan_commit",
    "plan_completion",
    "plan_optimization_failure",
    "plan_optimization_outcome",
    "plan_postpone",
    "plan_reject",
    "plan_schedule_assignment",
    "proposal_run_id_of",
    "protect_committed_and_terminal_state",
    "rejected_transition_event",
    "validate_mutation",
]
