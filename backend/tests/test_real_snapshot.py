"""Real-data integration: the offline NDLS -> AGC public-railway-data snapshot.

Covers the additive real-data path (backend.app.data.real_corridor_dataset):
integrity and fail-closed loading, the verified timetable, chainage / units,
zones and divisions, provenance classes, the derived candidate possession
windows, the CP-SAT run over them, and the guarantees that the synthetic
fallback is untouched and that nothing reaches the network at runtime.

The synthetic dataset's own tests are unchanged and still run against it.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import socket
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from backend.app.data.corridor_dataset import (
    CorridorDatasetError,
    DataBasis,
    load_corridor_dataset,
)
from backend.app.data.dataset_selection import (
    RAILWAY_DATA_ENV,
    RailwayDataConfigurationError,
    load_configured_dataset,
    railway_data_mode,
)
from backend.app.data.provenance import ProvenanceLevel, ProvenanceProfile
from backend.app.data.real_corridor_dataset import (
    CANDIDATE_WINDOW_LABEL,
    DATA_CLASSES,
    DEFAULT_SNAPSHOT_DIR,
    RealSnapshotError,
    load_real_corridor_dataset,
    load_real_snapshot,
)
from backend.app.data.timetable_adapter import convert_timetable
from backend.app.identity.actor import ActorRole, human_actor
from backend.app.jobs.optimization import JobOptimizationService
from backend.app.jobs.repository import JobRepository
from backend.app.jobs.service import JobService

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder = _load_module("pashupatastra_snapshot_builder", "scripts/real_data/build_ndls_agc_snapshot.py")


@pytest.fixture(scope="module")
def snapshot():
    return load_real_snapshot()


@pytest.fixture(scope="module")
def dataset():
    return load_real_corridor_dataset()


def _copy_snapshot(tmp_path: Path) -> Path:
    target = tmp_path / "snap"
    shutil.copytree(DEFAULT_SNAPSHOT_DIR, target)
    return target


# ----------------------------------------------------------------------
# Integrity and fail-closed loading
# ----------------------------------------------------------------------


def test_the_snapshot_loads_and_every_hash_matches(snapshot):
    assert snapshot.snapshot_id == "ndls-agc-2026-09-26"
    assert snapshot.manifest["offline"] is True
    assert list(snapshot.manifest["data_classes"]) == list(DATA_CLASSES)


def test_the_checked_in_snapshot_equals_a_rebuild():
    for relative, text in builder.build_all().items():
        assert (DEFAULT_SNAPSHOT_DIR / relative).read_text(encoding="utf-8") == text, (
            f"{relative} is out of date; run scripts/real_data/build_ndls_agc_snapshot.py"
        )


def test_every_manifest_source_has_full_metadata(snapshot):
    required = {
        "source_id", "name", "publisher", "url", "retrieved_at", "sha256",
        "data_as_of", "last_reviewed", "licence", "coverage",
    }
    for source in snapshot.manifest["sources"]:
        assert required <= set(source), source["source_id"]
        assert source["retrieved_at"] == "2026-09-26"
        assert len(source["sha256"]) == 64


@pytest.mark.parametrize(
    "victim",
    ["topology.json", "timetable.json", "rules.json", "sources/TAG-2026_T-2_Delhi-Agra-Bhopal-CSMT.pdf"],
)
def test_an_altered_file_is_refused_not_repaired(tmp_path, victim):
    root = _copy_snapshot(tmp_path)
    path = root / victim
    path.write_bytes(path.read_bytes() + b"\n ")
    with pytest.raises(RealSnapshotError, match="hash|altered"):
        load_real_snapshot(root)


def test_a_missing_snapshot_is_refused(tmp_path):
    with pytest.raises(RealSnapshotError):
        load_real_snapshot(tmp_path / "nowhere")


def _rewrite(root: Path, name: str, mutate) -> None:
    """Change a dataset file AND its manifest hash, so only validation can object."""

    import hashlib

    path = root / name
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["datasets"].values():
        if entry["file"] == name:
            entry["sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")


def test_an_unknown_data_class_is_refused(tmp_path):
    root = _copy_snapshot(tmp_path)
    _rewrite(root, "rules.json", lambda d: d["provenance"][0].__setitem__("class", "SYNTHETIC"))
    with pytest.raises(RealSnapshotError, match="unknown class"):
        load_real_snapshot(root)


def test_a_derived_value_must_say_how_it_was_derived(tmp_path):
    root = _copy_snapshot(tmp_path)

    def mutate(d):
        record = next(r for r in d["provenance"] if r["class"] == "DERIVED_FROM_REAL")
        record["derivation_method"] = None

    _rewrite(root, "topology.json", mutate)
    with pytest.raises(RealSnapshotError, match="DERIVED_FROM_REAL"):
        load_real_snapshot(root)


def test_a_provenance_record_may_not_cite_an_unknown_source(tmp_path):
    root = _copy_snapshot(tmp_path)
    _rewrite(root, "topology.json", lambda d: d["provenance"][0]["source_ids"].append("SRC-NOPE"))
    with pytest.raises(RealSnapshotError, match="unknown source"):
        load_real_snapshot(root)


def test_non_increasing_chainage_is_refused(tmp_path):
    root = _copy_snapshot(tmp_path)
    _rewrite(root, "topology.json", lambda d: d["stations"][2].__setitem__("km", 3.0))
    with pytest.raises(RealSnapshotError, match="increasing"):
        load_real_snapshot(root)


# ----------------------------------------------------------------------
# Topology: kilometres, no false precision, zones, direction
# ----------------------------------------------------------------------


def test_the_domain_model_is_in_kilometres_without_false_precision(snapshot):
    topology = snapshot.topology
    assert topology["distance_unit"] == "km"

    km = {s["station_id"]: s["km"] for s in topology["stations"]}
    assert km == {"NDLS": 0.0, "NZM": 7.0, "FDB": 28.0, "PWL": 54.8, "MTJ": 141.0, "RKM": 190.9, "AGC": 194.8}

    display = {s["station_id"]: s["km_display"] for s in topology["stations"]}
    assert display == {
        "NDLS": "0", "NZM": "7", "FDB": "28", "PWL": "~54.8", "MTJ": "141", "RKM": "~190.9", "AGC": "~194.8",
    }

    # The unsourced values from the earlier plan must not appear anywhere in the
    # stored topology.
    text = (DEFAULT_SNAPSHOT_DIR / "topology.json").read_text(encoding="utf-8")
    for false_precision in ("141.25", "191.02", "195.02", "57.30", "28.22", "7.28"):
        assert false_precision not in text


def test_pwl_mtj_conflict_is_recorded_in_provenance(snapshot):
    record = next(r for r in snapshot.topology["provenance"] if r["field"] == "stations.PWL.km")
    assert record["class"] == "DERIVED_FROM_REAL"
    assert record["confidence"] == "low"
    assert "UNRESOLVED CONFLICT" in record["note"]
    assert "~57" in record["note"]
    assert record["derivation_method"]


def test_chainage_derivations_reproduce_from_the_official_kmposts():
    # MTJ - (Palwal km-post - MTJ km-post)
    assert builder.STATION_KM["PWL"] == round(141 - (1473.981 + 9.31 - 1397.06), 1) == 54.8
    assert builder.STATION_KM["RKM"] == round(141 + (1397.06 - (1348.60 - 1.45)), 1) == 190.9
    assert builder.STATION_KM["AGC"] == round(141 + (1397.06 - (1348.60 - 1.45 - 3.88)), 1) == 194.8


def test_zone_and_division_are_attached_to_each_section(snapshot):
    by_id = {s["section_id"]: (s["zone"], s["division"]) for s in snapshot.topology["sections"]}
    for section in ("NDLS-NZM", "NZM-FDB", "FDB-PWL"):
        assert by_id[section] == ("Northern Railway", "Delhi Division")
    for section in ("PWL-MTJ", "MTJ-RKM", "RKM-AGC"):
        assert by_id[section] == ("North Central Railway", "Agra Division")


def test_coordinates_are_openstreetmap_and_not_claimed_official(snapshot):
    for station in snapshot.topology["stations"]:
        source = station["coordinate_source"]
        assert source["source_id"] == "SRC-OSM-STATIONS"
        assert source["official_railway_coordinate"] is False
        assert "OpenStreetMap" in source["attribution"]
    osm = snapshot.source("SRC-OSM-STATIONS")
    assert "ODbL" in osm["licence"]


def test_up_means_away_from_delhi_and_the_identifiers_are_kept(snapshot):
    tracks = {t["track_id"]: t["direction"] for t in snapshot.topology["tracks"]}
    assert tracks == {"UP-1": "UP", "DOWN-1": "DOWN"}
    convention = snapshot.topology["direction_convention"]
    assert "NDLS -> AGC" in convention["UP"]
    assert "opposite meaning" in convention["note"]

    km = {s["station_id"]: s["km"] for s in snapshot.topology["stations"]}
    for record in snapshot.timetable["service_records"]:
        route_km = [km[s] for s in record["route"]]
        if record["direction"] == "UP":
            assert record["track_assignment"] == "UP-1"
            assert route_km == sorted(route_km), record["train_number"]
        else:
            assert record["track_assignment"] == "DOWN-1"
            assert route_km == sorted(route_km, reverse=True), record["train_number"]


# ----------------------------------------------------------------------
# Timetable: the verified trains, and the wrong ones are gone
# ----------------------------------------------------------------------

VERIFIED = {
    "12138": "Punjab Mail", "12002": "Shatabdi", "12280": "Taj Express", "12050": "Gatiman Express",
    "12618": "Mangala Lakshadweep Express", "14212": "Intercity", "11058": "Express",
    "14211": "Intercity Express", "12049": "Gatiman Express", "12137": "Punjab Mail",
    "12279": "Taj Express", "12001": "Shatabdi Express", "11057": "Express",
}


def test_headline_trains_have_their_real_identity_and_direction(snapshot):
    trains = {t["train_number"]: t for t in snapshot.timetable["trains"]}
    assert len(trains) == 23
    for number, name in VERIFIED.items():
        assert trains[number]["train_name"] == name

    for number in ("12138", "12002", "12280", "12050", "12618", "14212", "11058"):
        assert trains[number]["direction"] == "UP", number
    for number in ("14211", "12049", "12137", "12279", "12001", "11057"):
        assert trains[number]["direction"] == "DN", number


def test_the_misattributed_train_numbers_are_not_carried_over(snapshot):
    numbers = {t["train_number"] for t in snapshot.timetable["trains"]}
    # Real numbers the synthetic dataset attached to the wrong name or direction,
    # or that T-2 does not carry on this corridor.
    for wrong in ("12192", "12625", "12415", "12925", "12919", "12057", "12058", "12047", "12048", "12431", "12432"):
        assert wrong not in numbers, wrong


def test_service_dates_follow_the_published_days_of_running(snapshot):
    def runs(number: str, day: str) -> bool:
        return any(
            r["train_number"] == number and r["service_date"] == day
            for r in snapshot.timetable["service_records"]
        )

    assert date(2026, 9, 10).weekday() == 3 and date(2026, 9, 11).weekday() == 4  # Thu, Fri

    # 'Except F': Gatimaan does not run on the Friday.
    assert runs("12050", "2026-09-10") and not runs("12050", "2026-09-11")
    assert runs("12049", "2026-09-10") and not runs("12049", "2026-09-11")
    # 'F,Su': Gondwana runs on the Friday only.
    assert runs("12406", "2026-09-11") and not runs("12406", "2026-09-10")
    # Daily trains run both days.
    assert runs("12280", "2026-09-10") and runs("12280", "2026-09-11")


def test_a_midnight_crossing_train_also_appears_the_day_before(snapshot):
    carried = [r for r in snapshot.timetable["service_records"] if r["carry_in"]]
    assert [(r["train_number"], r["service_date"]) for r in carried] == [("11057", "2026-09-09")]


def test_interpolated_passing_times_are_flagged_and_ordered(snapshot):
    for record in snapshot.timetable["service_records"]:
        bases = Counter(s["time_basis"] for s in record["station_times"])
        assert set(bases) <= {"TAG_PUBLISHED", "INTERPOLATED_BY_CHAINAGE"}
        for stop in record["station_times"]:
            if stop["time_basis"] == "INTERPOLATED_BY_CHAINAGE":
                assert stop["scheduled_arrival"] == stop["scheduled_departure"]

    taj = next(
        r for r in snapshot.timetable["service_records"]
        if r["train_number"] == "12280" and r["service_date"] == "2026-09-10"
    )
    times = {s["station_id"]: (s["scheduled_arrival"], s["time_basis"]) for s in taj["station_times"]}
    assert times["FDB"] == ("07:26", "TAG_PUBLISHED")
    assert times["PWL"][1] == "INTERPOLATED_BY_CHAINAGE"
    assert times["FDB"][0] < times["PWL"][0] < times["MTJ"][0]


def test_tag_times_at_faridabad_agree_with_the_northern_railway_annexure(snapshot):
    """Independent official cross-check: NR Annexure-A prints PTT arrival/departure at FDB."""

    annex = {r["train_number"]: r for r in snapshot.blocks["blocks"][0]["annexure_a_rows"]}

    def minutes(text: str) -> int:
        hh, mm = text.replace(".", ":").split(":")
        return int(hh) * 60 + int(mm)

    checked = 0
    for train in snapshot.timetable["trains"]:
        number = train["train_number"]
        fdb = next((s for s in train["published_stops"] if s["station_id"] == "FDB"), None)
        if fdb is None or number not in annex:
            continue
        tag = minutes(fdb["tag_departure"] or fdb["tag_arrival"])
        row = annex[number]
        assert min(abs(tag - minutes(row["ptt_arr"])), abs(tag - minutes(row["ptt_dep"]))) <= 1, number
        assert row["direction"] == ("UP" if train["direction"] == "UP" else "DN"), number
        checked += 1

    assert checked >= 8


def test_the_timetable_states_what_it_does_not_cover(snapshot):
    coverage = snapshot.timetable["coverage"]
    joined = " ".join(coverage["not_included"]).lower()
    for gap in ("freight", "emu", "working timetable"):
        assert gap in joined
    assert "NOT complete" in coverage["label"]


# ----------------------------------------------------------------------
# Provenance, classes and grounding
# ----------------------------------------------------------------------


def test_every_provenance_record_has_a_valid_class_and_a_real_source(snapshot):
    classes = Counter()
    for record in snapshot.all_provenance_records():
        assert record["class"] in DATA_CLASSES
        classes[record["class"]] += 1
    # The data is not flattened into one label.
    assert set(classes) == set(DATA_CLASSES)


def test_derived_chainage_is_never_labelled_directly_sourced(snapshot):
    for record in snapshot.topology["provenance"]:
        if record["field"].endswith(".km"):
            assert record["class"] == "DERIVED_FROM_REAL", record["field"]
            assert record["derived_from"] and record["derivation_method"]


def test_old_sips_keep_their_own_dates(snapshot):
    rkm = snapshot.source("SRC-NCR-SIP-RKM")
    assert rkm["data_as_of"] == "2012-03-20"
    assert "HISTORICAL" in rkm["currency"]
    assert snapshot.source("SRC-NCR-SIP-MTJ")["data_as_of"] == "2017-09-04"
    for entry in snapshot.infrastructure["running_lines"]:
        if entry["source_id"] == "SRC-NCR-SIP-RKM":
            assert entry["document_date"] == "2012-03-20"


def test_demo_jobs_are_grounded_in_works_but_remain_demo_observations(snapshot):
    grounded = {g["demo_job_key"]: g for g in snapshot.works["demo_job_grounding"]}
    assert set(grounded) == {
        "signal-fdb-pwl", "ballast-rkm-agc", "inspection-mtj-rkm", "fracture-mtj-rkm", "ohe-rkm-agc",
    }
    for entry in grounded.values():
        assert entry["not_evidence_of_defect"] is True
        assert entry["grounding_class"] == "REAL_DATED_SNAPSHOT"
        assert entry["observation_class"] == "DEMO_MAINTENANCE_INPUT"
        assert "DEMO MAINTENANCE INPUT" in entry["wording"]
        assert entry["grounding_items"]

    assert grounded["signal-fdb-pwl"]["grounding_items"][0]["item_no"] == 1575
    assert {i["item_no"] for i in grounded["ballast-rkm-agc"]["grounding_items"]} == {102, 110}
    assert grounded["ohe-rkm-agc"]["grounding_items"][0]["item_no"] == 217
    assert grounded["fracture-mtj-rkm"]["grounding_items"][0]["item_no"] == 115


def test_real_blocks_are_evidence_only(snapshot):
    blocks = {b["block_id"]: b for b in snapshot.blocks["blocks"]}
    fdb = blocks["NR-FDB-2026-09"]
    assert (fdb["period_from"], fdb["period_to"]) == ("2026-09-01", "2026-10-15")
    assert fdb["platform_changes"] == {"trains_up": 17, "trains_dn": 26, "from_platforms_to": "UP: platform 3 -> 4; DN: platform 2 -> 1"}
    assert blocks["CR-MTJ-2024-01"]["status_at_retrieval"] == "HISTORICAL"
    assert all(b["used_in_derivation"] is False for b in blocks.values())
    assert "not used" in fdb["timings_note"]
    assert any(r["class"] == "UNAVAILABLE_PUBLICLY" for r in snapshot.blocks["provenance"])


def test_unavailable_information_is_stated_not_invented(snapshot):
    assert any(r["class"] == "UNAVAILABLE_PUBLICLY" for r in snapshot.timetable["provenance"])
    assert snapshot.infrastructure["unavailable_publicly"]["class"] == "UNAVAILABLE_PUBLICLY"


# ----------------------------------------------------------------------
# The dataset the jobs pipeline consumes
# ----------------------------------------------------------------------


def test_the_real_dataset_has_the_canonical_shape(dataset):
    assert dataset.corridor_id == "CORR-NDLS-AGC"
    assert dataset.registry.section_ids() == (
        "NDLS-NZM", "NZM-FDB", "FDB-PWL", "PWL-MTJ", "MTJ-RKM", "RKM-AGC",
    )
    assert dataset.basis.label == "REAL_PUBLIC_SNAPSHOT"
    assert dataset.basis.timetable_train_provenance == "REAL_SCHEDULED"
    assert dataset.basis.asset_condition is ProvenanceLevel.SYNTHETIC
    assert dataset.corridor.zone.startswith("Northern Railway")
    assert "North Central Railway" in dataset.corridor.zone
    assert len(dataset.timetable_records) == len(dataset.snapshot.timetable["service_records"])


def test_chainage_resolves_through_the_registry_in_kilometres(dataset):
    assert dataset.registry.resolve_by_chainage(150.0).section_id == "MTJ-RKM"
    assert dataset.registry.resolve_by_chainage(54.8).section_id == "PWL-MTJ"  # half-open [start, end)
    assert dataset.registry.resolve_by_chainage(40.0).section_id == "FDB-PWL"


def test_every_train_converts_with_no_rejections(dataset):
    snap = convert_timetable(
        dataset.timetable_records,
        dataset.topology,
        provenance="REAL_SCHEDULED",
        horizon_start="2026-09-10T00:00:00+05:30",
    )
    assert snap.rejections == ()
    assert len(snap.trains) == len(dataset.timetable_records)
    assert snap.provenance == "REAL_SCHEDULED"


def test_the_synthetic_dataset_is_untouched():
    synthetic = load_corridor_dataset()
    assert synthetic.basis == DataBasis.synthetic()
    assert synthetic.snapshot is None
    assert [s for s in synthetic.registry.sections()][0].km_end == 7.0
    assert len(synthetic.timetable_records) == 48


def test_the_synthetic_loader_still_refuses_data_not_declared_synthetic(tmp_path):
    real_like = tmp_path / "real.json"
    real_like.write_text(json.dumps({"synthetic": False, "corridor": {"corridor_id": "X"}}), encoding="utf-8")
    with pytest.raises(CorridorDatasetError, match="synthetic"):
        load_corridor_dataset(real_like)


# ----------------------------------------------------------------------
# Selection: synthetic by default, real only by explicit opt-in, never a silent fallback
# ----------------------------------------------------------------------


def test_synthetic_is_the_default_and_selection_is_exact():
    assert railway_data_mode({}) == "synthetic"
    assert railway_data_mode({RAILWAY_DATA_ENV: "synthetic"}) == "synthetic"
    assert railway_data_mode({RAILWAY_DATA_ENV: "real"}) == "real"
    for bad in ("REAL", "Real", " real", "live", ""):
        with pytest.raises(RailwayDataConfigurationError):
            railway_data_mode({RAILWAY_DATA_ENV: bad})


def test_real_mode_selects_the_snapshot_and_default_mode_the_synthetic_dataset():
    assert load_configured_dataset({}).basis.label == "SYNTHETIC"
    assert load_configured_dataset({RAILWAY_DATA_ENV: "real"}).basis.label == "REAL_PUBLIC_SNAPSHOT"


def test_real_mode_never_falls_back_to_synthetic_when_the_snapshot_is_broken(monkeypatch):
    import backend.app.data.real_corridor_dataset as real

    monkeypatch.setattr(real, "DEFAULT_SNAPSHOT_DIR", Path("/definitely/not/here"))
    with pytest.raises(RealSnapshotError):
        load_configured_dataset({RAILWAY_DATA_ENV: "real"})


def test_real_mode_with_a_generated_corridor_is_a_configuration_error(monkeypatch):
    import backend.app.jobs.service as service_module

    monkeypatch.setattr(service_module, "DEFAULT_CORRIDOR_ID", "CORRIDOR_A")
    monkeypatch.setenv(RAILWAY_DATA_ENV, "real")
    with pytest.raises(ValueError, match="PASHUPAT_RAILWAY_DATA=real"):
        JobService._load_default_dataset()


# ----------------------------------------------------------------------
# Derived candidate windows and the real CP-SAT run
# ----------------------------------------------------------------------


def _service(tmp_path, name="real.db") -> JobService:
    return JobService(repository=JobRepository(tmp_path / name), dataset=load_real_corridor_dataset())


def test_possession_windows_are_derived_from_the_public_timetable(tmp_path):
    service = _service(tmp_path)
    inputs = service.possession_inputs()

    assert inputs.derivation == "CANONICAL_TIMETABLE_DERIVED"
    assert inputs.timetable_provenance is ProvenanceLevel.REAL_SCHEDULED
    # Derived windows are NOT a real/scheduled possession: conservative coarse tier.
    assert inputs.possession_provenance is ProvenanceLevel.SYNTHETIC
    assert inputs.possession_source_label == "DERIVED_FROM_PUBLIC_TIMETABLE"
    assert inputs.topology_provenance is ProvenanceLevel.REAL_STATIC
    assert inputs.asset_condition_provenance is ProvenanceLevel.SYNTHETIC
    assert inputs.rejections == ()

    resources = {(w.track_id, w.section_id) for w in inputs.windows}
    assert resources == {
        (t, s) for t in ("UP-1", "DOWN-1")
        for s in ("NDLS-NZM", "NZM-FDB", "FDB-PWL", "PWL-MTJ", "MTJ-RKM", "RKM-AGC")
    }
    assert all(w.window_type == "TRAIN_GAP" for w in inputs.windows)
    assert all(w.end_minute - w.start_minute >= 20 for w in inputs.windows)


def test_a_derived_window_never_overlaps_a_published_train(tmp_path):
    """Independent re-derivation: no window contains any train's section occupancy."""

    service = _service(tmp_path)
    inputs = service.possession_inputs()
    buffer = service.dataset.basis.safety_buffer_minutes

    for train in inputs.snapshot.trains:
        for traversal in train.traversals:
            for window in inputs.windows:
                if window.track_id != traversal.track_id or window.section_id != traversal.section_id:
                    continue
                assert (
                    window.end_minute <= traversal.enter_minute - buffer
                    or window.start_minute >= traversal.exit_minute + buffer
                ), (train.train_number, traversal.section_id, window.window_id)


