# STEP 16 — PASHUPATASTRA BACKEND / PRODUCT GAP AUDIT

**Status:** read-only audit. No production code, tests, frontend, contracts, dataset, provenance,
SectionRegistry, optimizer, train adapter, `jobs.db`, protected files or stash were modified.
This file is the only repository artifact. No commit. No push.

**Audit date:** 2026-09-13
**HEAD:** `d9f0264` (`feat(jobs): integrate maintenance jobs with optimizer`)
**Audited tree:** the **working tree**, not HEAD. See §0.

**Method.** Source was read directly (not inferred from docs or comments). Three runtime checks
were run, each pointed at a scratch database via `PASHUPAT_JOBS_DB` so `jobs.db` was never
opened:

| Check | Result |
|---|---|
| `python -m pytest -q -p no:cacheprovider backend` | **457 passed**, 52 subtests passed, 1 benign `StarletteDeprecationWarning` |
| `app.openapi()["paths"]` | 8 operations on 7 paths (listed in §5) |
| Default `JobService()` | corridor `CORRIDOR_A`, no dataset, possession derivation `GENERATED_STATIC_SLOTS` |
| `JobService()` with `PASHUPAT_CORRIDOR_ID=CORR-NDLS-AGC` | derivation `CANONICAL_TIMETABLE_DERIVED`, 149 windows, timetable & possession provenance `SYNTHETIC`, 6 sections |

Classification vocabulary: **IMPLEMENTED · PARTIALLY IMPLEMENTED · ARCHITECTURALLY READY ·
MISSING · BLOCKED BY EXTERNAL DATA**. Findings I verified by reading the exact code path are
stated as fact; the one I could only reason about is marked *(plausible, not reproduced)*.

---

## 0. READ THIS FIRST — WHAT IS AND IS NOT COMMITTED

Much of the backend this audit credits exists **only as uncommitted work** in the working tree:

- **Untracked (`??`), not in any commit:** `backend/app/audit/` (whole package),
  `data/canonical_train.py`, `data/corridor_dataset.py`, `data/horizon_anchor.py`,
  `data/provenance.py`, `data/section_registry.py`, `data/timetable_adapter.py`,
  `data/train_provider.py`, `jobs/resource_resolution.py`, and 11 test files.
- **Modified, not committed (`M`):** `jobs/service.py` (+453 lines), `jobs/optimization.py`,
  `jobs/models.py`, `optimizer/solver.py`, `data/generator.py`, `data/models.py`,
  `contracts/schemas.py`, fixtures, dataset.

So "IMPLEMENTED" below means *implemented in the working tree and passing its tests*. At HEAD,
the audit trail, section-aware resolution, four-axis provenance and the canonical-timetable
possession path do not exist. **Committing this baseline is a precondition for every
implementation step in §12.** A lost working tree would delete most of what Sprint 3 built.

The earlier `FINAL_BACKEND_SYSTEM_AUDIT.md` (protected, not edited) was written at commit
`13c74bf` and is now **stale** on several points: it says there is no persistence, no job
endpoint, no `COMPLETED` state, no solver seed pinning, and that "train information does NOT
drive scheduling decisions". All four are no longer true in the working tree. Train occupancy
**does** reach the solver on the dataset corridor (see the runtime check above), but **not** on
the default deployment corridor.

---

## 1. EXECUTIVE SUMMARY

### 1.1 How much of the target lifecycle is real

One overall percentage would imply more precision than the evidence supports. Coverage by
lifecycle phase:

| Phase | Backend coverage | Quality |
|---|---|---|
| A. Worker report | About ⅓: track, work type, span, manpower, description | Prototype. No identity, severity, evidence, geolocation or station |
| B. Validation | About ½: schema, track, section resolution, span-in-one-section | Section resolution is production-grade. No dedup, no plausibility checks |
| C. Scoring | Deterministic scorer runs at report time | Prototype. Not ML. Ignores what the worker reported |
| D. Scheduling | Most of it: CP-SAT, possession, section resources, pinning, fail-closed | **Strongest part.** Single-day horizon, no crew capacity |
| E. Authority | None, apart from one un-gated "commit" transition | Missing |
| F. SLA / escalation | None | Missing |
| G. Execution | None | Missing |
| H. Completion | One un-gated `notified → completed` transition | Missing apart from the terminal guard |
| I. Accountability | Immutable **optimization-run** audit only | Production-grade for runs. Missing for job decisions |
| J. Notifications | None (`/notify` sends nothing) | Missing |
| K. Data / provenance | Four-axis provenance, synthetic gate, train-provider seam | Production-grade for REAL vs SYNTHETIC. No ASSUMED tier |

**Summary:** about one quarter to one third of the target lifecycle has a working backend path.
That estimate is weighted by stage count, not by effort. What exists is concentrated in the
middle (score → section → possession → optimize → persist → audit the run). **Everything that
makes it a government workflow is absent:** identity, authority decisions, postponement,
execution, evidence, failure and re-report, notifications, escalation, and a per-job history.

### 1.2 Production-quality (in the working tree)

- **Fail-closed possession safety.** An empty window list is refused before solving
  (`jobs/optimization.py:103-109`). A block with no matching window is refused individually
  (`optimizer/solver.py:334-352`). An uncovered committed block is un-pinned rather than making
  the model infeasible (`solver.py:285-286`). Curtailing every window is refused
  (`simulation/disruptions.py:147-160`).
- **Section-aware resource identity** `(track_id, section_id)` (`solver.py:51-95`), with
  fail-closed chainage → section resolution at job creation (`jobs/resource_resolution.py:64-143`).
- **Canonical timetable → possession derivation**, including midnight rollover, skip-stop
  over-approximation and rejected-train section withholding (`data/timetable_adapter.py`).
- **Four-axis provenance with a weakest-link effective value** that cannot be set by hand
  (`data/provenance.py:160-252`), and a dataset loader that refuses anything not declared
  `"synthetic": true` (`data/corridor_dataset.py:156-163`).
- **Append-only optimization audit** with SQL `BEFORE UPDATE/DELETE` triggers
  (`audit/repository.py:75-95`).
- **Deterministic solver configuration**: single worker, seed 1 (`solver.py:47-48, 548-549`).
- **Atomic batch persistence** of optimization outcomes (`jobs/repository.py:327-449`), plus
  terminal-status guards (`repository.py:33, 365-370, 486-490`).

### 1.3 Prototype-quality

- The job report model (`JobCreateRequest`, `jobs/models.py:25-61`).
- The scorer: hand-set weights, synthetic asset features (`ml/scorer.py`, `data/feature_adapter.py`).
- Duration: fixed per work type (`feature_adapter.py:38-45`), so the worker cannot state it.
- The status model: 4 states and 2 manual transitions (`jobs/models.py:18-22`).
- SQLite, an in-process `threading.Lock`, a module-level service singleton (`jobs/router.py:37-41`).

### 1.4 Missing

Authentication and roles; authority approve, postpone and reject; multi-day and calendar
scheduling; evidence and photos; geolocation; worker-reported severity; duplicate detection;
execution state; completion evidence; not-completed and re-report; per-job audit and event
history; notifications; SLA, deadlines and escalation; crew/manpower constraints; an audit read
API.

### 1.5 Architecturally ready but not implemented

- **`TrainDataProvider` Protocol** (`data/train_provider.py:47-57`). A future authorized feed
  implements `fetch() -> TrainDataSnapshot`, and nothing downstream changes.
- **`ProvenanceProfile`** already models REAL_STATIC and REAL_SCHEDULED per axis.
- **`BlockCandidate.earliest_start_minute` / `latest_end_minute`** plus the absolute
  `horizon_start` anchor (`contracts/schemas.py:25, 159-171`). The contract can express "not
  before minute N". The jobs pipeline hard-codes 0 and 1440.
- **Solver `mutual_exclusion_group` and `dependencies`** (`solver.py:451-503`). They work, but
  the jobs pipeline hard-codes `None` and `[]` (`service.py:477-479`).
- **`horizon_anchor` + `timetable_adapter`** already handle multi-day service dates
  (`test_traversal_beyond_one_day_produces_valid_possession`).

### 1.6 Blocked only by unavailable government data

- Promoting any axis to REAL_*. `derived_possession_provenance` deliberately **raises** for a
  non-synthetic timetable (`service.py:151-184`), and `to_possession_source` raises for REAL
  levels (`provenance.py:323-349`).
- Real topology. Step 15 gate: **AMBER, REAL_STATIC ingestion NOT ELIGIBLE** (a different
  corridor, AII–RE; nothing here changes it).
- Real RTIS train position, real possession/block bookings (COA), real asset condition, forecast
  and weather feeds.

**Nothing in the SIH-critical workflow (§10) is blocked by government data.** Every demo gap is
ordinary backend work over the existing synthetic environment.

---

## 2. COMPLETE LIFECYCLE MATRIX

Paths are relative to `backend/app/` unless they start with `contracts/`, `backend/tests/` or
`frontend/`.

### A. Worker / Rail Engineer

