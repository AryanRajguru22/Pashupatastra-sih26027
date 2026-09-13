"""The one authority for railway section identity (Sprint 3 Step 7).

Sprint 3 Step 6's architecture gate settled what section_id means:

    a stable, opaque identifier for one inter-station railway section,
    whose authoritative definition lives in a Section record.

This module owns that definition. It exists because the codebase
previously had TWO incompatible section vocabularies writing into the
same canonical field - the generator's per-track alias
("SEC-CORRIDOR_A-UP-1", one-to-one with track_id, no span) and the
timetable adapter's station span ("NDLS-NZM"). Because
solver._possession_window_covers_block matches section_id by exact
string equality, a block keyed one way against a window keyed the other
never matches and is refused. One vocabulary, defined here, removes
that class of failure.

SCOPE - deliberately narrow. This is topology lookup only. It does NOT
parse timetables, optimize, score assets, or know anything about the
UI. It answers exactly four kinds of question:

    section_id            -> Section
    (station, station)    -> Section
    section_id            -> which track_ids serve it
    (km, track_id?)       -> Section   (Sprint 3 Step 11, resolve_by_chainage)

IDENTITY RULES
    - A section is DIRECTION-INDEPENDENT. The span between two adjacent
      stations is one section however a train crosses it.
    - Section identity and track identity stay separate. The optimizer's
      resource identity is the PAIR (track_id, section_id) - see
      solver._resource_key - so two running lines over the same span
      remain distinct resources without needing distinct section ids.
    - Compound identifiers such as "UP-1:7-32" are never identity.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from backend.app.data.models import Section, Station


class SectionValidationError(ValueError):
    """A Section is not internally consistent, or conflicts with another."""


class UnknownSectionError(KeyError):
    """No section is registered under that identity."""


class ChainageResolutionError(ValueError):
    """A kilometer location could not be resolved to exactly one section.

    Covers three distinct failure shapes, all fail-closed rather than
    guessed - see SectionRegistry.resolve_by_chainage:

      - km outside every registered section's span;
      - km matched by more than one section, and track_id (if given)
        does not narrow it to exactly one;
      - track_id given, but no section at that km lists it among
        track_ids.
    """


def format_section_id(
    low_km_station_id: str,
    high_km_station_id: str,
) -> str:
    """Build the conventional section_id spelling. ONE construction site.

    Callers MUST pass the two stations already ordered by increasing
    chainage; that ordering is what makes the result
    direction-independent. Everything that needs a conventional
    section_id goes through here or through SectionRegistry, so the
    convention can never drift between modules.

    The result is a NAMING CONVENTION, not the identity: nothing parses
    it back apart. A corridor whose sections come from a real source may
    carry entirely different strings.
    """

    return f"{low_km_station_id}-{high_km_station_id}"


def _validate_section(section: Section, corridor_id: str) -> None:
    """Task 7 consistency rules for a single Section."""

    if not section.section_id:
        raise SectionValidationError(
            "Section is missing a stable section_id."
        )

    if section.corridor_id != corridor_id:
        raise SectionValidationError(
            f"Section {section.section_id!r} declares corridor "
            f"{section.corridor_id!r} but is being registered on "
            f"{corridor_id!r}."
        )

    if not section.start_station_id or not section.end_station_id:
        raise SectionValidationError(
            f"Section {section.section_id!r} needs both a start and an "
            "end station."
        )

    if section.start_station_id == section.end_station_id:
        raise SectionValidationError(
            f"Section {section.section_id!r} starts and ends at the "
            f"same station ({section.start_station_id!r})."
        )

    if not section.km_start < section.km_end:
        raise SectionValidationError(
            f"Section {section.section_id!r} must have "
            f"km_start < km_end; got {section.km_start} .. "
            f"{section.km_end}."
        )


class SectionRegistry:
    """Authoritative per-corridor lookup of Section records.

    Build it either from an explicit list of Sections (a sourced
    topology) or from an ordered station list via
    from_stations(), which derives one section per adjacent station
    pair using the single naming convention above.
    """

    def __init__(
        self,
        corridor_id: str,
        sections: Iterable[Section],
    ) -> None:
        self.corridor_id = corridor_id

        self._by_id: Dict[str, Section] = {}

        # (start, end) normalised into a frozenset so lookup is
        # direction-independent by construction rather than by
        # convention.
        self._by_station_pair: Dict[frozenset, List[Section]] = {}

        for section in sections:
            _validate_section(section, corridor_id)

            if section.section_id in self._by_id:
                raise SectionValidationError(
                    f"Duplicate section_id {section.section_id!r} in "
                    f"corridor {corridor_id!r}. section_id must be "
                    "unique within a corridor."
                )

            self._by_id[section.section_id] = section

            pair = frozenset(
                {section.start_station_id, section.end_station_id}
            )

            # Two DISTINCT sections may share a station pair (parallel
            # routes between the same two stations). That is permitted
            # precisely because their section_ids differ - the identity
            # is the record, not the station pair.
            self._by_station_pair.setdefault(pair, []).append(section)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_stations(
        cls,
        corridor_id: str,
        stations: Sequence[Dict[str, Any] | Station],
        track_ids: Sequence[str] = (),
    ) -> SectionRegistry:
        """Derive one section per adjacent station pair.

        Stations are ordered by chainage first, so the derived ids are
        direction-independent regardless of the order supplied.

        track_ids, when given, are recorded as the running lines that
        serve every section of this corridor. That is an association
        only; it never becomes part of a section's identity.
        """

        normalised: List[Station] = [
            station
            if isinstance(station, Station)
            else Station.from_dict(station)
            for station in stations
        ]

        if len(normalised) < 2:
            raise SectionValidationError(
                "A corridor needs at least two stations to define one "
                f"section; got {len(normalised)}."
            )

        ordered = sorted(normalised, key=lambda s: s.km)

        station_ids = [s.station_id for s in ordered]

        if len(set(station_ids)) != len(station_ids):
            raise SectionValidationError(
                f"Duplicate station_id in corridor {corridor_id!r}: "
                f"{station_ids}"
            )

        sections = [
            Section(
                section_id=format_section_id(
                    start.station_id,
                    end.station_id,
                ),
                corridor_id=corridor_id,
                start_station_id=start.station_id,
                end_station_id=end.station_id,
                km_start=start.km,
                km_end=end.km,
                track_ids=tuple(track_ids),
            )
            for start, end in zip(ordered, ordered[1:])
        ]

        return cls(corridor_id, sections)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, section_id: str) -> Section:
        """Resolve a section_id to its authoritative Section record."""

        try:
            return self._by_id[section_id]
        except KeyError as exc:
            raise UnknownSectionError(
                f"No section {section_id!r} in corridor "
                f"{self.corridor_id!r}. Known sections: "
                f"{sorted(self._by_id)}"
            ) from exc

    def has(self, section_id: str) -> bool:
        return section_id in self._by_id

    def resolve_between(
        self,
        first_station_id: str,
        second_station_id: str,
    ) -> Section:
        """Look a section up by its two stations, in either order.

        Raises when the pair carries more than one distinct section -
        the caller must then address the one it means by section_id,
        because guessing between parallel routes is not safe.
        """

        pair = frozenset({first_station_id, second_station_id})

        matches = self._by_station_pair.get(pair, [])

        if not matches:
            raise UnknownSectionError(
                f"No section between {first_station_id!r} and "
                f"{second_station_id!r} in corridor "
                f"{self.corridor_id!r}."
            )

        if len(matches) > 1:
            raise SectionValidationError(
                f"Stations {first_station_id!r} and "
                f"{second_station_id!r} are joined by "
                f"{len(matches)} distinct sections "
                f"({[s.section_id for s in matches]}). Address the "
                "intended one by section_id."
            )

        return matches[0]

    def resolve_by_chainage(
        self,
        km: float,
        track_id: Optional[str] = None,
    ) -> Section:
        """Resolve one physical chainage location to its containing section.

        BOUNDARY CONVENTION (Sprint 3 Step 11): a section's span is
        HALF-OPEN, [km_start, km_end) - the station shared by two
        adjacent sections belongs to the section that STARTS there, not
        the one that ends there.

        This is NOT invented here. It is the pre-existing convention
        backend.app.data.train_adapter.section_for_km already
        established for exactly this question
        (`start_km <= km < end_km`, with the corridor's own terminus
        specially given to the final section because no section starts
        there). Reusing it is what keeps the codebase from carrying two
        silently-conflicting boundary conventions - train_adapter.py
        itself is untouched (Step 10/11 legacy-path rule), but its
        boundary ANSWER is reused, not its compound-track-id mechanism.

        Two situations are refused rather than guessed, both raising
        ChainageResolutionError:

          - km outside [min(km_start), max(km_end)] across every
            registered section - "outside the corridor" or negative;
          - km matched by more than one section (impossible for a
            registry built by from_stations, which produces strictly
            ordered non-overlapping spans, but not assumed impossible
            here - a registry built directly from an explicit Section
            list could describe overlapping/branching spans).

        track_id, when given, narrows an ambiguous geographic match down
        to sections that list it in track_ids - it does NOT change
        section identity, which stays purely geographic. If track_id
        eliminates every geographic candidate, that is reported as a
        track/location mismatch, not silently ignored; it is exactly
        the fail-closed multi-track case Step 11 requires.
        """

        if not self._by_id:
            raise ChainageResolutionError(
                f"Corridor {self.corridor_id!r} has no registered "
                "sections; cannot resolve any chainage."
            )

        corridor_min_km = min(
            section.km_start for section in self._by_id.values()
        )
        corridor_max_km = max(
            section.km_end for section in self._by_id.values()
        )

        if km < corridor_min_km or km > corridor_max_km:
            raise ChainageResolutionError(
                f"Kilometer {km} is outside corridor "
                f"{self.corridor_id!r}'s registered span "
                f"[{corridor_min_km}, {corridor_max_km}]."
            )

        geographic = [
            section
            for section in self._by_id.values()
            if section.km_start <= km < section.km_end
        ]

        if not geographic and km == corridor_max_km:
            # The corridor's own terminus: no section STARTS there, so
            # the half-open rule alone leaves it unresolved. Given to
            # the section(s) that END there instead - see the docstring.
            geographic = [
                section
                for section in self._by_id.values()
                if section.km_end == corridor_max_km
            ]

        if not geographic:
            raise ChainageResolutionError(
                f"No registered section in corridor "
                f"{self.corridor_id!r} contains kilometer {km}."
            )

        candidates = geographic

        if track_id is not None:
            track_matches = [
                section
                for section in geographic
                if track_id in section.track_ids
            ]

            if not track_matches:
                raise ChainageResolutionError(
                    f"Kilometer {km} resolves to section(s) "
                    f"{[s.section_id for s in geographic]} in corridor "
                    f"{self.corridor_id!r}, none of which list track "
                    f"{track_id!r} among their track_ids "
                    f"({[s.track_ids for s in geographic]})."
                )

            candidates = track_matches

        if len(candidates) > 1:
            raise ChainageResolutionError(
                f"Kilometer {km} is ambiguous between sections "
                f"{[s.section_id for s in candidates]} in corridor "
                f"{self.corridor_id!r}. Supply track_id to disambiguate, "
                "or address the intended section directly."
            )

        return candidates[0]

    def track_ids_for(self, section_id: str) -> Tuple[str, ...]:
        """Which running lines serve this span. Association, not identity."""

        return tuple(self.get(section_id).track_ids)

    def section_ids(self) -> Tuple[str, ...]:
        return tuple(self._by_id)

    def sections(self) -> Tuple[Section, ...]:
        return tuple(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, section_id: object) -> bool:
        return section_id in self._by_id


__all__ = [
    "ChainageResolutionError",
    "SectionRegistry",
    "SectionValidationError",
    "UnknownSectionError",
    "format_section_id",
]
