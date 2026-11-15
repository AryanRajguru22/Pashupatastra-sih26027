# Slice 10.1D.2 — Collection Scope Filtering: Architecture Gate

**Type:** Read-only architecture/design audit. No production code, tests, OpenAPI, schema,
migration, existing documentation or Git state was modified. This file is the only artifact.

**Baseline:** HEAD `995d8e06de6e7c4f019aec89b8e0322c3f5e747e` (Slice 10.1D.1),
`jobs.db` SHA-256 `245fd9ff9a9861af6ada9e6c53eb7ea2170493499b7b4386ef0634069e852386`.

**Labels used throughout**

| Tag | Meaning |
|---|---|
| **[OBS]** | Fact observed in the repository at the baseline, with `file:line` |
| **[PROP]** | Proposed design decision for the 10.1D.2 implementation |
| **[OPEN]** | Unresolved question; the proposal takes the narrow default |
| **[DEFER]** | Deliberately out of scope; named owner slice where one exists |

---

## 1. Executive verdict — **AMBER** (no blocker)

**AMBER, not RED:** nothing in the repository makes safe collection filtering impossible. The
single-resource seam from 10.1D/10.1D.1 is reusable without change. The repository keyset query
already has everything a bounded re-scan needs. No schema, index, migration, OpenAPI shape, status
code or request field has to change.

**AMBER, not GREEN:** collection filtering does not exist yet, and several existing code shapes would
make a *naive* implementation unsafe (§1.1). Today, **any** enforcing policy over-discloses on
`GET /v1/jobs` and `GET /v1/obligations`. They are role-gated only, and the code says so
[OBS `backend/app/jobs/service.py:1411-1419`, `:1714-1722`]. The risk is **latent**. The shipped
application constructs `UnenforcedPolicy` [OBS `service.py:597`, `router.py:116`], and a test pins
that [OBS `backend/tests/test_slice10_1d_security.py:470-475`]. So no live deployment exposes
anything it did not expose before 10.1D. Until 10.1D.2 lands, no deployment may install
`EnforcingPolicy` and rely on it for collections.

### 1.1 Existing code that would make a naive design UNSAFE

These are constraints the implementation must respect. They do not block it.

| # | Observation | Why a naive design is unsafe |
|---|---|---|
| U1 | The router, not the service, owns the jobs cursor. It asks for `limit + 1`, slices `rows[:limit]` and encodes `page[-1]` [OBS `router.py:345-363`]. | If scope filtering sits after the router's slice, authorized rows beyond the slice are dropped for good. If it sits before, the router's `len(rows) > limit` test and `page[-1]` no longer describe the scan, so rows can be skipped. **Cursor ownership has to move into the service.** |
| U2 | `obligations_page` reads history for **every** scanned row and derives an obligation for each [OBS `service.py:1755-1761`]. `derive_job_obligation` raises `ObligationIntegrityError` for an incoherent row, and the router turns that into **409 for the whole page** [OBS `router.py:184-188`; `backend/tests/test_slice9_safety.py:183`]. | If scope filtering comes after derivation, an out-of-scope incoherent job 409s an in-scope reader's page. The refusal leaks the other job's existence and a fact about its state, and it denies service. **Scope filtering must come before the history read and before derivation.** |
| U3 | `obligations_page` returns the full last scanned row (`page[-1]`) to the router, which encodes its `(created_at, job_id)` [OBS `service.py:1765`, `router.py:963-967`]. | Once filtering exists, that row may be out of scope. Its raw `job_id` would reach the client in a base64 cursor that anyone can decode, and a full out-of-scope row would reach the router layer. |
| U4 | The cursor is unsigned base64 JSON of `[created_at, job_id]`. Any pair of strings decodes [OBS `router.py:240-261`]. | A client can craft any start position. That is safe only if the cursor never carries authority and every row is filtered regardless of where the scan starts (§10). |
| U5 | A denial's message names the section and corridor [OBS `backend/app/identity/policy.py:239-243`]. | Per-row denials must be **swallowed and never aggregated** into any response, header or error. |
| U6 | `location_of` **raises** `ScopeError(CORRIDOR_CONFLICT)` on a corridor contradiction instead of returning an unresolved location [OBS `backend/app/identity/resource.py:141-146`]. `_authorize_job_resource` already turns that into `AuthorizationDenied` [OBS `service.py:2513-2516`]. | A predicate that calls `location_of` + `match_scope` directly would **crash the whole page** on such a row. The predicate must go through `_authorize_job_resource`. |
| U7 | `JobService.list_jobs` returns `repository.list_all()` with only the role gate [OBS `service.py:1422-1428`]. | It has no HTTP caller [OBS `router.py`, grep], but it is a public in-process read that hands out every row under an enforcing policy. |

**Blocker:** none. Every item above is handled by the design in §7–§10.

---

## 2. Current collection endpoint inventory

The inventory comes from every `@router.get/post` in `backend/app` [OBS grep:
`backend/app/api/routers/{health,optimize,recover}.py`, `backend/app/jobs/router.py`]. Only
`jobs/router.py` serves the v1 contract [OBS `backend/app/api/main.py:45-48`].

| Route | Shape | Returns multiple resource-scoped records? | Needs 10.1D.2 row filtering? |
|---|---|---|---|
| `GET /v1/jobs` [OBS `router.py:301-364`] | keyset page of **jobs** | **Yes**, jobs from any section | **Yes** |
| `GET /v1/obligations` [OBS `router.py:854-971`] | keyset page of obligations, **one per candidate job** | **Yes**, one per job, any section | **Yes** |
| `GET /v1/jobs/{id}` [OBS `router.py:367-396`] | one job | No | No. Already gated [OBS `service.py:1450-1473`] |
| `GET /v1/jobs/{id}/history` [OBS `router.py:399-424`] | list of events of **one** job | A sub-collection of one parent | No. The parent gates it [OBS `service.py:1479-1500`]; see §4.2 |
| `GET /v1/jobs/{id}/execution` [OBS `router.py:809-833`] | list of executions of **one** job | A sub-collection of one parent | No. The parent gates it [OBS `service.py:2330-2371`] |
| `GET /v1/jobs/{id}/proposal` [OBS `router.py:434-457`] | one proposal | No | No. Already gated [OBS `service.py:1506-1542`] |
| `GET /v1/jobs/{id}/obligation` [OBS `router.py:974-1005`] | one obligation | No | No. Already gated [OBS `service.py:1656-1692`] |
| `GET /v1/optimization-runs/{run_id}` [OBS `router.py:1008-1038`] | one run record | The payload embeds corridor-wide candidate data | No row filtering. Corridor-scoped; see §4.3 and [DEFER] |
| `POST /v1/corridors/{id}/optimize-jobs` [OBS `router.py:473-503`] | batch outcome for many jobs | Corridor-wide by design | No. A corridor action; see §4.3 and [DEFER] |
| `POST /optimize`, `POST /recover`, `GET /health` | legacy demo, not v1, no job rows | No | Out of scope |

In-process, non-HTTP: `JobService.list_jobs` [OBS `service.py:1422`] is called only from tests [OBS
`backend/tests/test_jobs_persistence_isolation.py:107,132-133`,
`test_job_lifecycle_accountability.py:252,1294`].

