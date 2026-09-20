"""Sprint 3 Slice 9: notification intent, and the honesty of the channel.

The most important tests in this file are the ones that prove what the
code does NOT do: it does not deliver anything, it does not claim to,
and it cannot name a person.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.app.identity.actor import ActorRole
from backend.app.jobs.events import canonical_json, event_timestamp
from backend.app.jobs.notifications import (
    RECIPIENT_ROLES,
    ChannelOutcome,
    ChannelResult,
    IntentType,
    NotificationIntent,
    RecordingChannel,
    derive_notification_intents,
    intent_key,
    intents_for,
    record_all,
)
from backend.app.jobs.obligations import (
    AttentionReason,
    JobObligation,
    ObligationReason,
    ObligationState,
    ObligationType,
)
from backend.app.jobs.sla_policy import ASSUMED_DEMO_SLA_VERSION


T0 = datetime(2026, 9, 20, 6, 0, 0, tzinfo=timezone.utc)

MODULE = Path(__file__).resolve().parents[1] / "app" / "jobs" / "notifications.py"


def obligation(
    *,
    obligation_type=ObligationType.APPROVAL_PENDING,
    state=ObligationState.ESCALATED_L1,
    owed_role=ActorRole.AUTHORITY,
    escalation_level=1,
    anchor_event_id="EVT-ANCHOR",
    optimization_run_id="RUN-1",
    job_id="JOB-1",
    attention=None,
    attention_event_id=None,
) -> JobObligation:
    return JobObligation(
        job_id=job_id,
        obligation_type=obligation_type,
        state=state,
        owed_role=owed_role,
        evaluated_at=event_timestamp(T0),
        clock_started_at=event_timestamp(T0 - timedelta(hours=3)),
        due_at=event_timestamp(T0 - timedelta(hours=1)),
        escalation_level=escalation_level,
        elapsed_seconds=10800.0,
        sla_seconds=7200.0,
        policy_version=ASSUMED_DEMO_SLA_VERSION,
        policy_assumed=True,
        anchor_event_id=anchor_event_id,
        optimization_run_id=optimization_run_id,
        reason_code=ObligationReason.APPROVAL_AWAITING_AUTHORITY,
        reason="awaiting an authority decision",
        attention_required=attention is not None,
        attention_reason_code=attention,
        attention_event_id=attention_event_id,
        attention_role=None if attention is None else ActorRole.ENGINEER,
    )


# ----------------------------------------------------------------------
# Intent generation
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "obligation_type,intent_type,role",
    [
        (ObligationType.APPROVAL_PENDING, IntentType.APPROVAL_PENDING, "AUTHORITY"),
        (
            ObligationType.EXECUTION_START_PENDING,
            IntentType.EXECUTION_START_PENDING,
            "WORKER",
        ),
        (ObligationType.COMPLETION_PENDING, IntentType.COMPLETION_PENDING, "WORKER"),
    ],
)
def test_each_late_obligation_raises_an_intent_for_its_role(
    obligation_type, intent_type, role
):
    intents = intents_for(obligation(obligation_type=obligation_type))

    assert len(intents) == 1
    assert intents[0].intent_type is intent_type
    assert intents[0].recipient_role.value == role


def test_a_replanning_obligation_tells_an_engineer_even_with_no_sla():
    intents = intents_for(
        obligation(
            obligation_type=ObligationType.REPLANNING_PENDING,
            state=ObligationState.NO_SLA_DEFINED,
            owed_role=ActorRole.ENGINEER,
            escalation_level=0,
        )
    )

    assert len(intents) == 1
    assert intents[0].intent_type is IntentType.REPLANNING_PENDING
    assert intents[0].recipient_role is ActorRole.ENGINEER


@pytest.mark.parametrize(
    "state,raises",
    [
        (ObligationState.WITHIN_SLA, False),
        (ObligationState.DUE_SOON, True),
        (ObligationState.OVERDUE, True),
        (ObligationState.ESCALATED_L1, True),
        (ObligationState.ESCALATED_L3, True),
    ],
)
def test_only_an_obligation_worth_telling_someone_about_raises_an_intent(state, raises):
    level = 1 if state.value.startswith("ESCALATED") else 0

    intents = intents_for(obligation(state=state, escalation_level=level))

    assert bool(intents) is raises


def test_a_moot_obligation_never_raises_an_intent_at_any_level():
    """Telling a role it is late for work the SYSTEM withdrew is a false accusation."""

    for level in (0, 1, 2, 3):
        assert (
            intents_for(
                obligation(state=ObligationState.MOOT, escalation_level=level)
            )
            == []
        )


def test_a_job_with_no_obligation_raises_nothing():
    assert (
        intents_for(
            obligation(
                obligation_type=ObligationType.NONE,
                state=ObligationState.NONE,
                owed_role=None,
                escalation_level=0,
            )
        )
        == []
    )


def test_attention_raises_its_own_intent_alongside_the_obligations_own():
    intents = intents_for(
        obligation(
            attention=AttentionReason.COMMITTED_BLOCK_CONFLICT,
            attention_event_id="EVT-CONFLICT",
        )
    )

    assert [i.intent_type for i in intents] == [
        IntentType.APPROVAL_PENDING,
        IntentType.ATTENTION_REQUIRED,
    ]
    assert intents[1].recipient_role is ActorRole.ENGINEER
    assert intents[1].anchor_event_id == "EVT-CONFLICT"


def test_attention_is_raised_even_when_the_obligations_own_clock_is_fine():
    intents = intents_for(
        obligation(
            state=ObligationState.WITHIN_SLA,
            escalation_level=0,
            attention=AttentionReason.INTEGRITY_REFUSAL_RECORDED,
            attention_event_id="EVT-REFUSAL",
        )
    )

    assert [i.intent_type for i in intents] == [IntentType.ATTENTION_REQUIRED]


def test_an_intent_carries_everything_needed_to_explain_itself():
    intent = intents_for(obligation())[0]
    payload = intent.to_dict()

    assert payload["job_id"] == "JOB-1"
    assert payload["obligation_type"] == "APPROVAL_PENDING"
    assert payload["escalation_level"] == 1
    assert payload["policy_version"] == ASSUMED_DEMO_SLA_VERSION
    assert payload["policy_assumed"] is True
    assert payload["anchor_event_id"] == "EVT-ANCHOR"
    assert payload["optimization_run_id"] == "RUN-1"
    assert payload["recipient_role"] == "AUTHORITY"
    assert payload["generated_at"] == event_timestamp(T0)
    assert payload["due_at"]


def test_deriving_over_many_obligations_preserves_order():
    obligations = [
        obligation(job_id="JOB-1"),
        obligation(job_id="JOB-2", state=ObligationState.WITHIN_SLA, escalation_level=0),
        obligation(job_id="JOB-3"),
    ]

    intents = derive_notification_intents(obligations)

    assert [i.job_id for i in intents] == ["JOB-1", "JOB-3"]


# ----------------------------------------------------------------------
# Deterministic intent key
# ----------------------------------------------------------------------


def test_the_intent_key_is_sha256_over_the_five_approved_facts():
    import hashlib

    expected = hashlib.sha256(
        canonical_json(
            {
                "job_id": "JOB-1",
                "obligation_type": "APPROVAL_PENDING",
                "escalation_level": 1,
                "policy_version": ASSUMED_DEMO_SLA_VERSION,
                "anchor_event_id": "EVT-ANCHOR",
            }
        ).encode("utf-8")
    ).hexdigest()

    assert intents_for(obligation())[0].intent_key == expected
    assert re.fullmatch(r"[0-9a-f]{64}", expected)


def test_the_same_input_always_produces_the_same_key():
    assert intents_for(obligation())[0].intent_key == (
        intents_for(obligation())[0].intent_key
    )


def test_a_different_proposal_run_produces_a_different_key():
    """What makes a re-proposal a genuinely new notifiable fact."""

    first = intents_for(obligation(anchor_event_id="EVT-RUN-1"))[0]
    second = intents_for(obligation(anchor_event_id="EVT-RUN-2"))[0]

    assert first.intent_key != second.intent_key


@pytest.mark.parametrize(
    "field,value",
    [
        ("job_id", "JOB-OTHER"),
        ("obligation_type", "COMPLETION_PENDING"),
        ("escalation_level", 2),
        ("policy_version", "sla-assumed-demo-9.9"),
        ("anchor_event_id", "EVT-OTHER"),
    ],
)
def test_every_key_component_actually_changes_the_key(field, value):
    base = {
        "job_id": "JOB-1",
        "obligation_type": "APPROVAL_PENDING",
        "escalation_level": 1,
        "policy_version": ASSUMED_DEMO_SLA_VERSION,
        "anchor_event_id": "EVT-ANCHOR",
    }

    assert intent_key(**base) != intent_key(**{**base, field: value})


def test_each_escalation_level_is_its_own_notifiable_fact():
    keys = {
        intents_for(
            obligation(
                state=ObligationState[f"ESCALATED_L{level}"], escalation_level=level
            )
        )[0].intent_key
        for level in (1, 2, 3)
    }

    assert len(keys) == 3


def test_the_key_reuses_the_projects_one_canonical_json():
    """Sec 9.2: do not invent a second hashing convention."""

    source = MODULE.read_text(encoding="utf-8")

    assert "from backend.app.jobs.events import canonical_json" in source
    assert "json.dumps" not in source


# ----------------------------------------------------------------------
# Role addressing: never an individual
# ----------------------------------------------------------------------


def test_every_recipient_is_one_of_the_three_roles():
    assert {role.value for role in RECIPIENT_ROLES.values()} == {
        "AUTHORITY",
        "WORKER",
        "ENGINEER",
    }
    assert set(RECIPIENT_ROLES) == set(IntentType)


def test_an_intent_has_no_field_that_could_hold_an_individual():
    payload = intents_for(obligation())[0].to_dict()

    for forbidden in (
        "recipient_id",
        "actor_id",
        "assigned_to",
        "assignee",
        "phone",
        "email",
        "contact",
        "address",
        "user_id",
    ):
        assert forbidden not in payload


def test_the_summary_names_a_role_and_blames_nobody():
    summary = intents_for(obligation())[0].summary

    assert "AUTHORITY" in summary
    assert "AUTHORITY-017" not in summary
    assert "No individual is named or implicated." in summary


def test_the_module_builds_no_recipient_directory():
    source = MODULE.read_text(encoding="utf-8").lower()

    # Mentioned in prose (to say they do not exist), never as code.
    for forbidden in ("recipients = ", "directory = ", "contacts = "):
        assert forbidden not in source


# ----------------------------------------------------------------------
# RecordingChannel: zero I/O, and no delivery claim anywhere
# ----------------------------------------------------------------------


def test_the_recording_channel_records_and_claims_only_that():
    channel = RecordingChannel(clock=lambda: T0)
    intent = intents_for(obligation())[0]

    result = channel.send(intent)

    assert result.outcome is ChannelOutcome.RECORDING_ONLY
    assert result.channel == "RECORDING"
    assert result.intent_key == intent.intent_key
    assert result.attempted_at == event_timestamp(T0)
    assert "Nothing was transmitted" in result.detail


def test_no_channel_result_can_ever_claim_delivery():
    channel = RecordingChannel(clock=lambda: T0)

    result = channel.send(intents_for(obligation())[0])

    assert result.delivered is False
    assert result.to_dict()["delivered"] is False


def test_the_outcome_vocabulary_contains_no_delivered_or_read_member():
    values = {outcome.value for outcome in ChannelOutcome}

    assert values == {"RECORDING_ONLY", "REJECTED", "TRANSIENT_FAILURE"}
    assert not any(
        word in value for value in values for word in ("DELIVER", "READ", "SENT")
    )


def test_the_recording_channel_performs_no_external_io():
    """Proved structurally: the module imports nothing that could do I/O.

    The same idiom test_slice8_boundaries uses to pin a module's
    boundaries - reading the source, rather than trusting a docstring.
    """

    source = MODULE.read_text(encoding="utf-8")

    for forbidden in (
        "import requests",
        "import httpx",
        "import socket",
        "import urllib",
        "import smtplib",
        "import sqlite3",
        "import boto3",
        "twilio",
        "firebase",
        "open(",
        "subprocess",
        "os.environ",
    ):
        assert forbidden not in source, forbidden


def test_the_notifications_module_declares_no_provider_or_credential():
    source = MODULE.read_text(encoding="utf-8").lower()

    # Each appears in prose saying it does not exist; none may appear as
    # an assignment, a URL or a settings key.
    for forbidden in ("api_key", "auth_token", "https://", "account_sid", "sender_id"):
        assert forbidden not in source, forbidden


def test_the_channel_records_every_attempt_in_order_and_exposes_a_copy():
    channel = RecordingChannel(clock=lambda: T0)
    intents = derive_notification_intents(
        [obligation(job_id="JOB-1"), obligation(job_id="JOB-2")]
    )

    results = record_all(intents, channel)

    assert [r.intent_key for r in results] == [i.intent_key for i in intents]
    assert [i.job_id for i in channel.intents] == ["JOB-1", "JOB-2"]
    assert isinstance(channel.records, tuple)
    assert len(channel.records) == 2


def test_the_channel_satisfies_the_protocol_it_claims_to():
    from backend.app.jobs.notifications import NotificationChannel

    channel: NotificationChannel = RecordingChannel()

    assert channel.name == "RECORDING"
    assert isinstance(channel.send(intents_for(obligation())[0]), ChannelResult)


# ----------------------------------------------------------------------
# Deriving and recording change nothing
# ----------------------------------------------------------------------


def test_deriving_intents_does_not_mutate_the_obligations_it_reads():
    before = obligation()
    snapshot = before.to_dict()

    derive_notification_intents([before])

    assert before.to_dict() == snapshot


def test_an_intent_is_immutable_once_derived():
    from dataclasses import FrozenInstanceError

    intent = intents_for(obligation())[0]

    with pytest.raises(FrozenInstanceError):
        intent.escalation_level = 99


def test_there_is_no_notification_table_and_no_outbox():
    source = MODULE.read_text(encoding="utf-8")

    assert "CREATE TABLE" not in source.upper()
    assert "notification_attempts" in source  # named only as future work
    assert "INSERT INTO" not in source.upper()
