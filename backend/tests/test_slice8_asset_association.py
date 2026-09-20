"""Sprint 3 Slice 8, items 1 and 2: bounded asset association and asset
reference integrity.

ITEM 1 - a report must not silently attach to an implausibly distant
asset. The bound is an explicit policy value; outside it the report is
refused (400 ASSET_ASSOCIATION_FAILED), never attached to a farther asset.

ITEM 2 - asset_id is a NAME into a regenerated, non-persisted asset set.
A persisted id must be validated against the ACTIVE set before it is
trusted; a stale or re-pointed id fails closed and is never resolved to
some other asset.
"""

from __future__ import annotations

import itertools
import math
import random
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from backend.app.api.main import app
from backend.app.data.corridor_dataset import load_corridor_dataset
from backend.app.data.feature_adapter import ScoringFeatureAdapter
from backend.app.jobs.asset_association import (
    ASSET_REFERENCE_UNFINGERPRINTED,
    ASSET_REFERENCE_VERIFIED,
    DEFAULT_ASSET_ASSOCIATION_MAX_KM,
    AssetAssociationError,
    AssetAssociationPolicy,
    AssetReferenceError,
    asset_fingerprint,
    select_asset,
    verify_asset_reference,
)
from backend.app.jobs.repository import JobRepository
from backend.app.jobs.router import service as router_service
from backend.app.jobs.service import JobService
from backend.tests.slice8_helpers import (
    CORRIDOR_ID,
    WORKER,
    created_event,
    job_count,
    make_asset,
    make_corridor,
    make_registry,
    make_request,
    make_service,
)


def select(assets, start_m, end_m, policy=None, track="T1", section="A-B"):
    return select_asset(
        assets,
        corridor_id=CORRIDOR_ID,
        registry=make_registry(("T1", "T2")),
        track_id=track,
        job_section_id=section,
        distance_start_m=start_m,
        distance_end_m=end_m,
        policy=policy or AssetAssociationPolicy(),
    )


