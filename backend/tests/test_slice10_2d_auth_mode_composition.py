"""Slice 10.2d: authentication mode, composition root, assurance floor.

Hygiene: every service here is built on a tmp database; neither `main`
nor `router` is ever re-imported under a changed environment.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from backend.app.api import composition as composition_module
from backend.app.api.composition import (
    AUTH_CONFIG_IN_DEMO_MODE,
    AUTHENTICATION_MECHANISM_UNAVAILABLE,
    DIRECTORY_IN_DEMO_MODE,
    IDENTITY_DIRECTORY_REQUIRED,
    INVALID_AUTH_MODE,
    SEPARATE_DATABASE_REQUIRED,
    UNTRUSTED_DIRECTORY,
    AuthConfigurationError,
    AuthMode,
    JobsComposition,
    compose_http_jobs,
    compose_jobs,
    resolve_auth_mode,
)
from backend.app.identity import authenticated_policy as policy_module
from backend.app.identity import actor as actor_module
from backend.app.identity.actor import (
    Actor,
    ActorRole,
    IdentityAssurance,
    human_actor,
    system_actor,
    unidentified_actor,
)
from backend.app.identity.assignments import RoleAssignment
from backend.app.identity.authenticated_policy import AuthenticatedEnforcingPolicy
from backend.app.identity.authorization import (
    AuthorizationDenied,
    JobAction,
    PolicyConfigurationError,
    UnenforcedPolicy,
)
from backend.app.identity.directory import IdentityDirectory
from backend.app.identity.person import ExternalIdentity, Person
from backend.app.identity.policy import EnforcingPolicy, StaticScopeDirectory
from backend.app.identity.resource import ResourceLocation
from backend.app.identity.scope import RailwayScope
from backend.app.identity.scope_assignment import RoleScopeAssignment
from backend.app.jobs.models import JobCreateRequest
from backend.app.jobs.optimization import JobOptimizationService
from backend.app.jobs.repository import DEFAULT_DB_PATH, JobRepository
from backend.app.jobs.service import JobService

W, E, A, ADM = (
    ActorRole.WORKER,
    ActorRole.ENGINEER,
    ActorRole.AUTHORITY,
    ActorRole.ADMIN,
)
AUTH = IdentityAssurance.AUTHENTICATED
CAP = actor_module._AUTHENTICATION_CAPABILITY
APP_ROOT = Path(__file__).resolve().parents[1] / "app"


def authed(role: ActorRole = W, actor_id: str = "P-ONE") -> Actor:
    return Actor(actor_id, role, AUTH, CAP)


def declared(role: ActorRole = W, actor_id: str = "P-ONE") -> Actor:
    return human_actor(actor_id, role)


def make_directory(corridor: str, sections: tuple[str, ...]) -> IdentityDirectory:
    scope = RailwayScope(corridor, list(sections))

    return IdentityDirectory(
        people=[
            Person(pid, ExternalIdentity("novaforge", f"sub-{pid}"))
            for pid in ("P-ONE", "P-ADMIN")
        ],
        role_assignments=[
            RoleAssignment("P-ONE", W),
            RoleAssignment("P-ONE", E),
            RoleAssignment("P-ONE", A),
            RoleAssignment("P-ADMIN", ADM),
        ],
        scope_assignments=[
            RoleScopeAssignment("P-ONE", W, scope),
            RoleScopeAssignment("P-ONE", E, scope),
            RoleScopeAssignment("P-ONE", A, scope),
        ],
    )


def location(corridor: str = "C1", section: str = "S1") -> ResourceLocation:
    return ResourceLocation(corridor, section)


@pytest.fixture()
def policy() -> AuthenticatedEnforcingPolicy:
    return AuthenticatedEnforcingPolicy(
        make_directory("C1", ("S1",)), topology=(("C1", ("S1",)),)
    )


@pytest.fixture()
def composed(tmp_path) -> JobsComposition:
    from backend.app.jobs.service import JobService as _JS

    probe = _JS(repository=JobRepository(tmp_path / "probe.db"))
    corridor = probe.registry.corridor_id
    sections = probe.registry.section_ids()

    return compose_jobs(
        AuthMode.NOVAFORGE,
        directory=make_directory(corridor, sections),
        db_path=tmp_path / "novaforge.db",
    )


# ------------------------------------------------ A. the assurance floor


@pytest.mark.parametrize("action", list(JobAction))
@pytest.mark.parametrize("role", [W, E, A, ADM])
def test_declared_actor_is_denied_before_role_by_both_methods(policy, role, action):
    actor = declared(role)

    with pytest.raises(AuthorizationDenied, match="authenticated identity is required"):
        policy.authorize(actor, action)

    with pytest.raises(AuthorizationDenied, match="authenticated identity is required"):
        policy.authorize_resource(actor, action, location())


@pytest.mark.parametrize("action", list(JobAction))
def test_system_and_unidentified_are_denied_by_both_methods(policy, action):
    for actor in (system_actor(), unidentified_actor()):
        with pytest.raises(AuthorizationDenied, match="authenticated identity is required"):
            policy.authorize(actor, action)

        with pytest.raises(AuthorizationDenied, match="authenticated identity is required"):
            policy.authorize_resource(actor, action, location())


def test_system_and_unidentified_never_report_authenticated():
    assert system_actor().assurance is not AUTH
    assert unidentified_actor().assurance is not AUTH


def test_authenticated_worker_in_scope_passes_and_out_of_scope_is_denied(policy):
    policy.authorize(authed(W), JobAction.REPORT_JOB)
    policy.authorize_resource(authed(W), JobAction.REPORT_JOB, location("C1", "S1"))

    with pytest.raises(AuthorizationDenied) as info:
        policy.authorize_resource(authed(W), JobAction.REPORT_JOB, location("C1", "S9"))

    assert "authenticated identity" not in str(info.value)


def test_authenticated_admin_is_denied_every_action_by_role_semantics(policy):
    for action in JobAction:
        with pytest.raises(AuthorizationDenied) as info:
            policy.authorize(authed(ADM, "P-ADMIN"), action)
        assert "authenticated identity" not in str(info.value)


def test_floor_detail_is_identical_across_roles_and_reveals_no_permission(policy):
    details = set()

    for role in (W, E, A, ADM):
        with pytest.raises(AuthorizationDenied) as info:
            policy.authorize(declared(role), JobAction.REPORT_JOB)
        details.add(str(info.value).split(": ", 1)[1])

    assert details == {"an authenticated identity is required"}


class _ActorSubclass(Actor):
    pass


@pytest.mark.parametrize(
    "bad",
    [None, "P-ONE", 7, object(), {"role": "WORKER"}, ("P-ONE", W)],
)
def test_non_actor_is_denied_without_crashing_the_formatter(policy, bad):
    with pytest.raises(AuthorizationDenied, match="authenticated identity is required"):
        policy.authorize(bad, JobAction.REPORT_JOB)

    with pytest.raises(AuthorizationDenied, match="authenticated identity is required"):
        policy.authorize_resource(bad, JobAction.REPORT_JOB, location())


def test_actor_subclass_is_denied_even_if_it_claims_authenticated(policy):
    sub = object.__new__(_ActorSubclass)

    for name, value in (
        ("actor_id", "P-ONE"),
        ("role", W),
        ("assurance", AUTH),
    ):
        object.__setattr__(sub, name, value)

    with pytest.raises(AuthorizationDenied, match="authenticated identity is required"):
        policy.authorize(sub, JobAction.REPORT_JOB)

    with pytest.raises(AuthorizationDenied, match="authenticated identity is required"):
        policy.authorize_resource(sub, JobAction.REPORT_JOB, location())


def test_authorize_resource_applies_the_floor_independently(policy, monkeypatch):
    # Even if the parent's role check were a no-op, the resource method
    # must still refuse a declared actor on its own.
    monkeypatch.setattr(EnforcingPolicy, "authorize", lambda self, actor, action: None)
    monkeypatch.setattr(
        AuthenticatedEnforcingPolicy,
        "authorize",
        lambda self, actor, action: None,
    )

    with pytest.raises(AuthorizationDenied, match="authenticated identity is required"):
        policy.authorize_resource(declared(W), JobAction.REPORT_JOB, location())


def test_floor_runs_before_role_and_scope_evaluation(policy, monkeypatch):
    lookups = []
    monkeypatch.setattr(
        IdentityDirectory,
        "scopes_for_actor",
        lambda self, *args: lookups.append(args) or (),
    )
    monkeypatch.setattr(
        "backend.app.identity.policy.role_permits",
        lambda *args: lookups.append(args) or True,
    )

    for actor in (declared(W), declared(ADM, "P-ADMIN"), unidentified_actor()):
        with pytest.raises(AuthorizationDenied, match="authenticated identity is required"):
            policy.authorize(actor, JobAction.REPORT_JOB)

        with pytest.raises(AuthorizationDenied, match="authenticated identity is required"):
            policy.authorize_resource(actor, JobAction.REPORT_JOB, location())

    assert lookups == []


# --------------------------------------- B. policy construction


def test_policy_requires_the_exact_identity_directory():
    good = make_directory("C1", ("S1",))
    AuthenticatedEnforcingPolicy(good)

    class Sub(IdentityDirectory):
        pass

    class Duck:
        def scopes_for_actor(self, actor_id, role):
            return ()

    for bad in (StaticScopeDirectory(), Sub(), Duck(), None, {}):
        with pytest.raises(PolicyConfigurationError):
            AuthenticatedEnforcingPolicy(bad)


def test_policy_is_resource_aware_and_enforcing(policy):
    from backend.app.identity.authorization import require_resource_aware

    assert require_resource_aware(policy) is policy
    assert policy.enforcing is True


# --------------------------------------- C. mode resolver


def test_unset_and_demo_resolve_to_demo():
    assert resolve_auth_mode({}) is AuthMode.DEMO
    assert resolve_auth_mode({"PASHUPAT_AUTH_MODE": "demo"}) is AuthMode.DEMO
    assert resolve_auth_mode({"PATH": "x", "PASHUPAT_JOBS_DB": "y"}) is AuthMode.DEMO


def test_novaforge_resolves_when_exact():
    assert resolve_auth_mode({"PASHUPAT_AUTH_MODE": "novaforge"}) is AuthMode.NOVAFORGE


@pytest.mark.parametrize(
    "value",
    ["Demo", "DEMO", "prod", "true", " demo", "demo ", "", "NovaForge", "novaforge\n", "0"],
)
def test_invalid_mode_values_are_refused_never_demo(value):
    with pytest.raises(AuthConfigurationError) as info:
        resolve_auth_mode({"PASHUPAT_AUTH_MODE": value})

    assert info.value.reason == INVALID_AUTH_MODE


@pytest.mark.parametrize(
    "extra",
    [
        "PASHUPAT_IDENTITY_DIRECTORY",
        "PASHUPAT_IDENTITY_",
        "PASHUPAT_AUTH_ISSUER",
        "PASHUPAT_AUTH_MODE_X",
    ],
)
@pytest.mark.parametrize("mode", [None, "demo"])
def test_reserved_config_with_unset_or_demo_mode_is_refused(extra, mode):
    environ = {extra: "x"}
    if mode is not None:
        environ["PASHUPAT_AUTH_MODE"] = mode

    with pytest.raises(AuthConfigurationError) as info:
        resolve_auth_mode(environ)

    assert info.value.reason == AUTH_CONFIG_IN_DEMO_MODE


def test_reserved_config_is_allowed_alongside_novaforge_mode():
    environ = {"PASHUPAT_AUTH_MODE": "novaforge", "PASHUPAT_IDENTITY_X": "1"}
    assert resolve_auth_mode(environ) is AuthMode.NOVAFORGE


def test_resolver_reads_only_the_injected_mapping(monkeypatch):
    monkeypatch.setenv("PASHUPAT_AUTH_MODE", "novaforge")
    assert resolve_auth_mode({}) is AuthMode.DEMO


def test_invalid_mode_wins_over_reserved_config():
    with pytest.raises(AuthConfigurationError) as info:
        resolve_auth_mode({"PASHUPAT_AUTH_MODE": "prod", "PASHUPAT_AUTH_X": "1"})

    assert info.value.reason == INVALID_AUTH_MODE


# --------------------------------------- D. composition


def test_demo_composition_is_the_unchanged_demo_wiring(tmp_path):
    composed = compose_jobs(AuthMode.DEMO, db_path=tmp_path / "demo.db")

    assert composed.mode is AuthMode.DEMO
    assert type(composed.service) is JobService
    assert type(composed.service.authorization) is UnenforcedPolicy
    assert type(composed.optimization_service) is JobOptimizationService
    assert composed.optimization_service.service is composed.service
    assert dataclasses.is_dataclass(composed)

    with pytest.raises(dataclasses.FrozenInstanceError):
        composed.service = None  # type: ignore[misc]


def test_demo_refuses_a_directory(tmp_path):
    with pytest.raises(AuthConfigurationError) as info:
        compose_jobs(
            AuthMode.DEMO,
            directory=make_directory("C1", ("S1",)),
            db_path=tmp_path / "d.db",
        )

    assert info.value.reason == DIRECTORY_IN_DEMO_MODE


def test_http_demo_composition_uses_the_default_jobs_wiring(monkeypatch, tmp_path):
    seen = {}

    def fake(mode, **kwargs):
        seen["mode"] = mode
        seen["kwargs"] = kwargs
        return "sentinel"

    monkeypatch.setattr(composition_module, "compose_jobs", fake)

    assert compose_http_jobs({}) == "sentinel"
    assert seen == {"mode": AuthMode.DEMO, "kwargs": {}}


def test_novaforge_composition_returns_the_exact_policy_and_shared_service(composed):
    assert composed.mode is AuthMode.NOVAFORGE
    assert type(composed.service.authorization) is AuthenticatedEnforcingPolicy
    assert composed.optimization_service.service is composed.service


def test_novaforge_topology_equals_the_services_own_registry(composed):
    registry = composed.service.registry

    assert composed.service.authorization._topology == {
        registry.corridor_id: frozenset(registry.section_ids())
    }


def test_novaforge_requires_a_directory(tmp_path):
    with pytest.raises(AuthConfigurationError) as info:
        compose_jobs(AuthMode.NOVAFORGE, db_path=tmp_path / "n.db")

    assert info.value.reason == IDENTITY_DIRECTORY_REQUIRED


def test_novaforge_refuses_untrusted_directories(tmp_path):
    class Sub(IdentityDirectory):
        pass

    for bad in (StaticScopeDirectory(), Sub(), object()):
        with pytest.raises(AuthConfigurationError) as info:
            compose_jobs(AuthMode.NOVAFORGE, directory=bad, db_path=tmp_path / "n.db")

        assert info.value.reason == UNTRUSTED_DIRECTORY

    assert not (tmp_path / "n.db").exists()


def test_novaforge_refuses_missing_and_default_database(tmp_path):
    good = make_directory("C1", ("S1",))

    for db in (None, DEFAULT_DB_PATH, str(DEFAULT_DB_PATH), Path(DEFAULT_DB_PATH).parent / "." / Path(DEFAULT_DB_PATH).name):
        with pytest.raises(AuthConfigurationError) as info:
            compose_jobs(AuthMode.NOVAFORGE, directory=good, db_path=db)

        assert info.value.reason == SEPARATE_DATABASE_REQUIRED


def test_compose_jobs_rejects_a_non_mode():
    for bad in ("demo", "novaforge", None, 1):
        with pytest.raises(AuthConfigurationError) as info:
            compose_jobs(bad)  # type: ignore[arg-type]

        assert info.value.reason == INVALID_AUTH_MODE


def test_http_novaforge_boot_is_refused_after_validation():
    with pytest.raises(AuthConfigurationError) as info:
        compose_http_jobs({"PASHUPAT_AUTH_MODE": "novaforge"})

    assert info.value.reason == AUTHENTICATION_MECHANISM_UNAVAILABLE


@pytest.mark.parametrize("value", ["prod", "Demo", ""])
def test_http_invalid_mode_never_composes_demo(value):
    with pytest.raises(AuthConfigurationError):
        compose_http_jobs({"PASHUPAT_AUTH_MODE": value})


def test_http_reserved_config_never_composes_demo():
    with pytest.raises(AuthConfigurationError) as info:
        compose_http_jobs({"PASHUPAT_IDENTITY_JWKS": "x"})

    assert info.value.reason == AUTH_CONFIG_IN_DEMO_MODE


# --------------------------------------- E. sticky setter guard


def test_floored_service_refuses_replacement_by_other_policies(composed):
    service = composed.service
    original = service.authorization

    class SubPolicy(AuthenticatedEnforcingPolicy):
        pass

    others = [
        UnenforcedPolicy(),
        EnforcingPolicy(StaticScopeDirectory()),
        EnforcingPolicy(make_directory("C1", ("S1",))),
        SubPolicy(make_directory("C1", ("S1",))),
    ]

    for other in others:
        with pytest.raises(PolicyConfigurationError):
            service.authorization = other

        assert service.authorization is original


def test_floored_service_accepts_another_exact_authenticated_policy(composed):
    replacement = AuthenticatedEnforcingPolicy(make_directory("C1", ("S1",)))
    composed.service.authorization = replacement
    assert composed.service.authorization is replacement


def test_demo_service_policy_swapping_is_unaffected(tmp_path):
    service = JobService(repository=JobRepository(tmp_path / "d.db"))
    enforcing = EnforcingPolicy(StaticScopeDirectory())

    service.authorization = enforcing
    assert service.authorization is enforcing

    service.authorization = UnenforcedPolicy()
    assert type(service.authorization) is UnenforcedPolicy

    service.authorization = AuthenticatedEnforcingPolicy(make_directory("C1", ("S1",)))
    assert type(service.authorization) is AuthenticatedEnforcingPolicy


def test_service_can_be_constructed_with_the_floored_policy_and_is_then_sticky(tmp_path):
    service = JobService(
        repository=JobRepository(tmp_path / "d.db"),
        authorization=AuthenticatedEnforcingPolicy(make_directory("C1", ("S1",))),
    )

    with pytest.raises(PolicyConfigurationError):
        service.authorization = UnenforcedPolicy()


# --------------------------------------- F. direct calls


def _request(service: JobService) -> JobCreateRequest:
    track = service.corridor.tracks[0]

    return JobCreateRequest(
        track_id=track.track_id,
        job_type="BALLAST_TAMPING",
        distance_start=1_000.0,
        distance_end=2_000.0,
        workers_min=2,
        workers_max=4,
        description="slice 10.2d",
    )


def _row_count(db: Path) -> int:
    with closing(sqlite3.connect(db)) as conn:
        return conn.execute("SELECT COUNT(*) FROM job_events").fetchone()[0]


def test_floored_service_denies_unauthenticated_callers_and_writes_nothing(composed, tmp_path):
    service = composed.service
    db = Path(service.repository.db_path)

    for actor in (declared(W), unidentified_actor(), None, system_actor()):
        with pytest.raises(AuthorizationDenied):
            service.create_job(_request(service), actor=actor)

    assert _row_count(db) == 0

    for actor in (declared(W), None):
        with pytest.raises(AuthorizationDenied):
            service.list_jobs(actor=actor)


def test_floored_service_denies_unauthenticated_optimize(composed):
    inspect_sig = inspect.signature(composed.optimization_service.optimize_corridor)

    assert "actor" in inspect_sig.parameters

    for actor in (declared(A), None):
        with pytest.raises(AuthorizationDenied):
            composed.optimization_service.optimize_corridor(actor=actor)


def test_authenticated_single_step_create_job_succeeds_on_a_tmp_database(composed):
    """P1-A entry criterion, recorded and NOT exercised further.

    A single AUTHENTICATED write works. The multi-step read-back of an
    AUTHENTICATED event (JobEvent.from_dict raising after commit) is the
    known, deferred defect fixed by Slice 10.2d.1; enforced HTTP stays
    unavailable until then.
    """

    job = composed.service.create_job(_request(composed.service), actor=authed(W))

    assert job["job_id"]


# --------------------------------------- G. demo compatibility


def test_request_actor_behaviour_is_unchanged():
    from backend.app.api.deps import request_actor

    actor = request_actor(actor_id="WORKER-1", actor_role="WORKER")

    assert actor.assurance is IdentityAssurance.DECLARED_UNVERIFIED
    assert request_actor(actor_id=None, actor_role=None).assurance is not AUTH


def test_router_module_globals_keep_the_test_seam():
    from backend.app.jobs import router

    assert isinstance(router.service, JobService)
    assert isinstance(router.optimization_service, JobOptimizationService)
    assert router.optimization_service.service is router.service
    assert type(router.service.authorization) is UnenforcedPolicy


# --------------------------------------- H. AST boundary


def _source(rel: str) -> str:
    return (APP_ROOT / rel).read_text(encoding="utf-8")


def _imports(source: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _names_authenticated_or_factory(source: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr in {
            "AUTHENTICATED",
            "_AUTHENTICATION_CAPABILITY",
        }:
            return True
        if isinstance(node, ast.Constant) and node.value == "AUTHENTICATED":
            return True
        if isinstance(node, ast.Name) and node.id in {
            "AUTHENTICATED",
            "authenticated_actor",
            "_AUTHENTICATION_CAPABILITY",
        }:
            return True

    return "authenticated_actor" in "\n".join(_imports(source))


@pytest.mark.parametrize("rel", ["jobs/router.py", "jobs/service.py", "api/composition.py"])
def test_router_service_and_composition_do_not_name_the_value_or_factory(rel):
    source = _source(rel)

    assert not _names_authenticated_or_factory(source)
    assert "authenticated_actor" not in source


def test_policy_module_reads_but_never_mints():
    source = _source("identity/authenticated_policy.py")
    tree = ast.parse(source)

    assert "_AUTHENTICATION_CAPABILITY" not in source
    assert "authenticated_actor" not in source

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            assert name != "Actor"

    assert not any(
        m.startswith(("backend.app.api", "backend.app.jobs", "sqlite3"))
        for m in _imports(source)
    )


def test_composition_does_not_import_main_or_router():
    imported = _imports(_source("api/composition.py"))

    assert not any(m.endswith((".main", ".router")) or ".routers" in m for m in imported)
    assert "backend.app.api.main" not in imported
    assert "backend.app.jobs.router" not in imported


def test_router_makes_exactly_one_composition_call():
    tree = ast.parse(_source("jobs/router.py"))
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and getattr(n.func, "id", None) in {"compose_http_jobs", "compose_jobs"}
    ]

    assert len(calls) == 1
    assert calls[0].func.id == "compose_http_jobs"


def test_optimization_service_none_fallback_is_untouched():
    source = _source("jobs/optimization.py")

    assert "service or JobService()" in source
