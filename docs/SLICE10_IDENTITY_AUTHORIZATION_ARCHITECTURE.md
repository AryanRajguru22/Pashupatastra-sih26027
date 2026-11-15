# Slice 10 — Identity, Authentication & Authorization Architecture

**Status:** Architecture gate. READ-ONLY audit. No implementation.
**Pashupatastra HEAD:** `985ca045c2cc297f9f0dc0f33aaac2f20badb4c7`
**NovaForge HEAD:** `5f3a2a6cd999286c032dce44a921132fe6d15e9f`
**Date:** 2026-09-20
**Verdict:** **AMBER**

---

## 1. Executive summary

### 1.1 A premise in the gate brief is out of date — stating it plainly

The brief describes "one-time short-lived audience-bound front-channel code + PKCE/state + backend
exchange + validated identity assertion" as *"a previously considered secure pattern"*, and instructs
that it not be assumed final.

The audit finds that NovaForge has **already built and shipped the back half of that pattern**, to a
high standard. It is not a proposal. It is running code with a forgery-resistance test suite:

| Artifact | Evidence |
|---|---|
| ES256 identity assertions, audience-bound, 300 s TTL | `NovaForge/server/src/lib/identityAssertion.ts:13,19-56` |
| A strict reference verifier **naming Pashupatastra** | `NovaForge/server/src/lib/identityAssertionVerifier.ts:4-5` |
| Public JWKS endpoint with key rotation | `NovaForge/server/src/app.ts:68-69`; `lib/identityKeys.ts:69-99` |
| `POST /internal/identity-assertions` (scope `identity:assert`) | `NovaForge/server/src/routes/internal.ts:32` |
| `POST /internal/introspect` (scope `identity:introspect`) | `NovaForge/server/src/routes/internal.ts:74` |
| Service-account principals, hashed secrets, scopes | `NovaForge/server/src/lib/serviceAuth.ts:16-101` |
| Forgery matrix: `alg=none`, HS256 confusion, kid reuse, wrong iss/aud, expiry, over-long lifetime, rotation | `NovaForge/server/test/p0-6-identity-trust.test.ts:255-366` |

So Section 5's "compare realistic options" exercise is largely settled by evidence: **Option D
(signed identity assertion) + Option E (server-side introspection) already exist, and are the only
options NovaForge actually supports.**

### 1.2 But there is a hole, and it decides the verdict

`routes/internal.ts` requires the relying service to present **the end user's NovaForge access
token** in the request body (`accessToken` — `internal.ts:31,41`). That token exists **only** as an
`httpOnly` cookie on NovaForge's own origin:

```ts
res.cookie(ACCESS_COOKIE, accessToken, { httpOnly: true, sameSite: "lax", secure, ... })
//  -- NovaForge/server/src/routes/auth.ts:51-56
```

No `domain` attribute is set, so it is a **host-only** cookie. CORS is pinned to a **single** origin:

```ts
app.use(cors({ origin: env.rpOrigin, credentials: true }));            // app.ts:46
rpOrigin: process.env.RP_ORIGIN ?? renderExternalUrl ?? "http://localhost:5173"  // lib/env.ts:24
```

A search of the entire NovaForge server for any front-channel handoff — `/authorize`, an
authorization `code`, `pkce`, `state`, a `redirect`, any `exchange` endpoint — returns **nothing**
across `routes/` and `lib/` (only unrelated prose matches). NovaForge's own source says so:

> `// Trust flow for a relying service (not integrated yet):`
> — `NovaForge/server/src/routes/internal.ts:16`

**There is today no mechanism by which Pashupatastra's backend can ever come to hold a user's
NovaForge access token.** The back-channel is ready; the browser→Pashupatastra-backend hop that feeds
it does not exist, and building it is a **NovaForge-side design decision with real security
consequences** — exactly the class of decision this gate exists to escalate rather than assume.

### 1.3 On the Pashupatastra side, the domain half is missing entirely

Pashupatastra models railway *assets* well: `SectionRegistry` is the single authority for section
identity (`backend/app/data/section_registry.py:1-33`), and every job carries `job_section_id`,
`asset_section_id`, `corridor_id`, `section_match` (`backend/app/jobs/asset_association.py:134-153`).

What does **not** exist anywhere is the binding **person → role → railway scope**. A grep across
`backend/app/` for `assigned_to`, `assignee`, `crew`, `roster`, `responsible_for`, `owner_id`,
`actor_section`, `actor_scope` returns only prose inside reason-code documentation — no data model,
no table, no field. Authorization therefore cannot become scope-aware by wiring alone; a missing
domain concept must first be invented, and that is Pashupatastra's decision, not NovaForge's.

### 1.4 Verdict rationale

The temptation is to call this GREEN because a ready-made, well-tested assertion API is sitting
there. The brief explicitly warns against that. Two decisions remain genuinely open, both
security-load-bearing:

1. **How does a credential cross from the user's browser to Pashupatastra's backend?** No supported
   path exists. Every candidate changes NovaForge.
2. **What is a railway scope, and who assigns it?** No concept exists. Until it does, an "enforcing"
   policy could enforce only role — strictly weaker than the operational rules the domain needs
   (Section 10).

Neither is a contradiction, so not RED. Both need explicit approval before code, so **AMBER**.

---

## 2. Current Pashupatastra identity inventory

### 2.1 The three independent facts

`backend/app/identity/actor.py` holds a deliberately minimal model whose own docstring is candid
about what it is not (`actor.py:1-37`).

| Concept | Definition | Location |
|---|---|---|
| `Actor` | frozen dataclass: `actor_id`, `role`, `assurance` | `actor.py:116,124-126` |
| `ActorKind` | `HUMAN` \| `SYSTEM` — **derived** from role, never stored | `actor.py:47-52`; property `actor.py:196-201` |
| `ActorRole` | `WORKER`, `ENGINEER`, `AUTHORITY`, `ADMIN`, `SYSTEM`, `UNIDENTIFIED` | `actor.py:54-74` |
| `IdentityAssurance` | `SYSTEM_INTERNAL`, `DECLARED_UNVERIFIED`, `NONE` | `actor.py:86-106` |

**There is no `AUTHENTICATED` member**, and **two** tests pin the enum's exact membership — one in
the domain suite and one inside the frozen-contract suite:

```python
def test_no_authenticated_assurance_exists_yet():
    assert {member.value for member in IdentityAssurance} == {
        "SYSTEM_INTERNAL", "DECLARED_UNVERIFIED", "NONE"}
#  -- backend/tests/test_actor_identity.py:86-93
#  -- and, duplicated as a contract pin:
#     backend/tests/test_slice7_contract_freeze.py:745-752
#     (test_no_authenticated_assurance_exists)
```

Both must be updated together in sub-slice 10.3 (§20). Missing the second one is the most likely way
to break the frozen-contract suite while believing only the domain suite was touched.

### 2.2 Invariants actually enforced at construction

`Actor.__post_init__` (`actor.py:128-199`) refuses, in order:

- unknown role / unknown assurance → `InvalidActorError`
- `actor_id` not matching `^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$` (`actor.py:108`)
- `role=SYSTEM` with assurance ≠ `SYSTEM_INTERNAL`, or an id not prefixed `SYSTEM`
- a **non-SYSTEM** role using a `SYSTEM…`-looking id (a human can never be recorded as the system)
- `role=UNIDENTIFIED` with assurance ≠ `NONE`
- **any human role with assurance ≠ `DECLARED_UNVERIFIED`** (`actor.py:190-194`)

That last clause is the single line Slice 10 must widen.

### 2.3 Where identity enters the system

**Exactly one external entry point** — `request_actor`, `backend/app/api/deps.py:33-95`:

| Input | Result |
|---|---|
| both headers absent | `unidentified_actor()` → `UNIDENTIFIED` / `NONE` |
| both present | `human_actor(id, role)` → `DECLARED_UNVERIFIED` |
| exactly one present | `400 ACTOR_HEADERS_INCOMPLETE` |
| `X-Actor-Role: SYSTEM` | `403 ACTOR_SYSTEM_ROLE_FORBIDDEN` |
| malformed | `400 ACTOR_INVALID` |

Its docstring names itself the replacement seam: *"This is the seam a later authentication slice
replaces: it will derive the Actor from a verified credential instead of from these headers"*
(`deps.py:55-57`).

In-process entry points — `JobService._actor_or_unidentified` (`service.py:2159`) and
`optimization.py:411` — default a missing actor to `unidentified_actor()`. They fabricate nothing.

### 2.4 What Pashupatastra currently trusts — the honest answer

> **Pashupatastra trusts nothing about a caller's identity, and is scrupulously honest about that.**

- `X-Actor-Id: AUTHORITY-017` is recorded, never believed. Assurance is stamped
  `DECLARED_UNVERIFIED` at construction (`actor.py:235-239`) and persisted as such
  (`history.py:71,142`).
- The only identity fact Pashupatastra *does* trust is one it generates itself: `SYSTEM_INTERNAL`
  actors are constructible only in-process via `system_actor()` (`actor.py:242-260`), and the HTTP
  layer refuses a caller-declared SYSTEM role (`deps.py:84-90`).

### 2.5 Where declared identity nonetheless changes behavior today

This matters: **assurance is already load-bearing**, so Slice 10 extends an existing pattern rather
than inventing one.

1. **Idempotency fails closed on `NONE`** — `service.py:1160-1173`: a key is scoped to the declaring
   actor, *"and an unidentified caller has no scope."*
2. **The Slice 9 identified-human guard** — `_require_identified_human` (`lifecycle.py:1878-1920`)
   refuses `assurance is NONE` or `role is UNIDENTIFIED`. Its docstring is explicit that this is
   accountability, not authentication (`lifecycle.py:1887-1893`).

   Guarded transitions: approve/commit (`1401`), reject (`1530`), postpone (`1652`), release
   (`1808`), start execution (`2058`), complete execution (`2204`), report-not-completed (`2309`).
   `create_job` is **deliberately unguarded** (`lifecycle.py:1908`) — an anonymous defect report is
   better than a suppressed one.

### 2.6 The authorization seam

`backend/app/identity/authorization.py`. `JobAction` enumerates 15 operations
(`authorization.py:34-80`): `REPORT_JOB`, `REQUEST_OPTIMIZATION`, `ASSIGN_SCHEDULE`, `COMMIT_BLOCK`,
`COMPLETE_JOB`, `READ_JOB_HISTORY`, `READ_BLOCK_PROPOSAL`, `REJECT_PROPOSAL`, `POSTPONE_PROPOSAL`,
`START_EXECUTION`, `REPORT_EXECUTION_NOT_COMPLETED`, `READ_JOB_EXECUTION`,
`RELEASE_COMMITTED_BLOCK`, `READ_JOB_OBLIGATIONS`, `READ_OPTIMIZATION_RUN`.

The seam is **called on every path** — 19 call sites verified: `service.py:761, 1308, 1341, 1469,
1518, 1567, 1608, 1676, 1738, 1801, 1894, 1966, 2021, 2083, 2203`, plus `optimization.py:413`.

The only shipped policy is `UnenforcedPolicy`, `enforcing = False`, `authorize()` returns `None`
(`authorization.py:114-124`). Module docstring: *"No permission described anywhere in this repository
is enforced today. Do not describe it otherwise."* (`authorization.py:13-15`).

**Signature finding, decisive for Section 10:** `authorize(self, actor, action)` takes **no resource
argument** (`authorization.py:109`). Scope-aware authorization cannot be expressed through this seam
as it stands.

### 2.7 Audit event identity representation

`job_events` (`backend/app/jobs/history.py:57-80`):

```
sequence, event_id, schema_version, entity_type, job_id, event_type, occurred_at,
actor_id, actor_role, actor_kind, actor_assurance,
reason, optimization_run_id, before_state_json, after_state_json, metadata_json
```

Four dedicated actor columns, all `NOT NULL`, written from `Actor.to_dict()` (`history.py:123-142`;
`actor.py:209-215`). Append-only guards installed (`history.py:92`).

**Finding that decides sub-slice shape:** `metadata_json TEXT NOT NULL` already exists.
Authenticated identity references can be carried there **additively, with no migration and no new
column**.

### 2.8 Notification recipient identity

