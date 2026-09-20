"""Sprint 3 Slice 9: obligation derivation, at the pure layer.

Every test here builds a job row and a history by hand and evaluates the
derivation at a chosen moment. No database, no service, no clock: that is
the point. derive_job_obligation is a pure function of (row, history,
policy, evaluated_at), and these tests are what pins that.

The service-level and HTTP-level behaviour is covered by
test_slice9_accountability_api.py; the safety properties (time never
mutates anything) by test_slice9_safety.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.app.identity.actor import OPTIMIZER, ActorRole, human_actor
from backend.app.jobs.events import JobEventType, event_timestamp, make_event
from backend.app.jobs.execution import (
    ExecutionDeviations,
    ExecutionRecord,
    ExecutionStatus,
)
from backend.app.jobs.obligations import (
    AttentionReason,
    ObligationIntegrityError,
    ObligationReason,
    ObligationState,
    ObligationType,
    derive_job_obligation,
    derive_obligations,
)
from backend.app.jobs.sla_policy import ASSUMED_DEMO_SLA_POLICY


E = JobEventType

T0 = datetime(2026, 9, 20, 6, 0, 0, tzinfo=timezone.utc)

AUTHORITY = human_actor("AUTHORITY-017", ActorRole.AUTHORITY)
WORKER = human_actor("WORKER-042", ActorRole.WORKER)

RUN = "RUN-1"
LATER_RUN = "RUN-2"


def at(hours: float) -> datetime:
    return T0 + timedelta(hours=hours)


def job_row(status, *, run_id=None, severity="CRITICAL", job_id="JOB-1", **extra):
    """A minimal maintenance_jobs row, shaped exactly as the repository returns one."""

    metadata = {"reported_severity": severity}

    if run_id is not None:
        metadata["proposal_run_id"] = run_id

    return {
        "job_id": job_id,
        "status": status,
        "block_candidate": {"metadata": metadata},
        **extra,
    }


def event(event_type, hours, *, actor=OPTIMIZER, run_id=RUN, job_id="JOB-1", **metadata):
    return make_event(
        job_id,
        event_type,
        actor,
        occurred_at=event_timestamp(at(hours)),
        optimization_run_id=run_id,
        metadata=metadata,
    )


def derive(job, events, hours, **kwargs):
    return derive_job_obligation(job, events, evaluated_at=at(hours), **kwargs)


# ----------------------------------------------------------------------
# Approval obligation
# ----------------------------------------------------------------------


def test_a_scheduled_job_with_an_unanswered_proposal_owes_an_authority_decision():
    proposed = event(E.BLOCK_PROPOSED, 0)

    obligation = derive(job_row("scheduled", run_id=RUN), [proposed], 1)

    assert obligation.obligation_type is ObligationType.APPROVAL_PENDING
    assert obligation.owed_role is ActorRole.AUTHORITY
    assert obligation.reason_code is ObligationReason.APPROVAL_AWAITING_AUTHORITY
    assert obligation.is_open is True


def test_the_approval_clock_starts_at_the_placement_event_and_names_it():
    proposed = event(E.BLOCK_PROPOSED, 0)

    obligation = derive(job_row("scheduled", run_id=RUN), [proposed], 1)

    assert obligation.clock_started_at == proposed.occurred_at
    assert obligation.anchor_event_id == proposed.event_id
    assert obligation.optimization_run_id == RUN
    # CRITICAL approval SLA is 2h.
    assert obligation.due_at == event_timestamp(at(2))
    assert obligation.sla_seconds == 2 * 3600


@pytest.mark.parametrize(
    "placement",
    [E.BLOCK_PROPOSED, E.BLOCK_REPROPOSED, E.COMMITTED_BLOCK_PRESERVED],
)
def test_every_placement_event_type_can_start_the_approval_clock(placement):
    obligation = derive(job_row("scheduled", run_id=RUN), [event(placement, 0)], 1)

    assert obligation.obligation_type is ObligationType.APPROVAL_PENDING


@pytest.mark.parametrize(
    "hours,state,level",
    [
        (0.0, ObligationState.WITHIN_SLA, 0),
        (1.0, ObligationState.WITHIN_SLA, 0),
        (1.5, ObligationState.DUE_SOON, 0),
        (2.0, ObligationState.ESCALATED_L1, 1),
        (4.0, ObligationState.ESCALATED_L2, 2),
        (8.0, ObligationState.ESCALATED_L3, 3),
        (500.0, ObligationState.ESCALATED_L3, 3),
    ],
)
def test_the_approval_obligation_walks_the_ladder_with_elapsed_time(hours, state, level):
    obligation = derive(
        job_row("scheduled", run_id=RUN), [event(E.BLOCK_PROPOSED, 0)], hours
    )

    assert (obligation.state, obligation.escalation_level) == (state, level)
    assert obligation.is_past_due is (level >= 1)


@pytest.mark.parametrize(
    "response", [E.BLOCK_COMMITTED, E.PROPOSAL_REJECTED, E.PROPOSAL_POSTPONED]
)
def test_an_answered_proposal_leaves_no_approval_obligation(response):
    """The answer moves the job out of 'scheduled'; no clock survives it.

    An authority response is recorded together with the status change, so
    the job is no longer 'scheduled' and the approval obligation simply
    stops being derivable - there is no stale clock to expire.
    """

    history = [event(E.BLOCK_PROPOSED, 0), event(response, 1)]
    status = "notified" if response is E.BLOCK_COMMITTED else "reported"
    run_id = RUN if response is E.BLOCK_COMMITTED else None

    obligation = derive(job_row(status, run_id=run_id), history, 500)

    assert obligation.obligation_type is not ObligationType.APPROVAL_PENDING


def test_a_rejected_proposal_leaves_no_obligation_at_all():
    history = [event(E.BLOCK_PROPOSED, 0), event(E.PROPOSAL_REJECTED, 1, actor=AUTHORITY)]

    obligation = derive(job_row("reported"), history, 5000)

    assert obligation.obligation_type is ObligationType.NONE
    assert obligation.state is ObligationState.NONE
    assert (
        obligation.reason_code is ObligationReason.NO_OBLIGATION_AWAITING_OPTIMIZATION
    )


def test_a_postponed_proposal_is_an_answer_not_a_new_deadline_on_a_human():
    """Sec 5.6: the authority's chosen date constrains SCHEDULING, not a person."""

    history = [
        event(E.BLOCK_PROPOSED, 0),
        event(E.PROPOSAL_POSTPONED, 1, actor=AUTHORITY, selected_date="2026-09-11"),
    ]

    obligation = derive(job_row("reported"), history, 5000)

    assert obligation.obligation_type is ObligationType.NONE
    assert obligation.is_past_due is False


