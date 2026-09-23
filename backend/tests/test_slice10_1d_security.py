"""Slice 10.1D: the security properties, stated as tests.

Role separation, the absence of any client-supplied scope or section, the
fail-closed handling of an unknown resource, and a static audit proving
no production path can still reach job data past both checks.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.identity.actor import (
    ActorRole,
    human_actor,
    unidentified_actor,
)
from backend.app.identity.authorization import AuthorizationDenied, JobAction
from backend.app.identity.resource import ResourceLocation
from backend.tests.slice10_1d_helpers import (
    ADMIN,
    AUTHORITY,
    AUTHORITY_ID,
    CORRIDOR,
    ENGINEER,
    ENGINEER_ID,
    IN_XY,
    IN_YZ,
    S_XY,
    S_YZ,
    S_ZW,
    WORKER,
    WORKER_ID,
    assignment,
    build_service,
    count_events,
    enforcing,
    fully_scoped,
    report,
)

A = JobAction
UNIDENTIFIED = unidentified_actor()


def _at(section_id=S_XY, corridor_id=CORRIDOR) -> ResourceLocation:
    return ResourceLocation(corridor_id=corridor_id, section_id=section_id)


# ----------------------------------------------------------------------
# Role separation: each role is refused the others' work
# ----------------------------------------------------------------------


def test_a_worker_cannot_commit_a_block(tmp_path: Path):
    service = build_service(tmp_path, authorization=fully_scoped())
    job = report(service, IN_XY)
    before = count_events(tmp_path / "jobs.db")

    with pytest.raises(AuthorizationDenied):
        service.notify(job["job_id"], actor=WORKER)

    assert service.repository.get(job["job_id"])["status"] == "reported"
    assert count_events(tmp_path / "jobs.db") == before


@pytest.mark.parametrize(
    "action",
    [A.COMMIT_BLOCK, A.REJECT_PROPOSAL, A.POSTPONE_PROPOSAL, A.RELEASE_COMMITTED_BLOCK],
)
def test_a_worker_holds_none_of_the_authority_decisions(action):
    with pytest.raises(AuthorizationDenied):
        fully_scoped().authorize(WORKER, action)


@pytest.mark.parametrize(
    "action",
    [A.START_EXECUTION, A.COMPLETE_JOB, A.REPORT_EXECUTION_NOT_COMPLETED],
)
def test_an_authority_cannot_execute_field_work(action):
    with pytest.raises(AuthorizationDenied):
        fully_scoped().authorize(AUTHORITY, action)


def test_an_engineer_cannot_complete_a_job():
    with pytest.raises(AuthorizationDenied):
        fully_scoped().authorize(ENGINEER, A.COMPLETE_JOB)


def test_an_engineer_cannot_approve_or_report():
    for action in (A.COMMIT_BLOCK, A.REPORT_JOB):
        with pytest.raises(AuthorizationDenied):
            fully_scoped().authorize(ENGINEER, action)


def test_a_worker_and_an_authority_cannot_request_an_optimization():
    for actor in (WORKER, AUTHORITY):
        with pytest.raises(AuthorizationDenied):
            fully_scoped().authorize(actor, A.REQUEST_OPTIMIZATION)


@pytest.mark.parametrize(
    "action",
    [
        A.REPORT_JOB,
        A.COMMIT_BLOCK,
        A.REJECT_PROPOSAL,
        A.POSTPONE_PROPOSAL,
        A.RELEASE_COMMITTED_BLOCK,
        A.START_EXECUTION,
        A.COMPLETE_JOB,
        A.REPORT_EXECUTION_NOT_COMPLETED,
        A.ASSIGN_SCHEDULE,
        A.REQUEST_OPTIMIZATION,
    ],
)
def test_an_admin_can_perform_no_operational_mutation(action):
    with pytest.raises(AuthorizationDenied):
        fully_scoped().authorize(ADMIN, action)


@pytest.mark.parametrize("action", list(JobAction))
def test_an_unidentified_caller_can_perform_nothing(action):
    with pytest.raises(AuthorizationDenied):
        fully_scoped().authorize(UNIDENTIFIED, action)


def test_an_unidentified_caller_cannot_report_a_job(tmp_path: Path):
    service = build_service(tmp_path, authorization=fully_scoped())

    with pytest.raises(AuthorizationDenied):
        report(service, IN_XY, actor=None)

    assert service.repository.list_all() == []
    assert count_events(tmp_path / "jobs.db") == 0


def test_a_system_role_can_perform_nothing_either():
    from backend.app.identity.actor import SYSTEM

    for action in JobAction:
        with pytest.raises(AuthorizationDenied):
            fully_scoped().authorize(SYSTEM, action)


# ----------------------------------------------------------------------
# A client can name a job. It can never name a scope or a section.
# ----------------------------------------------------------------------


def test_no_request_model_carries_a_scope_or_section_field():
    from backend.app.jobs import models

    forbidden = {
        "scope",
        "scopes",
        "section_id",
        "section_ids",
        "sections",
        "railway_scope",
        "permissions",
        "role",
    }

    import pydantic

    for name in dir(models):
        model = getattr(models, name)
        if (
            isinstance(model, type)
            and issubclass(model, pydantic.BaseModel)
            and name.endswith("Request")
        ):
            assert not set(model.model_fields) & forbidden, name


def test_no_route_accepts_a_scope_or_section_header_or_query():
    from backend.app.api.main import app

    schema = app.openapi()

    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            for parameter in operation.get("parameters", []):
                name = parameter["name"].lower()
                assert "scope" not in name, (path, method, name)
                assert "section" not in name, (path, method, name)


def test_the_only_actor_input_is_the_two_documented_headers():
    from backend.app.api import deps
    from backend.app.api.main import app

    assert deps.ACTOR_ID_HEADER == "X-Actor-Id"
    assert deps.ACTOR_ROLE_HEADER == "X-Actor-Role"

    # Whatever the actor dependency declares is what the contract
    # publishes, so assert over the published headers rather than over
    # source text: no scope- or section-shaped header may ever appear.
    declared = {
        parameter["name"]
        for operations in app.openapi()["paths"].values()
        for operation in operations.values()
        for parameter in operation.get("parameters", [])
        if parameter["in"] == "header"
    }

    assert declared <= {"X-Actor-Id", "X-Actor-Role", "Idempotency-Key"}, declared


def test_a_section_cannot_be_spoofed_through_the_request(tmp_path: Path):
    """The section is DERIVED from the track and distances, never supplied."""

    service = build_service(tmp_path, authorization=fully_scoped())

    from backend.app.jobs.models import JobCreateRequest

    assert "section_id" not in JobCreateRequest.model_fields

    # Two reports differing only in their distances land in different
    # sections, and the caller never named either one.
    a = report(service, IN_XY, description="a")
    b = report(service, IN_YZ, description="b")

    assert a["block_candidate"]["section_id"] == S_XY
    assert b["block_candidate"]["section_id"] == S_YZ


def test_a_worker_scoped_elsewhere_cannot_file_a_job_into_its_own_section(
    tmp_path: Path,
):
    """Choosing distances cannot move a job into the reporter's scope."""

    service = build_service(
        tmp_path,
        authorization=enforcing(assignment(WORKER_ID, ActorRole.WORKER, S_ZW)),
    )

    # The worker holds Z-W. A job whose distances resolve to X-Y is
    # refused, and no combination of request fields names a section.
    with pytest.raises(AuthorizationDenied):
        report(service, IN_XY)

    assert service.repository.list_all() == []


