"""Job lifecycle rules: transitions, protected state, and their events (Slice 1).

This module is pure: no I/O, no locking, no persistence. It decides
WHAT a transition does to a job and WHICH events describe it.
backend.app.jobs.repository decides HOW that is written - atomically,
with the events in the same transaction as the state change. The split
is what lets a later authorization or hash-chain slice wrap the write
path without re-deriving the rules.

STATES
    reported     -> no current proposal; eligible for optimization
    scheduled    -> has a CURRENT, uncommitted proposal
    notified     -> committed/pinned; the placement is protected
    in_progress  -> committed/pinned; field work on it has started
                    (Sprint 3 Slice 5)
    completed    -> terminal

EXECUTION TOKENS (Sprint 3 Slice 5)
    Entering in_progress, completing an in_progress job, and releasing an
    in_progress job back to reported are permitted ONLY for a JobMutation
    carrying the matching ExecutionTransition, and JobRepository.mutate_jobs
    additionally requires that token to be accompanied by exactly one
    matching EXECUTION_* event (validate_execution_events). The permission
    lives on the mutation, never on a status pair: the history-less
    repository primitives (update_status, update_schedule) build no
    JobMutation, so they structurally cannot start, complete or release
    execution. EVERY entry into completed requires a COMPLETE token from
    in_progress (Slice 5 Step 4 retired the legacy direct
    notified -> completed completion and its JOB_COMPLETED event).

    The only plans that set a token are plan_execution_start,
    plan_execution_complete and plan_execution_not_completed; the evidence
    they record and the execution history derived from their events live
    in backend.app.jobs.execution.

AUTHORITY RELEASE (Sprint 3 Slice 6)
    notified -> reported closes the one remaining lifecycle hole: an
    already approved (committed) block whose execution cannot begin -
    possession not granted, crew or safety restriction, authority
    cancellation before START. It is gated exactly as an execution
    transition is, by its own token (AuthorityRelease) carried on the
    JobMutation and cross-checked against exactly one BLOCK_RELEASED
    event (validate_release_events), so the history-less primitives
    structurally cannot perform it either. plan_release is the only plan
    that sets it.

    Release, reject, postpone and not-completed are four DIFFERENT
    operations and are never collapsed: reject and postpone act on a
    'scheduled', uncommitted proposal; release withdraws a commitment
    before work starts; EXECUTION_NOT_COMPLETED reports work that
    started and then failed. No new JobStatus exists for any of them -
    all four return the job to 'reported', and their events and reasons
    are what tell them apart.

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
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from contracts import DEFAULT_HORIZON_START, BlockStatus

from backend.app.identity.actor import OPTIMIZER, Actor, ActorKind, ActorRole
from backend.app.jobs.events import (
    JobEvent,
    JobEventType,
    JobStateSnapshot,
    make_event,
)
from backend.app.jobs.execution import (
    EXECUTION_ID_KEY,
    EvidenceItem,
    EvidencePhase,
    EvidenceValidationError,
    ExecutionIntegrityError,
    ExecutionRecord,
    ExecutionStatus,
    canonical_observed_at,
    committed_block_digest,
    is_execution_id,
    observed_minute,
    parse_observed_at,
    proposal_id_for,
    record_evidence,
    require_not_in_future,
    validate_evidence_set,
)
from backend.app.jobs.models import JobStatus


REPORTED = JobStatus.REPORTED.value
SCHEDULED = JobStatus.SCHEDULED.value
NOTIFIED = JobStatus.NOTIFIED.value
IN_PROGRESS = JobStatus.IN_PROGRESS.value
COMPLETED = JobStatus.COMPLETED.value

# Statuses from which a job can never return. See
# backend.app.jobs.repository, which re-exports this for existing callers.
TERMINAL_STATUSES = (COMPLETED,)

# Job statuses whose work is committed and must be pinned. in_progress
# work is physically on the track, so it is pinned exactly like notified
# work by every consumer of this tuple (optimization snapshot, solver
# outcome planning, proposal derivation).
COMMITTED_STATUSES = (NOTIFIED, IN_PROGRESS)

# The only lifecycle moves a job may make through the service path.
#
# There is no notified -> completed: work is completed only from
# in_progress, with a COMPLETE execution token (see
# _gate_execution_transition, which enforces this for every write path).
ALLOWED_TRANSITIONS: Dict[str, frozenset] = {
    REPORTED: frozenset({REPORTED, SCHEDULED}),
    SCHEDULED: frozenset({SCHEDULED, REPORTED, NOTIFIED}),
    NOTIFIED: frozenset({NOTIFIED, IN_PROGRESS, REPORTED}),
    IN_PROGRESS: frozenset({IN_PROGRESS, COMPLETED, REPORTED}),
    COMPLETED: frozenset(),
}

# Target statuses a COMMITTED job may move to, as far as the protected
# state guard is concerned. Every entry except the two -> reported moves
# keeps the commitment (block COMMITTED, exact placement). Both releases
# are token-gated and fully withdraw the commitment:
#   in_progress -> reported  ExecutionTransition(NOT_COMPLETED)
#   notified    -> reported  AuthorityRelease (Sprint 3 Slice 6)
_COMMITTED_TARGETS: Dict[str, Tuple[str, ...]] = {
    NOTIFIED: (NOTIFIED, IN_PROGRESS, REPORTED),
    IN_PROGRESS: (IN_PROGRESS, COMPLETED, REPORTED),
}

PROPOSAL_RUN_ID_KEY = "proposal_run_id"
COMMITTED_START_KEY = "committed_start_minute"
COMMITTED_END_KEY = "committed_end_minute"
# EXECUTION_ID_KEY is defined in backend.app.jobs.execution and re-exported.

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


class StaleExecutionError(RuntimeError):
    """The caller acted on an execution that is no longer the job's current one.

    The execution-level counterpart of StaleProposalError: a late outcome
    for an execution that was superseded by a later one.
    """


class ExecutionTokenError(CommittedJobError):
    """An execution-gated transition lacks, misuses or mismatches its token.

    A CommittedJobError subclass so every existing refusal path (409 over
    HTTP, TRANSITION_REJECTED in JobService._transition) treats it as a
    refusal to weaken committed work without new handling.
    """


class ExecutionTransitionKind(str, Enum):
    START = "START"
    COMPLETE = "COMPLETE"
    NOT_COMPLETED = "NOT_COMPLETED"


@dataclass(frozen=True)
class ExecutionTransition:
    """Permission, carried on a JobMutation, for one execution-gated move.

    START          notified    -> in_progress
    COMPLETE       in_progress -> completed
    NOT_COMPLETED  in_progress -> reported (commitment released)
    """

    kind: ExecutionTransitionKind
    execution_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", ExecutionTransitionKind(self.kind))

        if not isinstance(self.execution_id, str) or not self.execution_id.strip():
            raise ValueError("ExecutionTransition requires a non-blank execution_id")


class ReleaseTokenError(CommittedJobError):
    """An authority release lacks, misuses or mismatches its token.

    A CommittedJobError subclass for exactly the reason ExecutionTokenError
    is one: every existing refusal path (409 over HTTP, TRANSITION_REJECTED
    in JobService._transition) already treats "would weaken committed work"
    as a refusal, and an ungated release is precisely that.
    """


@dataclass(frozen=True)
class AuthorityRelease:
    """Permission, carried on a JobMutation, for the Slice 6 release.

    notified -> reported: an already approved (committed) block whose
    execution cannot begin is released back for replanning.

    proposal_run_id is the run whose committed placement is being
    released. It is the release's identity in the same way execution_id
    is an ExecutionTransition's: the guard checks it against the stored
    block's proposal_run_id, and validate_release_events checks it against
    the BLOCK_RELEASED event, so neither a forged token nor a forged event
    can stand alone.
    """

    proposal_run_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.proposal_run_id, str) or not self.proposal_run_id.strip():
            raise ValueError("AuthorityRelease requires a non-blank proposal_run_id")


# The one event type that must accompany an AuthorityRelease token.
RELEASE_EVENT_TYPE = JobEventType.BLOCK_RELEASED

# The BLOCK_RELEASED metadata key naming the released run, cross-checked
# against the token by validate_release_events.
RELEASED_RUN_ID_KEY = "released_proposal_run_id"

# The one event type that must accompany each token kind.
EXECUTION_EVENT_TYPES: Dict[ExecutionTransitionKind, JobEventType] = {
    ExecutionTransitionKind.START: JobEventType.EXECUTION_STARTED,
    ExecutionTransitionKind.COMPLETE: JobEventType.EXECUTION_COMPLETED,
    ExecutionTransitionKind.NOT_COMPLETED: JobEventType.EXECUTION_NOT_COMPLETED,
}


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

    # Sprint 3 Slice 5. Not a column: the permission for an
    # execution-gated transition. Only execution plans set it; every
    # other construction (including unchanged()) leaves it None.
    execution: Optional[ExecutionTransition] = None

    # Sprint 3 Slice 6. Not a column either: the permission for the
    # authority release of a committed, not-yet-executed block. Only
    # plan_release sets it. A mutation may never carry both tokens -
    # _gate_execution_transition refuses that outright.
    release: Optional[AuthorityRelease] = None

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
    An in_progress job must also name its open execution, and a notified
    job (whose work has not started) must not name one.
    """

    if status not in COMMITTED_STATUSES:
        return []

    problems = []
    metadata = block.get("metadata") or {}

    if status == IN_PROGRESS and not metadata.get(EXECUTION_ID_KEY):
        problems.append(
            f"block metadata carries no {EXECUTION_ID_KEY!r} for its open execution"
        )

    if status == NOTIFIED and EXECUTION_ID_KEY in metadata:
        problems.append(
            f"block metadata carries {EXECUTION_ID_KEY!r} "
            f"{metadata.get(EXECUTION_ID_KEY)!r} although no execution has started"
        )

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
    *,
    execution: Optional[ExecutionTransition] = None,
    release: Optional[AuthorityRelease] = None,
) -> None:
    """Invariants EVERY write path must respect, including low-level ones.

    The order of the checks below is load-bearing:

    1. A terminal job is never written - checked before any execution
       token is looked at, so a token can release commitment but never
       terminal state.
    2. A committed job whose STORED state is already inconsistent is never
       written (CommittedStateIntegrityError) - detected, not repaired.
    3. No write may PRODUCE an inconsistent committed job, so a future
       application bug cannot create the state check 2 refuses.
    4. Execution gate (Sprint 3 Slice 5): entering in_progress (only from
       notified), entering completed (only from in_progress) and releasing
       in_progress -> reported each require the matching
       ExecutionTransition token; releasing notified -> reported requires
       an AuthorityRelease naming the stored proposal run (Sprint 3 Slice
       6); a token on any other move, or both tokens at once, is misuse.
       The history-less repository primitives never pass a token, so they
       can do none of these.
    5. A non-committed job has nothing further to protect.
    6. A committed job may only move to the targets in _COMMITTED_TARGETS.
       Every move except the two token-gated releases keeps a COMMITTED,
       is_committed block, its exact placement, its placement metadata and
       its proposal run (and, once started, its execution). A release must
       fully un-commit the block.
    """

    job_id = current["job_id"]
    status = current["status"]

    # 1. Terminal.
    if status in TERMINAL_STATUSES:
        raise TerminalJobError(
            f"Job '{job_id}' is in terminal status '{status}' and cannot "
            "be modified"
        )

    # 2. Stored committed-state integrity.
    assert_committed_state_consistent([current])

    # 3. Resulting committed-state integrity.
    resulting = committed_state_problems(new_status, new_block, tuple(new_schedule))

    if resulting:
        raise CommittedJobError(
            f"Job '{job_id}' would become '{new_status}' with inconsistent "
            f"committed state: {', '.join(resulting)}"
        )

    # 4. Execution / release gate.
    _gate_execution_transition(current, new_status, new_block, execution, release)

    # 5. Non-committed current state.
    if status not in COMMITTED_STATUSES:
        return

    # 6. Committed current state.
    if new_status not in _COMMITTED_TARGETS[status]:
        raise CommittedJobError(
            f"Job '{job_id}' is committed ('{status}') and cannot be moved "
            f"back to '{new_status}'"
        )

    current_metadata = (current.get("block_candidate") or {}).get("metadata") or {}
    new_metadata = new_block.get("metadata") or {}

    if new_status == REPORTED:
        # A token-gated release (the token itself was verified in step 4):
        # in_progress -> reported on a NOT_COMPLETED execution token, or
        # notified -> reported on an AuthorityRelease. Either way the
        # commitment is withdrawn completely, exactly as _withdrawn()
        # withdraws an uncommitted proposal.
        leftovers = [
            key
            for key in (
                COMMITTED_START_KEY,
                COMMITTED_END_KEY,
                PROPOSAL_RUN_ID_KEY,
                EXECUTION_ID_KEY,
            )
            if key in new_metadata
        ]

        if (
            new_block.get("status") != BlockStatus.PLANNED.value
            or new_block.get("is_committed") is not False
            or tuple(new_schedule) != (None, None)
            or leftovers
        ):
            raise CommittedJobError(
                f"Job '{job_id}' releasing its commitment must become an "
                "uncommitted PLANNED block with no placement; got block "
                f"status {new_block.get('status')!r}, is_committed "
                f"{new_block.get('is_committed')!r}, placement "
                f"{tuple(new_schedule)}, leftover placement metadata {leftovers}"
            )

        return

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

    preserved_keys = [COMMITTED_START_KEY, COMMITTED_END_KEY, PROPOSAL_RUN_ID_KEY]

    if status == IN_PROGRESS:
        preserved_keys.append(EXECUTION_ID_KEY)

    for key in preserved_keys:
        if current_metadata.get(key) != new_metadata.get(key):
            raise CommittedJobError(
                f"Job '{job_id}' is committed; its block metadata {key!r} "
                f"cannot change from {current_metadata.get(key)!r} to "
                f"{new_metadata.get(key)!r}"
            )


