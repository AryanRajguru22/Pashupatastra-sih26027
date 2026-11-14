"""The authentication service: OIDC login, sessions, liveness, logout.

    browser --/auth/login--> NovaForge --code--> /auth/callback
        server: verify state + iss, exchange the code (client secret + PKCE
        verifier), verify the ID token and nonce with the pinned issuer's
        JWKS, ask NovaForge to introspect the access token, sync the user's
        roles into the directory, create a server-side session.
    browser --opaque cookie--> every request
        server: session -> (liveness check if stale) -> Actor through the
        existing authenticated_actor / directory chain.

Nothing a browser can send is an identity, a role or a scope. Tokens never
leave this process: the browser holds one opaque, random session handle.

LIVENESS (the staleness bound N = settings.freshness_seconds)
    A session is re-validated with NovaForge at most N seconds after the
    last successful check, before any request is served. The check refreshes
    the access token when it is about to expire (rotating the refresh token)
    and introspects it, which is live: it fails the moment the NovaForge
    session ended, the grant was revoked, the user or the application was
    disabled, or the user lost access; and it returns the CURRENT roles, which
    are synced into the directory. So a revocation or role change is enforced
    within N seconds, never later. If NovaForge cannot be reached the request
    FAILS CLOSED (503); there is no grace period.
"""

from __future__ import annotations

import hmac
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

from backend.app.auth.live_directory import NovaForgeDirectory
from backend.app.auth.nf_client import (
    NovaForgeClient,
    NovaForgeRejected,
    NovaForgeUnavailable,
    new_pkce,
    new_state,
)
from backend.app.auth.settings import AuthSettings
from backend.app.identity.actor import Actor, ActorRole
from backend.app.identity.identity_assertion import (
    AssertionVerificationError,
    IdentityAssertionVerifier,
)
from backend.app.identity.session import (
    SessionError,
    SessionInfo,
    SessionManager,
    SessionRejection,
)

LOGIN_TTL_SECONDS = 600
MAX_PENDING_LOGINS = 5_000
MAX_RETURN_PATH = 200


class AuthError(Exception):
    """An authentication refusal with a stable code and HTTP status."""

    def __init__(self, code: str, status: int, message: str) -> None:
        self.code = code
        self.status = status
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass
class _LoginTxn:
    state: str
    nonce: str
    verifier: str
    return_path: str
    created: float


@dataclass
class _Bundle:
    """Server-side token state for one session. Never serialised, never sent."""

    subject: str
    access_token: str = field(repr=False)
    refresh_token: Optional[str] = field(repr=False)
    id_token: str = field(repr=False)
    access_expires_at: float
    last_verified_at: float
    display_name: Optional[str] = None
    email: Optional[str] = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


@dataclass(frozen=True)
class Principal:
    actor: Actor
    session_ref: str
    display_name: Optional[str]
    email: Optional[str]
    roles: Tuple[str, ...]
    permissions: Tuple[str, ...]


def safe_return_path(value: Optional[str]) -> str:
    """A same-origin path, or "/". Never a URL: no open redirect through return_to."""

    if (
        not value
        or len(value) > MAX_RETURN_PATH
        or not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or any(ord(c) < 0x20 or ord(c) == 0x7F for c in value)
    ):
        return "/"
    return value


