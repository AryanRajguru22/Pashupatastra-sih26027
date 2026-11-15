# Slice 10.1D — Resource-Aware Authorization: Architecture / Implementation Gate

**Status:** READ-ONLY architecture gate. No implementation.
**Pashupatastra HEAD:** `8d37f0c80d4a7b72692973113ee43f0edbbf1e71` (10.1C)
**origin/main:** `985ca045c2cc297f9f0dc0f33aaac2f20badb4c7`
**Local commits since origin/main:** 10.1A `1615f58`, 10.1B `3fc1aa8`, 10.1C `8d37f0c`
**Test baseline:** 1831 passed, 52 subtests passed
**jobs.db SHA-256:** `245fd9ff9a9861af6ada9e6c53eb7ea2170493499b7b4386ef0634069e852386`
**Date:** 2026-09-21
**Verdict:** **AMBER** — four trust-boundary decisions need approval before code (§27).

---

## 1. Executive summary

The seam needed to go from `authorize(actor, action)` to resource-aware authorization
already exists and is already called on every mutating path. The work is smaller than it
looks, but it is **not** GREEN, because four decisions are genuinely a human's to make and
one of them (SoD) is blocked on a slice that has not happened yet.

The five findings that decide the shape, each verified against code at this HEAD:

| # | Finding | Consequence |
|---|---------|-------------|
| 1 | **Every one of the 16 `authorize()` calls fires before the job row is loaded.** The job is fetched afterwards, usually inside `lifecycle_lock` (`service.py:1898, 1971, 2026, 2084`). | Scope cannot be evaluated at today's call position. Either the call moves after the load, or the resource is resolved before it. §18 recommends moving it. |
| 2 | **The denial contract forbids WRITES, not READS.** `test_denied_commit_changes_no_state_and_records_nothing` asserts `status` unchanged, `count_events == before`, `AuditRepository.list_all() == []` (`test_job_lifecycle_accountability.py:1292-1306`). No test asserts "no repository read". | Authorization may load the job to resolve its section. The existing contract tests keep passing. This is what makes §18 cheap. |
| 3 | **`repository.get()` hydrates `block_candidate` from `block_candidate_json`** (`repository.py:788-790`). | 10.1C's `location_of(job_row, corridor_id=...)` works directly on the row the service already holds. No new resolver, no second topology, no schema change. |
| 4 | **`GET /v1/jobs` and `GET /v1/jobs/{job_id}` bypass `JobService` entirely** — they call `service.repository.list_page()` / `.get()` with no `actor` parameter and no `authorize()` (`router.py:290-357`). | This is the only place current code proves an **action is missing**. Every other read already passes the seam. |
| 5 | **Actor ids are role-prefixed by convention** — `WORKER-042`, `AUTHORITY-017`, `ENGINEER-003` — throughout tests and demo. | SoD on `actor_id` is **unsound today**: one person acting in two roles is two different ids, so self-approval is undetectable. SoD is blocked on role-neutral `person_id` reaching Actor construction (10.3). |

The recommended decomposition (§23) is four sub-slices, of which only the first two are
unblocked. Nothing here requires a database change, an API change, an OpenAPI change or a
NovaForge change.

---

## 2. Current authorization inventory

### 2.1 The seam

One module, 132 lines: `backend/app/identity/authorization.py`. It defines `JobAction`
(15 members), `AuthorizationDenied`, the `AuthorizationPolicy` Protocol and
`UnenforcedPolicy`. There is no `backend/app/authorization/` package and no
`backend/app/optimization/` package; the brief's paths do not exist. Authorization lives in
`identity/`, optimization in `backend/app/jobs/optimization.py` and `backend/app/optimizer/`.

```python
class AuthorizationPolicy(Protocol):
    enforcing: bool
    def authorize(self, actor: Actor, action: JobAction) -> None: ...
```

`UnenforcedPolicy` (`enforcing = False`) is the only policy that ships. **Nothing is
enforced today**, and the module docstring says so explicitly.

### 2.2 Call sites — 16, verified at this HEAD

The previous audit's count of 16 is **confirmed**. (A naive grep returns 18; two hits are
docstring prose at `authorization.py:6` and `service.py:479`.)

| # | Location | Action | Resource available at call time? |
|---|----------|--------|----------------------------------|
| 1 | `optimization.py:413` | `REQUEST_OPTIMIZATION` | corridor only (no job) |
| 2 | `service.py:761` | `REPORT_JOB` | **no** — section resolves at step 3.5, `service.py:858` |
| 3 | `service.py:1308` | `READ_JOB_HISTORY` | job loaded at 1310, **after** |
| 4 | `service.py:1341` | `READ_BLOCK_PROPOSAL` | job loaded in `_build_current_proposal`, after |
| 5 | `service.py:1469` | `READ_JOB_OBLIGATIONS` | job loaded at 1471, after |
| 6 | `service.py:1518` | `READ_JOB_OBLIGATIONS` | **collection** — no `job_id` at all |
| 7 | `service.py:1567` | `READ_OPTIMIZATION_RUN` | run loaded after; corridor-shaped |
| 8 | `service.py:1608` | `COMMIT_BLOCK` | job loaded inside `_transition`, after |
| 9 | `service.py:1676` | `REJECT_PROPOSAL` | after |
| 10 | `service.py:1738` | `POSTPONE_PROPOSAL` | after |
| 11 | `service.py:1801` | `RELEASE_COMMITTED_BLOCK` | after |
| 12 | `service.py:1894` | `START_EXECUTION` | job loaded at 1898, after |
| 13 | `service.py:1966` | `COMPLETE_JOB` | job loaded at 1971, after |
| 14 | `service.py:2021` | `REPORT_EXECUTION_NOT_COMPLETED` | job loaded at 2026, after |
| 15 | `service.py:2083` | `READ_JOB_EXECUTION` | job loaded at 2084, after |
| 16 | `service.py:2203` | `ASSIGN_SCHEDULE` | job loaded at 2205, after |

**Every single job-shaped call authorizes before the job exists in memory.** That is the
one structural fact 10.1D has to deal with.

### 2.3 Public methods with NO authorization

Two of these are harmless; the rest are the real §20 attack surface.

| Method | Actor param | Verdict |
|--------|-------------|---------|
| `JobService.create_job` (741) | yes | **Safe** — delegates to `create_job_with_outcome`, which authorizes `REPORT_JOB` |
| `JobService.approve_proposal` (1626) | yes | **Safe** — delegates to `notify()`, which authorizes `COMMIT_BLOCK` (deliberate; see the docstring) |
| `JobService.list_jobs` (1288) | **no** | **Unauthorized** — `repository.list_all()`, every job, no seam |
| `JobService.verify_job_asset_reference` (1234) | no | Unauthorized read |
| `JobService.active_block_candidates` (2165) | no | Unauthorized read of all active jobs |
| `JobService.possession_inputs` (2233) | no | Unauthorized read |
| `JobService.possession_windows` (2407) | no | Unauthorized read |
| `JobService.classify_for_optimization` (2428) | no | Unauthorized read |
| `JobService.optimization_snapshot` (2449) | no | Unauthorized read |
| `JobService.record_rejected_transition` (2128) | yes | Internal writer, called only from `_transition` |

