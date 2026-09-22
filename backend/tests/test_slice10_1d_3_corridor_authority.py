"""Slice 10.1D.3: corridor-COMPLETE authority (OQ-2, D-1/D-2/D-4 approved).

A corridor-scoped action (REQUEST_OPTIMIZATION, READ_OPTIMIZATION_RUN) is
no longer permitted by holding ANY scope in the corridor. It requires the
actor's OWN-ROLE scopes, taken together, to name EVERY section of the
corridor, proven against this policy's own server-side `topology` - never
the request, never a header, never a scope's own claim about "the whole
corridor" (there is no such claim: RailwayScope names sections
explicitly; see backend.app.identity.scope).

See docs/SLICE10_1D3_CORRIDOR_AUTHORIZATION_ARCHITECTURE.md for the full
architecture gate this slice implements (§10, §22-24).

This file is additive: backend.tests.test_slice10_1d_scope_enforcement.py
keeps proving section-scoped authorization (10.1D.1) and the fail-closed
matrix (10.1C); the five corridor-action cases there that used to expect
a partial-scope ENGINEER to pass have been inverted (see that file) and
are not re-proven here except where a byte-for-byte or HTTP-level
guarantee needs its own test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.identity.actor import ActorRole
from backend.app.identity.authorization import AuthorizationDenied, JobAction
from backend.app.identity.policy import EnforcingPolicy
from backend.app.identity.resource import ResourceLocation, ScopeMatch, covers_corridor
from backend.app.identity.scope import RailwayScope
from backend.app.jobs.optimization import JobOptimizationService
from backend.tests.slice10_1d_helpers import (
    AUTHORITY,
    AUTHORITY_ID,
    CORRIDOR,
    ENGINEER,
    ENGINEER_ID,
    IN_XY,
    OTHER_CORRIDOR,
    S_XY,
    S_YZ,
    S_ZW,
    WORKER,
    WORKER_ID,
    assignment,
    build_service,
    count_events,
    directory,
    enforcing,
    fully_scoped,
    report,
)

A = JobAction


def _record_run(service, run_id: str, corridor_id: str | None) -> None:
    """One real optimization_runs row, written directly (no solver run)."""

    from backend.app.audit.models import OptimizationRunRecord
    from backend.app.audit.repository import AuditRepository

    AuditRepository(service.repository.db_path).record(
        OptimizationRunRecord(
            run_id=run_id,
            actor=ENGINEER_ID,
            trigger="TEST",
            corridor_id=corridor_id,
            requested_at="2026-01-01T00:00:00+00:00",
            completed_at="2026-01-01T00:00:01+00:00",
            solver_status="OPTIMAL",
            solve_time_seconds=0.1,
            request_json='{"marker": "byte-for-byte"}',
            result_json='{"scheduled_blocks": []}',
            provenance_snapshot_json='{"possession_window_count": 0}',
            error=None,
        )
    )


# ----------------------------------------------------------------------
# 1. covers_corridor - the pure helper, in isolation (report §22 T-14)
# ----------------------------------------------------------------------


def _scope(*section_ids: str, corridor_id: str = CORRIDOR) -> RailwayScope:
    return RailwayScope(corridor_id, list(section_ids))


def test_covers_corridor_matches_on_full_union():
    scopes = (_scope(S_XY), _scope(S_YZ, S_ZW))

    assert (
        covers_corridor(scopes, CORRIDOR, (S_XY, S_YZ, S_ZW))
        is ScopeMatch.MATCH
    )


def test_covers_corridor_no_match_on_partial_union():
    scopes = (_scope(S_XY),)

    assert (
        covers_corridor(scopes, CORRIDOR, (S_XY, S_YZ, S_ZW))
        is ScopeMatch.NO_MATCH
    )


def test_covers_corridor_ignores_scopes_from_another_corridor():
    scopes = (_scope(S_XY, S_YZ, S_ZW, corridor_id=OTHER_CORRIDOR),)

    assert (
        covers_corridor(scopes, CORRIDOR, (S_XY, S_YZ, S_ZW))
        is ScopeMatch.NO_MATCH
    )


@pytest.mark.parametrize("corridor_id", [None, "", "   "])
def test_covers_corridor_unresolved_on_blank_corridor(corridor_id):
    assert (
        covers_corridor((_scope(S_XY),), corridor_id, (S_XY,))
        is ScopeMatch.UNRESOLVED
    )


def test_covers_corridor_unresolved_on_missing_topology():
    """No entry at all for the corridor - `None` from a topology lookup."""

    assert (
        covers_corridor((_scope(S_XY, S_YZ, S_ZW),), CORRIDOR, None)
        is ScopeMatch.UNRESOLVED
    )


def test_covers_corridor_unresolved_on_empty_topology():
    """An entry exists but names no section - never read as 'the whole
    corridor'."""

    assert (
        covers_corridor((_scope(S_XY, S_YZ, S_ZW),), CORRIDOR, ())
        is ScopeMatch.UNRESOLVED
    )


