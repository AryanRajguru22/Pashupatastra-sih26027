"""Sprint 3 Slice 9: the SLA policy, and the fact that it is ASSUMED.

Pins the numbers, the version, the ladder and - most importantly - the
construction-time refusals that stop this repository from ever shipping
an SLA policy that presents itself as verified railway policy.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.data.models import DefectSeverity
from backend.app.jobs.sla_policy import (
    ASSUMED_DEMO_SLA_POLICY,
    ASSUMED_DEMO_SLA_VERSION,
    MAX_ESCALATION_STEPS,
    InvalidSlaPolicyError,
    SlaPolicy,
    SlaPolicySet,
    resolve_sla,
)


HOUR = timedelta(hours=1)
START = datetime(2026, 9, 20, 6, 0, 0, tzinfo=timezone.utc)


def job(severity=None):
    metadata = {} if severity is None else {"reported_severity": severity}
    return {"job_id": "JOB-1", "block_candidate": {"metadata": metadata}}


# ----------------------------------------------------------------------
# The assumed values, per severity
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "severity,approval,execution_start,completion",
    [
        ("CRITICAL", 2, 1, 4),
        ("MODERATE", 6, 4, 12),
        ("MINOR", 24, 12, 24),
        ("NONE", 24, 12, 24),
    ],
)
def test_each_severity_resolves_to_its_assumed_durations(
    severity, approval, execution_start, completion
):
    policy = resolve_sla(job(severity))

    assert policy.approval_sla == approval * HOUR
    assert policy.execution_start_sla == execution_start * HOUR
    assert policy.completion_sla == completion * HOUR


def test_a_job_reported_without_a_severity_resolves_to_the_none_policy():
    assert resolve_sla(job()) is ASSUMED_DEMO_SLA_POLICY.for_severity(
        DefectSeverity.NONE
    )


def test_resolution_is_deterministic_and_reuses_the_existing_severity_enum():
    assert [s.value for s in DefectSeverity] == [
        "NONE",
        "MINOR",
        "MODERATE",
        "CRITICAL",
    ]

    for severity in DefectSeverity:
        assert resolve_sla(job(severity.value)) is resolve_sla(job(severity.value))


def test_an_unrecognised_stored_severity_is_refused_not_treated_as_none():
    with pytest.raises(InvalidSlaPolicyError, match="not a DefectSeverity"):
        resolve_sla(job("CATASTROPHIC"))


# ----------------------------------------------------------------------
# The policy says, on its face, that its numbers are assumed
# ----------------------------------------------------------------------


def test_the_shipped_policy_is_versioned_and_marked_assumed():
    assert ASSUMED_DEMO_SLA_VERSION == "sla-assumed-demo-1.0"
    assert ASSUMED_DEMO_SLA_POLICY.version == ASSUMED_DEMO_SLA_VERSION

    for severity in DefectSeverity:
        policy = ASSUMED_DEMO_SLA_POLICY.for_severity(severity)

        assert policy.version == ASSUMED_DEMO_SLA_VERSION
        assert policy.assumed is True
        assert policy.to_dict()["assumed"] is True


def test_a_policy_version_that_does_not_say_assumed_cannot_be_constructed():
    with pytest.raises(InvalidSlaPolicyError, match="must contain"):
        SlaPolicy(
            version="indian-railways-sla-2026",
            approval_sla=2 * HOUR,
            execution_start_sla=HOUR,
            completion_sla=4 * HOUR,
        )


def test_a_policy_cannot_claim_to_be_verified():
    with pytest.raises(InvalidSlaPolicyError, match="No verified"):
        SlaPolicy(
            version="sla-assumed-demo-1.0",
            approval_sla=2 * HOUR,
            execution_start_sla=HOUR,
            completion_sla=4 * HOUR,
            assumed=False,
        )


def test_a_policy_set_must_cover_every_severity_and_share_one_version():
    row = ASSUMED_DEMO_SLA_POLICY.for_severity(DefectSeverity.NONE)

    with pytest.raises(InvalidSlaPolicyError, match="must resolve every"):
        SlaPolicySet(
            version=ASSUMED_DEMO_SLA_VERSION,
            by_severity={DefectSeverity.NONE: row},
        )

    other = SlaPolicy(
        version="sla-assumed-other-9.9",
        approval_sla=HOUR,
        execution_start_sla=HOUR,
        completion_sla=HOUR,
    )

    with pytest.raises(InvalidSlaPolicyError, match="whole table of numbers"):
        SlaPolicySet(
            version=ASSUMED_DEMO_SLA_VERSION,
            by_severity={
                DefectSeverity.NONE: other,
                DefectSeverity.MINOR: row,
                DefectSeverity.MODERATE: row,
                DefectSeverity.CRITICAL: row,
            },
        )


# ----------------------------------------------------------------------
# Immutability
# ----------------------------------------------------------------------


def test_a_policy_cannot_be_mutated_after_construction():
    policy = ASSUMED_DEMO_SLA_POLICY.for_severity(DefectSeverity.CRITICAL)

    with pytest.raises(FrozenInstanceError):
        policy.approval_sla = 99 * HOUR

    with pytest.raises(FrozenInstanceError):
        ASSUMED_DEMO_SLA_POLICY.version = "sla-assumed-tampered"


def test_the_severity_table_itself_cannot_be_mutated():
    with pytest.raises(TypeError):
        ASSUMED_DEMO_SLA_POLICY.by_severity[DefectSeverity.CRITICAL] = None


def test_escalation_multipliers_are_a_tuple_not_a_shared_list():
    policy = ASSUMED_DEMO_SLA_POLICY.for_severity(DefectSeverity.CRITICAL)

    assert policy.escalation_multipliers == (1.0, 2.0, 4.0)
    assert isinstance(policy.escalation_multipliers, tuple)


# ----------------------------------------------------------------------
# The ladder: due-soon and the escalation thresholds
# ----------------------------------------------------------------------


def timing(hours, severity="CRITICAL"):
    policy = resolve_sla(job(severity))

    return policy.timing(
        sla=policy.approval_sla,
        started_at=START,
        evaluated_at=START + timedelta(hours=hours),
    )


def test_due_soon_begins_at_seventy_five_percent_of_the_sla():
    # CRITICAL approval SLA is 2h, so DUE_SOON begins at 1h30m.
    assert timing(1.49).is_due_soon is False
    assert timing(1.5).is_due_soon is True
    assert timing(1.5).is_past_due is False

    assert timing(1.5).due_soon_at == START + timedelta(hours=1.5)


@pytest.mark.parametrize(
    "hours,level",
    [
        (0.0, 0),
        (1.99, 0),
        (2.0, 1),
        (3.99, 1),
        (4.0, 2),
        (7.99, 2),
        (8.0, 3),
        (800.0, 3),
    ],
)
def test_escalation_levels_begin_at_one_two_and_four_times_the_sla(hours, level):
    assert timing(hours).escalation_level == level


def test_the_deadline_is_the_clock_start_plus_the_sla():
    assert timing(0).due_at == START + 2 * HOUR
    assert timing(0).sla == 2 * HOUR


def test_elapsed_is_never_negative_for_a_clock_that_appears_to_start_later():
    policy = resolve_sla(job("CRITICAL"))

    backwards = policy.timing(
        sla=policy.approval_sla,
        started_at=START,
        evaluated_at=START - HOUR,
    )

    assert backwards.elapsed == timedelta(0)
    assert backwards.escalation_level == 0
    assert backwards.is_past_due is False


# ----------------------------------------------------------------------
# Determinism, injection and clock hygiene
# ----------------------------------------------------------------------


def test_the_same_inputs_always_produce_the_same_timing():
    assert timing(5.0) == timing(5.0)


def test_evaluation_never_consults_a_wall_clock():
    """Two evaluations minutes apart at the same evaluated_at agree.

    The policy takes both moments as arguments; nothing inside it reads
    datetime.now, which is what lets an auditor recompute a past
    escalation exactly.
    """

    first = timing(3.0)
    second = timing(3.0)

    assert (first.escalation_level, first.due_at, first.elapsed) == (
        second.escalation_level,
        second.due_at,
        second.elapsed,
    )


def test_naive_datetimes_are_refused_rather_than_assumed_to_be_utc():
    policy = resolve_sla(job("CRITICAL"))

    with pytest.raises(InvalidSlaPolicyError, match="timezone-aware"):
        policy.timing(
            sla=HOUR,
            started_at=datetime(2026, 9, 20, 6, 0, 0),
            evaluated_at=START,
        )

    with pytest.raises(InvalidSlaPolicyError, match="timezone-aware"):
        policy.timing(
            sla=HOUR,
            started_at=START,
            evaluated_at=datetime(2026, 9, 20, 6, 0, 0),
        )


def test_an_alternative_policy_can_be_injected_without_changing_any_caller():
    tight = SlaPolicy(
        version="sla-assumed-test-tight",
        approval_sla=timedelta(minutes=10),
        execution_start_sla=timedelta(minutes=10),
        completion_sla=timedelta(minutes=10),
        due_soon_fraction=0.5,
        escalation_multipliers=(2.0,),
    )
    injected = SlaPolicySet(
        version="sla-assumed-test-tight",
        by_severity={severity: tight for severity in DefectSeverity},
    )

    resolved = resolve_sla(job("CRITICAL"), injected)

    assert resolved.approval_sla == timedelta(minutes=10)

    # A first escalation step strictly after the deadline is exactly the
    # configuration that makes OVERDUE reachable - see ObligationState.
    result = resolved.timing(
        sla=resolved.approval_sla,
        started_at=START,
        evaluated_at=START + timedelta(minutes=11),
    )

    assert (result.is_past_due, result.escalation_level) == (True, 0)


# ----------------------------------------------------------------------
# Incoherent policies are refused at construction, never at evaluation
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"version": ""}, "must carry a version"),
        ({"approval_sla": timedelta(0)}, "must be positive"),
        ({"completion_sla": -HOUR}, "must be positive"),
        ({"due_soon_fraction": 0.0}, "due_soon_fraction"),
        ({"due_soon_fraction": 1.5}, "due_soon_fraction"),
        ({"escalation_multipliers": ()}, "at least one escalation step"),
        ({"escalation_multipliers": (0.5, 1.0)}, "at or after the deadline"),
        ({"escalation_multipliers": (2.0, 1.0)}, "strictly increasing"),
        ({"escalation_multipliers": (1.0, 1.0)}, "strictly increasing"),
        ({"escalation_multipliers": (1.0, 2.0, 4.0, 8.0)}, "at most"),
    ],
)
def test_an_incoherent_policy_cannot_be_constructed(kwargs, match):
    base = {
        "version": "sla-assumed-demo-test",
        "approval_sla": 2 * HOUR,
        "execution_start_sla": HOUR,
        "completion_sla": 4 * HOUR,
    }

    with pytest.raises(InvalidSlaPolicyError, match=match):
        SlaPolicy(**{**base, **kwargs})


def test_the_escalation_step_limit_matches_the_state_vocabulary():
    from backend.app.jobs.obligations import ObligationState

    escalated = [s for s in ObligationState if s.value.startswith("ESCALATED_")]

    assert len(escalated) == MAX_ESCALATION_STEPS