`NotificationIntent.recipient_role: ActorRole` (`notifications.py:199`), addressed via a static
`RECIPIENT_ROLES` map (`notifications.py:111`). The module states the boundary outright:
*"recipient_role is AUTHORITY, WORKER or ENGINEER. There is no user…"* (`notifications.py:55`), and
delivery refuses rather than invents an address (`notifications.py:325`).

**No individual recipient identity is fabricated anywhere.** A correct red line, and preserved.

### 2.9 Railway domain vocabulary that does exist

| Concept | Where |
|---|---|
| `Section` — stable opaque id, direction-independent, single authority | `data/section_registry.py:1-33` |
| Lookup by id, station-pair, track, chainage | `data/section_registry.py:23-33` |
| `corridor_id` | `jobs/asset_association.py:138` |
| `job_section_id`, `asset_section_id`, `section_match` | `jobs/asset_association.py:134-136` |
| Resource identity is the pair `(track_id, section_id)` | `data/section_registry.py:27-30`; `optimizer/solver.py:485` |

**Absent:** any notion of a *division*, a *crew*, or of a **person** responsible for any of the above.

---

## 3. Current NovaForge identity / auth inventory

NovaForge is a TypeScript Express + Prisma/PostgreSQL service with a separate client
(`NovaForge/server/package.json`; `prisma/schema.prisma:6-7`).

### 3.1 Authentication mechanisms — proven, not assumed

For each mechanism, the brief asks: where credentials enter, where identity is established, what
artifact proves it, who signs, who validates, expiry, revocation, audience, replay, transport.

#### (a) WebAuthn passkey login — the primary credential

| Question | Answer | Evidence |
|---|---|---|
| Credentials enter | `POST /auth/login/options` → `POST /auth/login/verify` | `routes/auth.ts:388,418` |
| Identity established | Server-side WebAuthn assertion verification against stored `Credential.publicKey`, `counter` | `prisma/schema.prisma:79-94`; `lib/webauthn.ts` |
| Artifact proving identity | Authenticator signature over a server-issued challenge | `lib/challengeStore.ts` |
| Transport | Origin/RP-ID bound to `env.rpOrigin` / `env.rpId` | `lib/env.ts:22-24` |
| Brute-force control | `loginAttemptLimit`, `AuthThrottle` with `lockLevel` / `lockedUntil` | `routes/auth.ts:418`; `schema.prisma:121-127` |

#### (b) TOTP and recovery-code fallback logins

`POST /auth/login/totp` (`auth.ts:579`), `POST /auth/login/recovery-code` (`auth.ts:754`), both
behind `fallbackIpLimit` + `loginAttemptLimit`. TOTP secrets are encrypted at rest
(`lib/totpSecretCrypto.ts`; test `test/totp-secret-crypto.test.ts`) and replay is blocked by
`Credential.totpLastUsedStep` (`schema.prisma:94`). Per-user availability is togglable via
`User.totpLoginEnabled` / `recoveryCodeLoginEnabled` (`schema.prisma:64-65`).

#### (c) Session tokens — NovaForge-internal, never for Pashupatastra

| Property | Value | Evidence |
|---|---|---|
| Algorithm | **HS256**, secrets NovaForge alone holds | `lib/jwt.ts:41-46` |
| Access TTL | **15 min** | `lib/jwt.ts:15` |
| Refresh TTL | **30 days**, rotated | `lib/jwt.ts:16`; `lib/refreshRotation.ts` |
| Issuer / audience | `novaforge` / `novaforge:session`, pinned on verify | `lib/jwt.ts:32-38` |
| Type confusion defense | explicit `type` claim, separate secrets | `lib/jwt.ts:18-28` |
| Storage | `httpOnly`, `sameSite: "lax"`, `secure` when origin is https, **no `domain`** | `routes/auth.ts:51-61` |
| Revocation | **Authoritative per request** — `isSessionLive()` re-reads the `Session` row and the user's `status` on every call | `middleware/requireAuth.ts:30-41` |

`lib/jwt.ts:30-34` states the boundary directly: session tokens are *"never accepted by a downstream
service."*

#### (d) Identity assertions — the Pashupatastra-facing artifact

| Question | Answer | Evidence |
|---|---|---|
| Where issued | `POST /internal/identity-assertions`, service-authenticated, scope `identity:assert` | `routes/internal.ts:33` |
| Input | the **user's access token**, verified + `isSessionLive` + user `ACTIVE` | `internal.ts:38-53` |
| Artifact | ES256 JWT, `typ: "nf-identity+jwt"`, `token_use: "identity_assertion"` | `identityAssertionVerifier.ts:26-27` |
| Signed by | NovaForge identity key ring (P-256 private key) | `identityKeys.ts:18-24` |
| Validated by | relying service, offline, against published **public** JWKS | `identityAssertionVerifier.ts:9-12` |
| Expiry | **300 s** TTL; verifier caps lifetime at 600 s | `identityAssertion.ts:13`; `identityAssertionVerifier.ts:57` |
| Audience | `req.service!.audience` — **the authenticated service's own, never caller-chosen** | `internal.ts:57` |
| Replay protection | `jti` per assertion, `iat`-in-future rejected, 30 s clock tolerance | `identityAssertion.ts:37`; `identityAssertionVerifier.ts:56,89` |
| Refuses to issue | user `status !== "ACTIVE"` | `identityAssertion.ts:20` |

Claims carried (`identityAssertion.ts:33-41` + JWT registered claims):

```
iss, aud, sub, sid, jti, iat, exp,
token_use = "identity_assertion", principal_type = "user",
role, account_status = "ACTIVE", auth_time, trust_level
```

`trust_level` is notable: it is the **decayed, currently-effective** trust, recomputed at issue time
and deliberately *not* written back to the session row (`identityAssertion.ts:21-26`;
`lib/trust.ts`), proven by `test/identity-assertion-trust.test.ts:91-117`.

#### (e) Introspection — freshness beyond `exp`

`POST /internal/introspect`, scope `identity:introspect` (`internal.ts:74`). Re-verifies signature +
audience, then re-checks `isSessionLive(claims.sid, claims.sub)` and that the user is still `ACTIVE`,
returning current `role` and `accountStatus`. Inactive results deliberately carry **no reason**, so
it is not an oracle (`internal.ts:70-73`).

#### (f) Service-to-service authentication

`Authorization: Bearer nfsvc_<credentialId>.<secret>` (`lib/serviceAuth.ts:9`). 32 random bytes;
only SHA-256 stored; `timingSafeEqual` comparison performed even for unknown ids to avoid a timing
oracle (`serviceAuth.ts:59-63`). Checks: unrevoked, unexpired, account `ACTIVE`, scope granted
(`serviceAuth.ts:64-68,79-83`). Scopes are a closed set: `["identity:assert",
"identity:introspect"]` (`serviceAuth.ts:16`). *"A service credential can never be used as, or
exchanged for, a user session."* (`serviceAuth.ts:13-14`), tested at
`test/p0-6-identity-trust.test.ts:423`.

Provisioning is **SUPER_ADMIN-only** (`routes/serviceAccounts.ts:27,37,70,96,122`), tested at
`p0-6-identity-trust.test.ts:441`.

### 3.2 Key management and rotation

`lib/identityKeys.ts`. Separate key **family** from session tokens, and the file explains why
(`identityKeys.ts:3-11`): a public key lets a service verify NovaForge identities but never mint one.