def test_covers_corridor_no_wildcard_no_adjacency():
    """A held '*' or an adjacent-looking id never substitutes for the
    actual registered section ids - exact string membership only."""

    scopes = (_scope(S_XY, S_YZ),)  # missing S_ZW

    assert (
        covers_corridor(scopes, CORRIDOR, (S_XY, S_YZ, S_ZW))
        is ScopeMatch.NO_MATCH
    )


def test_covers_corridor_rejects_a_non_railwayscope_entry():
    with pytest.raises(TypeError):
        covers_corridor([object()], CORRIDOR, (S_XY,))


# ----------------------------------------------------------------------
# 2. EnforcingPolicy topology construction (report §22 T-06, T-07, T-08)
# ----------------------------------------------------------------------


def test_topology_defaults_to_empty_and_denies_every_corridor_action():
    """No `topology=` at all: every corridor-scoped action fails closed."""

    policy = EnforcingPolicy(
        directory(assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY, S_YZ, S_ZW))
    )

    with pytest.raises(AuthorizationDenied):
        policy.authorize_resource(
            ENGINEER, A.REQUEST_OPTIMIZATION, ResourceLocation(CORRIDOR, None)
        )


def test_topology_with_an_empty_section_set_denies_every_corridor_action():
    policy = EnforcingPolicy(
        directory(assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY, S_YZ, S_ZW)),
        topology=[(CORRIDOR, ())],
    )

    with pytest.raises(AuthorizationDenied):
        policy.authorize_resource(
            ENGINEER, A.REQUEST_OPTIMIZATION, ResourceLocation(CORRIDOR, None)
        )


def test_topology_rejects_a_duplicate_corridor_entry():
    with pytest.raises(ValueError):
        EnforcingPolicy(
            directory(),
            topology=[(CORRIDOR, (S_XY,)), (CORRIDOR, (S_YZ,))],
        )


def test_topology_rejects_a_blank_corridor_id():
    with pytest.raises(ValueError):
        EnforcingPolicy(directory(), topology=[("", (S_XY,))])


def test_topology_rejects_a_malformed_entry():
    with pytest.raises(TypeError):
        EnforcingPolicy(directory(), topology=[CORRIDOR])  # not a pair


# ----------------------------------------------------------------------
# 3. REQUEST_OPTIMIZATION: corridor-complete authority (D-1)
# ----------------------------------------------------------------------


def test_partial_engineer_scope_is_denied_optimization(tmp_path: Path):
    """T-01: no events, no audit row, no proposal change."""

    service = build_service(tmp_path, authorization=fully_scoped())
    job = report(service, IN_XY)
    db = tmp_path / "jobs.db"
    before_job = dict(service.repository.get(job["job_id"]))
    before_events = count_events(db)

    service.authorization = enforcing(
        assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY)
    )

    from backend.app.audit.repository import AuditRepository

    with pytest.raises(AuthorizationDenied):
        JobOptimizationService(service).optimize_corridor(CORRIDOR, actor=ENGINEER)

    assert count_events(db) == before_events
    assert AuditRepository(service.repository.db_path).list_all() == []
    assert dict(service.repository.get(job["job_id"])) == before_job


