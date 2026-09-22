"""Verifier for NovaForge signed identity assertions (Slice 10.2a).

    untrusted assertion string  ->  VerifiedIdentityAssertion   (or reject)

This is the one Pashupatastra primitive that can turn an identity
assertion into a verified identity fact. It is deliberately NOT
authentication mode, NOT an Actor, NOT a railway role and NOT a railway
scope:
  - it establishes WHO NovaForge says authenticated, not what they may do;
  - a later identity directory maps (identity_provider, subject) to a
    Person;
  - Pashupatastra's own RoleAssignment and RoleScopeAssignment remain
    the only source of railway authority.
Nothing is wired into the API yet. request_actor and X-Actor-* are
unchanged.

THE CONTRACT, AS IMPLEMENTED BY NOVAFORGE @ 5f3a2a6
(server/src/lib/identityAssertion.ts issueIdentityAssertion, signed with
jsonwebtoken 9 jwt.sign; reference verifier
server/src/lib/identityAssertionVerifier.ts)

  JOSE header (exactly these three members):
    alg  "ES256"             ECDSA P-256 / SHA-256, raw r||s signature.
    typ  "nf-identity+jwt"
    kid  key id of the current signing key (see trusted_keys).

  Claims:
    iss             identityIssuer(): IDENTITY_ISSUER, falling back to
                    env.rpOrigin (default "http://localhost:5173").
                    REQUIRED, exact match against the pinned configured
                    issuer.
    aud             the audience of the AUTHENTICATED service account,
                    never chosen by the caller (routes/internal.ts:57).
                    A single string. REQUIRED, exact match; an array is
                    refused.
    sub             User.id, a Prisma uuid(). REQUIRED. Becomes
                    ExternalIdentity.subject verbatim, never normalised.
    jti             randomUUID(). REQUIRED. Used for replay detection.
    iat             added by jsonwebtoken (integer seconds). REQUIRED.
    exp             iat + IDENTITY_ASSERTION_TTL_SEC (300). REQUIRED.
    sid             NovaForge Session.id (uuid). REQUIRED, exposed.
    token_use       "identity_assertion". REQUIRED, exact.
    principal_type  "user". REQUIRED, exact.
    account_status  "ACTIVE". REQUIRED, exact. NovaForge refuses to
                    issue for an inactive user.
    auth_time       floor(Session.createdAt / 1000). REQUIRED, exposed.
    role            NovaForge product role (SUPER_ADMIN, ..., MEMBER).
                    IGNORED. It is neither required nor exposed, and it
                    has no railway meaning (architecture report section 6).
    trust_level     decayed session trust 0..100. IGNORED and not
                    exposed.
    nbf             NOT issued. Honoured if ever present.
  Any other claim, such as a smuggled "person_id", "railway_role" or
  "scope", is ignored and never reaches the result.

  identity_provider is NOT a claim. The alias ("novaforge") comes from
  Pashupatastra configuration, pinned together with the issuer, and
  never from the token (architecture report section 5).

KNOWN CONTRACT GAPS (NovaForge NF-1..NF-4 / joint; not papered over here)
  - No "nonce" claim. The planned code-exchange flow must bind one
    (NF-3). This verifier deliberately has no optional nonce parameter,
    because a check that is silently skipped when the parameter is
    omitted is a latent weakening. The nonce check is added together
    with the claim.
  - The issuer silently defaults to rpOrigin. NovaForge deployments
    must set IDENTITY_ISSUER explicitly (a joint requirement). This
    verifier only accepts the issuer it was configured with.
  - NovaForge's reference verifier has no jti replay cache. This one
    does (InMemoryReplayGuard), within one process.
  - No liveness check beyond the 300 s assertion lifetime (B-2). A
    valid assertion proves authentication at iat, not that the session
    is live now.
  - The only current issuance endpoint needs the user's NovaForge
    access token, which Pashupatastra cannot hold (B-1). Until NF-1..3
    exist, no real assertion can reach this verifier.

REJECTIONS
  Every failure raises AssertionVerificationError carrying one
  AssertionRejection reason. The message is the reason code only: it
  never includes token segments, claim values, key material or the
  underlying library error, and the exception carries no chained cause
  or context. Any unexpected internal error is itself a rejection
  (fail closed). This includes the injected clock: a non-numeric,
  non-finite or non-positive reading (NaN, +-infinity, a negative or
  zero value, a non-numeric type) is CLOCK_UNAVAILABLE rather than being
  compared against - a NaN comparison is always False, so every
  temporal bound below would otherwise silently pass.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import InitVar, dataclass
from enum import Enum
from typing import Any, Callable, Dict, Mapping, Optional, Protocol, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from backend.app.identity._jose import (
    MalformedEncodingError,
    b64url_decode_canonical,
    strict_json_object,
)
from backend.app.identity.person import ExternalIdentity, PersonError
from backend.app.identity.trusted_keys import (
    ES256,
    KeySetUnavailable,
    TrustedKeyError,
    TrustedKeyProvider,
    validate_key_id,
)

ASSERTION_TYP = "nf-identity+jwt"
TOKEN_USE = "identity_assertion"
PRINCIPAL_TYPE_USER = "user"
ACCOUNT_STATUS_ACTIVE = "ACTIVE"

DEFAULT_CLOCK_TOLERANCE_SECONDS = 30
DEFAULT_MAX_LIFETIME_SECONDS = 300
DEFAULT_MAX_ASSERTION_LENGTH = 8192

MAX_CLOCK_TOLERANCE_SECONDS = 60
MAX_LIFETIME_CEILING_SECONDS = 300
MAX_ISSUER_LENGTH = 512
MAX_AUDIENCE_LENGTH = 256
MAX_CLAIM_IDENTIFIER_LENGTH = 256

_ALLOWED_HEADER_MEMBERS = frozenset({"alg", "typ", "kid"})

_REQUIRED_CLAIMS = (
    "iss",
    "aud",
    "sub",
    "jti",
    "iat",
    "exp",
    "sid",
    "token_use",
    "principal_type",
    "account_status",
    "auth_time",
)

_P256_ORDER_BYTES = 32


class AssertionRejection(str, Enum):
    """Why an assertion was refused. The only detail a rejection carries."""

    MALFORMED = "MALFORMED"
    TOO_LARGE = "TOO_LARGE"
    UNSUPPORTED_ALGORITHM = "UNSUPPORTED_ALGORITHM"
    UNSUPPORTED_HEADER = "UNSUPPORTED_HEADER"
    WRONG_TOKEN_TYPE = "WRONG_TOKEN_TYPE"
    INVALID_KEY_ID = "INVALID_KEY_ID"
    UNKNOWN_KEY = "UNKNOWN_KEY"
    KEY_SET_UNAVAILABLE = "KEY_SET_UNAVAILABLE"
    CLOCK_UNAVAILABLE = "CLOCK_UNAVAILABLE"
    INVALID_SIGNATURE = "INVALID_SIGNATURE"
    MISSING_CLAIM = "MISSING_CLAIM"
    INVALID_CLAIM = "INVALID_CLAIM"
    WRONG_ISSUER = "WRONG_ISSUER"
    WRONG_AUDIENCE = "WRONG_AUDIENCE"
    WRONG_TOKEN_USE = "WRONG_TOKEN_USE"
    INACTIVE_PRINCIPAL = "INACTIVE_PRINCIPAL"
    MALFORMED_SUBJECT = "MALFORMED_SUBJECT"
    INVALID_TEMPORAL_CLAIMS = "INVALID_TEMPORAL_CLAIMS"
    LIFETIME_TOO_LONG = "LIFETIME_TOO_LONG"
    ISSUED_IN_FUTURE = "ISSUED_IN_FUTURE"
    NOT_YET_VALID = "NOT_YET_VALID"
    EXPIRED = "EXPIRED"
    REPLAYED = "REPLAYED"
    REPLAY_GUARD_FULL = "REPLAY_GUARD_FULL"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AssertionVerificationError(Exception):
    """An identity assertion was refused. Carries a reason code only."""

    def __init__(self, reason: AssertionRejection) -> None:
        self.reason = AssertionRejection(reason)
        super().__init__(f"Identity assertion rejected: {self.reason.value}")


class AssertionVerifierConfigError(ValueError):
    """The verifier was configured with an unusable value."""


class _Rejected(Exception):
    """Internal control flow; never escapes this module."""

    def __init__(self, reason: AssertionRejection) -> None:
        self.reason = reason


def _is_bounded_token(value: object, max_length: int) -> bool:
    """A non-empty printable-ASCII string without whitespace, within bounds."""

    return (
        isinstance(value, str)
        and 0 < len(value) <= max_length
        and value.isascii()
        and all(ch.isprintable() and not ch.isspace() for ch in value)
    )


def _is_int(value: object) -> bool:
    # bool is an int subclass; True must never read as a timestamp of 1.
    return type(value) is int


def _is_valid_clock_reading(value: object) -> bool:
    """A wall-clock reading the verifier can trust: a finite, positive number.

    Every temporal check below is written as "reject if now fails this
    bound", and a comparison against NaN is always False, so a NaN (or
    infinite, or otherwise unusable) clock would silently pass every one
    of them - the verifier would fail OPEN instead of closed. This is
    checked once, explicitly, before now is used for anything.
    """

    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and value > 0


@dataclass(frozen=True)
class AssertionVerifierConfig:
    """What this deployment pins: who may issue, to whom, under which alias.

    issuer, audience and identity_provider have no defaults on purpose.
    A verifier that guesses its issuer or audience would accept tokens
    minted for someone else.
    """

    issuer: str
    audience: str
    identity_provider: str
    clock_tolerance_seconds: int = DEFAULT_CLOCK_TOLERANCE_SECONDS
    max_lifetime_seconds: int = DEFAULT_MAX_LIFETIME_SECONDS
    max_assertion_length: int = DEFAULT_MAX_ASSERTION_LENGTH

    def __post_init__(self) -> None:
        if not _is_bounded_token(self.issuer, MAX_ISSUER_LENGTH):
            raise AssertionVerifierConfigError(
                "issuer must be a non-empty printable string without whitespace."
            )
        if not _is_bounded_token(self.audience, MAX_AUDIENCE_LENGTH):
            raise AssertionVerifierConfigError(
                "audience must be a non-empty printable string without whitespace."
            )

        failure: Optional[str] = None
        try:
            ExternalIdentity(self.identity_provider, "probe")
        except PersonError as exc:
            failure = f"identity_provider is invalid: {exc}"
        if failure is not None:
            raise AssertionVerifierConfigError(failure)

        if not _is_int(self.clock_tolerance_seconds) or not (
            0 <= self.clock_tolerance_seconds <= MAX_CLOCK_TOLERANCE_SECONDS
        ):
            raise AssertionVerifierConfigError(
                f"clock_tolerance_seconds must be an int in [0, {MAX_CLOCK_TOLERANCE_SECONDS}]."
            )
        if not _is_int(self.max_lifetime_seconds) or not (
            0 < self.max_lifetime_seconds <= MAX_LIFETIME_CEILING_SECONDS
        ):
            raise AssertionVerifierConfigError(
                f"max_lifetime_seconds must be an int in (0, {MAX_LIFETIME_CEILING_SECONDS}]."
            )
        if not _is_int(self.max_assertion_length) or not (
            0 < self.max_assertion_length <= DEFAULT_MAX_ASSERTION_LENGTH
        ):
            raise AssertionVerifierConfigError(
                f"max_assertion_length must be an int in (0, {DEFAULT_MAX_ASSERTION_LENGTH}]."
            )


# Module-private: identifies the one legitimate caller of
# VerifiedIdentityAssertion's constructor (IdentityAssertionVerifier,
# below, in this same module). Never exported - it is not in __all__ and
# has no public name - so an ordinary `from ... import VerifiedIdentityAssertion`
# gives no way to satisfy the identity check in __post_init__.
_CONSTRUCTION_TOKEN = object()


@dataclass(frozen=True)
class VerifiedIdentityAssertion:
    """Verified identity facts, and nothing else.

    It holds no railway role, no railway scope, no authorization
    decision, no NovaForge role and no trust level. It is not an Actor:
    turning it into one requires the directory and RoleAssignment, which
    are later slices.

    CONSTRUCTION BOUNDARY. This type represents a cryptographically
    verified fact, so ordinary construction is refused. The trailing
    `_construction_token` parameter is a dataclasses InitVar: it must
    match the module-private sentinel above, but - unlike a regular
    field - it is never stored as an attribute, never appears in
    dataclasses.fields(), and plays no part in repr, equality or
    hashing. Only IdentityAssertionVerifier holds the sentinel. Every
    other field is independently validated in __post_init__ too, so
    holding the token still cannot produce a malformed result.
    """

    identity: ExternalIdentity
    issuer: str
    audience: str
    session_id: str
    assertion_id: str
    key_id: str
    issued_at: int
    expires_at: int
    auth_time: int
    _construction_token: InitVar[object] = None

    def __post_init__(self, _construction_token: object) -> None:
        if _construction_token is not _CONSTRUCTION_TOKEN:
            raise TypeError(
                "VerifiedIdentityAssertion cannot be constructed directly; "
                "only IdentityAssertionVerifier.verify() produces one."
            )
        if not isinstance(self.identity, ExternalIdentity):
            raise TypeError("identity must be an ExternalIdentity.")
        for name in ("issuer", "audience", "session_id", "assertion_id", "key_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise TypeError(f"{name} must be a non-empty string.")
        for name in ("issued_at", "expires_at", "auth_time"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise TypeError(f"{name} must be a positive int.")
        if self.expires_at <= self.issued_at:
            raise TypeError("expires_at must be after issued_at.")


class ReplayGuard(Protocol):
    """Remembers assertion ids until they could no longer verify anyway."""

    def check_and_record(self, key: Tuple[str, str], retain_until: float, now: float) -> None:
        """Record key, or raise ReplayDetected / ReplayGuardFull."""
        ...


class ReplayDetected(Exception):
    """The assertion id was already accepted and is still retained."""


class ReplayGuardFull(Exception):
    """The guard cannot record another id without forgetting a live one."""


DEFAULT_REPLAY_GUARD_CAPACITY = 100_000


class InMemoryReplayGuard:
    """A bounded, thread-safe, per-process set of accepted (issuer, jti) pairs.

    An entry is kept until its assertion could no longer pass the expiry
    check (exp + tolerance), then purged. When full of live entries it
    refuses new ones rather than evicting: evicting a live id would
    re-open exactly the replay it exists to stop.

    Scope: one process. A multi-process deployment needs a shared store
    behind the same ReplayGuard protocol (later slice).
    """

    def __init__(self, capacity: int = DEFAULT_REPLAY_GUARD_CAPACITY) -> None:
        if not _is_int(capacity) or capacity <= 0:
            raise ValueError("capacity must be a positive int.")
        self._capacity = capacity
        self._entries: Dict[Tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def check_and_record(self, key: Tuple[str, str], retain_until: float, now: float) -> None:
        with self._lock:
            expired = [k for k, until in self._entries.items() if until <= now]
            for k in expired:
                del self._entries[k]

            if key in self._entries:
                raise ReplayDetected()
            if len(self._entries) >= self._capacity:
                raise ReplayGuardFull()
            self._entries[key] = retain_until


class IdentityAssertionVerifier:
    """Verifies NovaForge identity assertions against pinned configuration.

    config        - pinned issuer, audience and provider alias
    keys          - the trusted-key seam (StaticKeyProvider,
                    CachingJwksProvider, ...)
    replay_guard  - required. Every accepted jti is recorded.
    clock         - wall-clock seconds since the epoch (exp and iat are
                    epoch seconds). Injected for tests.
    """

    def __init__(
        self,
        config: AssertionVerifierConfig,
        keys: TrustedKeyProvider,
        replay_guard: ReplayGuard,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(config, AssertionVerifierConfig):
            raise AssertionVerifierConfigError("config must be an AssertionVerifierConfig.")
        if keys is None or not callable(getattr(keys, "get_key", None)):
            raise AssertionVerifierConfigError("keys must provide get_key(kid).")
        if replay_guard is None or not callable(getattr(replay_guard, "check_and_record", None)):
            raise AssertionVerifierConfigError("replay_guard must provide check_and_record().")
        if not callable(clock):
            raise AssertionVerifierConfigError("clock must be callable.")
        self._config = config
        self._keys = keys
        self._replay_guard = replay_guard
        self._clock = clock

    @property
    def config(self) -> AssertionVerifierConfig:
        return self._config

    def verify(self, assertion: object) -> VerifiedIdentityAssertion:
        """Return verified identity facts, or raise AssertionVerificationError."""

        reason: AssertionRejection
        try:
            return self._verify(assertion)
        except _Rejected as rejected:
            reason = rejected.reason
        except Exception:  # noqa: BLE001 - anything unexpected fails closed
            reason = AssertionRejection.INTERNAL_ERROR
        # Raised outside the except blocks so the error carries no
        # __context__ or __cause__ that could expose library detail.
        raise AssertionVerificationError(reason)

    # -- steps ---------------------------------------------------------

    def _verify(self, assertion: object) -> VerifiedIdentityAssertion:
        cfg = self._config

        if not isinstance(assertion, str):
            raise _Rejected(AssertionRejection.MALFORMED)
        if len(assertion) > cfg.max_assertion_length:
            raise _Rejected(AssertionRejection.TOO_LARGE)

        segments = assertion.split(".")
        if len(segments) != 3 or not all(segments):
            raise _Rejected(AssertionRejection.MALFORMED)
        header_b64, payload_b64, signature_b64 = segments

        header = self._decode_object(header_b64)
        kid = self._check_header(header)

        key = self._resolve_key(kid)
        self._check_signature(
            key.public_key,
            f"{header_b64}.{payload_b64}".encode("ascii"),
            signature_b64,
        )

        # Nothing in the payload is looked at before the signature holds.
        claims = self._decode_object(payload_b64)
        return self._check_claims(claims, kid)

    @staticmethod
    def _decode_object(segment: str) -> Dict[str, Any]:
        try:
            return strict_json_object(b64url_decode_canonical(segment))
        except MalformedEncodingError:
            raise _Rejected(AssertionRejection.MALFORMED)

    @staticmethod
    def _check_header(header: Mapping[str, Any]) -> str:
        if header.get("alg") != ES256:
            # Covers "none", HS256 key-confusion, RS*/PS*, ES384, ...
            raise _Rejected(AssertionRejection.UNSUPPORTED_ALGORITHM)
        if set(header) - _ALLOWED_HEADER_MEMBERS:
            # crit, jwk, jku, x5u, x5c, b64, ... are never honoured.
            raise _Rejected(AssertionRejection.UNSUPPORTED_HEADER)
        if header.get("typ") != ASSERTION_TYP:
            raise _Rejected(AssertionRejection.WRONG_TOKEN_TYPE)
        try:
            return validate_key_id(header.get("kid"))
        except TrustedKeyError:
            raise _Rejected(AssertionRejection.INVALID_KEY_ID)

    def _resolve_key(self, kid: str):
        try:
            key = self._keys.get_key(kid)
        except KeySetUnavailable:
            raise _Rejected(AssertionRejection.KEY_SET_UNAVAILABLE)
        if key is None:
            raise _Rejected(AssertionRejection.UNKNOWN_KEY)
        if not isinstance(key.public_key, ec.EllipticCurvePublicKey) or not isinstance(
            key.public_key.curve, ec.SECP256R1
        ):
            raise _Rejected(AssertionRejection.UNKNOWN_KEY)
        return key

    @staticmethod
    def _check_signature(
        public_key: ec.EllipticCurvePublicKey, signing_input: bytes, signature_b64: str
    ) -> None:
        try:
            raw = b64url_decode_canonical(signature_b64)
        except MalformedEncodingError:
            raise _Rejected(AssertionRejection.INVALID_SIGNATURE)
        if len(raw) != 2 * _P256_ORDER_BYTES:
            raise _Rejected(AssertionRejection.INVALID_SIGNATURE)

        r = int.from_bytes(raw[:_P256_ORDER_BYTES], "big")
        s = int.from_bytes(raw[_P256_ORDER_BYTES:], "big")
        # JWS ES256 (RFC 7518 section 3.4) is raw r||s; cryptography takes DER.
        try:
            public_key.verify(encode_dss_signature(r, s), signing_input, ec.ECDSA(hashes.SHA256()))
        except (InvalidSignature, ValueError):
            raise _Rejected(AssertionRejection.INVALID_SIGNATURE)

    def _check_claims(self, claims: Mapping[str, Any], kid: str) -> VerifiedIdentityAssertion:
        cfg = self._config

        for name in _REQUIRED_CLAIMS:
            if name not in claims:
                raise _Rejected(AssertionRejection.MISSING_CLAIM)

        iss = claims["iss"]
        if not isinstance(iss, str) or iss != cfg.issuer:
            raise _Rejected(AssertionRejection.WRONG_ISSUER)

        aud = claims["aud"]
        if not isinstance(aud, str) or aud != cfg.audience:
            raise _Rejected(AssertionRejection.WRONG_AUDIENCE)

        if claims["token_use"] != TOKEN_USE:
            raise _Rejected(AssertionRejection.WRONG_TOKEN_USE)

        if (
            claims["principal_type"] != PRINCIPAL_TYPE_USER
            or claims["account_status"] != ACCOUNT_STATUS_ACTIVE
        ):
            raise _Rejected(AssertionRejection.INACTIVE_PRINCIPAL)

        iat, exp, auth_time = claims["iat"], claims["exp"], claims["auth_time"]
        nbf = claims.get("nbf")
        if not (_is_int(iat) and _is_int(exp) and _is_int(auth_time)):
            raise _Rejected(AssertionRejection.INVALID_TEMPORAL_CLAIMS)
        if "nbf" in claims and not _is_int(nbf):
            raise _Rejected(AssertionRejection.INVALID_TEMPORAL_CLAIMS)
        tolerance = cfg.clock_tolerance_seconds
        # auth_time is Session.createdAt, filled by the database clock
        # (DEFAULT CURRENT_TIMESTAMP); iat is the Node process clock. They
        # may disagree by a little, so the same tolerance applies.
        if iat <= 0 or auth_time <= 0 or exp <= iat or auth_time > iat + tolerance:
            raise _Rejected(AssertionRejection.INVALID_TEMPORAL_CLAIMS)
        if nbf is not None and not (iat <= nbf < exp):
            raise _Rejected(AssertionRejection.INVALID_TEMPORAL_CLAIMS)
        if exp - iat > cfg.max_lifetime_seconds:
            raise _Rejected(AssertionRejection.LIFETIME_TOO_LONG)

        now = self._clock()
        if not _is_valid_clock_reading(now):
            raise _Rejected(AssertionRejection.CLOCK_UNAVAILABLE)
        if iat > now + tolerance:
            raise _Rejected(AssertionRejection.ISSUED_IN_FUTURE)
        if nbf is not None and nbf > now + tolerance:
            raise _Rejected(AssertionRejection.NOT_YET_VALID)
        if now >= exp + tolerance:
            raise _Rejected(AssertionRejection.EXPIRED)

        sub = claims["sub"]
        if not _is_bounded_token(sub, MAX_CLAIM_IDENTIFIER_LENGTH):
            raise _Rejected(AssertionRejection.MALFORMED_SUBJECT)
        try:
            identity = ExternalIdentity(cfg.identity_provider, sub)
        except PersonError:
            raise _Rejected(AssertionRejection.MALFORMED_SUBJECT)

        sid, jti = claims["sid"], claims["jti"]
        if not _is_bounded_token(sid, MAX_CLAIM_IDENTIFIER_LENGTH):
            raise _Rejected(AssertionRejection.INVALID_CLAIM)
        if not _is_bounded_token(jti, MAX_CLAIM_IDENTIFIER_LENGTH):
            raise _Rejected(AssertionRejection.INVALID_CLAIM)

        # Last: only an otherwise-valid assertion consumes its jti.
        try:
            self._replay_guard.check_and_record((iss, jti), exp + tolerance, now)
        except ReplayDetected:
            raise _Rejected(AssertionRejection.REPLAYED)
        except ReplayGuardFull:
            raise _Rejected(AssertionRejection.REPLAY_GUARD_FULL)

        return VerifiedIdentityAssertion(
            identity=identity,
            issuer=iss,
            audience=aud,
            session_id=sid,
            assertion_id=jti,
            key_id=kid,
            issued_at=iat,
            expires_at=exp,
            auth_time=auth_time,
            _construction_token=_CONSTRUCTION_TOKEN,
        )


__all__ = [
    "ACCOUNT_STATUS_ACTIVE",
    "ASSERTION_TYP",
    "AssertionRejection",
    "AssertionVerificationError",
    "AssertionVerifierConfig",
    "AssertionVerifierConfigError",
    "DEFAULT_CLOCK_TOLERANCE_SECONDS",
    "DEFAULT_MAX_ASSERTION_LENGTH",
    "DEFAULT_MAX_LIFETIME_SECONDS",
    "IdentityAssertionVerifier",
    "InMemoryReplayGuard",
    "PRINCIPAL_TYPE_USER",
    "ReplayDetected",
    "ReplayGuard",
    "ReplayGuardFull",
    "TOKEN_USE",
    "VerifiedIdentityAssertion",
]