Most are optimizer-internal plumbing called by `JobOptimizationService` under an
already-authorized `REQUEST_OPTIMIZATION`. They are a bypass risk only if something outside
the optimizer calls them. `list_jobs` is the outlier: it is a general-purpose "give me every
job" method with no seam at all.

### 2.4 Test policies — 8 in 6 files, verified

| File | Line | Class | `enforcing` |
|------|------|-------|-------------|
| `test_job_lifecycle_accountability.py` | 1274 | `DenyPolicy` | True |
| `test_slice3_proposal_review.py` | 185 | `RecordingPolicy` | True |
| `test_slice3_proposal_review.py` | 891 | `RecordingPolicy` | True |
| `test_slice5_step3_execution_service.py` | 210 | `DenyPolicy` | True |
| `test_slice5_step4_evidence_gated_completion.py` | 675 | `DenyPolicy` | True |
| `test_slice6_authority_release.py` | 476 | `DenyPolicy` | True |
| `test_slice6_authority_release.py` | 487 | `RecordingPolicy` | False |
| `test_slice9_accountability_api.py` | 593 | `DenyPolicy` | True |

**All eight use the identical positional signature `def authorize(self, actor, action)`.**
None uses keywords. None inherits from anything. This exactly determines §17.

---

## 3. Current action inventory

`JobAction` has **15** members. Mapping the brief's §2 wish-list onto what actually exists:

| Brief's name | Real member | Note |
|--------------|-------------|------|
| `CREATE_JOB` | `REPORT_JOB` | same thing, different spelling |
| `APPROVE_PROPOSAL` | `COMMIT_BLOCK` | **deliberate**: `approve_proposal` delegates to `notify`; approval is the commit transition, not a second permission |
| `REJECT_PROPOSAL` | `REJECT_PROPOSAL` | ✓ |
| `POSTPONE_PROPOSAL` | `POSTPONE_PROPOSAL` | ✓ |
| `RELEASE_COMMITTED_BLOCK` | `RELEASE_COMMITTED_BLOCK` | ✓ |
| `START_EXECUTION` | `START_EXECUTION` | ✓ |
| `COMPLETE_JOB` | `COMPLETE_JOB` | ✓ |
| `REPORT_EXECUTION_NOT_COMPLETED` | `REPORT_EXECUTION_NOT_COMPLETED` | ✓ |
| `READ_OBLIGATIONS` | `READ_JOB_OBLIGATIONS` | one member, **two** call sites (single + list) |
| `READ_EXECUTION` | `READ_JOB_EXECUTION` | ✓ |
| `READ_PROPOSAL` | `READ_BLOCK_PROPOSAL` | ✓ |
| `READ_JOB` | **MISSING** | no action, and no seam — see below |
| — | `REQUEST_OPTIMIZATION` | corridor-scoped |
| — | `ASSIGN_SCHEDULE` | job-scoped, no HTTP route |
| — | `READ_JOB_HISTORY` | job-scoped |
| — | `READ_OPTIMIZATION_RUN` | corridor-shaped |

**One action is genuinely missing, and current code proves it:** `GET /v1/jobs` and
`GET /v1/jobs/{job_id}` read job data with no action and no seam. Every other read in the
application passes `authorize()`. This is the only addition this gate endorses. No other
new action is justified by current code.

### 3.1 Action classification

| Class | Actions |
|-------|---------|
| **Job-scoped** (one job, section from its block) | `COMMIT_BLOCK`, `REJECT_PROPOSAL`, `POSTPONE_PROPOSAL`, `RELEASE_COMMITTED_BLOCK`, `START_EXECUTION`, `COMPLETE_JOB`, `REPORT_EXECUTION_NOT_COMPLETED`, `ASSIGN_SCHEDULE`, `READ_JOB_HISTORY`, `READ_BLOCK_PROPOSAL`, `READ_JOB_EXECUTION`, `READ_JOB_OBLIGATIONS` (single), *(new)* `READ_JOB` |
| **Location-scoped, resolved late** | `REPORT_JOB` |
| **Corridor-scoped** | `REQUEST_OPTIMIZATION`, `READ_OPTIMIZATION_RUN` |
| **Collection-scoped** (filter, not gate) | `READ_JOB_OBLIGATIONS` (list), *(new)* `READ_JOB` (list) |
| **System-scoped** | none — `SYSTEM` is an actor role, not an action class |

Proposal- and execution-scoped actions are **not** a separate class: a proposal and an
execution both derive from one job's block, and the readiness audit already settled that the
job's `block_candidate.section_id` is the single scope key. Introducing proposal-level or
execution-level scope would create a second source of truth for the same section.

---

## 4. Resource abstraction

### 4.1 Is `ResourceLocation` sufficient?

**For scope, yes. For authorization as a whole, no — and that is correct.**

10.1C shipped `ResourceLocation(corridor_id, section_id)`, `ScopeMatch`, `location_of()` and
`match_scope()`. `location_of` already handles the job row shape, including the legacy row
whose `block_candidate` has no `section_id`. Because `repository.get()` hydrates
`block_candidate` into a dict (finding #3), the service can call
`location_of(job, corridor_id=self.corridor.corridor_id)` on the row it already holds.

`ResourceLocation` deliberately carries **no state**. The brief's §3 examples
(`APPROVE_PROPOSAL` needs lifecycle state, `COMPLETE_JOB` needs evidence state) are real
requirements, but they are **not authorization's job** — see §7. Lifecycle already enforces
them, transactionally, under the lock, and records refusals in history. Duplicating them in
authorization would create two answers to one question.

### 4.2 The choice

Of the brief's options A–E, the recommendation is **A + a thin identity wrapper**, not B, C
or D:

- **Not C (domain object).** Passing the job dict into the policy hands it the whole row and
  invites state checks inside authorization, which §7 forbids.
- **Not D (identity + state context).** State belongs to lifecycle. A policy that receives
  status will eventually branch on it.
- **Not B (a typed `Resource` hierarchy) yet.** The readiness audit §13.4 proposed
  `JobResource` / `LocationResource` / `CorridorResource`. That is the right *eventual*
  shape, but three types are not yet earned: the scope decision needs only
  `(corridor_id, section_id)` plus, for corridor-shaped actions, `corridor_id` alone.

**Recommended:** the policy receives `ResourceLocation` plus the *action* it is deciding, and
— when a job is involved — the `job_id` for audit messages only. Corridor-shaped actions
pass `ResourceLocation(corridor_id, section_id=None)` and are decided by a corridor-level
rule, never by `match_scope` (which would correctly return `UNRESOLVED` for them and deny).

If a later slice proves three types are needed, they can be introduced then; 10.1C's
`location_of` already normalizes every shape into one.

---

## 5. Role/action policy

**No role/action mapping exists anywhere in the codebase today.** The authorization module
docstring states the intended split in prose and explicitly says it is *deliberately not
encoded as a table*. `JobObligation.owed_role` (`obligations.py:258`) is the only place a
role is associated with an operation, and it is a *derived notification target*, not a
permission.

So the table has to be written from scratch. The minimum safe structure is a **frozen
mapping from `ActorRole` to `frozenset[JobAction]`** — a dict, not an engine. No rule
language, no conditions, no inheritance, no priority ordering.

Derived from the lifecycle's own docstrings (`authorization.py:17-20`, `lifecycle.py:1401`,
`lifecycle.py:1808`) and the Slice 5/6 comments:

| Role | Actions |
|------|---------|
| `WORKER` | `REPORT_JOB`, `START_EXECUTION`, `COMPLETE_JOB`, `REPORT_EXECUTION_NOT_COMPLETED`, + job-scoped reads |
| `ENGINEER` | `REQUEST_OPTIMIZATION`, `READ_OPTIMIZATION_RUN`, + job-scoped reads |
| `AUTHORITY` | `COMMIT_BLOCK`, `REJECT_PROPOSAL`, `POSTPONE_PROPOSAL`, `RELEASE_COMMITTED_BLOCK`, + job-scoped reads |
| `ADMIN` | **nothing operational.** Reads are a later policy decision (§13 of the readiness audit) |
| `SYSTEM` | not a human role; system actions bypass no seam but hold `SYSTEM_INTERNAL` assurance |
| `UNIDENTIFIED` | **nothing** |

**`ASSIGN_SCHEDULE` is assigned to no role.** It has no HTTP route and exists for direct
placement; leaving it unmapped means an enforcing policy denies it by default, which is the
fail-closed answer. If a deployment needs it, that is an explicit later grant.

Open question OQ-1 (§26): whether `ENGINEER` may `REPORT_JOB`, and whether `AUTHORITY` may.
Nothing in current code answers this.

---

## 6. Scope enforcement

Scope is evaluated **only after** the role/action check passes, and only for job- and
location-scoped actions.

```
role/action permitted?  --no-->  deny (no scope lookup, no repository read)
        |
       yes
        v
resolve ResourceLocation from the job row (location_of)
        v
scopes_for(person, role, assignments)   -> tuple[RailwayScope, ...]
        v
any(match_scope(scope, location) is MATCH)  -> permit
otherwise                                   -> deny
```

Three rules, all of them already enforced by 10.1C's types:

1. **`UNRESOLVED` is never a permit.** `match_scope` returns a three-valued enum, never a
   bool, specifically so `if match_scope(...)` cannot be written. A resource whose section
   is absent (the live legacy row), blank, non-string, or which spans several sections, is
   `UNRESOLVED` and therefore denied for every scoped role.
2. **No scope is never a permit.** `match_scope(None, ...)` returns `NO_MATCH`. An ADMIN, or
   a person with no `RoleScopeAssignment` for the role they selected, matches nothing.
3. **Corridor and section are matched as a pair.** A section string identical to one in
   scope but in another corridor is `NO_MATCH`, not a match.

**Corridor-shaped actions** (`REQUEST_OPTIMIZATION`, `READ_OPTIMIZATION_RUN`) must **not** go
through `match_scope`: they have no section, so it would return `UNRESOLVED` and deny
everything. They need a corridor-level rule — "the person holds any scope in this corridor".
Whether holding one section in a corridor should permit a corridor-wide optimization run is
**OQ-2** (§26) and is a genuine policy question, not an implementation detail.

---

## 7. State / lifecycle separation

**Authorization must not check state, and the order is authorization first.**

The two questions are distinct and already have distinct homes:

| | Question | Where | On refusal |
|---|---|---|---|
| **Authorization** | May this actor *attempt* this action on this resource? | `authorization.py`, before the mutation | raises `AuthorizationDenied`, **writes nothing** |
| **Lifecycle** | Is this transition valid from the current state? | `lifecycle.py` / `mutate_jobs`, inside `lifecycle_lock` | raises `InvalidTransitionError` etc., **writes `TRANSITION_REJECTED`** |

The brief's example resolves cleanly: an AUTHORITY with the right section **is authorized**
to attempt approval of a COMPLETED job, and the lifecycle then refuses it as an invalid
transition, recording the attempt. That is the correct behaviour — the person was permitted
to try, and the domain said no. Moving the state check into authorization would turn a
recorded domain refusal into an unrecorded permission error and lose the audit trail.

**Order: authorization → lifecycle.** Justification, from code rather than taste:

- Authorization denial must leave no trace of an unauthorized actor's attempt; lifecycle
  refusal must leave one. Running lifecycle first would write a `TRANSITION_REJECTED` event
  attributed to an actor who was never permitted to act — an audit record of an action that
  should never have been evaluated.
- `_transition` holds `lifecycle_lock` (`service.py:2113`). Authorizing inside the lock would
  hold it across policy evaluation, including any future directory lookup.

### 7.1 The asymmetry, and what it costs

A scope-denied attempt currently leaves **no record anywhere**. `AuthorizationDenied` is
raised and nothing is written. For an RBAC system this is a real observability gap: you
cannot answer "who tried to approve work outside their section?"

Adding an authorization-denial event would **break three existing contract tests** that
assert `count_events(db_path) == before` after a denial
(`test_job_lifecycle_accountability.py:1289, 1306, 1322`). This is **Decision 3** (§27).
The options are: accept the gap for now; log without persisting; or add a denial event and
amend those three tests. This gate does not choose.

---

## 8. Segregation of duties

### 8.1 SoD is blocked, and this is the strongest argument against GREEN

SoD needs to answer "did the same **person** create this job and later approve it?" The
substrate exists: `job_events` is append-only, holds `actor.actor_id` on every event, and
refuses UPDATE and DELETE at the SQL layer.

**But `actor_id` is not a person identifier today.** Every actor id in the codebase is
role-prefixed by convention — `WORKER-042`, `AUTHORITY-017`, `ENGINEER-003`
(`test_job_lifecycle_accountability.py:74-76`, `slice8_helpers.py:30-31`, and the
`X-Actor-Id` header examples in `deps.py:38`). One person acting as WORKER and later as
AUTHORITY presents **two different `actor_id`s**. An SoD rule comparing `actor_id` would
therefore **never fire** — it would silently approve every self-approval while appearing to
work. That is worse than no SoD at all.

