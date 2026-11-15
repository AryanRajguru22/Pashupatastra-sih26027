# D-3 — Legacy `POST /optimize`: Authorization and Audit Integrity

**Architecture / security gate. READ-ONLY. Nothing is implemented here.**

| | |
|---|---|
| Baseline | `HEAD` = `origin/main` = `e60ffeb43e540fcda4ecda7bba112fbb040872f3` |
| `jobs.db` SHA-256 | `245fd9ff9a9861af6ada9e6c53eb7ea2170493499b7b4386ef0634069e852386` (unchanged by this gate; inspected only through a scratch copy) |
| Date | 2026-09-22 |
| Origin | D-3 in `docs/SLICE10_1D3_CORRIDOR_AUTHORIZATION_ARCHITECTURE.md` §26 |

Labels: **[OBS]** observed in the repository (file:line) · **[PROP]** proposed ·
**[OPEN]** needs an owner decision · **[DEFER]** explicitly out of scope for D-3.

---

## 1. Executive verdict — **AMBER**

**What the legacy route does not do.** It never reads or writes a maintenance job, a proposal,
a job event or any lifecycle state. Its module imports only FastAPI's `APIRouter`, `AuditService`, `solve` and the
contracts (`backend/app/api/routers/optimize.py:18-22`).

**What it does do.** It lets **any caller, with no identity at all, write a permanent row into the
same `optimization_runs` table the authorized v1 pipeline writes to**. `GET /v1/optimization-runs/{id}`
then serves that row through the same read path as a legitimate run.

**This is live, not theoretical.**
- The dashboard calls the route on every server-side page load (`frontend/src/app/page.tsx:7`) and on
  every re-optimize click (`frontend/src/components/DashboardClient.tsx:42`).
- The real `jobs.db` already contains exactly one such row (§22).
- The earlier 10.1D.3 report called this "low reachability". That was wrong, and this gate corrects it.

**The recommended fix is narrow and cheap** (§18): make legacy `/optimize` stateless, exactly like
`/recover` already is. After the change:
- unauthenticated compute is still possible, but an unauthenticated caller can no longer create
  any persistent record;
- no route, response, status code, OpenAPI entry or frontend file changes;
- no `backend.app.jobs` import is added.

**Why AMBER and not GREEN.**
- **The fix closes only one of the two problems.** It stops pollution of the audit trail. It does
  not give the route an identity. That only arrives with Slice 10.3 authentication, or when the route
  is retired (Option A) after the dashboard moves to v1.
- **Three decisions are open.** Whether demo solves should keep any audit trail at all (D3-1). What
  v1 should do with the one existing legacy row (D3-2). Whether this `jobs.db` is ever an
  operational database (D3-3, the tiebreak for D3-2).

**Deviation from the brief's §7, stated up front.** The route is retained without the
corridor-complete rule, because R-1 removes the railway decision that rule exists to guard. The full
reconciliation is in §18. The stricter reading of §7 leads to Option A, not Option B.

**Why not RED.** No unauthorized path can touch job, proposal, lifecycle or committed state. The
damage is confined to the integrity of the audit trail.

---

## 2. Exact legacy `/optimize` route trace [OBS]

```
HTTP POST /optimize (JSON body)
  │  CORS allow_origins=["*"], no auth middleware          api/main.py:38-43
  │  route registered unversioned                           api/main.py:46
  ▼
optimize(request: OptimizationRequest)                      api/routers/optimize.py:29-30
  │  no Depends(), no actor, no header, no seam call         api/routers/optimize.py:29-35
  │  body parsed by FastAPI → 422 on schema error            tests/test_slice7_contract_freeze.py:173-180
  ▼
_audit_service.record_run(request, solve, trigger="optimize")   optimize.py:31-35
  │  _audit_service = AuditService() built at IMPORT          optimize.py:26
  │     → AuditRepository() → DEFAULT_DB_PATH                  audit/service.py:79-80; audit/repository.py:39
  │     → CREATE TABLE IF NOT EXISTS + append-only triggers    audit/repository.py:41,48-80
  │  actor defaults to SYSTEM_ACTOR                           audit/service.py:88; audit/models.py:27
  │  run_id minted server-side (uuid4)                        audit/service.py:44-47,106
  │  request_json = serialize(caller request)                 audit/service.py:108
  │  corridor_id  = request.corridor_id  (caller-supplied)    audit/service.py:110
  ▼
solve(request)                                              optimizer/solver.py:163
  │  pure function of the caller's payload; reads no repository
  ├─ raises → INSERT ERROR row, re-raise                     audit/service.py:112-131
  └─ returns → INSERT row with result_json                   audit/service.py:133-148
  ▼
INSERT INTO optimization_runs (plain INSERT, own commit)     audit/repository.py:82-127
  ▼
return OptimizationResult (no run_id field)                 contracts/schemas.py:304-314
```

