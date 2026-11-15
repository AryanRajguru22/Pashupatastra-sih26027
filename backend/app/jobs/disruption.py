"""Persisted DISRUPT -> RECOVER workflow for the real jobs pipeline.

WHAT THIS IS
    A disruption is a DECLARED, SIMULATED event: "track UP-1 between
    NZM and FDB cannot be used from 01:00 to 03:00". It is recorded
    against the jobs database, it changes what every later optimization
    may do, and it tells the authority exactly which approved work it
    invalidates. Pashupatastra never presents a disruption as an
    observed railway event: every record carries
    basis = "SIMULATED_DECLARED" and the declaring actor.

HOW IT AFFECTS PLANNING (one rule, applied in one place)
    Each active disruption SUBTRACTS its time span from the possession
    windows of the (track, section) it names. This is applied AFTER the
    timetable adapter has derived the windows (derivation still lives
    only in backend.app.data.timetable_adapter) and BEFORE the solver
    runs. Subtraction can only remove possession, so it can never make
    the model less safe than the undisrupted one. If subtraction would
    remove EVERY window the run is refused (fail closed): an empty
    window list would otherwise mean "possession is not modelled" and
    switch the solver's possession rule off.

COMMITTED BLOCKS ARE NEVER SILENTLY MOVED OR KEPT
    An approved (notified / in_progress) block that overlaps an active
    disruption cannot be re-planned by the optimizer: it is pinned, and
    its possession window no longer exists, so the model would be
    infeasible and take every unrelated proposal down with it. Instead
    the optimization is REFUSED before the solver runs
    (RecoveryBlockedError), naming each such job and the authority
    action that resolves it (release a notified block; report an
    in-progress block not-completed). An uncommitted proposal that
    overlaps a disruption cannot be approved (DisruptedPlacementError)
    and is re-planned by the next run.

STORAGE
    disruption_events is append-only (SQL triggers, the same guard as
    job_events). A disruption's state is derived from its events:
        REGISTERED, [CLEARED], RECOVERY_PLANNED* / RECOVERY_REFUSED*
    Nothing is updated or deleted.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from math import ceil, floor
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from contracts import PossessionWindow

from backend.app.data.horizon_anchor import parse_horizon_start
from backend.app.persistence.append_only import install_append_only_guards

DISRUPTION_EVENTS_TABLE = "disruption_events"

BASIS_SIMULATED_DECLARED = "SIMULATED_DECLARED"

# Job statuses (string values of backend.app.jobs.models.JobStatus). Kept
# as literals so this module does not import the lifecycle machinery.
_STATUS_SCHEDULED = "scheduled"
_STATUS_NOTIFIED = "notified"
_STATUS_IN_PROGRESS = "in_progress"

IMPACT_PROPOSAL_REPLAN = "PROPOSAL_REPLAN"
IMPACT_RELEASE_REQUIRED = "RELEASE_REQUIRED"
IMPACT_NOT_COMPLETED_REQUIRED = "NOT_COMPLETED_REPORT_REQUIRED"

_REQUIRED_ACTION = {
    IMPACT_PROPOSAL_REPLAN: (
        "None needed from the authority: the next optimization re-plans "
        "this uncommitted proposal. It cannot be approved until then."
    ),
    IMPACT_RELEASE_REQUIRED: (
        "The authority must release this approved block "
        "(POST /v1/jobs/{job_id}/proposal/release) before the plan can be "
        "recovered."
    ),
    IMPACT_NOT_COMPLETED_REQUIRED: (
        "Field work has started on a disrupted span. The worker must "
        "report it not completed "
        "(POST /v1/jobs/{job_id}/execution/not-completed) before the plan "
        "can be recovered."
    ),
}


class DisruptionKind(str, Enum):
    # The named track (optionally one section of it) cannot be used.
    TRACK_UNAVAILABLE = "TRACK_UNAVAILABLE"
    # A granted possession on one section is cut short or withdrawn.
    POSSESSION_CURTAILMENT = "POSSESSION_CURTAILMENT"


class DisruptionEventType(str, Enum):
    REGISTERED = "REGISTERED"
    CLEARED = "CLEARED"
    RECOVERY_PLANNED = "RECOVERY_PLANNED"
    RECOVERY_REFUSED = "RECOVERY_REFUSED"


class DisruptionNotFoundError(KeyError):
    pass


class DisruptionStateError(RuntimeError):
    """The disruption is not in a state that permits this operation."""


class RecoveryBlockedError(RuntimeError):
    """Approved work overlaps an active disruption; the solve is refused.

    `blocking` lists one entry per job: job_id, status, track, section,
    placement, the disruption ids that hit it and the required action.
    """

    def __init__(self, blocking: Sequence[Mapping[str, Any]]):
        self.blocking = [dict(entry) for entry in blocking]
        ids = ", ".join(entry["job_id"] for entry in self.blocking)
        super().__init__(
            "Recovery refused before any solve: approved work overlaps an "
            f"active disruption ({ids}). Each of these must be released or "
            "reported not-completed by the responsible role first; the "
            "optimizer will not move or keep a committed block on its own."
        )


class DisruptedPlacementError(RuntimeError):
    """An uncommitted proposal lies inside an active disruption."""


class AllPossessionRemovedError(RuntimeError):
    """Active disruptions leave no possession window at all."""


# ----------------------------------------------------------------------
# Records
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class DisruptionRecord:
    """The REGISTERED payload plus derived state."""

    disruption_id: str
    kind: DisruptionKind
    corridor_id: str
    track_id: str
    section_id: Optional[str]
    starts_at: str
    ends_at: str
    reason: str
    declared_by: str
    declared_role: str
    registered_at: str
    basis: str = BASIS_SIMULATED_DECLARED
    status: str = "ACTIVE"  # ACTIVE | CLEARED
    cleared_at: Optional[str] = None
    cleared_by: Optional[str] = None
    clear_reason: Optional[str] = None
    recovery_runs: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "disruption_id": self.disruption_id,
            "kind": self.kind.value,
            "corridor_id": self.corridor_id,
            "track_id": self.track_id,
            "section_id": self.section_id,
            "starts_at": self.starts_at,
            "ends_at": self.ends_at,
            "reason": self.reason,
            "declared_by": self.declared_by,
            "declared_role": self.declared_role,
            "registered_at": self.registered_at,
            "basis": self.basis,
            "status": self.status,
            "cleared_at": self.cleared_at,
            "cleared_by": self.cleared_by,
            "clear_reason": self.clear_reason,
            "recovery_runs": list(self.recovery_runs),
        }


def _parse_aware(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{label} must be an ISO-8601 datetime with an explicit UTC "
            f"offset, got {value!r}"
        ) from exc
    if parsed.tzinfo is None:
        raise ValueError(
            f"{label} must carry an explicit UTC offset (e.g. +05:30), "
            f"got naive {value!r}"
        )
    return parsed


def span_minutes(
    starts_at: str, ends_at: str, horizon_start: str
) -> tuple[int, int]:
    """The span as integer minutes from the horizon anchor.

    The start is rounded DOWN and the end UP, so the converted span always
    covers the declared one: rounding may only widen a disruption.
    """

    anchor = parse_horizon_start(horizon_start)
    start = (_parse_aware(starts_at, "starts_at") - anchor).total_seconds() / 60
    end = (_parse_aware(ends_at, "ends_at") - anchor).total_seconds() / 60
    return int(floor(start)), int(ceil(end))


def validate_declaration(
    *,
    kind: DisruptionKind,
    track_id: str,
    section_id: Optional[str],
    starts_at: str,
    ends_at: str,
    reason: str,
    known_tracks: Iterable[str],
    known_sections: Iterable[str],
    horizon_start: str,
    horizon_minutes: int,
) -> tuple[int, int]:
    """Validate a declaration; return its span in horizon minutes."""

    if track_id not in set(known_tracks):
        raise ValueError(
            f"Unknown track '{track_id}' for this corridor "
            f"(known: {sorted(set(known_tracks))})."
        )
    if section_id is not None and section_id not in set(known_sections):
        raise ValueError(
            f"Unknown section '{section_id}' for this corridor."
        )
    if kind is DisruptionKind.POSSESSION_CURTAILMENT and section_id is None:
        raise ValueError(
            "A possession curtailment names one section (section_id is "
            "required); use TRACK_UNAVAILABLE for a whole track."
        )
    if not reason or not reason.strip():
        raise ValueError("A disruption requires a reason.")

    start_m, end_m = span_minutes(starts_at, ends_at, horizon_start)
    if end_m <= start_m:
        raise ValueError("ends_at must be after starts_at.")
    if end_m <= 0 or start_m >= horizon_minutes:
        raise ValueError(
            "The disruption lies entirely outside the planning horizon "
            "and would change nothing."
        )
    return start_m, end_m


# ----------------------------------------------------------------------
# Pure application logic
# ----------------------------------------------------------------------


def _names(record: DisruptionRecord, track_id: str, section_id: Optional[str]) -> bool:
    """Whether the record names this (track, section) resource."""

    if record.track_id != track_id:
        return False
    if record.section_id is None:
        return True
    # A resource that names no section is matched by track alone in the
    # solver (see optimizer._possession_window_covers_block: a block with
    # no section is covered by ANY window on its track). It therefore
    # cannot be proven clear of a section-specific disruption, so it is
    # treated as hit. That only removes possession - the safe direction.
    if section_id is None:
        return True
    return record.section_id == section_id


def subtract_disruptions(
    windows: Sequence[PossessionWindow],
    active: Sequence[DisruptionRecord],
    horizon_start: str,
) -> list[PossessionWindow]:
    """Remove every active disruption's span from the windows it names.

    A window that is untouched is returned unchanged (same object). A
    window that is cut is replaced by its remaining fragments, each with
    a derived id `<window_id>~d<n>`. Fragments of zero length are
    dropped; nothing else is invented, so the result is always a subset
    of the input possession.
    """

    if not active:
        return list(windows)

    spans = [
        (record, *span_minutes(record.starts_at, record.ends_at, horizon_start))
        for record in active
    ]

    result: list[PossessionWindow] = []

    for window in windows:
        fragments = [(int(window.start_minute), int(window.end_minute))]
        cut = False

        for record, d_start, d_end in spans:
            if not _names(record, window.track_id, window.section_id):
                continue

            next_fragments: list[tuple[int, int]] = []
            for f_start, f_end in fragments:
                if d_end <= f_start or d_start >= f_end:
                    next_fragments.append((f_start, f_end))
                    continue
                cut = True
                if f_start < d_start:
                    next_fragments.append((f_start, d_start))
                if d_end < f_end:
                    next_fragments.append((d_end, f_end))
            fragments = next_fragments

        if not cut:
            result.append(window)
            continue

        for index, (f_start, f_end) in enumerate(fragments):
            if f_end > f_start:
                result.append(
                    replace(
                        window,
                        window_id=f"{window.window_id}~d{index}",
                        start_minute=f_start,
                        end_minute=f_end,
                    )
                )

    return result


def _job_section(job: Mapping[str, Any]) -> Optional[str]:
    candidate = job.get("block_candidate") or {}
    section = candidate.get("section_id")
    return section if isinstance(section, str) else None


def affected_jobs(
    jobs: Iterable[Mapping[str, Any]],
    active: Sequence[DisruptionRecord],
    horizon_start: str,
) -> list[dict[str, Any]]:
    """Active jobs whose placement overlaps an active disruption.

    Only jobs holding a placement (scheduled, notified, in_progress) can
    be affected. One entry per job, naming every disruption that hits it.
    """

    if not active:
        return []

    spans = [
        (record, *span_minutes(record.starts_at, record.ends_at, horizon_start))
        for record in active
    ]

    out: list[dict[str, Any]] = []

    for job in jobs:
        status = job.get("status")
        p_start = job.get("schedule_start_minute")
        p_end = job.get("schedule_end_minute")

        if status not in (_STATUS_SCHEDULED, _STATUS_NOTIFIED, _STATUS_IN_PROGRESS):
            continue
        if p_start is None or p_end is None:
            continue

        track = job["track_id"]
        section = _job_section(job)

        hits = [
            record.disruption_id
            for record, d_start, d_end in spans
            if _names(record, track, section)
            and d_start < p_end
            and d_end > p_start
        ]
        if not hits:
            continue

        impact = {
            _STATUS_SCHEDULED: IMPACT_PROPOSAL_REPLAN,
            _STATUS_NOTIFIED: IMPACT_RELEASE_REQUIRED,
            _STATUS_IN_PROGRESS: IMPACT_NOT_COMPLETED_REQUIRED,
        }[status]

        out.append(
            {
                "job_id": job["job_id"],
                "status": status,
                "track_id": track,
                "section_id": section,
                "start_minute": int(p_start),
                "end_minute": int(p_end),
                "disruption_ids": hits,
                "impact": impact,
                "required_action": _REQUIRED_ACTION[impact].replace(
                    "{job_id}", job["job_id"]
                ),
            }
        )

    return out


def committed_blockers(
    impacts: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return [
        dict(entry)
        for entry in impacts
        if entry["impact"] != IMPACT_PROPOSAL_REPLAN
    ]


def compare_plans(
    before: Mapping[str, Mapping[str, Any]],
    after: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Before/after placement comparison, keyed by job id.

    Each side maps job_id -> {status, start_minute, end_minute} for jobs
    that HOLD a placement on that side (absent = no placement).
    """

    unchanged: list[str] = []
    moved: list[dict[str, Any]] = []
    newly_unscheduled: list[dict[str, Any]] = []
    newly_scheduled: list[dict[str, Any]] = []

    for job_id in sorted(set(before) | set(after)):
        b = before.get(job_id)
        a = after.get(job_id)
        if b and a:
            if (b["start_minute"], b["end_minute"]) == (
                a["start_minute"],
                a["end_minute"],
            ):
                unchanged.append(job_id)
            else:
                moved.append({"job_id": job_id, "before": dict(b), "after": dict(a)})
        elif b and not a:
            newly_unscheduled.append({"job_id": job_id, "before": dict(b)})
        elif a and not b:
            newly_scheduled.append({"job_id": job_id, "after": dict(a)})

    return {
        "unchanged": unchanged,
        "moved": moved,
        "newly_unscheduled": newly_unscheduled,
        "newly_scheduled": newly_scheduled,
        "counts": {
            "unchanged": len(unchanged),
            "moved": len(moved),
            "newly_unscheduled": len(newly_unscheduled),
            "newly_scheduled": len(newly_scheduled),
        },
    }


