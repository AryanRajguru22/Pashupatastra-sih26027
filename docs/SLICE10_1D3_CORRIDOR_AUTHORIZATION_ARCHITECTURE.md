# Slice 10.1D.3 — Corridor-Level Authorization and Optimization-Run Visibility

**Architecture / security gate. READ-ONLY. Nothing is implemented here.**

| | |
|---|---|
| Baseline | `HEAD` = `origin/main` = `64ef171eb7ba072362d8ccfa93dfd44c1065676a` |
| `jobs.db` SHA-256 | `245fd9ff9a9861af6ada9e6c53eb7ea2170493499b7b4386ef0634069e852386` (unchanged by this gate) |
| Date | 2026-09-22 |
| Scope | R-4 from `docs/SLICE10_1D2_COLLECTION_SCOPE_ARCHITECTURE.md:750`, generalized to every corridor-wide surface |

Labels: **[OBS]** observed in the repository (file:line) · **[PROP]** proposed ·
**[OPEN]** needs an owner decision · **[DEFER]** explicitly out of scope for 10.1D.3.

---

## 1. Executive verdict — **AMBER**

**The corridor-authorization model can be decided now. It needs no API change, no schema change and
no optimizer change. Implementation should wait for two owner decisions, and one pre-existing
legacy-route defect needs a named owner.**

Findings that drive the verdict:

1. **[OBS] In this deployment the current corridor rule separates nobody.** `_require_corridor`
   permits any actor who holds *any* scope in the corridor (`backend/app/identity/policy.py:264-265`).
   `optimize_corridor` refuses every corridor except the deployment's own
   (`backend/app/jobs/optimization.py:418-424`). One corridor, so every scoped ENGINEER passes. The
   check exists but distinguishes no two scoped actors.
