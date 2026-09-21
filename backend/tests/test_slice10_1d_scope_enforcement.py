"""Slice 10.1D: railway scope enforcement, and the fail-closed matrix.

Every ambiguity denies. The three results 10.1C defined survive the trip
through a real policy: only MATCH permits, NO_MATCH denies, and
UNRESOLVED - an absent, blank or multi-section resource - denies too.
Absence of scope is never a global scope.

The corridor here has three sections so that "outside my scope" is
reachable at all; on the single-section deployment default it is not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.identity.actor import ActorRole, human_actor
from backend.app.identity.authorization import AuthorizationDenied, JobAction
from backend.app.identity.policy import EnforcingPolicy, StaticScopeDirectory
from backend.app.identity.resource import ResourceLocation
from backend.app.identity.scope import RailwayScope
from backend.tests.slice10_1d_helpers import (
    ADMIN,
    ADMIN_ID,
    AUTHORITY,
    AUTHORITY_ID,
    CORRIDOR,
    ENGINEER,
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
    count_events,
    directory,
    enforcing,
    fully_scoped,
    person,
    report,
    scope,
)

A = JobAction


def _worker_scoped(*sections: str) -> EnforcingPolicy:
    return enforcing(assignment(WORKER_ID, ActorRole.WORKER, *sections))


def _at(section_id, corridor_id=CORRIDOR) -> ResourceLocation:
    return ResourceLocation(corridor_id=corridor_id, section_id=section_id)


# ----------------------------------------------------------------------
# The policy in isolation
# ----------------------------------------------------------------------


def test_a_section_inside_the_scope_is_permitted():
    policy = _worker_scoped(S_XY)

    assert policy.authorize_resource(WORKER, A.READ_JOB, _at(S_XY)) is None


def test_every_section_of_a_multi_section_scope_is_permitted():
    policy = _worker_scoped(S_XY, S_YZ, S_ZW)

    for section in (S_XY, S_YZ, S_ZW):
        assert policy.authorize_resource(WORKER, A.READ_JOB, _at(section)) is None


def test_a_section_outside_the_scope_is_denied():
    policy = _worker_scoped(S_XY)

    with pytest.raises(AuthorizationDenied) as excinfo:
        policy.authorize_resource(WORKER, A.READ_JOB, _at(S_YZ))

    assert "outside every section" in str(excinfo.value)


def test_no_adjacent_section_is_inferred():
    policy = _worker_scoped(S_YZ)

    for neighbour in (S_XY, S_ZW):
        with pytest.raises(AuthorizationDenied):
            policy.authorize_resource(WORKER, A.READ_JOB, _at(neighbour))


def test_holding_one_section_is_never_the_whole_corridor():
    policy = _worker_scoped(S_XY)

    denied = 0
    for section in (S_YZ, S_ZW, "SOME-OTHER-SECTION"):
        with pytest.raises(AuthorizationDenied):
            policy.authorize_resource(WORKER, A.READ_JOB, _at(section))
        denied += 1

    assert denied == 3


def test_an_unresolved_section_is_denied_and_says_so():
    policy = _worker_scoped(S_XY)

    for missing in (None, "", "   "):
        with pytest.raises(AuthorizationDenied) as excinfo:
            policy.authorize_resource(WORKER, A.READ_JOB, _at(missing))

        assert "exactly one railway section" in str(excinfo.value)


def test_an_unresolved_corridor_is_denied():
    policy = _worker_scoped(S_XY)

    with pytest.raises(AuthorizationDenied):
        policy.authorize_resource(WORKER, A.READ_JOB, _at(S_XY, corridor_id=None))


def test_a_different_corridor_is_denied():
    policy = _worker_scoped(S_XY)

    with pytest.raises(AuthorizationDenied):
        policy.authorize_resource(
            WORKER, A.READ_JOB, _at(S_XY, corridor_id=OTHER_CORRIDOR)
        )


def test_an_identical_section_name_in_another_corridor_does_not_match():
    """Cross-corridor collision: same string, different corridor, no match."""

    policy = enforcing(
        assignment(WORKER_ID, ActorRole.WORKER, S_XY, corridor_id=OTHER_CORRIDOR)
    )

    # In its own corridor it matches.
    assert (
        policy.authorize_resource(
            WORKER, A.READ_JOB, _at(S_XY, corridor_id=OTHER_CORRIDOR)
        )
        is None
    )

    # The same section string in THIS deployment's corridor does not.
    with pytest.raises(AuthorizationDenied):
        policy.authorize_resource(WORKER, A.READ_JOB, _at(S_XY))


def test_holding_no_scope_at_all_denies_everything():
    policy = enforcing()  # a directory with people but no assignments

    with pytest.raises(AuthorizationDenied) as excinfo:
        policy.authorize_resource(WORKER, A.READ_JOB, _at(S_XY))

    assert "no railway scope" in str(excinfo.value)


def test_an_actor_the_directory_never_heard_of_denies():
    policy = _worker_scoped(S_XY)
    stranger = human_actor("WORKER-999", ActorRole.WORKER)

    with pytest.raises(AuthorizationDenied):
        policy.authorize_resource(stranger, A.READ_JOB, _at(S_XY))


def test_scope_is_role_specific_and_never_borrowed_from_another_role():
    policy = enforcing(
        assignment(WORKER_ID, ActorRole.WORKER, S_XY),
        assignment(AUTHORITY_ID, ActorRole.AUTHORITY, S_ZW),
    )

    # The same person id in a role it holds no scope under gets nothing.
    other_role = human_actor(WORKER_ID, ActorRole.AUTHORITY)

    with pytest.raises(AuthorizationDenied):
        policy.authorize_resource(other_role, A.COMMIT_BLOCK, _at(S_XY))


def test_a_location_of_the_wrong_type_is_a_type_error_not_a_permit():
    policy = _worker_scoped(S_XY)

    for bad in (None, {"corridor_id": CORRIDOR, "section_id": S_XY}, S_XY):
        with pytest.raises(TypeError):
            policy.authorize_resource(WORKER, A.READ_JOB, bad)


def test_the_resource_question_re_checks_the_role_on_its_own():
    """Called directly, it must not be weaker than the full sequence."""

    policy = _worker_scoped(S_XY)

    with pytest.raises(AuthorizationDenied):
        policy.authorize_resource(WORKER, A.COMMIT_BLOCK, _at(S_XY))


# ----------------------------------------------------------------------
# ADMIN
# ----------------------------------------------------------------------


def test_admin_has_no_action_and_no_scope():
    policy = fully_scoped()

    with pytest.raises(AuthorizationDenied):
        policy.authorize(ADMIN, A.COMMIT_BLOCK)

    with pytest.raises(AuthorizationDenied):
        policy.authorize_resource(ADMIN, A.READ_JOB, _at(S_XY))


def test_admin_cannot_be_given_a_scope_assignment_at_all():
    from backend.app.identity.assignments import AssignmentError

    with pytest.raises(AssignmentError) as excinfo:
        assignment(ADMIN_ID, ActorRole.ADMIN, S_XY, S_YZ, S_ZW)

    assert excinfo.value.reason == "ADMIN_HAS_NO_SCOPE"


# ----------------------------------------------------------------------
# Corridor-scoped actions
# ----------------------------------------------------------------------


def test_a_corridor_action_is_permitted_by_any_scope_in_that_corridor():
    policy = enforcing(assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY))

    assert (
        policy.authorize_resource(ENGINEER, A.REQUEST_OPTIMIZATION, _at(None))
        is None
    )


def test_a_corridor_action_ignores_the_section_entirely():
    policy = enforcing(assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY))

    # Holding only X-Y, the engineer may still request an optimization -
    # the run is a corridor-level thing. Naming a section it does NOT
    # hold changes nothing, because the section is not what is matched.
    assert (
        policy.authorize_resource(ENGINEER, A.REQUEST_OPTIMIZATION, _at(S_ZW))
        is None
    )


def test_a_corridor_action_in_another_corridor_is_denied():
    policy = enforcing(assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY))

    with pytest.raises(AuthorizationDenied) as excinfo:
        policy.authorize_resource(
            ENGINEER, A.REQUEST_OPTIMIZATION, _at(None, corridor_id=OTHER_CORRIDOR)
        )

    assert "no scope in corridor" in str(excinfo.value)


@pytest.mark.parametrize("missing", [None, "", "   "])
def test_a_corridor_action_with_no_corridor_is_denied(missing):
    policy = enforcing(assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY))

    with pytest.raises(AuthorizationDenied) as excinfo:
        policy.authorize_resource(
            ENGINEER, A.READ_OPTIMIZATION_RUN, _at(None, corridor_id=missing)
        )

    assert "names no corridor" in str(excinfo.value)


def test_a_corridor_action_never_becomes_a_section_grant():
    """Corridor membership is not permission over every section in it."""

    policy = enforcing(assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY))

    # Corridor-scoped: permitted.
    assert (
        policy.authorize_resource(ENGINEER, A.REQUEST_OPTIMIZATION, _at(None))
        is None
    )

    # Section-scoped on a section the engineer does not hold: denied.
    with pytest.raises(AuthorizationDenied):
        policy.authorize_resource(ENGINEER, A.READ_JOB, _at(S_ZW))


# ----------------------------------------------------------------------
# The directory
# ----------------------------------------------------------------------


def test_the_directory_returns_nothing_for_a_non_human_role():
    d = directory(assignment(WORKER_ID, ActorRole.WORKER, S_XY))

    for role in (ActorRole.SYSTEM, ActorRole.UNIDENTIFIED):
        assert d.scopes_for_actor(WORKER_ID, role) == ()


def test_the_directory_returns_nothing_for_an_unknown_actor():
    d = directory(assignment(WORKER_ID, ActorRole.WORKER, S_XY))

    assert d.scopes_for_actor("NOBODY-1", ActorRole.WORKER) == ()


def test_the_directory_separates_roles_for_one_person():
    d = directory(
        assignment(WORKER_ID, ActorRole.WORKER, S_XY),
        assignment(WORKER_ID, ActorRole.AUTHORITY, S_ZW),
    )

    assert d.scopes_for_actor(WORKER_ID, ActorRole.WORKER) == (scope(S_XY),)
    assert d.scopes_for_actor(WORKER_ID, ActorRole.AUTHORITY) == (scope(S_ZW),)


def test_the_directory_refuses_duplicate_people():
    with pytest.raises(ValueError):
        StaticScopeDirectory(people=[person(WORKER_ID), person(WORKER_ID)])


def test_the_directory_refuses_a_duplicate_role_scope_assignment():
    from backend.app.identity.assignments import AssignmentError

    d = directory(
        assignment(WORKER_ID, ActorRole.WORKER, S_XY),
        assignment(WORKER_ID, ActorRole.WORKER, S_XY),
    )

    with pytest.raises(AssignmentError) as excinfo:
        d.scopes_for_actor(WORKER_ID, ActorRole.WORKER)

    assert excinfo.value.reason == "DUPLICATE_ASSIGNMENT"


def test_a_policy_needs_a_real_directory():
    for bad in (None, object(), {"scopes_for_actor": None}):
        with pytest.raises(TypeError):
            EnforcingPolicy(bad)


# ----------------------------------------------------------------------
# End to end, through the service
# ----------------------------------------------------------------------


def test_a_report_inside_scope_creates_the_job(tmp_path: Path):
    service = build_service(tmp_path, authorization=_worker_scoped(S_XY))

    job = report(service, IN_XY)

    assert job["status"] == "reported"


def test_a_report_outside_scope_creates_nothing(tmp_path: Path):
    service = build_service(tmp_path, authorization=_worker_scoped(S_XY))

    with pytest.raises(AuthorizationDenied):
        report(service, IN_YZ)

    assert service.repository.list_all() == []
    assert count_events(tmp_path / "jobs.db") == 0


def test_an_out_of_scope_job_cannot_be_read(tmp_path: Path):
    service = build_service(tmp_path, authorization=fully_scoped())
    inside = report(service, IN_XY, description="inside")
    outside = report(service, IN_ZW, description="outside")

    service.authorization = _worker_scoped(S_XY)

    assert service.job_detail(inside["job_id"], actor=WORKER)["job_id"] == (
        inside["job_id"]
    )

    for read in (
        lambda jid: service.job_detail(jid, actor=WORKER),
        lambda jid: service.job_history(jid, actor=WORKER),
        lambda jid: service.job_obligation(jid, actor=WORKER),
        lambda jid: service.get_execution(jid, actor=WORKER),
    ):
        with pytest.raises(AuthorizationDenied):
            read(outside["job_id"])


def test_an_out_of_scope_job_cannot_be_mutated(tmp_path: Path):
    service = build_service(tmp_path, authorization=fully_scoped())
    outside = report(service, IN_ZW)
    job_id = outside["job_id"]
    db = tmp_path / "jobs.db"

    service.authorization = enforcing(
        assignment(AUTHORITY_ID, ActorRole.AUTHORITY, S_XY),
        assignment(WORKER_ID, ActorRole.WORKER, S_XY),
    )
    before = count_events(db)

    for call in (
        lambda: service.notify(job_id, actor=AUTHORITY),
        lambda: service.reject_proposal(
            job_id, actor=AUTHORITY, reason="no", expected_proposal_run_id="RUN-1"
        ),
        lambda: service.release_committed_block(
            job_id, actor=AUTHORITY, reason="no", expected_proposal_run_id="RUN-1"
        ),
        lambda: service.start_execution(
            job_id,
            actor=WORKER,
            expected_proposal_run_id="RUN-1",
            actual_start_at="2026-01-01T00:00:00+00:00",
            before_work_evidence=(),
        ),
        lambda: service.complete_execution(
            job_id,
            actor=WORKER,
            execution_id="EXEC-1",
            actual_end_at="2026-01-01T01:00:00+00:00",
            after_work_evidence=(),
        ),
    ):
        with pytest.raises(AuthorizationDenied):
            call()

    assert service.repository.get(job_id)["status"] == "reported"
    assert count_events(db) == before


def test_a_legacy_job_with_no_section_fails_closed(tmp_path: Path):
    """The live jobs.db holds a row whose block_candidate has no section."""

    service = build_service(tmp_path, authorization=fully_scoped())
    job = report(service, IN_XY)
    job_id = job["job_id"]

    # Strip the section exactly as the legacy row lacks it.
    import json
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(tmp_path / "jobs.db")) as conn, conn:
        row = conn.execute(
            "SELECT block_candidate_json FROM maintenance_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()[0]
        candidate = json.loads(row)
        candidate.pop("section_id", None)
        conn.execute(
            "UPDATE maintenance_jobs SET block_candidate_json = ? WHERE job_id = ?",
            (json.dumps(candidate), job_id),
        )
        conn.commit()

    assert "section_id" not in service.repository.get(job_id)["block_candidate"]

    with pytest.raises(AuthorizationDenied) as excinfo:
        service.job_detail(job_id, actor=WORKER)

    assert "exactly one railway section" in str(excinfo.value)


def test_a_multi_section_resource_fails_closed():
    """Spanning several sections is UNRESOLVED even if all are in scope."""

    from backend.app.identity.resource import location_of
    from types import SimpleNamespace

    policy = _worker_scoped(S_XY, S_YZ, S_ZW)
    spanning = location_of(
        SimpleNamespace(section_ids=(S_XY, S_YZ)), corridor_id=CORRIDOR
    )

    assert spanning.section_id is None

    with pytest.raises(AuthorizationDenied) as excinfo:
        policy.authorize_resource(WORKER, A.READ_JOB, spanning)

    assert "exactly one railway section" in str(excinfo.value)


def test_a_contradicting_corridor_is_a_denial_not_a_crash(tmp_path: Path):
    """CORRIDOR_CONFLICT must surface as 403, never as an unhandled 500."""

    service = build_service(tmp_path, authorization=fully_scoped())
    job = report(service, IN_XY)

    # A row claiming a different corridor than this deployment serves.
    contradicting = dict(service.repository.get(job["job_id"]))
    contradicting["corridor_id"] = OTHER_CORRIDOR

    with pytest.raises(AuthorizationDenied) as excinfo:
        service._authorize_job_resource(WORKER, A.READ_JOB, contradicting)

    assert "CORRIDOR_CONFLICT" in str(excinfo.value)


def _record_run(service, run_id: str, corridor_id: str | None) -> None:
    """Put one real optimization-run row in this service's own audit trail."""

    from backend.app.audit.models import OptimizationRunRecord
    from backend.app.audit.repository import AuditRepository

    AuditRepository(service.repository.db_path).record(
        OptimizationRunRecord(
            run_id=run_id,
            actor="ENGINEER-003",
            trigger="TEST",
            corridor_id=corridor_id,
            requested_at="2026-01-01T00:00:00+00:00",
            completed_at="2026-01-01T00:00:01+00:00",
            solver_status="OPTIMAL",
            solve_time_seconds=0.1,
            request_json="{}",
            result_json="{}",
            provenance_snapshot_json=None,
            error=None,
        )
    )


