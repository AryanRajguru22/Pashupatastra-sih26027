# Pashupatastra API contract — v1 (frozen for frontend integration)

Machine-readable form: `docs/openapi-v1.json` (guarded against silent drift by
`backend/tests/test_slice7_contract_freeze.py`). Changing anything below is a
contract change and needs a deliberate snapshot regeneration.

## Conventions

- **Base path:** every lifecycle route is under `/v1`.
- **Legacy, not part of v1:** `POST /optimize`, `POST /recover` (Milestone-1 solver
  demo, called by the existing dashboard; unversioned, default `{"detail": ...}` error
  body) and `GET /health`. Do not build lifecycle features on them.
- **Identity:** send `X-Actor-Id` **and** `X-Actor-Role` (`WORKER`, `ENGINEER`,
  `AUTHORITY`, `ADMIN`), or neither. One without the other is `400`; `SYSTEM` is `403`.
  The identity is *declared, not verified* (`assurance = DECLARED_UNVERIFIED`; no headers
  = `UNIDENTIFIED`/`NONE`). **Authentication and role enforcement do not exist yet**: the
  role column below is the *intended* split, not a check the server performs.
- **Named-actor requirement (Slice 9):** `approve` / `notify`, `reject`, `postpone`,
  `release` and the three `execution/*` operations require an **identified human**
  actor — send both headers with a non-`UNIDENTIFIED` role, or the request is
  `400 INVALID_TRANSITION` and nothing changes. This is **accountability, not
  authentication**: it only requires that *somebody* is named, never checks *which*
  role, and the recorded identity stays `DECLARED_UNVERIFIED`. `POST /v1/jobs`
  deliberately still accepts an unidentified reporter — a defect report without a
  name beats a lost one. Reads are unguarded.
- **Errors:** every v1 4xx is `{"code": "<STABLE_CODE>", "detail": "<prose>"}`.
  Switch on `code`; `detail` is display text and may be reworded. Request-shape errors are
  `422 VALIDATION_ERROR` with `detail` = FastAPI's error list.
- **Stale protection:** every operation that commits, withdraws, or starts a proposal
  names the run it reviewed (`expected_proposal_run_id`). A run that is not the job's
  current one is `409 STALE_PROPOSAL`; the refusal is recorded in history.
- **Not part of the contract:** the free-form `block_candidate` dict, event `metadata`
  internals, and the wording of any `detail`.

## Endpoints

| Method + path | Body | Success | Requires (state) | Intended role | Pin |
|---|---|---|---|---|---|
| `POST /v1/jobs` | `JobCreateRequest` (see *Field intake* below) | 201 `JobResponse` (also 201 for an idempotent replay) | — | WORKER | — |
| `GET /v1/jobs?status=&limit=&cursor=` | — | 200 `{items: JobResponse[], next_cursor}` | — | any | — |
| `GET /v1/jobs/{id}` | — | 200 `JobResponse` | job exists | any | — |
| `GET /v1/jobs/{id}/history` | — | 200 `{job_id, events[]}` | job exists | any | — |
| `GET /v1/jobs/{id}/proposal` | — | 200 `BlockProposalResponse` | status `scheduled` | any | — |
| `GET /v1/jobs/{id}/execution` | — | 200 `{job_id, executions[]}` | job exists | any | — |
| `GET /v1/jobs/{id}/obligation` | — | 200 `ObligationResponse` (type `NONE` when nothing is owed) | job exists | any | — |
| `GET /v1/obligations?state=&obligation_type=&role=&past_due=&attention=&limit=&cursor=` | — | 200 `{items, next_cursor, evaluated_at, policy_version, policy_assumed}` | — | any | — |
| `GET /v1/optimization-runs/{run_id}` | — | 200 `OptimizationRunResponse` | run exists | any | — |
| `POST /v1/corridors/{corridor_id}/optimize-jobs` | — | 200 `JobOptimizationResponse` | ≥1 eligible job | ENGINEER / SYSTEM | — |
| `POST /v1/jobs/{id}/proposal/approve` | `{expected_proposal_run_id}` | 200 `{job, message}` → `notified` | `scheduled` | AUTHORITY | **required** |
| `POST /v1/jobs/{id}/notify` | `{expected_proposal_run_id}` | 200 `{job, message}` → `notified` | `scheduled` | AUTHORITY | **required** (same effect as approve) |
| `POST /v1/jobs/{id}/proposal/reject` | `{expected_proposal_run_id, reason}` | 200 → `reported` | `scheduled` | AUTHORITY | **required** |
| `POST /v1/jobs/{id}/proposal/postpone` | `{expected_proposal_run_id, reason, selected_date}` | 200 → `reported`, not-before raised | `scheduled`; date inside horizon | AUTHORITY | **required** |
| `POST /v1/jobs/{id}/proposal/release` | `{expected_proposal_run_id, reason}` | 200 → `reported` | `notified` (not started) | AUTHORITY | **required** |
| `POST /v1/jobs/{id}/execution/start` | `{expected_proposal_run_id, actual_start_at, before_work_evidence[1..10]}` | 200 `{job, execution, message}` → `in_progress` | `notified` | WORKER | **required** |
| `POST /v1/jobs/{id}/execution/complete` | `{execution_id, actual_end_at, after_work_evidence[1..10]}` | 200 → `completed` (terminal) | `in_progress`, open `execution_id` | WORKER | `execution_id` |
| `POST /v1/jobs/{id}/execution/not-completed` | `{execution_id, actual_end_at, reason, evidence[0..10]}` | 200 → `reported` | `in_progress`, open `execution_id` | WORKER | `execution_id` |