def history_row_count(service) -> int:
    with closing(sqlite3.connect(service.repository.db_path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM job_events").fetchone()[0]


# ----------------------------------------------------------------------
# 1. The bound is explicit and documented
# ----------------------------------------------------------------------


def test_the_default_bound_is_a_named_documented_policy_value():
    assert DEFAULT_ASSET_ASSOCIATION_MAX_KM == 20.0
    assert AssetAssociationPolicy().max_distance_km == DEFAULT_ASSET_ASSOCIATION_MAX_KM
    assert AssetAssociationPolicy().require_same_section is False


@pytest.mark.parametrize(
    "bad", [-1, -0.001, math.nan, math.inf, -math.inf, True, "20", None]
)
def test_policy_rejects_an_invalid_bound(bad):
    with pytest.raises(ValueError, match="max_distance_km"):
        AssetAssociationPolicy(max_distance_km=bad)


def test_policy_rejects_a_non_bool_section_rule():
    with pytest.raises(ValueError, match="require_same_section"):
        AssetAssociationPolicy(require_same_section="yes")


def test_service_takes_its_bound_from_the_injected_policy(tmp_path):
    service = make_service(
        tmp_path,
        [make_asset("AST-T1-OHE-001", 5.0)],
        asset_policy=AssetAssociationPolicy(max_distance_km=1.0),
    )

    # 5.0 km asset vs a 1.0-1.4 km job: 3.6 km away, beyond a 1 km bound.
    with pytest.raises(AssetAssociationError):
        service.create_job(make_request(1_000, 1_400), actor=WORKER)

    assert job_count(service) == 0


# ----------------------------------------------------------------------
# 2. Valid nearby asset / boundary / outside / none
# ----------------------------------------------------------------------


def test_a_valid_nearby_asset_is_associated(tmp_path):
    service = make_service(tmp_path, [make_asset("AST-T1-OHE-001", 1.2)])

    job = service.create_job(make_request(1_000, 1_400), actor=WORKER)

    assert job["block_candidate"]["asset_id"] == "AST-T1-OHE-001"

    record = job["block_candidate"]["metadata"]["asset_association"]
    assert record["resolution"] == "DERIVED"
    assert record["distance_km"] == 0.0  # the asset lies inside the span
    assert record["section_match"] is True


@pytest.mark.parametrize(
    "asset_km, job_start_m, job_end_m",
    [
        (21.4, 1_000.0, 1_400.0),  # 20.0 km past the span's end
        (10.0, 30_000.0, 30_400.0),  # 20.0 km before the span's start
    ],
)
def test_an_asset_exactly_at_the_bound_is_accepted(
    asset_km, job_start_m, job_end_m
):
    chosen = select(
        [make_asset("AST-T1-OHE-001", asset_km)], job_start_m, job_end_m
    )

    assert chosen.asset.asset_id == "AST-T1-OHE-001"
    assert chosen.distance_km == 20.0


@pytest.mark.parametrize(
    "asset_km, job_start_m, job_end_m",
    [
        (21.41, 1_000.0, 1_400.0),  # 20.01 km past the end
        (9.99, 30_000.0, 30_400.0),  # 20.01 km before the start
    ],
)
def test_an_asset_just_beyond_the_bound_is_refused(
    asset_km, job_start_m, job_end_m
):
    with pytest.raises(AssetAssociationError, match="within 20.0 km"):
        select([make_asset("AST-T1-OHE-001", asset_km)], job_start_m, job_end_m)


def test_a_far_asset_refuses_the_report_and_persists_nothing(tmp_path):
    """The F1 regression: nothing is created, scored or recorded, and the
    nearest (far) asset is NOT used as a fallback."""

    service = make_service(tmp_path, [make_asset("AST-T1-OHE-001", 40.0)])

    with pytest.raises(AssetAssociationError, match="refused rather than attached"):
        service.create_job(make_request(1_000, 1_400), actor=WORKER)

    assert job_count(service) == 0
    assert history_row_count(service) == 0


def test_no_asset_on_the_track_is_refused(tmp_path):
    service = make_service(tmp_path, [], track_ids=("T1",))

    with pytest.raises(AssetAssociationError, match="No asset found for track_id 'T1'"):
        service.create_job(make_request(), actor=WORKER)

    assert job_count(service) == 0


def test_an_asset_on_another_track_is_never_a_candidate(tmp_path):
    """T2's asset sits exactly on the job; T1's only asset is 40 km away.
    The association must not hop tracks to the close one."""

    service = make_service(
        tmp_path,
        [
            make_asset("AST-T2-OHE-001", 1.2, track_id="T2"),
            make_asset("AST-T1-OHE-002", 41.0, track_id="T1"),
        ],
        track_ids=("T1", "T2"),
    )

    with pytest.raises(AssetAssociationError):
        service.create_job(make_request(1_000, 1_400, track_id="T1"), actor=WORKER)

    on_t2 = service.create_job(make_request(1_000, 1_400, track_id="T2"), actor=WORKER)
    assert on_t2["block_candidate"]["asset_id"] == "AST-T2-OHE-001"


# ----------------------------------------------------------------------
# 3. Multiple nearby assets and deterministic selection
# ----------------------------------------------------------------------


def test_the_asset_nearest_the_span_midpoint_wins():
    assets = [
        make_asset("AST-T1-OHE-001", 1.0),
        make_asset("AST-T1-OHE-002", 1.9),
        make_asset("AST-T1-OHE-003", 9.0),
    ]

    # midpoint 1.2 km: 0.2 / 0.7 / 7.8 km away.
    assert select(assets, 1_000, 1_400).asset.asset_id == "AST-T1-OHE-001"


def test_selection_matches_the_pre_slice8_nearest_by_midpoint_rule():
    """Existing behaviour preserved: whenever a valid asset exists the
    winner is exactly the old min(|asset.km - midpoint|)."""

    rng = random.Random(8)
    assets = [
        make_asset(f"AST-T1-OHE-{i:03d}", round(rng.uniform(0.5, 49.0), 2))
        for i in range(1, 9)
    ]

    for _ in range(40):
        start_m = round(rng.uniform(0, 45_000), 1)
        end_m = round(start_m + rng.uniform(50, 800), 1)
        midpoint_km = (start_m + end_m) / 2000.0

        legacy = min(assets, key=lambda a: abs(a.km_location - midpoint_km))
        in_bound = (
            max(0.0, start_m / 1000 - legacy.km_location, legacy.km_location - end_m / 1000)
            <= DEFAULT_ASSET_ASSOCIATION_MAX_KM
        )
        assert in_bound  # sanity: this fixture is dense enough

        assert select(assets, start_m, end_m).asset.asset_id == legacy.asset_id


def test_a_tie_breaks_on_the_lower_km_then_the_lower_asset_id():
    # 0.8 and 1.6 are both 0.4 km from the 1.2 km midpoint.
    tied_km = [make_asset("AST-T1-OHE-002", 1.6), make_asset("AST-T1-OHE-001", 0.8)]
    assert select(tied_km, 1_000, 1_400).asset.asset_id == "AST-T1-OHE-001"

    same_km = [make_asset("AST-T1-SIG-009", 1.2), make_asset("AST-T1-OHE-004", 1.2)]
    assert select(same_km, 1_000, 1_400).asset.asset_id == "AST-T1-OHE-004"


def test_selection_is_independent_of_input_order():
    assets = [
        make_asset("AST-T1-OHE-001", 0.8),
        make_asset("AST-T1-OHE-002", 1.6),
        make_asset("AST-T1-OHE-003", 1.9),
        make_asset("AST-T1-SIG-004", 30.0),
    ]

    winners = {
        select(list(order), 1_000, 1_400).asset.asset_id
        for order in itertools.permutations(assets)
    }

    assert winners == {"AST-T1-OHE-001"}


def test_repeated_selection_is_stable():
    assets = [make_asset("AST-T1-OHE-001", 3.3), make_asset("AST-T1-OHE-002", 5.1)]

    first = select(assets, 4_000, 4_400)

    for _ in range(5):
        assert select(assets, 4_000, 4_400).asset.asset_id == first.asset.asset_id


# ----------------------------------------------------------------------
# 4. Invalid / missing asset data
# ----------------------------------------------------------------------


def test_assets_with_unusable_data_are_never_candidates():
    good = make_asset("AST-T1-OHE-001", 3.0)
    bad = [
        make_asset("AST-T1-OHE-002", math.nan),
        make_asset("AST-T1-OHE-003", math.inf),
        make_asset("", 1.2),  # no id
    ]

    # The invalid ones would otherwise be nearest (1.2 km is in the span).
    assert select(bad + [good], 1_000, 1_400).asset.asset_id == good.asset_id


def test_only_unusable_assets_refuses_and_says_so():
    bad = [make_asset("AST-T1-OHE-002", math.nan), make_asset("", 1.2)]

    with pytest.raises(AssetAssociationError, match="No usable asset"):
        select(bad, 1_000, 1_400)


# ----------------------------------------------------------------------
# 5. Sections: never silent
# ----------------------------------------------------------------------


def _cross_section_assets():
    return [
        make_asset("AST-T1-OHE-001", 49.8),  # section A-B, 0.7 km before the job
        make_asset("AST-T1-OHE-002", 60.0),  # section B-C, 9.1 km after the job
    ]


def test_a_cross_section_association_is_allowed_but_recorded(tmp_path):
    """The geographically nearest asset sits just across the station. The
    default keeps it (the alternative is an asset 9 km away) but the
    crossing is written into the job and into JOB_CREATED - not silent."""

    service = make_service(tmp_path, _cross_section_assets())

    job = service.create_job(make_request(50_500, 50_900), actor=WORKER)

    assert job["block_candidate"]["section_id"] == "B-C"
    assert job["block_candidate"]["asset_id"] == "AST-T1-OHE-001"

    record = job["block_candidate"]["metadata"]["asset_association"]
    assert record["section_match"] is False
    assert record["asset_section_id"] == "A-B"
    assert record["job_section_id"] == "B-C"

    event = created_event(service, job["job_id"])
    assert event.metadata["asset_association"] == record


def test_require_same_section_refuses_a_cross_section_association(tmp_path):
    service = make_service(
        tmp_path,
        [make_asset("AST-T1-OHE-001", 49.8)],
        asset_policy=AssetAssociationPolicy(require_same_section=True),
    )

    with pytest.raises(AssetAssociationError, match="require_same_section"):
        service.create_job(make_request(50_500, 50_900), actor=WORKER)

    assert job_count(service) == 0


def test_require_same_section_uses_the_in_section_asset_when_one_is_in_bound(
    tmp_path,
):
    service = make_service(
        tmp_path,
        _cross_section_assets(),
        asset_policy=AssetAssociationPolicy(require_same_section=True),
    )

    job = service.create_job(make_request(50_500, 50_900), actor=WORKER)

    assert job["block_candidate"]["asset_id"] == "AST-T1-OHE-002"
    assert (
        job["block_candidate"]["metadata"]["asset_association"]["section_match"]
        is True
    )


# ----------------------------------------------------------------------
# 6. Scoring behaviour is preserved when a valid asset exists
# ----------------------------------------------------------------------


def test_scoring_is_unchanged_on_the_default_corridor(tmp_path):
    """On the generated CORRIDOR_A every location resolves to the same
    asset the old nearest-by-midpoint rule chose, and the job is scored
    exactly as that asset would have been."""

    from contracts import BlockCandidate

    service = JobService(repository=JobRepository(tmp_path / "jobs.db"))
    up_assets = [a for a in service.corridor.assets if a.track_id == "UP-1"]

    for start_m in (500.0, 4_000.0, 12_000.0, 21_000.0, 30_000.0, 34_000.0):
        midpoint_km = (start_m + start_m + 400) / 2000.0
        legacy = min(up_assets, key=lambda a: abs(a.km_location - midpoint_km))

        job = service.create_job(
            make_request(start_m, start_m + 400, track_id="UP-1"),
            actor=WORKER,
        )

        assert job["block_candidate"]["asset_id"] == legacy.asset_id

        rescored = ScoringFeatureAdapter.score_block_candidate(
            BlockCandidate.from_dict(job["block_candidate"]),
            service.corridor,
        )
        assert job["priority_score"] == rescored.priority_score
        assert job["risk_score"] == rescored.risk_score


def test_asset_association_metadata_is_recorded_with_provenance(tmp_path):
    service = make_service(tmp_path)

    job = service.create_job(make_request(), actor=WORKER)
    record = job["block_candidate"]["metadata"]["asset_association"]

    assert record["policy"] == {
        "max_distance_km": DEFAULT_ASSET_ASSOCIATION_MAX_KM,
        "require_same_section": False,
    }
    assert record["reference"]["corridor_id"] == CORRIDOR_ID
    assert record["reference"]["provenance"] == "SYNTHETIC"
    assert len(record["reference"]["fingerprint"]) == 64

    assert created_event(service, job["job_id"]).metadata["asset_association"] == record


def test_a_far_asset_is_a_400_with_a_stable_code(tmp_path, monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr(
        router_service,
        "asset_policy",
        AssetAssociationPolicy(max_distance_km=0.1),
    )

    # CORRIDOR_A's nearest UP-1 asset to km 2.0-2.4 is at 1.04 km.
    response = client.post(
        "/v1/jobs",
        json={
            "track_id": "UP-1",
            "job_type": "BALLAST_TAMPING",
            "distance_start": 2000.0,
            "distance_end": 2400.0,
            "workers_min": 2,
            "workers_max": 4,
            "description": "too far from any asset",
        },
        headers={"X-Actor-Id": "WORKER-042", "X-Actor-Role": "WORKER"},
    )

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "ASSET_ASSOCIATION_FAILED"


# ----------------------------------------------------------------------
# ITEM 2. Asset reference integrity
# ----------------------------------------------------------------------


def _created(tmp_path, assets=None):
    service = make_service(tmp_path, assets)
    job = service.create_job(make_request(), actor=WORKER)
    return service, job


def test_a_freshly_created_jobs_asset_reference_verifies(tmp_path):
    service, job = _created(tmp_path)

    check = service.verify_job_asset_reference(job)

    assert check.status == ASSET_REFERENCE_VERIFIED
    assert check.asset.asset_id == job["block_candidate"]["asset_id"]
    assert service.verify_job_asset_reference(job["job_id"]).status == ASSET_REFERENCE_VERIFIED


def test_an_unknown_job_id_is_a_key_error(tmp_path):
    service, _ = _created(tmp_path)

    with pytest.raises(KeyError):
        service.verify_job_asset_reference("JOB-DOES-NOT-EXIST")


def test_a_stale_asset_id_fails_closed_and_is_not_resolved_elsewhere(tmp_path):
    """The id does not exist in the active set. The other asset that IS
    there (and is right next to the job) must not be substituted."""

    service, job = _created(
        tmp_path,
        [make_asset("AST-T1-OHE-001", 1.2), make_asset("AST-T1-OHE-002", 1.3)],
    )

    stale = dict(job)
    stale["block_candidate"] = dict(job["block_candidate"], asset_id="AST-T1-OHE-999")

    with pytest.raises(AssetReferenceError, match="does not exist in the active asset set"):
        service.verify_job_asset_reference(stale)


def test_the_same_id_naming_a_different_asset_fails_closed(tmp_path):
    """F2 exactly: the asset set is regenerated differently (a different
    seed / track list), and the stored name now points at another
    physical asset. The fingerprint catches it."""

    service, job = _created(tmp_path, [make_asset("AST-T1-OHE-001", 1.2)])

    regenerated = make_service(
        tmp_path,
        [make_asset("AST-T1-OHE-001", 33.7, asset_type="SIGNAL_POST")],
        db_name="regenerated.db",
    )

    with pytest.raises(AssetReferenceError, match="no longer describes the asset"):
        regenerated.verify_job_asset_reference(job)


@pytest.mark.parametrize(
    "moved",
    [
        dict(km=1.25),  # moved by 50 m
        dict(km=1.2, asset_type="TURNOUT_POINT"),  # same place, other kind
        dict(km=1.2, track_id="T2"),  # other track
    ],
)
def test_any_change_to_identity_fields_is_detected(tmp_path, moved):
    service, job = _created(tmp_path, [make_asset("AST-T1-OHE-001", 1.2)])

    other = make_service(
        tmp_path,
        [
            make_asset(
                "AST-T1-OHE-001",
                moved["km"],
                track_id=moved.get("track_id", "T1"),
                asset_type=moved.get("asset_type", "OHE_MAST"),
            )
        ],
        track_ids=("T1", "T2"),
        db_name="other.db",
    )

    with pytest.raises(AssetReferenceError):
        other.verify_job_asset_reference(job)


def test_changing_condition_does_not_make_a_valid_reference_stale(tmp_path):
    service, job = _created(tmp_path, [make_asset("AST-T1-OHE-001", 1.2)])

    later = make_service(
        tmp_path,
        [
            make_asset(
                "AST-T1-OHE-001",
                1.2,
                condition_score=0.1,
                defect_severity="CRITICAL",
                last_maintained_days_ago=400,
            )
        ],
        db_name="later.db",
    )

    assert later.verify_job_asset_reference(job).status == ASSET_REFERENCE_VERIFIED


def test_a_reference_recorded_against_another_corridor_is_refused(tmp_path):
    """The same id text exists in both the generated corridor and the
    dataset corridor, at different positions. A job from one must not
    resolve in the other."""

    generated = JobService(repository=JobRepository(tmp_path / "gen.db"))
    job = generated.create_job(make_request(1_000, 1_400, track_id="UP-1"), actor=WORKER)
    asset_id = job["block_candidate"]["asset_id"]

    dataset = load_corridor_dataset()
    dataset_service = JobService(
        repository=JobRepository(tmp_path / "ds.db"),
        dataset=dataset,
    )

    # The id genuinely exists on the dataset corridor - only the corridor
    # and position differ.
    assert any(a.asset_id == asset_id for a in dataset_service.corridor.assets)

    with pytest.raises(AssetReferenceError, match="recorded against corridor"):
        dataset_service.verify_job_asset_reference(job)


def test_a_legacy_job_without_a_fingerprint_is_only_ever_existence_checked(tmp_path):
    service, job = _created(tmp_path)

    legacy = dict(job)
    block = dict(job["block_candidate"])
    block["metadata"] = {
        k: v for k, v in block["metadata"].items() if k != "asset_association"
    }
    legacy["block_candidate"] = block

    check = service.verify_job_asset_reference(legacy)
    assert check.status == ASSET_REFERENCE_UNFINGERPRINTED
    assert check.status != ASSET_REFERENCE_VERIFIED

    block["asset_id"] = "AST-GONE"
    with pytest.raises(AssetReferenceError):
        service.verify_job_asset_reference(legacy)


@pytest.mark.parametrize("blank", [None, ""])
def test_a_job_with_no_asset_id_is_refused(tmp_path, blank):
    service, job = _created(tmp_path)

    broken = dict(job)
    broken["block_candidate"] = dict(job["block_candidate"], asset_id=blank)

    with pytest.raises(AssetReferenceError, match="no asset_id"):
        service.verify_job_asset_reference(broken)


def test_an_id_carried_by_two_assets_is_ambiguous_and_refused():
    twin_a = make_asset("AST-T1-OHE-001", 1.2)
    twin_b = make_asset("AST-T1-OHE-001", 9.9)

    with pytest.raises(AssetReferenceError, match="ambiguous"):
        verify_asset_reference(CORRIDOR_ID, [twin_a, twin_b], "AST-T1-OHE-001")


def test_the_fingerprint_is_stable_and_covers_identity_not_condition():
    a = make_asset("AST-T1-OHE-001", 1.2)

    assert asset_fingerprint(CORRIDOR_ID, a) == asset_fingerprint(CORRIDOR_ID, a)
    assert asset_fingerprint(CORRIDOR_ID, a) != asset_fingerprint("OTHER", a)
    assert asset_fingerprint(CORRIDOR_ID, a) == asset_fingerprint(
        CORRIDOR_ID, make_asset("AST-T1-OHE-001", 1.2, condition_score=0.01)
    )
    assert asset_fingerprint(CORRIDOR_ID, a) != asset_fingerprint(
        CORRIDOR_ID, make_asset("AST-T1-OHE-001", 1.21)
    )
