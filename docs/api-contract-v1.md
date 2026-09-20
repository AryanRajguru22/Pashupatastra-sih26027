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
| `POST /v1/jobs` | `JobCreateRequest` | 201 `JobResponse` | — | WORKER | — |
| `GET /v1/jobs?status=&limit=&cursor=` | — | 200 `{items: JobResponse[], next_cursor}` | — | any | — |
| `GET /v1/jobs/{id}` | — | 200 `JobResponse` | job exists | any | — |
| `GET /v1/jobs/{id}/history` | — | 200 `{job_id, events[]}` | job exists | any | — |
| `GET /v1/jobs/{id}/proposal` | — | 200 `BlockProposalResponse` | status `scheduled` | any | — |
| `GET /v1/jobs/{id}/execution` | — | 200 `{job_id, executions[]}` | job exists | any | — |
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

## Error codes

| Status | Codes |
|---|---|
| 400 | `INVALID_REQUEST`, `INVALID_TRANSITION`, `EVIDENCE_INVALID`, `INVALID_CURSOR`, `ACTOR_HEADERS_INCOMPLETE`, `ACTOR_INVALID` |
| 403 | `ACTOR_SYSTEM_ROLE_FORBIDDEN`, `AUTHORIZATION_DENIED` (no policy denies today) |
| 404 | `JOB_NOT_FOUND`, `CORRIDOR_NOT_FOUND` |
| 409 | `STALE_PROPOSAL`, `STALE_EXECUTION`, `JOB_TERMINAL`, `JOB_COMMITTED`, `CONCURRENT_MODIFICATION`, `NO_CURRENT_PROPOSAL`, `NO_ELIGIBLE_JOBS`, `POSSESSION_DATA_UNAVAILABLE`, `TIMETABLE_COVERAGE_GAP`, `COMMITTED_STATE_INCONSISTENT`, `EXECUTION_HISTORY_INCONSISTENT` |
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
- **Authentication / RBAC / notifications / SLA / crew constraints** do not exist.
