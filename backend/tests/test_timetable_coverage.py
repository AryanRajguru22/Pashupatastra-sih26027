"""Slice 4 Step 1: the isolated timetable coverage gate.

Tests backend.app.data.timetable_coverage directly - a pure capability
that is NOT yet wired into JobService.possession_inputs,
JobOptimizationService, or any API route (see SLICE4_MULTIDAY_SCHEDULING_
DESIGN.md Sec.7.2). This suite is the only thing exercising it so far.

Covers, per the Slice 4 Step 1 brief:
  - one covered day + two-day horizon -> correct uncovered day
  - fully covered multi-day horizon -> no gaps
  - boundary at horizon start
  - boundary at the final represented date
  - horizon ending exactly at midnight
  - non-midnight horizon start
  - dates before/after the covered set
  - empty coverage
plus focused tests of covered_service_dates() itself, since
uncovered_horizon_days's correctness depends on it computing the right
set from raw records.
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.app.data.horizon_anchor import HorizonAnchorError
from backend.app.data.timetable_coverage import (
    TimetableCoverage,
    TimetableCoverageError,
    covered_service_dates,
    horizon_calendar_dates,
    uncovered_horizon_days,
)


HORIZON_MIDNIGHT = "2026-09-10T00:00:00+05:30"


def _coverage(*iso_dates: str) -> TimetableCoverage:
    return TimetableCoverage(
        covered_service_dates=frozenset(
            date.fromisoformat(d) for d in iso_dates
        )
    )


# ----------------------------------------------------------------------
# covered_service_dates() - derivation from raw records
# ----------------------------------------------------------------------


def test_covered_service_dates_collects_distinct_dates():
    records = [
        {"train_number": "1", "service_date": "2026-09-10"},
        {"train_number": "2", "service_date": "2026-09-11"},
        {"train_number": "3", "service_date": "2026-09-10"},  # duplicate
    ]

    assert covered_service_dates(records) == frozenset(
        {date(2026, 9, 10), date(2026, 9, 11)}
    )


def test_covered_service_dates_skips_records_with_no_service_date():
    """A record asserting no date contributes nothing - fail-closed
    direction: it can only shrink the covered set, never grow it."""

    records = [
        {"train_number": "1", "service_date": "2026-09-10"},
        {"train_number": "2"},  # no service_date at all
        {"train_number": "3", "service_date": None},
    ]

    assert covered_service_dates(records) == frozenset({date(2026, 9, 10)})


def test_covered_service_dates_empty_input_is_empty_coverage():
    assert covered_service_dates([]) == frozenset()


def test_covered_service_dates_rejects_malformed_date_rather_than_skipping():
    """A record that DOES assert a date, but gets it wrong, is a data
    defect worth surfacing - not silently absorbed like a missing field."""

    records = [{"train_number": "1", "service_date": "not-a-date"}]

    with pytest.raises(HorizonAnchorError):
        covered_service_dates(records)


def test_timetable_coverage_from_timetable_records_matches_direct_call():
    records = [{"train_number": "1", "service_date": "2026-09-10"}]

    coverage = TimetableCoverage.from_timetable_records(records)

    assert coverage.covered_service_dates == covered_service_dates(records)


# ----------------------------------------------------------------------
# horizon_calendar_dates() - which dates a horizon touches
# ----------------------------------------------------------------------


def test_horizon_calendar_dates_single_midnight_aligned_day():
    """horizon_minutes=1440 starting at midnight of D covers only D, not
    D+1 - the horizon's own [0, horizon_minutes) exclusivity mirrored at
    the calendar-date level."""

    assert horizon_calendar_dates(HORIZON_MIDNIGHT, 1440) == [
        date(2026, 9, 10)
    ]


def test_horizon_calendar_dates_two_midnight_aligned_days():
    assert horizon_calendar_dates(HORIZON_MIDNIGHT, 2880) == [
        date(2026, 9, 10),
        date(2026, 9, 11),
    ]


def test_horizon_calendar_dates_ending_exactly_at_midnight_excludes_that_date():
    """The exact boundary case: a horizon of exactly N*1440 minutes ends
    precisely at a calendar midnight, which must NOT be counted as
    touching that midnight's date."""

    # 3 days exactly: touches D, D+1, D+2 - never D+3.
    assert horizon_calendar_dates(HORIZON_MIDNIGHT, 4320) == [
        date(2026, 9, 10),
        date(2026, 9, 11),
        date(2026, 9, 12),
    ]


def test_horizon_calendar_dates_non_midnight_start_spans_two_dates():
    """A one-day horizon starting mid-day genuinely touches two calendar
    dates - the non-midnight horizon start case the canonical time model
    already supports (Sprint 3 Step 6 Case C)."""

    horizon_start = "2026-09-10T06:00:00+05:30"

    assert horizon_calendar_dates(horizon_start, 1440) == [
        date(2026, 9, 10),
        date(2026, 9, 11),
    ]


def test_horizon_calendar_dates_non_midnight_start_short_horizon_stays_one_date():
    horizon_start = "2026-09-10T06:00:00+05:30"

    # Only 1 hour, well before the next midnight.
    assert horizon_calendar_dates(horizon_start, 60) == [date(2026, 9, 10)]