| Capability | Status | Evidence |
|---|---|---|
| Authentication / identity boundary | **MISSING** | No auth anywhere. No `Depends()` on any route (`jobs/router.py`, `api/routers/*`). `audit/models.py:27` `SYSTEM_ACTOR = "system:unauthenticated-demo"`. `api/main.py` CORS `allow_origins=["*"]` |
| Create job | **IMPLEMENTED** | `POST /jobs` → `jobs/router.py:44-64` → `JobService.create_job` (`jobs/service.py:358-581`). Tests: `test_report_job_returns_201_and_persists`, `test_valid_job_is_reported_scored_and_persisted` |
| Location | **PARTIALLY IMPLEMENTED** | Linear location only: `distance_start` / `distance_end` in metres (`jobs/models.py:31-32`). No km-post, no landmark, no free-text location |
| Station / corridor | **PARTIALLY IMPLEMENTED** | Corridor is implicit: one corridor per deployment (`service.py:63-66`). The job has no station field. Station can only be derived through the registry section (`Section.start_station_id/end_station_id`), and that derivation is not exposed on `JobResponse` |
| Section | **IMPLEMENTED** (derived, not reported) | `resolve_job_resource` (`jobs/resource_resolution.py:64-143`) writes `section_id` into `block_candidate` (`service.py:463`). **Not** a top-level `JobResponse` field: it is visible only inside `block_candidate` |
| Work type | **IMPLEMENTED** | `JobType` enum, 6 values (`jobs/models.py:9-15`) |
| Manpower | **PARTIALLY IMPLEMENTED** | `workers_min` / `workers_max` validated (`jobs/models.py:34-35, 51-61`) and persisted. **Never used by the optimizer** (§6) |
| Duration | **PARTIALLY IMPLEMENTED** | Not accepted from the worker. Looked up in `WORK_TYPE_DEFAULT_DURATIONS` (`data/feature_adapter.py:38-45`, `service.py:385-393`). An unmarked engineering assumption |
| Description | **IMPLEMENTED** | 1–1000 chars (`jobs/models.py:37`). Stored, but not used by scoring |
| Severity | **MISSING** (as a worker input) | No severity field on `JobCreateRequest`. Scoring uses the **nearest synthetic asset's** `defect_severity` (`feature_adapter.py:229-236`), so a worker cannot report "critical" |
| Before photo / evidence | **MISSING** | No upload endpoint, no `UploadFile`, no evidence table (repo grep: 0 hits) |
| Timestamp | **IMPLEMENTED** (server time) | `created_at` / `updated_at` UTC ISO (`service.py:147-148, 529`). No worker-observed "defect found at" time |
| Geolocation | **MISSING** | No lat/lon anywhere in `backend/` or `contracts/` (grep: 0 hits) |

### B. Validation

| Capability | Status | Evidence |
|---|---|---|
| Required fields | **IMPLEMENTED** | Pydantic `extra="forbid"` plus `Field` constraints (`jobs/models.py:25-37`). 422 on malformed payload: `test_malformed_payload_returns_422`, `test_invalid_request_validation` |
| Invalid locations | **IMPLEMENTED** | Out-of-corridor, negative, malformed km and a track not serving the section are all rejected (`section_registry.py:281+`, `resource_resolution.py:105-127`). Tests: `test_job_out_of_corridor_range_is_rejected_at_creation`, `test_job_with_invalid_track_for_its_location_is_rejected` |
| Span crossing a section boundary | **IMPLEMENTED** | `resource_resolution.py:129-138`. Test: `test_job_crossing_a_section_boundary_is_rejected_at_creation` |
| Invalid duration | **MISSING** (not applicable today) | Duration is not an input. There is no plausibility check that a work type's duration fits any possession window (the solver just refuses later) |
| Invalid manpower | **PARTIALLY IMPLEMENTED** | Checks `>=1` and `max>=min` only (`jobs/models.py:34-35, 51-61`). No upper bound, no per-work-type minimum crew, no check against available workforce |
| Section resolution | **IMPLEMENTED** | Fail-closed, and a missing registry is refused (`service.py:430-437`). Test: `test_job_service_without_a_registry_fails_closed_on_create` |
| Duplicate jobs | **MISSING** | `job_id` is a fresh `uuid4` per request (`service.py:454-457`). No same-track/section/work-type/open-status check. Two identical reports create two jobs, and the solver will try to schedule both |
| Incomplete reports | **MISSING** (as a workflow state) | A report is either accepted in full or rejected with 400/422. There is no draft or "needs more information" state |

### C. AI / ML

| Capability | Status | Evidence |
|---|---|---|
| Scoring | **IMPLEMENTED** (deterministic heuristic) | `ScoringFeatureAdapter.score_block_candidate` (`data/feature_adapter.py:198-243`) → `ml/scorer.py:122-166`. Runs synchronously inside `create_job` (`service.py:518-523`) |
| Priority | **IMPLEMENTED** | `PRIORITY_WEIGHTS` (`ml/scorer.py:40-47`). Persisted as `priority_score` |
| Risk | **IMPLEMENTED** | `RISK_WEIGHTS` (`ml/scorer.py:27-35`). Persisted as `risk_score` |
| Explainability | **PARTIALLY IMPLEMENTED** | `build_explanation` returns the top labels (`ml/scorer.py:87-119`), stored at `block_candidate.metadata.scoring_explanation` and `scoring_features` (`feature_adapter.py:240-241`). There are no per-feature contributions (weight × value), no model version, and nothing top-level on `JobResponse` |
| Model / provider boundary | **MISSING** | Direct static call. No `ScoringProvider` protocol, no model id or version recorded, no provenance on scores. The package is named `ml/`, but it is a fixed-weight formula |
| Uses worker-reported facts | **MISSING** | Features come from `_nearest_asset` (`service.py:912-941`) over **synthetic** generated assets (`corridor_dataset.py:200-203`). Description, manpower and work type do not affect score (work type only through default duration) |
| Fallback behaviour | **PARTIALLY IMPLEMENTED** (fail-closed only) | A scorer `ValueError` (unknown asset or track) → HTTP 400 and the job is **not** persisted. There is no degraded mode ("persist as UNSCORED, flag for manual triage"). For a safety defect report, refusing the report entirely is arguably the wrong failure direction |

### D. Scheduling

| Capability | Status | Evidence |
|---|---|---|
| Train state | **PARTIALLY IMPLEMENTED** | Canonical `TrainDataSnapshot` via `StaticTimetableProvider` (`data/train_provider.py:60-131`). Scheduled or synthetic only. No live position |
| Timetable | **IMPLEMENTED on dataset corridor / NOT USED on default corridor** | `CORR-NDLS-AGC` → `CANONICAL_TIMETABLE_DERIVED` (149 windows, runtime check). Default `CORRIDOR_A` → `GENERATED_STATIC_SLOTS`, which models no trains (`service.py:127-131, 814-856`) |
| Train occupancy | **IMPLEMENTED** (dataset corridor) | `occupied_intervals_by_section` + `derive_section_possession_windows` (`timetable_adapter.py:627-768`). Occupancy is expressed **only** as the complement: train-free gaps become possession windows |
| Possession | **PARTIALLY IMPLEMENTED** | Possession is **derived** (train gaps) or **generated** (fixed slots). It is never **granted**: there is no concept of an authorised block/possession booking by a controller. A train-free gap is treated as permission to work |
| Section conflicts | **IMPLEMENTED** | No-overlap per `(track_id, section_id)` (`solver.py:406-446`). Tests: `test_same_track_same_section_blocks_cannot_overlap`, `test_same_track_different_sections_are_not_treated_as_one_resource` |
| Workforce | **MISSING** | `workers_min/max` never read by `solve()`. There is no crew pool, no cumulative capacity constraint, and the jobs pipeline sets `mutual_exclusion_group=None` (`service.py:479`) |
| Duration | **IMPLEMENTED** | `end == start + duration` (`solver.py:268-271`). Must fit inside one window (`solver.py:376-397`) |
| Safety | **IMPLEMENTED** (possession-level) | Fail-closed coverage, 10-min train safety buffer (`timetable_adapter.py:77`), 15-min headway (`service.py:144`), rejected-train withholding (`timetable_adapter.py:771-791`). See §6.4 for the horizon-coverage hazard |
| CP-SAT | **IMPLEMENTED** | `optimizer/solver.py:163-790`. Unmodified solver called once per run through `AuditService.record_run` |
| Infeasibility | **IMPLEMENTED** | Whole-model infeasible → every block refused with the solver reason (`solver.py:570-596`). Per-block reasons (`solver.py:645-756`). `summarize_outcome` never invents success (`jobs/optimization.py:193-242`) |
| Partial success | **IMPLEMENTED** | Scheduled and unscheduled reported separately (`jobs/models.py:143-188`). Test: `test_optimize_response_reports_partial_success_honestly` |
| Retry / replanning | **PARTIALLY IMPLEMENTED** | Re-running `optimize-jobs` replans `reported`/`scheduled` and pins `notified` (`service.py:876-906`). There is no targeted replan of one job, no trigger-on-event replan, and no carry-over to a later day (single 1440-minute horizon, `service.py:142-143`) |

### E. Authority

