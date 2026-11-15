# SLICE 7 ARCHITECTURE GATE — READ-ONLY SYSTEM AUDIT

**Project:** Pashupatastra — SIH26027
**Audit date:** 2026-09-20
**Baseline:** `bf90a9eea3f49dee4c188c793a60fa679799dd75` (`feat(backend): add authority release for committed blocks`)
**Mode:** READ-ONLY. No production code, tests, database, schema, protected file, commit or push was modified.
**Artifact added:** this file only (untracked).

---

## 0. BASELINE VERIFICATION

Every claimed baseline fact was independently re-verified before the audit, not inherited from the prompt.

| Claim | Verification | Result |
|---|---|---|
| 1071 tests / 52 subtests / 1 warning | `python -m pytest backend -q` | **1071 passed, 52 subtests passed, 1 warning, 89.83s, exit 0** ✅ |
| Only warning is the Starlette/httpx deprecation | test output | `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated` — pre-existing, third-party ✅ |
| `jobs.db` SHA-256 `245fd9ff…52386` | `sha256sum jobs.db`, before **and** after the test run | identical both times ✅ |
| `jobs.db` untouched by tests | `backend/tests/conftest.py` sets `PASHUPAT_JOBS_DB` to a per-session tempdir **before any test module imports** | confirmed ✅ |
| Protected files untracked | `git status --short` | exactly 3 `??` entries, unchanged ✅ |
| Frontend / solver / schema unmodified | working tree clean apart from those 3 | confirmed ✅ |

The test suite is safe to run against this repository. It does not touch the operational database.

---

## 1. EXECUTIVE VERDICT

**The domain core of Pashupatastra is finished. The infrastructure around it has not been started. These are not the same problem and should not be solved in the same phase.**

Six slices have produced something genuinely unusual for a hackathon project: a complete, accountable, fail-closed maintenance lifecycle in which **every state change is token-gated, atomically paired with its audit event, and attributable to an actor whose identity assurance is honestly labelled.** Committed work cannot be silently weakened. Terminal state cannot be re-entered. Completion cannot be claimed without before- and after-evidence whose timestamps are checked against the execution window. Missing timetable data fails closed rather than being read as free track. Provenance is tracked on four independent axes with a weakest-link rule, and there is deliberately no `LIVE` value anywhere in the vocabulary so static data cannot be mislabelled even by mistake.

That is deployable-grade *domain architecture*. It is genuinely ahead of where most of this problem statement's entrants will be, and the discipline is visible in the code rather than only in the documents.

What does **not** exist is the layer beneath it: there is **no authentication, no enforced authorization, no logging statement anywhere in `backend/app`, no container, no CI, no migration framework, no readiness probe, and no storage for the evidence files the lifecycle is built around.** Evidence today is an unresolved pointer string — the system gates on evidence it cannot itself hold or verify.

**The central finding of this audit:** the backend is not short of *domain* work. It is short of *contract* work — six small, mechanical items that are cheap now and breaking changes once a frontend ships. Those six items are Slice 7, and then backend feature development should stop.

**Verdict on the frontend gate: YES, with a six-item contract freeze first.**

**Verdict on the remaining P2s:** one belongs in Slice 7 (`_no_proposal_reason`), one should be left exactly as it is (earliest-start conservatism), one is not a defect at all and should be closed (stale-release reachability).

---

## 2. WHAT IS GENUINELY COMPLETE

Verified by reading the implementation, not the docstrings. Each of these is classification **E — ALREADY COMPLETE**.

### 2.1 Job lifecycle / state machine — COMPLETE
`ALLOWED_TRANSITIONS` (`lifecycle.py:149`) is an explicit closed table. `COMPLETED` maps to `frozenset()` — terminal is structurally terminal, not merely conventionally so. There is deliberately **no** `notified → completed` edge: work can only complete from `in_progress`, and only with a `COMPLETE` execution token. `COMMITTED_STATUSES = (NOTIFIED, IN_PROGRESS)` correctly treats in-progress work as pinned, because it is physically on the track.

### 2.2 Committed-block / terminal-state protection — COMPLETE
`protect_committed_and_terminal_state` (`lifecycle.py:469`) runs on **every** write path, including the legacy history-less repository primitives. Its check ordering is load-bearing and documented as such: terminal is refused *before* any token is examined, so a token can release a commitment but can never reopen terminal state. `committed_state_problems` detects an already-inconsistent stored job and raises `CommittedStateIntegrityError` — it **detects and refuses, never repairs**. A 409 with the violation named, never a 500 and never a silent fix.

### 2.3 Authority workflow — COMPLETE
Approve / reject / postpone / release all require `expected_proposal_run_id`, so an authority decision can never silently act on a proposal the authority did not review. Reject, postpone and release additionally require a mandatory `reason` (1–2000 chars). `RELEASE_COMMITTED_BLOCK` is correctly its own `JobAction` rather than a reuse of `REJECT_PROPOSAL` — withdrawing a commitment already made is a strictly larger decision than refusing an uncommitted proposal, and a future policy must be able to grant them separately. That distinction is right.

### 2.4 Execution / evidence lifecycle — COMPLETE **as a lifecycle**
`EvidenceItem` enforces explicit UTC offset, a 5-minute maximum future skew, coordinate pairing, and bounded reference/note lengths. `validate_evidence_set` enforces minimum counts, uniqueness within a set, ordering against `actual_start_at`/`actual_end_at`, and — importantly — **forbids reusing a before-work reference as after-work evidence**, which is exactly the shortcut a crew under time pressure would otherwise take. `build_execution_records` refuses incoherent history (an outcome without its start, two outcomes for one execution, a start while another is open) rather than guessing what it meant.

*(The storage half of evidence is a real gap — §3.6.)*

### 2.5 Audit / event model — COMPLETE
Two append-only tables (`job_events`, `optimization_runs`), each protected **twice**: no update/delete method is exposed, *and* SQL triggers abort `UPDATE`/`DELETE` from any connection, including a direct `sqlite3` session or a future migration script. State and history commit or roll back **in the same transaction** (`mutate_jobs`), so history cannot claim a transition that did not commit or omit one that did. `JobEvent.canonical_bytes()` is a deterministic serialization ready for a future hash chain, and — correctly — **no hash column exists yet**, because a hash column with no writer would be dead schema.