def test_a_reproposal_resets_the_clock_onto_the_new_proposal():
    """The single most important staleness property of the derivation."""

    history = [
        event(E.BLOCK_PROPOSED, 0),
        event(E.PROPOSAL_INVALIDATED, 1),
        event(E.BLOCK_REPROPOSED, 10, run_id=LATER_RUN),
    ]

    obligation = derive(job_row("scheduled", run_id=LATER_RUN), history, 11)

    assert obligation.clock_started_at == event_timestamp(at(10))
    assert obligation.optimization_run_id == LATER_RUN
    # One hour into a fresh 2h clock, not eleven hours into the old one.
    assert obligation.state is ObligationState.WITHIN_SLA
    assert obligation.elapsed_seconds == 3600


def test_a_superseded_proposal_can_never_produce_the_current_obligation():
    """An old placement event is invisible once a newer run owns the job."""

    history = [
        event(E.BLOCK_PROPOSED, 0),
        event(E.PROPOSAL_INVALIDATED, 1),
        event(E.BLOCK_REPROPOSED, 100, run_id=LATER_RUN),
    ]

    obligation = derive(job_row("scheduled", run_id=LATER_RUN), history, 100.5)

    assert obligation.anchor_event_id == history[-1].event_id
    assert obligation.state is ObligationState.WITHIN_SLA