def test_the_windows_are_labelled_candidates_and_the_label_never_says_real_or_live():
    assert CANDIDATE_WINDOW_LABEL == "CANDIDATE POSSESSION WINDOW - DERIVED FROM PUBLIC PASSENGER TIMETABLE"
    for forbidden in ("LIVE", "REAL POSSESSION", "AVAILABLE BLOCK"):
        assert forbidden not in CANDIDATE_WINDOW_LABEL.upper().replace("DERIVED", "")


def _run_demo_scenario(tmp_path, name: str):
    """Seed + optimize the demo jobs on the real dataset (the seed script's own path)."""

    demo = _load_seed_script_real()
    service = JobService(repository=JobRepository(tmp_path / name), dataset=load_real_corridor_dataset())
    ids = demo.seed(service)
    ids[demo.LIVE_INTAKE_JOB.key] = demo.report(service, demo.LIVE_INTAKE_JOB)["job_id"]
    authority = human_actor("AUTHORITY-017", ActorRole.AUTHORITY)
    outcome = JobOptimizationService(service).optimize_corridor("CORR-NDLS-AGC", actor=authority)
    return demo, service, ids, outcome


def _load_seed_script_real():
    import os

    previous = os.environ.get(RAILWAY_DATA_ENV)
    os.environ[RAILWAY_DATA_ENV] = "real"
    try:
        return _load_module("pashupatastra_seed_demo_real", "scripts/seed_demo.py")
    finally:
        if previous is None:
            os.environ.pop(RAILWAY_DATA_ENV, None)
        else:
            os.environ[RAILWAY_DATA_ENV] = previous


