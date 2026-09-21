"""Slice 10.1D: the resource-aware authorization seam itself.

Pins the SHAPE of the seam rather than any particular permission: that
the old two-argument question is unchanged and still answered first, that
the resource question is a separate method asked only after the resource
has been loaded and resolved, that a policy which cannot answer it cannot
be wired into a running application, and that a denial still writes
nothing while a lifecycle refusal still writes its own event.

Nothing here is enforcement - see test_slice10_1d_scope_enforcement.py.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from backend.app.identity.actor import ActorRole, human_actor, unidentified_actor
from backend.app.identity.authorization import (
    AuthorizationDenied,
    AuthorizationPolicy,
    JobAction,
    PolicyConfigurationError,
    ResourceAwarePolicy,
    RoleActionOnlyPolicy,
    UnenforcedPolicy,
    is_resource_aware,
    require_resource_aware,
)
from backend.app.identity.resource import ResourceLocation
from backend.tests.slice10_1d_helpers import (
    AUTHORITY,
    CORRIDOR,
    IN_XY,
    IN_YZ,
    S_XY,
    S_YZ,
    WORKER,
    build_service,
    count_events,
    event_types,
    report,
    section_of,
)


# ----------------------------------------------------------------------
# Test doubles
# ----------------------------------------------------------------------


class LegacyPolicy(RoleActionOnlyPolicy):
    """A pre-10.1D policy: the two-argument method and nothing else.

    Deliberately shaped exactly like the eight policies already living in
    the suite, so "the old ones keep working" is tested rather than
    asserted.

    Slice 10.1D.1 gave that shape a name and an honest declaration:
    RoleActionOnlyPolicy sets enforcing = False, because a policy that
    answers no resource question restricts no railway resource and is not
    a control a deployment may rely on. It may still refuse an individual
    action - that is exactly how these tests exercise the seam - and a
    policy that refuses more than it claims fails closed. See
    EnforcingLegacyPolicy below for the shape that is now REFUSED.
    """

    def __init__(self, *denied: JobAction):
        self.denied = set(denied)
        self.calls: list[JobAction] = []

    def authorize(self, actor, action):
        self.calls.append(action)
        if action in self.denied:
            raise AuthorizationDenied(actor, action, "denied by test policy")


class EnforcingLegacyPolicy:
    """The shape the guard exists to refuse: enforcing, two-argument.

    It claims to be a complete authorization control (enforcing = True)
    and then cannot decide a single railway resource. Never installed in
    a JobService by any test - that is the point: it CANNOT be.
    """

    enforcing = True

    def authorize(self, actor, action):
        return None


class RecordingPolicy(ResourceAwarePolicy):
    """Permits everything, records both questions in the order asked."""

    enforcing = False

    def __init__(self) -> None:
        self.role_calls: list[JobAction] = []
        self.resource_calls: list[tuple[JobAction, ResourceLocation]] = []
        self.order: list[str] = []

    def authorize(self, actor, action):
        self.role_calls.append(action)
        self.order.append(f"role:{action.value}")

    def authorize_resource(self, actor, action, location):
        self.resource_calls.append((action, location))
        self.order.append(f"resource:{action.value}")

    def locations_for(self, action: JobAction) -> list[ResourceLocation]:
        return [loc for act, loc in self.resource_calls if act is action]


class DenyResourcePolicy(ResourceAwarePolicy):
    """Permits every role/action; refuses the named actions by resource."""

    enforcing = True

    def __init__(self, *denied: JobAction):
        self.denied = set(denied)

    def authorize(self, actor, action):
        return None

    def authorize_resource(self, actor, action, location):
        if action in self.denied:
            raise AuthorizationDenied(actor, action, "resource denied by test")


class CountingRepository:
    """Wraps a JobRepository and counts the reads authorization may cause."""

    def __init__(self, inner):
        self._inner = inner
        self.gets = 0
        self.pages = 0
        self.list_alls = 0

    def get(self, job_id):
        self.gets += 1
        return self._inner.get(job_id)

    def list_page(self, **kwargs):
        self.pages += 1
        return self._inner.list_page(**kwargs)

    def list_all(self):
        self.list_alls += 1
        return self._inner.list_all()

    def __getattr__(self, name):
        return getattr(self._inner, name)


# ----------------------------------------------------------------------
# The Protocol and the shipped policy
# ----------------------------------------------------------------------


def test_the_policy_protocol_asks_two_separate_questions():
    assert hasattr(AuthorizationPolicy, "authorize")
    assert hasattr(AuthorizationPolicy, "authorize_resource")


def test_authorize_keeps_its_exact_two_argument_signature():
    params = list(
        inspect.signature(AuthorizationPolicy.authorize).parameters
    )

    assert params == ["self", "actor", "action"]


def test_authorize_resource_takes_a_required_location_not_a_default():
    signature = inspect.signature(AuthorizationPolicy.authorize_resource)

    assert list(signature.parameters) == ["self", "actor", "action", "location"]

    location = signature.parameters["location"]

    # A default of None is the whole trap this design exists to avoid: it
    # would let a caller that forgets the resource be silently permitted.
    assert location.default is inspect.Parameter.empty


def test_no_authorize_overload_accepts_a_resource_argument():
    for policy in (UnenforcedPolicy(), RecordingPolicy()):
        with pytest.raises(TypeError):
            policy.authorize(  # type: ignore[call-arg]
                unidentified_actor(),
                JobAction.READ_JOB,
                ResourceLocation(CORRIDOR, S_XY),
            )


def test_shipped_policy_answers_both_questions_and_still_enforces_nothing():
    policy = UnenforcedPolicy()
    actor = unidentified_actor()

    assert policy.enforcing is False

    for action in JobAction:
        assert policy.authorize(actor, action) is None
        assert (
            policy.authorize_resource(
                actor, action, ResourceLocation(CORRIDOR, S_XY)
            )
            is None
        )


def test_shipped_policy_is_resource_aware():
    assert is_resource_aware(UnenforcedPolicy())


def test_read_job_action_exists_and_is_a_read():
    assert JobAction.READ_JOB.value == "READ_JOB"
    assert JobAction("READ_JOB") is JobAction.READ_JOB


# ----------------------------------------------------------------------
# A policy that cannot answer the resource question cannot be installed
# ----------------------------------------------------------------------


def test_a_resource_aware_subclass_cannot_omit_the_resource_method():
    class Halfbuilt(ResourceAwarePolicy):
        enforcing = True

        def authorize(self, actor, action):
            return None

    with pytest.raises(TypeError):
        Halfbuilt()  # type: ignore[abstract]


def test_a_resource_aware_subclass_cannot_omit_the_role_method():
    class Halfbuilt(ResourceAwarePolicy):
        enforcing = True

        def authorize_resource(self, actor, action, location):
            return None

    with pytest.raises(TypeError):
        Halfbuilt()  # type: ignore[abstract]


def test_a_complete_subclass_constructs():
    class Complete(ResourceAwarePolicy):
        def authorize(self, actor, action):
            return None

        def authorize_resource(self, actor, action, location):
            return None

    assert Complete().enforcing is True


def test_wiring_guard_refuses_an_enforcing_two_argument_policy():
    with pytest.raises(PolicyConfigurationError) as excinfo:
        require_resource_aware(EnforcingLegacyPolicy())

    assert "authorize_resource" in str(excinfo.value)


def test_wiring_guard_admits_a_resource_aware_enforcing_policy():
    policy = DenyResourcePolicy()

    assert require_resource_aware(policy) is policy


def test_wiring_guard_leaves_a_non_enforcing_policy_alone():
    class NonEnforcingLegacy:
        enforcing = False

        def authorize(self, actor, action):
            return None

    policy = NonEnforcingLegacy()

    # It restricts nothing by its own declaration, so it cannot be a
    # scope bypass and there is nothing to refuse.
    assert require_resource_aware(policy) is policy
    assert require_resource_aware(UnenforcedPolicy()) is not None


def test_the_running_application_applies_the_wiring_guard():
    from backend.app.jobs import router as router_module

    assert is_resource_aware(router_module.service.authorization)
    assert "require_resource_aware" in inspect.getsource(router_module)


# ----------------------------------------------------------------------
# Backward compatibility: the eight existing policies keep working
# ----------------------------------------------------------------------


def test_a_two_argument_policy_still_permits_a_whole_report(tmp_path: Path):
    policy = LegacyPolicy()
    service = build_service(tmp_path, authorization=policy)

    job = report(service)

    assert job["status"] == "reported"
    assert policy.calls == [JobAction.REPORT_JOB]


def test_a_two_argument_policy_still_denies_and_creates_nothing(tmp_path: Path):
    service = build_service(
        tmp_path, authorization=LegacyPolicy(JobAction.REPORT_JOB)
    )

    with pytest.raises(AuthorizationDenied):
        report(service)

    assert service.repository.list_all() == []
    assert count_events(tmp_path / "jobs.db") == 0


def test_a_two_argument_policy_is_never_asked_the_resource_question(
    tmp_path: Path,
):
    policy = LegacyPolicy()
    service = build_service(tmp_path, authorization=policy)

    job = report(service)
    service.job_history(job["job_id"], actor=WORKER)
    service.job_detail(job["job_id"], actor=WORKER)

    # It cannot be asked - it has no such method - and the service must
    # not crash trying. The role/action seam still ran for every call.
    assert not is_resource_aware(policy)
    assert policy.calls == [
        JobAction.REPORT_JOB,
        JobAction.READ_JOB_HISTORY,
        JobAction.READ_JOB,
    ]


# ----------------------------------------------------------------------
# Order: role/action first and without I/O, resource after resolution
# ----------------------------------------------------------------------


def test_role_action_denial_happens_before_any_repository_read(tmp_path: Path):
    service = build_service(tmp_path, authorization=LegacyPolicy())
    job = report(service)

    counting = CountingRepository(service.repository)
    service.repository = counting
    service.authorization = LegacyPolicy(JobAction.COMMIT_BLOCK)

    with pytest.raises(AuthorizationDenied):
        service.notify(job["job_id"], actor=AUTHORITY)

    assert counting.gets == 0


def test_role_action_denial_on_a_read_also_reads_nothing(tmp_path: Path):
    service = build_service(tmp_path, authorization=LegacyPolicy())
    job = report(service)

    counting = CountingRepository(service.repository)
    service.repository = counting
    service.authorization = LegacyPolicy(JobAction.READ_JOB)

    with pytest.raises(AuthorizationDenied):
        service.job_detail(job["job_id"], actor=WORKER)

    assert counting.gets == 0
    assert counting.pages == 0
    assert counting.list_alls == 0


def test_both_questions_are_asked_in_order_for_one_read(tmp_path: Path):
    policy = RecordingPolicy()
    service = build_service(tmp_path, authorization=policy)
    job = report(service)

    policy.order.clear()
    service.job_detail(job["job_id"], actor=WORKER)

    assert policy.order == ["role:READ_JOB", "resource:READ_JOB"]


def test_the_resource_question_carries_the_jobs_resolved_section(
    tmp_path: Path,
):
    policy = RecordingPolicy()
    service = build_service(tmp_path, authorization=policy)

    job = report(service, IN_YZ)

    assert section_of(job) == S_YZ
    assert policy.locations_for(JobAction.REPORT_JOB) == [
        ResourceLocation(corridor_id=CORRIDOR, section_id=S_YZ)
    ]


def test_each_job_scoped_action_asks_the_resource_question_exactly_once(
    tmp_path: Path,
):
    policy = RecordingPolicy()
    service = build_service(tmp_path, authorization=policy)
    job = report(service)
    job_id = job["job_id"]

    for call, action in (
        (lambda: service.job_detail(job_id, actor=WORKER), JobAction.READ_JOB),
        (
            lambda: service.job_history(job_id, actor=WORKER),
            JobAction.READ_JOB_HISTORY,
        ),
        (
            lambda: service.job_obligation(job_id, actor=WORKER),
            JobAction.READ_JOB_OBLIGATIONS,
        ),
        (
            lambda: service.get_execution(job_id, actor=WORKER),
            JobAction.READ_JOB_EXECUTION,
        ),
    ):
        policy.resource_calls.clear()
        policy.role_calls.clear()

        call()

        assert policy.role_calls == [action]
        assert [a for a, _ in policy.resource_calls] == [action]


def test_a_read_of_a_missing_job_is_not_found_not_a_resource_denial(
    tmp_path: Path,
):
    policy = RecordingPolicy()
    service = build_service(tmp_path, authorization=policy)

    with pytest.raises(KeyError):
        service.job_detail("JOB-NOPE", actor=WORKER)

    # There was no resource to decide, so the resource question was never
    # asked. Inventing a denial here would turn a 404 into a 403.
    assert policy.resource_calls == []


def test_a_transition_on_a_missing_job_is_left_to_the_transition(
    tmp_path: Path,
):
    policy = RecordingPolicy()
    service = build_service(tmp_path, authorization=policy)

    with pytest.raises(KeyError):
        service.notify("JOB-NOPE", actor=AUTHORITY)

    assert policy.role_calls == [JobAction.COMMIT_BLOCK]
    assert policy.resource_calls == []


# ----------------------------------------------------------------------
# A denial writes nothing; a lifecycle refusal still writes its event
# ----------------------------------------------------------------------


def test_a_resource_denial_changes_no_state_and_records_nothing(
    tmp_path: Path,
):
    service = build_service(tmp_path, authorization=RecordingPolicy())
    job = report(service)
    before = count_events(tmp_path / "jobs.db")

    service.authorization = DenyResourcePolicy(JobAction.COMMIT_BLOCK)

    with pytest.raises(AuthorizationDenied):
        service.notify(job["job_id"], actor=AUTHORITY)

    assert service.repository.get(job["job_id"])["status"] == "reported"
    assert count_events(tmp_path / "jobs.db") == before


def test_a_resource_denial_on_report_creates_no_job(tmp_path: Path):
    service = build_service(
        tmp_path, authorization=DenyResourcePolicy(JobAction.REPORT_JOB)
    )

    with pytest.raises(AuthorizationDenied):
        report(service)

    assert service.repository.list_all() == []
    assert count_events(tmp_path / "jobs.db") == 0


def test_a_lifecycle_refusal_after_authorization_still_records_itself(
    tmp_path: Path,
):
    """Authorization permitting is not lifecycle permitting."""

    service = build_service(tmp_path, authorization=RecordingPolicy())
    job = report(service)
    db = tmp_path / "jobs.db"

    # 'reported' -> 'notified' is not an allowed transition. Authorization
    # says yes (the actor may attempt it); the lifecycle says no, and
    # records the attempt exactly as it always has.
    from backend.app.jobs.lifecycle import InvalidTransitionError

    with pytest.raises(InvalidTransitionError):
        service.notify(job["job_id"], actor=AUTHORITY)

    assert "TRANSITION_REJECTED" in event_types(db)
    assert service.repository.get(job["job_id"])["status"] == "reported"


def test_the_two_refusals_are_distinguishable_and_only_one_is_recorded(
    tmp_path: Path,
):
    service = build_service(tmp_path, authorization=RecordingPolicy())
    job = report(service)
    db = tmp_path / "jobs.db"
    before = event_types(db)

    service.authorization = DenyResourcePolicy(JobAction.COMMIT_BLOCK)
    with pytest.raises(AuthorizationDenied):
        service.notify(job["job_id"], actor=AUTHORITY)

    assert event_types(db) == before

    service.authorization = RecordingPolicy()
    from backend.app.jobs.lifecycle import InvalidTransitionError

    with pytest.raises(InvalidTransitionError):
        service.notify(job["job_id"], actor=AUTHORITY)

    assert event_types(db) == before + ["TRANSITION_REJECTED"]


# ----------------------------------------------------------------------
# Authorization never sees lifecycle state
# ----------------------------------------------------------------------


def test_the_resource_question_carries_no_status_evidence_or_history(
    tmp_path: Path,
):
    policy = RecordingPolicy()
    service = build_service(tmp_path, authorization=policy)
    job = report(service)

    service.job_detail(job["job_id"], actor=WORKER)

    (_, location) = policy.resource_calls[-1]

    assert isinstance(location, ResourceLocation)
    assert [f for f in vars(location)] == ["corridor_id", "section_id"]
    for forbidden in ("status", "evidence", "execution", "history", "job_id"):
        assert not hasattr(location, forbidden)


def test_the_location_ignores_whatever_the_row_says_about_status(
    tmp_path: Path,
):
    """Same row, five different statuses, one identical location.

    Reaches the service's own resolution helper directly: the public
    paths cannot put a row into an arbitrary status without going
    through the lifecycle, and the lifecycle is exactly what this claim
    must be independent of.
    """

    policy = RecordingPolicy()
    service = build_service(tmp_path, authorization=policy)

    row = {"job_id": "JOB-1", "block_candidate": {"section_id": S_XY}}
    seen = []

    for status in (
        "reported",
        "scheduled",
        "notified",
        "in_progress",
        "completed",
    ):
        policy.resource_calls.clear()

        service._authorize_job_resource(
            WORKER, JobAction.READ_JOB, {**row, "status": status}
        )

        seen.append(policy.resource_calls[-1][1])

    assert set(seen) == {ResourceLocation(corridor_id=CORRIDOR, section_id=S_XY)}


def _code_symbols(module) -> set:
    """Every name and attribute the module's CODE uses. Ignores prose."""

    tree = ast.parse(inspect.getsource(module))

    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }


def _string_literals(module) -> set:
    """Every string literal that is not a docstring."""

    tree = ast.parse(inspect.getsource(module))

    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(
            node,
            (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            text = ast.get_docstring(node, clean=False)
            if text is not None:
                docstrings.add(text)

    return {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and n.value not in docstrings
    }


def test_authorization_modules_never_touch_state_history_or_persistence():
    from backend.app.identity import policy as policy_module
    from backend.app.identity import role_actions

    for module in (policy_module, role_actions):
        symbols = _code_symbols(module)

        assert not symbols & {
            "JobStatus",
            "status",
            "repository",
            "history",
            "list_for_job",
            "list_all",
            "list_page",
            "execute",
            "connect",
        }, module.__name__

        # "status" as a dict key would be a state read written another way.
        assert "status" not in _string_literals(module), module.__name__


def test_authorization_modules_never_compare_actor_identities():
    """Segregation of duties is deferred to 10.3; nothing here fakes it."""

    from backend.app.identity import policy as policy_module

    source = inspect.getsource(policy_module)
    tree = ast.parse(source)

    comparisons = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Attribute)
        and node.left.attr == "actor_id"
    ]

    assert comparisons == []