def test_a_transition_rejected_is_not_a_response_and_the_obligation_stands():
    history = [
        event(E.BLOCK_PROPOSED, 0),
        make_event(
            "JOB-1",
            E.TRANSITION_REJECTED,
            AUTHORITY,
            occurred_at=event_timestamp(at(1)),
            metadata={"attempted_transition": "notify", "error_type": "StaleProposalError"},
        ),
    ]

    obligation = derive(job_row("scheduled", run_id=RUN), history, 3)

    assert obligation.obligation_type is ObligationType.APPROVAL_PENDING
    assert obligation.state is ObligationState.ESCALATED_L1


# ----------------------------------------------------------------------
# Mootness - a system withdrawal is never an authority failure
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "withdrawal", [E.PROPOSAL_INVALIDATED, E.OPTIMIZATION_REFUSED]
)
def test_a_system_withdrawal_makes_the_approval_obligation_moot_not_overdue(withdrawal):
    history = [
        event(E.BLOCK_PROPOSED, 0),
        event(withdrawal, 1, run_id=LATER_RUN, withdrawn_proposal_run_id=RUN),
    ]

    obligation = derive(job_row("reported"), history, 3)

    assert obligation.obligation_type is ObligationType.APPROVAL_PENDING
    assert obligation.state is ObligationState.MOOT
    assert obligation.reason_code is ObligationReason.APPROVAL_WITHDRAWN_BY_SYSTEM
    assert obligation.owed_role is None
    assert "not breached" in obligation.reason


@pytest.mark.parametrize("hours", [1.0, 3.0, 9.0, 1000.0, 100_000.0])
def test_a_moot_obligation_never_becomes_overdue_however_long_it_sits(hours):
    """Safety-critical: time must never turn a withdrawal into an accusation."""

    history = [
        event(E.BLOCK_PROPOSED, 0),
        event(E.PROPOSAL_INVALIDATED, 1, withdrawn_proposal_run_id=RUN),
    ]

    obligation = derive(job_row("reported"), history, hours)

    assert obligation.state is ObligationState.MOOT
    assert obligation.is_past_due is False
    assert obligation.is_open is False
    assert obligation.escalation_level == 0
    assert obligation.due_at is None


def test_a_moot_obligation_still_names_the_proposal_it_ended():
    history = [
        event(E.BLOCK_PROPOSED, 0),
        event(E.PROPOSAL_INVALIDATED, 1, run_id=LATER_RUN, withdrawn_proposal_run_id=RUN),
    ]

    obligation = derive(job_row("reported"), history, 5)

    assert obligation.optimization_run_id == RUN
    assert obligation.clock_started_at == event_timestamp(at(0))


# ----------------------------------------------------------------------
# Execution start obligation
# ----------------------------------------------------------------------


def committed_history(commit_hours=1.0):
    return [event(E.BLOCK_PROPOSED, 0), event(E.BLOCK_COMMITTED, commit_hours, actor=AUTHORITY)]


def test_a_notified_job_owes_a_worker_the_start_of_field_work():
    obligation = derive(job_row("notified", run_id=RUN), committed_history(), 1.5)

    assert obligation.obligation_type is ObligationType.EXECUTION_START_PENDING
    assert obligation.owed_role is ActorRole.WORKER
    assert obligation.reason_code is ObligationReason.EXECUTION_AWAITING_START


def test_the_execution_start_clock_anchors_on_block_committed_not_the_planned_window():
    history = committed_history(commit_hours=1.0)

    obligation = derive(
        job_row(
            "notified",
            run_id=RUN,
            # A planned window that is long in the past - exactly the
            # situation the fixed horizon creates today. It must not
            # affect the deadline.
            schedule_start_minute=10,
            schedule_end_minute=70,
        ),
        history,
        1.0,
    )

    assert obligation.clock_started_at == history[-1].occurred_at
    assert obligation.anchor_event_id == history[-1].event_id
    # CRITICAL execution-start SLA is 1h, measured from the commit.
    assert obligation.due_at == event_timestamp(at(2))
    assert obligation.state is ObligationState.WITHIN_SLA


