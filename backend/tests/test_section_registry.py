"""Sprint 3 Step 7: one canonical section vocabulary.

Before this step the codebase had TWO incompatible meanings living in
the same canonical field:

    generator          -> "SEC-CORRIDOR_A-UP-1"  (per-track alias, no span)
    timetable adapter  -> "NDLS-NZM"             (station span)

Because solver._possession_window_covers_block compares section_id by
exact string equality, a block keyed one way against a window keyed the
other never matches and is refused for safety. These tests pin the
single vocabulary that removes that failure class, and the separation
of section identity from track identity that makes it coherent.

Everything here is synthetic/abstract. No real station codes,
chainages, line counts or timetable values appear.
"""

from __future__ import annotations

import pytest

from backend.app.data.canonical_train import (
    CorridorTopology,
    SectionTraversal,
)
from backend.app.data.generator import CorridorDataGenerator
from backend.app.data.models import Section, TrackSegment
from backend.app.data.section_registry import (
    SectionRegistry,
    SectionValidationError,
    UnknownSectionError,
    format_section_id,
)


STATIONS = [
    {"station_id": "S0", "name": "Synthetic 0", "km": 0.0},
    {"station_id": "S1", "name": "Synthetic 1", "km": 12.0},
    {"station_id": "S2", "name": "Synthetic 2", "km": 30.0},
]


@pytest.fixture
def registry() -> SectionRegistry:
    return SectionRegistry.from_stations(
        "TEST_CORRIDOR",
        STATIONS,
        track_ids=["UP-1", "DOWN-1"],
    )


# ----------------------------------------------------------------------
# 1. Registry resolves a canonical section
# ----------------------------------------------------------------------


def test_registry_resolves_a_canonical_section(registry: SectionRegistry):
    section = registry.get("S0-S1")

    assert isinstance(section, Section)
    assert section.section_id == "S0-S1"
    assert section.corridor_id == "TEST_CORRIDOR"
    assert section.start_station_id == "S0"
    assert section.end_station_id == "S1"
    assert (section.km_start, section.km_end) == (0.0, 12.0)


def test_registry_derives_one_section_per_adjacent_pair(
    registry: SectionRegistry,
):
    assert registry.section_ids() == ("S0-S1", "S1-S2")
    assert len(registry) == 2


def test_unknown_section_is_rejected(registry: SectionRegistry):
    with pytest.raises(UnknownSectionError, match="No section"):
        registry.get("NOT-A-SECTION")


# ----------------------------------------------------------------------
# 2. Section lookup is direction-independent
# ----------------------------------------------------------------------


def test_section_lookup_is_direction_independent(registry: SectionRegistry):
    """The same span, whichever way it is named."""

    forward = registry.resolve_between("S0", "S1")
    reverse = registry.resolve_between("S1", "S0")

    assert forward.section_id == reverse.section_id == "S0-S1"
    assert forward is reverse


def test_derived_ids_do_not_depend_on_input_station_order():
    """Stations supplied in reverse still yield the same section ids."""

    forward = SectionRegistry.from_stations("C", STATIONS)
    reversed_input = SectionRegistry.from_stations("C", list(reversed(STATIONS)))

    assert forward.section_ids() == reversed_input.section_ids()


def test_topology_and_registry_agree_on_the_same_spelling():
    """Task 3: one construction site, so the two paths cannot drift.

    The canonical train adapter (CorridorTopology) and the
    SectionRegistry must produce identical section ids for identical
    topology - otherwise the vocabulary collision returns.
    """

    registry = SectionRegistry.from_stations("C", STATIONS)
    topology = CorridorTopology.from_stations(STATIONS)

    assert set(topology.all_section_ids) == set(registry.section_ids())
    assert topology.section_id_between_adjacent("S1", "S0") == (
        registry.resolve_between("S0", "S1").section_id
    )


# ----------------------------------------------------------------------
# 3. Distinct sections may share a station pair
# ----------------------------------------------------------------------


def test_distinct_sections_may_share_a_station_pair():
    """Parallel routes between the same two stations are permitted.

    This is exactly why the station-pair STRING is not the identity:
    the Section record is.
    """

    registry = SectionRegistry(
        "C",
        [
            Section(
                section_id="S0-S1-VIA-EAST",
                corridor_id="C",
                start_station_id="S0",
                end_station_id="S1",
                km_start=0.0,
                km_end=12.0,
                track_ids=("UP-1",),
            ),
            Section(
                section_id="S0-S1-VIA-WEST",
                corridor_id="C",
                start_station_id="S0",
                end_station_id="S1",
                km_start=0.0,
                km_end=14.0,
                track_ids=("UP-2",),
            ),
        ],
    )

    assert len(registry) == 2
    assert registry.get("S0-S1-VIA-EAST").km_end == 12.0
    assert registry.get("S0-S1-VIA-WEST").km_end == 14.0

    # Addressing them by station pair alone is ambiguous, so the
    # registry refuses to guess rather than picking one.
    with pytest.raises(SectionValidationError, match="distinct sections"):
        registry.resolve_between("S0", "S1")