| Capability | Status | Evidence |
|---|---|---|
| Pending approval state | **MISSING** | `JobStatus` = reported / scheduled / notified / completed (`jobs/models.py:18-22`). `GET /jobs?status=reported` is documented as "the authority review queue" (`router.py:74-77`), but no approval step follows it |
| Authority identity | **MISSING** | No principal. `SYSTEM_ACTOR` placeholder |
| Proposed window | **PARTIALLY IMPLEMENTED** | `scheduled` + `schedule_start_minute/end_minute`, relative to the fixed `DEFAULT_HORIZON_START` (`contracts/schemas.py:25`). Minutes, not a calendar datetime, on `JobResponse` |
| Approve | **PARTIALLY IMPLEMENTED** (unnamed, un-gated) | `POST /jobs/{id}/notify` (`service.py:595-627`) is functionally "approve and commit": it sets `block_status=COMMITTED`, `is_committed=True`. No actor, no reason, no authority check |
| Postpone | **MISSING** | No endpoint, state or field |
| Authority-selected date | **MISSING, and not expressible today** | `create_job` hard-codes `earliest_start_minute=0`, `latest_end_minute=1440` (`service.py:469-471`). The pipeline horizon is one day (`service.py:142-143`) |
| Re-optimization after postpone | **MISSING** | Depends on the two rows above |
| Approve revised proposal | **MISSING** | — |
| Commit / pin | **IMPLEMENTED** | `notify` → persisted committed block → `classify_for_optimization` → `existing_committed_blocks` → solver pin (`solver.py:276-314`). Tests: `test_future_optimization_preserves_notified_work`, `test_notified_work_survives_a_later_optimization_over_http` |
| Rejection | **MISSING** | No rejected, cancelled or withdrawn state. `BlockStatus.CANCELLED` exists in contracts but is never written (grep) |
| Authority notes / reason | **MISSING** | No field on any model |

### F. SLA / Escalation

| Capability | Status | Evidence |
|---|---|---|
| Response deadline | **MISSING** | No deadline/due field (grep: 0 hits) |
| Approaching deadline | **MISSING** | No scheduler or background job |
| Escalation | **MISSING** | — |
| Authority hierarchy | **MISSING** | — |
| Configurable SLA | **MISSING** | — |
| Emergency vs routine | **MISSING** as behaviour | `EMERGENCY_REPAIR` changes only the default duration (90 min) and scoring inputs. It gets no expedited path, no SLA and no bypass |
| Maximum postponement | **MISSING** | — |
| Forced action | **MISSING** | — |

### G. Execution

| Capability | Status | Evidence |
|---|---|---|
| Scheduled state | **IMPLEMENTED** | `scheduled` (`repository.py:398-402`) |
| Start window | **PARTIALLY IMPLEMENTED** | Minute offsets only. No wall-clock start/end exposed. No check that "now" is inside the window for any transition |
| Worker notification | **MISSING** | `/notify` sends nothing. It is a DB transition (§2-J) |
| Execution state | **MISSING** | No in-progress state. `complete` jumps straight from `notified` |
| Completion prompt | **MISSING** | — |

### H. Completion

| Capability | Status | Evidence |
|---|---|---|
| Yes / no | **PARTIALLY IMPLEMENTED** | "Yes" only: `POST /jobs/{id}/complete` (`service.py:633-658`). No "no" |
| After photo | **MISSING** | — |
| Timestamp | **PARTIALLY IMPLEMENTED** | `updated_at` overwritten on completion. No dedicated `completed_at` field, and it is lost on the next write (there is none after terminal, so in practice it survives) |
| Geolocation | **MISSING** | — |
| Evidence validation | **MISSING** | — |
| Completed terminal state | **IMPLEMENTED** | `TERMINAL_STATUSES = ("completed",)` (`repository.py:33`). Enforced in `list_active`, `apply_optimization_outcome`, `update_schedule`, `record_refusal`. Tests: `test_completed_job_cannot_re_enter_optimization`, `test_repository_also_refuses_to_reschedule_terminal_work`, `test_completed_job_is_never_touched_by_a_later_batch` |
| Failure reason | **MISSING** | — |
| Re-report | **MISSING** | — |
| Reschedule | **MISSING** (manual) | A `notified` job is pinned forever until completed. Nothing can un-commit it |

### I. Accountability

| Capability | Status | Evidence |
|---|---|---|
| Immutable audit: optimization runs | **IMPLEMENTED** | `optimization_runs` + triggers (`audit/repository.py:49-95`). Tests: `test_direct_sql_update_is_rejected_by_trigger`, `test_direct_sql_delete_is_rejected_by_trigger`, `test_duplicate_run_id_is_rejected_not_overwritten` |
| Immutable audit: job lifecycle | **MISSING** | Create, notify and complete write **no** audit record. `maintenance_jobs` is last-write-wins (`repository.py:257-325`) |
| Actor | **PARTIALLY IMPLEMENTED** | Column exists on runs. The value is always the placeholder |
| Timestamp | **IMPLEMENTED** (runs) / **PARTIALLY IMPLEMENTED** (jobs: `created_at`, `updated_at` only) | — |
| Reason | **MISSING** for human decisions. Solver reasons exist (`last_refusal_reason`, overwritten each run) | — |
| Before / after state | **PARTIALLY IMPLEMENTED** | Runs store full `request_json` / `result_json`. There is no before/after for job status changes |
| Optimization run linkage | **PARTIALLY IMPLEMENTED** | The run record exists, but `maintenance_jobs` stores no `run_id`, so you cannot answer "which run gave this job its window" |
| Authority decision history | **MISSING** | — |
| Postponement history | **MISSING** | — |
| Escalation history | **MISSING** | — |
| Audit read API | **MISSING** | `AuditRepository.get/list_all/list_by_corridor` exist (`audit/repository.py:146-183`) with no HTTP route |
| `/recover` audited | **MISSING** | `api/routers/recover.py` calls `simulate_disruption` directly, without `AuditService` |

### J. Notifications

`POST /jobs/{job_id}/notify` **does not notify anyone.** It is the commit/pin state transition
(`service.py:616-627`). No message, event, outbox, webhook, e-mail, SMS or push code exists
(grep: 0 hits outside that name).

| Notification | Status |
|---|---|
| Worker | **MISSING** |
| Authority | **MISSING** |
| Job scored | **MISSING** |
| Proposed | **MISSING** |
| Deadline | **MISSING** |
| Escalation | **MISSING** |
| Approved | **MISSING** |
| Postponed | **MISSING** |
| Execution | **MISSING** |
| Completion | **MISSING** |

There is no event infrastructure either. The only event-like record is the optimization run row.

### K. Data / Future Integration

| Capability | Status | Evidence |
|---|---|---|
| Real public data | **BLOCKED BY EXTERNAL DATA** | Nothing ingested. Step 15: AMBER, REAL_STATIC NOT ELIGIBLE |
| Assumptions | **MISSING as a labelled concept** | No ASSUMED tier (§7). Assumptions are unmarked constants |
| Synthetic data | **IMPLEMENTED** | Generator + dataset, both labelled SYNTHETIC. The loader refuses undeclared data (`corridor_dataset.py:156-163`). Test: `test_dataset_loader_refuses_data_not_declared_synthetic` |
| Provenance | **IMPLEMENTED** (optimization response + run audit) | `ProvenanceProfile` (`data/provenance.py`) on `JobOptimizationResponse.provenance` and in `provenance_snapshot_json`. **Not** on `JobResponse` or on scores |
| Provider interfaces | **PARTIALLY IMPLEMENTED** | Train: `TrainDataProvider` Protocol. Topology, assets, possession bookings, scoring, weather and forecast: none |
| Future authorized railway feed | **ARCHITECTURALLY READY + BLOCKED BY EXTERNAL DATA** | The seam exists. The provenance promotion rule deliberately raises (`service.py:151-184`) |
| RTIS-compatible boundary | **ARCHITECTURALLY READY** (scheduled shape only) | `TrainDataSnapshot` carries `observed_at` and `prefer_actual` actual-vs-scheduled times (`train_provider.py:91-131`). No `LIVE` value exists by design (`test_no_live_value_exists`). A position feed (lat/lon or current section, not stop times) would need a new adapter into `CanonicalTrainState` |
| Data freshness | **PARTIALLY IMPLEMENTED** | `observed_at` is carried on the snapshot. Nothing checks staleness or rejects old data |
| Data quality | **PARTIALLY IMPLEMENTED** | `TrainRejection` records + bounded audit summary (`jobs/optimization.py:165-190`). No data-quality state on jobs, assets or the corridor |
| Fail-closed behaviour | **IMPLEMENTED** | Possession, provenance, synthetic gate, section resolution, horizon anchor (naive datetime and missing `service_date` rejected: `test_missing_service_date_is_rejected_not_invented`) |

---

## 3. STATE MACHINE AUDIT

### 3.1 The actual job state machine (reconstructed from code)

**States:** `reported`, `scheduled`, `notified`, `completed` (`jobs/models.py:18-22`). They are
stored as free TEXT (`repository.py:77`) with no DB CHECK constraint.