def _gate_release_transition(
    current: Mapping[str, Any],
    new_status: str,
    release: Optional[AuthorityRelease],
) -> None:
    """The Slice 6 half of step 4: notified -> reported needs an AuthorityRelease.

    Symmetric with the execution gate, and for the same reason: the
    permission lives on the JobMutation, never on a status pair, so the
    history-less repository primitives (update_status, update_schedule),
    which build no JobMutation and therefore pass no token, structurally
    cannot release an approved block. The token must also NAME the stored
    proposal run, so a hand-built plan cannot release a block while
    claiming some other run in the BLOCK_RELEASED event it also forges.
    """

    job_id = current["job_id"]
    status = current["status"]
    is_release = status == NOTIFIED and new_status == REPORTED

    if not is_release:
        if release is not None:
            raise ReleaseTokenError(
                f"Job '{job_id}' move '{status}' -> '{new_status}' is not an "
                "authority release, but carries a release token"
            )
        return

    if release is None:
        raise ReleaseTokenError(
            f"Job '{job_id}' move 'notified' -> 'reported' releases an "
            "approved block and requires an authority release token; none "
            "was supplied"
        )

    current_run = proposal_run_id_of(current)

    if release.proposal_run_id != current_run:
        raise ReleaseTokenError(
            f"Job '{job_id}' release token names run "
            f"{release.proposal_run_id!r}, but the approved block is from "
            f"run {current_run!r}"
        )


