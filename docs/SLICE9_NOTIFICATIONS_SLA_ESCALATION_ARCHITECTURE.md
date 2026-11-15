# SLICE 9 ARCHITECTURE GATE — NOTIFICATIONS, SLA, ESCALATION, ACCOUNTABILITY

**Project:** Pashupatastra — SIH26027
**Audit date:** 2026-09-20
**Baseline:** `256b5d43f10841db9aaf30ea9f13ea9d398cc07d` (`feat(backend): harden field maintenance intake`)
**Mode:** READ-ONLY DESIGN GATE. No production code, test, schema, database, OpenAPI document, protected file, commit or migration was created or modified. The test suite was **not** run. This file is the only artifact added.
**Verdict:** **AMBER** — see §24.

---

## 1. EXECUTIVE SUMMARY

**Nothing exists.** There is no notification code, no notification event, no SLA field, no deadline, no escalation concept, no scheduler, no background worker, no retry, no delivery status, and no external provider dependency anywhere in the repository. `NOTIFIED` is a *lifecycle status* and `POST /v1/jobs/{id}/notify` is the *commit/pin* transition. **It notifies nobody.** The docs already say so plainly (`docs/api-contract-v1.md:114`), so there is no doc-versus-code drift to reconcile — only a blank space to design into.

**What exists is unusually good ground to build on.** Eight slices produced an append-only, per-job event history (`job_events`) in which every state change is recorded in the same transaction as the change itself, with actor identity, role, assurance, reason, the optimization run it belongs to, before/after state snapshots, free-form plain-JSON metadata, and a canonical byte serialization ready for a future hash chain. Every timestamp an SLA needs is already there, immutably.

**The central architectural finding is a decomposition, not a feature.** The three things the brief blends apart cleanly, and they have *different* durability requirements:

| Concern | Nature | Durable state needed? |
|---|---|---|
| **Obligation** ("who owes an action, and by when") | A **pure function** of committed events + policy + clock | **No.** Derive it. |
| **Intent** ("someone should be told X about job Y") | Also a pure function of the same three inputs | **No.** Derive it. |
| **Delivery** ("an SMS was attempted at 14:02 and failed") | An **external side effect**, unknowable unless recorded | **Yes.** This is the only place. |

That asymmetry **dissolves the transactional-outbox problem rather than solving it with machinery**. The classic hole — commit the state change, crash before writing the notification row, lose the notification forever — cannot occur when the intent is recomputed from the committed history on the next sweep. A crash costs latency, never a lost obligation. Only the delivery record needs a table, and only because "we sent it" is not derivable (§9).

**The blocking product gap is not notification. It is that the system cannot name a recipient.** There is no user registry, no authority directory, no contact detail, no assignment of a job to a person, and no `assigned_to` anywhere (verified: §2.9). `X-Actor-Id` is an unvalidated free string, and the only "identities" in the repository are test fixtures (`AUTHORITY-017`, `WORKER-042`). Items 1, 4 and 5 of the brief — *authority approval responsibility*, *escalation when the authority does not respond*, *field execution responsibility* — all presuppose an addressable responsible party. **You cannot escalate to someone the system cannot name.**

This is scopeable rather than fatal, and the product narrative supports the scoping: the demo playbook consistently speaks of "a section controller" — a **role**, never a named individual (`PASHUPATASTRA_FINAL_DEMO_PLAYBOOK.md:595,691,953`). So Slice 9 can address obligations and intents to a **role** (`AUTHORITY`, `WORKER`) and defer the individual directory to the authentication slice that must own it anyway. That is the difference between AMBER and RED.

**Recommended Slice 9 is smaller than the brief implies:** derive obligations and SLA state, expose them read-only, record escalation *level* as a derived condition and the *act of raising a notification* as an immutable fact — and **ship no delivery channel at all** (§18). Delivery needs a runner process that does not exist and a recipient model that does not exist. Building the derivation first is what makes delivery safe later.

---

## 2. CURRENT IMPLEMENTATION INVENTORY

Verified by reading the code at this HEAD, not by trusting any prior gap list. Three tables exist in total: `maintenance_jobs`, `job_events`, `optimization_runs` (`grep -rn "CREATE TABLE" backend` → 3 production sites).

### 2.1 Notifications — ABSENT, completely

| Searched for | Result |
|---|---|
| `notification`, `escalat`, `outbox`, `sla`, `deadline`, `due_at`, `respond_by`, `remind` | **Zero production matches.** The only hits are `days_overdue` (an *asset maintenance-age* scoring feature, unrelated — `data/feature_adapter.py:51`) and one docstring using "notification" to say a proposal is *not* one (`jobs/proposal.py:29`) |
| `apscheduler`, `celery`, `cron`, `BackgroundTasks`, `asyncio.create_task` | **Zero.** No scheduler, no background worker, no async task |
| `smtp`, `twilio`, `fcm`, `webhook`, `push_` | **Zero.** No provider, no channel, no transport |
| `requirements.txt` | `pydantic`, `ortools`, `pytest`, `fastapi`, `uvicorn`, `httpx`. Nothing notification-related |

**The naming hazard, stated now because it will cause real confusion later:** `POST /v1/jobs/{id}/notify` and `JobStatus.NOTIFIED` both mean *the proposal was committed and pinned*. Neither sends anything. Once real notifications exist, an endpoint named `/notify` that notifies nobody is actively misleading. v1 is frozen, so renaming is a v2 question (§14).

### 2.2 Job lifecycle events — PRESENT and strong

`backend/app/jobs/events.py`. **21 event types** (`events.py:97`), each recording:

`event_id`, `job_id`, `event_type`, `occurred_at`, `actor` (id / role / kind / assurance), `reason`, `optimization_run_id`, `before_state`, `after_state`, `metadata`, `schema_version`; `entity_type` fixed to `maintenance_job`.

Properties that matter to Slice 9:

- **Written in the same transaction as the state change** (`repository.create`, `repository.mutate_jobs`, both `BEGIN IMMEDIATE`). History can never claim a transition that did not commit, or omit one that did.
- **Append-only at the SQL layer** — `job_events_no_update` / `job_events_no_delete` triggers abort any UPDATE or DELETE from any connection (`persistence/append_only.py:35`).
- **One canonical timestamp format** — `EVENT_TIMESTAMP_PATTERN` (`events.py:75`) enforces UTC, microseconds always present, explicit `+00:00`. Validated at construction. This makes event timestamps **lexicographically comparable and safely subtractable** — the single most useful property for an SLA engine.
- **`metadata` must already be plain JSON**, round-tripped at construction, `allow_nan=False`.
- **`canonical_bytes()`** is hash-chain-ready; `sequence` is deliberately excluded from canonical content. No hash columns exist yet (deliberately — "a hash column with no writer would be dead schema").
- **Nothing emits events on its own.** Every event corresponds to a real service action.

The events relevant to obligation derivation:

| Obligation-relevant event | Meaning |
|---|---|
| `BLOCK_PROPOSED` / `BLOCK_REPROPOSED` | A placement now awaits authority review — **starts the approval clock** |
| `COMMITTED_BLOCK_PRESERVED` | A run kept committed work pinned (also a placement event) |
| `BLOCK_COMMITTED` | The authority approved/committed — **ends the approval clock, starts the execution clock** |
| `PROPOSAL_REJECTED` / `PROPOSAL_POSTPONED` | The authority responded by refusing/deferring — **ends the approval clock** |
| `PROPOSAL_INVALIDATED` / `OPTIMIZATION_REFUSED` | The *system* withdrew the placement — the obligation is **moot, not breached** |
| `BLOCK_RELEASED` | The authority withdrew a commitment — **ends the execution clock** |
| `EXECUTION_STARTED` | Field work began — **ends the start obligation, starts the completion obligation** |
| `EXECUTION_COMPLETED` | Terminal, with evidence |
| `EXECUTION_NOT_COMPLETED` | Work failed; commitment released for replanning |
| `TRANSITION_REJECTED` | An attempt was refused; state unchanged. Recorded, not silent |

### 2.3 Actors and roles — PRESENT, honestly unverified

`backend/app/identity/actor.py`. Three **independent** facts, never inferred from one another: `actor_id`, `role`, `assurance`. `kind` (HUMAN/SYSTEM) is *derived* from role so a record cannot claim `role=SYSTEM, kind=HUMAN`.

