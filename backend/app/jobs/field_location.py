"""Human-friendly field location -> corridor-absolute metres (Slice 8).

THE GAP
    The canonical resolver (backend.app.jobs.resource_resolution.
    resolve_job_resource) works in corridor-absolute metres. A field
    worker knows "400 m past Nizamuddin toward Faridabad", not
    "7 400 metres from the corridor origin".

THE LAYER
    human/field location                (from station, toward station, offset)
        -> section-relative location    (one Section, offset along it)
        -> corridor-absolute metres     (km_start/km_end of that Section)
        -> resolve_job_resource()       (UNCHANGED - the only authority)
        -> (track_id, section_id)

    This is a converter, not a second location model. Only corridor-
    absolute metres are ever stored; SectionRegistry stays the single
    source of section identity and its half-open [km_start, km_end)
    convention is inherited, not re-decided. A span that ends exactly on
    the far station therefore belongs to the NEXT section and is refused
    by the resolver as boundary-straddling, exactly as an absolute-metre
    request would be - describe such a job with an end offset just short
    of the station.

DIRECTION
    A Section is direction-independent. The (from, toward) pair says
    which end offsets are measured from and, with the section itself,
    fixes the direction of increasing offset. No track-direction
    (UP/DOWN) check is made: the data model attaches direction to a
    TrackSegment, not to a Section, so there is nothing to validate it
    against and none is invented.

FAILURE
    Every refusal is a FieldLocationError (a ValueError -> 400). In
    particular SectionRegistry raises UnknownSectionError, a KeyError
    subclass, for an unknown station pair; left alone, the HTTP layer
    would report that as 404 "job not found". It is caught here and
    re-raised as a FieldLocationError so the caller sees what was wrong.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from backend.app.data.section_registry import (
    SectionRegistry,
    SectionValidationError,
    UnknownSectionError,
)


# The declared absolute metres and the converted metres are compared with
# this tolerance: 1 millimetre. Both derive from the same 2-decimal
# kilometre figures, so any real disagreement is orders larger; this only
# absorbs float representation error (5.44 km * 1000).
LOCATION_MATCH_TOLERANCE_M = 0.001

_METRE_GRID = 6


class FieldLocationError(ValueError):
    """The field location cannot be converted safely. Fails closed: 400."""


class LocationInputConflictError(FieldLocationError):
    """The field location and the declared absolute metres disagree.

    Neither is preferred: a caller that gives two locations which do not
    describe the same span is refused, not silently corrected."""


@dataclass(frozen=True)
class ResolvedFieldLocation:
    section_id: str
    from_station_id: str
    toward_station_id: str
    offset_start_m: float
    offset_end_m: float
    section_length_m: float
    distance_start_m: float
    distance_end_m: float

    def to_metadata(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "from_station_id": self.from_station_id,
            "toward_station_id": self.toward_station_id,
            "offset_start_m": self.offset_start_m,
            "offset_end_m": self.offset_end_m,
            "derived_distance_start_m": self.distance_start_m,
            "derived_distance_end_m": self.distance_end_m,
        }


def _finite(name: str, value: Any) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise FieldLocationError(
            f"{name} must be a finite number; got {value!r}."
        )

    return float(value)


def convert_field_location(
    registry: SectionRegistry,
    from_station_id: str,
    toward_station_id: str,
    offset_start_m: float,
    offset_end_m: float,
) -> ResolvedFieldLocation:
    """Convert (from, toward, offsets) to corridor-absolute metres.

    Offsets are metres from `from_station_id` toward `toward_station_id`
    along the one section joining them. Raises FieldLocationError when:
      - either station id is blank, or they are the same station;
      - no section joins the two stations (unknown station, or stations
        that are not adjacent) or more than one does (parallel routes -
        the caller cannot be guessed for);
      - an offset is negative, non-finite, or offset_end <= offset_start;
      - offset_end lies beyond the section's far station.
    """

    if not from_station_id or not toward_station_id:
        raise FieldLocationError(
            "from_station_id and toward_station_id are both required."
        )

    if from_station_id == toward_station_id:
        raise FieldLocationError(
            f"from_station_id and toward_station_id are the same station "
            f"({from_station_id!r}); the direction of the offset is "
            "undefined."
        )

    start = _finite("offset_start_m", offset_start_m)
    end = _finite("offset_end_m", offset_end_m)

    if start < 0:
        raise FieldLocationError(
            f"offset_start_m must be >= 0; got {start}."
        )

    if end <= start:
        raise FieldLocationError(
            f"offset_end_m ({end}) must be greater than offset_start_m "
            f"({start})."
        )

    try:
        section = registry.resolve_between(from_station_id, toward_station_id)
    except UnknownSectionError as exc:
        raise FieldLocationError(
            f"No section joins stations {from_station_id!r} and "
            f"{toward_station_id!r} in corridor {registry.corridor_id!r}. "
            "Both must be stations at the two ends of one section: "
            f"{exc.args[0] if exc.args else exc}"
        ) from exc
    except SectionValidationError as exc:
        raise FieldLocationError(
            f"The stations {from_station_id!r} and {toward_station_id!r} "
            f"do not identify one section: {exc}"
        ) from exc

    length_m = round((section.km_end - section.km_start) * 1000.0, _METRE_GRID)

    if end > length_m:
        raise FieldLocationError(
            f"offset_end_m ({end}) is beyond section {section.section_id!r}, "
            f"which is {length_m} m long between {section.start_station_id!r} "
            f"and {section.end_station_id!r}."
        )

    if from_station_id == section.start_station_id:
        origin_m = section.km_start * 1000.0
        distance_start = origin_m + start
        distance_end = origin_m + end
    else:
        # Measured from the section's far (higher-km) end, so the larger
        # offset is the lower absolute distance.
        origin_m = section.km_end * 1000.0
        distance_start = origin_m - end
        distance_end = origin_m - start

    return ResolvedFieldLocation(
        section_id=section.section_id,
        from_station_id=from_station_id,
        toward_station_id=toward_station_id,
        offset_start_m=start,
        offset_end_m=end,
        section_length_m=length_m,
        distance_start_m=round(distance_start, _METRE_GRID),
        distance_end_m=round(distance_end, _METRE_GRID),
    )


def assert_matches_declared(
    resolved: ResolvedFieldLocation,
    declared_start_m: float,
    declared_end_m: float,
) -> None:
    """Refuse when the converted span is not the declared span.

    Distances stay REQUIRED on the frozen v1 request, so a field location
    can only be a cross-check on them, never a substitute: relaxing
    `distance_start`/`distance_end` would change JobCreateRequest.required
    and is a v1 contract change deferred to a later API version.
    """

    if (
        abs(resolved.distance_start_m - declared_start_m) > LOCATION_MATCH_TOLERANCE_M
        or abs(resolved.distance_end_m - declared_end_m) > LOCATION_MATCH_TOLERANCE_M
    ):
        raise LocationInputConflictError(
            "The field location and the declared distances describe "
            f"different spans: {resolved.offset_start_m}-"
            f"{resolved.offset_end_m} m from {resolved.from_station_id!r} "
            f"toward {resolved.toward_station_id!r} converts to "
            f"{resolved.distance_start_m}-{resolved.distance_end_m} m, but "
            f"distance_start/distance_end are {declared_start_m}-"
            f"{declared_end_m} m. Neither is preferred; correct one."
        )


__all__ = [
    "LOCATION_MATCH_TOLERANCE_M",
    "FieldLocationError",
    "LocationInputConflictError",
    "ResolvedFieldLocation",
    "assert_matches_declared",
    "convert_field_location",
]
