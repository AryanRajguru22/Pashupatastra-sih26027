"""The one authoritative horizon-anchoring conversion (Sprint 3 Step 8).

Sprint 3 Step 6's architecture gate settled the conversion contract
between a timetable's own local calendar (service_date + clock time +
day_offset) and OptimizationRequest.horizon_start:

    minute_from_horizon =
        (service_date_local_midnight
         + clock_time
         + day_offset * 1440)
        - horizon_start

This module is the single place that arithmetic is performed. Nothing
else in the codebase should re-derive it.

WHY service_date IS NOT INVENTED
    horizon_start anchors minute 0 of the PLANNING horizon. It is not,
    and must not be assumed to be, the calendar date a timetable event
    belongs to - Step 6 Decision 2, Case C/D/E below prove why: a
    horizon can start at 06:00 rather than midnight, and can run for
    more than one day. Without an explicit service_date, "23:30" is
    genuinely ambiguous - it could be the horizon's own first day or
    any later one. Guessing (e.g. "closest date to horizon_start")
    would fabricate chronology the source never stated. So this module
    REQUIRES service_date from the caller and never derives one from
    horizon_start, today's date, a fixture name, a train number, or
    anything else.

WHY THE ANCHOR IS FIXED-OFFSET ARITHMETIC, NOT A TIMEZONE CONVERSION
    service_date's midnight is interpreted in horizon_start's OWN
    tzinfo - the same local railway clock the whole timetable and the
    whole planning horizon are stated in. This is deliberately NOT a
    general timezone conversion: nothing here converts between
    calendar systems or DST regimes. It is valid specifically because
    Asia/Kolkata (the domain this railway system models) has no DST -
    a fixed UTC offset is exact for every date. A future timezone with
    DST would need this precondition re-examined; it is not silently
    assumed to generalise.

Every value here stays an INTEGER number of minutes. There is no
floating-point timestamp anywhere in this module's public contract.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Union

from backend.app.data.canonical_train import MINUTES_PER_DAY


class HorizonAnchorError(ValueError):
    """horizon_start / service_date / day_offset input is invalid."""


def parse_horizon_start(horizon_start: str) -> datetime:
    """Parse and validate an explicit-offset ISO-8601 horizon_start.

    Rejects a naive datetime outright. This enforces the SAME rule as
    contracts.schemas._validate_horizon_start (Sprint 3 Step 4) -
    duplicated rather than imported so this module stays
    dependency-free of the contracts package, but the rule itself -
    explicit UTC offset required, naive rejected - must never diverge
    between the two.
    """

    try:
        parsed = datetime.fromisoformat(horizon_start)
    except (TypeError, ValueError) as exc:
        raise HorizonAnchorError(
            "horizon_start must be an ISO-8601 datetime string with "
            f"an explicit UTC offset, got {horizon_start!r}"
        ) from exc

    if parsed.tzinfo is None:
        raise HorizonAnchorError(
            "horizon_start must carry an explicit UTC offset (e.g. "
            f"'+05:30' for Asia/Kolkata); got naive datetime "
            f"{horizon_start!r}"
        )

    return parsed


def _resolve_horizon_start(horizon_start: Union[str, datetime]) -> datetime:
    """Accept either the ISO-8601 string form or an already-parsed
    timezone-aware datetime. A naive datetime object is rejected just
    as a naive string would be - the tz-awareness rule applies
    regardless of which form the caller used."""

    if isinstance(horizon_start, datetime):
        if horizon_start.tzinfo is None:
            raise HorizonAnchorError(
                "horizon_start datetime must be timezone-aware; got a "
                "naive datetime."
            )
        return horizon_start

    return parse_horizon_start(horizon_start)


def parse_service_date(service_date: Union[str, date]) -> date:
    """Parse an explicit service date.

    Never invented, never defaulted. A service_date is source-supplied
    chronology data - see the module docstring for why this module
    never derives one on the caller's behalf.
    """

    if isinstance(service_date, datetime):
        raise HorizonAnchorError(
            "service_date must be a calendar date, not a datetime "
            f"with a time component; got {service_date!r}. Pass a "
            "date object or an ISO-8601 'YYYY-MM-DD' string."
        )

    if isinstance(service_date, date):
        return service_date

    try:
        return date.fromisoformat(str(service_date))
    except (TypeError, ValueError) as exc:
        raise HorizonAnchorError(
            "service_date must be an ISO-8601 date (YYYY-MM-DD), got "
            f"{service_date!r}"
        ) from exc


def horizon_relative_minutes(
    service_date: Union[str, date],
    clock_minutes: int,
    day_offset: int,
    horizon_start: Union[str, datetime],
) -> int:
    """The single authoritative horizon-anchoring conversion.

        minute_from_horizon =
            (service_date_local_midnight
             + clock_minutes
             + day_offset * 1440)
            - horizon_start

    Parameters
        service_date   explicit calendar date the event's clock time is
                        stated against (a date, or an ISO-8601
                        'YYYY-MM-DD' string). REQUIRED - never
                        defaulted or derived.
        clock_minutes   minutes since that date's local midnight,
                        0..1439 (i.e. an already-parsed "HH:MM").
        day_offset      explicit additional whole days past
                        service_date this specific event falls on.
                        0 for same-day; 1 for "the next day", etc.
                        May be negative.
        horizon_start   OptimizationRequest.horizon_start: an
                        explicit-UTC-offset ISO-8601 string, or an
                        already-parsed timezone-aware datetime.

    Returns an integer number of minutes relative to horizon_start.
    The result may be NEGATIVE (the event precedes horizon_start) or
    exceed 1440 (a horizon longer than one day, or a day_offset placing
    the event on a later calendar date) - see the six acceptance cases
    in this module's test suite (test_horizon_anchor.py) for worked
    examples covering both.

    Raises HorizonAnchorError for a naive/malformed horizon_start, a
    malformed service_date, or an out-of-range clock_minutes. Never
    fabricates a missing date and never clamps or wraps the result.
    """

    parsed_service_date = parse_service_date(service_date)
    parsed_horizon_start = _resolve_horizon_start(horizon_start)

    if not (0 <= clock_minutes < MINUTES_PER_DAY):
        raise HorizonAnchorError(
            "clock_minutes must be within one day (0.."
            f"{MINUTES_PER_DAY - 1}); got {clock_minutes}. Use "
            "day_offset to express additional days, not an "
            "out-of-range clock value."
        )

    # service_date's midnight in horizon_start's OWN timezone - the
    # single local clock the whole conversion is stated in. See the
    # module docstring for why this is not a timezone conversion.
    service_date_local_midnight = datetime(
        parsed_service_date.year,
        parsed_service_date.month,
        parsed_service_date.day,
        tzinfo=parsed_horizon_start.tzinfo,
    )

    event_datetime = service_date_local_midnight + timedelta(
        minutes=clock_minutes + day_offset * MINUTES_PER_DAY
    )

    delta = event_datetime - parsed_horizon_start

    total_seconds = delta.total_seconds()

    if total_seconds % 60 != 0:
        # Unreachable with a fixed-offset horizon_start and integer
        # minute inputs - guarded explicitly rather than silently
        # truncating a fractional result into a wrong integer minute.
        raise HorizonAnchorError(
            "Horizon offset did not resolve to a whole number of "
            f"minutes ({total_seconds} seconds). This should be "
            "unreachable with a fixed-offset horizon_start; check for "
            "a fractional UTC offset."
        )

    return int(total_seconds // 60)


__all__ = [
    "HorizonAnchorError",
    "horizon_relative_minutes",
    "parse_horizon_start",
    "parse_service_date",
]
