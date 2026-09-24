"""Programmatic, in-memory session core (Slice 10.2e).

    VerifiedIdentityAssertion --(create)--> session handle (returned once)
    session handle + CURRENT directory --(actor_for)--> live Actor

This is a session CORE, not a login. It has no HTTP endpoint, no cookie,
no CSRF, no OAuth/OIDC flow, no liveness call and no persistence. It only
answers: "given an assertion the verifier accepted, hand back an opaque
handle, and later turn that handle into an Actor for the CURRENT
directory". NovaForge browser login, the request-actor replacement and
NovaForge liveness are later slices.

WHAT A SESSION HOLDS
    The live VerifiedIdentityAssertion object. It cannot be rebuilt from
    stored data (only the verifier holds its construction token) and its
    jti is already spent, so a session can never be restored: the store is
    in-process and non-durable, and a restart logs everybody out. Nothing
    is written to jobs.db or anywhere else. A session, a manager or a
    stored row is never a credential and refuses pickling.

    It also holds person_id (fixed at creation), the requested role (a
    SELECTOR, never a grant), timestamps, and SHA-256(handle) as the
    lookup key. It never holds an Actor, a scope, a RoleAssignment, a
    NovaForge role, a trust level, the raw assertion string or the raw
    handle.

THE HANDLE
    256 random bits from the OS CSPRNG, generated here, returned exactly
    once by create() / set_role(), and never stored, logged or echoed in
    an error. session_ref is a separate random, non-secret identifier for
    audit correlation; it is not a credential.

ACTOR DERIVATION IS NEVER CACHED
    actor_for() calls authenticated_actor() with the session's verified
    assertion, the directory passed in NOW and the stored role selector,
    on every call. Roles and scopes therefore always reflect the current
    directory: a removed role or person is a denial on the next request.
    This module never constructs an Actor and never names the assurance
    value or the capability.

BOUNDED BY THE ASSERTION
    There is no liveness mechanism yet, so nothing may outlive the
    assertion: a session is unusable from verified.expires_at on
    (fresh_until), and creation itself requires now < expires_at. The
    idle, absolute and auth-age limits only ever shorten that. When a
    liveness source exists, fresh_until is where it plugs in.

TIME-OF-CHECK BOUNDARY
    Every operation is atomic under one lock. A request whose actor_for()
    already returned may finish with that Actor; a logout, rotation or
    expiry takes effect for the NEXT derivation. The Actor is a value,
    not a live reference to the session.

FAIL CLOSED
    Unknown, malformed, expired, idle and logged-out handles are one
    INVALID_SESSION. A full store or a full per-person quota refuses the
    new session; nothing is ever evicted. An unusable clock (NaN,
    infinite, non-positive, non-numeric) refuses the operation.
"""

from __future__ import annotations

import hashlib
import math
import secrets
import threading
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Callable, Dict, Optional, Tuple

from backend.app.identity.actor import ActorRole
from backend.app.identity.assignments import (
    INVALID_ROLE,
    ROLE_NOT_ASSIGNED,
    ROLE_SELECTION_REQUIRED,
    AssignmentError,
)
from backend.app.identity.authenticated_actor import authenticated_actor
from backend.app.identity.directory import (
    IDENTITY_NOT_ENROLLED,
    PERSON_NOT_ENROLLED,
    DirectoryError,
    IdentityDirectory,
)
from backend.app.identity.identity_assertion import VerifiedIdentityAssertion

DEFAULT_IDLE_TIMEOUT_SECONDS = 30 * 60
DEFAULT_ABSOLUTE_LIFETIME_SECONDS = 12 * 60 * 60

MAX_IDLE_TIMEOUT_SECONDS = 24 * 60 * 60
MAX_ABSOLUTE_LIFETIME_SECONDS = 12 * 60 * 60
MAX_AUTH_AGE_CEILING_SECONDS = 30 * 24 * 60 * 60
MAX_SESSIONS_CEILING = 100_000

HANDLE_BYTES = 32
SESSION_REF_BYTES = 16
_HANDLE_LENGTH = 43  # base64url of 32 bytes, unpadded
_HANDLE_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)


