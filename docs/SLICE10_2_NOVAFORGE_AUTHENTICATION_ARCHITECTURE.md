# Slice 10.2 — NovaForge Front-Channel Authentication: Architecture & Security Gate

| | |
|---|---|
| **Type** | Read-only architecture / security audit. No code, test, schema or config changed. |
| **Pashupatastra baseline** | `09d77b966d7018e4f8a4e3ca033ef29864f8eb02` (D-3, pushed; `main == origin/main`) |
| **NovaForge evidence** | Local checkout `D:\NovaForge` at `5f3a2a6cd999286c032dce44a921132fe6d15e9f` (`main`). Inspected read-only. It already had a local modification to `server/prisma/migrations/migration_lock.toml`, which this audit left alone. No fetch was performed, so whether this matches NovaForge `origin/main` is **UNVERIFIED**. |
| **Date** | 2026-09-23 |

### Label legend (used throughout)

- **EXISTS / VERIFIED**: read in source at the commit above, with a file:line citation. For NovaForge tests it means *present in source*. They were **not executed** by this audit.
- **MISSING**: looked for and not found.
- **PROPOSED**: this document's recommendation. It does not exist yet.
- **UNVERIFIED**: claimed by history or reasonably assumed, but not supported by inspectable source or configuration.

---

## 0. Executive verdict: **AMBER**

The Pashupatastra side is structurally ready. There is exactly one actor seam (`request_actor`). A fail-closed resource-aware `EnforcingPolicy` exists and looks up scope from a server-side directory **per call**. `Person` / `ExternalIdentity` are keyed by `(provider, subject)`. Scope is explicit, with no wildcards, and corridor-wide actions require the whole corridor. The job audit trail already records assurance per event.

The NovaForge side is half-built, and the missing half is not the part the brief assumed. **This is the central finding.**

> `POST /internal/identity-assertions` takes the **user's NovaForge access token** as input (`internal.ts:30,41`). That token is an HS256 JWT signed with a secret only NovaForge holds (`jwt.ts:41-48`). It is delivered only as a host-only `httpOnly` cookie on the NovaForge origin (`auth.ts:49-63`). **Pashupatastra has no legitimate way to obtain it.** The existing assertion endpoint therefore cannot be the output of a front-channel flow as it stands. The front-channel slice is not "put `/authorize` in front of the existing endpoint". NovaForge needs a **code-exchange path that mints the assertion from its own session**. The code must be bound to the relying service, an exactly-registered `redirect_uri`, a PKCE challenge and a nonce. `ServiceAccount` has no redirect-URI field (`schema.prisma:281-290`), so this is a NovaForge schema change.

A second, independent NovaForge gap: `/internal/introspect` re-verifies the **assertion**, including `exp` (`internal.ts:83` → `identityAssertionVerifier.ts:72-78`). An assertion lives 300 s (`identityAssertion.ts:13`). After that, introspection reports `active:false` for a perfectly live user. **No existing endpoint lets a relying party check session liveness beyond 300 s.** So Pashupatastra cannot keep a session longer than 5 minutes and still learn about revocation or disablement. This needs a joint contract (§8).

**Blockers keeping this from GREEN:**

| # | Blocker | Owner |
|---|---|---|
| B-1 | No authorization-code / PKCE / redirect-URI / nonce flow in NovaForge. The existing assertion endpoint requires a token the relying party cannot hold. | NovaForge |
| B-2 | No relying-party session-liveness check beyond the 300 s assertion TTL. | Joint |
| B-3 | No mode exclusivity in Pashupatastra. Declared `X-Actor-*` headers and `UnenforcedPolicy` must become impossible in an authenticated deployment. Today `JobService()` is constructed at import time with `UnenforcedPolicy` (`router.py:116`, `service.py:669`). | Pashupatastra |
| B-4 | Audit ambiguity. `optimization_runs.actor` is a bare string with no assurance (`audit/models.py:50`, `audit/repository.py:54`), and idempotency is keyed on bare `actor_id` (`idempotency.py:87`). Once `actor_id == person_id`, a declared caller and an authenticated person are indistinguishable in those two places. | Pashupatastra |
| B-5 | The directory is incomplete for authenticated use:<br>• no `(provider, subject) → Person` lookup;<br>• no refusal of duplicate `ExternalIdentity` across Persons (`StaticScopeDirectory` checks duplicate `person_id` only, `policy.py:116-125`);<br>• no `RoleAssignment` collection at all (`policy.py:109-130` takes only `RoleScopeAssignment`s), so `select_role` has nothing to select from and role gating depends entirely on a new selection step. | Pashupatastra |

None of these requires changing any 10.1A–10.1D.3 semantic.

---

## 1. Current-state evidence

### 1.1 Pashupatastra (EXISTS / VERIFIED at `09d77b9`)

| Fact | Evidence |
|---|---|
| `IdentityAssurance` = `SYSTEM_INTERNAL`, `DECLARED_UNVERIFIED`, `NONE`. No `AUTHENTICATED` member. | `backend/app/identity/actor.py:86-97` |
| A human role **must** carry `DECLARED_UNVERIFIED`. This hard invariant has to change for authentication. | `actor.py:188-194` |
| A SYSTEM actor must be `SYSTEM_INTERNAL` with an id prefixed `SYSTEM`, and humans may not use that prefix. | `actor.py:161-178` |
| The one HTTP actor seam: `X-Actor-Id`/`X-Actor-Role`. Both absent gives UNIDENTIFIED. A declared SYSTEM role gets 403. | `backend/app/api/deps.py:29-95` |
| Every `/v1` route takes `Depends(request_actor)`: 18 `@router.*` decorators, and 18 `Depends(request_actor)`. | `backend/app/jobs/router.py:272 … 1020` |
| Composition root: `service = JobService()` at import time. The default policy is `UnenforcedPolicy`. | `router.py:116`; `backend/app/jobs/service.py:669` |
| Construction guard: an enforcing policy without `authorize_resource` cannot be installed. | `authorization.py:297-330`; `service.py:798` |
| `EnforcingPolicy` (fail-closed role/action + section/corridor scope). **Not wired.** | `backend/app/identity/policy.py:150-357` |
| Scope is looked up **per decision** from an injected directory, never from the request. | `policy.py:253`; module doc `policy.py:19-23` |
| `StaticScopeDirectory` is keyed by `actor_id == person_id`. It is in-memory and static. It refuses duplicate `person_id` but not duplicate `ExternalIdentity`. It takes **only `RoleScopeAssignment`s**, with no `RoleAssignment` collection. So it cannot represent a scope-free role such as ADMIN, and cannot feed `select_role`. `EnforcingPolicy` never consults `RoleAssignment`. | `policy.py:95-147` (constructor `109-130`) |
| `ExternalIdentity(identity_provider, subject)` is case-sensitive, never normalised, and 1:1 with `Person`. `person_id` must be a legal actor_id. | `backend/app/identity/person.py:78-111`; 1:1 recorded in `docs/SLICE10_1_RAILWAY_IDENTITY_SCOPE_READINESS.md:550` |
| `RoleAssignment(person_id, role)` over human roles only. `select_role` treats the requested role as a **selector among held assignments**, and refuses ambiguity. | `backend/app/identity/assignments.py:80-190` |
| `RoleScopeAssignment`: ADMIN can never carry scope. Scopes do not merge across roles. | `backend/app/identity/scope_assignment.py:49-80,113-141` |
| Role → action table. ADMIN, SYSTEM and UNIDENTIFIED hold nothing. | `backend/app/identity/role_actions.py:89-118` |
| Corridor-scoped actions require corridor-complete scope against server-side topology. | `policy.py:315-357`; `role_actions.py:125-130` |
| `job_events` stores `actor_id, actor_role, actor_kind, actor_assurance` as untyped TEXT with no CHECK constraint, so a new enum value needs no schema change. The table is append-only guarded. | `backend/app/jobs/history.py:57-93` |
| `optimization_runs.actor` is a single TEXT. The v1 pipeline writes `requester.actor_id` only, with no role and no assurance. | `backend/app/audit/repository.py:54`; `backend/app/jobs/optimization.py:562` |
| The idempotency namespace is `(actor_id, idempotency_key)`, and its own docstring says it is scoped to the DECLARED id. | `backend/app/jobs/idempotency.py:34-39,87-95` |
| Legacy `/optimize` is stateless (D-3). `/recover` delegates to `simulate_disruption`. `backend/app/simulation/` contains no sqlite, repository, audit or file-write reference (static grep only, not executed). | `backend/app/api/routers/optimize.py:1-29`; `recover.py:24-35` |
| CORS is `allow_origins=["*"]` with `allow_headers=["*"]` and no `allow_credentials`. Browsers **will not** send cookies under this configuration. | `backend/app/api/main.py:38-43` |
| The frontend calls **only** the legacy `/optimize` and `/recover`, cross-origin through `NEXT_PUBLIC_API_URL`. It never calls `/v1` and never sends actor headers (0 matches for `actor` in `frontend/src`). | `frontend/src/lib/data.ts:42,78,231` |
| No JWT or crypto library is available. In the interpreter checked (`C:\Users\Aryan\AppData\Local\Programs\Python\Python311`, 3.11.9, which has `fastapi` 0.141.1), `jwt`, `jose` and `cryptography` each raise `ModuleNotFoundError`. `requirements.txt` lists none of them. | `requirements.txt` |
| **Seven** tests pin the `IdentityAssurance` member set or the absence of `AUTHENTICATED`. The earlier readiness doc said three. | `test_actor_identity.py:89`, `test_slice7_contract_freeze.py:748`, `test_slice9_safety.py:371`, `test_slice10_1_person_identity.py:372`, `test_slice10_1_role_assignment.py:607`, `test_slice10_1d_security.py:489`, `test_slice10_1d_2_collection_scope.py:994` |
| Seventeen test files send `X-Actor-*` headers (69 occurrences). | `backend/tests/*` |
| **Correction to the prior readiness doc:** it mentions `AssignmentBasis`, `VerifiedPrincipal` and `ActiveAssignment`. None of these exists in code (0 matches). They are PROPOSED names only. | grep over `backend/` |