```
                 POST /jobs
                     │
                     ▼
               ┌──────────┐   optimize-jobs, solver schedules it
               │ reported │ ───────────────────────────────────────┐
               └──────────┘                                         │
                   │  ▲ optimize-jobs, solver refuses it:           ▼
                   └──┘ status unchanged, last_refusal_reason set ┌───────────┐
                                                                   │ scheduled │◄──┐ optimize-jobs:
                                                                   └───────────┘   │ re-placed OR refused
                                                                         │    └────┘ (status stays)
                                                   POST /jobs/{id}/notify│
                                                                         ▼
                                                                   ┌──────────┐◄──┐ optimize-jobs:
                                                                   │ notified │   │ pinned, stays notified
                                                                   └──────────┘───┘
                                                 POST /jobs/{id}/complete│
                                                                         ▼
                                                                   ┌───────────┐
                                                                   │ completed │  TERMINAL
                                                                   └───────────┘
```

### 3.2 Transition table

| # | From → To | Trigger | Who can trigger | Data changed | Guard |
|---|---|---|---|---|---|
| T1 | ∅ → `reported` | `POST /jobs` | **Anyone with network access** | Full row inserted; `block_candidate_json` (PLANNED, scored, section-resolved) | Schema, track, duration model, nearest asset, registry, section resolution |
| T2 | `reported` → `scheduled` | `POST /corridors/{id}/optimize-jobs` | Anyone | `schedule_start/end_minute`, `status`, `updated_at`, `last_solver_status`, `last_refusal_reason=NULL`, block `status=SCHEDULED`, `metadata.committed_start/end_minute` (`repository.py:374-426`) | Solver placed it; the run lock is held |
| T3 | `scheduled` → `scheduled` | same | Anyone | New placement overwrites the old one | same |
| T4 | `reported`/`scheduled` → same status (refused) | same | Anyone | `updated_at`, `last_solver_status`, `last_refusal_reason` only (`repository.py:428-447`) | — |
| T5 | `notified` → `notified` (pinned) | same | Anyone | Schedule rewritten with the pinned values; block `status` set to `SCHEDULED` | Solver pinned it |
| T6 | `scheduled` → `notified` | `POST /jobs/{id}/notify` | Anyone | `status`, `updated_at`, block `status=COMMITTED`, `is_committed=True` (`service.py:621-627`) | Status must be `scheduled` (`service.py:607-614`) |
| T7 | `notified` → `completed` | `POST /jobs/{id}/complete` | Anyone | `status`, `updated_at` (`service.py:654-658`) | Status must be `notified` (`service.py:645-652`) |
| — | *any non-terminal* → `scheduled` | `JobService.set_schedule` / `JobRepository.update_schedule` | **No production caller** (tests only) | Hard-codes `status='scheduled'` (`repository.py:507`), which **would demote a `notified` job** if ever called | Terminal guard only |

**Terminal states:** `completed` only.

**Rejected transitions (tested):** notify from `reported` or `completed`, complete from
`reported` or `scheduled` → HTTP 400. Transitions on a missing job → 404. Rescheduling or
batch-including a completed job → `TerminalJobError` (409).
Tests: `test_illegal_transitions_are_rejected`, `test_transitions_on_missing_job_return_404`,
`test_job_lifecycle`.

### 3.3 Defects in the current machine (verified by reading the code path)

1. **A stale proposal survives a refusal.** When a `scheduled` job is refused in a later run,
   T4 leaves `status='scheduled'` and the **old** `schedule_start/end_minute` in place, with only
   `last_refusal_reason` set (`repository.py:428-447`). The API then reports a proposed window
   the latest solve rejected. A `scheduled` job can then be `notify`-committed onto a window the
   solver just refused.
2. **Committed block status regresses on re-optimization.** `apply_optimization_outcome` writes
   `block_candidate.status = SCHEDULED` for **every** scheduled entry, `notified` jobs included
   (`repository.py:391-393`). After one re-optimization a notified job's persisted block reads
   `status: SCHEDULED, is_committed: true`. Pinning still works (it keys on job status and
   `is_committed`), but the persisted contract is internally inconsistent.
3. **A whole-model infeasibility stamps committed jobs as refused.** On INFEASIBLE, every
   candidate (notified jobs included) gets `last_refusal_reason="solver found no feasible
   solution"` while it keeps `status='notified'` and its schedule (`solver.py:570-596` →
   `optimization.py:229-240` → `repository.py:428-447`).
4. **A commit cannot be undone.** Nothing leaves `notified` except `complete`. Operationally
   there is no way to cancel or postpone committed work.
5. **Pinned placement vs changed windows.** A pin adds `presence==1` and a fixed start
   (`solver.py:312-314`), and the possession rule requires the fixed interval to sit inside a
   matching window (`solver.py:376-397`). Un-pinning happens only when **no window matches the
   block's identity** (`solver.py:285-286`), not when windows still match the identity but no
   longer contain the pinned **time**. If possession timing ever shifts under a committed job,
   the whole model becomes INFEASIBLE and every job on every section is refused. *(Not reachable
   today: windows are deterministic for a fixed dataset and horizon. It becomes reachable as soon
   as possession data can change: live feeds, a moving horizon, curtailment in the jobs pipeline.)*
6. **Race between optimize and notify** *(plausible, not reproduced).* `notify` does not take
   the optimization lock (`optimization.py:287`). A job notified between
   `classify_for_optimization` and `apply_optimization_outcome` was solved as a free candidate,
   and its new placement is then written onto the now-committed job (`repository.py:374-426`
   keeps it `notified` but moves its window).
7. **Dead vocabulary.** `BlockStatus.UNSCHEDULED`, `AFFECTED` and `CANCELLED` are never written
   by any backend code (grep outside `contracts/`: 0 hits). `JobStatus` has no counterpart for
   them.

### 3.4 Comparison with the target lifecycle

The recommendation keeps the existing names and adds only what is missing. `scheduled` already
means "a proposed window exists", and `notified` already means "committed/pinned", so
**renaming them is not required**. What is required is to make the gate before `notified` an
authority decision.

| Target concept | Current state | Gap |
|---|---|---|
| REPORTED | `reported` | Exists. Missing: reporter identity, evidence, severity |
| (validation / triage) | — | Missing state for "report incomplete / needs information" (optional) |
| SCORED | implicit in `reported` | Acceptable as implicit. Missing: a "scored" event, score provenance and model version |
| UNSCHEDULED | `reported`/`scheduled` + `last_refusal_reason` | **Not a state.** Missing: an explicit refused/unschedulable outcome that clears the stale window (defect 1) |
| SCHEDULED (= proposed, pending authority) | `scheduled` | Exists, but there is **no separate pending-approval gate**. `notify` bypasses review |
| APPROVED / COMMITTED | `notified` | Exists as behaviour. Missing: authority actor, reason, authorization check, and a separation from worker notification |
| NOTIFIED (worker told) | conflated with commit | Missing: a real notification event, distinct from the commit |
| POSTPONED | — | **Missing state + transitions:** `scheduled → postponed(not_before, reason, actor)` → re-optimize → `scheduled` |
| REJECTED | — | **Missing** (target marks it "if supported"). Terminal |
| ESCALATED | — | **Missing.** Best modelled as a flag/event on a job, not a mutually-exclusive status, so escalation does not erase where the job is in the lifecycle |
| EXECUTING | — | **Missing:** `notified → executing` (work started, actor, time, optional geolocation) |
| COMPLETION REQUESTED | — | **Missing:** `executing → completion_requested` (after-evidence submitted, awaiting verification) |
| COMPLETED | `completed` | Exists. Missing: evidence precondition, verifier, `completed_at` |
| FAILED / NOT COMPLETED | — | **Missing:** `executing → not_completed(reason)` → re-report → `reported` or `postponed(not_before)` |
| Un-commit / cancel committed | — | **Missing:** `notified → postponed` or `→ cancelled` with reason and actor |

Missing transitions, collected: `scheduled→postponed`, `postponed→scheduled`,
`scheduled→rejected`, `notified→executing`, `executing→completion_requested`,
`completion_requested→completed`, `completion_requested→executing` (evidence rejected),
`executing→not_completed`, `not_completed→reported|postponed`, `notified→postponed|cancelled`,
and a refusal transition that clears a stale proposed window.

---

## 4. DATA MODEL GAP AUDIT

Current persistence: one table, `maintenance_jobs` (`repository.py:64-89` + `_ADDED_COLUMNS`
38-42), plus `optimization_runs` (`audit/repository.py:51-68`). Structured job data also lives
in `block_candidate_json.metadata` (`service.py:485-511`).

