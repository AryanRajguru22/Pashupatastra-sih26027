# Slice 5 — Approved Block → Field Execution → Evidence → Completion / Not Completed

**Architecture contract. Read-only design task: no production code, test, schema, `jobs.db`, stash or protected file was modified to produce this report.**

Baseline: `main` @ `50ddcbe` — re-verified for this report: `python -m pytest backend/tests` → **754 passed, 52 subtests, 1 warning**; `jobs.db` mtime/size unchanged before and after.

---

## 1. EXECUTIVE VERDICT

**DESIGN READY** — with four explicitly scoped conditions that the implementation must honour:

1. **One narrow change to a Slice 1 invariant.** A committed job must be able to leave commitment when field work is *not completed* (otherwise it can never be replanned: `optimization_snapshot` pins every committed job, `service.py:1622`, and the solver hard-pins it to `committed_start_minute`/`committed_end_minute`, `solver.py:289-305`). This is done by a permission token carried **on the `JobMutation`** (§3.4), never by a status-pair allowance. A status-pair edit would silently open `repository.update_status(job_id, "reported")` — a history-less un-pin path that no existing test exercises.
2. **Direct `notified → completed` is closed.** Completion without after-work evidence must be impossible, so `plan_completion`, `JobService.complete` and `POST /jobs/{job_id}/complete` are retired. This touches a known, enumerated set of existing tests (§11.3), which are pre-authorized changes.
3. **Three route-inventory tests** hard-code the mutating route set and must be updated deliberately (§11.3).
4. **No new table, no migration.** An execution is derived from append-only `job_events` exactly as a `BlockProposal` already is (`proposal.py:1-35`). This keeps `Plan = rows → (mutations, events)` unchanged, so state and history still commit or roll back together.

The three committed-state guard tests were checked against the proposed guard and remain valid (§3.5).

---

## 2. CURRENT ARCHITECTURE SUMMARY

### 2.1 State
- `JobStatus` (`models.py:21`): `reported`, `scheduled`, `notified`, `completed`. No `IN_PROGRESS`, no `NOT_COMPLETED`.
- `ALLOWED_TRANSITIONS` (`lifecycle.py:83`): `reported→{reported,scheduled}`, `scheduled→{scheduled,reported,notified}`, `notified→{notified,completed}`, `completed→∅`.
- `COMMITTED_STATUSES = (notified,)`, `TERMINAL_STATUSES = (completed,)`. Both are consumed as tuples by `service.optimization_snapshot` (`service.py:1622`), `proposal._derive_is_committed` (`proposal.py:317`), and `repository.list_active`.
- The placement lives in two places kept consistent: `schedule_start/end_minute` columns and `block_candidate.metadata.{committed_start_minute, committed_end_minute, proposal_run_id}`.

### 2.2 Write path (Slice 1)
- Pure planners in `lifecycle.py` return `(mutations, events)`; `JobRepository.mutate_jobs` (`repository.py:239`) runs them inside **one `BEGIN IMMEDIATE` transaction**: read rows → plan → `validate_mutation` → write rows → `append_events`.
- `validate_mutation` = `protect_committed_and_terminal_state` + transition graph + placement sanity (`lifecycle.py:342`).
- `protect_committed_and_terminal_state` (`lifecycle.py:276`) is **also** called directly by pre-Slice-1 primitives `update_status` and `update_schedule` (`repository.py:459, 575`), which build no `JobMutation` and write no history.
- `JobService._transition` (`service.py:1238`) holds `lifecycle_lock` (in-process `RLock`) and records `TRANSITION_REJECTED` for refused transitions. Optimization holds the same lock for snapshot→solve→write and re-checks statuses inside its transaction (`ConcurrentJobModificationError`).

### 2.3 History / audit
- `job_events` (`history.py`) is append-only via SQL triggers (`persistence/append_only.py`), indexed by `(job_id, sequence)` and `optimization_run_id`. Events carry actor, reason, `optimization_run_id`, `before_state`/`after_state` (`JobStateSnapshot`, fixed 6 fields), plain-JSON metadata, and have deterministic `canonical_bytes()`.
- `optimization_runs` (`audit/repository.py`) is append-only, one row per run (`RUN-…` id).

### 2.4 Proposal / authority review (Slices 2–3)
- `BlockProposal` is **derived**, never stored: job row + the placement event for `proposal_run_id` + the run record. `proposal_id = PROP-{run_id}-{job_id}`. Served only for status `scheduled`.
- Approve = `notify` with mandatory `expected_proposal_run_id` (no separate permission). Reject/postpone use `_withdrawn()` (clear placement, `reported`), mandatory reason and run id; postpone raises `earliest_start_minute` and widens `latest_end_minute` to `max(current, horizon_minutes)` (`lifecycle.py:1055-1066`).

### 2.5 Horizon (Slice 4)
- Minutes are relative to the fixed `DEFAULT_HORIZON_START = 2026-09-10T00:00:00+05:30`, width `JobService.horizon_minutes` (2880). The only conversion from calendar time is `horizon_anchor.horizon_relative_minutes`. There is no rolling "now".

### 2.6 Identity / authorization
- `Actor` (id, role, derived kind, assurance). HTTP actor from `X-Actor-Id`/`X-Actor-Role`; SYSTEM role refused (403). `UnenforcedPolicy` permits everything; every service method calls `authorize` first.

### 2.7 What is missing for Slice 5
Nothing models field work between commitment and completion; completion requires no evidence; a committed job can never be released for replanning.

---

## 3. STATE MACHINE DECISION

### 3.1 States

| status | meaning | committed? | terminal? | optimization |
|---|---|---|---|---|
| `reported` | no current proposal; eligible | no | no | free candidate |
| `scheduled` | current uncommitted proposal | no | no | free candidate |
| `notified` | approved block, committed, work not started | **yes** | no | pinned |
| **`in_progress`** (NEW) | approved block, work started, open execution | **yes** | no | pinned |
| `completed` | work completed with evidence | — | **yes** | excluded |

`COMMITTED_STATUSES = (notified, in_progress)`; `TERMINAL_STATUSES = (completed,)` unchanged.

