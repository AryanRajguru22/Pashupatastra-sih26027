"""Sprint 3 Slice 8, item 4: duplicate detection AND idempotency.

TWO DIFFERENT PROBLEMS
  duplicate detection - two workers, possibly the same defect. The second
      job is ALWAYS created; it is only flagged. Nothing is deleted,
      merged, rejected or altered.
  idempotency - one request sent twice (retry). No second job is created.
"""

from __future__ import annotations

import json
import random
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.api.main import app
from backend.app.identity.actor import ActorRole, human_actor
from backend.app.jobs import service as service_module
from backend.app.jobs.duplicate_detection import (
    DEFAULT_DUPLICATE_MAX_GAP_M,
    DEFAULT_DUPLICATE_WINDOW,
    DUPLICATE_RULE_VERSION,
    DuplicatePolicy,
    DuplicateProbe,
    find_duplicate_candidates,
)
from backend.app.jobs.events import JobEventType, event_timestamp
from backend.app.jobs.idempotency import (
    IDEMPOTENCY_EVENT_PREFIX,
    IdempotencyKeyConflictError,
    IdempotencyKeyError,
    idempotency_event_id,
)
from backend.app.jobs.repository import JobRepository
from backend.app.jobs.router import service as router_service
from backend.tests.execution_helpers import (
    complete_execution_body,
    start_execution_body,
)
from backend.tests.slice8_helpers import (
    OTHER_WORKER,
    WORKER,
    created_event,
    job_count,
    make_asset,
    make_request,
    make_service,
)


T0 = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)
ASSET = "AST-T1-OHE-001"


def probe(**overrides):
    fields = dict(
        track_id="T1",
        section_id="A-B",
        asset_id=ASSET,
        work_type="BALLAST_TAMPING",
        distance_start_m=1_000.0,
        distance_end_m=1_400.0,
        created_at=T0,
    )
    fields.update(overrides)
    return DuplicateProbe(**fields)


def row(
    job_id="JOB-0000000001",
    *,
    created_at=T0 - timedelta(hours=1),
    start=1_000.0,
    end=1_400.0,
    track="T1",
    work_type="BALLAST_TAMPING",
    section="A-B",
    asset=ASSET,
    status="reported",
):
    return {
        "job_id": job_id,
        "track_id": track,
        "work_type": work_type,
        "distance_start": start,
        "distance_end": end,
        "status": status,
        "created_at": created_at.isoformat(timespec="microseconds")
        if isinstance(created_at, datetime)
        else created_at,
        "block_candidate": {"section_id": section, "asset_id": asset},
    }


def assess(rows, policy=None, verify=lambda r: None, **probe_overrides):
    return find_duplicate_candidates(
        probe(**probe_overrides),
        rows,
        policy or DuplicatePolicy(),
        verify_asset=verify,
    )


def ids(assessment):
    return [c.job_id for c in assessment.candidates]


# ======================================================================
# A. DUPLICATE DETECTION
# ======================================================================


def test_thresholds_are_explicit_policy_values():
    assert DEFAULT_DUPLICATE_MAX_GAP_M == 500.0
    assert DEFAULT_DUPLICATE_WINDOW == timedelta(hours=72)
    assert DuplicatePolicy().to_dict() == {"max_gap_m": 500.0, "window_hours": 72.0}
    assert DUPLICATE_RULE_VERSION == "deterministic-proximity-1.0"


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf"), True, "500"])
def test_policy_rejects_invalid_gap(bad):
    with pytest.raises(ValueError, match="max_gap_m"):
        DuplicatePolicy(max_gap_m=bad)


def test_policy_rejects_invalid_window():
    with pytest.raises(ValueError, match="window"):
        DuplicatePolicy(window=timedelta(hours=-1))

    with pytest.raises(ValueError, match="window"):
        DuplicatePolicy(window=72)


def test_no_candidate():
    result = assess([])

    assert ids(result) == []
    assert result.possible_duplicate is False
    assert result.to_metadata()["candidate_jobs"] == []


