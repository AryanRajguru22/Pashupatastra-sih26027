"""Sprint 3 Step 9: canonical provenance unification.

Covers backend.app.data.provenance (the canonical four-axis model and
its weakest-link effective-provenance rule) and its two integration
points: the legacy vocabulary it migrates
(backend.app.data.canonical_train.TrainDataProvenance,
backend.app.jobs.service.POSSESSION_SOURCE_GENERATED_STATIC) and the
jobs-pipeline optimization outcome it now appears in
(backend.app.jobs.optimization.JobOptimizationService.optimize_corridor).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.data.canonical_train import TrainDataProvenance
from backend.app.data.provenance import (
    FRONTEND_DATA_PROVENANCE_MAP,
    ProvenanceLevel,
    ProvenanceProfile,
    _REALISM_TIER,
    from_possession_source,
    from_train_data_provenance,
    to_possession_source,
)
from backend.app.jobs.models import JobCreateRequest, JobOptimizationResponse
from backend.app.jobs.optimization import (
    JobOptimizationService,
    _build_provenance_profile,
)
from backend.app.jobs.repository import JobRepository
from backend.app.jobs.service import (
    POSSESSION_DERIVATION_CANONICAL_TIMETABLE,
    POSSESSION_DERIVATION_GENERATED_SLOTS,
    POSSESSION_SOURCE_GENERATED_STATIC,
    JobService,
    PossessionInputs,
)


# ----------------------------------------------------------------------
# Construction / fail-closed on missing or invalid provenance
# ----------------------------------------------------------------------


def test_construction_requires_all_four_axes():
    """No defaults: an axis a caller doesn't know cannot be omitted."""

    with pytest.raises(TypeError):
        ProvenanceProfile(  # type: ignore[call-arg]
            topology=ProvenanceLevel.SYNTHETIC,
            timetable=ProvenanceLevel.SYNTHETIC,
            asset_condition=ProvenanceLevel.SYNTHETIC,
            # possession omitted
        )


def test_invalid_axis_value_is_rejected():
    with pytest.raises(ValueError, match="topology"):
        ProvenanceProfile(
            topology="REAL_LIVE_MADE_UP",
            timetable=ProvenanceLevel.SYNTHETIC,
            asset_condition=ProvenanceLevel.SYNTHETIC,
            possession=ProvenanceLevel.SYNTHETIC,
        )


def test_string_values_are_coerced_to_the_enum():
    """Axes may be given as the plain string, matching to_dict()'s shape."""

    profile = ProvenanceProfile(
        topology="REAL_STATIC",
        timetable="SYNTHETIC",
        asset_condition="SYNTHETIC",
        possession="SYNTHETIC",
    )

    assert profile.topology is ProvenanceLevel.REAL_STATIC


# ----------------------------------------------------------------------
# Four axes preserved independently
# ----------------------------------------------------------------------


def test_four_axes_are_preserved_independently():
    profile = ProvenanceProfile(
        topology=ProvenanceLevel.REAL_STATIC,
        timetable=ProvenanceLevel.REAL_SCHEDULED,
        asset_condition=ProvenanceLevel.SYNTHETIC,
        possession=ProvenanceLevel.REAL_STATIC,
    )

    assert profile.topology is ProvenanceLevel.REAL_STATIC
    assert profile.timetable is ProvenanceLevel.REAL_SCHEDULED
    assert profile.asset_condition is ProvenanceLevel.SYNTHETIC
    assert profile.possession is ProvenanceLevel.REAL_STATIC


# ----------------------------------------------------------------------
# Weakest-link effective provenance - the worked examples from the
# Sprint 3 Step 9 task spec, encoded directly.
# ----------------------------------------------------------------------


def test_all_synthetic_effective_is_synthetic():
    profile = ProvenanceProfile(
        topology=ProvenanceLevel.SYNTHETIC,
        timetable=ProvenanceLevel.SYNTHETIC,
        asset_condition=ProvenanceLevel.SYNTHETIC,
        possession=ProvenanceLevel.SYNTHETIC,
    )

    assert profile.effective is ProvenanceLevel.SYNTHETIC


def test_mixed_real_and_one_synthetic_axis_effective_is_synthetic():
    """REAL_STATIC + REAL_SCHEDULED + SYNTHETIC + REAL_STATIC => SYNTHETIC.

    The task's own worked example, in axis order
    (topology, timetable, asset_condition, possession).
    """

    profile = ProvenanceProfile(
        topology=ProvenanceLevel.REAL_STATIC,
        timetable=ProvenanceLevel.REAL_SCHEDULED,
        asset_condition=ProvenanceLevel.SYNTHETIC,
        possession=ProvenanceLevel.REAL_STATIC,
    )

    assert profile.effective is ProvenanceLevel.SYNTHETIC