- **Never reached:** `backend.app.jobs.*`, `JobService`, any `JobAction`, `authorize`,
  `authorize_resource`, `require_resource_aware`, `maintenance_jobs`, `job_events`.
- **Evidence:** the module's imports (`api/routers/optimize.py:18-22`), plus an existing guard test
  that asserts the string `backend.app.jobs` never appears in this file
  (`backend/tests/test_job_lifecycle_accountability.py:1844-1846`).

---

## 3. All callers and invocation paths [OBS]

### 3.1 Callers of the HTTP route

| Caller | Evidence | Behavior on a non-2xx response |
|---|---|---|
| Dashboard SSR page load | `frontend/src/app/page.tsx:7` → `fetchOptimizationData()` (`frontend/src/lib/data.ts:70`) → `POST ${API_BASE_URL}/optimize` (`data.ts:78`) | falls back to a **fixture**, marked `DEGRADED`, with only a `console.warn` (`data.ts:107-123`) |
| Dashboard "Re-optimize" button | `frontend/src/components/DashboardClient.tsx:42`, `PlanView.tsx:586` | same fallback |
| `test_api.py` | `backend/tests/test_api.py:39,48,68,114,158` | — |
| `test_audit.py` (asserts that a row is written) | `backend/tests/test_audit.py:577-601` | — |
| `test_recover_api.py` | `backend/tests/test_recover_api.py:228-235` | — |
| Route inventory, error body, OpenAPI snapshot | `backend/tests/test_slice7_contract_freeze.py:157-181`, `:816-830`; `docs/openapi-v1.json:2330` | — |
| Mutating-route inventory and the no-jobs-import guard | `backend/tests/test_job_lifecycle_accountability.py:1808-1846` | — |
| Docs and demo material (prose only) | `README.md:45,72`, `docs/demo-runbook.md:68`, `docs/api-contract-v1.md:10`, `docs/integration.md:12,47` | — |

The dashboard posts its own fixture `frontend/src/data/corridor_a_blocks.json`, whose `corridor_id`
is `"CORRIDOR_A"` (line 2). That is **not** the deployment corridor, which is `CORR-NDLS-AGC` from
the default dataset.

### 3.2 Every path that invokes the same solver

| Path | Authorized? | Audited? | Evidence |
|---|---|---|---|
| Legacy `POST /optimize` | **no** | **yes**, in the shared DB | §2 |
| `POST /v1/corridors/{id}/optimize-jobs` | role + corridor-complete (10.1D.3) | yes: DB pinned to `JobService.repository.db_path`, `trigger="jobs_optimize"` | `jobs/optimization.py:401-403,413-437,558-572`; `jobs/lifecycle.py:1031` |
| Legacy `POST /recover` | no | **no**, stateless | `api/routers/recover.py:24-35`; `simulation/simulator.py:26-44` |
| `scripts/run_milestone1.py` | CLI, no HTTP | no | `scripts/run_milestone1.py:23,46` |

**[OBS] Where the defect sits.** The two paths resolve their audit database differently:
- the v1 pipeline **pins** its `AuditRepository` to its own service's file
  (`jobs/optimization.py:395-403`, comment included);
- the legacy route **resolves its own default independently** (`optimize.py:26`).

With default configuration both resolve to `DEFAULT_DB_PATH` (`jobs/repository.py:37-42`), so the
legacy route lands in the same file only by coincidence of defaults.

---

## 4. Current authorization behavior [OBS]

| Question | Answer |
|---|---|
| Actor dependency | none. The v1 routes use `Depends(request_actor)` (`api/deps.py:33-95`); `/optimize` does not |
| `JobAction` checked | none. `REQUEST_OPTIMIZATION` is checked only on the v1 path (`jobs/optimization.py:413-416`) |
| Corridor validated against the deployment | **no.** Any string is accepted; compare `jobs/optimization.py:418-424` on v1 |
| Corridor-complete scope (10.1D.3) | not applied |
| Policy consulted | none. `require_resource_aware` guards only `JobService` (`jobs/router.py:128`) |
| Transport controls | `allow_origins=["*"]`, all methods, all headers (`api/main.py:38-43`) |

Every caller in §11 (anonymous, SYSTEM header, UNIDENTIFIED, WORKER, AUTHORITY, ENGINEER with any
scope) is treated identically, because nothing reads the caller's identity.

---

## 5. Current mutation behavior [OBS]

