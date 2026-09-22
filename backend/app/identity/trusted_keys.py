"""Trusted signing keys for identity assertions (Slice 10.2a).

This module answers one question for the assertion verifier
(backend.app.identity.identity_assertion): "which public key, if any,
does Pashupatastra trust under this key id?" It holds PUBLIC keys only,
never a private key or shared secret, so nothing here can mint an
identity.

WHAT NOVAFORGE PUBLISHES (server/src/lib/identityKeys.ts @ 5f3a2a6)
    GET /.well-known/jwks.json (and /api/.well-known/jwks.json),
    Cache-Control: public, max-age=300. The body is
        {"keys": [{"kty": "EC", "crv": "P-256", "x": ..., "y": ...,
                   "kid": ..., "alg": "ES256", "use": "sig"}, ...]}
    It lists the current signing key first, then every key in
    IDENTITY_PREVIOUS_PUBLIC_KEYS that is still accepted during a
    rotation. The current kid comes from IDENTITY_SIGNING_KEY_ID in a
    configured deployment. A development process with no configured key
    generates "dev-ephemeral-<uuid>" at start-up, so the kid changes on
    every NovaForge restart.

STRICT PARSING
    parse_jwks refuses the WHOLE document, rather than skipping an
    entry, when any entry:
      - is not an EC P-256 public key, or its point is not on the curve;
      - carries private material ("d");
      - has a missing, empty or duplicate kid;
      - declares an alg other than ES256 or a use other than "sig".
    Skipping would let a malformed or hostile document silently shrink
    or reshape the trusted set. NovaForge publishes nothing else, so
    refusing costs nothing against the current contract.

THE PROVIDER SEAM
    The verifier depends on TrustedKeyProvider, not on HTTP. Two
    implementations:
      StaticKeyProvider      - a fixed, already-parsed key set.
      CachingJwksProvider    - wraps an injected fetch() (no HTTP client
                               lives here) and an injected clock.
    CachingJwksProvider behaves as follows:
      - It fetches on first use and whenever the cached set is older
        than max_age_seconds.
      - A successful fetch REPLACES the set wholesale, so a kid that
        NovaForge dropped after a rotation stops being trusted.
      - An unknown kid triggers at most one refetch per
        min_refetch_interval_seconds, which covers a rotation that
        happened inside the cache window. This is a rate limit, so an
        attacker cycling random kids cannot turn the verifier into a
        request amplifier.
      - When a fetch or parse fails and the cached set is stale, or
        there is no cached set, it fails closed with KeySetUnavailable.
        A failed refresh never extends the life of a stale set.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Dict, Mapping, Optional, Protocol, Union

from cryptography.hazmat.primitives.asymmetric import ec

from backend.app.identity._jose import (
    MalformedEncodingError,
    b64url_decode_canonical,
    strict_json_object,
)

ES256 = "ES256"

MAX_KEY_ID_LENGTH = 128
MAX_JWKS_KEYS = 32
MAX_JWKS_DOCUMENT_BYTES = 64 * 1024

_P256_COORDINATE_BYTES = 32

_ALLOWED_JWK_MEMBERS = frozenset({"kty", "crv", "x", "y", "kid", "alg", "use", "key_ops"})


class TrustedKeyError(ValueError):
    """A key set could not be parsed into trusted verification keys.

    The message names the rule that failed, never the key material.
    """


class KeySetUnavailable(RuntimeError):
    """No fresh trusted key set is available; verification must fail closed."""


@dataclass(frozen=True)
class TrustedKey:
    """One trusted ES256 verification key, identified by its kid."""

    kid: str
    public_key: ec.EllipticCurvePublicKey

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"TrustedKey(kid={self.kid!r})"


def validate_key_id(kid: object) -> str:
    """Return kid unchanged if it is an acceptable key id, else raise."""

    if not isinstance(kid, str) or not kid:
        raise TrustedKeyError("kid must be a non-empty string.")
    if len(kid) > MAX_KEY_ID_LENGTH:
        raise TrustedKeyError(f"kid exceeds {MAX_KEY_ID_LENGTH} characters.")
    if any(not ch.isprintable() or ch.isspace() for ch in kid) or not kid.isascii():
        raise TrustedKeyError("kid must be printable ASCII without whitespace.")
    return kid


def _coordinate(jwk: Mapping[str, object], name: str) -> int:
    value = jwk.get(name)
    if not isinstance(value, str):
        raise TrustedKeyError(f"JWK member {name!r} must be a base64url string.")
    raw: Optional[bytes] = None
    try:
        raw = b64url_decode_canonical(value)
    except MalformedEncodingError:
        pass
    if raw is None:
        raise TrustedKeyError(f"JWK member {name!r} is not canonical base64url.")
    if len(raw) != _P256_COORDINATE_BYTES:
        raise TrustedKeyError(f"JWK member {name!r} is not a P-256 coordinate.")
    return int.from_bytes(raw, "big")


def parse_jwk(jwk: object) -> TrustedKey:
    """Parse one public EC P-256 JWK into a TrustedKey, or raise."""

    if not isinstance(jwk, dict):
        raise TrustedKeyError("Each JWK must be a JSON object.")

    if "d" in jwk:
        raise TrustedKeyError("JWK carries private key material; refused.")

    unknown = set(jwk) - _ALLOWED_JWK_MEMBERS
    if unknown:
        raise TrustedKeyError("JWK carries unsupported members.")

    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        raise TrustedKeyError("Only EC P-256 keys are trusted.")

    if "alg" in jwk and jwk["alg"] != ES256:
        raise TrustedKeyError("JWK alg must be ES256 when present.")

    if "use" in jwk and jwk["use"] != "sig":
        raise TrustedKeyError("JWK use must be 'sig' when present.")

    if "key_ops" in jwk:
        ops = jwk["key_ops"]
        if not isinstance(ops, list) or ops != ["verify"]:
            raise TrustedKeyError("JWK key_ops must be exactly ['verify'] when present.")

    kid = validate_key_id(jwk.get("kid"))

    x = _coordinate(jwk, "x")
    y = _coordinate(jwk, "y")

    failure: Optional[str] = None
    try:
        public_key = ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
    except ValueError:
        failure = "JWK point is not on the P-256 curve."
    if failure is not None:
        raise TrustedKeyError(failure)

    return TrustedKey(kid=kid, public_key=public_key)


JwksDocument = Union[bytes, str, Mapping[str, object]]


def parse_jwks(document: JwksDocument) -> Dict[str, TrustedKey]:
    """Parse a JWKS document into {kid: TrustedKey}, refusing it whole on any fault."""

    if isinstance(document, (bytes, str)):
        raw = document.encode("utf-8") if isinstance(document, str) else document
        if len(raw) > MAX_JWKS_DOCUMENT_BYTES:
            raise TrustedKeyError("JWKS document is too large.")
        parsed: object = None
        try:
            parsed = strict_json_object(raw)
        except MalformedEncodingError:
            pass
        if parsed is None:
            raise TrustedKeyError("JWKS document is not a strict JSON object.")
    else:
        parsed = document

    if not isinstance(parsed, Mapping):
        raise TrustedKeyError("JWKS document must be a JSON object.")

    keys = parsed.get("keys")
    if not isinstance(keys, list) or not keys:
        raise TrustedKeyError("JWKS document must hold a non-empty 'keys' array.")
    if len(keys) > MAX_JWKS_KEYS:
        raise TrustedKeyError(f"JWKS document holds more than {MAX_JWKS_KEYS} keys.")

    result: Dict[str, TrustedKey] = {}
    for entry in keys:
        key = parse_jwk(entry)
        if key.kid in result:
            raise TrustedKeyError("JWKS document repeats a kid.")
        result[key.kid] = key

    return result


class TrustedKeyProvider(Protocol):
    """Resolves a kid to a trusted key.

    Returns None for a kid that is not trusted. Raises KeySetUnavailable
    when it cannot say which keys are trusted right now.
    """

    def get_key(self, kid: str) -> Optional[TrustedKey]:
        ...


class StaticKeyProvider:
    """A fixed trusted key set, parsed once at construction."""

    def __init__(self, jwks: JwksDocument) -> None:
        self._keys = parse_jwks(jwks)

    @property
    def key_ids(self) -> frozenset:
        return frozenset(self._keys)

    def get_key(self, kid: str) -> Optional[TrustedKey]:
        return self._keys.get(kid)


DEFAULT_JWKS_MAX_AGE_SECONDS = 300
DEFAULT_MIN_REFETCH_INTERVAL_SECONDS = 60


class CachingJwksProvider:
    """A trusted key set refreshed through an injected fetch(), fail-closed.

    fetch returns the raw JWKS document (bytes, str or an already-decoded
    mapping). It is the only place transport lives. Any exception it
    raises counts as a failed refresh. clock returns seconds as a float,
    monotonic or wall-clock, as long as it is consistent.
    """

    def __init__(
        self,
        fetch: Callable[[], JwksDocument],
        clock: Callable[[], float],
        *,
        max_age_seconds: float = DEFAULT_JWKS_MAX_AGE_SECONDS,
        min_refetch_interval_seconds: float = DEFAULT_MIN_REFETCH_INTERVAL_SECONDS,
    ) -> None:
        if not callable(fetch) or not callable(clock):
            raise TypeError("fetch and clock must be callables.")
        if not 0 < max_age_seconds <= 3600:
            raise ValueError("max_age_seconds must be in (0, 3600].")
        if not 0 < min_refetch_interval_seconds <= max_age_seconds:
            raise ValueError("min_refetch_interval_seconds must be in (0, max_age_seconds].")

        self._fetch = fetch
        self._clock = clock
        self._max_age = max_age_seconds
        self._min_refetch = min_refetch_interval_seconds
        self._lock = threading.Lock()
        self._keys: Optional[Dict[str, TrustedKey]] = None
        self._fetched_at: Optional[float] = None
        self._last_attempt_at: Optional[float] = None

    def _is_fresh(self, now: float) -> bool:
        return (
            self._keys is not None
            and self._fetched_at is not None
            and now - self._fetched_at <= self._max_age
        )

    def _may_attempt(self, now: float) -> bool:
        return self._last_attempt_at is None or now - self._last_attempt_at >= self._min_refetch

    def _refresh(self, now: float) -> None:
        """Try one fetch. On success replace the set; on failure keep state."""

        self._last_attempt_at = now
        try:
            keys = parse_jwks(self._fetch())
        except Exception:  # noqa: BLE001 - any transport/parse fault is a failed refresh
            return
        self._keys = keys
        self._fetched_at = now

    def get_key(self, kid: str) -> Optional[TrustedKey]:
        with self._lock:
            now = self._clock()

            if not self._is_fresh(now) and self._may_attempt(now):
                self._refresh(now)

            if not self._is_fresh(now):
                raise KeySetUnavailable("No fresh trusted key set is available.")

            assert self._keys is not None
            key = self._keys.get(kid)
            if key is not None:
                return key

            if self._may_attempt(now):
                self._refresh(now)
                if self._is_fresh(now):
                    return self._keys.get(kid)

            return None


__all__ = [
    "CachingJwksProvider",
    "DEFAULT_JWKS_MAX_AGE_SECONDS",
    "DEFAULT_MIN_REFETCH_INTERVAL_SECONDS",
    "ES256",
    "KeySetUnavailable",
    "MAX_KEY_ID_LENGTH",
    "StaticKeyProvider",
    "TrustedKey",
    "TrustedKeyError",
    "TrustedKeyProvider",
    "parse_jwk",
    "parse_jwks",
    "validate_key_id",
]