@pytest.mark.parametrize(
    "synthetic_axis",
    ["topology", "timetable", "asset_condition", "possession"],
)
def test_any_single_synthetic_axis_collapses_effective(synthetic_axis: str):
    """Weakest link: ANY one synthetic axis is enough, regardless of which."""

    axes = {
        "topology": ProvenanceLevel.REAL_STATIC,
        "timetable": ProvenanceLevel.REAL_SCHEDULED,
        "asset_condition": ProvenanceLevel.REAL_STATIC,
        "possession": ProvenanceLevel.REAL_SCHEDULED,
    }
    axes[synthetic_axis] = ProvenanceLevel.SYNTHETIC

    profile = ProvenanceProfile(**axes)

    assert profile.effective is ProvenanceLevel.SYNTHETIC


def test_all_real_static_effective_is_real_static():
    profile = ProvenanceProfile(
        topology=ProvenanceLevel.REAL_STATIC,
        timetable=ProvenanceLevel.REAL_STATIC,
        asset_condition=ProvenanceLevel.REAL_STATIC,
        possession=ProvenanceLevel.REAL_STATIC,
    )

    assert profile.effective is ProvenanceLevel.REAL_STATIC


def test_all_real_scheduled_effective_is_real_scheduled():
    """Pins the tier-representative distinction: an all-REAL_SCHEDULED
    profile must NOT be silently promoted to REAL_STATIC. If effective
    provenance were computed as max(enum) rather than a documented tier
    convention, this is exactly the case that would fail to distinguish
    the two - see req 7, "ordering is semantic, not lexical"."""

    profile = ProvenanceProfile(
        topology=ProvenanceLevel.REAL_SCHEDULED,
        timetable=ProvenanceLevel.REAL_SCHEDULED,
        asset_condition=ProvenanceLevel.REAL_SCHEDULED,
        possession=ProvenanceLevel.REAL_SCHEDULED,
    )

    assert profile.effective is ProvenanceLevel.REAL_SCHEDULED


def test_mixed_real_subtypes_prefer_real_static_as_representative():
    """REAL_STATIC + REAL_SCHEDULED + REAL_STATIC + REAL_STATIC => REAL_STATIC.

    The task's own worked example for the all-real case. REAL_STATIC and
    REAL_SCHEDULED are not ranked against each other by realism (both are
    tier 1); REAL_STATIC is a documented labelling convention for that
    tier, not a second weakest-link comparison - see
    backend/app/data/provenance.py's "EFFECTIVE PROVENANCE" docstring
    section.
    """

    profile = ProvenanceProfile(
        topology=ProvenanceLevel.REAL_STATIC,
        timetable=ProvenanceLevel.REAL_SCHEDULED,
        asset_condition=ProvenanceLevel.REAL_STATIC,
        possession=ProvenanceLevel.REAL_STATIC,
    )

    assert profile.effective is ProvenanceLevel.REAL_STATIC


def test_ordering_is_semantic_not_lexical():
    """Position/order independence: swapping WHICH axis holds SYNTHETIC
    must not change the result - a positional rule would be
    order-sensitive, the tier rule is not.

    NOTE on lexical order specifically: because ProvenanceLevel is a str
    Enum, plain value-lexicographic max(axes) happens to reproduce every
    outcome asserted in this file ('SYNTHETIC' > 'REAL_STATIC' >
    'REAL_SCHEDULED' by string comparison, coincidentally matching the
    tier rule across today's three-value vocabulary). That coincidence
    is exactly why "the outputs look right" is not sufficient evidence
    the rule is semantic rather than lexical - see
    test_real_values_share_one_realism_tier below, which pins the
    MECHANISM (both real values occupy the same tier) rather than
    outcomes a lexical accident could also produce.
    """

    profile_a = ProvenanceProfile(
        topology=ProvenanceLevel.SYNTHETIC,
        timetable=ProvenanceLevel.REAL_STATIC,
        asset_condition=ProvenanceLevel.REAL_STATIC,
        possession=ProvenanceLevel.REAL_STATIC,
    )
    profile_b = ProvenanceProfile(
        topology=ProvenanceLevel.REAL_STATIC,
        timetable=ProvenanceLevel.REAL_STATIC,
        asset_condition=ProvenanceLevel.REAL_STATIC,
        possession=ProvenanceLevel.SYNTHETIC,
    )

    assert profile_a.effective is ProvenanceLevel.SYNTHETIC
    assert profile_b.effective is ProvenanceLevel.SYNTHETIC