def test_one_candidate_with_its_signals():
    result = assess([row("JOB-A")])

    assert ids(result) == ["JOB-A"]
    assert result.possible_duplicate is True

    candidate = result.candidates[0]
    assert set(candidate.signals) == {
        "SAME_TRACK",
        "SAME_SECTION",
        "SAME_ASSET",
        "SAME_WORK_TYPE",
        "SPAN_NEAR",
        "TIME_NEAR",
    }
    assert candidate.span_gap_m == 0.0
    assert candidate.minutes_apart == 60.0


def test_multiple_candidates_are_ordered_oldest_first_then_by_job_id():
    rows = [
        row("JOB-C", created_at=T0 - timedelta(hours=1)),
        row("JOB-B", created_at=T0 - timedelta(hours=5)),
        row("JOB-A", created_at=T0 - timedelta(hours=1)),  # same time as JOB-C
        row("JOB-D", created_at=T0 - timedelta(hours=30)),
    ]

    assert ids(assess(rows)) == ["JOB-D", "JOB-B", "JOB-A", "JOB-C"]


def test_ordering_is_independent_of_input_order():
    rows = [
        row(f"JOB-{i}", created_at=T0 - timedelta(minutes=17 * i)) for i in range(8)
    ]
    expected = ids(assess(rows))

    rng = random.Random(3)
    for _ in range(10):
        shuffled = rows[:]
        rng.shuffle(shuffled)
        assert ids(assess(shuffled)) == expected


# ---- the temporal boundary --------------------------------------------


def test_exactly_at_the_temporal_threshold_is_a_candidate():
    at_edge = T0 - DEFAULT_DUPLICATE_WINDOW

    assert ids(assess([row("JOB-EDGE", created_at=at_edge)])) == ["JOB-EDGE"]
    # ...and the same distance in the other direction.
    assert ids(
        assess([row("JOB-EDGE", created_at=T0 + DEFAULT_DUPLICATE_WINDOW)])
    ) == ["JOB-EDGE"]


def test_one_microsecond_beyond_the_temporal_threshold_is_not():
    just_past = T0 - DEFAULT_DUPLICATE_WINDOW - timedelta(microseconds=1)

    assert ids(assess([row("JOB-OLD", created_at=just_past)])) == []


# ---- the spatial boundary ---------------------------------------------


def test_exactly_at_the_spatial_threshold_is_a_candidate():
    # New span 1000-1400. Existing 1900-2300 leaves a 500 m gap.
    result = assess([row("JOB-EDGE", start=1_900.0, end=2_300.0)])

    assert ids(result) == ["JOB-EDGE"]
    assert result.candidates[0].span_gap_m == 500.0

    # Same on the other side: existing 100-500 ends 500 m before 1000.
    assert ids(assess([row("JOB-EDGE", start=100.0, end=500.0)])) == ["JOB-EDGE"]


def test_just_beyond_the_spatial_threshold_is_not():
    assert ids(assess([row("JOB-FAR", start=1_900.001, end=2_300.0)])) == []
    assert ids(assess([row("JOB-FAR", start=100.0, end=499.999)])) == []


def test_overlapping_and_touching_spans_have_zero_gap():
    assert assess([row("JOB-O", start=1_200.0, end=1_600.0)]).candidates[0].span_gap_m == 0.0
    assert assess([row("JOB-T", start=1_400.0, end=1_800.0)]).candidates[0].span_gap_m == 0.0


def test_the_policy_thresholds_actually_drive_the_rule():
    near = row("JOB-N", start=1_900.0, end=2_300.0)  # 500 m gap

    assert ids(assess([near], DuplicatePolicy(max_gap_m=499.0))) == []
    assert ids(assess([near], DuplicatePolicy(max_gap_m=500.0))) == ["JOB-N"]

    old = row("JOB-Y", created_at=T0 - timedelta(hours=10))
    assert ids(assess([old], DuplicatePolicy(window=timedelta(hours=9)))) == []
    assert ids(assess([old], DuplicatePolicy(window=timedelta(hours=10)))) == ["JOB-Y"]


# ---- every signal is required -----------------------------------------


@pytest.mark.parametrize(
    "difference",
    [
        dict(asset="AST-T1-OHE-999"),  # different asset
        dict(work_type="TRACK_RENEWAL"),  # different defect / work type
        dict(section="B-C"),  # different section
        dict(track="T2"),  # different track
        dict(status="completed"),  # terminal: not a duplicate of new work
    ],
)
def test_a_single_differing_signal_prevents_a_flag(difference):
    assert ids(assess([row("JOB-X", **difference)])) == []