- `IDENTITY_SIGNING_PRIVATE_KEY` (PKCS#8 PEM, P-256), `IDENTITY_SIGNING_KEY_ID`
- `IDENTITY_PREVIOUS_PUBLIC_KEYS` — JSON array of public JWKs still accepted during rotation
  (`identityKeys.ts:17-18`)
- Curve enforced: EC P-256 only (`identityKeys.ts:43-47`)
- **Production refuses to issue with an unconfigured ring** (`identityKeys.ts:95-97`), tested at
  `p0-6-identity-trust.test.ts:367`
- Outside production an **ephemeral per-process key** is generated — assertions stop verifying after
  a restart (`identityKeys.ts:98-99`)
- Published at `GET /.well-known/jwks.json` and `/api/.well-known/jwks.json`, `Cache-Control:
  public, max-age=300` (`app.ts:56-69`)
- Rotation behavior tested end to end: *"old assertions verify while the old public key is published,
  then stop"* (`p0-6-identity-trust.test.ts:345`)

### 3.3 The verifier's rejection matrix — what is actually proven

`createIdentityAssertionVerifier` (`identityAssertionVerifier.ts:52-108`) rejects: non-ES256 `alg`;
wrong `typ`; unknown/absent `kid`; wrong `iss`; wrong `aud`; multi-valued `aud`; wrong `token_use`;
missing `sub`/`sid`/`jti`/`role`; missing `exp`/`iat`; lifetime > `maxLifetimeSec`; `iat` in the
future; `principal_type !== "user"`; `account_status !== "ACTIVE"`.

Each is a passing test (`p0-6-identity-trust.test.ts:282-344`), including the two attacks that matter
most: an attacker's own ES256 key **reusing a trusted `kid`** (`:286`) and **HS256 key confusion
using the public key as an HMAC secret** (`:300`).

`test/p0-6-identity-trust.test.ts:431` is directly relevant to Pashupatastra's red lines: *"an
unauthenticated caller can't obtain assertions by sending identity headers."*

### 3.4 NovaForge roles and account status

```prisma
enum Role { SUPER_ADMIN  ADMIN  SENIOR_APPROVER  APPROVER  MEMBER }   // schema.prisma:10-16
enum AccountStatus { ACTIVE  SUSPENDED  DISABLED }                    // schema.prisma:22-26
```

This is a **generic approval-governance vocabulary**, entirely disjoint from
WORKER/ENGINEER/AUTHORITY. See Section 8.

### 3.5 What NovaForge is *for* — and why it must not own railway rules

NovaForge's domain is generic multi-party approval of sensitive actions: `SensitiveAction`,
`ApprovalPolicy` (`quorumType`, `minApprovals`, `eligibleRoles`, `escalationTimeoutSec`),
`ApprovalRequest`, `ApprovalVote` with a **WebAuthn device signature per vote**
(`schema.prisma:164-226`), plus a hash-chained `AuditLog` with `prevHash`/`hash`/`seq` and an
`AuditChainHead` (`schema.prisma:238-274`).

This is genuinely generic and genuinely strong. It is also **not** railway scheduling, and nothing in
it knows what a section is.

### 3.6 Deployment assumptions

- PostgreSQL (`schema.prisma:6-7`); Render + docker-compose + nginx (`render.yaml`,
  `docker-compose.yml`, `Dockerfile.render`)
- Combined deploy can serve the client from the same process; bare API mount is skipped in that case
  to avoid route shadowing (`app.ts:97-110`)
- `TRUST_PROXY` deliberately **off by default** — trusting `X-Forwarded-For` on a directly exposed
  server would let clients choose their own IP for rate limiting (`app.ts:36-42`)
- `/internal` is *"intended to be reachable only from the private network in a real deployment"*
  (`internal.ts:12-13`) — **an assumption, not an enforced control.** See Section 16, FM-23.
- Socket.io with authenticated handshake and join-time re-validation (`lib/socketSecurity.ts`;
  `app.ts:138-140`)

---

## 4. Actual trust boundaries

### 4.1 Today (both systems, unintegrated)

```
┌─ NOVAFORGE TRUST DOMAIN ────────────────────────────────────────────────┐
│                                                                         │
│  USER ──passkey/TOTP/recovery──▶ POST /auth/login/*                     │
│                                    │                                    │
│                                    ▼                                    │
│                           Session row (PostgreSQL)   ◀── AUTHORITATIVE  │
│                                    │                                    │
│                                    ▼                                    │
│                   httpOnly cookie, sameSite=lax, HOST-ONLY              │
│                   (accessToken 15m / refreshToken 30d)                  │
│                                    │                                    │
│                                    ▼                                    │
│                  requireAuth → isSessionLive() EVERY request            │
└─────────────────────────────────────────────────────────────────────────┘

         ╔═══════════════════════════════════════════════════╗
         ║   NO PATH EXISTS ACROSS THIS BOUNDARY TODAY       ║
         ║   (no front-channel; cookie is host-only;         ║
         ║    CORS is single-origin)                         ║
         ╚═══════════════════════════════════════════════════╝

┌─ PASHUPATASTRA TRUST DOMAIN ────────────────────────────────────────────┐
│                                                                         │
│  CALLER ──X-Actor-Id / X-Actor-Role──▶ request_actor()  [deps.py:33]    │
│                                    │                                    │
│                      UNTRUSTED INPUT, recorded verbatim                 │
│                                    ▼                                    │
│                Actor(assurance=DECLARED_UNVERIFIED)                     │
│                                    │                                    │
│                    ┌───────────────┴───────────────┐                    │
│                    ▼                               ▼                    │
│    authorize(actor, action)              _require_identified_human()    │
│    UnenforcedPolicy: ALWAYS PERMITS      refuses NONE / UNIDENTIFIED    │
│    [authorization.py:118]                [lifecycle.py:1878]            │
│                    │                               │                    │
│                    └───────────────┬───────────────┘                    │
│                                    ▼                                    │
│            JOB / PROPOSAL / EXECUTION state transition                  │
│                                    │                                    │
│                                    ▼                                    │
│            job_events (append-only, assurance recorded honestly)        │
└─────────────────────────────────────────────────────────────────────────┘
```

### 4.2 Target boundary (the shape Slice 10 should reach)

```
       USER (browser)
         │  ① authenticate  ── passkey / TOTP
         ▼
┌──────────────────────────────────────────────────────────────┐
│ NOVAFORGE                                     TRUSTED ISSUER │
│   establishes WHO. Owns credentials, sessions, revocation.   │
│   Knows nothing about railway sections.                      │
└──────────────────────────────────────────────────────────────┘
         │  ② ??? THE MISSING HOP ??? (Section 5 / OQ-1)
         ▼
┌──────────────────────────────────────────────────────────────┐
│ PASHUPATASTRA BACKEND                                        │
│   ③ back-channel: POST /internal/identity-assertions         │
│      (its own nfsvc_ service credential + the user's token)  │
│         ──▶ ES256 assertion, aud = pashupatastra             │
│                                                              │
│   ④ VERIFY OFFLINE against cached JWKS   ◀─ CRYPTO PROOF     │
│      iss, aud, exp, kid, alg, token_use, lifetime            │
│         ↳ FAILURE ⇒ 401. NEVER a downgrade to DECLARED_*     │
│                                                              │
│   ⑤ VerifiedPrincipal{ sub, sid, iss, exp, nf_role,          │
│                        trust_level, auth_time }              │
│      nf_role is RECORDED. It is NOT an ActorRole.            │
│                                                              │
│   ⑥ DOMAIN DIRECTORY LOOKUP   ◀─ PASHUPATASTRA-OWNED         │
│      sub ──▶ actor_id + ActorRole + railway scope            │
│         ↳ no mapping ⇒ 403. NEVER a default role.            │
│                                                              │
│   ⑦ Actor(actor_id, role, assurance=AUTHENTICATED)           │
│                                                              │
│   ⑧ authorize(actor, action, resource)  ◀─ DOMAIN DECISION   │
│      RBAC + scope + state + SoD                              │
│                                                              │
│   ⑨ JOB / PROPOSAL / EXECUTION                               │
│                                                              │
│   ⑩ job_events: actor_id, role, kind, assurance              │
│      + metadata{ nf_sub, nf_iss, nf_sid }   NEVER the token  │
└──────────────────────────────────────────────────────────────┘
```

### 4.3 Classification demanded by the brief

| Element | Classification |
|---|---|
| NovaForge as issuer | **Trusted component** (after ④ validates its signature) |
| `X-Actor-Id` / `X-Actor-Role` | **Untrusted input** — always, forever |
| The assertion's `role` claim | **Untrusted for authorization**; trusted only as a recorded fact |
| ES256 signature over `{iss, aud, sub, sid, exp}` verified against JWKS | **Cryptographic proof** |
| ④ verification + ⑥ directory lookup | **Authorization decision** (identity half) |
| ⑧ `authorize(actor, action, resource)` | **Business-domain decision** — Pashupatastra-only |
| ⑨ state transition, SoD, scope | **Business-domain decision** — Pashupatastra-only |

### 4.4 The central rule, restated with its enforcement point

> **Once authentication exists, Pashupatastra must never accept `X-Actor-Id` as proof of identity.**

The single enforcement point is `request_actor` (`deps.py:33`) — the only external identity entry in
the codebase (§2.3). Because there is exactly one, this rule is enforceable by changing one function,
which is the strongest structural result of this audit.

---

## 5. Authentication mechanism comparison

Scored against what NovaForge **actually supports at HEAD `5f3a2a6`**, not against what is
conventional.

### A. Shared bearer access token (Pashupatastra receives the NovaForge session token directly)

| Property | Assessment |
|---|---|
| Security | **Poor.** Pashupatastra would hold a credential valid at NovaForge for 15 min. |
| Audience confusion | **Severe** — the defining failure. NovaForge pins `aud: novaforge:session` precisely to prevent this (`jwt.ts:33-38`), and tests it (`p0-6:449,461`). |
| Replay / leakage | A leaked token is a full NovaForge session. |
| Revocation | Good — `isSessionLive` is per-request (`requireAuth.ts:39`). |
| Service trust | Requires NovaForge to trust Pashupatastra with user session credentials. |
| Complexity | Low. |
| Fit with NovaForge | **Contradicts an explicit, tested design rule.** |
| Fit with Pashupatastra | Poor. |
| **Verdict** | **Rejected.** Directly violates `jwt.ts:30-34`. |

### B. OAuth/OIDC authorization code + PKCE

| Property | Assessment |
|---|---|
| Security | Excellent; the industry standard. |
| Replay / CSRF | Handled by `code_verifier` and `state`. |
| Revocation | Requires introspection — which **already exists** (`internal.ts:74`). |
| Complexity | **High** — needs `/authorize`, a consent/redirect surface, client registration, a code store, `code_challenge` handling. None exist. |
| Fit with NovaForge | **Nothing present.** Confirmed: no `/authorize`, no `pkce`, no `state`, no `redirect`, no `code` anywhere in `routes/` or `lib/`. |
| Migration | Largest of all options — a new protocol surface in NovaForge. |
| **Verdict** | Correct long-term, **disproportionate now**. Would discard the assertion machinery already built and tested. |

### C. One-time front-channel code + backend exchange

| Property | Assessment |
|---|---|
| Security | Strong if the code is single-use, short-lived (≤60 s), audience-bound, and exchanged over the back channel. |
| Replay | Mitigated by single-use + short TTL; requires server-side consumption state. |
| CSRF | Needs `state`; the code must not be usable without the service credential. |
| Leakage | Code appears in a URL → browser history, `Referer`, logs. Mitigated by one-time use and tiny TTL. |
| Revocation | Inherited from the assertion + introspection already built. |
| Complexity | **Moderate — and narrowly scoped.** One new NovaForge endpoint pair plus a code store. `lib/challengeStore.ts` already establishes the short-lived-server-side-artifact pattern. |
| Fit with NovaForge | **Additive.** It supplies exactly the missing ② and feeds the existing ③ unchanged. |
| Fit with Pashupatastra | Excellent — Pashupatastra's side is identical under B, C, or any option feeding ③. |
| Migration | Smallest that closes the gap. |
| **Verdict** | **Strongest candidate for ②** — but it is a **NovaForge change requiring approval** (OQ-1). |

### D. Signed identity assertion

| Property | Assessment |
|---|---|
| Security | **Excellent, and proven.** ES256, audience-bound, 300 s, strict verifier, forgery matrix passing (`p0-6:255-366`). |
| Replay | `jti`, `exp`, `iat`-future rejection, 30 s tolerance, lifetime cap. |
| Audience confusion | **Structurally prevented** — `aud` is `req.service!.audience`, never caller-chosen (`internal.ts:57`). |
| Leakage | A stolen assertion is usable ≤300 s and **only at Pashupatastra**. |
| Revocation | `exp` only, offline — see E. |
| Service trust | Public-key verification: Pashupatastra can verify but **never mint** (`identityKeys.ts:9-11`). |
| Complexity | **Zero on NovaForge's side — it exists.** Pashupatastra needs a Python port of `identityAssertionVerifier.ts` plus JWKS caching. |
| **Verdict** | **This is the identity artifact.** Not a choice so much as a finding. |

### E. Server-side session introspection

| Property | Assessment |
|---|---|
| Security | Strongest freshness guarantee; closes D's revocation window. |
| Availability | Couples every checked request to NovaForge uptime (Section 16, FM-1). |
| Non-oracle design | Inactive results carry no reason (`internal.ts:70-73`). |
| Complexity | **Zero on NovaForge's side — it exists** (`internal.ts:74`). |
| **Verdict** | **Adopt selectively**, not universally: mandatory for high-consequence mutations, skipped for reads. See Section 15. |

### F. Direct shared cookie

| Property | Assessment |
|---|---|
| Feasibility | **Impossible as deployed.** The cookie is host-only (no `domain` — `auth.ts:51-61`); `sameSite: "lax"` blocks cross-site XHR attachment; CORS allows one origin (`app.ts:46`); `httpOnly` blocks JS reads. |
| To enable | Would need a shared parent domain, `domain=`, `sameSite: "none"` — **widening CSRF exposure across both apps**. |
| **Verdict** | **Rejected.** Four independent controls would each have to be weakened. |

### G. Other mechanisms NovaForge actually supports

- **Service credentials (`nfsvc_…`)** — real and required, but they authenticate the **service**, not
  the user. They are the credential for hop ③, never for a human. Enforced:
  `p0-6-identity-trust.test.ts:423`.
- **Socket.io authenticated channel** (`lib/socketSecurity.ts`) — reuses `isSessionLive`; a live
  notification channel, not an identity transport for a separate backend.
- **WebAuthn per-vote signatures** (`schema.prisma:213`) — the mechanism that would underpin a future
  step-up/re-authentication requirement (Section 12, OQ-6).

### 5.1 Conclusion

**D + E are settled by evidence.** The open question is **② only**: how a credential reaches
Pashupatastra's backend so that ③ can be called. Ranked: **C** (smallest additive change) >
**B** (correct, disproportionate now) >> **A**, **F** (rejected on evidence).

**This choice is not the auditor's to make — it modifies NovaForge. It is OQ-1, and it is the primary
reason for the AMBER verdict.**

---

## 6. Recommended trust model

```
NOVAFORGE ASSERTS:   "NovaForge authenticated principal `sub` in session `sid`
                      at `auth_time`, for audience `pashupatastra`,
                      valid until `exp`, with generic role `role`
                      and current trust_level."

PASHUPATASTRA DECIDES: "`sub` maps to railway actor `actor_id`, who currently
                        holds domain role `ActorRole` over scope `S`,
                        and may therefore perform `action` on `resource`
                        in its current `state`."
```

Five rules, each traceable to evidence:

1. **Identity is asserted; authorization is decided.** NovaForge's verifier docstring already says
   *"Authorization decisions remain the relying service's own"* (`identityAssertionVerifier.ts:19`).
2. **No mapping ⇒ no access.** A verified NovaForge user with no Pashupatastra directory entry gets
   `403`, never a default role. This is the `_require_identified_human` fail-closed posture
   (`lifecycle.py:1878`) extended one level.
3. **Verification failure is `401`, never a downgrade.** Mirrors the existing NONE-fails-closed
   precedent (`service.py:1169`).
4. **Pashupatastra stores a reference, not a replica.** A directory row keyed by NovaForge `sub`
   holding railway role and scope — no emails, no credentials, no passwords, no session state. This
   satisfies the "no duplicate user database" red line: it is a **domain assignment table**, not a
   user store.
5. **Assurance stays honest.** `AUTHENTICATED` is constructible only from a validated assertion,
   structurally, in the same way `SYSTEM_INTERNAL` is constructible only in-process
   (`actor.py:161-178`).

---

## 7. Identity assertion design

### 7.1 Minimum information Pashupatastra actually needs

| Field | Source | Needed? | Why |
|---|---|---|---|
| `sub` | assertion | **Required** | The only stable key for the domain directory lookup. |
| `iss` | assertion | **Required** | Multi-issuer safety; recorded in audit. |
| `aud` | assertion | **Required** | Must equal Pashupatastra's own audience or reject. |
| `exp` | assertion | **Required** | Request-time validity. |
| `iat` | assertion | **Required** | Lifetime cap + future-issue check. |
| `jti` | assertion | **Required** | Present and verified; a replay cache is optional given 300 s TTL (OQ-5). |
| `sid` | assertion | **Required** | The introspection key (`internal.ts:86`) and the audit session reference. |
| `auth_time` | assertion | **Recommended** | Enables a future freshness requirement for high-consequence actions. |
| `trust_level` | assertion | **Recommended** | A ready-made input for a future step-up rule (OQ-6). |
| `role` (NovaForge) | assertion | **Recorded only** | **Never mapped to `ActorRole`.** See Section 8. |
| `account_status` | assertion | Implicit | Verifier already rejects non-`ACTIVE` (`identityAssertionVerifier.ts:94-96`). |
| Display name | — | **Not needed** | Not in the assertion; not required for any decision. Avoid importing PII. |
| Tenant/org | — | **Not applicable** | NovaForge is single-tenant (`schema.prisma`: no tenant model). |

### 7.2 The four concepts that must not collapse

| Concept | Question | Owner | Where it lives |
|---|---|---|---|
| **IDENTITY** | "Who are you?" | NovaForge | assertion `sub` / `sid` |
| **ROLE** | "What kind of operator are you?" | Pashupatastra | `ActorRole` (`actor.py:66`) |
| **RAILWAY RESPONSIBILITY** | "Which assets are yours?" | Pashupatastra | **DOES NOT EXIST** (§9) |
| **PERMISSION** | "May you do this, here, now?" | Pashupatastra | `authorize()` (`authorization.py:109`) |

The assertion's `role` claim is the trap: it is a NovaForge **generic governance** role occupying a
field named exactly like Pashupatastra's **railway operational** role. Collapsing them would be the
single most likely implementation error in this slice.

### 7.3 Proposed `VerifiedPrincipal` (shape only — not a schema)

A new frozen value object, deliberately **not** an `Actor`, so that "authenticated" and "authorized
railway operator" remain two steps:

```
VerifiedPrincipal:
    subject      # assertion sub
    session_id   # assertion sid
    issuer       # assertion iss
    issued_at, expires_at, auth_time
    novaforge_role   # RECORDED, never mapped
    trust_level
```

It carries **no railway concept at all**. Converting it to an `Actor` requires the directory lookup
(⑥), and that conversion is the only place `AUTHENTICATED` may be produced.

---

## 8. Role mapping

### 8.1 The two vocabularies side by side

| NovaForge `Role` (`schema.prisma:10-16`) | Pashupatastra `ActorRole` (`actor.py:54-74`) |
|---|---|
| `SUPER_ADMIN` | `WORKER` |
| `ADMIN` | `ENGINEER` |
| `SENIOR_APPROVER` | `AUTHORITY` |
| `APPROVER` | `ADMIN` |
| `MEMBER` | `SYSTEM` |
| | `UNIDENTIFIED` |

These are **disjoint vocabularies**. NovaForge's is about *approval governance* — who may vote on a
`SensitiveAction`, weighted by `voteWeight` and gated by `ApprovalPolicy.eligibleRoles`
(`schema.prisma:182-190`). Pashupatastra's is about *railway operations* — who reports a defect, who
grants a possession, who works the track.

### 8.2 Does NovaForge `ADMIN` mean Pashupatastra `ADMIN`?

> **No. Explicitly and permanently no.**

The collision is **lexical, not semantic** — the enums are the proof. Concretely:

- NovaForge `ADMIN` is a user-management capability. `p0-6-identity-trust.test.ts:188` shows it is
  *not even* trusted for account status changes inside NovaForge (*"not by ADMIN, and never on
  yourself"*), and service accounts are `SUPER_ADMIN`-only (`serviceAccounts.ts:27`).
- Pashupatastra `ADMIN` is a railway operational role sitting in the same enum as `AUTHORITY` — the
  role that grants track possession and commits blocks (`lifecycle.py:1401`).
- Auto-mapping would mean **anyone who can administer user accounts can approve a track possession**.
  That is a privilege escalation across trust domains, and it is on the brief's red-line list.

**Rule: the assertion's `role` claim is recorded in the audit metadata and used for nothing else.**

### 8.3 Per-role origin

| `ActorRole` | Origin | Justification |
|---|---|---|
| `WORKER` | **Pashupatastra directory assignment** | Requires a real maintenance-crew assignment. No NovaForge concept corresponds. |
| `ENGINEER` | **Pashupatastra directory assignment** | Requires a division/corridor planning assignment. |
| `AUTHORITY` | **Pashupatastra directory assignment** | Highest consequence — grants possession, commits blocks, releases commitments (`lifecycle.py:1401,1808`). Must be a deliberate railway appointment. |
| `ADMIN` | **Pashupatastra directory assignment** | Must **not** inherit from NovaForge `ADMIN`/`SUPER_ADMIN` (§8.2). |
| `SYSTEM` | **Derived internally only** | Already structurally enforced (`actor.py:161-178`) and refused at HTTP (`deps.py:84-90`). Unchanged. |
| `UNIDENTIFIED` | **Derived internally only** | The no-credential fallback (`actor.py:263-275`). Never client-selected. |

**From the client: none.** **From NovaForge: none.** Every railway role is a Pashupatastra domain
assignment. This is the answer to "which should never come from the client" — *all of them*.

### 8.4 Can one authenticated user hold multiple railway roles?

**Yes, and the domain requires it.** A realistic Indian Railways deployment has a Section Engineer
who is the planning `ENGINEER` for several sections and the possession-granting `AUTHORITY` for one.
Forcing one global role per person would either over-privilege them everywhere or block legitimate
work.

But `Actor` holds exactly **one** `role` (`actor.py:125`), and the whole event schema is built on
that (`history.py:66-71`). Two shapes are compatible with the existing model:

- **(i) Multiple assignments, one active per request.** The directory holds
  `(sub, role, scope)` rows; the request resolves to exactly one `Actor`. Resolution is either
  derived from the target resource's section or declared and then verified against the directory.
  Preserves the `Actor` shape, the event schema, and the audit trail's clarity.
- **(ii) A multi-role Actor.** Breaks `Actor`'s single-role invariant, the four `job_events` columns,
  and makes every audit entry ambiguous about the capacity in which a person acted.

**(i) is strongly indicated by the existing evidence.** It requires no change to `Actor`,
`job_events`, or the frozen v1 response schemas. **Resolution strategy is OQ-4.**

No schema is proposed here, per the brief.

---

## 9. Railway domain responsibility

### 9.1 What exists

| Concept | Modeled? | Evidence |
|---|---|---|
| Corridor | **Yes** — `corridor_id` | `asset_association.py:138` |
| Section | **Yes** — authoritative, direction-independent, opaque id | `data/section_registry.py:1-33` |
| Track | **Yes** — identity kept separate from section; resource key is `(track_id, section_id)` | `section_registry.py:27-30`; `solver.py:485` |
| Asset | **Yes** — with `asset_section_id`, `section_match`, fingerprint | `asset_association.py:134-153` |
| Job | **Yes** — carries `job_section_id` | `asset_association.py:135` |
| Station | **Yes** — section resolvable by station pair | `section_registry.py:24-26` |
| Chainage (km) | **Yes** — `resolve_by_chainage`, fail-closed on ambiguity | `section_registry.py:27-33,52-62` |
| Division | **No** | — |
| **AUTHORITY → section** | **NO** | — |
| **WORKER → crew → section** | **NO** | — |
| **ENGINEER → division → sections** | **NO** | — |

### 9.2 The gap, stated precisely

**The railway asset graph is complete. The person-to-asset binding is entirely absent.**

Confirmed by search: no `assigned_to`, `assignee`, `crew`, `roster`, `responsible_for`, `owner_id`,
`actor_section`, `actor_scope` anywhere in `backend/app/`. `AUTHORITY` today is a *string the caller
typed into a header* (`deps.py:33`), not a person with jurisdiction over anything.

### 9.3 Minimum missing concepts (concepts only — no data model, per the brief)

1. **Actor directory entry** — a stable Pashupatastra `actor_id` keyed by NovaForge `sub`. Needed
   because `actor_id` is the audit key (`history.py:66`) and must stay stable and railway-meaningful.
2. **Role assignment** — `(actor_id, ActorRole, scope)`. Needed because no railway role may originate
   from NovaForge (§8.3).
3. **Scope expression** — must express at least: one section; a set of sections; a corridor. The
   three worked examples in the brief map onto:
   - `AUTHORITY` → Jaipur–Ajmer → Section S3 → **single section**
   - `WORKER` → maintenance crew → Section S3 → **single section, via a crew**
   - `ENGINEER` → Jaipur division → multiple sections → **set, likely corridor- or division-derived**
4. **Crew** (for `WORKER`) — the brief's own example routes worker scope *through* a crew, which is
   also how railway maintenance actually works. Whether crew is a first-class concept or worker
   scope is assigned directly is **OQ-3**.
5. **Division** — needed only if `ENGINEER` scope is division-derived rather than an explicit section
   set. **Deferred**; corridor + section may suffice for the demo.
6. **Scope resolution for a job** — given a job, which scope value must an actor hold? `job_section_id`
   is the natural answer, but note `asset_section_id` may differ and `section_match` may be false
   (`asset_association.py:42-46`). **Which section governs authorization on a cross-section
   association is OQ-2 — and it is a genuine safety question, not a modeling detail.**

### 9.4 Why this cannot be deferred past enforcement

Scope must exist **before** `UnenforcedPolicy` is replaced. A role-only enforcing policy would permit
*any* `AUTHORITY` to approve possession on *any* section in the network — arguably worse than today's
honest non-enforcement, because it would *look* enforced. This ordering constraint drives Section 20.

---

## 10. Authorization model

### 10.1 Can the current `JobAction` set carry the brief's required actions?

| Required action | Present? | Mapping |
|---|---|---|
| `CREATE_JOB` | Yes | `REPORT_JOB` (`authorization.py:41`) |
| `APPROVE_PROPOSAL` | Yes, **by reuse** | `COMMIT_BLOCK` — approval *is* the commit transition; documented at `authorization.py:45-50` |
| `REJECT_PROPOSAL` | Yes | `authorization.py:51` |
| `POSTPONE_PROPOSAL` | Yes | `authorization.py:60` |
| `RELEASE_COMMITTED_BLOCK` | Yes | `authorization.py:70` — deliberately its own action (`authorization.py:64-69`) |
| `START_EXECUTION` | Yes | `authorization.py:60` |
| `COMPLETE_JOB` | Yes | `authorization.py:37` |
| `REPORT_EXECUTION_NOT_COMPLETED` | Yes | `authorization.py:61` |
| `READ_OBLIGATIONS` | Yes | `READ_JOB_OBLIGATIONS` (`authorization.py:79`) |
| `READ_EXECUTION` | Yes | `READ_JOB_EXECUTION` (`authorization.py:62`) |
| `READ_PROPOSAL` | Yes | `READ_BLOCK_PROPOSAL` (`authorization.py:43`) |
| `READ_JOB` | Yes | `READ_JOB_HISTORY` (`authorization.py:42`) |

**The action vocabulary is complete. No new `JobAction` member is required for Slice 10.** This is a
strong result — the seam was designed for this.

One judgment to flag: `COMMIT_BLOCK` serves both authority approval and in-process commit. If a
future policy must grant "may approve an authority proposal" separately from "may commit a block",
the reuse documented at `authorization.py:45-50` will need revisiting. It is adequate for Slice 10
and is listed as a deferred item.

### 10.2 What the model must become

Evaluating each level against the brief's own scenarios:

| Level | Sufficient? | Why |
|---|---|---|
| **RBAC only** | **No** | Cannot express "Authority A may not approve outside their section." Any `AUTHORITY` could approve anything. |
| **RBAC + resource scope** | **No** | Cannot express "a committed block may be released but a completed one may not" — that is a state question. |
| **RBAC + scope + state** | **Nearly** | Covers scope and lifecycle. Cannot express "the person who reported it may not approve it." |
| **RBAC + scope + state + SoD** | **Yes** | Covers every scenario in the brief. |

**Required end state: RBAC + resource scope + state + segregation of duties.**

Scenario-by-scenario, as the brief demands:

| Scenario | Needs |
|---|---|
| Authority A must not approve outside their section | scope |
| Worker A must not execute outside assigned scope | scope (crew-derived) |
| Worker must not approve their own proposal | **SoD** + role |
| Engineer may replan but not approve authority decisions | role |
| Admin manages users but gains no operational approval | role (and §8.2 — no NovaForge inheritance) |

### 10.3 The structural consequence

`AuthorizationPolicy.authorize(self, actor, action)` (`authorization.py:109`) takes **no resource**.
Scope and state cannot be decided without one.

The seam must gain a resource/context parameter. Because `UnenforcedPolicy` ignores its arguments
entirely (`authorization.py:123-124`), this can be done **additively** — an optional context
parameter leaves all 19 existing call sites valid and the existing denying test policy
(`tests/test_job_lifecycle_accountability.py`) working. The call sites must then be *progressively*
enriched with the resource each one already has in hand.

**This is the single largest code change in Slice 10, and it is why enforcement must come last.**

### 10.4 State is already available

Every guarded transition already loads the job before acting, and the lifecycle module owns the
legal-transition table (`lifecycle.py`). State-aware authorization does not need new data — only a
context object carrying what the call site already holds.

---

## 11. Scope model

Concepts only; no schema, per the brief.

### 11.1 Scope shapes the domain demands

| Role | Example | Shape |
|---|---|---|
| `AUTHORITY` | Jaipur–Ajmer, Section S3 | one section (possibly several) |
| `WORKER` | maintenance crew, Section S3 | section set, via crew |
| `ENGINEER` | Jaipur division, multiple sections | section set, corridor- or division-derived |
| `ADMIN` | — | **no operational scope** (§8.2) |

### 11.2 Minimum viable scope expression

The smallest thing covering all three: **a set of `section_id` values**, optionally *derived* from a
`corridor_id` rather than enumerated.

This works because:
- `section_id` is already the authoritative, stable, opaque railway identity
  (`section_registry.py:5-8`)
- every job already carries `job_section_id` (`asset_association.py:135`)
- the registry can already expand corridor/track/chainage → section (`section_registry.py:23-33`)

No new railway vocabulary is required. **Scope is a set of things the system already names.**

### 11.3 The unresolved edge — and it is a safety question

`asset_association.py:42-46` documents that an asset may sit **across a section boundary** from the
job, recording `asset_section_id`, `job_section_id`, `section_match`, with a cross-section
association *"visible in the job and in JOB_CREATED — never silent."*

So for authorization: **which section governs?**

- `job_section_id` — where the work is reported
- `asset_section_id` — where the asset actually is
- **both** — fail-closed: require authority over each

The physical possession affects the asset's section; the report belongs to the job's. Requiring both
is the conservative reading and fits the codebase's fail-closed posture (`service.py:1169`;
`lifecycle.py:1878`; `section_registry.py:52-62`). **This is OQ-2 and needs a domain expert's ruling,
not an auditor's preference.**

---

## 12. Segregation-of-duties analysis

### 12.1 Does the system need it?

The chain the brief names — same person REPORT → APPROVE → EXECUTE → COMPLETE — is today **entirely
possible**: `create_job` is deliberately unguarded (`lifecycle.py:1908`), and every subsequent step
requires only *an* identified human, never a *different* one (`lifecycle.py:1878-1920`).

Railway possession is a life-safety control. One person originating, self-approving, self-executing
and self-certifying a track possession defeats its purpose. **Architecturally, yes — SoD is
required.**

Per the brief's instruction, it is **not** automatically enforced here.

### 12.2 Current requirements vs. demo requirements — they conflict

| | Position | Evidence |
|---|---|---|
| **Current code** | No SoD. No constraint relates one event's actor to another's. | §12.1 |
| **Safety architecture** | REPORT/APPROVE and APPROVE/EXECUTE must be different people. | §12.3 |
| **Demo requirements** | **Undetermined — and the reason matters.** | see below |

The brief asks specifically for *demo requirements*. They were checked, and the answer is that they
do not exist yet: **the demo documentation specifies no actors at all.** A search for `actor`,
`X-Actor-Id`, `X-Actor-Role`, `WORKER-`, `AUTHORITY-` or `ENGINEER-` across `docs/demo-script.md`
(160 lines), `docs/demo-runbook.md` (105 lines) and `PASHUPATASTRA_FINAL_DEMO_PLAYBOOK.md`
(1452 lines) returns **zero** matches for any actor identifier or header.

This is consistent with those documents predating the Slice 1 actor model, and it has a concrete
consequence: **the demo's actor story is unwritten, so SoD is not yet in conflict with anything —
but it will be the moment someone writes the demo script's actual header values.**

The decision is therefore cheap to make *now* and expensive to retrofit later. If the demo is scripted
with distinct actor ids per phase (e.g. `WORKER-042` reports, `AUTHORITY-017` approves, `WORKER-042`
executes), SoD is fully compatible with it and **OQ-7 closes without an exception**. That costs
nothing today and is the auditor's recommendation. Only if a single operator id must drive the entire
lifecycle does SoD need to become mode-scoped.

**OQ-7 remains open, but it is a demo-scripting decision, not an architectural conflict.**

### 12.3 Which separations actually matter

| Pair | Required? | Reasoning |
|---|---|---|
| REPORT ≠ APPROVE | **Strongly** | Prevents manufacturing one's own possession grant. |
| APPROVE ≠ EXECUTE | **Strongly** | Approval is a safety control over the executing party. |
| EXECUTE ≠ COMPLETE | **No** | The crew that does the work reports its completion; evidence-gating (Slice 5) is the control here, not separation. |
| REPORT ≠ EXECUTE | **No** | A worker who spots a defect and later fixes it is normal, desirable railway practice. |

Note this is *partly* already implied by role: approval requires `AUTHORITY`, execution is `WORKER`
work. SoD adds the case where **one person holds both roles** (§8.4) — precisely the multi-role
scenario, which is exactly why SoD and multi-role assignment must be decided together.

### 12.4 Where should the rule live?

| Option | Assessment |
|---|---|
| **NovaForge generic permission** | **No.** NovaForge cannot see job lifecycles, sections, or `job_events`. Its `ApprovalPolicy` quorum machinery (`schema.prisma:182-190`) is generic *vote* separation, not railway conflict-of-interest. Putting it there would move railway business rules into the auth service — a red line. |
| **Pashupatastra domain rule** | **Yes.** The data needed — who performed which prior action on *this* job — lives only in `job_events` (`history.py`), which only Pashupatastra has. |

**SoD belongs in Pashupatastra's authorization policy**, evaluated against the job's own event
history. This is the clearest illustration of the ownership boundary in the whole audit: the same
*shape* of rule ("two different people") exists in both systems, but only Pashupatastra holds the
facts that make the railway version decidable.

### 12.5 Architectural implication

An SoD-capable policy must read prior events **before** deciding — meaning `authorize()` needs not
just a resource but access to job history. That is a heavier dependency than scope or state, and
argues for SoD being the **last** rule added, after the context parameter exists. See Section 20.

---

## 13. Audit identity model

### 13.1 How an authenticated actor should appear

Existing columns, unchanged in shape:

| Column | Authenticated value |
|---|---|
| `actor_id` | Pashupatastra railway actor id from the directory — **not** the NovaForge `sub` |
| `actor_role` | resolved `ActorRole` |
| `actor_kind` | `HUMAN` (derived, `actor.py:206`) |
| `actor_assurance` | **`AUTHENTICATED`** |

Keeping `actor_id` railway-meaningful preserves history readability and keeps existing events
comparable with new ones.

### 13.2 Where `AUTHENTICATED` may be created

Exactly one place: the conversion of a validated `VerifiedPrincipal` + directory entry into an
`Actor`. Enforced the way `SYSTEM_INTERNAL` already is (`actor.py:161-178`) — by `Actor.__post_init__`
invariants, so no other code path can produce it, not merely by convention.

Invariant to add (mirroring the existing human-role clause at `actor.py:190-194`): a human role may
carry `DECLARED_UNVERIFIED` **or** `AUTHENTICATED`; `UNIDENTIFIED` may carry only `NONE`; `SYSTEM`
only `SYSTEM_INTERNAL`.

### 13.3 What else the event should record

Additively, inside the **existing** `metadata_json` column (`history.py:79`) — **no migration**:

| Field | Record? | Why |
|---|---|---|
| NovaForge `sub` | **Yes** | The stable external reference; lets an auditor trace a railway action to a NovaForge principal. |
| `iss` | **Yes** | Multi-issuer disambiguation; proves which authority vouched. |
| `sid` | **Yes** | Ties the action to an authentication session — the key for a post-incident sweep of everything one compromised session did. |
| `auth_time` | Optional | Supports "how fresh was this authentication?" forensics. |
| NovaForge `role` | **Yes, as a recorded fact** | Proves the mapping was *not* taken from it (§8.2). |
| Domain role | Already in `actor_role` | Do not duplicate. |
| Domain scope | **Yes, once it exists** | The scope under which authority was exercised — otherwise a later reassignment makes history unreadable. |
| **Raw assertion / access token** | **ABSOLUTELY NOT** | Red line. `job_events` is append-only (`history.py:92`) and read back through the history API (`jobs/router.py:374`) — a token written there is permanently exfiltrated. |

### 13.4 The stable reference, stated plainly

> **Record `sub`, `iss`, `sid`. Never the assertion, never any part of it, never a hash of it that
> could serve as a bearer proof.**

`sub` is a NovaForge row id (`schema.prisma:55`), not a credential; disclosure forges nothing —
exactly the reasoning NovaForge applies to its own `jti` (`jwt.ts:60-68`).

### 13.5 Compatibility note

Adding metadata does not change the four actor columns, so history API response shape is unaffected.
`schema_version` (`history.py:60`) exists to record the change; whether the additional metadata
warrants a version bump is a deferred implementation decision.

---

## 14. System / service identity

### 14.1 Today

`SYSTEM_INTERNAL` is genuinely the strongest identity claim in Pashupatastra, because it is the only
one the system generates itself. It is constructible only in-process (`actor.py:242-260`), structurally
barred from human roles (`actor.py:161-178`), and refused at the HTTP boundary (`deps.py:84-90`).

**No change is required to in-process SYSTEM identity in Slice 10.** Components remain
`SYSTEM:OPTIMIZER`, `SYSTEM:SCORER` (`actor.py:279-281`).

### 14.2 The distinction that must hold

| Direction | Mechanism | Status |
|---|---|---|
| Pashupatastra → NovaForge | `nfsvc_…` service credential, scopes `identity:assert` + `identity:introspect` | **Exists** (`serviceAuth.ts:9,16`); needs provisioning (SUPER_ADMIN only — `serviceAccounts.ts:37,70`) |
| In-process Pashupatastra components | `system_actor()` | **Exists**, unchanged |
| A future **separate** Pashupatastra process (notification runner, live-train provider) | — | **Does not exist. Do not invent one.** |

### 14.3 Future out-of-process services

The brief rightly says not to implement a mechanism unsupported by actual infrastructure. Evidence:

- The **notification runner** does not exist as a process. `notifications.py:271,325` shows delivery
  refuses when no provider is configured — there is no separate service to authenticate.
- The **live-train provider** is a data-provenance concern (`data/provenance.py`, `train_provider.py`),
  not an authenticated caller today.
- The **optimizer** is in-process (`optimizer/solver.py`).

**Conclusion: no service-identity mechanism should be built in Slice 10 beyond Pashupatastra holding
one NovaForge service credential.** When a genuinely separate process appears, the choice among
signed service assertion / API key / mTLS should be made then, with its deployment topology known.
Deferred.

### 14.4 One credential-handling requirement

Pashupatastra's `nfsvc_` secret is a **high-value bearer credential**: it permits exchanging any live
user session for an assertion. It must be environment-configured, never committed, never logged,
never placed in `job_events`. NovaForge supports rotation (create a new credential, then revoke the
old — `serviceAccounts.ts:70,96`) and records `lastUsedAt` (`serviceAuth.ts:69`).

---

## 15. Session / revocation semantics

### 15.1 The core question

> **Can Pashupatastra continue trusting an unexpired assertion after NovaForge logout?**

**Offline verification alone: yes, for up to 300 seconds.** This is a real, bounded property of
Option D, not a flaw — `identityAssertionVerifier.ts:17-19` states it: *"Freshness beyond exp (e.g.
whether the session was revoked a minute ago) requires NovaForge's introspection endpoint."*