def test_full_engineer_scope_is_permitted_optimization(tmp_path: Path):
    """T-02: corridor-complete scope passes the seam."""

    service = build_service(tmp_path, authorization=fully_scoped())
    report(service, IN_XY)

    service.authorization = enforcing(
        assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY, S_YZ, S_ZW)
    )

    try:
        JobOptimizationService(service).optimize_corridor(CORRIDOR, actor=ENGINEER)
    except AuthorizationDenied:  # pragma: no cover - the thing being refuted
        pytest.fail("a corridor-complete engineer was refused")
    except Exception:
        # Downstream solver/possession outcome is not this test's concern.
        pass


def test_same_role_scope_union_is_permitted(tmp_path: Path):
    """T-03: two ENGINEER assignments, together, cover the corridor."""

    service = build_service(tmp_path, authorization=fully_scoped())
    report(service, IN_XY)

    service.authorization = enforcing(
        assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY),
        assignment(ENGINEER_ID, ActorRole.ENGINEER, S_YZ, S_ZW),
    )

    try:
        JobOptimizationService(service).optimize_corridor(CORRIDOR, actor=ENGINEER)
    except AuthorizationDenied:  # pragma: no cover
        pytest.fail("a same-role scope union covering the corridor was refused")
    except Exception:
        pass


def test_cross_role_scope_union_is_denied():
    """T-04: an ENGINEER assignment plus a WORKER assignment for the same
    person must NOT combine into corridor-complete ENGINEER authority.
    """

    policy = enforcing(
        assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY),
        assignment(ENGINEER_ID, ActorRole.WORKER, S_YZ, S_ZW),
    )

    with pytest.raises(AuthorizationDenied):
        policy.authorize_resource(
            ENGINEER, A.REQUEST_OPTIMIZATION, ResourceLocation(CORRIDOR, None)
        )


def test_topology_expansion_revokes_previously_complete_authority():
    """T-05: adding a section to the corridor's topology removes an
    existing assignment's completeness until that section is explicitly
    granted - never inferred, never grandfathered in.
    """

    complete = enforcing(
        assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY, S_YZ, S_ZW)
    )
    assert (
        complete.authorize_resource(
            ENGINEER, A.REQUEST_OPTIMIZATION, ResourceLocation(CORRIDOR, None)
        )
        is None
    )

    grown = enforcing(
        assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY, S_YZ, S_ZW),
        topology=[(CORRIDOR, (S_XY, S_YZ, S_ZW, "W-V"))],
    )

    with pytest.raises(AuthorizationDenied):
        grown.authorize_resource(
            ENGINEER, A.REQUEST_OPTIMIZATION, ResourceLocation(CORRIDOR, None)
        )


def test_missing_topology_denies_optimization():
    """T-06."""

    policy = enforcing(
        assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY, S_YZ, S_ZW),
        topology=(),
    )

    with pytest.raises(AuthorizationDenied):
        policy.authorize_resource(
            ENGINEER, A.REQUEST_OPTIMIZATION, ResourceLocation(CORRIDOR, None)
        )


def test_empty_topology_denies_optimization():
    """T-07."""

    policy = enforcing(
        assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY, S_YZ, S_ZW),
        topology=[(CORRIDOR, ())],
    )

    with pytest.raises(AuthorizationDenied):
        policy.authorize_resource(
            ENGINEER, A.REQUEST_OPTIMIZATION, ResourceLocation(CORRIDOR, None)
        )


def test_denied_optimization_does_not_invoke_the_solver(tmp_path: Path, monkeypatch):
    """The corridor check must run before the lock, the snapshot and the
    solver - not merely before the write."""

    import backend.app.jobs.optimization as optimization_module

    def _must_not_run(request):  # pragma: no cover - only runs on failure
        raise AssertionError("the solver must not be invoked for a denied request")

    monkeypatch.setattr(optimization_module, "solve", _must_not_run)

    service = build_service(tmp_path, authorization=fully_scoped())
    report(service, IN_XY)

    service.authorization = enforcing(
        assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY)
    )

    with pytest.raises(AuthorizationDenied):
        JobOptimizationService(service).optimize_corridor(CORRIDOR, actor=ENGINEER)


