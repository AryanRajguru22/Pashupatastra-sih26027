"""Job lifecycle events: the per-job, append-only domain history (Slice 1).

THREE HISTORIES, ONE RELATIONSHIP
    1. Job lifecycle history (this module, table job_events)
       - per job: who did what to WHICH job, when, why, and the state
         before and after.
    2. Optimization run audit (backend.app.audit, table optimization_runs)
       - per run: the full solver request, result and provenance.
    3. System-wide security audit / hash chain (future security slice).

    They compose rather than overlap. A job event produced by an
    optimization carries `optimization_run_id`, which is the primary key
    of that run's optimization_runs row - the run's request and result
    are never copied into job history. A future hash chain will consume
    JobEvent.canonical_bytes() below; it does not need a different event
    definition.

WHAT AN EVENT RECORDS
    event_id, job_id, event_type, occurred_at, actor (id, role, kind,
    assurance), reason, optimization_run_id, before_state, after_state,
    metadata, schema_version. entity_type is fixed to "maintenance_job".

    Every event corresponds to a real service action. Nothing here emits
    events on its own; backend.app.jobs.lifecycle builds them from actual
    transitions and the repository persists them in the SAME transaction
    as the state change they describe, so history can never claim a
    transition that did not commit, or omit one that did.

CANONICAL FORM (hash-chain readiness, not a hash chain)
    canonical_bytes() is the exact byte string a future chain will hash
    as SHA256(canonical_bytes + previous_hash). It is deterministic by
    construction:
      - an explicit, fixed field set (no incidental attributes);
      - json.dumps(sort_keys=True, separators=(",", ":")) - the same
        sort_keys approach backend.app.audit.service.serialize_request
        uses, with compact separators so whitespace cannot vary;
      - UTF-8, ensure_ascii=False;
      - allow_nan=False, and metadata must already be plain JSON values
        (validated at construction) rather than being stringified by a
        `default=` fallback that could render the same value two ways;
      - timestamps in one fixed format (EVENT_TIMESTAMP_PATTERN);
      - actor role/kind/assurance and every state field are enum values,
        never free text.

    Storage-assigned `sequence` is deliberately NOT part of the canonical
    content: ordering will be protected by chaining each record to its
    predecessor's hash, which a canonical position field cannot do.
    No hash columns exist yet - a hash column with no writer would be
    dead schema.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Mapping, Optional

from backend.app.identity.actor import (
    Actor,
    ActorKind,
    ActorRole,
    IdentityAssurance,
)


EVENT_SCHEMA_VERSION = 1

ENTITY_TYPE_MAINTENANCE_JOB = "maintenance_job"

# UTC, microsecond precision, explicit +00:00 offset. One format only:
# datetime.isoformat() drops the fractional part when it is zero, which
# would make two equal-precision timestamps serialize differently.
EVENT_TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00$"
)


def event_timestamp(moment: Optional[datetime] = None) -> str:
    """Format a moment (default: now) in the one canonical event format."""

    moment = moment or datetime.now(timezone.utc)

    if moment.tzinfo is None:
        raise ValueError("Event timestamps must be timezone-aware.")

    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds")


def new_event_id() -> str:
    return f"EVT-{uuid.uuid4().hex.upper()}"


class JobEventType(str, Enum):
    """Every kind of job lifecycle event the backend emits.

    Human-initiated
      JOB_CREATED              a job was reported
      OPTIMIZATION_REQUESTED   an optimization that included this job was
                               requested (actor = the requester)
      SCHEDULE_ASSIGNED        a placement was written directly, not
                               proposed by the optimizer
      BLOCK_COMMITTED          the current proposal was committed/pinned
                               (the existing `notify` transition)
      JOB_COMPLETED            the job reached its terminal state
      PROPOSAL_REJECTED        an authority refused the CURRENT proposal
                               outright (Sprint 3 Slice 3); the job
                               returns to 'reported'
      PROPOSAL_POSTPONED       an authority deferred the CURRENT
                               proposal to a not-before date (Sprint 3
                               Slice 3); the job returns to 'reported'
                               with earliest_start_minute raised
      TRANSITION_REJECTED      a requested transition was refused; the
                               attempt is recorded, state is unchanged
      EXECUTION_STARTED        field work on the committed block started
                               (Sprint 3 Slice 5): notified -> in_progress
      EXECUTION_COMPLETED      field work finished with evidence:
                               in_progress -> completed
      EXECUTION_NOT_COMPLETED  field work could not be completed; the
                               commitment is released for replanning:
                               in_progress -> reported

    System decisions
      JOB_SCORED               priority/risk computed (SYSTEM:SCORER)
      OPTIMIZATION_COMPLETED   the solver returned for a run that
                               considered this job (SYSTEM:OPTIMIZER)
      OPTIMIZATION_FAILED      the run could not produce an outcome
      BLOCK_PROPOSED           a first proposal for a reported job
      BLOCK_REPROPOSED         a new proposal replaced the previous one
      OPTIMIZATION_REFUSED     a reported job was considered and refused
      PROPOSAL_INVALIDATED     a previous uncommitted proposal is no
                               longer valid and was withdrawn
      COMMITTED_BLOCK_PRESERVED  the run kept committed work pinned
      COMMITTED_BLOCK_CONFLICT   the run could not honour committed work;
                                 the commitment is kept and the conflict
                                 is made visible
    """

    JOB_CREATED = "JOB_CREATED"
    JOB_SCORED = "JOB_SCORED"
    OPTIMIZATION_REQUESTED = "OPTIMIZATION_REQUESTED"
    OPTIMIZATION_COMPLETED = "OPTIMIZATION_COMPLETED"
    OPTIMIZATION_FAILED = "OPTIMIZATION_FAILED"
    BLOCK_PROPOSED = "BLOCK_PROPOSED"
    BLOCK_REPROPOSED = "BLOCK_REPROPOSED"
    OPTIMIZATION_REFUSED = "OPTIMIZATION_REFUSED"
    PROPOSAL_INVALIDATED = "PROPOSAL_INVALIDATED"
    COMMITTED_BLOCK_PRESERVED = "COMMITTED_BLOCK_PRESERVED"
    COMMITTED_BLOCK_CONFLICT = "COMMITTED_BLOCK_CONFLICT"
    SCHEDULE_ASSIGNED = "SCHEDULE_ASSIGNED"
    BLOCK_COMMITTED = "BLOCK_COMMITTED"
    JOB_COMPLETED = "JOB_COMPLETED"
    PROPOSAL_REJECTED = "PROPOSAL_REJECTED"
    PROPOSAL_POSTPONED = "PROPOSAL_POSTPONED"
    TRANSITION_REJECTED = "TRANSITION_REJECTED"
    EXECUTION_STARTED = "EXECUTION_STARTED"
    EXECUTION_COMPLETED = "EXECUTION_COMPLETED"
    EXECUTION_NOT_COMPLETED = "EXECUTION_NOT_COMPLETED"


@dataclass(frozen=True)
class JobStateSnapshot:
    """The lifecycle-relevant state of one job at one moment.

    A fixed field set rather than a copy of the whole row: these are the
    facts a transition changes and an auditor needs to compare. Scores
    and report content are recorded on the events that produce them
    (JOB_CREATED, JOB_SCORED), not repeated in every snapshot.
    """

    status: str
    block_status: str
    is_committed: bool
    schedule_start_minute: Optional[int]
    schedule_end_minute: Optional[int]
    proposal_run_id: Optional[str]

    @classmethod
    def from_job(cls, job: Mapping[str, Any]) -> "JobStateSnapshot":
        block = job.get("block_candidate") or {}
        metadata = block.get("metadata") or {}

        return cls(
            status=str(job["status"]),
            block_status=str(block.get("status", "")),
            is_committed=bool(block.get("is_committed", False)),
            schedule_start_minute=_optional_int(
                job.get("schedule_start_minute")
            ),
            schedule_end_minute=_optional_int(job.get("schedule_end_minute")),
            proposal_run_id=metadata.get("proposal_run_id"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "block_status": self.block_status,
            "is_committed": self.is_committed,
            "schedule_start_minute": self.schedule_start_minute,
            "schedule_end_minute": self.schedule_end_minute,
            "proposal_run_id": self.proposal_run_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "JobStateSnapshot":
        return cls(
            status=data["status"],
            block_status=data["block_status"],
            is_committed=bool(data["is_committed"]),
            schedule_start_minute=_optional_int(data["schedule_start_minute"]),
            schedule_end_minute=_optional_int(data["schedule_end_minute"]),
            proposal_run_id=data["proposal_run_id"],
        )


def _optional_int(value: Any) -> Optional[int]:
    return None if value is None else int(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_json(value: Any) -> str:
    """Public alias of the canonical JSON form used by JobEvent.canonical_bytes().

    Exposed so other modules that need the SAME deterministic
    serialization (e.g. backend.app.jobs.proposal.BlockProposal.digest())
    do not re-implement it independently and risk a second, divergent
    spelling of "canonical JSON" in this codebase.
    """

    return _canonical_json(value)


@dataclass(frozen=True)
class JobEvent:
    """One immutable job lifecycle event. Validated at construction."""

    event_id: str
    job_id: str
    event_type: JobEventType
    occurred_at: str
    actor: Actor
    reason: Optional[str] = None
    optimization_run_id: Optional[str] = None
    before_state: Optional[JobStateSnapshot] = None
    after_state: Optional[JobStateSnapshot] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    schema_version: int = EVENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_type", JobEventType(self.event_type))

        if not self.event_id or not self.job_id:
            raise ValueError("event_id and job_id are required.")

        if not EVENT_TIMESTAMP_PATTERN.match(self.occurred_at):
            raise ValueError(
                f"occurred_at {self.occurred_at!r} is not in the canonical "
                "event format; build it with event_timestamp()."
            )

        if not isinstance(self.actor, Actor):
            raise TypeError("actor must be an Actor.")

        # Metadata must already be plain JSON. Round-tripping it proves
        # that, and stores a private copy so a caller mutating its own
        # dict afterwards cannot change a constructed event.
        try:
            normalized = json.loads(_canonical_json(dict(self.metadata)))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Event metadata must be plain JSON values: {exc}"
            ) from exc

        object.__setattr__(self, "metadata", normalized)

    @property
    def entity_type(self) -> str:
        return ENTITY_TYPE_MAINTENANCE_JOB

    def to_dict(self) -> Dict[str, Any]:
        """The fixed, canonical field set of this event."""

        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "entity_type": self.entity_type,
            "job_id": self.job_id,
            "event_type": self.event_type.value,
            "occurred_at": self.occurred_at,
            "actor": self.actor.to_dict(),
            "reason": self.reason,
            "optimization_run_id": self.optimization_run_id,
            "before_state": (
                None if self.before_state is None else self.before_state.to_dict()
            ),
            "after_state": (
                None if self.after_state is None else self.after_state.to_dict()
            ),
            "metadata": self.metadata,
        }

    def canonical_bytes(self) -> bytes:
        """Deterministic serialization for a future integrity chain."""

        return _canonical_json(self.to_dict()).encode("utf-8")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "JobEvent":
        actor = data["actor"]
        before = data.get("before_state")
        after = data.get("after_state")

        rebuilt = cls(
            event_id=data["event_id"],
            job_id=data["job_id"],
            event_type=JobEventType(data["event_type"]),
            occurred_at=data["occurred_at"],
            actor=Actor(
                actor_id=actor["actor_id"],
                role=ActorRole(actor["role"]),
                assurance=IdentityAssurance(actor["assurance"]),
            ),
            reason=data.get("reason"),
            optimization_run_id=data.get("optimization_run_id"),
            before_state=None if before is None else JobStateSnapshot.from_dict(before),
            after_state=None if after is None else JobStateSnapshot.from_dict(after),
            metadata=dict(data.get("metadata") or {}),
            schema_version=int(data.get("schema_version", EVENT_SCHEMA_VERSION)),
        )

        # kind is derived from role; a stored kind that disagrees is
        # corruption, not a second opinion.
        stored_kind = actor.get("kind")
        if stored_kind is not None and ActorKind(stored_kind) is not rebuilt.actor.kind:
            raise ValueError(
                f"Stored actor kind {stored_kind!r} contradicts role "
                f"{rebuilt.actor.role.value!r} for event {rebuilt.event_id!r}."
            )

        return rebuilt


def make_event(
    job_id: str,
    event_type: JobEventType,
    actor: Actor,
    *,
    occurred_at: Optional[str] = None,
    reason: Optional[str] = None,
    optimization_run_id: Optional[str] = None,
    before_state: Optional[JobStateSnapshot] = None,
    after_state: Optional[JobStateSnapshot] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> JobEvent:
    return JobEvent(
        event_id=new_event_id(),
        job_id=job_id,
        event_type=event_type,
        occurred_at=occurred_at or event_timestamp(),
        actor=actor,
        reason=reason,
        optimization_run_id=optimization_run_id,
        before_state=before_state,
        after_state=after_state,
        metadata=dict(metadata or {}),
    )


__all__ = [
    "ENTITY_TYPE_MAINTENANCE_JOB",
    "EVENT_SCHEMA_VERSION",
    "EVENT_TIMESTAMP_PATTERN",
    "JobEvent",
    "JobEventType",
    "JobStateSnapshot",
    "canonical_json",
    "event_timestamp",
    "make_event",
    "new_event_id",
]