### 2.6 Actor identity model — COMPLETE **as a model**
Three facts (`actor_id`, `role`, `assurance`) tracked independently and never inferred from one another; `kind` derived from `role` so a record cannot claim `role=SYSTEM, kind=HUMAN`. A human `actor_id` may not start with `SYSTEM`, and the HTTP layer returns 403 on a caller-declared `SYSTEM` role — an automated decision can never be attributed to a person, and vice versa. **`IdentityAssurance` has no `AUTHENTICATED` member**, because authentication does not exist and nothing is permitted to claim it. This is the same discipline as the absent `LIVE` provenance value, applied consistently.

### 2.7 CP-SAT safety / fail-closed — COMPLETE, no defect found
Possession control activates when the request declares any window. A block whose `(track_id, section_id)` identity no window covers is **refused individually** (`possession_uncovered`), not scheduled unprotected — and, per the Slice 4 fix, refusing it individually rather than collapsing the whole model to `INFEASIBLE` is what keeps one horizon-infeasible block from destroying an otherwise-feasible batch. Section matching is deliberately asymmetric: a block declaring a `section_id` requires an exact section match, so a possession for "UP-1 section A" cannot silently cover work on section B. `num_search_workers=1` with a fixed seed is a measured, documented reproducibility decision (28 distinct schedules across 30 identical solves under the parallel default; 1 under a single worker).

**I found no correctness or safety defect in the optimizer and recommend no change to it.**

### 2.8 Multi-day horizon & timetable coverage gate — COMPLETE
`horizon_minutes` is a single knob constrained to whole calendar days, so "postpone to date D" stays meaningful. The coverage gate refuses the **entire** attempt when the timetable does not describe every date the horizon spans — including days that *are* covered — because a partial derivation from a request that cannot be trusted as a whole is worse than a refusal. Missing timetable data is never read as absence of trains. This is the single best safety decision in the codebase.

### 2.9 Canonical train adapter & SectionRegistry — COMPLETE
The adapter removes three real bugs from the naive approach: trains that skip stops, positional zipping of station lists (which silently reverses for DOWN-direction trains), and times that wrap at midnight. `section_id` is direction-independent (`NDLS-NZM`); direction lives on `track_id`. Compound identifiers like `UP-1:7-32` are structurally prevented and pinned by contract tests.

### 2.10 Provenance model — COMPLETE and exemplary
Four independent axes; `effective` is a **computed property, never a stored field**, so an all-synthetic profile asserting `REAL_STATIC` cannot be constructed. `from_dict` ignores any stored `effective` key and recomputes. All four axes are required with no defaults — a caller that does not know an axis's provenance **cannot construct a profile at all**, and must fail closed at the boundary. `derived_possession_provenance` refuses to resolve the "is a window derived from a real timetable itself real?" question, raising rather than guessing. Leaving a genuinely open question open, in code, with a fail-closed default, is the correct engineering call and should not be "fixed."

### 2.11 Stale-proposal & concurrency protection — COMPLETE
`BEGIN IMMEDIATE` takes SQLite's write lock before the first read, so read-check-write cannot interleave — including across processes, which the in-process `RLock` cannot see. `ConcurrentJobModificationError` fails closed on a snapshot mismatch.

### 2.12 Test architecture — COMPLETE and unusually strong
1071 tests against ~16.8k lines of application code (~27k lines of tests). Notably, tests pin *architectural rules*, not just behaviour — e.g. `test_production_code_never_calls_history_less_repository_writes` fails if any module under `backend/app` ever calls a write primitive that skips history, and `test_no_live_value_exists` fails if anyone adds a `LIVE` provenance member. These are guardrails against future drift, which is what an audit wants to see.

---

## 3. REMAINING BLOCKERS

### 3.1 No API versioning — **A** / architecture
Routes are `/jobs`, `/corridors/{id}/optimize-jobs`. No prefix anywhere in `backend/app`. Free to add today; a breaking change for every frontend call site once a UI ships.

### 3.2 Collapsed error envelope — **A** / architecture + UX — *highest-value item in this audit*
`router.py:88` maps **seven distinct domain exceptions** to a single 409 with `detail=str(exc)`:

`TerminalJobError`, `CommittedJobError`, `CommittedStateIntegrityError`, `StaleProposalError`, `StaleExecutionError`, `ExecutionIntegrityError`, `ConcurrentJobModificationError`

There are 46 `detail=str(exc)` sites in the router. A frontend **cannot programmatically distinguish** "the proposal you reviewed is stale — reload" (recoverable, retry) from "stored committed state is inconsistent — escalate to an engineer" (not recoverable, must not retry). Both arrive as 409 + prose. The same collapse hits 400: `EvidenceValidationError` is a `ValueError` subclass, so "photo timestamp is in the future" and "duplicate evidence reference" are indistinguishable to a field app that must tell the worker what to fix.

The backend already knows the precise reason. It is discarded at the HTTP boundary. Adding a stable `code` field is mechanical and additive — the existing `detail` prose stays untouched.

### 3.3 No pagination on `GET /jobs` — **A** / architecture
Returns an unbounded list. Harmless at demo scale; the *shape change* from `[...]` to `{items, next_cursor}` is breaking once a UI consumes it.

### 3.4 `_no_proposal_reason` misattributes authority decisions — **A** / domain correctness — *this is the user's P2 #2, and it is worse than described*
`service.py:1990`. A job in `reported` with a `last_refusal_reason` set reports:

> `"job was considered and left UNSCHEDULED: <reason>"`

**Verified mechanism.** `plan_release` routes through `_withdrawn` (`lifecycle.py:1282`), which sets `last_refusal_reason = reason` — *"reason becomes last_refusal_reason, exactly as for a rejection."* Reject, postpone and not-completed all share that same `_withdrawn` mutation. So the stale-value theory is **not** what happens, and the actual behaviour is sharper:

> The **authority's own mandatory reason** — "possession not granted by TI", "crew unavailable" — is retrieved correctly, then wrapped in a sentence asserting the *optimizer* left the job unscheduled.

The reason is right. The attribution frame is false, and it is false for **all four** withdrawal paths (release, reject, postpone, not-completed). This is not cosmetic: it is the string a frontend renders in the authority queue, and it credits a named human's decision to the solver — inverting the exact accountability property six slices were built to protect.

The fix is history-aware read semantics: consult the most recent lifecycle event and let `BLOCK_RELEASED` / `PROPOSAL_REJECTED` / `PROPOSAL_POSTPONED` / `EXECUTION_NOT_COMPLETED` each frame their own reason with their own actor role, falling back to `last_refusal_reason` only for a genuine `OPTIMIZATION_REFUSED`. The history is already there, already ordered, already indexed by `job_id`.

**Verdict: belongs in Slice 7.** Shipping a UI against a read API that misattributes an authority decision is cheap to prevent now and an accountability complaint later.

### 3.5 Actor header contract not frozen — **A** / security + architecture
`X-Actor-Id` / `X-Actor-Role` is the documented authentication seam and behaves correctly (both-or-neither, 403 on `SYSTEM`, `DECLARED_UNVERIFIED` assurance). It is not surfaced in the OpenAPI schema as the identity contract, and no test pins it as frozen. The frontend will hardcode these headers on every call; when authentication lands, exactly one place should change. Pin it now.

### 3.6 Evidence is gated but not held — **B** / architecture + domain — *largest single gap in the system*
`evidence_reference` is a 1–200 character **string**. There is no blob storage, no upload endpoint, no media handling, no integrity hash of the referenced artifact, and no check that the reference resolves to anything at all. The product vision states **MANDATORY BEFORE/AFTER EVIDENCE**; what exists is a mandatory *claim* that evidence exists elsewhere.

To be fair to the design: the validation around that pointer is genuinely strong (timestamps, ordering, uniqueness, no-reuse), and the module docstring is honest that it stores "only the reference." But a worker can complete a job by typing `PHOTO-1` and `PHOTO-2`. Geolocation is captured and range-checked (`-90..90`, `-180..180`) but **never validated against the job's own chainage** — there is no geofence, so coordinates in another state pass.

This is category **B**, not A: it does not block frontend work, because the wire contract for evidence (`evidence_reference` + `evidence_kind` + `captured_at` + coordinates) is already the right shape and an upload endpoint returning a reference slots in underneath it without changing it. But it must be closed before any pilot in which evidence carries accountability weight.

### 3.7 Zero logging — **B** / operational infrastructure
`grep -rn "import logging\|logger\." backend/app` returns **nothing**. Not one log statement in 16.8k lines. Audit events cover *domain* actions; they cover no request, no error, no solver timing, no startup. A refused optimization leaves no operational trace. Nothing here is diagnosable in a pilot.

### 3.8 No authentication — **B** / security
By design and honestly labelled. `IdentityAssurance` has no `AUTHENTICATED` member, the headers are documented as unverified, and history records `DECLARED_UNVERIFIED`. The seam is clean; the implementation is absent. Any caller can claim to be any authority.

### 3.9 RBAC seam exists, enforcement does not — **B** / security
`UnenforcedPolicy.enforcing = False` permits everything. Every action does pass through `authorize()` before any read or write, and a denying policy is proven to stop an action with no state change and no event. The seam is done; the policy table is not. The intended split (WORKER reports/executes, AUTHORITY approves/releases, SYSTEM scores/optimizes, ADMIN manages identity) is documented but deliberately not encoded.

### 3.10 Health endpoint is a literal — **B** / operational infrastructure
`GET /health` returns `{"status": "healthy"}` unconditionally. It does not check the database, the dataset, or timetable coverage. A deployment with an unreachable DB reports healthy. No separate readiness probe.

### 3.11 Production database readiness — **B** / operational infrastructure
SQLite; additive `ALTER TABLE` migrations only (`_ADDED_COLUMNS`), no Alembic, no down-migrations, no WAL mode configured, no documented backup or restore procedure.

**Deployment constraint worth stating explicitly:** `router.py:76` constructs `service = JobService()` at **module import**. Under multi-worker uvicorn each worker gets its own instance and its own `lifecycle_lock`, so cross-worker correctness rests **entirely** on `BEGIN IMMEDIATE` and the snapshot check. That is believed sufficient and is documented as such — but it is untested under concurrent multi-process load, and it is precisely the kind of thing that surfaces during a pilot rather than in a test suite.

### 3.12 No containerization, CI, or dependency pinning — **B** / operational infrastructure
No `Dockerfile`, no `docker-compose`, no `.github/`. `requirements.txt` is 7 lines, describes itself as "Milestone 1 dependencies only," carries no upper bounds, no lockfile, and no Python version pin. "Works on my machine" is currently the deployment story.

### 3.13 CORS `allow_origins=["*"]` — **B** / security
Combined with header-declared identity, any origin can drive the full lifecycle as any role.

### 3.14 Secrets handling — **B** / security
A live-looking third-party API key sits in `.env` in the working tree. It is correctly `.gitignore`d and **is absent from git history** (verified: `git log --all -- .env` is empty). There is no `.env.example`, no secret manager, and no documented rotation. Low severity today; must be settled before any environment with real credentials. *(Value deliberately not reproduced in this report.)*

### 3.15 No idempotency keys — **B** / architecture
No `POST` accepts an idempotency key. A timed-out `create_job` retry creates a duplicate job; the service source itself notes at `service.py:1360` that Slice 5 "has no idempotency key" to recover a lost `execution_id` (recoverable in practice via `GET /jobs/{id}/execution`, but by luck of read-API design rather than by intent).

### 3.16 No optimization-run read API — **B** / architecture
`optimization_runs` stores the full solver request, result and provenance snapshot, append-only. **No HTTP route exposes it.** Per-job history is readable; the run that produced a placement is not. A management/audit surface will need it.

### 3.17 Notification architecture does not exist — **B** / architecture
`NOTIFIED` is a status name, not a message. No delivery channel, no template, no outbox, no recipient model. Nothing tells a crew a block was approved — the demo narrative depends on an operator watching a screen.