@pytest.mark.parametrize("status", ["reported", "scheduled", "notified", "in_progress"])
def test_every_non_terminal_status_is_comparable(status):
    assert ids(assess([row("JOB-S", status=status)])) == ["JOB-S"]


def test_an_unverifiable_asset_reference_is_skipped_and_reported_not_trusted():
    rows = [row("JOB-STALE"), row("JOB-OK")]

    result = assess(
        rows,
        verify=lambda r: "asset reference unverified: stale" if r["job_id"] == "JOB-STALE" else None,
    )

    assert ids(result) == ["JOB-OK"]
    assert result.skipped == (("JOB-STALE", "asset reference unverified: stale"),)
    assert result.to_metadata()["skipped"] == [
        {"job_id": "JOB-STALE", "reason": "asset reference unverified: stale"}
    ]


def test_an_unrelated_jobs_bad_reference_is_never_even_examined():
    called = []

    result = assess(
        [row("JOB-X", track="T2")],
        verify=lambda r: called.append(r["job_id"]) or "bad",
    )

    assert ids(result) == [] and result.skipped == () and called == []


@pytest.mark.parametrize("bad", [None, "not-a-time", "2026-09-10T12:00:00"])  # last is naive
def test_an_unusable_created_at_is_skipped_and_reported(bad):
    result = assess([row("JOB-BAD", created_at=bad)])

    assert ids(result) == []
    assert result.skipped[0][0] == "JOB-BAD"


def test_the_assessment_is_plain_json():
    rendered = assess([row("JOB-A"), row("JOB-B")]).to_metadata()

    assert json.loads(json.dumps(rendered)) == rendered
    assert rendered["rule_version"] == DUPLICATE_RULE_VERSION
    assert rendered["possible_duplicate"] is True
    assert rendered["candidate_jobs"] == ["JOB-A", "JOB-B"]
    assert rendered["thresholds"] == {"max_gap_m": 500.0, "window_hours": 72.0}


# ---- through create_job: advisory, never blocking ---------------------


def _assets():
    return [make_asset(ASSET, 1.2), make_asset("AST-T1-OHE-002", 30.0)]


def test_a_possible_duplicate_is_still_created_and_the_first_is_untouched(tmp_path):
    service = make_service(tmp_path, _assets())

    first = service.create_job(make_request(1_000, 1_400), actor=WORKER)
    first_events_before = [s.event.event_id for s in service.job_history(first["job_id"])]
    first_row_before = service.repository.get(first["job_id"])

    second = service.create_job(make_request(1_100, 1_500), actor=OTHER_WORKER)

    # created - not merged, rejected or replaced
    assert second["job_id"] != first["job_id"]
    assert second["status"] == "reported"
    assert job_count(service) == 2

    detection = second["block_candidate"]["metadata"]["duplicate_detection"]
    assert detection["possible_duplicate"] is True
    assert detection["candidate_jobs"] == [first["job_id"]]
    assert detection["candidates"][0]["signals"] == [
        "SAME_TRACK",
        "SAME_SECTION",
        "SAME_ASSET",
        "SAME_WORK_TYPE",
        "SPAN_NEAR",
        "TIME_NEAR",
    ]

    # the first worker's report is byte-for-byte what it was
    assert service.repository.get(first["job_id"]) == first_row_before
    assert [
        s.event.event_id for s in service.job_history(first["job_id"])
    ] == first_events_before
    assert (
        first["block_candidate"]["metadata"]["duplicate_detection"]["possible_duplicate"]
        is False
    )

    # recorded immutably in JOB_CREATED
    assert created_event(service, second["job_id"]).metadata["duplicate_detection"] == detection


def test_multiple_candidates_through_the_service_are_ordered(tmp_path):
    service = make_service(tmp_path, _assets())

    a = service.create_job(make_request(1_000, 1_400), actor=WORKER)
    b = service.create_job(make_request(1_050, 1_450), actor=WORKER)
    c = service.create_job(make_request(1_100, 1_500), actor=WORKER)

    detection = c["block_candidate"]["metadata"]["duplicate_detection"]

    assert detection["candidate_jobs"] == [a["job_id"], b["job_id"]]
    assert job_count(service) == 3


