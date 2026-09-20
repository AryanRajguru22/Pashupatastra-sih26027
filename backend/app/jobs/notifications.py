"""Notification intent, and the channel seam it would be sent through
(Sprint 3 Slice 9).

*** NOTHING IN THIS MODULE DELIVERS A NOTIFICATION. ***

    There is no SMS, no email, no push, no WhatsApp, no webhook, no
    Twilio, no FCM, no SMTP, no provider and no credential anywhere in
    this repository, and this module adds none. The only channel that
    ships, RecordingChannel, performs ZERO external I/O: it appends an
    in-process record and returns. Its strongest truthful outcome is
    RECORDING_ONLY, and the vocabulary deliberately contains no
    DELIVERED, no READ and no SENT - an in-process function call is not
    a delivered notification, and even a real provider accepting a
    message would only ever justify ACCEPTED_BY_PROVIDER, never proof
    that a human received or read it.

THREE CONCEPTS, NEVER CONFLATED

    INTENT   "role AUTHORITY should be told that job J's approval is at
             escalation level 1." DERIVED - a pure function of committed
             events, policy and a clock. Needs no row, and gets none.

    ATTEMPT  "at 14:02:11Z a send was attempted via channel C." An
             external act, NOT derivable, and therefore durable when it
             exists. It does not exist yet.

    RESULT   "the provider accepted / refused / timed out." Also an
             external fact, also not derivable.

    Slice 9 implements INTENT only. No notification table, no outbox, no
    retry, no delivery status. See "THE FUTURE BOUNDARY" below.

WHY THERE IS NO OUTBOX
    The two canonical outbox failures do not arise here, because intent
    is derived rather than stored:

      - "the event committed but the send crashed" costs LATENCY, never
        an obligation: the next read recomputes the same intent from the
        same committed history, because the OBLIGATION is still true.
        That is strictly stronger than an outbox table, which can itself
        fail to be written.

      - "the send happened but the transaction rolled back" is
        structurally impossible: intents are computed only from events
        already committed to job_events (written inside the same
        BEGIN IMMEDIATE transaction as the state change), so a
        rolled-back transition leaves no event and produces no intent.

    The residual risk is the safe direction only: a send succeeding while
    its record fails gives a possible DUPLICATE, never a lost obligation
    - and that is what intent_key exists to let a future delivery layer
    de-duplicate against.

ROLE-ADDRESSED, NEVER PERSON-ADDRESSED
    recipient_role is AUTHORITY, WORKER or ENGINEER. There is no user
    table, no authority directory and no contact detail in this
    repository, so there is nothing to address an individual with, and an
    intent that named one would be fiction. An intent says a ROLE should
    be told that an OBLIGATION is late. It can never assert that a named
    person failed.

INTENTS INFORM; THEY DECIDE NOTHING
    Deriving, generating or "sending" an intent performs no transition,
    writes no event and changes no row. A late, duplicated, failed or
    out-of-order notification therefore cannot corrupt maintenance state.
    Every intent carries job_id, the anchoring event_id, the run id and
    the moment it was raised, so a recipient (and an auditor) can see it
    describes a PAST moment - a notification is a statement about an
    instant, never a command and never current truth.

THE FUTURE BOUNDARY (deferred, specified)
    When real delivery ships it needs exactly ONE new append-only table,
    `notification_attempts`, carrying the intent_key with a UNIQUE
    constraint (the same idiom job_events.event_id UNIQUE already uses
    for idempotent intake), the policy_version that justified the send,
    and the provider's own result. It gets install_append_only_guards
    like every other durable table here. A table for ATTEMPTS is
    justified because delivery is not derivable; a table for INTENTS
    never is.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Optional, Protocol, Sequence

from backend.app.identity.actor import ActorRole
from backend.app.jobs.events import canonical_json, event_timestamp
from backend.app.jobs.obligations import (
    JobObligation,
    ObligationState,
    ObligationType,
)


class IntentType(str, Enum):
    """What a recipient would be told. Mirrors the obligation vocabulary."""

    APPROVAL_PENDING = "APPROVAL_PENDING"
    EXECUTION_START_PENDING = "EXECUTION_START_PENDING"
    COMPLETION_PENDING = "COMPLETION_PENDING"
    REPLANNING_PENDING = "REPLANNING_PENDING"
    #: Raised from an obligation's attention flag, independently of its
    #: own clock - see backend.app.jobs.obligations.AttentionReason.
    ATTENTION_REQUIRED = "ATTENTION_REQUIRED"


#: Which role would be told, per intent type. The only addressing this
#: system is capable of - see the module docstring.
RECIPIENT_ROLES: dict[IntentType, ActorRole] = {
    IntentType.APPROVAL_PENDING: ActorRole.AUTHORITY,
    IntentType.EXECUTION_START_PENDING: ActorRole.WORKER,
    IntentType.COMPLETION_PENDING: ActorRole.WORKER,
    IntentType.REPLANNING_PENDING: ActorRole.ENGINEER,
    IntentType.ATTENTION_REQUIRED: ActorRole.ENGINEER,
}

_FROM_OBLIGATION: dict[ObligationType, IntentType] = {
    ObligationType.APPROVAL_PENDING: IntentType.APPROVAL_PENDING,
    ObligationType.EXECUTION_START_PENDING: IntentType.EXECUTION_START_PENDING,
    ObligationType.COMPLETION_PENDING: IntentType.COMPLETION_PENDING,
    ObligationType.REPLANNING_PENDING: IntentType.REPLANNING_PENDING,
}


class ChannelOutcome(str, Enum):
    """What a channel can truthfully claim about one attempt.

    RECORDING_ONLY is the strongest claim anything in this repository can
    make today, and it is deliberately the ONLY member that is not a
    failure. There is no DELIVERED and no READ, because no code here can
    establish either. When a real provider is integrated, the honest
    member to add is ACCEPTED_BY_PROVIDER - provider acceptance, which is
    still not human receipt.
    """

    #: Recorded in process. Nothing left this application. NOT delivered.
    RECORDING_ONLY = "RECORDING_ONLY"
    #: Reserved vocabulary for a future real channel.
    REJECTED = "REJECTED"
    TRANSIENT_FAILURE = "TRANSIENT_FAILURE"


def intent_key(
    *,
    job_id: str,
    obligation_type: str,
    escalation_level: int,
    policy_version: str,
    anchor_event_id: Optional[str],
) -> str:
    """The deterministic identity of one notification intent.

    SHA-256 over the canonical JSON of exactly five facts. Built with
    backend.app.jobs.events.canonical_json and hashed the same way
    backend.app.jobs.execution.block_identity_digest is, so this codebase
    keeps ONE spelling of "canonical JSON" and one hashing convention,
    not a second that could silently diverge.

    anchor_event_id is what makes the key stable across repeated reads of
    an unchanged situation and DIFFERENT after a re-proposal: a new run
    writes a new placement event, so the same job at the same escalation
    level under the same policy keys differently once the decision it
    refers to has changed. escalation_level is included so each level is
    its own notifiable fact rather than a repeat of the last one.

    No notification table exists in Slice 9. This key is for
    deterministic identity now, and for the UNIQUE de-duplication
    constraint a future delivery layer will enforce it with.
    """

    payload = {
        "job_id": job_id,
        "obligation_type": obligation_type,
        "escalation_level": escalation_level,
        "policy_version": policy_version,
        "anchor_event_id": anchor_event_id,
    }

    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class NotificationIntent:
    """One thing a role would be told, derived from one obligation.

    Carries everything needed to explain itself without a second lookup:
    which job, which obligation, at what level, under which policy
    version, anchored to which event and which optimization run, and when
    it was raised. Not persisted, and never persisted - see the module
    docstring.
    """

    intent_key: str
    intent_type: IntentType
    recipient_role: ActorRole

    job_id: str
    obligation_type: ObligationType
    obligation_state: ObligationState
    escalation_level: int

    policy_version: str
    policy_assumed: bool

    anchor_event_id: Optional[str]
    optimization_run_id: Optional[str]

    generated_at: str
    due_at: Optional[str]
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_key": self.intent_key,
            "intent_type": self.intent_type.value,
            "recipient_role": self.recipient_role.value,
            "job_id": self.job_id,
            "obligation_type": self.obligation_type.value,
            "obligation_state": self.obligation_state.value,
            "escalation_level": self.escalation_level,
            "policy_version": self.policy_version,
            "policy_assumed": self.policy_assumed,
            "anchor_event_id": self.anchor_event_id,
            "optimization_run_id": self.optimization_run_id,
            "generated_at": self.generated_at,
            "due_at": self.due_at,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class ChannelResult:
    """What one channel reported about one attempt. Never a delivery claim."""

    channel: str
    outcome: ChannelOutcome
    intent_key: str
    attempted_at: str
    detail: str = ""

    @property
    def delivered(self) -> bool:
        """Always False. No channel in this repository can deliver anything.

        Present so a caller asking the question gets an honest answer
        instead of inferring delivery from a non-failure outcome.
        """

        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "outcome": self.outcome.value,
            "intent_key": self.intent_key,
            "attempted_at": self.attempted_at,
            "detail": self.detail,
            "delivered": self.delivered,
        }


class NotificationChannel(Protocol):
    """Anything that could carry a notification intent outward.

    Mirrors the backend.app.data.train_provider.TrainDataProvider
    precedent: the seam is designed and exercised; no provider is
    integrated. A future channel needs a recipient address (which does
    not exist - there is no directory), credentials, rate limits, cost
    control and a data-protection review. None of that is in scope.
    """

    name: str

    def send(self, intent: NotificationIntent) -> ChannelResult:
        """Attempt one intent. Must never raise for an ordinary failure."""
        ...


class RecordingChannel:
    """The only channel that ships. Performs ZERO external I/O.

    It opens no socket, resolves no host, reads no credential, imports no
    provider SDK and touches no file or database. It appends the attempt
    to an in-process list and returns RECORDING_ONLY, which is exactly
    what happened and nothing more.

    That is deliberately useful: it exercises the seam, it makes the
    accountability view demonstrable, and it cannot pretend an SMS was
    sent. Everything it records says NOT DELIVERED on its face.
    """

    name = "RECORDING"

    def __init__(self, clock=None):
        # Injectable for the same reason JobService.clock is: a test
        # asserts on the recorded moment without sleeping or patching.
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._records: list[ChannelResult] = []
        self._intents: list[NotificationIntent] = []

    @property
    def records(self) -> tuple[ChannelResult, ...]:
        """Every attempt, oldest first. A copy: callers cannot rewrite it."""

        return tuple(self._records)

    @property
    def intents(self) -> tuple[NotificationIntent, ...]:
        """Every intent handed to this channel, oldest first."""

        return tuple(self._intents)

    def send(self, intent: NotificationIntent) -> ChannelResult:
        result = ChannelResult(
            channel=self.name,
            outcome=ChannelOutcome.RECORDING_ONLY,
            intent_key=intent.intent_key,
            attempted_at=event_timestamp(self._clock()),
            detail=(
                "Recorded in process only. Nothing was transmitted: this "
                "deployment has no notification provider, and no recipient "
                "directory exists to address one with."
            ),
        )

        self._intents.append(intent)
        self._records.append(result)

        return result


# ----------------------------------------------------------------------
# Derivation (pure - a future sweep calls exactly this)
# ----------------------------------------------------------------------


def _summary(intent_type: IntentType, obligation: JobObligation) -> str:
    """Prose about a ROLE and an OBLIGATION. Never about a person."""

    role = RECIPIENT_ROLES[intent_type].value

    if intent_type is IntentType.ATTENTION_REQUIRED:
        reason = (
            "an unspecified condition"
            if obligation.attention_reason_code is None
            else obligation.attention_reason_code.value
        )

        return (
            f"Role {role} should review job {obligation.job_id}: {reason} was "
            "recorded and needs human reconciliation."
        )

    return (
        f"Role {role} should be told that job {obligation.job_id} has an "
        f"outstanding {obligation.obligation_type.value} obligation "
        f"(state {obligation.state.value}, escalation level "
        f"{obligation.escalation_level}). No individual is named or implicated."
    )


def _intent(
    intent_type: IntentType,
    obligation: JobObligation,
    *,
    anchor_event_id: Optional[str],
    escalation_level: int,
) -> NotificationIntent:
    return NotificationIntent(
        intent_key=intent_key(
            job_id=obligation.job_id,
            obligation_type=intent_type.value,
            escalation_level=escalation_level,
            policy_version=obligation.policy_version,
            anchor_event_id=anchor_event_id,
        ),
        intent_type=intent_type,
        recipient_role=RECIPIENT_ROLES[intent_type],
        job_id=obligation.job_id,
        obligation_type=obligation.obligation_type,
        obligation_state=obligation.state,
        escalation_level=escalation_level,
        policy_version=obligation.policy_version,
        policy_assumed=obligation.policy_assumed,
        anchor_event_id=anchor_event_id,
        optimization_run_id=obligation.optimization_run_id,
        generated_at=obligation.evaluated_at,
        due_at=obligation.due_at,
        summary=_summary(intent_type, obligation),
    )


def intents_for(obligation: JobObligation) -> list[NotificationIntent]:
    """Every intent one obligation raises right now. Pure; raises nothing.

    An obligation raises its own intent once it is worth telling somebody
    about - DUE_SOON, past due at any escalation level, or owed with no
    allowance defined (NO_SLA_DEFINED). A WITHIN_SLA obligation raises
    none: nobody is late, and notifying about it would be noise.

    A MOOT obligation raises NOTHING, ever. The system withdrew the work;
    telling a role it is late would be a false accusation.

    The attention condition is INDEPENDENT of the obligation's own clock,
    so a job can raise both its obligation's intent and an
    ATTENTION_REQUIRED one - they are different facts addressed to
    different roles.
    """

    raised: list[NotificationIntent] = []

    intent_type = _FROM_OBLIGATION.get(obligation.obligation_type)
    notifiable = (
        obligation.is_due_soon
        or obligation.is_past_due
        or obligation.state is ObligationState.NO_SLA_DEFINED
    )

    if (
        intent_type is not None
        and obligation.state is not ObligationState.MOOT
        and notifiable
    ):
        raised.append(
            _intent(
                intent_type,
                obligation,
                anchor_event_id=obligation.anchor_event_id,
                escalation_level=obligation.escalation_level,
            )
        )

    if obligation.attention_required:
        raised.append(
            _intent(
                IntentType.ATTENTION_REQUIRED,
                obligation,
                anchor_event_id=obligation.attention_event_id,
                # Attention is a condition, not a clock: it has no level.
                escalation_level=0,
            )
        )

    return raised


def derive_notification_intents(
    obligations: Iterable[JobObligation],
) -> list[NotificationIntent]:
    """Every intent a set of obligations raises. Pure, ordered, side-effect free.

    Derives only; it never sends. Handing the result to a channel is a
    separate, explicit step - see RecordingChannel - precisely so that
    reading the accountability view cannot cause a notification.
    """

    raised: list[NotificationIntent] = []

    for obligation in obligations:
        raised.extend(intents_for(obligation))

    return raised


def record_all(
    intents: Sequence[NotificationIntent],
    channel: NotificationChannel,
) -> list[ChannelResult]:
    """Hand every intent to one channel, in order. Returns what it claimed."""

    return [channel.send(intent) for intent in intents]


__all__ = [
    "RECIPIENT_ROLES",
    "ChannelOutcome",
    "ChannelResult",
    "IntentType",
    "NotificationChannel",
    "NotificationIntent",
    "RecordingChannel",
    "derive_notification_intents",
    "intent_key",
    "intents_for",
    "record_all",
]