def test_an_optimization_run_in_the_engineers_corridor_is_readable(
    tmp_path: Path,
):
    service = build_service(tmp_path, authorization=fully_scoped())
    _record_run(service, "RUN-1", CORRIDOR)

    service.authorization = enforcing(
        assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY)
    )

    assert service.optimization_run("RUN-1", actor=ENGINEER).run_id == "RUN-1"


def test_an_optimization_run_in_another_corridor_is_denied(tmp_path: Path):
    service = build_service(tmp_path, authorization=fully_scoped())
    _record_run(service, "RUN-2", OTHER_CORRIDOR)

    service.authorization = enforcing(
        assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY)
    )

    with pytest.raises(AuthorizationDenied) as excinfo:
        service.optimization_run("RUN-2", actor=ENGINEER)

    assert "no scope in corridor" in str(excinfo.value)


def test_an_optimization_run_naming_no_corridor_fails_closed(tmp_path: Path):
    """corridor_id is Optional on the stored record. Unknown is not allowed."""

    service = build_service(tmp_path, authorization=fully_scoped())
    _record_run(service, "RUN-3", None)

    service.authorization = enforcing(
        assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY)
    )

    with pytest.raises(AuthorizationDenied) as excinfo:
        service.optimization_run("RUN-3", actor=ENGINEER)

    assert "names no corridor" in str(excinfo.value)