def test_real_values_share_one_realism_tier():
    """The actual mechanism, not an output a lexical max() could fake:
    REAL_STATIC and REAL_SCHEDULED occupy the SAME realism tier (neither
    ranked above the other), and SYNTHETIC occupies a strictly lower
    tier than both. This is what test_ordering_is_semantic_not_lexical's
    outcomes are actually built on - if _REALISM_TIER were ever replaced
    by str.__lt__ / max(enum), this test (not that one) is what would
    catch it, because a lexical scheme has no notion of "same tier" at
    all.
    """

    assert (
        _REALISM_TIER[ProvenanceLevel.REAL_STATIC]
        == _REALISM_TIER[ProvenanceLevel.REAL_SCHEDULED]
    )
    assert (
        _REALISM_TIER[ProvenanceLevel.SYNTHETIC]
        < _REALISM_TIER[ProvenanceLevel.REAL_STATIC]
    )
    assert (
        _REALISM_TIER[ProvenanceLevel.SYNTHETIC]
        < _REALISM_TIER[ProvenanceLevel.REAL_SCHEDULED]
    )


# ----------------------------------------------------------------------
# Single source of truth: effective cannot be set, stored, or trusted
# from a tampered dict.
# ----------------------------------------------------------------------


def test_effective_is_not_a_constructor_argument():
    with pytest.raises(TypeError):
        ProvenanceProfile(
            topology=ProvenanceLevel.SYNTHETIC,
            timetable=ProvenanceLevel.SYNTHETIC,
            asset_condition=ProvenanceLevel.SYNTHETIC,
            possession=ProvenanceLevel.SYNTHETIC,
            effective=ProvenanceLevel.REAL_STATIC,  # type: ignore[call-arg]
        )


def test_effective_has_no_setter():
    profile = ProvenanceProfile(
        topology=ProvenanceLevel.SYNTHETIC,
        timetable=ProvenanceLevel.SYNTHETIC,
        asset_condition=ProvenanceLevel.SYNTHETIC,
        possession=ProvenanceLevel.SYNTHETIC,
    )

    with pytest.raises(AttributeError):
        profile.effective = ProvenanceLevel.REAL_STATIC  # type: ignore[misc]


def test_to_dict_round_trips_through_from_dict():
    profile = ProvenanceProfile(
        topology=ProvenanceLevel.REAL_STATIC,
        timetable=ProvenanceLevel.REAL_SCHEDULED,
        asset_condition=ProvenanceLevel.SYNTHETIC,
        possession=ProvenanceLevel.REAL_STATIC,
    )

    restored = ProvenanceProfile.from_dict(profile.to_dict())

    assert restored == profile
    assert restored.effective == profile.effective


def test_from_dict_ignores_a_tampered_effective_key():
    """A caller cannot smuggle axes=all-SYNTHETIC with effective=REAL_STATIC
    through storage and have it come back authoritative - this is the
    "req 18 / req 12" guarantee on the READ side, not just construction."""

    tampered = {
        "topology": "SYNTHETIC",
        "timetable": "SYNTHETIC",
        "asset_condition": "SYNTHETIC",
        "possession": "SYNTHETIC",
        "effective": "REAL_STATIC",  # smuggled, must be ignored
    }

    restored = ProvenanceProfile.from_dict(tampered)

    assert restored.effective is ProvenanceLevel.SYNTHETIC


# ----------------------------------------------------------------------
# No LIVE value anywhere in the canonical vocabulary.
# ----------------------------------------------------------------------


def test_no_live_value_exists():
    values = {member.value for member in ProvenanceLevel}

    assert "LIVE" not in values
    assert not any("LIVE" in value for value in values)


# ----------------------------------------------------------------------
# Legacy vocabulary migration.
# ----------------------------------------------------------------------


def test_from_train_data_provenance_maps_the_real_production_enum():
    """Exercises the actual TrainDataProvenance enum, not hardcoded
    strings, so this breaks if Step 5's enum ever changes shape."""

    assert (
        from_train_data_provenance(TrainDataProvenance.REAL_SCHEDULED.value)
        is ProvenanceLevel.REAL_SCHEDULED
    )
    assert (
        from_train_data_provenance(
            TrainDataProvenance.SYNTHETIC_SCHEDULED.value
        )
        is ProvenanceLevel.SYNTHETIC
    )


def test_from_train_data_provenance_rejects_unknown_values():
    with pytest.raises(ValueError):
        from_train_data_provenance("LIVE")


def test_from_possession_source_maps_the_real_production_constant():
    assert (
        from_possession_source(POSSESSION_SOURCE_GENERATED_STATIC)
        is ProvenanceLevel.SYNTHETIC
    )


def test_from_possession_source_rejects_unknown_values():
    with pytest.raises(ValueError):
        from_possession_source("LIVE_POSSESSION_FEED")


