"""Slice 10.2a: NovaForge identity assertion verifier and trusted-key seam.

All key material is generated at test time. The one fixed token below
was minted by NovaForge's own jsonwebtoken 9.0.3 with a throwaway key
whose private half was discarded; only its public JWK is kept.
"""

from __future__ import annotations

import ast
import base64
import dataclasses
import hashlib
import hmac
import inspect
import json
import math
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from backend.app.identity import identity_assertion as assertion_module
from backend.app.identity import trusted_keys as keys_module
from backend.app.identity.identity_assertion import (
    AssertionRejection,
    AssertionVerificationError,
    AssertionVerifierConfig,
    AssertionVerifierConfigError,
    IdentityAssertionVerifier,
    InMemoryReplayGuard,
    ReplayDetected,
    ReplayGuardFull,
    VerifiedIdentityAssertion,
)
from backend.app.identity.person import ExternalIdentity
from backend.app.identity.trusted_keys import (
    CachingJwksProvider,
    KeySetUnavailable,
    StaticKeyProvider,
    TrustedKeyError,
    parse_jwks,
)

ISSUER = "https://novaforge.test"
AUDIENCE = "pashupatastra"
ALIAS = "novaforge"
NOW = 1_800_000_000
TOL = 30
P256_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551

R = AssertionRejection


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def unb64(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def enc_json(obj: Any) -> str:
    return b64(json.dumps(obj, separators=(",", ":")).encode("utf-8"))


class SigningKey:
    def __init__(self, kid: Optional[str] = None) -> None:
        self.kid = kid or f"kid-{uuid.uuid4()}"
        self.private = ec.generate_private_key(ec.SECP256R1())

    @property
    def public_jwk(self) -> Dict[str, str]:
        numbers = self.private.public_key().public_numbers()
        return {
            "kty": "EC",
            "crv": "P-256",
            "x": b64(numbers.x.to_bytes(32, "big")),
            "y": b64(numbers.y.to_bytes(32, "big")),
            "kid": self.kid,
            "alg": "ES256",
            "use": "sig",
        }

    def public_pem(self) -> bytes:
        return self.private.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )

    def sign_raw(self, signing_input: bytes) -> bytes:
        der = self.private.sign(signing_input, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def jwks(*keys: SigningKey) -> Dict[str, Any]:
    return {"keys": [k.public_jwk for k in keys]}


def base_claims(**overrides: Any) -> Dict[str, Any]:
    claims: Dict[str, Any] = {
        "token_use": "identity_assertion",
        "principal_type": "user",
        "sid": str(uuid.uuid4()),
        "role": "SUPER_ADMIN",
        "account_status": "ACTIVE",
        "auth_time": NOW - 120,
        "trust_level": 97,
        "iat": NOW - 10,
        "exp": NOW - 10 + 300,
        "aud": AUDIENCE,
        "iss": ISSUER,
        "sub": str(uuid.uuid4()),
        "jti": str(uuid.uuid4()),
    }
    claims.update(overrides)
    return claims


def mint(
    key: SigningKey,
    claims: Optional[Dict[str, Any]] = None,
    header: Optional[Dict[str, Any]] = None,
    drop_claims: Iterable[str] = (),
    drop_header: Iterable[str] = (),
    raw_payload: Optional[bytes] = None,
    raw_header: Optional[bytes] = None,
) -> str:
    hdr: Dict[str, Any] = {"alg": "ES256", "typ": "nf-identity+jwt", "kid": key.kid}
    hdr.update(header or {})
    for name in drop_header:
        hdr.pop(name, None)
    body = dict(claims if claims is not None else base_claims())
    for name in drop_claims:
        body.pop(name, None)
    h = b64(raw_header) if raw_header is not None else enc_json(hdr)
    p = b64(raw_payload) if raw_payload is not None else enc_json(body)
    signing_input = f"{h}.{p}".encode("ascii")
    return f"{h}.{p}.{b64(key.sign_raw(signing_input))}"


class FixedClock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def config(**overrides: Any) -> AssertionVerifierConfig:
    values: Dict[str, Any] = {"issuer": ISSUER, "audience": AUDIENCE, "identity_provider": ALIAS}
    values.update(overrides)
    return AssertionVerifierConfig(**values)


def verifier(provider, clock: Optional[FixedClock] = None, guard=None, **cfg: Any):
    return IdentityAssertionVerifier(
        config(**cfg),
        provider,
        guard if guard is not None else InMemoryReplayGuard(),
        clock or FixedClock(),
    )


def rejection(v: IdentityAssertionVerifier, token: Any) -> AssertionRejection:
    with pytest.raises(AssertionVerificationError) as info:
        v.verify(token)
    return info.value.reason


@pytest.fixture()
def key() -> SigningKey:
    return SigningKey("key-current")


@pytest.fixture()
def v(key: SigningKey) -> IdentityAssertionVerifier:
    return verifier(StaticKeyProvider(jwks(key)))


# ----------------------------------------------------------------------
# A. Positive
# ----------------------------------------------------------------------


def test_valid_assertion_yields_verified_identity(key, v):
    claims = base_claims()
    result = v.verify(mint(key, claims))

    assert isinstance(result, VerifiedIdentityAssertion)
    assert result.identity == ExternalIdentity(ALIAS, claims["sub"])
    assert result.issuer == ISSUER
    assert result.audience == AUDIENCE
    assert result.session_id == claims["sid"]
    assert result.assertion_id == claims["jti"]
    assert result.key_id == key.kid
    assert result.issued_at == claims["iat"]
    assert result.expires_at == claims["exp"]
    assert result.auth_time == claims["auth_time"]


# NOVAFORGE_MINTED (end of file) was minted by
# D:/NovaForge/server/node_modules/jsonwebtoken (9.0.3) with the exact
# options of server/src/lib/identityAssertion.ts issueIdentityAssertion,
# iss "https://novaforge.test", aud "pashupatastra". Public key only; the
# private key was discarded when the minting process exited.


def test_assertion_minted_by_novaforge_library_verifies():
    token = NOVAFORGE_MINTED["assertion"]
    v = verifier(StaticKeyProvider(NOVAFORGE_MINTED["jwks"]), FixedClock(NOVAFORGE_MINTED_IAT + 10))

    result = v.verify(token)

    assert result.identity.identity_provider == ALIAS
    assert result.identity.subject == NOVAFORGE_MINTED_SUB
    assert result.key_id.startswith("dev-ephemeral-")
    assert result.expires_at - result.issued_at == 300


def test_result_contains_only_identity_facts():
    assert [f.name for f in dataclasses.fields(VerifiedIdentityAssertion)] == [
        "identity",
        "issuer",
        "audience",
        "session_id",
        "assertion_id",
        "key_id",
        "issued_at",
        "expires_at",
        "auth_time",
    ]
    forbidden = {"role", "roles", "scope", "scopes", "railway_role", "person_id", "trust_level",
                 "authorized", "decision", "actor", "assurance", "account_status"}
    assert not forbidden & {f.name for f in dataclasses.fields(VerifiedIdentityAssertion)}


def test_result_is_frozen(key, v):
    result = v.verify(mint(key))
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.identity = ExternalIdentity(ALIAS, "someone-else")  # type: ignore[misc]


# ----------------------------------------------------------------------
# A2. Construction boundary (B3: VerifiedIdentityAssertion cannot be forged)
# ----------------------------------------------------------------------


def _identity_kwargs(**overrides: Any) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = dict(
        identity=ExternalIdentity(ALIAS, "u"),
        issuer=ISSUER,
        audience=AUDIENCE,
        session_id="s",
        assertion_id="j",
        key_id="k",
        issued_at=1,
        expires_at=2,
        auth_time=1,
    )
    kwargs.update(overrides)
    return kwargs


def test_direct_construction_without_token_fails():
    # This is the "ordinary import" path: VerifiedIdentityAssertion is
    # imported at module scope like any other public name, and called
    # exactly as a normal dataclass would be.
    with pytest.raises(TypeError):
        VerifiedIdentityAssertion(**_identity_kwargs())


def test_direct_construction_with_wrong_token_fails():
    for guess in (object(), None, 0, "", "_CONSTRUCTION_TOKEN", assertion_module):
        with pytest.raises(TypeError):
            VerifiedIdentityAssertion(**_identity_kwargs(_construction_token=guess))


def test_the_verifiers_own_token_is_not_reachable_through_the_public_module_surface():
    # __all__ is the "ordinary import" surface (what `from module import *`
    # or a documented import would expose); the sentinel is not on it.
    assert "_CONSTRUCTION_TOKEN" not in assertion_module.__all__


def test_verifier_still_constructs_a_valid_result_through_the_boundary(key, v):
    result = v.verify(mint(key))
    assert isinstance(result, VerifiedIdentityAssertion)


@pytest.mark.parametrize(
    "overrides",
    [
        {"identity": "not-an-identity"},
        {"identity": None},
        {"issuer": ""},
        {"issuer": 1},
        {"issuer": None},
        {"audience": ""},
        {"session_id": ""},
        {"assertion_id": ""},
        {"key_id": ""},
        {"issued_at": "1"},
        {"issued_at": 1.0},
        {"issued_at": True},
        {"issued_at": 0},
        {"issued_at": -1},
        {"expires_at": "2"},
        {"expires_at": 0},
        {"auth_time": None},
        {"auth_time": False},
        {"issued_at": 5, "expires_at": 5},  # exp == iat
        {"issued_at": 5, "expires_at": 4},  # exp < iat
    ],
)
def test_invalid_fields_cannot_produce_a_verified_result_even_with_the_real_token(overrides):
    # Using the module's own sentinel proves this is not merely "the
    # token is secret" - a value object must still refuse to represent
    # garbage, in case the verifier itself ever has a bug.
    kwargs = _identity_kwargs(**overrides)
    kwargs["_construction_token"] = assertion_module._CONSTRUCTION_TOKEN
    with pytest.raises(TypeError):
        VerifiedIdentityAssertion(**kwargs)


def test_valid_fields_with_the_real_token_succeed():
    kwargs = _identity_kwargs()
    kwargs["_construction_token"] = assertion_module._CONSTRUCTION_TOKEN
    result = VerifiedIdentityAssertion(**kwargs)
    assert result.issuer == ISSUER


def test_construction_token_is_not_a_stored_field_or_visible_anywhere():
    kwargs = _identity_kwargs()
    kwargs["_construction_token"] = assertion_module._CONSTRUCTION_TOKEN
    result = VerifiedIdentityAssertion(**kwargs)

    # InitVar leaves its class-level *declared default* (None) visible
    # through ordinary attribute lookup - that is just Python's normal
    # class-attribute fallback - but the actual per-call value passed to
    # __init__ is never stored anywhere, on the class or the instance.
    assert result._construction_token is None
    assert result._construction_token is not assertion_module._CONSTRUCTION_TOKEN
    assert "_construction_token" not in {f.name for f in dataclasses.fields(result)}
    assert "_construction_token" not in repr(result)
    assert "_construction_token" not in dataclasses.asdict(result)

    # Confirm the readable-back value is not itself a working token.
    kwargs["_construction_token"] = result._construction_token
    with pytest.raises(TypeError):
        VerifiedIdentityAssertion(**kwargs)


def _constructs_verified_identity_assertion(source: str) -> bool:
    """True if source contains a call whose callee resolves to the name
    VerifiedIdentityAssertion, however it was imported (bare name or
    qualified through a module/alias attribute)."""

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name == "VerifiedIdentityAssertion":
            return True
    return False


def test_scanner_detects_the_pattern_it_guards_against():
    assert _constructs_verified_identity_assertion(
        "from backend.app.identity.identity_assertion import VerifiedIdentityAssertion\n"
        "x = VerifiedIdentityAssertion(identity=None)\n"
    )
    assert _constructs_verified_identity_assertion(
        "from backend.app.identity import identity_assertion as m\n"
        "x = m.VerifiedIdentityAssertion(identity=None)\n"
    )
    assert not _constructs_verified_identity_assertion(
        "from backend.app.identity.identity_assertion import VerifiedIdentityAssertion\n"
        "print(VerifiedIdentityAssertion)\n"
    )


def test_no_production_module_constructs_verified_identity_assertion_directly():
    root = Path(__file__).resolve().parents[1] / "app"
    identity_assertion_file = root / "identity" / "identity_assertion.py"

    offenders = []
    for path in root.rglob("*.py"):
        if path == identity_assertion_file:
            continue
        if _constructs_verified_identity_assertion(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(root)))

    assert offenders == []