| Field | Exists? | Where / notes |
|---|---|---|
| Worker identity | **No** | — (`MaintenanceDefect.reported_by` exists in `data/models.py:42` as a *source* label such as `TRC_SURVEY`, not a person, and is unused by jobs) |
| Exact job location | **Partial** | `distance_start`, `distance_end` (metres, linear). No lat/lon, no km-post notation |
| Station | **No** | Derivable from `section_id` via `SectionRegistry.get(...).start/end_station_id`. Not stored or exposed |
| Section | **Yes** (nested) | `block_candidate.section_id`. Not a column, not on `JobResponse` top level |
| Track | **Yes** | `track_id` column |
| Work type | **Yes** | `work_type` column |
| Manpower | **Yes** | `workers_min`, `workers_max` columns |
| Duration | **Nested only** | `block_candidate.duration_minutes`, assumed per work type. No column, no worker-stated value |
| Defect description | **Yes** | `description` |
| Priority | **Yes** | `priority_score` |
| Risk | **Yes** | `risk_score` |
| Score explanation | **Nested only** | `metadata.scoring_explanation`, `metadata.scoring_features` |
| Evidence | **No** | — |
| Before photo | **No** | — |
| After photo | **No** | — |
| Timestamps | **Partial** | `created_at`, `updated_at`. No `reported_observed_at`, `approved_at`, `started_at`, `completed_at`, `postponed_at` |
| Geolocation | **No** | — |
| Authority | **No** | — |
| Approval | **Implicit** | `status='notified'` + `is_committed`. No approver, time or note |
| Postponement | **No** | No `not_before`, count or history |
| Reason | **Solver only** | `last_refusal_reason` (overwritten each run). No human decision reason |
| Deadline | **No** | — |
| Escalation | **No** | — |
| Execution | **No** | — |
| Completion | **Status only** | `status='completed'` |
| Failure reason | **No** | — |
| Audit history (job) | **No** | Last-write-wins row |
| Audit history (optimization) | **Yes** | `optimization_runs` |
| Provenance | **Run-level only** | `provenance_snapshot_json` on runs, `provenance` on the optimization response. None per job, per score or per location |
| Calendar schedule | **No** | `schedule_*_minute` relative to a constant `DEFAULT_HORIZON_START = "2026-09-10T00:00:00+05:30"` (`contracts/schemas.py:25`). The job row does not store which horizon its minutes are relative to |
| Corridor id | **No column** | Implicit per deployment |
| Optimization run id | **No** | Not stored on the job |
| Idempotency / dedup key | **No** | — |

Structural notes:

- **No schema-migration framework.** `_migrate` adds columns only (`repository.py:96-118`).
  Adding tables (events, evidence, decisions) is possible in the same style, but renames and
  constraints are not.
- **No status CHECK constraint.** Any string can be written by a direct SQL client or a buggy
  caller.
- **The horizon is not persisted with the schedule.** Once a multi-day or moving horizon exists,
  a stored `schedule_start_minute=90` is ambiguous unless the anchor is stored alongside it. That
  is a **prerequisite** for postponement.

---

## 5. API / CONTRACT GAP AUDIT

### 5.1 Existing endpoints (from `app.openapi()`)

| Method | Path | Handler | Audited? | Tests |
|---|---|---|---|---|
| GET | `/health` | `api/routers/health.py` | n/a | `test_api.py` |
| POST | `/optimize` | `api/routers/optimize.py` (stateless fixture solve) | **Yes** (`optimization_runs`, trigger `optimize`) | `test_api.py`, `test_optimize_endpoint_is_audited` |
| POST | `/recover` | `api/routers/recover.py` | **No** | `test_recover_api.py`, `test_possession_curtailment_fail_closed.py` |
| POST | `/jobs` | `jobs/router.py:44` | **No** | `test_jobs_api_workflow.py` |
| GET | `/jobs` (`?status=`) | `jobs/router.py:67` | n/a | `test_list_jobs_and_filter_by_status`, `test_invalid_status_filter_is_rejected` |
| GET | `/jobs/{job_id}` | `jobs/router.py:95` | n/a | `test_get_single_job` |
| POST | `/corridors/{corridor_id}/optimize-jobs` | `jobs/router.py:116` | **Yes** (trigger `jobs_optimize`) | `test_jobs_pipeline_optimize_corridor_is_audited`, `test_optimize_corridor_schedules_reported_jobs` |
| POST | `/jobs/{job_id}/notify` | `jobs/router.py:164` | **No** | `test_full_lifecycle_report_optimize_notify_complete` |
| POST | `/jobs/{job_id}/complete` | `jobs/router.py:201` | **No** | same |

The frontend consumes only `/optimize` and `/recover` (`frontend/src/lib/data.ts:78, 231`).
`frontend/src/types/contracts.ts:234-240` states that no frontend code consumes the jobs API yet.

No route has an authorization boundary. CORS is `*`.

### 5.2 Target operations

Legend: ✅ yes · 🟡 partial · ❌ no.

| Target operation | Endpoint | Request | Response | Validation | AuthZ | Persist | Audit | Event | Tests |
|---|---|---|---|---|---|---|---|---|---|
| Report job | ✅ `POST /jobs` | 🟡 no severity, evidence, geo, reporter | ✅ `JobResponse` | ✅ | ❌ | ✅ | ❌ | ❌ | ✅ |
| Attach before-evidence | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Get job / list / queue | ✅ | ✅ | 🟡 no section, station, explanation or provenance at top level | ✅ | ❌ | n/a | n/a | n/a | ✅ |
| Score job | 🟡 inline in create | — | 🟡 nested | ✅ fail-closed | ❌ | ✅ | ❌ | ❌ | ✅ |
| Optimize / propose | ✅ corridor-wide | ✅ (path only) | ✅ honest outcome + provenance | ✅ | ❌ | ✅ atomic | ✅ run | ❌ | ✅ |
| Optimize one job / after a date | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Approve proposal | 🟡 `/notify` (misnamed, no actor or reason) | 🟡 empty body | ✅ | 🟡 status only | ❌ | ✅ | ❌ | ❌ | ✅ |
| Postpone with earliest date | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Reject | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Commit / pin | 🟡 same as approve | — | — | — | ❌ | ✅ | ❌ | ❌ | ✅ |
| Start execution | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Request completion + after-evidence | 🟡 `/complete` (no evidence) | 🟡 empty body | ✅ | 🟡 status only | ❌ | ✅ | ❌ | ❌ | ✅ |
| Not completed + reason | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Re-report / reschedule | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Job history / audit timeline | ❌ | ❌ | ❌ | ❌ | ❌ | 🟡 run rows only | 🟡 | ❌ | ❌ |
| Optimization run lookup | ❌ (repository methods only) | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ | ❌ | 🟡 repository tests |
| Notifications feed | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| SLA / escalation | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

### 5.3 Contract observations

- **Two contract styles coexist deliberately.** Jobs use Pydantic (`jobs/models.py`); solver
  contracts use dataclasses (`contracts/schemas.py`). The dormant Pydantic set in
  `contracts/{common,block_candidate,…}.py` is guarded by `test_contract_canonical_path.py`. New
  workflow contracts should follow the `jobs/models.py` style for HTTP bodies.
- **`JobResponse.block_candidate: dict`** leaks the internal solver contract to clients. Useful
  now, but a future public contract should expose `section_id`, `scoring_explanation` and
  calendar times as first-class fields.
- **Error mapping** is consistent: 400 for a domain `ValueError`, 404 for a missing job or
  corridor, 409 for terminal / no-eligible / possession-unavailable, 422 for schema errors.
- **`OptimizationRequest.train_timetable`** (`contracts/schemas.py:267`) is still never read by
  the solver. Train data reaches the solver only as possession windows. That is the correct
  design, but the field is misleading.

---

## 6. OPTIMIZER GAP AUDIT

`optimizer/solver.py` read end to end. A constraint is credited only when `solve()` posts it.

### 6.1 Currently implemented (posted in the CP-SAT model)

| Consideration | Implemented how | Evidence |
|---|---|---|
| Section-aware resources | No-overlap per `(track_id, section_id)` | `solver.py:51-67, 406-446` |
| Track | Part of the resource key; windows must match `track_id` | `solver.py:89-90` |
| Possession | The block must lie wholly inside exactly one matching window when present. Uncovered → refused | `solver.py:324-397` |
| Train occupancy | **Indirect only**, as the complement encoded in derived windows (dataset corridor) | `timetable_adapter.py:698-768` |
| Maintenance duration | `end = start + duration`, block time window clamp | `solver.py:147-160, 225-271` |
| Priority | Objective coefficient only (`risk + priority` ×1000) | `solver.py:135-144, 511-524` |
| Risk | Objective coefficient only | same |
| Existing committed blocks | Pinned to exact start/end, un-pinned if possession-uncovered | `solver.py:217-314` |
| Headway | Buffered interval (duration + 15 min) on the same resource | `solver.py:406-446` |
| Dependencies | Presence implication + ordering | `solver.py:451-470` (never populated by jobs) |
| Mutual exclusion | No-overlap per group | `solver.py:477-503` (never populated by jobs) |
| Infeasibility | Status mapping; per-block reasons | `solver.py:553-596, 645-756` |
| Deterministic behaviour | 1 worker, seed 1, 10 s limit. Scope documented at `solver.py:534-547` | `test_solver_determinism.py` (5 tests) |
| Audit linkage | Each run → `optimization_runs` with request, result and provenance snapshot | `jobs/optimization.py:350-363` |

### 6.2 Needs implementation (for the SIH workflow)