def test_to_possession_source_round_trips_synthetic():
    assert (
        to_possession_source(ProvenanceLevel.SYNTHETIC)
        == POSSESSION_SOURCE_GENERATED_STATIC
        == "GENERATED_STATIC"
    )


def test_to_possession_source_has_no_label_for_real_yet():
    """Documents a real gap rather than inventing a label: no possession
    source in this repository has ever been REAL_*."""

    with pytest.raises(ValueError):
        to_possession_source(ProvenanceLevel.REAL_STATIC)


def test_frontend_vocabulary_map_matches_documented_correspondence():
    assert FRONTEND_DATA_PROVENANCE_MAP["SYNTHETIC_FIXTURE"] == {
        "topology": ProvenanceLevel.SYNTHETIC,
        "timetable": ProvenanceLevel.SYNTHETIC,
        "asset_condition": ProvenanceLevel.SYNTHETIC,
        "possession": ProvenanceLevel.SYNTHETIC,
    }
    assert (
        FRONTEND_DATA_PROVENANCE_MAP["REALISTIC_STATIC"]
        is ProvenanceLevel.REAL_STATIC
    )
    assert "LIVE_FEED" not in FRONTEND_DATA_PROVENANCE_MAP


# ----------------------------------------------------------------------
# Jobs-pipeline integration: provenance actually appears in the
# optimization outcome and API response, and no LIVE value leaks in.
# ----------------------------------------------------------------------


def _report_one_job(service: JobService) -> None:
    service.create_job(
        JobCreateRequest(
            track_id="UP-1",
            job_type="BALLAST_TAMPING",
            distance_start=1000.0,
            distance_end=1400.0,
            workers_min=2,
            workers_max=4,
            description="Step 9 provenance test job",
        )
    )


ALL_SYNTHETIC = {
    "topology": "SYNTHETIC",
    "timetable": "SYNTHETIC",
    "asset_condition": "SYNTHETIC",
    "possession": "SYNTHETIC",
    "effective": "SYNTHETIC",
}


def test_build_provenance_profile_is_all_synthetic_on_both_paths():
    """Honest statement of current production reality: no axis in the
    jobs pipeline is backed by real data, whichever possession source
    ran.

    Sprint 3 Step 10 made this profile a function of the inputs actually
    used (see _build_provenance_profile), so the Step 9 claim is now
    checked against BOTH possession paths rather than a single hardcoded
    profile: passing a synthetic timetable through the canonical adapter
    must not promote any axis, and the generator path must stay exactly
    as it was.
    """

    canonical = _build_provenance_profile(
        PossessionInputs(
            windows=[],
            derivation=POSSESSION_DERIVATION_CANONICAL_TIMETABLE,
            timetable_provenance=ProvenanceLevel.SYNTHETIC,
            possession_provenance=ProvenanceLevel.SYNTHETIC,
        )
    )

    generated = _build_provenance_profile(
        PossessionInputs(
            windows=[],
            derivation=POSSESSION_DERIVATION_GENERATED_SLOTS,
            timetable_provenance=ProvenanceLevel.SYNTHETIC,
            possession_provenance=ProvenanceLevel.SYNTHETIC,
        )
    )

    assert canonical.to_dict() == ALL_SYNTHETIC
    assert generated.to_dict() == ALL_SYNTHETIC


def test_optimize_corridor_outcome_includes_canonical_provenance(
    tmp_path: Path,
):
    service = JobService(repository=JobRepository(tmp_path / "jobs.db"))
    optimizer = JobOptimizationService(service)

    _report_one_job(service)

    outcome = optimizer.optimize_corridor("CORRIDOR_A")

    assert outcome["possession_source"] == "GENERATED_STATIC"
    assert outcome["provenance"] == {
        "topology": "SYNTHETIC",
        "timetable": "SYNTHETIC",
        "asset_condition": "SYNTHETIC",
        "possession": "SYNTHETIC",
        "effective": "SYNTHETIC",
    }

    # The Pydantic response model accepts the plain dict and validates
    # it into the nested ProvenanceResponse - this is what confirms
    # `effective` actually reaches the HTTP response body rather than
    # silently vanishing as an un-serialized dataclass property.
    response = JobOptimizationResponse(**outcome)

    assert response.provenance.effective == "SYNTHETIC"
    assert response.provenance.possession == "SYNTHETIC"
    assert response.possession_source == "GENERATED_STATIC"


def test_no_live_value_in_jobs_pipeline_provenance(tmp_path: Path):
    service = JobService(repository=JobRepository(tmp_path / "jobs.db"))
    optimizer = JobOptimizationService(service)

    _report_one_job(service)

    outcome = optimizer.optimize_corridor("CORRIDOR_A")

    values = list(outcome["provenance"].values()) + [
        outcome["possession_source"]
    ]

    assert not any("LIVE" in value for value in values)