SoD becomes sound only once the role-neutral `person_id` from 10.1A is what reaches
`Actor.actor_id` — which is 10.3 (authenticated actor). **SoD must not be implemented in
10.1D.**

### 8.2 The design, recorded for when it is unblocked

- **Minimum identity key:** role-neutral `person_id`, compared exactly. Not
  `(person_id, role)` — the whole point is to catch one person switching roles.
- **Events that matter:** `JOB_CREATED` (who reported) versus `BLOCK_COMMITTED` /
  `PROPOSAL_REJECTED` / `PROPOSAL_POSTPONED` / `BLOCK_RELEASED` (who decided). Execution
  events are a separate question — whether the worker who executed may also report
  not-completed is arguably not an SoD conflict at all.
- **Inside or beside `authorize()`?** **Beside.** SoD needs the job's event history, which is
  a repository read the policy must not own. It belongs as a separate evaluation that the
  service performs after the scope check and before the lifecycle call, so the policy object
  stays a pure function of (actor, action, location) and never gains a database dependency.
- **Order:** after authorization, before lifecycle. An SoD violation is a refusal to permit,
  not an invalid transition.

No SoD seam is required in 10.1D. Adding one now would be speculative.

---

## 9. Person / role / scope resolution

The eventual chain, and where each link exists today:

```
NovaForge assertion   -> ExternalIdentity   [10.1A, exists]
ExternalIdentity      -> Person             [10.1A, exists]
Person                -> RoleAssignment     [10.1B, exists; select_role picks one]
(Person, role)        -> RailwayScope(s)    [10.1C, exists; scopes_for]
Person                -> Actor              [10.3, DOES NOT EXIST]
```

**Scope must not go into `Actor`.** `Actor.to_dict()` is the canonical event serialization
(`events.py:316`), `canonical_bytes()` hashes it (`events.py:329-331`), and
`JobEvent.from_dict` rebuilds an Actor from exactly three fields (`events.py:345-348`).
Adding a field would change the canonical bytes of every event and break round-tripping,
which `test_stored_events_round_trip_to_identical_canonical_bytes` pins. The Actor stays
three fields.

**Therefore: scope travels beside the Actor, resolved from a directory at decision time.**
The authorization context is `Actor` + a directory lookup keyed by
`(actor.actor_id, actor.role)`. Because `(person, role)` is unique and `person_id` is
role-neutral and stable, that returns exactly one assignment.

The directory itself does not exist yet. For 10.1D it should be an **injected read-only
Protocol** with an in-memory implementation, exactly as `SectionDirectory` was injected in
10.1C — not a database table (§21).

---

## 10. Multi-role security

The escalation the brief describes is **already half-closed**, and the remaining half is a
10.3 problem, not a 10.1D one.

What `deps.py:request_actor` does today:

- Both headers absent → `unidentified_actor()`, assurance `NONE`.
- `X-Actor-Role: SYSTEM` → **403**, explicitly (`deps.py:86-91`). A caller can never act as
  the system.
- `X-Actor-Role: UNIDENTIFIED` → 400.
- Anything else → `human_actor(...)` with assurance `DECLARED_UNVERIFIED`.

So role spoofing is *recorded honestly* but *not prevented*: a client may send
`X-Actor-Role: ADMIN` and be recorded as a DECLARED_UNVERIFIED ADMIN. Nothing is enforced,
so nothing is currently gained by it — but the moment an enforcing policy ships, that header
becomes a privilege grant.

**The trusted role-selection boundary must be:**

1. The client may **name** a role; it may never **manufacture** one.
2. The named role is a **selector among assignments the person already holds** — exactly the
   contract `assignments.select_role()` already implements in 10.1B: unknown role →
   `INVALID_ROLE`, not held → `ROLE_NOT_ASSIGNED`, held → that assignment.
3. **Ambiguity is refused, never resolved.** `select_role` raises `ROLE_SELECTION_REQUIRED`
   when a person holds several roles and names none. It never picks "first" or "highest
   privilege". This is precisely the defence the brief asks for, and it already exists.
4. The role a request acts under is therefore always `directory ∩ declared`, never
   `declared`.

**This cannot be completed in 10.1D**, because step 2 requires the person behind the request
to be known, which requires authentication (10.3). What 10.1D *can* do is ensure the
enforcing policy reads the role from a resolved assignment rather than from `Actor.role`
directly — so that when 10.3 lands, the header stops being load-bearing. Until then, an
enforcing policy in demo mode is trusting a header, which is why §15 insists demo and
production are different policy objects.

---

## 11. Resource-location matrix

How each domain object resolves to `ResourceLocation`, verified against 10.1C's `location_of`:

| Object | corridor_id | section_id | Result |
|--------|-------------|------------|--------|
| **Job row** (`repository.get`) | caller-supplied (`self.corridor.corridor_id`) | `block_candidate["section_id"]`, hydrated at `repository.py:788` | resolved for jobs created via `create_job` |
| **Legacy job row** | caller-supplied | key **absent** — the live `jobs.db` holds one | `UNRESOLVED` → denied |
| **BlockCandidate** | none — caller supplies | `section_id`, `Optional` | resolved or `UNRESOLVED` |
| **BlockProposal** | **own field** (`proposal.py:91`) | `section_id`, `Optional` | own corridor; conflicting caller corridor raises `CORRIDOR_CONFLICT` |
| **PossessionWindow** | none | `section_id`, `Optional` (`schemas.py:110`) | caller must supply corridor |
| **ScheduledBlock** | none | `section_id`, `Optional` (`schemas.py:213`) | caller must supply corridor |
| **Execution** (`ExecutionRecord`) | none | `Optional` | derived from the same block — never a second source |
| **Asset** | none | **no field at all** (`models.py:60-76`) | always `UNRESOLVED`; track/km never become a section |
| **Optimization run** | `corridor_id`, `Optional` (`audit/models.py:52`) | none | corridor-shaped only |
| **JobObligation** | none | none (`obligations.py:255-263`) | carries `owed_role` only; scope must come from the job |

**Multi-section resources** are `UNRESOLVED` by design in 10.1C, even when every one of their
sections is inside the scope. Authorization inherits that: fail closed, never "any" or
"all".

**Cross-corridor conflict**: `location_of` raises `ScopeError(CORRIDOR_CONFLICT)` when the
resource carries one corridor and the caller supplies another. Authorization must treat a
raised `CORRIDOR_CONFLICT` as a **denial**, not as a 500.

---

## 12. Action × resource matrix