def test_different_work_type_or_distant_span_is_not_flagged(tmp_path):
    service = make_service(tmp_path, _assets())
    service.create_job(make_request(1_000, 1_400), actor=WORKER)

    other_type = service.create_job(
        make_request(1_000, 1_400, job_type="TRACK_RENEWAL"), actor=WORKER
    )
    far = service.create_job(make_request(5_000, 5_400), actor=WORKER)

    for job in (other_type, far):
        assert job["block_candidate"]["metadata"]["duplicate_detection"]["possible_duplicate"] is False


def test_a_different_section_is_not_flagged(tmp_path):
    service = make_service(
        tmp_path,
        [make_asset(ASSET, 1.2), make_asset("AST-T1-OHE-002", 50.6)],
    )
    service.create_job(make_request(1_000, 1_400), actor=WORKER)  # A-B

    other_section = service.create_job(make_request(50_400, 50_800), actor=WORKER)  # B-C

    assert other_section["block_candidate"]["section_id"] == "B-C"
    assert (
        other_section["block_candidate"]["metadata"]["duplicate_detection"]["possible_duplicate"]
        is False
    )


@pytest.mark.parametrize(
    "offset, flagged",
    [
        (timedelta(0), True),
        (DEFAULT_DUPLICATE_WINDOW, True),  # exactly at the boundary
        (DEFAULT_DUPLICATE_WINDOW + timedelta(microseconds=1), False),
    ],
)
def test_the_temporal_boundary_through_the_service(tmp_path, monkeypatch, offset, flagged):
    service = make_service(tmp_path, _assets())
    clock = {"now": T0}
    monkeypatch.setattr(
        service_module, "event_timestamp", lambda moment=None: event_timestamp(clock["now"])
    )

    first = service.create_job(make_request(1_000, 1_400), actor=WORKER)
    clock["now"] = T0 + offset
    second = service.create_job(make_request(1_000, 1_400), actor=OTHER_WORKER)

    detection = second["block_candidate"]["metadata"]["duplicate_detection"]
    assert detection["possible_duplicate"] is flagged
    assert detection["candidate_jobs"] == ([first["job_id"]] if flagged else [])
    assert job_count(service) == 2  # created either way


def test_a_stale_asset_reference_on_an_existing_job_is_skipped_and_reported(tmp_path):
    original = make_service(tmp_path, [make_asset(ASSET, 1.2)])
    first = original.create_job(make_request(1_000, 1_400), actor=WORKER)

    # Same database, but the asset set was regenerated: ASSET now names an
    # asset somewhere else. The stored reference can no longer be trusted.
    regenerated = make_service(
        tmp_path,
        [make_asset(ASSET, 1.2, asset_type="SIGNAL_POST")],
    )
    second = regenerated.create_job(make_request(1_000, 1_400), actor=OTHER_WORKER)

    detection = second["block_candidate"]["metadata"]["duplicate_detection"]
    assert detection["possible_duplicate"] is False
    assert detection["skipped"][0]["job_id"] == first["job_id"]
    assert "asset reference unverified" in detection["skipped"][0]["reason"]
    assert job_count(regenerated) == 2


def test_detection_adds_no_event_type_or_status_and_no_extra_events(tmp_path):
    service = make_service(tmp_path, _assets())
    first = service.create_job(make_request(1_000, 1_400), actor=WORKER)
    second = service.create_job(make_request(1_000, 1_400), actor=OTHER_WORKER)

    for job in (first, second):
        kinds = [s.event.event_type for s in service.job_history(job["job_id"])]
        assert kinds == [JobEventType.JOB_CREATED, JobEventType.JOB_SCORED]


# ======================================================================
# B. IDEMPOTENCY
# ======================================================================


def key() -> str:
    return f"key-{uuid.uuid4().hex}"


def test_the_same_request_and_key_creates_one_job(tmp_path):
    service = make_service(tmp_path, _assets())
    k = key()

    first = service.create_job_with_outcome(
        make_request(1_000, 1_400, idempotency_key=k), actor=WORKER
    )
    second = service.create_job_with_outcome(
        make_request(1_000, 1_400, idempotency_key=k), actor=WORKER
    )

    assert first.replayed is False
    assert second.replayed is True
    assert second.job["job_id"] == first.job["job_id"]
    assert job_count(service) == 1

    kinds = [s.event.event_type for s in service.job_history(first.job["job_id"])]
    assert kinds == [JobEventType.JOB_CREATED, JobEventType.JOB_SCORED]  # no second creation


