"""Sprint 3 Step 8: the authoritative horizon-anchoring conversion.

Tests backend.app.data.horizon_anchor.horizon_relative_minutes directly
against the six acceptance cases from the Step 8 brief, plus the
required rejection behaviors. This is the low-level contract; end-to-end
integration through the canonical timetable adapter is covered in
test_horizon_anchor_integration.py.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from backend.app.data.horizon_anchor import (
    HorizonAnchorError,
    horizon_relative_minutes,
    parse_horizon_start,
    parse_service_date,
)


# ----------------------------------------------------------------------
# A-F: the six acceptance cases from the Step 8 brief, verbatim.
# ----------------------------------------------------------------------


def test_case_a_same_day():
    assert (
        horizon_relative_minutes(
            "2026-09-10", 510, 0, "2026-09-10T00:00:00+05:30"
        )
        == 510
    )


def test_case_b_overnight_both_events():
    horizon_start = "2026-09-10T00:00:00+05:30"

    # 23:30, day_offset 0 -> 1410
    assert (
        horizon_relative_minutes("2026-09-10", 1410, 0, horizon_start)
        == 1410
    )

    # 04:30 the NEXT day, day_offset 1, same service_date -> 1710
    assert (
        horizon_relative_minutes("2026-09-10", 270, 1, horizon_start)
        == 1710
    )


def test_case_c_horizon_does_not_begin_at_midnight():
    assert (
        horizon_relative_minutes(
            "2026-09-10", 510, 0, "2026-09-10T06:00:00+05:30"
        )
        == 150
    )


def test_case_d_event_before_horizon():
    assert (
        horizon_relative_minutes(
            "2026-09-10", 270, 0, "2026-09-10T06:00:00+05:30"
        )
        == -90
    )


def test_case_e_horizon_over_24h():
    assert (
        horizon_relative_minutes(
            "2026-09-11", 480, 0, "2026-09-10T06:00:00+05:30"
        )
        == 1560
    )


def test_case_f_explicit_day_offset():
    assert (
        horizon_relative_minutes(
            "2026-09-10", 60, 1, "2026-09-10T06:00:00+05:30"
        )
        == 1140
    )


# ----------------------------------------------------------------------
# H: naive timezone rejection
# ----------------------------------------------------------------------


def test_naive_horizon_start_string_is_rejected():
    with pytest.raises(HorizonAnchorError, match="explicit UTC offset"):
        horizon_relative_minutes(
            "2026-09-10", 0, 0, "2026-09-10T00:00:00"
        )


def test_naive_horizon_start_datetime_object_is_rejected():
    naive = datetime(2026, 9, 10, 0, 0, 0)

    with pytest.raises(HorizonAnchorError, match="timezone-aware"):
        horizon_relative_minutes("2026-09-10", 0, 0, naive)


def test_parse_horizon_start_accepts_aware_datetime_object():
    aware = datetime(2026, 9, 10, tzinfo=timezone(timedelta(hours=5, minutes=30)))

    assert (
        horizon_relative_minutes("2026-09-10", 0, 0, aware) == 0
    )


def test_malformed_horizon_start_is_rejected():
    with pytest.raises(HorizonAnchorError, match="ISO-8601"):
        horizon_relative_minutes("2026-09-10", 0, 0, "not-a-datetime")


def test_parse_horizon_start_helper_directly():
    parsed = parse_horizon_start("2026-09-10T00:00:00+05:30")

    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(hours=5, minutes=30)


# ----------------------------------------------------------------------
# service_date: required, never invented
# ----------------------------------------------------------------------


def test_service_date_accepts_date_object():
    assert (
        horizon_relative_minutes(
            date(2026, 9, 10), 510, 0, "2026-09-10T00:00:00+05:30"
        )
        == 510
    )


def test_service_date_rejects_datetime_with_time_component():
    """A service_date is a calendar date, not a timestamp."""

    with pytest.raises(HorizonAnchorError, match="calendar date"):
        horizon_relative_minutes(
            datetime(2026, 9, 10, 8, 0),
            0,
            0,
            "2026-09-10T00:00:00+05:30",
        )


def test_malformed_service_date_is_rejected():
    with pytest.raises(HorizonAnchorError, match="ISO-8601 date"):
        horizon_relative_minutes(
            "10-09-2026", 0, 0, "2026-09-10T00:00:00+05:30"
        )


def test_parse_service_date_helper_directly():
    assert parse_service_date("2026-09-10") == date(2026, 9, 10)
    assert parse_service_date(date(2026, 9, 10)) == date(2026, 9, 10)


# ----------------------------------------------------------------------
# clock_minutes bounds - day_offset carries additional days, not the
# clock value itself.
# ----------------------------------------------------------------------


def test_clock_minutes_out_of_range_is_rejected():
    with pytest.raises(HorizonAnchorError, match="within one day"):
        horizon_relative_minutes(
            "2026-09-10", 1440, 0, "2026-09-10T00:00:00+05:30"
        )


def test_negative_clock_minutes_is_rejected():
    with pytest.raises(HorizonAnchorError, match="within one day"):
        horizon_relative_minutes(
            "2026-09-10", -5, 0, "2026-09-10T00:00:00+05:30"
        )


# ----------------------------------------------------------------------
# Never clamps: results may be very negative or very large.
# ----------------------------------------------------------------------


def test_result_may_be_deeply_negative():
    got = horizon_relative_minutes(
        "2026-09-01", 0, 0, "2026-09-10T00:00:00+05:30"
    )

    assert got == -9 * 1440


def test_result_may_exceed_a_week():
    got = horizon_relative_minutes(
        "2026-09-20", 0, 0, "2026-09-10T00:00:00+05:30"
    )

    assert got == 10 * 1440


def test_negative_day_offset_is_permitted():
    """Nothing in the contract forbids an explicit offset before
    service_date; the arithmetic handles it without special-casing."""

    got = horizon_relative_minutes(
        "2026-09-10", 0, -1, "2026-09-10T00:00:00+05:30"
    )

    assert got == -1440