def test_cp_sat_solves_the_demo_jobs_over_the_real_derived_windows(tmp_path):
    demo, service, ids, outcome = _run_demo_scenario(tmp_path, "a.db")

    assert outcome["solver_status"] == "OPTIMAL"
    assert outcome["counts"]["scheduled"] == 5 and outcome["counts"]["unscheduled"] == 0
    assert outcome["possession_derivation"] == "CANONICAL_TIMETABLE_DERIVED"
    assert outcome["possession_source"] == "DERIVED_FROM_PUBLIC_TIMETABLE"

    windows = {}
    for window in service.possession_inputs().windows:
        windows.setdefault((window.section_id, window.track_id), []).append(
            (window.start_minute, window.end_minute)
        )

    for key, job_id in ids.items():
        stored = service.repository.get(job_id)
        block = stored["block_candidate"]
        start, end = stored["schedule_start_minute"], stored["schedule_end_minute"]
        assert any(ws <= start and end <= we for ws, we in windows[(block["section_id"], block["track_id"])]), key


def test_the_real_run_reports_its_provenance_axes_and_stays_synthetic_overall(tmp_path):
    _, _, _, outcome = _run_demo_scenario(tmp_path, "b.db")
    provenance = outcome["provenance"]
    assert provenance["topology"] == "REAL_STATIC"
    assert provenance["timetable"] == "REAL_SCHEDULED"
    # Candidate windows derived by rule are never labelled a real/scheduled possession.
    assert provenance["possession"] == "SYNTHETIC"
    assert outcome["possession_source"] == "DERIVED_FROM_PUBLIC_TIMETABLE"
    assert provenance["asset_condition"] == "SYNTHETIC"
    # One synthetic axis (the demo asset condition) keeps the weakest-link result non-real.
    assert provenance["effective"] == "SYNTHETIC"
    assert ProvenanceProfile.from_dict(provenance).effective is ProvenanceLevel.SYNTHETIC