### 3.18 No duplicate-job detection — **C** / domain
Two workers reporting the same defect on the same chainage create two independent jobs competing for possession.

### 3.19 Crew/resource capacity not constrained — **C** / domain
`workers_min` / `workers_max` are validated on intake, stored, and carried through — but `grep crew|workers` against `solver.py` returns **no constraint**. The optimizer can schedule concurrent blocks requiring more crew than exists. The solver has a mutual-exclusion-group mechanism that could express a crew pool; it is unused for this.

### 3.20 No SLA / escalation — **C** / domain
No deadline model, no overdue detection, no escalation path.

### 3.21 `/notify` can commit an unpinned proposal — **A** / domain correctness
`CommitBlockRequest.expected_proposal_run_id` is **Optional**; omitting the body "keeps the pre-Slice-1 behaviour (commit the current proposal)." `ApproveProposalRequest` makes the same field **required** precisely so "an approval can never silently commit whatever happens to be current."

Both routes commit. One is pinned, one is not. Freezing both into a v1 contract would hand the frontend a route that commits whatever proposal happens to be current at that instant — the exact race the pin exists to prevent. This is a contract-shape decision, so it belongs in Slice 7 (see 7.6), not a later slice.

### 3.22 NovaForge — **not present in this repository**
The baseline states "NovaForge has not been modified." A case-insensitive search for `Nova` across the entire repository (excluding `node_modules` and `.git`) returns **zero matches** — no module, no import, no configuration, no documentation reference. If NovaForge is an external component, **there is no integration seam to it in this codebase**, and "unmodified" is trivially true because it is absent. Audit item 26 (NovaForge integration readiness) therefore cannot be assessed from this repository and should be treated as **not started**, not as complete. If an integration is intended, its seam is unbuilt — class C, pending a stated requirement.

---

## 4. G0–G24 GAP RECONCILIATION

Against the original `STEP16_BACKEND_PRODUCT_GAP_AUDIT.md` gap list. **Do not assume the old list is still accurate — it is not. Most of it is closed.**