| Action | Role(s) | Resource | Scope required | State handled where | SoD |
|--------|---------|----------|----------------|---------------------|-----|
| `REPORT_JOB` | WORKER *(OQ-1)* | location, resolved at step 3.5 | yes — **but after resolution** | n/a (creation) | later: reporter identity is the SoD anchor |
| `REQUEST_OPTIMIZATION` | ENGINEER | corridor | corridor-level *(OQ-2)* | optimizer | no |
| `COMMIT_BLOCK` (approve) | AUTHORITY | job | yes | lifecycle (`notify`) | **yes, later** — reporter ≠ approver |
| `REJECT_PROPOSAL` | AUTHORITY | job | yes | lifecycle | later |
| `POSTPONE_PROPOSAL` | AUTHORITY | job | yes | lifecycle | later |
| `RELEASE_COMMITTED_BLOCK` | AUTHORITY | job | yes | lifecycle | later |
| `START_EXECUTION` | WORKER | job | yes | lifecycle | no |
| `COMPLETE_JOB` | WORKER | job | yes | lifecycle + evidence gate | later *(OQ-3)* |
| `REPORT_EXECUTION_NOT_COMPLETED` | WORKER | job | yes | lifecycle | no |
| `ASSIGN_SCHEDULE` | **none** | job | yes | lifecycle | no |
| `READ_JOB_HISTORY` | any human | job | yes | n/a | no |
| `READ_BLOCK_PROPOSAL` | any human | job | yes | n/a | no |
| `READ_JOB_EXECUTION` | any human | job | yes | n/a | no |
| `READ_JOB_OBLIGATIONS` (single) | any human | job | yes | n/a | no |
| `READ_JOB_OBLIGATIONS` (list) | any human | **collection** | **filter, not gate** | n/a | no |
| `READ_OPTIMIZATION_RUN` | ENGINEER, AUTHORITY | corridor | corridor-level *(OQ-2)* | n/a | no |
| *(new)* `READ_JOB` (single) | any human | job | yes | n/a | no |
| *(new)* `READ_JOB` (list) | any human | **collection** | **filter, not gate** | n/a | no |

- **No railway resource needed:** `REQUEST_OPTIMIZATION`, `READ_OPTIMIZATION_RUN` (corridor
  only).
- **Needs a job:** everything job-scoped above.
- **Needs a proposal / execution:** none independently — both derive from the job's block.
- **Needs section/corridor context only:** `REPORT_JOB`, after step 3.5.

---

## 13. Read authorization

Slice 9 and earlier shipped five read paths. They fall into **three** groups, and the
distinction between *authorize* and *filter* is what matters:

| Endpoint | Seam today | Shape | Recommendation |
|----------|-----------|-------|----------------|
| `GET /v1/jobs/{id}/history` | `READ_JOB_HISTORY` | single job | **resource-authorize** — gate on the job's section |
| `GET /v1/jobs/{id}/proposal` | `READ_BLOCK_PROPOSAL` | single job | resource-authorize |
| `GET /v1/jobs/{id}/execution` | `READ_JOB_EXECUTION` | single job | resource-authorize |
| `GET /v1/jobs/{id}/obligation` | `READ_JOB_OBLIGATIONS` | single job | resource-authorize |
| `GET /v1/optimization-runs/{run_id}` | `READ_OPTIMIZATION_RUN` | corridor | corridor-authorize *(OQ-2)* |
| `GET /v1/obligations` | `READ_JOB_OBLIGATIONS` | **collection** | **scope-filter**, not gate |
| `GET /v1/jobs` | **none** | **collection** | needs a seam **and** a filter |
| `GET /v1/jobs/{id}` | **none** | single job | needs a seam |

**Authorization and filtering are different operations and must not be conflated.**

- A **gate** on a single resource answers yes/no and returns 403 on no.
- A **filter** on a collection silently omits out-of-scope rows and returns 200. Returning
  403 for a list because it *contains* an out-of-scope job would be wrong; so would returning
  rows the caller may not see.

**Filtering must be in Python, not SQL.** `maintenance_jobs` has **no section column**
(`repository.py:127-153`); the section lives inside `block_candidate_json`. Query-level
filtering is therefore impossible without a schema change, which §21 rejects. Post-filtering
in Python over the page is the only option without a migration.

**This creates a real pagination problem, and it is Decision 2 (§27).** Both list endpoints
use keyset pagination with "fetch limit+1 to detect another page"
(`router.py:322`, `service.py:1528`). If rows are filtered *after* the page is fetched, a
page of 50 may return 3 items while still reporting `next_cursor`, and a caller cannot tell
a short page from the last page. The frozen v1 response shape (`{items, next_cursor}`)
cannot express "filtered". Options: filter and accept short pages; over-fetch and loop until
the page fills; or defer list filtering entirely and scope-gate only the single-resource
reads. This gate does not choose.

---

## 14. Fail-closed matrix

Every ambiguity denies. `UNRESOLVED` is never authorized.

| Condition | Result | Mechanism |
|-----------|--------|-----------|
| No actor supplied | **deny** | `_resolve_actor` → `unidentified_actor()`; `UNIDENTIFIED` maps to no actions |
| Unauthenticated actor (`DECLARED_UNVERIFIED`) | **deny in production; permit in demo** | different policy object, never a flag — see §15 |
| `UNIDENTIFIED` role | **deny** | empty action set |
| `SYSTEM` role from a client | **deny (403)** | already enforced at `deps.py:86-91` |
| Invalid role string | **deny (400)** | `human_actor` raises `InvalidActorError` |
| Role not assigned to the person | **deny** | `select_role` → `ROLE_NOT_ASSIGNED` (10.1B) |
| Role ambiguous, none requested | **deny** | `select_role` → `ROLE_SELECTION_REQUIRED` |
| Action not permitted for role | **deny** | role/action table, before any repository read |
| No scope for (person, role) | **deny** | `scopes_for` → `()`; `match_scope(None, ...)` → `NO_MATCH` |
| ADMIN on an operational action | **deny** | ADMIN has no actions and no scope |
| `NO_MATCH` | **deny** | wrong corridor, or section outside the set |
| `UNRESOLVED` | **deny** | missing/blank/non-string section, or multi-section resource |
| Missing resource (job not found) | **deny, then 404** | authorize first; a scoped caller must not learn a job exists outside their scope by the error code *(OQ-4)* |
| Malformed resource | **deny** | `location_of` yields `None` section → `UNRESOLVED` |
| Stale assignment | **deny by construction** | no temporal validity; assignments are config, changed by redeploy |
| Conflicting corridor | **deny** | `location_of` raises `CORRIDOR_CONFLICT`; caught and denied, never 500 |
| Unknown section | **deny** | `railway_scope` refuses the scope at load; a live unknown section → `NO_MATCH` |

**OQ-4 (§26):** whether "not found" or "not in your scope" should be distinguishable. 404 for
both leaks nothing; 403-then-404 leaks existence. Recommendation: authorize first and return
403 uniformly, accepting that a caller cannot distinguish. Needs approval.