def test_smuggled_authority_claims_never_reach_the_result(key, v):
    claims = base_claims(
        person_id="WORKER-042",
        railway_role="AUTHORITY",
        scope=["SEC-1"],
        identity_provider="attacker-idp",
        actor_id="ADMIN-1",
        role="SUPER_ADMIN",
    )
    result = v.verify(mint(key, claims))

    dumped = json.dumps(dataclasses.asdict(result))
    for value in ("WORKER-042", "AUTHORITY", "SEC-1", "attacker-idp", "ADMIN-1", "SUPER_ADMIN"):
        assert value not in dumped
    assert result.identity.identity_provider == ALIAS


def test_subject_is_kept_verbatim_and_case_sensitive(key, v):
    result = v.verify(mint(key, base_claims(sub="AbC-123:x.Y_z")))
    assert result.identity.subject == "AbC-123:x.Y_z"


def test_nbf_when_present_and_valid_is_accepted(key, v):
    assert v.verify(mint(key, base_claims(nbf=NOW - 10)))


@pytest.mark.parametrize(
    "now",
    [
        NOW - 10 - TOL,  # iat exactly at now + tolerance
        NOW - 10 + 300 + TOL - 0.001,  # just inside exp + tolerance
    ],
)
def test_temporal_boundaries_inside_tolerance_accept(key, now):
    v = verifier(StaticKeyProvider(jwks(key)), FixedClock(now))
    assert v.verify(mint(key))