class SessionRejection(str, Enum):
    """Why a session operation was refused. The only detail an error carries."""

    INVALID_SESSION = "INVALID_SESSION"
    UNTRUSTED_ASSERTION = "UNTRUSTED_ASSERTION"
    UNTRUSTED_DIRECTORY = "UNTRUSTED_DIRECTORY"
    ASSERTION_EXPIRED = "ASSERTION_EXPIRED"
    AUTH_TOO_OLD = "AUTH_TOO_OLD"
    ASSERTION_ALREADY_USED = "ASSERTION_ALREADY_USED"
    ACCESS_DENIED = "ACCESS_DENIED"
    ROLE_SELECTION_REQUIRED = "ROLE_SELECTION_REQUIRED"
    ROLE_NOT_ASSIGNED = "ROLE_NOT_ASSIGNED"
    INVALID_ROLE = "INVALID_ROLE"
    SESSION_STORE_FULL = "SESSION_STORE_FULL"
    PERSON_SESSION_LIMIT = "PERSON_SESSION_LIMIT"
    CLOCK_UNAVAILABLE = "CLOCK_UNAVAILABLE"


class SessionError(Exception):
    """A session operation was refused. Carries a reason code only."""

    def __init__(self, reason: SessionRejection) -> None:
        self.reason = SessionRejection(reason)
        super().__init__(f"Session refused: {self.reason.value}")


class SessionConfigError(ValueError):
    """The session manager was configured with an unusable value."""


def _is_int(value: object) -> bool:
    # bool is an int subclass; True must never read as a limit of 1.
    return type(value) is int