---

## 15. Demo mode

Demo mode must keep working and must **not** become trusted.

Current demo: headers → `human_actor(...)` with `DECLARED_UNVERIFIED`. There is no
authentication and `IdentityAssurance` deliberately has **no** `AUTHENTICATED` member
(`actor.py:34-38`) — that member arrives with the code that can justify it.

**The boundary must be the policy object, not a flag.**

```
DEMO:        DECLARED_UNVERIFIED + declared role + scope from a SERVER-SIDE fixture
PRODUCTION:  AUTHENTICATED       + directory role + directory scope
```

Three invariants:

1. **Scope is never client-supplied.** There is no `X-Actor-Section` header and there must
   never be one. Demo scope comes from a server-side fixture keyed by the declared
   `actor_id`. The request carries no scope, ever.
2. **A demo policy must not be installable in production.** If the switch were
   `if settings.demo:` inside one policy, a misconfiguration becomes a bypass. Two classes,
   selected at construction, make the mistake structural rather than silent.
3. **`UnenforcedPolicy` stays exactly as it is.** It is the honest default and the current
   shipped behaviour; 10.1D adds a policy beside it, and does not change which one ships by
   default. Flipping the default is a separate, explicit decision.

Until 10.3, a demo enforcing policy is trusting a header — which is acceptable *for a demo*
and unacceptable in production. That asymmetry is exactly why they are different objects.

---

## 16. API compatibility

**v1 is frozen and does not need to change.** Resource-aware authorization is introduced
entirely through internal service and dependency changes. No new request field, header,
query parameter or response field is required, and no v2 is warranted.

| Surface | Change | Why none is needed |
|---------|--------|--------------------|
| Request bodies | **none** | the resource is derived server-side from the job the path already names |
| Headers | **none** | scope is never client-supplied (§15); `X-Actor-Id` / `X-Actor-Role` are unchanged |
| Path / query params | **none** | `job_id` in the path is already the resource identity |
| Response shapes | **none** for single-resource reads | a denial is an error, not a new field |
| Status codes | `403` on an authorization denial | already the code `AuthorizationDenied` maps to; already emitted for a SYSTEM role at `deps.py:86-91` |
| OpenAPI | **no change** | no schema, parameter or response model changes |

Two caveats, both already surfaced:

1. **List endpoints are the one place v1 could be affected.** Scope-filtering `GET /v1/jobs`
   or `GET /v1/obligations` does not change the response *shape* (`{items, next_cursor}` is
   unchanged) but does change its *contents* for a scoped caller, and the frozen shape cannot
   signal "this page was filtered". That is Decision 2 (§27), and it is the reason §23
   sequences single-resource gating ahead of list filtering.
2. **`GET /v1/jobs` and `GET /v1/jobs/{id}` must gain an `actor` dependency** to be
   authorized at all, since they currently take none (§2.3). Adding
   `actor: Actor = Depends(request_actor)` is **backward compatible**: both headers absent
   already yields `unidentified_actor()`, so existing callers that send nothing keep working
   exactly as they do now, and the OpenAPI addition is an optional header already documented
   on every other route.

The contract freeze (`test_slice7_contract_freeze.py`) must stay green throughout; it is the
check that proves this claim rather than merely asserting it.

---

## 17. Test-policy compatibility

All 8 test policies use the positional `def authorize(self, actor, action)`.

**Rejected: `authorize(actor, action, resource=None)`.** It is the smallest diff and it
keeps every test green — and it is exactly the trap the red lines in §24 name. A default of `None`
means a production policy that forgets to pass the resource silently performs no scope check
and returns "permitted". The failure is invisible: no error, no log, full access. Choosing
it would optimize for code size at the cost of the security boundary, which the brief
explicitly forbids.

**Recommended: a separate Protocol method.**

```python
class AuthorizationPolicy(Protocol):
    enforcing: bool
    def authorize(self, actor, action) -> None: ...
    def authorize_resource(self, actor, action, location) -> None: ...
```

- The 8 legacy test policies still implement only the two-argument `authorize` method and now explicitly declare `enforcing = False` via `RoleActionOnlyPolicy`; an enforcing two-argument-only policy is rejected at the `JobService` construction boundary. This is intentional and preserves the security invariant.
- `UnenforcedPolicy` gains a no-op `authorize_resource`, staying `enforcing = False`.
- **The service asserts at construction that an `enforcing` policy implements
  `authorize_resource`.** A 2-arg-only policy is therefore *structurally unable* to be
  installed as an enforcing production policy — the omission fails loudly at wiring time,
  not silently at decision time.
- A test policy that wants to exercise scope opts in by implementing the second method.

The security boundary stays unambiguous: there is no code path where "no resource was
passed" reads as "permitted".

---

## 18. Authorization policy design

### 18.1 The order, corrected against the brief

The brief's proposed sequence is nearly right. Two corrections, both from evidence:

```
1. resolve the actor                       (exists: _resolve_actor)
2. role/action check                       <-- CHEAP, no I/O, do it FIRST
3. load the job / resolve the resource     <-- a READ; permitted by the denial contract
4. resolve ResourceLocation                (location_of)
5. scope check                             (scopes_for + match_scope)
6. [later] SoD                             (needs history; NOT in 10.1D)
7. lifecycle validates state               (unchanged, under the lock)
8. mutation                                (unchanged)
```

- **Step 2 before step 3** is a correction to the brief's order (it had "determine required
  resource" second). A role that may never perform an action should be refused **without a
  database read**. This keeps the cheapest, most common denial free of I/O and avoids an
  unauthorized caller causing repository work.
- **Step 3 is legitimate.** Finding #2: the denial contract forbids writes, not reads. The
  existing tests assert `count_events == before` and unchanged status — a `get()` violates
  neither.

### 18.2 Where the call moves

Today `authorize()` sits at the top of each method and the job is loaded later. The minimal
change is to **split the call in two** at each job-scoped site:

```python
starter = self._resolve_actor(actor)
self.authorization.authorize(starter, JobAction.START_EXECUTION)   # role/action, unchanged

with self.lifecycle_lock:
    job = self.repository.get(job_id)
    if job is None:
        raise KeyError(...)
    self.authorization.authorize_resource(                          # NEW: scope
        starter,
        JobAction.START_EXECUTION,
        location_of(job, corridor_id=self.corridor.corridor_id),
    )
    ...
```

This keeps the existing call **exactly where it is**, with its exact signature, so all 16
sites and all 8 test policies continue to behave identically. The scope check is a **second,
additive** call. `UnenforcedPolicy` implements both as no-ops.

### 18.3 `REPORT_JOB` is the exception