def test_auth_time_just_after_iat_within_tolerance_accepts(key, v):
    # Session.createdAt comes from the database clock, iat from Node's.
    assert v.verify(mint(key, base_claims(auth_time=NOW - 10 + TOL)))


def test_lifetime_exactly_at_maximum_accepts(key, v):
    assert v.verify(mint(key, base_claims(iat=NOW - 10, exp=NOW - 10 + 300)))


# ----------------------------------------------------------------------
# B. Signature, key and algorithm
# ----------------------------------------------------------------------


def _mutate_middle_byte(raw: bytes) -> bytes:
    mid = len(raw) // 2
    return raw[:mid] + bytes([raw[mid] ^ 0x01]) + raw[mid + 1 :]


def test_tampered_payload_is_rejected(key, v):
    h, p, s = mint(key).split(".")
    body = json.loads(unb64(p))
    body["sub"] = "someone-else"
    assert rejection(v, f"{h}.{enc_json(body)}.{s}") is R.INVALID_SIGNATURE


def test_tampered_payload_byte_is_rejected(key, v):
    h, p, s = mint(key).split(".")
    tampered = b64(_mutate_middle_byte(unb64(p)))
    assert rejection(v, f"{h}.{tampered}.{s}") in {R.INVALID_SIGNATURE}


def test_tampered_header_is_rejected(key, v):
    h, p, s = mint(key).split(".")
    header = json.loads(unb64(h))
    header = {"typ": header["typ"], "alg": header["alg"], "kid": header["kid"]}  # re-ordered
    assert rejection(v, f"{enc_json(header)}.{p}.{s}") is R.INVALID_SIGNATURE


def test_tampered_signature_is_rejected(key, v):
    h, p, s = mint(key).split(".")
    tampered = b64(_mutate_middle_byte(unb64(s)))
    assert tampered != s
    assert rejection(v, f"{h}.{p}.{tampered}") is R.INVALID_SIGNATURE


@pytest.mark.parametrize("length", [0, 63, 65, 72])
def test_wrong_length_signature_is_rejected(key, v, length):
    h, p, _ = mint(key).split(".")
    sig = b64(b"\x01" * length) if length else "AA"
    assert rejection(v, f"{h}.{p}.{sig}") is R.INVALID_SIGNATURE


def test_der_encoded_signature_is_rejected(key, v):
    h, p, _ = mint(key).split(".")
    der = key.private.sign(f"{h}.{p}".encode(), ec.ECDSA(hashes.SHA256()))
    assert rejection(v, f"{h}.{p}.{b64(der)}") is R.INVALID_SIGNATURE


def test_zero_signature_is_rejected(key, v):
    h, p, _ = mint(key).split(".")
    assert rejection(v, f"{h}.{p}.{b64(bytes(64))}") is R.INVALID_SIGNATURE


def test_non_canonical_signature_encoding_is_rejected(key, v):
    token = mint(key)
    h, p, s = token.split(".")
    # The final char of a 64-byte signature carries 4 spare bits; flipping
    # one of them keeps the bytes but changes the spelling.
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    last = alphabet.index(s[-1])
    variant = s[:-1] + alphabet[last ^ 0x01]
    assert unb64(variant) == unb64(s)
    assert rejection(v, f"{h}.{p}.{variant}") is R.INVALID_SIGNATURE


def test_attacker_key_reusing_trusted_kid_is_rejected(key, v):
    attacker = SigningKey(key.kid)
    assert rejection(v, mint(attacker)) is R.INVALID_SIGNATURE


def test_unknown_kid_is_rejected(key, v):
    assert rejection(v, mint(SigningKey("kid-never-published"))) is R.UNKNOWN_KEY


@pytest.mark.parametrize("kid", [None, "", 7, ["key-current"], "has space", "x" * 129, "tab\tkid"])
def test_missing_or_malformed_kid_is_rejected(key, v, kid):
    header = {} if kid is None else {"kid": kid}
    drop = ["kid"] if kid is None else []
    assert rejection(v, mint(key, header=header, drop_header=drop)) is R.INVALID_KEY_ID


@pytest.mark.parametrize("alg", ["none", "None", "HS256", "RS256", "PS256", "ES384", "ES512", "es256", "EdDSA", "", None, 256])
def test_unsupported_algorithm_is_rejected(key, v, alg):
    header = {"alg": alg} if alg is not None else {}
    drop = ["alg"] if alg is None else []
    assert rejection(v, mint(key, header=header, drop_header=drop)) is R.UNSUPPORTED_ALGORITHM


def test_alg_none_unsigned_token_is_rejected(key, v):
    h = enc_json({"alg": "none", "typ": "nf-identity+jwt", "kid": key.kid})
    p = enc_json(base_claims())
    assert rejection(v, f"{h}.{p}.") is R.MALFORMED
    assert rejection(v, f"{h}.{p}.{b64(b'x')}") is R.UNSUPPORTED_ALGORITHM