@pytest.mark.parametrize(
    "hours,state",
    [
        (1.0, ObligationState.WITHIN_SLA),
        (1.75, ObligationState.DUE_SOON),
        (2.0, ObligationState.ESCALATED_L1),
        (3.0, ObligationState.ESCALATED_L2),
        (5.0, ObligationState.ESCALATED_L3),
    ],
)
def test_the_execution_start_obligation_escalates_from_the_commit(hours, state):
    obligation = derive(job_row("notified", run_id=RUN), committed_history(), hours)

    assert obligation.state is state


def test_a_started_execution_leaves_no_start_obligation():
    # Starting moves the job to in_progress; the start obligation is
    # replaced by the completion obligation, never left hanging.
    obligation = derive(
        job_row("in_progress", run_id=RUN),
        started_history(),
        3,
        execution_records=[open_execution()],
    )

    assert obligation.obligation_type is not ObligationType.EXECUTION_START_PENDING
    assert obligation.obligation_type is ObligationType.COMPLETION_PENDING


def test_a_released_block_ends_the_execution_obligation_without_breach():
    history = committed_history() + [
        event(E.BLOCK_RELEASED, 2, actor=AUTHORITY, reason="Possession not granted")
    ]

    obligation = derive(job_row("reported"), history, 900)

    assert obligation.obligation_type is ObligationType.REPLANNING_PENDING
    assert obligation.is_past_due is False
    assert obligation.reason_code is ObligationReason.REPLANNING_AFTER_RELEASE


# ----------------------------------------------------------------------
# Completion obligation
# ----------------------------------------------------------------------


def open_execution(started_at_hours=2.0, execution_id="EXE-" + "A" * 32):
    return ExecutionRecord(
        execution_id=execution_id,
        job_id="JOB-1",
        attempt_number=1,
        optimization_run_id=RUN,
        proposal_id="PRP-1",
        track_id="UP-1",
        section_id=None,
        planned_start_minute=10,
        planned_end_minute=70,
        committed_block_digest="0" * 64,
        status=ExecutionStatus.STARTED,
        started_by={"actor_id": "WORKER-042", "role": "WORKER"},
        started_recorded_at=event_timestamp(at(started_at_hours)),
        actual_start_at="2026-09-10T00:10:00+05:30",
        actual_start_minute=10,
        before_work_evidence=(),
        ended_by=None,
        ended_recorded_at=None,
        actual_end_at=None,
        actual_end_minute=None,
        after_work_evidence=(),
        not_completed_reason=None,
        failure_evidence=(),
        deviations=ExecutionDeviations(started_before_planned_start=False),
    )


def started_history(started_at_hours=2.0, execution_id="EXE-" + "A" * 32):
    return committed_history() + [
        event(
            E.EXECUTION_STARTED,
            started_at_hours,
            actor=WORKER,
            execution_id=execution_id,
        )
    ]


def test_an_in_progress_job_owes_a_worker_an_outcome():
    obligation = derive(
        job_row("in_progress", run_id=RUN),
        started_history(),
        3,
        execution_records=[open_execution()],
    )

    assert obligation.obligation_type is ObligationType.COMPLETION_PENDING
    assert obligation.owed_role is ActorRole.WORKER
    assert obligation.reason_code is ObligationReason.COMPLETION_AWAITING_OUTCOME


def test_the_completion_clock_uses_started_recorded_at_not_the_workers_own_claim():
    """A deadline must not be movable by the party it binds."""

    record = open_execution(started_at_hours=2.0)

    obligation = derive(
        job_row("in_progress", run_id=RUN),
        started_history(started_at_hours=2.0),
        2.0,
        execution_records=[record],
    )

    assert obligation.clock_started_at == record.started_recorded_at
    assert obligation.clock_started_at != record.actual_start_at
    # CRITICAL completion SLA is 4h from the recorded start.
    assert obligation.due_at == event_timestamp(at(6))


def test_there_is_no_separate_evidence_pending_state():
    """Completion is already evidence-gated; the open record IS that state."""

    obligation = derive(
        job_row("in_progress", run_id=RUN),
        started_history(),
        3,
        execution_records=[open_execution()],
    )

    assert obligation.obligation_type is ObligationType.COMPLETION_PENDING
    assert "evidence" in obligation.reason