def _code_symbols(module) -> set:
    """Every name and attribute the module's CODE uses. Ignores prose."""

    tree = ast.parse(inspect.getsource(module))

    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }


def test_scope_comes_only_from_the_directory():
    from backend.app.identity import policy as policy_module

    symbols = _code_symbols(policy_module)

    # The one source of a scope is the injected directory.
    assert "scopes_for_actor" in symbols

    # Nothing request-shaped can reach it.
    assert not symbols & {
        "Header",
        "Query",
        "Depends",
        "request",
        "headers",
        "payload",
        "body",
        "json",
    }


# ----------------------------------------------------------------------
# Unknown or malformed resources fail closed
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "row",
    [
        {},
        {"block_candidate": None},
        {"block_candidate": {}},
        {"block_candidate": {"section_id": None}},
        {"block_candidate": {"section_id": ""}},
        {"block_candidate": {"section_id": 7}},
        {"block_candidate": "X-Y"},
        {"section_id": S_XY, "block_candidate": {}},
    ],
)
def test_a_malformed_job_row_is_denied_never_permitted(tmp_path: Path, row):
    service = build_service(tmp_path, authorization=fully_scoped())

    with pytest.raises(AuthorizationDenied):
        service._authorize_job_resource(WORKER, A.READ_JOB, row)


def test_a_top_level_section_on_the_row_cannot_override_the_block(
    tmp_path: Path,
):
    """A forged top-level section_id must not become the scope key."""

    service = build_service(
        tmp_path,
        authorization=enforcing(assignment(WORKER_ID, ActorRole.WORKER, S_XY)),
    )

    forged = {
        "job_id": "JOB-1",
        "section_id": S_XY,
        "block_candidate": {"section_id": S_ZW},
    }

    with pytest.raises(AuthorizationDenied):
        service._authorize_job_resource(WORKER, A.READ_JOB, forged)