def _hs256(secret: bytes, key: SigningKey) -> str:
    h = enc_json({"alg": "HS256", "typ": "nf-identity+jwt", "kid": key.kid})
    p = enc_json(base_claims())
    sig = hmac.new(secret, f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{b64(sig)}"


def test_hs256_key_confusion_with_public_pem_is_rejected(key, v):
    assert rejection(v, _hs256(key.public_pem(), key)) is R.UNSUPPORTED_ALGORITHM


def test_hs256_key_confusion_with_public_jwk_json_is_rejected(key, v):
    secret = json.dumps(key.public_jwk).encode()
    assert rejection(v, _hs256(secret, key)) is R.UNSUPPORTED_ALGORITHM


def test_hs256_confusion_rejected_even_with_es256_header_label(key, v):
    h = enc_json({"alg": "ES256", "typ": "nf-identity+jwt", "kid": key.kid})
    p = enc_json(base_claims())
    sig = hmac.new(key.public_pem(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    assert rejection(v, f"{h}.{p}.{b64(sig + sig)}") is R.INVALID_SIGNATURE


def test_embedded_attacker_jwk_with_trusted_kid_is_rejected(key, v):
    attacker = SigningKey(key.kid)
    jwk = attacker.public_jwk
    assert rejection(v, mint(attacker, header={"jwk": jwk})) is R.UNSUPPORTED_HEADER


@pytest.mark.parametrize(
    "member,value",
    [
        ("jku", "https://attacker.example/jwks.json"),
        ("x5u", "https://attacker.example/cert.pem"),
        ("x5c", ["MIIB"]),
        ("x5t", "abc"),
        ("crit", ["exp"]),
        ("b64", False),
        ("cty", "JWT"),
        ("zip", "DEF"),
    ],
)
def test_unsupported_header_members_are_rejected(key, v, member, value):
    assert rejection(v, mint(key, header={member: value})) is R.UNSUPPORTED_HEADER


@pytest.mark.parametrize("typ", ["JWT", "jwt", "NF-IDENTITY+JWT", "nf-identity", "", 1])
def test_wrong_token_type_is_rejected(key, v, typ):
    assert rejection(v, mint(key, header={"typ": typ})) is R.WRONG_TOKEN_TYPE


def test_missing_token_type_is_rejected(key, v):
    assert rejection(v, mint(key, drop_header=["typ"])) is R.WRONG_TOKEN_TYPE


def test_novaforge_session_token_shape_is_rejected(key, v):
    # server/src/lib/jwt.ts: HS256, iss "novaforge", aud "novaforge:session".
    h = enc_json({"alg": "HS256", "typ": "JWT"})
    p = enc_json({"sub": "u", "sessionId": "s", "iss": "novaforge", "aud": "novaforge:session",
                  "iat": NOW, "exp": NOW + 900})
    sig = hmac.new(b"not-the-real-secret", f"{h}.{p}".encode(), hashlib.sha256).digest()
    assert rejection(v, f"{h}.{p}.{b64(sig)}") is R.UNSUPPORTED_ALGORITHM


# ----------------------------------------------------------------------
# C. Claims
# ----------------------------------------------------------------------


@pytest.mark.parametrize("iss", ["https://attacker.test", "https://novaforge.test/", "HTTPS://NOVAFORGE.TEST",
                                 "http://localhost:5173", "novaforge", "", 1, None, [ISSUER]])
def test_wrong_issuer_is_rejected(key, v, iss):
    assert rejection(v, mint(key, base_claims(iss=iss))) is R.WRONG_ISSUER


@pytest.mark.parametrize("aud", ["other-service", "Pashupatastra", "novaforge:session", "", [AUDIENCE],
                                 [AUDIENCE, "other"], None, 1])
def test_wrong_audience_is_rejected(key, v, aud):
    assert rejection(v, mint(key, base_claims(aud=aud))) is R.WRONG_AUDIENCE


@pytest.mark.parametrize("token_use", ["access", "session", "", None, "IDENTITY_ASSERTION"])
def test_wrong_token_use_is_rejected(key, v, token_use):
    assert rejection(v, mint(key, base_claims(token_use=token_use))) is R.WRONG_TOKEN_USE


@pytest.mark.parametrize(
    "overrides",
    [
        {"principal_type": "service"},
        {"principal_type": "USER"},
        {"account_status": "SUSPENDED"},
        {"account_status": "DISABLED"},
        {"account_status": "active"},
        {"account_status": None},
    ],
)
def test_inactive_or_non_user_principal_is_rejected(key, v, overrides):
    assert rejection(v, mint(key, base_claims(**overrides))) is R.INACTIVE_PRINCIPAL


@pytest.mark.parametrize(
    "claim",
    ["iss", "aud", "sub", "jti", "iat", "exp", "sid", "token_use", "principal_type", "account_status", "auth_time"],
)
def test_each_missing_required_claim_is_rejected(key, v, claim):
    assert rejection(v, mint(key, drop_claims=[claim])) is R.MISSING_CLAIM


@pytest.mark.parametrize("claim", ["role", "trust_level"])
def test_ignored_claims_are_not_required(key, v, claim):
    assert v.verify(mint(key, drop_claims=[claim]))


@pytest.mark.parametrize(
    "sub",
    ["", " ", " leading", "trailing ", "in side", "tab\tsub", "nl\nsub", "\x00", "é-subject", "x" * 257, 42, None, ["a"]],
)
def test_malformed_subject_is_rejected(key, v, sub):
    assert rejection(v, mint(key, base_claims(sub=sub))) is R.MALFORMED_SUBJECT


@pytest.mark.parametrize("claim", ["sid", "jti"])
@pytest.mark.parametrize("value", ["", " x", "a b", "x" * 257, 5, None])
def test_malformed_session_or_assertion_id_is_rejected(key, v, claim, value):
    assert rejection(v, mint(key, base_claims(**{claim: value}))) is R.INVALID_CLAIM


def test_expired_assertion_is_rejected(key):
    v = verifier(StaticKeyProvider(jwks(key)), FixedClock(NOW - 10 + 300 + TOL))
    assert rejection(v, mint(key)) is R.EXPIRED


def test_long_expired_assertion_is_rejected(key):
    v = verifier(StaticKeyProvider(jwks(key)), FixedClock(NOW + 86_400))
    assert rejection(v, mint(key)) is R.EXPIRED


def test_iat_beyond_tolerance_in_future_is_rejected(key):
    v = verifier(StaticKeyProvider(jwks(key)), FixedClock(NOW - 10 - TOL - 1))
    assert rejection(v, mint(key)) is R.ISSUED_IN_FUTURE


def test_nbf_in_future_is_rejected(key):
    claims = base_claims(nbf=NOW + TOL + 1, iat=NOW, exp=NOW + 300)
    assert rejection(verifier(StaticKeyProvider(jwks(key))), mint(key, claims)) is R.NOT_YET_VALID


def test_lifetime_over_maximum_is_rejected(key, v):
    assert rejection(v, mint(key, base_claims(iat=NOW - 10, exp=NOW - 10 + 301))) is R.LIFETIME_TOO_LONG


def test_lifetime_over_novaforge_reference_ceiling_is_rejected(key, v):
    # NovaForge's reference verifier would accept 600 s; this one pins 300.
    assert rejection(v, mint(key, base_claims(iat=NOW - 10, exp=NOW - 10 + 600))) is R.LIFETIME_TOO_LONG


@pytest.mark.parametrize(
    "overrides",
    [
        {"iat": float(NOW - 10)},
        {"exp": float(NOW + 290)},
        {"iat": True},
        {"exp": True},
        {"auth_time": False},
        {"iat": str(NOW - 10)},
        {"exp": None},
        {"auth_time": 1.5},
        {"iat": 0, "auth_time": 0, "exp": 300},
        {"iat": -5},
        {"exp": NOW - 10},  # exp == iat
        {"exp": NOW - 20},  # exp < iat
        {"auth_time": NOW - 10 + TOL + 1},  # authenticated after issuance, beyond tolerance
        {"nbf": None},
        {"nbf": float(NOW)},
        {"nbf": NOW - 11},  # before iat
        {"nbf": NOW + 290},  # at exp
    ],
)
def test_invalid_temporal_claims_are_rejected(key, v, overrides):
    assert rejection(v, mint(key, base_claims(**overrides))) is R.INVALID_TEMPORAL_CLAIMS


# ----------------------------------------------------------------------
# C2. Clock integrity (B1: a non-finite injected clock must fail closed)
# ----------------------------------------------------------------------
#
# Every temporal check is written as "reject if now fails this bound".
# A comparison against NaN is always False, so an unusable clock would
# silently pass every one of them and the verifier would fail OPEN. Each
# case below pairs an otherwise-valid, otherwise-still-live token with a
# broken clock reading and confirms the token is refused anyway, with the
# dedicated CLOCK_UNAVAILABLE reason rather than being silently accepted
# or misreported as an unrelated temporal failure.


@pytest.mark.parametrize(
    "reading",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        -1,
        -1.0,
        0,
        0.0,
        "1800000000",
        None,
        [],
        object(),
        True,
        False,
    ],
    ids=[
        "nan", "positive-infinity", "negative-infinity", "negative-int",
        "negative-float", "zero-int", "zero-float", "numeric-string",
        "none", "list", "object", "bool-true", "bool-false",
    ],
)
def test_unusable_clock_reading_is_rejected_as_clock_unavailable(key, reading):
    v = verifier(StaticKeyProvider(jwks(key)), FixedClock(reading))
    assert rejection(v, mint(key)) is R.CLOCK_UNAVAILABLE


def test_unusable_clock_reading_rejects_even_a_token_that_would_otherwise_verify(key):
    # Not merely "the clock check fires before the token is read" - the
    # token here is genuinely valid; only the clock is broken.
    v = verifier(StaticKeyProvider(jwks(key)), FixedClock(float("nan")))
    with pytest.raises(AssertionVerificationError) as info:
        v.verify(mint(key))
    assert info.value.reason is R.CLOCK_UNAVAILABLE
    assert info.value.__cause__ is None and info.value.__context__ is None


@pytest.mark.parametrize("reading", [NOW - 10, NOW, NOW + 10, 1000, 1000.5, 10_000_000_000])
def test_valid_finite_positive_clock_readings_are_accepted(key, reading):
    v = verifier(StaticKeyProvider(jwks(key)), FixedClock(reading))
    claims = base_claims(iat=int(reading) - 1, exp=int(reading) - 1 + 300, auth_time=int(reading) - 1)
    assert v.verify(mint(key, claims))


def test_default_clock_is_real_time_and_still_works(key):
    # The default clock argument (time.time) must be unaffected by the
    # added validation: a token minted "now" must still verify normally.
    v = IdentityAssertionVerifier(config(), StaticKeyProvider(jwks(key)), InMemoryReplayGuard())
    import time as time_module

    now = int(time_module.time())
    claims = base_claims(iat=now - 1, exp=now - 1 + 300, auth_time=now - 1)
    assert v.verify(mint(key, claims))


# ----------------------------------------------------------------------
# D. Malformed tokens
# ----------------------------------------------------------------------


@pytest.mark.parametrize("token", [None, 1, b"a.b.c", ["a", "b", "c"], "", "abc", "a.b", "a.b.c.d", "..", "a..c", ".b.c"])
def test_structurally_malformed_tokens_are_rejected(v, token):
    assert rejection(v, token) is R.MALFORMED


def test_oversized_token_is_rejected(v):
    assert rejection(v, "a" * 8193) is R.TOO_LARGE


def test_non_base64url_segments_are_rejected(key, v):
    h, p, s = mint(key).split(".")
    for bad in (f"{h}=.{p}.{s}", f"{h}.{p}+.{s}", f"{h.replace('e', '/')}.{p}.{s}", f"{h}.{p}==.{s}"):
        assert rejection(v, bad) in {R.MALFORMED, R.INVALID_SIGNATURE}


@pytest.mark.parametrize(
    "raw_header",
    [b"not json", b"[1,2]", b'"ES256"', b"\xff\xfe", b'{"alg":"ES256","alg":"ES256","typ":"nf-identity+jwt"}'],
)
def test_malformed_header_json_is_rejected(key, v, raw_header):
    assert rejection(v, mint(key, raw_header=raw_header)) is R.MALFORMED


def test_duplicate_kid_header_member_is_rejected(key, v):
    raw = ('{"alg":"ES256","typ":"nf-identity+jwt","kid":"key-current","kid":"other"}').encode()
    assert rejection(v, mint(key, raw_header=raw)) is R.MALFORMED


@pytest.mark.parametrize(
    "raw_payload",
    [b"not json", b"[]", b"null", b'{"exp": NaN}', b'{"exp": Infinity}', b"\xc3\x28"],
)
def test_malformed_but_validly_signed_payload_is_rejected(key, v, raw_payload):
    assert rejection(v, mint(key, raw_payload=raw_payload)) is R.MALFORMED


def test_duplicate_subject_claim_is_rejected_even_when_signed(key, v):
    claims = base_claims()
    body = json.dumps(claims, separators=(",", ":"))
    raw = (body[:-1] + ',"sub":"victim-subject"}').encode()
    assert rejection(v, mint(key, raw_payload=raw)) is R.MALFORMED


# ----------------------------------------------------------------------
# E. Replay
# ----------------------------------------------------------------------


def test_same_assertion_twice_is_replay(key, v):
    token = mint(key)
    assert v.verify(token)
    assert rejection(v, token) is R.REPLAYED


def test_same_jti_in_a_fresh_signature_is_replay(key, v):
    jti = str(uuid.uuid4())
    assert v.verify(mint(key, base_claims(jti=jti)))
    assert rejection(v, mint(key, base_claims(jti=jti, iat=NOW - 5, exp=NOW + 295))) is R.REPLAYED


def test_malleated_ecdsa_signature_is_caught_as_replay(key, v):
    token = mint(key)
    h, p, s = token.split(".")
    raw = unb64(s)
    r, sv = raw[:32], int.from_bytes(raw[32:], "big")
    malleated = b64(r + (P256_N - sv).to_bytes(32, "big"))
    assert v.verify(token)
    assert rejection(v, f"{h}.{p}.{malleated}") is R.REPLAYED


def test_rejected_assertion_does_not_consume_its_jti(key, v):
    jti = str(uuid.uuid4())
    assert rejection(v, mint(key, base_claims(jti=jti, aud="other"))) is R.WRONG_AUDIENCE
    assert v.verify(mint(key, base_claims(jti=jti)))


def test_replay_entries_are_purged_after_expiry_window(key):
    clock = FixedClock()
    guard = InMemoryReplayGuard()
    v = verifier(StaticKeyProvider(jwks(key)), clock, guard)
    v.verify(mint(key))
    assert len(guard) == 1

    clock.now = NOW - 10 + 300 + TOL
    guard.check_and_record(("i", "other"), clock.now + 10, clock.now)
    assert len(guard) == 1


def test_full_replay_guard_refuses_instead_of_evicting(key):
    guard = InMemoryReplayGuard(capacity=1)
    v = verifier(StaticKeyProvider(jwks(key)), guard=guard)
    first = mint(key)
    assert v.verify(first)
    assert rejection(v, mint(key)) is R.REPLAY_GUARD_FULL
    assert rejection(v, first) is R.REPLAYED


def test_replay_guard_keys_on_issuer_and_jti():
    guard = InMemoryReplayGuard()
    guard.check_and_record(("iss-a", "j"), NOW + 10, NOW)
    guard.check_and_record(("iss-b", "j"), NOW + 10, NOW)
    with pytest.raises(ReplayDetected):
        guard.check_and_record(("iss-a", "j"), NOW + 10, NOW)


def test_replay_guard_capacity_must_be_positive():
    for bad in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            InMemoryReplayGuard(capacity=bad)


def test_replay_guard_full_error_type():
    guard = InMemoryReplayGuard(capacity=1)
    guard.check_and_record(("i", "a"), NOW + 10, NOW)
    with pytest.raises(ReplayGuardFull):
        guard.check_and_record(("i", "b"), NOW + 10, NOW)


# ----------------------------------------------------------------------
# F. JWKS parsing
# ----------------------------------------------------------------------


def test_novaforge_shaped_jwks_parses(key):
    previous = SigningKey("key-previous")
    parsed = parse_jwks(json.dumps(jwks(key, previous)))
    assert set(parsed) == {"key-current", "key-previous"}


def test_jwks_accepts_bytes_str_and_mapping(key):
    doc = jwks(key)
    for form in (doc, json.dumps(doc), json.dumps(doc).encode()):
        assert set(parse_jwks(form)) == {key.kid}


def _private_jwk(key: SigningKey) -> Dict[str, str]:
    jwk = key.public_jwk
    d = key.private.private_numbers().private_value
    jwk["d"] = b64(d.to_bytes(32, "big"))
    return jwk


def _off_curve_jwk(key: SigningKey) -> Dict[str, str]:
    jwk = key.public_jwk
    y = int.from_bytes(unb64(jwk["y"]), "big")
    jwk["y"] = b64(((y + 1) % (2**256)).to_bytes(32, "big"))
    return jwk


def _rsa_jwk() -> Dict[str, str]:
    numbers = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key().public_numbers()
    return {"kty": "RSA", "kid": "rsa", "n": b64(numbers.n.to_bytes(256, "big")), "e": "AQAB"}


def _p384_jwk() -> Dict[str, str]:
    numbers = ec.generate_private_key(ec.SECP384R1()).public_key().public_numbers()
    return {"kty": "EC", "crv": "P-384", "kid": "p384",
            "x": b64(numbers.x.to_bytes(48, "big")), "y": b64(numbers.y.to_bytes(48, "big"))}


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda k: {"keys": [_private_jwk(k)]}, id="private-d"),
        pytest.param(lambda k: {"keys": [k.public_jwk, k.public_jwk]}, id="duplicate-kid"),
        pytest.param(lambda k: {"keys": [{**k.public_jwk, "kid": ""}]}, id="empty-kid"),
        pytest.param(lambda k: {"keys": [{n: v for n, v in k.public_jwk.items() if n != "kid"}]}, id="missing-kid"),
        pytest.param(lambda k: {"keys": [{**k.public_jwk, "kid": 5}]}, id="int-kid"),
        pytest.param(lambda k: {"keys": [_off_curve_jwk(k)]}, id="off-curve"),
        pytest.param(lambda k: {"keys": [_rsa_jwk()]}, id="rsa"),
        pytest.param(lambda k: {"keys": [_p384_jwk()]}, id="p384"),
        pytest.param(lambda k: {"keys": [k.public_jwk, _rsa_jwk()]}, id="mixed-with-rsa"),
        pytest.param(lambda k: {"keys": [{**k.public_jwk, "kty": "oct", "k": "c2VjcmV0"}]}, id="symmetric"),
        pytest.param(lambda k: {"keys": [{**k.public_jwk, "alg": "RS256"}]}, id="wrong-alg"),
        pytest.param(lambda k: {"keys": [{**k.public_jwk, "use": "enc"}]}, id="wrong-use"),
        pytest.param(lambda k: {"keys": [{**k.public_jwk, "key_ops": ["sign"]}]}, id="wrong-key-ops"),
        pytest.param(lambda k: {"keys": [{**k.public_jwk, "x5u": "https://x"}]}, id="unknown-member"),
        pytest.param(lambda k: {"keys": [{**k.public_jwk, "x": k.public_jwk["x"] + "A"}]}, id="bad-x-length"),
        pytest.param(lambda k: {"keys": [{**k.public_jwk, "y": "!!"}]}, id="bad-y-encoding"),
        pytest.param(lambda k: {"keys": [{**k.public_jwk, "x": 5}]}, id="int-x"),
        pytest.param(lambda k: {"keys": []}, id="empty-keys"),
        pytest.param(lambda k: {"keys": {}}, id="keys-not-list"),
        pytest.param(lambda k: {}, id="no-keys"),
        pytest.param(lambda k: [k.public_jwk], id="top-level-array"),
        pytest.param(lambda k: {"keys": ["not-an-object"]}, id="entry-not-object"),
        pytest.param(lambda k: "not json", id="not-json"),
        pytest.param(lambda k: '{"keys": [], "keys": []}', id="duplicate-member"),
        pytest.param(lambda k: {"keys": [SigningKey(f"k{i}").public_jwk for i in range(33)]}, id="too-many"),
        pytest.param(lambda k: "x" * (64 * 1024 + 1), id="too-large"),
    ],
)
def test_malformed_jwks_is_refused_whole(key, build):
    with pytest.raises(TrustedKeyError) as info:
        parse_jwks(build(key))
    message = str(info.value)
    for secret in (key.public_jwk["x"], key.public_jwk["y"], _private_jwk(key)["d"]):
        assert secret not in message