**The architectural answer must therefore be action-dependent**, because NovaForge already supplies
the tool to close the window (`internal.ts:74`) and it costs a network round trip.

### 15.2 Proposed semantics

| Action class | Freshness requirement | Rationale |
|---|---|---|
| Reads (`READ_JOB_HISTORY`, `READ_BLOCK_PROPOSAL`, `READ_JOB_EXECUTION`, `READ_JOB_OBLIGATIONS`, `READ_OPTIMIZATION_RUN`) | Assertion `exp` only | ≤300 s of stale read access is an acceptable, bounded exposure. |
| Ordinary mutations (`REPORT_JOB`, `REQUEST_OPTIMIZATION`, `ASSIGN_SCHEDULE`) | Assertion `exp` only | Reversible; recorded; low consequence. |
| **High-consequence mutations** — `COMMIT_BLOCK`/approve, `REJECT_PROPOSAL`, `POSTPONE_PROPOSAL`, `RELEASE_COMMITTED_BLOCK`, `START_EXECUTION`, `COMPLETE_JOB` | **Introspect before acting** | These are exactly the set already guarded by `_require_identified_human` (`lifecycle.py:1401,1530,1652,1808,2058,2204,2309`) — the codebase has already identified them as the consequential ones. |

That symmetry is a strong signal: the existing guard list is the right introspection list.