def test_the_real_run_is_deterministic(tmp_path):
    def business(name):
        demo, service, ids, outcome = _run_demo_scenario(tmp_path, name)
        return {
            key: (
                service.repository.get(job_id)["schedule_start_minute"],
                service.repository.get(job_id)["schedule_end_minute"],
                service.repository.get(job_id)["priority_score"],
            )
            for key, job_id in ids.items()
        }, outcome["provenance"], outcome["counts"]

    assert business("first.db") == business("second.db")


def test_the_real_seed_scenario_verifies():
    import tempfile

    demo = _load_seed_script_real()
    with tempfile.TemporaryDirectory() as scratch:
        service = JobService(
            repository=JobRepository(Path(scratch) / "jobs.db"), dataset=load_real_corridor_dataset()
        )
        rehearsal = demo.rehearse(service, demo.seed(service))
        assert demo.check_invariants(service, rehearsal) == []

    assert demo.REAL_DATA_MODE is True
    assert all(job.description.startswith("DEMO INPUT:") for job in demo.SEED_JOBS)
    assert demo.LIVE_INTAKE_JOB.description.startswith("DEMO INPUT:")
    assert demo.LIVE_INTAKE_JOB.evidence_reference.startswith("demo-input/")


# ----------------------------------------------------------------------
# Distance units: km in the domain, metres at the frozen API/DB boundary
# ----------------------------------------------------------------------