@pytest.mark.parametrize(
    "hours,state",
    [
        (2.0, ObligationState.WITHIN_SLA),
        (5.0, ObligationState.DUE_SOON),
        (6.0, ObligationState.ESCALATED_L1),
        (10.0, ObligationState.ESCALATED_L2),
        (18.0, ObligationState.ESCALATED_L3),
    ],
)
def test_the_completion_obligation_escalates_from_the_recorded_start(hours, state):
    obligation = derive(
        job_row("in_progress", run_id=RUN),
        started_history(),
        hours,
        execution_records=[open_execution()],
    )

    assert obligation.state is state


def test_a_completed_job_owes_nothing():
    history = started_history() + [event(E.EXECUTION_COMPLETED, 3, actor=WORKER)]

    obligation = derive(job_row("completed", run_id=RUN), history, 9999)

    assert obligation.obligation_type is ObligationType.NONE
    assert obligation.state is ObligationState.NONE
    assert obligation.reason_code is ObligationReason.NO_OBLIGATION_TERMINAL
    assert obligation.is_open is False


def test_work_reported_not_completed_answers_the_obligation_and_needs_replanning():
    history = started_history() + [
        event(E.EXECUTION_NOT_COMPLETED, 3, actor=WORKER, reason="Rain stopped work")
    ]

    obligation = derive(job_row("reported"), history, 900)

    assert obligation.obligation_type is ObligationType.REPLANNING_PENDING
    assert obligation.reason_code is ObligationReason.REPLANNING_AFTER_NOT_COMPLETED
    assert obligation.owed_role is ActorRole.ENGINEER
    assert obligation.is_past_due is False


# ----------------------------------------------------------------------
# Replanning, and the flood that must not happen
# ----------------------------------------------------------------------


def test_a_newly_reported_job_is_not_an_obligation_on_anybody():
    history = [event(E.JOB_CREATED, 0, actor=WORKER, run_id=None)]

    obligation = derive(job_row("reported"), history, 10_000)

    assert obligation.obligation_type is ObligationType.NONE
    assert obligation.owed_role is None
    assert obligation.is_open is False


def test_a_replanned_job_stops_owing_replanning_once_it_is_proposed_again():
    history = [
        event(E.BLOCK_PROPOSED, 0),
        event(E.BLOCK_COMMITTED, 1, actor=AUTHORITY),
        event(E.BLOCK_RELEASED, 2, actor=AUTHORITY, reason="Possession not granted"),
        event(E.BLOCK_PROPOSED, 5, run_id=LATER_RUN),
    ]

    obligation = derive(job_row("scheduled", run_id=LATER_RUN), history, 5.5)

    assert obligation.obligation_type is ObligationType.APPROVAL_PENDING
    assert obligation.optimization_run_id == LATER_RUN


# ----------------------------------------------------------------------
# Attention: orthogonal to the obligation, never a competing one
# ----------------------------------------------------------------------


def test_a_committed_block_conflict_raises_attention_without_erasing_the_obligation():
    history = committed_history() + [
        event(
            E.COMMITTED_BLOCK_CONFLICT,
            2,
            run_id=LATER_RUN,
            solver_status="INFEASIBLE",
            commitment_preserved=True,
        )
    ]

    obligation = derive(job_row("notified", run_id=RUN), history, 2.5)

    assert obligation.obligation_type is ObligationType.EXECUTION_START_PENDING
    assert obligation.attention_required is True
    assert obligation.attention_reason_code is AttentionReason.COMMITTED_BLOCK_CONFLICT
    assert obligation.attention_event_id == history[-1].event_id
    assert obligation.attention_role is ActorRole.ENGINEER


def test_a_conflict_recorded_before_the_current_commitment_is_history_not_attention():
    history = [
        event(E.BLOCK_PROPOSED, 0),
        event(E.COMMITTED_BLOCK_CONFLICT, 0.5, run_id=LATER_RUN),
        event(E.BLOCK_COMMITTED, 1, actor=AUTHORITY),
    ]

    obligation = derive(job_row("notified", run_id=RUN), history, 1.5)

    assert obligation.attention_required is False