def test_private_jwk_is_refused_as_private_material_not_merely_unknown(key):
    with pytest.raises(TrustedKeyError) as info:
        parse_jwks({"keys": [_private_jwk(key)]})
    assert "private" in str(info.value)


def test_static_provider_refuses_malformed_jwks(key):
    with pytest.raises(TrustedKeyError):
        StaticKeyProvider({"keys": [_private_jwk(key)]})


# ----------------------------------------------------------------------
# G. Key provider seam: rotation, unknown and stale keys
# ----------------------------------------------------------------------


class ScriptedFetch:
    def __init__(self, *documents: Any) -> None:
        self.documents: List[Any] = list(documents)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        doc = self.documents[0] if len(self.documents) == 1 else self.documents.pop(0)
        if isinstance(doc, Exception):
            raise doc
        return doc


def test_rotation_window_accepts_current_and_previous_keys():
    old, new = SigningKey("key-2026-01"), SigningKey("key-2026-02")
    v = verifier(StaticKeyProvider(jwks(new, old)))
    assert v.verify(mint(new))
    assert v.verify(mint(old))


def test_caching_provider_verifies_current_key(key):
    fetch = ScriptedFetch(jwks(key))
    v = verifier(CachingJwksProvider(fetch, FixedClock()))
    assert v.verify(mint(key))
    assert v.verify(mint(key))
    assert fetch.calls == 1


