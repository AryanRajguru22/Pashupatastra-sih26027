"""Field execution of an approved block, and its evidence (Sprint 3 Slice 5).

WHAT THIS IS
    The pure domain representation of executing a committed block:
      - EvidenceItem, the caller-observed facts of one piece of evidence,
        validated at construction;
      - RecordedEvidence, that evidence as stored inside the execution
        event that carried it (server-minted evidence_id and phase);
      - ExecutionRecord, one execution attempt, DERIVED from the job's
        append-only history by build_execution_records;
      - committed_block_digest, the one digest of a block's stable
        identity/placement, shared with
        backend.app.jobs.proposal.BlockProposal.digest;
      - the observation-time rules (explicit offset, future skew, the
        horizon-relative actual minutes).

    There is no job_executions table. An execution is reconstructed from
    EXECUTION_STARTED / EXECUTION_COMPLETED / EXECUTION_NOT_COMPLETED
    events exactly as backend.app.jobs.proposal reconstructs a
    BlockProposal: the events have to be written for the audit anyway,
    and a second, independently-written store would be a second source of
    truth. The only row-level marker is block_candidate.metadata.
    execution_id (EXECUTION_ID_KEY), which the pure lifecycle plans use
    for stale checks without I/O.

    The transitions themselves (plan_execution_start / _complete /
    _not_completed) live in backend.app.jobs.lifecycle beside every other
    plan. This module does not import lifecycle, so lifecycle can import
    it.

HISTORY IS CHECKED, NEVER REPAIRED
    build_execution_records refuses (ExecutionIntegrityError) any history
    that does not describe a coherent sequence of executions - an outcome
    without its start, two outcomes for one execution, a start while
    another execution is still open, mismatched run / job / proposal
    identity, malformed metadata, or a job row that disagrees with the
    history about which execution is open. It never guesses what an
    inconsistent history "meant".
"""

from __future__ import annotations

import hashlib
import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from backend.app.data.horizon_anchor import horizon_relative_minutes, parse_horizon_start
from backend.app.jobs.events import JobEvent, JobEventType, canonical_json, event_timestamp
from backend.app.jobs.models import JobStatus


# The block_candidate.metadata key naming a job's open (or, once
# completed, final) execution. Re-exported by backend.app.jobs.lifecycle.
EXECUTION_ID_KEY = "execution_id"

EXECUTION_ID_PATTERN = re.compile(r"^EXE-[0-9A-F]{32}$")
EVIDENCE_ID_PATTERN = re.compile(r"^EVD-[0-9A-F]{32}$")

MAX_EVIDENCE_ITEMS = 10
MAX_EVIDENCE_REFERENCE_LENGTH = 200
MAX_EVIDENCE_NOTE_LENGTH = 500

# How far past the recording moment an observed timestamp may lie before
# it is refused as a claim about the future.
MAX_OBSERVATION_FUTURE_SKEW = timedelta(minutes=5)

EXECUTION_EVENT_TYPES = frozenset(
    {
        JobEventType.EXECUTION_STARTED,
        JobEventType.EXECUTION_COMPLETED,
        JobEventType.EXECUTION_NOT_COMPLETED,
    }
)


class EvidenceValidationError(ValueError):
    """Evidence or an observed timestamp breaks an execution evidence rule.

    A ValueError so a request layer maps it to 400. The lifecycle plans
    re-raise it as InvalidTransitionError so a refused transition is
    recorded like every other invalid one.
    """


class ExecutionIntegrityError(RuntimeError):
    """Stored execution history is not a coherent sequence of executions.

    Like backend.app.jobs.lifecycle.CommittedStateIntegrityError, this is
    detected, never repaired: nothing is derived from, or written on top
    of, a history whose meaning is unknown.
    """

    def __init__(self, message: str, job_id: str):
        super().__init__(message)
        self.job_id = job_id


class EvidenceKind(str, Enum):
    PHOTO = "PHOTO"
    VIDEO = "VIDEO"
    DOCUMENT = "DOCUMENT"
    MEASUREMENT = "MEASUREMENT"


