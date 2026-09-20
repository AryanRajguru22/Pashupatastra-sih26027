"""JobObligation: who currently owes an action on a job, since when, and
by when (Sprint 3 Slice 9).

WHAT THIS IS
    A read-time, DERIVED representation, in exactly the sense
    backend.app.jobs.proposal.BlockProposal and
    backend.app.jobs.execution.ExecutionRecord already are. There is no
    `job_obligations` table, no obligation row, no `status_entered_at`
    column and no new event type. An obligation is a pure function of

        (the job row) + (its append-only history) + (an SLA policy)
                      + (one moment of evaluation)

    and two evaluations at the same moment over the same history produce
    byte-identical results. That reproducibility is what makes a derived
    deadline auditable: a stored deadline can silently disagree with the
    history it came from; a derived one cannot.

    Because derivation is pure, a future sweep/runner can call
    derive_job_obligation and derive_obligations unchanged. No scheduler,
    cron, worker or task queue exists in this repository today, and this
    module does not add one: obligations are evaluated ON READ.

ROLE-ADDRESSED, NEVER PERSON-ADDRESSED
    `owed_role` is an ActorRole - AUTHORITY, WORKER or ENGINEER - and
    never an individual. There is no user table, no authority directory,
    no contact detail and no job-to-person assignment anywhere in this
    repository (and this slice adds none), so an obligation that named a
    person would be fiction. An obligation says "AUTHORITY owes a
    decision on this job"; it can never say "AUTHORITY-017 failed to
    answer". Escalating against a named individual requires
    authentication, which does not exist.

TIME NEVER CHANGES THE LIFECYCLE
    Nothing in this module writes. It appends no event, performs no
    transition, and touches no row. An obligation going OVERDUE or
    ESCALATED_L3 approves nothing, rejects nothing, releases nothing,
    completes nothing and cancels nothing. This mirrors
    ExecutionDeviations - "recorded, never enforced" - which is the
    existing precedent for exactly this posture. A human still decides.

MOOTNESS IS SAFETY-CRITICAL
    When the SYSTEM withdrew a proposal (PROPOSAL_INVALIDATED,
    OPTIMIZATION_REFUSED) or the authority released a commitment
    (BLOCK_RELEASED), the obligation ENDED - it was not breached. This
    module reports MOOT (or a replanning obligation), never OVERDUE.
    Reporting a system withdrawal as an authority failure would be a
    false accusation, and it is exactly what a naive "status ==
    'scheduled' AND old" query produces.

HISTORY IS CHECKED, NEVER REPAIRED
    Like build_execution_records, this module refuses incoherent history
    (ObligationIntegrityError, or the ExecutionIntegrityError it lets
    through) rather than guessing. A read endpoint fails closed; it never
    fabricates an obligation over state whose meaning is unknown.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Iterable, Mapping, Optional, Sequence

from backend.app.identity.actor import ActorRole
from backend.app.jobs.events import JobEvent, JobEventType, event_timestamp
from backend.app.jobs.execution import (
    ExecutionRecord,
    ExecutionStatus,
    build_execution_records,
)
from backend.app.jobs.lifecycle import proposal_run_id_of
from backend.app.jobs.models import JobStatus
from backend.app.jobs.sla_policy import (
    ASSUMED_DEMO_SLA_POLICY,
    SlaPolicy,
    SlaPolicySet,
    SlaTiming,
)


# The events that place a proposal in front of an authority. Identical to
# backend.app.jobs.proposal._PLACEMENT_EVENT_TYPES - the approval clock
# starts exactly where BlockProposal.generated_at already does, so the
# deadline can never be anchored to a different moment than the proposal
# a reviewer was shown.
PLACEMENT_EVENT_TYPES = frozenset(
    {
        JobEventType.BLOCK_PROPOSED,
        JobEventType.BLOCK_REPROPOSED,
        JobEventType.COMMITTED_BLOCK_PRESERVED,
    }
)

# An authority ANSWERED the proposal. Any of these ends the approval
# clock; which one it was is a different question (the history says).
APPROVAL_RESPONSE_EVENT_TYPES = frozenset(
    {
        JobEventType.BLOCK_COMMITTED,
        JobEventType.PROPOSAL_REJECTED,
        JobEventType.PROPOSAL_POSTPONED,
    }
)

# The SYSTEM withdrew the proposal. NOT a response, and NOT a breach -
# see the module docstring.
SYSTEM_WITHDRAWAL_EVENT_TYPES = frozenset(
    {
        JobEventType.PROPOSAL_INVALIDATED,
        JobEventType.OPTIMIZATION_REFUSED,
    }
)

# A committed block ended without its work being completed, leaving the
# job to be planned again. These are the only two events that raise a
# replanning obligation; a rejection or a postponement does not, because
# the authority ANSWERED and the job simply awaits the next run (and a
# postponement's chosen date constrains scheduling, not a human).
REPLANNING_TRIGGER_EVENT_TYPES = frozenset(
    {
        JobEventType.BLOCK_RELEASED,
        JobEventType.EXECUTION_NOT_COMPLETED,
    }
)

# TRANSITION_REJECTED metadata error_type values that mean stored state
# is inconsistent rather than that a caller asked for something invalid.
# A recorded refusal of this kind needs a human to reconcile it.
INTEGRITY_ERROR_TYPES = frozenset(
    {
        "CommittedStateIntegrityError",
        "ExecutionIntegrityError",
    }
)


class ObligationIntegrityError(RuntimeError):
    """The job row and its history do not describe a coherent obligation.

    The same posture as CommittedStateIntegrityError and
    ExecutionIntegrityError: detected, never repaired. Nothing is derived
    from a history whose meaning is unknown, and no obligation is
    invented to fill the gap.
    """

    def __init__(self, message: str, job_id: str):
        super().__init__(message)
        self.job_id = job_id


class ObligationType(str, Enum):
    """What kind of action is owed. See the table in the module docstring."""

    #: Nobody owes anything right now.
    NONE = "NONE"
    #: An authority must decide on the job's current proposal.
    APPROVAL_PENDING = "APPROVAL_PENDING"
    #: Field work on an approved block has not begun.
    EXECUTION_START_PENDING = "EXECUTION_START_PENDING"
    #: Field work has begun and has not concluded either way.
    COMPLETION_PENDING = "COMPLETION_PENDING"
    #: A commitment ended without the work being done; the job needs to
    #: be planned again.
    REPLANNING_PENDING = "REPLANNING_PENDING"


class ObligationState(str, Enum):
    """How the obligation stands against its SLA at the moment of evaluation.

    The ladder WITHIN_SLA -> DUE_SOON -> OVERDUE -> ESCALATED_L1..L3 is
    monotonic in elapsed time and derived purely from the policy. It
    carries NO lifecycle authority whatsoever.

    OVERDUE means "past the deadline, but the policy's first escalation
    threshold has not been reached". Under ASSUMED_DEMO_SLA_POLICY, whose
    first escalation multiplier is 1.0 (L1 begins AT the deadline),
    OVERDUE is therefore never produced - such an obligation reports
    ESCALATED_L1 directly. That is a property of those particular
    assumed numbers, not of this derivation: a policy with a grace period
    before L1 produces OVERDUE. `is_past_due` is the predicate to ask
    when the question is "is this late?", precisely so a caller never has
    to enumerate the escalated states to find out.

    NO_SLA_DEFINED is an honest gap, not a lifecycle state: the
    obligation is genuinely owed, but SlaPolicy defines no allowance for
    its type (today, REPLANNING_PENDING). Reporting WITHIN_SLA would
    claim conformance with a deadline that does not exist. Adding a
    `replanning_sla` to SlaPolicy is the one-line change that retires it.
    """

    #: No obligation exists, so there is nothing to time.
    NONE = "NONE"
    #: The obligation ended because the SYSTEM withdrew the work, or the
    #: authority withdrew its own commitment. Never a failure by anyone.
    MOOT = "MOOT"
    #: Owed, and no allowance is defined for this obligation type.
    NO_SLA_DEFINED = "NO_SLA_DEFINED"

    WITHIN_SLA = "WITHIN_SLA"
    DUE_SOON = "DUE_SOON"
    OVERDUE = "OVERDUE"
    ESCALATED_L1 = "ESCALATED_L1"
    ESCALATED_L2 = "ESCALATED_L2"
    ESCALATED_L3 = "ESCALATED_L3"


_ESCALATED_STATES = (
    ObligationState.ESCALATED_L1,
    ObligationState.ESCALATED_L2,
    ObligationState.ESCALATED_L3,
)

#: Stable machine-readable reasons. A client switches on these; the
#: accompanying prose may be reworded.
class ObligationReason(str, Enum):
    NO_OBLIGATION_TERMINAL = "NO_OBLIGATION_TERMINAL"
    NO_OBLIGATION_AWAITING_OPTIMIZATION = "NO_OBLIGATION_AWAITING_OPTIMIZATION"
    APPROVAL_AWAITING_AUTHORITY = "APPROVAL_AWAITING_AUTHORITY"
    APPROVAL_WITHDRAWN_BY_SYSTEM = "APPROVAL_WITHDRAWN_BY_SYSTEM"
    EXECUTION_AWAITING_START = "EXECUTION_AWAITING_START"
    COMPLETION_AWAITING_OUTCOME = "COMPLETION_AWAITING_OUTCOME"
    REPLANNING_AFTER_RELEASE = "REPLANNING_AFTER_RELEASE"
    REPLANNING_AFTER_NOT_COMPLETED = "REPLANNING_AFTER_NOT_COMPLETED"


class AttentionReason(str, Enum):
    """Why a job needs an engineer to look at it, beyond its own clock.

    Orthogonal to the obligation, not a competing obligation: a notified
    job whose approved block an optimization run could not honour still
    owes EXECUTION_START_PENDING against its own real deadline, AND
    needs a human to reconcile the conflict. Modelling attention as a
    second obligation would force one of those two facts to be dropped.
    """

    COMMITTED_BLOCK_CONFLICT = "COMMITTED_BLOCK_CONFLICT"
    INTEGRITY_REFUSAL_RECORDED = "INTEGRITY_REFUSAL_RECORDED"


@dataclass(frozen=True)
class JobObligation:
    """What one job currently owes, evaluated at one moment. Derived.

    Every field is either read straight from the job row / an event, or
    computed from those plus the policy. Nothing here is stored.

    clock_started_at and due_at are canonical event timestamps (UTC,
    microsecond, explicit +00:00) so they compare lexicographically with
    every occurred_at in job_events, and both are None when no clock
    applies. anchor_event_id names the exact event that started the
    clock, which is what lets an auditor recompute this obligation and
    what makes a notification intent key change after a re-proposal.
    """

    job_id: str
    obligation_type: ObligationType
    state: ObligationState
    owed_role: Optional[ActorRole]

    evaluated_at: str

    clock_started_at: Optional[str] = None
    due_at: Optional[str] = None
    escalation_level: int = 0
    elapsed_seconds: Optional[float] = None
    sla_seconds: Optional[float] = None

    policy_version: str = ""
    policy_assumed: bool = True

    anchor_event_id: Optional[str] = None
    optimization_run_id: Optional[str] = None

    reason_code: ObligationReason = ObligationReason.NO_OBLIGATION_TERMINAL
    reason: str = ""

    attention_required: bool = False
    attention_reason_code: Optional[AttentionReason] = None
    attention_event_id: Optional[str] = None
    attention_role: Optional[ActorRole] = None

    @property
    def is_open(self) -> bool:
        """Someone owes an action right now (moot and none are not open)."""

        return self.obligation_type is not ObligationType.NONE and (
            self.state is not ObligationState.MOOT
        )

    @property
    def is_past_due(self) -> bool:
        """Past its deadline. The predicate for 'which are overdue?'.

        True for OVERDUE and for every ESCALATED_Ln - see
        ObligationState for why asking for the OVERDUE state alone is
        not the same question.
        """

        return self.state is ObligationState.OVERDUE or self.state in _ESCALATED_STATES

    @property
    def is_due_soon(self) -> bool:
        return self.state is ObligationState.DUE_SOON

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "obligation_type": self.obligation_type.value,
            "state": self.state.value,
            "owed_role": None if self.owed_role is None else self.owed_role.value,
            "evaluated_at": self.evaluated_at,
            "clock_started_at": self.clock_started_at,
            "due_at": self.due_at,
            "escalation_level": self.escalation_level,
            "elapsed_seconds": self.elapsed_seconds,
            "sla_seconds": self.sla_seconds,
            "policy_version": self.policy_version,
            "policy_assumed": self.policy_assumed,
            "anchor_event_id": self.anchor_event_id,
            "optimization_run_id": self.optimization_run_id,
            "reason_code": self.reason_code.value,
            "reason": self.reason,
            "is_open": self.is_open,
            "is_past_due": self.is_past_due,
            "attention_required": self.attention_required,
            "attention_reason_code": (
                None
                if self.attention_reason_code is None
                else self.attention_reason_code.value
            ),
            "attention_event_id": self.attention_event_id,
            "attention_role": (
                None if self.attention_role is None else self.attention_role.value
            ),
        }


# ----------------------------------------------------------------------
# Reading history
# ----------------------------------------------------------------------


def _events_of(events: Iterable[Any]) -> list[JobEvent]:
    """Accept StoredJobEvent values (a history read) or bare JobEvent values."""

    unwrapped: list[JobEvent] = []

    for item in events:
        event = getattr(item, "event", item)

        if not isinstance(event, JobEvent):
            raise TypeError(f"expected a JobEvent or StoredJobEvent, got {item!r}")

        unwrapped.append(event)

    return unwrapped


def _last(events: Sequence[JobEvent], types: frozenset) -> Optional[JobEvent]:
    for event in reversed(events):
        if event.event_type in types:
            return event

    return None


def _first(events: Sequence[JobEvent], types: frozenset) -> Optional[JobEvent]:
    for event in events:
        if event.event_type in types:
            return event

    return None


def _index_of(events: Sequence[JobEvent], event: JobEvent) -> int:
    for position, candidate in enumerate(events):
        if candidate.event_id == event.event_id:
            return position

    raise ValueError(f"event {event.event_id!r} is not in this history")


def _latest_placement_for_run(
    job_id: str,
    run_id: str,
    events: Sequence[JobEvent],
) -> JobEvent:
    """The event that placed the CURRENT proposal in front of an authority.

    Matched on the job's own current proposal_run_id, which is what makes
    a superseded proposal incapable of producing a current obligation: a
    re-proposal writes a new run id onto the job, and the old placement
    event stops matching. An overdue approval is therefore always an
    overdue decision on the proposal a reviewer would be shown right now.
    """

    matches = [
        event
        for event in events
        if event.optimization_run_id == run_id
        and event.event_type in PLACEMENT_EVENT_TYPES
    ]

    if not matches:
        raise ObligationIntegrityError(
            f"Job '{job_id}' names optimization run {run_id!r} as its current "
            "proposal, but its history records no placement event for that "
            "run; the job row and its history disagree. Explicit "
            "reconciliation is required.",
            job_id,
        )

    return matches[-1]


def _parse(moment: str) -> datetime:
    """A canonical event timestamp as an aware datetime."""

    return datetime.fromisoformat(moment)


# ----------------------------------------------------------------------
# Attention (orthogonal to the obligation - see AttentionReason)
# ----------------------------------------------------------------------


def _attention(
    events: Sequence[JobEvent],
    since: Optional[JobEvent],
) -> tuple[Optional[AttentionReason], Optional[JobEvent]]:
    """Whether an engineer needs to look at this job, and because of what.

    COMMITTED_BLOCK_CONFLICT is scoped to `since` (the event that created
    the current commitment): a conflict recorded against a commitment
    that has since been released or executed is history, not a live
    condition. Note the conflict event carries the id of the run that
    COULD NOT honour the commitment, not the run that made it, so the
    match is positional rather than by run id.

    An integrity refusal counts only when it is the job's most recent
    event: anything that happened afterwards means the job moved on.
    """

    start = 0 if since is None else _index_of(events, since) + 1
    recent = events[start:]

    conflict = _last(recent, frozenset({JobEventType.COMMITTED_BLOCK_CONFLICT}))

    if conflict is not None:
        return AttentionReason.COMMITTED_BLOCK_CONFLICT, conflict

    if events:
        last = events[-1]

        if (
            last.event_type is JobEventType.TRANSITION_REJECTED
            and last.metadata.get("error_type") in INTEGRITY_ERROR_TYPES
        ):
            return AttentionReason.INTEGRITY_REFUSAL_RECORDED, last

    return None, None


# ----------------------------------------------------------------------
# Building one obligation
# ----------------------------------------------------------------------


def _timed(
    *,
    job_id: str,
    obligation_type: ObligationType,
    owed_role: ActorRole,
    policy: SlaPolicy,
    sla: timedelta,
    anchor: JobEvent,
    evaluated_at: datetime,
    reason_code: ObligationReason,
    reason: str,
    optimization_run_id: Optional[str],
) -> JobObligation:
    """An open obligation with a live clock, timed against `policy`."""

    timing: SlaTiming = policy.timing(
        sla=sla,
        started_at=_parse(anchor.occurred_at),
        evaluated_at=evaluated_at,
    )

    return JobObligation(
        job_id=job_id,
        obligation_type=obligation_type,
        state=_state_of(timing),
        owed_role=owed_role,
        evaluated_at=event_timestamp(evaluated_at),
        clock_started_at=anchor.occurred_at,
        due_at=event_timestamp(timing.due_at),
        escalation_level=timing.escalation_level,
        elapsed_seconds=timing.elapsed.total_seconds(),
        sla_seconds=sla.total_seconds(),
        policy_version=policy.version,
        policy_assumed=policy.assumed,
        anchor_event_id=anchor.event_id,
        optimization_run_id=optimization_run_id,
        reason_code=reason_code,
        reason=reason,
    )


def _state_of(timing: SlaTiming) -> ObligationState:
    """The ladder, from pure timing facts. Monotonic in elapsed time."""

    if timing.escalation_level >= 1:
        return _ESCALATED_STATES[min(timing.escalation_level, 3) - 1]

    if timing.is_past_due:
        return ObligationState.OVERDUE

    if timing.is_due_soon:
        return ObligationState.DUE_SOON

    return ObligationState.WITHIN_SLA


def _untimed(
    *,
    job_id: str,
    obligation_type: ObligationType,
    state: ObligationState,
    owed_role: Optional[ActorRole],
    policy: SlaPolicy,
    evaluated_at: datetime,
    reason_code: ObligationReason,
    reason: str,
    anchor: Optional[JobEvent] = None,
    optimization_run_id: Optional[str] = None,
) -> JobObligation:
    """An obligation with no live clock: none, moot, or no SLA defined."""

    return JobObligation(
        job_id=job_id,
        obligation_type=obligation_type,
        state=state,
        owed_role=owed_role,
        evaluated_at=event_timestamp(evaluated_at),
        clock_started_at=None if anchor is None else anchor.occurred_at,
        due_at=None,
        escalation_level=0,
        elapsed_seconds=None,
        sla_seconds=None,
        policy_version=policy.version,
        policy_assumed=policy.assumed,
        anchor_event_id=None if anchor is None else anchor.event_id,
        optimization_run_id=optimization_run_id,
        reason_code=reason_code,
        reason=reason,
    )


# ----------------------------------------------------------------------
# The four obligations
# ----------------------------------------------------------------------


def _approval(
    job: Mapping[str, Any],
    events: Sequence[JobEvent],
    policy: SlaPolicy,
    evaluated_at: datetime,
) -> tuple[JobObligation, Optional[JobEvent]]:
    """status 'scheduled': an authority owes a decision on the CURRENT proposal.

    'scheduled' already MEANS "has a current, uncommitted proposal
    awaiting a human decision", so no new status is needed to express
    this obligation. The clock starts at the placement event's
    occurred_at - the same moment BlockProposal.generated_at reports -
    and a re-proposal resets it for free, because a new run writes a new
    proposal_run_id onto the job and the derivation follows it.
    """

    job_id = job["job_id"]
    run_id = proposal_run_id_of(job)

    if run_id is None:
        raise ObligationIntegrityError(
            f"Job '{job_id}' is 'scheduled' but carries no optimization_run_id "
            "for its placement; no approval obligation can be derived from a "
            "proposal the job cannot name.",
            job_id,
        )

    anchor = _latest_placement_for_run(job_id, run_id, events)
    after = events[_index_of(events, anchor) + 1 :]

    # Defensive, and deliberately not silent. A response or a withdrawal
    # for the CURRENT run cannot coexist with status 'scheduled' (both
    # change the status and clear or replace the run id), so seeing one
    # means the row and the history disagree.
    settled = [
        event
        for event in after
        if event.optimization_run_id == run_id
        and event.event_type
        in (APPROVAL_RESPONSE_EVENT_TYPES | SYSTEM_WITHDRAWAL_EVENT_TYPES)
    ]

    if settled:
        raise ObligationIntegrityError(
            f"Job '{job_id}' is 'scheduled' on run {run_id!r}, but its history "
            f"already records {settled[-1].event_type.value} for that run. "
            "Explicit reconciliation is required.",
            job_id,
        )

    return (
        _timed(
            job_id=job_id,
            obligation_type=ObligationType.APPROVAL_PENDING,
            owed_role=ActorRole.AUTHORITY,
            policy=policy,
            sla=policy.approval_sla,
            anchor=anchor,
            evaluated_at=evaluated_at,
            reason_code=ObligationReason.APPROVAL_AWAITING_AUTHORITY,
            reason=(
                f"The proposal from optimization run {run_id} is awaiting an "
                "authority decision (approve, reject or postpone)."
            ),
            optimization_run_id=run_id,
        ),
        anchor,
    )


def _execution_start(
    job: Mapping[str, Any],
    events: Sequence[JobEvent],
    policy: SlaPolicy,
    evaluated_at: datetime,
) -> tuple[JobObligation, Optional[JobEvent]]:
    """status 'notified': an approved block whose work has not begun.

    THE ANCHOR, AND WHY IT IS NOT THE PLANNED WINDOW
        The clock starts at BLOCK_COMMITTED.occurred_at - when the
        approval was recorded - and NOT at the block's
        planned_start_minute converted to wall clock.

        The deployment's planning horizon is a hard-coded constant
        (contracts.DEFAULT_HORIZON_START), so every currently-scheduled
        block's planned window already lies in the past. Anchoring on the
        planned window today would mark every approved job instantly
        overdue - a deadline that says nothing about anybody's conduct.

        The planned-window anchor is the operationally more meaningful
        statement and is deferred, not rejected: it becomes correct once
        the horizon tracks real time, which requires timetable data
        covering the live planning window (the Slice 4 coverage gate
        fails closed otherwise). That is a data-infrastructure
        dependency, not a design preference.
    """

    job_id = job["job_id"]
    run_id = proposal_run_id_of(job)

    if run_id is None:
        raise ObligationIntegrityError(
            f"Job '{job_id}' is 'notified' but carries no optimization_run_id "
            "for its committed block.",
            job_id,
        )

    commits = [
        event
        for event in events
        if event.optimization_run_id == run_id
        and event.event_type is JobEventType.BLOCK_COMMITTED
    ]

    if not commits:
        raise ObligationIntegrityError(
            f"Job '{job_id}' is 'notified' on run {run_id!r}, but its history "
            "records no BLOCK_COMMITTED event for that run; the job row and "
            "its history disagree. Explicit reconciliation is required.",
            job_id,
        )

    anchor = commits[-1]

    return (
        _timed(
            job_id=job_id,
            obligation_type=ObligationType.EXECUTION_START_PENDING,
            owed_role=ActorRole.WORKER,
            policy=policy,
            sla=policy.execution_start_sla,
            anchor=anchor,
            evaluated_at=evaluated_at,
            reason_code=ObligationReason.EXECUTION_AWAITING_START,
            reason=(
                f"The block approved under optimization run {run_id} has been "
                "committed and field execution has not started."
            ),
            optimization_run_id=run_id,
        ),
        anchor,
    )


def _completion(
    job: Mapping[str, Any],
    events: Sequence[JobEvent],
    policy: SlaPolicy,
    evaluated_at: datetime,
    records: Sequence[ExecutionRecord],
) -> tuple[JobObligation, Optional[JobEvent]]:
    """status 'in_progress': started work that has not concluded either way.

    Anchored on ExecutionRecord.started_recorded_at - the server's own
    canonical record of when the start was reported - and NOT on
    actual_start_at, which is the worker's claim about observation time.
    A deadline must not be movable by the party it binds. (actual_start_at
    remains the right value for deviation reporting, a different
    question.)

    There is deliberately no separate "evidence pending" state: completion
    is already evidence-gated (Slice 5 Step 4), so an open ExecutionRecord
    IS the outstanding-evidence condition. Satisfied by EXECUTION_COMPLETED
    or answered by EXECUTION_NOT_COMPLETED.
    """

    job_id = job["job_id"]
    open_records = [record for record in records if record.status is ExecutionStatus.STARTED]

    if len(open_records) != 1:
        raise ObligationIntegrityError(
            f"Job '{job_id}' is 'in_progress' but its history rebuilds "
            f"{len(open_records)} open executions; exactly one is required to "
            "derive a completion obligation.",
            job_id,
        )

    record = open_records[0]
    anchor = None

    for event in events:
        if (
            event.event_type is JobEventType.EXECUTION_STARTED
            and event.metadata.get("execution_id") == record.execution_id
        ):
            anchor = event

    if anchor is None or anchor.occurred_at != record.started_recorded_at:
        raise ObligationIntegrityError(
            f"Job '{job_id}' has an open execution {record.execution_id!r} "
            "whose EXECUTION_STARTED event cannot be located in its history.",
            job_id,
        )

    return (
        _timed(
            job_id=job_id,
            obligation_type=ObligationType.COMPLETION_PENDING,
            owed_role=ActorRole.WORKER,
            policy=policy,
            sla=policy.completion_sla,
            anchor=anchor,
            evaluated_at=evaluated_at,
            reason_code=ObligationReason.COMPLETION_AWAITING_OUTCOME,
            reason=(
                f"Execution {record.execution_id} is open: it must be completed "
                "with after-work evidence, or reported not completed with a "
                "reason."
            ),
            optimization_run_id=record.optimization_run_id,
        ),
        anchor,
    )


def _reported(
    job: Mapping[str, Any],
    events: Sequence[JobEvent],
    policy: SlaPolicy,
    evaluated_at: datetime,
) -> tuple[JobObligation, Optional[JobEvent]]:
    """status 'reported': usually nothing is owed by a human.

    'reported' ALONE is not an obligation. A newly reported job is
    waiting for the next optimization run, not for a person, and calling
    that "someone owes an action" would flood an accountability view with
    every report ever filed.

    Two histories are different:

      1. a commitment that ended without the work being done
         (BLOCK_RELEASED, EXECUTION_NOT_COMPLETED) with no placement
         since - the job genuinely needs replanning attention; and
      2. a proposal the SYSTEM withdrew (PROPOSAL_INVALIDATED,
         OPTIMIZATION_REFUSED) - the approval obligation that existed is
         MOOT. Nobody failed to answer it, so it must never be reported
         as overdue, at any evaluation time, ever.

    A rejection or a postponement produces neither: the authority
    answered, and the job simply awaits the next optimization run.
    """

    job_id = job["job_id"]

    placement = _last(events, PLACEMENT_EVENT_TYPES)
    floor = 0 if placement is None else _index_of(events, placement) + 1
    since_placement = events[floor:]

    replanning = _last(since_placement, REPLANNING_TRIGGER_EVENT_TYPES)

    if replanning is not None:
        after_release = ObligationReason.REPLANNING_AFTER_RELEASE
        after_failure = ObligationReason.REPLANNING_AFTER_NOT_COMPLETED
        released = replanning.event_type is JobEventType.BLOCK_RELEASED

        return (
            _untimed(
                job_id=job_id,
                obligation_type=ObligationType.REPLANNING_PENDING,
                # Owed, but this slice's SlaPolicy defines no allowance
                # for replanning - see ObligationState.NO_SLA_DEFINED.
                state=ObligationState.NO_SLA_DEFINED,
                owed_role=ActorRole.ENGINEER,
                policy=policy,
                evaluated_at=evaluated_at,
                reason_code=after_release if released else after_failure,
                reason=(
                    "An approved block was released before execution began; "
                    if released
                    else "Field work was reported not completed; "
                )
                + "the job needs to be planned again.",
                anchor=replanning,
                optimization_run_id=replanning.optimization_run_id,
            ),
            replanning,
        )

    # Only an UNANSWERED proposal can be moot. If an authority rejected or
    # postponed this placement, the obligation was ANSWERED - and a later
    # OPTIMIZATION_REFUSED is the next run declining to schedule an
    # already-reported job, not a withdrawal of the proposal they
    # answered. Reporting that as "the system withdrew this before an
    # authority answered it" would be a false statement in the one field
    # whose whole job is explaining the decision.
    answered = _last(since_placement, APPROVAL_RESPONSE_EVENT_TYPES)

    # The FIRST withdrawal after the placement is the one that ended the
    # approval obligation; later refusals are about the reported job.
    withdrawal = (
        None if answered is not None
        else _first(since_placement, SYSTEM_WITHDRAWAL_EVENT_TYPES)
    )

    if withdrawal is not None and placement is not None:
        withdrawn_run = withdrawal.metadata.get("withdrawn_proposal_run_id")

        return (
            _untimed(
                job_id=job_id,
                obligation_type=ObligationType.APPROVAL_PENDING,
                state=ObligationState.MOOT,
                # Moot obligations are owed by nobody, by definition.
                owed_role=None,
                policy=policy,
                evaluated_at=evaluated_at,
                reason_code=ObligationReason.APPROVAL_WITHDRAWN_BY_SYSTEM,
                reason=(
                    "The system withdrew this proposal "
                    f"({withdrawal.event_type.value}) before an authority "
                    "answered it. The approval obligation ended; it was not "
                    "breached, and nobody failed to respond."
                ),
                anchor=placement,
                optimization_run_id=withdrawn_run or placement.optimization_run_id,
            ),
            placement,
        )

    return (
        _untimed(
            job_id=job_id,
            obligation_type=ObligationType.NONE,
            state=ObligationState.NONE,
            owed_role=None,
            policy=policy,
            evaluated_at=evaluated_at,
            reason_code=ObligationReason.NO_OBLIGATION_AWAITING_OPTIMIZATION,
            reason=(
                "The job is reported and awaiting the next optimization run. "
                "No human owes an action on it."
            ),
        ),
        None,
    )


# ----------------------------------------------------------------------
# Entry points (pure - a future sweep calls exactly these)
# ----------------------------------------------------------------------


def derive_job_obligation(
    job: Mapping[str, Any],
    events: Sequence[Any],
    *,
    evaluated_at: datetime,
    policy: SlaPolicySet = ASSUMED_DEMO_SLA_POLICY,
    execution_records: Optional[Sequence[ExecutionRecord]] = None,
) -> JobObligation:
    """The one current obligation for one job, at one moment. Pure.

    `events` is the job's own history in commit order (StoredJobEvent
    values from JobHistoryRepository.list_for_job, or bare JobEvent
    values). `evaluated_at` is the single authoritative "now" for this
    evaluation: it is passed in rather than read from a clock here so
    that every obligation in one cross-job query shares one timestamp,
    and so tests are deterministic without monkey-patching anything.

    `execution_records` may be supplied by a caller that has already
    rebuilt them (avoiding a second pass over the same history);
    otherwise they are rebuilt here, and build_execution_records'
    ExecutionIntegrityError is deliberately allowed to propagate.

    Raises ObligationIntegrityError when the row and the history
    disagree. Never returns a partial or repaired result.
    """

    if evaluated_at.tzinfo is None:
        raise ValueError("evaluated_at must be timezone-aware.")

    job_id = job["job_id"]
    history = _events_of(events)
    resolved = policy.resolve_sla(job)
    status = str(job["status"])

    if status == JobStatus.SCHEDULED.value:
        obligation, anchor = _approval(job, history, resolved, evaluated_at)

    elif status == JobStatus.NOTIFIED.value:
        obligation, anchor = _execution_start(job, history, resolved, evaluated_at)

    elif status == JobStatus.IN_PROGRESS.value:
        records = (
            build_execution_records(job, history)
            if execution_records is None
            else execution_records
        )
        obligation, anchor = _completion(
            job, history, resolved, evaluated_at, records
        )

    elif status == JobStatus.REPORTED.value:
        obligation, anchor = _reported(job, history, resolved, evaluated_at)

    elif status == JobStatus.COMPLETED.value:
        obligation, anchor = (
            _untimed(
                job_id=job_id,
                obligation_type=ObligationType.NONE,
                state=ObligationState.NONE,
                owed_role=None,
                policy=resolved,
                evaluated_at=evaluated_at,
                reason_code=ObligationReason.NO_OBLIGATION_TERMINAL,
                reason="The job is completed. Nothing is owed on it.",
            ),
            None,
        )

    else:
        raise ObligationIntegrityError(
            f"Job '{job_id}' carries status {status!r}, which is not a "
            "JobStatus this derivation understands.",
            job_id,
        )

    reason_code, event = _attention(history, anchor)

    if reason_code is None:
        return obligation

    from dataclasses import replace as _replace

    return _replace(
        obligation,
        attention_required=True,
        attention_reason_code=reason_code,
        attention_event_id=None if event is None else event.event_id,
        attention_role=ActorRole.ENGINEER,
    )


def derive_obligations(
    jobs_with_events: Iterable[tuple[Mapping[str, Any], Sequence[Any]]],
    *,
    evaluated_at: datetime,
    policy: SlaPolicySet = ASSUMED_DEMO_SLA_POLICY,
) -> list[JobObligation]:
    """One obligation per job, all evaluated at the SAME moment. Pure.

    ONE `evaluated_at` flows through every job rather than each
    derivation consulting a clock of its own, so a page of obligations is
    a coherent snapshot of one instant and not a smear across the time
    the query took to run.
    """

    return [
        derive_job_obligation(
            job, events, evaluated_at=evaluated_at, policy=policy
        )
        for job, events in jobs_with_events
    ]


#: The statuses that can carry an obligation. 'completed' is terminal and
#: 'reported' carries one only after a withdrawal, but it is included
#: because that case must be visible - see _reported.
OBLIGATION_CANDIDATE_STATUSES = (
    JobStatus.REPORTED.value,
    JobStatus.SCHEDULED.value,
    JobStatus.NOTIFIED.value,
    JobStatus.IN_PROGRESS.value,
)


__all__ = [
    "APPROVAL_RESPONSE_EVENT_TYPES",
    "AttentionReason",
    "INTEGRITY_ERROR_TYPES",
    "JobObligation",
    "OBLIGATION_CANDIDATE_STATUSES",
    "ObligationIntegrityError",
    "ObligationReason",
    "ObligationState",
    "ObligationType",
    "PLACEMENT_EVENT_TYPES",
    "REPLANNING_TRIGGER_EVENT_TYPES",
    "SYSTEM_WITHDRAWAL_EVENT_TYPES",
    "derive_job_obligation",
    "derive_obligations",
]