def test_the_key_is_recorded_on_job_created_with_a_deterministic_event_id(tmp_path):
    service = make_service(tmp_path, _assets())
    k = key()

    job = service.create_job(make_request(idempotency_key=k), actor=WORKER)
    event = created_event(service, job["job_id"])

    assert event.event_id == idempotency_event_id(WORKER.actor_id, k)
    assert event.event_id.startswith(IDEMPOTENCY_EVENT_PREFIX)
    assert event.metadata["idempotency"]["key"] == k
    assert len(event.metadata["idempotency"]["request_fingerprint"]) == 64
    # the raw key is not embedded in the id
    assert k not in event.event_id


def test_the_event_id_separates_actors_and_keys():
    assert idempotency_event_id("a", "b:c") != idempotency_event_id("a:b", "c")
    assert idempotency_event_id("W1", "k") != idempotency_event_id("W2", "k")
    assert idempotency_event_id("W1", "k") == idempotency_event_id("W1", "k")


def test_conflicting_reuse_of_a_key_fails_closed(tmp_path):
    service = make_service(tmp_path, _assets())
    k = key()

    first = service.create_job(make_request(1_000, 1_400, idempotency_key=k), actor=WORKER)

    for different in (
        make_request(1_000, 1_400, description="something else", idempotency_key=k),
        make_request(2_000, 2_400, idempotency_key=k),
        make_request(1_000, 1_400, job_type="TRACK_RENEWAL", idempotency_key=k),
    ):
        with pytest.raises(IdempotencyKeyConflictError, match="different request"):
            service.create_job(different, actor=WORKER)

    assert job_count(service) == 1
    assert service.repository.get(first["job_id"]) is not None


def test_a_key_is_scoped_to_the_actor(tmp_path):
    service = make_service(tmp_path, _assets())
    k = key()

    mine = service.create_job(make_request(idempotency_key=k), actor=WORKER)
    theirs = service.create_job(make_request(idempotency_key=k), actor=OTHER_WORKER)

    assert mine["job_id"] != theirs["job_id"]
    assert job_count(service) == 2


def test_without_a_key_behaviour_is_unchanged(tmp_path):
    service = make_service(tmp_path, _assets())

    a = service.create_job(make_request(), actor=WORKER)
    b = service.create_job(make_request(), actor=WORKER)

    assert a["job_id"] != b["job_id"]
    assert job_count(service) == 2
    assert created_event(service, a["job_id"]).event_id.startswith("EVT-")
    assert not created_event(service, a["job_id"]).event_id.startswith(IDEMPOTENCY_EVENT_PREFIX)
    assert "idempotency" not in created_event(service, a["job_id"]).metadata


def test_a_key_without_an_identified_actor_is_refused(tmp_path):
    service = make_service(tmp_path, _assets())

    with pytest.raises(IdempotencyKeyError, match="requires actor headers") as caught:
        service.create_job(make_request(idempotency_key=key()))

    assert not isinstance(caught.value, IdempotencyKeyConflictError)
    assert job_count(service) == 0


def test_a_retry_after_a_service_restart_still_replays(tmp_path):
    k = key()
    before = make_service(tmp_path, _assets())
    first = before.create_job(make_request(idempotency_key=k), actor=WORKER)

    after = make_service(tmp_path, _assets())  # new process-equivalent, same file
    replay = after.create_job_with_outcome(make_request(idempotency_key=k), actor=WORKER)

    assert replay.replayed is True
    assert replay.job["job_id"] == first["job_id"]
    assert job_count(after) == 1


def test_a_replay_reports_the_jobs_current_state(tmp_path):
    service = make_service(tmp_path, _assets())
    k = key()
    first = service.create_job(make_request(idempotency_key=k), actor=WORKER)

    with closing(sqlite3.connect(service.repository.db_path)) as conn, conn:
        conn.execute(
            "UPDATE maintenance_jobs SET priority_score = 0.123 WHERE job_id = ?",
            (first["job_id"],),
        )

    replay = service.create_job(make_request(idempotency_key=k), actor=WORKER)

    assert replay["priority_score"] == 0.123