def test_caching_provider_picks_up_rotated_key_on_unknown_kid():
    old, new = SigningKey("key-old"), SigningKey("key-new")
    clock = FixedClock()
    fetch = ScriptedFetch(jwks(old), jwks(new, old))
    provider = CachingJwksProvider(fetch, clock)
    v = verifier(provider, clock)

    assert v.verify(mint(old))
    clock.now += 61
    assert v.verify(mint(new))
    assert v.verify(mint(old))
    assert fetch.calls == 2


def test_unknown_kid_refetch_is_rate_limited(key):
    clock = FixedClock()
    fetch = ScriptedFetch(jwks(key))
    v = verifier(CachingJwksProvider(fetch, clock), clock)
    stranger = SigningKey("kid-unknown")

    v.verify(mint(key))  # initial fetch
    clock.now += 61
    assert rejection(v, mint(stranger)) is R.UNKNOWN_KEY
    for _ in range(20):
        assert rejection(v, mint(stranger)) is R.UNKNOWN_KEY
    assert fetch.calls == 2


def test_stale_key_dropped_from_jwks_is_rejected_after_refresh():
    old, new = SigningKey("key-old"), SigningKey("key-new")
    clock = FixedClock()
    fetch = ScriptedFetch(jwks(new, old), jwks(new))
    v = verifier(CachingJwksProvider(fetch, clock), clock)

    assert v.verify(mint(old))
    clock.now += 301  # cache max age passes; the next lookup refetches
    token = mint(old, base_claims(iat=int(clock.now) - 5, exp=int(clock.now) + 295, auth_time=int(clock.now) - 60))
    assert rejection(v, token) is R.UNKNOWN_KEY
    assert fetch.calls == 2