def test_denied_optimization_does_not_mutate_the_job_or_its_proposal(tmp_path: Path):
    """A denied attempt leaves the job's stored row - and therefore any
    proposal it carries - byte-for-byte unchanged, not merely 'no new
    events'."""

    service = build_service(tmp_path, authorization=fully_scoped())
    job = report(service, IN_XY)

    before = dict(service.repository.get(job["job_id"]))
    before_events = count_events(tmp_path / "jobs.db")

    service.authorization = enforcing(
        assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY)
    )

    with pytest.raises(AuthorizationDenied):
        JobOptimizationService(service).optimize_corridor(CORRIDOR, actor=ENGINEER)

    assert dict(service.repository.get(job["job_id"])) == before
    assert count_events(tmp_path / "jobs.db") == before_events


# ----------------------------------------------------------------------
# 4. READ_OPTIMIZATION_RUN: corridor-complete authority (D-1), role-only
#    for AUTHORITY/WORKER (D-2)
# ----------------------------------------------------------------------


def test_partial_scope_run_read_is_denied(tmp_path: Path):
    """T-09."""

    service = build_service(tmp_path, authorization=fully_scoped())
    _record_run(service, "RUN-1", CORRIDOR)

    service.authorization = enforcing(
        assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY)
    )

    with pytest.raises(AuthorizationDenied):
        service.optimization_run("RUN-1", actor=ENGINEER)


def test_full_scope_run_read_returns_the_stored_record_unchanged(tmp_path: Path):
    """T-10: byte-for-byte, never filtered/rewritten/redacted."""

    service = build_service(tmp_path, authorization=fully_scoped())
    _record_run(service, "RUN-1", CORRIDOR)

    service.authorization = enforcing(
        assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY, S_YZ, S_ZW)
    )

    run = service.optimization_run("RUN-1", actor=ENGINEER)

    assert run.run_id == "RUN-1"
    assert run.request_json == '{"marker": "byte-for-byte"}'
    assert run.result_json == '{"scheduled_blocks": []}'
    assert run.provenance_snapshot_json == '{"possession_window_count": 0}'


def test_authority_run_read_is_denied_by_role_before_any_db_read(tmp_path: Path):
    """AUTHORITY never holds READ_OPTIMIZATION_RUN (D-2), even with full
    corridor scope. A run id that does not exist still yields
    AuthorizationDenied, not KeyError/404 - proof the role check runs
    before the database is ever consulted.
    """

    service = build_service(tmp_path, authorization=fully_scoped())

    with pytest.raises(AuthorizationDenied):
        service.optimization_run("RUN-DOES-NOT-EXIST", actor=AUTHORITY)


def test_worker_run_read_is_denied_by_role(tmp_path: Path):
    service = build_service(tmp_path, authorization=fully_scoped())

    with pytest.raises(AuthorizationDenied):
        service.optimization_run("RUN-DOES-NOT-EXIST", actor=WORKER)


def test_run_in_a_foreign_corridor_is_denied_even_with_full_scope(tmp_path: Path):
    service = build_service(tmp_path, authorization=fully_scoped())
    _record_run(service, "RUN-2", OTHER_CORRIDOR)

    service.authorization = enforcing(
        assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY, S_YZ, S_ZW)
    )

    with pytest.raises(AuthorizationDenied):
        service.optimization_run("RUN-2", actor=ENGINEER)


def test_run_naming_no_corridor_is_denied_even_with_full_scope(tmp_path: Path):
    service = build_service(tmp_path, authorization=fully_scoped())
    _record_run(service, "RUN-3", None)

    service.authorization = enforcing(
        assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY, S_YZ, S_ZW)
    )

    with pytest.raises(AuthorizationDenied):
        service.optimization_run("RUN-3", actor=ENGINEER)


# ----------------------------------------------------------------------
# 5. Denial messages never enumerate sections (M)
# ----------------------------------------------------------------------


