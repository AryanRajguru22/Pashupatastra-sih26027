"""Advisory duplicate detection for newly reported jobs (Slice 8).

WHAT A DUPLICATE IS HERE
    Two workers, independently, reporting what may be the same
    maintenance issue. That is NOT the same problem as a retried request
    (one worker, one report, sent twice) - that is idempotency, handled
    by the idempotency key, and it creates NO second job. A possible
    duplicate creates a second job, always.

NON-NEGOTIABLE
    Detection NEVER deletes, merges, rejects, replaces, holds or edits a
    worker's report. It produces an advisory record that is stored,
    immutably, in the new job's block metadata and in its JOB_CREATED
    event. A human (the existing reject-with-reason action) decides.
    A worker who walked to a fault and filed a report must never see it
    vanish because an algorithm found it familiar.

THE RULE (deterministic-proximity-1.0) - every signal must hold
    An existing, non-terminal job is a candidate iff ALL of:
      SAME_TRACK      same track_id
      SAME_SECTION    same resolved section_id
      SAME_ASSET      same asset (id), with the candidate's own asset
                      reference verified against the active asset set
      SAME_WORK_TYPE  same job_type
      SPAN_NEAR       gap between the two chainage spans <= max_gap_m
                      (0 when they overlap; inclusive)
      TIME_NEAR       created within `window` of each other (inclusive)
    It is deliberately conservative: it would rather miss a duplicate
    (an authority reviews one extra job later) than flag two unrelated
    jobs and train people to ignore the flag.

THRESHOLDS
    max_gap_m = 500 m. A worker describing the same fault twice will
    typically differ by tens to a few hundred metres of chainage; 500 m
    is generous for that and small against the 7-67 km sections.
    window = 72 h, the gate's figure for how long an open report is still
    plausibly "the same issue" before it has been triaged.
    Both are DuplicatePolicy fields (JobService(duplicate_policy=...)),
    recorded in every assessment, not literals in business logic.

WHAT IT DOES NOT DO
    No description-text similarity, no GPS proximity, no ML: none of
    them can be validated against data that exists. rule_version is the
    seam a later detector plugs into. A missed detection under
    concurrent submissions is accepted and documented - intake takes no
    global lock, so two simultaneous overlapping reports may not see each
    other; detection is advisory and can be re-run over history later.

Pure and deterministic: candidates are ordered by (created_at, job_id).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable, Mapping, Optional

from backend.app.jobs.lifecycle import TERMINAL_STATUSES


DUPLICATE_RULE_VERSION = "deterministic-proximity-1.0"

DEFAULT_DUPLICATE_MAX_GAP_M = 500.0
DEFAULT_DUPLICATE_WINDOW = timedelta(hours=72)

SIGNAL_SAME_TRACK = "SAME_TRACK"
SIGNAL_SAME_SECTION = "SAME_SECTION"
SIGNAL_SAME_ASSET = "SAME_ASSET"
SIGNAL_SAME_WORK_TYPE = "SAME_WORK_TYPE"
SIGNAL_SPAN_NEAR = "SPAN_NEAR"
SIGNAL_TIME_NEAR = "TIME_NEAR"

_ALL_SIGNALS = (
    SIGNAL_SAME_TRACK,
    SIGNAL_SAME_SECTION,
    SIGNAL_SAME_ASSET,
    SIGNAL_SAME_WORK_TYPE,
    SIGNAL_SPAN_NEAR,
    SIGNAL_TIME_NEAR,
)

_GRID = 6


@dataclass(frozen=True)
class DuplicatePolicy:
    max_gap_m: float = DEFAULT_DUPLICATE_MAX_GAP_M
    window: timedelta = DEFAULT_DUPLICATE_WINDOW

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_gap_m, bool)
            or not isinstance(self.max_gap_m, (int, float))
            or not math.isfinite(self.max_gap_m)
            or self.max_gap_m < 0
        ):
            raise ValueError(
                "DuplicatePolicy.max_gap_m must be a finite number >= 0; "
                f"got {self.max_gap_m!r}."
            )

        if not isinstance(self.window, timedelta) or self.window < timedelta(0):
            raise ValueError(
                "DuplicatePolicy.window must be a non-negative timedelta; "
                f"got {self.window!r}."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_gap_m": float(self.max_gap_m),
            "window_hours": self.window.total_seconds() / 3600.0,
        }


@dataclass(frozen=True)
class DuplicateProbe:
    """The facts about the NEW job that detection compares against."""

    track_id: str
    section_id: str
    asset_id: str
    work_type: str
    distance_start_m: float
    distance_end_m: float
    created_at: datetime


@dataclass(frozen=True)
class DuplicateCandidate:
    job_id: str
    signals: tuple
    span_gap_m: float
    minutes_apart: float
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "signals": list(self.signals),
            "span_gap_m": self.span_gap_m,
            "minutes_apart": self.minutes_apart,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class DuplicateAssessment:
    candidates: tuple
    # (job_id, reason) for rows detection could not evaluate - reported,
    # never silently treated as a match or as a non-match.
    skipped: tuple
    policy: DuplicatePolicy

    @property
    def possible_duplicate(self) -> bool:
        return bool(self.candidates)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "rule_version": DUPLICATE_RULE_VERSION,
            "possible_duplicate": self.possible_duplicate,
            "candidate_jobs": [c.job_id for c in self.candidates],
            "candidates": [c.to_dict() for c in self.candidates],
            "skipped": [
                {"job_id": job_id, "reason": reason}
                for job_id, reason in self.skipped
            ],
            "required_signals": list(_ALL_SIGNALS),
            "thresholds": self.policy.to_dict(),
        }


def parse_created_at(value: Any) -> Optional[datetime]:
    """An aware datetime, or None when the stored value is unusable."""

    if not isinstance(value, str):
        return None

    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None

    return moment if moment.tzinfo is not None else None


def span_gap_m(
    start_a: float,
    end_a: float,
    start_b: float,
    end_b: float,
) -> float:
    """Metres between two spans; 0 when they overlap or touch."""

    return round(max(0.0, max(start_a, start_b) - min(end_a, end_b)), _GRID)


def find_duplicate_candidates(
    probe: DuplicateProbe,
    existing_jobs: Iterable[Mapping[str, Any]],
    policy: DuplicatePolicy,
    *,
    verify_asset: Callable[[Mapping[str, Any]], Optional[str]],
) -> DuplicateAssessment:
    """Assess `probe` against `existing_jobs` (repository job dicts).

    verify_asset(row) returns None when the row's persisted asset
    reference is trustworthy, else a short reason; an unverifiable row
    cannot supply the SAME_ASSET signal, so it is skipped and reported
    rather than trusted.
    """

    candidates: list[tuple[datetime, str, DuplicateCandidate]] = []
    skipped: list[tuple[str, str]] = []

    for row in existing_jobs:
        job_id = row["job_id"]

        if row.get("status") in TERMINAL_STATUSES:
            continue

        # Cheap, certain exclusions first, so a stale asset reference on
        # an unrelated job is never even reported.
        if row["track_id"] != probe.track_id:
            continue

        if row["work_type"] != probe.work_type:
            continue

        block = row.get("block_candidate") or {}

        if block.get("section_id") != probe.section_id:
            continue

        row_created = parse_created_at(row.get("created_at"))

        if row_created is None:
            skipped.append((job_id, "created_at is missing or not a timezone-aware ISO timestamp"))
            continue

        apart = abs(probe.created_at - row_created)

        if apart > policy.window:
            continue

        gap = span_gap_m(
            probe.distance_start_m,
            probe.distance_end_m,
            float(row["distance_start"]),
            float(row["distance_end"]),
        )

        if gap > policy.max_gap_m:
            continue

        if block.get("asset_id") != probe.asset_id:
            continue

        problem = verify_asset(row)

        if problem is not None:
            skipped.append((job_id, problem))
            continue

        candidates.append(
            (
                row_created,
                job_id,
                DuplicateCandidate(
                    job_id=job_id,
                    signals=_ALL_SIGNALS,
                    span_gap_m=gap,
                    minutes_apart=round(apart.total_seconds() / 60.0, 3),
                    created_at=row["created_at"],
                ),
            )
        )

    candidates.sort(key=lambda item: (item[0], item[1]))
    skipped.sort()

    return DuplicateAssessment(
        candidates=tuple(item[2] for item in candidates),
        skipped=tuple(skipped),
        policy=policy,
    )


__all__ = [
    "DEFAULT_DUPLICATE_MAX_GAP_M",
    "DEFAULT_DUPLICATE_WINDOW",
    "DUPLICATE_RULE_VERSION",
    "DuplicateAssessment",
    "DuplicateCandidate",
    "DuplicatePolicy",
    "DuplicateProbe",
    "find_duplicate_candidates",
    "parse_created_at",
    "span_gap_m",
]