class EvidencePhase(str, Enum):
    BEFORE_WORK = "BEFORE_WORK"
    AFTER_WORK = "AFTER_WORK"
    NOT_COMPLETED = "NOT_COMPLETED"


class ExecutionStatus(str, Enum):
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    NOT_COMPLETED = "NOT_COMPLETED"


def new_execution_id() -> str:
    """EXE-<uuid4 hex, upper>. Minted by the caller of a start plan, never inside it."""

    return f"EXE-{uuid.uuid4().hex.upper()}"


def new_evidence_id() -> str:
    return f"EVD-{uuid.uuid4().hex.upper()}"


def is_execution_id(value: Any) -> bool:
    return isinstance(value, str) and bool(EXECUTION_ID_PATTERN.match(value))


def proposal_id_for(optimization_run_id: str, job_id: str) -> str:
    """The one spelling of a proposal's identity."""

    return f"PROP-{optimization_run_id}-{job_id}"


# ----------------------------------------------------------------------
# Digest
# ----------------------------------------------------------------------


def block_identity_digest(
    *,
    proposal_id: str,
    job_id: str,
    optimization_run_id: str,
    corridor_id: str,
    track_id: str,
    section_id: Optional[str],
    start_minute: int,
    end_minute: int,
    duration_minutes: int,
    work_type: str,
) -> str:
    """SHA-256 hex over a block's stable identity and placement.

    The single implementation behind BlockProposal.digest() and
    committed_block_digest(). Scores, explanation, provenance, generated_at,
    execution timestamps, evidence and actors are deliberately absent: the
    digest says WHICH placement this is, nothing else. Uses the same
    canonical JSON as JobEvent.canonical_bytes().
    """

    payload = {
        "proposal_id": proposal_id,
        "job_id": job_id,
        "optimization_run_id": optimization_run_id,
        "corridor_id": corridor_id,
        "track_id": track_id,
        "section_id": section_id,
        "start_minute": start_minute,
        "end_minute": end_minute,
        "duration_minutes": duration_minutes,
        "work_type": work_type,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def committed_block_digest(
    job: Mapping[str, Any],
    *,
    corridor_id: str,
    optimization_run_id: str,
) -> str:
    """The digest of a job row's CURRENT committed block.

    Built from the same fields, sourced the same way, as
    backend.app.jobs.proposal.build_block_proposal: the placement comes
    from the schedule columns and duration is their difference. So the
    digest captured when execution starts equals the digest of the
    proposal that was approved, and recomputing it at the outcome
    reproduces it unless the committed block changed.
    """

    start = int(job["schedule_start_minute"])
    end = int(job["schedule_end_minute"])
    block = job.get("block_candidate") or {}

    return block_identity_digest(
        proposal_id=proposal_id_for(optimization_run_id, job["job_id"]),
        job_id=job["job_id"],
        optimization_run_id=optimization_run_id,
        corridor_id=corridor_id,
        track_id=job["track_id"],
        section_id=block.get("section_id"),
        start_minute=start,
        end_minute=end,
        duration_minutes=end - start,
        work_type=job["work_type"],
    )


# ----------------------------------------------------------------------
# Observed time
# ----------------------------------------------------------------------


def parse_observed_at(value: Union[str, datetime], field_name: str) -> datetime:
    """An observed moment: ISO-8601 with an explicit offset, or an aware datetime."""

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError as exc:
            raise EvidenceValidationError(
                f"{field_name} must be an ISO-8601 datetime with an explicit "
                f"UTC offset, got {value!r}"
            ) from exc
    else:
        raise EvidenceValidationError(
            f"{field_name} must be an ISO-8601 datetime string, got {value!r}"
        )

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EvidenceValidationError(
            f"{field_name} must carry an explicit UTC offset (e.g. '+05:30'); "
            f"got naive {value!r}"
        )

    return parsed


def canonical_observed_at(moment: datetime) -> str:
    """The canonical UTC event-timestamp form of an observed moment."""

    return event_timestamp(moment)


def require_not_in_future(moment: datetime, now: datetime, field_name: str) -> None:
    if moment > now + MAX_OBSERVATION_FUTURE_SKEW:
        raise EvidenceValidationError(
            f"{field_name} {canonical_observed_at(moment)} is more than "
            f"{int(MAX_OBSERVATION_FUTURE_SKEW.total_seconds() // 60)} minutes "
            f"after the recording time {canonical_observed_at(now)}"
        )


def observed_minute(moment: datetime, horizon_start: str, *, ceil: bool) -> int:
    """The horizon-relative minute of an observed moment.

    Delegates to horizon_relative_minutes, the one authoritative
    conversion: the moment is first expressed in horizon_start's OWN
    offset, whose calendar date and clock minute are then converted.
    floor (ceil=False) drops the sub-minute part - for an actual start;
    ceil adds one minute when there is one - for an actual end. Never
    clamped to the horizon.
    """

    anchor = parse_horizon_start(horizon_start)
    local = moment.astimezone(anchor.tzinfo)
    minute = horizon_relative_minutes(
        local.date(), local.hour * 60 + local.minute, 0, anchor
    )

    if ceil and (local.second or local.microsecond):
        minute += 1

    return minute


# ----------------------------------------------------------------------
# Evidence
# ----------------------------------------------------------------------


def _optional_coordinate(value: Any, name: str, bound: float) -> Optional[float]:
    if value is None:
        return None

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceValidationError(f"{name} must be a number, got {value!r}")

    number = float(value)

    if not math.isfinite(number) or not -bound <= number <= bound:
        raise EvidenceValidationError(
            f"{name} must be within [-{bound:g}, {bound:g}], got {value!r}"
        )

    return number


@dataclass(frozen=True)
class EvidenceItem:
    """One piece of caller-supplied evidence, validated and normalized.

    Only these five facts are caller-controlled. evidence_id and phase are
    assigned when the evidence is recorded (RecordedEvidence); who recorded
    it and when are the carrying event's actor and occurred_at. There is no
    free-form metadata.

    captured_at is normalized to the canonical UTC event-timestamp form.
    """

    evidence_reference: str
    evidence_kind: EvidenceKind
    captured_at: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    note: Optional[str] = None

    def __post_init__(self) -> None:
        reference = self.evidence_reference

        if not isinstance(reference, str):
            raise EvidenceValidationError(
                f"evidence_reference must be a string, got {reference!r}"
            )

        reference = reference.strip()

        if not reference:
            raise EvidenceValidationError("evidence_reference must not be blank")

        if len(reference) > MAX_EVIDENCE_REFERENCE_LENGTH:
            raise EvidenceValidationError(
                f"evidence_reference must be at most "
                f"{MAX_EVIDENCE_REFERENCE_LENGTH} characters"
            )

        try:
            kind = EvidenceKind(self.evidence_kind)
        except ValueError as exc:
            raise EvidenceValidationError(
                f"evidence_kind {self.evidence_kind!r} is not one of "
                f"{[member.value for member in EvidenceKind]}"
            ) from exc

        captured = canonical_observed_at(parse_observed_at(self.captured_at, "captured_at"))

        latitude = _optional_coordinate(self.latitude, "latitude", 90.0)
        longitude = _optional_coordinate(self.longitude, "longitude", 180.0)

        if (latitude is None) != (longitude is None):
            raise EvidenceValidationError(
                "latitude and longitude must be given together or not at all"
            )

        if self.note is not None:
            if not isinstance(self.note, str):
                raise EvidenceValidationError(f"note must be a string, got {self.note!r}")

            if len(self.note) > MAX_EVIDENCE_NOTE_LENGTH:
                raise EvidenceValidationError(
                    f"note must be at most {MAX_EVIDENCE_NOTE_LENGTH} characters"
                )

        object.__setattr__(self, "evidence_reference", reference)
        object.__setattr__(self, "evidence_kind", kind)
        object.__setattr__(self, "captured_at", captured)
        object.__setattr__(self, "latitude", latitude)
        object.__setattr__(self, "longitude", longitude)

    @property
    def captured_moment(self) -> datetime:
        return datetime.fromisoformat(self.captured_at)


@dataclass(frozen=True)
class RecordedEvidence:
    """Evidence as stored inside an execution event's metadata."""

    evidence_id: str
    phase: EvidencePhase
    evidence_reference: str
    evidence_kind: EvidenceKind
    captured_at: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    note: Optional[str] = None

    @classmethod
    def record(
        cls, item: EvidenceItem, phase: EvidencePhase, evidence_id: str
    ) -> "RecordedEvidence":
        return cls(
            evidence_id=evidence_id,
            phase=EvidencePhase(phase),
            evidence_reference=item.evidence_reference,
            evidence_kind=item.evidence_kind,
            captured_at=item.captured_at,
            latitude=item.latitude,
            longitude=item.longitude,
            note=item.note,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "phase": self.phase.value,
            "evidence_reference": self.evidence_reference,
            "evidence_kind": self.evidence_kind.value,
            "captured_at": self.captured_at,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RecordedEvidence":
        """Strictly rebuild stored evidence; any deviation is EvidenceValidationError."""

        expected = {
            "evidence_id",
            "phase",
            "evidence_reference",
            "evidence_kind",
            "captured_at",
            "latitude",
            "longitude",
            "note",
        }

        if not isinstance(data, Mapping) or set(data) != expected:
            raise EvidenceValidationError(
                f"stored evidence must have exactly the keys {sorted(expected)}"
            )

        evidence_id = data["evidence_id"]

        if not isinstance(evidence_id, str) or not EVIDENCE_ID_PATTERN.match(evidence_id):
            raise EvidenceValidationError(f"invalid stored evidence_id {evidence_id!r}")

        try:
            phase = EvidencePhase(data["phase"])
        except ValueError as exc:
            raise EvidenceValidationError(
                f"invalid stored evidence phase {data['phase']!r}"
            ) from exc

        item = EvidenceItem(
            evidence_reference=data["evidence_reference"],
            evidence_kind=data["evidence_kind"],
            captured_at=data["captured_at"],
            latitude=data["latitude"],
            longitude=data["longitude"],
            note=data["note"],
        )
        recorded = cls.record(item, phase, evidence_id)

        if recorded.to_dict() != dict(data):
            raise EvidenceValidationError(
                f"stored evidence {evidence_id!r} is not in normalized form"
            )

        return recorded

    @property
    def captured_moment(self) -> datetime:
        return datetime.fromisoformat(self.captured_at)


def validate_evidence_set(
    items: Sequence[EvidenceItem],
    *,
    phase: EvidencePhase,
    now: datetime,
    minimum: int,
    captured_not_after: Optional[datetime] = None,
    captured_not_before: Optional[datetime] = None,
    forbidden_references: Sequence[str] = (),
) -> Tuple[EvidenceItem, ...]:
    """The collection-level rules for one transition's evidence.

    minimum..MAX_EVIDENCE_ITEMS items; unique references; every captured_at
    not in the future and inside the given bounds; no reference that
    appears in forbidden_references (a before-work reference re-submitted
    as after-work proves nothing).
    """

    phase = EvidencePhase(phase)
    items = tuple(items)

    for item in items:
        if not isinstance(item, EvidenceItem):
            raise EvidenceValidationError(
                f"{phase.value} evidence must be EvidenceItem values, got {item!r}"
            )

    if not minimum <= len(items) <= MAX_EVIDENCE_ITEMS:
        raise EvidenceValidationError(
            f"{phase.value} evidence requires {minimum}-{MAX_EVIDENCE_ITEMS} "
            f"items; got {len(items)}"
        )

    references = [item.evidence_reference for item in items]
    duplicates = sorted({ref for ref in references if references.count(ref) > 1})

    if duplicates:
        raise EvidenceValidationError(
            f"{phase.value} evidence references must be unique; repeated: {duplicates}"
        )

    reused = sorted(set(references) & set(forbidden_references))

    if reused:
        raise EvidenceValidationError(
            f"{phase.value} evidence cannot reuse before-work evidence "
            f"references of the same execution: {reused}"
        )

    for item in items:
        captured = item.captured_moment
        label = f"{phase.value} evidence {item.evidence_reference!r} captured_at"
        require_not_in_future(captured, now, label)

        if captured_not_after is not None and captured > captured_not_after:
            raise EvidenceValidationError(
                f"{label} {item.captured_at} is after "
                f"{canonical_observed_at(captured_not_after)}"
            )

        if captured_not_before is not None and captured < captured_not_before:
            raise EvidenceValidationError(
                f"{label} {item.captured_at} is before "
                f"{canonical_observed_at(captured_not_before)}"
            )

    return items


def record_evidence(
    items: Sequence[EvidenceItem], phase: EvidencePhase
) -> List[Dict[str, Any]]:
    """Assign evidence ids and a phase; the JSON-safe form stored in event metadata."""

    return [
        RecordedEvidence.record(item, phase, new_evidence_id()).to_dict()
        for item in items
    ]


# ----------------------------------------------------------------------
# ExecutionRecord
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionDeviations:
    """Departures from the approved window: recorded, never enforced."""

    started_before_planned_start: bool
    ended_after_planned_end: Optional[bool] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "started_before_planned_start": self.started_before_planned_start,
            "ended_after_planned_end": self.ended_after_planned_end,
        }


@dataclass(frozen=True)
class ExecutionRecord:
    """One execution attempt of one approved block, derived from job history.

    No created_at / updated_at: the start and outcome events' occurred_at
    (started_recorded_at / ended_recorded_at) are those facts, immutably.
    """

    execution_id: str
    job_id: str
    attempt_number: int
    optimization_run_id: str
    proposal_id: str
    track_id: str
    section_id: Optional[str]
    planned_start_minute: int
    planned_end_minute: int
    committed_block_digest: str
    status: ExecutionStatus

    started_by: Dict[str, str]
    started_recorded_at: str
    actual_start_at: str
    actual_start_minute: int
    before_work_evidence: Tuple[RecordedEvidence, ...]

    ended_by: Optional[Dict[str, str]]
    ended_recorded_at: Optional[str]
    actual_end_at: Optional[str]
    actual_end_minute: Optional[int]
    after_work_evidence: Tuple[RecordedEvidence, ...]
    not_completed_reason: Optional[str]
    failure_evidence: Tuple[RecordedEvidence, ...]

    deviations: ExecutionDeviations

    @property
    def is_open(self) -> bool:
        return self.status is ExecutionStatus.STARTED

    @property
    def actual_start_moment(self) -> datetime:
        return datetime.fromisoformat(self.actual_start_at)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "job_id": self.job_id,
            "attempt_number": self.attempt_number,
            "optimization_run_id": self.optimization_run_id,
            "proposal_id": self.proposal_id,
            "track_id": self.track_id,
            "section_id": self.section_id,
            "planned_start_minute": self.planned_start_minute,
            "planned_end_minute": self.planned_end_minute,
            "committed_block_digest": self.committed_block_digest,
            "status": self.status.value,
            "started_by": dict(self.started_by),
            "started_recorded_at": self.started_recorded_at,
            "actual_start_at": self.actual_start_at,
            "actual_start_minute": self.actual_start_minute,
            "before_work_evidence": [e.to_dict() for e in self.before_work_evidence],
            "ended_by": None if self.ended_by is None else dict(self.ended_by),
            "ended_recorded_at": self.ended_recorded_at,
            "actual_end_at": self.actual_end_at,
            "actual_end_minute": self.actual_end_minute,
            "after_work_evidence": [e.to_dict() for e in self.after_work_evidence],
            "not_completed_reason": self.not_completed_reason,
            "failure_evidence": [e.to_dict() for e in self.failure_evidence],
            "deviations": self.deviations.to_dict(),
        }