- `ActorRole`: `WORKER`, `ENGINEER`, `AUTHORITY`, `ADMIN`, `SYSTEM`, `UNIDENTIFIED`
- `IdentityAssurance`: `SYSTEM_INTERNAL`, `DECLARED_UNVERIFIED`, `NONE` — **there is no `AUTHENTICATED` member**, deliberately, "the security slice adds it together with the code that can justify it"
- `actor_id` is pattern-bounded (`^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$`) so it is safe to echo into an audit record and cannot smuggle separators into a future hash chain
- A human `actor_id` may not start with `SYSTEM`; a SYSTEM actor must carry `SYSTEM_INTERNAL`. The HTTP layer additionally refuses a caller-declared `SYSTEM` role with **403** (`api/deps.py:86`)
- Both actor headers must be sent together or neither; one alone is **400** (`api/deps.py:76`)

### 2.4 Authorization — SEAM PRESENT, nothing enforced

`backend/app/identity/authorization.py`. 13 `JobAction` members; every state-changing service method calls `authorize(actor, action)` **before** reading or writing anything, so a denying policy stops an action with no state change and no event (proven by test). The only shipped policy is `UnenforcedPolicy`, `enforcing = False` (`authorization.py:111`), which permits everything. **No permission in this repository is enforced today.**

### 2.5 Audit / optimization runs — PRESENT, with two divergences

`backend/app/audit/`. `optimization_runs` (`audit/repository.py:52`) is append-only by the same shared trigger mechanism, exposes no update or delete method, and stores `request_json`, `result_json`, `provenance_snapshot_json`, `solver_status`, `solve_time_seconds`, `requested_at`, `completed_at`, `error`. Two divergences from the job-event model that Slice 9 must know about:

1. **A different timestamp format.** `audit/service.py:41` uses `datetime.now(timezone.utc).isoformat()`, which **omits the fractional part when microseconds are exactly zero**. `job_events.occurred_at` always carries microseconds. The two are *nearly* the same and both parse with `fromisoformat`, but they are not the same format, and **string comparison across the two tables is unsafe**. Only matters if SLA arithmetic ever spans both; it should not need to (§5, §6).
2. **A different actor model.** `optimization_runs.actor` is a plain string, defaulting to `SYSTEM_ACTOR = "system:unauthenticated-demo"` (`audit/models.py:30`). The jobs pipeline passes `requester.actor_id` (`jobs/optimization.py:550`), so the run's actor agrees with the job's `OPTIMIZATION_REQUESTED` event — but it is a bare string, not an `Actor`, and carries no role or assurance.

**There is no HTTP route for `optimization_runs`.** It is readable only in-process.

### 2.6 Derived read models — PRESENT, and the precedent Slice 9 should follow

Two first-class read models are already derived from history alone, with no second store:

- **`BlockProposal`** (`jobs/proposal.py`) — built from the job row + the specific placement event that produced the current placement + the `optimization_runs` row for that run. Critically, **`generated_at = event.occurred_at`** (`proposal.py:382`) — the placement event's canonical timestamp. **This is the approval SLA's clock start, and it already exists.**
- **`ExecutionRecord`** (`jobs/execution.py:580`) — one execution attempt rebuilt from `EXECUTION_*` events. There is **no `job_executions` table**, and the module says why: *"the events have to be written for the audit anyway, and a second, independently-written store would be a second source of truth."*

`ExecutionRecord` carries `started_recorded_at`, `actual_start_at`, `ended_recorded_at`, `actual_end_at` — separating **when the system recorded it** from **when the worker says it happened**. It also carries `ExecutionDeviations` (`started_before_planned_start`, `ended_after_planned_end`) — "departures from the approved window: **recorded, never enforced**."

`build_execution_records` **refuses** incoherent history (`ExecutionIntegrityError`) rather than guessing what it meant.

### 2.7 Timestamps and observation-time rules — PRESENT

- Canonical event time: UTC, microsecond, explicit offset (§2.2).
- Observed times (`actual_start_at`, `captured_at`) require an **explicit UTC offset**, are refused if more than `MAX_OBSERVATION_FUTURE_SKEW = 5 minutes` past the recording moment (`execution.py:70`), and are converted to horizon-relative minutes by the one authoritative conversion.
- **`horizon_relative_minutes`** (`data/horizon_anchor.py`) is the single place minute↔calendar arithmetic happens. Its docstring is explicit that fixed-offset arithmetic is valid **specifically because Asia/Kolkata has no DST**, and that "a future timezone with DST would need this precondition re-examined; it is not silently assumed to generalise."

### 2.8 The planning horizon — FIXED, and it constrains one SLA variant

- `DEFAULT_HORIZON_START = "2026-09-10T00:00:00+05:30"` (`contracts/schemas.py:25`) — a **hard-coded constant**.
- `OPTIMIZATION_HORIZON_START = DEFAULT_HORIZON_START`, `OPTIMIZATION_HORIZON_MINUTES = 2880` (`jobs/service.py:286-287`).
- Every run anchors to it: `horizon_start = OPTIMIZATION_HORIZON_START` (`jobs/optimization.py:467`).
- **It is not configurable.** The only environment variables in the entire application are `PASHUPAT_JOBS_DB` and `PASHUPAT_CORRIDOR_ID`.

**Consequence.** Today is 2026-09-20; the horizon covers 2026-09-10 → 2026-09-12 in local railway time. Every currently-scheduled block's planned wall-clock window is **already in the past**. A naive execution SLA of the form "work should have started at its planned minute" would therefore mark **every job instantly overdue**. This selects the anchor (§6); it does not block the capability.

Moving `horizon_start` to "now" is **not** a one-line change: the Slice 4 timetable coverage gate fails closed when the horizon covers service dates the timetable does not describe (`data/timetable_coverage.py`), and the checked-in dataset asserts exactly two service dates. That makes a dynamic horizon a **data-infrastructure dependency**, not a design preference.

### 2.9 Recipients, assignment, ownership — ABSENT, completely

| Searched for | Result |
|---|---|
| `recipient`, `contact`, `phone`, `email`, `assigned_to`, `assignee`, `supervisor`, `directory`, `user` | **Zero production matches** across `backend/app` and `contracts` |

There is **no user table, no authority registry, no contact detail, and no field on a job naming who is responsible for it**. `AUTHORITY-017` / `WORKER-042` exist only as test fixtures. The demo narrative names a **role** ("a section controller"), never an individual (`PASHUPATASTRA_FINAL_DEMO_PLAYBOOK.md:595,691,953`). The Milestone-1 frontend (`frontend/src/`) is a static-JSON dashboard with no user or auth concept.

### 2.10 API v1 — FROZEN, 15 operations

`backend/app/jobs/router.py`, prefix `/v1`. 27 `ErrorCode` members (`api/errors.py:32`). The drift guard `_contract_shape` (`tests/test_slice7_contract_freeze.py:783`) compares, per operation, the parameter list, the request-body `$ref` and `required` flag, and **the set of response status codes**; plus, per schema, its **`required` list** — *not* its property list. Slice 8 confirmed the mechanics: an optional added field passes silently; a new response status or a changed `required` list fails loudly.

### 2.11 Query shape — the real constraint on an accountability dashboard

| Table | Indexes | Missing for Slice 9 |
|---|---|---|
| `maintenance_jobs` | PK on `job_id` only | **No index on `status` or `created_at`** |
| `job_events` | `(job_id, sequence)`, `(optimization_run_id)`, UNIQUE `event_id` | **No index on `event_type` or `occurred_at`** |
| `optimization_runs` | PK on `run_id` | — |

`JobHistoryRepository` (`jobs/history.py`) exposes `list_for_job`, `list_for_run`, `get_by_event_id`. **There is no cross-job query and no time-range query.** `maintenance_jobs` has **no column recording when the current status was entered**; `updated_at` is rewritten by *any* mutation, including an optimizer re-proposal, so it cannot answer "how long has this been awaiting approval?"

Therefore a dashboard answering "all overdue approvals" today must either read every candidate job's full history (an **N+1 read**) or gain a new query method. This is the precise missing backend capability (§6, §15).

### 2.12 Clock injection — HALF-WIRED

`JobService.clock` is injectable (`jobs/service.py:552`) and used by exactly four call sites — release and the three execution transitions. Every other write path calls bare `event_timestamp()`: `create_job` (`:895`), `notify` (`:1418`), `reject` (`:1494`), `postpone` (`:1562`), `record_rejected_transition` (`:1938`), `set_schedule` (`:2026`), and the optimization pipeline (`optimization.py:473,576,650`).

**Why this matters concretely:** an SLA engine is a function of *now*. Without a deterministic clock across the paths an SLA observes, its tests must manipulate real time or monkey-patch module globals. Finishing the injection is a small, bounded Slice 9 scope item.

### 2.13 Accountability guards — PRESENT but ASYMMETRIC