class AuthService:
    def __init__(
        self,
        settings: AuthSettings,
        client: NovaForgeClient,
        directory: NovaForgeDirectory,
        verifier: IdentityAssertionVerifier,
        sessions: SessionManager,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self._client = client
        self.directory = directory
        self._verifier = verifier
        self._sessions = sessions
        self._clock = clock
        self._lock = threading.Lock()
        self._logins: Dict[str, _LoginTxn] = {}
        self._bundles: Dict[str, _Bundle] = {}

    # -- login ----------------------------------------------------------

    def begin_login(self, return_to: Optional[str]) -> Tuple[str, str]:
        """(login transaction id for the cookie, URL to send the browser to)."""

        import secrets

        txn_id = secrets.token_urlsafe(32)
        state = new_state()
        nonce = secrets.token_urlsafe(32)
        verifier, challenge = new_pkce()
        now = self._clock()
        with self._lock:
            for key in [k for k, t in self._logins.items() if now - t.created > LOGIN_TTL_SECONDS]:
                del self._logins[key]
            if len(self._logins) >= MAX_PENDING_LOGINS:
                raise AuthError("too_many_logins", 503, "Too many sign-ins in progress; try again shortly.")
            self._logins[txn_id] = _LoginTxn(state, nonce, verifier, safe_return_path(return_to), now)
        url = self._client.authorization_url(state=state, nonce=nonce, challenge=challenge)
        return txn_id, url

    def complete_login(self, txn_id: Optional[str], params: Dict[str, str]) -> Tuple[str, str]:
        """(session handle, frontend path to land on), or raise AuthError."""

        now = self._clock()
        with self._lock:
            txn = self._logins.pop(txn_id, None) if txn_id else None
        # The transaction is single-use: a replayed callback finds nothing.
        if txn is None or now - txn.created > LOGIN_TTL_SECONDS:
            raise AuthError("login_expired", 400, "The sign-in request expired. Please start again.")

        issuer = params.get("iss")
        if issuer is None or not hmac.compare_digest(issuer.encode(), self.settings.issuer.encode()):
            raise AuthError("issuer_mismatch", 400, "The sign-in response came from an unexpected issuer.")
        state = params.get("state")
        if state is None or not hmac.compare_digest(state.encode(), txn.state.encode()):
            raise AuthError("state_mismatch", 400, "The sign-in response did not match the request.")

        error = params.get("error")
        if error is not None:
            if error == "access_denied":
                raise AuthError("not_authorized", 403, "You have not been granted access to Pashupatastra.")
            raise AuthError("provider_error", 400, "The identity provider could not sign you in.")

        code = params.get("code")
        if not code or len(code) > 200:
            raise AuthError("invalid_response", 400, "The sign-in response was incomplete.")

        try:
            tokens = self._client.exchange_code(code, txn.verifier)
        except NovaForgeRejected:
            raise AuthError("exchange_failed", 400, "The sign-in could not be completed.") from None
        except NovaForgeUnavailable:
            raise AuthError("provider_unavailable", 503, "The identity provider is unavailable.") from None

        access_token = tokens.get("access_token")
        id_token = tokens.get("id_token")
        refresh_token = tokens.get("refresh_token")
        expires_in = tokens.get("expires_in")
        if (
            tokens.get("token_type") != "Bearer"
            or not isinstance(access_token, str)
            or not isinstance(id_token, str)
            or not (refresh_token is None or isinstance(refresh_token, str))
            or not isinstance(expires_in, int)
        ):
            raise AuthError("invalid_response", 400, "The sign-in response was malformed.")

        try:
            verified = self._verifier.verify_id_token(id_token, txn.nonce)
        except AssertionVerificationError:
            raise AuthError("invalid_id_token", 400, "The identity token was not valid.") from None
        subject = verified.identity.subject

        info = self._introspect_checked(access_token, subject)
        self.directory.sync(subject, info.get("assignments"))
        roles = self.directory.roles_for(self._person(subject))
        if not roles:
            self._best_effort_revoke(refresh_token or access_token)
            raise AuthError("not_authorized", 403, "You have not been granted access to Pashupatastra.")

        try:
            handle = self._sessions.create(
                verified, self.directory.current(), roles[0] if len(roles) == 1 else None
            )
        except SessionError as exc:
            self._best_effort_revoke(refresh_token or access_token)
            raise AuthError("session_refused", 403, f"A session could not be created ({exc.reason.value}).") from None

        session_info = self._sessions.info(handle.handle)
        self._sessions.extend_fresh(handle.handle, now + 2 * self.settings.freshness_seconds)
        with self._lock:
            self._bundles[session_info.session_ref] = _Bundle(
                subject=subject,
                access_token=access_token,
                refresh_token=refresh_token,
                id_token=id_token,
                access_expires_at=now + expires_in,
                last_verified_at=now,
                display_name=info.get("name") if isinstance(info.get("name"), str) else None,
                email=info.get("email") if isinstance(info.get("email"), str) else None,
            )
        return handle.handle, txn.return_path

    # -- per-request ----------------------------------------------------

    def authenticate(self, handle: Optional[str]) -> Principal:
        """The live Principal for a session handle, or raise AuthError."""

        if not handle:
            raise AuthError("unauthenticated", 401, "Sign in required.")
        try:
            info = self._sessions.info(handle)
        except SessionError:
            raise AuthError("unauthenticated", 401, "Sign in required.") from None
        with self._lock:
            bundle = self._bundles.get(info.session_ref)
        if bundle is None:
            self._sessions.logout(handle)
            raise AuthError("unauthenticated", 401, "Sign in required.")

        with bundle.lock:
            if self._clock() - bundle.last_verified_at >= self.settings.freshness_seconds:
                self._revalidate(handle, info, bundle)

        try:
            actor = self._sessions.actor_for(handle, self.directory.current())
        except SessionError as exc:
            if exc.reason is SessionRejection.ROLE_SELECTION_REQUIRED:
                raise AuthError("role_selection_required", 409, "Choose which role to act in.") from None
            self._end(handle, info)
            raise AuthError("access_revoked", 401, "Your access has changed. Please sign in again.") from None

        roles = self.directory.roles_for(info.person_id)
        perms = self.directory.permissions_for(actor.actor_id, actor.role) or frozenset()
        return Principal(
            actor=actor,
            session_ref=info.session_ref,
            display_name=bundle.display_name,
            email=bundle.email,
            roles=tuple(r.value for r in roles),
            permissions=tuple(sorted(perms)),
        )

    def session_summary(self, handle: Optional[str]) -> Dict[str, Any]:
        """Like authenticate, but a pending role choice is a normal answer."""

        try:
            principal = self.authenticate(handle)
        except AuthError as exc:
            if exc.code != "role_selection_required":
                raise
            info = self._sessions.info(handle or "")
            with self._lock:
                bundle = self._bundles.get(info.session_ref)
            roles = self.directory.roles_for(info.person_id)
            return {
                "authenticated": True,
                "selection_required": True,
                "active_role": None,
                "actor_id": info.person_id,
                "roles": [r.value for r in roles],
                "name": bundle.display_name if bundle else None,
                "email": bundle.email if bundle else None,
                "permissions": [],
            }
        return {
            "authenticated": True,
            "selection_required": False,
            "active_role": principal.actor.role.value,
            "actor_id": principal.actor.actor_id,
            "roles": list(principal.roles),
            "name": principal.display_name,
            "email": principal.email,
            "permissions": list(principal.permissions),
        }

    def select_role(self, handle: Optional[str], role: str) -> str:
        """Choose the role to act in among those NovaForge granted. Rotates the handle."""

        if not handle:
            raise AuthError("unauthenticated", 401, "Sign in required.")
        try:
            info = self._sessions.info(handle)
            with self._lock:
                bundle = self._bundles.get(info.session_ref)
            if bundle is None:
                raise AuthError("unauthenticated", 401, "Sign in required.")
            with bundle.lock:
                if self._clock() - bundle.last_verified_at >= self.settings.freshness_seconds:
                    self._revalidate(handle, info, bundle)
            return self._sessions.set_role(handle, role, self.directory.current()).handle
        except SessionError as exc:
            if exc.reason in (SessionRejection.ROLE_NOT_ASSIGNED, SessionRejection.INVALID_ROLE):
                raise AuthError("role_not_granted", 403, "That role has not been granted to you.") from None
            raise AuthError("unauthenticated", 401, "Sign in required.") from None

    def require_action(self, actor: Actor, action: Any) -> None:
        """Role/permission check without a resource (for the legacy what-if solver routes)."""

        self._policy.authorize(actor, action)  # raises AuthorizationDenied

    # -- logout ---------------------------------------------------------

    def logout(self, handle: Optional[str]) -> str:
        """End the session here and at NovaForge; returns the URL that ends the NovaForge session."""

        id_token: Optional[str] = None
        if handle:
            try:
                info = self._sessions.info(handle)
            except SessionError:
                info = None
            if info is not None:
                with self._lock:
                    bundle = self._bundles.pop(info.session_ref, None)
                if bundle is not None:
                    id_token = bundle.id_token
                    self._best_effort_revoke(bundle.refresh_token or bundle.access_token)
            self._sessions.logout(handle)
        return self._client.logout_url(id_token_hint=id_token, state=new_state())

    # -- internals ------------------------------------------------------

    _policy: Any = None  # set by the composition root (NovaForgePolicy)

    def _person(self, subject: str) -> str:
        from backend.app.auth.live_directory import person_id_for

        return person_id_for(subject)

    def _best_effort_revoke(self, token: str) -> None:
        try:
            self._client.revoke(token)
        except (NovaForgeRejected, NovaForgeUnavailable):
            pass

    def _end(self, handle: str, info: SessionInfo) -> None:
        self._sessions.logout(handle)
        with self._lock:
            self._bundles.pop(info.session_ref, None)

    def _introspect_checked(self, access_token: str, subject: str) -> Dict[str, Any]:
        try:
            info = self._client.introspect(access_token)
        except NovaForgeRejected:
            raise AuthError("introspection_failed", 502, "The identity provider refused the check.") from None
        except NovaForgeUnavailable:
            raise AuthError("provider_unavailable", 503, "The identity provider is unavailable.") from None
        if (
            info.get("active") is not True
            or info.get("sub") != subject
            or info.get("client_id") != self.settings.client_id
            or info.get("aud") != self.settings.audience
        ):
            raise AuthError("session_inactive", 401, "Your session is no longer active.")
        return info

    def _revalidate(self, handle: str, info: SessionInfo, bundle: _Bundle) -> None:
        """Called with bundle.lock held: confirm with NovaForge, refresh roles."""

        now = self._clock()
        try:
            if bundle.refresh_token and now >= bundle.access_expires_at - 15:
                tokens = self._client.refresh(bundle.refresh_token)
                new_access = tokens.get("access_token")
                new_refresh = tokens.get("refresh_token")
                expires_in = tokens.get("expires_in")
                if not isinstance(new_access, str) or not isinstance(new_refresh, str) or not isinstance(expires_in, int):
                    raise NovaForgeUnavailable("malformed refresh response")
                bundle.access_token = new_access
                bundle.refresh_token = new_refresh
                bundle.access_expires_at = now + expires_in
            result = self._introspect_checked(bundle.access_token, bundle.subject)
        except NovaForgeRejected:
            # NovaForge answered and refused (revoked grant, ended session, disabled user/app).
            self._end(handle, info)
            raise AuthError("session_inactive", 401, "Your session is no longer active.") from None
        except NovaForgeUnavailable:
            raise AuthError("provider_unavailable", 503, "The identity provider is unavailable.") from None
        except AuthError as exc:
            if exc.code == "session_inactive":
                self._end(handle, info)
            raise

        self.directory.sync(bundle.subject, result.get("assignments"))
        bundle.display_name = result.get("name") if isinstance(result.get("name"), str) else bundle.display_name
        bundle.email = result.get("email") if isinstance(result.get("email"), str) else bundle.email
        bundle.last_verified_at = now
        try:
            self._sessions.extend_fresh(handle, now + 2 * self.settings.freshness_seconds)
        except SessionError:
            self._end(handle, info)
            raise AuthError("unauthenticated", 401, "Sign in required.") from None


__all__ = ["AuthError", "AuthService", "Principal", "safe_return_path"]