def _event_of(item: Any) -> JobEvent:
    """Accept a StoredJobEvent (history read) or a bare JobEvent (a plan's output)."""

    event = getattr(item, "event", item)

    if not isinstance(event, JobEvent):
        raise TypeError(f"expected a JobEvent or StoredJobEvent, got {item!r}")

    return event


class _Reader:
    """Typed access to one event's metadata; any deviation is an integrity error."""

    def __init__(self, job_id: str, event: JobEvent):
        self.job_id = job_id
        self.event = event
        self.metadata = event.metadata

    def fail(self, problem: str) -> ExecutionIntegrityError:
        return ExecutionIntegrityError(
            f"Job '{self.job_id}' execution history is inconsistent at "
            f"{self.event.event_type.value} event {self.event.event_id!r}: "
            f"{problem}. Explicit reconciliation is required.",
            self.job_id,
        )

    def get(self, key: str, kind, *, optional: bool = False):
        if key not in self.metadata:
            raise self.fail(f"metadata is missing {key!r}")

        value = self.metadata[key]

        if value is None and optional:
            return None

        if kind is int and (isinstance(value, bool) or not isinstance(value, int)):
            raise self.fail(f"metadata {key!r} must be an integer, got {value!r}")

        if kind is bool and not isinstance(value, bool):
            raise self.fail(f"metadata {key!r} must be a boolean, got {value!r}")

        if kind is str and (not isinstance(value, str) or not value):
            raise self.fail(f"metadata {key!r} must be a non-empty string, got {value!r}")

        return value

    def timestamp(self, key: str) -> str:
        value = self.get(key, str)

        try:
            moment = parse_observed_at(value, key)
        except EvidenceValidationError as exc:
            raise self.fail(str(exc)) from exc

        if canonical_observed_at(moment) != value:
            raise self.fail(f"metadata {key!r} {value!r} is not in canonical form")

        return value

    def evidence(self, key: str, phase: EvidencePhase) -> Tuple[RecordedEvidence, ...]:
        raw = self.get(key, list)

        if not isinstance(raw, list):
            raise self.fail(f"metadata {key!r} must be a list of evidence")

        try:
            items = tuple(RecordedEvidence.from_dict(entry) for entry in raw)
        except EvidenceValidationError as exc:
            raise self.fail(f"metadata {key!r}: {exc}") from exc

        wrong = [item.evidence_id for item in items if item.phase is not phase]

        if wrong:
            raise self.fail(f"evidence {wrong} in {key!r} is not phase {phase.value}")

        return items

    def deviation(self, key: str) -> bool:
        deviations = self.metadata.get("deviations")

        if not isinstance(deviations, dict) or key not in deviations:
            raise self.fail(f"metadata 'deviations' is missing {key!r}")

        if not isinstance(deviations[key], bool):
            raise self.fail(f"deviation {key!r} must be a boolean")

        return deviations[key]