### 1.2 NovaForge (EXISTS / VERIFIED in source at `5f3a2a6`; tests present, not executed)

| Capability | Status | Evidence |
|---|---|---|
| `POST /internal/identity-assertions` (service credential, scope `identity:assert`). Input is the **user access token**. Checks session live and user ACTIVE. | EXISTS | `server/src/routes/internal.ts:30-66` |
| ES256 assertion, `typ: nf-identity+jwt`, `kid`, `iss`, `aud` = the **authenticated service's** audience (never caller-chosen), `sub` = user id, `jti`, TTL 300 s. Claims: `token_use`, `principal_type`, `sid`, `role`, `account_status`, `auth_time`, `trust_level`. | EXISTS | `server/src/lib/identityAssertion.ts:13,19-53` |
| Issuer = `IDENTITY_ISSUER`, else `env.rpOrigin` (the default is `http://localhost:5173`). | EXISTS | `identityAssertion.ts:15-17`; `server/src/lib/env.ts:23` |
| Reference verifier "written the way Pashupatastra must verify": alg pinned to ES256, `typ` checked, `kid` must be in JWKS, P-256 only, exact `iss` and single-string `aud`, requires `sub/sid/jti/role/exp/iat`, lifetime ≤ `maxLifetimeSec` (default 600), 30 s clock tolerance, rejects future `iat`, `principal_type == user`, `account_status == ACTIVE`. **No `jti` replay cache.** | EXISTS | `server/src/lib/identityAssertionVerifier.ts:52-99` |
| `POST /internal/introspect` (scope `identity:introspect`). Re-verifies the assertion **including exp**, then checks the session is live and the user ACTIVE. Returns `{active, sub, sid, aud, exp, role, accountStatus}`. Inactive results carry no reason. | EXISTS | `internal.ts:68-106` |
| JWKS at `/.well-known/jwks.json` and `/api/.well-known/jwks.json`, `Cache-Control: max-age=300`. Current key plus `IDENTITY_PREVIOUS_PUBLIC_KEYS` for rotation. | EXISTS | `server/src/app.ts:56-69`; `server/src/lib/identityKeys.ts:69-112` |
| Production refuses to issue without a configured key. **Dev generates an ephemeral key on every process start**, so `kid` changes on restart. | EXISTS | `identityKeys.ts:95-100` |
| Service auth: `Bearer nfsvc_<uuid>.<secret>`, stores only SHA-256 of the secret, timing-safe comparison, must be ACTIVE, unrevoked and unexpired, per-scope. Scopes are `identity:assert` and `identity:introspect` **only**. | EXISTS | `server/src/lib/serviceAuth.ts:16-88` |
| `ServiceAccount { name, audience @unique, status, scopes }`. **No `redirect_uris`, no client type, no PKCE policy.** | EXISTS (fields) / MISSING (RP registration) | `server/prisma/schema.prisma:281-303` |
| A provisioning script already anticipates `--name pashupatastra --audience pashupatastra`. | EXISTS | `server/scripts/create-service-account.ts:6` |
| Session tokens: HS256, `iss: novaforge`, `aud: novaforge:session`, access 15 m, refresh 30 d, typed claims. Never accepted downstream. | EXISTS | `server/src/lib/jwt.ts:14-56` |
| Browser session cookies `accessToken` and `refreshToken`: `httpOnly`, `SameSite=Lax`, `secure` iff the origin is https, **no `domain`**, so host-only. | EXISTS | `server/src/routes/auth.ts:43-63` |
| `isSessionLive(sid, sub)`: the session row exists and is unexpired, and the user is ACTIVE. Used by REST, sockets and assertion issuance. | EXISTS | `server/src/middleware/requireAuth.ts:39-48` |
| Disabling or suspending a user deletes their sessions. Status is authoritative even if a session survives. | EXISTS | `server/src/routes/users.ts:200`; `requireAuth.ts:45` |
| Logout deletes the session row and clears cookies. It requires the NovaForge cookie, so a relying party cannot call it on the user's behalf. | EXISTS | `auth.ts:1029-1044` |
| `User.id` is `uuid()`. `email` is `@unique`. `Role` = `SUPER_ADMIN, ADMIN, SENIOR_APPROVER, APPROVER, MEMBER`. `AccountStatus` = `ACTIVE, SUSPENDED, DISABLED`. Users can be hard-deleted. | EXISTS | `schema.prisma:10-26,54-66`; `users.ts:322-354` |
| Forgery/security matrix: attacker key reusing a trusted kid, `alg=none`, HS256 key confusion, session token presented as an assertion, wrong iss/aud, expired, excessive lifetime, wrong token type and non-ACTIVE principal, audience isolation between services, rotation, prod-without-key, service credential cannot reach user routes, identity headers do not obtain assertions. | EXISTS in source, **not executed** | `server/test/p0-6-identity-trust.test.ts:255-461` |
| `/authorize`, authorization code, PKCE, `state`, login `nonce`, redirect/callback, `iss` response parameter, RP-initiated or back-channel logout, OIDC discovery. | **MISSING** | Case-insensitive grep for `oauth\|/authorize\|code_challenge\|pkce\|redirect_uri\|redirectUri\|nonce` found 0 files in `client/src` and 0 matches for `oauth\|redirect_uri\|redirectUri\|code_challenge` in `schema.prisma`. In `server/src` it matched only 3 files, and all are unrelated `nonce` uses: WebAuthn approval-signature nonce in `approvalSignature.ts:37,53,86` and `approvals.ts:99`, AES-GCM IV in `totpSecretCrypto.ts:16`. |
| `/internal/*` is mounted on the public app under `/api/internal` (and bare `/internal` when no client build is present). The comment says it is "intended to be reachable only from the private network". | EXISTS; network isolation **UNVERIFIED** | `app.ts:94-105`; `internal.ts:11-13` |

### 1.3 Baseline integrity (this audit)

- HEAD `09d77b966d7018e4f8a4e3ca033ef29864f8eb02`. The pre-existing untracked set is unchanged. `stash@{0}` is unchanged.
- The `jobs.db` SHA-256 computed before and after this audit is `245fd9ff9a9861af6ada9e6c53eb7ea2170493499b7b4386ef0634069e852386`. The reference string supplied in the brief is 61 characters, so it is not a valid SHA-256 and **does not match**. The file was never opened for writing. The reference value should be corrected at its source.

---

## 2. Trust boundary