| Can legacy `/optimize`… | Answer | Evidence |
|---|---|---|
| read jobs, blocks or possession windows from the server | **no.** It uses only the caller's payload | `optimize.py:18-35` |
| invoke CP-SAT | **yes**, once per call | `audit/service.py:113` |
| mutate jobs, create or withdraw proposals, touch committed or uncommitted work | **no** | import guard `test_job_lifecycle_accountability.py:1844-1846` |
| write `job_events` | **no** | same |
| write `optimization_runs` | **yes, one row per call, success or solver exception** | `audit/service.py:112-148` |
| return corridor-wide *server* information | **no.** The result is derived only from caller input | `solver.py:163` |
| create records later visible through v1 | **yes** | §9 |
| create DDL at import | yes. `CREATE TABLE IF NOT EXISTS` and trigger install run when `api.main` is imported | `optimize.py:26`; `audit/repository.py:41,48-80`; acknowledged in `backend/tests/conftest.py:1-13` |

**Transaction boundary.** Each INSERT is its own connection-scoped commit
(`audit/repository.py:92-127`). There is no lock (compare `jobs/optimization.py:443`), and none is
needed: no shared mutable job state exists on this path.

**The only persistent mutation an unauthorized caller can cause is one `optimization_runs` row per
request.**

---

## 6. Current audit behavior [OBS]

| Field | Legacy `/optimize` | v1 `optimize-jobs` |
|---|---|---|
| `run_id` | server `uuid4` (`audit/service.py:106`) | server `uuid4`, minted before the run (`jobs/optimization.py:487`) |
| `actor` | literal `"system:unauthenticated-demo"` (`audit/models.py:27`) | `requester.actor_id` (`jobs/optimization.py:562`) |
| `trigger` | literal `"optimize"` (`optimize.py:34`) | `"jobs_optimize"` (`jobs/lifecycle.py:1031`) |
| `corridor_id` | **caller-supplied** (`audit/service.py:110`) | validated deployment corridor (`jobs/optimization.py:418-424`) |
| `request_json` | **caller-supplied**, serialized | server-built from the snapshot (`jobs/optimization.py:499-510`) |
| `result_json`, `solver_status`, `solve_time_seconds` | derived by the solver **from caller input** | derived from server state |
| `provenance_snapshot_json` | **NULL**: none passed (`optimize.py:31-35`, `audit/service.py:89,109`) | always set (`jobs/optimization.py:564-571`) |
| `error` | exception text on an ERROR row | same |
| authorization context (role, assurance, scope) | not recorded | **not recorded either**: `actor` is a bare string (`audit/models.py:36-60`) |

---

## 7. `OptimizationRunRecord` integrity analysis

**[OBS] An unauthenticated caller can create a persistent `OptimizationRunRecord`.**
- The table is append-only: no code path updates or deletes it, and SQL triggers reject
  UPDATE/DELETE (`audit/repository.py:11-18,73-78`).
- So a forged row **cannot be removed or corrected**.

**What the caller controls.** `corridor_id` and the entire `request_json`:
- candidates, blocks, tracks and possession windows;
- horizon and headway.

Through its input, the caller also indirectly controls `result_json`, `solver_status`,
`solve_time_seconds` and `error`.

**What the server derives.** `run_id`, `requested_at`, `completed_at`, `actor`, `trigger`, and the
absence of provenance.

**Can a forged row pass for a legitimate railway run?**
- **By content, yes.** The caller can name `corridor_id="CORR-NDLS-AGC"` and submit candidates,
  sections and windows shaped exactly like real ones. Nothing validates them against topology.
- **By metadata, no.** `trigger="optimize"`, `actor="system:unauthenticated-demo"` and a NULL
  `provenance_snapshot_json` are all **server-set and not caller-controlled**. They mark every
  legacy row reliably. The jobs pipeline can never produce that combination (§6).

The weakness is that the v1 read path serves both kinds of row identically (§9). A human or a
downstream consumer must know to check `trigger`; nothing enforces or signals it.

**Correlation.** Legacy run ids are never returned (`OptimizationResult` has no `run_id`,
`contracts/schemas.py:304-314`) and are random `uuid4`. A forged row therefore cannot be
*targeted* by a later read unless someone reads the database. It still sits in the table, where:
- `AuditRepository.list_all` / `list_by_corridor` would return it to any future listing surface
  (`audit/repository.py:142-166`);
- a future run-history route would inherit it.

**Growth.** With no identity there is also no rate limit, so an anonymous caller can grow an
append-only table without bound. Deletion is impossible by design.

---

## 8. Actor and provenance analysis

### 8.1 Actor modelling

**[OBS] Actor kinds already modelled.**
- `SYSTEM:<component>`, e.g. `SYSTEM:OPTIMIZER` (`identity/actor.py:242-255,281`).
- `UNIDENTIFIED` with assurance `NONE` (`identity/actor.py:112,263-275`).
- A human role with `DECLARED_UNVERIFIED` (`identity/actor.py:86-97,218-238`).
- Nothing authenticated yet; that is 10.3.