2. **[OBS] R-4 is bigger than disclosure: it is a cross-section write.** A section-scoped ENGINEER
   who triggers `optimize-jobs` makes the system:
   - snapshot every active job in the corridor (`backend/app/jobs/service.py:3161-3176`);
   - write `OPTIMIZATION_REQUESTED` (with the requester's actor) and `OPTIMIZATION_COMPLETED` events
     onto every one of those jobs (`backend/app/jobs/lifecycle.py:1034-1047`, `:1136-1150`);
   - re-place or withdraw uncommitted proposals in every section, including on failure
     (`backend/app/jobs/optimization.py:593-607`, `:641-667`; `backend/app/jobs/lifecycle.py:1301-1340`).

   A section-scoped actor therefore changes proposals in sections it holds no authority over.
3. **[OBS] The legacy `POST /optimize` writes to the audit trail without authentication.** It has no
   actor dependency and no authorization call (`backend/app/api/routers/optimize.py:29-35`). It
   writes `optimization_runs` rows through a default `AuditService()` →
   `AuditRepository(DEFAULT_DB_PATH)` (`backend/app/audit/service.py:79-80`;
   `backend/app/audit/repository.py:33,39`). That is the same repo-root `jobs.db` the v1 service
   opens by default (`backend/app/jobs/repository.py:37-42`; `backend/app/jobs/service.py:722-724`).
   The row's `corridor_id` comes from the caller (`backend/app/audit/service.py:110`), and
   `GET /v1/optimization-runs/{id}` later serves such rows.
   This predates 10.1D and is **not** R-4. It is still the most serious item found, so it is named
   as decision **D-3** with its own owner. It is not buried in a deferred table.

Why the verdict is AMBER and not RED:

- **[OBS] No deployment installs an enforcing policy.** `EnforcingPolicy` is constructed nowhere
  under `backend/app/`; the only hit outside `policy.py` is a comment
  (`backend/app/jobs/service.py:1915`). The router builds `JobService()`
  (`backend/app/jobs/router.py:116`), which defaults to `UnenforcedPolicy`
  (`backend/app/jobs/service.py:669`).
- So the gap is **latent**. It only matters once enforcement is installed. The legacy route's lack of
  authentication is the same as the whole pre-10.3 application's
  (`backend/app/api/deps.py:53-58`).
- **[OBS] Legacy run ids are never returned to callers.** `OptimizationResult` has no `run_id` field
  (`contracts/schemas.py` §OptimizationResult, lines 304-314), and ids are `uuid4`
  (`backend/app/audit/service.py:44-47`). Reaching a polluted row through v1 therefore needs
  database access or an id leaked some other way.

What must happen before implementation starts:

- **D-1**: approve the corridor-completeness rule in §10.
- **D-2**: confirm that AUTHORITY stays without `READ_OPTIMIZATION_RUN` (the default).
- **D-3**: record an owner and a remediation for the legacy `/optimize` audit write.

---

## 2. Corridor-wide endpoint inventory (exact)

Route registration: `backend/app/api/main.py:45-48`.

| # | Route | Handler | Actor? | Seam action | Corridor-wide because |
|---|---|---|---|---|---|
| E-1 | `POST /v1/corridors/{corridor_id}/optimize-jobs` | `backend/app/jobs/router.py:474-504` | yes (`request_actor`) | `REQUEST_OPTIMIZATION`, role then corridor (`backend/app/jobs/optimization.py:413-437`) | solves and **mutates** every active job in the corridor. The response lists all of them (§16) |
| E-2 | `GET /v1/optimization-runs/{run_id}` | `backend/app/jobs/router.py:1013-1043` | yes | `READ_OPTIMIZATION_RUN`, role → load → corridor (`backend/app/jobs/service.py:1897-1925`) | returns the stored request, result and provenance **verbatim**: every candidate, window and placement in the corridor |
| E-3 | `POST /optimize` (legacy) | `backend/app/api/routers/optimize.py:29-35` | **none** | **none** | solves caller-supplied candidates for a caller-named corridor and **writes an audit row** |
| E-4 | `POST /recover` (legacy) | `backend/app/api/routers/recover.py:24-35` | **none** | **none** | solves caller-supplied data. Returns the whole request, the disruption and the result. **No persistence** |

**Section-scoped routes that carry corridor-derived data** (not corridor-wide themselves, but a place
where corridor data can leak; see §16):

| Route | Carries | Evidence |
|---|---|---|
| `GET /v1/jobs`, `GET /v1/jobs/{id}` | `proposal_run_id` | `backend/app/jobs/models.py:189` |
| `GET /v1/jobs/{id}/history` | each event's `optimization_run_id` and metadata, including **corridor-wide batch counts** | `backend/app/jobs/models.py:339`; `backend/app/jobs/lifecycle.py:1091-1095,1148` |
| `GET /v1/jobs/{id}/proposal` | `optimization_run_id`, `trains_avoided`, `committed_resource_mates` | `backend/app/jobs/models.py:420`; `backend/app/jobs/optimization.py:269-301` |
| `GET /v1/jobs/{id}/execution` | `optimization_run_id` | `backend/app/jobs/models.py:601` |
| `GET /v1/obligations`, `GET /v1/jobs/{id}/obligation` | `optimization_run_id` | `backend/app/jobs/models.py:694` |

There is no export endpoint, no run-listing endpoint and no optimization-history collection
[OBS: route inventory frozen at `backend/tests/test_slice7_contract_freeze.py:140-164`].
`AuditRepository.list_all` and `list_by_corridor` exist (`backend/app/audit/repository.py:142-166`),
but no route reaches them.

---

## 3. Service-level corridor-wide surfaces (exact)

| # | Surface | Evidence | Nature |
|---|---|---|---|
| S-1 | `JobOptimizationService.optimize_corridor` | `backend/app/jobs/optimization.py:405-639` | corridor read + corridor write + audit write |
| S-2 | `JobService.optimization_snapshot` / `classify_for_optimization` | `backend/app/jobs/service.py:3121-3178` | reads **all** active jobs (`list_active`); no section filter |
| S-3 | `JobService.possession_inputs` / `possession_windows` | `backend/app/jobs/service.py:2926`, `:3100-3119` | derives corridor possession windows plus train rejections |
| S-4 | `JobService.active_block_candidates` | `backend/app/jobs/service.py:2854-2871` | every active job's candidate; **no actor, no seam** (in-process only, no route) |
| S-5 | `JobService.optimization_run` | `backend/app/jobs/service.py:1879-1925` | one corridor-scoped audit record |
| S-6 | `JobService.authorize_corridor` | `backend/app/jobs/service.py:2800-2822` | builds `ResourceLocation(corridor_id, None)` |
| S-7 | `AuditService.record_run` | `backend/app/audit/service.py:82-150` | writes an immutable run row; `corridor_id` taken from the request |
| S-8 | `AuditRepository.get/list_all/list_by_corridor` | `backend/app/audit/repository.py:129-166` | unscoped reads (only `get` is reachable, via S-5) |
| S-9 | `plan_optimization_outcome` / `plan_optimization_failure` | `backend/app/jobs/lifecycle.py:1051-1150`, `:1301-1340` | per-job events and mutations for **every** considered job |
| S-10 | `simulate_disruption` | `backend/app/simulation/simulator.py:26` | pure; no persistence, no audit import |
| S-11 | `solve` | `backend/app/optimizer/solver.py:163` | one global CP-SAT model per request |

`JobService.set_schedule` (`ASSIGN_SCHEDULE`) is **not** corridor-wide. It places one job and
resource-authorizes that one job's section (`backend/app/jobs/service.py:2877-2923`).

---

## 4. Resource classification

These classes follow what the repository already models; §6 of the brief asked for no new ones.

| Class | Example in repo | Located by | Evidence | Treatment |
|---|---|---|---|---|
| **A. Section resource** | a job row, its `BlockCandidate`, its proposal, its executions, its obligation | `(corridor, block_candidate.section_id)` | `backend/app/identity/resource.py:87-148`; `backend/app/jobs/service.py:2600-2628` | unchanged (10.1D.1 / 10.1D.2) |
| **B. Multi-section resource** | none persisted. `match_scope` already refuses these as UNRESOLVED | — | `backend/app/identity/resource.py:25-33,110-111` | **not introduced.** No stored object is "S1+S2+S3" |
| **C. Corridor resource** | `OptimizationRunRecord`; an optimization request (the action) | `(corridor, None)` | `backend/app/audit/models.py:36-60`; `backend/app/jobs/service.py:2818-2822` | **[PROP] corridor-complete authority** (§10) |
| **D. Derived corridor response** | `JobOptimizationResponse`; the run's `request_json`/`result_json`/`provenance_snapshot_json` | inherits from C | `backend/app/jobs/models.py:443-493,732-761` | visible **in full, or not at all**. Never filtered (§9) |
| **E. Caller-supplied corridor payload** | legacy `/optimize` and `/recover` bodies | nothing server-side | `contracts/schemas.py` `OptimizationRequest`/`RecoveryRequest` | holds no server state, so scope filtering has no meaning. The **audit write** is the problem (D-3) |

**[OBS] A run is corridor-scoped, not "multi-section".** The snapshot takes every active job in the
repository with no section predicate (`backend/app/jobs/service.py:3161-3176`), and the request
carries every corridor track (`backend/app/jobs/optimization.py:501-504`).

A run "covering S1–S4" in the brief's example is therefore a corridor run in which S5 and S6 happened
to have no active jobs. Its request still names every track and every derived possession window.

**[OBS] A run record is an immutable snapshot and a traceability record.** It is append-only through
SQL triggers (`backend/app/audit/repository.py:73-78`) and no update or delete path exists
(`backend/app/audit/repository.py:11-18`). Its payload fields are opaque, pre-serialized strings by
contract (`backend/app/jobs/models.py:740-742`).

---

## 5. Current authorization behavior [OBS]

| Surface | Role check | Resource check | Order |
|---|---|---|---|
| E-1 optimize-jobs | `REQUEST_OPTIMIZATION`; ENGINEER only (`backend/app/identity/role_actions.py:89-95`) | `_require_corridor`: **any** scope in the corridor | role → validate corridor (404) → corridor → lock → snapshot (`backend/app/jobs/optimization.py:413-446`) |
| E-2 run read | `READ_OPTIMIZATION_RUN`; ENGINEER only | `_require_corridor` on `run.corridor_id` | role → DB `get` → 404 if missing → corridor → 403 (`backend/app/jobs/service.py:1897-1923`) |
| E-3 `/optimize` | none | none | — |
| E-4 `/recover` | none | none | — |
| `set_schedule` | `ASSIGN_SCHEDULE` is granted to **no** role (`backend/app/identity/role_actions.py:42-45`) | section (`backend/app/jobs/service.py:2901`) | role → load → section |

Default shipped policy: `UnenforcedPolicy` permits everything
(`backend/app/identity/authorization.py:260-284`; `backend/app/jobs/service.py:669`).

Corridor actions are classified at `backend/app/identity/role_actions.py:115-120`, and the rule is
chosen at `backend/app/identity/policy.py:195-200`.

---

## 6. Current security gaps

| ID | Gap | Severity | Evidence | Disposition |
|---|---|---|---|---|
| G-1 | A section-scoped ENGINEER can trigger a corridor optimization that **writes to jobs in every section**: events, proposal re-placement and withdrawal | High (once enforcement is wired) | §1 finding 2 | **10.1D.3** (D-1) |
| G-2 | A section-scoped ENGINEER reads the full corridor run: every candidate, window and placement, plus provenance | High (once enforced) | `backend/app/jobs/service.py:1916-1925`; `backend/app/jobs/router.py:1043` | **10.1D.3** (D-1) |
| G-3 | The optimize-jobs response lists every job in the corridor | Medium; closes together with G-1 | `backend/app/jobs/optimization.py:617-639` | **10.1D.3** (D-1) |
| G-4 | Legacy `/optimize` lets an unauthenticated caller write an audit row with a forged `corridor_id`, and v1 serves it | High integrity / low reachability | §1 finding 3 | **D-3**, named owner (§26) |
| G-5 | Legacy `/optimize` and `/recover` are unauthenticated solver compute (resource exhaustion) | Medium | `backend/app/api/routers/optimize.py:29`; `recover.py:24` | [DEFER] 10.3 / legacy retirement |
| G-6 | Corridor-wide batch counts reach section readers through job history | Low | `backend/app/jobs/lifecycle.py:1091-1095,1148` | accepted residual (§16) |
| G-7 | The run-read order distinguishes a missing run (404) from a denied one (403) | Low | `backend/app/jobs/service.py:1908-1923` | accepted (§10.3) |

---

## 7. Section-scoped actor analysis (the brief's example)

Corridor `CORR-NDLS-AGC`. **[OBS]** The default dataset declares this corridor with 7 stations and 6
segments (`pashupatastra_realistic_dataset.json` → `corridor`), so the registry derived from the
stations has sections S1–S6 (`backend/app/data/section_registry.py:171-226`).

The actor holds scope `{S1, S2}`. The run holds candidates from S1–S6 (or S1–S4).

### 7.1 Actor = AUTHORITY, scope {S1,S2}

| Capability | Today (enforcing) | Proposed | Why |
|---|---|---|---|
| See S1/S2 jobs, history, proposal, execution, obligation | ✅ | ✅ | section reads (`backend/app/identity/role_actions.py:68-76,96-104`) |
| See S3–S6 jobs | ❌ (403 single / absent from collections) | ❌ | 10.1D.1 / 10.1D.2 |
| **Approve** (commit), reject, postpone or release an S1/S2 proposal | ✅, then lifecycle + stale-run pin | ✅, unchanged | section actions; `expected_proposal_run_id` required (`backend/app/jobs/models.py:213-254`) |
| Approve anything in S3–S6 | ❌ | ❌ | section rule |
| **Request** optimization | ❌ **by role** | ❌ by role (and by D-1) | AUTHORITY lacks `REQUEST_OPTIMIZATION` |
| **Inspect** the run record | ❌ **by role** | ❌ by role, pending D-2 | ENGINEER-only (`backend/app/identity/role_actions.py:51-54`) |
| Inspect *why* its own S1/S2 proposal was placed | ✅ via `GET /v1/jobs/{id}/proposal` | ✅ | that route is the authorized per-section view of the run (§9, model D) |
| Export | n/a | n/a | no export surface exists (§2) |
| Assign a schedule directly | ❌ (no role holds it) | ❌ | `backend/app/identity/role_actions.py:42-45` |

The approving AUTHORITY never needs the corridor record to approve its own section's block. The
proposal already carries the run id it is pinned to and the explanation for that one job
(`backend/app/jobs/optimization.py:215-305`).

### 7.2 Actor = ENGINEER, scope {S1,S2}

| Capability | Today (enforcing) | Proposed |
|---|---|---|
| Section reads, S1/S2 | ✅ | ✅ |
| **Trigger** optimize-jobs | ✅ **(gap G-1)**, which mutates S3–S6 | ❌ 403 `AUTHORIZATION_DENIED` before any read or write |
| **Read** any run | ✅ **(gap G-2)** | ❌ 403 |
| See the optimize-jobs response | ✅ (G-3) | unreachable: the request is refused |

---

## 8. Corridor-scoped actor analysis

An ENGINEER whose same-role scopes in `CORR-NDLS-AGC`, taken together, name every registered section:

- may request optimization, receive the full `JobOptimizationResponse`, and read any run whose
  `corridor_id` is that corridor, in full, verbatim;
- still gets **no** section action it was not already granted through that same scope (it holds
  every section, so section reads pass through the normal section rule);
- still passes through every lifecycle guard. Authorization never reads state
  (`backend/app/identity/authorization.py:46-51`).

An AUTHORITY with full-corridor scope is still refused `REQUEST_OPTIMIZATION` and
`READ_OPTIMIZATION_RUN` by role. Full-corridor scope is **not** a new role. It is a property of
assignments the actor already holds.

---

## 9. Partial-visibility analysis

Actor `{S1,S2}`, run `{S1..S4}` (or `{S1..S6}`).

| Model | Verdict | Reason |
|---|---|---|
| **A. Full denial** | **✅ for partial-scope actors** | the same outcome as E for anyone below corridor-complete scope |
| **B. Filtered view** | ❌ | see *Why B and C fail* below |
| **C. Redacted run** | ❌ | same as B. "The run exists" is already known (§10.3), so a redacted shell adds nothing that is safe to add |
| **D. Authorized sub-run view** | ✅ **already exists** | `GET /v1/jobs/{id}/proposal`, `/history` and `/obligation` on each in-scope job give the run id, placement, window, objective score and trains avoided for **that job**. Built once at run time, per job (`backend/app/jobs/optimization.py:215-305`), and section-authorized (`backend/app/jobs/service.py:1637-1649`). This is the section-scoped view of a run |
| **E. Corridor-level authority required** | **✅ for the full record and for requesting a run** | the only model that is honest about an immutable corridor snapshot and needs no contract change |

**Why B and C fail:**

1. **Contract.** `request_json` is a **required** `str` (`backend/app/jobs/models.py:758`), and the
   three payloads are opaque strings *by deliberate choice*, so v1 does not freeze the inner shape of
   `OptimizationRequest` or `OptimizationResult` (`backend/app/jobs/models.py:740-742`). Filtering
   means parsing and re-serializing, which ties v1 to that inner shape.
2. **Integrity.** A filtered string is no longer the stored record, yet the route promises "the
   immutable audit record" (`backend/app/jobs/router.py:1022-1033`).
3. **Leakage survives the filtering** (§16):
   - `tracks` and `possession_windows` for every section;
   - `existing_committed_blocks`;
   - `total_priority_scheduled` and `total_risk_mitigated` (corridor sums, `backend/app/optimizer/solver.py:774-797`);
   - `solver_status` and `solve_time_seconds`;
   - `infeasibility_reasons`;
   - `rejection_reasons` that name other block ids (`backend/app/optimizer/solver.py:683-756`);
   - `provenance_snapshot_json`'s `uncovered_tracks` and `rejected_train_numbers`
     (`backend/app/jobs/optimization.py:564-571`, `:190-212`).

   Note that `provenance_snapshot_json` is **payload, not harmless metadata**.

**Decision [PROP]:** E for the corridor record and for the corridor action. D, which already exists,
for the section need. A dedicated per-section run view is [OPEN] / [DEFER]. It would be a new route or
a v2 shape (§19), never an in-place change to E-2.

---

## 10. Optimization-run authorization model [PROP] — answers OQ-2

### 10.1 The rule (D-1)

> A **corridor-scoped action** on corridor *C* is permitted only if the union of the section ids of
> every `RailwayScope` the actor holds **under the acting role** in *C* is a superset of *C*'s
> registered section set, taken from server-side topology. Anything less is `AuthorizationDenied`,
> which maps to 403 `AUTHORIZATION_DENIED`.

Properties:

- **Explicit, no wildcard.** Coverage is proven by naming every section. It is consistent with
  `backend/app/identity/scope.py:11-18`: *"a section added to a corridor's topology later is NOT
  covered by an existing scope"*. Adding a section therefore **removes** corridor authority until an
  assignment names that section. That fails closed.
- **Same role only.** `ScopeDirectory.scopes_for_actor(actor_id, role)` already returns scopes for
  one role (`backend/app/identity/policy.py:72-74`), so a WORKER assignment can never complete an
  ENGINEER's coverage.
- **Union across assignments** (D-4). Two ENGINEER assignments for S1–S3 and S4–S6 cover the
  corridor. Each section is still explicitly granted; requiring a single assignment would only be an
  artefact of how data was entered. *[OPEN, low stakes; recommended as stated.]*
- **Topology from the server, never the request.** The same rule as scope
  (`backend/app/identity/policy.py:19-23`).
- **No topology for *C*, or an empty section set:** deny ("corridor-wide authority cannot be proven").
  Absence is never a grant.
- **The denial message must not list the uncovered sections.** Router 403 bodies echo the exception
  text (`backend/app/jobs/router.py:235`); see §16.

### 10.2 Where it lives

- **[OBS]** `_require_corridor` is a `@staticmethod` that sees only `(actor, action, location, scopes)`
  (`backend/app/identity/policy.py:245-271`). `EnforcingPolicy` holds a `ScopeDirectory` and **no
  topology** (`backend/app/identity/policy.py:142-149`).
- **[OBS]** The existing `SectionDirectory` protocol exposes `corridor_id` and `has()` only, and
  cannot list a corridor's sections (`backend/app/identity/scope.py:61-67`).
- **[OBS]** `SectionRegistry.section_ids()` can list them (`backend/app/data/section_registry.py:399-400`).

**[PROP] Cost, stated honestly.** This is a constructor change to `EnforcingPolicy`: a new
keyword-only `topology` argument taking corridor section sets, keyed by `corridor_id`. A duplicate
corridor is refused at construction. Its default is **empty**, so every corridor action is denied
unless a deployment supplies topology. This keeps the ten existing `EnforcingPolicy(...)`
constructions in `test_slice10_1d_1_construction_guard.py` valid and fails closed.

Three things do **not** change:

- the `authorize_resource(actor, action, ResourceLocation)` signature;
- `JobService.authorize_corridor`, which already passes `ResourceLocation(corridor, None)`;
- the construction guard and the collection predicate.

A small pure helper beside `match_scope` in `backend/app/identity/resource.py` (for example
`covers_corridor(scopes, corridor_id, corridor_section_ids) -> ScopeMatch`, using the same
three-valued fail-closed result) keeps the policy free of set logic and can be tested in isolation.

### 10.3 `GET /v1/optimization-runs/{id}` with an out-of-scope `run_id`

- **[OBS] Run existence is already disclosed** to section readers. The run id appears in
  `JobResponse.proposal_run_id`, `JobEventResponse.optimization_run_id`,
  `BlockProposalResponse.optimization_run_id`, `ExecutionResponse.optimization_run_id` and
  `ObligationResponse.optimization_run_id` (`backend/app/jobs/models.py:189,339,420,601,694`).
  Ids are unguessable `uuid4` (`backend/app/audit/service.py:44-47`).
- **Decision [PROP]:** keep the current order (role → load → 404 → corridor → 403). A partial-scope
  ENGINEER gets **403**. A random id gets 404. Hiding existence would defend a fact the API already
  publishes, and it would need a status-code change.
- The run read stays behind `READ_OPTIMIZATION_RUN` on role, so WORKER and AUTHORITY get 403 before
  any database read (`backend/app/jobs/service.py:1898`). They get no existence oracle at all.

---

## 11. `POST /v1/corridors/{id}/optimize-jobs` model [PROP]

| Question | Answer |
|---|---|
| Who can call it today | anyone under `UnenforcedPolicy`; under `EnforcingPolicy`, any ENGINEER with any scope in the corridor [OBS §5] |
| Action | `REQUEST_OPTIMIZATION` (`backend/app/jobs/optimization.py:413-416,433-437`) |
| Resource | the corridor, `ResourceLocation(C, None)` |
| Corridor-wide input? | the path's `corridor_id` only. It must equal the deployment corridor or the call is a 404 (`:418-424`). The job set is **server-derived**, never supplied by the caller |
| Can the caller limit it to sections? | **no.** There is no body and no query parameter |
| Is client-supplied corridor data trusted? | only the path id, and only after it is checked against the deployment |
| Can results show unauthorized sections? | yes: every job, track, placement and refusal reason (§16) |
| Can a section-scoped authority trigger it? | AUTHORITY: no (role). ENGINEER {S1,S2}: **yes today (G-1); no under D-1** |

**Is decomposition by section safe? No, and it is not needed now.**

- **[OBS]** The solver builds one global CP-SAT model (`backend/app/optimizer/solver.py:163`).
  Several things couple sections:
  - `dependencies` (`:458-478`);
  - `mutual_exclusion_group` no-overlap across any blocks (`:482-512`);
  - one objective summed over all blocks (`:515-533`);
  - one shared time limit and one global status. A non-OPTIMAL/FEASIBLE status unschedules
    **every** block (`:577-604`);
  - an all-or-nothing batch write (`backend/app/jobs/lifecycle.py:1101-1106`);
  - possession derivation that withholds every section a rejected train may occupy
    (`backend/app/data/timetable_adapter.py:786-787`).
- **[OBS, incidental]** The jobs intake sets `dependencies=[]` and `mutual_exclusion_group=None` on
  every job (`backend/app/jobs/service.py:1179-1181`), and resource identity is `(track_id, section_id)`
  (`backend/app/optimizer/solver.py:51-67`). So *today's* jobs-pipeline model happens to separate by
  section.
- That is an accident of the intake path. The contract allows both coupling fields
  (`contracts/schemas.py:174-175`), and the legacy route accepts them from callers. **This gate
  refuses to design on it.** Section-partitioned optimization is [DEFER]: it changes optimizer inputs,
  which is out of scope, and it needs a domain ruling.
- **Conclusion.** Requesting an optimization is a corridor act with corridor-wide effects. It requires
  corridor-complete authority (D-1). The response is then disclosed to someone entitled to all of
  it, so **no response filtering is needed**, which is why G-3 closes together with G-1.
- **Ordering [PROP].** The check stays where it is: after corridor validation, before the lock,
  snapshot, audit row or any event (`backend/app/jobs/optimization.py:426-437`). A denied request
  runs nothing and records nothing (existing guarantee,
  `backend/tests/test_slice10_1d_scope_enforcement.py:565-588`).

---

### 11.2 Legacy `POST /optimize` model

| Question | Answer [OBS] |
|---|---|
| Who can call it | anyone. No `request_actor`, no header requirement (`backend/app/api/routers/optimize.py:29-35`) |
| JobAction | **none**. The seam is never consulted |
| Resource | none on the server. Candidates, windows, tracks and `corridor_id` all come from the caller |
| Corridor validated? | **no**. Any string is accepted and written to `optimization_runs.corridor_id` (`backend/app/audit/service.py:110`) |
| Section limiting | only by what the caller chooses to send |
| Can the response show unauthorized server data? | **no**. It returns the solver result over the caller's own input |
| Side effect | **an immutable audit row** in the shared `jobs.db` (`backend/app/audit/service.py:79-80`; `backend/app/audit/repository.py:33,39`), actor `system:unauthenticated-demo` (`backend/app/audit/models.py:27`) |
| Section-scoped authority involved? | no. No identity is involved at all |

**Model:** there is no authorization to reason about. Scope filtering has no meaning here, and adding
`REQUEST_OPTIMIZATION` would break the dashboard, which sends no actor headers. The defect is the
**write**, handled by **D-3** (§26). The legacy route is not part of v1 (`backend/app/api/main.py:25-33`).

## 12. `POST /recover` model

| Question | Answer [OBS] |
|---|---|
| Resource | none on the server. Input is a caller-supplied `OptimizationRequest` plus a `DisruptionEvent` (`backend/app/api/routers/recover.py:24-27`) |
| Corridor-wide? | yes, over **caller** data |
| Returns | the disruption, the updated request and the recovery result (`:31-35`), all derived from the caller's own input |
| Actor / authorization | none |
| Persistence | **none.** `simulator.py` imports only the solver and disruption helpers (`backend/app/simulation/simulator.py:7-14`). No audit write |
| Exposes server operational state? | **no**: it reads no repository, registry or timetable |
| Is scope filtering meaningful? | **no.** There is nothing server-side to filter |
| Needs corridor authority? | not for confidentiality. It is unauthenticated compute (G-5) → [DEFER] 10.3 / legacy retirement |

`/recover` is **not** a 10.1D.3 surface and must not be "fixed" with scope filtering. The frontend
calls it without actor headers (`frontend/src/lib/data.ts:231`), so any gating is a client-visible
change on a non-v1 route.

---

## 13. `READ_OPTIMIZATION_RUN`

| | |
|---|---|
| Role | ENGINEER only [OBS `backend/app/identity/role_actions.py:89-95`]. The earlier doc's table said "ENGINEER, AUTHORITY" (`docs/SLICE10_1D_RESOURCE_AUTHORIZATION_ARCHITECTURE.md:474`); the code chose the narrower grant, and the code is authoritative |
| Scope granularity | corridor-complete [PROP D-1] |
| Partial scope enough? | **no** |
| Decomposable? | not the record (§9). The per-job derived views already are (model D) |
| **[OPEN D-2]** | Grant AUTHORITY this read *with* corridor-complete scope? It becomes safe once D-1 lands. Default: **no** (widening is a decision). Approval does not need it (§7.1) |

## 14. `REQUEST_OPTIMIZATION`

| | |
|---|---|
| Role | ENGINEER only [OBS] |
| Scope granularity | corridor-complete [PROP D-1] |
| Partial scope enough? | **no.** The action writes to every active job in the corridor (§1 finding 2) |
| Decomposable? | not without optimizer and input changes [DEFER] (§11) |
| Cross-section coupling | structural in the solver, incidentally absent in intake (§11) |

## 15. `ASSIGN_SCHEDULE`

| | |
|---|---|
| Role | none: granted to no role (`backend/app/identity/role_actions.py:42-45,79-108`) |
| Scope granularity | section (`backend/app/identity/role_actions.py:127-139`; `backend/app/jobs/service.py:2901`) |
| Route | none (`backend/app/jobs/service.py:2886`) |
| Corridor question? | **none.** It places one job and bypasses the optimizer. **No change** |

Other optimization-related actions: none. `JobAction` has no further optimization member
(`backend/app/identity/authorization.py:64-117`).

---

## 16. Data-leakage analysis

Channels through which S3–S6 data reaches an `{S1,S2}` actor, **today, under an enforcing policy**:

| Channel | Carries | Via | After D-1 |
|---|---|---|---|
| run `request_json` | every candidate: ids, tracks, sections, asset ids, durations, windows, scores, metadata (distances, workers); all corridor tracks; every possession window; committed blocks; headway; horizon | E-2 | closed (403) |
| run `result_json` | every placement (time, section, scores); every unscheduled block; `rejection_reasons` naming other block ids; corridor totals; status; solve time; infeasibility reasons | E-2 | closed |
| run `provenance_snapshot_json` | `possession_window_count`, `uncovered_tracks`, `rejected_train_numbers` (≤20) and their count, derivation, four-axis provenance | E-2 | closed |
| run metadata | `actor` (the requester's id), `trigger`, timestamps, `corridor_id`, `error` | E-2 | closed |
| optimize-jobs response | all job ids, tracks, placements, refusal reasons, counts, `uncovered_tracks` | E-1 | closed (request refused) |
| 403 body text | `AuthorizationDenied` detail is echoed (`backend/app/jobs/router.py:157,235`). Today's section denial names a section and corridor (`backend/app/identity/policy.py:238-243`) | any denied call | **[PROP]** the corridor denial says only "does not cover every section of corridor C". It never lists the missing sections |
| job history `OPTIMIZATION_COMPLETED` metadata | corridor `considered/scheduled/unscheduled` counts | `GET /v1/jobs/{id}/history`, in scope | **residual, accepted**: aggregates only; history is append-only and canonical event serialization is frozen by instruction [DEFER] |
| job history `OPTIMIZATION_REQUESTED` | the requester's actor id and corridor | same | residual. After D-1 the requester is corridor-authoritative, so the attribution is legitimate |
| proposal `trains_avoided` | train numbers from rejections with **no** section attribution (`backend/app/jobs/optimization.py:296-301`) | `GET /v1/jobs/{id}/proposal` | residual, low: a train number without a location |
| proposal `committed_resource_mates` | ids of blocks on the **same** `(track, section)` (`backend/app/jobs/optimization.py:269-278`) | same | none cross-section |
| refusal reason text | `"dependency not scheduled: X"` or `"mutual exclusion group conflict …: X"` can name a cross-section id. `"lower objective …: X"` is same-resource only (`backend/app/optimizer/solver.py:676-756`) | job `last_refusal_reason` / history | **not produced today** (intake sets no deps or mutex); becomes live if intake ever sets them. [DEFER] |
| run id | existence of a run | five v1 response models (§10.3) | by design |

---

## 17. Information-disclosure matrix (after 10.1D.3)

| Data | WORKER {S1,S2} | ENGINEER {S1,S2} | AUTHORITY {S1,S2} | ENGINEER {S1..S6} | AUTHORITY {S1..S6} |
|---|---|---|---|---|---|
| S1/S2 job, history, proposal, execution, obligation | ✅ | ✅ | ✅ | ✅ | ✅ |
| S3–S6 same | ❌ | ❌ | ❌ | ✅ | ✅ |
| run id (via an in-scope resource) | ✅ | ✅ | ✅ | ✅ | ✅ |
| full run record | ❌ role | ❌ **scope** | ❌ role (D-2) | ✅ | ❌ role (D-2) |
| request an optimization | ❌ role | ❌ **scope** | ❌ role | ✅ | ❌ role |
| optimize-jobs response | ❌ | ❌ | ❌ | ✅ | ❌ |
| commit / reject / postpone / release S1/S2 | ❌ role | ❌ role | ✅ (+ lifecycle) | ❌ role | ✅ |
| corridor batch counts in history | ✅ residual | ✅ | ✅ | ✅ | ✅ |

---

## 18. NovaForge compatibility

- **Nothing here authenticates.** The rule consumes an `Actor` and the server-side scopes and
  topology. It is indifferent to whether the actor came from declared headers
  (`backend/app/api/deps.py:33-95`) or, later, a NovaForge-verified credential.
- **NovaForge supplies neither topology nor scope.** Corridor completeness is computed from
  `RoleScopeAssignment`s (`backend/app/identity/scope_assignment.py`) and `SectionRegistry`, both
  Pashupatastra-owned. A NovaForge claim naming sections or corridors must be ignored for this
  purpose, as for all scope (`backend/app/identity/policy.py:19-23`).
- **Approval stays a section act.** The flow "Authority clicks APPROVE → NovaForge approval →
  Pashupatastra commit" is `COMMIT_BLOCK` on one job. That is a section check plus lifecycle,
  including the stale-run pin (`backend/app/jobs/models.py:213-230`), and needs no corridor
  authority.
- **A NovaForge approval result cannot:**
  - widen scope to S3–S6;
  - confer corridor-complete authority;
  - bypass `StaleProposalError`, `CommittedJobError` or any lifecycle guard.

  Authorization runs before lifecycle, and lifecycle runs regardless of who authorized.
- Keying stays as today: `actor_id == Person.person_id` (`backend/app/identity/policy.py:84-88`).
  A future authenticated actor carries the same id unchanged.

---

## 19. API v1 compatibility

| Concern | Impact |
|---|---|
| New request fields | **none** |
| Response shapes | **none**. `OptimizationRunResponse` and `JobOptimizationResponse` are unchanged; §9 rejects filtering precisely to keep them unchanged |
| Status codes | **none new.** Both routes already return 403 `AUTHORIZATION_DENIED` (`backend/app/jobs/router.py:157`), and the vocabulary is frozen (`backend/tests/test_slice7_contract_freeze.py:833-843`) |
| OpenAPI snapshot | unchanged (`docs/openapi-v1.json`, asserted by `test_slice7_contract_freeze.py:816-830`) |
| Shipped runtime behavior | **unchanged**. No production code constructs `EnforcingPolicy`: a repo-wide grep of `*.py` outside `backend/tests/` and `backend/app/identity/` finds only the comment at `backend/app/jobs/service.py:1915`. Only tests that install enforcement see the new rule |
| **D-3(b), if pulled in** | would turn currently-200 reads of non-`jobs_optimize` runs into 404 `OPTIMIZATION_RUN_NOT_FOUND` on a frozen v1 route. The code and the shape are unchanged, but the **behavior changes**. It needs its own explicit approval and is **not** covered by the "no change" statements above |
| Behavior change under enforcement | a partial-scope ENGINEER goes from 200 to 403 on E-1 and E-2. Intended, and it is the whole point |
| A per-section run view | would be a **new route or v2** [OPEN/DEFER]. Never an in-place change to E-2 |

---

## 20. Database / data-model impact

**None.** No table, column, index, migration or trigger.

- **[OBS] Corridor:** `optimization_runs.corridor_id` (`backend/app/audit/repository.py:52-70`). It
  may be NULL, which is fail-closed today (`backend/app/identity/policy.py:256-262`).
- **[OBS] Topology:** `JobService.registry` (`backend/app/jobs/service.py:743-767`) and
  `SectionRegistry.section_ids()`. Both are in memory.
- **[OBS] Scope:** the `ScopeDirectory` configuration, with no schema
  (`backend/app/identity/policy.py:77-89`).
- **[OBS]** No run is linked to a section set in storage. D-1 does not need one, because it compares
  the actor with the **corridor**, not with the run's contents.
- **[DEFER] Future need, only if a per-section run view is ever approved:** a derived per-run section
  footprint (candidate *and* window sections), computed at write time. **Not** needed for 10.1D.3.
- **[DEFER] For D-3 option (b)** below: the `trigger_name` column already exists
  (`backend/app/audit/repository.py:55`), so no schema change is needed either.

---

## 21. Performance impact

- **Coverage check.** It costs O(|scopes| + |sections|) set work on in-memory data.
  - 6 sections: trivial.
  - Hundreds of sections: still microseconds. The corridor section set can be frozen once at policy
    construction.
- **No added I/O.** No database read, no JSON parsing and no candidate loading is added to either
  route.
- **Run reads** stay one primary-key lookup (`backend/app/audit/repository.py:129-140`). Rejecting
  model B avoids parsing a request or result per read, and those can be large on big corridors.
- **Optimize** is unchanged: the check still precedes the lock and the snapshot.
- **Repeated reads** have no per-run cost beyond today's.
- **Future directory.** A NovaForge-backed or remote `ScopeDirectory` should be request-cached, as
  already deferred in 10.1D.2 (R-5). Topology is static configuration and is not fetched per request.

---

## 22. Test strategy [PROP]

New file `backend/tests/test_slice10_1d_3_corridor_authority.py`. It uses the three-section helper
corridor (`backend/tests/slice10_1d_helpers.py:27-41`) and scratch databases through `tmp_path` only.

| ID | Property |
|---|---|
| T-01 | ENGINEER `{X-Y}`: `optimize_corridor` → `AuthorizationDenied`. **No events, no audit row** (event count and `list_all()` unchanged) |
| T-02 | ENGINEER `{X-Y,Y-Z,Z-W}` passes the seam |
| T-03 | Coverage by **union**: two ENGINEER assignments `{X-Y}` + `{Y-Z,Z-W}` → permitted |
| T-04 | **Same role only**: ENGINEER `{X-Y}` plus WORKER `{Y-Z,Z-W}` for the same person → denied |
| T-05 | Topology grows: add a fourth section → a previously complete scope is denied |
| T-06 | No topology for the corridor, or `topology` omitted → denied (fail closed) |
| T-07 | Duplicate corridor in `topology` → refused at `EnforcingPolicy` construction |
| T-08 | Run read: partial ENGINEER → 403; full ENGINEER → 200 with the **byte-identical** stored strings |
| T-09 | Run read: AUTHORITY or WORKER, even with full scope → 403 by role, **before** any database read |
| T-10 | Run in another corridor; run with NULL corridor → still denied (existing messages kept) |
| T-11 | The denial message names neither the uncovered sections nor any in-scope section |
| T-12 | HTTP: `POST /v1/corridors/{C}/optimize-jobs` and `GET /v1/optimization-runs/{id}` → 403 `{code: AUTHORIZATION_DENIED}` under an installed enforcing policy |
| T-13 | Section actions unchanged: a corridor-complete ENGINEER is decided by the section rule for `READ_JOB` (no corridor shortcut) |
| T-14 | `covers_corridor` unit tests: MATCH / NO_MATCH / UNRESOLVED (blank corridor, empty section set, foreign-corridor scopes ignored, exact-string equality, no wildcard) |
| T-15 | Regression: the full existing suite under `UnenforcedPolicy` passes unchanged; the OpenAPI snapshot and error-code vocabulary tests pass unchanged |

Existing tests whose **expectations intentionally invert**, and must be reviewed as a decision:

- `test_slice10_1d_scope_enforcement.py:231-238`
- `test_slice10_1d_scope_enforcement.py:240-250`
- `test_slice10_1d_scope_enforcement.py:275-289` (first half)
- `test_slice10_1d_scope_enforcement.py:522-532`
- `test_slice10_1d_scope_enforcement.py:591-611`

Each one uses an ENGINEER scoped only to `S_XY` and currently asserts that corridor actions are
permitted.

---

## 23. Production files expected to change [PROP]

| File | Change |
|---|---|
| `backend/app/identity/policy.py` | `EnforcingPolicy.__init__` gains a keyword-only `topology` (corridor → frozenset of sections; default empty; duplicate corridor refused). `_require_corridor` requires corridor-complete coverage under the acting role; its denial text lists no sections. Module docstring §"TWO SCOPE RULES" updated |
| `backend/app/identity/resource.py` | new pure `covers_corridor(...) -> ScopeMatch`, exported |
| `backend/app/identity/scope.py` | *optional:* a `CorridorSections` Protocol (`corridor_id`, `section_ids()`) that `SectionRegistry` already satisfies structurally |
| `backend/app/identity/role_actions.py` | docstring only: OQ-2 answered. **No table change** (D-2 default) |
| `backend/app/jobs/service.py` | *comment only:* `authorize_corridor` and `optimization_run` comments that describe "any scope in corridor" |

**Not changed:**

- `backend/app/identity/authorization.py`: the seam, `ResourceLocation`, the construction guard;
- `backend/app/jobs/optimization.py`, `jobs/router.py`, `jobs/models.py`, `jobs/lifecycle.py`;
- `optimizer/solver.py`, `audit/*`, `api/*`, `contracts/*`;
- `docs/openapi-v1.json`, `JobStatus`, `JobEventType`, the collection filtering.

Wiring an enforcing policy into `jobs/router.py` stays a **separate** decision, as it has been since
10.1D.

## 24. Test files expected to change or be added [PROP]

- **Add** `backend/tests/test_slice10_1d_3_corridor_authority.py` (§22).
- **Modify** `backend/tests/slice10_1d_helpers.py`: `enforcing()` / `fully_scoped()` pass the
  helper registry as `topology`, so fully scoped fixtures stay corridor-complete.
- **Modify** `backend/tests/test_slice10_1d_scope_enforcement.py`: the five inversions in §22.
- **Expected unchanged; verify:**
  - `test_slice10_1d_1_construction_guard.py`, because of the empty `topology` default;
  - `test_slice10_1d_2_collection_scope.py`, which covers section actions only;
  - `test_slice10_1d_security.py`, `test_slice10_1d_role_actions.py`,
    `test_slice9_accountability_api.py` and `test_slice7_contract_freeze.py`.

---

## 25. Explicit non-goals

- Authentication, NovaForge integration or front-channel, OAuth/PKCE, token issuance.
- SoD. Notifications. Frontend.
- Changing the optimizer, its inputs, or section-partitioned optimization.
- Filtering, redacting or re-serializing run records. Any new run route.
- Changing lifecycle, `JobStatus`, `JobEventType` or canonical event serialization.
- Changes to the single-resource seam or to 10.1D.2 collection filtering.
- Wiring `EnforcingPolicy` into the deployed router.
- Schema, migration or index changes.
- Remediating legacy `/optimize` or `/recover` inside 10.1D.3, unless D-3 explicitly pulls option (b) in.
- A new role.

---

## 26. Risks and deferred decisions

| ID | Item | Status |
|---|---|---|
| **D-1** | Corridor-complete rule for `REQUEST_OPTIMIZATION` and `READ_OPTIMIZATION_RUN` (answers OQ-2, `docs/SLICE10_1D_RESOURCE_AUTHORIZATION_ARCHITECTURE.md:904`). It **narrows** the rule the code ships today. That code shipped but was never enforced in a deployment (§1), so it narrows nothing anyone relies on. It also overrides the 10.1D doc's suggestion "permit for ENGINEER" on this evidence: the write impact is corridor-wide | **[OPEN] approval needed** |
| **D-2** | Grant AUTHORITY `READ_OPTIMIZATION_RUN` under D-1? | **[OPEN]**, default no |
| **D-3** | **Legacy `/optimize` writes unauthenticated audit rows with a caller-chosen `corridor_id` into the shared `optimization_runs` table, and v1 serves them** (G-4). Options: **(a)** pin the legacy `AuditService` to a separate database file; **(b)** have `optimization_run` serve only `trigger == "jobs_optimize"` rows (`backend/app/jobs/lifecycle.py:1031`) and return 404 otherwise, using the existing column, with no schema change; **(c)** put the legacy route behind actor + `REQUEST_OPTIMIZATION` (breaks the dashboard, which sends no actor headers); **(d)** retire the route. Recommended: **(b)** now, as a separately approved step, then **(d)** once the dashboard moves to v1. Rows already written cannot be deleted (append-only), which is another reason for (b) | **[OPEN] needs a named owner** |
| D-4 | Union of same-role assignments versus a single-assignment requirement | [OPEN], recommend union |
| R-1 | Adding a section revokes corridor authority until re-assigned. Operational friction | accepted: fail closed by design |
| R-2 | Corridor batch counts in job history (G-6) | accepted residual |
| R-3 | Cross-section ids in refusal reasons if intake ever sets `dependencies` or `mutual_exclusion_group` | [DEFER]: re-gate whenever intake changes |
| R-4′ | A per-section run view for partial-scope ENGINEERs | [DEFER]: new route or v2 |
| R-5 | Unauthenticated solver compute on `/optimize` and `/recover` (G-5) | [DEFER] 10.3 / retirement |
| R-6 | 404/403 distinction on the run read (G-7) | accepted: existence is already public (§10.3) |
| R-7 | A request-cached remote `ScopeDirectory` (carried over from 10.1D.2 R-5) | [DEFER] NovaForge |
| R-8 | `JobService.active_block_candidates` returns every job's candidate with no actor (`backend/app/jobs/service.py:2854-2871`). In-process only; no route | [DEFER]: add the seam before any route uses it |

---

## 27. Implementation sequence [PROP] (after D-1, D-2 and D-4 are approved)

1. Add `covers_corridor` to `backend/app/identity/resource.py`, with T-14 unit tests. Pure; nothing
   else changes.
2. Add the `topology` argument to `EnforcingPolicy`: validation, duplicate refusal, empty default,
   with T-06 and T-07.
3. Switch `_require_corridor` to corridor-complete coverage with a non-enumerating denial message
   (T-01 to T-05, T-11).
4. Update `slice10_1d_helpers.py` to pass topology. Apply the five §22 inversions, each with a
   comment citing D-1.
5. Service-level and HTTP tests T-08 to T-10, T-12 and T-13.
6. Docstring and comment updates (`policy.py`, `role_actions.py`, `service.py` comments).
7. Full suite with `PASHUPAT_JOBS_DB` pointed at a scratch file. Confirm the OpenAPI snapshot and the
   error-code vocabulary are unchanged (T-15).
8. *(Only if D-3(b) is approved as part of this slice)* Filter on trigger in `optimization_run`, with
   its own test and an explicit note that legacy rows become 404 on v1.

## 28. Acceptance criteria

1. Under `EnforcingPolicy`, an ENGINEER whose same-role scopes do not cover every registered section
   of *C* gets `AuthorizationDenied` for both:
   - `REQUEST_OPTIMIZATION`, **with no event, no audit row and no proposal change**;
   - `READ_OPTIMIZATION_RUN`.
2. An ENGINEER whose same-role scopes cover all of *C* is permitted both, and receives the run
   record's stored strings unchanged.
3. Missing topology, an empty section set, a NULL or blank corridor, or a foreign corridor is always
   denied. Nothing is inferred.
4. Denial text never lists a section.
5. Section-scoped actions, single-resource authorization and 10.1D.2 collection filtering behave
   exactly as before (existing suites green).
6. `UnenforcedPolicy` default behavior, `docs/openapi-v1.json`, error-code vocabulary, `JobStatus`,
   `JobEventType`, lifecycle and canonical event serialization are unchanged.
7. No schema, migration, index or optimizer change.
8. Scope and topology come only from server-side configuration. No request field, header or
   NovaForge claim affects coverage.
9. D-3 has a named owner and a chosen option recorded before 10.1D.3 is merged.

---

*Files inspected for this gate (read-only):*

- **identity:** `backend/app/identity/{authorization,policy,role_actions,resource,scope}.py`
- **jobs:**
  - `backend/app/jobs/{router,service,optimization,models,lifecycle}.py` (targeted ranges)
  - `backend/app/jobs/repository.py:30-42`
- **audit:** `backend/app/audit/{service,models,repository}.py`
- **api:** `backend/app/api/{main,deps}.py`, `backend/app/api/routers/{optimize,recover}.py`
- **optimizer and data:**
  - `backend/app/optimizer/solver.py` (targeted ranges)
  - `backend/app/data/section_registry.py` (API)
  - `backend/app/simulation/{simulator,disruptions}.py` (imports and definitions)
- **contracts:** `contracts/schemas.py` (targeted)
- **tests:**
  - `backend/tests/slice10_1d_helpers.py`
  - `backend/tests/test_slice10_1d_scope_enforcement.py` (targeted)
  - `backend/tests/test_slice7_contract_freeze.py` (targeted)
  - grep over `backend/tests/test_slice10_1d*.py` and `test_slice9_accountability_api.py`
- **dataset, frontend and earlier gates:**
  - `pashupatastra_realistic_dataset.json` (corridor block)
  - `frontend/` (grep, excluding `node_modules`)
  - `docs/SLICE10_1D2_COLLECTION_SCOPE_ARCHITECTURE.md` and
    `docs/SLICE10_1D_RESOURCE_AUTHORIZATION_ARCHITECTURE.md` (targeted)