| Consideration | Why it does not exist today |
|---|---|
| **Postponement date / not-before** | The contract supports `earliest_start_minute`, but `create_job` hard-codes 0 and there is no field to store an authority date (`service.py:469`) |
| **Multi-day horizon** | `OPTIMIZATION_HORIZON_MINUTES = 1440`, `BlockCandidate.latest_end_minute = 1440` default, and `create_job` hard-codes 1440 (`service.py:142-143, 471`). The comment at `service.py:137-141` records that these are coupled and "must move together if this pipeline ever adopts a longer planning window" |
| **Moving horizon anchor** | `OPTIMIZATION_HORIZON_START = DEFAULT_HORIZON_START`, a fixed constant (`service.py:142`, `contracts/schemas.py:25`). Every run schedules the same calendar day |
| **Manpower / crew** | `workers_min/max` are in `metadata` and never read by `solve()`. There is no cumulative crew constraint and no crew pool |
| **Replanning scope** | Always the whole corridor. There is no "re-optimize this job only, keeping every other proposal stable" |
| **Proposal stability** | The objective rewards presence only (`solver.py:540-547`), so a re-run can move unrelated `scheduled` jobs arbitrarily. After authority review starts, a reshuffle of already-reviewed proposals is an operational problem |
| **Time-validity of pins** | See §3.3 defect 5 |
| **Explainability of placement** | Reasons explain refusal. There is nothing about why a window was chosen, or "earliest feasible after X" |
| **Run ↔ job linkage** | `run_id` is not written to jobs |
| **Unscheduled state hygiene** | See §3.3 defect 1 |

### 6.3 Future enhancement (post-SIH)

Tie-breaking objective (earliest-feasible, stability, minimal disruption); configurable
risk/priority weighting; urgency decay (overdue penalty); machine/equipment resources (the
dataset already names `TOWER_WAGON_01` in `conflict_pairs`, unused); travel and setup time
between sections; multi-corridor scaling and solve-time benchmarking beyond ~24 candidates;
stochastic delay buffers from RTIS; possession *booking* optimization rather than only
consumption of possessions.

### 6.4 Safety hazard to close before any multi-day or postponement work

**Timetable coverage of the horizon is not validated.** `derive_section_possession_windows`
emits windows only for `(track, section)` keys that have at least one traversal. For each such
key it treats **every minute not covered by a known traversal as train-free, up to
`horizon_minutes`** (`timetable_adapter.py:731-766`). Nothing checks that the timetable's
`service_date`s actually cover the horizon.

- Today this is safe **by coincidence**: the horizon is one day (2026-09-10), and every dataset
  train has `service_date` 2026-09-10.
- The existing test `test_traversal_beyond_one_day_produces_valid_possession`
  (`backend/tests/test_jobs_canonical_timetable_integration.py:481-510`) shows the behaviour: a
  timetable whose only train runs on 2026-09-11 still yields possession windows for 2026-09-10.
  In practice that is a full-day window on that section, for a day the timetable says nothing
  about.
- If the horizon is extended to allow "earliest feasible date after postponement", every
  uncovered day would read as train-free and the solver would place work there. **This would
  fail open.**

The hazard is closed only by a fail-closed **horizon-coverage gate**: refuse windows for any
day with no timetable coverage, or refuse the request. It is listed as P0 in §10 and step 1 in
§12.

---

## 7. HYBRID DATA AUDIT

### 7.1 What the code distinguishes today

| Concept | Supported? | How |
|---|---|---|
| REAL (authoritative) | **Vocabulary only** | `ProvenanceLevel.REAL_STATIC`, `REAL_SCHEDULED` (`provenance.py:116-133`). No code path produces them. Promotion raises (`service.py:178-184`, `provenance.py:345-349`) |
| SYNTHETIC | **Yes, enforced** | Every axis on both corridor paths (`optimization.py:123-157`; `test_build_provenance_profile_is_all_synthetic_on_both_paths`). Weakest-link collapse (`provenance.py:207-225`) |
| ASSUMED | **No** | There is no ASSUMED member. `ProvenanceLevel` has exactly three members (`test_no_live_value_exists` pins the no-LIVE half) |
| Derivation vs provenance | **Yes** | `possession_derivation` (`CANONICAL_TIMETABLE_DERIVED` / `GENERATED_STATIC_SLOTS`) is kept separate from provenance (`service.py:86-106`) |

### 7.2 The exact gap: engineering assumptions are unlabelled

The prototype runs on explicit engineering assumptions that are plain hard-coded constants,
reported under no provenance axis:

| Assumption | Location |
|---|---|
| Duration per work type (45/90/90/120/240/90 min) | `data/feature_adapter.py:38-45` |
| Train safety buffer 10 min | `data/timetable_adapter.py:77` |
| Minimum possession window 20 min | `data/timetable_adapter.py:78` |
| Minimum headway 15 min | `jobs/service.py:144` |
| Generated possession slots 00:30–05:00, 11:30–14:30, 21:00–23:30 | `data/generator.py:228-285` |
| Scoring weights and normalisation caps | `ml/scorer.py:27-47`, `data/feature_adapter.py:23-36, 94-134` |
| `days_overdue = last_maintained_days_ago − 30` | `data/feature_adapter.py:224-227` |
| Asset fields defaulted in `Asset` | `data/models.py:61-75` |
| Rollover / max-leg bounds | `data/timetable_adapter.py:84, 91` |

The four axes (topology, timetable, asset_condition, possession) describe **input datasets**.
They have no slot for **parameters** (durations, buffers, headways, weights) or for
**worker-reported facts** (a report is neither REAL railway data nor SYNTHETIC). As a result:

- When the timetable becomes REAL, a result will still silently depend on assumed buffers and
  durations, and nothing in the response will say so.
- A worker's field report has no provenance label at all.

**What the future system needs, at design level only:** an assumption register (id, value,
unit, rationale, owner, source class `ASSUMED`) referenced by id from each run's provenance
snapshot, plus a separate provenance class for human-reported operational facts (for example
`FIELD_REPORTED`). This should sit alongside the four-axis profile, with a documented rule that
ASSUMED parameters never promote `effective` to REAL_*. **Decide this before any REAL_* data is
connected.** Otherwise the weakest-link rule will report REAL for a result whose safety margins
are assumptions.

### 7.3 The NDLS→AGC environment is still explicitly synthetic

- `pashupatastra_realistic_dataset.json`: `"synthetic": true`. Its `note` says station/trains
  are "illustrative, not an official timetable" (read-only check).
- `load_corridor_dataset` refuses anything else (`corridor_dataset.py:156-163`).
- `_canonical_possession_inputs` labels the provider `SYNTHETIC_SCHEDULED`
  (`service.py:786-791`). Runtime check: timetable = SYNTHETIC, possession = SYNTHETIC.
- Assets on that corridor are generated (`corridor_dataset.py:200-203`), so asset_condition =
  SYNTHETIC.
- **Not converted to REAL_STATIC. The Step 15 gate (AMBER, NOT ELIGIBLE) is untouched.** Step 15
  concerns the AII–RE corridor and imposes no change here.

### 7.4 Minor documentation drift (not a code defect)

The docstring of `from_possession_source` (`provenance.py:295-307`) still says the jobs pipeline
"never consults backend.app.data.train_provider at all". Since Step 10 it does, on the dataset
corridor (`service.py:763-812`). Worth correcting when that file is next edited.

---

## 8. FUTURE GOVERNMENT-DATA ARCHITECTURE

Can each feed be accepted **without rewriting the optimizer**?

The answer is yes for everything that can be expressed as possession windows, candidate time
bounds, resource keys or objective coefficients. The solver only knows `OptimizationRequest`, so
the optimizer core is well isolated. The **jobs service**, not the solver, is where feeds are
currently hard-wired.

| Feed | Existing seam | Status | What would eventually be introduced |
|---|---|---|---|
| Authorized train-position (RTIS-class) | `TrainDataProvider.fetch() -> TrainDataSnapshot` (`train_provider.py:47-57`) | **ARCHITECTURALLY READY** for stop-time-shaped data; **BLOCKED BY EXTERNAL DATA** | A position → `SectionTraversal` adapter, a freshness/staleness gate on `observed_at`, a settled derived-provenance rule (currently raises), and a re-plan trigger. The solver stays unchanged |
| Timetable feed | Same Protocol + `StaticTimetableProvider` | **ARCHITECTURALLY READY** | A provider selection mechanism (today `JobService._canonical_possession_inputs` constructs `StaticTimetableProvider` directly, `service.py:786-791`), plus the horizon-coverage gate (§6.4) |
| Railway topology | `SectionRegistry.from_stations`, `CorridorTopology.from_stations` | **PARTIALLY READY**: the registry is the single authority, but it is loaded from one JSON file | A `TopologyProvider` returning stations/sections/running lines with provenance. Step 15 constraints apply (direction-independent sections, datum-carrying chainages, no invented UP/DOWN lines) |
| Maintenance system (job source) | None. Jobs come only from `POST /jobs` | **MISSING** | A `MaintenanceJobSource` interface (import, dedup, external id), mapping onto the same `create_job` validation |
| Asset condition system | None. Assets are generated inside the service and dataset loader | **MISSING** | An `AssetConditionProvider` (defects, inspections, condition score, with provenance) consumed by `ScoringFeatureAdapter` |
| Possession / block system (COA-class) | None. Possession is derived or generated only | **MISSING** | A `PossessionProvider` producing **granted** windows, intersected with train-free derivation (never unioned). The solver already consumes `PossessionWindow`, so no solver change is needed |
| Forecast data | None | **MISSING** | Enters as scoring features or candidate time bounds |
| Weather data | None | **MISSING** | Enters as window curtailment (reuse the disruption semantics) or scoring features |
| Scoring model | Direct function call | **MISSING** | A `ScoringProvider` with model id and version recorded per job |

