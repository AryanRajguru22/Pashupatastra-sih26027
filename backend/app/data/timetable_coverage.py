"""Timetable coverage: which calendar dates a timetable input actually
attempted to describe (Slice 4 Step 1 - see
SLICE4_MULTIDAY_SCHEDULING_DESIGN.md Sec.7).

WHAT THIS MODULE IS
    A pure, isolated capability answering exactly one question: does a
    timetable input assert ANY data for a given calendar date? It does
    not derive possession, does not touch CanonicalTrainState, and does
    not change backend.app.data.timetable_adapter.
    derive_section_possession_windows in any way - that function's
    signature and behavior are unchanged by this module's existence.

WHAT "COVERED" MEANS - AND WHAT IT DOES NOT MEAN
    A calendar date is "covered" by a TimetableCoverage if at least one
    timetable record in the input asserted that service_date. This is
    PRESENCE, not COMPLETENESS: a covered date is not proof that every
    train running that day is represented, only that the caller's
    timetable input attempted to describe that date at all. This
    module deliberately has no notion of an expected train count, a
    completeness heuristic, or any other signal that would let it infer
    "this date's data is complete" - inventing one would fabricate
    authority the source data does not carry. See the design doc SS7.3
    for why partial-coverage detection is explicitly out of scope.

WIRED IN (Slice 4 Step 4)
    JobService._canonical_possession_inputs calls
    TimetableCoverage.from_timetable_records and uncovered_horizon_days
    on this corridor's timetable_records before any possession window
    is derived: an uncovered date raises TimetableCoverageGapError and
    refuses the whole request, so no possession window is derived and
    the solver never runs. This gate applies only to the CANONICAL
    timetable-backed path (a corridor with timetable_records) - see
    JobService.possession_inputs and _generated_possession_inputs's own
    docstring for why the generated-synthetic-slots path never calls
    this module at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Iterable, List, Union

from backend.app.data.horizon_anchor import (
    HorizonAnchorError,
    parse_horizon_start,
    parse_service_date,
)


class TimetableCoverageError(ValueError):
    """horizon_start / horizon_minutes given to a coverage computation is invalid.

    Distinct from HorizonAnchorError (a malformed horizon_start or
    service_date value, raised straight through from
    backend.app.data.horizon_anchor - the one authoritative parser for
    both): this covers errors specific to the coverage computation
    itself, such as a non-positive horizon_minutes.
    """


def _resolve_horizon_start(horizon_start: Union[str, datetime]) -> datetime:
    """Accept either the ISO-8601 string form or an already-parsed
    timezone-aware datetime.

    Mirrors backend.app.data.horizon_anchor._resolve_horizon_start
    exactly, duplicated rather than imported because that helper is
    private (leading underscore) to its own module - this module's only
    dependency on horizon_anchor is its PUBLIC surface
    (parse_horizon_start / parse_service_date / HorizonAnchorError), the
    same boundary horizon_anchor.py itself draws around its own
    duplicated naive-datetime rule.
    """

    if isinstance(horizon_start, datetime):
        if horizon_start.tzinfo is None:
            raise HorizonAnchorError(
                "horizon_start datetime must be timezone-aware; got a "
                "naive datetime."
            )
        return horizon_start

    return parse_horizon_start(horizon_start)


def covered_service_dates(
    records: Iterable[Dict[str, Any]],
) -> frozenset:
    """Distinct service_date values asserted across timetable records.

    A record with no service_date at all contributes nothing - it
    asserts no date, so it cannot be read as covering one. This is the
    fail-closed direction: a malformed/incomplete record can only ever
    SHRINK the covered set, never grow it. A record whose service_date
    IS present but malformed (not a valid ISO-8601 date) raises
    HorizonAnchorError via parse_service_date - the one authoritative
    date parser - rather than being silently skipped, because that
    record positively asserted a date and got it wrong, which is a data
    defect worth surfacing rather than absorbing.

    records stay OPAQUE dicts, exactly like
    backend.app.data.corridor_dataset.CorridorDataset.timetable_records -
    this function reads only the one field it needs (service_date) and
    nothing else about timetable record shape.
    """

    dates: set = set()

    for record in records:
        raw = record.get("service_date")

        if raw is None:
            continue

        dates.add(parse_service_date(raw))

    return frozenset(dates)


@dataclass(frozen=True)
class TimetableCoverage:
    """Which calendar dates a timetable input actually attempted to describe.

    See module docstring for what `covered_service_dates` does and does
    NOT assert about a date.
    """

    covered_service_dates: frozenset

    @classmethod
    def from_timetable_records(
        cls,
        records: Iterable[Dict[str, Any]],
    ) -> "TimetableCoverage":
        """Derive coverage mechanically from the records themselves.

        No coverage data is entered by hand anywhere: this is always
        computed from whatever service_date values the timetable input
        already carries - see covered_service_dates.
        """

        return cls(covered_service_dates=covered_service_dates(records))


def horizon_calendar_dates(
    horizon_start: Union[str, datetime],
    horizon_minutes: int,
) -> List[date]:
    """Every calendar date the half-open horizon interval touches.

    The horizon is [horizon_start, horizon_start + horizon_minutes),
    exactly the same half-open convention
    backend.app.optimizer.solver and
    backend.app.data.timetable_adapter.derive_section_possession_windows
    already use for the minute axis. Dates are read in horizon_start's
    OWN tzinfo - the same fixed-offset local railway clock every other
    horizon-anchored computation in this codebase uses; see
    backend.app.data.horizon_anchor's module docstring for why this is
    deliberately not a general timezone conversion.

    A horizon that ends exactly at a calendar midnight does not touch
    that midnight's date at all: horizon_minutes=1440 starting at
    midnight of day D covers only D, not D+1, matching
    OptimizationRequest's own existing [0, horizon_minutes) exclusivity
    at the minute level.
    """

    if horizon_minutes <= 0:
        raise TimetableCoverageError(
            f"horizon_minutes must be positive; got {horizon_minutes}."
        )

    parsed_start = _resolve_horizon_start(horizon_start)
    horizon_end = parsed_start + timedelta(minutes=horizon_minutes)

    start_date = parsed_start.date()
    end_date = horizon_end.date()

    # Exclusive upper bound: a horizon ending exactly at a calendar
    # midnight does not include any part of that date.
    if horizon_end.time() == time(0, 0):
        end_date -= timedelta(days=1)

    span_days = (end_date - start_date).days

    return [
        start_date + timedelta(days=offset)
        for offset in range(span_days + 1)
    ]


def uncovered_horizon_days(
    coverage: TimetableCoverage,
    horizon_start: Union[str, datetime],
    horizon_minutes: int,
) -> List[date]:
    """Calendar dates inside the horizon that `coverage` asserts no data for.

    Returns dates in horizon order (earliest first). An empty result
    means every calendar date the horizon touches has at least one
    timetable record asserting that date - NOT that the data for those
    dates is complete; see the module docstring.

    Dates `coverage` carries OUTSIDE the horizon are irrelevant here and
    never appear in the result - this function only ever reports on
    dates the horizon itself actually touches.
    """

    return [
        day
        for day in horizon_calendar_dates(horizon_start, horizon_minutes)
        if day not in coverage.covered_service_dates
    ]


__all__ = [
    "TimetableCoverage",
    "TimetableCoverageError",
    "covered_service_dates",
    "horizon_calendar_dates",
    "uncovered_horizon_days",
]