**[OBS] The audit row keeps none of this.** `OptimizationRunRecord.actor` is a bare string
(`audit/models.py:36-60`).
- The legacy value `"system:unauthenticated-demo"` is its own placeholder. It is **not** the
  `Actor` model's `SYSTEM:` form or `UNIDENTIFIED`.
- A v1 run stores `"ENGINEER-003"` (declared, unverified) or `"UNIDENTIFIED"`. It does not store
  the role or the assurance.
- A future NovaForge-authenticated actor would store an id indistinguishable from today's declared
  one.

`actor_id` alone is therefore **not** sufficient to tell "declared" from "authenticated". This is
consistent with the 10.1A design, where `person_id` becomes the stable key once authentication
exists.

### 8.2 Can existing columns tell the kinds of run apart?

| Kind | Distinguishable today? | How |
|---|---|---|
| Legacy demo solve | **yes** | `trigger_name="optimize"` (server-set) plus NULL provenance |
| v1 jobs-pipeline run | **yes** | `trigger_name="jobs_optimize"` plus non-NULL provenance |
| Test fixture rows | yes, by trigger (`"TEST"`/`"test"`) | `test_audit.py:187`; `test_slice10_1d_3_corridor_authority.py:60-80` |
| Synthetic data inside a v1 run | yes | `provenance_snapshot_json.provenance` axes (`jobs/optimization.py:145-179,569`) |
| Declared vs future NovaForge-authenticated human | **no** | nothing records assurance. **[DEFER]** to 10.3: add an assurance/principal column then, as a future schema change |

**Conclusion [OBS/PROP].** `trigger_name` already separates legacy from authorized runs. **D-3
needs no schema change.** The declared-vs-authenticated distinction belongs to 10.3, not D-3.

---

## 9. Interaction with `GET /v1/optimization-runs/{id}`

### 9.1 Chain [OBS]

1. `jobs/router.py:116`: `service = JobService()` → `JobRepository()` → `DEFAULT_DB_PATH`
   (`jobs/repository.py:37-42,92`).
2. `optimization_run` reads `AuditRepository(self.repository.db_path).get(run_id)`
   (`jobs/service.py:1906`).
3. The legacy route writes to `AuditRepository()` → the same `DEFAULT_DB_PATH`
   (`audit/repository.py:39`).

So a legacy row **is** in the v1 read path. Nothing in `optimization_run` looks at `trigger`,
`actor` or provenance (`jobs/service.py:1879-1924`).

### 9.2 Behavior per case

| Case | Shipped `UnenforcedPolicy` | `EnforcingPolicy` + 10.1D.3 topology |
|---|---|---|
| Run exists, jobs pipeline | 200 | 200 for a corridor-complete ENGINEER; 403 otherwise |
| Run exists, **legacy, `corridor_id="CORRIDOR_A"`** (the real row) | **200 to anyone** | 403 to everyone: no topology for `CORRIDOR_A`, so fail closed (`identity/policy.py` `_require_corridor`) |
| Legacy row forging `corridor_id="CORR-NDLS-AGC"` | 200 to anyone | **200 to a corridor-complete ENGINEER, indistinguishable except by `trigger`** |
| No trusted actor | served; `actor` is shown as stored | same |
| Missing provenance | served with `provenance_snapshot_json: null` (the field is optional, `jobs/models.py:760`) | same |
| NULL or blank corridor | 200 | 403 "names no corridor" |
| Predates the authorization model | same as any row | same |

The dangerous cell is the third row. The recommended fix stops **new** such rows. Rows written
before the fix stay forever (§22).

---

## 10. Security threat model

| Threat | Today | After §18 |
|---|---|---|
| **T1 Audit forgery.** Plant a run claiming the real corridor with fabricated candidates or outcomes | possible, anonymous | **closed for new rows** |
| **T2 Audit flooding.** Unbounded append-only growth by anonymous callers | possible | **closed** (no write) |
| **T3 Misattribution.** A forged run read as an authority decision | possible; mitigated only by `trigger` | new rows impossible; old rows remain labelled |
| **T4 Compute exhaustion.** Unauthenticated CP-SAT solves | possible (also `/recover`) | **unchanged**, [DEFER] 10.3 / retirement |
| **T5 State tampering.** Jobs, proposals, commitments | **not possible** (§5) | not possible |
| **T6 Information disclosure** through the response | none: echoes the caller's own data | none |
| **T7 Import-time DDL** on repo-root `jobs.db` | yes (`optimize.py:26`, plus `jobs/router.py:116`) | legacy half removed; the router half remains (conftest still needed) |

---

## 11. Failure-mode matrix

Rows marked **Today** are [OBS] (per §4, identity is ignored). Rows marked **After** are [PROP] for
the recommended stateless route.