**Selection and wiring gap.** Provider choice today is a two-branch `if` on
`self.dataset.timetable_records` (`service.py:755-761`) plus an env var for the corridor. A
future deployment needs explicit per-axis provider configuration that fails closed when a
provider is unavailable, instead of falling back to another source.

---

## 9. SECURITY / GOVERNMENT-DEPLOYMENT GAP

### 9.1 Implemented security-relevant controls

| Control | Evidence |
|---|---|
| Append-only optimization audit, enforced in both the class and SQL triggers | `audit/repository.py:11-22, 75-95`; trigger tests |
| Duplicate run id rejected, not overwritten | `audit/repository.py:99-107` |
| Fail-closed possession and provenance boundaries | §1.2 |
| Terminal-status guards at service **and** repository level | `repository.py:33, 365-370, 486-490, 553-557` |
| Input validation: schema, `extra="forbid"`, length limits | `jobs/models.py:25-37` |
| Parameterised SQL (no string-built values); the only f-string SQL uses fixed column names and placeholders | `repository.py:113-118, 216-228` |
| Atomic batch writes; connections closed | `repository.py:327-449`; `test_repository_does_not_leak_connections` |
| Test isolation from the runtime DB | `backend/tests/conftest.py`; `test_tests_do_not_use_the_repository_root_database` |
| Runtime DB and `.env` git-ignored | `.gitignore` |
| Audit rejection summary bounded (no timetable payload copied) | `jobs/optimization.py:160-190` |

### 9.2 Future requirements (not implemented)

| Area | Current state | Requirement |
|---|---|---|
| Authentication | None. Every route is anonymous | Identity for every call. Demo: a clearly-labelled stub principal. Deployment: government-approved identity provider / SSO |
| Authorization / roles | None | Role boundaries: worker (report, execute, complete, evidence), planner (optimize), authority (approve, postpone, reject), auditor (read-only). Enforced server-side per transition |
| Authority hierarchy | None | Section → division → zone chain for escalation and delegation |
| Separation of duties | None | Reporter ≠ approver; completer ≠ completion verifier |
| Audit integrity | Rows are immutable, but the SQLite file itself can be replaced or edited offline | Hash-chained or signed event records, off-host replication, a per-job event log |
| Evidence integrity | Not applicable (no evidence) | Content hash at upload, immutable storage, capture metadata, chain of custody |
| File upload security | Not applicable | Size and type allow-lists, content sniffing, no execution path, storage outside web root, malware scanning in deployment |
| Sensitive operational data | Train movements, possession windows and defect locations are served anonymously with CORS `*` (`api/main.py`) | Restrict origins; authenticated reads; classify operational data; TLS |
| API security | No rate limiting, no request size limits beyond Pydantic, docs (`/docs`, `/openapi.json`) public | Rate limiting, disabling docs in production, error-detail hygiene (errors currently echo internal messages) |
| Concurrency | In-process lock only (`optimization.py:245-259`); `notify`/`complete` not serialised with optimize | DB-level locking or optimistic concurrency (version column) |
| Secrets | None used today. Env vars only for paths and corridor | A secrets manager when feeds and identity providers arrive |
| Data retention | None: rows kept forever, no archival or deletion policy | A retention schedule per record class. Audit retention ≥ the statutory period (to be confirmed with the owner) |
| Deployment | SQLite file at repo root; single uvicorn process | Managed DB, backups, migrations, multi-instance safety |

No claim of any compliance certification is made or implied by anything in this repository.

---

## 10. DEMO-CRITICAL GAPS

**Target demo:** worker reports a critical job → before evidence → AI scores → optimizer
schedules around trains and possession → authority receives proposal → authority postpones →
earliest feasible date after postponement → authority approves → committed/pinned → execution →
after evidence → completed → full audit trail.

| # | Step | Today |
|---|---|---|
| 1 | Worker reports a **critical** job | 🟡 Report works; "critical" cannot be stated |
| 2 | Attaches before evidence | ❌ |
| 3 | AI scores it | 🟡 Scores, but from the synthetic nearest asset, not the report |
| 4 | Scheduled around trains/possession | 🟡 Works only if `PASHUPAT_CORRIDOR_ID=CORR-NDLS-AGC`. The default corridor models no trains |
| 5 | Authority receives proposal | 🟡 `GET /jobs?status=scheduled` only. No authority role, no notification |
| 6 | Authority postpones | ❌ |
| 7 | Earliest feasible date after postponement | ❌ Single fixed day |
| 8 | Authority approves | 🟡 `/notify` does it anonymously |
| 9 | Committed / pinned | ✅ |
| 10 | Execution occurs | ❌ |
| 11 | After evidence | ❌ |
| 12 | Job completes | 🟡 Without evidence |
| 13 | Audit trail of the whole history | ❌ Only optimization runs are recorded |

### 10.1 Gap list

