"""The jobs composition root and authentication mode (Slice 10.2d).

The ONE place that chooses which policy, directory and topology the jobs
services are built with. backend.app.jobs.router calls compose_http_jobs
exactly once, at import, and keeps the module-level names `service` and
`optimization_service` (tests replace those two names).

MODE
    PASHUPAT_AUTH_MODE selects it, read once and process-wide:

        unset / "demo"   DEMO       byte-identical to before this slice
        "novaforge"      NOVAFORGE  validated, then HTTP boot is REFUSED

    Matching is exact (no stripping, no case folding). Any other value is
    refused; nothing ever falls back to demo. Reserved identity/auth
    configuration (any PASHUPAT_IDENTITY_* or any PASHUPAT_AUTH_* other
    than the mode variable itself) with an unset or "demo" mode is refused
    too, so a host that lost its mode variable cannot silently boot
    X-Actor demo mode while identity configuration is present.

    Residual risk: removing ALL auth configuration still boots demo. The
    mitigation is operational (the deployment asserts its mode).

ENFORCED HTTP IS INTENTIONALLY UNAVAILABLE IN 10.2d
    There is no request actor source (10.2f), no directory loader
    (10.2d.2) and authenticated events cannot yet be re-read (10.2d.1).
    compose_http_jobs therefore refuses NOVAFORGE with
    AUTHENTICATION_MECHANISM_UNAVAILABLE. Programmatic composition
    (compose_jobs) works, for tests and for the later slices.

ENTRY CRITERION FOR LIFTING THAT REFUSAL (P1-A, Slice 10.2d.1)
    A write by an AUTHENTICATED actor commits and then fails on read-back,
    because JobEvent.from_dict rejects the AUTHENTICATED value; the
    caller sees a 400 after the mutation. Enforced HTTP must not be served
    until sanctioned AUTHENTICATED event rehydration exists.

The JobOptimizationService(service=None) fallback (a second, unenforced
JobService) is untouched and remains demo-only in this slice.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping, Optional

from backend.app.identity.authenticated_policy import AuthenticatedEnforcingPolicy
from backend.app.identity.directory import IdentityDirectory
from backend.app.jobs.optimization import JobOptimizationService
from backend.app.jobs.repository import DEFAULT_DB_PATH, JobRepository
from backend.app.jobs.service import JobService

AUTH_MODE_ENV = "PASHUPAT_AUTH_MODE"
RESERVED_AUTH_ENV_PREFIXES = ("PASHUPAT_IDENTITY_", "PASHUPAT_AUTH_")

INVALID_AUTH_MODE = "INVALID_AUTH_MODE"
AUTH_CONFIG_IN_DEMO_MODE = "AUTH_CONFIG_IN_DEMO_MODE"
IDENTITY_DIRECTORY_REQUIRED = "IDENTITY_DIRECTORY_REQUIRED"
UNTRUSTED_DIRECTORY = "UNTRUSTED_DIRECTORY"
SEPARATE_DATABASE_REQUIRED = "SEPARATE_DATABASE_REQUIRED"
AUTHENTICATION_MECHANISM_UNAVAILABLE = "AUTHENTICATION_MECHANISM_UNAVAILABLE"
DIRECTORY_IN_DEMO_MODE = "DIRECTORY_IN_DEMO_MODE"


class AuthMode(str, Enum):
    DEMO = "demo"
    NOVAFORGE = "novaforge"


class AuthConfigurationError(RuntimeError):
    """The deployment's authentication configuration is refused."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"{reason}: {message}")


@dataclass(frozen=True)
class JobsComposition:
    mode: AuthMode
    service: JobService
    optimization_service: JobOptimizationService