def build_execution_records(
    job: Mapping[str, Any],
    events: Sequence[Any],
) -> List[ExecutionRecord]:
    """Every execution of this job, oldest first, derived from its history.

    events is the job's own history in commit order (StoredJobEvent from
    JobHistoryRepository.list_for_job, or bare JobEvent values). Only
    EXECUTION_* events are read. The job row is cross-checked: an
    in_progress job must have exactly one open execution, named by its
    block's execution_id; any other status must have none.

    Raises ExecutionIntegrityError for any inconsistency - never a partial
    or repaired result.
    """

    job_id = job["job_id"]
    by_id: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []

    for item in events:
        event = _event_of(item)

        if event.event_type not in EXECUTION_EVENT_TYPES:
            continue

        reader = _Reader(job_id, event)

        if event.job_id != job_id:
            raise reader.fail(f"event belongs to job {event.job_id!r}")

        execution_id = reader.get(EXECUTION_ID_KEY, str)

        if not is_execution_id(execution_id):
            raise reader.fail(f"execution_id {execution_id!r} is malformed")

        run_id = event.optimization_run_id

        if not run_id or reader.get("optimization_run_id", str) != run_id:
            raise reader.fail(
                "event and metadata optimization_run_id disagree or are missing"
            )

        if reader.get("proposal_id", str) != proposal_id_for(run_id, job_id):
            raise reader.fail(
                f"proposal_id {reader.metadata.get('proposal_id')!r} is not "
                f"{proposal_id_for(run_id, job_id)!r}"
            )

        if event.event_type is JobEventType.EXECUTION_STARTED:
            by_id[execution_id] = _started(reader, execution_id, run_id, by_id, order)
            order.append(execution_id)
            continue

        state = by_id.get(execution_id)

        if state is None:
            raise reader.fail(
                f"outcome for execution {execution_id!r}, which never started"
            )

        if state["status"] is not ExecutionStatus.STARTED:
            raise reader.fail(
                f"execution {execution_id!r} already has outcome "
                f"{state['status'].value}"
            )

        if run_id != state["optimization_run_id"]:
            raise reader.fail(
                f"outcome run {run_id!r} differs from the execution's run "
                f"{state['optimization_run_id']!r}"
            )

        _ended(reader, state)

    records = [
        _freeze(by_id[execution_id], job_id) for execution_id in order
    ]

    open_records = [record for record in records if record.is_open]
    row_execution_id = ((job.get("block_candidate") or {}).get("metadata") or {}).get(
        EXECUTION_ID_KEY
    )

    if job.get("status") == JobStatus.IN_PROGRESS.value:
        if len(open_records) != 1 or open_records[0].execution_id != row_execution_id:
            raise ExecutionIntegrityError(
                f"Job '{job_id}' is 'in_progress' with execution "
                f"{row_execution_id!r}, but its history has open executions "
                f"{[r.execution_id for r in open_records]}. Explicit "
                "reconciliation is required.",
                job_id,
            )
    elif open_records:
        raise ExecutionIntegrityError(
            f"Job '{job_id}' is '{job.get('status')}', but its history has "
            f"open executions {[r.execution_id for r in open_records]}. "
            "Explicit reconciliation is required.",
            job_id,
        )

    return records