def _gate_execution_transition(
    current: Mapping[str, Any],
    new_status: str,
    new_block: Mapping[str, Any],
    execution: Optional[ExecutionTransition],
    release: Optional[AuthorityRelease] = None,
) -> None:
    """Step 4 of protect_committed_and_terminal_state. See its docstring."""

    job_id = current["job_id"]
    status = current["status"]

    if execution is not None and release is not None:
        raise ReleaseTokenError(
            f"Job '{job_id}' mutation carries both a "
            f"{execution.kind.value} execution token and an authority "
            "release token; a move is one or the other, never both"
        )

    # The Slice 6 release is gated first and separately: it is the only
    # move an AuthorityRelease may permit, and an AuthorityRelease is the
    # only thing that permits it.
    _gate_release_transition(current, new_status, release)

    if new_status == IN_PROGRESS and status != IN_PROGRESS:
        required = ExecutionTransitionKind.START
        described = f"'{status}' -> 'in_progress'"
    elif new_status == COMPLETED:
        # Every entry into completed, from ANY status - including the
        # history-less update_status primitive on a non-committed job,
        # which step 5 would otherwise wave through.
        required = ExecutionTransitionKind.COMPLETE
        described = f"'{status}' -> 'completed'"
    elif new_status == REPORTED and status == IN_PROGRESS:
        required = ExecutionTransitionKind.NOT_COMPLETED
        described = "'in_progress' -> 'reported'"
    else:
        if execution is not None:
            raise ExecutionTokenError(
                f"Job '{job_id}' move '{status}' -> '{new_status}' is not an "
                f"execution transition, but carries a {execution.kind.value} "
                "execution token"
            )
        return

    if execution is None:
        raise ExecutionTokenError(
            f"Job '{job_id}' move {described} requires a "
            f"{required.value} execution token; none was supplied"
        )

    if execution.kind is not required:
        raise ExecutionTokenError(
            f"Job '{job_id}' move {described} requires a {required.value} "
            f"execution token, not {execution.kind.value}"
        )

    if required is ExecutionTransitionKind.START:
        if status != NOTIFIED:
            raise ExecutionTokenError(
                f"Job '{job_id}' can only start execution from 'notified', "
                f"not '{status}'"
            )

        named = (new_block.get("metadata") or {}).get(EXECUTION_ID_KEY)
    else:
        if required is ExecutionTransitionKind.COMPLETE and status != IN_PROGRESS:
            raise ExecutionTokenError(
                f"Job '{job_id}' can only complete execution from "
                f"'in_progress', not '{status}'"
            )

        named = ((current.get("block_candidate") or {}).get("metadata") or {}).get(
            EXECUTION_ID_KEY
        )

    if named != execution.execution_id:
        raise ExecutionTokenError(
            f"Job '{job_id}' execution token names execution "
            f"{execution.execution_id!r}, but the block names {named!r}"
        )


def assert_placement_invariants(
    job_id: str,
    status: str,
    start: Optional[int],
    end: Optional[int],
) -> None:
    """A status and its placement must agree, on EVERY write path.

    'reported' means "no current placement" and 'scheduled'/'notified'/
    'in_progress' each mean "this exact window", so a row claiming one
    while carrying the other is a lifecycle invariant violation whichever
    path produced it. Extracted from validate_mutation (unchanged in
    order, wording and error type) so JobRepository.update_status - the
    history-less primitive, which builds no JobMutation and so never
    reaches validate_mutation - is held to the same invariant instead of
    being able to strand a live window on a 'reported' job.
    """

    if status == REPORTED and (start is not None or end is not None):
        raise InvalidTransitionError(
            f"Job '{job_id}' would be 'reported' while still "
            "carrying a proposed window"
        )

    if status in (SCHEDULED,) + COMMITTED_STATUSES and (
        start is None or end is None or end <= start
    ):
        raise InvalidTransitionError(
            f"Job '{job_id}' would be '{status}' "
            "without a valid placement"
        )