### 3.2 Q1 — Is a real `IN_PROGRESS` status needed? **Yes.**
A derived alternative (job stays `notified`, "in progress" = open execution) was evaluated and rejected, for reasons in the code rather than convenience:
- **The pure plan must see it.** `Plan` receives only rows; duplicate-start and complete-before-start must be decided inside the `BEGIN IMMEDIATE` transaction from the row. A derived design still needs a row marker (e.g. `execution_id` in block metadata) — i.e. a hidden status that the transition graph cannot govern.
- **Canonical history must show it.** `JobStateSnapshot` is the audit record of a transition. Under the derived design `EXECUTION_STARTED` would read `notified → notified`.
- **Pinning must continue while work is physically on the track.** Adding `in_progress` to `COMMITTED_STATUSES` makes every existing tuple consumer (`service.py:1622`, `proposal.py:317`, `_plan_placement`/`_plan_refusal` committed branches, `plan_optimization_failure`) pin it with **no edit** to those sites.
- Blast radius is therefore confined to `lifecycle.py` (graph, guard, placement check), `JobStatus`, and `_no_proposal_reason`.

### 3.3 Q2 — How is NOT_COMPLETED represented? **As an execution outcome, not a status.**
The job returns to **`reported`**; the outcome is the immutable `EXECUTION_NOT_COMPLETED` event. Justification: a `not_completed` status would have to mean exactly "no current proposal, eligible for optimization" — which is `reported`. This is the same precedent as reject and postpone (both `scheduled → reported`, distinguished only by their event). `last_refusal_reason` is set to the reason, as `plan_reject` does.

### 3.4 Transitions

```
reported    → reported | scheduled
scheduled   → scheduled | reported | notified
notified    → notified | in_progress                       (notified→completed REMOVED)
in_progress → in_progress | completed | reported
completed   → ∅
```

| transition | plan | event | requires |
|---|---|---|---|
| `notified → in_progress` | `plan_execution_start` | `EXECUTION_STARTED` | token START; `expected_proposal_run_id == proposal_run_id_of(job)`; ≥1 before-work evidence; identified human actor |
| `in_progress → completed` | `plan_execution_complete` | `EXECUTION_COMPLETED` | token COMPLETE; `execution_id == row's execution_id`; ≥1 after-work evidence; `actual_end_at`; identified human actor |
| `in_progress → reported` | `plan_execution_not_completed` | `EXECUTION_NOT_COMPLETED` | token NOT_COMPLETED; `execution_id` match; reason; `actual_end_at`; identified human actor |
| `notified → notified`, `in_progress → in_progress` | optimization | `COMMITTED_BLOCK_PRESERVED` / `…_CONFLICT` / `OPTIMIZATION_FAILED` | unchanged |

Explicitly **not** allowed: `notified → reported` (no "cancel an approved block" in Slice 5 — see §13), failure before start, completion from `notified`.

### 3.5 The guard change (the one Slice 1 edit)

`JobMutation` gains one trailing field `execution: Optional[ExecutionTransition] = None`, where
`ExecutionTransition = (kind ∈ {START, COMPLETE, NOT_COMPLETED}, execution_id)`. `JobMutation.unchanged()` leaves it `None`; only the three execution plans set it.

`protect_committed_and_terminal_state(current, new_status, new_block, new_schedule, *, execution=None)` — order is load-bearing:

1. `current.status` terminal → `TerminalJobError` (**unconditional, first**; a token releases *committed*, never *terminal*).
2. `assert_committed_state_consistent([current])` on the **stored** row (unchanged).
3. Resulting committed state must be consistent (unchanged; now also covers `in_progress`).
4. **Execution gate (new):**
   - entering `in_progress` requires `execution.kind == START` and `current.status == notified`;
   - entering `completed` requires `execution.kind == COMPLETE` and `current.status == in_progress`;
   - `in_progress → reported` requires `execution.kind == NOT_COMPLETED`;
   - a token present on any other transition → `CommittedJobError` (token misuse).
5. Non-committed current status → return.
6. Committed current status: allowed new statuses are `notified→{notified, in_progress}`, `in_progress→{in_progress, completed, reported*}`. For every case except `reported*`, block must stay `COMMITTED`/`is_committed` and the placement must be byte-identical (existing checks). For `reported*` (release), the new block must be `PLANNED`, `is_committed=False`, placement `(None, None)`.

`update_status` / `update_schedule` pass no token, so they **structurally cannot** start, complete, or release — independent of any test.

`validate_mutation` passes `mutation.execution` through, and its placement check at `lifecycle.py:369` becomes `(SCHEDULED,) + COMMITTED_STATUSES`. `_withdrawn()` clears both schedule columns, so the release mutation satisfies the "`reported` carries no window" rule at `lifecycle.py:363`.

`mutate_jobs` adds one cross-check after `validate_mutation`: a mutation carrying a token must be accompanied, in the same plan's events, by **exactly one** event for that job of the matching type whose `metadata.execution_id` equals the token's id (pure helper `validate_execution_events`). A buggy plan cannot set a token without its evidence-bearing event.

`committed_state_problems` gains two integrity findings: `in_progress` without `block.metadata.execution_id`; `notified` carrying `execution_id`.

**Verification against the existing guard tests:**
- `test_committed_block_does_not_regress_to_scheduled_on_reoptimization` — optimization path; committed branches keep status; never requests demotion. **Unaffected.**
- `test_every_kind_of_inconsistent_committed_row_blocks_every_write_path` — integrity check (step 2) runs on the stored row before any new-status logic. `update_status(job,"completed")` still raises `CommittedStateIntegrityError`. The `service.complete` attempt is replaced by the execution calls, which call `assert_committed_state_consistent([job])` **as their first statement** so a corrupted row surfaces as integrity (not `InvalidTransitionError`). **Holds.**
- `test_committed_placement_cannot_be_reassigned_through_any_write_path` — its demotions target `scheduled` / `notified+SCHEDULED`; still refused. **Holds**, and gains `update_status(job_id, "reported")` → `CommittedJobError`.

### 3.6 Terminal state
`completed` is terminal. The block stays `COMMITTED` and keeps `execution_id`; the row is never written again (step 1); `list_active` excludes it; `mutate_jobs(reject_terminal=True)` refuses it in optimization.