# ----------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------


def _event_id() -> str:
    return f"DEVT-{uuid.uuid4().hex.upper()}"


def new_disruption_id() -> str:
    return f"DIS-{uuid.uuid4().hex[:12].upper()}"


class DisruptionRepository:
    """Append-only event store for disruptions (same database file)."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {DISRUPTION_EVENTS_TABLE} (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    disruption_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    actor_role TEXT NOT NULL,
                    actor_assurance TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS {DISRUPTION_EVENTS_TABLE}_by_id "
                f"ON {DISRUPTION_EVENTS_TABLE} (disruption_id, sequence)"
            )
            install_append_only_guards(conn, DISRUPTION_EVENTS_TABLE)

    def append(
        self,
        disruption_id: str,
        event_type: DisruptionEventType,
        *,
        actor: Any,
        at: str,
        payload: Mapping[str, Any],
    ) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                f"""
                INSERT INTO {DISRUPTION_EVENTS_TABLE}
                (event_id, disruption_id, event_type, occurred_at,
                 actor_id, actor_role, actor_assurance, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _event_id(),
                    disruption_id,
                    event_type.value,
                    at,
                    actor.actor_id,
                    actor.role.value,
                    actor.assurance.value,
                    json.dumps(dict(payload), sort_keys=True),
                ),
            )

    def events(self, disruption_id: Optional[str] = None) -> list[dict[str, Any]]:
        query = f"SELECT * FROM {DISRUPTION_EVENTS_TABLE}"
        params: tuple = ()
        if disruption_id is not None:
            query += " WHERE disruption_id = ?"
            params = (disruption_id,)
        query += " ORDER BY sequence"
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "sequence": row["sequence"],
                "event_id": row["event_id"],
                "disruption_id": row["disruption_id"],
                "event_type": row["event_type"],
                "occurred_at": row["occurred_at"],
                "actor_id": row["actor_id"],
                "actor_role": row["actor_role"],
                "actor_assurance": row["actor_assurance"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def _fold(self, events: list[dict[str, Any]]) -> dict[str, DisruptionRecord]:
        records: dict[str, DisruptionRecord] = {}
        for event in events:
            did = event["disruption_id"]
            kind = event["event_type"]
            payload = event["payload"]
            if kind == DisruptionEventType.REGISTERED.value:
                records[did] = DisruptionRecord(
                    disruption_id=did,
                    kind=DisruptionKind(payload["kind"]),
                    corridor_id=payload["corridor_id"],
                    track_id=payload["track_id"],
                    section_id=payload.get("section_id"),
                    starts_at=payload["starts_at"],
                    ends_at=payload["ends_at"],
                    reason=payload["reason"],
                    declared_by=event["actor_id"],
                    declared_role=event["actor_role"],
                    registered_at=event["occurred_at"],
                    basis=payload.get("basis", BASIS_SIMULATED_DECLARED),
                )
            elif did in records and kind == DisruptionEventType.CLEARED.value:
                records[did] = replace(
                    records[did],
                    status="CLEARED",
                    cleared_at=event["occurred_at"],
                    cleared_by=event["actor_id"],
                    clear_reason=payload.get("reason"),
                )
            elif did in records and kind == DisruptionEventType.RECOVERY_PLANNED.value:
                records[did] = replace(
                    records[did],
                    recovery_runs=records[did].recovery_runs
                    + (payload["optimization_run_id"],),
                )
        return records

    def all(self) -> list[DisruptionRecord]:
        return list(self._fold(self.events()).values())

    def active(self) -> list[DisruptionRecord]:
        return [r for r in self.all() if r.status == "ACTIVE"]

    def get(self, disruption_id: str) -> DisruptionRecord:
        record = self._fold(self.events(disruption_id)).get(disruption_id)
        if record is None:
            raise DisruptionNotFoundError(
                f"Unknown disruption '{disruption_id}'."
            )
        return record


__all__ = [
    "AllPossessionRemovedError",
    "BASIS_SIMULATED_DECLARED",
    "DisruptedPlacementError",
    "DisruptionEventType",
    "DisruptionKind",
    "DisruptionNotFoundError",
    "DisruptionRecord",
    "DisruptionRepository",
    "DisruptionStateError",
    "IMPACT_NOT_COMPLETED_REQUIRED",
    "IMPACT_PROPOSAL_REPLAN",
    "IMPACT_RELEASE_REQUIRED",
    "RecoveryBlockedError",
    "affected_jobs",
    "committed_blockers",
    "compare_plans",
    "new_disruption_id",
    "span_minutes",
    "subtract_disruptions",
    "validate_declaration",
]