def test_duplicate_section_id_in_one_corridor_is_rejected():
    def make(section_id: str) -> Section:
        return Section(
            section_id=section_id,
            corridor_id="C",
            start_station_id="S0",
            end_station_id="S1",
            km_start=0.0,
            km_end=12.0,
        )

    with pytest.raises(SectionValidationError, match="Duplicate section_id"):
        SectionRegistry("C", [make("S0-S1"), make("S0-S1")])


# ----------------------------------------------------------------------
# 4. Track identity stays separate from section identity
# ----------------------------------------------------------------------


def test_track_id_is_separate_from_section_id(registry: SectionRegistry):
    section = registry.get("S0-S1")

    # The section is a span; the tracks are the roads over it.
    assert section.track_ids == ("UP-1", "DOWN-1")
    assert registry.track_ids_for("S0-S1") == ("UP-1", "DOWN-1")

    # No track id is embedded in the section's identity.
    for track_id in section.track_ids:
        assert track_id not in section.section_id


def test_two_tracks_share_one_section_id(registry: SectionRegistry):
    """One span, two roads - distinguished by the (track, section) pair."""

    section_id = registry.get("S0-S1").section_id

    resource_keys = {
        (track_id, section_id)
        for track_id in registry.track_ids_for(section_id)
    }

    assert resource_keys == {("UP-1", "S0-S1"), ("DOWN-1", "S0-S1")}
    assert len(resource_keys) == 2


# ----------------------------------------------------------------------
# 7. Section consistency validation
# ----------------------------------------------------------------------


def _section(**overrides) -> dict:
    base = dict(
        section_id="S0-S1",
        corridor_id="C",
        start_station_id="S0",
        end_station_id="S1",
        km_start=0.0,
        km_end=12.0,
    )
    base.update(overrides)
    return base


def test_section_must_belong_to_its_corridor():
    with pytest.raises(SectionValidationError, match="declares corridor"):
        SectionRegistry("C", [Section(**_section(corridor_id="OTHER"))])


def test_section_km_must_be_monotonic():
    with pytest.raises(SectionValidationError, match="km_start < km_end"):
        SectionRegistry("C", [Section(**_section(km_start=12.0, km_end=0.0))])


def test_section_endpoints_must_differ():
    with pytest.raises(SectionValidationError, match="same station"):
        SectionRegistry("C", [Section(**_section(end_station_id="S0"))])


def test_section_needs_a_stable_id():
    with pytest.raises(SectionValidationError, match="stable section_id"):
        SectionRegistry("C", [Section(**_section(section_id=""))])


def test_corridor_needs_two_stations_to_have_a_section():
    with pytest.raises(SectionValidationError, match="at least two stations"):
        SectionRegistry.from_stations("C", STATIONS[:1])


# ----------------------------------------------------------------------
# 9. Compound track ids are rejected by canonical models
# ----------------------------------------------------------------------


def test_section_rejects_compound_track_id():
    with pytest.raises(ValueError, match="bare identifier"):
        Section(**_section(track_ids=("UP-1:0-12",)))


def test_traversal_rejects_compound_track_id():
    with pytest.raises(ValueError, match="bare identifier"):
        SectionTraversal(
            section_id="S0-S1",
            track_id="UP-1:0-12",
            enter_minute=10,
            exit_minute=20,
        )


def test_format_section_id_is_the_single_construction_site():
    assert format_section_id("S0", "S1") == "S0-S1"


# ----------------------------------------------------------------------
# 5-7. Generator migration
# ----------------------------------------------------------------------


def test_track_segment_no_longer_has_a_section_id():
    """Task 5: the misnomer is gone, not merely re-pointed."""

    track = CorridorDataGenerator().generate_corridor_tracks(
        "CORRIDOR_A", num_tracks=2, corridor_length_km=35.0
    )[0]

    assert not hasattr(track, "section_id")
    assert track.segment_name == "SEG-CORRIDOR_A-UP-1"

    # And the alias is not silently reintroduced through serialization.
    assert "section_id" not in track.to_dict()
    assert track.to_dict()["segment_name"] == "SEG-CORRIDOR_A-UP-1"


def test_generator_section_id_comes_from_the_registry():
    """Task 4: canonical identity, not a per-track alias."""

    generator = CorridorDataGenerator()
    request = generator.generate_scenario_a(42)

    registry = generator.build_section_registry(
        "CORRIDOR_A",
        generator.generate_corridor_tracks(
            "CORRIDOR_A", num_tracks=2, corridor_length_km=35.0
        ),
        35.0,
    )

    expected = registry.section_ids()[0]

    assert expected == "CORRIDOR_A_S0-CORRIDOR_A_S1"

    for candidate in request.candidates:
        assert candidate.section_id == expected
        # The old per-track alias must be gone entirely.
        assert not candidate.section_id.startswith("SEC-")
        assert candidate.track_id not in candidate.section_id


def test_generated_block_candidate_has_bare_track_and_canonical_section():
    """Task 8.6."""

    request = CorridorDataGenerator().generate_scenario_a(42)

    for candidate in request.candidates:
        assert ":" not in candidate.track_id
        assert candidate.track_id in {"UP-1", "DOWN-1"}
        assert candidate.section_id