| ID | Gap | Priority | Why | Dependencies | Complexity |
|---|---|---|---|---|---|
| G0 | **Commit the Sprint 3 working tree** | **P0** | Audit, SectionRegistry, provenance and the canonical path exist only uncommitted (§0) | — | Low |
| G1 | **Demo corridor = `CORR-NDLS-AGC`** (config/default decision) | **P0** | Otherwise "around trains" is false in the demo | G0 | Low |
| G2 | **Job event log**: append-only `job_events` (job_id, from_status, to_status, actor, role, reason, payload, run_id, at) written by every transition, plus `GET /jobs/{id}/history` | **P0** | Step 13 and all accountability depend on it. Every later slice writes into it | G0 | Medium |
| G3 | **Actor identity stub**: an explicit principal + role on each request (a clearly-labelled demo header or token), replacing `SYSTEM_ACTOR` | **P0** | Without it the audit trail cannot show worker vs authority | G2 | Low–Medium |
| G4 | **Horizon-coverage fail-closed gate** (§6.4) | **P0** | Multi-day scheduling fails open without it | G0 | Medium |
| G5 | **Multi-day horizon + persisted calendar schedule**: pipeline horizon > 1 day, anchor stored with each schedule, calendar datetimes on `JobResponse` | **P0** | Postponement to a date is not expressible without it | G4 | Medium–High |
| G6 | **Dataset timetable coverage for the demo days**: service dates beyond 2026-09-10, still SYNTHETIC | **P0** | G4 would (correctly) refuse any day with no trains | G4, G5. Dataset change, stays `"synthetic": true` | Low–Medium |
| G7 | **Authority decision endpoints**: approve (performs today's commit with actor + note), postpone (`not_before` + reason) → `postponed` → re-optimize → `scheduled`. Approve only from `scheduled`; the authority role is required | **P0** | Core of the government workflow | G2, G3, G5 | Medium |
| G8 | **Per-job not-before in candidate assembly**: `earliest_start_minute` from the postponement date instead of hard-coded 0 | **P0** | This is what "earliest feasible date after X" means for the solver. It is already supported by `solve()` | G5, G7 | Low |
| G9 | **Clear stale proposals on refusal** (§3.3 defect 1) | **P0** | Otherwise an authority can approve a window the solver just refused | G2 | Low |
| G10 | **Before/after evidence**: upload endpoint, SHA-256, type/size limits, evidence table linked to job + phase + actor | **P0** | Steps 2 and 11 | G2, G3 | Medium |
| G11 | **Execution + completion with evidence**: `notified → executing → completed`, completion requires after-evidence | **P0** | Steps 10–12 | G7, G10 | Medium |
| G12 | **Worker-reported severity** in the report, used by scoring and labelled as field-reported | **P0** | Step 1 "critical" + step 3 must reflect it | G2 | Low–Medium |
| G13 | Run ↔ job linkage (`run_id` on each job event from optimize) | P1 | Audit shows which run proposed which window | G2 | Low |
| G14 | Not-completed + reason → re-report / postpone | P1 | In the target lifecycle, not in the demo script | G11, G7 | Medium |
| G15 | Proposal stability on re-optimization (keep already-proposed windows unless infeasible) | P1 | Postponing one job must not reshuffle others under review | G7 | Medium |
| G16 | Fix committed block status regression (§3.3 defect 2) and serialise notify/approve with optimize (defect 6) | P1 | Contract consistency and a race | G7 | Low |
| G17 | Notification feed (in-app, derived from job events) | P1 | Demo narration "authority receives proposal", "worker notified" | G2 | Low–Medium |
| G18 | Time-validity check for pins (§3.3 defect 5) | P1 | Becomes reachable once possession varies by day | G5 | Medium |
| G19 | Geolocation on report and completion | P1 | Credibility of the field workflow | G10 | Low |
| G20 | Duplicate report detection (same track/section/overlapping span/work type/open) | P1 | Field reality | G2 | Low–Medium |
| G21 | Audit `/recover`; audit read API for runs | P2 | Completeness | G2 | Low |
| G22 | SLA deadline + escalation (configurable, emergency vs routine) | P2 for SIH | In the target lifecycle, not in the demo script. Can be shown as a computed "due by / overdue" flag | G2, G7 | Medium |
| G23 | Crew capacity constraint from `workers_min` | P2 | Judges may ask; not in the demo script | — | Medium |
| G24 | ASSUMED-parameter register surfaced in run provenance (§7.2) | P2 for SIH (P0 before any REAL data) | Honesty of claims | — | Medium |

---

## 11. POST-SIH GAPS (kept out of the SIH-critical list)

- Real RTIS / authorized train-position feeds, and the derived-provenance promotion rule
  (currently raises by design).
- Real timetable, topology (Step 15 gate AMBER), COA possession/block bookings and asset
  condition feeds.
- Pan-India / multi-corridor scaling (one corridor per process today); solver benchmarking at
  scale; managed DB; multi-instance locking.
- Mobile field app: offline capture, camera, GPS, sync.
- Predictive maintenance: trained models with a model registry and versioning, replacing the
  fixed-weight scorer behind a `ScoringProvider`.
- Weather and forecast integration.
- Immersive digital twin.
- Advanced analytics: KPI trends, backlog ageing, section reliability.
- Enterprise authentication/SSO, the full authority hierarchy, delegation, separation of duties.
- Government deployment hardening: TLS, secrets management, retention policy, hash-chained
  audit, backups/DR, security review, and any certification the owner requires.
- Machine/equipment resources, travel/setup time, possession-booking optimization.

---

## 12. RECOMMENDED IMPLEMENTATION ORDER

Vertical slices. Each slice ends with a demonstrable HTTP flow, tests, and events in the job
history. Safety prerequisites come before the features they protect.

**Step 0 — Baseline (no behaviour change).** Commit the audited working tree as-is, so every
later slice diffs against a real baseline (G0). Settle the demo corridor configuration (G1).

**Slice 1 — Accountable job lifecycle (foundation).**
Append-only `job_events` with the same trigger-based immutability pattern as
`optimization_runs`; a demo actor/role principal (G3); write events for create, optimize outcome
(with `run_id`), notify and complete (G2, G13); `GET /jobs/{id}/history`. Fix stale proposals on
refusal (G9).
*Demo after slice:* report → optimize → commit → complete, with a full timeline of who, what and
why.

**Slice 2 — Honest field report.**
Worker-reported severity feeding scoring, with the score explanation and a field-reported label
at top level of `JobResponse` (G12); expose `section_id` and derived station names; geolocation
fields (G19); duplicate detection (G20).
*Demo:* a worker reports a CRITICAL defect, and the explanation shows why it ranks first.

**Slice 3 — Evidence.**
Before-evidence upload bound to the job and actor, with hash, limits and history events (G10).
*Demo:* the report carries a photo and its hash appears in history.

**Slice 4 — Calendar-safe multi-day planning (safety first).**
Horizon-coverage fail-closed gate (G4) **before** extending the horizon; then a multi-day
horizon, stored anchor and calendar datetimes (G5); synthetic timetable coverage for the demo
days, still `"synthetic": true` (G6); a time-validity guard for pins (G18).
*Demo:* the optimizer proposes a window on a specific date and refuses days with no train data.

**Slice 5 — Authority review.**
Approve (commit, with actor and note) and postpone (`not_before`, reason) endpoints with a role
check (G7); `earliest_start_minute` from the postponement (G8); re-optimize → new proposal;
proposal stability (G15); serialise decisions with optimization and fix block status regression
(G16).
*Demo:* authority postpones → system proposes the earliest feasible window after that date →
authority approves → pinned.

**Slice 6 — Execution and completion.**
`executing`, and completion that requires after-evidence (G11); not-completed + reason →
re-report/postpone (G14).
*Demo:* the full SIH script end-to-end with the history timeline.

**Slice 7 — Notifications and SLA.**
An in-app notification feed derived from job events (G17); response deadlines, overdue flags and
a single-level escalation (G22).

**Slice 8 — Honesty and hardening for Q&A.**
Audit `/recover` and a run read API (G21); an assumption register in run provenance (G24);
optional crew capacity (G23); restricted CORS.

**Slice 9 — Only after SIH:** §11.

Slice 1 is first because every later decision must be recorded as it happens; it cannot be
back-filled. Slice 4 comes before Slice 5 because postponement without calendar-safe planning
would fail open (§6.4).

---

## 13. FINAL VERDICT

### "Can the current backend already demonstrate the complete Pashupatastra government-style workflow?"

**No.**

It can honestly demonstrate a real and well-guarded **middle segment**: an anonymous report →
section-resolved → deterministically scored → CP-SAT scheduled inside train-derived synthetic
possession windows (on `CORR-NDLS-AGC`) → committed and pinned across re-optimization → marked
complete and excluded from future runs, with an immutable record of every optimization run and
honest SYNTHETIC provenance.

What prevents the complete workflow, in order of how hard it blocks:

1. **Postponement to a date cannot be expressed.** The jobs pipeline plans one fixed day
   (`OPTIMIZATION_HORIZON_MINUTES = 1440`, constant `DEFAULT_HORIZON_START`, hard-coded
   `earliest_start_minute=0` / `latest_end_minute=1440` in `create_job`). Simply extending the
   horizon would **fail open**, because timetable coverage of the horizon is not validated (§6.4).
2. **There is no authority.** No identity, no roles, no approve, postpone or reject.
   `/notify` is an anonymous commit that notifies nobody.
3. **There is no per-job accountability.** Only optimization runs are audited; job decisions
   leave no actor, reason or history.
4. **There is no evidence, execution or failure path.** No uploads, no executing state, no
   after-evidence, no not-completed/re-report.
5. **The report cannot say "critical".** Scoring uses synthetic asset attributes, not the
   worker's report.
6. **The default deployment corridor models no trains.** "Around trains" is true only with
   `PASHUPAT_CORRIDOR_ID=CORR-NDLS-AGC`.
7. **The foundation is uncommitted** (§0).

### "What is the smallest set of implementation steps required to make the complete workflow genuinely executable?"

Eight steps. Each builds on the one before.

1. **Commit the working tree** and set the demo corridor to `CORR-NDLS-AGC`.
2. **Job event log + demo actor/role principal**: every transition records actor, role, reason,
   before/after status and run id; `GET /jobs/{id}/history`; clear stale proposals on refusal.
3. **Report fields**: worker severity (used by scoring and labelled field-reported),
   geolocation, top-level section and explanation.
4. **Evidence upload** (before and after phases, hashed, linked to job and actor).
5. **Horizon-coverage fail-closed gate**, then **multi-day horizon with a stored anchor and
   calendar times**, plus synthetic timetable coverage for the demo days.
6. **Authority endpoints**: approve (commit), and postpone with `not_before` →
   `earliest_start_minute` → re-optimize → new proposal. Role-checked and event-logged.
7. **Execution and completion**: `notified → executing → completed`, with after-evidence
   required.
8. **Minimal notification feed** derived from job events, so "authority receives proposal" and
   "worker notified" are real events rather than narration.

The CP-SAT solver needs **no rewrite** for any of these. Steps 5–6 use constraint inputs it
already honours (`earliest_start_minute`, `horizon_minutes`, `existing_committed_blocks`,
possession windows). Everything else is jobs-service, persistence and API work over the existing
fail-closed data boundaries, and **none of it is blocked by government data**.

---

## APPENDIX A — REPOSITORY INTEGRITY

Baseline captured at the start of this step (SHA-256):

| File | SHA-256 |
|---|---|
| `jobs.db` | `57db9010f5f594bce1cb796261448dcdfeb40d39a2643e8f7fcde8f9d12f5714` |
| `FINAL_BACKEND_SYSTEM_AUDIT.md` | `b74d16acacc7b58068f412f0ffade17cee167807fddf35798b812a0519c36b04` |
| `PASHUPATASTRA_FINAL_DEMO_PLAYBOOK.md` | `e5676e48283202e7b85311ec3f2ef64d11d43d45d83ffa50b8513e76a97ee7e8` |
| `SIH2026-IDEA-Presentation-Format.pdf` | `e079d901f3f603a6045dffe9d04c5f6fcc19cc11aee2a77ca04d5e7213cfd848` |
| `git diff` (all tracked changes) | `fe438bfb27a018e9cbf97908d3395d74038820ca46cb0c473a8cfd8bdfbca6da` |

`stash@{0}` = `3e657d7c6096ea2a13044d4100682e9f2f369e0d`; HEAD = `d9f0264`. The pre-existing
`M`/`??` entries in the working tree predate this step. Post-write verification is in the
accompanying response.