```
 ┌──────────────┐  (1) top-level GET /auth/login          ┌───────────────────────────────┐
 │   Browser    │ ──────────────────────────────────────► │ Pashupatastra BACKEND (RP)    │
 │ (untrusted)  │ ◄────────── 302 to NovaForge ────────── │  • mint state, PKCE verifier, │
 │              │   Set-Cookie: __Host-pp_login (tx id)   │    nonce; store tx server-side│
 │              │                                         └───────────────────────────────┘
 │              │  (2) top-level GET /oauth/authorize?client_id&redirect_uri&state
 │              │      &code_challenge(S256)&nonce   [NovaForge Lax cookie IS sent]
 │              │ ──────────────────────────────────────► ┌───────────────────────────────┐
 │              │                                         │ NovaForge (IdP)               │
 │              │   passkey / TOTP login if no session ◄► │  T1 authenticates the human   │
 │              │                                         │  T2 validates client +        │
 │              │ ◄── 302 redirect_uri?code&state&iss ─── │     exact redirect_uri        │
 │              │                                         │  T3 binds code to (svc, uri,  │
 │              │  (3) top-level GET /auth/callback       │     challenge, nonce, sid)    │
 │              │ ──────────────────────────────────────► └──────────────▲────────────────┘
 │              │                                         ┌──────────────┴────────────────┐
 │              │                                         │ Pashupatastra BACKEND         │
 │              │                                         │  T4 state == tx.state (once)  │
 │              │                                         │  T5 iss param == pinned iss   │
 │              │       (4) back channel, service cred:   │  ── POST code-exchange ──────►│NF
 │              │           code + verifier + uri         │  ◄── ES256 identity assertion │
 │              │                                         │  T6 verify sig/kid/alg/typ/   │
 │              │                                         │     iss/aud/exp/lifetime/nonce│
 │              │                                         │  T7 (iss,sub) → Person        │
 │              │ ◄─ Set-Cookie: __Host-pp_session ────── │  T8 new server-side session   │
 │              │     (opaque id, rotated at login)       │     {iss,sub,sid,person_id}   │
 └──────┬───────┘                                         └───────────────────────────────┘
        │ (5) every API call (same-origin, cookie)
        ▼
 ┌──────────────────────────────────────────────────────────────────────────────────────┐
 │ Pashupatastra BACKEND per request                                                    │
 │  T9  CSRF: Origin allowlist + custom header on non-GET                               │
 │  T10 X-Actor-* present? → reject (novaforge mode)                                    │
 │  T11 session lookup → freshness (NovaForge liveness, bounded staleness ≤ 300 s)      │
 │  T12 IdentityDirectory: person_id → RoleAssignment (active role re-selected)          │
 │  T13 Actor(person_id, role, AUTHENTICATED)  ← ONLY constructor for this assurance     │
 │  T14 EnforcingPolicy.authorize(actor, action)          role/action table              │
 │  T15 resource resolution → ResourceLocation            server data only               │
 │  T16 EnforcingPolicy.authorize_resource(...)           directory.scopes_for_actor     │
 │       └─ RoleScopeAssignment → RailwayScope, topology (SectionRegistry)               │
 │  T17 lifecycle + append-only audit (job_events actor + assurance + auth context)      │
 └──────────────────────────────────────────────────────────────────────────────────────┘
```

**Where each trust decision happens**

| Decision | Made by | Input it may trust | Input it must never trust |
|---|---|---|---|
| T1 Is this a real human with a live credential? | NovaForge | Its own passkey/TOTP ceremony | Anything from Pashupatastra |
| T2/T3 Which relying party may receive a code, and where it may go | NovaForge | Its RP registration (PROPOSED fields) | The `redirect_uri` query value except as an exact-match key |
| T4/T5 Is this callback the answer to *our* request? | PP backend | Server-side login transaction | Query `state` beyond equality comparison. Any redirect target in the query. |
| T6 Did NovaForge say this, for us, just now? | PP backend | Pinned `iss`, own `aud`, NovaForge JWKS | Header `alg`/`jku`/`x5u`. Token-embedded keys. |
| T7 Which Person is this? | PP backend | `(pinned-provider-alias, sub)` | `email`, `name`, NovaForge `role`, any client value |
| T8/T11 Is the session still valid? | PP backend (+ NovaForge liveness) | Server-side session row; NovaForge liveness answer | Client-held expiry, client-held claims |
| T12 Which railway role? | PP backend | Pashupatastra directory (`RoleAssignment`) | NovaForge `role` claim; client "claims" (a role *selector* is allowed, see §6) |
| T16 Which sections/corridors? | PP backend | `RoleScopeAssignment` + server topology | Any request field (already enforced, `policy.py:19-23`) |
| T17 Who is recorded? | PP backend | The T13 Actor only | Headers, bodies, the frontend |

The Pashupatastra **frontend** makes no trust decision. It is a display surface. It never holds an assertion, a person_id it could assert, a role, or a scope it could assert.

---

## 3. Authentication protocol analysis

### 3.1 Why a code-exchange flow is required (not optional)

| Candidate | Can PP obtain it? | Why / why not |
|---|---|---|
| NovaForge access-token cookie | **No** | Host-only on the NovaForge origin (`auth.ts:51-56`, no `domain`). HS256 with a NovaForge-only secret (`jwt.ts:27-48`). Sharing the secret would let PP mint NovaForge sessions. |
| `POST /internal/identity-assertions` | **No** | Requires that same access token as input (`internal.ts:30-41`). |
| `POST /internal/introspect` | Only with an assertion already in hand | Cannot bootstrap a login. |
| **PROPOSED** code → assertion exchange | Yes | NovaForge reads its own cookie during a top-level navigation (Lax permits it) and hands PP a one-time code. PP redeems it over the back channel with its service credential and PKCE verifier. |

### 3.2 PROPOSED NovaForge contract (joint; shaped as an OIDC subset so it can grow into full OIDC)

**Authorize**: `GET {NF}/api/oauth/authorize`

| Parameter | Rule |
|---|---|
| `response_type` | Must be `code`. Anything else is an error shown **on NovaForge** (never redirected). |
| `client_id` | Identifies a `ServiceAccount` that is ACTIVE and has the PROPOSED `redirectUris` and a new scope `identity:authorize`. Unknown → error on NovaForge, no redirect. |
| `redirect_uri` | **Byte-exact** match to one registered URI. No prefix match, wildcard, or normalisation. Mismatch → error on NovaForge, **never redirect** (open-redirect / code-theft defence). |
| `state` | Required. 1–512 printable chars. Echoed verbatim. NovaForge does not interpret it. |
| `code_challenge`, `code_challenge_method` | Required. `S256` only (`plain` refused). The challenge must be 43 base64url chars. |
| `nonce` | Required. Bound into the code and echoed as a `nonce` claim in the resulting assertion. |
| No NovaForge session | NovaForge's own login, then resume the **same authorize request**. The resume target must be an internal NovaForge path, never a caller URL. |
| Success | Mint 256-bit random `code`, store **only its hash** with `{serviceAccountId, redirectUri, codeChallenge, nonce, userId, sessionId, expiresAt ≤ 60 s, consumedAt null}`, then `302 redirect_uri?code=…&state=…&iss=…` (RFC 9207 `iss` for mix-up defence). |
| User denies / any error after client+uri validated | `302 redirect_uri?error=…&state=…&iss=…`. No detail beyond an error code. |

**Exchange**: `POST {NF}/api/internal/authorization-codes/exchange`, `Authorization: Bearer nfsvc_…` (new scope `identity:exchange`)

| Check (all or nothing, in a single transaction) | Failure |
|---|---|
| Service credential valid, ACTIVE, has the scope | 401/403 |
| Code hash exists, `consumedAt IS NULL`, not expired. **Atomically** set `consumedAt`. | `invalid_grant` |
| Code's `serviceAccountId` == authenticated service | `invalid_grant` |
| Body `redirect_uri` == code's `redirectUri` (exact) | `invalid_grant` |
| `BASE64URL(SHA256(code_verifier)) == codeChallenge` (constant-time), verifier 43–128 chars | `invalid_grant` |
| `isSessionLive(sessionId, userId)` and user ACTIVE | `invalid_grant` |
| Replay of an already-consumed code | `invalid_grant`. Also revoke the grant created by the first redemption (RFC 6749 §4.1.2). |

Success returns `{ assertion, expiresAt, tokenType: "nf-identity+jwt", grantId }`. The assertion is the **existing** ES256 format (`identityAssertion.ts:33-51`) plus a `nonce` claim. `aud` stays derived from the service (`internal.ts:57`).

**Liveness**: see §8.3.

### 3.3 State / PKCE / redirect / CSRF analysis