def validate_mutation(current: Mapping[str, Any], mutation: JobMutation) -> None:
    """Full service-path validation: protected state plus the transition graph."""

    protect_committed_and_terminal_state(
        current,
        mutation.status,
        mutation.block_candidate,
        (mutation.schedule_start_minute, mutation.schedule_end_minute),
        execution=mutation.execution,
        release=mutation.release,
    )

    allowed = ALLOWED_TRANSITIONS.get(current["status"])

    if allowed is None or mutation.status not in allowed:
        raise InvalidTransitionError(
            f"Job '{current['job_id']}' cannot move from "
            f"'{current['status']}' to '{mutation.status}'"
        )

    assert_placement_invariants(
        current["job_id"],
        mutation.status,
        mutation.schedule_start_minute,
        mutation.schedule_end_minute,
    )


def validate_execution_events(
    mutations: Sequence[JobMutation],
    events: Sequence[JobEvent],
) -> None:
    """Every execution token is backed by exactly one matching event, and vice versa.

    Pure. Defence in depth against a buggy planner (Sprint 3 Slice 5):

    - a mutation carrying an ExecutionTransition must be accompanied, in
      the SAME plan, by exactly one event for that job whose type is the
      token kind's EXECUTION_* type and whose metadata.execution_id is the
      token's execution_id;
    - an EXECUTION_* event may only be written alongside the token-carrying
      mutation it describes, so history can never claim an execution
      transition the job row did not make;
    - an EXECUTION_COMPLETED event must carry non-empty after-work
      evidence. plan_execution_complete already validates minimum=1
      before it builds the event, so no production path can reach this;
      it is here for the same reason as every other check in this
      function - a buggy or hand-built planner must not be able to record
      a completion with no evidence behind it (Slice 5 audit F1);
    - JOB_COMPLETED, the retired legacy completion event, is never written
      (it stays readable in stored history only).

    Raises ExecutionTokenError; JobRepository.mutate_jobs calls this
    inside its transaction, so a refusal writes neither rows nor events.
    """

    for event in events:
        if event.event_type is JobEventType.JOB_COMPLETED:
            raise ExecutionTokenError(
                f"JOB_COMPLETED event {event.event_id!r} for job "
                f"'{event.job_id}' is a retired legacy event; completion is "
                "recorded only as EXECUTION_COMPLETED"
            )

    execution_types = frozenset(EXECUTION_EVENT_TYPES.values())
    tokens = {
        mutation.job_id: mutation.execution
        for mutation in mutations
        if mutation.execution is not None
    }

    for job_id, token in tokens.items():
        expected_type = EXECUTION_EVENT_TYPES[token.kind]
        matching = [
            event
            for event in events
            if event.job_id == job_id
            and event.event_type is expected_type
            and event.metadata.get(EXECUTION_ID_KEY) == token.execution_id
        ]

        if len(matching) != 1:
            raise ExecutionTokenError(
                f"Job '{job_id}' carries a {token.kind.value} execution token "
                f"for execution {token.execution_id!r}, which requires exactly "
                f"one {expected_type.value} event naming it; found {len(matching)}"
            )

    for event in events:
        if event.event_type not in execution_types:
            continue

        token = tokens.get(event.job_id)

        if (
            token is None
            or EXECUTION_EVENT_TYPES[token.kind] is not event.event_type
            or event.metadata.get(EXECUTION_ID_KEY) != token.execution_id
        ):
            raise ExecutionTokenError(
                f"{event.event_type.value} event {event.event_id!r} for job "
                f"'{event.job_id}' is not backed by a matching execution "
                "token on that job's mutation"
            )

        if event.event_type is JobEventType.EXECUTION_COMPLETED and not (
            event.metadata.get("after_work_evidence") or []
        ):
            raise ExecutionTokenError(
                f"EXECUTION_COMPLETED event {event.event_id!r} for job "
                f"'{event.job_id}' records no after-work evidence; work is "
                "never completed without it"
            )