def test_generated_possession_window_has_bare_track_and_canonical_section():
    """Task 8.7."""

    request = CorridorDataGenerator().generate_scenario_a(42)

    section_ids = {w.section_id for w in request.possession_windows}

    for window in request.possession_windows:
        assert ":" not in window.track_id
        assert window.track_id in {"UP-1", "DOWN-1"}

    # One span, shared by both tracks - direction-independent.
    assert section_ids == {"CORRIDOR_A_S0-CORRIDOR_A_S1"}


def test_generated_blocks_and_windows_share_one_vocabulary():
    """The failure this whole step exists to prevent.

    A block and a window on the same track must agree on section_id,
    or solver._possession_window_covers_block refuses the block.
    """

    request = CorridorDataGenerator().generate_scenario_a(42)

    block_sections = {c.section_id for c in request.candidates}
    window_sections = {w.section_id for w in request.possession_windows}

    assert block_sections == window_sections


def test_generator_resource_grouping_is_unchanged_by_the_migration():
    """Semantics preserved: one resource per track, exactly as before.

    The old per-track alias made section_id functionally dependent on
    track_id. The new single span is shared, so grouping still comes
    out one-per-track - the vocabulary changed, the partition did not.
    """

    from backend.app.optimizer.solver import _resource_key

    request = CorridorDataGenerator().generate_scenario_a(42)

    keys = {_resource_key(c) for c in request.candidates}
    tracks = {c.track_id for c in request.candidates}

    assert len(keys) == len(tracks)


def test_synthetic_sections_are_obviously_synthetic():
    """Task 9: no real station codes are introduced anywhere."""

    stations = CorridorDataGenerator().generate_synthetic_stations(
        "CORRIDOR_A", 35.0
    )

    assert [s["station_id"] for s in stations] == [
        "CORRIDOR_A_S0",
        "CORRIDOR_A_S1",
    ]
    assert all("synthetic" in s["name"] for s in stations)
    assert stations[0]["km"] == 0.0
    assert stations[1]["km"] == 35.0


def test_multi_section_corridor_refuses_blanket_assignment():
    """A corridor with real topology cannot silently get one section."""

    generator = CorridorDataGenerator()

    multi = SectionRegistry.from_stations("C", STATIONS)

    with pytest.raises(ValueError, match="only knows how to assign"):
        generator._sole_section_id(multi)


# ----------------------------------------------------------------------
# 8. Canonical train adapter resolves through the SAME vocabulary
# ----------------------------------------------------------------------


def test_adapter_section_ids_resolve_in_the_registry():
    """Task 8.8 - end to end, not merely two agreeing formatters.

    Every section_id the timetable adapter emits for a train must be a
    section the corridor's own SectionRegistry can resolve. This is the
    property that would catch a future drift where one side starts
    prefixing or normalising and the other does not.
    """

    from backend.app.data.timetable_adapter import convert_train

    registry = SectionRegistry.from_stations(
        "TEST_CORRIDOR", STATIONS, track_ids=["UP-1", "DOWN-1"]
    )
    topology = CorridorTopology.from_stations(STATIONS)

    state = convert_train(
        {
            "train_number": "T1",
            "track_assignment": "UP-1",
            "direction": "UP",
            "station_times": [
                {"station_id": "S2", "scheduled_arrival": "10:00",
                 "scheduled_departure": "10:00"},
                {"station_id": "S1", "scheduled_arrival": "10:20",
                 "scheduled_departure": "10:22"},
                {"station_id": "S0", "scheduled_arrival": "10:45",
                 "scheduled_departure": "10:45"},
            ],
        },
        topology,
    )

    assert state.traversals

    for traversal in state.traversals:
        assert registry.has(traversal.section_id), (
            f"adapter emitted {traversal.section_id!r}, which the "
            f"registry cannot resolve: {registry.section_ids()}"
        )

        section = registry.get(traversal.section_id)

        # The traversal's track must be one the section actually serves.
        assert traversal.track_id in section.track_ids


def test_adapter_and_generator_vocabularies_are_the_same_shape():
    """Both paths produce registry-resolvable ids, never compound ones."""

    from backend.app.data.timetable_adapter import convert_train

    topology = CorridorTopology.from_stations(STATIONS)

    state = convert_train(
        {
            "train_number": "T1",
            "track_assignment": "UP-1",
            "station_times": [
                {"station_id": "S0", "scheduled_arrival": "10:00",
                 "scheduled_departure": "10:00"},
                {"station_id": "S1", "scheduled_arrival": "10:20",
                 "scheduled_departure": "10:20"},
            ],
        },
        topology,
    )

    generated = CorridorDataGenerator().generate_scenario_a(42)

    adapter_ids = {t.section_id for t in state.traversals}
    generator_ids = {c.section_id for c in generated.candidates}

    for section_id in adapter_ids | generator_ids:
        # Same shape: an opaque station-pair key, never a compound
        # track/km string, and never a per-track alias.
        assert ":" not in section_id
        assert not section_id.startswith("SEC-")