def test_stale_cache_with_failing_fetch_fails_closed(key):
    clock = FixedClock()
    fetch = ScriptedFetch(jwks(key), OSError("network down"))
    v = verifier(CachingJwksProvider(fetch, clock), clock)

    assert v.verify(mint(key))
    clock.now += 301
    token = mint(key, base_claims(iat=int(clock.now) - 5, exp=int(clock.now) + 295, auth_time=int(clock.now) - 60))
    assert rejection(v, token) is R.KEY_SET_UNAVAILABLE


def test_no_key_set_ever_fetched_fails_closed(key):
    v = verifier(CachingJwksProvider(ScriptedFetch(OSError("down")), FixedClock()))
    assert rejection(v, mint(key)) is R.KEY_SET_UNAVAILABLE


def test_malformed_refresh_keeps_fresh_set_but_never_extends_it(key):
    clock = FixedClock()
    fetch = ScriptedFetch(jwks(key), {"keys": [_private_jwk(key)]})
    provider = CachingJwksProvider(fetch, clock)

    assert provider.get_key(key.kid) is not None
    clock.now += 61
    assert provider.get_key("kid-unknown") is None  # refresh attempt fails; old set still fresh
    assert provider.get_key(key.kid) is not None
    clock.now = NOW + 301
    with pytest.raises(KeySetUnavailable):
        provider.get_key(key.kid)


def test_failed_fetch_while_fresh_reports_unknown_not_unavailable(key):
    clock = FixedClock()
    fetch = ScriptedFetch(jwks(key), RuntimeError("boom"))
    provider = CachingJwksProvider(fetch, clock)
    assert provider.get_key(key.kid) is not None
    clock.now += 61
    assert provider.get_key("other") is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_age_seconds": 0},
        {"max_age_seconds": 3601},
        {"min_refetch_interval_seconds": 0},
        {"max_age_seconds": 60, "min_refetch_interval_seconds": 61},
    ],
)
def test_caching_provider_rejects_bad_timings(kwargs):
    with pytest.raises(ValueError):
        CachingJwksProvider(lambda: {}, FixedClock(), **kwargs)


def test_provider_returning_non_p256_key_is_refused(key):
    class Hostile:
        def get_key(self, kid):
            return keys_module.TrustedKey(kid=kid, public_key=ec.generate_private_key(ec.SECP384R1()).public_key())

    assert rejection(verifier(Hostile()), mint(key)) is R.UNKNOWN_KEY


# ----------------------------------------------------------------------
# H. Security properties
# ----------------------------------------------------------------------


def test_rejection_carries_reason_only_and_no_chain(key, v):
    token = mint(key, base_claims(aud="other"))
    with pytest.raises(AssertionVerificationError) as info:
        v.verify(token)
    err = info.value

    assert str(err) == "Identity assertion rejected: WRONG_AUDIENCE"
    assert err.__cause__ is None
    assert err.__context__ is None
    for segment in token.split("."):
        assert segment not in str(err)
        assert segment not in repr(err)


def test_signature_failure_does_not_leak_library_detail(key, v):
    h, p, s = mint(SigningKey(key.kid)).split(".")
    with pytest.raises(AssertionVerificationError) as info:
        v.verify(f"{h}.{p}.{s}")
    assert str(info.value) == "Identity assertion rejected: INVALID_SIGNATURE"
    assert info.value.__context__ is None and info.value.__cause__ is None


def test_unexpected_provider_error_fails_closed_without_leaking(key):
    class Broken:
        def get_key(self, kid):
            raise RuntimeError("PRIVATE-DETAIL-xyz")

    with pytest.raises(AssertionVerificationError) as info:
        verifier(Broken()).verify(mint(key))
    assert info.value.reason is R.INTERNAL_ERROR
    assert "PRIVATE-DETAIL" not in str(info.value)
    assert info.value.__context__ is None


def test_unexpected_clock_error_fails_closed(key):
    def clock():
        raise RuntimeError("clock broke")

    v = IdentityAssertionVerifier(config(), StaticKeyProvider(jwks(key)), InMemoryReplayGuard(), clock)
    assert rejection(v, mint(key)) is R.INTERNAL_ERROR


def test_verify_accepts_no_client_actor_inputs():
    params = list(inspect.signature(IdentityAssertionVerifier.verify).parameters)
    assert params == ["self", "assertion"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"issuer": ""},
        {"issuer": " https://x"},
        {"issuer": None},
        {"audience": ""},
        {"audience": "a b"},
        {"audience": ["pashupatastra"]},
        {"identity_provider": ""},
        {"identity_provider": " novaforge"},
        {"identity_provider": "x" * 65},
        {"clock_tolerance_seconds": -1},
        {"clock_tolerance_seconds": 61},
        {"clock_tolerance_seconds": True},
        {"max_lifetime_seconds": 0},
        {"max_lifetime_seconds": 301},
        {"max_lifetime_seconds": 300.0},
        {"max_assertion_length": 0},
        {"max_assertion_length": 8193},
    ],
)
def test_config_rejects_unusable_values(overrides):
    with pytest.raises(AssertionVerifierConfigError):
        config(**overrides)


def test_config_has_no_default_issuer_audience_or_provider():
    with pytest.raises(TypeError):
        AssertionVerifierConfig()  # type: ignore[call-arg]
    required = [
        f.name
        for f in dataclasses.fields(AssertionVerifierConfig)
        if f.default is dataclasses.MISSING
    ]
    assert required == ["issuer", "audience", "identity_provider"]