def _started(reader: _Reader, execution_id, run_id, by_id, order) -> Dict[str, Any]:
    if execution_id in by_id:
        raise reader.fail(f"execution {execution_id!r} started more than once")

    for previous in order:
        status = by_id[previous]["status"]

        if status is ExecutionStatus.STARTED:
            raise reader.fail(f"execution {previous!r} is still open")

        if status is ExecutionStatus.COMPLETED:
            raise reader.fail(f"execution {previous!r} already completed the job")

        if by_id[previous]["optimization_run_id"] == run_id:
            raise reader.fail(
                f"optimization run {run_id!r} was already executed by {previous!r}"
            )

    attempt_number = reader.get("attempt_number", int)

    if attempt_number != len(order) + 1:
        raise reader.fail(
            f"attempt_number {attempt_number} is not {len(order) + 1}"
        )

    planned_start = reader.get("planned_start_minute", int)
    planned_end = reader.get("planned_end_minute", int)

    if planned_end <= planned_start:
        raise reader.fail(f"planned placement {(planned_start, planned_end)} is invalid")

    digest = reader.get("committed_block_digest", str)
    actual_start_at = reader.timestamp("actual_start_at")
    actual_start_minute = reader.get("actual_start_minute", int)
    before = reader.evidence("before_work_evidence", EvidencePhase.BEFORE_WORK)

    if not before:
        raise reader.fail("an execution start carries no before-work evidence")

    return {
        "execution_id": execution_id,
        "attempt_number": attempt_number,
        "optimization_run_id": run_id,
        "proposal_id": reader.metadata["proposal_id"],
        "track_id": reader.get("track_id", str),
        "section_id": reader.get("section_id", str, optional=True),
        "planned_start_minute": planned_start,
        "planned_end_minute": planned_end,
        "committed_block_digest": digest,
        "status": ExecutionStatus.STARTED,
        "started_by": reader.event.actor.to_dict(),
        "started_recorded_at": reader.event.occurred_at,
        "actual_start_at": actual_start_at,
        "actual_start_minute": actual_start_minute,
        "before_work_evidence": before,
        "started_before_planned_start": reader.deviation("started_before_planned_start"),
        "ended_by": None,
        "ended_recorded_at": None,
        "actual_end_at": None,
        "actual_end_minute": None,
        "after_work_evidence": (),
        "not_completed_reason": None,
        "failure_evidence": (),
        "ended_after_planned_end": None,
    }


