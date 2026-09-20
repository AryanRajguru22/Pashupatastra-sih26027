"""Slice 10.1C: pure scope/resource matching (no authorization).

match_scope answers only "does this resource's (corridor_id, section_id)
belong to this explicit scope?" with a three-valued, fail-closed result:

    MATCH       resolved, same corridor, section in the scope's set
    NO_MATCH    resolved, but a different corridor or a section outside the set
    UNRESOLVED  the resource cannot be reduced to one (corridor, section)

Only MATCH may ever be read as "inside the scope". UNRESOLVED is never a
grant. Nothing here is RBAC, SoD or a permission decision.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from types import SimpleNamespace

import pytest

from backend.app.data.corridor_dataset import load_corridor_dataset
from backend.app.data.models import Asset
from backend.app.data.provenance import ProvenanceLevel, ProvenanceProfile
from backend.app.identity import resource as resource_module
from backend.app.identity.resource import (
    ResourceLocation,
    ScopeMatch,
    location_of,
    match_scope,
)
from backend.app.identity.scope import RailwayScope, ScopeError
from backend.app.jobs.proposal import BlockProposal
from contracts import BlockCandidate, PossessionWindow, ScheduledBlock

CORRIDOR = "CORR-NDLS-AGC"
OTHER = "CORR-OTHER"
S1, S2, S3, S4, S5, S6 = (
    "NDLS-NZM",
    "NZM-FDB",
    "FDB-PWL",
    "PWL-MTJ",
    "MTJ-RKM",
    "RKM-AGC",
)


@pytest.fixture(scope="module")
def registry():
    return load_corridor_dataset().registry


@pytest.fixture()
def scope() -> RailwayScope:
    return RailwayScope(CORRIDOR, [S2, S3, S4])


def _loc(section, corridor=CORRIDOR) -> ResourceLocation:
    return ResourceLocation(corridor_id=corridor, section_id=section)


# ----------------------------------------------------------------------
# Core matching
# ----------------------------------------------------------------------


def test_same_corridor_and_section_in_scope_matches(scope):
    assert match_scope(scope, _loc(S3)) is ScopeMatch.MATCH


def test_every_section_of_a_multi_section_scope_matches(scope):
    for section in (S2, S3, S4):
        assert match_scope(scope, _loc(section)) is ScopeMatch.MATCH


def test_same_corridor_different_section_is_no_match(scope):
    assert match_scope(scope, _loc(S1)) is ScopeMatch.NO_MATCH
    assert match_scope(scope, _loc(S5)) is ScopeMatch.NO_MATCH
    assert match_scope(scope, _loc(S6)) is ScopeMatch.NO_MATCH


def test_different_corridor_same_section_name_is_no_match(scope):
    assert match_scope(scope, _loc(S3, corridor=OTHER)) is ScopeMatch.NO_MATCH


def test_corridor_and_section_are_matched_as_a_pair():
    other = RailwayScope(OTHER, [S3])

    assert match_scope(other, _loc(S3, corridor=OTHER)) is ScopeMatch.MATCH
    assert match_scope(other, _loc(S3, corridor=CORRIDOR)) is ScopeMatch.NO_MATCH


def test_no_implicit_adjacent_section_matching():
    scope = RailwayScope(CORRIDOR, [S3])

    assert match_scope(scope, _loc(S2)) is ScopeMatch.NO_MATCH
    assert match_scope(scope, _loc(S4)) is ScopeMatch.NO_MATCH


def test_no_implicit_corridor_wide_matching():
    scope = RailwayScope(CORRIDOR, [S3])

    for section in (S1, S2, S4, S5, S6, "SOME-UNLISTED-SECTION"):
        assert match_scope(scope, _loc(section)) is not ScopeMatch.MATCH


def test_matching_is_exact_string_equality_no_case_or_whitespace_folding(scope):
    assert match_scope(scope, _loc(S3.lower())) is ScopeMatch.NO_MATCH
    assert match_scope(scope, _loc(S3 + " ")) is ScopeMatch.NO_MATCH
    assert match_scope(scope, _loc(S3, corridor=CORRIDOR.lower())) is ScopeMatch.NO_MATCH


@pytest.mark.parametrize("token", ["*", "ALL", "all", ""])
def test_wildcard_looking_resource_section_never_matches(scope, token):
    assert match_scope(scope, _loc(token)) is not ScopeMatch.MATCH


def test_matching_does_not_depend_on_scope_construction_order():
    a = RailwayScope(CORRIDOR, [S4, S2])
    b = RailwayScope(CORRIDOR, [S2, S4])

    for section in (S1, S2, S3, S4):
        assert match_scope(a, _loc(section)) is match_scope(b, _loc(section))


def test_match_scope_has_no_side_effects_on_the_scope(scope):
    before = (scope.corridor_id, scope.section_ids)
    match_scope(scope, _loc(S1))
    match_scope(scope, _loc(S3))

    assert (scope.corridor_id, scope.section_ids) == before


# ----------------------------------------------------------------------
# Fail closed: unresolved resources
# ----------------------------------------------------------------------


def test_missing_resource_section_is_unresolved(scope):
    assert match_scope(scope, _loc(None)) is ScopeMatch.UNRESOLVED


def test_unknown_resource_corridor_is_unresolved(scope):
    assert match_scope(scope, _loc(S3, corridor=None)) is ScopeMatch.UNRESOLVED


def test_missing_corridor_and_section_is_unresolved(scope):
    assert match_scope(scope, _loc(None, corridor=None)) is ScopeMatch.UNRESOLVED


@pytest.mark.parametrize("bad", [3, b"NZM-FDB", ("NZM-FDB",), 1.5, True])
def test_non_string_identifiers_are_unresolved_not_matched(scope, bad):
    assert match_scope(scope, _loc(bad)) is ScopeMatch.UNRESOLVED
    assert match_scope(scope, _loc(S3, corridor=bad)) is ScopeMatch.UNRESOLVED


def test_unresolved_is_distinct_from_no_match_and_from_match():
    assert len({ScopeMatch.MATCH, ScopeMatch.NO_MATCH, ScopeMatch.UNRESOLVED}) == 3
    assert {m.name for m in ScopeMatch} == {"MATCH", "NO_MATCH", "UNRESOLVED"}


def test_result_is_never_a_bool_so_it_cannot_be_read_as_truthy_authorization(scope):
    for location in (_loc(S3), _loc(S1), _loc(None), _loc(S3, corridor=None)):
        result = match_scope(scope, location)

        assert isinstance(result, ScopeMatch)
        assert not isinstance(result, bool)
        assert result is not True and result is not False


def test_only_match_is_a_grant():
    assert [m for m in ScopeMatch if m is ScopeMatch.MATCH] == [ScopeMatch.MATCH]


def test_no_scope_never_matches_anything():
    # ADMIN carries no scope: the absence of a scope covers nothing.
    assert match_scope(None, _loc(S3)) is ScopeMatch.NO_MATCH
    assert match_scope(None, _loc(None)) is ScopeMatch.NO_MATCH


def test_non_scope_and_non_location_arguments_raise_type_error(scope):
    with pytest.raises(TypeError):
        match_scope("*", _loc(S3))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        match_scope({"corridor_id": CORRIDOR}, _loc(S3))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        match_scope(scope, {"corridor_id": CORRIDOR, "section_id": S3})  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        match_scope(scope, None)  # type: ignore[arg-type]


def test_resource_location_is_a_frozen_pair():
    location = _loc(S3)

    assert dataclasses.is_dataclass(ResourceLocation)
    assert ResourceLocation.__dataclass_params__.frozen
    assert {f.name for f in dataclasses.fields(ResourceLocation)} == {
        "corridor_id",
        "section_id",
    }
    with pytest.raises(dataclasses.FrozenInstanceError):
        location.section_id = S1  # type: ignore[misc]


# ----------------------------------------------------------------------
# Multi-section (spanning) resources: fail closed, never guessed
# ----------------------------------------------------------------------


def test_multi_section_resource_is_unresolved_even_when_every_section_is_in_scope(
    scope,
):
    spanning = SimpleNamespace(section_ids=(S2, S3))

    location = location_of(spanning, corridor_id=CORRIDOR)

    assert location.section_id is None
    assert match_scope(scope, location) is ScopeMatch.UNRESOLVED


def test_multi_section_resource_partially_outside_scope_is_unresolved(scope):
    spanning = SimpleNamespace(section_ids=[S1, S2])

    assert (
        match_scope(scope, location_of(spanning, corridor_id=CORRIDOR))
        is ScopeMatch.UNRESOLVED
    )


def test_multi_section_resource_never_matches_by_first_or_any_member(scope):
    for members in ([S2, S1], [S1, S2], {S2, S3, S4}, (S3, S3, S2)):
        location = location_of(
            SimpleNamespace(section_ids=members), corridor_id=CORRIDOR
        )
        assert match_scope(scope, location) is not ScopeMatch.MATCH


def test_single_member_section_ids_collection_resolves_to_that_section(scope):
    location = location_of(SimpleNamespace(section_ids=(S3,)), corridor_id=CORRIDOR)

    assert location == _loc(S3)
    assert match_scope(scope, location) is ScopeMatch.MATCH


def test_empty_section_ids_collection_is_unresolved(scope):
    location = location_of(SimpleNamespace(section_ids=()), corridor_id=CORRIDOR)

    assert match_scope(scope, location) is ScopeMatch.UNRESOLVED


def test_singular_and_plural_section_fields_that_disagree_are_unresolved(scope):
    both = SimpleNamespace(section_id=S2, section_ids=(S3,))

    assert (
        match_scope(scope, location_of(both, corridor_id=CORRIDOR))
        is ScopeMatch.UNRESOLVED
    )


# ----------------------------------------------------------------------
# location_of over the repository's existing resource shapes
# ----------------------------------------------------------------------


def test_possession_window_uses_its_section_and_the_supplied_corridor(scope):
    window = PossessionWindow("W1", "UP-1", 0, 60, section_id=S3)

    assert location_of(window, corridor_id=CORRIDOR) == _loc(S3)
    assert match_scope(scope, location_of(window, corridor_id=CORRIDOR)) is (
        ScopeMatch.MATCH
    )


def test_possession_window_has_no_corridor_so_none_supplied_is_unresolved(scope):
    window = PossessionWindow("W1", "UP-1", 0, 60, section_id=S3)

    location = location_of(window)

    assert location == ResourceLocation(corridor_id=None, section_id=S3)
    assert match_scope(scope, location) is ScopeMatch.UNRESOLVED


def test_possession_window_without_section_is_unresolved(scope):
    window = PossessionWindow("W1", "UP-1", 0, 60)

    assert (
        match_scope(scope, location_of(window, corridor_id=CORRIDOR))
        is ScopeMatch.UNRESOLVED
    )


def test_scheduled_block_uses_its_section(scope):
    block = ScheduledBlock("B1", "UP-1", 0, 60, "INSPECTION", section_id=S1)

    assert (
        match_scope(scope, location_of(block, corridor_id=CORRIDOR))
        is ScopeMatch.NO_MATCH
    )
    inside = ScheduledBlock("B2", "UP-1", 0, 60, "INSPECTION", section_id=S4)
    assert (
        match_scope(scope, location_of(inside, corridor_id=CORRIDOR))
        is ScopeMatch.MATCH
    )


def test_scheduled_block_without_section_is_unresolved(scope):
    block = ScheduledBlock("B1", "UP-1", 0, 60, "INSPECTION")

    assert (
        match_scope(scope, location_of(block, corridor_id=CORRIDOR))
        is ScopeMatch.UNRESOLVED
    )


def test_block_candidate_uses_its_section(scope):
    candidate = BlockCandidate("B1", "A1", "UP-1", "INSPECTION", 60, section_id=S2)

    assert (
        match_scope(scope, location_of(candidate, corridor_id=CORRIDOR))
        is ScopeMatch.MATCH
    )


def test_block_candidate_without_section_is_unresolved(scope):
    candidate = BlockCandidate("B1", "A1", "UP-1", "INSPECTION", 60)

    assert (
        match_scope(scope, location_of(candidate, corridor_id=CORRIDOR))
        is ScopeMatch.UNRESOLVED
    )


def _proposal(corridor_id: str, section_id) -> BlockProposal:
    level = list(ProvenanceLevel)[0]
    return BlockProposal(
        proposal_id="PR1",
        job_id="J1",
        optimization_run_id="RUN1",
        corridor_id=corridor_id,
        track_id="UP-1",
        section_id=section_id,
        start_minute=0,
        end_minute=60,
        duration_minutes=60,
        work_type="INSPECTION",
        priority_score=0.5,
        risk_score=0.5,
        objective_score=0.5,
        is_committed=False,
        explanation=(),
        provenance=ProvenanceProfile(level, level, level, level),
        generated_at="2026-01-01T00:00:00+00:00",
    )


def test_block_proposal_carries_its_own_corridor_and_section(scope):
    assert match_scope(scope, location_of(_proposal(CORRIDOR, S3))) is ScopeMatch.MATCH
    assert (
        match_scope(scope, location_of(_proposal(OTHER, S3))) is ScopeMatch.NO_MATCH
    )
    assert (
        match_scope(scope, location_of(_proposal(CORRIDOR, None)))
        is ScopeMatch.UNRESOLVED
    )


def test_a_supplied_corridor_that_contradicts_the_resources_own_is_refused():
    with pytest.raises(ScopeError) as excinfo:
        location_of(_proposal(CORRIDOR, S3), corridor_id=OTHER)

    assert excinfo.value.reason == "CORRIDOR_CONFLICT"


def test_a_supplied_corridor_equal_to_the_resources_own_is_accepted(scope):
    location = location_of(_proposal(CORRIDOR, S3), corridor_id=CORRIDOR)

    assert location == _loc(S3)


def test_asset_has_no_section_so_it_is_unresolved(scope):
    asset = Asset("A1", "Rail", "RAIL", "UP-1", 12.5)

    location = location_of(asset, corridor_id=CORRIDOR)

    assert location == ResourceLocation(corridor_id=CORRIDOR, section_id=None)
    assert match_scope(scope, location) is ScopeMatch.UNRESOLVED


def test_asset_track_or_km_never_becomes_a_section(scope):
    asset = Asset("A1", "Rail", "RAIL", "UP-1", 12.5)

    assert location_of(asset, corridor_id=CORRIDOR).section_id is None


def test_job_record_reads_the_block_candidates_section(scope):
    job = {
        "job_id": "J1",
        "track_id": "UP-1",
        "block_candidate": {"block_id": "B1", "section_id": S3},
    }

    assert (
        match_scope(scope, location_of(job, corridor_id=CORRIDOR)) is ScopeMatch.MATCH
    )


def test_legacy_job_record_with_no_section_key_is_unresolved_never_a_match(scope):
    """The live jobs.db holds a job whose block_candidate has no section_id."""

    legacy = {
        "job_id": "J-LEGACY",
        "track_id": "UP-1",
        "block_candidate": {"block_id": "B1", "track_id": "UP-1"},
    }

    location = location_of(legacy, corridor_id=CORRIDOR)

    assert location.section_id is None
    assert match_scope(scope, location) is ScopeMatch.UNRESOLVED


@pytest.mark.parametrize(
    "job",
    [
        {},
        {"block_candidate": None},
        {"block_candidate": {}},
        {"block_candidate": {"section_id": None}},
        {"block_candidate": {"section_id": ""}},
        {"block_candidate": "NZM-FDB"},
        {"block_candidate": {"section_id": 7}},
    ],
)
def test_malformed_or_missing_job_block_candidate_is_unresolved(scope, job):
    assert (
        match_scope(scope, location_of(job, corridor_id=CORRIDOR))
        is ScopeMatch.UNRESOLVED
    )


def test_job_record_uses_the_block_candidate_not_a_top_level_section_id(scope):
    job = {"section_id": S3, "block_candidate": {"section_id": S1}}

    assert (
        match_scope(scope, location_of(job, corridor_id=CORRIDOR))
        is ScopeMatch.NO_MATCH
    )


def test_job_record_top_level_section_is_ignored_when_block_candidate_lacks_one(
    scope,
):
    job = {"section_id": S3, "block_candidate": {}}

    assert (
        match_scope(scope, location_of(job, corridor_id=CORRIDOR))
        is ScopeMatch.UNRESOLVED
    )


def test_plain_mapping_with_a_section_is_read_directly(scope):
    assert (
        match_scope(scope, location_of({"section_id": S2}, corridor_id=CORRIDOR))
        is ScopeMatch.MATCH
    )


def test_object_without_any_location_fields_is_unresolved(scope):
    assert (
        match_scope(scope, location_of(object(), corridor_id=CORRIDOR))
        is ScopeMatch.UNRESOLVED
    )
    assert (
        match_scope(scope, location_of(None, corridor_id=CORRIDOR))
        is ScopeMatch.UNRESOLVED
    )


def test_location_of_does_not_mutate_its_input():
    job = {"block_candidate": {"section_id": S3}}
    snapshot = {"block_candidate": {"section_id": S3}}

    location_of(job, corridor_id=CORRIDOR)

    assert job == snapshot


def test_multi_section_scope_across_the_real_registry_sections(registry, scope):
    results = {
        section_id: match_scope(scope, _loc(section_id))
        for section_id in registry.section_ids()
    }

    assert {k for k, v in results.items() if v is ScopeMatch.MATCH} == {S2, S3, S4}
    assert {k for k, v in results.items() if v is ScopeMatch.NO_MATCH} == {S1, S5, S6}


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


def test_resource_module_never_references_actor_assurance_or_job_action():
    assert not _names(resource_module) & {
        "Actor",
        "ActorRole",
        "human_actor",
        "system_actor",
        "unidentified_actor",
        "IdentityAssurance",
        "JobAction",
        "AuthorizationPolicy",
        "authorize",
    }


def test_resource_module_imports_no_actor_authorization_jobs_api_or_persistence():
    imported = _imports(resource_module)
    top = {name.split(".")[0] for name in imported}

    for sibling in ("actor", "authorization", "person", "assignments"):
        assert f"backend.app.identity.{sibling}" not in imported
    assert not any(n.startswith("backend.app.jobs") for n in imported)
    assert not any(n.startswith("backend.app.api") for n in imported)
    assert not any(n.startswith("backend.app.persistence") for n in imported)
    assert not any(n.startswith("backend.app.data") for n in imported)
    assert not any("novaforge" in n.lower() for n in imported)
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


def test_resource_module_holds_no_global_mutable_state():
    mutable = [
        name
        for name, value in vars(resource_module).items()
        if not name.startswith("__") and isinstance(value, (list, dict, set))
    ]

    assert mutable == []