def test_horizon_calendar_dates_accepts_already_parsed_datetime():
    from datetime import datetime, timezone, timedelta

    parsed = datetime(
        2026, 9, 10, 0, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))
    )

    assert horizon_calendar_dates(parsed, 1440) == [date(2026, 9, 10)]


def test_horizon_calendar_dates_rejects_non_positive_horizon_minutes():
    with pytest.raises(TimetableCoverageError):
        horizon_calendar_dates(HORIZON_MIDNIGHT, 0)

    with pytest.raises(TimetableCoverageError):
        horizon_calendar_dates(HORIZON_MIDNIGHT, -60)


def test_horizon_calendar_dates_rejects_naive_horizon_start():
    with pytest.raises(HorizonAnchorError):
        horizon_calendar_dates("2026-09-10T00:00:00", 1440)


# ----------------------------------------------------------------------
# uncovered_horizon_days() - the actual coverage gate
# ----------------------------------------------------------------------


def test_one_covered_day_two_day_horizon_reports_the_second_day():
    coverage = _coverage("2026-09-10")

    assert uncovered_horizon_days(coverage, HORIZON_MIDNIGHT, 2880) == [
        date(2026, 9, 11)
    ]


def test_fully_covered_multi_day_horizon_has_no_gaps():
    coverage = _coverage("2026-09-10", "2026-09-11", "2026-09-12")

    assert (
        uncovered_horizon_days(coverage, HORIZON_MIDNIGHT, 3 * 1440) == []
    )


def test_boundary_at_horizon_start_date_is_recognised_as_covered():
    """The horizon's own first date, exactly, must count as covered when
    the coverage set names it - not off-by-one excluded."""

    coverage = _coverage("2026-09-10")

    assert uncovered_horizon_days(coverage, HORIZON_MIDNIGHT, 1440) == []


def test_boundary_at_the_final_represented_date():
    """The LAST date the horizon touches, exactly, must count as covered
    when named - and a horizon ending exactly at that date's midnight
    must not spuriously report the day AFTER it as uncovered."""

    # 3-day horizon: D, D+1, D+2. Coverage names exactly those three.
    coverage = _coverage("2026-09-10", "2026-09-11", "2026-09-12")

    assert uncovered_horizon_days(coverage, HORIZON_MIDNIGHT, 4320) == []

    # Coverage missing only the LAST date must report exactly that one.
    partial = _coverage("2026-09-10", "2026-09-11")

    assert uncovered_horizon_days(partial, HORIZON_MIDNIGHT, 4320) == [
        date(2026, 9, 12)
    ]


def test_horizon_ending_exactly_at_midnight_does_not_demand_the_next_date():
    """A horizon of exactly 2 days (2880 minutes) starting at midnight
    ends exactly at midnight two days later; that boundary date itself
    is never demanded as covered, since the horizon never touches it."""

    coverage = _coverage("2026-09-10", "2026-09-11")

    # If the exclusive-boundary logic were wrong, this would spuriously
    # report 2026-09-12 as uncovered.
    assert uncovered_horizon_days(coverage, HORIZON_MIDNIGHT, 2880) == []


def test_non_midnight_horizon_start_reports_both_touched_dates_when_uncovered():
    horizon_start = "2026-09-10T06:00:00+05:30"

    assert uncovered_horizon_days(
        TimetableCoverage(covered_service_dates=frozenset()),
        horizon_start,
        1440,
    ) == [date(2026, 9, 10), date(2026, 9, 11)]


def test_non_midnight_horizon_start_with_partial_coverage():
    horizon_start = "2026-09-10T06:00:00+05:30"
    coverage = _coverage("2026-09-10")  # only the first touched date

    assert uncovered_horizon_days(coverage, horizon_start, 1440) == [
        date(2026, 9, 11)
    ]


def test_coverage_dates_outside_the_horizon_are_irrelevant():
    """Dates the coverage set names that the horizon never touches must
    not affect the result at all - only horizon-touched dates matter."""

    coverage = _coverage("2020-01-01", "2026-09-10", "2099-12-31")

    assert uncovered_horizon_days(coverage, HORIZON_MIDNIGHT, 1440) == []


def test_dates_entirely_before_and_after_the_covered_set():
    """A horizon that does not overlap the covered set at all reports
    every one of its own dates as uncovered."""

    coverage = _coverage("2026-09-10")

    before_horizon = "2026-09-01T00:00:00+05:30"
    assert uncovered_horizon_days(coverage, before_horizon, 1440) == [
        date(2026, 9, 1)
    ]

    after_horizon = "2026-09-20T00:00:00+05:30"
    assert uncovered_horizon_days(coverage, after_horizon, 2880) == [
        date(2026, 9, 20),
        date(2026, 9, 21),
    ]


def test_empty_coverage_reports_every_horizon_date_as_uncovered():
    empty = TimetableCoverage(covered_service_dates=frozenset())

    assert uncovered_horizon_days(empty, HORIZON_MIDNIGHT, 4320) == [
        date(2026, 9, 10),
        date(2026, 9, 11),
        date(2026, 9, 12),
    ]


def test_uncovered_days_are_returned_in_horizon_order():
    coverage = _coverage("2026-09-11")  # only the middle day covered

    assert uncovered_horizon_days(coverage, HORIZON_MIDNIGHT, 3 * 1440) == [
        date(2026, 9, 10),
        date(2026, 9, 12),
    ]