| Caller / condition | Today: status | Today: `optimization_runs` row | After: status | After: row |
|---|---|---|---|---|
| Anonymous, valid body | 200 | **yes** | 200 | **no** |
| `X-Actor-Role: SYSTEM` | 200 (the header is ignored; only v1 refuses it with 403, `api/deps.py:85-90`) | yes | 200 | no |
| UNIDENTIFIED / invalid actor headers | 200 (ignored) | yes | 200 | no |
| WORKER / AUTHORITY / ENGINEER (any scope) | 200 | yes | 200 | no |
| Malformed body | 422, FastAPI default body (`test_slice7_contract_freeze.py:173-180`) | no | 422 unchanged | no |
| Unknown or caller-invented corridor | 200 | **yes, with that corridor** | 200 | no |
| Solver raises | 500 after an **ERROR row** (`audit/service.py:112-131`) | yes | 500 | no |
| Audit INSERT fails (e.g. locked database) | 500. On the success path the result is lost. On the error path the INSERT failure **replaces** the solver exception, which survives only as `__context__` (`audit/service.py:114-131`) | no | cannot happen: there is no write | — |
| Concurrent calls | independent; no lock and no shared state | one row each | independent | none |
| Stale request | not applicable: no proposals or pins exist on this path | — | — | — |

Under the recommended design, **no identity check is added and no 403 is introduced**. That is
deliberate (§18): the route stops writing, so it needs no authorization to stay safe. Its identity
is 10.3's job.

---

## 12. Transaction and atomicity analysis

- **[OBS] v1 ordering is correct and is the reference.** Role → corridor validation (404) →
  corridor-complete scope (403) → lock → snapshot → possession → audit INSERT → one batch
  transaction for jobs and events (`jobs/optimization.py:413-607`). An unauthorized request writes
  nothing (asserted in `test_slice10_1d_3_corridor_authority.py:217-239,349-389`).
- **[OBS] Legacy ordering.** Parse → solve → INSERT. With no authorization step, the invariant
  "unauthorized ⇒ no persistent mutation" is **violated on every call**.
- **[PROP] After the fix.** Parse → solve → return, with no persistence. The invariant holds by
  construction, because there is nothing left to authorize before writing.
- **Residual after authorization (v1 only).** If the solver raises, v1 leaves an `ERROR` run row
  plus `OPTIMIZATION_FAILED` history and withdrawn proposals (`jobs/optimization.py:574-578,641-667`).
  That is correct and unchanged.

---

## 13. Option A — Retire legacy `/optimize`

| Aspect | Analysis |
|---|---|
| Security / audit | best: T1 to T4 close for this route |
| API compatibility | removes a route in the frozen snapshot (`docs/openapi-v1.json:2330`) and the inventory (`test_slice7_contract_freeze.py:157-165`): an **OpenAPI change** |
| Frontend | every dashboard load silently degrades to canned fixture data, with only a `console.warn` (`data.ts:107-123`). The live CP-SAT demo disappears without a visible error |
| Tests | `test_api.py` (5 call sites), `test_audit.py:577-601`, `test_recover_api.py:228-235`, three slice-7 tests, the lifecycle inventory |
| NovaForge | compatible, and cleanest |
| Complexity / risk | low code, **high demo risk** mid-cycle. SLICE7 kept the route for exactly this reason (`SLICE7_ARCHITECTURE_GATE_AUDIT.md:401-402`) |
| Verdict | **the correct end state, not available now.** [DEFER] until the dashboard consumes v1 |

## 14. Option B — Put `/optimize` behind the same authorization

| Aspect | Analysis |
|---|---|
| Security | **none in the shipped configuration.** Under `UnenforcedPolicy` the checks permit everyone (`identity/authorization.py:260-284`), so the forgery continues unchanged. It only helps where `EnforcingPolicy` is installed, and no deployment installs it |
| Semantics | category error. `REQUEST_OPTIMIZATION`'s resource is the corridor's **server jobs**, but `/optimize` solves the **caller's** payload. Being corridor-complete in *C* would authorize writing a fabricated run *labelled* C: authorization would legitimize the forgery instead of preventing it |
| Frontend | sends no actor headers and names `CORRIDOR_A`. Under enforcement it is denied, so the dashboard degrades exactly as in A |
| Architecture | needs the deployment's policy, which lives on `jobs.router.service`. The route would have to reach `backend.app.jobs`, which is exactly what the boundary test forbids (`test_job_lifecycle_accountability.py:1844-1846`). The check is a literal source-string check, so indirection could evade it, but that would defeat a deliberate boundary rather than honor it. B therefore **costs an approved amendment to an architectural boundary test** |
| OpenAPI | `Depends(request_actor)` adds two header parameters: an **OpenAPI change** |
| Verdict | **rejected** |

