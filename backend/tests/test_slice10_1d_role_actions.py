"""Slice 10.1D: the role/action table.

Pins exactly which railway role may attempt which action, that the table
is the only thing deciding it, and that everything absent is denied - so
a new action is refused for every role until it is granted deliberately.
"""

from __future__ import annotations

import pytest

from backend.app.identity.actor import HUMAN_ROLES, ActorRole
from backend.app.identity.authorization import JobAction
from backend.app.identity.role_actions import (
    CORRIDOR_SCOPED_ACTIONS,
    JOB_READS,
    ROLE_ACTIONS,
    SECTION_SCOPED_ACTIONS,
    permitted_actions,
    role_permits,
)

A = JobAction


# ----------------------------------------------------------------------
# The table, spelled out
# ----------------------------------------------------------------------


def test_worker_holds_field_work_and_reads_and_nothing_else():
    assert permitted_actions(ActorRole.WORKER) == frozenset(
        {
            A.REPORT_JOB,
            A.START_EXECUTION,
            A.COMPLETE_JOB,
            A.REPORT_EXECUTION_NOT_COMPLETED,
            A.READ_JOB,
            A.READ_JOB_HISTORY,
            A.READ_BLOCK_PROPOSAL,
            A.READ_JOB_EXECUTION,
            A.READ_JOB_OBLIGATIONS,
        }
    )


def test_engineer_holds_optimization_and_reads_and_nothing_else():
    assert permitted_actions(ActorRole.ENGINEER) == frozenset(
        {
            A.REQUEST_OPTIMIZATION,
            A.READ_OPTIMIZATION_RUN,
            A.READ_JOB,
            A.READ_JOB_HISTORY,
            A.READ_BLOCK_PROPOSAL,
            A.READ_JOB_EXECUTION,
            A.READ_JOB_OBLIGATIONS,
        }
    )


def test_authority_holds_the_four_decisions_and_reads_and_nothing_else():
    assert permitted_actions(ActorRole.AUTHORITY) == frozenset(
        {
            A.COMMIT_BLOCK,
            A.REJECT_PROPOSAL,
            A.POSTPONE_PROPOSAL,
            A.RELEASE_COMMITTED_BLOCK,
            A.READ_JOB,
            A.READ_JOB_HISTORY,
            A.READ_BLOCK_PROPOSAL,
            A.READ_JOB_EXECUTION,
            A.READ_JOB_OBLIGATIONS,
        }
    )


@pytest.mark.parametrize(
    "role", [ActorRole.ADMIN, ActorRole.SYSTEM, ActorRole.UNIDENTIFIED]
)
def test_admin_system_and_unidentified_hold_nothing(role):
    assert permitted_actions(role) == frozenset()

    for action in JobAction:
        assert not role_permits(role, action)


def test_admin_is_an_assignable_role_that_still_holds_no_action():
    """ADMIN is administrative, not operational. Both facts at once."""

    assert ActorRole.ADMIN in HUMAN_ROLES
    assert permitted_actions(ActorRole.ADMIN) == frozenset()


def test_assign_schedule_is_granted_to_no_role():
    for role in ActorRole:
        assert not role_permits(role, A.ASSIGN_SCHEDULE)


# ----------------------------------------------------------------------
# Separation between roles
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "action,holder",
    [
        (A.REPORT_JOB, ActorRole.WORKER),
        (A.START_EXECUTION, ActorRole.WORKER),
        (A.COMPLETE_JOB, ActorRole.WORKER),
        (A.REPORT_EXECUTION_NOT_COMPLETED, ActorRole.WORKER),
        (A.REQUEST_OPTIMIZATION, ActorRole.ENGINEER),
        (A.READ_OPTIMIZATION_RUN, ActorRole.ENGINEER),
        (A.COMMIT_BLOCK, ActorRole.AUTHORITY),
        (A.REJECT_PROPOSAL, ActorRole.AUTHORITY),
        (A.POSTPONE_PROPOSAL, ActorRole.AUTHORITY),
        (A.RELEASE_COMMITTED_BLOCK, ActorRole.AUTHORITY),
    ],
)
def test_each_operational_action_has_exactly_one_holding_role(action, holder):
    holders = [role for role in ActorRole if role_permits(role, action)]

    assert holders == [holder]


def test_the_narrow_default_for_the_open_questions_is_recorded():
    """OQ-1 and OQ-2 take the narrowest reading until somebody decides.

    Widening a grant later is a decision; narrowing one that has already
    shipped is a regression.
    """

    # OQ-1: only a WORKER reports a job.
    assert not role_permits(ActorRole.ENGINEER, A.REPORT_JOB)
    assert not role_permits(ActorRole.AUTHORITY, A.REPORT_JOB)

    # OQ-2: only an ENGINEER reads an optimization run.
    assert not role_permits(ActorRole.WORKER, A.READ_OPTIMIZATION_RUN)
    assert not role_permits(ActorRole.AUTHORITY, A.READ_OPTIMIZATION_RUN)


def test_no_role_holds_every_action():
    for role in ActorRole:
        assert permitted_actions(role) != frozenset(JobAction)


def test_every_operational_role_may_read_a_job_it_is_scoped_to():
    for role in (ActorRole.WORKER, ActorRole.ENGINEER, ActorRole.AUTHORITY):
        assert JOB_READS <= permitted_actions(role)


# ----------------------------------------------------------------------
# Completeness: a new action cannot slip through unclassified
# ----------------------------------------------------------------------


def test_every_action_is_classified_exactly_once():
    overlap = CORRIDOR_SCOPED_ACTIONS & SECTION_SCOPED_ACTIONS
    missing = set(JobAction) - CORRIDOR_SCOPED_ACTIONS - SECTION_SCOPED_ACTIONS

    assert overlap == set(), f"classified both ways: {overlap}"
    assert missing == set(), f"unclassified, so undeniable-by-rule: {missing}"


def test_the_corridor_scoped_actions_are_exactly_the_two_without_a_section():
    assert CORRIDOR_SCOPED_ACTIONS == frozenset(
        {A.REQUEST_OPTIMIZATION, A.READ_OPTIMIZATION_RUN}
    )


def test_every_granted_action_is_a_real_job_action():
    for role, actions in ROLE_ACTIONS.items():
        assert isinstance(role, ActorRole)
        for action in actions:
            assert isinstance(action, JobAction)


def test_every_role_has_an_entry_so_none_falls_through_by_accident():
    assert set(ROLE_ACTIONS) == set(ActorRole)


# ----------------------------------------------------------------------
# Fail closed
# ----------------------------------------------------------------------


def test_an_unknown_role_holds_nothing():
    class NotARole:
        pass

    assert permitted_actions(NotARole()) == frozenset()  # type: ignore[arg-type]
    assert not role_permits(NotARole(), A.READ_JOB)  # type: ignore[arg-type]


def test_the_table_hands_out_frozen_sets_that_cannot_be_widened():
    actions = permitted_actions(ActorRole.WORKER)

    assert isinstance(actions, frozenset)
    assert not hasattr(actions, "add")

    # And a caller mutating what it got back cannot reach the table.
    widened = actions | {A.COMMIT_BLOCK}

    assert A.COMMIT_BLOCK not in permitted_actions(ActorRole.WORKER)
    assert widened is not actions


def test_the_table_holds_no_wildcard_or_all_marker():
    for actions in ROLE_ACTIONS.values():
        assert "*" not in {getattr(a, "value", a) for a in actions}
        assert "ALL" not in {getattr(a, "value", a) for a in actions}