### 15.3 Scenario-by-scenario

| Scenario | Semantics |
|---|---|
| User logs out of NovaForge, Pashupatastra "session" active | Pashupatastra holds **no session of its own** — it must not create one. Next high-consequence action introspects → `active: false` → `401`. Reads fail once the assertion expires (≤300 s). |
| Approval page open in the browser | The page holds no authority. The *action* is authorized when submitted, not when rendered. |
| Request in flight at logout moment | Completes if already past its authorization point. Bounded and acceptable — NovaForge's own model has the same property. |
| Token copied / stolen | Usable ≤300 s, **only at Pashupatastra's audience** (`internal.ts:57`). Introspection closes it immediately for high-consequence actions once the session is revoked. |
| User disabled | NovaForge refuses to issue (`identityAssertion.ts:20`); `isSessionLive` fails (`requireAuth.ts:45`); introspection returns `active: false` (`internal.ts:93`). Disabling also deletes sessions (`requireAuth.ts:35-38`). |
| NovaForge role changes | **Irrelevant to Pashupatastra authorization by design** (§8.2). It changes only the recorded metadata fact. |
| **Railway assignment changes** | **Pashupatastra's own revocation surface** — takes effect on the next directory lookup, i.e. the next request. Stronger than NovaForge-side revocation. |