def test_a_key_whose_job_is_gone_is_spent_not_reused(tmp_path):
    service = make_service(tmp_path, _assets())
    k = key()
    first = service.create_job(make_request(idempotency_key=k), actor=WORKER)

    with closing(sqlite3.connect(service.repository.db_path)) as conn, conn:
        conn.execute("DELETE FROM maintenance_jobs WHERE job_id = ?", (first["job_id"],))

    with pytest.raises(IdempotencyKeyConflictError, match="no longer exists"):
        service.create_job(make_request(idempotency_key=k), actor=WORKER)

    assert job_count(service) == 0


# ---- concurrency ------------------------------------------------------


def test_losing_the_write_race_returns_the_winner_and_leaves_no_orphan(tmp_path, monkeypatch):
    """The early check misses (the winner commits just after it); the
    UNIQUE event_id then refuses the loser's write and rolls its job row
    back. Deterministic, no timing."""

    k = key()
    winner_service = make_service(tmp_path, _assets())
    winner = winner_service.create_job(make_request(idempotency_key=k), actor=WORKER)

    loser_service = make_service(tmp_path, _assets())
    real = loser_service._idempotent_replay
    calls = {"n": 0}

    def miss_first_time(*args):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(*args)

    monkeypatch.setattr(loser_service, "_idempotent_replay", miss_first_time)

    outcome = loser_service.create_job_with_outcome(
        make_request(idempotency_key=k), actor=WORKER
    )

    assert calls["n"] == 2  # the constraint, not the pre-check, decided it
    assert outcome.replayed is True
    assert outcome.job["job_id"] == winner["job_id"]
    assert job_count(loser_service) == 1  # the loser's job row was rolled back


def test_losing_the_race_with_a_different_request_is_a_conflict(tmp_path, monkeypatch):
    k = key()
    make_service(tmp_path, _assets()).create_job(
        make_request(1_000, 1_400, idempotency_key=k), actor=WORKER
    )

    loser = make_service(tmp_path, _assets())
    real = loser._idempotent_replay
    calls = {"n": 0}
    monkeypatch.setattr(
        loser,
        "_idempotent_replay",
        lambda *a: (calls.__setitem__("n", calls["n"] + 1) or None)
        if calls["n"] == 0
        else real(*a),
    )

    with pytest.raises(IdempotencyKeyConflictError):
        loser.create_job(make_request(2_000, 2_400, idempotency_key=k), actor=WORKER)

    assert job_count(loser) == 1


def _hammer(services, k, requests_per_service=3):
    barrier = threading.Barrier(len(services) * requests_per_service)

    def submit(service):
        barrier.wait()
        return service.create_job_with_outcome(
            make_request(idempotency_key=k), actor=WORKER
        )

    with ThreadPoolExecutor(max_workers=len(services) * requests_per_service) as pool:
        futures = [
            pool.submit(submit, service)
            for service in services
            for _ in range(requests_per_service)
        ]
        return [f.result() for f in futures]


def test_concurrent_identical_submissions_on_one_service_create_one_job(tmp_path):
    service = make_service(tmp_path, _assets())

    outcomes = _hammer([service], key(), requests_per_service=10)

    assert job_count(service) == 1
    assert len({o.job["job_id"] for o in outcomes}) == 1
    assert sum(1 for o in outcomes if not o.replayed) == 1


def test_concurrent_identical_submissions_across_connections_create_one_job(tmp_path):
    """Independent services = independent connections to one database
    file, the in-process stand-in for separate workers."""

    services = [make_service(tmp_path, _assets()) for _ in range(4)]

    outcomes = _hammer(services, key(), requests_per_service=3)

    assert job_count(services[0]) == 1
    assert len({o.job["job_id"] for o in outcomes}) == 1
    assert sum(1 for o in outcomes if not o.replayed) == 1