**Conclusion [OBS]:** exactly **two HTTP collection endpoints** need row filtering. They share **one
repository query** (`JobRepository.list_page`) and **one cursor** (`_encode_cursor`/`_decode_cursor`).
One in-process method (`list_jobs`) needs the same predicate.

---

## 3. Current authorization behavior

| Path | Role/action gate | Resource/scope gate | Evidence |
|---|---|---|---|
| `jobs_page` | `authorize(reader, READ_JOB)` before any read | **none** | [OBS `service.py:1445-1448`] |
| `obligations_page` | `authorize(reader, READ_JOB_OBLIGATIONS)` before any read | **none** | [OBS `service.py:1738-1739`] |
| `list_jobs` | `authorize(reader, READ_JOB)` | **none** | [OBS `service.py:1425-1428`] |
| every single-job read | `authorize` → `repository.get` → `_authorize_job_resource` | yes | [OBS `service.py:1463-1471`, `:1490-1498`, `:1527-1540`, `:1677-1685`, `:2353-2369`] |
| `optimization_run` | `authorize(READ_OPTIMIZATION_RUN)` → load → `authorize_corridor` | corridor only | [OBS `service.py:1787-1810`] |

Other seam facts the design relies on:

- `_authorize_resource` skips the resource question **only** for a policy with no
  `authorize_resource`. It raises `PolicyConfigurationError` if such a policy claims
  `enforcing=True` [OBS `service.py:2476-2488`]. The construction guard makes that branch
  unreachable [OBS `service.py:715-718`; `authorization.py:297-330`].
- `UnenforcedPolicy.authorize_resource` returns `None` for everything
  [OBS `authorization.py:278-284`].
- `EnforcingPolicy.authorize_resource` re-checks the role, then looks up scopes. It denies on no
  scope, requires `ScopeMatch.MATCH` for section-scoped actions, and denies `UNRESOLVED`
  [OBS `policy.py:167-210`, `:215-243`].
- The HTTP actor: no headers gives `UNIDENTIFIED`, a `SYSTEM` role gets 403, and every declared
  actor is `DECLARED_UNVERIFIED` [OBS `backend/app/api/deps.py:33-73`].
- `ADMIN`, `SYSTEM` and `UNIDENTIFIED` hold no action at all [OBS `role_actions.py:105-107`], so an
  enforcing policy refuses them the collection **with 403 at the role gate**, before any row is
  read.

---

## 4. Resource-location availability by endpoint

### 4.1 Jobs and obligation candidates (the rows that need filtering)

[OBS] A job row is a dict from `JobRepository._row_to_dict`. `block_candidate` is parsed from
`block_candidate_json` [OBS `repository.py:782-792`]. There is **no `corridor_id` column** and **no
`section_id` column** [OBS `repository.py:127-151`]. The section lives only inside the JSON blob.

[OBS] `location_of(job, corridor_id=self.corridor.corridor_id)` reads `block_candidate.section_id`
(or a single-valued `section_ids`). The deployment corridor is supplied because the row carries none
[OBS `resource.py:87-148`; `service.py:2514`].

| Case | `location_of` result | `EnforcingPolicy` decision | Collection outcome [PROP] |
|---|---|---|---|
| resolved `(corridor, section)` inside scope | `MATCH` | permit | **returned** |
| resolved, section outside scope | `NO_MATCH` | deny | **skipped** |
| legacy job, `block_candidate` has no `section_id` | `section_id=None` → `UNRESOLVED` | deny [OBS `policy.py:228-236`] | **skipped** |
| `block_candidate` not a mapping | `section_id=None` → `UNRESOLVED` [OBS `resource.py:90-94`] | deny | **skipped** |
| `section_ids` with ≠ 1 distinct value, or contradicting `section_id` | `None` → `UNRESOLVED` [OBS `resource.py:99-116`] | deny | **skipped** |
| blank / whitespace / non-string section | `None` → `UNRESOLVED` [OBS `resource.py:73-78`] | deny | **skipped** |
| row carries its own `corridor_id` that contradicts the deployment's | `ScopeError(CORRIDOR_CONFLICT)` → `AuthorizationDenied` [OBS `service.py:2513-2516`] | deny | **skipped** (never a crash, never a 500) |
| row carries a blank `corridor_id` | `corridor_id=None` → `UNRESOLVED` [OBS `resource.py:137-139`] | deny | **skipped** |
| malformed JSON in `block_candidate_json` | `json.loads` raises inside `_row_to_dict` [OBS `repository.py:788-790`] | not reached | **request fails closed (500), no data**. This is existing behavior for every read and is unchanged [DEFER] |

Obligations are derived **per candidate job**. An obligation has no location of its own: its location
is its job's location [OBS `service.py:1757-1761`]. Filtering the candidate job row is the complete
answer for obligations.

### 4.2 Sub-collections of one parent job (history, execution)

- [OBS] History is `self.history.list_for_job(job_id)`: events of **that job only**, returned after
  the parent job is resource-authorized [OBS `service.py:1498-1500`].
- [OBS] The one piece of event metadata that names **other** jobs is the Slice 8 duplicate assessment
  in `JOB_REPORTED` [OBS `service.py:1055-1067,1156-1157`]. It only considers rows in the **same
  section** [OBS `backend/app/jobs/duplicate_detection.py:231-234`]. The deployment has one corridor,
  so a same-section reference is always in the reader's scope if the parent is. **No cross-scope
  reference leaks.**
- [OBS] `ExecutionRecord` carries its own `section_id` [OBS `backend/app/jobs/execution.py:593`]. The
  seam deliberately uses the **job's** `block_candidate.section_id` as the only scope key
  [OBS `service.py:2498-2500`]. [PROP] 10.1D.2 keeps it that way and introduces no second source of
  truth.
- [OBS] Proposals are built from one job, one run and that job's events [OBS
  `backend/app/jobs/proposal.py:331-362`].

**Conclusion:** no per-row filtering is needed inside history or execution lists. Parent-gating is
complete.

### 4.3 Corridor-shaped resources

- `OptimizationRun` is located by `run.corridor_id` only, `section_id=None`
  [OBS `service.py:1806-1810`, `:2538-2542`]. The corridor rule applies
  [OBS `policy.py:245-271`; `role_actions.py:115-120`].
- [OBS] The run record returns `request_json`/`result_json` verbatim [OBS `models.py:740-742`]. Those
  hold **every candidate in the corridor, from every section**. A section-scoped ENGINEER can read
  corridor-wide candidate data through the run record. This follows OQ-2 (does holding one section
  permit corridor-wide actions?), which `role_actions.py:47-54` answered narrowly by making the read
  ENGINEER-only. It is **not a collection-pagination problem**, and filtering a stored audit record
  would falsify it. **[DEFER]** to the OQ-2 resolution.
- `JobOptimizationResponse` lists every job in the batch. It is the output of a corridor-authorized
  mutation [OBS `backend/app/jobs/optimization.py:413-437`]. **[DEFER]**, same reason.
- `BlockCandidate`/`BlockProposal`/`PossessionWindow`/`ScheduledBlock` are not returned as rows by any
  collection endpoint. They reach clients only inside a single job, proposal or run response.
  **No 10.1D.2 work.**

---

## 5. Pagination / cursor analysis (current state)