def test_a_recorded_integrity_refusal_raises_attention():
    history = committed_history() + [
        make_event(
            "JOB-1",
            E.TRANSITION_REJECTED,
            WORKER,
            occurred_at=event_timestamp(at(2)),
            metadata={
                "attempted_transition": "execution_start",
                "error_type": "CommittedStateIntegrityError",
            },
        )
    ]

    obligation = derive(job_row("notified", run_id=RUN), history, 2.5)

    assert obligation.attention_required is True
    assert (
        obligation.attention_reason_code is AttentionReason.INTEGRITY_REFUSAL_RECORDED
    )


def test_an_ordinary_refused_transition_is_not_an_attention_condition():
    history = committed_history() + [
        make_event(
            "JOB-1",
            E.TRANSITION_REJECTED,
            WORKER,
            occurred_at=event_timestamp(at(2)),
            metadata={
                "attempted_transition": "notify",
                "error_type": "InvalidTransitionError",
            },
        )
    ]

    obligation = derive(job_row("notified", run_id=RUN), history, 2.5)

    assert obligation.attention_required is False


# ----------------------------------------------------------------------
# Never an individual
# ----------------------------------------------------------------------


def test_no_obligation_ever_names_an_individual():
    """Sec 16.3: obligations are role-addressed; naming a person would be fiction."""

    cases = [
        (job_row("scheduled", run_id=RUN), [event(E.BLOCK_PROPOSED, 0)], {}),
        (job_row("notified", run_id=RUN), committed_history(), {}),
        (
            job_row("in_progress", run_id=RUN),
            started_history(),
            {"execution_records": [open_execution()]},
        ),
    ]

    for job, history, kwargs in cases:
        obligation = derive(job, history, 3, **kwargs)
        payload = obligation.to_dict()

        assert payload["owed_role"] in {"AUTHORITY", "WORKER", "ENGINEER"}
        assert "AUTHORITY-017" not in repr(payload)
        assert "WORKER-042" not in repr(payload)
        assert not any(
            key in payload
            for key in ("actor_id", "assigned_to", "recipient", "contact", "assignee")
        )


# ----------------------------------------------------------------------
# Determinism and the single evaluation moment
# ----------------------------------------------------------------------


def test_the_same_history_and_moment_always_produce_the_same_obligation():
    job = job_row("scheduled", run_id=RUN)
    history = [event(E.BLOCK_PROPOSED, 0)]

    assert derive(job, history, 3) == derive(job, history, 3)
    assert derive(job, history, 3).to_dict() == derive(job, history, 3).to_dict()


def test_every_obligation_reports_the_policy_version_that_produced_it():
    obligation = derive(
        job_row("scheduled", run_id=RUN), [event(E.BLOCK_PROPOSED, 0)], 3
    )

    assert obligation.policy_version == ASSUMED_DEMO_SLA_POLICY.version
    assert obligation.policy_assumed is True
    assert obligation.to_dict()["policy_assumed"] is True


def test_a_cross_job_derivation_shares_one_evaluation_moment():
    pairs = [
        (job_row("scheduled", run_id=RUN, job_id="JOB-1"), [event(E.BLOCK_PROPOSED, 0)]),
        (
            job_row("scheduled", run_id=RUN, job_id="JOB-2"),
            [event(E.BLOCK_PROPOSED, 1, job_id="JOB-2")],
        ),
    ]

    obligations = derive_obligations(pairs, evaluated_at=at(3))

    assert {o.evaluated_at for o in obligations} == {event_timestamp(at(3))}
    assert [o.job_id for o in obligations] == ["JOB-1", "JOB-2"]


def test_severity_changes_the_deadline_and_nothing_else():
    history = [event(E.BLOCK_PROPOSED, 0)]

    critical = derive(job_row("scheduled", run_id=RUN, severity="CRITICAL"), history, 3)
    minor = derive(job_row("scheduled", run_id=RUN, severity="MINOR"), history, 3)

    assert critical.due_at == event_timestamp(at(2))
    assert minor.due_at == event_timestamp(at(24))
    assert critical.state is ObligationState.ESCALATED_L1
    assert minor.state is ObligationState.WITHIN_SLA
    assert critical.obligation_type is minor.obligation_type