def validate_release_events(
    mutations: Sequence[JobMutation],
    events: Sequence[JobEvent],
) -> None:
    """Every AuthorityRelease is backed by exactly one BLOCK_RELEASED event, and vice versa.

    Pure, and the exact counterpart of validate_execution_events for the
    Slice 6 release (see that function's docstring for the reasoning):

    - a mutation carrying an AuthorityRelease must be accompanied, in the
      SAME plan, by exactly one BLOCK_RELEASED event for that job whose
      metadata RELEASED_RUN_ID_KEY is the token's proposal_run_id;
    - a BLOCK_RELEASED event may only be written alongside the
      token-carrying mutation it describes, so history can never claim a
      release the job row did not make - nor omit one it did.

    Raises ReleaseTokenError; JobRepository.mutate_jobs calls this inside
    its transaction, so a refusal writes neither rows nor events.
    """

    tokens = {
        mutation.job_id: mutation.release
        for mutation in mutations
        if mutation.release is not None
    }

    for job_id, token in tokens.items():
        matching = [
            event
            for event in events
            if event.job_id == job_id
            and event.event_type is RELEASE_EVENT_TYPE
            and event.metadata.get(RELEASED_RUN_ID_KEY) == token.proposal_run_id
        ]

        if len(matching) != 1:
            raise ReleaseTokenError(
                f"Job '{job_id}' carries an authority release token for run "
                f"{token.proposal_run_id!r}, which requires exactly one "
                f"{RELEASE_EVENT_TYPE.value} event naming it; found "
                f"{len(matching)}"
            )

    for event in events:
        if event.event_type is not RELEASE_EVENT_TYPE:
            continue

        token = tokens.get(event.job_id)

        if (
            token is None
            or event.metadata.get(RELEASED_RUN_ID_KEY) != token.proposal_run_id
        ):
            raise ReleaseTokenError(
                f"{RELEASE_EVENT_TYPE.value} event {event.event_id!r} for job "
                f"'{event.job_id}' is not backed by a matching authority "
                "release token on that job's mutation"
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

    # execution_id is only ever present on an in_progress block, so this
    # changes nothing for the scheduled-job withdrawals (reject, postpone,
    # solver refusal, optimization failure); it completes the release of
    # a not-completed execution.
    for key in (
        COMMITTED_START_KEY,
        COMMITTED_END_KEY,
        PROPOSAL_RUN_ID_KEY,
        EXECUTION_ID_KEY,
    ):
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


def _raise_not_before(
    block: Mapping[str, Any],
    not_before_minute: int,
    horizon_minutes: int,
    *,
    never_lower: bool,
) -> Tuple[Dict[str, Any], int, int, int]:
    """A replanning window: a not-before and a widened latest end.

    Shared by plan_postpone and plan_execution_not_completed. Returns
    (new_block, earliest_start_minute, original_latest_end_minute,
    widened_latest_end_minute); the given block is not modified.

    never_lower=False ASSIGNS earliest_start_minute = not_before_minute
    (postpone: the authority chose the date). never_lower=True takes
    max(stored earliest_start_minute, not_before_minute) (execution not
    completed: a prior postponement's not-before is never lowered).

    latest_end_minute always becomes max(current, horizon_minutes) - the
    Slice 4 Step 3 widening, see plan_postpone's WINDOW WIDENING note.

    No admissibility check lives here: plan_postpone refuses a not-before
    outside the horizon itself, while a not-completed release is never
    refused or clamped because of the horizon.
    """

    updated = copy.deepcopy(dict(block))

    if never_lower:
        earliest = max(int(updated.get("earliest_start_minute", 0)), int(not_before_minute))
    else:
        earliest = int(not_before_minute)

    updated["earliest_start_minute"] = earliest

    # Widen, never shrink - see plan_postpone's WINDOW WIDENING note.
    original_latest_end = int(updated.get("latest_end_minute", 1440))
    widened_latest_end = max(original_latest_end, int(horizon_minutes))
    updated["latest_end_minute"] = widened_latest_end

    return updated, earliest, original_latest_end, widened_latest_end


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
    horizon_minutes: int,
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

    horizon_minutes (Slice 4 Step 3) is the caller's DEPLOYMENT horizon -
    JobService.postpone_proposal passes its own self.horizon_minutes -
    not the job's own stored block.latest_end_minute. Before Step 3
    those two values always happened to agree (every job's
    latest_end_minute was set to the same 1440 constant at creation), so
    reading the stored field looked like reading the deployment horizon
    but was not actually doing so: a job whose stored latest_end_minute
    had drifted from the CURRENT deployment horizon - the exact
    situation Step 3's own widening below creates - would otherwise be
    checked against its own stale value instead of what this call is
    actually authorized to offer.

    Fails closed (InvalidTransitionError) if not_before_minute falls
    outside [0, horizon_minutes): a postponement can never silently
    create schedulable availability beyond what this deployment's
    optimization horizon supports, and a "postpone into the past" is
    rejected the same way a "postpone beyond the horizon" is - both are
    an invalid target, not a stale proposal.

    WINDOW WIDENING (Slice 4 Step 3). A valid postponement raises
    earliest_start_minute to not_before_minute AND widens
    latest_end_minute to max(current latest_end_minute, horizon_minutes).
    Without this, a job postponed close to (or past) its OLD
    latest_end_minute would satisfy the admissibility check above yet
    still have latest_start = latest_end - duration < earliest_start
    once the widened horizon is in effect, so the solver's own
    _clamp_window (backend.app.optimizer.solver) would mark it
    window_infeasible on the very next optimization - a postponed job
    silently and permanently unschedulable, which is exactly the defect
    this step exists to close (SLICE4_MULTIDAY_SCHEDULING_DESIGN.md
    Sec.8.2). The widening only ever RAISES latest_end_minute (max, never
    a plain assignment): a job whose stored window is already wider than
    the current deployment horizon - e.g. from an earlier postponement
    made under a wider horizon - must not be narrowed by a later one.

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

        if not_before_minute < 0 or not_before_minute >= horizon_minutes:
            raise InvalidTransitionError(
                f"Job '{job_id}' cannot be postponed to {selected_date!r}: "
                f"it resolves to minute {not_before_minute} relative to "
                "this deployment's optimization horizon, which only "
                f"covers [0, {horizon_minutes}); choose a date inside the "
                "supported horizon"
            )

        before = _snapshot(job)
        original_start = job.get("schedule_start_minute")
        original_end = job.get("schedule_end_minute")

        withdrawn = _withdrawn(
            replace(JobMutation.unchanged(job), updated_at=at), job, reason
        )
        postponed_block, _, original_latest_end, widened_latest_end = _raise_not_before(
            withdrawn.block_candidate,
            not_before_minute,
            horizon_minutes,
            never_lower=False,
        )

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
                "original_latest_end_minute": original_latest_end,
                "widened_latest_end_minute": widened_latest_end,
            },
        )

        return [mutation], [event]

    return plan


# ----------------------------------------------------------------------
# Authority release of a committed block (Sprint 3 Slice 6)
# ----------------------------------------------------------------------