[OBS] Keyset order is `ORDER BY created_at DESC, job_id DESC` with the predicate
`(created_at < ? OR (created_at = ? AND job_id < ?))` [OBS `repository.py:469-486`]. It is strictly
monotone, and `job_id` is the primary key [OBS `repository.py:128`], so a position is unique and
paging never repeats a row.

[OBS] `created_at` is `event_timestamp(self.clock())`: UTC ISO-8601 with microseconds
[OBS `backend/app/jobs/events.py:82-90`; `service.py:1053`]. `job_id` is `JOB-` + 10 random hex
characters [OBS `service.py:1075-1077`].

[OBS] `/v1/jobs` uses the `limit + 1` idiom in the router. `next_cursor` is non-null iff a
`(limit+1)`-th row exists [OBS `router.py:344-363`]. Pinned by
`test_pagination_is_complete_stable_and_duplicate_free` (5 jobs, `limit=2` → exactly 3 pages, paged
equals unpaged) and `test_exact_page_boundary_has_no_trailing_cursor`
[OBS `backend/tests/test_slice7_contract_freeze.py:350-388`].

[OBS] `/v1/obligations` does the same internally. It is **already documented** in v1 as "a page may
hold fewer than `limit` items — even zero — while `next_cursor` is still set"
[OBS `docs/api-contract-v1.md:131-135`; `models.py:711-719`; `router.py:917-921`].

[OBS] The `/v1/jobs` contract says only "next_cursor is null on the last page"
[OBS `router.py:328-333`; `models.py:194-199`]. It neither promises nor forbids short pages.

### 5.1 Interaction analysis (the eleven questions)

| # | Concern | Finding |
|---|---|---|
| 1 | keyset ordering | Unchanged. Filtering removes rows from the ordered stream and never reorders it. |
| 2 | opaque cursors | Unchanged format. The service decides what the cursor **position** is (§9); the router only encodes it. |
| 3 | short pages | Occur under an enforcing policy when the scan budget runs out before `limit` in-scope rows are found. Never under `UnenforcedPolicy` (§14). |
| 4 | empty page + non-null cursor | Occurs only when a budget-bounded scan finds zero in-scope rows. Required: without it a sparse scope cannot make progress without an unbounded scan. |
| 5 | out-of-scope rows | Examined, never returned, never named in the cursor (§10). |
| 6 | UNRESOLVED rows | Treated exactly like NO_MATCH: skipped. |
| 7 | `next_cursor` | Last **returned** row when the page is full and a lookahead proved another in-scope row exists. Otherwise the last **examined** position when the budget ran out. Otherwise null (§9). |
| 8 | stable repeated requests | The same actor, scope and table give byte-identical responses: a pure function of `(after, limit, status, scope, table)`. |
| 9 | scope change between requests | Defined in §9.3. Each request uses the scope current at that request. |
| 10 | duplicates / skips | No duplicates (monotone keyset). No in-scope row is skipped (§9.2 proof). |
| 11 | malicious cursors | Choose a start position only. Grant nothing (§10). |

### 5.2 Is "over-fetch then filter" safe and sufficient?

**Safe: yes**, provided that (a) the predicate is the single-resource seam itself, (b) filtering
happens before derivation and history reads, and (c) the cursor is computed from the scan and not
from the returned list. **Sufficient: only if bounded.** A plain "fetch `k × limit` then filter"
still produces short pages and needs the same continuation rule. An unbounded "keep fetching until
full" is forbidden by the brief and by the Slice 9 scale assumption. **[PROP]** The answer is
**bounded chunked scanning with a fixed per-request budget** (§8).

---

## 6. Threat / information-leakage analysis

**Threat model [OBS]:** today every HTTP identity is **declared** (`DECLARED_UNVERIFIED`)
[OBS `deps.py:53-60`; `policy.py:10-17`]. A caller can already claim any `actor_id` and
therefore any scope. Until NovaForge establishes trusted identity, collection scope filtering is
**correctness + defence-in-depth, not a trust boundary**. The design must still be leak-minimal so
that the semantics stay unchanged when a trusted identity is introduced.

| Channel | Current (enforcing policy, no 10.1D.2) | After 10.1D.2 [PROP] | Residual |
|---|---|---|---|
| total counts / total pages | not present in any response [OBS `models.py:194-202,711-729`] | **must stay absent** | none |
| result count | counts all rows | counts in-scope rows only | none beyond the caller's own scope |
| existence of an out-of-scope resource | **full row disclosed** | never disclosed as a row | see "empty pages" |
| ordering | — | global order preserved; the in-scope subsequence reveals nothing about out-of-scope rows | none |
| `next_cursor` contents | `job_id` of any row | **never an out-of-scope `job_id`**; at a budget boundary may carry the `created_at` of an out-of-scope row | a timestamp, at most once per `B` rows scanned |
| cursor validity | syntactic only | **unchanged, syntactic only**: a well-formed cursor is never refused because of what lies behind it, so 400 vs 200 is no oracle | none |
| empty pages / continuation | n/a | an empty page with a non-null cursor means "≥ `B` rows scanned, none yours" | coarse density of the whole table, only past `B` rows |
| crafted-cursor probing | n/a | a crafted `(t, "")` start plus the boundary cursor returned shows the `created_at` of the row `B` positions later | an out-of-scope **timestamp** oracle, never ids or content; only reachable when > `B` out-of-scope rows follow `t` |
| timing | O(rows) | O(rows examined) ≤ O(`B` + tie group) | density signal; not constant-time |
| error messages | a 409 from an out-of-scope incoherent row (U2) | per-row `AuthorizationDenied` swallowed; derivation only over in-scope rows | 409 only from the caller's **own** rows |
| response metadata | `evaluated_at`, `policy_version` | unchanged; independent of scope | none |
| role-denied vs scoped-out | 403 | role-denied → **403** (says nothing about any resource); zero in-scope rows → **200 empty** | none |

**Minimum safe contract [PROP]:**

1. No total, count or page-number field is ever added.
2. `next_cursor` never contains an identifier of an out-of-scope resource.
3. Cursor acceptance never depends on scope.
4. No per-row denial text reaches any response.
5. No derived computation (history, obligation) runs on an out-of-scope row.
6. Short and empty pages are allowed and documented. Clients follow `next_cursor` until null, as
   `/v1/obligations` already requires.

**Accepted residual leakage** (timestamp at budget boundaries, density via empty pages and timing),
and why:

- It shows *that* activity happened in the corridor, never *what*, *where* or *which job*.
- Callers are railway-internal operational roles in the same corridor.
- Identity is declared today, so a stronger guarantee would be theatre.
- At Slice 9 scale (tens to low thousands of jobs) with `B = 1000`, most scopes finish inside one
  budget, so no out-of-scope position is ever emitted.

**Avoided:** ids, content, section names and counts. **[DEFER]** A sealed (AEAD or HMAC) cursor that
removes the residual oracle belongs with NovaForge-era production hardening. It needs key management
and invalidates outstanding cursors, so it is not minimal here.

---

## 7. Scope-filtering algorithm

### 7.1 Authorization order [PROP]