def _is_valid_clock_reading(value: object) -> bool:
    """A finite, positive number: a NaN would silently pass every bound."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and value > 0


@dataclass(frozen=True)
class SessionConfig:
    """Validated, immutable session limits.

    idle timeout and absolute lifetime carry the documented defaults
    (architecture doc section 8.1: 30 minutes idle, at most 12 hours
    absolute). The auth-age bound and the two capacities have no
    documented value, so they have no default either: a deployment states
    them.
    """

    max_auth_age_seconds: int
    max_sessions: int
    per_person_limit: int
    idle_timeout_seconds: int = DEFAULT_IDLE_TIMEOUT_SECONDS
    absolute_lifetime_seconds: int = DEFAULT_ABSOLUTE_LIFETIME_SECONDS

    def __post_init__(self) -> None:
        if not _is_int(self.max_auth_age_seconds) or not (
            0 < self.max_auth_age_seconds <= MAX_AUTH_AGE_CEILING_SECONDS
        ):
            raise SessionConfigError(
                f"max_auth_age_seconds must be an int in (0, {MAX_AUTH_AGE_CEILING_SECONDS}]."
            )
        if not _is_int(self.max_sessions) or not (
            0 < self.max_sessions <= MAX_SESSIONS_CEILING
        ):
            raise SessionConfigError(
                f"max_sessions must be an int in (0, {MAX_SESSIONS_CEILING}]."
            )
        if not _is_int(self.per_person_limit) or not (
            0 < self.per_person_limit <= self.max_sessions
        ):
            raise SessionConfigError(
                "per_person_limit must be an int in (0, max_sessions]."
            )
        if not _is_int(self.idle_timeout_seconds) or not (
            0 < self.idle_timeout_seconds <= MAX_IDLE_TIMEOUT_SECONDS
        ):
            raise SessionConfigError(
                f"idle_timeout_seconds must be an int in (0, {MAX_IDLE_TIMEOUT_SECONDS}]."
            )
        if not _is_int(self.absolute_lifetime_seconds) or not (
            0 < self.absolute_lifetime_seconds <= MAX_ABSOLUTE_LIFETIME_SECONDS
        ):
            raise SessionConfigError(
                "absolute_lifetime_seconds must be an int in "
                f"(0, {MAX_ABSOLUTE_LIFETIME_SECONDS}]."
            )


@dataclass(frozen=True)
class Session:
    """One server-side session. Never a credential; never serialized."""

    handle_digest: str = field(repr=False)
    session_ref: str
    verified: VerifiedIdentityAssertion = field(repr=False)
    person_id: str
    requested_role: Optional[ActorRole]
    created_at: float
    last_seen_at: float
    absolute_expires_at: float
    fresh_until: float

    def __reduce_ex__(self, protocol: int):  # noqa: D401
        raise TypeError("A Session cannot be serialized.")


@dataclass(frozen=True)
class SessionHandle:
    """What create() / set_role() return: the raw handle, once."""

    handle: str = field(repr=False)
    session_ref: str
    expires_at: float

    def __reduce_ex__(self, protocol: int):
        raise TypeError("A SessionHandle cannot be serialized.")


def _digest(handle: object) -> Optional[str]:
    """SHA-256 of a well-formed handle, or None for anything else."""

    if (
        not isinstance(handle, str)
        or len(handle) != _HANDLE_LENGTH
        or not set(handle) <= _HANDLE_ALPHABET
    ):
        return None
    return hashlib.sha256(handle.encode("ascii")).hexdigest()


class SessionManager:
    """Thread-safe, per-process, non-durable session store."""

    def __init__(
        self,
        config: SessionConfig,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(config, SessionConfig):
            raise SessionConfigError("config must be a SessionConfig.")
        if not callable(clock):
            raise SessionConfigError("clock must be callable.")
        self._config = config
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: Dict[str, Session] = {}
        # (issuer, assertion id) -> retain-until. One assertion, one session.
        self._used_assertions: Dict[Tuple[str, str], float] = {}

    def __reduce_ex__(self, protocol: int):
        raise TypeError("A SessionManager cannot be serialized.")

    @property
    def config(self) -> SessionConfig:
        return self._config

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)

    # -- helpers (callers hold the lock) --------------------------------

    def _now(self) -> float:
        reading: object
        try:
            reading = self._clock()
        except Exception:  # noqa: BLE001 - an unusable clock fails closed
            reading = None
        if not _is_valid_clock_reading(reading):
            raise SessionError(SessionRejection.CLOCK_UNAVAILABLE)
        return float(reading)  # type: ignore[arg-type]

    def _is_live(self, session: Session, now: float) -> bool:
        return (
            now < session.fresh_until
            and now < session.absolute_expires_at
            and now - session.last_seen_at < self._config.idle_timeout_seconds
        )

    def _purge(self, now: float) -> None:
        for key in [k for k, s in self._sessions.items() if not self._is_live(s, now)]:
            del self._sessions[key]
        for key in [k for k, until in self._used_assertions.items() if until <= now]:
            del self._used_assertions[key]

    def _live_session(self, handle: object, now: float) -> Session:
        digest = _digest(handle)
        session = self._sessions.get(digest) if digest is not None else None
        if session is None:
            raise SessionError(SessionRejection.INVALID_SESSION)
        if not self._is_live(session, now):
            del self._sessions[session.handle_digest]
            raise SessionError(SessionRejection.INVALID_SESSION)
        return session

    def _new_handle(self) -> Tuple[str, str]:
        while True:
            handle = secrets.token_urlsafe(HANDLE_BYTES)
            digest = _digest(handle)
            if digest is not None and digest not in self._sessions:
                return handle, digest

    @staticmethod
    def _require_directory(directory: object) -> None:
        if type(directory) is not IdentityDirectory:
            raise SessionError(SessionRejection.UNTRUSTED_DIRECTORY)

    @staticmethod
    def _resolve(
        verified: VerifiedIdentityAssertion,
        directory: IdentityDirectory,
        role: Optional[ActorRole | str],
    ) -> Tuple[str, Optional[ActorRole]]:
        """(person_id, selected role or None), through the current directory."""

        reason = SessionRejection.ACCESS_DENIED
        try:
            person = directory.person_for(verified.identity)
            if role is None:
                return person.person_id, None
            assignment = directory.select_role(person, role)
            return person.person_id, assignment.role
        except AssignmentError as exc:
            if exc.reason == INVALID_ROLE:
                reason = SessionRejection.INVALID_ROLE
            elif exc.reason == ROLE_NOT_ASSIGNED:
                reason = SessionRejection.ROLE_NOT_ASSIGNED
        except Exception:  # noqa: BLE001 - any directory fault is a denial
            pass
        # Raised outside the except blocks: no chained cause or context,
        # so nothing the directory said can escape.
        raise SessionError(reason)

    # -- operations -----------------------------------------------------

    def create(
        self,
        verified: VerifiedIdentityAssertion,
        directory: IdentityDirectory,
        requested_role: Optional[ActorRole | str] = None,
    ) -> SessionHandle:
        """A new session for a verified assertion, or raise SessionError."""

        if type(verified) is not VerifiedIdentityAssertion:
            raise SessionError(SessionRejection.UNTRUSTED_ASSERTION)
        self._require_directory(directory)

        cfg = self._config
        with self._lock:
            now = self._now()
            self._purge(now)

            if now >= verified.expires_at:
                raise SessionError(SessionRejection.ASSERTION_EXPIRED)
            if now - verified.auth_time > cfg.max_auth_age_seconds:
                raise SessionError(SessionRejection.AUTH_TOO_OLD)

            used_key = (verified.issuer, verified.assertion_id)
            if used_key in self._used_assertions:
                raise SessionError(SessionRejection.ASSERTION_ALREADY_USED)

            person_id, role = self._resolve(verified, directory, requested_role)

            # The used-assertion map needs no cap of its own: each entry
            # needs an assertion the verifier accepted and self-purges at
            # that assertion's expiry (at most its 300 s lifetime).
            if len(self._sessions) >= cfg.max_sessions:
                raise SessionError(SessionRejection.SESSION_STORE_FULL)
            if (
                sum(1 for s in self._sessions.values() if s.person_id == person_id)
                >= cfg.per_person_limit
            ):
                raise SessionError(SessionRejection.PERSON_SESSION_LIMIT)

            handle, digest = self._new_handle()
            session_ref = secrets.token_urlsafe(SESSION_REF_BYTES)
            absolute = min(
                now + cfg.absolute_lifetime_seconds,
                float(verified.auth_time + cfg.max_auth_age_seconds),
            )
            session = Session(
                handle_digest=digest,
                session_ref=session_ref,
                verified=verified,
                person_id=person_id,
                requested_role=role,
                created_at=now,
                last_seen_at=now,
                absolute_expires_at=absolute,
                fresh_until=float(verified.expires_at),
            )
            self._sessions[digest] = session
            self._used_assertions[used_key] = float(verified.expires_at)

            return SessionHandle(
                handle, session_ref, min(absolute, session.fresh_until)
            )

    def actor_for(self, handle: str, directory: IdentityDirectory):
        """The live Actor for this handle under the CURRENT directory."""

        self._require_directory(directory)

        reason = SessionRejection.ACCESS_DENIED
        destroy = False
        with self._lock:
            now = self._now()
            session = self._live_session(handle, now)

            try:
                actor = authenticated_actor(
                    session.verified, directory, session.requested_role
                )
                if actor.actor_id != session.person_id:
                    destroy = True
                else:
                    self._sessions[session.handle_digest] = replace(
                        session, last_seen_at=now
                    )
                    return actor
            except AssignmentError as exc:
                if exc.reason == ROLE_SELECTION_REQUIRED:
                    reason = SessionRejection.ROLE_SELECTION_REQUIRED
            except DirectoryError as exc:
                destroy = exc.reason in (IDENTITY_NOT_ENROLLED, PERSON_NOT_ENROLLED)
            except Exception:  # noqa: BLE001 - fail closed
                pass

            if destroy:
                self._sessions.pop(session.handle_digest, None)
        raise SessionError(reason)

    def set_role(
        self,
        handle: str,
        role: ActorRole | str,
        directory: IdentityDirectory,
    ) -> SessionHandle:
        """Select a role the CURRENT directory already assigns; rotate the handle.

        An authorization change, not an authentication event: no new
        assertion is needed. The old handle stops working atomically.
        """

        self._require_directory(directory)
        if role is None:
            raise SessionError(SessionRejection.INVALID_ROLE)

        with self._lock:
            now = self._now()
            session = self._live_session(handle, now)

            try:
                person_id, selected = self._resolve(
                    session.verified, directory, role
                )
            except SessionError as exc:
                if exc.reason is SessionRejection.ACCESS_DENIED:
                    # The person is gone or a directory fault: no session
                    # should outlive that.
                    self._sessions.pop(session.handle_digest, None)
                raise

            if person_id != session.person_id:
                self._sessions.pop(session.handle_digest, None)
                raise SessionError(SessionRejection.ACCESS_DENIED)

            new_handle, new_digest = self._new_handle()
            del self._sessions[session.handle_digest]
            rotated = replace(
                session,
                handle_digest=new_digest,
                requested_role=selected,
                last_seen_at=now,
            )
            self._sessions[new_digest] = rotated

            return SessionHandle(
                new_handle,
                rotated.session_ref,
                min(rotated.absolute_expires_at, rotated.fresh_until),
            )

    def logout(self, handle: str) -> None:
        """End the session. Idempotent; reveals nothing about the handle."""

        digest = _digest(handle)
        if digest is None:
            return
        with self._lock:
            self._sessions.pop(digest, None)


__all__ = [
    "DEFAULT_ABSOLUTE_LIFETIME_SECONDS",
    "DEFAULT_IDLE_TIMEOUT_SECONDS",
    "Session",
    "SessionConfig",
    "SessionConfigError",
    "SessionError",
    "SessionHandle",
    "SessionManager",
    "SessionRejection",
]