def _ended(reader: _Reader, state: Dict[str, Any]) -> None:
    if reader.get("committed_block_digest", str) != state["committed_block_digest"]:
        raise reader.fail("committed_block_digest differs from the execution start")

    actual_end_at = reader.timestamp("actual_end_at")

    if datetime.fromisoformat(actual_end_at) < datetime.fromisoformat(
        state["actual_start_at"]
    ):
        raise reader.fail("actual_end_at precedes actual_start_at")

    state["actual_end_at"] = actual_end_at
    state["actual_end_minute"] = reader.get("actual_end_minute", int)
    state["ended_by"] = reader.event.actor.to_dict()
    state["ended_recorded_at"] = reader.event.occurred_at
    state["ended_after_planned_end"] = reader.deviation("ended_after_planned_end")

    if reader.event.event_type is JobEventType.EXECUTION_COMPLETED:
        after = reader.evidence("after_work_evidence", EvidencePhase.AFTER_WORK)

        if not after:
            raise reader.fail("a completion carries no after-work evidence")

        state["after_work_evidence"] = after
        state["status"] = ExecutionStatus.COMPLETED
        return

    reason = reader.event.reason

    if not isinstance(reason, str) or not reason.strip():
        raise reader.fail("a not-completed outcome carries no reason")

    state["not_completed_reason"] = reason
    state["failure_evidence"] = reader.evidence(
        "failure_evidence", EvidencePhase.NOT_COMPLETED
    )
    state["status"] = ExecutionStatus.NOT_COMPLETED