def test_the_frozen_v1_boundary_stays_in_metres_over_a_km_domain(tmp_path):
    demo, service, ids, _ = _run_demo_scenario(tmp_path, "c.db")
    stored = service.repository.get(ids["fracture-mtj-rkm"])

    # MTJ (141 km) + 5700..6000 m toward RKM  ->  corridor-absolute METRES in the DB.
    assert stored["distance_start"] == 146700.0
    assert stored["distance_end"] == 147000.0

    # ... which resolves back to the km-domain section.
    assert service.registry.resolve_by_chainage(stored["distance_start"] / 1000.0).section_id == "MTJ-RKM"


# ----------------------------------------------------------------------
# Offline: nothing on the runtime path reaches the network
# ----------------------------------------------------------------------


def test_runtime_never_touches_the_network(tmp_path, monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("the real-data runtime path attempted network access")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)

    dataset = load_real_corridor_dataset()
    service = JobService(repository=JobRepository(tmp_path / "off.db"), dataset=dataset)
    assert service.possession_inputs().windows

    demo = _load_seed_script_real()
    ids = demo.seed(service)
    assert len(ids) == 4
    JobOptimizationService(service).optimize_corridor(
        "CORR-NDLS-AGC", actor=human_actor("AUTHORITY-017", ActorRole.AUTHORITY)
    )