| # | Original gap | Status now | Class | Type |
|---|---|---|---|---|
| G0 | Job lifecycle / state machine | **COMPLETE** | E | domain |
| G1 | Authority approve / postpone / reject | **COMPLETE** | E | domain |
| G2 | Authority release of committed block | **COMPLETE** (Slice 6) | E | domain |
| G3 | Execution lifecycle | **COMPLETE** | E | domain |
| G4 | Completion evidence gating | **COMPLETE** | E | safety |
| G5 | Not-completed / re-report | **COMPLETE** | E | domain |
| G6 | Per-job history | **COMPLETE** | E | architecture |
| G7 | Audit / event model | **COMPLETE** | E | architecture |
| G8 | Run linkage | **COMPLETE** | E | architecture |
| G9 | Actor identity model | **COMPLETE** | E | security |
| G10 | Multi-day calendar / horizon | **COMPLETE** | E | domain |
| G11 | Severity on intake | **COMPLETE** | E | domain |
| G12 | Data provenance model | **COMPLETE** | E | data integration |
| G13 | Synthetic/real boundary | **COMPLETE** (honest) | E | data integration |
| G14 | Real-data provider seams | **COMPLETE** (seam only) | E / D | data integration |
| G15 | Stale-proposal protection | **COMPLETE** | E | safety |
| G16 | Committed/terminal protection | **COMPLETE** | E | safety |
| G17 | Geolocation | **PARTIAL** — captured & range-checked, never geofenced | C | domain |
| G18 | Evidence / photos | **PARTIAL** — gated, not stored | **B** | architecture |
| G19 | Audit read API | **PARTIAL** — per-job yes, per-run no | B | architecture |
| G20 | Authorization / RBAC | **SEAM ONLY** — nothing enforced | B | security |
| G21 | Authentication | **MISSING** (honestly labelled) | B | security |
| G22 | Notifications | **MISSING** | B | architecture |
| G23 | SLA / escalation | **MISSING** | C | domain |
| G24 | Crew / resource constraints | **MISSING** from solver | C | domain |
| G25 | Duplicate detection | **MISSING** | C | domain |
| G26 | Production deployment | **MISSING** entirely | B | operational infra |
| G27 | Observability / logging | **MISSING** entirely | B | operational infra |
| **G28** | **API versioning** *(new)* | **MISSING** | **A** | architecture |
| **G29** | **Structured error codes** *(new)* | **MISSING** | **A** | architecture |
| **G30** | **List pagination** *(new)* | **MISSING** | **A** | architecture |
| **G31** | **Read-semantics attribution** *(new, = P2 #2)* | **DEFECT** | **A** | domain correctness |
| **G32** | **Actor header contract frozen** *(new)* | **UNPINNED** | **A** | security |
| **G33** | **`/notify` commits an unpinned proposal** *(new)* | **DEFECT for v1** | **A** | domain correctness |
| **G34** | **NovaForge integration** | **ABSENT from repository** — no seam exists | C | architecture |

**Score: 17 of 27 original gaps fully closed. 4 partial. 6 missing — all infrastructure or future-enhancement, none domain-critical.**

**Audit item 26 (NovaForge integration readiness) is unassessable from this repository** — see §3.22. It is neither complete nor regressed; there is simply nothing here.

### The three known P2s — disposition

| P2 | Finding | Recommendation |
|---|---|---|
| **#1 Earliest-start conservatism** — `_raise_not_before(released_end, never_lower=True)` makes a 22:00–23:00 block released at 09:00 yield `earliest_start = 23:00` | Conservative, consistent with `NOT_COMPLETED`, and **safe in the right direction**: it delays work, never advances it into an unprotected slot. Changing it means introducing a second, different notion of "when may released work be reconsidered," for a benefit measured in hours on a synthetic dataset. | **LEAVE AS IS. Class C. Document the behaviour; do not fix it in Slice 7.** |
| **#2 `_no_proposal_reason`** misattributes authority decisions to the optimizer | Real read-API defect (§3.4). Renders in the frontend. Inverts accountability. | **FIX IN SLICE 7. Class A.** |
| **#3 Literal stale-release sequence unreachable** | Re-optimization preserves a notified job's committed block/run, so the design's literal sequence cannot occur. A **stronger reachable** stale-generation replay case is already tested. An unreachable path is evidence the invariant holds upstream. | **NOT A DEFECT. Close it. Record the reachability argument in the Slice 6 design doc's test-coverage note and move on.** |

---

## 5. DEPLOYMENT-READINESS ASSESSMENT

### LEVEL 1 — HACKATHON DEMO READY

> *Can we demonstrate the complete maintenance lifecycle without manually editing the DB?*

**YES — verified. Every transition has an HTTP route. No DB editing is required at any point.**

```
POST /jobs                                   → report (scored, JOB_CREATED + JOB_SCORED)
POST /corridors/{id}/optimize-jobs           → optimize (BLOCK_PROPOSED, run recorded)
GET  /jobs/{id}/proposal                     → review (explanation + 4-axis provenance)
POST /jobs/{id}/proposal/approve             → commit (BLOCK_COMMITTED)
POST /jobs/{id}/execution/start              → start + before-evidence (IN_PROGRESS)
POST /jobs/{id}/execution/complete           → complete + after-evidence (COMPLETED)
GET  /jobs/{id}/history                      → full accountable audit trail

Alternate paths, all live:
POST /jobs/{id}/proposal/reject | /postpone | /release
POST /jobs/{id}/execution/not-completed
```

**Blockers: none functional.** Two presentation caveats:

1. **There is no UI for any of this.** The existing Next.js dashboard consumes only the legacy `/optimize` and `/recover` endpoints (Milestone 1 solver demo) and reads static JSON — it has **zero** connection to the jobs lifecycle. Today the lifecycle demos as curl or Swagger. That is the single largest gap between what is built and what a judging panel will perceive.
2. Evidence is typed strings, not photographs. Say so plainly in the demo rather than letting a judge assume otherwise — the project's honesty discipline is itself a differentiator, and claiming photo capture would waste it.

### LEVEL 2 — PILOT READY

> *If Railway gave us authorized data access, what would still need implementation?*

Concrete blockers, in dependency order:

1. **Authentication** — an authority approval must be attributable to a verified person. Add `AUTHENTICATED` to `IdentityAssurance` **together with** the code that justifies it.
2. **RBAC enforcement** — replace `UnenforcedPolicy` with a real table. Depends on (1).
3. **Evidence storage** (§3.6) — object storage, upload endpoint returning a reference, integrity hash, geofence against job chainage.
4. **Logging + observability** (§3.7) — structured request/error/solver logging. Non-negotiable for diagnosing a pilot.
5. **PostgreSQL + Alembic** — SQLite is not a multi-writer pilot database; the module-level singleton (§3.11) needs proving under real concurrency.
6. **Notification delivery** (§3.17) — a crew must be told a block was approved.
7. **Readiness probe** (§3.10) — DB reachable, dataset loaded, coverage satisfied.
8. **Containerization + pinned dependencies** (§3.12).
9. **Real data ingestion** — see §6. Category **D**, blocked on authorization, not on engineering.

### LEVEL 3 — PRODUCTION READY

10. Hash-chain the event log (`canonical_bytes()` is already designed for it; the chain is one column and one writer away).
11. Backup, restore and disaster-recovery procedure, tested.
12. CI/CD with the full suite gating every merge.
13. Secret manager; rotation policy; remove `.env` from the workflow entirely.
14. CORS locked to known origins; rate limiting; security review / pen test.
15. Crew and resource capacity as real solver constraints (§3.19).
16. SLA and escalation (§3.20).
17. Multi-corridor deployment — the singleton `JobService` and single-corridor assumption both need revisiting.
18. Trained model to replace `BASELINE_DETERMINISTIC`, with its own `MODEL_TYPE` and version, plus a validation methodology.

---

## 6. REAL DATA — STRICT CLASSIFICATION

**Every production input to the optimizer today is SYNTHETIC. There is no real data in the running pipeline. There is no live feed of any kind.**

| Input | Actual classification | Evidence |
|---|---|---|
| Corridor topology (NDLS–AGC) | **SYNTHETIC** | `optimization.py:175` hardcodes `topology=SYNTHETIC`. Station codes (NDLS, NZM, AGC) are real IR codes and chainages are plausible, but nothing was transcribed from an official topology source. The code comments call this out explicitly: *"realistic-looking identifiers are exactly what the weakest-link rule exists to stop reading as REAL_*."* |
| Timetable (48 train records) | **SYNTHETIC** | `pashupatastra_realistic_dataset.json` declares `"synthetic": true` and states *"station/trains are illustrative, not an official timetable."* Day 2 is a deterministic repeat of day 1 with only `service_date` changed. |
| Asset condition / defects | **SYNTHETIC** | `optimization.py:177`. Generated by `CorridorDataGenerator`. |
| Possession windows | **SYNTHETIC** | Derived from the synthetic timetable, or from `generate_possession_windows(seed=42)`. `POSSESSION_SOURCE_GENERATED_STATIC` is a *derivation* label, not a provenance value. |
| Priority / risk scores | **SYNTHETIC inputs, deterministic formula** | `MODEL_TYPE = "BASELINE_DETERMINISTIC"`, hand-authored weights, **not trained on Indian Railways data** — and the module says so. |
| Jaipur–Ajmer topology research | **REAL_STATIC evidence, deliberately NOT ingested** | `STEP14_AUTHORITATIVE_SOURCE_AUDIT.md`: NWR WTT 2025 is a genuine authoritative source; gate verdict **AMBER**; 8 of 9 JP–FL block sections remain UNKNOWN; explicitly *"must still NOT advance to a full REAL_STATIC topology ingestion."* |

**`REAL_STATIC`: none in the pipeline. `REAL_SCHEDULED`: none. `ASSUMED`: none — and that absence is a design achievement, not an omission.** The architecture makes "assumed" unconstructible: all four `ProvenanceProfile` axes are required with no defaults, so a caller that does not know an axis cannot build a profile at all. There is no `UNKNOWN` and no `LIVE` member to hide behind.

### Can providers be swapped without changing the optimizer or domain logic?

**Yes for the timetable axis. Partially for possession. Not yet for topology or asset condition.**

- **Timetable — YES.** `TrainDataProvider` is a one-method Protocol returning a `TrainDataSnapshot`. A live provider implements `fetch()` and nothing downstream changes: the solver and contracts know only `contracts.PossessionWindow`, and no provider name appears in any downstream layer. This seam is genuinely done.
- **Possession — PARTIALLY.** Windows derive correctly from whichever provider ran, but `derived_possession_provenance` **raises** for any non-synthetic timetable. That is intentional fail-closed behaviour on a genuinely open question ("is a window derived from a real timetable itself real?"). It must be answered — *as a policy decision, not a code change* — before a real timetable connects. Correct call; flagging it as the one deliberate blocker on this path.
- **Topology — NO.** `topology=ProvenanceLevel.SYNTHETIC` is hardcoded at `optimization.py:175`. A real topology source has no seam to enter through. This is small (one constructor argument threaded through) but it is not currently swappable.
- **Asset condition — NO.** Same hardcode at `optimization.py:177`; no provider protocol exists for inspection data at all.

**Bottom line:** the seam the project most loudly claims (timetable) is real and works. Two axes still need the same treatment. That work is **category D-adjacent** — build the seam when there is a source to put behind it, not before, or you get an interface shaped by guesses.

---

## 7. FRONTEND READINESS GATE

### Is the backend mature enough to begin frontend implementation?

## **YES — after a six-item contract freeze.**

**Why YES.** The domain model, lifecycle, authority workflow, evidence gating, event history and provenance model are complete and internally coherent. Everything still missing splits cleanly into two buckets, and **neither changes the wire contract**:

- **Infrastructure** (auth, RBAC enforcement, logging, Docker, CI, Postgres, notifications) — changes deployment and behaviour, not request/response shapes. Authentication in particular slots into the *existing* actor seam: the frontend sends identity headers today and sends a bearer token later, and only `deps.request_actor` changes.
- **Real data** (category D) — changes provenance *values* the UI already renders as data, never their *shape*.

Continuing backend feature work before a frontend exists would be building against imagined requirements. The next genuine information about what this backend needs will come from building a screen against it — and this is exactly the "endless backend work that does not materially improve deployment readiness" the gate was called to prevent.

**Why the six items first.** Each is a contract-shape change: trivial today, a coordinated breaking migration once a UI consumes it. They are §3.1, §3.2, §3.3, §3.4, §3.5 and §3.21 — and together they are Slice 7.

### The exact contract to freeze

Once Slice 7 lands, **these are frozen** and any change requires an explicit version bump:

**Routes** (all under `/v1`):
```
POST   /v1/jobs
GET    /v1/jobs?status=&limit=&cursor=
GET    /v1/jobs/{job_id}
GET    /v1/jobs/{job_id}/history
GET    /v1/jobs/{job_id}/proposal
GET    /v1/jobs/{job_id}/execution
POST   /v1/corridors/{corridor_id}/optimize-jobs
POST   /v1/jobs/{job_id}/notify          (pin REQUIRED in v1 — see 7.6)
POST   /v1/jobs/{job_id}/proposal/approve | /reject | /postpone | /release
POST   /v1/jobs/{job_id}/execution/start | /complete | /not-completed
GET    /health                           (unversioned, by convention)

LEGACY — frozen, NOT part of the v1 product contract:
POST   /optimize      ← Milestone-1 solver demo; existing dashboard depends on it
POST   /recover       ← Milestone-1 recovery demo; existing dashboard depends on it
```

**The lifecycle frontend must not be built against `/optimize` or `/recover`.** They are kept unversioned solely so the existing Milestone-1 dashboard keeps working.

**Response models** — freeze as they stand, they are well designed:
`JobResponse`, `JobHistoryResponse` / `JobEventResponse`, `BlockProposalResponse` (with `ProposalExplanationItem` + `ProvenanceResponse`), `ExecutionResponse` / `ExecutionActionResponse`, `JobOptimizationResponse`, `ActorResponse`.

**Identity contract:** `X-Actor-Id` + `X-Actor-Role`, both-or-neither, `SYSTEM` refused with 403, assurance always `DECLARED_UNVERIFIED` until authentication exists. Documented in OpenAPI and pinned by test.

**Error envelope:** `{ "code": "<STABLE_ENUM>", "detail": "<prose>" }` on every 4xx. `code` is what the UI switches on; `detail` is what it shows.

**Enums the UI may switch on:** `JobStatus`, `JobType`, `JobEventType`, `ProvenanceLevel`, `EvidenceKind`, `ExecutionStatus`, `ActorRole`, `IdentityAssurance`.

**Explicitly NOT frozen** (frontend must not depend on these): the `block_candidate` free-form dict, event `metadata` dict internals, exact prose of any `detail` string, or `optimization_run_id` / `execution_id` formats beyond their documented patterns.

---

## 8. RECOMMENDED SLICE 7

### **Slice 7 — API Contract Freeze**

**Is it needed?** **Yes** — but as the *smallest possible* slice, and as the *last* backend feature slice before the frontend.

**Why it is necessary.** Four of its six items are breaking wire changes that are nearly free now and expensive after a UI ships. The other two are live accountability defects: `_no_proposal_reason` credits a named authority's decision to the solver, and `/notify` can commit a proposal nobody pinned. Both are the one thing this system exists to get right. Slice 7 is not new capability; it is making six slices of finished capability *safely consumable*.

### Exact scope — six items, nothing else

| # | Item | Detail |
|---|---|---|
| **7.1** | **URL versioning** | `/v1` prefix on the **jobs lifecycle** routes via `APIRouter(prefix=...)`. `/health` stays unversioned by convention. **`POST /optimize` and `POST /recover` stay unversioned and unmoved** — see the decision note below. |
| **7.6** | **Require the pin on `/notify`** | Make `CommitBlockRequest.expected_proposal_run_id` **required** in v1, matching `ApproveProposalRequest` (§3.21) — *or*, if any in-process caller depends on the optional form, leave `/notify` out of the v1 contract entirely and mark it legacy. Pick one; do not freeze an unpinned commit route. |
| **7.2** | **Structured error codes** | A `JobErrorCode` enum; every `HTTPException` carries `{code, detail}`. Map each of the seven `_CONFLICTS` classes to its **own** code; split `EvidenceValidationError` out of the generic `ValueError` → 400 path. `detail` prose unchanged. |
| **7.3** | **List pagination** | `GET /v1/jobs` → `{items: [...], next_cursor: str \| null}`, with `limit` (default 50, max 200) and `cursor`. Stable ordering by `created_at, job_id`. |
| **7.4** | **History-aware `_no_proposal_reason`** | Consult the latest lifecycle event before falling back to `last_refusal_reason`. `PROPOSAL_REJECTED` / `PROPOSAL_POSTPONED` / `BLOCK_RELEASED` / `EXECUTION_NOT_COMPLETED` must each report **their own** reason and actor role — never "the optimizer left it unscheduled." **Read semantics only: no stored column changes, no new event type, no lifecycle change.** |
| **7.5** | **Freeze the actor-header contract** | Document `X-Actor-Id` / `X-Actor-Role` in OpenAPI as the identity seam; add a test pinning both-or-neither, 403-on-SYSTEM, and `DECLARED_UNVERIFIED` assurance. |

> **Decision note on `/optimize` and `/recover` (do not leave this implicit).**
> `main.py` registers `POST /optimize` and `POST /recover`, and the existing Next.js dashboard calls both (`frontend/src/lib/data.ts` → `${API_BASE_URL}/optimize`, `/recover`). Moving them under `/v1` **breaks the working Milestone-1 demo mid-SIH-cycle**; leaving them silently unversioned makes the versioning story incoherent.
>
> **Recommendation: leave both unversioned and unmoved, and mark them LEGACY — frozen, and explicitly NOT part of the v1 product contract.** They serve the standalone solver/recovery demo, not the maintenance lifecycle. The v1 contract covers the jobs lifecycle only. State this in the frozen-contract section so the frontend team knows they are out of scope and must not build the lifecycle UI against them.

### What Slice 7 must NOT contain

**Hard exclusions — any of these means the slice has failed:**

- ❌ Authentication (its own slice, post-frontend)
- ❌ RBAC enforcement — `UnenforcedPolicy` stays
- ❌ Notification delivery, SLA, escalation
- ❌ Crew / resource constraints
- ❌ Evidence media storage or upload endpoints
- ❌ **Any new `JobStatus`** — the five statuses are correct and sufficient
- ❌ **Any schema change** — no new tables, no new columns, no migration
- ❌ **Any optimizer change** — no defect was found; do not touch CP-SAT
- ❌ **Any lifecycle/transition change** — `ALLOWED_TRANSITIONS` is frozen
- ❌ **P2 #1** — leave earliest-start conservatism exactly as it is
- ❌ Logging, Docker, CI, Postgres — all post-frontend
- ❌ Opportunistic refactoring of `lifecycle.py` or `service.py`

### Model recommendation: **Sonnet**

Every item is mechanically specified against a 1071-test suite that will catch regressions immediately. 7.1/7.3/7.5/7.6 are near-mechanical; 7.2 is a systematic 46-site mapping; 7.4 is the only one needing judgment, and its judgment is already made in this report (consult the latest lifecycle event; read-only; `last_refusal_reason` only for `OPTIMIZATION_REFUSED`). This is precision-execution work, not design work — Sonnet is the right call *because the specification is complete*, not because the slice is unimportant. **If 7.4 turns out to need more than 40 lines, stop and escalate to Opus rather than expanding scope.**

### Acceptance criteria

1. All 1071 existing tests pass (updated for `/v1` and the pagination envelope; **no test deleted, no assertion weakened**).
2. New tests: every `_CONFLICTS` class maps to a distinct `code`; `EvidenceValidationError` maps to its own `code`; pagination is stable across pages with no duplicates or omissions; the actor-header contract is pinned.
3. **`_no_proposal_reason` regression test:** a job released by an authority after a prior optimization refusal reports the **release** and its actor role — *not* `"job was considered and left UNSCHEDULED"`. Same for reject, postpone and not-completed.
4. `git diff --stat` touches **no** file under `backend/app/optimizer/`, `backend/app/data/`, or `contracts/`.
5. `ALLOWED_TRANSITIONS`, `JobStatus`, `JobEventType` and the DB schema are **byte-identical** to baseline.
6. `jobs.db` SHA-256 unchanged: `245fd9ff…52386`.
7. OpenAPI (`/openapi.json`) exported and committed as the frozen contract artifact the frontend builds against.
8. One commit: `feat(api): freeze versioned contract with structured errors`.

---

## 9. POSSIBLE SLICE 8+ (dependency order)

**Slice 7 → FRONTEND → then, and only then:**

| Slice | Content | Class | Depends on |
|---|---|---|---|
| **8** | **Authentication** — `AUTHENTICATED` assurance + the credential verification that justifies it; replace header trust in `deps.request_actor` only | B / security | Slice 7 |
| **9** | **RBAC enforcement** — the real policy table; WORKER/AUTHORITY/ENGINEER/ADMIN split | B / security | Slice 8 |
| **10** | **Observability** — structured logging, request IDs, solver timing, readiness probe | B / ops | Slice 7 |
| **11** | **Evidence storage** — object storage, upload endpoint, integrity hash, geofence vs. job chainage | B / architecture | Slice 8 |
| **12** | **Deployment** — Dockerfile, pinned deps, CI, PostgreSQL + Alembic, multi-worker validation | B / ops | Slices 8–10 |
| **13** | **Notifications** — delivery channel + outbox for the `NOTIFIED` transition | B / architecture | Slices 8, 12 |
| **14** | **Management/reporting** — optimization-run read API, corridor plan view, completion metrics | C / UX | Frontend |
| **15** | **Real data ingestion** — topology + asset-condition provider seams; settle the possession-provenance promotion rule | **D** | Authorized access |
| **16** | Crew constraints, duplicate detection, SLA/escalation, event hash chain | C / future | — |

---

## 10. WHAT SHOULD EXPLICITLY NOT BE BUILT NOW

Stated plainly, because the risk to this project is not missing capability — it is **continuing to build backend depth nobody can see.**

1. **Do not touch the CP-SAT optimizer.** No correctness or safety defect was found. The possession fail-closed logic, per-block refusal isolation and single-worker determinism are all correct and measured.
2. **Do not change the schema.** Every audit item is satisfiable without one.
3. **Do not add a new `JobStatus`.** Five statuses express the full lifecycle. `RELEASED` and `CANCELLED` are both correctly modelled as `reported` + a reason.
4. **Do not "fix" P2 #1.** Conservative earliest-start is safe in the right direction and consistent with `NOT_COMPLETED`.
5. **Do not build authentication before the frontend.** The seam is clean; building it now means guessing at session handling with no UI to constrain it.
6. **Do not build notifications, SLA, escalation, crew constraints or duplicate detection now.** None is demo-critical; each is much better specified once a UI exists.
7. **Do not build real-data ingestion seams for topology or asset condition.** Category D. An interface designed against an imagined source will be wrong. Step 14 already made this call for Jaipur–Ajmer; honour it.
8. **Do not refactor `lifecycle.py` or `service.py` for elegance.** They are large but coherent, densely documented, and covered by 1071 tests. Size is not a defect.
9. **Do not add a hash chain yet.** `canonical_bytes()` is ready; a chain with no threat model and no verifier is ceremony.
10. **Above all: do not start Slice 8 before the frontend exists.**

---

## 11. FINAL RECOMMENDED ROADMAP TO BACKEND FREEZE

```
  NOW ──► SLICE 7 (Contract Freeze, Sonnet, 6 items)
             │  versioning · error codes · pagination
             │  read-semantics fix · actor contract pinned
             │  /notify pin required
             ▼
          EXPORT openapi.json ──► the frozen contract artifact
             ▼
      ★ BACKEND FEATURE FREEZE ★
             ▼
          FRONTEND IMPLEMENTATION
             │  worker report → authority queue → proposal review
             │  → approve/reject/postpone/release → field execution
             │  → evidence capture → completion → audit trail
             ▼
          DEMO / SIH SUBMISSION
             ▼
          Slices 8-13 (auth, RBAC, observability, evidence
                        storage, deployment, notifications)
             ▼
          PILOT (if authorized data access is granted)
```

**The critical judgment in this audit:** the backend's next marginal hour is worth far more spent on a screen than on a seventh slice of domain depth. Six slices have produced a domain model that can carry a pilot. What cannot currently be *shown* is any of it.

---

# BACKEND FREEZE CRITERIA

**When every box below is ticked: STOP backend feature development and build the frontend.**

### Contract shape
- [ ] All **jobs lifecycle** routes served under `/v1`; `/health` unversioned
- [ ] `/optimize` and `/recover` left unmoved and documented as LEGACY, outside the v1 contract
- [ ] `/v1/jobs/{id}/notify` requires `expected_proposal_run_id` — **or** `/notify` is excluded from v1 as legacy
- [ ] Every 4xx returns `{code, detail}` with a stable enum `code`
- [ ] Each of the seven `_CONFLICTS` classes maps to its **own** distinct `code`
- [ ] `EvidenceValidationError` has its own `code`, separate from generic 400
- [ ] `GET /v1/jobs` returns `{items, next_cursor}` with `limit` + `cursor`
- [ ] `openapi.json` exported and committed as the frozen contract artifact

### Read correctness
- [ ] `_no_proposal_reason` reports the **actual** last lifecycle decision and its actor role
- [ ] A released / rejected / postponed / not-completed job **never** reports "considered and left UNSCHEDULED" — the authority's reason is never framed as an optimizer refusal
- [ ] `last_refusal_reason` is used as the displayed reason **only** for a genuine `OPTIMIZATION_REFUSED`
- [ ] Regression test exists for each of those four withdrawal paths

### Identity seam
- [ ] `X-Actor-Id` / `X-Actor-Role` documented in OpenAPI as the identity contract
- [ ] Both-or-neither, 403-on-SYSTEM, and `DECLARED_UNVERIFIED` assurance each pinned by test
- [ ] No `AUTHENTICATED` assurance value exists (authentication does not exist yet)

### Unchanged from baseline — verify by diff
- [ ] `ALLOWED_TRANSITIONS` byte-identical
- [ ] `JobStatus` — exactly five members, unchanged
- [ ] `JobEventType` — no new members
- [ ] Database schema — no new table, no new column
- [ ] `backend/app/optimizer/` — untouched
- [ ] `backend/app/data/` — untouched
- [ ] `contracts/` — untouched
- [ ] `jobs.db` SHA-256 = `245fd9ff9a9861af6ada9e6c53eb7ea2170493499b7b4386ef0634069e852386`

### Demonstrability
- [ ] Full lifecycle exercised end-to-end over HTTP with **zero** manual DB edits
- [ ] All lifecycle branches reachable via API: approve · reject · postpone · release · not-completed · complete
- [ ] `GET /v1/jobs/{id}/history` returns a complete, ordered, attributed audit trail
- [ ] Provenance reported on all four axes with `effective = SYNTHETIC`, and labelled as synthetic in the demo narrative

### Test integrity
- [ ] ≥ 1071 tests pass; **no test deleted; no assertion weakened**
- [ ] New tests cover every Slice 7 item
- [ ] Only the pre-existing Starlette/httpx deprecation warning remains

### Explicitly deferred — must be **absent** from Slice 7
- [ ] No authentication · no RBAC enforcement · no notifications · no SLA
- [ ] No crew constraints · no evidence storage · no duplicate detection
- [ ] No logging/Docker/CI/Postgres · no optimizer change · no schema change
- [ ] No new `JobStatus` · no change to earliest-start conservatism (P2 #1)

---

**Once this checklist is satisfied, the backend is frozen. Build the frontend.**