```
1. actor resolution         _resolve_actor (None → UNIDENTIFIED)          [existing]
2. collection action gate   authorization.authorize(actor, ACTION)        [existing; no I/O; 403]
3. bounded keyset scan      repository.list_page(...) in chunks           [existing query]
4. per row: resolve+decide  _authorize_job_resource(actor, ACTION, row)   [existing seam]
                              → permit: keep   |  AuthorizationDenied: skip
5. derive (obligations)     history.list_for_jobs(KEPT ids) → derive      [existing, now kept-only]
6. client narrowing         status (SQL) / state, type, role, past_due,
                            attention (router keep())                     [existing; narrow only]
7. response                 items + next_cursor from the scan position     [router encodes]
```

This is the brief's order, with two refinements the repository forces:

- **(3)+(4) interleave.** The scan decides each row as it reads it, because the budget and the cursor
  depend on how many rows were **examined**, not how many were fetched (§8).
- **(5) comes after (4).** U2 requires it: history and derivation must never touch an out-of-scope
  row.
- `status` for `/v1/jobs` is applied in SQL at (3). That is safe because it can only remove rows;
  every row it lets through still passes (4). The router's `keep()` for `/v1/obligations` runs at
  (6), **after** the service has fixed the cursor. **The service owns the cursor. Client filters
  never influence it and never expand anything.** [OBS `router.py:936-967`]

### 7.2 The per-row predicate [PROP]

A private sibling that both paths share:

```python
def _job_resource_permitted(self, actor, action, job) -> bool:
    try:
        self._authorize_job_resource(actor, action, job)
    except AuthorizationDenied:
        return False
    return True
```

Why this, and not a new policy method or a direct `match_scope` call:

- **Identical by construction.** A row appears in a collection **iff** `GET /v1/jobs/{id}` (or
  `/obligation`) would return 200 for the same actor. One code path decides both, so the collection
  and single-resource semantics cannot drift. This is the strongest property available and costs
  nothing.
- It goes through `location_of` → `ScopeError` → `AuthorizationDenied` [OBS `service.py:2513-2516`],
  so the corridor-conflict row (U6) is a skip, not a crash.
- It goes through `_authorize_resource` [OBS `service.py:2476-2488`], so `UnenforcedPolicy` and
  `RoleActionOnlyPolicy` keep exactly the behavior they have today, and the enforcing-without-method
  backstop stays active.
- **Only `AuthorizationDenied` is caught.** `PolicyConfigurationError`, `TypeError` and any other
  exception propagate and fail the request closed. The predicate never widens an unexpected error
  into "permit" or into a silent skip.
- No change to `AuthorizationPolicy`, `ResourceAwarePolicy`, `EnforcingPolicy`, `JobAction` or
  `role_actions`. The single-resource seam is not reopened.
- A new policy method such as `filter(...)` or `scopes_for(actor)` would change the abstract base
  (`ResourceAwarePolicy`, `authorization.py:188-211`). Every existing test policy would have to be
  rewritten, and scope would move toward the caller. Rejected.

Cost: one exception per skipped row, plus one `scopes_for_actor` lookup per examined row. The lookup
is in-memory for `StaticScopeDirectory` [OBS `policy.py:114-129`]. It is bounded by `B` per request
(§11).

---

## 8. Over-fetch / bounded-scanning strategy [PROP]

**Constants** (in `service.py`, next to the collection methods):

| Name | Value | Meaning |
|---|---|---|
| `COLLECTION_SCAN_BUDGET` (`B`) | **1000** rows | Maximum rows **examined** per request, before tie completion |
| `COLLECTION_SCAN_CHUNK` | **200** rows | Maximum size of each follow-up keyset query (= `MAX_PAGE_SIZE`) |
| `COLLECTION_TIE_CEILING` | **200** rows | Maximum extra rows examined to finish one `created_at` tie group |

`B` is set on the instance in `__init__` (`self.collection_scan_budget = COLLECTION_SCAN_BUDGET`),
the same way the suite already swaps policies on a live service. Tests can then exercise budget
exhaustion with a handful of rows. No constructor parameter is added. The effective budget is
`max(self.collection_scan_budget, limit + 1)`, so a full page can always be assembled when every row
is permitted.

**Algorithm** (`_scoped_scan`, private):

```text
inputs: actor, action, limit, status|statuses, after
budget = max(self.collection_scan_budget, limit + 1)   # guarantees the unenforced path is unchanged
kept = []; examined = 0; last = None; position = after
chunk = limit + 1                                   # first query == today's query
loop:
    rows = repository.list_page(limit=chunk, status|statuses, after=position)
    for row in rows:
        if examined >= budget and row.created_at != last.created_at:
            return kept, boundary(last)             # BUDGET STOP at a tie-group edge
        if examined >= budget + TIE_CEILING:
            raise CollectionScanLimitError          # fail closed, no data (§15)
        examined += 1
        if permitted(actor, action, row):
            if len(kept) == limit:
                return kept, pos(kept[-1])          # LOOKAHEAD proved another in-scope row
            kept.append(row)
        last = row
    if len(rows) < chunk:
        return kept, None                           # TABLE EXHAUSTED
    position = pos(rows[-1])                        # internal only; never emitted
    chunk = COLLECTION_SCAN_CHUNK + 1

pos(r)       = (r.created_at, r.job_id)
boundary(r)  = pos(r)              if r is in kept   (an authorized, returned row)
             = (r.created_at, "")  otherwise         (id-free; exact, see §10.2)
```

**Answers to the specific scaling cases:**

| Case | Behavior |
|---|---|
| dense scope / `UnenforcedPolicy` | One query, `limit + 1` rows. Identical to today (§14). |
| default `limit = 50`, sparse scope | Up to `B = 1000` rows examined in ≤ 1 + ⌈(1000−51)/200⌉ = **6** keyset queries. Returns what it found; continues via cursor. |
| `limit = 200`, sparse | Up to 1000 rows in ≤ **6** queries. |
| highly fragmented scope | Same as sparse. Fragmentation does not matter because each row is decided on its own. |
| **all rows out of scope** | Each request examines `B` rows and returns `items: []` with a boundary cursor, or null at the end of the table. A full walk takes ⌈N / B⌉ requests, each bounded. At N ≤ low thousands, that is ≤ a handful. |
| actor with no scope at all | Same as "all out of scope". 200 with empty pages; never 403, because the role gate passed. [DEFER] A "no scope" short-circuit needs a new policy query; see §23. |
| legacy `UNRESOLVED` rows | Skipped. They count toward `B`. |

**Never** "load everything and filter in Python" on an HTTP path. The only O(all rows) method is the
in-process `list_jobs`, which already is O(all rows). It gets the same predicate and remains
unreachable from any route (§18, T-21).

---

## 9. Empty-page / continuation semantics

### 9.1 Exact response contract [PROP]

| Outcome | `items` | `next_cursor` |
|---|---|---|
| `limit` in-scope rows found, **and** a further in-scope row found within the budget | `limit` rows | position of the **last returned** row |
| table (with the SQL `status` filter) exhausted | 0 … `limit` rows | **null** |
| budget reached (at a tie-group edge) before either of the above | 0 … `limit` rows (possibly short, possibly empty) | **last examined** position: `pos(last)` if returned, else `(created_at, "")` |

It follows that:

- `next_cursor = null` means **"no further row exists at all"**: the scan reached the end of the
  table, so a null cursor never hides an in-scope row. A **non-null** cursor means only that the scan
  stopped, either because the page is full and another in-scope row was proven, or because the
  budget ran out. It promises nothing about whether further in-scope rows exist. Clients must
  therefore never stop on a short or empty page. Only a null cursor ends a walk.
- An **exact boundary** (exactly `limit` in-scope rows remain) gives **null**, because the lookahead
  runs the table out. `test_exact_page_boundary_has_no_trailing_cursor` keeps holding under enforcing
  policies too, as long as the tail after those rows is shorter than the budget. If the tail is
  longer, the answer is a non-null cursor followed by an empty terminal page. That is the documented
  short/empty-page rule, not a violation.
- An **empty page with a non-null cursor** only ever means "the budget ran out". Clients follow
  `next_cursor` until null, exactly as `/v1/obligations` already documents
  [OBS `docs/api-contract-v1.md:131-135`].

### 9.2 Out-of-scope rows: skip and keep scanning

An out-of-scope or `UNRESOLVED` row is **skipped and scanning continues**. The alternatives fail:

- *Terminating the page* at an out-of-scope row turns every foreign row into a page break. That
  multiplies requests and leaks row positions for no benefit.
- *Raising an error* turns a collection into an existence oracle and denies service. It is exactly
  the behavior the 10.1D comment rules out [OBS `service.py:1411-1414`].

**No in-scope row is skipped** (proof sketch). The keyset stream is totally ordered. Every row
strictly between the incoming `after` and the emitted position was examined in this request.
Examined rows are either returned or out of scope at examination time. The lookahead row, which is in
scope and not returned, lies *after* the emitted `pos(kept[-1])`, so the next request examines it
first among in-scope rows. `(c, "")` is emitted only at a tie-group edge, so every row with
`created_at == c` was examined. Nothing unexamined is jumped over.

**No row repeats**: positions strictly decrease in keyset order. **No infinite loop**: every
non-terminal response advances the position by ≥ 1 row, and by ≥ `B` rows when the budget stops it.

### 9.3 Scope changes between requests

The cursor carries **no scope**. Each request evaluates rows against the directory's scope at that
request:

- **Narrowed** scope: later pages exclude rows no longer in scope. Rows already returned cannot be
  recalled; a client that must reflect revocation re-fetches from the first page.
- **Widened** scope: later pages include newly in-scope rows *after* the cursor. Rows *before* the
  cursor that were skipped are not revisited within that walk. To see a widened scope completely,
  start again from the first page. This is correct: those rows were not authorized when examined.
- No duplicates and no rows before the cursor either way, because the keyset guarantee is independent
  of scope.

[OPEN] OQ-D2-1: should a client be told its scope changed mid-walk? The proposal is no: there is no
signal and no API change. It is a NovaForge-era UX question.

---

## 10. Cursor safety

### 10.1 Invariants [PROP]