## 15. Option C — Internal-only

| Aspect | Analysis |
|---|---|
| Mechanism | there is none: one FastAPI app with `allow_origins=["*"]` (`api/main.py:38-43`). It would need a config flag that skips `include_router`, or a network control outside the repo |
| Frontend | the dashboard is an external caller (browser and Next server), so it breaks as in A |
| OpenAPI | the route vanishes when the flag is off, so the snapshot test depends on configuration |
| Verdict | **rejected.** It combines A's breakage with configuration-dependent contracts |

## 16. Option D — Keep it unauthorized

This option splits into two variants, and the split decides the outcome.

- **D-as-is (unauthorized *and* persisting).** **Rejected.** This is the defect itself: T1, T2 and T3.
- **D-stateless (unauthorized, persisting nothing).** There is a legitimate reason for an
  unauthenticated *compute* surface: a stateless demo or what-if solver over caller data, which
  holds no server state and makes no railway decision.
  - The architecture already accepts exactly this for `/recover`
    (`api/routers/recover.py:24-35`; `SLICE10_1D3_CORRIDOR_AUTHORIZATION_ARCHITECTURE.md` §12).
  - A solve over caller data is **not** a railway optimization decision, so it has no place in the
    authoritative run audit.

  **This is the recommendation (§18).** Its remaining cost is T4, which is 10.3's.

## 17. Option E — Compatibility wrapper around the v1 path

| Aspect | Analysis |
|---|---|
| Fit | none. v1 optimizes **server jobs** and returns `JobOptimizationResponse` (`jobs/models.py:443-493`). Legacy optimizes a **payload** and returns `OptimizationResult`. A wrapper would either ignore the caller's body, which breaks the contract, or feed caller candidates into the jobs pipeline, which is an injection path into lifecycle state and **much worse** than today |
| Architecture | same boundary-test cost as B |
| Verdict | **rejected** |

## 18. Recommended architecture [PROP]

**On the brief's §7 ("if `/optimize` is retained, `REQUEST_OPTIMIZATION` must require the
10.1D.3 corridor-complete rule").** This recommendation retains the route *without* that rule. The
departure is deliberate:
- §7 presumes a retained `/optimize` that *makes a railway optimization decision*. R-1 makes it not
  one. It becomes stateless compute over caller-supplied data: no server state, no record. That is
  exactly `/recover`'s existing posture, which never required corridor authority.
- The condition is therefore dissolved, not waived.
- If the owner reads §7 as binding on the route's *existence* rather than its *effect*, the answer
  is **Option A (retire)**, not Option B. B's check is a no-op under the shipped `UnenforcedPolicy`,
  and under enforcement it would *authorize* a fabricated run labelled with the caller's corridor
  instead of preventing it (§14).

**R-1: make legacy `POST /optimize` stateless.**
- `optimize()` returns `solve(request)` directly: no `AuditService`, no `AuditRepository`, no write.
- This is exactly the `/recover` posture.
- It is the **only** option that meets the stated invariant "unauthorized ⇒ no persistent mutation"
  **in the shipped configuration**. It does so by removing the write, not by adding an identity check
  the shipped `UnenforcedPolicy` would wave through.
- Request model, response model, status codes, the 422 body, the OpenAPI entry and the frontend are
  all unchanged. No `backend.app.jobs` import is added, and the boundary test stays green.
- It also removes the legacy half of the import-time DDL (T7). The router half remains, so
  `backend/tests/conftest.py` is **still needed**.

**R-2: the v1 read path.**
- **Default [PROP]:** no change. D-3 must not silently remove records.
- Existing legacy rows remain readable as stored and are self-labelled (§22).

**R-3: end state [DEFER].**
- Option A, once the dashboard consumes v1 (`optimize-jobs` plus the run read).
- Identity on any surviving public solver arrives with 10.3.

**Alternative considered: pin the legacy `AuditService` to a separate database file.**
- It is also one line, and it also keeps new rows out of v1.
- **Not recommended.** Anonymous callers could still write permanently, and the table would still
  grow without bound. It also needs a new configured path. The invariant is violated in a different
  file rather than removed.
- It remains the fallback if the owner decides demo solves **must** stay audited (D3-1).

---

## 19. NovaForge compatibility

- R-1 adds nothing identity-related. Nothing needs to be undone when NovaForge arrives.
- Pashupatastra issues no credentials, and NovaForge supplies no topology.
- The authoritative run trail stays written **only** by `JobOptimizationService`, which already
  passes role + corridor-complete scope before its write (10.1D.3). That is the single place a
  future NovaForge-verified actor will flow through. No second authorization implementation, no
  duplicated scope logic, no new role.
