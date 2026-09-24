"""P0-3: the deterministic demo seed / reset (scripts/seed_demo.py).

Every test builds its own demo service over a tmp_path database. The
root jobs.db is never opened; the tests that aim the reset at it prove it
is refused and left byte-identical.

Placement minutes are NOT asserted exactly (they are only stable for one
OR-Tools version and solver configuration - see optimizer/solver.py).
What is asserted is two-run equality and the scenario's invariants, which
is also what `seed_demo.py --verify` checks on the recording machine.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.data.feature_adapter import WORK_TYPE_DEFAULT_DURATIONS

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "seed_demo.py"
ROOT_DB = REPO_ROOT / "jobs.db"

_spec = importlib.util.spec_from_file_location("pashupatastra_seed_demo", SCRIPT)
demo = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = demo  # dataclasses resolve annotations through sys.modules
_spec.loader.exec_module(demo)

AUTHORITY = {"X-Actor-Id": "AUTHORITY-017", "X-Actor-Role": "AUTHORITY"}
DAY = demo.DAY_MINUTES


def sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


@pytest.fixture()
def service(tmp_path):
    return demo.build_demo_service(tmp_path / "demo" / "jobs.db")


@pytest.fixture()
def rehearsed(service):
    rehearsal = demo.rehearse(service, demo.seed(service))
    return service, rehearsal


@pytest.fixture(autouse=True)
def root_jobs_db_is_untouched():
    before = sha256(ROOT_DB)
    yield
    assert sha256(ROOT_DB) == before


# ------------------------------------------------------------- the seed


def test_seed_reports_every_job_and_leaves_it_reported(service):
    ids = demo.seed(service)

    assert list(ids) == [job.key for job in demo.SEED_JOBS]
    assert len(set(ids.values())) == len(demo.SEED_JOBS)

    for job in demo.SEED_JOBS:
        stored = service.repository.get(ids[job.key])
        assert stored["status"] == "reported"
        assert stored["schedule_start_minute"] is None
        assert stored["work_type"] == job.job_type
        assert stored["track_id"] == job.track_id
        assert stored["block_candidate"]["section_id"] == (
            f"{job.from_station_id}-{job.toward_station_id}"
        )
        metadata = stored["block_candidate"]["metadata"]
        assert metadata["reported_severity"] == job.severity
        assert metadata["evidence_reference"].startswith("synthetic-demo/")

        # Reported through the canonical path: created and scored, by
        # the declared field worker, with no proposal and nothing owed.
        events = [e.event for e in service.history.list_for_job(ids[job.key])]
        assert [e.event_type.value for e in events] == ["JOB_CREATED", "JOB_SCORED"]
        assert events[0].actor.actor_id == job.reporter_id
        obligation = service.job_obligation(ids[job.key])
        assert obligation.obligation_type.value == "NONE"


def test_seed_is_idempotent_by_key_and_refuses_a_non_empty_database(service):
    ids = demo.seed(service)

    with pytest.raises(demo.DemoResetRefused):
        demo.seed(service)

    # A re-report of the same field observation is answered, not re-created.
    replay = demo.report(service, demo.SEED_JOBS[0])
    assert replay["job_id"] == ids[demo.SEED_JOBS[0].key]
    assert len(service.repository.list_all()) == len(demo.SEED_JOBS)


def test_jobs_are_found_by_what_they_are_not_by_a_hardcoded_id(service):
    ids = demo.seed(service)

    for job in demo.SEED_JOBS:
        assert demo.find_demo_job(service, job)["job_id"] == ids[job.key]
    assert demo.find_demo_job(service, demo.LIVE_INTAKE_JOB) is None

    source = SCRIPT.read_text(encoding="utf-8")
    assert "JOB-" not in source


def test_every_demo_job_fits_a_possession_window_on_its_own_track(service):
    windows = demo._windows_by_resource(service)

    for job in demo.SEED_JOBS + (demo.LIVE_INTAKE_JOB,):
        candidate = demo.job_request(job, service)
        section = f"{job.from_station_id}-{job.toward_station_id}"
        stored_duration = WORK_TYPE_DEFAULT_DURATIONS[candidate.job_type.value]
        longest = max(end - start for start, end in windows[(section, job.track_id)])
        assert longest >= stored_duration, job.key

    # 240 minutes fits no single possession on this corridor, so the
    # scenario must not rely on track renewal.
    assert all(j.job_type != "TRACK_RENEWAL" for j in demo.SEED_JOBS + (demo.LIVE_INTAKE_JOB,))


# ---------------------------------------------------- the rehearsed demo


def test_the_whole_demo_satisfies_every_invariant(rehearsed):
    service, rehearsal = rehearsed

    assert demo.check_invariants(service, rehearsal) == []


def test_every_job_is_placed_inside_a_real_window_in_both_runs(rehearsed):
    service, rehearsal = rehearsed
    windows = demo._windows_by_resource(service)

    for run in (rehearsal.first_run, rehearsal.second_run):
        assert run["solver_status"] == "OPTIMAL"
        assert run["counts"]["unscheduled"] == 0
        assert run["counts"]["considered"] == len(rehearsal.ids)

    for key, job_id in rehearsal.ids.items():
        candidate = service.repository.get(job_id)["block_candidate"]
        available = windows[(candidate["section_id"], candidate["track_id"])]
        for placements in (rehearsal.after_first, rehearsal.after_second):
            start, end = placements[key]["start_minute"], placements[key]["end_minute"]
            assert end - start == candidate["duration_minutes"]
            assert any(ws <= start and end <= we for ws, we in available), key


def test_postponement_moves_the_target_from_day_1_to_day_2(rehearsed):
    service, rehearsal = rehearsed
    before = rehearsal.after_first[demo.POSTPONE_TARGET]
    after = rehearsal.after_second[demo.POSTPONE_TARGET]

    assert before["status"] == "scheduled" and before["end_minute"] <= DAY
    assert after["status"] == "scheduled" and after["start_minute"] >= DAY

    events = [
        e.event
        for e in service.history.list_for_job(rehearsal.ids[demo.POSTPONE_TARGET])
    ]
    postponed = [e for e in events if e.event_type.value == "PROPOSAL_POSTPONED"]
    assert len(postponed) == 1
    assert postponed[0].actor.actor_id == demo.AUTHORITY_ID
    assert demo.POSTPONE_REASON in (postponed[0].reason or "")


def test_approved_block_is_protected_by_re_optimization_then_executed(rehearsed):
    service, rehearsal = rehearsed
    key = demo.APPROVE_TARGET
    first, second = rehearsal.after_first[key], rehearsal.after_second[key]

    assert second["status"] == "notified"
    assert (second["start_minute"], second["end_minute"]) == (
        first["start_minute"],
        first["end_minute"],
    )
    assert rehearsal.second_run["counts"]["committed"] == 1

    events = [e.event.event_type.value for e in service.history.list_for_job(rehearsal.ids[key])]
    assert "COMMITTED_BLOCK_PRESERVED" in events
    assert events[-2:] == ["EXECUTION_STARTED", "EXECUTION_COMPLETED"]
    assert rehearsal.final[key]["status"] == "completed"


def test_the_critical_live_report_is_the_top_priority(rehearsed):
    service, rehearsal = rehearsed
    scores = {
        key: service.repository.get(job_id)["priority_score"]
        for key, job_id in rehearsal.ids.items()
    }

    assert max(scores, key=scores.get) == demo.LIVE_INTAKE_JOB.key


def test_rejection_is_recorded_with_its_reason(rehearsed):
    service, rehearsal = rehearsed
    events = [
        e.event
        for e in service.history.list_for_job(rehearsal.ids[demo.REJECT_TARGET])
    ]
    rejected = [e for e in events if e.event_type.value == "PROPOSAL_REJECTED"]

    assert len(rejected) == 1
    assert rejected[0].reason and demo.REJECT_REASON in rejected[0].reason


def test_provenance_stays_synthetic(rehearsed):
    _, rehearsal = rehearsed

    for run in (rehearsal.first_run, rehearsal.second_run):
        assert set(run["provenance"].values()) == {"SYNTHETIC"}


def test_business_output_is_identical_on_two_fresh_databases(tmp_path):
    summaries, id_sets = [], []

    for name in ("first", "second"):
        service = demo.build_demo_service(tmp_path / name / "jobs.db")
        rehearsal = demo.rehearse(service, demo.seed(service))
        assert demo.check_invariants(service, rehearsal) == []
        summaries.append(demo.business_summary(service, rehearsal))
        id_sets.append(set(rehearsal.ids.values()))

    assert summaries[0] == summaries[1]
    assert id_sets[0].isdisjoint(id_sets[1])  # ids are random; the business is not

    obligations = {k: j["obligation"] for k, j in summaries[0]["jobs"].items()}
    assert obligations[demo.APPROVE_TARGET]["type"] == "NONE"  # completed
    assert obligations[demo.POSTPONE_TARGET] == {
        "type": "APPROVAL_PENDING",
        "state": "WITHIN_SLA",
        "owed_role": "AUTHORITY",
    }


def test_a_failed_live_intake_still_leaves_the_target_on_day_1(service):
    ids = demo.seed(service)
    optimizer = demo.JobOptimizationService(service)
    outcome = optimizer.optimize_corridor(
        demo.DEMO_CORRIDOR_ID,
        actor=demo.human_actor(demo.AUTHORITY_ID, demo.ActorRole.AUTHORITY),
    )

    assert outcome["counts"]["unscheduled"] == 0
    assert service.repository.get(ids[demo.POSTPONE_TARGET])["schedule_end_minute"] <= DAY


# ------------------------------------ observable through the /v1 API


def test_postponement_is_visible_through_the_v1_api(service, monkeypatch):
    from backend.app.api.main import app
    from backend.app.jobs import router

    ids = demo.seed(service)
    monkeypatch.setattr(router, "service", service)
    monkeypatch.setattr(router, "optimization_service", demo.JobOptimizationService(service))
    client = TestClient(app)
    target = ids[demo.POSTPONE_TARGET]

    first = client.post(f"/v1/corridors/{demo.DEMO_CORRIDOR_ID}/optimize-jobs", headers=AUTHORITY)
    assert first.status_code == 200, first.text
    assert first.json()["counts"]["unscheduled"] == 0

    job = client.get(f"/v1/jobs/{target}").json()
    assert job["status"] == "scheduled" and job["schedule_end_minute"] <= DAY
    proposal = client.get(f"/v1/jobs/{target}/proposal").json()
    assert proposal["end_minute"] <= DAY

    postponed = client.post(
        f"/v1/jobs/{target}/proposal/postpone",
        json={
            "expected_proposal_run_id": job["proposal_run_id"],
            "reason": demo.POSTPONE_REASON,
            "selected_date": demo.POSTPONE_DATE,
        },
        headers=AUTHORITY,
    )
    assert postponed.status_code == 200, postponed.text
    assert postponed.json()["job"]["status"] == "reported"

    second = client.post(f"/v1/corridors/{demo.DEMO_CORRIDOR_ID}/optimize-jobs", headers=AUTHORITY)
    assert second.status_code == 200, second.text

    moved = client.get(f"/v1/jobs/{target}").json()
    assert moved["status"] == "scheduled" and moved["schedule_start_minute"] >= DAY
    history = client.get(f"/v1/jobs/{target}/history").json()["events"]
    assert "PROPOSAL_POSTPONED" in [e["event_type"] for e in history]


# ------------------------------------------------------------ the reset


def test_reset_refuses_the_repository_root_jobs_db():
    before = sha256(ROOT_DB)

    with pytest.raises(demo.DemoResetRefused):
        demo.reset_database(ROOT_DB)
    with pytest.raises(demo.DemoResetRefused):
        demo.reset_database(REPO_ROOT / "backend" / ".." / "jobs.db")

    assert sha256(ROOT_DB) == before


def test_reset_refuses_relative_paths_and_directories(tmp_path):
    with pytest.raises(demo.DemoResetRefused):
        demo.reset_database("demo/jobs.db")
    with pytest.raises(demo.DemoResetRefused):
        demo.reset_database(tmp_path)
    assert tmp_path.is_dir()


def test_reset_refuses_to_delete_a_file_that_is_not_a_jobs_database(tmp_path):
    document = tmp_path / "PASHUPATASTRA_FINAL_DEMO_PLAYBOOK.pdf"
    document.write_bytes(b"%PDF-1.7\nprotected presentation\n")
    other_sqlite = tmp_path / "other.db"
    with closing(sqlite3.connect(other_sqlite)) as conn:
        conn.execute("CREATE TABLE unrelated (x INTEGER)")
        conn.commit()

    for path in (document, other_sqlite):
        before = sha256(path)
        with pytest.raises(demo.DemoResetRefused):
            demo.reset_database(path)
        assert sha256(path) == before


def test_reset_wipes_an_existing_demo_database_and_its_sidecars(tmp_path):
    path = tmp_path / "demo" / "jobs.db"
    service = demo.build_demo_service(path)
    demo.seed(service)
    for suffix in ("-wal", "-shm", "-journal"):
        Path(f"{path}{suffix}").write_bytes(b"stale")

    demo.reset_database(path)

    assert demo.build_demo_service(path).repository.list_all() == []
    for suffix in ("-wal", "-shm", "-journal"):
        assert not Path(f"{path}{suffix}").exists()


def test_reset_accepts_a_missing_or_empty_file(tmp_path):
    missing = tmp_path / "fresh" / "jobs.db"
    empty = tmp_path / "empty.db"
    empty.write_bytes(b"")

    for path in (missing, empty):
        demo.reset_database(path)
        assert demo.build_demo_service(path).repository.list_all() == []


# ---------------------------------------------------------------- CLI


def _demo_env(monkeypatch, db_path):
    monkeypatch.setenv("PASHUPAT_CORRIDOR_ID", demo.DEMO_CORRIDOR_ID)
    monkeypatch.setenv("PASHUPAT_JOBS_DB", str(db_path))
    for name in list(__import__("os").environ):
        if name.startswith(("PASHUPAT_AUTH_", "PASHUPAT_IDENTITY_")):
            monkeypatch.delenv(name)


def test_cli_reset_seeds_the_named_database_only(tmp_path, monkeypatch, capsys):
    path = tmp_path / "demo" / "jobs.db"
    _demo_env(monkeypatch, path)

    assert demo.main([]) == 0
    assert demo.main([]) == 0  # a second reset starts from scratch again

    jobs = demo.build_demo_service(path).repository.list_all()
    assert len(jobs) == len(demo.SEED_JOBS)
    assert {j["status"] for j in jobs} == {"reported"}
    out = capsys.readouterr().out
    assert "SYNTHETIC ILLUSTRATIVE SCENARIO" in out
    assert "MTJ -> RKM" in out


def test_cli_verify_passes_and_never_touches_the_demo_database(tmp_path, monkeypatch, capsys):
    path = tmp_path / "demo" / "jobs.db"
    _demo_env(monkeypatch, path)

    assert demo.main(["--verify"]) == 0
    assert "VERIFY PASS" in capsys.readouterr().out
    assert not path.exists()


@pytest.mark.parametrize(
    "corridor, db",
    [
        (None, "set"),
        ("CORRIDOR_A", "set"),
        ("CORR-NDLS-AGC", None),
        ("CORR-NDLS-AGC", "root"),
    ],
)
def test_cli_refuses_a_non_demo_environment(tmp_path, monkeypatch, corridor, db):
    before = sha256(ROOT_DB)
    _demo_env(monkeypatch, tmp_path / "jobs.db")
    if corridor is None:
        monkeypatch.delenv("PASHUPAT_CORRIDOR_ID")
    else:
        monkeypatch.setenv("PASHUPAT_CORRIDOR_ID", corridor)
    if db is None:
        monkeypatch.delenv("PASHUPAT_JOBS_DB")
    elif db == "root":
        monkeypatch.setenv("PASHUPAT_JOBS_DB", str(ROOT_DB))

    assert demo.main([]) == 2
    assert sha256(ROOT_DB) == before


def test_cli_refuses_when_the_backend_would_not_boot_in_demo_mode(tmp_path, monkeypatch):
    _demo_env(monkeypatch, tmp_path / "jobs.db")
    monkeypatch.setenv("PASHUPAT_AUTH_MODE", "novaforge")

    assert demo.main([]) == 2
    assert not (tmp_path / "jobs.db").exists()


def test_script_never_imports_the_router_or_the_app():
    imported = set()
    for node in ast.walk(ast.parse(SCRIPT.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)

    assert "backend.app.api.main" not in imported
    assert "backend.app.jobs.router" not in imported