def test_a_naive_evaluation_moment_is_refused():
    with pytest.raises(ValueError, match="timezone-aware"):
        derive_job_obligation(
            job_row("reported"),
            [],
            evaluated_at=datetime(2026, 9, 20, 6, 0, 0),
        )


# ----------------------------------------------------------------------
# Incoherent history fails closed; nothing is fabricated
# ----------------------------------------------------------------------


def test_a_scheduled_job_with_no_proposal_run_id_is_refused():
    with pytest.raises(ObligationIntegrityError, match="no optimization_run_id"):
        derive(job_row("scheduled"), [event(E.BLOCK_PROPOSED, 0)], 1)


def test_a_scheduled_job_with_no_placement_event_for_its_run_is_refused():
    with pytest.raises(ObligationIntegrityError, match="no placement event"):
        derive(job_row("scheduled", run_id="RUN-MISSING"), [event(E.BLOCK_PROPOSED, 0)], 1)


def test_a_scheduled_job_whose_run_was_already_answered_is_refused():
    history = [event(E.BLOCK_PROPOSED, 0), event(E.BLOCK_COMMITTED, 1, actor=AUTHORITY)]

    with pytest.raises(ObligationIntegrityError, match="already records"):
        derive(job_row("scheduled", run_id=RUN), history, 2)


def test_a_notified_job_with_no_commit_event_is_refused():
    with pytest.raises(ObligationIntegrityError, match="no BLOCK_COMMITTED"):
        derive(job_row("notified", run_id=RUN), [event(E.BLOCK_PROPOSED, 0)], 1)


def test_an_in_progress_job_with_no_open_execution_is_refused():
    with pytest.raises(ObligationIntegrityError, match="open executions"):
        derive(
            job_row("in_progress", run_id=RUN),
            started_history(),
            3,
            execution_records=[],
        )


def test_an_open_execution_with_no_matching_start_event_is_refused():
    with pytest.raises(ObligationIntegrityError, match="cannot be located"):
        derive(
            job_row("in_progress", run_id=RUN),
            committed_history(),
            3,
            execution_records=[open_execution()],
        )


def test_an_unknown_status_is_refused_rather_than_guessed():
    with pytest.raises(ObligationIntegrityError, match="not a JobStatus"):
        derive(job_row("archived"), [], 1)


# ----------------------------------------------------------------------
# An ANSWERED proposal is never reported as withdrawn-before-answering
# ----------------------------------------------------------------------


@pytest.mark.parametrize("response", [E.PROPOSAL_REJECTED, E.PROPOSAL_POSTPONED])
def test_a_later_optimizer_refusal_does_not_relabel_an_answered_proposal(response):
    """Ordinary history: an authority answers, then the next run declines
    to schedule the now-reported job. That second event is NOT a
    withdrawal of the proposal they already answered, and saying so
    would be a false explanation."""

    history = [
        event(E.BLOCK_PROPOSED, 0),
        event(response, 1, actor=AUTHORITY),
        event(E.OPTIMIZATION_REFUSED, 5, run_id=LATER_RUN),
    ]

    obligation = derive(job_row("reported"), history, 9)

    assert obligation.obligation_type is ObligationType.NONE
    assert obligation.state is ObligationState.NONE
    assert (
        obligation.reason_code is ObligationReason.NO_OBLIGATION_AWAITING_OPTIMIZATION
    )
    assert "before an authority answered" not in obligation.reason


def test_a_genuinely_unanswered_proposal_is_still_moot_after_a_later_refusal():
    """The mirror case must keep working: nobody answered, so the
    withdrawal really did end an outstanding obligation."""

    history = [
        event(E.BLOCK_PROPOSED, 0),
        event(E.PROPOSAL_INVALIDATED, 1, run_id=LATER_RUN, withdrawn_proposal_run_id=RUN),
        event(E.OPTIMIZATION_REFUSED, 5, run_id="RUN-3"),
    ]

    obligation = derive(job_row("reported"), history, 9)

    assert obligation.state is ObligationState.MOOT
    # Attributed to the withdrawal that actually ended it, not the latest
    # refusal that happened to come after.
    assert "PROPOSAL_INVALIDATED" in obligation.reason
    assert obligation.is_past_due is False