def test_denial_message_never_enumerates_sections(tmp_path: Path):
    """T-11/T-14: neither a missing section nor a held one appears."""

    service = build_service(tmp_path, authorization=fully_scoped())
    _record_run(service, "RUN-1", CORRIDOR)

    service.authorization = enforcing(
        assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY)
    )

    with pytest.raises(AuthorizationDenied) as excinfo:
        service.optimization_run("RUN-1", actor=ENGINEER)

    message = str(excinfo.value)

    for section_id in (S_XY, S_YZ, S_ZW):
        assert section_id not in message

    assert "does not hold corridor-wide authority" in message
    assert CORRIDOR in message  # the corridor id itself is not sensitive


def test_optimize_denial_message_never_enumerates_sections(tmp_path: Path):
    service = build_service(tmp_path, authorization=fully_scoped())
    report(service, IN_XY)

    service.authorization = enforcing(
        assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY)
    )

    with pytest.raises(AuthorizationDenied) as excinfo:
        JobOptimizationService(service).optimize_corridor(CORRIDOR, actor=ENGINEER)

    message = str(excinfo.value)

    for section_id in (S_XY, S_YZ, S_ZW):
        assert section_id not in message


# ----------------------------------------------------------------------
# 6. Section-scoped actions and construction guarantees are untouched
# ----------------------------------------------------------------------


def test_section_scoped_reads_are_unaffected_by_topology():
    """READ_JOB (and the other section-scoped actions) never consult
    `topology` at all - only CORRIDOR_SCOPED_ACTIONS do.
    """

    policy = enforcing(
        assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY), topology=()
    )

    assert (
        policy.authorize_resource(ENGINEER, A.READ_JOB, ResourceLocation(CORRIDOR, S_XY))
        is None
    )


def test_authority_can_still_act_on_its_own_section(tmp_path: Path):
    """J: AUTHORITY's section-scoped approve/reject/postpone/release path
    is untouched by the corridor-complete rule - REJECT_PROPOSAL is
    section-scoped (see role_actions.SECTION_SCOPED_ACTIONS) and never
    asks the corridor question at all.
    """

    service = build_service(tmp_path, authorization=fully_scoped())
    job = report(service, IN_XY)

    service.authorization = enforcing(
        assignment(AUTHORITY_ID, ActorRole.AUTHORITY, S_XY),
        assignment(WORKER_ID, ActorRole.WORKER, S_XY),
    )

    try:
        service.reject_proposal(
            job["job_id"],
            actor=AUTHORITY,
            reason="not needed",
            expected_proposal_run_id="RUN-NONE",
        )
    except AuthorizationDenied:  # pragma: no cover - the thing being refuted
        pytest.fail("a section-scoped authority was refused by authorization")
    except Exception:
        # The job was never optimized, so lifecycle has its own (correct)
        # reason to refuse this reject - not what this test is about.
        pass


def test_enforcing_policy_still_requires_a_real_directory():
    with pytest.raises(TypeError):
        EnforcingPolicy(object())


# ----------------------------------------------------------------------
# 7. UnenforcedPolicy regression (T-15, N)
# ----------------------------------------------------------------------


def test_unenforced_policy_still_permits_everything_by_default(tmp_path: Path):
    """The shipped default (UnenforcedPolicy) is unchanged by 10.1D.3: it
    has no topology and consults no directory, so it never asks either
    question - see backend.app.identity.authorization.UnenforcedPolicy.
    """

    service = build_service(tmp_path)
    report(service, IN_XY, actor=WORKER)

    try:
        JobOptimizationService(service).optimize_corridor(CORRIDOR, actor=ENGINEER)
    except AuthorizationDenied:  # pragma: no cover - the thing being refuted
        pytest.fail("UnenforcedPolicy refused an action")
    except Exception:
        # Any downstream solver/possession outcome is not this test's
        # concern; only that authorization never blocks it.
        pass


