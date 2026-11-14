"""Validated configuration for the NovaForge-backed deployment mode.

Everything is read from the environment ONCE, validated as a whole, and
frozen. Missing or unsafe configuration refuses to boot (AuthConfigurationError
from backend.app.api.composition): there is no fallback to demo mode.

    PASHUPAT_AUTH_MODE                novaforge
    PASHUPAT_AUTH_ISSUER              public origin of NovaForge, pinned exactly
                                      (https://id.example.com)
    PASHUPAT_AUTH_CLIENT_ID           this application's client_id
    PASHUPAT_AUTH_CLIENT_SECRET       its client secret (never logged)
    PASHUPAT_AUTH_AUDIENCE            the `aud` NovaForge puts in access tokens
                                      issued to this application
    PASHUPAT_AUTH_REDIRECT_URI        the registered callback, byte-exact
    PASHUPAT_AUTH_FRONTEND_ORIGIN     browser origin of the web console; the
                                      only origin CORS and post-login/logout
                                      redirects may use
    PASHUPAT_AUTH_JOBS_DB             SQLite file for authenticated operation
                                      (must not be the demo database)
  optional
    PASHUPAT_AUTH_BACKCHANNEL_URL     where THIS server reaches NovaForge when
                                      that differs from the public issuer
                                      (e.g. http://novaforge:4000 in compose)
    PASHUPAT_AUTH_POST_LOGOUT_URI     registered post-logout redirect
                                      (default: frontend origin + "/")
    PASHUPAT_AUTH_COOKIE_SECURE       "true"/"false"; default true unless the
                                      frontend origin is http on localhost
    PASHUPAT_AUTH_FRESHNESS_SECONDS   max age of a liveness check (default 30)
    PASHUPAT_AUTH_ALLOW_INSECURE_HTTP "true" to permit a non-loopback http
                                      issuer (never use in production)
    PASHUPAT_AUTH_ROLE_MAP            JSON {"novaforge_role_key": "WORKER", ...}
    PASHUPAT_AUTH_CORRIDOR_ID         corridor the scope grants apply to
                                      (default: the service's own corridor)
    PASHUPAT_AUTH_MAX_AUTH_AGE_SECONDS, PASHUPAT_AUTH_IDLE_SECONDS,
    PASHUPAT_AUTH_SESSION_SECONDS     session limits
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Mapping, Optional
from urllib.parse import urlsplit

from backend.app.identity.actor import ActorRole

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

DEFAULT_ROLE_MAP: Mapping[str, ActorRole] = {
    "worker": ActorRole.WORKER,
    "engineer": ActorRole.ENGINEER,
    "authority": ActorRole.AUTHORITY,
    "admin": ActorRole.ADMIN,
}

# Roles a NovaForge role key may map to. SYSTEM / UNIDENTIFIED can never be
# granted by an identity provider.
MAPPABLE_ROLES = frozenset(
    {ActorRole.WORKER, ActorRole.ENGINEER, ActorRole.AUTHORITY, ActorRole.ADMIN}
)


class AuthSettingsError(ValueError):
    """The authentication configuration is missing or unsafe."""


def _origin(value: str, name: str, *, allow_insecure: bool) -> str:
    parts = urlsplit(value)
    host = parts.hostname or ""
    if (
        parts.scheme not in ("http", "https")
        or not host
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
        or parts.username
        or parts.password
    ):
        raise AuthSettingsError(f"{name} must be an origin like https://host[:port].")
    if parts.scheme == "http" and host not in LOOPBACK_HOSTS and not allow_insecure:
        raise AuthSettingsError(
            f"{name} must be https (plain http is only accepted for localhost)."
        )
    return f"{parts.scheme}://{parts.netloc}"


def _exact_uri(value: str, name: str, *, allow_insecure: bool) -> str:
    parts = urlsplit(value)
    host = parts.hostname or ""
    if (
        parts.scheme not in ("http", "https")
        or not host
        or parts.fragment
        or parts.username
        or parts.password
        or "*" in value
    ):
        raise AuthSettingsError(f"{name} must be an absolute URL without fragment or wildcard.")
    if parts.scheme == "http" and host not in LOOPBACK_HOSTS and not allow_insecure:
        raise AuthSettingsError(f"{name} must be https (plain http is only accepted for localhost).")
    return value


def _bool(value: Optional[str], name: str, default: bool) -> bool:
    if value is None:
        return default
    if value in ("true", "1"):
        return True
    if value in ("false", "0"):
        return False
    raise AuthSettingsError(f"{name} must be 'true' or 'false'.")


def _int(value: Optional[str], name: str, default: int, low: int, high: int) -> int:
    if value is None:
        return default
    try:
        number = int(value)
    except ValueError as exc:
        raise AuthSettingsError(f"{name} must be an integer.") from exc
    if not low <= number <= high:
        raise AuthSettingsError(f"{name} must be between {low} and {high}.")
    return number


@dataclass(frozen=True)
class AuthSettings:
    issuer: str
    backchannel_url: str
    client_id: str
    client_secret: str = field(repr=False)
    audience: str
    redirect_uri: str
    frontend_origin: str
    post_logout_uri: str
    jobs_db_path: str
    cookie_secure: bool
    freshness_seconds: int
    max_auth_age_seconds: int
    idle_seconds: int
    session_seconds: int
    role_map: Mapping[str, ActorRole]
    corridor_id: Optional[str]
    identity_provider: str = "novaforge"

    @property
    def session_cookie(self) -> str:
        return "__Host-pp_session" if self.cookie_secure else "pp_session"

    @property
    def login_cookie(self) -> str:
        return "__Host-pp_login" if self.cookie_secure else "pp_login"

    @classmethod
    def from_environ(cls, environ: Mapping[str, str]) -> "AuthSettings":
        def need(name: str) -> str:
            value = environ.get(name)
            if not value or value != value.strip():
                raise AuthSettingsError(f"{name} is required (no surrounding whitespace).")
            return value

        allow_insecure = _bool(
            environ.get("PASHUPAT_AUTH_ALLOW_INSECURE_HTTP"),
            "PASHUPAT_AUTH_ALLOW_INSECURE_HTTP",
            False,
        )
        issuer = _origin(need("PASHUPAT_AUTH_ISSUER"), "PASHUPAT_AUTH_ISSUER", allow_insecure=allow_insecure)
        backchannel = environ.get("PASHUPAT_AUTH_BACKCHANNEL_URL")
        backchannel_url = (
            _origin(backchannel, "PASHUPAT_AUTH_BACKCHANNEL_URL", allow_insecure=True)
            if backchannel
            else issuer
        )
        frontend = _origin(
            need("PASHUPAT_AUTH_FRONTEND_ORIGIN"),
            "PASHUPAT_AUTH_FRONTEND_ORIGIN",
            allow_insecure=allow_insecure,
        )
        host = urlsplit(frontend).hostname or ""
        cookie_secure = _bool(
            environ.get("PASHUPAT_AUTH_COOKIE_SECURE"),
            "PASHUPAT_AUTH_COOKIE_SECURE",
            not (frontend.startswith("http://") and host in LOOPBACK_HOSTS),
        )
        if not cookie_secure and not (frontend.startswith("http://") and (host in LOOPBACK_HOSTS or allow_insecure)):
            raise AuthSettingsError(
                "PASHUPAT_AUTH_COOKIE_SECURE=false is only allowed for a localhost frontend."
            )

        role_map: Mapping[str, ActorRole] = dict(DEFAULT_ROLE_MAP)
        raw_map = environ.get("PASHUPAT_AUTH_ROLE_MAP")
        if raw_map:
            try:
                parsed = json.loads(raw_map)
                role_map = {str(k): ActorRole(v) for k, v in parsed.items()}
            except (ValueError, AttributeError, TypeError) as exc:
                raise AuthSettingsError("PASHUPAT_AUTH_ROLE_MAP must be a JSON object of role names.") from exc
            if any(role not in MAPPABLE_ROLES for role in role_map.values()):
                raise AuthSettingsError("PASHUPAT_AUTH_ROLE_MAP may only grant WORKER, ENGINEER, AUTHORITY or ADMIN.")

        secret = need("PASHUPAT_AUTH_CLIENT_SECRET")
        if len(secret) < 32:
            raise AuthSettingsError("PASHUPAT_AUTH_CLIENT_SECRET looks too short to be a real secret.")

        return cls(
            issuer=issuer,
            backchannel_url=backchannel_url,
            client_id=need("PASHUPAT_AUTH_CLIENT_ID"),
            client_secret=secret,
            audience=need("PASHUPAT_AUTH_AUDIENCE"),
            redirect_uri=_exact_uri(
                need("PASHUPAT_AUTH_REDIRECT_URI"),
                "PASHUPAT_AUTH_REDIRECT_URI",
                allow_insecure=allow_insecure,
            ),
            frontend_origin=frontend,
            post_logout_uri=_exact_uri(
                environ.get("PASHUPAT_AUTH_POST_LOGOUT_URI") or f"{frontend}/",
                "PASHUPAT_AUTH_POST_LOGOUT_URI",
                allow_insecure=allow_insecure,
            ),
            jobs_db_path=need("PASHUPAT_AUTH_JOBS_DB"),
            cookie_secure=cookie_secure,
            freshness_seconds=_int(environ.get("PASHUPAT_AUTH_FRESHNESS_SECONDS"), "PASHUPAT_AUTH_FRESHNESS_SECONDS", 30, 1, 300),
            max_auth_age_seconds=_int(environ.get("PASHUPAT_AUTH_MAX_AUTH_AGE_SECONDS"), "PASHUPAT_AUTH_MAX_AUTH_AGE_SECONDS", 30 * 24 * 3600, 60, 30 * 24 * 3600),
            idle_seconds=_int(environ.get("PASHUPAT_AUTH_IDLE_SECONDS"), "PASHUPAT_AUTH_IDLE_SECONDS", 30 * 60, 60, 24 * 3600),
            session_seconds=_int(environ.get("PASHUPAT_AUTH_SESSION_SECONDS"), "PASHUPAT_AUTH_SESSION_SECONDS", 12 * 3600, 60, 12 * 3600),
            role_map=role_map,
            corridor_id=environ.get("PASHUPAT_AUTH_CORRIDOR_ID") or None,
        )


__all__ = ["AuthSettings", "AuthSettingsError", "DEFAULT_ROLE_MAP", "MAPPABLE_ROLES"]