---

## 4. EXECUTION MODEL

### 4.1 Q3/Q4 — Cardinality
- **At most one execution per committed proposal generation** (`optimization_run_id`). Start requires `notified`, which is left on start and can only be re-entered through a *new* approval of a *new* run.
- **Multiple attempts per job** = multiple executions, each tied to a different run: `attempt_number = 1 + count(prior EXECUTION_STARTED for job)`.

### 4.2 Storage: derived, not a table
An `ExecutionRecord` is **derived** from the job's own `job_events` (new module `backend/app/jobs/execution.py`, `build_execution_records(job, events)`), mirroring `build_block_proposal`. Rationale:
- The events must be written anyway (audit). A `job_executions` table would copy their content into a second source of truth — the exact dual-write problem `proposal.py:21-26` documents avoiding.
- It keeps `Plan` unchanged; a third table would need `Plan` to return a third collection and `mutate_jobs` to write it.
- Append-only is already enforced by triggers; `install_append_only_guards` on a mutable execution table would have aborted the start→complete update anyway.
- Canonical bytes / future hash chain cover executions and evidence for free.

The only row-level marker is `block_candidate.metadata.execution_id` (key constant `EXECUTION_ID_KEY`), set on START, removed on NOT_COMPLETED (added to `_without_placement`'s popped keys), kept on COMPLETE. It follows the `proposal_run_id` precedent and lets pure plans do stale checks without I/O. `JobStateSnapshot` is **not** changed (its `from_dict` reads fixed keys; changing it would break reading existing events).

### 4.3 ExecutionRecord fields (derived)

| field | source |
|---|---|
| `execution_id` | `EXE-<uuid hex upper>`, minted by the service, in all three event metadata |
| `job_id` | event |
| `attempt_number` | computed at start, in `EXECUTION_STARTED` metadata |
| `optimization_run_id` | `EXECUTION_STARTED.optimization_run_id` = `proposal_run_id_of(job)` at start |
| `proposal_id` | `PROP-{run_id}-{job_id}` (same formula as `proposal.py:359`) |
| `track_id`, `section_id` | job / block at start |
| `planned_start_minute`, `planned_end_minute` | the committed placement at start (copied into metadata, never written back) |
| `committed_block_digest` | SHA-256 over the same identity/placement field set as `BlockProposal.digest()` (shared helper, same `canonical_json`) |
| `status` | `STARTED` / `COMPLETED` / `NOT_COMPLETED` — latest event for that `execution_id` |
| `started_by`, `started_recorded_at` | `EXECUTION_STARTED` actor / `occurred_at` |
| `actual_start_at`, `actual_start_minute` | caller observation (normalized UTC canonical format) / horizon-relative floor minute |
| `before_work_evidence[]` | `EXECUTION_STARTED` metadata |
| `ended_by`, `ended_recorded_at` | outcome event actor / `occurred_at` |
| `actual_end_at`, `actual_end_minute` | caller observation / horizon-relative ceil minute |
| `after_work_evidence[]` | `EXECUTION_COMPLETED` metadata |
| `not_completed_reason`, `failure_evidence[]` | `EXECUTION_NOT_COMPLETED` reason / metadata |
| `deviations` | `{started_before_planned_start, ended_after_planned_end}` booleans, recorded not enforced |

No `created_at`/`updated_at`: the start and outcome events' `occurred_at` are those facts, immutably.

### 4.4 Relationships
```
Job (maintenance_jobs row, status, current block)
 └─ Generation g = optimization_run_id R_g   (optimization_runs row, BLOCK_PROPOSED/REPROPOSED event)
     └─ Approval   BLOCK_COMMITTED (optimization_run_id = R_g)
         └─ Execution E_g   EXECUTION_STARTED (optimization_run_id = R_g, execution_id = E_g)
             └─ Outcome     EXECUTION_COMPLETED | EXECUTION_NOT_COMPLETED (same ids)
```
All execution events carry `optimization_run_id = R_g`, so the existing `job_events_by_run` index already groups a generation's proposal, approval, execution and outcome.

### 4.5 Immutability of the approved plan
Execution plans never assign `schedule_*`, `track`, `section`, `earliest/latest`, `proposal_run_id` or `committed_*` for START/COMPLETE — enforced structurally by guard step 6 (placement byte-identical, block stays COMMITTED). Actual times are metadata only. At COMPLETE/NOT_COMPLETED the plan recomputes the committed-block digest from the row and refuses (`CommittedStateIntegrityError`) if it differs from the start digest.

### 4.6 Actor requirement
All three execution transitions refuse `UNIDENTIFIED` and `SYSTEM` actors inside the plan (`InvalidTransitionError`: "an identified human actor is required"). This is an accountability invariant ("completion actor exists"), not RBAC: no role is checked.

---

## 5. EVIDENCE MODEL

### 5.1 Input (`EvidenceInput`, pydantic, `extra="forbid"`)
| field | rule |
|---|---|
| `evidence_reference` | str 1–200, stripped non-blank; opaque pointer to an external system (same shape as `JobCreateRequest.evidence_reference`) |
| `evidence_kind` | enum `PHOTO`, `VIDEO`, `DOCUMENT`, `MEASUREMENT` |
| `captured_at` | ISO-8601 with explicit offset; naive rejected |
| `latitude`, `longitude` | optional floats in [-90,90] / [-180,180]; both or neither |
| `note` | optional str ≤ 500 |

No free-form metadata dict (unbounded, non-canonical risk) — `note` is enough for Slice 5.

### 5.2 Stored (inside the transition event's metadata)
`evidence_id` (`EVD-<uuid>`, server-minted), `phase` (`BEFORE_WORK` / `AFTER_WORK` / `NOT_COMPLETED`), the five input fields (timestamps normalized to the canonical event format), `recorded_by` = the event actor (not duplicated), `recorded_at` = event `occurred_at` (not duplicated).

### 5.3 Rules
- START: 1–10 `BEFORE_WORK` items. **No before-work evidence → no start.** Each `captured_at ≤ actual_start_at`.
- COMPLETE: 1–10 `AFTER_WORK` items. **No after-work evidence → no completion.** Each `captured_at ≥ actual_start_at` of the execution. No `evidence_reference` may equal a before-work reference of the same execution (a "before" photo re-submitted as "after" proves nothing).
- NOT_COMPLETED: 0–10 items, optional.
- Within one request, `evidence_reference` values must be unique.
- Every `captured_at`, `actual_start_at`, `actual_end_at` must be `≤ clock() + 5 min` (injectable `JobService.clock`, default UTC now, so tests are clock-independent). `actual_end_at ≥ actual_start_at`.
- Validated twice: request model (422/400) and again inside the pure plan (defence in depth for in-process callers).

### 5.4 Immutability
Evidence exists only inside `job_events` rows → UPDATE/DELETE abort at the SQL layer. There is no edit path.

### 5.5 Correction (designed, deferred)
Correction = a new `EVIDENCE_RECORDED` event with `supersedes_evidence_id`, allowed only while `in_progress`, previous record preserved. It never gates completion. **Not in Slice 5 core** (§12 Step 6, optional): because evidence is submitted inline with the transition it gates, no core invariant depends on it, and deferring it removes the evidence-vs-completion race entirely. Post-completion correction is an open question (§13).

---

## 6. REPLANNING MODEL

### 6.1 Q6 — What makes a job replannable
Exactly one thing: a committed `EXECUTION_NOT_COMPLETED` transition (`in_progress → reported`). Nothing else releases commitment.

### 6.2 `plan_execution_not_completed` — what it writes
Built on `_withdrawn()` (as reject/postpone are):
- `status = reported`, `schedule_* = None`, `last_refusal_reason = reason`, `updated_at = at`;
- block: `PLANNED`, `is_committed=False`, `committed_start/end`, `proposal_run_id`, `execution_id` removed;
- `earliest_start_minute = max(stored earliest_start_minute, planned_end_minute, actual_end_minute)`;
- `latest_end_minute = max(stored latest_end_minute, horizon_minutes)` — the Slice 4 Step 3 widening; without it the job reproduces the `window_infeasible` defect (`lifecycle.py:993-1007`). A shared `_raise_not_before(block, minute, horizon_minutes)` helper is extracted and `plan_postpone` is refactored onto it with **byte-identical** behaviour (postpone *assigns* not-before; failure takes the `max` — the helper takes the mode explicitly).
- Event `EXECUTION_NOT_COMPLETED`: `optimization_run_id = released run`, `reason`, metadata `{execution_id, actual_end_at, actual_end_minute, released_start_minute, released_end_minute, released_proposal_run_id, committed_block_digest, previous_earliest_start_minute, not_before_minute, original_latest_end_minute, widened_latest_end_minute, failure_evidence[]}`.

### 6.3 Q7 — `earliest_start_minute`
- `actual_end_minute` = ceil minute of `actual_end_at` via `horizon_relative_minutes(local_date, clock_minutes, 0, OPTIMIZATION_HORIZON_START)` (+1 if seconds/microseconds > 0). The one authoritative conversion is reused, not re-derived.
- `planned_end_minute` is included deliberately: the released block's window is not re-offered; a new proposal starts strictly after it.
- `max` with the stored value: a prior postponement's not-before is never lowered.
- **Never clamped, and the failure report is never refused because of the horizon.** If `not_before ≥ horizon_minutes`, the next optimization refuses the job with the solver's own `window_infeasible` reason (`OPTIMIZATION_REFUSED`). That is honest: the planning horizon genuinely does not cover time after the failure. Fabricating an earlier proposal would invent chronology.

### 6.4 Q8 — Protecting the old committed block
The old block is never mutated into the new one. The release clears the *current* placement; the *historical* placement is preserved immutably in `BLOCK_PROPOSED`/`BLOCK_COMMITTED` (run R1), `EXECUTION_STARTED` (planned minutes + digest) and `EXECUTION_NOT_COMPLETED` (released minutes), plus the R1 `optimization_runs` row. Nothing is deleted.

### 6.5 Q9 — New generation
- Next `POST /corridors/{id}/optimize-jobs` mints `R2 = new_run_id()`; the job (now `reported`) receives `BLOCK_PROPOSED` with `optimization_run_id=R2`; `proposal_id = PROP-R2-{job}`.
- Approval requires `expected_proposal_run_id = R2`; an approval or start naming R1 is stale (409).
- Start creates `E2` with `attempt_number = 2`, `optimization_run_id = R2`.
- **Scope of the claim:** every generation is *provable from history* and every *executed* generation is listed by `GET /jobs/{job_id}/execution` with its run id and planned placement. A non-executed historical generation (rejected/postponed/withdrawn) is visible in `/history` but is **not** rebuildable as a `BlockProposal` object — `build_block_proposal` reads the current row and serves only `scheduled`. No historical-proposal read path is added in Slice 5.

### 6.6 Stale protection interplay
- `proposal_run_id` is removed on release, so no path can commit/start R1 again.
- A late COMPLETE for E1 after release sees `status=reported` → refused; after R2 approval sees `notified` → refused; after E2 start sees `execution_id=E2` → `StaleExecutionError`.

---

## 7. AUTHORIZATION MODEL

Following the `COMMIT_BLOCK`/approve precedent (`authorization.py:45-50`: one domain transition, one action):

| action | status | used by |
|---|---|---|
| `START_EXECUTION` | **new** | `start_execution` |
| `COMPLETE_JOB` | **reused** (it *is* the terminal completion transition) | `complete_execution` |
| `REPORT_EXECUTION_NOT_COMPLETED` | **new** | `report_execution_not_completed` |
| `READ_JOB_EXECUTION` | **new** (precedent: `READ_BLOCK_PROPOSAL` for a derived read) | `job_executions` |

No `RECORD_EVIDENCE` action: evidence is never recorded on its own in Slice 5 core. (It is added with the deferred correction step.) Policy stays `UnenforcedPolicy`; `authorize` is called before any read or write; a denying policy test proves no state/event change per action. The module docstring's role note gains: WORKER starts / completes / reports not-completed.

---

## 8. API SURFACE

Only these four routes are added. All mutating bodies are `extra="forbid"`; actor via `Depends(request_actor)`.

| route | body | success | errors |
|---|---|---|---|
| `POST /jobs/{job_id}/execution/start` | `StartExecutionRequest{expected_proposal_run_id, actual_start_at, before_work_evidence: [EvidenceInput] (1..10)}` | 200 `ExecutionActionResponse{job, execution, message}` | 403 denied · 404 missing · 409 `StaleProposalError`/`CommittedJobError`/`CommittedStateIntegrityError`/`TerminalJobError`/`ConcurrentJobModificationError` · 400 invalid transition, actor, timestamps, evidence rules · 422 schema |
| `POST /jobs/{job_id}/execution/complete` | `CompleteExecutionRequest{execution_id, actual_end_at, after_work_evidence: [EvidenceInput] (1..10)}` | 200 `ExecutionActionResponse` | as above, plus 409 `StaleExecutionError` |
| `POST /jobs/{job_id}/execution/not-completed` | `ExecutionNotCompletedRequest{execution_id, actual_end_at, reason (1..2000), evidence: [EvidenceInput] (0..10)}` | 200 `ExecutionActionResponse` | as complete |
| `GET /jobs/{job_id}/execution` | — | 200 `JobExecutionsResponse{job_id, executions: [ExecutionResponse]}` (oldest first; `[]` if never executed) | 403 · 404 · 409 `ExecutionIntegrityError` (row says `in_progress` but history has no matching start) |

**Removed:** `POST /jobs/{job_id}/complete` (and `JobService.complete`, `plan_completion`). Keeping a route that can never succeed would be dead surface; no frontend, script or doc calls it (checked). `JOB_COMPLETED` stays in `JobEventType` for reading existing rows but is no longer emitted.

**Not added:** `/execution/evidence` (deferred, §5.5); a separate `/history` (exists).

Error mapping follows the router's existing pattern; `StaleExecutionError` and `ExecutionIntegrityError` join `_CONFLICTS`.

---

## 9. DATABASE DESIGN

**No new table, no new column, no migration, no new index.**

| need | satisfied by |
|---|---|
| execution + outcome record | `job_events` rows `EXECUTION_STARTED/COMPLETED/NOT_COMPLETED` |
| evidence | metadata of those rows |
| append-only | existing `job_events_no_update` / `_no_delete` triggers |
| per-job read | existing `job_events_by_job (job_id, sequence)` |
| per-generation read | existing `job_events_by_run (optimization_run_id)` |
| current execution pointer | `block_candidate_json.metadata.execution_id` (existing column) |
| new status | `maintenance_jobs.status` is `TEXT` with no CHECK constraint — `'in_progress'` needs no DDL |

Existing databases: every Slice 1–4 row stays valid (`notified` jobs can start; legacy `completed` jobs stay terminal and show `executions: []`; stored `JOB_COMPLETED` events still deserialize). Upgrade and fresh-create tests are still required (§11 H) to prove this rather than assume it. `EVENT_SCHEMA_VERSION` stays 1 (new event types and metadata keys are additive; snapshot shape unchanged).

---

## 10. CONCURRENCY AND SAFETY

**Mechanism: no new concurrency architecture.** Every execution transition runs in `JobService._transition` under `lifecycle_lock` (in-process serialization) and in `mutate_jobs`' `BEGIN IMMEDIATE` transaction whose plan re-reads the row (cross-process serialization, fail-closed). The service's pre-read of history (before-evidence references, planned placement) happens while holding the same `RLock`; a cross-process change between that read and the write is caught by the in-plan `execution_id`/status/run-id checks.

| race | outcome |
|---|---|
| start / start (same job) | first → `in_progress`; second's plan sees `in_progress` → `InvalidTransitionError` (400), `TRANSITION_REJECTED` recorded; exactly one `EXECUTION_STARTED` |
| start / not-completed | not-completed before start sees `notified` → 400; after start it needs the E id the caller could only get from the start response |
| start / complete | same as above |
| complete / not-completed (same E) | first wins; second sees `completed` (400, invalid transition — §15.1) or `reported` (400); never both outcomes |
| evidence / complete | impossible: evidence is inline in the transition |
| complete / reoptimization | in-process: lock serializes. If optimization first: job is pinned (`COMMITTED_BLOCK_PRESERVED`), then completes. If completion first: job is terminal and excluded. Cross-process: optimization's `expected_statuses` check refuses the whole batch (`ConcurrentJobModificationError`); `reject_terminal` refuses terminal jobs |
| start / reoptimization | same as above; `in_progress` is committed so a successful optimization pins it |
| not-completed / reoptimization | in-process serialized; cross-process the optimization batch is refused; the next run proposes R2 |
| execution against stale/cancelled proposal | start requires `notified` + `expected_proposal_run_id == proposal_run_id_of(job)` → otherwise 400 (status) / 409 (`StaleProposalError`) |
| authority action vs execution | approve/reject/postpone require `scheduled`; an `in_progress`/`notified` job refuses them. After release, R1-pinned authority actions are stale |
| execution vs later generation | late E1 complete/not-completed against E2 → `StaleExecutionError` (409) |
| corrupted committed row | every execution plan calls `assert_committed_state_consistent([job])` first → `CommittedStateIntegrityError` (409), row byte-identical, refusal recorded |
| row placement changed during execution | guard step 6 prevents it; digest re-check at outcome detects out-of-band edits |
| history-less primitive un-pin / complete / start | no `JobMutation` → no token → refused (`CommittedJobError` / `TerminalJobError` / integrity) |

### 10.1 Q14 — COMPLETED terminal, absolutely
Terminal check is the first statement of the guard and precedes the token logic; `ALLOWED_TRANSITIONS[completed] = ∅`; `list_active` and `reject_terminal` exclude it from optimization; `plan_optimization_failure` skips it; all three execution plans require `notified`/`in_progress`; authority plans require `scheduled`. No token kind targets a completed row.

### 10.2 Q15 — No completion without after evidence
Four independent layers: request model (`min_length=1`); plan (`InvalidTransitionError` if empty); guard (entering `completed` requires COMPLETE token); `mutate_jobs` cross-check (token requires a matching `EXECUTION_COMPLETED` event, and `validate_execution_events` asserts its `after_work_evidence` is non-empty). The only other way to `completed` — `update_status` — carries no token.

---

## 11. TEST MATRIX

New files: `test_slice5_execution_lifecycle.py` (pure/service), `test_slice5_execution_api.py`, `test_slice5_replanning.py`, `test_slice5_execution_concurrency.py`. Shared helper module `backend/tests/execution_helpers.py` (`start(service, job)`, `execute_to_completion(service, job)`, `evidence(...)`) with a fixed injected clock and in-horizon timestamps (e.g. `2026-09-10T…+05:30`). All tests use the isolated DB from `conftest.py`.

### A. Eligibility
A1 notified job starts → `in_progress`, block still COMMITTED, placement and `proposal_run_id` unchanged · A2 `scheduled` refused (400) · A3 `reported` refused · A4 rejected job (now `reported`) refused · A5 postponed job refused · A6 completed refused (400, invalid transition — §15.1) · A7 each corruption variant of §3.5 refused with `CommittedStateIntegrityError`, row byte-identical, `TRANSITION_REJECTED` recorded · A8 wrong `expected_proposal_run_id` → 409 · A9 UNIDENTIFIED actor refused; SYSTEM actor refused in-process · A10 authorization denial → no state, no event (per new action).

### B. Before evidence
B1 empty list → 422 (HTTP) / 400 (service) · B2 one valid item → start · B3 duplicate reference in request → rejected · B4 `captured_at` after `actual_start_at` → rejected · B5 naive timestamp → rejected · B6 future beyond skew → rejected · B7 lat without lon → rejected · B8 evidence_id server-minted, phase BEFORE_WORK, recorded_by = actor, recorded_at = event time · B9 >10 items → rejected.

### C. Transitions
C1 duplicate start refused, one `EXECUTION_STARTED` · C2 complete before start refused · C3 not-completed before start refused · C4 complete with empty after evidence refused at model **and** plan · C5 complete succeeds, `completed`, block still COMMITTED, `execution_id` kept · C6 not-completed without reason refused · C7 without `actual_end_at` → 422 · C8 `actual_end_at < actual_start_at` refused · C9 after reference equal to a before reference refused · C10 wrong `execution_id` → 409 `StaleExecutionError` · C11 deviation flags recorded, not enforced.

### D. Terminal
D1 completed → optimization excludes it, no new system events · D2 start/complete/not-completed on completed → 400 (§15.1) · D3 approve/reject/postpone on completed refused · D4 `update_status(completed_job, …)` refused · D5 SQL UPDATE/DELETE of execution events aborts.

### E. Replanning
E1 not-completed → `reported`, schedule cleared, block PLANNED, `proposal_run_id`/`execution_id` removed, `last_refusal_reason = reason` · E2 `earliest_start_minute = max(stored, planned_end, actual_end_minute)`; `latest_end_minute` widened · E3 prior postponement not-before not lowered · E4 R1 events, R1 `optimization_runs` row, E1 start/outcome events all still present and unchanged · E5 next optimization mints R2 ≠ R1, `BLOCK_PROPOSED` with R2, `proposal_id` PROP-R2 · E6 approve with R1 → 409; approve with R2 → `notified` · E7 start with R1 → 409 · E8 second execution E2 (`attempt_number=2`, run R2) completes · E9 `GET /execution` lists E1 NOT_COMPLETED (R1) then E2 COMPLETED (R2) · E10 not-before ≥ horizon → failure still recorded; next optimization refuses with `window_infeasible`, no fabricated proposal · E11 late E1 complete after E2 start → 409 · E12 `plan_postpone` behaviour byte-identical after helper extraction (existing Slice 3/4 tests unchanged and green).

### F. Concurrency (threads + barrier, as in `test_concurrent_commits_of_one_proposal_succeed_exactly_once`)
F1 start/start → exactly one success · F2 start/not-completed · F3 complete/not-completed on same E → exactly one outcome event · F4 complete vs optimization → job ends `completed`; optimization either pinned it or excluded it; invariants hold · F5 start vs optimization → pinned or refused, never moved · F6 out-of-process simulation: status changed between optimization snapshot and write → `ConcurrentJobModificationError`, batch rolled back · F7 committed row corrupted between service pre-read and write → integrity refusal, rollback.

### G. Audit
G1 each transition emits exactly one event of its type, correct before/after snapshot, actor preserved · G2 `execution_id` and `optimization_run_id` on all three · G3 human actors only (extend `test_system_actors_are_only_ever_recorded_for_automated_decisions`) · G4 canonical bytes round-trip for execution events · G5 a plan setting a token without its event → refused by `mutate_jobs`, rolled back · G6 `update_status(committed_job, "reported")` → `CommittedJobError` · G7 `update_status(in_progress_job, "completed")` → refused · G8 route inventory contains exactly the new routes · G9 bypass scanner still green.

### H. Database
H1 fresh DB: no new tables created; schema identical to Slice 4 · H2 upgrade: a DB built at Slice 4 schema with notified/scheduled/completed jobs and their events → notified job starts and completes; legacy completed job remains terminal and returns `executions: []`; legacy `JOB_COMPLETED` rows deserialize · H3 existing `optimization_runs` rows unchanged · H4 full existing suite green.

### 11.3 Pre-authorized changes to existing tests
Replace direct completion with `execute_to_completion` (or the execution call under test), **without weakening any assertion's intent**:
- `test_jobs.py:108, 141`
- `test_jobs_api_workflow.py:204-251` (route `/complete` → `/execution/*`)
- `test_jobs_canonical_timetable_integration.py:939-947`
- `test_job_section_resolution.py:844-854`
- `test_jobs_persistence_isolation.py:99`
- `test_jobs_optimization_workflow.py:447-477`, and `:687` (`update_status(reported, "completed")` fixture — becomes impossible; replace with the real execution flow or a clearly-labelled direct-SQL terminal-row fixture)
- `test_job_lifecycle_accountability.py`: `:327-346` (completion event → `EXECUTION_COMPLETED`), `:342` (chronological lifecycle), `:755-824` and `:826-848` (replace `service.complete` attempts; add not-completed attempt; `attempted_transition` names change), `:909-943` (add `update_status(job_id, "reported")`), `:945-990` (terminal), `:1143-1185` (concurrency with completion), `:1495-1535` (history endpoint), `:1647-1668`, `:1759-1788` (**route inventory set**), `:1790-1811` (every mutation over HTTP), `:1861-1886` and `:1889-1904` (**route lists**; `_proposal_review_body` must emit valid execution bodies so 422 cannot mask 403/400), `:1934-1960` (system-actor classification).

---

## 12. IMPLEMENTATION PLAN

Each step leaves the full suite green.

**Step 1 — Lifecycle state model and guard (pure).** *Opus.*
Files: `jobs/models.py` (`IN_PROGRESS`), `jobs/lifecycle.py` (`COMMITTED_STATUSES`, `ALLOWED_TRANSITIONS` adds `notified→in_progress`, `in_progress→{in_progress,completed,reported}` while **temporarily keeping** `notified→completed`; `ExecutionTransition`; `JobMutation.execution`; guard steps 4/6; `validate_mutation`; `committed_state_problems` execution_id findings; `_without_placement` pops `execution_id`; `validate_execution_events`), `jobs/repository.py` (`mutate_jobs` cross-check), `jobs/events.py` (three event types), `jobs/service.py` (`_no_proposal_reason` for `in_progress`).
Staging note: in Step 1 the "entering `completed`" gate applies only to `in_progress → completed`; legacy `notified → completed` and the `update_status(reported, "completed")` fixture (`test_jobs_optimization_workflow.py:687`) keep working until Step 4 turns the gate into "every entry into `completed` requires a COMPLETE token from `in_progress`".
Invariant: nothing can enter/leave `in_progress` without a token and its event; terminal first; stored-integrity before transitions.
Tests: G5, G6, G7 (for `in_progress`), A7 at plan level, the three guard tests from §3.5 extended.

**Step 2 — Evidence value objects and execution plans (pure).** *Opus* (release plan + horizon arithmetic).
Files: new `jobs/execution.py` (evidence normalization/validation, `ExecutionRecord`, `build_execution_records`, digest helper shared with `proposal.py`), `jobs/lifecycle.py` (`plan_execution_start`, `plan_execution_complete`, `plan_execution_not_completed`, `_raise_not_before` extracted from `plan_postpone`), `StaleExecutionError`, `ExecutionIntegrityError`.
Invariant: approved plan immutable; evidence mandatory; replan window correct; postpone unchanged.
Tests: B*, C*, E1–E3, E10 (pure), E12, G4.

**Step 3 — Service methods and authorization.** *Sonnet High.*
Files: `identity/authorization.py` (3 actions), `jobs/service.py` (`start_execution`, `complete_execution`, `report_execution_not_completed`, `get_execution`, injectable `clock`, `_transition` catches the two new errors), `jobs/models.py` (request/response models).
Invariant: authorize first; everything via `_transition` under `lifecycle_lock`; refusals recorded.
Tests: A1–A10, service-level B/C, D1–D4, A10 denial per action.

**Reality check (see §15.3): the 4 HTTP routes below (planned for Step 5)
were actually implemented in this same Step 3 pass**, not deferred - the
service methods and `jobs/router.py`'s 4 routes landed together. §15.3
records this so the chronology here matches what shipped.

**Step 4 — Close legacy completion and migrate tests.** *Opus* (edits Slice 1 invariant tests; must not weaken them).
Files: `lifecycle.py` (remove `notified→completed`, `plan_completion`; widen the guard so every entry into `completed` requires a COMPLETE token from `in_progress`), `service.py` (remove `complete`), `router.py` (remove `/complete`), tests in §11.3, new `tests/execution_helpers.py`.
Invariant: no completion without after evidence, on any path.
Tests: §11.3 list, D*, G7, full suite.

**Step 5 — HTTP routes.** *Sonnet High.*
Files: `jobs/router.py` (4 routes, error mapping), `test_job_lifecycle_accountability.py` inventory/system-role/`_proposal_review_body` updates, `test_slice5_execution_api.py`.
Invariant: strict bodies, actor propagation, no mutation via GET, correct status codes.
Tests: G8, API variants of A/B/C/E, 422/400/403/404/409 mapping.

**Reality check (see §15.3): `jobs/router.py`'s 4 routes were already
delivered in Step 3**, together with the service methods they call.
Step 5, as actually executed, was the P1/P2 remediation pass over that
same router code (empty-history 200, `ExecutionIntegrityError` → 409,
the `report_execution_not_completed` rename) rather than a first
implementation of the routes.

**Step 6 — Replanning, concurrency and upgrade proof suites.** *Sonnet High* (Opus review of F results).
Files: `test_slice5_replanning.py`, `test_slice5_execution_concurrency.py`, upgrade fixture.
Tests: E4–E11, F1–F7, H1–H4.

**Step 7 (optional, deferrable) — Evidence correction.** *Sonnet High.*
`EVIDENCE_RECORDED` + `supersedes_evidence_id`, `RECORD_EVIDENCE` action, `POST /jobs/{job_id}/execution/evidence`, `in_progress` only.

---

## 13. RISKS / OPEN QUESTIONS

**Risks (with mitigations already in the design)**
1. *Guard narrowing opens an un-pin path.* A status-pair edit would let `update_status(job_id,"reported")` release a commitment without history. **Mitigation:** token on `JobMutation` + event cross-check + new test G6.
2. *Legacy completion removal breaks many tests.* **Mitigation:** enumerated in §11.3, isolated in Step 4.
3. *Fixed horizon anchor vs wall clock.* Minutes are relative to `2026-09-10T00:00+05:30` with no rolling "now". Observation timestamps outside the horizon make the replanned job honestly unschedulable (E10). The demo must submit in-horizon observation times. This is a pre-existing architectural property, not introduced here.
4. *Cross-process optimization refused by a concurrent execution transition* (`ConcurrentJobModificationError`). Existing Slice 1 semantics; acceptable for the single-process deployment.

**Genuinely open (domain decisions, not blocking Slice 5 core)**
1. **Lapsed or cancelled approved block.** A `notified` job whose work never starts (possession not granted, crew unavailable) has no exit: `notified→reported` is deliberately not added and "failure before start" is refused by the brief. Needs a domain decision on an authority-level "release approved block" transition (Slice 6 candidate).
2. **Start outside the planned window.** Recorded as a deviation flag, not refused. Should a start observed after `planned_end` (outside the approved possession) be refused? Domain review required.
3. **Evidence correction after completion.** Terminal rows are never written; whether an append-only correction event may be added to a completed job's history is undecided (Step 7 limits correction to `in_progress`).
4. **Retry idempotency.** A client retrying a start after a lost response gets 400 (already in progress) and must `GET /execution` to recover `execution_id`. An idempotency key is not designed.

---

## 14. FINAL RECOMMENDATION

- **Architecture ready:** yes — DESIGN READY under the four conditions in §1.
- **Implementation can begin:** yes, at Step 1.
- **First implementation prompt (Opus) should accomplish exactly Step 1:** add `JobStatus.IN_PROGRESS`; add it to `COMMITTED_STATUSES`; extend `ALLOWED_TRANSITIONS` (keeping `notified→completed` temporarily); introduce `ExecutionTransition` and `JobMutation.execution`; implement guard steps 1–6 in the stated order so that entering `in_progress`, `in_progress→completed`, and releasing `in_progress→reported` each require the matching token (legacy `notified→completed` stays ungated until Step 4); add `validate_execution_events` to `mutate_jobs`; add the `execution_id` integrity findings; add the three event types; update `_no_proposal_reason`. No service method, no route, no evidence model yet. Deliver with tests proving: (a) `update_status(job_id,"reported")` and `update_status(in_progress_job,"completed")` are refused; (b) a plan that sets a token without its matching event is rolled back; (c) the three existing committed-state guard tests pass unmodified; (d) an `in_progress` job is pinned by optimization exactly like a `notified` job; (e) the full 754-test baseline stays green.

---

## 15. IMPLEMENTATION REALITY (post-Step 3 remediation)

This section records three already-existing facts about what actually
shipped, found during the Opus review of Step 3 and confirmed against
the code. None of them change the architecture in §1–§11; they document
behaviour the design already implied but did not spell out, plus one
chronology correction. No transition rule, guard, or route was redesigned
to produce this section.

### 15.1 Completed-job execution routes return 400, not 409

`plan_execution_start` / `plan_execution_complete` / `plan_execution_not_completed`
(`lifecycle.py`) each check the job's *current status* first —
`notified` for start, `in_progress` for complete/not-completed — and
raise `InvalidTransitionError` (a `ValueError`, mapped to HTTP 400)
before any terminal-state check ever runs. A `completed` job fails this
same status check like any other wrong-status job, so
`start_execution`/`complete_execution`/`report_execution_not_completed`
against a completed job return **400**, not the 409 a reader might
expect from `TerminalJobError`/`_CONFLICTS`. `TerminalJobError` guards a
different call path (`plan_schedule_assignment`, `set_schedule` — not
reachable from any HTTP route) and is never reached from the execution
routes at all.

This is **intentional current behaviour**, left unchanged by the Step 3
remediation. It is consistent with every other status-mismatch refusal
in this module (e.g. starting a `reported` job also gets 400) — a
completed job is simply one more status the transition does not accept
from. Step 4 (closing legacy `notified→completed`) may reconcile this
with a dedicated terminal check if that step's own invariants require
it; this design does not decide that now.

Regression test: `test_execution_actions_on_completed_job_are_rejected_as_invalid_transition`
(service level) and `test_http_execution_actions_on_completed_job_return_400`
(HTTP level), both in `test_slice5_step3_execution_service.py`.

### 15.2 Some pre-transition failures are never recorded as TRANSITION_REJECTED

`JobService._transition` records `TRANSITION_REJECTED` only for
exceptions raised **from inside** `repository.mutate_jobs` (i.e. from
the pure `lifecycle.py` plan itself, running inside the transaction).
Two categories of failure happen earlier and are therefore never
recorded that way:

1. **Evidence construction.** `JobService._evidence_items` builds
   `EvidenceItem` values (validating `evidence_reference`,
   `evidence_kind`, coordinates, etc.) *before* `lifecycle_lock` is even
   acquired. A structurally invalid item raises `EvidenceValidationError`
   (a `ValueError`, 400) with no lock held and no event written at all —
   see `test_start_rejects_structurally_invalid_evidence`, which asserts
   the event count is unchanged.
2. **Pre-read integrity/lookup failures.** `start_execution`,
   `complete_execution` and `report_execution_not_completed` each
   acquire `lifecycle_lock`, read the job row, and reconstruct its
   execution history (`build_execution_records`) **before** calling
   `_transition`. A missing job (`KeyError`) or an `ExecutionIntegrityError`
   raised by that reconstruction happens outside `_transition`'s
   try/except, so nothing is recorded either — the row and history are
   simply left untouched and the exception propagates.

This is deliberate, fail-closed behaviour, not an oversight: both
categories fail *before* anything resembling a "transition was attempted
against this job" is even well-formed enough to describe, so there is
nothing coherent to record as a rejected attempt. The behaviour is
identical to how a malformed `JobCreateRequest` never writes a
`JOB_CREATED` event — validation failures upstream of a mutation are
refused, not recorded as refused mutations. This section documents the
existing rule; nothing changed to produce it.

### 15.3 Execution routes chronology correction

The original plan (§12, Steps 3 and 5) put the 4 HTTP execution routes
in a separate Step 5, after Step 4 closed the legacy completion path.
In reality, `jobs/router.py`'s `POST /jobs/{job_id}/execution/start`,
`/complete`, `/not-completed` and `GET /jobs/{job_id}/execution` were
implemented **in Step 3**, alongside the service methods they call —
Step 4 (closing legacy `notified→completed`) has not run yet as of this
remediation. The Step 3/Step 5 bullets above are annotated in place
rather than rewritten, so the plan's original shape stays visible next
to what actually happened. This remediation pass (the P1/P2 fixes in
§15.1–15.2 and the code review that produced them) is the work that was
originally scoped as part of Step 5's "error mapping" and Step 3's
`report_execution_not_completed` naming; both step descriptions above
already used the corrected name and the router file, which is why this
note exists rather than a rewrite of either step.