| Control | Design | Owner | Status |
|---|---|---|---|
| `state` generation | 256-bit CSPRNG, base64url. Stored server-side in a login transaction keyed by an opaque id in `__Host-pp_login` (`HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=600`). `Lax`, not `Strict`: the callback is a cross-site top-level navigation, and `Strict` would drop the cookie. | PP | PROPOSED |
| `state` validation | Constant-time equality with the transaction's value. The transaction is **deleted on first use** (success or failure). A missing cookie or missing transaction is a hard failure. | PP | PROPOSED |
| Login CSRF | Prevented by binding `state` to the browser's own transaction cookie. An attacker's code+state pair cannot be completed in the victim's browser because the victim holds no matching transaction. | PP | PROPOSED |
| PKCE | S256 only. The verifier (43–128 chars, 256-bit entropy) lives **only** in the server-side transaction and never reaches the browser. The challenge goes to NovaForge. | PP + NF | PROPOSED |
| Redirect URI | One exact registered URI per environment. PP sends the same string in authorize and exchange. NovaForge compares byte-exact at both points. | NF (+ PP config) | PROPOSED |
| Open redirect (PP) | Post-login destination is **not** a query parameter. If "return to page" is wanted, store a same-origin **path** (must start with `/`, must not start with `//` or `/\`) in the transaction at `/auth/login`. | PP | PROPOSED |
| Open redirect (NF) | Invalid client or URI → error page, never a redirect. The login-resume target is an internal path. | NF | PROPOSED |
| Code lifetime | ≤ 60 s, single use, only its hash stored. | NF | PROPOSED |
| Code replay | Atomic consume. A second redemption fails and revokes the grant. | NF | PROPOSED |
| Callback replay | The transaction is deleted on first use, so a replayed callback URL finds no transaction. The code is also already consumed. | PP + NF | PROPOSED |
| Mix-up / issuer | PP checks the `iss` query parameter against the pinned issuer before exchange, and checks the assertion's `iss` again. | PP | PROPOSED |
| Nonce | 256-bit, stored in the transaction, must equal the assertion's `nonce` claim. Defence-in-depth against assertion injection, given PKCE is already present. | PP + NF (claim) | PROPOSED; `nonce` claim MISSING in NF |
| Audience | Must equal PP's configured audience (e.g. `pashupatastra`) as a single string. Already enforced by NF's issuance model (`internal.ts:57`). | PP | PROPOSED verification; issuance EXISTS |

### 3.4 Integration-boundary separation

| Category | Items |
|---|---|
| **Pashupatastra can implement independently now** | 10.2a assertion verifier + JWKS cache (testable with a locally generated ES256 key); 10.2b identity directory (`(alias, sub) → Person`, `RoleAssignment` set, duplicate refusal, reload); 10.2c `AUTHENTICATED` assurance + factory + the seven enum pins; 10.2d auth mode, startup validation, composition root, refusing `UnenforcedPolicy`; 10.2g audit enrichment; SEC-2 mode-exclusivity tests; the fake-NovaForge harness for SEC-1 |
| **Requires NovaForge changes** | NF-1 RP registration (`redirectUris`, new scopes); NF-2 `/oauth/authorize` (S256, exact URI, state, nonce, 60 s hashed single-use code, RFC 9207 `iss`); NF-3 code exchange (atomic consume, PKCE, `nonce` claim, `grantId`); NF-4 grant liveness; `IDENTITY_ISSUER` required in production |
| **Requires a joint contract** | Exact endpoint paths, parameter names and error codes (§3.2); `client_id` form (ServiceAccount id vs name); `nonce` claim format; liveness semantics and the staleness bound N (L1, §8.3); issuer string per environment; JWKS URL and rotation window (`IDENTITY_PREVIOUS_PUBLIC_KEYS` retention ≥ assertion TTL + tolerance); redirect URI per environment; service-credential rotation procedure; 10.2e/10.2f/FE-1 depend on all of these |
| **Remains UNVERIFIED** | NovaForge tests passing (read, not executed); `/internal` network isolation (Q9); `User.id` immutability as a contract (Q11); whether `5f3a2a6` matches NovaForge's deployed/remote state (Q12); `/recover` statelessness at runtime (static grep only) |

---

## 4. Token / assertion trust model

| Artifact | Who holds it | What it proves | Use in PP |
|---|---|---|---|
| NovaForge session cookie | Browser, NovaForge origin only | A NovaForge session | **None.** PP never sees or accepts it. |
| NovaForge access token (HS256) | Browser cookie | The same | **Never accepted.** NovaForge's own tests assert it is rejected as an assertion (`p0-6…:316`). |
| OAuth access token | Not issued (no resource API is being delegated) | — | **Not introduced.** PP is not calling NovaForge APIs on the user's behalf, so a delegated access token would be unneeded bearer state. |
| Authorization code | Browser (briefly), then PP backend | Nothing by itself. Worthless without the PKCE verifier and service credential. | Input to the exchange only |
| **Identity assertion (ES256)** | PP backend only, received over the back channel | "NovaForge authenticated `sub` in session `sid` for audience PP, at `iat`" | **The authoritative authentication input.** Verified once at callback, then discarded after its claims are copied into the server-side session. |
| NovaForge liveness answer | PP backend | That session is still live and the user ACTIVE, now | Freshness input only (§8) |
| **PP session cookie** `__Host-pp_session` | Browser | Nothing by itself: an opaque 256-bit handle | Lookup key for the server-side session |
| PP server-side session | PP backend | Holds `{iss, sub, sid, person_id, active_role, created_at, last_verified_at, jti}` | **Source of the per-request Actor**, rebuilt through the directory every request |

**Why the session stores identity, not authority.** The session stores `(iss, sub, sid, person_id)` and a *selected* `active_role`. It never stores scopes or permissions. The Actor is rebuilt per request, and `EnforcingPolicy` already reads scope from the directory per decision (`policy.py:253`). A role or scope revocation therefore takes effect on the next request after the directory changes, with no cache to invalidate. The `active_role` is re-validated through `select_role` against the current assignments on every request. If the role is no longer held, the request is refused and no fallback role is picked.

**Verifier specification (PROPOSED, mirrors `identityAssertionVerifier.ts:52-99`, tightened where marked):**

1. Decode the header. `alg == "ES256"` exactly. `typ == "nf-identity+jwt"`. `kid` must be present. Ignore `jku`, `x5u`, `jwk` and `crit`, and reject if `crit` is present.
2. Key: look up `kid` in the cached JWKS (EC, P-256 only). On an unknown `kid`, refetch once, rate-limited (≤ 1 per 60 s). This is needed because dev NovaForge rotates an ephemeral key on every restart (`identityKeys.ts:98-99`). Still unknown → reject.
3. Verify the signature with `algorithms=["ES256"]`.
4. `iss` == **pinned** configured issuer (exact). NovaForge's issuer defaults to `rpOrigin` (`identityAssertion.ts:16`), so NovaForge deployments **must** set `IDENTITY_ISSUER` explicitly (joint requirement).
5. `aud` is a **string** equal to PP's audience (an array is rejected).
6. `exp`, `iat` present. Now ≤ `exp` + 30 s. `iat` ≤ now + 30 s. **`exp − iat ≤ 300`** (tighter than NovaForge's reference 600, matching issuance TTL).
7. `token_use == "identity_assertion"`, `principal_type == "user"`, `account_status == "ACTIVE"`.
8. `sub`, `sid`, `jti`, `nonce` are non-empty strings. `sub` passes `ExternalIdentity` validation unchanged (`person.py:37-60`, ≤ 256 chars, no strip).
9. `nonce` == the login transaction's nonce.
10. `jti` not seen within `exp + 30 s` (small in-process seen-set). A defence-in-depth replay guard: the reference verifier has none, and the back-channel delivery already makes replay unlikely.
11. `role` and `trust_level` are **recorded only**, never an input to any Pashupatastra decision.

**Explicitly prevented:**

| Attack input | Control |
|---|---|
| Client-supplied actor headers | Rejected outright in NovaForge mode (T10). They are not silently ignored, so a misconfigured client fails visibly. |
| Client-supplied `person_id` | No route accepts it as identity. `person_id` comes only from the directory via `(iss, sub)`. |
| Client-supplied role | Only a **selector** among held assignments (`select_role`, `assignments.py:147-190`), and only through a dedicated role-switch endpoint that re-validates. There is no `X-Actor-Role`. |
| Client-supplied scope | Impossible by construction (`policy.py:19-23`), and unchanged. |
| Forged assertion | ES256 against pinned JWKS. `alg` pinned. `typ`/`iss`/`aud`/lifetime checks. |
| Confused deputy | `aud` is bound by NovaForge to the authenticated service (`internal.ts:57`). The code is bound to the service and URI. PP never forwards its service credential or an assertion to anyone. |

---

## 5. Identity mapping: `(identity_provider, subject) → Person`

| Question | Answer | Basis |
|---|---|---|
| Provider alias | Derived from the **pinned issuer in PP configuration** (`novaforge` ↔ `https://nf.example`), never from a token claim. | PROPOSED; consistent with `person.py:82-84` ("logical alias … not a URL") |
| Subject | NovaForge `User.id`, a `uuid()` (`schema.prisma:55`). Used verbatim, case-sensitive. | EXISTS (format) |
| Is identity immutable? | `ExternalIdentity` and `Person` are frozen values (`person.py:78,97`). No NovaForge route updating `User.id` was found in `routes/users.ts` (role/status/delete only). Immutability of `User.id` is structurally expected, but not guaranteed by a NovaForge contract, so it is **UNVERIFIED as a contract**. | EXISTS / UNVERIFIED |
| Email | **Attribute only**, never an identifier, never a lookup key, never a linking key. Email is mutable and recyclable. Auto-linking by email is an account-takeover vector. | PROPOSED |
| Subject changes (user deleted and re-created) | New `sub` → a new, unknown `ExternalIdentity` → *authenticated but not enrolled* → refused (403 `IDENTITY_NOT_ENROLLED`). Re-linking to the old Person is an explicit administrative directory change. Never automatic. | PROPOSED |
| User disabled/suspended in NovaForge | NovaForge deletes sessions and treats status as authoritative (`users.ts:200`, `requireAuth.ts:45`). PP learns of it at the next liveness check (≤ staleness bound, §8). | EXISTS (NF) / PROPOSED (PP) |
| Person disabled in PP | PROPOSED directory flag or removal. The Person then has no assignments → no actions (fail closed). | PROPOSED |
| Duplicate external identities | **Must be refused** at directory load. `StaticScopeDirectory` currently refuses duplicate `person_id` only (`policy.py:121-125`). | MISSING (B-5) |
| One Person, multiple external identities | **Not supported.** 10.1A established 1:1 (`person.py:10`; readiness doc line 550). This slice does not change that. Multi-IdP linking would be a separate decision. | EXISTS (constraint preserved) |
| Just-in-time provisioning | **None.** An unknown `(iss, sub)` creates no Person and gets no default role. | PROPOSED (fail closed) |
| Actor id | `Actor.actor_id = Person.person_id`. This is exactly why 10.1A made `person_id` a legal actor_id (`person.py:16-20`). | EXISTS (design intent) |

---

## 6. Role mapping: `Person → RoleAssignment`

- **Source of truth: Pashupatastra.** The NovaForge `Role` (`SUPER_ADMIN … MEMBER`, `schema.prisma:10-16`) describes NovaForge's approval product. It has no railway meaning. It is **recorded** in the auth context and **never mapped**: not to ADMIN, not to any railway role, and never to SYSTEM. NovaForge SUPER_ADMIN gets zero railway authority.
- **Who assigns:** a Pashupatastra directory administrator, out of band (configuration file now, PROPOSED). ADMIN in Pashupatastra still holds no job action and no scope (`role_actions.py:115`, `scope_assignment.py:61-66`). Whether ADMIN gains an assignment-management surface is an open question (§16). It is not granted here.
- **Selecting vs claiming.** A multi-role Person must choose which role a request acts under. `select_role` already models this correctly: the requested role is a selector, and the result comes from the held collection (`assignments.py:24-30`). PROPOSED transport: an `active_role` chosen at login (auto when exactly one role is held, required choice when several) and changeable through `POST /auth/session/role`, which re-runs `select_role`. **Selection is allowed. Claiming a role not held is impossible.**
- **Authoritative, not mapped:** assignments are Pashupatastra data. NovaForge provisions nothing railway-related.
- **Directory gap (MISSING, B-5):** `StaticScopeDirectory` holds no `RoleAssignment` set (`policy.py:109-130`), and `EnforcingPolicy` never consults one. Today a role is effectively "held" only if some scope assignment exists for it, and a scope-free role (ADMIN) cannot be represented. 10.2b must add an explicit `RoleAssignment` set to the directory. At load it must refuse any `RoleScopeAssignment` whose `role_assignment` (`scope_assignment.py:76-80`) is absent from that set, so scope can never exist for a role the Person does not hold. `select_role` then runs against that set.
- **Revocation:** removing a `RoleAssignment` or `RoleScopeAssignment` from the directory takes effect on the next request (per-request `select_role` + per-decision `scopes_for_actor`). There is one gap: `StaticScopeDirectory` is built once in memory. A revocation today needs a process restart. PROPOSED: a file-backed directory with validated atomic reload (last-good retained on a parse error, never "empty = allow").
- **Stale sessions:** no role or scope is cached in the session. The only cached thing is `active_role` as a selector, re-validated each request.

---

## 7. Railway scope: `RoleAssignment → RailwayScope`

Preserved without change:

- scope = `corridor_id` + explicit `section_ids`. No wildcard or hierarchy. An empty set is refused (`scope.py:11-18`).
- Corridor-scoped actions (`REQUEST_OPTIMIZATION`, `READ_OPTIMIZATION_RUN`) require corridor-complete scope against server-side topology (`policy.py:315-357`).
- Section-scoped authority actions stay section-scoped (`role_actions.py:137-149`).
- ADMIN carries no scope.

**Source of truth:** Pashupatastra server-side configuration: the `RoleScopeAssignment` set plus topology from `SectionRegistry`. **Never NovaForge, never the request, never the token.** NovaForge has no railway model and must not gain one. Putting scope in NovaForge would move railway authority into a system whose administrators (SUPER_ADMIN) are not railway authorities.

---

## 8. Session and revocation model

### 8.1 Pashupatastra session (PROPOSED)

| Property | Value |
|---|---|
| Handle | 256-bit random, cookie `__Host-pp_session; HttpOnly; Secure; SameSite=Lax; Path=/`. No `Domain`. |
| Fixation | A new id is minted at successful callback. Any pre-existing session cookie is discarded. The login transaction cookie is cleared. |
| Store | Server-side. The first slice may use an in-process store (restart = everyone re-logs in, acceptable). It must **not** live in `jobs.db`: keep auth state out of the append-only operational store. |
| Idle timeout | 30 min (PROPOSED default) |
| Absolute lifetime | ≤ 12 h and ≤ NovaForge session expiry. Never outlives the NovaForge session it was derived from. |
| Freshness | `last_verified_at`. If older than **N ≤ 300 s** (PROPOSED default 60 s), check NovaForge liveness synchronously before serving. On failure, the session is destroyed → 401. |
| NovaForge unreachable during a freshness check | **Fail closed** for state-changing requests. Reads may be allowed only while within N; after that, fail closed too. No "grace mode". |

### 8.2 Revocation matrix

| Event | Effect in PP | Latency |
|---|---|---|
| PP logout (`POST /auth/logout`, CSRF-protected) | Server-side session deleted, cookie cleared | Immediate |
| NovaForge logout / session revoked / expiry | Next liveness check → inactive → PP session destroyed | ≤ N |
| NovaForge user DISABLED / SUSPENDED / deleted | Same (`isSessionLive` checks status) | ≤ N |
| PP Person removed / disabled | Directory lookup fails → 403 / session destroyed | Next request after directory reload |
| Role revoked | `select_role` fails → 403, active role cleared | Next request after reload |
| Scope revoked | `scopes_for_actor` no longer covers → 403 | Next request after reload |
| Stale assertion | Assertions are consumed once at callback and never reused for requests | n/a |
| Replayed assertion | `nonce` mismatch (no transaction), `jti` seen-set, and no endpoint accepts a bare assertion | Immediate |
| Replayed code | NovaForge atomic consume + grant revocation | Immediate |

### 8.3 The liveness gap (B-2) and joint options

`/internal/introspect` cannot serve past 300 s, because it rejects expired assertions (`internal.ts:83`). Options:

| Option | Mechanism | Assessment |
|---|---|---|
| L1 | NovaForge adds `POST /internal/grants/status {grantId}` (scope `identity:introspect`), answering `active` only for a grant **this service** redeemed, from `isSessionLive(sid, sub)` + user ACTIVE. | **Recommended.** A service can query only sessions delegated to it, so there is no enumeration oracle. It reuses NovaForge's authoritative check. |
| L2 | Allow introspect to accept an expired-but-validly-signed assertion within the grant's lifetime | Blurs the meaning of `exp`. Rejected. |
| L3 | PP forces a silent top-level re-authorize every ≤ 300 s | Works with only the authorize and exchange endpoints, but disrupts in-flight UI and form posts. Acceptable only as an interim. |
| L4 | NovaForge back-channel logout push to PP | Good complement to L1, but it is a push over the network and can be lost, so it cannot replace a pull. PROPOSED later. |

RP-initiated logout at NovaForge (ending the NovaForge session from PP) is **MISSING**. It is optional for this slice: PP logout ends PP access, and the NovaForge session is the user's own to end.

---

## 9. Audit integration

| Requirement | Today | Needed |
|---|---|---|
| Every state-changing op has a verified actor | All `/v1` writes take `request_actor` (router.py). Actors are DECLARED or UNIDENTIFIED. | In NovaForge mode, `request_actor` returns only an AUTHENTICATED Actor, or raises 401/403. UNIDENTIFIED is never produced from HTTP. |
| Audit actor comes from the trusted boundary | `job_events` records the Actor passed in (history.py:68-71) | Unchanged mechanism, new source (T13). |
| Headers cannot override | n/a | T10 rejects `X-Actor-*` whenever the mode is `novaforge`. |
| `SYSTEM` reserved | `system_actor` in-process only. HTTP 403 on a declared SYSTEM (`deps.py:85-90`, `actor.py:161-178`). | Unchanged. The AUTHENTICATED factory must refuse SYSTEM and UNIDENTIFIED roles and any `SYSTEM*` id. |
| UNIDENTIFIED cannot act | `role_actions.py:117` grants nothing, but only under `EnforcingPolicy` | NovaForge mode must refuse `UnenforcedPolicy` at startup (B-3), otherwise an AUTHENTICATED actor under `UnenforcedPolicy` is authenticated yet authorized for everything. |
| Append-only preserved | Guards installed (`history.py:93`) | No schema change is needed for `job_events` (TEXT columns, no CHECK). |
| `IdentityAssurance.AUTHENTICATED` | MISSING. The invariant forbids it (`actor.py:188-194`). **Seven** tests pin its absence (§1.1). | Add the member, relax the invariant to `{DECLARED_UNVERIFIED, AUTHENTICATED}` for human roles, and update all seven pins **in one commit**. |
| `optimization_runs.actor` | Bare `actor_id` (`optimization.py:562`), no assurance (**B-4**) | Record `{actor_id, role, assurance, iss, sub, sid}` in `provenance_snapshot_json` (existing column, no schema change). Or add a nullable `actor_assurance` column via additive migration (decision Q5). |
| Idempotency namespace | `(actor_id, key)` (`idempotency.py:87`) (**B-4**) | Mode exclusivity per database: an authenticated deployment uses a DB that never held declared-mode writes. The alternative (assurance in the key derivation) changes `event_id` derivation. Decision Q6. |
| Authentication context on events | none | `metadata_json.auth = {iss, sub, sid, session_ref_hash}` on human events. No schema change. |
| Legacy `SYSTEM_ACTOR = "system:unauthenticated-demo"` | `audit/models.py:27`, default for `record_run` | v1 always passes an actor. Leave the constant. D-3 removed the legacy route's use. |

**Construction discipline (PROPOSED):** an AUTHENTICATED Actor is created by exactly one factory in a new auth module, called only from the NovaForge-mode `request_actor`. A source-scanning test, in the style of the existing 10.1D.1 construction-guard test, asserts that no other module references `IdentityAssurance.AUTHENTICATED`.

---

## 10. Backward compatibility

| Surface | Demo mode (default, unchanged) | NovaForge mode |
|---|---|---|
| Mode selection | `PASHUPAT_AUTH_MODE` unset or `demo` | `PASHUPAT_AUTH_MODE=novaforge`. **Process-level only, never per-request.** Startup validates all required config and fails the boot on any gap. |
| `X-Actor-*` | Exactly as today (`deps.py`) | Rejected with 400 `ACTOR_HEADERS_NOT_ACCEPTED` |
| `UnenforcedPolicy` | Still default | Refused at the composition root (`router.py:116` becomes config-driven). `EnforcingPolicy(directory, topology=SectionRegistry)` is required. |
| Existing tests (17 files with actor headers) | Unaffected: the test suite runs in demo mode | New mode tested separately |
| Enum pins (7 tests) | Must be updated when `AUTHENTICATED` is added. This is a deliberate contract change, done in one commit. | — |
| OpenAPI (`test_slice7_contract_freeze`) | Header parameters unchanged | New `/auth/*` routes are additive. The frozen `/v1` schemas are unchanged. Snapshot regenerated deliberately. |
| Frontend | Unchanged; calls legacy routes cross-origin | Needs the same-origin proxy + `credentials` (FE-1). CORS `*` must become an explicit allowlist with `allow_credentials=True`, or be unnecessary behind a same-origin proxy. |
| Legacy `/optimize` | Stateless, open | Stateless. PROPOSED: require **any** authenticated session (resource-cost protection), with no role or scope, since it touches no stored data. Response contract unchanged. (Q4) |
| `/recover` | Stateless, open | Same treatment as `/optimize`. Static grep shows no persistence in `backend/app/simulation/`. Stateless status by execution is UNVERIFIED. |
| `/health` | Open | Open |
| Synthetic demo workflows / playbook | Unchanged | Not supported in NovaForge mode. Demo continues on demo mode. |
| `AUTHENTICATED` reachable in demo mode | **Impossible**: the factory is only called by the NovaForge-mode dependency | — |

---

## 11. Threat matrix

PP = Pashupatastra, NF = NovaForge. "Status" is the state of the **control**, not the threat.

| # | Threat | Attack | Control | Owner | Status |
|---|---|---|---|---|---|
| 1 | Forged actor header | `X-Actor-Id: P-017`, `X-Actor-Role: AUTHORITY` | Rejected in NovaForge mode (T10). In demo mode it is recorded DECLARED_UNVERIFIED (honest, not trusted). | PP | Demo: EXISTS. NF-mode: PROPOSED |
| 2 | Forged identity assertion | Own key, reused kid, `alg=none`, HS256 key confusion | ES256 pin, JWKS kid lookup, typ/iss/aud checks | PP (mirror NF) | NF reference + tests EXIST (not executed). PP verifier MISSING |
| 3 | Stolen authorization code | Leaked via logs, referrer, history | PKCE S256 (verifier never leaves PP backend), 60 s single-use, service credential required, exact URI binding | NF + PP | PROPOSED |
| 4 | PKCE failure / downgrade | `plain` method, missing challenge | S256 mandatory at authorize. Verifier checked constant-time at exchange. | NF | PROPOSED |
| 5 | State mismatch / login CSRF | Attacker plants their own code in the victim's browser | `state` bound to a server-side transaction via `__Host-` cookie, single use | PP | PROPOSED |
| 6 | Redirect URI manipulation | `redirect_uri=https://evil/…`, suffix/prefix tricks | Byte-exact registered match. Mismatch never redirects. Same URI required at exchange. | NF | PROPOSED (registration field MISSING) |
| 7 | Token replay | Replay assertion or callback | Assertion accepted only at callback, nonce-bound, jti seen-set. Transaction single use. Code atomic consume. | PP + NF | PROPOSED |
| 8 | Expired assertion | Old assertion presented | `exp` + 30 s tolerance, lifetime ≤ 300 | PP | PROPOSED (NF reference EXISTS) |
| 9 | Wrong audience | Assertion minted for service B | `aud` single-string == PP audience. NF binds `aud` to the authenticated service. | PP + NF | NF EXISTS (`internal.ts:57`, test `p0-6…:241`). PP PROPOSED |
| 10 | Wrong issuer / mix-up | Different IdP or NF env | Pinned `iss`. RFC 9207 `iss` param checked before exchange. `IDENTITY_ISSUER` set explicitly. | PP + NF | PROPOSED |
| 11 | Disabled user | Session outlives NF disable | NF deletes sessions + status authoritative. PP liveness ≤ N. | NF + joint | NF EXISTS. PP freshness PROPOSED (needs L1) |
| 12 | Revoked role | Continues acting as AUTHORITY | Per-request `select_role` against the directory. No role cached. | PP | Seam EXISTS. Reload PROPOSED |
| 13 | Revoked scope | Continues acting on a section | Per-decision `scopes_for_actor` (`policy.py:253`) | PP | EXISTS (static). Reload PROPOSED |
| 14 | Confused deputy | PP induced to use its service credential for another party. A NovaForge assertion for PP replayed at another RP. | PP never forwards its credential or assertions. Audience bound per service. Codes bound per service. | PP + NF | PROPOSED / NF audience EXISTS |
| 15 | Privilege escalation via NF role | NF SUPER_ADMIN → railway ADMIN/AUTHORITY | NF `role` recorded only, never mapped | PP | PROPOSED (principle recorded in readiness doc line 315) |
| 16 | Privilege escalation via ADMIN | ADMIN acting operationally | ADMIN holds no action and no scope | PP | EXISTS (`role_actions.py:115`) |
| 17 | Escalation via `UnenforcedPolicy` in prod | Authenticated but unenforced | NovaForge mode refuses it at startup | PP | MISSING (B-3) |
| 18 | Session fixation | Attacker pre-sets the victim's session cookie | New session id at login. `__Host-` prefix prevents subdomain cookie injection. | PP | PROPOSED |
| 19 | Cross-user session confusion | Two tabs or users, one browser | One session per cookie. Login replaces any prior session. Identity comes from the server-side record only. | PP | PROPOSED |
| 20 | Cross-railway-scope access | Corridor A engineer optimizes corridor B | Corridor-complete rule + topology. Collection filtering (10.1D.2). | PP | EXISTS (under EnforcingPolicy) |
| 21 | Frontend/backend identity mismatch | UI shows user X, backend acts as Y | UI displays only `GET /auth/session` from the backend. The frontend sends no identity. | PP | PROPOSED |
| 22 | CSRF on state-changing routes | Cross-site POST with the ambient cookie | `SameSite=Lax` + Origin allowlist + required custom header on non-GET. CORS explicit allowlist. | PP | PROPOSED (today CORS `*`, `main.py:40`) |
| 23 | XSS stealing credentials | Script reads the token | No token in JS: `HttpOnly` opaque cookie only. The assertion never reaches the browser. | PP | PROPOSED (design choice, §12) |
| 24 | Duplicate external identity | Two Persons share `(iss, sub)` → ambiguous actor | Refuse at directory load | PP | MISSING (B-5) |
| 25 | Email-based takeover | Recycled email links to an old Person | Email never an identifier | PP | PROPOSED |
| 26 | Audit/idempotency spoof across modes | Declared `X-Actor-Id: <real person_id>` indistinguishable in `optimization_runs` and the idempotency namespace | Mode exclusivity + separate DB + assurance recorded in run provenance | PP | MISSING (B-4) |
| 27 | JWKS poisoning / fetch abuse | `jku` header, forced refetch storm | Fixed JWKS URL from config, https in prod, refetch rate limit | PP | PROPOSED |
| 28 | `/internal` reachable publicly | Credential stuffing against the service API | Service credential (hash, timing-safe) + rate limit (600/min). Private network isolation. | NF (deploy) | Auth EXISTS. Network isolation UNVERIFIED |
| 29 | Service credential leak (PP side) | Secret in repo/logs | Env or secret store only. Never logged. Rotatable (`revokedAt`, `expiresAt`). | PP + NF | NF rotation EXISTS. PP handling PROPOSED |
| 30 | NovaForge outage | IdP unreachable | Fail closed for new logins and past-staleness requests. No local fallback identity. | PP | PROPOSED |

---

## 12. Architecture options

| Option | Description | Security consequences | Operational consequences |
|---|---|---|---|
| **A. Status quo** | Declared `X-Actor-*` behind a network perimeter | Any caller is anyone. Audit honestly says DECLARED_UNVERIFIED. Not a trust boundary. | Zero work. **Demo only.** |
| **B. Browser holds the assertion** | SPA receives the ES256 assertion and sends it as `Authorization: Bearer` | Bearer in JS → any XSS steals a 300 s identity. Needs a jti replay cache (NovaForge's reference verifier has none). NovaForge would have to release assertions to browsers, which contradicts its "service-only" model (`internal.ts:11-13`). A 300 s TTL forces a re-auth loop in the SPA. | Stateless backend, but more complexity in the frontend. |
| **C. Backend confidential relying party + server-side session** (code + PKCE + back-channel exchange) | §2–§8 | Nothing replayable reaches the browser. PKCE + a service credential double-bind the code. Revocation is bounded by N. Actor is rebuilt per request. | Needs a server-side session store, a liveness contract (L1), CSRF handling, and a same-origin frontend proxy. |
| **D. Shared parent-domain cookie** | PP reads NovaForge's cookie via `Domain=.example` | Breaks NovaForge's host-only property (`auth.ts:51-56`). PP would need the HS256 secret, and with it the ability to **mint NovaForge sessions**. Any subdomain compromise becomes an IdP compromise. | Forces both products onto one registrable domain. **Rejected.** |
| **E. Identity-injecting gateway** | A reverse proxy authenticates and injects `X-User` | Trust rests entirely on network topology (any path around the proxy is spoofable). It is the declared-header problem again, one hop away. Only acceptable with a signed header plus mTLS. | Extra infrastructure. The PP code change is small, but the trust boundary is the deployment. Not recommended now. |
| **F. Full OIDC in NovaForge** | Discovery, `/authorize`, `/token`, `id_token`, `userinfo`, RP logout | Standards-audited semantics, library support in PP. A superset of C. | Largest NovaForge change. C's contract (§3.2) is deliberately an OIDC subset, so C can grow into F without changing PP's trust model. |

**Requirements that decide the choice:** (R1) nothing replayable in browser JS; (R2) the actor is derived only from a cryptographically verified NovaForge statement; (R3) revocation latency is bounded and small; (R4) no shared secrets between products; (R5) no change to 10.1A–10.1D.3 semantics; (R6) demo mode is unchanged.

A violates R2. B violates R1. D violates R4. E violates R2 unless mTLS plus signing is added. **C satisfies R1–R6.** F also does, at higher NovaForge cost.

---

## 13. Recommended target architecture

**Option C**: the Pashupatastra FastAPI backend as a confidential relying party:

1. **Same-origin topology.** The Next.js frontend proxies `/api/*` (including `/api/auth/*`) to FastAPI through rewrites, so the session cookie is first-party on the frontend origin. There is no third-party-cookie dependency and no credentialed CORS. The redirect URI is `https://<frontend-origin>/api/auth/callback`. FastAPI must not trust `X-Forwarded-*` except from the configured proxy. (If the deployment cannot be same-origin, the fallback is an explicit CORS allowlist with `allow_credentials=True` on a same-site subdomain; that is decision Q1.)
2. **NovaForge** adds authorize, code exchange and grant liveness (§3.2, §8.3 L1) and sets `IDENTITY_ISSUER` explicitly.
3. **PP verifies** the assertion once (§4), maps `(pinned-alias, sub) → Person` (§5), selects `active_role` (§6), and creates a server-side session holding identity, never authority (§8).
4. **Per request:** CSRF check → reject `X-Actor-*` → session → freshness (≤ N) → `select_role` → `Actor(person_id, role, AUTHENTICATED)` → `EnforcingPolicy` (existing) → lifecycle and audit (existing), with auth context in metadata.
5. **Mode exclusivity:** `PASHUPAT_AUTH_MODE=novaforge` is process-wide. It refuses `UnenforcedPolicy` and declared headers, requires an authenticated session for `/v1` and the legacy compute routes, and uses a database never written in demo mode.

---

## 14. Explicit non-goals

- Implementing anything in NovaForge from this repository. The NovaForge items are a contract specification.
- Mapping NovaForge roles, trust level or approval quorums to railway authority. Step-up authentication for AUTHORITY commits is a later, separate decision.
- Multi-IdP support or multiple external identities per Person (10.1A stays 1:1).
- JIT provisioning, self-service enrolment, or role requests.
- Railway scope inside NovaForge.
- An ADMIN operational surface, or ADMIN management of assignments.
- A persistent (DB-backed) identity directory. A file-backed, reloadable one is the next step.
- Changing `/v1` request/response schemas, the legacy response contracts, or demo-mode behaviour.
- Segregation-of-duties checks (possible once `actor_id == person_id` is real; a separate slice).
- Service-to-service callers of Pashupatastra. SYSTEM stays in-process only.

---

## 15. Required implementation slices (after this gate; none implemented)

Numbering reconciles with the prior docs (`SLICE10_IDENTITY_AUTHORIZATION_ARCHITECTURE.md` §20, readiness doc lines 45-51): prior 10.2 "verification seam" = **10.2a**; prior 10.3 "AUTHENTICATED actor" = **10.2c + 10.2f**; prior 10.4 "audit metadata" = **10.2g**; prior 10.5 "enforcing policy wiring" = **10.2d**; prior 10.6 "NovaForge integration" = **NF-1…NF-4 + 10.2e + FE-1**.

### NovaForge (separate repository, separate review)

| Slice | Content | Tests |
|---|---|---|
| NF-1 | RP registration: `ServiceAccount.redirectUris String[]` (exact), scopes `identity:authorize`, `identity:exchange`. `IDENTITY_ISSUER` required in production. | Registration validation. Prod boot fails without issuer. |
| NF-2 | `GET /api/oauth/authorize` (§3.2): S256 only, exact URI, state echo, nonce, internal-only login resume, hashed 60 s single-use code bound to `(svc, uri, challenge, nonce, user, sid)`, RFC 9207 `iss`. | Every row of §3.3 on the NF side. No redirect on a bad client or URI. |
| NF-3 | `POST /api/internal/authorization-codes/exchange`: atomic consume, PKCE, URI, service binding, live session, `nonce` claim added, `grantId`, replay → grant revoked. | Replay, wrong verifier, wrong service, wrong URI, expired code, disabled user between authorize and exchange. |
| NF-4 | `POST /api/internal/grants/status` (L1). Answers only for the calling service's own grants. | Cross-service isolation, disabled user, logged-out session. |

### Pashupatastra backend

| Slice | Content | Must not touch |
|---|---|---|
| **10.2a** Verification seam | `backend/app/identity/assertion.py`: `VerifiedPrincipal`, verifier (§4 spec), JWKS cache/refetch. Adds `PyJWT[crypto]` / `cryptography`. Pure. No request path. | Any route, `actor.py`, tests' enum pins |
| **10.2b** Identity directory | `(alias, sub) → Person` lookup. Refuse duplicate `ExternalIdentity`. Add an explicit `RoleAssignment` set, and refuse any scope assignment whose role assignment is absent. Reloadable, file-backed, validated (last-good on error, never empty-means-allow). `active_role` selection helper over `select_role`. | 10.1A–D semantics |
| **10.2c** AUTHENTICATED assurance | Add the enum member. Relax the `actor.py:188-194` invariant. Single factory. Update the **seven** enum-pin tests together. Source-scan guard. | `request_actor` |
| **10.2d** Mode + composition root | `PASHUPAT_AUTH_MODE`. Startup validation. Config-driven `JobService` policy at `router.py:116`. Refuse `UnenforcedPolicy` in NovaForge mode. `EnforcingPolicy(directory, topology=SectionRegistry)`. | Demo defaults |
| **10.2e** Login/session endpoints | `/auth/login`, `/auth/callback`, `/auth/logout`, `/auth/session`, `/auth/session/role`. Transaction and session stores. `__Host-` cookies. CSRF (Origin + header). Freshness via NF-4. | `/v1` schemas |
| **10.2f** `request_actor` replacement | NovaForge mode: session → Actor or 401/403. Reject `X-Actor-*`. Demo mode byte-identical. | Demo-mode behaviour |
| **10.2g** Audit enrichment | Actor block `{actor_id, role, assurance, iss, sub, sid}` in `optimization_runs.provenance_snapshot_json`. `metadata_json.auth` on human job events. Idempotency decision (Q6). | Append-only guards, existing columns |

### Pashupatastra frontend

| Slice | Content |
|---|---|
| FE-1 | Same-origin rewrite of `/api/*`. Login/logout buttons (top-level navigation, never `fetch` for login). Display identity from `GET /api/auth/session`. Role switcher for multi-role Persons. Send the CSRF header on non-GET. **No identity values sent in any request.** |

### Test / security work

| Slice | Content |
|---|---|
| SEC-1 | In-process fake NovaForge (ES256 keypair, authorize/exchange/liveness stubs) driving the full §11 matrix end-to-end against FastAPI `TestClient` in NovaForge mode. |
| SEC-2 | Mode-exclusivity tests: NovaForge mode refuses `UnenforcedPolicy`, headers, missing config. Demo mode is unchanged (existing suite green). |
| SEC-3 | Manual joint test against a real NovaForge build (issuer, JWKS rotation, disable user mid-session, logout propagation within N). |

### Migration / demo compatibility

| Slice | Content |
|---|---|
| MIG-1 | Demo remains the default. The authenticated deployment uses a fresh `PASHUPAT_JOBS_DB`. Document the directory file format and the provisioning runbook. OpenAPI snapshot regenerated deliberately for the additive `/auth/*` routes. |

**Order:** 10.2a → 10.2b → 10.2c → 10.2d, which need nothing from NovaForge, in parallel with NF-1 → NF-4. Then 10.2e → 10.2f → 10.2g → FE-1 → SEC-1/2/3.

---

## 16. Acceptance criteria (for the implementation slices)

1. In NovaForge mode, no request can produce an Actor except through a verified assertion → directory → `select_role` chain. Proven by a source-scan test and the SEC-1 matrix.
2. In NovaForge mode, any `X-Actor-Id` / `X-Actor-Role` header produces 400 and no state change.
3. NovaForge mode with `UnenforcedPolicy`, or with missing issuer, audience, JWKS URL, redirect URI, service credential or origin allowlist, **fails at startup**.
4. The assertion verifier rejects: wrong alg, `none`, HS256 confusion, unknown kid, attacker key reusing a kid, wrong typ, wrong iss, array or wrong aud, expired, future iat, lifetime > 300, missing sub/sid/jti/nonce, nonce mismatch, non-ACTIVE, non-user principal, replayed jti.
5. The callback rejects: missing or unknown transaction, state mismatch, reused transaction, `iss` mismatch, `error` responses. None of these creates a session.
6. The session id changes at login. Cookies are `__Host-`, `HttpOnly`, `Secure`, `SameSite=Lax`.
7. A disabled NovaForge user loses PP access within N seconds (SEC-3), and a revoked role or scope on the next request after directory reload (SEC-1).
8. A NovaForge `role` of `SUPER_ADMIN` confers nothing in PP (test).
9. Duplicate `ExternalIdentity` in the directory is refused at load.
10. An unknown `(iss, sub)` gets 403 `IDENTITY_NOT_ENROLLED`. No Person or role is created.
11. `job_events` for authenticated actions carry `actor_assurance = AUTHENTICATED` and `metadata.auth.{iss,sub,sid}`. `optimization_runs` provenance carries assurance.
12. Demo mode: the entire existing suite passes, with the only changes being the seven enum-pin tests and the OpenAPI snapshot (additive).
13. Legacy `/optimize` and `/recover` response contracts are byte-identical in both modes.
14. No bearer token, assertion, `person_id`, role or scope is ever readable by frontend JavaScript.

---

## 17. Open questions / blockers

| # | Question | Default if unanswered | Blocks |
|---|---|---|---|
| Q1 | Deployment topology: can frontend and backend be same-origin (proxy) or at least same-site? | Same-origin proxy | 10.2e, FE-1 |
| Q2 | Will NovaForge implement NF-1…NF-4 (or full OIDC, Option F)? | Required. No workaround meets R1–R4. | Everything past 10.2d (**B-1, B-2**) |
| Q3 | Freshness bound N | 60 s (must be ≤ 300) | 10.2e |
| Q4 | Must legacy `/optimize` and `/recover` require a session in NovaForge mode? | Yes, any enrolled Person, no role/scope | 10.2f |
| Q5 | Run-record assurance: provenance JSON (no schema change) or an additive column? | Provenance JSON | 10.2g |
| Q6 | Idempotency namespace across modes: DB separation only, or assurance in key derivation? | DB separation | 10.2g |
| Q7 | Who administers the directory file, and how are changes reviewed and reloaded? | Ops-owned file + reload signal | 10.2b |
| Q8 | Does ADMIN ever manage assignments in-app? | No (out of scope) | — |
| Q9 | Is NovaForge `/internal` network-isolated in the target deployment? | Assume not; rely on the credential | Deploy review |
| Q10 | Should NovaForge `trust_level` / `auth_time` ever gate railway actions (step-up)? | Recorded only | — |
| Q11 | Is `User.id` immutable by NovaForge contract? | Treat as immutable; a change = new identity | NF confirmation |
| Q12 | Does the local NovaForge checkout (`5f3a2a6`) match what will be deployed? | UNVERIFIED | NF-1 |

---

## 18. Files inspected

**Pashupatastra:** `backend/app/identity/{__init__,actor,person,assignments,scope,scope_assignment,role_actions,authorization,policy}.py`; `backend/app/api/{main,deps}.py`; `backend/app/api/routers/{optimize,recover}.py`; `backend/app/jobs/router.py` (1–340 + route signatures); `backend/app/jobs/{history,events,idempotency,optimization,service}.py` (targeted); `backend/app/audit/{service,models,repository}.py`; `backend/app/simulation/` (grep); `frontend/src` (grep), `frontend/src/lib/data.ts`; `backend/tests` (grep for enum pins and actor headers); `docs/SLICE10_1_RAILWAY_IDENTITY_SCOPE_READINESS.md`, `docs/SLICE10_IDENTITY_AUTHORIZATION_ARCHITECTURE.md` (grep); `requirements.txt`.

**NovaForge (read-only):** `server/src/app.ts`; `server/src/routes/{internal,auth(targeted),users(grep)}.ts`; `server/src/lib/{identityAssertion,identityAssertionVerifier,jwt,serviceAuth,identityKeys,env(grep)}.ts`; `server/src/middleware/requireAuth.ts`; `server/prisma/schema.prisma` (enums, User, Session, ServiceAccount, ServiceCredential); `server/scripts/create-service-account.ts` (grep); `server/test/{p0-6-identity-trust,identity-assertion-trust}.test.ts` (test names only).