# ----------------------------------------------------------------------
# Router / service bypass audit
# ----------------------------------------------------------------------


def _router_source() -> str:
    from backend.app.jobs import router

    return inspect.getsource(router)


def test_no_route_reaches_the_repository_directly():
    """Every route goes through JobService, which owns the seam.

    This is the check that would have caught the two unauthorized reads
    the 10.1D gate found: GET /v1/jobs and GET /v1/jobs/{job_id} called
    service.repository directly and passed no seam at all.
    """

    tree = ast.parse(_router_source())

    offenders = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_"):
            continue

        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Attribute)
                and inner.attr == "repository"
                and isinstance(inner.value, ast.Name)
                and inner.value.id == "service"
            ):
                offenders.append(node.name)

    assert offenders == [], f"routes reaching the repository directly: {offenders}"


def test_every_route_that_returns_job_data_takes_an_actor():
    from backend.app.api.main import app

    schema = app.openapi()

    for path, operations in schema["paths"].items():
        if not path.startswith("/v1/"):
            continue

        for method, operation in operations.items():
            headers = {
                p["name"] for p in operation.get("parameters", []) if p["in"] == "header"
            }

            assert "X-Actor-Id" in headers, (path, method)
            assert "X-Actor-Role" in headers, (path, method)


def test_the_list_and_detail_reads_now_pass_the_seam():
    source = _router_source()

    assert "service.jobs_page(" in source
    assert "service.job_detail(" in source


def test_every_public_service_method_that_reads_a_job_authorizes():
    """A public JobService method must not hand back job data unseen."""

    from backend.app.jobs.service import JobService

    tree = ast.parse(inspect.getsource(inspect.getmodule(JobService)))

    # Methods that deliberately hold no seam, each with a stated reason.
    exempt = {
        "__init__",
        # Delegating wrappers: the method they call authorizes.
        "create_job",
        "approve_proposal",
        # Optimizer-internal plumbing, reached only under an already
        # authorized REQUEST_OPTIMIZATION, never from a route.
        "active_block_candidates",
        "possession_inputs",
        "possession_windows",
        "classify_for_optimization",
        "optimization_snapshot",
        "verify_job_asset_reference",
        # Internal writer, called only from _transition.
        "record_rejected_transition",
        # Public resource-authorization helper.
        "authorize_corridor",
        # Slice 10.1D.1: the guarded `authorization` property. Its setter
        # IS the authorization guard (require_resource_aware), not a
        # caller of one; asking it to call authorize() would be circular.
        # It hands back a policy, never a job row.
        "authorization",
    }

    unguarded = []

    for klass in ast.walk(tree):
        if not isinstance(klass, ast.ClassDef) or klass.name != "JobService":
            continue

        for fn in klass.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if fn.name.startswith("_") or fn.name in exempt:
                continue

            body = ast.dump(fn)

            if "'authorize'" not in body:
                unguarded.append(fn.name)

    assert unguarded == [], f"public methods with no authorization: {unguarded}"


def test_the_exempt_optimizer_helpers_are_not_reachable_from_a_route():
    source = _router_source()

    for helper in (
        "active_block_candidates",
        "possession_inputs",
        "possession_windows",
        "classify_for_optimization",
        "optimization_snapshot",
        "list_all",
        "list_page",
    ):
        assert f".{helper}(" not in source, helper


# ----------------------------------------------------------------------
# The shipped application still enforces nothing
# ----------------------------------------------------------------------


def test_the_running_application_ships_the_unenforced_policy():
    from backend.app.identity.authorization import UnenforcedPolicy
    from backend.app.jobs import router as router_module

    assert isinstance(router_module.service.authorization, UnenforcedPolicy)
    assert router_module.service.authorization.enforcing is False


def test_the_unauthenticated_reads_still_work_exactly_as_before():
    from backend.app.api.main import app

    client = TestClient(app)

    assert client.get("/v1/jobs").status_code == 200
    assert client.get("/v1/jobs/JOB-DOES-NOT-EXIST").status_code == 404


def test_declared_headers_are_still_never_treated_as_authenticated():
    from backend.app.api.deps import request_actor
    from backend.app.identity.actor import IdentityAssurance

    actor = request_actor(actor_id="WORKER-042", actor_role="WORKER")

    assert actor.assurance is IdentityAssurance.DECLARED_UNVERIFIED
    # 10.2c: the level exists, but no header can produce it.
    assert actor.assurance is not IdentityAssurance.AUTHENTICATED
