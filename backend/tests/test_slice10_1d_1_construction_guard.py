"""Slice 10.1D.1: the construction-time resource-aware policy guarantee.

WHAT 10.1D LEFT OPEN
    10.1D put `require_resource_aware` at the route wiring
    (backend.app.jobs.router). That covered the one JobService this
    application builds today and nothing else: a future production
    entrypoint constructing its own JobService with an enforcing
    two-argument policy would have skipped every railway scope check
    with no error anywhere.

WHAT THIS PINS
    The guarantee now hangs off the construction boundary of the object
    that ASKS the question - JobService.authorization - so it holds for
    every entrypoint that will ever build one, and for a policy swapped
    in after construction on exactly the same terms.

    THE INVARIANT
        No JobService, however constructed and whoever constructs it,
        can hold a policy that declares enforcing = True and implements
        no authorize_resource.

    Nothing here is enforcement of any permission - see
    test_slice10_1d_scope_enforcement.py. The shipped application still
    installs UnenforcedPolicy and still enforces nothing.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from backend.app.identity.authorization import (
    AuthorizationDenied,
    JobAction,
    PolicyConfigurationError,
    ResourceAwarePolicy,
    RoleActionOnlyPolicy,
    UnenforcedPolicy,
    is_resource_aware,
    require_resource_aware,
)
from backend.app.identity.resource import ResourceLocation
from backend.app.jobs.service import JobService
from backend.tests.slice10_1d_helpers import (
    CORRIDOR,
    S_XY,
    WORKER,
    build_service,
    count_events,
    fully_scoped,
    report,
)


# ----------------------------------------------------------------------
# Test doubles: the three shapes the guard distinguishes
# ----------------------------------------------------------------------


class HalfBuiltEnforcingPolicy:
    """Enforcing by its own declaration, and cannot decide a resource.

    The exact shape 10.1D.1 exists to keep out of a JobService. Written
    by hand rather than by subclassing ResourceAwarePolicy, because that
    base already refuses it at instantiation - this is the policy that
    was never built on the base at all.
    """

    enforcing = True

    def __init__(self, *denied: JobAction):
        self.denied = set(denied)

    def authorize(self, actor, action):
        if action in self.denied:
            raise AuthorizationDenied(actor, action, "denied by test policy")


class SeamOnlyPolicy(RoleActionOnlyPolicy):
    """A legacy role/action-seam double: two arguments, enforcing False."""

    def __init__(self, *denied: JobAction):
        self.denied = set(denied)
        self.calls: list[JobAction] = []

    def authorize(self, actor, action):
        self.calls.append(action)
        if action in self.denied:
            raise AuthorizationDenied(actor, action, "denied by test policy")


class ResourceAwareEnforcingPolicy(ResourceAwarePolicy):
    """A valid enforcing policy: both questions, both answerable."""

    enforcing = True

    def __init__(self) -> None:
        self.role_calls: list[JobAction] = []
        self.resource_calls: list[JobAction] = []

    def authorize(self, actor, action):
        self.role_calls.append(action)

    def authorize_resource(self, actor, action, location):
        self.resource_calls.append(action)


# ----------------------------------------------------------------------
# A. An enforcing policy with no authorize_resource cannot cross the
#    production service boundary - by construction or by assignment
# ----------------------------------------------------------------------


def test_construction_refuses_an_enforcing_two_argument_policy(tmp_path: Path):
    with pytest.raises(PolicyConfigurationError) as excinfo:
        build_service(tmp_path, authorization=HalfBuiltEnforcingPolicy())

    assert "authorize_resource" in str(excinfo.value)
    assert "HalfBuiltEnforcingPolicy" in str(excinfo.value)


def test_a_bare_jobservice_also_refuses_it(tmp_path: Path):
    """Not a property of the test helper: of JobService itself.

    The smallest possible construction - no repository, no corridor, no
    registry, exactly what a new production entrypoint would write.
    """

    from backend.app.jobs.repository import JobRepository

    with pytest.raises(PolicyConfigurationError):
        JobService(
            repository=JobRepository(tmp_path / "jobs.db"),
            authorization=HalfBuiltEnforcingPolicy(),
        )


def test_reassignment_refuses_it_too_and_leaves_the_old_policy_in_place(
    tmp_path: Path,
):
    """The setter is the other half: a guarded __init__ alone is not a guard."""

    service = build_service(tmp_path)
    original = service.authorization

    with pytest.raises(PolicyConfigurationError):
        service.authorization = HalfBuiltEnforcingPolicy()

    assert service.authorization is original
    assert is_resource_aware(service.authorization)


def test_the_refused_service_never_became_usable(tmp_path: Path):
    """A refused construction produces no service and no database rows."""

    from backend.app.jobs.repository import JobRepository

    with pytest.raises(PolicyConfigurationError):
        JobService(
            repository=JobRepository(tmp_path / "jobs.db"),
            authorization=HalfBuiltEnforcingPolicy(),
        )

    # Whatever the repository created, no job and no event exists.
    assert count_events(tmp_path / "jobs.db") == 0


def test_the_policy_is_installed_before_the_constructor_has_any_side_effect():
    """The refusal precedes the repository, the history and the dataset.

    Asserted from source rather than by constructing a bare JobService:
    a default-constructed service opens this deployment's real database,
    which a test must never do. The ORDER is the whole property - a
    policy this service may not hold is refused before any file is
    opened, any dataset is loaded and any collaborator is built.
    """

    lines = inspect.getsource(JobService.__init__).splitlines()

    def first(fragment: str) -> int:
        for index, line in enumerate(lines):
            if line.strip().startswith(fragment):
                return index
        raise AssertionError(f"not found in __init__: {fragment}")

    policy_line = first("self.authorization = ")

    for later in (
        "self.repository = ",
        "self.history = ",
        "self.asset_policy = ",
        "self.clock",
    ):
        assert policy_line < first(later), later


def test_the_seam_itself_fails_closed_if_such_a_policy_ever_appears(
    tmp_path: Path,
):
    """The last backstop, reachable only by defeating the property.

    Writing the private attribute is not something any caller does; it is
    the one way left to install such a policy, and it must still not end
    in a silently permitted railway scope.
    """

    service = build_service(tmp_path)
    job = report(service)

    service._authorization = HalfBuiltEnforcingPolicy()

    with pytest.raises(PolicyConfigurationError) as excinfo:
        service.job_detail(job["job_id"], actor=WORKER)

    assert "authorize_resource" in str(excinfo.value)


def test_the_backstop_never_decides_such_a_check_as_permitted(tmp_path: Path):
    """PolicyConfigurationError is not an AuthorizationDenied, and is not a pass."""

    service = build_service(tmp_path)
    job = report(service)
    service._authorization = HalfBuiltEnforcingPolicy()

    with pytest.raises(PolicyConfigurationError):
        service.notify(job["job_id"], actor=WORKER)

    assert service.repository.get(job["job_id"])["status"] == "reported"


# ----------------------------------------------------------------------
# B. UnenforcedPolicy still constructs and works exactly as before
# ----------------------------------------------------------------------


def test_the_default_policy_is_still_unenforced_and_installs(tmp_path: Path):
    service = build_service(tmp_path)

    assert isinstance(service.authorization, UnenforcedPolicy)
    assert service.authorization.enforcing is False
    assert is_resource_aware(service.authorization)


def test_the_default_policy_still_permits_a_whole_report_and_read(
    tmp_path: Path,
):
    service = build_service(tmp_path)

    job = report(service)

    assert job["status"] == "reported"
    assert service.job_detail(job["job_id"], actor=WORKER)["job_id"] == job["job_id"]
    assert [j["job_id"] for j in service.jobs_page(limit=10, actor=WORKER).items] == [
        job["job_id"]
    ]


def test_the_shipped_application_still_enforces_nothing():
    from backend.app.jobs import router as router_module

    assert isinstance(router_module.service.authorization, UnenforcedPolicy)
    assert router_module.service.authorization.enforcing is False


# ----------------------------------------------------------------------
# C. The legacy two-argument seam doubles still test the old seam
# ----------------------------------------------------------------------


def test_a_role_action_only_policy_still_installs_and_permits(tmp_path: Path):
    policy = SeamOnlyPolicy()
    service = build_service(tmp_path, authorization=policy)

    job = report(service)

    assert job["status"] == "reported"
    assert policy.calls == [JobAction.REPORT_JOB]


def test_a_role_action_only_policy_still_denies_and_writes_nothing(
    tmp_path: Path,
):
    service = build_service(
        tmp_path, authorization=SeamOnlyPolicy(JobAction.REPORT_JOB)
    )

    with pytest.raises(AuthorizationDenied):
        report(service)

    assert service.repository.list_all() == []
    assert count_events(tmp_path / "jobs.db") == 0


def test_a_role_action_only_policy_is_still_never_asked_the_resource_question(
    tmp_path: Path,
):
    policy = SeamOnlyPolicy()
    service = build_service(tmp_path, authorization=policy)

    job = report(service)
    service.job_detail(job["job_id"], actor=WORKER)

    assert not is_resource_aware(policy)
    assert policy.calls == [JobAction.REPORT_JOB, JobAction.READ_JOB]


def test_the_base_declares_it_is_not_an_enforcing_control():
    """enforcing = False is the honest claim, and it is not overridable by accident."""

    assert RoleActionOnlyPolicy.enforcing is False
    assert SeamOnlyPolicy().enforcing is False

    # It is abstract: it cannot be instantiated as a policy in its own
    # right, and a subclass must still answer the role/action question.
    with pytest.raises(TypeError):
        RoleActionOnlyPolicy()  # type: ignore[abstract]


def test_a_seam_double_that_reclaims_enforcing_is_refused(tmp_path: Path):
    """Subclassing the base is not a bypass: the declaration is what is checked."""

    class Dishonest(RoleActionOnlyPolicy):
        enforcing = True

        def authorize(self, actor, action):
            return None

    with pytest.raises(PolicyConfigurationError):
        build_service(tmp_path, authorization=Dishonest())


def test_every_seam_double_in_the_suite_is_non_enforcing():
    """The eight legacy policies, checked from source rather than by memory.

    Any class in the suite that declares enforcing = True must also
    define authorize_resource - otherwise it is the very shape the guard
    refuses, and it would fail the moment a test installed it.
    """

    offenders = []

    for path in sorted(Path("backend/tests").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue

            declares_enforcing_true = any(
                isinstance(stmt, ast.Assign)
                and any(
                    isinstance(t, ast.Name) and t.id == "enforcing"
                    for t in stmt.targets
                )
                and isinstance(stmt.value, ast.Constant)
                and stmt.value.value is True
                for stmt in node.body
            )

            if not declares_enforcing_true:
                continue

            has_resource = any(
                isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
                and stmt.name == "authorize_resource"
                for stmt in node.body
            )

            defines_authorize = any(
                isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
                and stmt.name == "authorize"
                for stmt in node.body
            )

            if defines_authorize and not has_resource:
                # Permitted only where the class exists to BE refused.
                if node.name in {
                    "HalfBuiltEnforcingPolicy",
                    "EnforcingLegacyPolicy",
                    "Halfbuilt",
                    "Dishonest",
                }:
                    continue
                offenders.append(f"{path.name}:{node.name}")

    assert offenders == [], offenders


# ----------------------------------------------------------------------
# D. A valid resource-aware enforcing policy constructs successfully
# ----------------------------------------------------------------------


def test_a_resource_aware_enforcing_policy_constructs(tmp_path: Path):
    policy = ResourceAwareEnforcingPolicy()
    service = build_service(tmp_path, authorization=policy)

    assert service.authorization is policy
    assert policy.enforcing is True


def test_a_resource_aware_enforcing_policy_is_asked_both_questions(
    tmp_path: Path,
):
    policy = ResourceAwareEnforcingPolicy()
    service = build_service(tmp_path, authorization=policy)

    job = report(service)
    service.job_detail(job["job_id"], actor=WORKER)

    assert policy.role_calls == [JobAction.REPORT_JOB, JobAction.READ_JOB]
    assert policy.resource_calls == [JobAction.REPORT_JOB, JobAction.READ_JOB]


def test_the_real_enforcing_policy_still_constructs_and_reassigns(
    tmp_path: Path,
):
    """backend.app.identity.policy.EnforcingPolicy crosses the boundary freely."""

    service = build_service(tmp_path, authorization=fully_scoped())

    assert service.authorization.enforcing is True

    service.authorization = fully_scoped()

    assert is_resource_aware(service.authorization)


def test_require_resource_aware_still_returns_the_policy_it_was_given():
    policy = ResourceAwareEnforcingPolicy()

    assert require_resource_aware(policy) is policy
    assert require_resource_aware(UnenforcedPolicy()) is not None


# ----------------------------------------------------------------------
# G. The router's defence in depth is retained and still correct
# ----------------------------------------------------------------------


def test_the_router_still_states_the_requirement_where_it_picks_a_policy():
    from backend.app.jobs import router as router_module

    source = inspect.getsource(router_module)

    assert "require_resource_aware" in source
    assert is_resource_aware(router_module.service.authorization)


# ----------------------------------------------------------------------
# H. No production construction path can install such a policy
# ----------------------------------------------------------------------


def _app_sources() -> list[Path]:
    return sorted(Path("backend/app").rglob("*.py"))


def _declared_enforcing(node: ast.ClassDef):
    """The literal the class assigns to `enforcing`, or None if it does not."""

    for stmt in node.body:
        if not isinstance(stmt, ast.Assign):
            continue
        if not any(
            isinstance(t, ast.Name) and t.id == "enforcing" for t in stmt.targets
        ):
            continue
        if isinstance(stmt.value, ast.Constant):
            return stmt.value.value

    return None


def test_no_production_module_defines_an_enforcing_two_argument_policy():
    """Every enforcing policy under backend/app answers both questions.

    A class that answers only the role/action question is admissible ONLY
    if it declares enforcing = False in so many words - which is what
    RoleActionOnlyPolicy does, and why it is not listed here. Anything
    else is the shape the construction guard refuses, and shipping one
    would mean a deployment could import it and be refused at startup.
    """

    offenders = []

    for path in _app_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue

            methods = {
                stmt.name
                for stmt in node.body
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
            }

            if "authorize" not in methods or "authorize_resource" in methods:
                continue

            if _declared_enforcing(node) is False:
                continue

            offenders.append(f"{path}:{node.name}")

    assert offenders == [], offenders


def test_no_production_module_subclasses_the_role_action_only_base():
    """RoleActionOnlyPolicy is a test instrument. A deployment may not install one."""

    offenders = []

    for path in _app_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue

            for base in node.bases:
                name = (
                    base.attr if isinstance(base, ast.Attribute)
                    else base.id if isinstance(base, ast.Name)
                    else None
                )
                if name == "RoleActionOnlyPolicy":
                    offenders.append(f"{path}:{node.name}")

    assert offenders == [], offenders


def test_the_service_installs_every_policy_through_the_guard():
    """From source: nothing in JobService writes the policy past the setter.

    The property is the one door. If a future edit were to assign
    self._authorization directly anywhere else in the service, the
    guarantee would have a second, unguarded entrance.
    """

    tree = ast.parse(inspect.getsource(inspect.getmodule(JobService)))

    writes = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue

        targets = node.targets if isinstance(node, ast.Assign) else [node.target]

        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "_authorization"
            ):
                writes.append(node.lineno)

    # Exactly one: the setter's own line.
    assert len(writes) == 1, writes

    setter = JobService.__dict__["authorization"].fset
    assert "require_resource_aware" in inspect.getsource(setter)


def test_every_jobservice_construction_in_the_application_is_guarded():
    """Not by inspection of call sites - by the constructor itself.

    This is the whole point of 10.1D.1: a static audit of construction
    SITES can only ever cover the sites that exist today. The guard is
    inside JobService.__init__, so the property below is true of every
    site, present and future.
    """

    init_source = inspect.getsource(JobService.__init__)

    assert "self.authorization = " in init_source
    assert "self._authorization" not in init_source


# ----------------------------------------------------------------------
# F. No lifecycle or event behaviour changed
# ----------------------------------------------------------------------


def test_an_authorization_denial_still_records_nothing(tmp_path: Path):
    service = build_service(
        tmp_path, authorization=SeamOnlyPolicy(JobAction.REPORT_JOB)
    )

    with pytest.raises(AuthorizationDenied):
        report(service)

    assert count_events(tmp_path / "jobs.db") == 0


def test_a_resource_denial_still_records_nothing(tmp_path: Path):
    class DenyResource(ResourceAwarePolicy):
        enforcing = True

        def authorize(self, actor, action):
            return None

        def authorize_resource(self, actor, action, location):
            raise AuthorizationDenied(actor, action, "resource denied by test")

    service = build_service(tmp_path, authorization=DenyResource())

    with pytest.raises(AuthorizationDenied):
        report(service)

    assert count_events(tmp_path / "jobs.db") == 0


def test_a_permitted_report_still_writes_its_ordinary_events(tmp_path: Path):
    """The guard is a construction check; it adds and removes no event."""

    service = build_service(tmp_path)
    job = report(service)

    assert job["status"] == "reported"
    assert count_events(tmp_path / "jobs.db") > 0


def test_the_resource_question_still_receives_only_a_location(tmp_path: Path):
    seen: list[object] = []

    class Watching(ResourceAwarePolicy):
        enforcing = True

        def authorize(self, actor, action):
            return None

        def authorize_resource(self, actor, action, location):
            seen.append(location)

    service = build_service(tmp_path, authorization=Watching())
    report(service)

    assert seen
    assert all(isinstance(loc, ResourceLocation) for loc in seen)
    assert seen[0].corridor_id == CORRIDOR
    assert seen[0].section_id == S_XY