Lifecycle: `scheduled` —approve→ `notified` —start→ `in_progress` —complete→ `completed`;
`scheduled` —reject/postpone→ `reported`; `notified` —release→ `reported`;
`in_progress` —not-completed→ `reported`. `completed` is terminal.

## Field intake (Slice 8) — optional additions to `POST /v1/jobs`

Nothing here is required, so `JobCreateRequest.required` is unchanged and every request
that was valid before is still valid and behaves the same. `distance_start` /
`distance_end` (corridor-absolute metres) remain the **only stored location**.

- **`field_location`** `{from_station_id, toward_station_id, offset_start_m, offset_end_m}`
  — the span in human terms: metres from `from_station_id` toward `toward_station_id`
  along the one section joining them. It is converted through the section registry and
  must describe **exactly** the declared `distance_*` (1 mm tolerance) or the request is
  `400 LOCATION_INPUT_CONFLICT`. It is a cross-check, not a substitute: station-only
  intake (omitting the distances) would relax `required` and is a later API version's
  decision. Unknown / non-adjacent / ambiguous stations, negative or out-of-section
  offsets: `400 FIELD_LOCATION_INVALID`. A span ending exactly on the far station is
  refused (half-open sections), as it would be in metres.
- **`idempotency_key`** (1–128 of `A-Za-z0-9._:-`) — a retried submission from the same
  `X-Actor-Id` with the same request returns the job the first one created (**201, same
  body, `Idempotent-Replayed: true` header**) and creates nothing. The same key with a
  different request is `409 IDEMPOTENCY_KEY_CONFLICT`. Requires actor headers (`400`
  without). Enforced by a database UNIQUE constraint, so it holds across threads and
  processes sharing the database, and does not expire. It is scoped to the *declared*
  actor id, which is not authenticated.
- **Asset association** is derived, not supplied: the nearest asset on the job's own track
  to the span midpoint, and only if it lies within **20 km** of the span (a plausibility
  ceiling sized to the sparse synthetic asset set, not a physical claim). Otherwise
  `400 ASSET_ASSOCIATION_FAILED` — the report is refused, never attached to a distant
  asset. `block_candidate.metadata.asset_association` records the asset, its distance,
  whether it is in the job's section (`section_match`) and a fingerprint of its identity.
- **Duplicate detection** is advisory. A new report that matches an open job on track,
  section, asset, work type, span (≤ 500 m apart) and time (≤ 72 h) is **still created**;
  `block_candidate.metadata.duplicate_detection` lists `candidate_jobs` and the signals.
  Nothing is merged, rejected or changed. An authority decides with the existing
  reject-with-reason action.

## Accountability (Slice 9) — derived obligations, read-only

**These three routes read; nothing here writes, sends, or decides anything.**

An **obligation** answers *who currently owes an action on this job, since when, and by
when*. It is **derived** at read time from the job's own committed history plus an SLA
policy plus one evaluation moment — there is no obligation table, no `status_entered_at`
column, no new status and no new event type. Given the same three inputs, an auditor
recomputes any past obligation exactly.