def _freeze(state: Mapping[str, Any], job_id: str) -> ExecutionRecord:
    return ExecutionRecord(
        execution_id=state["execution_id"],
        job_id=job_id,
        attempt_number=state["attempt_number"],
        optimization_run_id=state["optimization_run_id"],
        proposal_id=state["proposal_id"],
        track_id=state["track_id"],
        section_id=state["section_id"],
        planned_start_minute=state["planned_start_minute"],
        planned_end_minute=state["planned_end_minute"],
        committed_block_digest=state["committed_block_digest"],
        status=state["status"],
        started_by=state["started_by"],
        started_recorded_at=state["started_recorded_at"],
        actual_start_at=state["actual_start_at"],
        actual_start_minute=state["actual_start_minute"],
        before_work_evidence=state["before_work_evidence"],
        ended_by=state["ended_by"],
        ended_recorded_at=state["ended_recorded_at"],
        actual_end_at=state["actual_end_at"],
        actual_end_minute=state["actual_end_minute"],
        after_work_evidence=state["after_work_evidence"],
        not_completed_reason=state["not_completed_reason"],
        failure_evidence=state["failure_evidence"],
        deviations=ExecutionDeviations(
            started_before_planned_start=state["started_before_planned_start"],
            ended_after_planned_end=state["ended_after_planned_end"],
        ),
    )


__all__ = [
    "EXECUTION_ID_KEY",
    "EvidenceItem",
    "EvidenceKind",
    "EvidencePhase",
    "EvidenceValidationError",
    "ExecutionDeviations",
    "ExecutionIntegrityError",
    "ExecutionRecord",
    "ExecutionStatus",
    "MAX_EVIDENCE_ITEMS",
    "MAX_OBSERVATION_FUTURE_SKEW",
    "RecordedEvidence",
    "block_identity_digest",
    "build_execution_records",
    "canonical_observed_at",
    "committed_block_digest",
    "is_execution_id",
    "new_evidence_id",
    "new_execution_id",
    "observed_minute",
    "parse_observed_at",
    "proposal_id_for",
    "record_evidence",
    "require_not_in_future",
    "validate_evidence_set",
]
