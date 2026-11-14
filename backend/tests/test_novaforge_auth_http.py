"""The NovaForge-enforced deployment over real HTTP, against a fake identity provider.

The fake issues genuine ES256 tokens and implements just enough of
NovaForge's /oauth endpoints (authorize, token with PKCE and refresh
rotation, introspection, revocation, JWKS). Everything on the Pashupatastra
side is the production code: settings, composition, verifier, session core,
directory, policy and the FastAPI app. The cross-application test against the
real NovaForge lives in the NovaForge repository (server/test/pashupatastra-e2e.test.ts).
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from fastapi.testclient import TestClient

from backend.app.api.composition import (
    AuthConfigurationError,
    compose_novaforge_http,
)
from backend.app.api.main import build_app
from backend.app.auth import runtime as auth_runtime
from backend.app.jobs import router as jobs_router

ISSUER = "https://id.test"
FRONTEND = "https://pp.test"
CLIENT_ID = "pashupatastra"
AUDIENCE = "pashupatastra-api"
SECRET = "nfcs_" + "s" * 43
REDIRECT = f"{FRONTEND}/api/pp/auth/callback"
CSRF = {"X-PP-CSRF": "1"}

ALL_PERMS = {
    "WORKER": ["job.report_job", "job.start_execution", "job.complete_job", "job.read_job", "job.read_job_history"],
    "ENGINEER": ["job.request_optimization", "job.read_optimization_run", "job.read_job"],
    "AUTHORITY": ["job.commit_block", "job.read_job", "job.read_job_history", "job.reject_proposal"],
}


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeNovaForge:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.kid = "test-key-1"
        self.users: Dict[str, Dict[str, Any]] = {}
        self.grants: Dict[str, Dict[str, Any]] = {}
        self.codes: Dict[str, Dict[str, Any]] = {}
        self.access: Dict[str, str] = {}  # access token -> grant id
        self.refresh: Dict[str, Dict[str, Any]] = {}  # refresh token -> {grant, used}
        self.down = False
        self.calls: List[str] = []

    # ---- helpers --------------------------------------------------------
    def jwk(self) -> Dict[str, str]:
        n = self.key.public_key().public_numbers()
        return {
            "kty": "EC", "crv": "P-256", "alg": "ES256", "use": "sig", "kid": self.kid,
            "x": b64(n.x.to_bytes(32, "big")), "y": b64(n.y.to_bytes(32, "big")),
        }

    def sign(self, header: Dict[str, Any], claims: Dict[str, Any]) -> str:
        signing_input = f"{b64(json.dumps(header, separators=(',', ':')).encode())}.{b64(json.dumps(claims, separators=(',', ':')).encode())}"
        r, s = decode_dss_signature(self.key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256())))
        return f"{signing_input}.{b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"

    def add_user(self, sub: str, assignments: List[Dict[str, Any]], name: str = "Test User") -> None:
        self.users[sub] = {"sub": sub, "name": name, "email": f"{sub}@example.test", "assignments": assignments, "active": True}

    def role(self, role: str, resource: str = "*", perms: Optional[List[str]] = None) -> Dict[str, Any]:
        return {"role": role.lower(), "resource": resource, "permissions": ALL_PERMS.get(role, []) if perms is None else perms}

    def id_token(self, grant: Dict[str, Any], *, nonce: str, **over: Any) -> str:
        now = int(self.clock())
        claims = {
            "iss": ISSUER, "aud": CLIENT_ID, "sub": grant["sub"], "iat": now, "exp": now + 300,
            "auth_time": now - 5, "jti": secrets.token_hex(8), "sid": grant["id"], "nonce": nonce,
            "token_use": "id_token", "principal_type": "user", "account_status": "ACTIVE",
        }
        claims.update(over)
        return self.sign({"alg": "ES256", "typ": "JWT", "kid": self.kid}, claims)

    def live_grant(self, grant_id: str) -> Optional[Dict[str, Any]]:
        grant = self.grants.get(grant_id)
        user = self.users.get(grant["sub"]) if grant else None
        if not grant or grant["revoked"] or not user or not user["active"] or not user["assignments"]:
            return None
        return grant

    def tokens_for(self, grant: Dict[str, Any], nonce: Optional[str]) -> Dict[str, Any]:
        access = "at." + secrets.token_urlsafe(24)
        self.access[access] = grant["id"]
        refresh = "nfrt_" + secrets.token_urlsafe(32)[:43].ljust(43, "x")
        self.refresh[refresh] = {"grant": grant["id"], "used": False}
        body = {"access_token": access, "token_type": "Bearer", "expires_in": 600, "refresh_token": refresh, "scope": "openid"}
        if nonce is not None:
            body["id_token"] = self.id_token(grant, nonce=nonce)
        return body

    # ---- the browser leg: what NovaForge's /authorize does --------------
    def authorize(self, location: str, sub: str, *, deny: bool = False) -> str:
        q = {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}
        assert q["client_id"] == CLIENT_ID and q["redirect_uri"] == REDIRECT
        assert q["code_challenge_method"] == "S256" and q["response_type"] == "code"
        if deny:
            return f"{REDIRECT}?error=access_denied&state={q['state']}&iss={ISSUER}"
        grant = {"id": secrets.token_hex(8), "sub": sub, "revoked": False}
        self.grants[grant["id"]] = grant
        code = secrets.token_urlsafe(16)
        self.codes[code] = {"grant": grant["id"], "nonce": q["nonce"], "challenge": q["code_challenge"], "used": False}
        return f"{REDIRECT}?code={code}&state={q['state']}&iss={ISSUER}"

    # ---- the back channel ----------------------------------------------
    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(path)
        if self.down:
            return httpx.Response(503, json={"error": "temporarily_unavailable"})
        if path == "/.well-known/jwks.json":
            return httpx.Response(200, json={"keys": [self.jwk()]})
        expected = "Basic " + base64.b64encode(f"{CLIENT_ID}:{SECRET}".encode()).decode()
        if request.headers.get("authorization") != expected:
            return httpx.Response(401, json={"error": "invalid_client"})
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if path == "/api/oauth/token" and form.get("grant_type") == "authorization_code":
            code = self.codes.get(form.get("code", ""))
            if not code or code["used"]:
                if code:
                    self.grants[code["grant"]]["revoked"] = True
                return httpx.Response(400, json={"error": "invalid_grant"})
            code["used"] = True
            verifier = form.get("code_verifier", "")
            if b64(hashlib.sha256(verifier.encode()).digest()) != code["challenge"] or form.get("redirect_uri") != REDIRECT:
                return httpx.Response(400, json={"error": "invalid_grant"})
            grant = self.live_grant(code["grant"])
            if not grant:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json=self.tokens_for(grant, code["nonce"]))
        if path == "/api/oauth/token" and form.get("grant_type") == "refresh_token":
            record = self.refresh.get(form.get("refresh_token", ""))
            if not record:
                return httpx.Response(400, json={"error": "invalid_grant"})
            if record["used"]:
                self.grants[record["grant"]]["revoked"] = True
                return httpx.Response(400, json={"error": "invalid_grant"})
            grant = self.live_grant(record["grant"])
            if not grant:
                return httpx.Response(400, json={"error": "invalid_grant"})
            record["used"] = True
            return httpx.Response(200, json=self.tokens_for(grant, None))
        if path == "/api/oauth/introspect":
            grant = self.live_grant(self.access.get(form.get("token", ""), ""))
            if not grant:
                return httpx.Response(200, json={"active": False})
            user = self.users[grant["sub"]]
            return httpx.Response(200, json={
                "active": True, "sub": grant["sub"], "client_id": CLIENT_ID, "aud": AUDIENCE,
                "name": user["name"], "email": user["email"], "assignments": user["assignments"],
            })
        if path == "/api/oauth/revoke":
            record = self.refresh.get(form.get("token", ""))
            if record:
                self.grants[record["grant"]]["revoked"] = True
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"error": "not_found"})


class World:
    def __init__(self, tmp_path, monkeypatch, freshness: int = 30) -> None:
        self.clock = FakeClock()
        self.nf = FakeNovaForge(self.clock)
        environ = {
            "PASHUPAT_AUTH_MODE": "novaforge",
            "PASHUPAT_AUTH_ISSUER": ISSUER,
            "PASHUPAT_AUTH_CLIENT_ID": CLIENT_ID,
            "PASHUPAT_AUTH_CLIENT_SECRET": SECRET,
            "PASHUPAT_AUTH_AUDIENCE": AUDIENCE,
            "PASHUPAT_AUTH_REDIRECT_URI": REDIRECT,
            "PASHUPAT_AUTH_FRONTEND_ORIGIN": FRONTEND,
            "PASHUPAT_AUTH_JOBS_DB": str(tmp_path / "enforced" / "jobs-auth.db"),
            "PASHUPAT_AUTH_FRESHNESS_SECONDS": str(freshness),
        }
        self.environ = environ
        http = httpx.Client(transport=httpx.MockTransport(self.nf.handler), base_url=ISSUER)
        composition = compose_novaforge_http(environ, http_client=http, clock=self.clock)
        self.auth = composition.auth
        # The jobs router holds module-level services (replaced by tests, as elsewhere).
        monkeypatch.setattr(jobs_router, "service", composition.service)
        monkeypatch.setattr(jobs_router, "optimization_service", composition.optimization_service)
        self.app = build_app(self.auth)
        self.client = TestClient(self.app, base_url=FRONTEND, follow_redirects=False)

    # -- flow ---------------------------------------------------------------
    def login(self, sub: str, client: Optional[TestClient] = None, return_to: str = "/jobs") -> TestClient:
        c = client or self.client
        start = c.get(f"/auth/login?return_to={return_to}")
        assert start.status_code == 302, start.text
        callback = self.nf.authorize(start.headers["location"], sub)
        done = c.get(callback.replace(FRONTEND + "/api/pp", ""))
        assert done.status_code == 302, done.text
        assert done.headers["location"] == f"{FRONTEND}{return_to}", done.headers["location"]
        return c

    def new_browser(self) -> TestClient:
        return TestClient(self.app, base_url=FRONTEND, follow_redirects=False)


def job_body() -> Dict[str, Any]:
    return {
        "track_id": "UP-1", "job_type": "BALLAST_TAMPING", "distance_start": 1000.0,
        "distance_end": 1400.0, "workers_min": 2, "workers_max": 4, "description": "Field-reported defect",
    }


@pytest.fixture(scope="module")
def _world(tmp_path_factory):
    # Building the jobs service loads the corridor dataset (slow), so one
    # deployment serves the module; `world` below resets all mutable state.
    monkeypatch = pytest.MonkeyPatch()
    world = World(tmp_path_factory.mktemp("enforced"), monkeypatch)
    yield world
    auth_runtime.set_auth_service(None)
    monkeypatch.undo()


@pytest.fixture()
def world(_world):
    nf = _world.nf
    nf.users.clear(), nf.grants.clear(), nf.codes.clear(), nf.access.clear(), nf.refresh.clear(), nf.calls.clear()
    nf.down = False
    _world.clock.now = 1_800_000_000.0
    _world.auth._sessions._sessions.clear()
    _world.auth._sessions._used_assertions.clear()
    _world.auth._bundles.clear()
    _world.auth._logins.clear()
    _world.client.cookies.clear()
    return _world


# ---------------------------------------------------------------- configuration


def test_refuses_to_boot_with_incomplete_or_unsafe_configuration(tmp_path):
    base = {
        "PASHUPAT_AUTH_MODE": "novaforge", "PASHUPAT_AUTH_ISSUER": ISSUER, "PASHUPAT_AUTH_CLIENT_ID": CLIENT_ID,
        "PASHUPAT_AUTH_CLIENT_SECRET": SECRET, "PASHUPAT_AUTH_AUDIENCE": AUDIENCE, "PASHUPAT_AUTH_REDIRECT_URI": REDIRECT,
        "PASHUPAT_AUTH_FRONTEND_ORIGIN": FRONTEND, "PASHUPAT_AUTH_JOBS_DB": str(tmp_path / "a.db"),
    }
    compose_novaforge_http(base)  # the complete configuration is accepted
    for override, reason in [
        ({"PASHUPAT_AUTH_ISSUER": "http://id.example.com"}, "AUTH_CONFIGURATION_INVALID"),
        ({"PASHUPAT_AUTH_CLIENT_SECRET": "short"}, "AUTH_CONFIGURATION_INVALID"),
        ({"PASHUPAT_AUTH_REDIRECT_URI": f"{FRONTEND}/cb#frag"}, "AUTH_CONFIGURATION_INVALID"),
        ({"PASHUPAT_AUTH_REDIRECT_URI": "https://*.pp.test/cb"}, "AUTH_CONFIGURATION_INVALID"),
        ({"PASHUPAT_AUTH_FRONTEND_ORIGIN": "https://pp.test/path"}, "AUTH_CONFIGURATION_INVALID"),
        ({"PASHUPAT_AUTH_ROLE_MAP": '{"x": "SYSTEM"}'}, "AUTH_CONFIGURATION_INVALID"),
        ({"PASHUPAT_AUTH_COOKIE_SECURE": "false"}, "AUTH_CONFIGURATION_INVALID"),
    ]:
        with pytest.raises(AuthConfigurationError) as info:
            compose_novaforge_http({**base, **override})
        assert info.value.reason == reason, override
    for missing in ("PASHUPAT_AUTH_CLIENT_ID", "PASHUPAT_AUTH_CLIENT_SECRET", "PASHUPAT_AUTH_JOBS_DB", "PASHUPAT_AUTH_ISSUER"):
        broken = {k: v for k, v in base.items() if k != missing}
        with pytest.raises(AuthConfigurationError):
            compose_novaforge_http(broken)


# ---------------------------------------------------------------------- login


def test_login_redirects_to_novaforge_with_pkce_state_nonce_and_a_login_cookie(world):
    response = world.client.get("/auth/login?return_to=/review")
    assert response.status_code == 302
    location = urlsplit(response.headers["location"])
    q = {k: v[0] for k, v in parse_qs(location.query).items()}
    assert f"{location.scheme}://{location.netloc}{location.path}" == f"{ISSUER}/api/oauth/authorize"
    assert q["response_type"] == "code" and q["code_challenge_method"] == "S256" and q["client_id"] == CLIENT_ID
    assert q["redirect_uri"] == REDIRECT and len(q["state"]) >= 40 and len(q["nonce"]) >= 40 and len(q["code_challenge"]) == 43
    assert "code_verifier" not in response.headers["location"]
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("__Host-pp_login=") and "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    assert "Path=/" in cookie and "Domain" not in cookie


def test_full_login_creates_a_session_and_never_exposes_tokens(world):
    world.nf.add_user("sub-w", [world.nf.role("WORKER")], name="Wendy Worker")
    browser = world.login("sub-w")
    session_cookie = [c for c in browser.cookies.jar if c.name == "__Host-pp_session"][0]
    assert len(session_cookie.value) == 43
    me = browser.get("/auth/me").json()
    assert me["authenticated"] and me["active_role"] == "WORKER" and me["name"] == "Wendy Worker"
    assert me["actor_id"].startswith("nf-") and "sub-w" not in me["actor_id"] and "at." not in json.dumps(me)
    assert "job.report_job" in me["permissions"]
    # nothing in any response or cookie carries a token
    for name, value in browser.cookies.items():
        assert not value.startswith("at.") and value.count(".") < 2


def test_authenticated_write_reads_back_with_authenticated_assurance(world):
    world.nf.add_user("sub-w", [world.nf.role("WORKER")])
    browser = world.login("sub-w")
    created = browser.post("/v1/jobs", json=job_body(), headers=CSRF)
    assert created.status_code == 201, created.text
    job_id = created.json()["job_id"]
    got = browser.get(f"/v1/jobs/{job_id}")
    assert got.status_code == 200
    history = browser.get(f"/v1/jobs/{job_id}/history").json()
    actor = history["events"][0]["actor"]
    assert actor["assurance"] == "AUTHENTICATED" and actor["role"] == "WORKER" and actor["actor_id"].startswith("nf-")


def test_return_to_can_never_be_an_external_url(world):
    world.nf.add_user("sub-w", [world.nf.role("WORKER")])
    for evil in ("https://evil.test/", "//evil.test", "/\\evil.test", "javascript:alert(1)"):
        browser = world.new_browser()
        start = browser.get("/auth/login", params={"return_to": evil})
        done = browser.get(world.nf.authorize(start.headers["location"], "sub-w").replace(FRONTEND + "/api/pp", ""))
        assert done.headers["location"] == f"{FRONTEND}/", evil


# ------------------------------------------------------- callback verification


def test_callback_rejects_missing_replayed_and_mismatched_transactions(world):
    world.nf.add_user("sub-w", [world.nf.role("WORKER")])
    # no login cookie at all
    fresh = world.new_browser()
    start = world.client.get("/auth/login")
    callback = world.nf.authorize(start.headers["location"], "sub-w").replace(FRONTEND + "/api/pp", "")
    assert "error?code=login_expired" in fresh.get(callback).headers["location"]
    # a valid callback works once; replaying the same URL does not
    ok = world.client.get(callback)
    assert ok.headers["location"] == f"{FRONTEND}/"
    assert "login_expired" in world.client.get(callback).headers["location"]
    # tampered state
    b = world.new_browser()
    s2 = b.get("/auth/login")
    cb2 = world.nf.authorize(s2.headers["location"], "sub-w").replace(FRONTEND + "/api/pp", "")
    assert "state_mismatch" in b.get(cb2.replace("state=", "state=x")).headers["location"]
    # a response from the wrong issuer (mix-up defence)
    c = world.new_browser()
    s3 = c.get("/auth/login")
    cb3 = world.nf.authorize(s3.headers["location"], "sub-w").replace(FRONTEND + "/api/pp", "").replace(f"iss={ISSUER}", "iss=https://evil.test")
    assert "issuer_mismatch" in c.get(cb3).headers["location"]
    # and with no iss at all
    d = world.new_browser()
    s4 = d.get("/auth/login")
    cb4 = world.nf.authorize(s4.headers["location"], "sub-w").replace(FRONTEND + "/api/pp", "").replace(f"&iss={ISSUER}", "")
    assert "issuer_mismatch" in d.get(cb4).headers["location"]


def test_login_csrf_an_attackers_code_cannot_be_completed_in_a_victims_browser(world):
    world.nf.add_user("attacker", [world.nf.role("WORKER")])
    attacker = world.new_browser()
    start = attacker.get("/auth/login")
    attackers_callback = world.nf.authorize(start.headers["location"], "attacker").replace(FRONTEND + "/api/pp", "")
    victim = world.new_browser()  # holds no matching login transaction
    assert "login_expired" in victim.get(attackers_callback).headers["location"]
    assert victim.get("/auth/me").status_code == 401


def test_access_denied_from_novaforge_is_reported_without_a_session(world):
    browser = world.new_browser()
    start = browser.get("/auth/login")
    denied = world.nf.authorize(start.headers["location"], "nobody", deny=True).replace(FRONTEND + "/api/pp", "")
    assert "error?code=not_authorized" in browser.get(denied).headers["location"]
    assert browser.get("/auth/me").status_code == 401


def test_a_user_with_no_mappable_role_gets_no_session(world):
    world.nf.add_user("sub-x", [world.nf.role("platform_super_admin", perms=["x"])])  # unknown role key
    browser = world.new_browser()
    start = browser.get("/auth/login")
    done = browser.get(world.nf.authorize(start.headers["location"], "sub-x").replace(FRONTEND + "/api/pp", ""))
    assert "not_authorized" in done.headers["location"]
    assert browser.get("/auth/me").status_code == 401


@pytest.mark.parametrize(
    "mutation",
    ["wrong_nonce", "wrong_aud", "wrong_iss", "expired", "wrong_typ", "assertion_token_use", "future_iat", "wrong_alg_key"],
)
def test_forged_or_mismatched_id_tokens_are_rejected(world, mutation, monkeypatch):
    world.nf.add_user("sub-w", [world.nf.role("WORKER")])
    original = world.nf.id_token

    def tampered(grant, *, nonce, **over):
        now = int(world.clock())
        if mutation == "wrong_nonce":
            return original(grant, nonce="not-the-nonce")
        if mutation == "wrong_aud":
            return original(grant, nonce=nonce, aud="another-client")
        if mutation == "wrong_iss":
            return original(grant, nonce=nonce, iss="https://evil.test")
        if mutation == "expired":
            return original(grant, nonce=nonce, iat=now - 1000, exp=now - 700)
        if mutation == "assertion_token_use":
            return original(grant, nonce=nonce, token_use="identity_assertion")
        if mutation == "future_iat":
            return original(grant, nonce=nonce, iat=now + 1000, exp=now + 1300)
        claims = {"iss": ISSUER, "aud": CLIENT_ID, "sub": grant["sub"], "iat": now, "exp": now + 300, "auth_time": now, "jti": "j", "sid": "s", "nonce": nonce,
                  "token_use": "id_token", "principal_type": "user", "account_status": "ACTIVE"}
        if mutation == "wrong_typ":
            return world.nf.sign({"alg": "ES256", "typ": "at+jwt", "kid": world.nf.kid}, claims)
        # signed by a key the JWKS does not publish
        rogue = FakeNovaForge(world.clock)
        rogue.kid = world.nf.kid
        return rogue.sign({"alg": "ES256", "typ": "JWT", "kid": world.nf.kid}, claims)

    monkeypatch.setattr(world.nf, "id_token", tampered)
    browser = world.new_browser()
    start = browser.get("/auth/login")
    done = browser.get(world.nf.authorize(start.headers["location"], "sub-w").replace(FRONTEND + "/api/pp", ""))
    assert "invalid_id_token" in done.headers["location"], mutation
    assert browser.get("/auth/me").status_code == 401


# --------------------------------------------------- requests need a session


def test_unauthenticated_and_forged_requests_are_refused(world):
    assert world.client.get("/v1/jobs").status_code == 401
    assert world.client.get("/v1/jobs").json()["code"] == "UNAUTHENTICATED"
    assert world.client.get("/v1/jobs", cookies={"__Host-pp_session": "A" * 43}).status_code == 401
    assert world.client.get("/v1/jobs", cookies={"__Host-pp_session": "garbage"}).status_code == 401
    assert world.client.post("/optimize", json={}, headers=CSRF).status_code == 401
    assert world.client.post("/recover", json={}, headers=CSRF).status_code == 401
    # an access-token-shaped or id-token-shaped value is not a session
    assert world.client.get("/v1/jobs", headers={"Authorization": "Bearer at.abc"}).status_code == 401


def test_declared_actor_headers_are_refused_not_ignored(world):
    world.nf.add_user("sub-w", [world.nf.role("WORKER")])
    browser = world.login("sub-w")
    declared = {"X-Actor-Id": "AUTHORITY-017", "X-Actor-Role": "AUTHORITY"}
    response = browser.get("/v1/jobs", headers=declared)
    assert response.status_code == 400 and response.json()["code"] == "ACTOR_HEADERS_NOT_ACCEPTED"
    anonymous = world.client.get("/v1/jobs", headers=declared)
    assert anonymous.status_code == 400  # never treated as an identity
    assert world.client.post("/v1/jobs", json=job_body(), headers={**declared, **CSRF}).status_code == 400


def test_state_changing_requests_need_the_csrf_header_and_a_matching_origin(world):
    world.nf.add_user("sub-w", [world.nf.role("WORKER")])
    browser = world.login("sub-w")
    assert browser.post("/v1/jobs", json=job_body()).status_code == 403
    assert browser.post("/v1/jobs", json=job_body()).json()["code"] == "CSRF_REJECTED"
    assert browser.post("/v1/jobs", json=job_body(), headers={**CSRF, "Origin": "https://evil.test"}).status_code == 403
    assert browser.post("/v1/jobs", json=job_body(), headers={**CSRF, "Origin": FRONTEND}).status_code == 201
    assert browser.post("/auth/logout").status_code == 403  # logout is CSRF-protected too


def test_cors_allows_only_the_console_origin_with_credentials(world):
    ok = world.client.options("/v1/jobs", headers={"Origin": FRONTEND, "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == FRONTEND
    assert ok.headers.get("access-control-allow-credentials") == "true"
    evil = world.client.options("/v1/jobs", headers={"Origin": "https://evil.test", "Access-Control-Request-Method": "GET"})
    assert evil.headers.get("access-control-allow-origin") is None


def test_responses_carry_hardening_headers_and_are_not_cacheable(world):
    r = world.client.get("/auth/config")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store" and "default-src 'none'" in r.headers["content-security-policy"]
    assert r.json() == {"mode": "novaforge", "login_path": "/auth/login", "issuer": ISSUER}


# --------------------------------------------------------- authorization


def test_roles_come_from_novaforge_and_the_policy_still_decides(world):
    world.nf.add_user("sub-w", [world.nf.role("WORKER")])
    world.nf.add_user("sub-a", [world.nf.role("AUTHORITY")])
    world.nf.add_user("sub-e", [world.nf.role("ENGINEER")])
    worker, authority, engineer = world.login("sub-w"), world.login("sub-a", world.new_browser()), world.login("sub-e", world.new_browser())
    assert worker.post("/v1/jobs", json=job_body(), headers=CSRF).status_code == 201
    # an authority cannot report a job; an engineer cannot either
    for forbidden in (authority, engineer):
        r = forbidden.post("/v1/jobs", json=job_body(), headers=CSRF)
        assert r.status_code == 403 and r.json()["code"] == "AUTHORIZATION_DENIED"
    # the solver routes need a role that may request optimization
    assert worker.post("/optimize", json={"corridor_id": "X"}, headers=CSRF).status_code == 403
    assert engineer.post("/optimize", json={"corridor_id": "X"}, headers=CSRF).status_code == 422  # authorised; body invalid


def test_the_novaforge_permission_can_only_narrow_never_widen(world):
    # Holding the role is not enough without the permission NovaForge grants for the action...
    world.nf.add_user("sub-w", [world.nf.role("WORKER", perms=["job.read_job"])])
    worker = world.login("sub-w")
    assert worker.post("/v1/jobs", json=job_body(), headers=CSRF).status_code == 403
    # ...and a permission NovaForge grants cannot give a role an action PP's own table withholds.
    world.nf.add_user("sub-e", [world.nf.role("ENGINEER", perms=["job.report_job", "job.commit_block"])])
    engineer = world.login("sub-e", world.new_browser())
    assert engineer.post("/v1/jobs", json=job_body(), headers=CSRF).status_code == 403


def test_resource_scoped_assignments_become_railway_scope(world):
    section = sorted(world.auth.directory._sections)[0]
    world.nf.add_user("sub-in", [world.nf.role("WORKER", f"corridor/{world.auth.directory._corridor}/{section}")])
    world.nf.add_user("sub-other", [world.nf.role("WORKER", "corridor/SOMEWHERE-ELSE/*")])
    world.nf.add_user("sub-junk", [world.nf.role("WORKER", f"corridor/{world.auth.directory._corridor}/no-such-section")])
    ok = world.login("sub-in")
    assert ok.post("/v1/jobs", json=job_body(), headers=CSRF).status_code == 201
    # an assignment naming another corridor or an unknown section grants nothing at all
    for sub in ("sub-other", "sub-junk"):
        b = world.new_browser()
        start = b.get("/auth/login")
        done = b.get(world.nf.authorize(start.headers["location"], sub).replace(FRONTEND + "/api/pp", ""))
        assert "not_authorized" in done.headers["location"], sub


def test_multiple_roles_require_an_explicit_choice_among_granted_roles(world):
    world.nf.add_user("sub-m", [world.nf.role("WORKER"), world.nf.role("AUTHORITY")])
    browser = world.login("sub-m")
    me = browser.get("/auth/me").json()
    assert me["selection_required"] and me["active_role"] is None and sorted(me["roles"]) == ["AUTHORITY", "WORKER"]
    blocked = browser.get("/v1/jobs")
    assert blocked.status_code == 409 and blocked.json()["code"] == "ROLE_SELECTION_REQUIRED"
    # a role the user was not granted cannot be selected
    assert browser.post("/auth/session/role", json={"role": "ENGINEER"}, headers=CSRF).status_code == 403
    assert browser.post("/auth/session/role", json={"role": "SYSTEM"}, headers=CSRF).status_code == 403
    chosen = browser.post("/auth/session/role", json={"role": "WORKER"}, headers=CSRF)
    assert chosen.status_code == 200 and chosen.json()["active_role"] == "WORKER"
    assert browser.post("/v1/jobs", json=job_body(), headers=CSRF).status_code == 201


# ------------------------------------------------------ liveness & revocation


def test_a_role_change_in_novaforge_is_enforced_within_the_staleness_bound(world):
    world.nf.add_user("sub-w", [world.nf.role("WORKER")])
    browser = world.login("sub-w")
    assert browser.post("/v1/jobs", json=job_body(), headers=CSRF).status_code == 201
    # NovaForge now says: authority, not worker
    world.nf.users["sub-w"]["assignments"] = [world.nf.role("AUTHORITY")]
    world.clock.advance(5)  # inside the bound: the cached view still applies, by design
    assert browser.post("/v1/jobs", json=job_body(), headers=CSRF).status_code == 201
    world.clock.advance(30)  # past it: re-validated against NovaForge
    refused = browser.post("/v1/jobs", json=job_body(), headers=CSRF)
    assert refused.status_code in (401, 403, 409), refused.text
    me = browser.get("/auth/me")
    assert me.status_code in (200, 401)
    if me.status_code == 200:
        assert me.json()["roles"] == ["AUTHORITY"]


def test_removing_all_access_ends_the_session(world):
    world.nf.add_user("sub-w", [world.nf.role("WORKER")])
    browser = world.login("sub-w")
    assert browser.get("/v1/jobs").status_code == 200
    world.nf.users["sub-w"]["assignments"] = []
    world.clock.advance(31)
    assert browser.get("/v1/jobs").status_code == 401
    assert browser.get("/auth/me").status_code == 401  # and it stays ended


@pytest.mark.parametrize("cause", ["grant_revoked", "user_disabled"])
def test_a_revoked_session_stops_working_at_the_next_check(world, cause):
    world.nf.add_user("sub-w", [world.nf.role("WORKER")])
    browser = world.login("sub-w")
    assert browser.get("/v1/jobs").status_code == 200
    if cause == "grant_revoked":
        for g in world.nf.grants.values():
            g["revoked"] = True
    else:
        world.nf.users["sub-w"]["active"] = False
    world.clock.advance(31)
    assert browser.get("/v1/jobs").status_code == 401
    assert browser.get("/v1/jobs").status_code == 401


def test_tokens_are_refreshed_and_rotated_before_they_expire(world):
    world.nf.add_user("sub-w", [world.nf.role("WORKER")])
    browser = world.login("sub-w")
    first_refresh = next(iter(world.nf.refresh))
    for _ in range(30):  # 30 x 31 s ~ 15 min: past the 600 s access-token lifetime
        world.clock.advance(31)
        assert browser.get("/v1/jobs").status_code == 200
    assert world.nf.refresh[first_refresh]["used"] is True  # rotated
    assert len(world.nf.refresh) > 1 and world.nf.calls.count("/api/oauth/token") >= 2


def test_novaforge_being_unreachable_fails_closed_without_ending_the_session(world):
    world.nf.add_user("sub-w", [world.nf.role("WORKER")])
    browser = world.login("sub-w")
    world.nf.down = True
    world.clock.advance(31)
    down = browser.get("/v1/jobs")
    assert down.status_code == 503 and down.json()["code"] == "AUTH_PROVIDER_UNAVAILABLE"
    assert browser.post("/v1/jobs", json=job_body(), headers=CSRF).status_code == 503
    world.nf.down = False  # recovered: the same session continues
    assert browser.get("/v1/jobs").status_code == 200


def test_session_is_bounded_by_idle_and_absolute_limits(world):
    world.nf.add_user("sub-w", [world.nf.role("WORKER")])
    browser = world.login("sub-w")
    world.clock.advance(31 * 60)  # idle 30 min
    assert browser.get("/v1/jobs").status_code == 401
    browser = world.login("sub-w", world.new_browser())
    for _ in range(13):  # active use for > 12 h (absolute lifetime)
        world.clock.advance(3500)
        status = browser.get("/v1/jobs").status_code
        if status == 401:
            break
    assert status == 401


# ----------------------------------------------------------------- logout


def test_logout_ends_the_session_revokes_at_novaforge_and_returns_the_end_session_url(world):
    world.nf.add_user("sub-w", [world.nf.role("WORKER")])
    browser = world.login("sub-w")
    stolen_cookie = dict(browser.cookies.items())
    out = browser.post("/auth/logout", headers=CSRF)
    assert out.status_code == 200
    url = urlsplit(out.json()["logout_url"])
    q = parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}{url.path}" == f"{ISSUER}/api/oauth/logout"
    assert q["post_logout_redirect_uri"] == [f"{FRONTEND}/"] and q["client_id"] == [CLIENT_ID] and "id_token_hint" in q and "state" in q
    assert all(g["revoked"] for g in world.nf.grants.values())
    assert browser.get("/v1/jobs").status_code == 401
    # a copied cookie is worthless after logout
    replay = world.new_browser()
    assert replay.get("/v1/jobs", cookies=stolen_cookie).status_code == 401
    assert browser.post("/auth/logout", headers=CSRF).status_code == 200  # idempotent


def test_a_new_session_handle_is_minted_at_every_login(world):
    world.nf.add_user("sub-w", [world.nf.role("WORKER")])
    seen = set()
    for _ in range(3):
        b = world.login("sub-w", world.new_browser())
        seen.add(dict(b.cookies.items())["__Host-pp_session"])
    assert len(seen) == 3
    # a pre-planted cookie is never adopted (no fixation): login ignores it and issues its own
    victim = world.new_browser()
    victim.cookies.set("__Host-pp_session", "F" * 43, domain="pp.test", path="/")
    world.login("sub-w", victim)
    assert dict(victim.cookies.items())["__Host-pp_session"] != "F" * 43


# --------------------------------------------------------------- demo mode


def test_demo_mode_is_untouched_by_the_integration():
    demo = TestClient(build_app(None))
    assert demo.get("/auth/config").json() == {"mode": "demo"}
    assert demo.get("/auth/me").status_code == 404
    assert demo.get("/health").status_code == 200