- **[DEFER] 10.3.** Record assurance or principal on `optimization_runs` so declared and
  authenticated runs can be told apart (§8.2).

## 20. API compatibility

| Surface | Impact of R-1 |
|---|---|
| `POST /optimize` request / response / 422 body | unchanged |
| `POST /optimize` status codes | unchanged: 200, 422, and 500 on a solver exception |
| v1 routes, response shapes and error codes | unchanged |
| `docs/openapi-v1.json` | **unchanged**. The snapshot compares params, body and responses (`test_slice7_contract_freeze.py:789-830`), and none of them move |
| Observable behavior change | legacy calls no longer create `optimization_runs` rows. No client can observe this, because run ids were never returned |

## 21. Migration strategy

1. Remove the audit wrapper from `optimize.py`. Deploy.
2. There is no data migration and no schema change.
3. Update the one test that asserts legacy rows are written.
4. Later, with the dashboard on v1: Option A, as its own approved OpenAPI change.

## 22. Existing-record strategy

**[OBS] The real `jobs.db` holds exactly one run** (read from a scratch copy):
- `trigger_name='optimize'`
- `corridor_id='CORRIDOR_A'`
- `actor='system:unauthenticated-demo'`
- `solver_status='OPTIMAL'`
- `provenance_snapshot_json=NULL`
- `requested_at=2026-09-10T11:37:37Z`

It is the only row, and there are no `jobs_optimize` rows.

- It **cannot be deleted**: append-only by triggers (`audit/repository.py:73-78`), and deleting it
  would also violate "do not silently make old records disappear".
- Under enforcement it is already unreadable to everyone, because `CORRIDOR_A` has no topology.
  Under the shipped policy it is readable by anyone who knows its uuid.

**[OPEN D3-2] What v1 should do with legacy-trigger rows:**

| Choice | Cost |
|---|---|
| (i) **Keep serving as stored** (recommended default) | nothing hides; the row is self-labelled by `trigger`/`actor`/NULL provenance |
| (ii) 404 for `trigger != "jobs_optimize"` | no schema change, but a currently-200 read on a frozen v1 route becomes 404; needs its own approval |
| (iii) additive `authority_class` / `legacy` field on `OptimizationRunResponse` | an OpenAPI change (additive) |

The tiebreak is **D3-3**: does this `jobs.db` ever ship as an operational railway database, or only
as the demo/dev file?
- If operational: prefer (ii) or (iii).
- If demo: (i), with this section as the documentation.

---

## 23. Production files expected to change [PROP]

| File | Change |
|---|---|
| `backend/app/api/routers/optimize.py` | drop the `AuditService` import and `_audit_service`; return `solve(request)`; rewrite the docstring to say stateless and non-authoritative, like `/recover` |

**Not changed:**

- `api/main.py`, `api/deps.py`, `api/routers/recover.py`;
- `audit/*`, still used by v1; `identity/*`, 10.1D.1–3 untouched;
- `jobs/*`, `optimizer/*`, `contracts/*`, `frontend/*`;
- `docs/openapi-v1.json`.

Under D3-2 (ii) only, `jobs/service.py` `optimization_run` would gain a trigger filter. It is **not**
part of the recommended scope.

## 24. Test files expected to change or be added [PROP]

- **Modify** `backend/tests/test_audit.py:577-601`. `test_optimize_endpoint_is_audited` inverts to
  `test_legacy_optimize_endpoint_writes_no_audit_row`: 200, and the row count is unchanged in an
  isolated database.
  - The test monkeypatches `optimize_router._audit_service`, which will no longer exist. Assert
    instead against a `PASHUPAT_JOBS_DB` scratch file, or against `AuditRepository(tmp).list_all()`
    through a patched default.
- **Add** `backend/tests/test_d3_legacy_optimize_is_stateless.py`:
  - `/optimize` with and without actor headers → 200 and zero new rows;
  - the solver raising → 500 and zero rows;
  - `optimize.py` source contains no `AuditService` / `AuditRepository`;
  - the v1 run read of a pre-existing legacy row is still 200 under `UnenforcedPolicy` (R-2);
  - v1 `optimize-jobs` is still audited with `trigger="jobs_optimize"` (regression).
- **Expected unchanged; verify:**
  - `test_api.py`, `test_recover_api.py`;
  - `test_slice7_contract_freeze.py`, including the OpenAPI snapshot;
  - `test_job_lifecycle_accountability.py`;
  - every `test_slice10_1d*` file and the full suite.

## 25. Schema / data migration

**None.** No table, column, index or trigger. `trigger_name` already discriminates (§8.2).

[DEFER 10.3] An assurance/principal column on `optimization_runs`.

## 26. OpenAPI impact

**None** for R-1. Option A (later) and D3-2 (iii) would each be an explicit, versioned OpenAPI change.