def test_concurrent_conflicting_submissions_yield_one_job_and_conflicts(tmp_path):
    services = [make_service(tmp_path, _assets()) for _ in range(3)]
    k = key()
    barrier = threading.Barrier(6)

    def submit(service, index):
        barrier.wait()
        try:
            return service.create_job_with_outcome(
                make_request(1_000, 1_400, description=f"variant {index % 2}", idempotency_key=k),
                actor=WORKER,
            )
        except IdempotencyKeyConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(
            pool.map(lambda i: submit(services[i % 3], i), range(6))
        )

    created = [r for r in results if not isinstance(r, Exception) and not r.replayed]
    assert len(created) == 1
    assert job_count(services[0]) == 1
    # every variant that is not the winner's is a conflict, never a second job
    winner_description = created[0].job["description"]
    for r in results:
        if isinstance(r, Exception):
            continue
        assert r.job["description"] == winner_description


_PROCESS_SCRIPT = """
import json, sys, time
from backend.app.identity.actor import ActorRole, human_actor
from backend.app.jobs.models import JobCreateRequest
from backend.app.jobs.service import JobService

key, fire_at = sys.argv[1], float(sys.argv[2])
service = JobService()
request = JobCreateRequest(
    track_id="UP-1", job_type="BALLAST_TAMPING",
    distance_start=1000.0, distance_end=1400.0,
    workers_min=2, workers_max=4, description="cross-process retry",
    idempotency_key=key,
)
while time.time() < fire_at:
    pass
out = service.create_job_with_outcome(request, human_actor("WORKER-042", ActorRole.WORKER))
print(json.dumps({"job_id": out.job["job_id"], "replayed": out.replayed}))
"""


def test_concurrent_identical_submissions_from_separate_processes_create_one_job(tmp_path):
    db = tmp_path / "shared.db"
    JobRepository(db)  # create the schema once, before the race

    root = Path(__file__).resolve().parents[2]
    env = {**__import__("os").environ, "PASHUPAT_JOBS_DB": str(db)}
    k = key()
    fire_at = time.time() + 6.0

    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _PROCESS_SCRIPT, k, str(fire_at)],
            cwd=root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(4)
    ]

    results = []
    for proc in procs:
        stdout, stderr = proc.communicate(timeout=120)
        assert proc.returncode == 0, stderr
        results.append(json.loads(stdout.strip().splitlines()[-1]))

    assert len({r["job_id"] for r in results}) == 1
    assert sum(1 for r in results if not r["replayed"]) == 1

    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM maintenance_jobs").fetchone()[0] == 1
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM job_events WHERE event_type = 'JOB_CREATED'"
            ).fetchone()[0]
            == 1
        )


# ======================================================================
# C. HTTP behaviour and the frozen v1 contract
# ======================================================================

client = TestClient(app)
WORKER_HEADERS = {"X-Actor-Id": "WORKER-042", "X-Actor-Role": "WORKER"}
OTHER_HEADERS = {"X-Actor-Id": "WORKER-777", "X-Actor-Role": "WORKER"}
ENGINEER_HEADERS = {"X-Actor-Id": "ENGINEER-003", "X-Actor-Role": "ENGINEER"}
AUTHORITY_HEADERS = {"X-Actor-Id": "AUTHORITY-017", "X-Actor-Role": "AUTHORITY"}


@pytest.fixture
def clean_jobs_table():
    def wipe():
        with closing(sqlite3.connect(router_service.repository.db_path)) as conn, conn:
            conn.execute("DELETE FROM maintenance_jobs")
            conn.commit()

    wipe()
    yield
    wipe()


def _body(**extra):
    return {
        "track_id": "UP-1",
        "job_type": "BALLAST_TAMPING",
        "distance_start": 1000.0,
        "distance_end": 1400.0,
        "workers_min": 2,
        "workers_max": 4,
        "description": "field-reported defect",
        **extra,
    }


def _listed():
    return client.get("/v1/jobs").json()["items"]


def test_http_replay_returns_the_same_job_with_a_replay_header(clean_jobs_table):
    k = key()

    first = client.post("/v1/jobs", json=_body(idempotency_key=k), headers=WORKER_HEADERS)
    second = client.post("/v1/jobs", json=_body(idempotency_key=k), headers=WORKER_HEADERS)

    assert first.status_code == second.status_code == 201
    assert "Idempotent-Replayed" not in first.headers
    assert second.headers["Idempotent-Replayed"] == "true"
    assert second.json()["job_id"] == first.json()["job_id"]
    assert len(_listed()) == 1


