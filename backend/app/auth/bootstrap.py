"""Builds the enforced (NovaForge) deployment from the environment.

Called only by backend.app.api.composition.compose_novaforge_http. It lives in
the auth package so the session core is consumed here, and nowhere in the
demo-mode composition path.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Mapping

from backend.app.api.composition import (
    AUTH_CONFIGURATION_INVALID,
    SEPARATE_DATABASE_REQUIRED,
    UNTRUSTED_DIRECTORY,
    AuthConfigurationError,
    AuthMode,
    _is_default_database,
)
from backend.app.auth.live_directory import NovaForgeDirectory
from backend.app.auth.nf_client import NovaForgeClient
from backend.app.auth.policy import NovaForgePolicy
from backend.app.auth.service import AuthService
from backend.app.auth.settings import AuthSettings, AuthSettingsError
from backend.app.identity.identity_assertion import (
    OIDC_ID_TOKEN_TYP,
    OIDC_ID_TOKEN_USE,
    AssertionVerifierConfig,
    IdentityAssertionVerifier,
    InMemoryReplayGuard,
)
from backend.app.identity.session import SessionConfig, SessionManager
from backend.app.identity.trusted_keys import CachingJwksProvider
from backend.app.jobs.optimization import JobOptimizationService
from backend.app.jobs.repository import JobRepository
from backend.app.jobs.service import JobService


def build_authenticated_deployment(
    environ: Mapping[str, str],
    *,
    http_client: Any = None,
    clock: Any = None,
    jobs_composition: Any,
):
    try:
        settings = AuthSettings.from_environ(environ)
    except AuthSettingsError as exc:
        raise AuthConfigurationError(AUTH_CONFIGURATION_INVALID, str(exc)) from None

    db_path = settings.jobs_db_path
    if _is_default_database(db_path):
        raise AuthConfigurationError(
            SEPARATE_DATABASE_REQUIRED,
            "novaforge mode requires an explicit database that is not the "
            "default demo database.",
        )
    Path(db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    service = JobService(repository=JobRepository(db_path))
    corridor_id = settings.corridor_id or service.registry.corridor_id
    section_ids = service.registry.section_ids()
    live = NovaForgeDirectory(
        identity_provider=settings.identity_provider,
        corridor_id=corridor_id,
        section_ids=section_ids,
        role_map=settings.role_map,
    )
    policy = NovaForgePolicy(live, topology=((corridor_id, section_ids),))
    service.authorization = policy

    optimization_service = JobOptimizationService(service)
    if service.authorization is not policy or optimization_service.service is not service:
        raise AuthConfigurationError(
            UNTRUSTED_DIRECTORY, "the composed services do not hold the enforced policy."
        )

    now = clock or time.time
    client = NovaForgeClient(settings, http_client)
    keys = CachingJwksProvider(client.fetch_jwks, now)
    verifier = IdentityAssertionVerifier(
        AssertionVerifierConfig(
            issuer=settings.issuer,
            audience=settings.client_id,
            identity_provider=settings.identity_provider,
            typ=OIDC_ID_TOKEN_TYP,
            token_use=OIDC_ID_TOKEN_USE,
        ),
        keys,
        InMemoryReplayGuard(),
        now,
    )
    sessions = SessionManager(
        SessionConfig(
            max_auth_age_seconds=settings.max_auth_age_seconds,
            max_sessions=10_000,
            per_person_limit=20,
            idle_timeout_seconds=settings.idle_seconds,
            absolute_lifetime_seconds=settings.session_seconds,
        ),
        now,
    )
    auth = AuthService(settings, client, live, verifier, sessions, now)
    auth._policy = policy
    return jobs_composition(AuthMode.NOVAFORGE, service, optimization_service, auth)