| Field | Meaning |
|---|---|
| `obligation_type` | `APPROVAL_PENDING` (owed by AUTHORITY), `EXECUTION_START_PENDING` / `COMPLETION_PENDING` (WORKER), `REPLANNING_PENDING` (ENGINEER), or `NONE` |
| `state` | `WITHIN_SLA` → `DUE_SOON` → `OVERDUE` → `ESCALATED_L1..L3`, plus `MOOT`, `NO_SLA_DEFINED`, `NONE` |
| `owed_role` | `AUTHORITY` / `WORKER` / `ENGINEER`, or `null`. **Never a person** |
| `clock_started_at`, `anchor_event_id` | when the clock started, and the exact event that started it |
| `due_at`, `sla_seconds`, `escalation_level`, `elapsed_seconds` | the deadline and where we are against it |
| `policy_version`, `policy_assumed` | which table of durations produced `due_at`, and that it is **assumed** |
| `is_past_due` | **the predicate for "is this late?"** — see the note below |
| `attention_required`, `attention_reason_code`, `attention_event_id` | a condition needing an ENGINEER (`COMMITTED_BLOCK_CONFLICT`, `INTEGRITY_REFUSAL_RECORDED`), orthogonal to the obligation's own clock |

**Use `past_due=true`, not `state=OVERDUE`, to list late work.** `OVERDUE` is the exact
state for "past the deadline but before the first escalation step". Under the shipped
policy the first step *is* the deadline, so late obligations report `ESCALATED_L1` and
upward and `OVERDUE` is never produced. `is_past_due` covers both.

**Mootness.** When the system withdrew a proposal (`PROPOSAL_INVALIDATED`,
`OPTIMIZATION_REFUSED`) the obligation reports `MOOT` — **never** `OVERDUE`, at any
elapsed time. A system withdrawal is not an authority failure. A released commitment or
work reported not-completed becomes `REPLANNING_PENDING` for the same reason.

**Nothing is owed is an answer, not an error.** `GET /v1/jobs/{id}/obligation` returns
`200` with `obligation_type: "NONE"` for a completed job, or a reported one awaiting the
next optimization run. `404` means the *job* does not exist.

**Pagination is over candidate jobs, not over obligations.** Obligations are derived after
a page of jobs is read, so a filtered request returns the matches *within* each page: a
page may hold fewer than `limit` items — even zero — while `next_cursor` is still set.
Follow `next_cursor` until it is `null`. The cursor is the same opaque keyset cursor
`GET /v1/jobs` uses.

`409 OBLIGATION_STATE_INCONSISTENT` means the job row and its history do not describe a
coherent obligation. The read fails closed rather than reporting a fabricated deadline;
escalate to an engineer, do not retry.

### The SLA values are ASSUMED DEMO ENGINEERING VALUES

> **Not Indian Railways policy.** Not derived from any IR circular, manual, schedule of
> powers or corridor agreement. Not reviewed or approved by any railway authority. They
> are plausible engineering placeholders, chosen so the accountability model can be
> demonstrated. Every obligation carries `policy_assumed: true` and the
> `policy_version` that produced it — **a client that displays `due_at` must display
> that too.**

| Reported severity | Approval | Execution start | Completion |
|---|---|---|---|
| `CRITICAL` | 2 h | 1 h | 4 h |
| `MODERATE` | 6 h | 4 h | 12 h |
| `MINOR` / `NONE` (or unreported) | 24 h | 12 h | 24 h |

`DUE_SOON` begins at 75% of the SLA; escalation levels L1/L2/L3 begin at 1×, 2× and 4×.
All durations are **wall-clock**: there is no working calendar, shift model, weekend rule
or holiday list anywhere in this system, and inventing one would be fabricated domain
data.

### What starts each clock

- **Approval** — the placement event (`BLOCK_PROPOSED` / `BLOCK_REPROPOSED` /
  `COMMITTED_BLOCK_PRESERVED`) for the job's *current* `proposal_run_id`. A re-proposal
  therefore resets the clock, and a superseded proposal can never produce a current
  obligation.
- **Execution start** — `BLOCK_COMMITTED.occurred_at`, **not** the block's planned
  window. The planning horizon is a fixed constant, so every approved block's planned
  window already lies in the past; anchoring there would mark every job instantly
  overdue. The planned-window anchor is the operationally better statement and is
  **deferred until the horizon tracks real time** — a data dependency (the timetable must
  cover the live window), not a design preference.
- **Completion** — `ExecutionRecord.started_recorded_at`, the server's own record, **not**
  the worker-supplied `actual_start_at`. A deadline must not be movable by the party it
  binds.