def test_http_conflicting_reuse_is_409_with_a_stable_code(clean_jobs_table):
    k = key()
    client.post("/v1/jobs", json=_body(idempotency_key=k), headers=WORKER_HEADERS)

    response = client.post(
        "/v1/jobs",
        json=_body(description="a different report", idempotency_key=k),
        headers=WORKER_HEADERS,
    )

    assert response.status_code == 409
    assert response.json()["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert len(_listed()) == 1


def test_http_a_key_without_actor_headers_is_a_400_not_a_conflict(clean_jobs_table):
    response = client.post("/v1/jobs", json=_body(idempotency_key=key()))

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_REQUEST"
    assert _listed() == []


def test_http_a_malformed_key_is_a_422(clean_jobs_table):
    for bad in ("", "has space", "x" * 129, "semi;colon"):
        response = client.post(
            "/v1/jobs", json=_body(idempotency_key=bad), headers=WORKER_HEADERS
        )
        assert response.status_code == 422, bad


def test_http_a_possible_duplicate_is_created_and_flagged(clean_jobs_table):
    first = client.post("/v1/jobs", json=_body(), headers=WORKER_HEADERS)
    second = client.post(
        "/v1/jobs",
        json=_body(distance_start=1100.0, distance_end=1500.0),
        headers=OTHER_HEADERS,
    )

    assert first.status_code == second.status_code == 201

    detection = second.json()["block_candidate"]["metadata"]["duplicate_detection"]
    assert detection["possible_duplicate"] is True
    assert detection["candidate_jobs"] == [first.json()["job_id"]]
    assert len(_listed()) == 2

    # the flag is on GET too, and the first job did not change
    fetched = client.get(f"/v1/jobs/{second.json()['job_id']}").json()
    assert fetched["block_candidate"]["metadata"]["duplicate_detection"] == detection
    assert client.get(f"/v1/jobs/{first.json()['job_id']}").json()["status"] == "reported"


def test_a_slice8_report_travels_the_whole_lifecycle_and_stays_synthetic(clean_jobs_table):
    """Regression across optimize -> approve -> start -> complete for a job
    created with every Slice 8 field: none of them disturbs the lifecycle,
    and a human field report does not promote any provenance axis."""

    section = router_service.registry.get(router_service.registry.section_ids()[0])

    created = client.post(
        "/v1/jobs",
        json=_body(
            idempotency_key=key(),
            field_location={
                "from_station_id": section.start_station_id,
                "toward_station_id": section.end_station_id,
                "offset_start_m": 1000.0,
                "offset_end_m": 1400.0,
            },
        ),
        headers=WORKER_HEADERS,
    )
    assert created.status_code == 201, created.text
    job_id = created.json()["job_id"]

    optimized = client.post(
        "/v1/corridors/CORRIDOR_A/optimize-jobs", headers=ENGINEER_HEADERS
    )
    assert optimized.status_code == 200, optimized.text
    assert optimized.json()["provenance"] == {
        "topology": "SYNTHETIC",
        "timetable": "SYNTHETIC",
        "asset_condition": "SYNTHETIC",
        "possession": "SYNTHETIC",
        "effective": "SYNTHETIC",
    }

    scheduled = client.get(f"/v1/jobs/{job_id}").json()
    assert scheduled["status"] == "scheduled"

    approved = client.post(
        f"/v1/jobs/{job_id}/proposal/approve",
        json={"expected_proposal_run_id": scheduled["proposal_run_id"]},
        headers=AUTHORITY_HEADERS,
    )
    assert approved.status_code == 200, approved.text
    notified = client.get(f"/v1/jobs/{job_id}").json()
    assert notified["status"] == "notified"

    started = client.post(
        f"/v1/jobs/{job_id}/execution/start",
        json=start_execution_body(notified),
        headers=WORKER_HEADERS,
    )
    assert started.status_code == 200, started.text

    running = client.get(f"/v1/jobs/{job_id}").json()
    completed = client.post(
        f"/v1/jobs/{job_id}/execution/complete",
        json=complete_execution_body(running, started.json()["execution"]["execution_id"]),
        headers=WORKER_HEADERS,
    )
    assert completed.status_code == 200, completed.text
    assert client.get(f"/v1/jobs/{job_id}").json()["status"] == "completed"