| Invariant | Mechanism |
|---|---|
| A cursor grants no authority | The cursor is only a start position. Every returned row passes `_job_resource_permitted` after it is read. Scope comes only from the server-side directory [OBS `policy.py:19-24`]. |
| Out-of-scope rows cannot be recovered by manipulation | Any crafted `(created_at, job_id)` just picks where to start. The rows after it are still filtered. `after` is bound as SQL parameters [OBS `repository.py:469-486`], so there is no injection. |
| No out-of-scope identifier ever emitted | `boundary(r)` emits `job_id` only for a returned row. Otherwise it emits `""`. |
| No scope-dependent validity | `_decode_cursor` stays purely syntactic [OBS `router.py:245-261`]. |
| Deterministic boundaries | A pure function of the inputs (§5.1 #8). |
| Format unchanged | Still `base64(json([created_at, job_id]))`. `""` is a legal string for the existing decoder, so no v1 cursor changes shape and every outstanding cursor stays valid. |

### 10.2 Why `(created_at, "")` is exact

With `after = (c, "")` the repository predicate becomes
`created_at < c OR (created_at = c AND job_id < "")`. No `job_id` sorts below `""`, so this reduces
to `created_at < c` [OBS `repository.py:470`]. That equals "strictly after the last examined row"
**iff every row with `created_at == c` has been examined**. This is why the budget stop happens only
at a tie-group edge (§8).

In production, ties need two jobs created in the same microsecond from the service clock
[OBS `events.py:90`]. Tie groups are therefore effectively of size 1. `COLLECTION_TIE_CEILING`
turns the theoretical case (an injected constant test clock, more than 200 rows at one instant) into
a fail-closed error rather than an unbounded scan or an id leak (§15, T-17).

### 10.3 Obligations cursor [PROP]

`obligations_page` returns a **position tuple** `(created_at, job_id)` or `None`, instead of a full
row. U3 is closed by construction: no out-of-scope row object ever leaves the service.

---

## 11. Performance analysis

- **SQL per request (jobs):** ≤ 1 + ⌈(`B` − `limit` − 1) / 200⌉ keyset queries (≤ 6), each
  `LIMIT ≤ 201` and parameter-bound. The tie extension almost never adds one. Today's count is 1.
  Under `UnenforcedPolicy` it **stays 1**.
- **SQL per request (obligations):** the same keyset queries, plus **one** batched
  `history.list_for_jobs(kept_ids)` [OBS `backend/app/jobs/history.py:184-230`]. History for
  out-of-scope rows is no longer read at all. The existing N+1 guard
  (`calls == {"per_job": 0, "batched": 1}`, `test_slice9_accountability_api.py:338-341`) keeps
  holding.
- **No N+1.** `block_candidate` is already parsed by `_row_to_dict`. `location_of` is pure. The
  directory lookup is in-memory. One caveat: a future remote directory must be request-cached (§16).
- **CPU:** at most ~`B + ceiling` JSON rows decoded and matched. Sub-millisecond each, so tens of ms
  worst case at `B = 1000`.
- **Scale fit:** the Slice 9 assumption is tens to low thousands of jobs
  [OBS `history.py:199-207`]. At N ≤ 1000, **one request can always reach the end of the table**, so
  budget stops, and with them every boundary-cursor leakage path, **never occur at pilot scale**.
- **Index:** not needed. The existing `ORDER BY created_at DESC, job_id DESC` scan is unchanged.
  Scope cannot be pushed into SQL without duplicating `location_of` in `json_extract`, which the
  brief forbids and which would split the one fail-closed resolver into two. **No schema or index
  change** (§20).

---

## 12. API v1 compatibility analysis

| Aspect | Change? | Evidence / reasoning |
|---|---|---|
| request fields | **No** | `status`, `limit`, `cursor`; obligation filters unchanged. No scope parameter; none may be added [OBS `policy.py:19-24`]. |
| response shapes | **No** | `JobListResponse{items,next_cursor}`, `ObligationListResponse{…}` unchanged [OBS `models.py:194-202,711-729`]. |
| status codes | **No** | Role denial is still 403 `AUTHORIZATION_DENIED` [OBS `router.py:157`]. Scoped-out rows are simply absent. The tie-ceiling error is an internal fail-closed error that production data cannot reach; it adds no documented code. |
| cursor format | **No** | Same encoding. Old cursors stay valid (§10.1). |
| cursor semantics under the shipped policy | **No** | Byte-identical behavior (§14). The Slice 7 pagination tests pass unchanged. |
| cursor semantics under an enforcing policy | **Narrative extension for `/v1/jobs` only** | Short or empty pages with a non-null cursor become possible. v1 never promised full pages, and `/v1/obligations` already documents this rule. **Not a shape change.** |
| `docs/openapi-v1.json` | **No change required** | The snapshot test compares only params, body ref, response codes and `required` [OBS `test_slice7_contract_freeze.py:789-830`]. Descriptions are **not** compared. |

**[PROP]** In the implementation slice, **do not edit** the `list_jobs` route docstring or
`JobListResponse`'s docstring. Their text flows into `app.openapi()` descriptions, and the committed
snapshot would then drift in prose (not in shape). Put the short/empty-page note for `/v1/jobs` in
`docs/api-contract-v1.md`, beside the existing obligations paragraph, **before any deployment wires an
enforcing policy**. [OPEN] OQ-D2-2: include that one-paragraph contract note in 10.1D.2 itself, or
in the slice that wires enforcement? Recommendation: include it in 10.1D.2, since it describes code
that ships then. It is a documentation change, not an OpenAPI change.

---

## 13. Role/action mapping

| Endpoint | JobAction | Scope rule | Evidence |
|---|---|---|---|
| `GET /v1/jobs` | `READ_JOB` | section (`JOB_READS ⊂ SECTION_SCOPED_ACTIONS`) | [OBS `service.py:1446`; `role_actions.py:68-76,127-139`] |
| `GET /v1/obligations` | `READ_JOB_OBLIGATIONS` | section | [OBS `service.py:1739`] |
| `JobService.list_jobs` | `READ_JOB` | section | [OBS `service.py:1426`] |
| `GET /v1/jobs/{id}/history` | `READ_JOB_HISTORY` | section (parent-gated) | [OBS `service.py:1491,1498`] |
| `GET /v1/jobs/{id}/execution` | `READ_JOB_EXECUTION` | section (parent-gated) | [OBS `service.py:2354,2367`] |
| `GET /v1/optimization-runs/{id}` | `READ_OPTIMIZATION_RUN` | corridor | [OBS `service.py:1788,1806`] |

**The inventory is sufficient. No new JobAction.** A collection read is the same permission as a
single read of each of its rows. `READ_JOB_LIST` or `READ_OBLIGATION_LIST` would be pure symmetry and
would let the two drift apart. The per-row predicate passes the **same** action as the collection
gate, so role/action (step 2) and resource/scope (step 4) stay two separate questions asked through
two separate methods [OBS `authorization.py:149-159`]. No SoD, and no actor-id comparison.

---

## 14. UnenforcedPolicy / RoleActionOnlyPolicy / enforcing behavior

| Policy | `authorize` | per-row predicate | Collection result |
|---|---|---|---|
| `UnenforcedPolicy` (shipped default) [OBS `authorization.py:260-284`] | permits | `authorize_resource` → `None` → **every row kept** | First chunk is `limit + 1`. All rows kept. The lookahead is the `(limit+1)`-th row, so the cursor is `pos(page[-1])`. **Identical rows, identical cursor, identical single SQL query as today.** Legacy `UNRESOLVED` rows are still listed, as today. |
| `RoleActionOnlyPolicy` subclasses (test instruments) [OBS `authorization.py:214-257`] | may deny (403) | `_authorize_resource` skips the resource question because the policy is not resource-aware and not enforcing [OBS `service.py:2478-2486`] → every row kept | Same as today: a role denial gives 403 before any read; otherwise unfiltered. |
| `EnforcingPolicy` / any `ResourceAwarePolicy` | role table | real scope decision | Filtered per §7–§9. |
| hypothetical enforcing policy with no `authorize_resource` | — | — | Cannot be installed [OBS `service.py:715-718`]. If forced in, the predicate lets `PolicyConfigurationError` **propagate** (it is not caught), so the request fails closed. It never silently permits. |

The default test/demo policy does **not** start enforcing scope, and no enforcing policy is weakened
to keep an old test passing. The invariant "an enforcing policy must not silently bypass resource
authorization" now also holds for collections.

---

## 15. Fail-closed cases

| Case | Outcome |
|---|---|
| role lacks the action (incl. ADMIN, UNIDENTIFIED under enforcing) | 403 before any repository read |
| SYSTEM declared over HTTP | 403 at `request_actor` [OBS `deps.py:65-67`] |
| actor absent from the directory / no scope under the role | every row skipped → 200 empty pages |
| row `NO_MATCH` | skipped |
| row `UNRESOLVED` (legacy no-section, blank, multi-section, non-mapping candidate) | skipped |
| row corridor contradicts the deployment (`CORRIDOR_CONFLICT`) | skipped (via `AuthorizationDenied`), never a crash |
| policy raises anything other than `AuthorizationDenied` | propagates; request fails; **no partial data** |
| enforcing policy lacking `authorize_resource` | refused at construction; `PolicyConfigurationError` at request time if forced |
| out-of-scope row whose obligation would be incoherent | never derived, so no 409 caused by someone else's job |
| in-scope row whose obligation is incoherent | 409 `OBLIGATION_STATE_INCONSISTENT`, as today |
| tie group larger than `COLLECTION_TIE_CEILING` at the budget edge | `CollectionScanLimitError`, request fails, no data. Unreachable with a real clock. |
| malformed stored `block_candidate_json` | existing `json.loads` failure; request fails; unchanged [DEFER] |
| client `status` / `state` / `role` / … filters | narrow only; never consulted for authorization |
| crafted cursor | start position only; rows still filtered |

---

## 16. NovaForge trust-boundary compatibility

The separation this gate preserves:

```
NovaForge            "who is this, and what trusted context was established?"
   │  (future: replaces backend.app.api.deps.request_actor's header parsing)
   ▼
Actor(actor_id == Person.person_id, role, assurance)        ← no scope inside
   │
Pashupatastra        authorize(actor, READ_*)                ← role/action
                     ScopeDirectory.scopes_for_actor(...)    ← railway scope, server-side
                     authorize_resource(actor, READ_*, loc)  ← per resource / per row
   │
Pashupatastra        lifecycle (not consulted by any read)
```

- **[OBS]** The collection design consumes only an `Actor`. Scope comes from the injected
  `ScopeDirectory`, keyed by `actor_id == Person.person_id` [OBS `policy.py:84-89`], which Slice 10.1A
  designed to carry a NovaForge-backed `ExternalIdentity` [OBS
  `backend/tests/slice10_1d_helpers.py:107-111`].
- **[PROP]** When NovaForge replaces `request_actor`, the Actor's assurance changes and nothing in
  §7–§10 does. The collection filtering semantics are **independent of how the Actor was
  established**.
- **[PROP]** Scope is never read from a token, claim, header or query parameter. A NovaForge claim
  cannot widen a collection. The railway-domain directory stays Pashupatastra's.
- **[OBS]** Collection reads are read-only. They never approve, commit or transition anything, so no
  NovaForge approval path passes through them. Approval stays a separate mutation that must still
  pass scope, stale-proposal, lifecycle and committed-state checks [OBS `service.py:1818-1888`].
  **Nothing in 10.1D.2 creates a second approval mechanism.**
- **[DEFER] Requirements on a future live directory** (NovaForge-era):
  1. `scopes_for_actor` must be cheap or request-cached. The predicate calls it once per examined
     row, up to `B` times.
  2. It must present a **request-consistent snapshot**, so one page is not decided under two
     different scopes.
  3. A directory failure must raise, not return `()`-as-success-by-accident. Raising propagates
     (§15); returning `()` would silently empty collections, which is also fail-closed but
     misleading.

---

## 17. Test matrix [PROP]

New file `backend/tests/test_slice10_1d_2_collection_scope.py`, built on
`backend/tests/slice10_1d_helpers.py` (three sections `X-Y`/`Y-Z`/`Z-W`, `enforcing(...)`,
`assignment(...)`, `report(...)`).

| ID | Test | Asserts |
|---|---|---|
| T-01 | worker scoped to `X-Y`; jobs in all three sections | `jobs_page` / `GET /v1/jobs` return only `X-Y` jobs, in keyset order |
| T-02 | multi-section scope `{X-Y, Z-W}` | exactly the union; `Y-Z` never appears |
| T-03 | no adjacent / corridor-wide inference | `{X-Y}` never yields `Y-Z` |
| T-04 | legacy job (no `section_id`) | absent under enforcing; **present** under `UnenforcedPolicy` |
| T-05 | multi-section / blank / non-mapping `block_candidate` rows | absent |
| T-06 | row with a contradicting `corridor_id` | absent; no exception; 200 |
| T-07 | actor with no scope / unknown actor | 200, `items == []`; the walk terminates with a null cursor |
| T-08 | ADMIN / UNIDENTIFIED under enforcing | 403 before any repository read (spy repository) |
| T-09 | full walk with `limit=1..3` over interleaved in/out rows | union of pages == in-scope rows; no duplicates; order == unpaged; ends null |
| T-10 | exact boundary under enforcing (tail < budget) | `next_cursor is None` |
| T-11 | budget exhaustion (`service.collection_scan_budget = 5`, 12 out-of-scope rows then 1 in-scope) | first page `items == []`, cursor non-null; following cursors eventually returns the in-scope row; each request examines ≤ budget (+ tie) rows (spy) |
| T-12 | **cursor never names an out-of-scope id** | decode every emitted cursor; its `job_id` ∈ returned ids ∪ {`""`}; never an out-of-scope id |
| T-13 | crafted cursor with an out-of-scope row's `(created_at, job_id)` | returns only in-scope rows after that position; never the named row |
| T-14 | cursor validity is scope-independent | the same syntactically valid cursor → 200 for scoped and unscoped actors alike |
| T-15 | scope narrowed between pages | a later page excludes now-out-of-scope rows; no error |
| T-16 | scope widened between pages | later rows are included; no duplicates |
| T-17 | constant injected clock, tie group > ceiling at the budget edge | `CollectionScanLimitError`, no data |
| T-18 | tie group ≤ ceiling at the budget edge | cursor is `(c, "")`; next page starts after the whole group; nothing skipped |
| T-19 | obligations: an out-of-scope **incoherent** job | an in-scope reader gets 200 (no 409); an in-scope incoherent job still 409s |
| T-20 | obligations: history batch holds only in-scope ids | spy on `list_for_jobs` args; still exactly one batched call |
| T-21 | obligations: router filters narrow only | `role=` / `state=` never surface an out-of-scope obligation; the cursor is the same with and without filters |
| T-22 | `list_jobs` in-process | filtered under enforcing; unchanged under `UnenforcedPolicy`; still not reachable from the router (source assertion) |
| T-23 | per-row `AuthorizationDenied` text never reaches a response | no section id of an out-of-scope row appears in any body or header |
| T-24 | a policy raising a non-`AuthorizationDenied` exception per row | the request fails; no partial page |
| T-25 | collection ⇔ detail consistency | for every row, `row ∈ jobs_page` ⇔ `job_detail` returns 200 (property-style over a fixture) |
| T-26 | `UnenforcedPolicy` equivalence | same items and cursors as a raw `list_page(limit+1)` walk; **exactly one** `list_page` call per request |
| T-27 | `RoleActionOnlyPolicy` | a role denial still gives 403; otherwise unfiltered |

**Unchanged and must still pass:** all of `test_slice7_contract_freeze.py` (pagination 7.3 and the
OpenAPI snapshot), `test_slice9_accountability_api.py`, `test_slice9_safety.py`, and every
`test_slice10_1*` file.

---

## 18. Exact production files expected to change [PROP]

| File | Change |
|---|---|
| `backend/app/jobs/service.py` | Add `COLLECTION_SCAN_BUDGET`, `COLLECTION_SCAN_CHUNK`, `COLLECTION_TIE_CEILING`; `self.collection_scan_budget` in `__init__`; a `CollectionScanLimitError` (RuntimeError); a small frozen `ScopedPage(items, next_after)` result; private `_job_resource_permitted` and `_scoped_scan`. `jobs_page` returns `ScopedPage` and owns `limit + 1`. `obligations_page` scans via `_scoped_scan`, reads history and derives for kept rows only, and returns a position tuple instead of a row. `list_jobs` filters through `_job_resource_permitted`. Update the two "10.1D.2 deferred" comments [OBS `:1411-1419`, `:1714-1722`]. |
| `backend/app/jobs/router.py` | `list_jobs`: call `service.jobs_page(limit=limit, …)` (no `+1`, no slicing) and encode `page.next_after`. `list_obligations`: encode the returned position. **Route docstrings unchanged** (§12). |

**No new production files.** The predicate and scan are private `JobService` methods beside the seam
they reuse. Unchanged: `backend/app/identity/*`, `repository.py`, `models.py`, `obligations.py`,
`history.py`, `optimization.py`, `backend/app/api/*`.

---

## 19. Exact test files expected to change / add [PROP]

| File | Change |
|---|---|
| **new** `backend/tests/test_slice10_1d_2_collection_scope.py` | T-01 … T-27 |
| `backend/tests/test_slice9_accountability_api.py` | Adapt to `obligations_page` returning a position tuple (≈ `:305`, `:354-361`, which currently read `next_row["created_at"]` and `next_row["job_id"]`). No assertion is weakened. |
| `backend/tests/test_slice10_1d_1_construction_guard.py` | `:256` asserts the truthiness of `jobs_page(...)`. Make it explicit (`.items`). It would still pass as-is, since a dataclass is truthy, but it should not rely on that. |
| `backend/tests/test_slice10_1d_security.py` | Optional: add `list_jobs` to the router-unreachable helper list (`:445-457`) as a source assertion. No new exemption is needed, because the new methods are private. |

`slice10_1d_helpers.py`: optional additive helpers (e.g. a `report` variant that places a job in each
section). No behavior change.

---

## 20. Schema / index changes

**None.** No table, column, migration or index. There is no `section_id` or `corridor_id` column, and
adding one would create a second source of truth next to `block_candidate_json` and a backfill problem
for the legacy row. The existing keyset scan is sufficient at the declared scale (§11). **[DEFER]** A
denormalized, indexed `section_id` belongs with measurements at a scale well beyond Slice 9's
assumption. The same precedent applies as the deferred `(track_id, created_at)` index
[OBS `history.py:197-207`].

## 21. OpenAPI changes

**None required.** Params, bodies, response codes and required fields are all unchanged, and
`_contract_shape` compares only those [OBS `test_slice7_contract_freeze.py:789-813`]. Do not edit the
route or model docstrings that feed descriptions (§12). A contract **prose** note goes in
`docs/api-contract-v1.md` ([OPEN] OQ-D2-2).

---

## 22. Explicit non-goals

Authentication; NovaForge integration, front-channel or approval transport; OAuth/PKCE; token
issuance; a second approval mechanism; SoD (10.3); new JobActions; changes to `EnforcingPolicy`,
`ResourceAwarePolicy`, `AuthorizationPolicy`, `role_actions`, `location_of` or `match_scope`; scope
inside `Actor`; canonical event serialization; `JobStatus`/`JobEventType`/transitions; optimizer
behavior; schema, index or migration; OpenAPI shape; sealed cursors; total counts; an admin read
surface; wiring `EnforcingPolicy` into the shipped router; frontend.

---

## 23. Risks / deferred items

| ID | Item | Disposition |
|---|---|---|
| R-1 | Residual `created_at` oracle at budget boundaries, including via crafted start positions | Accepted under the declared-identity threat model; unreachable at pilot scale (§11). **[DEFER]** Sealed cursor, NovaForge-era hardening |
| R-2 | Density and timing signal from empty pages and scan time | Accepted; not constant-time by design |
| R-3 | A no-scope actor scans `B` rows per request for nothing | Bounded. **[DEFER]** A short-circuit needs a policy query ("holds any scope?"), a seam change |
| R-4 | Run records and optimize-jobs responses expose corridor-wide candidate data to a section-scoped ENGINEER | **[DEFER]** OQ-2 (`role_actions.py:47-54`). Not a pagination problem |
| R-5 | A live or remote `ScopeDirectory` could be N+1 or inconsistent within a page | **[DEFER]** NovaForge integration: request-cached, snapshot-consistent (§16) |
| R-6 | `/v1/jobs` clients that stop at an empty page before a null cursor | Mitigated by documenting the obligations-style rule (OQ-D2-2) before any enforcing wiring; unreachable under the shipped policy |
| R-7 | Scope widened mid-walk does not revisit earlier rows | Defined behavior (§9.3); restart from page 1 |
| R-8 | Malformed stored `block_candidate_json` fails the whole read | Existing behavior, all reads. **[DEFER]** |
| R-9 | ADMIN has no read surface at all | Existing decision (`role_actions.py:26-30`). **[DEFER]** |
| R-10 | `list_jobs` stays O(all rows) | In-process only, unreachable from routes; unchanged complexity |

**Open questions:** OQ-D2-1 (signal a mid-walk scope change? proposal: no). OQ-D2-2 (land the
contract prose note in 10.1D.2? proposal: yes, in `docs/api-contract-v1.md`). OQ-D2-3 (is
`B = 1000` right? It is chosen as 5 × `MAX_PAGE_SIZE`, and at the declared scale one request reaches
the end of the table. Revisit only with measurements).

---

## 24. Implementation sequence [PROP]

1. **Tests first (red):** add T-01…T-27 against the current code. The filtering, cursor and
   obligations-409 tests fail; the `UnenforcedPolicy` equivalence tests pass.
2. `service.py`: constants, `ScopedPage`, `CollectionScanLimitError`, `_job_resource_permitted`.
3. `service.py`: `_scoped_scan` exactly as in §8.
4. `service.py`: `jobs_page` → `_scoped_scan`, returning `ScopedPage`.
5. `router.py`: `list_jobs` consumes `ScopedPage`; drop `+1` and the slicing.
6. `service.py`: `obligations_page` → `_scoped_scan` → history for kept rows → derive → position.
   `router.py`: encode the position.
7. `service.py`: `list_jobs` filtered; update the two deferral comments.
8. Adapt `test_slice9_accountability_api.py` and `test_slice10_1d_1_construction_guard.py` (§19).
9. Full suite on a **scratch database** (`PASHUPAT_JOBS_DB`); confirm `docs/openapi-v1.json` shape
   parity and the Slice 7 pagination tests pass unchanged.
10. (OQ-D2-2) Contract prose note in `docs/api-contract-v1.md`.

## 25. Acceptance criteria

1. Under any enforcing policy, no `/v1/jobs`, `/v1/obligations` or `list_jobs` result contains a row
   whose single-resource read would be refused for the same actor (T-25).
2. `UNRESOLVED`, `NO_MATCH` and corridor-conflict rows are never returned and never crash the page.
3. No emitted cursor contains an out-of-scope `job_id` (T-12). Cursor acceptance is scope-independent
   (T-14).
4. Every request examines at most `max(B, limit+1)` + `COLLECTION_TIE_CEILING` rows and issues a
   bounded number of keyset queries (T-11, T-17).
5. Full walks are complete, duplicate-free and ordered, ending with a null cursor (T-09).
6. Under `UnenforcedPolicy`, items, cursors and the SQL query count are identical to baseline, and all
   Slice 7 and Slice 9 tests pass unchanged except the §19 adaptations (T-26).
7. Obligations: no history read and no derivation for out-of-scope rows. An out-of-scope incoherent
   job cannot cause a 409 (T-19, T-20).
8. No change to the identity package, repository, models, schema, OpenAPI shape, status codes,
   JobAction inventory, lifecycle or event serialization.
9. `jobs.db` is untouched by the test run (scratch database only).

---

## Proposed implementation boundary (summary)

- **Production files to modify:** `backend/app/jobs/service.py`, `backend/app/jobs/router.py`.
- **New production files:** none.
- **Test files to modify:** `backend/tests/test_slice9_accountability_api.py`,
  `backend/tests/test_slice10_1d_1_construction_guard.py`; optional
  `backend/tests/test_slice10_1d_security.py`.
- **New tests:** `backend/tests/test_slice10_1d_2_collection_scope.py` (T-01…T-27).
- **Repository changes:** none (`JobRepository.list_page` already takes `limit`, `status`/`statuses`
  and `after`).
- **SQL / schema / index / migration:** none.
- **OpenAPI:** none. Optional contract prose in `docs/api-contract-v1.md` (OQ-D2-2).

---

## Appendix — files inspected

`backend/app/identity/{authorization,policy,role_actions,resource,scope,scope_assignment,person,assignments,actor}.py`;
`backend/app/jobs/{service,router,repository,obligations,optimization,history,models,proposal,execution,events,duplicate_detection}.py`;
`backend/app/api/{deps,main}.py`, `backend/app/api/routers/*`;
`backend/tests/{test_slice7_contract_freeze,test_slice9_accountability_api,test_slice9_safety,test_slice10_1d_security,test_slice10_1d_1_construction_guard,test_slice10_1d_scope_enforcement,slice10_1d_helpers,test_jobs_persistence_isolation,test_job_lifecycle_accountability}.py`;
`docs/api-contract-v1.md`, `docs/openapi-v1.json`.
Repository-wide greps: `list_jobs`, `jobs_page`, `obligations_page`, `get_execution`, `READ_JOB*`,
`READ_OPTIMIZATION_RUN`, `next_cursor`, `cursor`, `authorize_resource`, `location_of`, `match_scope`,
route decorators, cross-job references in event metadata.