`_require_identified_human` (`lifecycle.py:1872`) — *"Accountability, not RBAC: an execution transition needs a named person"* — refuses `UNIDENTIFIED` and SYSTEM actors. It guards:

| Transition | Requires an identified human? |
|---|---|
| `start_execution`, `complete_execution`, `report_execution_not_completed` | **Yes** (`lifecycle.py:2018, 2164, 2269`) |
| `release_committed_block` | **Yes** (`lifecycle.py:1802`) |
| **`notify` / `approve_proposal`** | **No** |
| **`reject_proposal`** | **No** (`plan_reject`, `lifecycle.py:1491` — requires a non-blank `reason` and `expected_proposal_run_id`, but no identity) |
| **`postpone_proposal`** | **No** |
| `create_job` | No (deliberate: an unidentified report is better than a lost one) |

So **an approval, rejection or postponement can be recorded against `UNIDENTIFIED` / assurance `NONE`** — while the field worker who executes it cannot. This directly undercuts brief item 1, *authority approval responsibility* (§16).

### 2.14 Provenance — PRESENT, four axes, all SYNTHETIC

`data/provenance.py`: `topology`, `timetable`, `asset_condition`, `possession`; `ProvenanceLevel` is `REAL_STATIC` / `REAL_SCHEDULED` / `SYNTHETIC` with **no `LIVE` member**; `effective` is the weakest link. All four are `SYNTHETIC` today.

---

## 3. EXISTING CAPABILITIES TO REUSE (do not rebuild any of these)

