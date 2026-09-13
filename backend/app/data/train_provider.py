"""Train data provider boundary (Sprint 3 Step 5).

The intended end-to-end shape, which this module establishes without
connecting anything external:

    provider (static timetable / future feed)
        -> TrainDataProvider
        -> CanonicalTrainState
        -> possession derivation
        -> OptimizationRequest
        -> solver

The boundary exists so provider-specific vocabulary stays HERE. The
solver, the contracts and the optimizer know only about
contracts.PossessionWindow; nothing downstream can tell which provider
produced a window, and no provider name appears in those layers.

NO LIVE PROVIDER IS IMPLEMENTED. StaticTimetableProvider reads an
already-loaded timetable structure and nothing else - no network, no
credentials, no polling. TrainDataProvenance has no "LIVE" member, so a
static provider cannot label its output as live even by mistake.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional, Protocol, Sequence, Union

from contracts import PossessionWindow

from backend.app.data.canonical_train import (
    CorridorTopology,
    TrainDataProvenance,
    TrainDataSnapshot,
)
from backend.app.data.section_registry import SectionRegistry
from backend.app.data.timetable_adapter import (
    DEFAULT_MAX_LEG_MINUTES,
    DEFAULT_MAX_ROLLOVER_DAYS,
    DEFAULT_MINIMUM_WINDOW_MINUTES,
    DEFAULT_SAFETY_BUFFER_MINUTES,
    convert_timetable,
    derive_section_possession_windows,
)


class TrainDataProvider(Protocol):
    """Anything that can produce canonical train state for a corridor.

    A future live feed implements this same one method. Because the
    return type is a TrainDataSnapshot - already canonical, already
    carrying its own rejections and provenance - swapping providers
    cannot change the shape of what possession derivation consumes.
    """

    def fetch(self) -> TrainDataSnapshot:
        ...


class StaticTimetableProvider:
    """First provider: an in-memory scheduled timetable.

    Deliberately takes already-parsed records rather than a path or a
    URL, so this class has no I/O of its own and no notion of where the
    timetable came from beyond the provenance label it is given.

    provenance defaults to SYNTHETIC_SCHEDULED because every timetable
    currently checked into this repository is synthetic demo data.
    Pass TrainDataProvenance.REAL_SCHEDULED only for records actually
    transcribed from a published timetable.

    HORIZON ANCHORING (Sprint 3 Step 10)
        horizon_start, when given, is passed straight through to
        convert_timetable, which anchors every resulting traversal to
        OptimizationRequest.horizon_start via
        backend.app.data.horizon_anchor - see convert_train's docstring.
        It lives on the provider rather than on fetch() because it is a
        CONVERSION parameter, exactly like prefer_actual and
        max_rollover_days: the provider is what turns source records
        into canonical horizon-relative state, so it must know the
        horizon that state is stated against.

        Leaving it None preserves the Step 5 behaviour exactly
        (traversal minutes relative to the train's own first event), so
        every pre-Step-10 caller keeps working unmodified. In anchored
        mode each record must carry an explicit service_date; a record
        without one is rejected individually, never given an invented
        date.
    """

    def __init__(
        self,
        records: Sequence[Dict[str, Any]],
        topology: CorridorTopology,
        provenance: str = TrainDataProvenance.SYNTHETIC_SCHEDULED.value,
        observed_at: Optional[str] = None,
        prefer_actual: bool = True,
        max_rollover_days: int = DEFAULT_MAX_ROLLOVER_DAYS,
        max_leg_minutes: int = DEFAULT_MAX_LEG_MINUTES,
        horizon_start: Optional[Union[str, datetime]] = None,
    ) -> None:
        if provenance not in {
            member.value for member in TrainDataProvenance
        }:
            raise ValueError(
                f"Unknown train data provenance {provenance!r}. "
                "Permitted values are "
                f"{[m.value for m in TrainDataProvenance]} - note there "
                "is deliberately no LIVE value."
            )

        self._records = list(records)
        self._topology = topology
        self._provenance = provenance
        self._observed_at = observed_at
        self._prefer_actual = prefer_actual
        self._max_rollover_days = max_rollover_days
        self._max_leg_minutes = max_leg_minutes
        self._horizon_start = horizon_start

    def fetch(self) -> TrainDataSnapshot:
        return convert_timetable(
            self._records,
            self._topology,
            prefer_actual=self._prefer_actual,
            max_rollover_days=self._max_rollover_days,
            max_leg_minutes=self._max_leg_minutes,
            provenance=self._provenance,
            observed_at=self._observed_at,
            horizon_start=self._horizon_start,
        )


class SectionResolutionError(ValueError):
    """A derived window's (track_id, section_id) is not in the registry."""


def validate_windows_against_registry(
    windows: Sequence[PossessionWindow],
    registry: SectionRegistry,
) -> None:
    """Every derived window must resolve in the corridor's registry.

    Sprint 3 Step 7 made SectionRegistry the sole authority for section
    identity; Step 10 wires the timetable path into production, so this
    is where the two are checked against each other. Three invariants,
    all fail-closed - a window that cannot be resolved is an error, and
    is never repaired by guessing a section or dropping the section_id:

      1. the window declares a section_id at all;
      2. that section_id is registered in this corridor;
      3. the window's track_id is one of the running lines the registry
         records as serving that section.

    In practice this cannot fail when the topology and the registry were
    built from the same station list (see
    backend.app.data.corridor_dataset.load_corridor_dataset, which does
    exactly that). It is checked anyway because the failure it guards
    against - two section vocabularies drifting apart so that windows
    and blocks never match, and work is silently refused - is precisely
    the defect Step 7 was created to remove.
    """

    for window in windows:
        if not window.section_id:
            raise SectionResolutionError(
                f"Derived possession window {window.window_id!r} "
                "carries no section_id. Canonical windows are always "
                "section-scoped; a window without one cannot be "
                "resolved against the corridor's SectionRegistry."
            )

        if ":" in window.track_id:
            raise SectionResolutionError(
                "track_id must stay a bare identifier such as 'UP-1'; "
                f"got compound {window.track_id!r} on window "
                f"{window.window_id!r}."
            )

        if not registry.has(window.section_id):
            raise SectionResolutionError(
                f"Derived possession window {window.window_id!r} "
                f"resolves to section {window.section_id!r}, which is "
                f"not registered in corridor {registry.corridor_id!r} "
                f"(known: {sorted(registry.section_ids())})."
            )

        serving_tracks = registry.track_ids_for(window.section_id)

        if serving_tracks and window.track_id not in serving_tracks:
            raise SectionResolutionError(
                f"Derived possession window {window.window_id!r} is on "
                f"track {window.track_id!r}, which the registry does "
                f"not record as serving section {window.section_id!r} "
                f"(serving tracks: {list(serving_tracks)})."
            )


def possession_windows_from_provider(
    provider: TrainDataProvider,
    topology: CorridorTopology,
    horizon_minutes: int,
    safety_buffer_minutes: int = DEFAULT_SAFETY_BUFFER_MINUTES,
    minimum_window_minutes: int = DEFAULT_MINIMUM_WINDOW_MINUTES,
    registry: Optional[SectionRegistry] = None,
) -> tuple[list[PossessionWindow], TrainDataSnapshot]:
    """Run one provider fetch all the way to canonical possession windows.

    Returns the windows AND the snapshot they came from, so a caller can
    surface rejections/provenance rather than seeing only the windows
    that survived. A caller that discards the snapshot loses the record
    of which trains were refused, which is exactly the information an
    operator needs to explain a missing maintenance opportunity.

    registry, when given, is checked against every derived window before
    the windows are returned - see validate_windows_against_registry.
    It is a parameter of this bridge rather than a separate step a
    caller must remember, so a production caller cannot accidentally
    skip section validation.
    """

    snapshot = provider.fetch()

    windows = derive_section_possession_windows(
        snapshot,
        topology,
        horizon_minutes=horizon_minutes,
        safety_buffer_minutes=safety_buffer_minutes,
        minimum_window_minutes=minimum_window_minutes,
    )

    if registry is not None:
        validate_windows_against_registry(windows, registry)

    return windows, snapshot


__all__ = [
    "SectionResolutionError",
    "StaticTimetableProvider",
    "TrainDataProvider",
    "possession_windows_from_provider",
    "validate_windows_against_registry",
]