def test_verifier_requires_a_replay_guard_and_a_provider(key):
    with pytest.raises(AssertionVerifierConfigError):
        IdentityAssertionVerifier(config(), StaticKeyProvider(jwks(key)), None)  # type: ignore[arg-type]
    with pytest.raises(AssertionVerifierConfigError):
        IdentityAssertionVerifier(config(), None, InMemoryReplayGuard())  # type: ignore[arg-type]
    with pytest.raises(AssertionVerifierConfigError):
        IdentityAssertionVerifier({"issuer": ISSUER}, StaticKeyProvider(jwks(key)), InMemoryReplayGuard())  # type: ignore[arg-type]


def _imports(module) -> set:
    tree = ast.parse(inspect.getsource(module))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("module", [assertion_module, keys_module])
def test_modules_import_no_http_persistence_api_or_authorization(module):
    imported = _imports(module)
    top = {n.split(".")[0] for n in imported}
    assert not top & {"sqlite3", "requests", "httpx", "urllib", "fastapi", "starlette", "jwt", "jose", "authlib", "os"}
    for forbidden in (
        "backend.app.api",
        "backend.app.jobs",
        "backend.app.persistence",
        "backend.app.identity.actor",
        "backend.app.identity.authorization",
        "backend.app.identity.policy",
        "backend.app.identity.assignments",
        "backend.app.identity.scope",
        "backend.app.identity.scope_assignment",
        "backend.app.identity.role_actions",
        "backend.app.identity.resource",
    ):
        assert not any(n == forbidden or n.startswith(forbidden + ".") for n in imported), forbidden


@pytest.mark.parametrize("module", [assertion_module, keys_module])
def test_modules_reference_no_actor_role_or_scope_types(module):
    tree = ast.parse(inspect.getsource(module))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not names & {"Actor", "ActorRole", "human_actor", "RoleAssignment", "RailwayScope",
                        "RoleScopeAssignment", "IdentityAssurance", "EnforcingPolicy"}


def test_no_key_material_or_secret_in_new_sources():
    root = Path(__file__).resolve().parents[1] / "app" / "identity"
    for name in ("identity_assertion.py", "trusted_keys.py", "_jose.py"):
        text = (root / name).read_text(encoding="utf-8")
        assert "BEGIN" not in text and "PRIVATE KEY" not in text
    this = Path(__file__).read_text(encoding="utf-8")
    assert "BEGIN " + "PRIVATE" not in this and "BEGIN " + "EC" not in this


def test_identity_package_surface_is_unchanged():
    import backend.app.identity as identity

    for name in ("IdentityAssertionVerifier", "VerifiedIdentityAssertion", "StaticKeyProvider",
                 "CachingJwksProvider", "parse_jwks"):
        assert name not in identity.__all__
        assert not hasattr(identity, name)


def test_request_actor_seam_is_untouched():
    from backend.app.api import deps

    params = list(inspect.signature(deps.request_actor).parameters)
    assert params == ["actor_id", "actor_role"]
    assert "identity_assertion" not in inspect.getsource(deps)


# ----------------------------------------------------------------------
# Fixed interop vector (see test_assertion_minted_by_novaforge_library_verifies)
# ----------------------------------------------------------------------

NOVAFORGE_MINTED: Dict[str, Any] = {
    "assertion": (
        "eyJhbGciOiJFUzI1NiIsInR5cCI6Im5mLWlkZW50aXR5K2p3dCIsImtpZCI6ImRldi1lcGhlbWVyYWwtNzk5YWZhNjctZjhkNS00OGU0LWI5NWEtMmI5Nzc0NGNlOTY1In0"
        ".eyJ0b2tlbl91c2UiOiJpZGVudGl0eV9hc3NlcnRpb24iLCJwcmluY2lwYWxfdHlwZSI6InVzZXIiLCJzaWQiOiIzMTFmYzQ5OC1hZTk1LTQ2N2UtYjEzNi1hODNiOTM5MDI3N2QiLCJyb2xlIjoiU1VQRVJfQURNSU4iLCJhY2NvdW50X3N0YXR1cyI6IkFDVElWRSIsImF1dGhfdGltZSI6MTc5MDExMzYyMywidHJ1c3RfbGV2ZWwiOjk3LCJpYXQiOjE3OTAxMTM2ODMsImV4cCI6MTc5MDExMzk4MywiYXVkIjoicGFzaHVwYXRhc3RyYSIsImlzcyI6Imh0dHBzOi8vbm92YWZvcmdlLnRlc3QiLCJzdWIiOiI0ZTBhYWI5Ny1kYTg2LTQ3NjYtOTgwYS0xZThiMDExNWIxNWYiLCJqdGkiOiIxMzQ3NWY4Yy1mNWYwLTRhY2EtYjkzZC0zMjkyYzAxY2ZkODYifQ"
        ".xKIGXpvKrC53h2WEH928le3WfuJDU_NcEOUU4PGKAIPynJUV4s2ULoxcs_fvK_C2TpjsfqYvohIxwD8J_AIohg"
    ),
    "jwks": {
        "keys": [
            {
                "kty": "EC",
                "crv": "P-256",
                "x": "lpuc9JEwzkUBxs5_qDAkzGT4u6ji7FdaZ7WMFCLuo6g",
                "y": "ezErtIQEjmXp_M0QOEfLSxuKKdqqglpge5wyDlUN1qc",
                "kid": "dev-ephemeral-799afa67-f8d5-48e4-b95a-2b97744ce965",
                "alg": "ES256",
                "use": "sig",
            }
        ]
    },
}
NOVAFORGE_MINTED_IAT = 1790113683
NOVAFORGE_MINTED_SUB = "4e0aab97-da86-4766-980a-1e8b0115b15f"


def test_novaforge_vector_carries_the_discovered_contract():
    h, p, _ = NOVAFORGE_MINTED["assertion"].split(".")
    assert json.loads(unb64(h)) == {
        "alg": "ES256",
        "typ": "nf-identity+jwt",
        "kid": "dev-ephemeral-799afa67-f8d5-48e4-b95a-2b97744ce965",
    }
    assert sorted(json.loads(unb64(p))) == sorted(
        ["token_use", "principal_type", "sid", "role", "account_status", "auth_time",
         "trust_level", "iat", "exp", "aud", "iss", "sub", "jti"]
    )  # no nbf, no nonce, no identity_provider claim


def test_novaforge_vector_is_bound_to_its_audience_and_issuer():
    provider = StaticKeyProvider(NOVAFORGE_MINTED["jwks"])
    clock = FixedClock(NOVAFORGE_MINTED_IAT + 10)
    token = NOVAFORGE_MINTED["assertion"]
    assert rejection(verifier(provider, clock, audience="other-service"), token) is R.WRONG_AUDIENCE
    assert rejection(verifier(provider, clock, issuer="http://localhost:5173"), token) is R.WRONG_ISSUER
    assert rejection(verifier(provider, FixedClock(NOVAFORGE_MINTED_IAT + 330)), token) is R.EXPIRED