`REPORT_JOB` authorizes at `service.py:761`; the section is only known after
`resolve_job_resource` at step 3.5 (`service.py:858`). The two-call design handles this
naturally: the role/action check stays at 761, and the scope check goes immediately after
step 3.5 — **before** step 6 (the database write at `service.py:1071`) and before the
idempotency replay is returned. No third mechanism is needed.

---

## 19. Performance

First implementation can and should be **in-process, deterministic and read-only**.

| Lookup | Cost | Note |
|--------|------|------|
| Role/action | O(1) frozenset membership | no I/O |
| Role selection | O(assignments per person) | in-memory directory |
| Scope lookup | O(assignments per person) | `scopes_for` sorts a handful of items |
| Scope match | O(1) frozenset membership | `match_scope` |
| Resource resolution | **one `repository.get()`** | the row the method was going to load anyway |
| SoD history scan | O(events per job) | **not in 10.1D** |

The only new I/O is **zero** for job-scoped mutations: `get()` is already called inside the
lock. The read paths already load the job too. No caching, no memoization, no index is
justified. `possession_inputs` and the optimizer paths are untouched.

The one place to watch is list filtering (§13): filtering after a keyset page means either
short pages or an over-fetch loop. That is a correctness question before it is a performance
one.

---

## 20. Threat model