def plan_release(
    job_id: str,
    *,
    actor: Actor,
    at: str,
    reason: str,
    expected_proposal_run_id: str,
    horizon_minutes: int,
) -> Plan:
    """notified -> reported: release an APPROVED block whose execution cannot begin.

    WHICH HOLE THIS CLOSES
        Approval commits a block. Until Slice 6 the only way out of
        'notified' was START, into execution: reject and postpone act on
        a 'scheduled', UNCOMMITTED proposal, and EXECUTION_NOT_COMPLETED
        requires an execution that actually started. A committed block
        whose possession was never granted, whose crew or safety
        clearance fell through, or which the authority cancelled before
        START, had no valid path back. This is that path, and the ONLY
        one: there is deliberately no possession-denied, crew-unavailable
        or cancelled status - the mandatory reason says which it was.

    THE FOUR ARE NOT INTERCHANGEABLE
        reject         the proposal was refused before commitment
        postpone       the proposal was not accepted for THIS placement
        release        the block was committed, but cannot proceed (here)
        not-completed  execution started and then failed

    WHAT IT DOES
        Through _withdrawn() - the same "clear placement, return to
        reported" mutation a solver refusal, a rejection, a postponement
        and a not-completed release all apply - the job becomes
        'reported' with no placement, its block PLANNED / is_committed
        False, and committed_start/end, proposal_run_id and (defensively;
        a notified job never carries one) execution_id removed. reason
        becomes last_refusal_reason, exactly as for a rejection.

        The released commitment survives in history only: BLOCK_PROPOSED,
        BLOCK_COMMITTED, this BLOCK_RELEASED event and the run's
        optimization_runs row are untouched. Nothing here fabricates a
        replacement proposal or triggers an optimization; the job simply
        becomes eligible for the next one, which mints its own run.

    REPLANNING WINDOW
        earliest_start_minute = max(stored earliest_start_minute,
                                    released_end_minute)
        via the shared _raise_not_before(), so the window whose
        possession has just collapsed is never simply offered back and a
        prior postponement's not-before is never lowered. As for a
        not-completed release it is NOT clamped: at or beyond
        horizon_minutes the next optimization honestly refuses the job as
        window-infeasible rather than inventing availability.
        latest_end_minute = max(current, horizon_minutes) - the Slice 4
        widening, shared with plan_postpone.

    expected_proposal_run_id and reason are both mandatory, for the same
    reason they are on plan_reject: an authority decision must name the
    exact commitment it releases, and say why.
    """

    _require_nonblank(expected_proposal_run_id, "expected_proposal_run_id")
    _require_nonblank(reason, "reason")

    def plan(rows):
        job = rows[job_id]

        assert_committed_state_consistent([job])

        status = job["status"]

        # 'scheduled' (reject or postpone instead), 'reported' (nothing
        # to release), 'in_progress' (report not-completed instead) and
        # 'completed' (terminal) are all refused here - by code, not by
        # documentation.
        if status != NOTIFIED:
            raise InvalidTransitionError(
                f"Job '{job_id}' cannot release a committed block from "
                f"status '{status}'; only an approved ('notified') block "
                "that has not started execution can be released"
            )

        _require_identified_human(actor, job_id)

        current_run = proposal_run_id_of(job)

        if expected_proposal_run_id != current_run:
            raise StaleProposalError(
                f"Job '{job_id}' approved block is from run {current_run!r}, "
                f"not the expected {expected_proposal_run_id!r}; the block "
                "being released is not the one that was approved"
            )

        before = _snapshot(job)
        released_start = job["schedule_start_minute"]
        released_end = job["schedule_end_minute"]
        previous_earliest = int(
            (job.get("block_candidate") or {}).get("earliest_start_minute", 0)
        )

        withdrawn = _withdrawn(
            replace(JobMutation.unchanged(job), updated_at=at), job, reason
        )
        block, not_before, original_latest_end, widened_latest_end = _raise_not_before(
            withdrawn.block_candidate,
            int(released_end),
            horizon_minutes,
            never_lower=True,
        )

        mutation = replace(
            withdrawn,
            block_candidate=block,
            release=AuthorityRelease(current_run),
        )

        event = make_event(
            job_id,
            JobEventType.BLOCK_RELEASED,
            actor,
            occurred_at=at,
            reason=reason,
            optimization_run_id=current_run,
            before_state=before,
            after_state=_snapshot(mutation.as_job(job)),
            metadata={
                "transition": "release",
                "optimization_run_id": current_run,
                "proposal_id": proposal_id_for(current_run, job_id),
                "expected_proposal_run_id": expected_proposal_run_id,
                RELEASED_RUN_ID_KEY: current_run,
                "released_start_minute": released_start,
                "released_end_minute": released_end,
                "track_id": job["track_id"],
                "section_id": (job.get("block_candidate") or {}).get("section_id"),
                "previous_earliest_start_minute": previous_earliest,
                "not_before_minute": not_before,
                "original_latest_end_minute": original_latest_end,
                "widened_latest_end_minute": widened_latest_end,
            },
        )

        return [mutation], [event]

    return plan


# ----------------------------------------------------------------------
# Field execution (Sprint 3 Slice 5)
# ----------------------------------------------------------------------


def _require_identified_human(actor: Actor, job_id: str) -> None:
    """Accountability, not RBAC: an execution transition needs a named person."""

    if (
        not isinstance(actor, Actor)
        or actor.kind is not ActorKind.HUMAN
        or actor.role is ActorRole.UNIDENTIFIED
    ):
        raise InvalidTransitionError(
            f"Job '{job_id}' execution transitions require an identified "
            f"human actor; got {getattr(actor, 'actor_id', actor)!r}"
        )


def _recording_moment(at: str):
    """The recording time `at` (canonical event timestamp) as an aware datetime.

    This is the domain "now" for the future-skew rule: an observation may
    not claim to lie more than MAX_OBSERVATION_FUTURE_SKEW after the moment
    it is recorded. The service derives `at` from its injectable clock.
    """

    return parse_observed_at(at, "at")


def _observations(check: Callable[[], Any]) -> Any:
    """Run observation/evidence validation, refusing as InvalidTransitionError."""

    try:
        return check()
    except EvidenceValidationError as exc:
        raise InvalidTransitionError(str(exc)) from exc


def _execution_id_of(job: Mapping[str, Any]) -> Optional[str]:
    block = job.get("block_candidate") or {}
    return (block.get("metadata") or {}).get(EXECUTION_ID_KEY)


def _open_execution_checks(
    job: Mapping[str, Any],
    execution_id: str,
    execution: ExecutionRecord,
    corridor_id: str,
    transition: str,
) -> str:
    """Shared stale / identity / digest checks for an execution outcome.

    Returns the digest recomputed from the current committed block.
    Order: integrity of the stored row, status, the caller's execution is
    still the row's current one (stale), the supplied record really is
    that open execution, and the committed block is unchanged since start.
    """

    job_id = job["job_id"]

    assert_committed_state_consistent([job])

    if job["status"] != IN_PROGRESS:
        raise InvalidTransitionError(
            f"Job '{job_id}' cannot {transition} from status '{job['status']}'; "
            "execution has not started"
        )

    current_execution = _execution_id_of(job)

    if execution_id != current_execution:
        raise StaleExecutionError(
            f"Job '{job_id}' is executing {current_execution!r}, not "
            f"{execution_id!r}; the execution being reported is not the "
            "current one"
        )

    run_id = proposal_run_id_of(job)

    if (
        not isinstance(execution, ExecutionRecord)
        or execution.job_id != job_id
        or execution.execution_id != execution_id
        or execution.status is not ExecutionStatus.STARTED
        or execution.optimization_run_id != run_id
        or execution.proposal_id != proposal_id_for(run_id, job_id)
        or (execution.planned_start_minute, execution.planned_end_minute)
        != (job["schedule_start_minute"], job["schedule_end_minute"])
    ):
        raise ExecutionIntegrityError(
            f"Job '{job_id}' open execution {execution_id!r} on run {run_id!r} "
            "does not match the execution record derived from its history",
            job_id,
        )

    digest = committed_block_digest(
        job, corridor_id=corridor_id, optimization_run_id=run_id
    )

    if digest != execution.committed_block_digest:
        raise CommittedStateIntegrityError(
            f"Job '{job_id}' committed block digest {digest} differs from the "
            f"digest {execution.committed_block_digest} captured when execution "
            f"{execution_id!r} started; the approved block changed during "
            "execution and was not modified. Explicit reconciliation is required.",
            [job_id],
        )

    return digest