### 15.4 The logout gap Pashupatastra must not create

If Pashupatastra ever issues its **own** session cookie after verifying an assertion, NovaForge
logout stops meaning anything and the ≤300 s bound becomes unbounded.

> **Pashupatastra must hold no independent session.** Each request carries its own proof.

This is also why the existing stateless `request_actor` design (`deps.py:33`) is the right seam: it
is already per-request, with no session concept to accidentally extend.

---

## 16. Failure-mode matrix

**Governing rule:** every authentication failure is **FAIL CLOSED**. A failed authentication must
never become `DECLARED_UNVERIFIED` — that would silently convert a rejected credential into an
accepted demo actor, destroying the meaning of the boundary. The codebase already sets this
precedent twice (`service.py:1169`; `lifecycle.py:1878`).

| # | Failure | Posture | Response | Why |
|---|---|---|---|---|
| FM-1 | **NovaForge unavailable** (assertion exchange) | **FAIL CLOSED** | `503` | Cannot establish identity. Availability must never buy authority. |
| FM-2 | **NovaForge unavailable** (introspection, high-consequence action) | **FAIL CLOSED** | `503` | A track possession must not be approved because the auth service was down. |
| FM-3 | **NovaForge unavailable** (read with a valid unexpired assertion) | **FAIL OPEN, bounded** | `200` | Offline verification is the designed property (`identityAssertionVerifier.ts:9-12`); bound is `exp` ≤300 s. |
| FM-4 | Invalid token | FAIL CLOSED | `401` | `identityAssertionVerifier.ts:61-62` |
| FM-5 | Expired token | FAIL CLOSED | `401` | `identityAssertionVerifier.ts:73-79` |
| FM-6 | Wrong audience | FAIL CLOSED | `401` | Cross-service replay. Tested `p0-6:325`. |
| FM-7 | Wrong issuer | FAIL CLOSED | `401` | Tested `p0-6:321`. |
| FM-8 | Signature failure | FAIL CLOSED | `401` | Tested `p0-6:286` (attacker key reusing trusted `kid`), `:291` (`alg=none`), `:300` (HS256 confusion). |
| FM-9 | Key rotation, unknown `kid` | FAIL CLOSED, then refresh | `401`; re-fetch JWKS with rate limiting, retry once | NovaForge publishes old keys during rotation (`identityKeys.ts:17-18`). Refetch must be rate-limited or it becomes a DoS amplifier against NovaForge. |
| FM-10 | JWKS unreachable, cache warm | FAIL OPEN, bounded | continue on cache | `Cache-Control: max-age=300` (`app.ts:58`). Bound the cache; never trust it indefinitely. |
| FM-11 | JWKS unreachable, cache cold | FAIL CLOSED | `503` | No keys, no verification, no trust. |
| FM-12 | Replayed one-time code | FAIL CLOSED | `401` | **Depends on ② (OQ-1).** Single-use consumption must be atomic. |
| FM-13 | Duplicated callback | FAIL CLOSED on the second | `401` | Same as FM-12. |
| FM-14 | CSRF on the front-channel | FAIL CLOSED | `401` | Requires `state`; **part of OQ-1.** |
| FM-15 | Stolen assertion | FAIL CLOSED after ≤300 s; immediately for high-consequence actions via introspection | `401` | `internal.ts:74` |
| FM-16 | Disabled user | FAIL CLOSED | `401` | `identityAssertion.ts:20`; `requireAuth.ts:45`; `internal.ts:93` |
| FM-17 | NovaForge role changed | **No effect on authorization** | — | By design (§8.2). Metadata only. |
| FM-18 | **Railway assignment changed / removed** | FAIL CLOSED on next request | `403` | Directory lookup is per-request. Pashupatastra's own revocation. |
| FM-19 | Stale domain mapping | FAIL CLOSED | `403` | Never fall back to a default role. No mapping ⇒ no access. |
| FM-20 | Clock skew | Tolerate ±30 s, then FAIL CLOSED | `401` | `identityAssertionVerifier.ts:56`. Do not widen. |
| FM-21 | Pashupatastra unavailable | N/A | — | No authorization decision is made; nothing is granted. |
| FM-22 | **NovaForge compromise** | **Total identity compromise — accepted, bounded by domain** | — | An attacker controlling the signing key mints any `sub`. **Pashupatastra's mitigation is the directory:** a forged `sub` with no railway assignment still gets `403` (FM-19), and railway scope still bounds a forged *known* `sub`. Authentication compromise does not equal unlimited railway authority. |
| FM-23 | **`/internal` exposed publicly** | FAIL CLOSED by credential | `401` | Service auth is required regardless (`serviceAuth.ts:74-84`); network isolation is stated as an *assumption* only (`internal.ts:12-13`). Defense in depth, not the primary control. |
| FM-24 | Pashupatastra service credential leaked | **Critical** | revoke | Permits exchanging any live session for an assertion. Revocation: `serviceAccounts.ts:96`. §14.4. |

### 16.1 The prohibited transition, stated once more

```
verification failure  ──✗──▶  DECLARED_UNVERIFIED
verification failure  ──✓──▶  401
```

There must be **no code path** from a presented-but-invalid credential to an accepted actor of any
kind. A caller who presents nothing may still be `UNIDENTIFIED`/`NONE` in demo mode (§17); a caller
who presents something invalid is rejected outright.

---

## 17. Demo-mode boundary

### 17.1 The requirement

Demo support must not be removed (the brief says so, and `docs/demo-runbook.md`,
`docs/demo-script.md`, `PASHUPATASTRA_FINAL_DEMO_PLAYBOOK.md` depend on it), and a demo actor must
never be able to masquerade as authenticated.

### 17.2 Proposed boundary

| | Demo mode | Production mode |
|---|---|---|
| Credential | none | NovaForge assertion |
| `X-Actor-*` headers | accepted | **rejected** (Section 18) |
| Assurance | `DECLARED_UNVERIFIED` / `NONE` | `AUTHENTICATED` |
| Selection | **deployment configuration only** | default |
| Enforcing policy | role-only or unenforced | RBAC + scope + state + SoD |

### 17.3 The four rules that keep the boundary honest

1. **Mode is a deployment setting, never a request property.** No header, parameter or body field may
   select demo mode — otherwise it is simply an authentication bypass.
2. **A demo actor can never reach `AUTHENTICATED`.** Guaranteed structurally, because `AUTHENTICATED`
   is producible only from a validated assertion (§13.2) — the same technique that already prevents
   a human from claiming `SYSTEM_INTERNAL` (`actor.py:174-194`).
3. **In production, `X-Actor-*` are rejected, not ignored.** See §18.3.
4. **The audit trail distinguishes them permanently.** `actor_assurance` is already `NOT NULL` on
   every event (`history.py:71`), so a demo-era event and a production-era event are distinguishable
   forever — including retrospectively, since existing history is all `DECLARED_UNVERIFIED`/`NONE`.

### 17.4 Why this is safe

Demo mode is not a weakened version of authentication; it is the **honest absence** of it. That is
exactly what today's system already is (§2.4). Slice 10 adds a mode that *has* authentication without
changing what the absence means.

**Recommended default: authentication required.** Demo mode should be opt-in via explicit
configuration, so a misconfigured production deployment fails closed into "no access" rather than
open into "anyone may declare themselves AUTHORITY".

---

## 18. API contract impact

### 18.1 What is frozen

The v1 contract is frozen (`docs/api-contract-v1.md`, `docs/openapi-v1.json`), with route inventory,
error envelope and actor-header semantics pinned by `backend/tests/test_slice7_contract_freeze.py`:
`test_exact_v1_and_legacy_route_inventory` (`:164`),
`test_actor_headers_are_documented_on_every_actor_aware_v1_route` (`:670`),
`test_actor_headers_are_both_or_neither` (`:694`),
`test_a_caller_can_never_declare_the_system_role` (`:701`).

### 18.2 What can be introduced additively

| Change | Additive? | Notes |
|---|---|---|
| A new request header carrying the assertion (e.g. `Authorization: Bearer <assertion>`) | **Yes** | New optional header. No route, schema or error-envelope change. |
| Authentication as a FastAPI dependency | **Yes** | `request_actor` (`deps.py:33`) is already a dependency; it is replaced in place. |
| New error codes for auth failures | **Yes** | The envelope is `{code, detail}` (`test_slice7:239`), and `ErrorCode`'s own docstring states the rule: *"Additions are compatible; renames and removals require a new API version."* (`backend/app/api/errors.py:32-34`). |
| `AUTHENTICATED` appearing in history responses | **Yes** | **Verified against the frozen spec:** `ActorResponse.assurance` in `docs/openapi-v1.json:10-13` is declared as a bare `{"type": "string"}` with **no `enum` constraint**. Adding a fourth value therefore does not alter the published schema at all. Same for `role` and `kind` (`openapi-v1.json:14-21`). |
| Response schema changes | **None required** | Confirmed three ways: the OpenAPI schema is unconstrained (`openapi-v1.json:4-30`), the response model types `assurance` as `str` (`models.py:314`), and the history read passes the stored string through (`history.py:279`). |

The unconstrained `assurance` field is a material finding, not a lucky accident — it is what makes
§18.4's "no v2 required" conclusion safe rather than merely plausible. Had the spec pinned a
three-value `enum`, adding `AUTHENTICATED` would have been a published-schema change and this section
would read differently.

Note, however, that the **descriptions** of the `X-Actor-Id`/`X-Actor-Role` header parameters are
pinned throughout `openapi-v1.json` (e.g. `:2421`, `:2434`, and ~20 further occurrences) and state
*"NOT verified: recorded with DECLARED_UNVERIFIED assurance."* Those descriptions become
mode-dependent and will need updating when 10.3 lands — a documentation change, not a schema change.

### 18.3 Should authenticated requests reject caller-supplied actor headers?

> **Reject with `400`. Do not ignore.**