| Threat | Status today | Mitigation |
|--------|--------------|------------|
| **Privilege escalation** (client sends a role it does not hold) | **OPEN** — headers are trusted, nothing enforced | role must come from `select_role` over directory assignments, never from the header; completed at 10.3 |
| **Role spoofing** | **OPEN**, but honestly recorded as `DECLARED_UNVERIFIED` | as above; `SYSTEM` already 403 |
| **Scope spoofing** | **CLOSED by design** | no scope input exists on any request; scope comes only from the directory/fixture |
| **Corridor spoofing** | **CLOSED** | deployment serves one corridor; `create_job` rejects a mismatching `corridor_id` with 400 (`service.py:789-795`); `location_of` raises `CORRIDOR_CONFLICT` |
| **Section spoofing** | **CLOSED** | section is derived by `resolve_job_resource`, never client-supplied |
| **Cross-corridor collision** | **CLOSED (10.1C)** | scope carries `corridor_id`; identical section strings in another corridor are `NO_MATCH` |
| **Missing-section bypass** | **CLOSED (10.1C)** | `UNRESOLVED` is a distinct non-permitting result; pinned against the live legacy row shape |
| **Stale identity** | **ACCEPTED RISK** | no temporal validity; narrowing a grant needs a redeploy. Must be revisited before production |
| **Duplicate role assignment** | **CLOSED (10.1B)** | `_checked` refuses duplicates |
| **Duplicate scope assignment** | **CLOSED (10.1C)** | `DUPLICATE_ASSIGNMENT`; frozenset makes `{S2,S3}` and `{S3,S2}` equal |
| **SoD bypass** | **OPEN and unfixable today** | role-prefixed `actor_id`s make self-approval undetectable (§8.1). Blocked on 10.3 |
| **Demo-mode escalation** | **OPEN** | two distinct policy classes (§15); demo policy never installable in production |
| **Authorization / lifecycle confusion** | **CLOSED by discipline** | §7: authorization never reads state; lifecycle never checks identity |
| **Test-only bypass leaking to production** | **OPEN if the optional-argument form is chosen** | §17: separate method + construction-time assertion |
| **Direct service-method bypass** | **OPEN** | `list_jobs`, `active_block_candidates`, `possession_inputs`, `possession_windows`, `classify_for_optimization`, `optimization_snapshot`, `verify_job_asset_reference` have no seam (§2.3) |
| **Router-level repository bypass** | **OPEN** | `GET /v1/jobs` and `GET /v1/jobs/{id}` never touch `JobService` (§1, finding #4) |

The last two are the ones this gate newly surfaces and the previous audit did not count.

---

## 21. Database decision

**No database change in 10.1D.** Confirmed against the actual requirement:

- The role/action table is a constant.
- Assignments and scopes are configuration, loaded into memory, injected as a read-only
  Protocol — the same pattern 10.1C used for `SectionDirectory`.
- Resource resolution reads `block_candidate_json`, which already exists and is already
  hydrated (`repository.py:788`).
- SoD would read `job_events`, which already exists and is already append-only. And SoD is
  out of scope anyway.

The **only** thing that would justify a migration is SQL-level scope filtering of list
endpoints, which would need a `section_id` column on `maintenance_jobs`. §13 rejects that
for now in favour of Python post-filtering. If list filtering at scale later demands it, it
is an additive, backfillable column — a separate slice with its own gate.

---

## 22. NovaForge boundary

Unchanged and not touched by this gate.

```
NovaForge:       WHO IS THIS PERSON?                 (identity assertion, 300s bearer)
Pashupatastra:   WHAT RAILWAY ROLE AND SCOPE?        (directory: Person -> RoleAssignment -> RailwayScope)
Pashupatastra:   MAY THIS PERSON DO THIS HERE?       (this slice's policy)
```

Three rules, all already recorded in the readiness audit §16 and reaffirmed here:

1. **NovaForge assertions must never carry railway section ids.** NovaForge knows nothing
   about railway sections and cannot validate one against `SectionRegistry`. Baking scope
   into a 300-second token freezes a possibly-stale grant and makes every scope change a
   re-issue problem.
2. **A NovaForge `ADMIN` role is never a railway `ADMIN`.** The NovaForge role claim is
   *recorded*, never mapped.
3. **The directory is the only place a railway scope may originate.**

---

## 23. Minimum implementation plan

Four sub-slices. **Only the first two are unblocked.**

### 10.1D.1 — Resource-aware seam (UNBLOCKED)

| | |
|---|---|
| **Files** | `identity/authorization.py` (add `authorize_resource` to Protocol + `UnenforcedPolicy`); `jobs/service.py`, `jobs/optimization.py` (add the second call at each job-scoped site); new `backend/tests/test_slice10_1d_authorization_seam.py` |
| **Production changes** | Protocol gains one method; 13 job-scoped sites gain a second call after the job is loaded; `REPORT_JOB` gains one after step 3.5 |
| **Tests** | seam is called exactly once per action per call; `UnenforcedPolicy` permits everything; a recording policy sees the resolved location; a denial still writes nothing; all 8 existing test policies unchanged and green |
| **API impact** | none |
| **DB impact** | none |
| **Security boundary** | the seam exists and is called; it decides nothing yet |
| **Acceptance** | 1831 + new tests green; `UnenforcedPolicy` still the default; no behaviour change |

### 10.1D.2 — Role/action policy (UNBLOCKED)

| | |
|---|---|
| **Files** | new `identity/role_actions.py` (frozen mapping); new `test_slice10_1d_role_actions.py` |
| **Production changes** | the constant table + an `EnforcingPolicy` that consults it; **not wired as default** |
| **Tests** | every action maps to at least one role or is deliberately unmapped; `ADMIN` maps to no operational action; `UNIDENTIFIED` maps to nothing; `ASSIGN_SCHEDULE` denied by default |
| **API impact** | none while unwired |
| **DB impact** | none |
| **Security boundary** | first real refusal; still not shipped by default |
| **Acceptance** | table matches §5; construction-time assertion refuses a 2-arg-only enforcing policy |

### 10.1D.3 — Scope enforcement (BLOCKED on a directory decision, §27 Decision 1)

| | |
|---|---|
| **Files** | new `identity/directory.py` (read-only Protocol + in-memory impl); `EnforcingPolicy` gains the scope branch |
| **Production changes** | directory injected into `JobService`; scope consulted in `authorize_resource` |
| **Tests** | the §14 fail-closed matrix, every row; `UNRESOLVED` never permits; legacy job denied; cross-corridor denied |
| **API impact** | none if single-resource only; **yes** if list filtering is included (§13) |
| **DB impact** | none |
| **Acceptance** | every §14 row pinned; demo and production policies are distinct classes |

### 10.1D.4 — SoD (BLOCKED on 10.3)

Do not start. Requires role-neutral `person_id` in `Actor.actor_id` (§8.1). Attempting it on
today's role-prefixed ids produces a check that silently never fires.

**Separately, and arguably first:** closing the two unauthenticated read endpoints
(`GET /v1/jobs`, `GET /v1/jobs/{id}`) is independent of all four and could ship as its own
small change — routing them through `JobService` with a new `READ_JOB` action. It is the
only §3 gap current code proves.

---

## 24. Red lines

Restated as testable invariants for the implementation:

| Red line | How it is held |
|----------|----------------|
| Never trust client role | role resolved via `select_role` over directory assignments (10.3); until then, demo policy only |
| Never trust client scope | no scope input exists on any request; none may be added |
| NovaForge ADMIN ≠ railway ADMIN | role claim recorded, never mapped |
| Missing scope ≠ global | `scopes_for` → `()`; `match_scope(None, ...)` → `NO_MATCH` |
| `UNRESOLVED` ≠ authorized | three-valued enum, never a bool; only `MATCH` permits |
| No raw tokens in Actor/events | `Actor` stays three fields; canonical bytes unchanged |
| No duplicated NovaForge identity | `ExternalIdentity` keys, never copies, the provider record |
| No duplicated topology | `SectionDirectory` Protocol injected; identity never builds a registry |
| No bypassed lifecycle | authorization never checks or replaces state |
| No bypassed audit | denial writes nothing; lifecycle refusal still writes `TRANSITION_REJECTED` |
| No silently weakened safety | 1831-test baseline must stay green |
| Demo actors never authenticated | `IdentityAssurance` gains no `AUTHENTICATED` member in this slice |
| No compatibility bypass | separate Protocol method + construction-time assertion (§17) |

---

## 25. Acceptance criteria

10.1D as a whole is complete only when:

- [ ] `authorize_resource` exists on the Protocol and is called at every job-scoped site
- [ ] the role/action table exists and is a constant
- [ ] `ADMIN` maps to no operational action and no scope
- [ ] `UNIDENTIFIED` maps to nothing
- [ ] `UNRESOLVED` and `NO_MATCH` both deny, pinned by test
- [ ] the legacy sectionless job is denied for every scoped role
- [ ] cross-corridor identical section strings deny
- [ ] authorization never reads job status
- [ ] a denial still writes no event and changes no state
- [ ] lifecycle still records `TRANSITION_REJECTED` on a refused transition
- [ ] all 8 existing test policies work unchanged
- [ ] an enforcing policy lacking `authorize_resource` is refused at construction
- [ ] demo and production policies are distinct classes
- [ ] `Actor` unchanged; canonical event bytes unchanged
- [ ] no schema, API, OpenAPI or NovaForge change
- [ ] 1831 + new tests green; jobs.db unchanged

---

## 26. Open questions

| # | Question | Blocks | Recommendation |
|---|----------|--------|----------------|
| **OQ-1** | May `ENGINEER` or `AUTHORITY` report a job, or is `REPORT_JOB` WORKER-only? | 10.1D.2 | Nothing in code answers it. Start WORKER-only (narrowest), widen on evidence |
| **OQ-2** | Does holding one section in a corridor permit corridor-wide actions (`REQUEST_OPTIMIZATION`, `READ_OPTIMIZATION_RUN`)? | 10.1D.3 | Permit for ENGINEER, since optimization is inherently corridor-wide and already single-corridor; needs approval |
| **OQ-3** | Is "the worker who started also completed" an SoD conflict? | 10.1D.4 | Probably not — it is the same duty, not a separated one |
| **OQ-4** | 403 vs 404 for an out-of-scope job: may a caller learn it exists? | 10.1D.3 | Uniform 403 after authorize; accept the ambiguity |
| **OQ-5** | Should an authorization denial be recorded? | §7.1 | Deferred; it breaks three existing contract tests |

---

## 27. Verdict

**AMBER.**

Not because the change is large — 10.1D.1 is genuinely small, and the denial contract
(finding #2) plus the hydrated `block_candidate` (finding #3) make the mechanics cheap.
AMBER because **four trust-boundary decisions require a human**, and one sub-slice is
blocked outright:

1. **Decision 1 — the two unauthenticated read endpoints.** `GET /v1/jobs` and
   `GET /v1/jobs/{id}` bypass `JobService` entirely. Closing them means routing through the
   service and adding a `READ_JOB` action; leaving them is a knowing hole. Either way it is
   a choice, and it is the only place current code proves an action is missing.
2. **Decision 2 — list filtering versus frozen v1.** Scope-filtering a page changes what a
   frozen v1 response contains for a scoped caller, and keyset pagination cannot express a
   filtered short page. Filter, over-fetch, or defer.
3. **Decision 3 — the audit gap on denial.** A scope denial currently leaves no trace, and
   recording one breaks three existing contract tests that assert `count_events == before`.
4. **Decision 4 — SoD sequencing.** SoD cannot be built on today's role-prefixed `actor_id`s
   without producing a check that silently never fires. It must wait for 10.3. This is the
   strongest single argument against GREEN.

None of these is a contradiction between stated invariants, so it is **not RED**. No
privilege-escalation path is being newly opened, and the existing header trust is honestly
recorded as `DECLARED_UNVERIFIED` and enforced nowhere.

**Exact next step:** obtain approval on Decisions 1–3, then implement **10.1D.1 only** — the
`authorize_resource` seam with `UnenforcedPolicy` as a no-op and no behaviour change. It is
independently valuable, reversible, and provable against the existing 1831-test baseline.
