"""Slice 10.2e: the programmatic, in-memory session core.

No HTTP, no cookies, no persistence. Assertions come from the real 10.2a
verifier with a locally generated ES256 key, exactly as the 10.2c tests do.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import math
import pickle
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Set

import pytest

import backend.app.identity as identity_package
from backend.app.api.composition import (
    AUTHENTICATION_MECHANISM_UNAVAILABLE,
    AuthConfigurationError,
    AuthMode,
    compose_http_jobs,
    compose_jobs,
    resolve_auth_mode,
)
from backend.app.api.deps import request_actor
from backend.app.identity.actor import Actor, ActorRole, IdentityAssurance
from backend.app.identity.assignments import RoleAssignment
from backend.app.identity.authenticated_policy import AuthenticatedEnforcingPolicy
from backend.app.identity.directory import IdentityDirectory
from backend.app.identity.identity_assertion import (
    IdentityAssertionVerifier,
    InMemoryReplayGuard,
    VerifiedIdentityAssertion,
)
from backend.app.identity.person import ExternalIdentity, Person
from backend.app.identity.policy import StaticScopeDirectory
from backend.app.identity.session import (
    DEFAULT_ABSOLUTE_LIFETIME_SECONDS,
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    Session,
    SessionConfig,
    SessionConfigError,
    SessionError,
    SessionHandle,
    SessionManager,
    SessionRejection as R,
)
from backend.app.identity.trusted_keys import StaticKeyProvider
from backend.tests.test_slice10_2a_identity_assertion import (
    ALIAS,
    NOW,
    FixedClock,
    SigningKey,
    base_claims,
    config,
    jwks,
    mint,
)
from backend.tests import test_slice10_2c_authenticated_assurance as t2c

W, E, A, ADM = ActorRole.WORKER, ActorRole.ENGINEER, ActorRole.AUTHORITY, ActorRole.ADMIN
AUTH = IdentityAssurance.AUTHENTICATED

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "backend" / "app"
SESSION_SOURCE = (APP_ROOT / "identity" / "session.py").read_text(encoding="utf-8")

SUBJECTS = {name: str(uuid.uuid4()) for name in ("ONE", "MULTI", "ADMIN", "OTHER")}

# Assertion facts from base_claims: iat = NOW-10, exp = NOW+290, auth_time = NOW-120.
EXP = NOW - 10 + 300


# ---------------------------------------------------------------- helpers


def directory(
    spec: Optional[Dict[str, Sequence[ActorRole]]] = None,
    subjects: Optional[Dict[str, str]] = None,
) -> IdentityDirectory:
    spec = spec if spec is not None else {
        "P-ONE": [W],
        "P-MULTI": [W, E],
        "P-ADMIN": [ADM],
    }
    subjects = subjects or {
        "P-ONE": SUBJECTS["ONE"],
        "P-MULTI": SUBJECTS["MULTI"],
        "P-ADMIN": SUBJECTS["ADMIN"],
    }
    return IdentityDirectory(
        people=[Person(pid, ExternalIdentity(ALIAS, subjects[pid])) for pid in spec],
        role_assignments=[RoleAssignment(pid, r) for pid, roles in spec.items() for r in roles],
    )


@pytest.fixture()
def key() -> SigningKey:
    return SigningKey("key-current")


@pytest.fixture()
def verifier(key) -> IdentityAssertionVerifier:
    return IdentityAssertionVerifier(
        config(), StaticKeyProvider(jwks(key)), InMemoryReplayGuard(), FixedClock()
    )


@pytest.fixture()
def make(key, verifier):
    """A fresh genuine VerifiedIdentityAssertion per call (fresh jti)."""

    def _make(subject: str = SUBJECTS["ONE"], **claims: Any) -> VerifiedIdentityAssertion:
        return verifier.verify(mint(key, base_claims(sub=subject, **claims)))

    return _make


@pytest.fixture()
def clock() -> FixedClock:
    return FixedClock()


def manager(clock, **overrides: Any) -> SessionManager:
    values: Dict[str, Any] = {
        "max_auth_age_seconds": 3600,
        "max_sessions": 10,
        "per_person_limit": 3,
    }
    values.update(overrides)
    return SessionManager(SessionConfig(**values), clock)


def refused(reason: R, fn, *args, **kwargs) -> SessionError:
    with pytest.raises(SessionError) as info:
        fn(*args, **kwargs)
    assert info.value.reason is reason
    return info.value


# ------------------------------------------------------- A. creation


def test_create_with_a_genuine_assertion_and_derive_the_actor(make, clock):
    m, d = manager(clock), directory()
    issued = m.create(make(), d)

    assert isinstance(issued, SessionHandle)
    assert len(m) == 1

    actor = m.actor_for(issued.handle, d)
    assert type(actor) is Actor
    assert (actor.actor_id, actor.role, actor.assurance) == ("P-ONE", W, AUTH)


def test_exact_type_is_required_for_the_assertion(make, clock):
    m, d = manager(clock), directory()
    genuine = make()

    class Sub(VerifiedIdentityAssertion):
        pass

    forged = object.__new__(Sub)
    for name in (
        "identity", "issuer", "audience", "session_id", "assertion_id",
        "key_id", "issued_at", "expires_at", "auth_time",
    ):
        object.__setattr__(forged, name, getattr(genuine, name))

    duck = SimpleNamespace(**{
        n: getattr(genuine, n) for n in (
            "identity", "issuer", "audience", "session_id", "assertion_id",
            "key_id", "issued_at", "expires_at", "auth_time",
        )
    })

    for bad in (forged, duck, None, "token", genuine.identity):
        refused(R.UNTRUSTED_ASSERTION, m.create, bad, d)
    assert len(m) == 0


def test_directory_must_be_exactly_an_identity_directory(make, clock):
    m = manager(clock)
    sessions = m.create(make(), directory())
    static = StaticScopeDirectory([])

    refused(R.UNTRUSTED_DIRECTORY, m.create, make(), static)
    refused(R.UNTRUSTED_DIRECTORY, m.actor_for, sessions.handle, static)
    refused(R.UNTRUSTED_DIRECTORY, m.set_role, sessions.handle, W, static)


def test_an_expired_assertion_cannot_create_a_session(make):
    verified = make()
    d = directory()

    refused(R.ASSERTION_EXPIRED, manager(FixedClock(EXP)).create, verified, d)
    refused(R.ASSERTION_EXPIRED, manager(FixedClock(EXP + 500)).create, verified, d)
    # One second before expiry is still creatable.
    assert manager(FixedClock(EXP - 1)).create(verified, d)


def test_auth_time_max_age_is_enforced_at_the_boundary(make, clock):
    d = directory()
    age = NOW - (NOW - 120)  # 120 s

    assert manager(clock, max_auth_age_seconds=age).create(make(), d)
    refused(R.AUTH_TOO_OLD, manager(clock, max_auth_age_seconds=age - 1).create, make(), d)


def test_one_assertion_can_create_only_one_session(make, clock):
    m, d = manager(clock), directory()
    verified = make()

    first = m.create(verified, d)
    refused(R.ASSERTION_ALREADY_USED, m.create, verified, d)

    # Not even after the first session is gone.
    m.logout(first.handle)
    refused(R.ASSERTION_ALREADY_USED, m.create, verified, d)


def test_a_failed_create_does_not_burn_the_assertion(make, clock):
    m, d = manager(clock), directory()
    verified = make()

    refused(R.ROLE_NOT_ASSIGNED, m.create, verified, d, E)
    assert m.create(verified, d, W)


# -------------------------------------------------- B. handle and storage


def test_handle_is_256_random_bits_and_unique(make, clock):
    m, d = manager(clock, max_sessions=100, per_person_limit=100), directory()
    handles = [m.create(make(), d).handle for _ in range(40)]

    assert len(set(handles)) == 40
    for handle in handles:
        assert len(handle) == 43
        assert len(base64.urlsafe_b64decode(handle + "=")) == 32


def test_raw_handle_is_never_stored_only_its_sha256(make, clock):
    m, d = manager(clock), directory()
    issued = m.create(make(), d)
    digest = hashlib.sha256(issued.handle.encode("ascii")).hexdigest()

    assert list(m._sessions) == [digest]
    session = m._sessions[digest]
    assert session.handle_digest == digest

    haystack = repr(vars(m)) + repr(session) + "".join(
        repr(getattr(session, f)) for f in session.__dataclass_fields__ if f != "verified"
    )
    assert issued.handle not in haystack
    # repr of the store contents never shows the digest either.
    assert digest not in repr(session)
    assert issued.handle not in repr(issued)
    assert issued.handle not in str(m._used_assertions)


def test_session_ref_is_separate_from_the_handle_and_no_credential(make, clock):
    m, d = manager(clock), directory()
    issued = m.create(make(), d)
    digest = hashlib.sha256(issued.handle.encode("ascii")).hexdigest()

    assert issued.session_ref not in (issued.handle, digest)
    assert issued.session_ref not in issued.handle and issued.handle not in issued.session_ref
    assert m._sessions[digest].session_ref == issued.session_ref

    refused(R.INVALID_SESSION, m.actor_for, issued.session_ref, d)
    padded = (issued.session_ref * 3)[:43]
    refused(R.INVALID_SESSION, m.actor_for, padded, d)


def test_session_holds_no_actor_scope_role_assignment_or_raw_material(make, clock):
    m, d = manager(clock), directory()
    verified = make()
    issued = m.create(verified, d, W)
    session = m._sessions[hashlib.sha256(issued.handle.encode("ascii")).hexdigest()]

    assert set(Session.__dataclass_fields__) == {
        "handle_digest", "session_ref", "verified", "person_id",
        "requested_role", "created_at", "last_seen_at",
        "absolute_expires_at", "fresh_until",
    }
    assert session.verified is verified
    assert session.requested_role is W
    for value in vars(session).values():
        assert not isinstance(value, (Actor, RoleAssignment))
        if type(value) is str:  # ActorRole is a str enum, so match the exact type
            assert value in (session.handle_digest, session.session_ref, session.person_id)


def test_nothing_session_related_can_be_serialized(make, clock):
    m, d = manager(clock), directory()
    issued = m.create(make(), d)
    session = next(iter(m._sessions.values()))

    for obj in (m, session, issued):
        with pytest.raises(TypeError):
            pickle.dumps(obj)


def test_sessions_never_touch_the_jobs_database(make, clock):
    db = REPO_ROOT / "jobs.db"
    before = hashlib.sha256(db.read_bytes()).hexdigest() if db.exists() else None

    m, d = manager(clock), directory()
    issued = m.create(make(), d)
    m.actor_for(issued.handle, d)
    m.set_role(issued.handle, W, d)
    m.logout(issued.handle)

    after = hashlib.sha256(db.read_bytes()).hexdigest() if db.exists() else None
    assert before == after


# ------------------------------------ C. actor derivation, current directory


def test_actor_is_derived_through_the_current_directory_every_time(make, clock):
    m = manager(clock)
    issued = m.create(make(), directory())

    later = directory(
        {"P-ONE": [E]}, {"P-ONE": SUBJECTS["ONE"]}
    )
    actor = m.actor_for(issued.handle, later)
    assert (actor.actor_id, actor.role) == ("P-ONE", E)


def test_removing_the_held_role_after_login_is_a_denial(make, clock):
    m = manager(clock)
    issued = m.create(make(SUBJECTS["MULTI"]), directory(), E)
    assert m.actor_for(issued.handle, directory()).role is E

    without = directory({"P-MULTI": [W]}, {"P-MULTI": SUBJECTS["MULTI"]})
    refused(R.ACCESS_DENIED, m.actor_for, issued.handle, without)
    # The person is still enrolled: the session survives so a role can be re-selected.
    assert len(m) == 1
    rotated = m.set_role(issued.handle, W, without)
    assert m.actor_for(rotated.handle, without).role is W


def test_removing_the_person_is_a_denial_and_destroys_the_session(make, clock):
    m, d = manager(clock), directory()
    issued = m.create(make(), d)

    refused(R.ACCESS_DENIED, m.actor_for, issued.handle, IdentityDirectory())
    assert len(m) == 0
    # It does not come back when the person is re-enrolled.
    refused(R.INVALID_SESSION, m.actor_for, issued.handle, d)


def test_identity_that_maps_to_a_different_person_destroys_the_session(make, clock):
    m, d = manager(clock), directory()
    issued = m.create(make(), d)

    rebound = directory({"P-OTHER": [W]}, {"P-OTHER": SUBJECTS["ONE"]})
    refused(R.ACCESS_DENIED, m.actor_for, issued.handle, rebound)
    assert len(m) == 0
    refused(R.INVALID_SESSION, m.actor_for, issued.handle, d)


def test_requested_role_is_a_selector_never_a_grant(make, clock):
    m, d = manager(clock), directory()

    refused(R.ROLE_NOT_ASSIGNED, m.create, make(), d, E)
    refused(R.ROLE_NOT_ASSIGNED, m.create, make(), d, ADM)
    refused(R.ROLE_NOT_ASSIGNED, m.create, make(), d, "AUTHORITY")
    for junk in ("SYSTEM", "UNIDENTIFIED", "worker", "", "root", 7):
        refused(R.INVALID_ROLE, m.create, make(), d, junk)
    assert len(m) == 0

    # And a stored selector cannot grant later: the directory decides per call.
    issued = m.create(make(SUBJECTS["MULTI"]), d, E)
    narrowed = directory({"P-MULTI": [W]}, {"P-MULTI": SUBJECTS["MULTI"]})
    refused(R.ACCESS_DENIED, m.actor_for, issued.handle, narrowed)


def test_a_role_is_never_taken_from_novaforge(key, verifier, clock):
    verified = verifier.verify(
        mint(key, base_claims(sub=SUBJECTS["ONE"], role="SUPER_ADMIN", trust_level=100))
    )
    m, d = manager(clock), directory()
    actor = m.actor_for(m.create(verified, d).handle, d)
    assert actor.role is W


def test_multiple_roles_follow_the_directory_selection_semantics(make, clock):
    m, d = manager(clock), directory()
    issued = m.create(make(SUBJECTS["MULTI"]), d)

    # No selector, two roles: exactly select_role's ROLE_SELECTION_REQUIRED.
    refused(R.ROLE_SELECTION_REQUIRED, m.actor_for, issued.handle, d)
    assert len(m) == 1

    rotated = m.set_role(issued.handle, E, d)
    assert m.actor_for(rotated.handle, d).role is E


# ------------------------------------------------------- D. set_role


def test_set_role_requires_a_role_the_directory_assigns(make, clock):
    m, d = manager(clock), directory()
    issued = m.create(make(SUBJECTS["MULTI"]), d)

    refused(R.ROLE_NOT_ASSIGNED, m.set_role, issued.handle, A, d)
    refused(R.ROLE_NOT_ASSIGNED, m.set_role, issued.handle, ADM, d)
    for junk in (None, "SYSTEM", "", "worker", 3):
        refused(R.INVALID_ROLE, m.set_role, issued.handle, junk, d)

    # A refused change leaves the session and its handle untouched.
    assert m.set_role(issued.handle, W, d)


def test_set_role_rotates_the_handle_and_invalidates_the_old_one(make, clock):
    m, d = manager(clock), directory()
    first = m.create(make(SUBJECTS["MULTI"]), d, W)
    second = m.set_role(first.handle, E, d)

    assert second.handle != first.handle
    assert second.session_ref == first.session_ref
    assert len(m) == 1
    refused(R.INVALID_SESSION, m.actor_for, first.handle, d)
    refused(R.INVALID_SESSION, m.set_role, first.handle, W, d)
    assert m.actor_for(second.handle, d).role is E


def test_set_role_needs_no_new_assertion_and_does_not_extend_lifetime(make, clock):
    m, d = manager(clock), directory()
    first = m.create(make(SUBJECTS["MULTI"]), d, W)
    second = m.set_role(first.handle, E, d)
    assert second.expires_at == first.expires_at


def test_set_role_for_a_vanished_person_destroys_the_session(make, clock):
    m, d = manager(clock), directory()
    issued = m.create(make(), d)

    refused(R.ACCESS_DENIED, m.set_role, issued.handle, W, IdentityDirectory())
    assert len(m) == 0


# ------------------------------------------------------ E. logout


def test_logout_invalidates_the_handle_and_is_idempotent(make, clock):
    m, d = manager(clock), directory()
    issued = m.create(make(), d)

    assert m.logout(issued.handle) is None
    refused(R.INVALID_SESSION, m.actor_for, issued.handle, d)
    assert m.logout(issued.handle) is None
    assert len(m) == 0

    for junk in (None, 5, "", "x", "x" * 43, "!" * 43, b"bytes", issued.session_ref):
        assert m.logout(junk) is None  # type: ignore[arg-type]


def test_an_actor_already_derived_survives_a_later_logout(make, clock):
    """The documented time-of-check boundary: the Actor is a value."""

    m, d = manager(clock), directory()
    issued = m.create(make(), d)
    actor = m.actor_for(issued.handle, d)

    m.logout(issued.handle)

    assert actor.actor_id == "P-ONE"
    refused(R.INVALID_SESSION, m.actor_for, issued.handle, d)


def test_unknown_expired_and_logged_out_handles_are_indistinguishable(make):
    clock = FixedClock()
    m, d = manager(clock, idle_timeout_seconds=100), directory()

    gone = m.create(make(), d)
    m.logout(gone.handle)
    stale = m.create(make(), d)
    clock.now = NOW + 100

    messages = set()
    for handle in (gone.handle, stale.handle, "A" * 43, "not-a-handle", None):
        err = refused(R.INVALID_SESSION, m.actor_for, handle, d)
        messages.add(str(err))
    assert len(messages) == 1


# ------------------------------------------------------ F. time bounds


def test_session_is_bounded_by_the_assertion_expiry(make):
    clock = FixedClock()
    m, d = manager(clock), directory()  # idle 30 min, absolute 12 h, auth age 1 h
    issued = m.create(make(), d)
    session = next(iter(m._sessions.values()))

    assert session.fresh_until == EXP
    assert issued.expires_at <= EXP
    assert session.absolute_expires_at > EXP  # the other limits are far longer

    clock.now = EXP - 1
    assert m.actor_for(issued.handle, d)
    clock.now = EXP
    refused(R.INVALID_SESSION, m.actor_for, issued.handle, d)
    assert len(m) == 0


def test_idle_timeout(make):
    clock = FixedClock()
    m, d = manager(clock, idle_timeout_seconds=100), directory()
    issued = m.create(make(), d)

    clock.now = NOW + 99
    assert m.actor_for(issued.handle, d)  # activity resets the idle clock
    clock.now = NOW + 198
    assert m.actor_for(issued.handle, d)
    clock.now = NOW + 298
    refused(R.INVALID_SESSION, m.actor_for, issued.handle, d)


def test_idle_timeout_without_activity(make):
    clock = FixedClock()
    m, d = manager(clock, idle_timeout_seconds=100), directory()
    issued = m.create(make(), d)

    clock.now = NOW + 100
    refused(R.INVALID_SESSION, m.actor_for, issued.handle, d)
    assert len(m) == 0


def test_absolute_timeout_is_not_extended_by_activity(make):
    clock = FixedClock()
    m, d = manager(clock, absolute_lifetime_seconds=50), directory()
    issued = m.create(make(), d)

    for t in (10, 20, 30, 40, 49):
        clock.now = NOW + t
        assert m.actor_for(issued.handle, d)
    clock.now = NOW + 50
    refused(R.INVALID_SESSION, m.actor_for, issued.handle, d)


def test_absolute_lifetime_is_also_capped_by_auth_age(make):
    clock = FixedClock()
    m, d = manager(clock, max_auth_age_seconds=130), directory()  # auth_time = NOW-120
    issued = m.create(make(), d)

    clock.now = NOW + 9
    assert m.actor_for(issued.handle, d)
    clock.now = NOW + 10
    refused(R.INVALID_SESSION, m.actor_for, issued.handle, d)


@pytest.mark.parametrize(
    "reading",
    [math.nan, math.inf, -math.inf, 0, -1, None, "1800000000", True, [], object()],
)
def test_an_unusable_clock_fails_closed(make, reading):
    good = FixedClock()
    m, d = manager(good), directory()
    issued = m.create(make(SUBJECTS["MULTI"]), d, W)
    verified = make()

    good.now = reading  # type: ignore[assignment]
    refused(R.CLOCK_UNAVAILABLE, m.create, verified, d)
    refused(R.CLOCK_UNAVAILABLE, m.actor_for, issued.handle, d)
    refused(R.CLOCK_UNAVAILABLE, m.set_role, issued.handle, E, d)

    # An unusable clock did not destroy anything, and logout needs no clock.
    good.now = NOW
    assert m.actor_for(issued.handle, d)
    good.now = math.nan  # type: ignore[assignment]
    assert m.logout(issued.handle) is None


def test_a_raising_clock_fails_closed(make, clock):
    def boom() -> float:
        raise RuntimeError("clock down")

    m = SessionManager(SessionConfig(3600, 10, 3), boom)
    refused(R.CLOCK_UNAVAILABLE, m.create, make(), directory())


# ------------------------------------------------------ G. capacity


def test_global_capacity_fails_closed_and_never_evicts(make, clock):
    m, d = manager(clock, max_sessions=2, per_person_limit=2), directory()
    a = m.create(make(SUBJECTS["ONE"]), d)
    b = m.create(make(SUBJECTS["ADMIN"]), d)

    refused(R.SESSION_STORE_FULL, m.create, make(SUBJECTS["MULTI"]), d, W)
    assert len(m) == 2
    assert m.actor_for(a.handle, d) and m.actor_for(b.handle, d)

    m.logout(a.handle)
    assert m.create(make(SUBJECTS["MULTI"]), d, W)


def test_expired_sessions_free_capacity_but_live_ones_are_never_evicted(make):
    clock = FixedClock()
    m, d = manager(clock, max_sessions=1, per_person_limit=1, idle_timeout_seconds=100), directory()
    first = m.create(make(), d)
    refused(R.SESSION_STORE_FULL, m.create, make(SUBJECTS["ADMIN"]), d)

    clock.now = NOW + 100
    assert m.create(make(SUBJECTS["ADMIN"]), d)
    refused(R.INVALID_SESSION, m.actor_for, first.handle, d)


def test_per_person_limit(make, clock):
    m, d = manager(clock, max_sessions=10, per_person_limit=2), directory()
    m.create(make(), d)
    m.create(make(), d)

    refused(R.PERSON_SESSION_LIMIT, m.create, make(), d)
    assert m.create(make(SUBJECTS["ADMIN"]), d)  # another person is unaffected
    assert len(m) == 3


# ------------------------------------------------------ H. concurrency


def _run_threads(count: int, target) -> List[Any]:
    barrier = threading.Barrier(count)
    results: List[Any] = [None] * count

    def worker(i: int) -> None:
        barrier.wait()
        try:
            results[i] = ("ok", target(i))
        except SessionError as exc:
            results[i] = ("err", exc.reason)
        except BaseException as exc:  # noqa: BLE001 - surfaced by the asserts
            results[i] = ("boom", exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_concurrent_creation_from_one_assertion_yields_one_session(make, clock):
    m, d = manager(clock, max_sessions=50, per_person_limit=50), directory()
    verified = make()

    results = _run_threads(24, lambda i: m.create(verified, d))

    assert [r[0] for r in results].count("ok") == 1
    assert all(r == ("err", R.ASSERTION_ALREADY_USED) for r in results if r[0] != "ok")
    assert len(m) == 1


def test_concurrent_creation_cannot_exceed_capacity(make, clock):
    m, d = manager(clock, max_sessions=3, per_person_limit=3), directory()
    assertions = [make() for _ in range(16)]

    results = _run_threads(16, lambda i: m.create(assertions[i], d))

    assert [r[0] for r in results].count("ok") == 3
    assert all(r == ("err", R.PERSON_SESSION_LIMIT) or r == ("err", R.SESSION_STORE_FULL)
               for r in results if r[0] != "ok")
    assert len(m) == 3


def test_concurrent_role_rotation_leaves_exactly_one_valid_handle(make, clock):
    m, d = manager(clock), directory()
    issued = m.create(make(SUBJECTS["MULTI"]), d, W)

    results = _run_threads(12, lambda i: m.set_role(issued.handle, E if i % 2 else W, d))

    winners = [r[1] for r in results if r[0] == "ok"]
    assert len(winners) == 1
    assert all(r == ("err", R.INVALID_SESSION) for r in results if r[0] != "ok")
    assert len(m) == 1
    refused(R.INVALID_SESSION, m.actor_for, issued.handle, d)
    assert m.actor_for(winners[0].handle, d)


def test_concurrent_logout_and_lookup_are_safe(make, clock):
    m, d = manager(clock), directory()
    issued = m.create(make(), d)

    def target(i: int):
        if i == 0:
            return m.logout(issued.handle)
        return m.actor_for(issued.handle, d)

    results = _run_threads(16, target)

    assert not [r for r in results if r[0] == "boom"]
    for tag, value in results[1:]:
        assert (tag == "ok" and value.actor_id == "P-ONE") or (
            tag == "err" and value is R.INVALID_SESSION
        )
    refused(R.INVALID_SESSION, m.actor_for, issued.handle, d)
    assert len(m) == 0


# ------------------------------------------------------ I. errors


def test_errors_carry_a_reason_only_and_no_chained_detail(make, clock):
    m, d = manager(clock), directory()
    verified = make()
    issued = m.create(verified, d)

    errors = [
        refused(R.ASSERTION_ALREADY_USED, m.create, verified, d),
        refused(R.INVALID_SESSION, m.actor_for, "A" * 43, d),
        refused(R.ACCESS_DENIED, m.create, make("unenrolled-subject"), d),
        refused(R.ACCESS_DENIED, m.actor_for, issued.handle, IdentityDirectory()),
    ]
    for err in errors:
        assert str(err) == f"Session refused: {err.reason.value}"
        assert err.__cause__ is None and err.__context__ is None
        for secret in (issued.handle, verified.assertion_id, verified.session_id,
                       "unenrolled-subject", SUBJECTS["ONE"], "P-ONE"):
            assert secret not in str(err) and secret not in repr(err)


def test_unenrolled_identity_is_indistinguishable_from_no_assignment(make, clock):
    m = manager(clock)
    no_roles = IdentityDirectory(
        people=[Person("P-NONE", ExternalIdentity(ALIAS, SUBJECTS["OTHER"]))]
    )
    # An enrolled person with no role still gets a session (roles are
    # resolved on derivation, exactly as select_role does), but no actor.
    issued = m.create(make(SUBJECTS["OTHER"]), no_roles)
    a = refused(R.ACCESS_DENIED, m.actor_for, issued.handle, no_roles)
    b = refused(R.ACCESS_DENIED, m.create, make("someone-else"), no_roles)
    assert str(a) == str(b)


def test_config_is_validated_and_immutable():
    ok = dict(max_auth_age_seconds=60, max_sessions=4, per_person_limit=2)
    cfg = SessionConfig(**ok)
    assert cfg.idle_timeout_seconds == DEFAULT_IDLE_TIMEOUT_SECONDS == 1800
    assert cfg.absolute_lifetime_seconds == DEFAULT_ABSOLUTE_LIFETIME_SECONDS == 43200
    with pytest.raises(Exception):
        cfg.max_sessions = 9  # type: ignore[misc]

    for bad in (
        {"max_auth_age_seconds": 0}, {"max_auth_age_seconds": True},
        {"max_auth_age_seconds": 60.5}, {"max_auth_age_seconds": 10**9},
        {"max_sessions": 0}, {"max_sessions": 10**7},
        {"per_person_limit": 0}, {"per_person_limit": 5},
        {"idle_timeout_seconds": 0}, {"idle_timeout_seconds": 10**6},
        {"absolute_lifetime_seconds": 0}, {"absolute_lifetime_seconds": 43201},
    ):
        with pytest.raises(SessionConfigError):
            SessionConfig(**{**ok, **bad})

    # The three undocumented limits have no default.
    with pytest.raises(TypeError):
        SessionConfig()  # type: ignore[call-arg]

    with pytest.raises(SessionConfigError):
        SessionManager(object())  # type: ignore[arg-type]
    with pytest.raises(SessionConfigError):
        SessionManager(cfg, clock=5)  # type: ignore[arg-type]


# ------------------------------------------ J. structural security pins


def _tree() -> ast.AST:
    return ast.parse(SESSION_SOURCE)


def test_session_module_does_not_name_the_authenticated_value_or_capability():
    assert not t2c.mentions_authenticated_value(SESSION_SOURCE)
    assert not t2c.references_capability(SESSION_SOURCE)
    assert not t2c.constructs_actor_as_authenticated(SESSION_SOURCE)
    assert "_AUTHENTICATION_CAPABILITY" not in SESSION_SOURCE

    names: Set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(a.name.split(".")[-1] for a in node.names)
    assert "IdentityAssurance" not in names
    assert "AUTHENTICATED" not in names


def test_session_module_never_constructs_an_actor():
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            assert name not in {
                "Actor", "human_actor", "system_actor", "unidentified_actor",
                "VerifiedIdentityAssertion", "RecordedActor", "rehydrate_actor",
            }


ALLOWED_IMPORTS = {
    "__future__", "hashlib", "math", "secrets", "threading", "time",
    "dataclasses", "enum", "typing",
    "backend.app.identity.actor",
    "backend.app.identity.assignments",
    "backend.app.identity.authenticated_actor",
    "backend.app.identity.directory",
    "backend.app.identity.identity_assertion",
}


def test_session_module_reads_no_environment_and_does_no_io():
    imported: Set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert imported <= ALLOWED_IMPORTS, imported - ALLOWED_IMPORTS

    for node in ast.walk(_tree()):
        if isinstance(node, ast.Name):
            assert node.id not in {"open", "os", "environ", "getenv", "sqlite3", "socket", "print"}
        if isinstance(node, ast.Attribute):
            assert node.attr not in {"environ", "getenv", "read_text", "write_text",
                                     "read_bytes", "write_bytes", "connect", "urlopen"}
    for token in ("environ", "getenv", "sqlite", "urllib", "socket", "PASHUPAT_"):
        assert token not in SESSION_SOURCE


def test_session_is_not_exported_or_used_by_any_other_production_module():
    init_source = (APP_ROOT / "identity" / "__init__.py").read_text(encoding="utf-8")
    assert "session" not in init_source.lower()
    for name in ("SessionManager", "SessionConfig", "SessionError", "Session"):
        assert name not in identity_package.__all__
        assert not hasattr(identity_package, name)

    users = [
        rel for rel, src in t2c.production_sources()
        if rel != "identity/session.py" and (
            "identity.session" in src or "identity import session" in src
        )
    ]
    assert users == []


def _mentions(source: str, wanted: Set[str]) -> bool:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name) and node.id in wanted:
            return True
        if isinstance(node, ast.Attribute) and node.attr in wanted:
            return True
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in wanted:
            return True
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[-1] in wanted:
                return True
            if any(a.name in wanted for a in node.names):
                return True
        if isinstance(node, ast.Import) and any(
            a.name.split(".")[-1] in wanted for a in node.names
        ):
            return True
    return False


def test_only_three_production_modules_hold_the_verified_type_or_call_the_factory():
    referencing = {
        rel for rel, src in t2c.production_sources()
        if _mentions(src, {"VerifiedIdentityAssertion", "authenticated_actor"})
    }
    assert referencing == {
        "identity/identity_assertion.py",
        "identity/authenticated_actor.py",
        "identity/session.py",
    }


def test_the_allowlist_scanner_can_fail():
    assert _mentions("from backend.app.identity.authenticated_actor import x", {"authenticated_actor"})
    assert _mentions("authenticated_actor(a, b)", {"authenticated_actor"})
    assert _mentions("import a.b.VerifiedIdentityAssertion", {"VerifiedIdentityAssertion"})
    assert not _mentions("x = 1", {"VerifiedIdentityAssertion", "authenticated_actor"})


def test_existing_authentication_pins_are_unchanged():
    assert t2c.ALLOWED_CAPABILITY_MODULES == {
        "identity/actor.py",
        "identity/authenticated_actor.py",
    }
    assert t2c.ALLOWED_VALUE_NAMING_MODULES - t2c.ALLOWED_CAPABILITY_MODULES == {
        "identity/authenticated_policy.py",
        "identity/recorded_actor.py",
    }
    assert "identity/session.py" not in t2c.ALLOWED_VALUE_NAMING_MODULES


# ------------------------------------------ K. HTTP and demo unchanged


def test_http_novaforge_boot_is_still_refused():
    with pytest.raises(AuthConfigurationError) as info:
        compose_http_jobs({"PASHUPAT_AUTH_MODE": "novaforge"})
    assert info.value.reason == AUTHENTICATION_MECHANISM_UNAVAILABLE


def test_request_actor_is_unchanged_and_ignores_sessions():
    none = request_actor(actor_id=None, actor_role=None)
    assert none.assurance is IdentityAssurance.NONE

    declared = request_actor(actor_id="WORKER-042", actor_role="worker")
    assert declared.assurance is IdentityAssurance.DECLARED_UNVERIFIED
    assert declared.actor_id == "WORKER-042"

    for rel in ("api/deps.py", "api/composition.py", "api/main.py", "jobs/router.py", "jobs/service.py"):
        src = (APP_ROOT / rel).read_text(encoding="utf-8")
        assert "identity.session" not in src and "SessionManager" not in src


def test_demo_mode_is_unchanged(tmp_path):
    assert resolve_auth_mode({}) is AuthMode.DEMO
    composed = compose_jobs(AuthMode.DEMO, db_path=tmp_path / "demo.db")
    assert composed.mode is AuthMode.DEMO
    assert type(composed.service.authorization) is not AuthenticatedEnforcingPolicy
    with pytest.raises(AuthConfigurationError):
        resolve_auth_mode({"PASHUPAT_AUTH_SESSION_IDLE": "1"})