| # | Capability | Reuse for |
|---|---|---|
| 1 | `job_events`, append-only, transactional with state | The **only** source of obligation truth |
| 2 | `EVENT_TIMESTAMP_PATTERN` canonical UTC microsecond time | All SLA arithmetic; safe string comparison |
| 3 | `BlockProposal.generated_at` (`proposal.py:382`) | Approval SLA clock start — **already derived** |
| 4 | `ExecutionRecord` + `started_recorded_at` / `ended_recorded_at` | Execution SLA clock start/stop |
| 5 | `ExecutionDeviations` — "recorded, never enforced" | The exact precedent for how escalation should behave |
| 6 | Derived-read-model pattern (`proposal.py`, `execution.py`) | Derive obligations; add no second store |
| 7 | `Actor` (id/role/**assurance**) | Honest attribution of every escalation |
| 8 | `JobAction` + `AuthorizationPolicy` seam | New read actions pass the same seam |
| 9 | `install_append_only_guards` (`persistence/append_only.py`) | Any new durable table gets the same immutability, one mechanism |
| 10 | `canonical_json` / `canonical_bytes()` | Deterministic intent keys and digests |
| 11 | **Slice 8's deterministic-`event_id` + UNIQUE idempotency trick** | Notification de-duplication (§9) — the codebase's own proven idiom |
| 12 | `horizon_relative_minutes` (`data/horizon_anchor.py`) | The one minute↔calendar conversion; never re-derive |
| 13 | `_require_identified_human` (`lifecycle.py:1872`) | Extend to approval paths (§16) |
| 14 | `JobService.clock` | Deterministic SLA tests — finish the wiring (§2.12) |
| 15 | `_no_proposal_reason` (`service.py:2310`) — history-aware attribution | The precedent: it already answers "who decided this, and why" |
| 16 | `list_page` keyset pagination (`repository.py:418`) | Accountability list endpoints |
| 17 | `TRANSITION_REJECTED` | Refused escalation attempts are recorded, not silent |

**Item 15 deserves emphasis.** `_no_proposal_reason` already walks a job's history backwards and distinguishes *an authority's decision* (naming the role, the actor id and the reason they gave) from *an optimizer outcome*. That is accountability logic that already exists and already has the right shape. Slice 9's obligation model is its generalisation, not a new invention.

---

## 4. GAPS

| # | Gap | Type | Severity |
|---|---|---|---|
| **N1** | **No recipient model.** No user registry, no authority directory, no contact detail, no job→person assignment (§2.9). Nothing can be addressed | **architecture / product** | **Blocking for individual addressing; scopeable to roles** |
| **N2** | **No notification of any kind.** No intent, no delivery, no status, no channel, no provider | product | High |
| **N3** | **No obligation model.** Nothing answers "who owes an action on this job?" | product | High |
| **N4** | **No SLA policy.** No deadline, no configuration, no versioning, no snapshot | product | High |
| **N5** | **No escalation concept**, no level, no trigger | product | High |
| **N6** | **No runner.** No scheduler, worker, cron or task queue. Nothing can act on the passage of time — every state change today is request-driven | **architecture** | **High** |
| **N7** | **No cross-job / time-range event query**, and no index on `event_type`, `occurred_at`, `status` (§2.11). An accountability view is an N+1 read | architecture / performance | Medium |
| **N8** | **No `status_entered_at`.** `updated_at` is rewritten by any mutation, so elapsed-in-state is not answerable from the row | architecture | Medium |
| **N9** | **Approval paths accept an unidentified actor** while execution paths do not (§2.13) | **accountability** | **Medium-High** |
| **N10** | **Fixed `horizon_start`** makes planned-window-relative deadlines meaningless today (§2.8) | data dependency | Medium (scope-limiting, not blocking) |
| **N11** | **Clock injection incomplete** (§2.12) — SLA logic would be hard to test deterministically | testability | Medium |
| **N12** | **No HTTP route for `optimization_runs`** — a run's request/result/provenance is unreadable over the API | API | Low-Medium |
| **N13** | **Two timestamp formats** across `job_events` and `optimization_runs` (§2.5) | consistency | Low |
| **N14** | **`/notify` and `NOTIFIED` mean "commit"** — a naming collision that becomes actively misleading once notifications exist | naming | Low (v2) |
| **N15** | **No holiday / working-calendar data** of any kind | data | Medium (policy question, §11) |

---

## 5. APPROVAL SLA MODEL

### 5.1 Who is responsible

**Intended:** `AUTHORITY` (the "section controller" of the demo narrative). **Actually enforced:** nobody — `UnenforcedPolicy` permits everyone, and the approval paths do not even require an identified human (§2.13). **The system cannot name *which* authority** (N1).

> **Slice 9 must therefore express approval responsibility as `role = AUTHORITY`, an unassigned queue obligation — never as a named person.** Anything stronger would be fiction.

### 5.2 What starts the clock — fully derivable, zero new state

The current proposal's `generated_at`, which is the `occurred_at` of the latest `BLOCK_PROPOSED` / `BLOCK_REPROPOSED` / `COMMITTED_BLOCK_PRESERVED` event whose `optimization_run_id` equals the job's current `proposal_run_id` (`proposal.py:177,382`).

```
job.status == SCHEDULED
    AND job.proposal_run_id == R
    AND placement event E for run R exists
        -> approval_clock_start = E.occurred_at      (canonical UTC)
        -> approval_due_at      = E.occurred_at + policy.approval_sla
```

This is exactly what `build_block_proposal` already computes. **No new column, no new event, no new table.**

**Re-proposal resets the clock, correctly.** A new optimization run that re-places the job emits `BLOCK_REPROPOSED` with a new run id, and the obligation becomes "review *this* proposal" — a different decision, deserving its own clock. This falls out of the derivation for free rather than needing a rule.

### 5.3 What constitutes a response

| Event on the same `optimization_run_id` | Outcome |
|---|---|
| `BLOCK_COMMITTED` | **Responded — approved.** Clock stops. Execution obligation begins (§6) |
| `PROPOSAL_REJECTED` | **Responded — refused**, with a mandatory reason. Clock stops |
| `PROPOSAL_POSTPONED` | **Responded — deferred**, with a mandatory reason and a chosen date. Clock stops; see §5.6 |
| `PROPOSAL_INVALIDATED`, `OPTIMIZATION_REFUSED` | **Not a response — the obligation is MOOT.** The system withdrew the proposal; nobody failed to answer |
| `TRANSITION_REJECTED` | **Not a response.** An attempt was refused; the obligation stands |

`BLOCK_RELEASED` is *not* an approval response — it withdraws an approval that was already given, and belongs to the execution phase (§6.5).

**The moot-versus-breached distinction is the most important rule in this section.** An escalation that blames an authority for not answering a proposal the optimizer had already withdrawn would be a false accusation, and it is exactly the failure mode a naive "status == scheduled AND old" query produces.

### 5.4 Is `scheduled` the right trigger? Yes — no new state is needed

`status == SCHEDULED` **means** "has a current, uncommitted proposal awaiting a human decision." The status machine already carries the obligation. Adding `PENDING_APPROVAL` or `AWAITING_AUTHORITY` would duplicate `SCHEDULED` and require a change to `ALLOWED_TRANSITIONS` (`lifecycle.py:149`) — explicitly out of scope, and unnecessary.

### 5.5 What happens with no response

Nothing, today — and that is the gap. Under Slice 9: the job becomes **derived-overdue**, obligations escalate by **level** (§10), and *notification intents* are raised. **No lifecycle transition occurs.** An unanswered proposal stays `scheduled` and stays committable; the passage of time must never approve, reject, cancel or expire a maintenance decision (§13, §20).

### 5.6 Postponement

`postpone_proposal` returns the job to `reported` and writes `earliest_start_minute = horizon_relative_minutes(selected_date, 0, 0, horizon_start)` (`service.py:1550`), refusing a date outside the deployment horizon. The approval obligation **ends** (it was answered). A **new** approval obligation begins only when a later run re-proposes the job. The authority's chosen date is a *scheduling* constraint, not a new deadline on a human.

---

## 6. EXECUTION SLA MODEL

### 6.1 The anchor decision — and why the obvious anchor is wrong today

There are two candidate anchors, and they are not equivalent:

| Anchor | Question answered | Works today? |
|---|---|---|
| **A. `BLOCK_COMMITTED.occurred_at`** (wall clock) | "An approved block whose execution has not started within *T* of approval" | **Yes — zero dependencies** |
| **B. `planned_start_minute` → wall clock** | "Work did not start at its approved slot" | **No** — requires `horizon_start` to track real time (N10, §2.8) |

**Recommendation: anchor on A for Slice 9.** It uses a canonical, immutable, already-recorded timestamp; it answers the brief's §B question ("what happens if field work never starts?") completely; and it has no data dependency.

**B is the operationally more meaningful statement** and should be built — but only once `horizon_start` tracks real time, which requires timetable data covering the live planning window (the coverage gate fails closed otherwise). Building B now would mark every job overdue the instant the feature shipped (§2.8). **That is a data-infrastructure dependency, and it belongs to a later slice.**

The groundwork for B already exists and should not be rebuilt: `ExecutionDeviations.started_before_planned_start` and `ended_after_planned_end` already record planned-versus-actual, deliberately without enforcing it.

### 6.2 Start obligation

```
job.status == NOTIFIED
    AND BLOCK_COMMITTED event C for the current proposal_run_id
    AND no EXECUTION_STARTED for that run
        -> execution_start_due_at = C.occurred_at + policy.execution_start_sla
```

Responsible party: **`role = WORKER`** (unassigned — N1 again). Satisfied by `EXECUTION_STARTED`. Ended without breach by `BLOCK_RELEASED` (the authority withdrew the commitment — §6.5).

### 6.3 Completion obligation

```
job.status == IN_PROGRESS
    AND an OPEN ExecutionRecord (status == STARTED)
        -> completion_due_at = record.started_recorded_at + policy.completion_sla
```

Satisfied by `EXECUTION_COMPLETED` (which is evidence-gated already — Slice 5) or answered by `EXECUTION_NOT_COMPLETED` (which carries a mandatory reason and returns the job to `reported` for replanning).

**Use `started_recorded_at`, not `actual_start_at`, for the deadline.** `actual_start_at` is a *worker claim* about observation time; `started_recorded_at` is the server's own canonical record. A deadline must not be movable by the party it binds. (`actual_start_at` remains the right value for *deviation* reporting, which is a different question.)

### 6.4 Evidence follow-up (brief item 6)

Completion evidence is **already mandatory and already gated** — `complete_execution` cannot succeed without it (Slice 5 Step 4). So "pending completion evidence" is **not** a separate obligation: it is identical to the completion obligation in §6.3. An open `ExecutionRecord` *is* the outstanding-evidence state. **No new concept is needed**; the accountability view should simply label it clearly.

### 6.5 Release, not-completed, and replanning

| Event | Effect on obligations |
|---|---|
| `BLOCK_RELEASED` | Execution obligation **ends, not breached** (the authority withdrew it, with a mandatory reason). Job → `reported`. A new approval obligation starts on the next re-proposal |
| `EXECUTION_NOT_COMPLETED` | Completion obligation **answered** (mandatory reason + optional evidence). Job → `reported`, `earliest_start_minute` never lowered |
| Re-optimization | A new `BLOCK_PROPOSED`/`BLOCK_REPROPOSED` starts a **fresh** approval obligation with a **fresh** clock |
| `COMMITTED_BLOCK_CONFLICT` | The commitment is kept and the conflict is made visible. The execution obligation **stands**; this is a strong candidate for its own escalation signal — a job whose approved block the optimizer could not honour needs human attention |

**Every one of these is derivable from events that already exist.** None needs a new event type.

---

## 7. ACCOUNTABILITY MODEL

### 7.1 The one question

> **"Who currently owes an action on this job, since when, and by when?"**

**This is deterministically derivable from (job row + its events + policy + clock) with no new durable state.** The derivation is a pure function; two evaluations at the same clock value over the same history produce identical results.

### 7.2 The obligation table (the proposed `JobObligation` derived read model)

| Obligation | Precondition (all derivable) | Owed by (role) | Satisfied by | Clock start |
|---|---|---|---|---|
| `APPROVAL_PENDING` | `scheduled` + placement event for current run + no response | AUTHORITY | commit / reject / postpone | placement `occurred_at` |
| `EXECUTION_START_PENDING` | `notified` + no `EXECUTION_STARTED` for the run | WORKER | `EXECUTION_STARTED` | `BLOCK_COMMITTED.occurred_at` |
| `COMPLETION_PENDING` | `in_progress` + open `ExecutionRecord` | WORKER | `EXECUTION_COMPLETED` / `..._NOT_COMPLETED` | `started_recorded_at` |
| `REPLANNING_PENDING` | `reported` + a prior withdrawal event | ENGINEER / SYSTEM | next optimization run | withdrawal `occurred_at` |
| `ATTENTION_REQUIRED` | `COMMITTED_BLOCK_CONFLICT`, or an integrity refusal recorded | ENGINEER | human reconciliation | the event's `occurred_at` |
| `NONE` | `completed` (terminal) | — | — | — |

Each carries a derived **state**: `WITHIN_SLA` → `DUE_SOON` → `OVERDUE` → `ESCALATED_L1..Ln`, plus `MOOT` (§5.3).

`reported` with no prior withdrawal is **not an obligation on a human** — it is a job awaiting the next optimization run. Calling that "someone owes an action" would flood the dashboard with every newly reported job.

### 7.3 Recently-happened views (brief item 7)

"Recently released / postponed / rejected" are **event queries**, not obligations: filter `job_events` by `event_type ∈ {BLOCK_RELEASED, PROPOSAL_POSTPONED, PROPOSAL_REJECTED, EXECUTION_NOT_COMPLETED}` ordered by `occurred_at` desc. Every needed field — actor, role, assurance, reason, before/after state, run id — is already on the event. **This needs no new data whatsoever, only a query path that does not exist (N7).**

---

## 8. NOTIFICATION INTENT / DELIVERY ARCHITECTURE

### 8.1 Three distinct concepts, never conflated

| | Definition | Derivable? | Durable? |
|---|---|---|---|
| **INTENT** | "role AUTHORITY should be told that job J's approval is overdue at level 1" | **Yes** — pure function of events + policy + clock | No row required |
| **ATTEMPT** | "at 14:02:11Z a send was attempted via channel C" | **No** — an external act | **Required** |
| **RESULT** | "the provider accepted / rejected / timed out" | **No** | **Required** |

> **An in-process function call is not a delivered notification.** Nothing in Slice 9 may report "notified" unless a provider confirmed acceptance — and even then, provider acceptance is *not* human receipt. The honest vocabulary is `ACCEPTED_BY_PROVIDER`, never `DELIVERED` and never `READ`.

### 8.2 The provider abstraction (design it; integrate nothing)

Mirror the existing `TrainDataProvider` Protocol precedent (`data/train_provider.py`):

```
NotificationChannel (Protocol)
    name: str                          # IN_APP | SMS | EMAIL | PUSH | ...
    def send(message) -> ChannelResult  # ACCEPTED | REJECTED | TRANSIENT_FAILURE
```

Ship exactly **one** implementation: a `RecordingChannel` that performs no I/O and records the attempt. It is honest (it claims nothing), it exercises the seam, and it makes the accountability view real without pretending an SMS was sent.

**Possible future channels** (in-app, SMS, email, push, WhatsApp / a government-approved messaging channel) each need: a recipient address (N1), credentials, rate limits, cost control, and a data-protection review. **None is in scope.**

### 8.3 Severity, correlation, channel

- **Severity** derives from escalation level and the job's own `risk_score` / `priority_score` / reported severity. It is not a new stored field.
- **Correlation** is already expressible: `job_id`, the anchoring `event_id`, and `optimization_run_id` are all present on events. A notification should carry all three so an auditor can join it back to the exact decision.
- **Channel** selection is policy, not domain. Until a recipient model exists there is nothing to select between.

---

## 9. OUTBOX / RETRY / IDEMPOTENCY ANALYSIS

### 9.1 The transactional outbox problem, and why it mostly dissolves here

The brief asks the two canonical questions. Both have clean answers **because intent is derived rather than stored**:

> **"The application commits a job event but crashes before sending the notification — how is it eventually delivered?"**

The intent was never a row that could be lost. The next sweep recomputes it from the committed history and finds it still outstanding, because the *obligation* (an unanswered proposal past its deadline) is still true. **A crash costs latency, never an obligation.** This is strictly stronger than an outbox table, which can itself fail to be written.

> **"Notification sends first and the transaction rolls back — how do we prevent a false notification?"**

It cannot happen in this direction. Intents are computed **only from already-committed** events (`job_events` is written inside the same `BEGIN IMMEDIATE` transaction as the state change). A rolled-back transition leaves no event, so no intent is ever derived from it. **A false notification about a transition that did not commit is structurally impossible** — provided the sweep reads committed state and never inspects in-flight work.

The residual risk is the opposite and much safer one: **send succeeds, attempt record fails → a possible duplicate send, never a lost one.** Handled by de-duplication:

### 9.2 De-duplication — reuse Slice 8's proven idiom

Give every intent a **deterministic key**:

```
intent_key = SHA256(canonical_json({
    job_id, obligation_type, escalation_level,
    policy_version, anchor_event_id
}))
```

`anchor_event_id` (the placement or commit event that started the clock) is what makes the key stable across sweeps and *different* after a re-proposal. Enforce it with a **UNIQUE constraint** on the attempt store, exactly as Slice 8 enforced one-job-per-idempotency-key through `job_events.event_id UNIQUE` — the same codebase's own proven mechanism, with tests already demonstrating it holds across threads, connections and OS processes.

Consequence: **two concurrent sweeps (or two workers) cannot double-escalate.** One wins the INSERT; the other sees the constraint and stops. No lock, no coordinator.

### 9.3 Retry

Retry only `TRANSIENT_FAILURE`, with bounded attempts and exponential backoff, each attempt its own append-only row. A permanently failing notification must become **visible in the accountability view** — a notification that silently gave up is precisely the "silent operational failure" the brief (item 10) exists to prevent.

### 9.4 Persistence options compared

| Option | Intent | Attempt/result | Verdict |
|---|---|---|---|
| **A. Pure derivation, no delivery at all** | derived | none | **Correct for Slice 9's core.** Zero schema change. Honest: it cannot claim anything was sent because nothing is |
| **B. Derived intent + new append-only `notification_attempts` table** | derived | new table | **Correct the moment delivery ships.** One genuinely necessary table; gets `install_append_only_guards` |
| **C. Delivery inside `job_events` metadata** | derived | `job_events` | **Structurally blocked.** `job_events` is UPDATE/DELETE-trigger-protected, so a `PENDING→SENT→FAILED` lifecycle can only be N append-only rows; and a notification addressed to a *role* is not a fact about *one job's lifecycle*. It would also pollute the canonical history a future hash chain will cover |
| **D. Outbox table for intents** | new table | new table | **Rejected.** Adds the write-then-crash hole that derivation does not have, plus reconciliation between the outbox and the history it duplicates |
| **E. External queue / broker** | — | external | **Rejected for now.** No deployment story; SQLite single-file is the current reality |

**Recommendation: A for Slice 9, with B specified and deferred.** A new table for *attempts* is justified when delivery ships, because delivery is not derivable; a table for *intents* never is.

---

## 10. ESCALATION MODEL

### 10.1 The four representations, compared

The brief asks for a comparison rather than a choice. Here it is:

| Representation | Strengths | Weaknesses | Verdict |
|---|---|---|---|
| **Derived condition** (recompute level from elapsed time + policy) | No writes; cannot drift; automatically corrects when a job is answered late or a policy changes; no schema | Cannot record "we actually told someone"; recomputation must be cheap (N7) | **Chosen for escalation *state*** |
| **Immutable event** (new `JobEventType`) | Fits the existing audit; transactional | **Requires a new `JobEventType`** — the brief forbids adding one without proof; and an escalation is *not* a job state change, so it would pollute lifecycle history; would also mean time-passage writing into job history with no actor | **Rejected** |
| **Notification record** (durable attempt row) | Records the external act, which nothing else can | Only meaningful once a channel exists | **Chosen for the delivery *act*** (§9, option B) |
| **Separate operational task** (a work-item entity with its own lifecycle) | Rich: assignment, acknowledgement, ownership transfer | **Requires the recipient model that does not exist** (N1); a second lifecycle to keep consistent with the job's | **Deferred** — the right long-term home, blocked on N1 |

**Chosen architecture: escalation *level* is derived; the *act of raising a notification* is durable.** State that can be recomputed is recomputed; facts about the outside world are recorded.

### 10.2 Levels (policy, not lifecycle)

```
WITHIN_SLA -> DUE_SOON -> OVERDUE -> ESCALATED_L1 -> ESCALATED_L2 -> ...
```

Thresholds are multiples of the base SLA defined by policy (§11). Levels are **advisory and monotonic in elapsed time**, and carry **no lifecycle authority** (§12, §20).

### 10.3 The non-negotiable safety property

> **No notification, escalation, deadline or sweep may change the maintenance lifecycle by itself.**

Escalation makes an obligation *visible*. A human still approves, rejects, postpones, releases, starts or completes. Time may not approve work, cancel work, or mark work complete. This mirrors `ExecutionDeviations` — "recorded, never enforced" — which is the existing precedent for exactly this posture.

---

## 11. POLICY / VERSIONING MODEL

### 11.1 The smallest extensible model

A frozen dataclass in code, mirroring `AssetAssociationPolicy` / `DuplicatePolicy` from Slice 8 — injectable, documented, versioned, never a literal buried in business logic:

```
SlaPolicy(
    version: str,                       # e.g. "sla-1.0" — recorded with every derivation
    approval_sla: timedelta,
    execution_start_sla: timedelta,
    completion_sla: timedelta,
    due_soon_fraction: float,           # when DUE_SOON begins
    escalation_steps: tuple[timedelta]   # L1, L2, ... past the deadline
)
```

**Start with ONE dimension of variation: defect severity** (`CRITICAL` / `MODERATE` / `MINOR` / `NONE` — the enum already exists and is already recorded at intake as `reported_severity`). An `EMERGENCY_REPAIR` job and a `ROUTINE_INSPECTION` cannot share an approval deadline, and severity is the field that already carries urgency.

**Do not** build variation by corridor, authority role, job type or maintenance category until there is a stated operational need. The resolution function `resolve_sla(job) -> SlaPolicy` is the seam those dimensions are added at later, without changing any caller.

### 11.2 The eleven questions, answered

| # | Question | Answer |
|---|---|---|
| 1 | Where does SLA configuration live? | **In code**, as a versioned frozen dataclass with a documented default. Not a database table (nothing writes it), not an env var (unreviewable, untestable) |
| 2 | Is it versioned? | **Yes** — `version` is mandatory and recorded with every derived obligation and every notification intent key |
| 3 | Is the applied SLA snapshot stored? | **For a derived obligation, no** — it is recomputed, and the version it was computed under is reported alongside it. **For a notification attempt, yes** — the attempt row records the `policy_version` that justified it, permanently. That is what makes a past escalation defensible |
| 4 | Can a policy change alter historical deadlines? | **It changes current derived state, and it must never alter a recorded attempt.** Tightening the SLA may newly mark existing jobs overdue — the honest consequence of deriving. What is immutable is the *record of what was sent under which version*. State that plainly rather than pretending derived deadlines are frozen |
| 5 | Weekends / holidays? | **Not handled. There is no calendar data in the repository (N15).** Slice 9 must be **wall-clock** and say so. Inventing an IR holiday calendar would be fabricated domain data — the same error the codebase refuses everywhere else |
| 6 | Wall-clock or operational time? | **Wall-clock for Slice 9.** Operational time needs a working-calendar and shift model that do not exist. Maintenance possession is largely a night activity, so operational time is genuinely the better long-term answer — it is deferred for lack of data, not because it is wrong |
| 7 | Authoritative timezone? | **Computation in UTC** (every event timestamp already is). **Display in Asia/Kolkata (+05:30)**, the local railway clock the whole horizon and timetable are stated in. The fixed-offset assumption is already documented as valid only because Asia/Kolkata has no DST (`horizon_anchor.py`) |
| 8 | Postponement? | Approval obligation **answered**; a new one begins only on re-proposal. The chosen date constrains scheduling, not a human (§5.6) |
| 9 | Release? | Execution obligation **ends, not breached**; job → `reported` (§6.5) |
| 10 | `NOT_COMPLETED`? | Completion obligation **answered** with a mandatory reason; job → `reported` (§6.5) |
| 11 | After replanning? | A **fresh** obligation with a **fresh** clock, anchored to the new placement event. Falls out of the derivation |

---

## 12. STATE-MACHINE INTERACTION

**Slice 9 requires NO change to the state machine.** Specifically: no new `JobStatus`, no change to `ALLOWED_TRANSITIONS` (`lifecycle.py:149`), no new `JobEventType`, no new `JobAction` for the write side.

| Brief concept | Existing representation |
|---|---|
| pending approval | `status == SCHEDULED` with an unanswered placement event |
| overdue approval | **derived** from that event's `occurred_at` + policy |
| pending execution | `status == NOTIFIED` with no `EXECUTION_STARTED` |
| pending completion evidence | `status == IN_PROGRESS` with an open `ExecutionRecord` |
| escalated | **derived** level; the *act* of notifying is a durable attempt row |
| notification sent | a durable attempt row — **not** a lifecycle state |

**Why no `ESCALATED` status.** It would be orthogonal to the real lifecycle (a job can be escalated in three different phases), it would multiply `ALLOWED_TRANSITIONS`, it would require a de-escalation transition, and it would let time mutate the lifecycle. Escalation is an *attribute of an obligation*, not a stage of maintenance work.

New **read** actions (`READ_JOB_OBLIGATIONS`, `READ_NOTIFICATIONS`) should be added to `JobAction` so the reads pass the same authorization seam as every other read — following the `READ_BLOCK_PROPOSAL` / `READ_JOB_EXECUTION` precedent.

---

## 13. RACE-CONDITION AND FAILURE ANALYSIS

Every dangerous case the brief lists, with its fail-safe semantics.

| # | Case | Behaviour under the recommended design |
|---|---|---|
| 1 | **Authority disappears after proposal** | Obligation stays derived-overdue and escalates by level. Job stays `scheduled` and committable. Nothing expires |
| 2 | **Authority changes** | No effect — obligations are addressed to `role`, not to an individual (N1). Once a directory exists, the obligation is unchanged; only addressing changes |
| 3 | **Actor is invalid** | Already refused at construction (`InvalidActorError`) and at the HTTP boundary (400/403). Notifications *from the system* are `SYSTEM` actors; escalation never fabricates a human |
| 4 | **Provider fails** | Attempt recorded as failure; retried if transient; **permanently failed notifications are surfaced in the accountability view** — never silently dropped (brief item 10) |
| 5 | **Duplicate notification** | Prevented by the deterministic `intent_key` UNIQUE constraint (§9.2) |
| 6 | **Notification succeeds, DB write fails** | **Possible duplicate send, never a lost obligation.** The intent remains derivable; the dedup key prevents a second send once the record lands. This direction is explicitly the safe one |
| 7 | **DB write succeeds, notification fails** | Attempt row says `FAILED`; retry or surface. The obligation is unaffected |
| 8 | **Process crashes mid-notification** | Next sweep recomputes the same intent. Worst case a duplicate attempt, caught by the dedup key |
| 9 | **Two workers trigger the same escalation** | One wins the UNIQUE INSERT; the other stops. No lock needed |
| 10 | **Job postponed immediately before escalation** | The postponement is a committed event; the obligation is **answered** and the intent is no longer derivable. If a notification had already been sent, it is a **stale but honest record** of what was true when sent — never retracted, never rewritten |
| 11 | **Proposal becomes stale** | `PROPOSAL_INVALIDATED` makes the obligation **MOOT, not breached** (§5.3) |
| 12 | **Committed block released** | Execution obligation ends, not breached (§6.5) |
| 13 | **Job becomes `COMPLETED` while an old escalation is pending** | `completed` is terminal; obligation becomes `NONE`. A pending intent stops being derivable. **An in-flight send may still land** — see 14 |
| 14 | **Old notification arrives after state changed** | **Unavoidable and must be designed for, not prevented.** Every notification must carry the `job_id`, the anchoring `event_id` and the moment it was raised, so a recipient (and an auditor) can see it describes a past state. A notification is a **statement about a moment**, never a command and never current truth |
| 15 | **Two concurrent sweeps** | Idempotent by construction: derivation is pure, and sends are deduped |
| 16 | **Clock skew / non-monotonic clock** | Canonical UTC timestamps are recorded by the writer. A backwards clock could make a deadline appear unmet; bounded by the same `MAX_OBSERVATION_FUTURE_SKEW` reasoning already applied to observations. Worth an explicit guard |

**The governing safety rule, restated:** a notification can only ever *inform*. It has no authority over the lifecycle, so a late, duplicated, failed or out-of-order notification can never corrupt maintenance state. This is what makes the whole design fail-safe.

---

## 14. API IMPACT

v1 is frozen. The drift guard compares per-operation response-status **sets** and per-schema **`required`** lists (§2.10).

### 14.1 Backward-compatible (safe)

| Change | Why compatible |
|---|---|
| **New read-only routes** under `/v1` | New paths are additive; the guard compares per-path shape. Requires a deliberate `docs/openapi-v1.json` regeneration, exactly as Slice 8 did |
| **Optional response fields** on existing responses | Properties are not compared; `required` is unchanged |
| **New `ErrorCode` members** | Additive; `test_error_code_vocabulary_is_frozen` is updated deliberately |
| Obligation data inside `block_candidate` metadata | Already explicitly outside the contract ("the free-form `block_candidate` dict... **not part of the contract**") |

### 14.2 Would require v2 (do not do in Slice 9)

- Making any new request field **required**
- Adding a **response status code** to an existing operation
- **Renaming** `/notify` or `JobStatus.NOTIFIED` (N14) — the right fix, but a breaking change
- Changing the shape of `JobResponse` / `JobHistoryResponse`

### 14.3 Recommended new endpoints — read-only, all of them

| Route | Returns |
|---|---|
| `GET /v1/obligations` | Derived obligations, filterable by `state` (`OVERDUE`, `DUE_SOON`, …), `obligation_type`, `role`; keyset-paginated like `GET /v1/jobs` |
| `GET /v1/jobs/{id}/obligation` | The one current obligation for one job, with clock start, due-at, state, level and policy version |
| `GET /v1/jobs/{id}/notifications` | Notification attempts for one job (only once delivery ships) |
| `GET /v1/optimization-runs/{run_id}` | Closes N12 — a run's request, result and provenance, currently unreadable over HTTP |

**All read-only, deliberately.** There must be no "acknowledge escalation" or "silence notification" mutation in Slice 9: acknowledgement without authentication is meaningless (anyone could acknowledge anything), and a silence switch is a way to hide an operational failure — the opposite of brief item 10.

---

## 15. PERSISTENCE IMPACT

### 15.1 Recommendation

| Slice 9 part | Persistence |
|---|---|
| Obligation / SLA / escalation derivation | **No schema change. None.** Pure functions over `job_events` + the job row |
| Accountability read APIs | **No schema change**, but **new read query methods** are required (N7) |
| Notification delivery (**deferred**) | **One** new append-only table when it ships, with `install_append_only_guards` and a UNIQUE `intent_key` |

### 15.2 The query capability that is genuinely missing (N7)

`JobHistoryRepository` can only read one job's or one run's events. To answer "every job whose approval is overdue" the derivation needs, in one pass:

1. candidate jobs by status (`scheduled`, `notified`, `in_progress`) — `list_by_status` exists; and
2. for each, the **one** event that anchors its clock.

Options, in preference order:

- **(a) A targeted read query** returning, per job, the latest anchoring event — a single `job_events` query filtered by `job_id IN (...)` and `event_type IN (...)`, joined in application code. No schema change, no index, correct at demo and pilot scale. **Recommended.**
- **(b) An index** on `job_events (event_type, occurred_at)` and/or `maintenance_jobs (status)` — a *performance* change, to be requested **with measurements**, exactly as Slice 8 deferred its `(track_id, created_at)` index.
- **(c) A denormalised `status_entered_at` column** (N8) — **rejected**: a second source of truth for a fact the history already holds, and the precise error `CommittedStateIntegrityError` exists to prevent.

### 15.3 Scale honesty

The derivation is O(active jobs) per sweep with a bounded number of events read per job. At demo and pilot scale (tens to low thousands of jobs) this is fine and should be stated as *measured at that scale only*. A deployment with hundreds of thousands of jobs needs (b), with measurements.

---

## 16. SECURITY / AUTHENTICATION BOUNDARY

**The boundary does not move in Slice 9.** Actor identity stays `DECLARED_UNVERIFIED`; `UnenforcedPolicy.enforcing` stays `False`; `IdentityAssurance` gains no `AUTHENTICATED` member.

### 16.1 What must be prevented, and how

| Threat | Control |
|---|---|
| **Self-generated fake escalation** | Escalation is **derived**, never caller-submitted. There is no endpoint that accepts "job X is overdue". A notification intent cannot be injected |
| **Fake authority identity** | Unchanged: `X-Actor-Id` is recorded as `DECLARED_UNVERIFIED`, never as a verified person. **Obligations are addressed to a role, so no notification can assert that a named individual failed** |
| **Actor spoofing treated as authenticated** | `assurance` is recorded on every event and must be **surfaced in every accountability view**. A dashboard that shows "AUTHORITY-017 approved this" without showing that the identity was unverified would be the single most dangerous UI in the system |
| **SYSTEM exposed as an external actor** | Unchanged: `api/deps.py:86` returns **403** for a caller-declared SYSTEM role; `Actor` forbids a human `actor_id` beginning with `SYSTEM` |
| **Role treated as authorization** | New read actions pass the existing seam, which **permits everything**. The report must not describe role-scoped obligations as access control |

### 16.2 The asymmetry that must be fixed (N9)

Execution and release require an identified human (`_require_identified_human`, `lifecycle.py:1872`); **approval, rejection and postponement do not** (§2.13). An approval can therefore be recorded against `UNIDENTIFIED` / assurance `NONE`.

This makes brief item 1 — *authority approval responsibility* — partly unanswerable: the system can say an approval happened but not always that *a person* made it. **Extending `_require_identified_human` to the three authority-decision paths is a small, well-precedented, in-scope Slice 9 change** (it is accountability, not RBAC, exactly as its docstring says). It is technically a behaviour change for unidentified callers, so it needs its own decision — see §22 Q3.

### 16.3 The honest statement Slice 9 must keep making

> Obligations are addressed to **roles**, not people. Identities are **declared, not verified**. No permission is enforced. An escalation records that *an obligation* is overdue — never that *a named individual* failed. Attributing blame to a person requires authentication, which does not exist.

---

## 17. GOVERNMENT-GRADE AUDITABILITY

**Engineering auditability only. No legal or compliance certification is claimed or implied.**

| Requirement | Today | After recommended Slice 9 |
|---|---|---|
| **WHO** | ✅ `actor` (id, role, kind, **assurance**) on every event | ✅ unchanged; assurance surfaced in every view |
| **WHAT** | ✅ 21 event types + before/after state | ✅ plus derived obligation state |
| **WHEN** | ✅ canonical UTC microsecond `occurred_at` | ✅ plus derived deadlines |
| **WHY** | ✅ mandatory `reason` on reject / postpone / release / not-completed | ✅ plus the policy rule that produced an escalation |
| **FOR WHICH JOB** | ✅ `job_id` on every event | ✅ |
| **UNDER WHICH PROPOSAL** | ✅ `optimization_run_id` + derived `proposal_id` + `BlockProposal.digest()` | ✅ |
| **UNDER WHICH SLA POLICY** | ❌ | ✅ `policy_version` reported with every obligation and **recorded permanently on every notification attempt** |
| **WITH WHICH ESCALATION LEVEL** | ❌ | ✅ derived level, reproducible from history + version + clock |
| **WITH WHICH NOTIFICATION ATTEMPT** | ❌ | ⚠️ **only when delivery ships** (deferred) |
| **WITH WHICH DELIVERY RESULT** | ❌ | ⚠️ **only when delivery ships**; and then as `ACCEPTED_BY_PROVIDER`, never `DELIVERED` or `READ` |

**Two structural strengths worth recording.** (1) Every fact above is already immutable at the SQL layer, from any connection. (2) `canonical_bytes()` means a future hash chain covers all of it without a different event definition — and because escalation is *derived* rather than written into `job_events`, it cannot pollute that chain.

**The reproducibility property that makes derivation auditable:** given the history, the policy version and a clock value, any auditor can recompute the exact obligation state the system reported at that moment. A stored deadline can be wrong and never noticed; a derived one cannot disagree with the history it comes from.

**The honest limit:** because escalation *state* is derived, there is no record of "at 09:00 this was L1" unless a notification attempt was recorded at 09:00. If a permanent record of every escalation transition is required, the notification attempt row is the only thing that provides it — a reason to bring delivery (option B) forward, not a reason to write escalation into `job_events`.

---

## 18. RECOMMENDED MINIMAL SLICE 9 SCOPE

**Four items. No schema change. No new `JobStatus`, `JobEventType` or write action. No delivery channel. No frontend.**

| # | Item | Files (expected) | Size |
|---|---|---|---|
| **9.1** | **`SlaPolicy`** — versioned frozen dataclass, severity-resolved, injectable on `JobService`, wall-clock, documented defaults | new `jobs/sla_policy.py` | Small |
| **9.2** | **Obligation derivation** — a pure `JobObligation` read model built from the job row + its anchoring event + policy + clock; `MOOT` handled; mirrors `proposal.py` / `execution.py` | new `jobs/obligations.py` | Medium |
| **9.3** | **Accountability query service + read APIs** — the targeted read query of §15.2(a), plus `GET /v1/obligations`, `GET /v1/jobs/{id}/obligation`, `GET /v1/optimization-runs/{run_id}`; keyset-paginated; deliberate OpenAPI regeneration | `repository.py` (read only), `history.py` (read only), `router.py`, `models.py`, `errors.py` | Medium |
| **9.4** | **Notification intent derivation + `RecordingChannel`** — derived intents with deterministic `intent_key`, the `NotificationChannel` Protocol, and **one** no-I/O recording implementation. **No provider, no SMS, no email, no push** | new `jobs/notifications.py` | Medium |

**Two small adjacent fixes, worth taking while adjacent:**
- Finish **clock injection** on the remaining write paths (N11) — required for deterministic SLA tests.
- Extend **`_require_identified_human`** to approve / reject / postpone (N9, §16.2) — subject to §22 Q3.

**Explicitly NOT in Slice 9:** any real delivery channel, the `notification_attempts` table, a runner/scheduler process, acknowledgement endpoints, an individual recipient directory, operational-time or holiday calendars, planned-window-anchored execution SLA.

**Model recommendation: Sonnet** for 9.1–9.3 (mechanical, guarded by 1267 tests). **Escalate to Opus** if the obligation derivation starts wanting a `status_entered_at` column or a new event type — that is the signal the slice has outgrown its boundary, and the correct response is to stop and re-approve.

---

## 19. EXPLICITLY DEFERRED

| Deferred item | Blocked on |
|---|---|
| **Individual recipient directory** (users, contacts, job→person assignment) | **Authentication / RBAC slice** — it must own identity (N1) |
| **Any real channel** (SMS, email, push, in-app, WhatsApp) | Recipient model + credentials + rate limits + cost + data-protection review |
| **`notification_attempts` table** | Ships with the first real channel (§9.4, option B) |
| **A runner / scheduler process** (N6) | Deployment decision: in-process loop, external cron, or on-read evaluation (§22 Q2) |
| **Acknowledge / silence / snooze endpoints** | Authentication — unauthenticated acknowledgement is meaningless |
| **Planned-window-anchored execution SLA** | A **dynamic `horizon_start`**, which needs timetable data covering the live window (N10, §2.8) |
| **Operational time, working calendars, holidays, shifts** | Authorised IR calendar data (N15) |
| **Escalation as an assignable operational task** | Recipient model (N1) |
| **Per-escalation-transition permanent record** | Ships with notification attempts (§17) |
| **Renaming `/notify` / `NOTIFIED`** | API v2 (N14) |
| **Indexes on `job_events(event_type, occurred_at)`, `maintenance_jobs(status)`** | Measured need at pilot scale (§15.2b) |
| **Hash-chained audit** | Security slice; `canonical_bytes()` is ready |
| **Severity override, crew constraints, live Railway data** | Prior slices' own deferrals; unchanged |

---

## 20. ACCEPTANCE CRITERIA (for the eventual implementation)

1. All **1267** existing tests pass; **52** subtests pass; no test deleted, no assertion weakened.
2. `git diff --stat` touches **no** file under `backend/app/optimizer/` or `contracts/`.
3. `JobStatus`, `ALLOWED_TRANSITIONS`, `JobEventType` and `JobAction`'s **write** actions are byte-identical to baseline.
4. **No `CREATE TABLE`, `ALTER TABLE` or `CREATE INDEX`** anywhere in the diff.
5. `jobs.db` SHA-256 unchanged: `245fd9ff…52386`.
6. **Proven by test: no derived obligation, escalation or notification changes any job's status, placement, committed state or history.** A job left unanswered past every escalation level is still `scheduled` and still committable.
7. **Proven by test: a `MOOT` obligation is never reported as overdue** — a withdrawn or invalidated proposal never blames an authority.
8. **Proven by test: derivation is deterministic** — identical (history, policy, clock) yields identical obligations, independent of row or event ordering.
9. **Proven by test: every boundary** — exactly at the SLA, one microsecond before, one microsecond after, at each escalation step.
10. **Proven by test: every terminating event** (commit, reject, postpone, release, not-completed, invalidate, complete) ends its obligation with the correct outcome, and re-proposal starts a **fresh** clock.
11. **Proven by test: an intent key is stable across sweeps and different after re-proposal**; concurrent evaluation cannot double-raise.
12. Every new `JobCreateRequest`-style field (if any) is **optional**; `required` lists unchanged; a request valid before Slice 9 behaves identically.
13. `docs/openapi-v1.json` and `docs/api-contract-v1.md` regenerated **deliberately**; drift test passes against the new snapshot; every new `ErrorCode` in the frozen vocabulary test.
14. Provenance unchanged: all four axes still `SYNTHETIC`, `effective` still `SYNTHETIC`.
15. **No claim of authentication, delivery, receipt, geofencing or live data.** Every accountability view surfaces `assurance`. The contract doc still states plainly that notifications are not delivered.
16. Protected files, `jobs.db` and `stash@{0}` untouched.

---

## 21. RISKS

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | **Escalation to a role nobody owns** — role-addressed obligations with no directory may mean nobody acts, while the dashboard shows an escalation | **High** | State the limitation explicitly; the accountability view is read by a named operator in the demo. Directory is the auth slice's job |
| R2 | **A dashboard implying verified identity** — showing "AUTHORITY-017 approved" invites reading it as authenticated | **High** | `assurance` mandatory in every view and every response; acceptance criterion 15 |
| R3 | **Derived deadlines shift when policy changes** (§11.2 Q4) | Medium | Report `policy_version` with every obligation; record it permanently on any notification; document plainly rather than faking frozen deadlines |
| R4 | **Alert fatigue** — an over-eager SLA floods the queue and trains operators to ignore it | Medium | Conservative defaults; severity-resolved; `reported` jobs are not obligations (§7.2) |
| R5 | **N+1 read cost** of the accountability view at scale | Medium | §15.2(a) targeted query; state that it is measured at demo/pilot scale only; index later with measurements |
| R6 | **Someone "fixes" the fixed `horizon_start`** to make planned-window SLA work and trips the timetable coverage gate | Medium | N10 documented as a data dependency; the gate fails closed by design |
| R7 | **Scope creep into a task/workflow system** (assignment, acknowledgement, ownership) | Medium | §10.1 records it as the deferred long-term shape, blocked on N1 |
| R8 | **Delivery claimed before it exists** — a `RecordingChannel` described as "notifications working" | **High** | Vocabulary discipline: `ACCEPTED_BY_PROVIDER`, never `DELIVERED`/`READ`; §8.1 |
| R9 | **A sweep process mutating state** if a future maintainer adds "auto-cancel after N days" | **High** | §10.3 as an invariant with a test (acceptance criterion 6) |
| R10 | **Clock skew** making deadlines appear met or missed | Low-Medium | UTC throughout; explicit guard; injectable clock (N11) |
| R11 | **Escalation state has no permanent record** while delivery is deferred (§17) | Low-Medium | Documented limit; resolved when attempts ship |

---

## 22. OPEN QUESTIONS (decisions required before implementation)

**Q1 — Recipient model: roles now, or individuals first?** (blocks the most)
Slice 9 as recommended addresses obligations to `role = AUTHORITY` / `WORKER`. The alternative is to build a minimal user/authority directory first — which means deciding identity before authentication exists, and risks building a second identity model the auth slice must then replace. **Recommendation: roles now.** Confirm.

**Q2 — Evaluation trigger: on-read, in-process loop, or external scheduler?** (N6)
Obligations can be derived **on read** (no runner at all — the accountability endpoint computes fresh), or by a background sweep. On-read is simpler, has no new process, cannot drift, and is sufficient while nothing is actually delivered. A sweep is only needed once something must be *sent* without a reader. **Recommendation: on-read for Slice 9.** Confirm.

**Q3 — Extend `_require_identified_human` to approve / reject / postpone?** (N9)
It closes a real accountability hole, but it is a behaviour change: an unidentified caller that can approve today would get a 400. Some existing tests may construct such calls. **Recommendation: yes, with the test impact measured first.** Confirm.

**Q4 — SLA default values.** What are the operationally defensible numbers for approval, execution start and completion, per severity? This is **domain input, not an engineering choice** — placeholder values must be labelled `ASSUMED`, never presented as IR practice (hybrid data rule).

**Q5 — Escalation levels.** How many, at what multiples, and does each target a different (future) role? Two levels are enough to prove the model.

**Q6 — Is `COMMITTED_BLOCK_CONFLICT` an escalation trigger?** A job whose approved block the optimizer could not honour arguably needs attention more urgently than a late approval. Recommended as `ATTENTION_REQUIRED` (§7.2); confirm.

**Q7 — Should `REPLANNING_PENDING` exist at all?** A `reported` job awaiting the next optimization run is arguably a *system* obligation, and including it may add noise. Recommended: include, but only when a prior withdrawal event exists (§7.2).

---

## 23. RECOMMENDED IMPLEMENTATION ORDER

1. **Decide Q1, Q2, Q3** — they change what gets built. Nothing starts before these.
2. **`SlaPolicy` (9.1)** — pure, no dependencies, testable alone. Land it with its boundary tests.
3. **Finish clock injection** (N11) — needed to test everything after this deterministically.
4. **Obligation derivation (9.2)** — pure functions over history. Land with the full boundary matrix (acceptance criteria 6–10) **before any API surface exists**, so the semantics are proven independently of HTTP.
5. **The targeted read query** (§15.2a) — read-only repository/history methods, no schema change.
6. **Read APIs (9.3)** — then regenerate `docs/openapi-v1.json` and `docs/api-contract-v1.md` deliberately, and update the frozen error-code vocabulary.
7. **`_require_identified_human` extension** (N9) — separately, after measuring test impact, so it can be reverted independently.
8. **Notification intent + `RecordingChannel` (9.4)** — last, because it is the part that most invites over-claiming. Ship it only if 1–7 land cleanly.
9. **Full verification** — focused tests, lifecycle/accountability regression, Slice 7 contract tests, full suite, single known warning, `jobs.db` hash, no migration, protected files, stash, complete diff review.
10. **STOP.** Delivery, the attempts table, a runner and the recipient directory are each their own approved slice.

**Boundary in one line:** Slice 9 should make the system able to *say* who owes what and by when — derived from history it already has, addressed to roles it can actually name — without inventing a recipient, a lifecycle state, a stored deadline, or a delivery it cannot perform.

---

## 24. VERDICT: AMBER

**Not GREEN**, and deliberately not — the design is possible, which is not the bar. Four decisions must be made by the user before implementation can safely begin:

1. **Recipient model (Q1).** There is no way to name a responsible party (N1). Role-addressed obligations are the recommended scoping, but that is a **product decision about what "responsibility" means** in this system, not an implementation detail.
2. **Evaluation trigger (Q2).** Nothing in the system can act on the passage of time (N6). On-read derivation avoids a new process entirely; a sweep is a deployment commitment.
3. **Accountability-versus-identity (Q3).** Approval paths currently accept an unidentified actor while execution paths do not (N9). Closing it is right and is a behaviour change.
4. **SLA values (Q4).** These are domain numbers. Engineering must not invent them and present them as railway practice.

**Not RED** — there is no architectural blocker. Every timestamp, actor, reason and state transition the model needs is already recorded immutably; obligations and intents are genuinely derivable with **no schema change**; the one place durable state is required (delivery) is cleanly separable and correctly deferred; and the fixed `horizon_start` constrains **one variant** of the execution SLA (planned-window-anchored) while leaving the capability fully achievable on a commit-time anchor.

**Recommended next step:** answer Q1, Q2, Q3 and Q4, then approve Slice 9 scope 9.1–9.4 as written in §18.

---

**No implementation may begin until this report is reviewed and explicitly approved.**