`GET /v1/optimization-runs/{run_id}` resolves the `optimization_run_id` that obligations
and proposals name: the run's request, result, solver status, timing and provenance
snapshot, as stored. Append-only; there is no route that updates or deletes a run.

## Error codes

| Status | Codes |
|---|---|
| 400 | `INVALID_REQUEST`, `INVALID_TRANSITION`, `EVIDENCE_INVALID`, `INVALID_CURSOR`, `ACTOR_HEADERS_INCOMPLETE`, `ACTOR_INVALID`, `ASSET_ASSOCIATION_FAILED`, `FIELD_LOCATION_INVALID`, `LOCATION_INPUT_CONFLICT` |
| 403 | `ACTOR_SYSTEM_ROLE_FORBIDDEN`, `AUTHORIZATION_DENIED` (no policy denies today) |
| 404 | `JOB_NOT_FOUND`, `CORRIDOR_NOT_FOUND`, `OPTIMIZATION_RUN_NOT_FOUND` |
| 409 | `STALE_PROPOSAL`, `STALE_EXECUTION`, `JOB_TERMINAL`, `JOB_COMMITTED`, `CONCURRENT_MODIFICATION`, `NO_CURRENT_PROPOSAL`, `NO_ELIGIBLE_JOBS`, `POSSESSION_DATA_UNAVAILABLE`, `TIMETABLE_COVERAGE_GAP`, `COMMITTED_STATE_INCONSISTENT`, `EXECUTION_HISTORY_INCONSISTENT`, `IDEMPOTENCY_KEY_CONFLICT`, `ASSET_REFERENCE_INVALID` (not returned by any v1 route today), `OBLIGATION_STATE_INCONSISTENT`, `SLA_POLICY_UNRESOLVABLE` |
| 422 | `VALIDATION_ERROR` |

`GET /v1/jobs/{id}/proposal` returning `409 NO_CURRENT_PROPOSAL` carries a `detail` that
says *why* — an authority/worker decision (reject, postpone, release, not-completed) is
named as such with the actor's role and id, and only a genuine solver outcome reads
"considered and left UNSCHEDULED". `COMMITTED_STATE_INCONSISTENT` and
`EXECUTION_HISTORY_INCONSISTENT` are not retryable: escalate to an engineer.

## Honest boundaries (do not present these as more than they are)

- **Evidence** is a reference string plus kind, capture time and optional coordinates.
  The lifecycle *requires* it and validates its timestamps and uniqueness; no file is
  stored, uploaded or resolved by this backend. Coordinates are range-checked only —
  there is **no geofencing** against the job's location.
- **Data provenance** is `SYNTHETIC` on every axis today; `effective` is the weakest link.
  A human field report does not promote any axis.
- **Asset identity is not durable.** Assets are regenerated in memory, not stored; an
  `asset_id` is a name into that set. Slice 8 detects a stale or re-pointed id (it fails
  closed and never resolves to another asset) but cannot make identity durable — that
  needs an authorised asset register.
- **Idempotency is not authentication.** Keys are scoped to the declared actor id.
- **No notification is ever delivered.** There is no SMS, email, push, WhatsApp,
  webhook or provider of any kind, and no recipient directory, contact detail or
  job-to-person assignment. Notification *intents* are derived in process and can be
  recorded for inspection; the strongest truthful outcome is `RECORDING_ONLY`.
  Nothing in this system may be presented as `DELIVERED`, `READ` or `SMS_SENT`.
  **No API exposes notifications**, deliberately, because there is nothing to expose.
- **Obligations are addressed to roles, never to people.** An escalation records that
  *an obligation* is late. It never asserts that a named individual failed —
  attributing blame to a person needs authentication, which does not exist.
- **There is no scheduler.** No cron, worker, task queue or background sweep exists.
  Obligations and escalations are computed **on read**. A job's escalation level is
  therefore only "current" as of the moment it was queried, and no permanent record
  of "at 09:00 this was L1" exists — that would require the delivery-attempt table
  this slice deliberately defers.
- **Time never changes the lifecycle.** No deadline, escalation or notification
  approves, rejects, releases, cancels, expires or completes anything. An unanswered
  proposal stays `scheduled` and stays committable, however late it is.
- **Authentication / RBAC / real notifications / crew constraints** do not exist. SLA
  policy exists but its values are **assumed demo values**, not railway policy.