Silently ignoring them means a client believing it is acting as `AUTHORITY-017` while the server acts
as someone else — a dangerous divergence in a system where the actor is the audit record. An explicit
refusal surfaces the client bug immediately.

This mirrors the existing treatment of a caller-declared SYSTEM role — refused with `403`, never
ignored (`deps.py:84-90`).

### 18.4 Is a v2 required?

**No.** Every change above is additive: no route changes, no response-shape changes, no error-envelope
changes. The behavioral change — headers refused in production mode — is **mode-scoped**, not
version-scoped, and demo mode preserves v1 semantics exactly.

However, `test_slice7_contract_freeze.py:670,694,701` assert the *current* header behavior. Those
tests remain correct **for demo mode**; production-mode behavior is new and needs its own tests
alongside them. **Nothing in the frozen contract needs to be deleted.** This is an important result:
the freeze is compatible with Slice 10.

### 18.5 OpenAPI

`docs/openapi-v1.json` is **not modified in this gate**. When implemented, the new header is an
addition to existing operations, and the route inventory test (`test_slice7:164`) must continue to
pass unchanged.

---

## 19. NovaForge ↔ Pashupatastra ownership matrix

Every cell justified from repository evidence, as the brief requires.

| Capability | NovaForge | Pashupatastra | Evidence |
|---|---|---|---|
| **Login** | **Owns** | None | `routes/auth.ts:388,418,579,754` — passkey, TOTP, recovery code. Pashupatastra has no login surface at all. |
| **Password / credential handling** | **Owns** | **Never** | `Credential` model with WebAuthn keys, encrypted TOTP secrets (`schema.prisma:79-94`; `lib/totpSecretCrypto.ts`). Pashupatastra stores no credential of any kind. |
| **Session** | **Owns** | **Must hold none** | `Session` (`schema.prisma:129-137`); authoritative per-request check (`requireAuth.ts:39-48`). Pashupatastra is per-request stateless (`deps.py:33`) and §15.4 requires it stays so. |
| **Identity** | **Owns** (`sub`, `sid`) | **Maps only** | `identityAssertion.ts:50`. Pashupatastra resolves `sub` → railway actor; it does not define who a person is. |
| **Authentication proof** | **Issues** — ES256, signs | **Verifies** — public key only | `identityKeys.ts:9-11`: a public key lets a service verify but never mint. Verifier: `identityAssertionVerifier.ts:52-108`. |
| **Generic roles** | **Owns** | **Records, never maps** | `schema.prisma:10-16` (`SUPER_ADMIN…MEMBER`) vs `actor.py:54-74` (`WORKER…UNIDENTIFIED`) — disjoint. §8.2. |
| **Generic permissions** | **Owns** (approval quorum) | None | `ApprovalPolicy.eligibleRoles`, `quorumType`, `minApprovals` (`schema.prisma:182-190`). Not used by Pashupatastra. |
| **Railway role** | **Never** | **Owns** | No railway concept exists in NovaForge; `ActorRole` is Pashupatastra's (`actor.py:54-74`). §8.3. |
| **Railway section assignment** | **Never** | **Owns** (**does not yet exist**) | `SectionRegistry` is *"the one authority for railway section identity"* (`section_registry.py:1-8`). Person→section binding absent (§9.2). |
| **Job authorization** | **Never** | **Owns** | 19 `authorize()` call sites, all Pashupatastra (`service.py:761…2203`; `optimization.py:413`). NovaForge's verifier defers explicitly: *"Authorization decisions remain the relying service's own"* (`identityAssertionVerifier.ts:19`). |
| **Approval consequence** | **Never** | **Owns** | Approval *is* the block-commit transition (`authorization.py:45-50`; `lifecycle.py:1401`) — a scheduling consequence NovaForge cannot see. |
| **Execution consequence** | **Never** | **Owns** | Evidence-gated execution (`jobs/execution.py`; `lifecycle.py:2058,2204,2309`). |
| **Audit event** | **Owns its own** (hash-chained) | **Owns its own** (append-only) | Two independent trails: `AuditLog` + `AuditChainHead` with `prevHash`/`seq` (`schema.prisma:238-274`) vs `job_events` with append-only guards (`history.py:57-92`). Neither should replace the other; §13.3 links them via `sub`/`sid`. |
| **Notification recipient** | **Could own** (addresses) | **Owns** (which *role* is told) | Pashupatastra addresses by role and fabricates nothing (`notifications.py:55,111,199,325`). Resolving a role+scope to an individual's address would need a NovaForge user directory — **deferred, and the red line against fabricating recipients holds.** |

### 19.1 The one-line test for any future cell

> If answering the question requires knowing what a **section**, a **possession**, or a **block** is,
> it is Pashupatastra's. If it requires knowing a **credential**, a **session**, or a **signing key**,
> it is NovaForge's.

---

## 20. Minimum implementation slices

Ordering is derived from dependency evidence, not convenience. The brief's suggested order is adjusted
in two places, with reasons given.

### 10.1 — Assertion verification seam

| | |
|---|---|
| **Purpose** | Turn an opaque token into a `VerifiedPrincipal`, or reject. No domain concept touched. |
| **Files** | New `backend/app/identity/assertion.py` (+ JWKS cache). Nothing existing modified. |
| **Data** | None. |
| **API** | None. |
| **Boundary** | Establishes cryptographic proof. **The security core of Slice 10.** |
| **Tests** | Port NovaForge's rejection matrix (`p0-6-identity-trust.test.ts:282-344`) into pytest: `alg=none`, HS256 confusion, attacker key with trusted `kid`, wrong `iss`/`aud`, expiry, over-long lifetime, future `iat`, wrong `token_use`, non-ACTIVE principal, rotation. Test vectors can be generated from NovaForge's own signing code. |
| **Depends on** | Nothing. **Startable today.** |
| **Deferred** | Introspection; anything domain-related. |

*Why first:* zero coupling, highest security value, and independently testable without NovaForge
running.

### 10.2 — Domain identity directory

| | |
|---|---|
| **Purpose** | `sub` → (`actor_id`, `ActorRole`, scope). |
| **Files** | New `backend/app/identity/directory.py`; a seeded fixture for demo. |
| **Data** | **New persistent concept — requires a schema decision (deferred to implementation approval).** |
| **API** | None (admin surface deferred). |
| **Boundary** | **Where NovaForge identity becomes railway authority.** Fail-closed: no mapping ⇒ `403`. |
| **Tests** | unknown `sub` → denied; no default role ever; NovaForge `role` never consulted (§8.2); multi-assignment resolution (OQ-4). |
| **Depends on** | 10.1 for its input shape. |
| **Deferred** | Directory management UI/API; assignment lifecycle/audit. |

*Why second, ahead of the brief's ordering:* `Actor` requires a `role`, and **no railway role may come
from NovaForge** (§8.3). Without the directory there is nothing from which to construct an
authenticated `Actor`. The brief lists "Authenticated Actor construction" before "Domain identity
mapping"; the evidence inverts it.

### 10.3 — `AUTHENTICATED` assurance and authenticated Actor construction

| | |
|---|---|
| **Purpose** | Add the enum member, widen the invariant, replace `request_actor`. |
| **Files** | `identity/actor.py` (enum + `__post_init__` clause at `:194-199`), `api/deps.py` (`request_actor`), a new constructor `authenticated_actor()`. |
| **Data** | None. |
| **API** | Additive: new auth header; production mode rejects `X-Actor-*` with `400` (§18.3). |
| **Boundary** | **The `X-Actor-Id` red line is enforced here**, at the single entry point. |
| **Tests that will need updating (do not touch now; listed for planning)** | **Two** tests assert the enum's exact membership and both must change together: `backend/tests/test_actor_identity.py:86` (`test_no_authenticated_assurance_exists_yet`) **and** `backend/tests/test_slice7_contract_freeze.py:745` (`test_no_authenticated_assurance_exists`). These are the only two tests whose *purpose* is superseded. Every other assurance test stays valid: `test_actor_identity.py:107,114,119,121` and `test_slice7_contract_freeze.py:738` (which asserts a header-declared actor is recorded `DECLARED_UNVERIFIED` — still exactly right for demo mode). `test_slice7_contract_freeze.py:670/694/701` likewise remain correct **for demo mode** and gain production-mode counterparts rather than being replaced. |
| **New tests** | `AUTHENTICATED` unreachable without a validated assertion; a demo actor can never reach it; failed verification is `401`, never a downgrade; `X-Actor-*` refused in production mode. |
| **Depends on** | 10.1 + 10.2. |
| **Deferred** | Enforcement; scope. |

### 10.4 — Authentication-aware audit events

| | |
|---|---|
| **Purpose** | Record `sub`, `iss`, `sid` (and the NovaForge `role` as a recorded fact) in event metadata. |
| **Files** | `jobs/events.py`, `jobs/history.py` (**write path only**). |
| **Data** | **Additive into the existing `metadata_json` column** (`history.py:79`). **No migration. No new column.** |
| **API** | None (metadata is not in the frozen response shape). |
| **Boundary** | Makes an authenticated action **provable after the fact**. |
| **Tests** | metadata present when authenticated, absent when not; **no raw token anywhere in `job_events`** — assert it explicitly; existing events still readable. |
| **Depends on** | 10.3. |
| **Deferred** | Scope in metadata (needs 10.5); `schema_version` bump decision. |

*Why before enforcement:* you must be able to **prove** an authenticated action before you start
**denying** on the basis of one. Enforcement without an honest trail is the worse ordering. This
matches the brief's own 10.5 but moves it ahead of scope authorization.

### 10.5 — Railway scope and the first enforcing policy

| | |
|---|---|
| **Purpose** | Add the resource/context parameter; ship a policy with `enforcing = True`. |
| **Files** | `identity/authorization.py` (protocol + new policy), all 19 `authorize()` call sites (`service.py`, `optimization.py:413`), scope resolution against `SectionRegistry`. |
| **Data** | Scope on directory entries (from 10.2). |
| **API** | New `403` codes. Additive to the error envelope. |
| **Boundary** | **First real authorization in the system's history.** Highest blast radius. |
| **Tests** | authority denied outside their section; worker denied outside scope; scope respected per action; `UnenforcedPolicy` still passes its existing tests; the denying test policy in `test_job_lifecycle_accountability.py` still works. |
| **Depends on** | 10.2 (scope data), 10.3 (trustworthy identity), 10.4 (provable trail). |
| **Deferred** | SoD (needs event-history access — §12.5); cross-section rule (OQ-2). |

*Why last among the domestic slices:* it is the only one that can **break working behavior**, and it
is the only one that must not ship before scope data exists (§9.4).

### 10.6 — NovaForge integration

| | |
|---|---|
| **Purpose** | Real end-to-end flow: front-channel hop ② + service credential + live exchange. |
| **Files** | HTTP client for `/internal/identity-assertions`; JWKS fetch; introspection for high-consequence actions; **plus NovaForge-side changes for ②**. |
| **Data** | Service credential in environment config (§14.4). |
| **API** | None beyond 10.3. |
| **Boundary** | Crosses the trust domain for real. |
| **Tests** | Integration against a running NovaForge; failure modes FM-1/2/3, FM-9/10/11. |
| **Depends on** | **OQ-1 being answered, and 10.1–10.3 being complete.** |
| **BLOCKED** | **This is the AMBER item. It cannot start until hop ② is decided and approved, because it modifies NovaForge.** |

### 20.1 Ordering summary and the key insight

```
10.1 ──▶ 10.2 ──▶ 10.3 ──▶ 10.4 ──▶ 10.5
 │                  │
 │                  └──▶ 10.6  (BLOCKED on OQ-1 — NovaForge change)
 └─ startable today, no blockers
```

**10.1 through 10.5 can be built and fully tested without NovaForge running**, using locally
generated ES256 test vectors — exactly as NovaForge's own verifier tests do
(`p0-6-identity-trust.test.ts:255-366`). Only 10.6 needs the unblocked integration.

**This means the AMBER blocker does not stall the work.** It blocks one sub-slice, and that sub-slice
is last.

---

## 21. Explicitly deferred items

