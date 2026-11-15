"""Persisted DISRUPT -> RECOVER workflow on the real jobs pipeline.

Covers the pure window arithmetic, declaration validation, the full
lifecycle scenario on the demo corridor (register -> refused recovery ->
release -> recover -> clear), the safety refusals (committed blocks are
never silently moved or kept, a disrupted proposal cannot be approved,
removing every window fails closed), append-only storage, and server-side
authorization under an enforcing policy.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from contracts import PossessionWindow

from backend.app.identity.actor import ActorRole, human_actor
from backend.app.identity.authorization import AuthorizationDenied, JobAction
from backend.app.identity.role_actions import role_permits
from backend.app.jobs.disruption import (
    AllPossessionRemovedError,
    DisruptedPlacementError,
    DisruptionKind,
    DisruptionNotFoundError,
    DisruptionRecord,
    DisruptionStateError,
    RecoveryBlockedError,
    affected_jobs,
    compare_plans,
    span_minutes,
    subtract_disruptions,
    validate_declaration,
)
from backend.app.jobs.disruption_service import DisruptionService
from backend.app.jobs.lifecycle import proposal_run_id_of
from backend.app.jobs.optimization import JobOptimizationService
from backend.app.jobs.service import OPTIMIZATION_HORIZON_START

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "pashupatastra_seed_demo_for_disruption", REPO_ROOT / "scripts" / "seed_demo.py"
)
demo = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = demo
_spec.loader.exec_module(demo)

CORRIDOR = demo.DEMO_CORRIDOR_ID
ENGINEER = human_actor("ENGINEER-003", ActorRole.ENGINEER)
AUTHORITY = human_actor("AUTHORITY-017", ActorRole.AUTHORITY)
WORKER = human_actor("WORKER-042", ActorRole.WORKER)

H0 = datetime.fromisoformat(OPTIMIZATION_HORIZON_START)


def iso(minute: float) -> str:
    return (H0 + timedelta(minutes=minute)).isoformat()


def record(
    disruption_id="DIS-T1",
    track="UP-1",
    section="NDLS-NZM",
    start=100,
    end=200,
    kind=DisruptionKind.POSSESSION_CURTAILMENT,
) -> DisruptionRecord:
    return DisruptionRecord(
        disruption_id=disruption_id,
        kind=kind,
        corridor_id=CORRIDOR,
        track_id=track,
        section_id=section,
        starts_at=iso(start),
        ends_at=iso(end),
        reason="test",
        declared_by="ENGINEER-003",
        declared_role="ENGINEER",
        registered_at=iso(0),
    )


def window(wid="W1", track="UP-1", section="NDLS-NZM", start=0, end=300):
    return PossessionWindow(
        window_id=wid, track_id=track, start_minute=start,
        end_minute=end, section_id=section,
    )


# ----------------------------------------------------------------------
# Pure arithmetic
# ----------------------------------------------------------------------


def test_span_rounds_outward_so_a_disruption_can_only_widen():
    start, end = span_minutes(iso(10.4), iso(20.2), OPTIMIZATION_HORIZON_START)
    assert (start, end) == (10, 21)


def test_untouched_window_is_returned_unchanged_and_other_resources_ignored():
    w_other_track = window("A", track="DOWN-1")
    w_other_section = window("B", section="NZM-FDB")
    out = subtract_disruptions(
        [w_other_track, w_other_section], [record()], OPTIMIZATION_HORIZON_START
    )
    assert out == [w_other_track, w_other_section]
    assert out[0] is w_other_track


def test_a_disruption_in_the_middle_splits_a_window_in_two():
    out = subtract_disruptions([window()], [record(start=100, end=200)], OPTIMIZATION_HORIZON_START)
    assert [(w.start_minute, w.end_minute) for w in out] == [(0, 100), (200, 300)]
    assert [w.window_id for w in out] == ["W1~d0", "W1~d1"]


def test_a_covering_disruption_removes_the_window_and_edges_trim():
    covering = subtract_disruptions([window()], [record(start=-10, end=400)], OPTIMIZATION_HORIZON_START)
    assert covering == []
    trimmed = subtract_disruptions([window()], [record(start=250, end=400)], OPTIMIZATION_HORIZON_START)
    assert [(w.start_minute, w.end_minute) for w in trimmed] == [(0, 250)]


def test_subtraction_never_adds_possession():
    windows = [window("A", start=0, end=300), window("B", section="NZM-FDB", start=50, end=90)]
    disruptions = [record(start=40, end=60), record("DIS-T2", section=None, start=250, end=260,
                                                    kind=DisruptionKind.TRACK_UNAVAILABLE)]
    out = subtract_disruptions(windows, disruptions, OPTIMIZATION_HORIZON_START)

    def covered(ws):
        return {
            (w.track_id, w.section_id, m)
            for w in ws for m in range(w.start_minute, w.end_minute)
        }

    assert covered(out) <= covered(windows)


def test_track_unavailable_without_section_hits_every_section_of_the_track():
    rec = record(section=None, kind=DisruptionKind.TRACK_UNAVAILABLE, start=0, end=300)
    out = subtract_disruptions(
        [window("A"), window("B", section="NZM-FDB"), window("C", track="DOWN-1")],
        [rec], OPTIMIZATION_HORIZON_START,
    )
    assert [w.window_id for w in out] == ["C"]


def test_compare_plans_classifies_every_job():
    before = {
        "same": {"status": "scheduled", "start_minute": 1, "end_minute": 2},
        "moved": {"status": "scheduled", "start_minute": 1, "end_minute": 2},
        "lost": {"status": "scheduled", "start_minute": 1, "end_minute": 2},
    }
    after = {
        "same": {"status": "scheduled", "start_minute": 1, "end_minute": 2},
        "moved": {"status": "scheduled", "start_minute": 5, "end_minute": 6},
        "new": {"status": "scheduled", "start_minute": 9, "end_minute": 10},
    }
    diff = compare_plans(before, after)
    assert diff["unchanged"] == ["same"]
    assert [m["job_id"] for m in diff["moved"]] == ["moved"]
    assert [m["job_id"] for m in diff["newly_unscheduled"]] == ["lost"]
    assert [m["job_id"] for m in diff["newly_scheduled"]] == ["new"]


def test_affected_jobs_uses_overlap_status_and_section():
    def job(jid, status, track="UP-1", section="NDLS-NZM", s=150, e=170):
        return {
            "job_id": jid, "status": status, "track_id": track,
            "schedule_start_minute": s, "schedule_end_minute": e,
            "block_candidate": {"section_id": section},
        }

    jobs = [
        job("sched", "scheduled"),
        job("notified", "notified"),
        job("running", "in_progress"),
        job("reported", "reported", s=None, e=None),
        job("adjacent", "notified", s=200, e=230),       # starts exactly at the end
        job("other-section", "notified", section="NZM-FDB"),
        job("other-track", "notified", track="DOWN-1"),
    ]
    hits = {h["job_id"]: h["impact"] for h in affected_jobs(jobs, [record()], OPTIMIZATION_HORIZON_START)}
    assert hits == {
        "sched": "PROPOSAL_REPLAN",
        "notified": "RELEASE_REQUIRED",
        "running": "NOT_COMPLETED_REPORT_REQUIRED",
    }


# ----------------------------------------------------------------------
# Declaration validation
# ----------------------------------------------------------------------

_KW = dict(
    kind=DisruptionKind.TRACK_UNAVAILABLE, track_id="UP-1", section_id=None,
    starts_at=iso(60), ends_at=iso(120), reason="why",
    known_tracks=["UP-1", "DOWN-1"], known_sections=["NDLS-NZM"],
    horizon_start=OPTIMIZATION_HORIZON_START, horizon_minutes=2880,
)


def _bad(**changes):
    with pytest.raises(ValueError):
        validate_declaration(**{**_KW, **changes})


def test_declaration_validation_fails_closed():
    assert validate_declaration(**_KW) == (60, 120)
    _bad(track_id="SIDING-9")
    _bad(section_id="NOWHERE-HERE")
    _bad(kind=DisruptionKind.POSSESSION_CURTAILMENT, section_id=None)
    _bad(reason="   ")
    _bad(starts_at="2026-09-10T01:00:00")                    # naive
    _bad(starts_at=iso(120), ends_at=iso(60))                # reversed
    _bad(starts_at=iso(-500), ends_at=iso(-400))             # before the horizon
    _bad(starts_at=iso(3000), ends_at=iso(3100))             # after the horizon


# ----------------------------------------------------------------------
# Lifecycle scenario on the demo corridor
# ----------------------------------------------------------------------


@pytest.fixture()
def env(tmp_path):
    service = demo.build_demo_service(tmp_path / "demo" / "jobs.db")
    ids = demo.seed(service)
    ids[demo.LIVE_INTAKE_JOB.key] = demo.report(service, demo.LIVE_INTAKE_JOB)["job_id"]
    optimizer = JobOptimizationService(service)
    disruptions = DisruptionService(optimizer)
    optimizer.optimize_corridor(CORRIDOR, actor=ENGINEER)
    return service, optimizer, disruptions, ids


def stored(service, ids, key):
    return service.repository.get(ids[key])


def approve(service, ids, key):
    job = stored(service, ids, key)
    return service.approve_proposal(
        ids[key], AUTHORITY, expected_proposal_run_id=proposal_run_id_of(job)
    )


def declare_over(disruptions, job, *, kind=DisruptionKind.POSSESSION_CURTAILMENT, pad=5):
    section = job["block_candidate"]["section_id"]
    return disruptions.register(
        CORRIDOR, ENGINEER, kind=kind, track_id=job["track_id"], section_id=section,
        starts_at=iso(job["schedule_start_minute"] - pad),
        ends_at=iso(job["schedule_end_minute"] + pad),
        reason="Simulated: overhead line fault",
    )


def run_count(service):
    with closing(sqlite3.connect(service.repository.db_path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM optimization_runs").fetchone()[0]


def test_disruption_is_recorded_labelled_simulated_and_lists_its_impact(env):
    service, _, disruptions, ids = env
    approve(service, ids, demo.APPROVE_TARGET)
    approved = stored(service, ids, demo.APPROVE_TARGET)

    view = declare_over(disruptions, approved)

    assert view["basis"] == "SIMULATED_DECLARED"
    assert view["status"] == "ACTIVE"
    assert view["declared_by"] == "ENGINEER-003"
    impact = {a["job_id"]: a["impact"] for a in view["affected_jobs"]}
    assert impact[ids[demo.APPROVE_TARGET]] == "RELEASE_REQUIRED"
    assert view["blocking_recovery"] == [ids[demo.APPROVE_TARGET]]
    assert disruptions.list_disruptions(CORRIDOR, AUTHORITY)[0]["disruption_id"] == view["disruption_id"]


def test_recovery_is_refused_before_any_solve_while_approved_work_is_hit(env):
    service, optimizer, disruptions, ids = env
    approve(service, ids, demo.APPROVE_TARGET)
    approved = stored(service, ids, demo.APPROVE_TARGET)
    scheduled_before = {
        jid: service.repository.get(jid)["status"] for jid in ids.values()
    }
    view = declare_over(disruptions, approved)
    runs_before = run_count(service)

    with pytest.raises(RecoveryBlockedError) as refused:
        disruptions.recover(CORRIDOR, view["disruption_id"], ENGINEER)

    assert [b["job_id"] for b in refused.value.blocking] == [ids[demo.APPROVE_TARGET]]
    assert "release" in refused.value.blocking[0]["required_action"].lower()
    # No solver run, nothing written or withdrawn, the commitment is intact.
    assert run_count(service) == runs_before
    assert {
        jid: service.repository.get(jid)["status"] for jid in ids.values()
    } == scheduled_before
    assert stored(service, ids, demo.APPROVE_TARGET)["status"] == "notified"
    # A plain optimization is refused for the same reason.
    with pytest.raises(RecoveryBlockedError):
        optimizer.optimize_corridor(CORRIDOR, actor=ENGINEER)
    # The refusal is part of the disruption's audit trail.
    kinds = [e["event_type"] for e in disruptions.get(CORRIDOR, view["disruption_id"], AUTHORITY)["events"]]
    assert kinds[0] == "REGISTERED" and "RECOVERY_REFUSED" in kinds


def test_release_then_recover_replans_outside_the_disruption(env):
    service, _, disruptions, ids = env
    approve(service, ids, demo.APPROVE_TARGET)
    approved = stored(service, ids, demo.APPROVE_TARGET)
    view = declare_over(disruptions, approved)
    d_start = approved["schedule_start_minute"] - 5
    d_end = approved["schedule_end_minute"] + 5
    track = approved["track_id"]
    section = approved["block_candidate"]["section_id"]

    service.release_committed_block(
        ids[demo.APPROVE_TARGET], AUTHORITY,
        expected_proposal_run_id=proposal_run_id_of(approved),
        reason="Disruption: line fault on this span",
    )

    result = disruptions.recover(CORRIDOR, view["disruption_id"], ENGINEER)

    assert result["optimization"]["solver_status"] in ("OPTIMAL", "FEASIBLE")
    assert view["disruption_id"] in result["active_disruption_ids"]

    # Every placement, whatever its status, avoids the disrupted span.
    for jid in ids.values():
        job = service.repository.get(jid)
        if job["schedule_start_minute"] is None:
            continue
        if job["track_id"] == track and job["block_candidate"]["section_id"] == section:
            assert (
                job["schedule_end_minute"] <= d_start
                or job["schedule_start_minute"] >= d_end
            ), job["job_id"]

    counts = result["comparison"]["counts"]
    assert sum(counts.values()) >= 1
    # The run records the disruption in its provenance and is linked to it.
    run_id = result["optimization"]["optimization_run_id"]
    assert run_id in result["disruption"]["recovery_runs"]
    with closing(sqlite3.connect(service.repository.db_path)) as conn:
        snapshot = conn.execute(
            "SELECT provenance_snapshot_json FROM optimization_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
    assert view["disruption_id"] in snapshot and "SIMULATED_DECLARED" in snapshot
    with closing(sqlite3.connect(service.repository.db_path)) as conn:
        triggers = [
            row[0]
            for row in conn.execute(
                "SELECT metadata_json FROM job_events "
                "WHERE event_type = 'OPTIMIZATION_REQUESTED' AND optimization_run_id = ?",
                (run_id,),
            )
        ]
    assert triggers and all('"trigger": "recovery"' in t for t in triggers)


def test_unaffected_committed_blocks_stay_pinned_through_recovery(env):
    service, _, disruptions, ids = env
    # Approve one job, then disrupt a DIFFERENT track/section pair than it uses.
    approve(service, ids, demo.APPROVE_TARGET)
    approved = stored(service, ids, demo.APPROVE_TARGET)
    other = next(
        job for job in (service.repository.get(j) for j in ids.values())
        if job["job_id"] != approved["job_id"] and job["schedule_start_minute"] is not None
        and (job["track_id"], job["block_candidate"]["section_id"])
        != (approved["track_id"], approved["block_candidate"]["section_id"])
    )
    view = declare_over(disruptions, other)
    assert view["blocking_recovery"] == []

    result = disruptions.recover(CORRIDOR, view["disruption_id"], ENGINEER)

    after = stored(service, ids, demo.APPROVE_TARGET)
    assert after["status"] == "notified"
    assert (after["schedule_start_minute"], after["schedule_end_minute"]) == (
        approved["schedule_start_minute"], approved["schedule_end_minute"]
    )
    assert ids[demo.APPROVE_TARGET] in result["comparison"]["unchanged"]


def test_a_proposal_inside_a_disruption_cannot_be_approved(env):
    service, _, disruptions, ids = env
    target = stored(service, ids, demo.APPROVE_TARGET)
    assert target["status"] == "scheduled"
    declare_over(disruptions, target)

    with pytest.raises(DisruptedPlacementError):
        approve(service, ids, demo.APPROVE_TARGET)

    # Refused, nothing changed, and the refusal is in the job's history.
    assert stored(service, ids, demo.APPROVE_TARGET)["status"] == "scheduled"
    types = [x.event.event_type.value for x in service.history.list_for_job(ids[demo.APPROVE_TARGET])]
    assert types[-1] == "TRANSITION_REJECTED"


def test_clearing_ends_the_effect_and_recovery_of_a_cleared_disruption_is_refused(env):
    service, optimizer, disruptions, ids = env
    target = stored(service, ids, demo.APPROVE_TARGET)
    view = declare_over(disruptions, target)

    cleared = disruptions.clear(CORRIDOR, view["disruption_id"], ENGINEER, reason="Line fault repaired")
    assert cleared["status"] == "CLEARED" and cleared["clear_reason"] == "Line fault repaired"

    with pytest.raises(DisruptionStateError):
        disruptions.clear(CORRIDOR, view["disruption_id"], ENGINEER, reason="again")
    with pytest.raises(DisruptionStateError):
        disruptions.recover(CORRIDOR, view["disruption_id"], ENGINEER)

    # A normal optimization works again and approval is no longer blocked.
    optimizer.optimize_corridor(CORRIDOR, actor=ENGINEER)
    approve(service, ids, demo.APPROVE_TARGET)
    assert stored(service, ids, demo.APPROVE_TARGET)["status"] == "notified"


def test_disruptions_removing_every_window_fail_closed(env):
    service, optimizer, disruptions, ids = env
    for track in [t.track_id for t in service.corridor.tracks]:
        disruptions.register(
            CORRIDOR, ENGINEER, kind=DisruptionKind.TRACK_UNAVAILABLE, track_id=track,
            section_id=None, starts_at=iso(-60), ends_at=iso(service.horizon_minutes + 60),
            reason="Simulated: full closure",
        )

    with pytest.raises(AllPossessionRemovedError):
        optimizer.optimize_corridor(CORRIDOR, actor=ENGINEER)

    # Nothing was scheduled by the refused run: no placement can exist
    # without a possession window.
    for jid in ids.values():
        assert service.repository.get(jid)["status"] in ("reported",)


def test_unknown_disruption_and_wrong_corridor(env):
    _, _, disruptions, _ = env
    with pytest.raises(DisruptionNotFoundError):
        disruptions.get(CORRIDOR, "DIS-NOPE", ENGINEER)
    with pytest.raises(KeyError):
        disruptions.list_disruptions("CORR-OTHER", ENGINEER)


def test_disruption_events_are_append_only(env):
    service, _, disruptions, ids = env
    view = declare_over(disruptions, stored(service, ids, demo.APPROVE_TARGET))

    with closing(sqlite3.connect(service.repository.db_path)) as conn:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("UPDATE disruption_events SET actor_id = 'x'")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("DELETE FROM disruption_events")
    assert disruptions.get(CORRIDOR, view["disruption_id"], ENGINEER)["declared_by"] == "ENGINEER-003"


def test_a_database_that_predates_the_table_still_opens(tmp_path):
    # Additive schema: CREATE TABLE IF NOT EXISTS on an existing jobs.db.
    service = demo.build_demo_service(tmp_path / "old" / "jobs.db")
    with closing(sqlite3.connect(service.repository.db_path)) as conn:
        conn.execute("DROP TRIGGER IF EXISTS disruption_events_no_delete")
        conn.execute("DROP TRIGGER IF EXISTS disruption_events_no_update")
        conn.execute("DROP TABLE IF EXISTS disruption_events")
        conn.commit()
    optimizer = JobOptimizationService(service)  # re-creates it
    assert DisruptionService(optimizer).list_disruptions(CORRIDOR, ENGINEER) == []


# ----------------------------------------------------------------------
# Authorization
# ----------------------------------------------------------------------


def test_role_table_grants():
    assert role_permits(ActorRole.ENGINEER, JobAction.REGISTER_DISRUPTION)
    assert role_permits(ActorRole.ENGINEER, JobAction.CLEAR_DISRUPTION)
    assert role_permits(ActorRole.ENGINEER, JobAction.READ_DISRUPTION)
    assert role_permits(ActorRole.AUTHORITY, JobAction.READ_DISRUPTION)
    for role in (ActorRole.WORKER, ActorRole.AUTHORITY, ActorRole.ADMIN):
        assert not role_permits(role, JobAction.REGISTER_DISRUPTION)
        assert not role_permits(role, JobAction.CLEAR_DISRUPTION)
    assert not role_permits(ActorRole.WORKER, JobAction.READ_DISRUPTION)


def test_enforcing_policy_gates_every_disruption_call(tmp_path):
    from backend.tests.slice10_1d_helpers import (
        CORRIDOR as CORR3, TRACK, build_service, fully_scoped,
    )

    service = build_service(tmp_path, authorization=fully_scoped())
    disruptions = DisruptionService(JobOptimizationService(service))
    kw = dict(
        kind=DisruptionKind.TRACK_UNAVAILABLE, track_id=TRACK, section_id=None,
        starts_at=iso(60), ends_at=iso(120), reason="Simulated",
    )

    for denied in (WORKER, AUTHORITY):
        with pytest.raises(AuthorizationDenied):
            disruptions.register(CORR3, denied, **kw)
    with pytest.raises(AuthorizationDenied):
        disruptions.register(CORR3, None, **kw)

    view = disruptions.register(CORR3, ENGINEER, **kw)
    did = view["disruption_id"]

    assert disruptions.get(CORR3, did, AUTHORITY)["disruption_id"] == did
    with pytest.raises(AuthorizationDenied):
        disruptions.get(CORR3, did, WORKER)
    for denied in (WORKER, AUTHORITY):
        with pytest.raises(AuthorizationDenied):
            disruptions.clear(CORR3, did, denied, reason="x")
        with pytest.raises(AuthorizationDenied):
            disruptions.recover(CORR3, did, denied)

    # Denied callers changed nothing.
    assert disruptions.get(CORR3, did, ENGINEER)["status"] == "ACTIVE"


# ----------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------


def test_http_surface(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.app.api.main import app
    from backend.app.jobs import router as jobs_router

    service = demo.build_demo_service(tmp_path / "http" / "jobs.db")
    demo.seed(service)
    optimizer = JobOptimizationService(service)
    monkeypatch.setattr(jobs_router, "service", service)
    monkeypatch.setattr(jobs_router, "optimization_service", optimizer)
    monkeypatch.setattr(jobs_router, "disruption_service", DisruptionService(optimizer))

    client = TestClient(app)
    eng = {"X-Actor-Id": "ENGINEER-003", "X-Actor-Role": "ENGINEER"}
    auth = {"X-Actor-Id": "AUTHORITY-017", "X-Actor-Role": "AUTHORITY"}
    base = f"/v1/corridors/{CORRIDOR}/disruptions"
    body = {
        "kind": "TRACK_UNAVAILABLE", "track_id": "UP-1", "section_id": "NDLS-NZM",
        "starts_at": iso(0), "ends_at": iso(60), "reason": "Simulated",
    }

    # Demo mode runs an UNENFORCED policy over HTTP (README, trust model):
    # role gating is proven at service level under an enforcing policy
    # (test_enforcing_policy_gates_every_disruption_call), not here.
    created = client.post(base, json=body, headers=eng)
    assert created.status_code == 201, created.text
    did = created.json()["disruption_id"]
    assert created.json()["basis"] == "SIMULATED_DECLARED"

    assert client.get(base, headers=auth).json()["items"][0]["disruption_id"] == did
    assert client.get(f"{base}/DIS-NOPE", headers=eng).json()["code"] == "DISRUPTION_NOT_FOUND"
    assert client.post(base, json={**body, "track_id": "NOPE"}, headers=eng).status_code == 400

    recovered = client.post(f"{base}/{did}/recover", headers=eng)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["optimization"]["solver_status"] in ("OPTIMAL", "FEASIBLE")

    assert client.post(f"{base}/{did}/clear", json={"reason": "done"}, headers=eng).status_code == 200
    again = client.post(f"{base}/{did}/recover", headers=eng)
    assert again.status_code == 409 and again.json()["code"] == "DISRUPTION_STATE_CONFLICT"


# ----------------------------------------------------------------------
# The demo rehearsal (scripts/seed_demo.py --verify runs the same code)
# ----------------------------------------------------------------------


def test_demo_disruption_rehearsal_satisfies_every_invariant(tmp_path):
    service = demo.build_demo_service(tmp_path / "rehearsal" / "jobs.db")
    rehearsal = demo.rehearse_disruption(service, demo.seed(service))

    assert demo.check_disruption_invariants(service, rehearsal) == []
    # The forced move is reported against where the block WAS, not as new work.
    moved = {m["job_id"]: m for m in rehearsal.recovery["comparison"]["moved"]}
    approved = rehearsal.ids[demo.APPROVE_TARGET]
    assert approved in moved
    assert "BLOCK_RELEASED" in moved[approved]["before"]["status"]