def plan_execution_start(
    job_id: str,
    *,
    actor: Actor,
    at: str,
    execution_id: str,
    expected_proposal_run_id: str,
    actual_start_at: str,
    before_work_evidence: Sequence[EvidenceItem],
    corridor_id: str,
    previous_executions: Sequence[ExecutionRecord] = (),
    horizon_start: str = DEFAULT_HORIZON_START,
) -> Plan:
    """notified -> in_progress: field work on the approved block has started.

    execution_id is minted by the caller (backend.app.jobs.execution.
    new_execution_id), never inside the plan. previous_executions is the
    job's execution history (build_execution_records); attempt_number is
    1 + its length. corridor_id is part of the committed block digest.

    The approved block is not touched: status, placement, track, section,
    proposal_run_id and committed_start/end are copied unchanged; the only
    row change is block metadata execution_id. The actual start and the
    before-work evidence are recorded on EXECUTION_STARTED only. Starting
    outside the planned window is recorded as a deviation, not refused.
    """

    def plan(rows):
        job = rows[job_id]

        assert_committed_state_consistent([job])

        if job["status"] != NOTIFIED:
            raise InvalidTransitionError(
                f"Job '{job_id}' cannot start execution from status "
                f"'{job['status']}'; only an approved ('notified') block can "
                "be executed"
            )

        _require_identified_human(actor, job_id)
        _require_nonblank(expected_proposal_run_id, "expected_proposal_run_id")

        current_run = proposal_run_id_of(job)

        if expected_proposal_run_id != current_run:
            raise StaleProposalError(
                f"Job '{job_id}' approved block is from run {current_run!r}, "
                f"not the expected {expected_proposal_run_id!r}; the block "
                "being executed is not the one that was approved"
            )

        if not is_execution_id(execution_id):
            raise InvalidTransitionError(
                f"execution_id {execution_id!r} is not of the form EXE-<32 hex>"
            )

        previous = tuple(previous_executions)

        if any(
            record.job_id != job_id
            or record.is_open
            or record.execution_id == execution_id
            or record.optimization_run_id == current_run
            for record in previous
        ):
            raise ExecutionIntegrityError(
                f"Job '{job_id}' is 'notified' on run {current_run!r}, but its "
                "execution history already has an open execution, an execution "
                f"of this run, or execution {execution_id!r}",
                job_id,
            )

        now = _recording_moment(at)

        def observe():
            started = parse_observed_at(actual_start_at, "actual_start_at")
            require_not_in_future(started, now, "actual_start_at")
            evidence = validate_evidence_set(
                before_work_evidence,
                phase=EvidencePhase.BEFORE_WORK,
                now=now,
                minimum=1,
                captured_not_after=started,
            )
            return started, evidence

        started, evidence = _observations(observe)

        planned_start = job["schedule_start_minute"]
        planned_end = job["schedule_end_minute"]
        actual_start_minute = _observations(
            lambda: observed_minute(started, horizon_start, ceil=False)
        )

        block = copy.deepcopy(job["block_candidate"])
        block["metadata"] = {**(block.get("metadata") or {}), EXECUTION_ID_KEY: execution_id}

        mutation = replace(
            JobMutation.unchanged(job),
            status=IN_PROGRESS,
            updated_at=at,
            block_candidate=block,
            execution=ExecutionTransition(ExecutionTransitionKind.START, execution_id),
        )

        event = make_event(
            job_id,
            JobEventType.EXECUTION_STARTED,
            actor,
            occurred_at=at,
            optimization_run_id=current_run,
            before_state=_snapshot(job),
            after_state=_snapshot(mutation.as_job(job)),
            metadata={
                "transition": "execution_start",
                EXECUTION_ID_KEY: execution_id,
                "attempt_number": len(previous) + 1,
                "optimization_run_id": current_run,
                "proposal_id": proposal_id_for(current_run, job_id),
                "expected_proposal_run_id": expected_proposal_run_id,
                "corridor_id": corridor_id,
                "track_id": job["track_id"],
                "section_id": (job.get("block_candidate") or {}).get("section_id"),
                "planned_start_minute": planned_start,
                "planned_end_minute": planned_end,
                "committed_block_digest": committed_block_digest(
                    job, corridor_id=corridor_id, optimization_run_id=current_run
                ),
                "horizon_start": horizon_start,
                "actual_start_at": canonical_observed_at(started),
                "actual_start_minute": actual_start_minute,
                "before_work_evidence": record_evidence(
                    evidence, EvidencePhase.BEFORE_WORK
                ),
                "deviations": {
                    "started_before_planned_start": actual_start_minute < planned_start,
                },
            },
        )

        return [mutation], [event]

    return plan


def _observe_end(actual_end_at, execution: ExecutionRecord, now, horizon_start):
    ended = parse_observed_at(actual_end_at, "actual_end_at")
    require_not_in_future(ended, now, "actual_end_at")

    if ended < execution.actual_start_moment:
        raise EvidenceValidationError(
            f"actual_end_at {canonical_observed_at(ended)} precedes "
            f"actual_start_at {execution.actual_start_at}"
        )

    return ended, observed_minute(ended, horizon_start, ceil=True)


def plan_execution_complete(
    job_id: str,
    *,
    actor: Actor,
    at: str,
    execution_id: str,
    actual_end_at: str,
    after_work_evidence: Sequence[EvidenceItem],
    execution: ExecutionRecord,
    corridor_id: str,
    horizon_start: str = DEFAULT_HORIZON_START,
) -> Plan:
    """in_progress -> completed: field work finished, with after-work evidence.

    execution is the job's open ExecutionRecord (build_execution_records),
    which supplies the start facts the row does not carry: actual start,
    before-work references and the digest captured at start. The committed
    block digest is recomputed from the row and must equal it.

    The block stays COMMITTED, keeps its placement, run and execution_id.
    """

    def plan(rows):
        job = rows[job_id]
        digest = _open_execution_checks(
            job, execution_id, execution, corridor_id, "complete execution"
        )
        _require_identified_human(actor, job_id)

        now = _recording_moment(at)

        def observe():
            ended, minute = _observe_end(actual_end_at, execution, now, horizon_start)
            evidence = validate_evidence_set(
                after_work_evidence,
                phase=EvidencePhase.AFTER_WORK,
                now=now,
                minimum=1,
                captured_not_before=execution.actual_start_moment,
                forbidden_references=[
                    item.evidence_reference for item in execution.before_work_evidence
                ],
            )
            return ended, minute, evidence

        ended, actual_end_minute, evidence = _observations(observe)
        run_id = execution.optimization_run_id

        mutation = replace(
            JobMutation.unchanged(job),
            status=COMPLETED,
            updated_at=at,
            execution=ExecutionTransition(ExecutionTransitionKind.COMPLETE, execution_id),
        )

        event = make_event(
            job_id,
            JobEventType.EXECUTION_COMPLETED,
            actor,
            occurred_at=at,
            optimization_run_id=run_id,
            before_state=_snapshot(job),
            after_state=_snapshot(mutation.as_job(job)),
            metadata={
                "transition": "execution_complete",
                EXECUTION_ID_KEY: execution_id,
                "optimization_run_id": run_id,
                "proposal_id": execution.proposal_id,
                "committed_block_digest": digest,
                "actual_end_at": canonical_observed_at(ended),
                "actual_end_minute": actual_end_minute,
                "after_work_evidence": record_evidence(
                    evidence, EvidencePhase.AFTER_WORK
                ),
                "deviations": {
                    "started_before_planned_start": (
                        execution.deviations.started_before_planned_start
                    ),
                    "ended_after_planned_end": (
                        actual_end_minute > execution.planned_end_minute
                    ),
                },
            },
        )

        return [mutation], [event]

    return plan