| # | Item | Why deferred |
|---|---|---|
| D-1 | Front-channel hop ② | **OQ-1.** Requires NovaForge changes and explicit approval. |
| D-2 | Directory management API/UI | Fixture-seeded entries suffice for demo; a management surface is its own slice with its own authorization problem. |
| D-3 | Segregation of duties | Needs event-history access inside `authorize()` (§12.5). **Not blocked by the demo** — the demo specifies no actors today (§12.2), so **OQ-7** is a scripting decision, not a conflict. |
| D-4 | Individual notification recipients | Needs a NovaForge user directory. The no-fabrication red line holds (`notifications.py:55,325`). |
| D-5 | Service identity for out-of-process components | No such process exists (§14.3). |
| D-6 | Division concept | Corridor + section set may suffice (§9.3). |
| D-7 | `jti` replay cache | 300 s TTL + audience binding may make it unnecessary. **OQ-5.** |
| D-8 | Step-up / re-authentication using `trust_level` / `auth_time` | Claims are available; the policy is undecided. **OQ-6.** |
| D-9 | Separating `COMMIT_BLOCK` into approve vs. commit | Reuse is adequate for Slice 10 (`authorization.py:45-50`). |
| D-10 | `schema_version` bump for event metadata | Implementation decision inside 10.4. |
| D-11 | Frontend integration | Explicitly out of scope until the backend auth contract is stable (red line R-11). |
| D-12 | Linking the two audit trails operationally | Both exist; correlation via `sub`/`sid` is sufficient for now. |
| D-13 | Cross-section authorization rule | **OQ-2.** Needs domain-expert ruling. |

---

## 22. Security red lines

Carried forward from the brief, each with its enforcement point in this architecture.

| # | Red line | Where it is held |
|---|---|---|
| R-1 | No duplicate user database in Pashupatastra | §6 rule 4 — the directory stores `sub` + railway role + scope. No credentials, emails or sessions. It is a **domain assignment table**. |
| R-2 | Never trust `X-Actor-Id` as authentication | §4.4, §18.3 — enforced at the single entry point `deps.py:33`, rejected with `400` in production mode. |
| R-3 | Never trust client-supplied role as authorization | §8.3 — every railway role is a directory assignment. None from client, none from NovaForge. |
| R-4 | NovaForge generic `ADMIN` ≠ railway `ADMIN` | §8.2 — answered explicitly no; disjoint enums are the proof. |
| R-5 | No raw tokens in audit history | §13.3, §13.4 — record `sub`/`iss`/`sid` only. 10.4 carries an explicit test. |
| R-6 | Never downgrade failed auth to `DECLARED_UNVERIFIED` | §16 + §16.1 — every failure mode is `401`/`403`/`503`. |
| R-7 | Auth service makes no railway scheduling decisions | §19 — all 19 `authorize()` call sites stay in Pashupatastra; NovaForge's verifier defers explicitly (`identityAssertionVerifier.ts:19`). |
| R-8 | Pashupatastra business rules never move into NovaForge | §12.4 — SoD stays domain-side because only Pashupatastra holds `job_events`. |
| R-9 | Notification recipient identity never fabricated | §19 — current refusing behavior preserved (`notifications.py:325`). |
| R-10 | No authentication implementation before the trust model is agreed | **This document. Verdict AMBER — 10.6 is blocked.** |
| R-11 | No frontend integration before the backend auth contract is stable | D-11. |

---

## 23. Open architectural questions

Ordered by blocking severity.

**OQ-1 — How does a credential cross from the browser to Pashupatastra's backend?** *(BLOCKING —
drives the AMBER verdict.)*
No supported path exists (§1.2). Option C (one-time front-channel code) is the smallest additive
change; Option B (full OIDC) is the correct long-term answer. **Requires a NovaForge change and
explicit approval. Auditor's recommendation: C, with `state`, single-use, ≤60 s TTL, audience-bound
— but this is a decision for the project owner.**

**OQ-2 — On a cross-section asset association, which section governs authorization?**
`job_section_id`, `asset_section_id`, or both (§11.3). The codebase already records all three and
makes the mismatch visible (`asset_association.py:42-46`). **This is a railway safety question
requiring a domain ruling. Auditor's inclination: require both (fail-closed).**

**OQ-3 — Is "crew" a first-class concept, or is worker scope assigned directly to sections?**
The brief's own example routes worker scope through a crew (§9.3). Direct assignment is simpler and
demo-sufficient.

**OQ-4 — How is the active role chosen when a user holds several?**
Derived from the target resource's section, or declared-then-verified (§8.4). Affects 10.2's shape.

**OQ-5 — Is a `jti` replay cache required?**
300 s TTL + audience binding + per-request re-verification may make it unnecessary (D-7).

**OQ-6 — Should `trust_level` or `auth_time` gate high-consequence actions?**
The claims are already issued (`identityAssertion.ts:39-40`) and trust decay is already tested
(`identity-assertion-trust.test.ts:91-117`). Requiring, say, `trust_level ≥ 50` for a possession
approval is available essentially free — but it is a policy decision (D-8).

**OQ-7 — Does the demo require a single person to drive the full lifecycle?**
Checked against the demo documentation: **it specifies no actors at all** — zero matches for any
actor identifier or header across all three demo documents (§12.2). So no conflict exists *yet*.
**Recommendation: script the demo with distinct actor ids per phase, which closes this question at
zero cost.** Only if a single operator id is required does SoD become mode-scoped. Must be settled
before 10.5.

**OQ-8 — Who is the Pashupatastra actor-directory administrator, and how is that bootstrapped?**
Creating railway role assignments is itself a high-privilege operation. Deferred (D-2), but the
bootstrap problem should be named now.

---

## 24. Acceptance criteria

For this **gate** (achieved):

- [x] Pashupatastra identity surface fully inventoried against actual code, not intent (§2)
- [x] NovaForge authentication proven from source and tests, not assumed (§3)
- [x] Every authentication mechanism documented with credentials-in, proof, signer, validator, expiry, revocation, audience, replay, transport (§3.1)
- [x] Trust boundary diagrams for current and target states (§4)
- [x] All seven mechanism options A–G compared against actual NovaForge capability (§5)
- [x] Identity / role / responsibility / permission kept distinct (§7.2)
- [x] NovaForge `ADMIN` → Pashupatastra `ADMIN` answered explicitly: **no** (§8.2)
- [x] Multi-role question answered with a recommended representation (§8.4)
- [x] Missing railway-scope concepts identified without inventing a data model (§9.3)
- [x] Authorization model level chosen with reasoning: RBAC + scope + state + SoD (§10.2)
- [x] SoD analyzed, **not** auto-enforced (§12)
- [x] Audit identity upgrade specified; raw tokens excluded (§13)
- [x] Failure-mode matrix with fail-open/closed and justification for all 24 (§16)
- [x] Demo-mode boundary defined without removing demo support (§17)
- [x] API contract impact assessed; v2 shown unnecessary (§18)
- [x] Ownership matrix with evidence in every cell (§19)
- [x] Implementation split into sub-slices with evidence-derived ordering (§20)
- [x] Deferred items and red lines explicit (§21, §22)
- [x] No production code, tests, schemas, OpenAPI or `jobs.db` modified

For **10.1–10.5** (future):

- [ ] Verification rejects every case in NovaForge's forgery matrix
- [ ] No code path produces `AUTHENTICATED` without a validated assertion
- [ ] No code path converts a verification failure into any accepted actor
- [ ] Unmapped `sub` is denied, with no default role
- [ ] NovaForge `role` never determines an `ActorRole`
- [ ] No raw token in `job_events` — asserted by test
- [ ] `X-Actor-*` rejected in production mode, accepted in demo mode
- [ ] Existing 1482 backend tests still pass, save the **two** superseded enum-membership tests —
      `test_actor_identity.py:86` and `test_slice7_contract_freeze.py:745` (§20, 10.3)
- [ ] `docs/openapi-v1.json` route inventory unchanged (`test_slice7_contract_freeze.py:164`), with
      only header *descriptions* updated (§18.2)

For **10.6**: blocked on OQ-1.

---

## 25. Architecture verdict

# AMBER

**Integration is possible, and the technical foundation is unusually strong — but two decisions
require explicit approval before implementation of the integration sub-slice.**

### Why not GREEN

The brief warns against GREEN-because-technically-possible, and a ready-made, well-tested assertion
API is precisely the thing that tempts a premature GREEN. Two open items are genuinely
security-load-bearing:

1. **No front-channel exists** (§1.2). NovaForge's own source says *"not integrated yet"*
   (`internal.ts:15`). The cookie is host-only, `sameSite: "lax"`, CORS is single-origin, and there
   is no `/authorize`, no code, no PKCE, no state. Closing this **modifies NovaForge** — not the
   auditor's call (**OQ-1**).
2. **Railway scope does not exist** (§9.2). Enforcing role without scope would let any `AUTHORITY`
   approve possession anywhere on the network while *appearing* enforced — arguably worse than
   today's honest non-enforcement.

### Why not RED

No contradiction was found. The two systems' boundaries are **complementary and mutually
consistent**:

- NovaForge's verifier explicitly defers authorization to the relying service
  (`identityAssertionVerifier.ts:19`).
- NovaForge's role vocabulary is disjoint from Pashupatastra's, so there is no *ambiguity* to
  resolve — only a mapping to refuse (§8.2).
- Pashupatastra's identity seam was designed for exactly this replacement and says so
  (`actor.py:30-37`; `deps.py:55-57`; `authorization.py:20-22`).
- The audit schema accepts authenticated identity **additively**, with no migration (§13.3).
- The frozen v1 contract survives unchanged; **no v2 is needed** (§18.4).

The two codebases were, independently, built toward the same boundary. That is the strongest
finding in this audit.

### Recommended next step

**Decide OQ-1 (the front-channel), then begin sub-slice 10.1.**

These are independent and can proceed in parallel: **10.1 through 10.5 require no NovaForge change
and are startable immediately**, testable end to end with locally generated ES256 vectors, exactly as
NovaForge's own verifier tests do. Only 10.6 waits on OQ-1.

Secondary approvals needed before 10.5: **OQ-2** (cross-section rule) and **OQ-7** (demo vs. SoD).

---

## Appendix A — Gate closure record

```
SLICE 10 ARCHITECTURE GATE

Pashupatastra HEAD:      985ca045c2cc297f9f0dc0f33aaac2f20badb4c7
Pashupatastra origin/main: 985ca045c2cc297f9f0dc0f33aaac2f20badb4c7
                         (HEAD == origin/main, no force push)
NovaForge HEAD:          5f3a2a6cd999286c032dce44a921132fe6d15e9f

working trees:
  Pashupatastra  - no tracked file modified. Untracked: the 5 protected
                   files (unchanged) + this new report.
  NovaForge      - READ ONLY. Carries one PRE-EXISTING modification that
                   was present before this audit began and was NOT touched
                   by it:  M server/prisma/migrations/migration_lock.toml
                   No NovaForge file was read-locked, modified or staged.

jobs.db SHA-256:         245fd9ff9a9861af6ada9e6c53eb7ea2170493499b7b4386ef0634069e852386
                         (identical to the Slice 9 baseline; verified before
                         and after the audit. The API was never imported and
                         pytest was never run, so nothing could touch it.)

protected files:         UNTOUCHED (sizes/mtimes unchanged)
                           FINAL_BACKEND_SYSTEM_AUDIT.md
                           PASHUPATASTRA_FINAL_DEMO_PLAYBOOK.md
                           SIH2026-IDEA-Presentation-Format.pdf
                           SLICE7_ARCHITECTURE_GATE_AUDIT.md
                           docs/SLICE9_NOTIFICATIONS_SLA_ESCALATION_ARCHITECTURE.md

stash:                   UNTOUCHED - stash@{0} intact
                         ("pre-existing local changes predating Tyagi/Ayush
                           merges (obsolete P0 fix to old solver.py)")

production code changed: NONE
tests changed:           NONE
schema changes:          NONE
API contract changes:    NONE
migrations:              NONE
commits / pushes:        NONE
frontend work:           NONE

report created:          docs/SLICE10_IDENTITY_AUTHORIZATION_ARCHITECTURE.md

architecture verdict:    AMBER

recommended next step:   Decide OQ-1 (the browser -> Pashupatastra-backend
                         front-channel hop; recommended: one-time code +
                         state, single-use, <=60s TTL, audience-bound).
                         In parallel and unblocked, begin sub-slice 10.1
                         (assertion verification seam) - it needs no
                         NovaForge change and is testable today with
                         locally generated ES256 vectors.
```

---

*End of Slice 10 architecture gate report. No implementation performed.*
