"""HTTP client for NovaForge's OAuth 2.0 / OIDC endpoints (back channel).

Endpoints are NOT discovered at runtime: they are derived from the pinned
issuer, so a poisoned discovery document can never redirect the token
exchange or the client secret somewhere else. Every call has a hard timeout.

Errors are split into the two cases callers must treat differently:

  NovaForgeRejected     NovaForge answered and said no (invalid_grant,
                        invalid_client, ...). The session is over.
  NovaForgeUnavailable  no usable answer (network, timeout, 5xx, garbage).
                        Fail closed, but this says nothing about whether the
                        session is still valid, so it is retried, not fatal.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any, Dict, Optional
from urllib.parse import quote, urlencode

import httpx

from backend.app.auth.settings import AuthSettings

SCOPE = "openid profile email roles offline_access"
TIMEOUT = httpx.Timeout(5.0, connect=3.0)
MAX_RESPONSE_BYTES = 256 * 1024


class NovaForgeRejected(Exception):
    def __init__(self, error: str) -> None:
        self.error = error
        super().__init__(error)


class NovaForgeUnavailable(Exception):
    pass


def new_state() -> str:
    return secrets.token_urlsafe(32)


def new_pkce() -> tuple[str, str]:
    """(verifier, S256 challenge). The verifier never leaves the server."""

    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def half_hash(value: str) -> str:
    digest = hashlib.sha256(value.encode("ascii")).digest()[:16]
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class NovaForgeClient:
    def __init__(self, settings: AuthSettings, http: Optional[httpx.Client] = None) -> None:
        self._s = settings
        self._http = http or httpx.Client(timeout=TIMEOUT, follow_redirects=False)

    # -- browser-facing URLs (built from the PUBLIC issuer) --------------

    def authorization_url(self, *, state: str, nonce: str, challenge: str) -> str:
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self._s.client_id,
                "redirect_uri": self._s.redirect_uri,
                "scope": SCOPE,
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
            quote_via=quote,
        )
        return f"{self._s.issuer}/api/oauth/authorize?{query}"

    def logout_url(self, *, id_token_hint: Optional[str], state: str) -> str:
        params: Dict[str, str] = {
            "client_id": self._s.client_id,
            "post_logout_redirect_uri": self._s.post_logout_uri,
            "state": state,
        }
        if id_token_hint:
            params["id_token_hint"] = id_token_hint
        return f"{self._s.issuer}/api/oauth/logout?{urlencode(params, quote_via=quote)}"

    # -- back channel ----------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self._s.backchannel_url}{path}"

    def _auth(self) -> httpx.BasicAuth:
        # RFC 6749 2.3.1: id and secret are form-urlencoded before Basic encoding.
        return httpx.BasicAuth(quote(self._s.client_id, safe=""), quote(self._s.client_secret, safe=""))

    def _post(self, path: str, data: Dict[str, str]) -> Dict[str, Any]:
        try:
            response = self._http.post(self._url(path), data=data, auth=self._auth())
        except httpx.HTTPError as exc:
            raise NovaForgeUnavailable("transport error") from exc
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise NovaForgeUnavailable("oversized response")
        if response.status_code >= 500 or response.status_code == 429:
            raise NovaForgeUnavailable(f"status {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise NovaForgeUnavailable("non-JSON response") from exc
        if not isinstance(body, dict):
            raise NovaForgeUnavailable("unexpected response")
        if response.status_code != 200:
            error = body.get("error")
            raise NovaForgeRejected(error if isinstance(error, str) and len(error) < 64 else "invalid_request")
        return body

    def exchange_code(self, code: str, verifier: str) -> Dict[str, Any]:
        return self._post(
            "/api/oauth/token",
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._s.redirect_uri,
                "code_verifier": verifier,
            },
        )

    def refresh(self, refresh_token: str) -> Dict[str, Any]:
        return self._post("/api/oauth/token", {"grant_type": "refresh_token", "refresh_token": refresh_token})

    def introspect(self, access_token: str) -> Dict[str, Any]:
        return self._post("/api/oauth/introspect", {"token": access_token})

    def revoke(self, token: str) -> None:
        self._post("/api/oauth/revoke", {"token": token})

    def fetch_jwks(self) -> bytes:
        try:
            response = self._http.get(self._url("/.well-known/jwks.json"))
        except httpx.HTTPError as exc:
            raise NovaForgeUnavailable("transport error") from exc
        if response.status_code != 200 or len(response.content) > 64 * 1024:
            raise NovaForgeUnavailable("jwks unavailable")
        return response.content

    def close(self) -> None:
        self._http.close()


__all__ = [
    "NovaForgeClient",
    "NovaForgeRejected",
    "NovaForgeUnavailable",
    "SCOPE",
    "half_hash",
    "new_pkce",
    "new_state",
]
