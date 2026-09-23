"""Slice 10.1D.2: railway-scope filtering for collection reads.

Slice 10.1D gated ONE job (job_detail, job_history, ...): out of scope is
a refusal for a single-resource read. GET /v1/jobs and GET /v1/obligations
are COLLECTIONS: a page must neither refuse because it happens to contain
a job the caller may not see, nor hand that job over. This slice adds
per-row scope filtering to those two reads, reusing the existing
single-resource seam (_authorize_job_resource) as the per-row decision so
a collection can never disagree with GET /v1/jobs/{id} for the same
actor.

See docs/SLICE10_1D2_COLLECTION_SCOPE_ARCHITECTURE.md for the full design
this file exercises: the bounded scan, the cursor-safety invariants and
the "derive nothing for an out-of-scope row" ordering.

The corridor here is the three-section one from slice10_1d_helpers
(X-Y / Y-Z / Z-W): the deployment default corridor has a single section,
so "outside my scope" is unreachable on it.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.identity.actor import ActorRole
from backend.app.identity.authorization import (
    AuthorizationDenied,
    JobAction,
    UnenforcedPolicy,
)
from backend.app.jobs.events import event_timestamp
from backend.app.jobs.obligations import ObligationIntegrityError
from backend.app.jobs.repository import JobRepository
from backend.app.jobs.router import _decode_cursor, _encode_cursor
from backend.app.jobs.service import (
    COLLECTION_TIE_CEILING,
    CollectionScanLimitError,
    JobService,
    ScopedPage,
)
from backend.tests.slice10_1d_helpers import (
    ADMIN,
    AUTHORITY_ID,
    CORRIDOR,
    ENGINEER_ID,
    IN_XY,
    IN_YZ,
    IN_ZW,
    OTHER_CORRIDOR,
    S_XY,
    S_YZ,
    S_ZW,
    WORKER,
    WORKER_ID,
    assignment,
    build_service,
    directory,
    enforcing,
    fully_scoped,
    report,
)

A = JobAction

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _worker_scoped(*sections: str, corridor_id: str = CORRIDOR):
    """An enforcing policy that scopes ONLY the WORKER role."""

    return enforcing(
        assignment(WORKER_ID, ActorRole.WORKER, *sections, corridor_id=corridor_id)
    )


def _at(offset_seconds: float) -> str:
    """A deterministic, monotone created_at string for row fixtures."""

    return event_timestamp(BASE + timedelta(seconds=offset_seconds))


def _insert(
    repo: JobRepository,
    job_id: str,
    created_at: str,
    section_id: str | None,
    *,
    status: str = "reported",
    track_id: str = "T1",
) -> dict[str, Any]:
    """Insert a minimal job row directly, bypassing JobService.

    For pagination/budget/tie-group fixtures, where volume and precise
    (created_at, job_id, section) control matter and going through
    service.create_job (asset association, duplicate detection, event
    recording) would be slow and would add noise unrelated to what these
    tests check. Mirrors the direct-DB-manipulation style already used
    by test_slice10_1d_scope_enforcement.py.
    """

    block_candidate: dict[str, Any] = {}
    if section_id is not None:
        block_candidate["section_id"] = section_id

    return repo.create(
        {
            "job_id": job_id,
            "track_id": track_id,
            "work_type": "BALLAST_TAMPING",
            "distance_start": 0.0,
            "distance_end": 10.0,
            "workers_min": 2,
            "workers_max": 4,
            "description": "slice 10.1D.2 fixture",
            "status": status,
            "priority_score": 0.0,
            "risk_score": 0.0,
            "created_at": created_at,
            "block_candidate": block_candidate,
        }
    )


def _walk_jobs(
    service: JobService, *, limit: int, actor: Any = WORKER
) -> list[dict[str, Any]]:
    """Follow next_cursor (as a scan position) to the end. Fails the test
    if it does not terminate in a generous number of requests."""

    seen: list[dict[str, Any]] = []
    after = None

    for _ in range(500):
        page = service.jobs_page(limit=limit, after=after, actor=actor)
        seen.extend(page.items)

        if page.next_after is None:
            return seen

        after = page.next_after

    pytest.fail("jobs_page walk did not terminate within 500 requests")


# ----------------------------------------------------------------------
# 1. Basic row filtering
# ----------------------------------------------------------------------


def test_in_scope_job_appears(tmp_path: Path):
    service = build_service(tmp_path, authorization=_worker_scoped(S_XY))
    job = report(service, IN_XY)

    page = service.jobs_page(limit=10, actor=WORKER)

    assert [j["job_id"] for j in page.items] == [job["job_id"]]
    assert [j["job_id"] for j in service.list_jobs(actor=WORKER)] == [job["job_id"]]


def test_out_of_scope_job_is_hidden(tmp_path: Path):
    service = build_service(tmp_path, authorization=fully_scoped())
    inside = report(service, IN_XY, description="inside")
    outside = report(service, IN_ZW, description="outside")

    service.authorization = _worker_scoped(S_XY)

    page = service.jobs_page(limit=10, actor=WORKER)

    ids = [j["job_id"] for j in page.items]
    assert ids == [inside["job_id"]]
    assert outside["job_id"] not in ids
    assert outside["job_id"] not in [j["job_id"] for j in service.list_jobs(actor=WORKER)]


def test_unresolved_legacy_job_is_hidden(tmp_path: Path):
    """A job whose block_candidate has no section_id - the shape of the
    one legacy row in the live database - is UNRESOLVED and never a
    permit, exactly as job_detail already refuses it."""

    service = build_service(tmp_path, authorization=fully_scoped())
    legacy = _insert(service.repository, "JOB-LEGACY", _at(0), section_id=None)
    normal = report(service, IN_XY, description="normal")

    service.authorization = _worker_scoped(S_XY)

    page = service.jobs_page(limit=10, actor=WORKER)

    ids = [j["job_id"] for j in page.items]
    assert ids == [normal["job_id"]]
    assert legacy["job_id"] not in ids


def test_missing_scope_is_an_empty_page_not_an_error(tmp_path: Path):
    """An actor the directory has never heard of holds no scope - the
    absence of a grant, never a global one - and every row is skipped,
    but the request itself succeeds."""

    service = build_service(tmp_path, authorization=fully_scoped())
    report(service, IN_XY)
    report(service, IN_YZ)

    # A directory with no assignment at all for WORKER-042.
    service.authorization = enforcing()

    page = service.jobs_page(limit=10, actor=WORKER)

    assert page.items == []
    assert page.next_after is None


def test_a_multi_section_scope_returns_exactly_the_union(tmp_path: Path):
    service = build_service(tmp_path, authorization=fully_scoped())
    xy = report(service, IN_XY, description="xy")
    yz = report(service, IN_YZ, description="yz")
    zw = report(service, IN_ZW, description="zw")

    service.authorization = _worker_scoped(S_XY, S_ZW)

    ids = {j["job_id"] for j in service.jobs_page(limit=10, actor=WORKER).items}

    assert ids == {xy["job_id"], zw["job_id"]}
    assert yz["job_id"] not in ids


def test_no_adjacent_section_is_inferred(tmp_path: Path):
    """Scoped to X-Y only: the neighbouring Y-Z job must never leak in."""

    service = build_service(tmp_path, authorization=fully_scoped())
    xy = report(service, IN_XY, description="xy")
    report(service, IN_YZ, description="yz")

    service.authorization = _worker_scoped(S_XY)

    ids = {j["job_id"] for j in service.jobs_page(limit=10, actor=WORKER).items}
    assert ids == {xy["job_id"]}


def test_a_contradicting_corridor_row_is_skipped_not_crashed(tmp_path: Path):
    """CORRIDOR_CONFLICT (location_of's ScopeError) must be a per-row
    skip, never an unhandled exception that fails the whole page."""

    service = build_service(tmp_path, authorization=fully_scoped())
    good = report(service, IN_XY, description="good")

    contradicting = dict(service.repository.get(good["job_id"]))
    contradicting = {
        **contradicting,
        "job_id": "JOB-CONTRADICT",
        "corridor_id": OTHER_CORRIDOR,
    }

    real_list_page = service.repository.list_page

    def _injected(**kwargs):
        return [contradicting] + list(real_list_page(**kwargs))

    service.repository.list_page = _injected

    service.authorization = _worker_scoped(S_XY)

    page = service.jobs_page(limit=10, actor=WORKER)

    assert [j["job_id"] for j in page.items] == [good["job_id"]]


def test_a_malformed_stored_row_still_fails_the_request_closed(tmp_path: Path):
    """Invalid JSON in block_candidate_json is an existing failure for
    every read; collection scanning must not turn it into a silent
    inclusion or a silent exclusion - it must still surface."""

    service = build_service(tmp_path, authorization=fully_scoped())
    good = report(service, IN_XY)

    with closing(sqlite3.connect(service.repository.db_path)) as conn, conn:
        conn.execute(
            "UPDATE maintenance_jobs SET block_candidate_json = ? WHERE job_id = ?",
            ("{not valid json", good["job_id"]),
        )
        conn.commit()

    service.authorization = _worker_scoped(S_XY)

    with pytest.raises(json.JSONDecodeError):
        service.jobs_page(limit=10, actor=WORKER)


def test_collection_matches_single_resource_access(tmp_path: Path):
    """A row belongs in the page iff the same actor's single-job read of
    it would succeed - the collection predicate's defining property."""

    service = build_service(tmp_path, authorization=fully_scoped())
    xy = report(service, IN_XY, description="xy")
    yz = report(service, IN_YZ, description="yz")
    zw = report(service, IN_ZW, description="zw")

    service.authorization = _worker_scoped(S_XY, S_ZW)

    page_ids = {j["job_id"] for j in service.jobs_page(limit=10, actor=WORKER).items}

    for job in (xy, yz, zw):
        try:
            service.job_detail(job["job_id"], actor=WORKER)
            readable = True
        except AuthorizationDenied:
            readable = False

        assert (job["job_id"] in page_ids) == readable


# ----------------------------------------------------------------------
# 2. Role gate: fail closed before any repository read
# ----------------------------------------------------------------------


def test_admin_and_unidentified_are_refused_before_any_repository_read(tmp_path: Path):
    from backend.app.identity.actor import unidentified_actor

    service = build_service(tmp_path, authorization=fully_scoped())
    report(service, IN_XY)

    def _boom(**kwargs):
        raise AssertionError("repository.list_page must not run for a role-denied actor")

    service.repository.list_page = _boom

    with pytest.raises(AuthorizationDenied):
        service.jobs_page(limit=10, actor=ADMIN)

    with pytest.raises(AuthorizationDenied):
        service.jobs_page(limit=10, actor=unidentified_actor())

    with pytest.raises(AuthorizationDenied):
        service.obligations_page(limit=10, actor=ADMIN)


# ----------------------------------------------------------------------
# 3. Limits: 1, 50 (default), 200 (max)
# ----------------------------------------------------------------------


@pytest.mark.parametrize("limit", [1, 50, 200])
def test_full_walk_is_complete_stable_and_duplicate_free_at_every_limit(
    tmp_path: Path, limit: int
):
    service = build_service(tmp_path, authorization=fully_scoped())
    created = {
        report(service, IN_XY, description=f"job-{i}")["job_id"] for i in range(7)
    }

    service.authorization = _worker_scoped(S_XY)

    seen = [j["job_id"] for j in _walk_jobs(service, limit=limit)]

    assert len(seen) == len(set(seen)) == 7
    assert set(seen) == created


# ----------------------------------------------------------------------
# 4. Out-of-scope rows interleaved: nothing skipped, nothing duplicated
# ----------------------------------------------------------------------


def test_out_of_scope_rows_interleaved_are_skipped_without_losing_in_scope_rows(
    tmp_path: Path,
):
    service = build_service(tmp_path, authorization=fully_scoped())

    in_scope_ids = set()
    for i in range(6):
        # Alternate sections so every other row (by created_at) is out of
        # scope for a worker who only holds S_XY.
        span = IN_XY if i % 2 == 0 else IN_YZ
        job = report(service, span, description=f"job-{i}")
        if i % 2 == 0:
            in_scope_ids.add(job["job_id"])

    service.authorization = _worker_scoped(S_XY)

    seen = [j["job_id"] for j in _walk_jobs(service, limit=2)]

    assert set(seen) == in_scope_ids
    assert len(seen) == len(set(seen))


# ----------------------------------------------------------------------
# 5. Cursor safety
# ----------------------------------------------------------------------


def test_crafted_cursor_at_an_out_of_scope_rows_position_grants_nothing(
    tmp_path: Path,
):
    """A client may craft any cursor: it only ever chooses a start
    position, never authority. Feeding it the real position of an
    out-of-scope row must never surface that row or any other one."""

    service = build_service(tmp_path, authorization=fully_scoped())
    out_of_scope = report(service, IN_YZ, description="secret")
    in_scope = report(service, IN_XY, description="visible")

    service.authorization = _worker_scoped(S_XY)

    crafted = (out_of_scope["created_at"], out_of_scope["job_id"])

    page = service.jobs_page(limit=10, after=crafted, actor=WORKER)

    ids = [j["job_id"] for j in page.items]
    assert out_of_scope["job_id"] not in ids
    # Whether in_scope appears depends only on its (created_at, job_id)
    # position relative to the crafted cursor, exactly as for any real
    # cursor - never on what the crafted cursor names.
    assert set(ids) <= {in_scope["job_id"]}


def test_cursor_does_not_expose_a_rejected_job_id(tmp_path: Path):
    """At a budget boundary, an out-of-scope row's job_id must never
    appear in the emitted cursor."""

    service = build_service(tmp_path, authorization=fully_scoped())
    service.collection_scan_budget = 3

    # Five out-of-scope rows, distinct created_at (no tie), then nothing
    # else: the scan must stop mid-table, at the budget, with an
    # id-free boundary. limit=1 keeps the EFFECTIVE budget
    # (max(collection_scan_budget, limit + 1)) at the small override
    # rather than being raised past it by the requested page size.
    out_ids = []
    for i in range(5):
        job = _insert(
            service.repository, f"JOB-OUT-{i}", _at(100 - i), section_id=S_YZ
        )
        out_ids.append(job["job_id"])

    service.authorization = _worker_scoped(S_XY)

    page = service.jobs_page(limit=1, actor=WORKER)

    assert page.items == []
    assert page.next_after is not None

    created_at, job_id = page.next_after
    assert job_id == ""
    assert job_id not in out_ids


def test_cursor_acceptance_is_scope_independent(tmp_path: Path):
    """A syntactically valid cursor is accepted the same way regardless
    of what the actor's scope is - cursor validity is never an oracle."""

    service = build_service(tmp_path, authorization=fully_scoped())
    job = report(service, IN_XY)
    cursor_position = (job["created_at"], job["job_id"])

    scoped = service.jobs_page(limit=10, after=cursor_position, actor=WORKER)
    service.authorization = enforcing()  # no scope at all
    unscoped = service.jobs_page(limit=10, after=cursor_position, actor=WORKER)

    # Neither call raises for the cursor itself; both simply differ in
    # how many (if any) rows the actor may see from that position.
    assert isinstance(scoped, ScopedPage)
    assert isinstance(unscoped, ScopedPage)
    assert unscoped.items == []


# ----------------------------------------------------------------------
# 6. Budget exhaustion and tie-group safety
# ----------------------------------------------------------------------


def test_budget_exhaustion_produces_short_pages_and_still_completes(tmp_path: Path):
    service = build_service(tmp_path, authorization=fully_scoped())
    service.collection_scan_budget = 4

    # 20 out-of-scope rows (distinct created_at), then one in-scope row
    # older than all of them.
    for i in range(20):
        _insert(service.repository, f"JOB-OUT-{i:03d}", _at(1000 - i), section_id=S_YZ)

    target = _insert(service.repository, "JOB-IN-TARGET", _at(0), section_id=S_XY)

    service.authorization = _worker_scoped(S_XY)

    saw_short_page = False
    seen = []
    after = None
    queries = 0

    real_list_page = service.repository.list_page

    def _counting(**kwargs):
        nonlocal queries
        queries += 1
        return real_list_page(**kwargs)

    service.repository.list_page = _counting

    for _ in range(50):
        page = service.jobs_page(limit=5, after=after, actor=WORKER)
        seen.extend(j["job_id"] for j in page.items)

        if len(page.items) < 5 and page.next_after is not None:
            saw_short_page = True

        if page.next_after is None:
            break

        after = page.next_after
    else:
        pytest.fail("walk did not terminate")

    assert seen == [target["job_id"]]
    assert saw_short_page
    # Bounded: nowhere near a full unbounded scan of 21 rows per request.
    assert queries < 40


def test_all_rows_out_of_scope_still_terminates_with_empty_pages(tmp_path: Path):
    service = build_service(tmp_path, authorization=fully_scoped())
    service.collection_scan_budget = 5

    for i in range(23):
        _insert(service.repository, f"JOB-OUT-{i:03d}", _at(500 - i), section_id=S_YZ)

    service.authorization = _worker_scoped(S_XY)

    seen = _walk_jobs(service, limit=10)

    assert seen == []


def test_a_tie_group_within_the_safe_ceiling_is_handled_exactly(tmp_path: Path):
    """A created_at value shared by several rows is a normal, if
    unlikely, keyset situation and must not lose or duplicate a row."""

    service = build_service(tmp_path, authorization=fully_scoped())
    service.collection_scan_budget = 3

    tie_ts = _at(500)
    tie_ids = set()
    for i in range(10):
        job = _insert(
            service.repository, f"JOB-TIE-{i:02d}", tie_ts, section_id=S_XY
        )
        tie_ids.add(job["job_id"])

    older = _insert(service.repository, "JOB-OLDER", _at(0), section_id=S_XY)

    service.authorization = _worker_scoped(S_XY)

    seen = [j["job_id"] for j in _walk_jobs(service, limit=4)]

    assert set(seen) == tie_ids | {older["job_id"]}
    assert len(seen) == len(set(seen))


def test_a_mixed_tie_group_at_a_budget_boundary_resumes_without_loss_or_duplication(
    tmp_path: Path,
):
    """A created_at value shared by SOME in-scope and SOME out-of-scope
    rows, sized so the group itself becomes the budget-stop boundary.

    Ties never stop mid-group (at_tie_boundary is False for every row
    but the group's first), so the whole group is always examined
    together: the in-scope rows are already in THIS page, and the
    boundary this page emits - (created_at, "") when the group's own
    last-examined row was itself out of scope - must exclude the ENTIRE
    group on the next request, not just the rows still unseen. See
    docs/SLICE10_1D2_COLLECTION_SCOPE_ARCHITECTURE.md Sec.10.2.
    """

    service = build_service(tmp_path, authorization=fully_scoped())
    service.collection_scan_budget = 3

    tie_ts = _at(500)
    in_scope_tie_ids = set()
    for i in range(6):
        # 2 of 6 rows in scope, interleaved rather than grouped, so the
        # stop cannot land on a convenient all-kept or all-skipped edge.
        section = S_XY if i % 3 == 0 else S_YZ
        job = _insert(
            service.repository, f"JOB-MIX-{i:02d}", tie_ts, section_id=section
        )
        if section == S_XY:
            in_scope_tie_ids.add(job["job_id"])

    older = _insert(service.repository, "JOB-OLDER", _at(0), section_id=S_XY)

    service.authorization = _worker_scoped(S_XY)

    # limit=5: effective budget = max(3, 6) = 6, exactly the tie group's
    # size, so the group's own end is where the budget stop must land -
    # not before it (mid-group) and not after (having silently pulled in
    # the older row too).
    pages = []
    after = None
    for _ in range(10):
        page = service.jobs_page(limit=5, after=after, actor=WORKER)
        pages.append(page)
        if page.next_after is None:
            break
        after = page.next_after
    else:
        pytest.fail("walk did not terminate")

    assert len(pages) == 2, "expected the tie group's own boundary to end page 1"

    seen = [j["job_id"] for page in pages for j in page.items]
    assert set(seen) == in_scope_tie_ids | {older["job_id"]}
    assert len(seen) == len(set(seen))


def test_a_tie_group_larger_than_the_safe_ceiling_fails_closed(tmp_path: Path):
    """More rows share one created_at, at a scan boundary, than the
    bounded tie allowance permits: refuse rather than guess an inexact
    continuation position that could skip or repeat a row."""

    service = build_service(tmp_path, authorization=fully_scoped())
    service.collection_scan_budget = 2

    tie_ts = _at(500)
    total = 2 + COLLECTION_TIE_CEILING + 5

    for i in range(total):
        _insert(service.repository, f"JOB-TIE-{i:04d}", tie_ts, section_id=S_YZ)

    service.authorization = _worker_scoped(S_XY)

    # limit=1 keeps the effective budget (max(collection_scan_budget,
    # limit + 1)) at the small override above.
    with pytest.raises(CollectionScanLimitError):
        service.jobs_page(limit=1, actor=WORKER)


# ----------------------------------------------------------------------
# 7. Obligations: derive nothing for an out-of-scope row
# ----------------------------------------------------------------------


def test_out_of_scope_incoherent_job_does_not_break_an_authorized_page(
    tmp_path: Path,
):
    """A 'scheduled' job with no optimization_run_id raises
    ObligationIntegrityError if ever derived. When it is OUT of scope,
    it must never be derived at all, so an authorized reader's page must
    not fail because of it."""

    service = build_service(tmp_path, authorization=fully_scoped())

    good = report(service, IN_XY, description="good")

    broken = _insert(
        service.repository,
        "JOB-BROKEN",
        _at(0),
        section_id=S_YZ,
        status="scheduled",
    )

    service.authorization = _worker_scoped(S_XY)

    obligations, next_after, evaluated_at = service.obligations_page(
        limit=10, actor=WORKER
    )

    job_ids = {o.job_id for o in obligations}
    assert good["job_id"] in job_ids
    assert broken["job_id"] not in job_ids


def test_in_scope_incoherent_job_still_fails_closed(tmp_path: Path):
    """The same broken row, now IN scope, must still 409-equivalent
    (raise ObligationIntegrityError): scope filtering must never weaken
    Slice 9's own fail-closed guarantee for a job the actor CAN see."""

    service = build_service(tmp_path, authorization=fully_scoped())

    _insert(
        service.repository,
        "JOB-BROKEN",
        _at(0),
        section_id=S_XY,
        status="scheduled",
    )

    service.authorization = _worker_scoped(S_XY)

    with pytest.raises(ObligationIntegrityError):
        service.obligations_page(limit=10, actor=WORKER)


def test_obligations_history_is_read_only_for_kept_rows(tmp_path: Path):
    """The batched history read must never touch an out-of-scope job's
    history - not merely its derivation."""

    service = build_service(tmp_path, authorization=fully_scoped())
    good = report(service, IN_XY, description="good")
    bad = report(service, IN_YZ, description="bad")

    service.authorization = _worker_scoped(S_XY)

    seen_ids: list[str] = []
    real_list_for_jobs = service.history.list_for_jobs

    def _spy(job_ids):
        seen_ids.extend(job_ids)
        return real_list_for_jobs(job_ids)

    service.history.list_for_jobs = _spy

    service.obligations_page(limit=10, actor=WORKER)

    assert good["job_id"] in seen_ids
    assert bad["job_id"] not in seen_ids


def test_obligations_cursor_follows_the_new_position_contract(tmp_path: Path):
    """The router-facing cursor is now a plain scan position, and a full
    walk over it is complete, stable and duplicate-free - same guarantee
    as jobs_page, exercised through obligations_page directly."""

    service = build_service(tmp_path, authorization=fully_scoped())
    created = {
        report(service, IN_XY, description=f"o-{i}")["job_id"] for i in range(5)
    }

    service.authorization = _worker_scoped(S_XY)

    seen = []
    after = None

    for _ in range(50):
        obligations, next_after, _ = service.obligations_page(
            limit=2, after=after, actor=WORKER
        )
        seen.extend(o.job_id for o in obligations)

        if next_after is None:
            break

        assert isinstance(next_after, tuple) and len(next_after) == 2
        after = next_after
    else:
        pytest.fail("obligations_page walk did not terminate")

    assert set(seen) == created
    assert len(seen) == len(set(seen))


# ----------------------------------------------------------------------
# 8. UnenforcedPolicy / RoleActionOnlyPolicy equivalence
# ----------------------------------------------------------------------


def test_unenforced_policy_matches_pre_10_1d_2_behavior_exactly(tmp_path: Path):
    """Under the shipped default policy, every row is permitted, so
    jobs_page must issue exactly the one repository query it issued
    before Slice 10.1D.2, and return exactly what a raw
    list_page(limit + 1) walk would."""

    service = build_service(tmp_path, authorization=UnenforcedPolicy())
    for i in range(9):
        report(service, IN_XY, description=f"job-{i}")

    queries = 0
    real_list_page = service.repository.list_page

    def _counting(**kwargs):
        nonlocal queries
        queries += 1
        return real_list_page(**kwargs)

    service.repository.list_page = _counting

    page = service.jobs_page(limit=3, actor=WORKER)

    assert queries == 1

    expected_rows = real_list_page(limit=4, status=None, after=None)
    expected_items = expected_rows[:3]
    expected_cursor = (
        (expected_items[-1]["created_at"], expected_items[-1]["job_id"])
        if len(expected_rows) > 3
        else None
    )

    assert [j["job_id"] for j in page.items] == [j["job_id"] for j in expected_items]
    assert page.next_after == expected_cursor


def test_unenforced_policy_still_lists_a_legacy_unresolved_job(tmp_path: Path):
    """Under UnenforcedPolicy, a legacy no-section row must keep
    appearing exactly as it did before this slice - filtering must never
    turn on merely because a row happens to be unresolvable."""

    service = build_service(tmp_path, authorization=UnenforcedPolicy())
    legacy = _insert(service.repository, "JOB-LEGACY", _at(0), section_id=None)

    page = service.jobs_page(limit=10, actor=WORKER)

    assert [j["job_id"] for j in page.items] == [legacy["job_id"]]


def test_role_action_only_policy_role_denial_still_blocks_the_collection(
    tmp_path: Path,
):
    from backend.app.identity.authorization import RoleActionOnlyPolicy

    class DenyEverything(RoleActionOnlyPolicy):
        def authorize(self, actor, action):
            raise AuthorizationDenied(actor, action)

    service = build_service(tmp_path, authorization=fully_scoped())
    report(service, IN_XY)
    service.authorization = DenyEverything()

    with pytest.raises(AuthorizationDenied):
        service.jobs_page(limit=10, actor=WORKER)


def test_role_action_only_policy_otherwise_stays_unfiltered(tmp_path: Path):
    from backend.app.identity.authorization import RoleActionOnlyPolicy

    class PermitEverything(RoleActionOnlyPolicy):
        def authorize(self, actor, action):
            return None

    service = build_service(tmp_path, authorization=PermitEverything())
    xy = report(service, IN_XY, description="xy")
    yz = report(service, IN_YZ, description="yz")

    ids = {j["job_id"] for j in service.jobs_page(limit=10, actor=WORKER).items}

    assert ids == {xy["job_id"], yz["job_id"]}


def test_an_enforcing_policy_with_no_authorize_resource_never_silently_permits(
    tmp_path: Path,
):
    """The construction-time guard (10.1D.1) makes this unreachable
    through normal construction; the predicate's own fail-closed
    backstop must still hold if it is ever forced in."""

    from backend.app.identity.authorization import PolicyConfigurationError

    service = build_service(tmp_path, authorization=fully_scoped())
    report(service, IN_XY)

    class HalfBuiltEnforcingPolicy:
        enforcing = True

        def authorize(self, actor, action):
            return None

    service._authorization = HalfBuiltEnforcingPolicy()

    with pytest.raises(PolicyConfigurationError):
        service.jobs_page(limit=10, actor=WORKER)


# ----------------------------------------------------------------------
# 9. HTTP-level checks (router owns the cursor; NovaForge-seam stability)
# ----------------------------------------------------------------------


@pytest.fixture()
def http_service(tmp_path: Path, monkeypatch):
    """A JobService on the three-section corridor, wired into the router."""

    import backend.app.jobs.router as router_module
    from backend.app.api.main import app

    service = build_service(tmp_path)
    monkeypatch.setattr(router_module, "service", service)

    return TestClient(app), service


def test_get_v1_jobs_over_http_filters_by_scope(http_service):
    http, service = http_service
    inside = report(service, IN_XY, description="inside")
    report(service, IN_YZ, description="outside")

    service.authorization = _worker_scoped(S_XY)

    response = http.get(
        "/v1/jobs", headers={"X-Actor-Id": WORKER_ID, "X-Actor-Role": "WORKER"}
    )
    body = response.json()

    assert response.status_code == 200
    assert [item["job_id"] for item in body["items"]] == [inside["job_id"]]


def test_get_v1_jobs_response_shape_is_unchanged(http_service):
    """Slice 7 froze {items, next_cursor}: no new top-level field."""

    http, service = http_service
    report(service, IN_XY)
    service.authorization = _worker_scoped(S_XY)

    response = http.get(
        "/v1/jobs", headers={"X-Actor-Id": WORKER_ID, "X-Actor-Role": "WORKER"}
    )

    assert set(response.json()) == {"items", "next_cursor"}


def test_crafted_cursor_over_http_cannot_recover_an_out_of_scope_job(http_service):
    http, service = http_service
    secret = report(service, IN_YZ, description="secret")
    visible = report(service, IN_XY, description="visible")

    service.authorization = _worker_scoped(S_XY)

    crafted = _encode_cursor(secret["created_at"], secret["job_id"])

    response = http.get(
        "/v1/jobs",
        params={"cursor": crafted},
        headers={"X-Actor-Id": WORKER_ID, "X-Actor-Role": "WORKER"},
    )

    assert response.status_code == 200
    ids = [item["job_id"] for item in response.json()["items"]]
    assert secret["job_id"] not in ids
    assert set(ids) <= {visible["job_id"]}


def test_an_out_of_scope_incoherent_obligation_is_200_not_409_over_http(
    http_service,
):
    http, service = http_service
    good = report(service, IN_XY, description="good")
    _insert(service.repository, "JOB-BROKEN", _at(0), section_id=S_YZ, status="scheduled")

    service.authorization = _worker_scoped(S_XY)

    response = http.get(
        "/v1/obligations",
        headers={"X-Actor-Id": WORKER_ID, "X-Actor-Role": "WORKER"},
    )

    assert response.status_code == 200
    ids = {item["job_id"] for item in response.json()["items"]}
    assert "JOB-BROKEN" not in ids


def test_obligations_client_filters_narrow_only_never_expand(http_service):
    """A role= / state= filter must never surface a row the actor could
    not otherwise see - narrowing only, never a second authorization
    channel."""

    http, service = http_service
    report(service, IN_XY, description="xy")
    report(service, IN_YZ, description="yz")

    service.authorization = _worker_scoped(S_XY)

    unfiltered = http.get(
        "/v1/obligations",
        headers={"X-Actor-Id": WORKER_ID, "X-Actor-Role": "WORKER"},
    ).json()

    filtered = http.get(
        "/v1/obligations",
        params={"role": "WORKER"},
        headers={"X-Actor-Id": WORKER_ID, "X-Actor-Role": "WORKER"},
    ).json()

    filtered_ids = {item["job_id"] for item in filtered["items"]}
    unfiltered_ids = {item["job_id"] for item in unfiltered["items"]}

    assert filtered_ids <= unfiltered_ids


def test_declared_actor_seam_is_unchanged_for_collection_reads(http_service):
    """This slice must not touch how an Actor is established: it still
    consumes the existing DECLARED_UNVERIFIED seam, never a NovaForge
    channel (Slice 10.1D.2 does not implement NovaForge)."""

    from backend.app.api.deps import request_actor
    from backend.app.identity.actor import IdentityAssurance

    actor = request_actor(actor_id=WORKER_ID, actor_role="WORKER")
    assert actor.assurance is IdentityAssurance.DECLARED_UNVERIFIED
    # 10.2c: the level exists, but no header can produce it.
    assert actor.assurance is not IdentityAssurance.AUTHENTICATED

    http, service = http_service
    assert isinstance(service.authorization, UnenforcedPolicy)

    response = http.get(
        "/v1/jobs", headers={"X-Actor-Id": WORKER_ID, "X-Actor-Role": "WORKER"}
    )
    assert response.status_code == 200


def test_the_unauthenticated_read_still_works_unchanged(http_service):
    """No headers -> UNIDENTIFIED actor -> under the shipped
    UnenforcedPolicy, still 200 - exactly Slice 10.1D's own pin."""

    http, service = http_service
    report(service, IN_XY)

    assert http.get("/v1/jobs").status_code == 200


# ----------------------------------------------------------------------
# 10. Router-source: no route reaches list_jobs or list_all directly
# ----------------------------------------------------------------------


def test_the_router_still_uses_jobs_page_and_never_list_jobs_or_list_all():
    import inspect

    import backend.app.jobs.router as router_module

    source = inspect.getsource(router_module)

    assert "service.jobs_page(" in source
    assert "service.obligations_page(" in source
    assert ".list_jobs(" not in source
    assert ".list_all(" not in source
