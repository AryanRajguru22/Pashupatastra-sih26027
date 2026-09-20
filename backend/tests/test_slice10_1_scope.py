"""Slice 10.1C: RailwayScope (corridor_id + explicit section_ids).

Pins that a scope is a finite, explicit, immutable set of sections of one
corridor; that it can be validated against authoritative topology
(SectionRegistry, injected read-only); and that nothing wildcard, global
or implicit can be expressed. The multi-section tests use the checked-in
CORR-NDLS-AGC dataset corridor (six sections), loaded read-only.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect

import pytest

from backend.app.data.corridor_dataset import load_corridor_dataset
from backend.app.data.section_registry import SectionRegistry
from backend.app.identity import scope as scope_module
from backend.app.identity.scope import RailwayScope, ScopeError, railway_scope

CORRIDOR = "CORR-NDLS-AGC"
S1, S2, S3, S4, S5, S6 = (
    "NDLS-NZM",
    "NZM-FDB",
    "FDB-PWL",
    "PWL-MTJ",
    "MTJ-RKM",
    "RKM-AGC",
)


@pytest.fixture(scope="module")
def registry() -> SectionRegistry:
    return load_corridor_dataset().registry


@pytest.fixture(scope="module")
def other_registry(registry) -> SectionRegistry:
    """Same station spans, different corridor: identical section strings."""

    stations = [
        {"station_id": s.start_station_id, "km": s.km_start}
        for s in registry.sections()
    ]
    last = registry.sections()[-1]
    stations.append({"station_id": last.end_station_id, "km": last.km_end})
    return SectionRegistry.from_stations("CORR-OTHER", stations, ["UP-1", "DOWN-1"])


def _reason(excinfo) -> str:
    return excinfo.value.reason


# ----------------------------------------------------------------------
# Fixture sanity: the dataset corridor is the multi-section one
# ----------------------------------------------------------------------


def test_dataset_corridor_has_six_sections(registry):
    assert registry.corridor_id == CORRIDOR
    assert registry.section_ids() == (S1, S2, S3, S4, S5, S6)


def test_other_corridor_reuses_the_same_section_strings(registry, other_registry):
    assert other_registry.corridor_id == "CORR-OTHER"
    assert other_registry.section_ids() == registry.section_ids()


# ----------------------------------------------------------------------
# Construction (structural)
# ----------------------------------------------------------------------


def test_valid_single_section_scope():
    scope = RailwayScope(CORRIDOR, [S3])

    assert scope.corridor_id == CORRIDOR
    assert scope.section_ids == frozenset({S3})


def test_valid_multi_section_scope():
    scope = RailwayScope(CORRIDOR, [S2, S3, S4])

    assert scope.section_ids == frozenset({S2, S3, S4})


def test_scope_is_immutable():
    scope = RailwayScope(CORRIDOR, [S2])

    with pytest.raises(dataclasses.FrozenInstanceError):
        scope.corridor_id = "OTHER"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        scope.section_ids = frozenset({S1})  # type: ignore[misc]

    assert isinstance(scope.section_ids, frozenset)
    assert not hasattr(scope.section_ids, "add")
    assert RailwayScope.__dataclass_params__.frozen


def test_scope_is_hashable():
    assert len({RailwayScope(CORRIDOR, [S2]), RailwayScope(CORRIDOR, [S2])}) == 1


def test_mutating_the_source_collection_cannot_widen_scope():
    source = {S2}
    scope = RailwayScope(CORRIDOR, source)
    source.add(S1)

    assert scope.section_ids == frozenset({S2})


@pytest.mark.parametrize("empty", [[], (), set(), frozenset(), iter(())])
def test_empty_section_set_is_rejected_and_never_means_whole_corridor(empty):
    with pytest.raises(ScopeError) as excinfo:
        RailwayScope(CORRIDOR, empty)

    assert _reason(excinfo) == "EMPTY_SCOPE"


def test_none_section_ids_is_rejected():
    with pytest.raises(ScopeError) as excinfo:
        RailwayScope(CORRIDOR, None)  # type: ignore[arg-type]

    assert _reason(excinfo) == "INVALID_SECTION"


@pytest.mark.parametrize(
    "token", ["*", "ALL", "all", "All", " * ", "**", "*-*", "NDLS-*"]
)
def test_wildcard_and_all_markers_are_rejected(token):
    with pytest.raises(ScopeError) as excinfo:
        RailwayScope(CORRIDOR, [token])

    assert _reason(excinfo) in {"WILDCARD_SECTION", "INVALID_SECTION"}


def test_wildcard_beside_a_real_section_is_still_rejected():
    with pytest.raises(ScopeError):
        RailwayScope(CORRIDOR, [S2, "*"])


@pytest.mark.parametrize("token", ["*", "ALL", "all"])
def test_wildcard_reason_is_specific_for_star_and_all(token):
    with pytest.raises(ScopeError) as excinfo:
        RailwayScope(CORRIDOR, [token])

    assert _reason(excinfo) == "WILDCARD_SECTION"


def test_duplicate_sections_in_a_sequence_are_rejected():
    with pytest.raises(ScopeError) as excinfo:
        RailwayScope(CORRIDOR, [S2, S3, S2])

    assert _reason(excinfo) == "DUPLICATE_SECTION"


def test_set_input_cannot_carry_duplicates_and_is_accepted():
    assert RailwayScope(CORRIDOR, {S2, S2, S3}).section_ids == frozenset({S2, S3})


def test_section_ordering_is_canonical_for_equality_and_hash():
    a = RailwayScope(CORRIDOR, [S3, S2])
    b = RailwayScope(CORRIDOR, [S2, S3])
    c = RailwayScope(CORRIDOR, (S2, S3))

    assert a == b == c
    assert hash(a) == hash(b) == hash(c)


def test_sorted_section_ids_is_deterministic_and_implies_no_ranking():
    scope = RailwayScope(CORRIDOR, [S4, S2, S3])

    assert scope.sorted_section_ids() == tuple(sorted({S2, S3, S4}))
    assert (
        scope.sorted_section_ids()
        == RailwayScope(CORRIDOR, [S3, S4, S2]).sorted_section_ids()
    )


def test_a_bare_string_is_not_treated_as_a_collection_of_characters():
    with pytest.raises(ScopeError) as excinfo:
        RailwayScope(CORRIDOR, S2)  # type: ignore[arg-type]

    assert _reason(excinfo) == "INVALID_SECTION"


@pytest.mark.parametrize("bad", [None, 3, b"x", ("a",), ""])
def test_invalid_corridor_id_is_rejected(bad):
    with pytest.raises(ScopeError) as excinfo:
        RailwayScope(bad, [S2])  # type: ignore[arg-type]

    assert _reason(excinfo) == "INVALID_CORRIDOR"


@pytest.mark.parametrize("bad", ["   ", " CORR", "CORR ", "CO\nRR", "*", "ALL"])
def test_corridor_id_whitespace_control_and_wildcards_are_rejected(bad):
    with pytest.raises(ScopeError) as excinfo:
        RailwayScope(bad, [S2])

    assert _reason(excinfo) == "INVALID_CORRIDOR"


@pytest.mark.parametrize("bad", [None, 3, "", "   ", " NZM-FDB", "NZM-FDB ", "a\tb"])
def test_invalid_section_member_is_rejected(bad):
    with pytest.raises(ScopeError) as excinfo:
        RailwayScope(CORRIDOR, [bad])  # type: ignore[list-item]

    assert _reason(excinfo) == "INVALID_SECTION"


def test_ids_are_kept_exactly_as_given_never_recased_or_stripped():
    scope = RailwayScope(CORRIDOR, ["ndls-nzm"])

    assert scope.section_ids == frozenset({"ndls-nzm"})
    assert scope.section_ids != frozenset({S1})


def test_scope_has_no_track_asset_wildcard_or_global_fields():
    names = {f.name for f in dataclasses.fields(RailwayScope)}

    assert names == {"corridor_id", "section_ids"}


def test_scope_offers_no_expansion_or_hierarchy_api():
    public = {n for n in dir(RailwayScope) if not n.startswith("_")}

    assert not public & {
        "expand",
        "adjacent",
        "neighbours",
        "neighbors",
        "parent",
        "children",
        "whole_corridor",
        "corridor_wide",
        "all_sections",
        "is_global",
    }


# ----------------------------------------------------------------------
# Construction against authoritative topology
# ----------------------------------------------------------------------


def test_factory_builds_a_valid_scope_from_the_registry(registry):
    scope = railway_scope(registry, CORRIDOR, [S3, S2])

    assert scope == RailwayScope(CORRIDOR, [S2, S3])


def test_factory_accepts_every_registered_section_only_when_listed(registry):
    scope = railway_scope(registry, CORRIDOR, registry.section_ids())

    assert scope.section_ids == frozenset(registry.section_ids())


def test_factory_rejects_a_corridor_the_directory_does_not_describe(registry):
    with pytest.raises(ScopeError) as excinfo:
        railway_scope(registry, "CORR-NOPE", [S2])

    assert _reason(excinfo) == "INVALID_CORRIDOR"


def test_factory_rejects_an_unknown_section(registry):
    with pytest.raises(ScopeError) as excinfo:
        railway_scope(registry, CORRIDOR, [S2, "NDLS-XXXX"])

    assert _reason(excinfo) == "UNKNOWN_SECTION"


def test_factory_rejects_a_syntactically_valid_but_unregistered_section(registry):
    with pytest.raises(ScopeError) as excinfo:
        railway_scope(registry, CORRIDOR, ["S3"])

    assert _reason(excinfo) == "UNKNOWN_SECTION"


def test_factory_rejects_a_section_belonging_to_another_corridor(
    registry, other_registry
):
    # The strings are identical, but the sections are registered under
    # CORR-OTHER only. A scope labelled CORR-OTHER cannot be validated
    # against the NDLS directory.
    assert other_registry.has(S2)

    with pytest.raises(ScopeError) as excinfo:
        railway_scope(registry, "CORR-OTHER", [S2])

    assert _reason(excinfo) == "INVALID_CORRIDOR"


def test_factory_rejects_a_section_only_present_in_another_corridor(registry):
    stations = [
        {"station_id": "AAA", "km": 0},
        {"station_id": "BBB", "km": 10},
    ]
    only_other = SectionRegistry.from_stations("CORR-OTHER", stations)

    with pytest.raises(ScopeError) as excinfo:
        railway_scope(registry, CORRIDOR, ["AAA-BBB"])
    assert _reason(excinfo) == "UNKNOWN_SECTION"

    assert (
        railway_scope(only_other, "CORR-OTHER", ["AAA-BBB"]).corridor_id
        == "CORR-OTHER"
    )


def test_factory_does_not_let_a_mislabelled_directory_validate_a_scope(
    other_registry,
):
    # other_registry knows S2 under CORR-OTHER. Labelling the scope with
    # the NDLS corridor must fail on the corridor, not pass because
    # has() happens to be true.
    with pytest.raises(ScopeError) as excinfo:
        railway_scope(other_registry, CORRIDOR, [S2])

    assert _reason(excinfo) == "INVALID_CORRIDOR"


@pytest.mark.parametrize(
    "bad,reason",
    [([], "EMPTY_SCOPE"), (["*"], "WILDCARD_SECTION"), ([S2, S2], "DUPLICATE_SECTION")],
)
def test_factory_propagates_structural_refusals(registry, bad, reason):
    with pytest.raises(ScopeError) as excinfo:
        railway_scope(registry, CORRIDOR, bad)

    assert _reason(excinfo) == reason


def test_a_section_added_to_the_directory_later_is_not_covered(registry):
    class Growing:
        corridor_id = CORRIDOR

        def __init__(self):
            self.known = set(registry.section_ids())

        def has(self, section_id):
            return section_id in self.known

    directory = Growing()
    scope = railway_scope(directory, CORRIDOR, [S2])
    directory.known.add("NEW-SECTION")

    assert "NEW-SECTION" not in scope.section_ids


def test_factory_reads_the_directory_only_through_has(registry):
    calls = []

    class Spy:
        corridor_id = CORRIDOR

        def has(self, section_id):
            calls.append(section_id)
            return registry.has(section_id)

    railway_scope(Spy(), CORRIDOR, [S2, S3])

    assert sorted(calls) == sorted([S2, S3])


def test_a_real_registry_satisfies_the_directory_protocol(registry):
    from backend.app.identity.scope import SectionDirectory

    assert isinstance(registry, SectionDirectory)


# ----------------------------------------------------------------------
# Isolation
# ----------------------------------------------------------------------


def _imports(module) -> set:
    tree = ast.parse(inspect.getsource(module))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _names(module) -> set:
    tree = ast.parse(inspect.getsource(module))
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }


def test_scope_module_never_references_actor_assurance_or_job_action():
    assert not _names(scope_module) & {
        "Actor",
        "ActorRole",
        "human_actor",
        "system_actor",
        "unidentified_actor",
        "IdentityAssurance",
        "JobAction",
    }


def test_scope_module_imports_no_identity_siblings_or_authorization():
    imported = _imports(scope_module)

    for sibling in ("actor", "authorization", "person", "assignments"):
        assert f"backend.app.identity.{sibling}" not in imported


def test_scope_module_imports_no_authentication_persistence_http_or_novaforge():
    imported = _imports(scope_module)
    top = {name.split(".")[0] for name in imported}

    assert not top & {
        "sqlite3",
        "jwt",
        "jose",
        "requests",
        "httpx",
        "fastapi",
        "authlib",
        "sqlalchemy",
    }
    assert not any(n.startswith("backend.app.jobs") for n in imported)
    assert not any(n.startswith("backend.app.api") for n in imported)
    assert not any(n.startswith("backend.app.persistence") for n in imported)
    assert not any("novaforge" in n.lower() for n in imported)


def test_scope_module_holds_no_global_mutable_state():
    mutable = [
        name
        for name, value in vars(scope_module).items()
        if not name.startswith("__") and isinstance(value, (list, dict, set))
    ]

    assert mutable == []


def test_identity_package_public_surface_does_not_export_scope():
    import backend.app.identity as identity

    assert "RailwayScope" not in identity.__all__
    assert not hasattr(identity, "RailwayScope")