def test_corridor_scoped_and_section_scoped_action_tables_are_unchanged():
    """No role table change: 10.1D.3 changes HOW a corridor-scoped action
    is authorized, never WHICH role holds it (role_actions.py untouched
    in substance)."""

    from backend.app.identity.role_actions import (
        CORRIDOR_SCOPED_ACTIONS,
        ROLE_ACTIONS,
        SECTION_SCOPED_ACTIONS,
    )

    assert CORRIDOR_SCOPED_ACTIONS == {
        A.REQUEST_OPTIMIZATION,
        A.READ_OPTIMIZATION_RUN,
    }
    assert A.REQUEST_OPTIMIZATION in ROLE_ACTIONS[ActorRole.ENGINEER]
    assert A.READ_OPTIMIZATION_RUN in ROLE_ACTIONS[ActorRole.ENGINEER]
    assert A.READ_OPTIMIZATION_RUN not in ROLE_ACTIONS[ActorRole.AUTHORITY]
    assert A.READ_OPTIMIZATION_RUN not in ROLE_ACTIONS[ActorRole.WORKER]
    assert A.REQUEST_OPTIMIZATION not in SECTION_SCOPED_ACTIONS
    assert A.READ_OPTIMIZATION_RUN not in SECTION_SCOPED_ACTIONS


# ----------------------------------------------------------------------
# 8. HTTP-level checks (T-12)
# ----------------------------------------------------------------------


@pytest.fixture()
def http_service(tmp_path: Path, monkeypatch):
    """A JobService AND JobOptimizationService, both wired into the
    router, on the three-section corridor."""

    import backend.app.jobs.router as router_module
    from backend.app.api.main import app

    service = build_service(tmp_path)
    monkeypatch.setattr(router_module, "service", service)
    monkeypatch.setattr(
        router_module, "optimization_service", JobOptimizationService(service)
    )

    return TestClient(app), service


def test_http_optimize_jobs_denies_partial_scope_engineer(http_service):
    http, service = http_service
    report(service, IN_XY)

    service.authorization = enforcing(
        assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY)
    )

    response = http.post(
        f"/v1/corridors/{CORRIDOR}/optimize-jobs",
        headers={"X-Actor-Id": ENGINEER_ID, "X-Actor-Role": "ENGINEER"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "AUTHORIZATION_DENIED"


def test_http_optimize_jobs_permits_corridor_complete_engineer(http_service):
    """Corridor-complete scope must not be refused by AUTHORIZATION.
    Any downstream solver/possession outcome (a 409, say) is not this
    test's concern - see the same caution in
    test_slice10_1d_scope_enforcement.test_an_optimization_request_in_
    the_engineers_own_corridor_passes_the_seam.
    """

    http, service = http_service
    report(service, IN_XY)

    service.authorization = enforcing(
        assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY, S_YZ, S_ZW)
    )

    response = http.post(
        f"/v1/corridors/{CORRIDOR}/optimize-jobs",
        headers={"X-Actor-Id": ENGINEER_ID, "X-Actor-Role": "ENGINEER"},
    )

    assert response.status_code != 403
    if response.status_code != 200:
        assert response.json().get("code") != "AUTHORIZATION_DENIED"


def test_http_optimization_run_denies_partial_scope_engineer(http_service):
    http, service = http_service
    _record_run(service, "RUN-HTTP-1", CORRIDOR)

    service.authorization = enforcing(
        assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY)
    )

    response = http.get(
        "/v1/optimization-runs/RUN-HTTP-1",
        headers={"X-Actor-Id": ENGINEER_ID, "X-Actor-Role": "ENGINEER"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "AUTHORIZATION_DENIED"


def test_http_optimization_run_permits_corridor_complete_engineer(http_service):
    http, service = http_service
    _record_run(service, "RUN-HTTP-2", CORRIDOR)

    service.authorization = enforcing(
        assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY, S_YZ, S_ZW)
    )

    response = http.get(
        "/v1/optimization-runs/RUN-HTTP-2",
        headers={"X-Actor-Id": ENGINEER_ID, "X-Actor-Role": "ENGINEER"},
    )

    assert response.status_code == 200
    assert response.json()["run_id"] == "RUN-HTTP-2"


def test_http_optimization_run_denies_authority_by_role(http_service):
    http, service = http_service
    _record_run(service, "RUN-HTTP-3", CORRIDOR)

    service.authorization = fully_scoped()

    response = http.get(
        "/v1/optimization-runs/RUN-HTTP-3",
        headers={"X-Actor-Id": AUTHORITY_ID, "X-Actor-Role": "AUTHORITY"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "AUTHORIZATION_DENIED"