def test_an_optimization_request_is_corridor_authorized_before_any_work(
    tmp_path: Path,
):
    """The denial must beat the optimizer's own refusals, and write nothing."""

    from backend.app.audit.repository import AuditRepository
    from backend.app.jobs.optimization import JobOptimizationService

    service = build_service(tmp_path, authorization=fully_scoped())
    report(service, IN_XY)
    db = tmp_path / "jobs.db"
    before = count_events(db)

    # An engineer scoped to a DIFFERENT corridor entirely.
    service.authorization = enforcing(
        assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY, corridor_id=OTHER_CORRIDOR)
    )

    with pytest.raises(AuthorizationDenied) as excinfo:
        JobOptimizationService(service).optimize_corridor(CORRIDOR, actor=ENGINEER)

    assert "no scope in corridor" in str(excinfo.value)
    assert count_events(db) == before
    assert AuditRepository(service.repository.db_path).list_all() == []


def test_an_optimization_request_in_the_engineers_own_corridor_passes_the_seam(
    tmp_path: Path,
):
    """Not an optimizer test: only that authorization lets it through."""

    from backend.app.jobs.optimization import JobOptimizationService

    service = build_service(tmp_path, authorization=fully_scoped())
    report(service, IN_XY)

    service.authorization = enforcing(
        assignment(ENGINEER_ID, ActorRole.ENGINEER, S_XY)
    )

    try:
        JobOptimizationService(service).optimize_corridor(CORRIDOR, actor=ENGINEER)
    except AuthorizationDenied:  # pragma: no cover - the thing being refuted
        pytest.fail("an in-corridor engineer was refused")
    except Exception:
        # Any downstream solver/possession outcome is fine and is not
        # what this test is about; authorization is what must not refuse.
        pass


def test_the_corridor_rule_receives_the_corridor_and_never_a_section(
    tmp_path: Path,
):
    from backend.app.identity.authorization import ResourceAwarePolicy
    from backend.app.jobs.optimization import JobOptimizationService

    seen: list = []

    class Recording(ResourceAwarePolicy):
        enforcing = False

        def authorize(self, actor, action):
            return None

        def authorize_resource(self, actor, action, location):
            seen.append((action, location))

    service = build_service(tmp_path, authorization=Recording())

    try:
        JobOptimizationService(service).optimize_corridor(CORRIDOR, actor=ENGINEER)
    except Exception:
        pass

    assert (A.REQUEST_OPTIMIZATION, ResourceLocation(CORRIDOR, None)) in seen