## 27. Explicit non-goals

- Authentication, NovaForge, OAuth/PKCE, token issuance. SoD, notifications, frontend work.
- Changing the optimizer.
- Any change to 10.1D.1, 10.1D.2 or 10.1D.3.
- Retiring `/optimize` or `/recover` now.
- Rate limiting (T4).
- Deleting or rewriting existing runs.
- Schema changes.
- Wiring `EnforcingPolicy` into the deployment.

## 28. Risks and deferred decisions

| ID | Item | Status |
|---|---|---|
| **D3-1** | Should demo solves keep *any* audit trail? Recommended: **no** (R-1). Fallback: pin to a separate DB file (§18 alternative) | **[OPEN]** |
| **D3-2** | v1 treatment of legacy-trigger rows: (i) / (ii) / (iii) (§22). Recommended default: (i) | **[OPEN]** |
| **D3-3** | Is `jobs.db` ever operational? This is the tiebreak for D3-2 | **[OPEN]** |
| R-a | Loss of per-solve demo telemetry. Mitigation, if wanted: a log line, not an audit row | accepted |
| R-b | T4 unauthenticated compute on `/optimize` and `/recover` | [DEFER] 10.3 / retirement |
| R-c | Pre-fix forged rows claiming the real corridor are served to corridor-complete ENGINEERs as ordinary runs | the only real one is `CORRIDOR_A`; other deployments' databases are unknown → D3-2 |
| R-d | Import-time DDL from `jobs/router.py:116` remains | [DEFER]; conftest keeps covering tests |
| R-e | Retiring `/optimize` (Option A) | [DEFER] until the dashboard moves to v1 |
| R-f | The audit row does not record actor assurance | [DEFER] 10.3 |

## 29. Implementation sequence [PROP] (after D3-1 is decided as recommended)

1. Rewrite `optimize.py` to be stateless, with an updated docstring.
2. Invert `test_audit.py::test_optimize_endpoint_is_audited`.
3. Add `test_d3_legacy_optimize_is_stateless.py`.
4. Run the full suite on a scratch `PASHUPAT_JOBS_DB`. Confirm the OpenAPI snapshot, the route
   inventory and the boundary guard are unchanged. Run `git diff --check`.
5. *(Only if D3-2 (ii) or (iii) is approved.)* Make the read-path change as its own commit.

## 30. Acceptance criteria

1. `POST /optimize` creates **zero** `optimization_runs` rows for every caller and every outcome,
   including a solver exception.
2. `POST /optimize` request, response, status codes and 422 body are unchanged, and the OpenAPI
   snapshot passes unmodified.
3. `api/routers/optimize.py` imports neither `backend.app.jobs` nor `backend.app.audit`.
4. The v1 `optimize-jobs` path is still audited (`trigger="jobs_optimize"`, actor, provenance), and
   10.1D.3 corridor-complete behavior is unchanged.
5. Existing `optimization_runs` rows are neither deleted nor altered. Their v1 read behavior is as
   decided in D3-2.
6. No schema, index, migration, `JobStatus`, `JobEventType`, lifecycle, optimizer, frontend or
   NovaForge change.
7. The full backend suite is green, and `jobs.db` is untouched.

---

*Files inspected (read-only):*

- **API:**
  - `backend/app/api/routers/optimize.py`, `recover.py`
  - `backend/app/api/main.py`, `deps.py`, `errors.py` (handlers)
- **Audit and identity:**
  - `backend/app/audit/service.py`, `models.py`, `repository.py`
  - `backend/app/identity/actor.py` (targeted)
- **Jobs, optimizer and simulation:**
  - `backend/app/jobs/optimization.py`, `service.py`, `router.py`, `repository.py`, `lifecycle.py`,
    `models.py` (targeted)
  - `backend/app/optimizer/solver.py` (grep)
  - `backend/app/simulation/simulator.py`
- **Tests:**
  - `backend/tests/conftest.py`
  - `test_audit.py`, `test_api.py`, `test_recover_api.py` (grep and targeted)
  - `test_slice7_contract_freeze.py`, `test_job_lifecycle_accountability.py` (targeted)
- **Frontend:** `frontend/src/lib/data.ts`, `src/app/page.tsx`, `src/components/DashboardClient.tsx`,
  `src/data/corridor_a_blocks.json`
- **Scripts:** `scripts/run_milestone1.py`
- **Contract and docs:**
  - `docs/openapi-v1.json`
  - `SLICE7_ARCHITECTURE_GATE_AUDIT.md` (lines 399-403)
  - `docs/SLICE10_1D3_CORRIDOR_AUTHORIZATION_ARCHITECTURE.md`
  - repo-wide grep for `/optimize` and `record_run`
- **Data:** a scratch copy of `jobs.db` (`optimization_runs`)