def test_runtime_modules_import_no_network_libraries():
    import ast

    banned = {"urllib", "urllib.request", "http.client", "requests", "httpx", "socket", "aiohttp", "ftplib"}
    for relative in (
        "backend/app/data/real_corridor_dataset.py",
        "backend/app/data/dataset_selection.py",
        "backend/app/data/corridor_dataset.py",
    ):
        tree = ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            assert not (set(names) & banned), (relative, names)


def test_the_snapshot_builder_itself_needs_no_network():
    import ast

    tree = ast.parse((REPO_ROOT / "scripts/real_data/build_ndls_agc_snapshot.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & {"urllib", "requests", "httpx", "socket", "http", "aiohttp"})


# ----------------------------------------------------------------------
# Frontend: build-time projection, no hard-coded chainage, honest wording
# ----------------------------------------------------------------------


def test_the_frontend_snapshot_module_is_in_sync():
    frontend_builder = _load_module(
        "pashupatastra_frontend_snapshot_builder", "scripts/real_data/build_frontend_snapshot.py"
    )
    assert frontend_builder.OUTPUT.read_text(encoding="utf-8") == frontend_builder.render(), (
        "frontend/src/data/ndlsAgcSnapshot.generated.ts is stale; "
        "run scripts/real_data/build_frontend_snapshot.py"
    )


def test_the_frontend_has_no_hard_coded_real_chainage_tables():
    """Station/chainage tables come from the snapshot; the only literal table is the synthetic fallback."""

    src = REPO_ROOT / "frontend" / "src"
    field_location = (src / "lib" / "fieldLocation.ts").read_text(encoding="utf-8")
    scene = (src / "components" / "CorridorScene.tsx").read_text(encoding="utf-8")

    assert "kmEnd: 195" not in field_location and "kmEnd: 66" not in field_location
    assert "FDB: 32" not in scene and "const TOTAL_KM = 195" not in scene
    assert "@/lib/railwayData" in field_location and "@/lib/railwayData" in scene

    railway_data = (src / "lib" / "railwayData.ts").read_text(encoding="utf-8")
    assert "SYNTHETIC_SECTIONS" in railway_data  # the deterministic fallback stays available


def test_the_frontend_never_uses_forbidden_live_wording():
    import re

    forbidden = re.compile(r"COMMAND CENTER|AI LIVE|LIVE RAILWAY DATA|REAL-TIME|REAL TIME", re.IGNORECASE)
    for path in (REPO_ROOT / "frontend" / "src").rglob("*.tsx"):
        text = path.read_text(encoding="utf-8")
        assert not forbidden.search(text), path.relative_to(REPO_ROOT)


def test_the_about_page_carries_the_required_statement():
    text = (REPO_ROOT / "frontend" / "src" / "app" / "(console)" / "about" / "page.tsx").read_text(encoding="utf-8")
    flat = " ".join(text.split())
    for sentence in (
        "Pashupatastra uses published railway infrastructure and timetable data as an offline, dated reference layer.",
        "Operational possession windows are derived from the public timetable using explicit engineering rules.",
        "Maintenance observations shown in this demonstration are controlled field/demo inputs grounded in real railway work categories.",
        "No live or confidential Indian Railways operational feed is connected.",
    ):
        assert sentence in flat, sentence


def test_the_frontend_moves_no_train_on_the_corridor_scene():
    scene = (REPO_ROOT / "frontend" / "src" / "components" / "CorridorScene.tsx").read_text(encoding="utf-8")
    # The moving 'laser' pulses that could read as trains exist only in the synthetic fallback.
    assert "!IS_REAL &&" in scene


def test_the_frontend_loads_nothing_from_the_internet():
    """Fonts are self-hosted: no stylesheet, font or script is fetched from a website."""

    import re

    src = REPO_ROOT / "frontend" / "src"
    fetchers = re.compile(
        r"fonts\.googleapis|fonts\.gstatic|<script[^>]+src=[\"']https?:|<link[^>]+href=[\"']https?:|@import\s+url\(\s*[\"']?https?:",
        re.IGNORECASE,
    )
    for path in list(src.rglob("*.tsx")) + list(src.rglob("*.css")) + list(src.rglob("*.ts")):
        if path.name.endswith(".generated.ts"):
            continue  # data strings (source links shown to the reader), never fetched
        assert not fetchers.search(path.read_text(encoding="utf-8")), path.relative_to(REPO_ROOT)

    fonts = (src / "app" / "fonts.css").read_text(encoding="utf-8")
    assert "url(\"/fonts/" in fonts and "http" not in fonts
    for name in re.findall(r"/fonts/([\w.-]+\.woff2)", fonts):
        assert (REPO_ROOT / "frontend" / "public" / "fonts" / name).is_file(), name


def test_ui_never_presents_candidate_windows_as_real_possession_and_labels_demo_assets():
    src = REPO_ROOT / "frontend" / "src"
    card = (src / "components" / "ProposalCard.tsx").read_text(encoding="utf-8")
    assert "candidate-window-note" in card and "not a real or scheduled possession" in card
    assert '"DERIVED_FROM_REAL"' in card and "DEMO_MAINTENANCE_INPUT" in card
    # the coarse possession tier is never shown to the reader as a class in the real strip
    assert "backend axis value" not in card

    detail = (src / "app" / "(console)" / "jobs" / "[jobId]" / "page.tsx").read_text(encoding="utf-8")
    assert "DEMO ASSET RECORD" in detail and "not an Indian Railways asset record" in detail
    scoring = (src / "components" / "ScoringPanel.tsx").read_text(encoding="utf-8")
    assert "DEMO ASSET RECORD" in scoring and "not Indian Railways asset-condition data" in scoring


def test_the_modal_focus_fix_in_ui_tsx_is_intact():
    ui = (REPO_ROOT / "frontend" / "src" / "components" / "ui.tsx").read_text(encoding="utf-8")
    assert "const latest = useRef({ onClose, busy });" in ui
    assert "latest.current.onClose()" in ui
    assert "}, [open]);" in ui