def resolve_auth_mode(environ: Mapping[str, str]) -> AuthMode:
    """The mode this environment selects, or raise. Never defaults on error."""

    raw = environ.get(AUTH_MODE_ENV)

    if raw is not None and raw not in {m.value for m in AuthMode}:
        raise AuthConfigurationError(
            INVALID_AUTH_MODE,
            f"{AUTH_MODE_ENV} must be exactly 'demo' or 'novaforge'.",
        )

    if raw == AuthMode.NOVAFORGE.value:
        return AuthMode.NOVAFORGE

    reserved = sorted(
        name
        for name in environ
        if name != AUTH_MODE_ENV
        and any(name.startswith(prefix) for prefix in RESERVED_AUTH_ENV_PREFIXES)
    )

    if reserved:
        raise AuthConfigurationError(
            AUTH_CONFIG_IN_DEMO_MODE,
            "identity/authentication configuration is present but the mode "
            f"is not 'novaforge' (set: {', '.join(reserved)}).",
        )

    return AuthMode.DEMO


def _is_default_database(db_path: str | Path) -> bool:
    def norm(path: str | Path) -> str:
        return os.path.normcase(os.path.realpath(os.fspath(path)))

    return norm(db_path) == norm(DEFAULT_DB_PATH)


def compose_jobs(
    mode: AuthMode,
    *,
    directory: Optional[IdentityDirectory] = None,
    db_path: str | Path | None = None,
) -> JobsComposition:
    """Build the jobs services for a mode. Refuses mixed configurations."""

    if not isinstance(mode, AuthMode):
        raise AuthConfigurationError(
            INVALID_AUTH_MODE, f"Unknown authentication mode {mode!r}."
        )

    if mode is AuthMode.DEMO:
        if directory is not None:
            raise AuthConfigurationError(
                DIRECTORY_IN_DEMO_MODE,
                "an identity directory cannot be supplied in demo mode.",
            )

        # Exactly as before this slice: default policy, default database
        # unless a caller (a test) names its own.
        service = (
            JobService()
            if db_path is None
            else JobService(repository=JobRepository(db_path))
        )

        return JobsComposition(mode, service, JobOptimizationService(service))

    if directory is None:
        raise AuthConfigurationError(
            IDENTITY_DIRECTORY_REQUIRED,
            "novaforge mode requires an identity directory.",
        )

    if type(directory) is not IdentityDirectory:
        raise AuthConfigurationError(
            UNTRUSTED_DIRECTORY,
            "novaforge mode requires exactly an IdentityDirectory; got "
            f"{type(directory).__name__}.",
        )

    if db_path is None or _is_default_database(db_path):
        raise AuthConfigurationError(
            SEPARATE_DATABASE_REQUIRED,
            "novaforge mode requires an explicit database that is not the "
            "default demo database.",
        )

    service = JobService(repository=JobRepository(db_path))

    # The topology is the service's OWN registry: server-side data, never
    # the request. The default policy is held only until the next line,
    # before any traffic can reach this service.
    service.authorization = AuthenticatedEnforcingPolicy(
        directory,
        topology=(
            (service.registry.corridor_id, service.registry.section_ids()),
        ),
    )

    optimization_service = JobOptimizationService(service)

    if (
        type(service.authorization) is not AuthenticatedEnforcingPolicy
        or optimization_service.service is not service
    ):
        raise AuthConfigurationError(
            UNTRUSTED_DIRECTORY,
            "the composed services do not hold the enforced policy.",
        )

    return JobsComposition(mode, service, optimization_service)


def compose_http_jobs(environ: Mapping[str, str]) -> JobsComposition:
    """The router's single call: resolve the mode, then compose for HTTP."""

    mode = resolve_auth_mode(environ)

    if mode is AuthMode.NOVAFORGE:
        raise AuthConfigurationError(
            AUTHENTICATION_MECHANISM_UNAVAILABLE,
            "novaforge mode cannot serve HTTP yet: no request "
            "authentication mechanism exists in this release.",
        )

    return compose_jobs(mode)


__all__ = [
    "AUTH_MODE_ENV",
    "RESERVED_AUTH_ENV_PREFIXES",
    "AuthConfigurationError",
    "AuthMode",
    "JobsComposition",
    "compose_http_jobs",
    "compose_jobs",
    "resolve_auth_mode",
]