def plan_execution_not_completed(
    job_id: str,
    *,
    actor: Actor,
    at: str,
    execution_id: str,
    actual_end_at: str,
    reason: str,
    execution: ExecutionRecord,
    corridor_id: str,
    horizon_minutes: int,
    failure_evidence: Sequence[EvidenceItem] = (),
    horizon_start: str = DEFAULT_HORIZON_START,
) -> Plan:
    """in_progress -> reported: the work was not completed; release for replanning.

    The committed block is RELEASED, never rewritten into a new proposal:
    through _withdrawn() the job becomes 'reported' with no placement, the
    block PLANNED / is_committed False, and committed_start/end,
    proposal_run_id and execution_id are removed. The approved placement
    survives only in history - BLOCK_PROPOSED, BLOCK_COMMITTED,
    EXECUTION_STARTED, this EXECUTION_NOT_COMPLETED event and the run's
    optimization_runs row.

    REPLANNING WINDOW
        earliest_start_minute = max(stored earliest_start_minute,
                                    planned_end_minute, actual_end_minute)
        so the released window is never simply offered again and a prior
        postponement is never lowered. It is NOT clamped: at or beyond
        horizon_minutes, the next optimization honestly refuses the job as
        window-infeasible.
        latest_end_minute = max(current, horizon_minutes) - the Slice 4
        widening shared with plan_postpone (_raise_not_before).
    """

    _require_nonblank(reason, "reason")

    def plan(rows):
        job = rows[job_id]
        digest = _open_execution_checks(
            job, execution_id, execution, corridor_id, "report execution not completed"
        )
        _require_identified_human(actor, job_id)

        now = _recording_moment(at)

        def observe():
            ended, minute = _observe_end(actual_end_at, execution, now, horizon_start)
            evidence = validate_evidence_set(
                failure_evidence,
                phase=EvidencePhase.NOT_COMPLETED,
                now=now,
                minimum=0,
            )
            return ended, minute, evidence

        ended, actual_end_minute, evidence = _observations(observe)

        run_id = proposal_run_id_of(job)
        released_start = job["schedule_start_minute"]
        released_end = job["schedule_end_minute"]
        previous_earliest = int(
            (job.get("block_candidate") or {}).get("earliest_start_minute", 0)
        )

        withdrawn = _withdrawn(
            replace(JobMutation.unchanged(job), updated_at=at), job, reason
        )
        block, not_before, original_latest_end, widened_latest_end = _raise_not_before(
            withdrawn.block_candidate,
            max(int(released_end), actual_end_minute),
            horizon_minutes,
            never_lower=True,
        )

        mutation = replace(
            withdrawn,
            block_candidate=block,
            execution=ExecutionTransition(
                ExecutionTransitionKind.NOT_COMPLETED, execution_id
            ),
        )

        event = make_event(
            job_id,
            JobEventType.EXECUTION_NOT_COMPLETED,
            actor,
            occurred_at=at,
            reason=reason,
            optimization_run_id=run_id,
            before_state=_snapshot(job),
            after_state=_snapshot(mutation.as_job(job)),
            metadata={
                "transition": "execution_not_completed",
                EXECUTION_ID_KEY: execution_id,
                "optimization_run_id": run_id,
                "proposal_id": execution.proposal_id,
                "committed_block_digest": digest,
                "actual_end_at": canonical_observed_at(ended),
                "actual_end_minute": actual_end_minute,
                "failure_evidence": record_evidence(
                    evidence, EvidencePhase.NOT_COMPLETED
                ),
                "released_start_minute": released_start,
                "released_end_minute": released_end,
                "released_proposal_run_id": run_id,
                "previous_earliest_start_minute": previous_earliest,
                "not_before_minute": not_before,
                "original_latest_end_minute": original_latest_end,
                "widened_latest_end_minute": widened_latest_end,
                "deviations": {
                    "started_before_planned_start": (
                        execution.deviations.started_before_planned_start
                    ),
                    "ended_after_planned_end": (
                        actual_end_minute > execution.planned_end_minute
                    ),
                },
            },
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
    "RELEASED_RUN_ID_KEY",
    "RELEASE_EVENT_TYPE",
    "AuthorityRelease",
    "ReleaseTokenError",
    "CommittedJobError",
    "CommittedStateIntegrityError",
    "ConcurrentJobModificationError",
    "EXECUTION_EVENT_TYPES",
    "EXECUTION_ID_KEY",
    "ExecutionTokenError",
    "ExecutionTransition",
    "ExecutionTransitionKind",
    "IN_PROGRESS",
    "InvalidTransitionError",
    "JobMutation",
    "OUTCOME_ERROR",
    "OUTCOME_NOT_RUN",
    "OptimizationAttempt",
    "PROPOSAL_RUN_ID_KEY",
    "Plan",
    "StaleExecutionError",
    "StaleProposalError",
    "TERMINAL_STATUSES",
    "TerminalJobError",
    "assert_committed_state_consistent",
    "assert_placement_invariants",
    "committed_state_problems",
    "creation_events",
    "plan_commit",
    "plan_execution_complete",
    "plan_execution_not_completed",
    "plan_execution_start",
    "plan_optimization_failure",
    "plan_optimization_outcome",
    "plan_postpone",
    "plan_reject",
    "plan_release",
    "plan_schedule_assignment",
    "proposal_run_id_of",
    "protect_committed_and_terminal_state",
    "rejected_transition_event",
    "validate_execution_events",
    "validate_mutation",
    "validate_release_events",
]
