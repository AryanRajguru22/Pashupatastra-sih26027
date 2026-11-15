# Pashupatastra — Frontend + Demo Product Specification (Stitch Handoff)

| | |
|---|---|
| **Status** | Authoritative handoff for Stitch (visual generation) and the Claude Code integration that follows |
| **Backend baseline** | `cc4fae2` (HEAD = origin/main), frozen for frontend integration |
| **API contract** | `docs/api-contract-v1.md` + `docs/openapi-v1.json` (all lifecycle routes under `/v1`) |
| **Demo scenario** | `scripts/seed_demo.py` (seed/reset) and `scripts/seed_demo.py --verify` (rehearsal) |
| **Demo environment** | `PASHUPAT_CORRIDOR_ID=CORR-NDLS-AGC`, `PASHUPAT_JOBS_DB=<absolute path of a dedicated demo DB>`, auth mode unset (demo) |
| **Deadline** | Frontend freeze on **26 Sep 2026** |

> **Synthetic data statement (applies to the whole document and the whole UI).**
> The seeded NDLS–AGC scenario is **synthetic and illustrative**. The corridor uses
> real Indian Railways station **codes** (NDLS, NZM, FDB, PWL, MTJ, RKM, AGC), but its
> topology/chainage, timetable, asset condition and possession windows are synthetic.
> The optimizer labels every run `provenance.effective = SYNTHETIC`. The seed jobs are
> invented, and the evidence references are placeholders under `synthetic-demo/`.
> **Nothing in this system is Indian Railways operational data and nothing is live.**

### How this document was produced

The document is based on the code at `cc4fae2`: the router, models, service,
lifecycle, events, proposal, optimization, obligations, identity, scorer and seed
script. It is not based on the older design documents. Every payload value quoted
as *"observed in rehearsal"* came from one run of the seed's own `rehearse()`
sequence against a **temporary** database on the demo machine. That run used an
ENGINEER actor for optimization. The root and demo `jobs.db` files were hashed
before and after and did not change. Job IDs, event IDs, run IDs and wall-clock
timestamps are random or run-dependent. Placements are reproducible only for the
same OR-Tools version, solver configuration and job-creation order. Only the
invariants that `--verify` checks are **guaranteed** (see §2.3).

---

## Table of contents

1. Product purpose
2. Primary demo story
3. User roles
4. Navigation / information architecture
5. Command Center
6. Field Inspection screen
7. Maintenance Job Queue
8. Explainable Risk Scoring
9. Optimization / block planning
10. Proposal review
11. Authority actions
12. Re-optimization
13. Execution
14. Audit timeline
15. SLA / obligations
16. Operational simulation
17. Data provenance
18. Visual design direction for Stitch
19. Consolidated Stitch prompt
20. Claude Code integration plan
21. Critical path / deadline (P0/P1/P2)
22. Final acceptance test
23. Findings: blockers, assumptions, do-not-implement list

---

## 1. Product purpose

### 1.1 What Pashupatastra does

Pashupatastra is a **decision-support system for railway maintenance block
planning**. It takes a field defect report through a single, auditable path:

1. A field worker reports a defect at a human-readable location (for example,
   "5.7–6.0 km from Mathura Jn toward Raja Ki Mandi, DOWN line").
2. The system converts the location to a canonical section and track and records
   the evidence reference.
3. The job gets an explainable priority/risk score from a deterministic baseline
   scorer.
4. An engineer runs a constraint optimizer (Google OR-Tools CP-SAT). It places the
   job inside a **possession window**, a train-free gap derived from the corridor
   timetable, without disturbing already-committed work.
5. An authority approves, postpones or rejects the proposal, with a reason.
6. The crew executes the committed block and records before-work and after-work
   evidence references.
7. Every step is recorded in an append-only history. The system continuously
   derives who owes the next action and by when (SLA obligations).

### 1.2 The railway problem it solves

Maintenance needs **track possession**: a stretch of line closed to trains. On a
dense trunk route, possession time is scarce. Today, placing work in that time
depends on manual coordination between field staff, engineers and the authority
that grants the block. That coordination has weak traceability, and a late
change can silently disturb blocks already agreed. Pashupatastra keeps
three things together:

- **Safety**: work is placed only inside possession windows, and committed blocks are pinned.
- **Accountability**: every decision has a named, declared actor, a reason and a before/after state.
- **Explainability**: each score and each placement carries structured reasons.

### 1.3 Primary user journey

`Field report → Job queue (scored) → Optimize → Proposal review → Authority decision → Re-optimize → Crew execution with evidence → Completion → Audit + obligations`

### 1.4 What the SIH demo must prove

1. A field report becomes a scored, located, auditable maintenance job, **through the UI with no Swagger**.
2. The optimizer places work only inside possession windows and explains the placement with structured reason codes.
3. An authority's approve, postpone and reject decisions have real, visible effects:
   - an approved block is **preserved** across re-optimization;
   - a postponed job **moves to the permitted date**;
   - a rejection is recorded with its reason.
4. Execution requires evidence references and moves the job to a terminal `completed` state.
5. Every step appears in the audit timeline with actor, role, identity assurance and state before/after.
6. Obligations show who owes the next action, against an **explicitly assumed** SLA policy.
7. The team is honest about data. Everything is synthetic, identities are declared and not authenticated, and the scorer is not a trained ML model.

### 1.5 Implemented today vs demo simulation vs future

| Capability | Implemented today (real code, `/v1`, persisted) | Demo simulation / synthetic | Future / production (NOT built) |
|---|---|---|---|
| Field intake with station+offset location cross-check | ✅ `POST /v1/jobs` | Corridor topology is synthetic | Real asset register, GIS |
| Idempotent intake | ✅ `idempotency_key` | — | — |
| Explainable risk/priority score | ✅ deterministic baseline (`BASELINE_DETERMINISTIC`, `baseline-deterministic-1.0`) | Asset features come from generated synthetic assets | Trained model (would carry a different model type) |
| CP-SAT optimization over possession windows | ✅ `POST /v1/corridors/{id}/optimize-jobs` | Windows derived from a **synthetic** timetable (`CANONICAL_TIMETABLE_DERIVED`) | Live timetable / real possession feed |
| Proposal with structured explanation | ✅ `GET /v1/jobs/{id}/proposal` | — | — |
| Approve / postpone / reject / release | ✅ `/v1/jobs/{id}/proposal/*` | — | — |
| Committed-block preservation on re-optimization | ✅ `COMMITTED_BLOCK_PRESERVED` event | — | — |
| Execution start / complete / not-completed with evidence refs | ✅ `/v1/jobs/{id}/execution/*` | Evidence refs are placeholder strings; **no file storage** | Evidence upload/storage, geofencing |
| Append-only audit history | ✅ `GET /v1/jobs/{id}/history` | — | Hash chain (designed, not built) |
| Derived SLA obligations | ✅ `/v1/obligations`, `/v1/jobs/{id}/obligation` | SLA values are **assumed demo engineering values** | Real IR policy, notifications, scheduler |
| Identity | Headers `X-Actor-Id`/`X-Actor-Role`, recorded `DECLARED_UNVERIFIED` | Demo personas | Authentication and enforced RBAC. Enforced HTTP mode is refused at boot today. |
| Disruption what-if (Plan/Disrupt/Recover) | ✅ legacy `POST /optimize`, `POST /recover` (stateless) | **Simulation** on the synthetic `CORRIDOR_A` fixture, not NDLS–AGC | Disruption on the live job pipeline |
| Train delay / ETA / live conflict detection | ❌ | ❌ | Needs new backend work |
| Notifications (SMS/email/push) | ❌ (deliberately absent; no API) | ❌ | Future |
| Maps / live train tracking | ❌ | ❌ | Future |

---

## 2. Primary demo story

### 2.1 Cast and fixed values

| Persona (UI) | `X-Actor-Id` | `X-Actor-Role` | Source |
|---|---|---|---|
| Field worker / crew | `WORKER-042` | `WORKER` | `seed_demo.CREW_ID` (reporter of the live intake and the crew that executes) |
| Planning engineer | `ENGINEER-007` | `ENGINEER` | **UI choice.** The seed has no engineer ID; any ID matching `^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$` is valid. The contract's intended optimize role is ENGINEER. |
| Section authority | `AUTHORITY-017` | `AUTHORITY` | `seed_demo.AUTHORITY_ID` |

Seeded jobs (all `reported` after the seed; seed reporters `WORKER-021/033/042/058`):

| Demo key | Work type | Severity | Field location | Track | Section | Duration | Priority / Risk (observed) |
|---|---|---|---|---|---|---|---|
| `ballast-rkm-agc` | BALLAST_TAMPING | MODERATE | RKM → AGC, 3000–3600 m | DOWN-1 | RKM-AGC | 120 min | 0.6845 / 0.6334 |
| `ohe-rkm-agc` | OHE_MAINTENANCE | MODERATE | RKM → AGC, 1000–1400 m | UP-1 | RKM-AGC | 90 min | 0.6188 / 0.5720 |
| `inspection-mtj-rkm` | ROUTINE_INSPECTION | MINOR | MTJ → RKM, 500–900 m | UP-1 | MTJ-RKM | 45 min | 0.4932 / 0.4106 |
| `signal-fdb-pwl` | SIGNALLING_INTERLOCKING | MODERATE | FDB → PWL, 4000–4300 m | DOWN-1 | FDB-PWL | 90 min | 0.6628 / 0.6251 |

**Live intake**, entered in the UI during the recording. Use exactly these values,
which `seed_demo.py` also prints:

| Field | Value |
|---|---|
| job_type | `EMERGENCY_REPAIR` |
| severity | `CRITICAL` |
| track_id | `DOWN-1` |
| field_location | `MTJ` → `RKM`, offset 5700–6000 m |
| distance_start / distance_end | `138700` / `139000` (MTJ is at km 133.0 → 133 000 + 5 700 and 133 000 + 6 000; see §6.3) |
| workers_min / workers_max | `2` / `4` (the seed's values; keep them so the rehearsal and the recording match) |
| description | `Rail fracture found on patrol; caution order issued.` |
| evidence_reference | `synthetic-demo/inspection/fracture-mtj-rkm.jpg` |
| reporter | `WORKER-042` / `WORKER` |

Observed score for the live intake: **priority 0.7944, risk 0.7729**. Both are ≥ 0.75,
so both fall in the scorer's HIGH band. The job gets section `MTJ-RKM` and a 90-minute
duration.

Authority reasons, verbatim from the seed:

- Postpone `ballast-rkm-agc` to **2026-09-11**, with reason `Festival special trains on the RKM-AGC down line on 10 Sep`.
- Reject `inspection-mtj-rkm`, with reason `Covered by the scheduled trolley inspection this week`.

**Planning horizon.** All placements are minutes from `2026-09-10T00:00:00+05:30`,
with a horizon of 2880 minutes: **10 Sep 00:00 IST to 12 Sep 00:00 IST, 2026**. The UI
must present this as *"Planning horizon: 10–11 Sep 2026 (synthetic)"*, never as
"today".

### 2.2 Stage-by-stage trace

Conventions: *"Plan time"* means horizon minutes rendered in IST. *"Recorded"*
means the server's wall-clock `occurred_at`/`created_at` (UTC in the API; render in
IST). Every mutating call carries the persona's two actor headers.

| # | Stage | User | UI screen | Data displayed | Action | API | Expected response / state change | Next |
|---|---|---|---|---|---|---|---|---|
| 1 | Field inspection | Worker (WORKER-042) | **Field Inspection** form | Corridor `CORR-NDLS-AGC` (read-only), station pickers, track, work type, severity | Open form; optionally "Load demo script values" | — (no call) | — | 2 |
| 2 | Location / section entry | Worker | Field Inspection → *Location* group | From station, toward station (adjacent only), offsets in metres; live preview: section id, section length, derived corridor-absolute metres | Enter MTJ → RKM, 5700–6000 m, DOWN-1 | — (client-side conversion, §6.3) | Preview shows section **MTJ-RKM**, derived **138 700 – 139 000 m** | 3 |
| 3 | Defect / maintenance info | Worker | Field Inspection → *Defect* group | Work type (6 enum values) with default duration shown for information, severity (4 values), description counter (≤1000), crew size min/max | Select EMERGENCY_REPAIR, CRITICAL, type description, 2/4 | — | — | 4 |
| 4 | Evidence reference | Worker | Field Inspection → *Evidence* group | Text field "Evidence reference (ID or path in another system)" ≤200 chars; helper: *"Reference only. No file is uploaded or stored."* | Enter `synthetic-demo/inspection/fracture-mtj-rkm.jpg` | — | — | 5 |
| 5 | Maintenance job creation | Worker | Field Inspection → Submit | Confirmation summary | **Submit report** | `POST /v1/jobs` (body §6.2, headers WORKER) | `201 JobResponse`: `status: "reported"`, `job_id: JOB-XXXXXXXXXX`, scores set, `schedule_*: null`. Events `JOB_CREATED` (worker) + `JOB_SCORED` (`SYSTEM:SCORER`) | 6 (auto-navigate to the new Job Detail) |
| 6 | Explainable risk scoring | Any | **Job Detail** → *Explainable Risk Scoring* panel | `priority_score`, `risk_score`, and from the `JOB_SCORED` event metadata: `scoring_explanation` (e.g. "Severe defect", "Maintenance overdue", "High asset criticality"), `scoring_features` (raw inputs), `scoring_model_type`, `scoring_model_version`, `reported_severity` | Read | `GET /v1/jobs/{id}`, `GET /v1/jobs/{id}/history` | — | 7 |
| 7 | Job queue | Engineer | **Maintenance Jobs** queue | 5 jobs, all `reported`; the new CRITICAL job at the top when sorted by priority | Review | `GET /v1/jobs?limit=200` (follow `next_cursor`) | — | 8 |
| 8 | Optimization | Engineer (ENGINEER-007) | **Planning** → *Run optimization* | Corridor, horizon 10–11 Sep, active job count, last run summary | **Run optimization** (confirm dialog) | `POST /v1/corridors/CORR-NDLS-AGC/optimize-jobs` | `200`: `solver_status: "OPTIMAL"`, `counts {considered 5, committed 0, scheduled 5, unscheduled 0}`, `possession_derivation: CANONICAL_TIMETABLE_DERIVED`, `possession_window_count: 287`, provenance all `SYNTHETIC`. All 5 jobs move to `scheduled`. Events per job: `OPTIMIZATION_REQUESTED` (engineer), `OPTIMIZATION_COMPLETED`, `BLOCK_PROPOSED` (`SYSTEM:OPTIMIZER`). Obligations become `APPROVAL_PENDING` (AUTHORITY), `WITHIN_SLA` | 9 |
| 9 | Block proposal | Authority | **Proposal Review** (from the queue or the Planning results) | Placement in plan time, duration, section/track, scores, objective score, explanation codes verbatim, provenance, run id | Open proposal | `GET /v1/jobs/{id}/proposal` | `200 BlockProposalResponse` (§10) | 10 |
| 10 | Train / possession safety info | Authority | Proposal Review → *Safety basis* block | `POSSESSION_WINDOW_REQUIRED` detail (window in plan time), `CONFLICTING_TRAINS_AVOIDED` or `NO_TRAIN_LEVEL_DATA` detail **verbatim**, `COMMITTED_BLOCKS_RESPECTED`, run's `possession_derivation`, window count, `uncovered_tracks` | Read | (same proposal payload; the run summary comes from the optimize response or `GET /v1/optimization-runs/{run_id}`) | — | 11 |
| 11 | Authority review | Authority (AUTHORITY-017) | Proposal Review → *Decision* bar | Three buttons: Approve, Postpone, Reject; obligation chip "Awaiting AUTHORITY, WITHIN_SLA, due …" | Choose an action | `GET /v1/jobs/{id}/proposal` **re-fetched** right before opening the dialog | — | 12/13/14 |
| 12 | Approve | Authority | Approve dialog on the **CRITICAL fracture** job | Before → after: `scheduled` → `notified` ("Approved, committed"), placement **11 Sep 00:16–01:46 IST** (plan minutes 1456–1546), run id | **Approve and commit block** | `POST /v1/jobs/{id}/proposal/approve` `{expected_proposal_run_id}` | `200 {job, message: "Proposal approved"}`: `status: "notified"`, block `COMMITTED`. Event `BLOCK_COMMITTED` (authority). Obligation → `EXECUTION_START_PENDING` (WORKER) | back to queue |
| 13 | Postpone | Authority | Postpone dialog on **ballast-rkm-agc** | Current placement **10 Sep 00:00–02:00 IST** (0–120); date selector limited to horizon dates; reason (required) | Select **2026-09-11**, enter the reason, **Postpone** | `POST /v1/jobs/{id}/proposal/postpone` `{expected_proposal_run_id, reason, selected_date: "2026-09-11"}` | `200 {message: "Proposal postponed"}`: `status: "reported"`, placement cleared, `block_candidate.earliest_start_minute: 1440`, `last_refusal_reason` = the reason. Event `PROPOSAL_POSTPONED` (metadata: `not_before_minute`, `original_start/end_minute`, `selected_date`). Obligation → `NONE` / `NO_OBLIGATION_AWAITING_OPTIMIZATION` | back to queue |
| 14 | Reject | Authority | Reject dialog on **inspection-mtj-rkm** | Current placement **11 Sep 18:07–18:52 IST** (2527–2572); reason (required); honest note (§11.4) | Enter the reason, **Reject proposal** | `POST /v1/jobs/{id}/proposal/reject` `{expected_proposal_run_id, reason}` | `200 {message: "Proposal rejected"}`: `status: "reported"`. Event `PROPOSAL_REJECTED` (metadata `rejected_start_minute`/`rejected_end_minute`). `GET …/proposal` now returns `409 NO_CURRENT_PROPOSAL` with a detail naming the rejecting authority | 15 |
| 15 | Re-optimization | Engineer | **Planning** → *Run optimization* → *Re-optimization comparison* | Previous run vs new run per job (§12) | **Run optimization** | `POST /v1/corridors/CORR-NDLS-AGC/optimize-jobs` | `200`: `OPTIMAL`, `counts {considered 5, committed 1, scheduled 5, unscheduled 0}`. The fracture job's committed block is **unchanged** (event `COMMITTED_BLOCK_PRESERVED`). Ballast is now on **11 Sep 15:15–17:15** (2355–2475, day 2). The rejected inspection is **proposed again at the same placement** (2527–2572). Other open proposals are re-proposed (`BLOCK_REPROPOSED`; placements may change) | 16 |
| 16 | Execution start | Worker/crew (WORKER-042) | **Execution** → fracture job → *Start execution* dialog | Committed block (plan time), run id, planned start/end; form: actual start (default = planned start + 1 min in plan time, IST offset), ≥1 before-work evidence item | **Start execution** | `POST /v1/jobs/{id}/execution/start` `{expected_proposal_run_id: job.proposal_run_id, actual_start_at, before_work_evidence:[…]}` | `200 {job, execution, message: "Execution started"}`: `status: "in_progress"`, `execution.execution_id: EXE-…`, `status: "STARTED"`, `deviations.started_before_planned_start: false`. Event `EXECUTION_STARTED`. Obligation → `COMPLETION_PENDING` (WORKER) | 17 |
| 17 | Evidence | Worker | Start/Complete dialogs → *Evidence items* repeater | Reference (≤200), kind (`PHOTO`/`VIDEO`/`DOCUMENT`/`MEASUREMENT`), captured_at (explicit offset), optional lat/long **pair**, optional note (≤500) | Add item(s) | (part of 16/18) | Server mints `evidence_id` `EVD-…` and `phase` (`BEFORE_WORK`/`AFTER_WORK`) | 18 |
| 18 | Completion | Worker | **Execution** → *Complete execution* dialog | Execution id, actual start, form: actual end (default = planned end), ≥1 after-work evidence item (a before-work reference must not be reused) | **Complete execution** | `POST /v1/jobs/{id}/execution/complete` `{execution_id, actual_end_at, after_work_evidence:[…]}` | `200`: `status: "completed"` (terminal), `execution.status: "COMPLETED"`, `deviations.ended_after_planned_end: false`. Event `EXECUTION_COMPLETED`. Obligation → `NONE` / `NO_OBLIGATION_TERMINAL` | 19 |
| 19 | Audit timeline | Any (judge-facing) | **Job Detail → Audit** (fracture job) | 11 events in order: `JOB_CREATED`, `JOB_SCORED`, `OPTIMIZATION_REQUESTED`, `OPTIMIZATION_COMPLETED`, `BLOCK_PROPOSED`, `BLOCK_COMMITTED`, `OPTIMIZATION_REQUESTED`, `OPTIMIZATION_COMPLETED`, `COMMITTED_BLOCK_PRESERVED`, `EXECUTION_STARTED`, `EXECUTION_COMPLETED` (plus any recorded refusals) | Scroll / expand | `GET /v1/jobs/{id}/history` | — | 20 |
| 20 | SLA / obligations | Any | **Obligations** | 4 open `APPROVAL_PENDING` obligations (AUTHORITY, `WITHIN_SLA`, due times in wall clock), the completed job `NONE`; banner "ASSUMED DEMO SLA, policy `sla-assumed-demo-1.0`, not Indian Railways policy" | Read / filter | `GET /v1/obligations` (+ `GET /v1/jobs/{id}/obligation`) | — | end; optional legacy simulation |

### 2.3 What is guaranteed vs illustrative

`--verify` asserts these properties, so they are **guaranteed** on the recording machine:

- Both optimizer runs return `OPTIMAL` or `FEASIBLE` with **0 unscheduled** jobs.
- Every placement lies inside a possession window on its own section and track, and has exactly the job's duration.
- `ballast-rkm-agc` is on **day 1** after run 1 and on **day 2** (`start_minute ≥ 1440`) after run 2.
- The approved fracture block has the **identical** placement after run 2 and stays `notified`, and run 2 recorded `COMMITTED_BLOCK_PRESERVED` for it.
- The reject target had a proposal to reject (it was `scheduled` after run 1).
- The fracture job ends `completed`.

**Illustrative only** (observed in rehearsal, not asserted; never hard-code them in the UI):

- Exact minutes for OHE (run 1: 2475–2565, run 2: 514–604) and signal (run 1: 2261–2351, run 2: 2259–2349).
- The rejected inspection's re-proposal at the same slot (2527–2572).
- The ballast day-2 placement 2355–2475.
- Fracture 1456–1546 and all scores.

The live intake must be entered **after** the seed and **before** the first optimize.
Solver placement depends on candidate (creation) order.

### 2.4 Timing rule for recording

SLA clocks run on **wall-clock time** and start when a proposal exists. The seed
deliberately does not optimize. For a CRITICAL job, approval becomes `DUE_SOON` at
75% of 2 h (about 90 min) and escalates at 2 h. **Record promptly after step 8.**
If rehearsing, reset with the seed first.

---

## 3. User roles

Roles come from `backend/app/identity/actor.py` (`ActorRole`), the intended
permission table in `backend/app/identity/role_actions.py`, and the request
dependency in `backend/app/api/deps.py`.

> **Demo-mode enforcement statement (must appear in the UI's About/help and in the persona switcher).**
> The backend composition in demo mode (`PASHUPAT_AUTH_MODE` unset) installs
> `UnenforcedPolicy`. Identity is **declared** through `X-Actor-Id` + `X-Actor-Role`
> and recorded as `DECLARED_UNVERIFIED`. The server **does not authenticate and does
> not enforce the role table** in this mode. It enforces only these rules:
> - both headers or neither (`400 ACTOR_HEADERS_INCOMPLETE`);
> - `SYSTEM` is refused (`403 ACTOR_SYSTEM_ROLE_FORBIDDEN`);
> - approve, notify, reject, postpone, release and all `execution/*` calls require a **named human** actor (`400 INVALID_TRANSITION` otherwise).
>
> Role gating in the UI is a **presentation convenience for the demo**. It must never
> be described as production authorization. The enforced (NOVAFORGE) HTTP mode is
> refused at boot in this release.

| Role | Responsibilities (intended, `ROLE_ACTIONS`) | Permitted demo actions | UI exposes to this persona | UI must NOT expose to this persona |
|---|---|---|---|---|
| **WORKER** | Report jobs; start/complete execution; report not-completed; read jobs | `POST /v1/jobs`; `execution/start`, `execution/complete`, `execution/not-completed`; all reads | Field Inspection form; Execution screen actions; read-only queue, detail, audit, obligations | Run optimization; Approve/Postpone/Reject/Release |
| **ENGINEER** | Request optimization; read optimization runs; read jobs. Owes `REPLANNING_PENDING` obligations and is the `attention_role` for conflicts/integrity refusals | `POST /v1/corridors/{id}/optimize-jobs`; `GET /v1/optimization-runs/{id}`; all reads | Planning screen (Run optimization, run audit record); read-only elsewhere | Field intake (`REPORT_JOB` is WORKER-only in the intended table, OQ-1 open); authority decisions; execution actions |
| **AUTHORITY** | Commit (approve), reject, postpone, release blocks; read jobs. Owes `APPROVAL_PENDING` | `proposal/approve` (or `notify`), `proposal/reject`, `proposal/postpone`, `proposal/release`; reads | Proposal Review decision bar; Release on committed jobs (P1); read-only elsewhere | Optimization run; field intake; execution actions. **Not granted `READ_OPTIMIZATION_RUN`** in the intended table (10.1D.3 D-2); the UI can still show the run summary that arrives in proposal/optimize responses |
| **ADMIN** | **Holds nothing** (administrative role with no railway scope) | None | **Do not offer ADMIN as a demo persona** | Everything |
| **SYSTEM** | Application actors recorded on events: `SYSTEM:SCORER`, `SYSTEM:OPTIMIZER` (assurance `SYSTEM_INTERNAL`) | Cannot be sent over HTTP (403) | Shown only in audit as "System" | Never selectable |
| **UNIDENTIFIED** | No headers sent (assurance `NONE`) | Reads; intake without a name (idempotency then refused) | Not a persona. The UI **always sends headers** | — |

---

## 4. Navigation / information architecture

### 4.1 Decision

The `/v1` lifecycle becomes the product's main path. The legacy Plan/Disrupt/Recover
workspace stays, **moved under its own clearly labelled "Disruption Simulation"
section**. It does not share KPIs or data with the lifecycle screens.

```
┌ Global status bar (always visible) ───────────────────────────────────────────┐
│ PASHUPATASTRA │ Corridor CORR-NDLS-AGC │ Horizon 10–11 Sep 2026 (synthetic) │
│ Backend ● CONNECTED │ DATA: SYNTHETIC │ DEMO MODE: identity declared │ Persona ▾ │
└────────────────────────────────────────────────────────────────────────────────┘
┌ Left nav ────────────┐
│ Command Center       │  /                       (P0)
│ OPERATE              │
│  Field Inspection    │  /inspection             (P0)
│  Maintenance Jobs    │  /jobs                   (P0)  → /jobs/[jobId] (detail, tabs)
│  Planning            │  /planning               (P0)  optimize + results + re-opt diff
│  Authority Review    │  /review                 (P0)  queue filtered to status=scheduled
│  Execution           │  /execution              (P0)  notified + in_progress jobs
│ ASSURANCE            │
│  Audit Trail         │  /audit                  (P0)  job picker → timeline (same component as detail tab)
│  Obligations (SLA)   │  /obligations            (P0)
│ SIMULATION           │
│  Disruption Sim.     │  /simulation             (P0 relabel of legacy; SIMULATION badge)
│ About this demo      │  /about                  (P1)  honesty statements, env, versions
└──────────────────────┘
```

### 4.2 What belongs where

| Area | Contains | Backed by |
|---|---|---|
| Command Center | Lifecycle KPIs, critical jobs, pending decisions, active/completed work, obligations summary, last optimization status, provenance | `/v1` only (§5) |
| Maintenance Workflow (Field Inspection, Jobs, Job Detail, Authority Review) | Intake, queue, scoring, proposal, decisions | `/v1/jobs*` |
| Planning / Optimization | Run optimization, run result, re-optimization comparison, run audit record | `/v1/corridors/{id}/optimize-jobs`, `/v1/optimization-runs/{id}` |
| Execution | Start, complete, not-completed (P1), execution records | `/v1/jobs/{id}/execution*` |
| Audit | Per-job timeline | `/v1/jobs/{id}/history` |
| Obligations | Derived obligations | `/v1/obligations`, `/v1/jobs/{id}/obligation` |
| Operational Simulation | Legacy Plan / Disrupt / Recover on the Corridor A synthetic fixture | Legacy `POST /optimize`, `POST /recover` (stateless; nothing persisted) |

### 4.3 Job Detail tabs (`/jobs/[jobId]`)

`Overview` (location, report, evidence ref, asset association, duplicate advisory) ·
`Scoring` · `Proposal` (only when `status == scheduled`; otherwise the state panel) ·
`Execution` · `Audit` · `Obligation`.

---

## 5. Command Center

Everything on this screen comes from `/v1` and `/health` **except** the legacy
simulation tile, which is visually separated and labelled SIMULATION.

### 5.1 Data sources

| Element | Source | Real `/v1`? | Notes |
|---|---|---|---|
| Backend connectivity badge | `GET /health` → `{"status":"healthy"}` | Real (connectivity only) | Proves the backend answered, **never** data provenance |
| Data provenance badge | `provenance.effective` from the latest optimize response / run (`SYNTHETIC`) | Real | Before any run: show `DATA: SYNTHETIC (seeded demo scenario)` from the static rule in §17 |
| Active jobs | Count of `GET /v1/jobs` items with `status != completed` | Real | Follow `next_cursor` until null |
| Status breakdown | Count by `status`: reported / scheduled / notified / in_progress / completed | Real | Display names in §18.5 |
| Critical jobs | Items with `block_candidate.metadata.reported_severity == "CRITICAL"` and not completed | Real, **non-contract field** | `block_candidate` is outside the frozen contract. Parse defensively; hide the tile if absent |
| Pending authority decisions | Count `status == scheduled`. Cross-check: `GET /v1/obligations?obligation_type=APPROVAL_PENDING` | Real | — |
| Awaiting execution / In progress | `notified` / `in_progress` counts | Real | — |
| Completed | `completed` count | Real | — |
| Open obligations, past due, needs engineer attention | `GET /v1/obligations` (all pages): `is_open`, `is_past_due`, `attention_required` | Real (derived at read time) | Show `evaluated_at` and the ASSUMED SLA label |
| Last optimization status | Session-held last optimize response. On reload, use the distinct `proposal_run_id` values of current jobs → `GET /v1/optimization-runs/{id}` → latest `completed_at` | Real | **No "list runs" endpoint exists.** If no job carries a run id, show "No optimization run yet" |
| Top-priority jobs table (5 rows) | `GET /v1/jobs`, sorted by `priority_score` desc | Real | — |
| Legacy tile "Disruption Simulation" | Link to `/simulation` | Fixture/simulation | **Never** show its KPIs (priority captured, risk mitigated %, corridor capacity %) on the lifecycle Command Center. Those are computed from the Corridor A fixture |

### 5.2 KPI cards (P0)

1. **Active maintenance jobs.** Value: count. Sub-line: `R · S · A · P` counts.
2. **Critical (reported severity).** Value: count. Sub-line: "CRITICAL, not completed".
3. **Awaiting authority decision.** Value: count of `scheduled`. Sub-line: soonest `due_at` among `APPROVAL_PENDING` (wall clock) + state chip.
4. **Committed / in progress.** Value: `notified + in_progress`.
5. **Completed.** Value: count.
6. **Open obligations.** Value: count `is_open`. Sub-line: past due N · attention N · "ASSUMED DEMO SLA".
7. **Last optimization.** `solver_status` chip, `counts.scheduled/considered`, `solve_time_seconds`, `possession_derivation`, run id (short), recorded time.

Beneath: **Critical & pending work** table (priority desc; columns as in §7 but
compact), then an **Obligations due soonest** list (5 rows).

**Empty/setup state.** If `GET /v1/jobs` returns zero items, or any job's
`block_candidate.section_id` is not one of the six NDLS–AGC sections (§6.3), show a
blocking banner: *"Demo database not seeded or backend not started with
PASHUPAT_CORRIDOR_ID=CORR-NDLS-AGC. See the runbook."*

---

## 6. Field Inspection screen

### 6.1 Contract (`JobCreateRequest`, `extra="forbid"`: send no other keys)

| Field | Required by API | Type / limits | UI control | UI rule |
|---|---|---|---|---|
| `track_id` | **yes** | string, must be a corridor track | Segmented control `UP-1` / `DOWN-1` | Required |
| `job_type` | **yes** | enum `TRACK_RENEWAL`, `BALLAST_TAMPING`, `OHE_MAINTENANCE`, `SIGNALLING_INTERLOCKING`, `ROUTINE_INSPECTION`, `EMERGENCY_REPAIR` | Select with a readable label and the default duration shown for information (240/120/90/90/45/90 min) | Required |
| `distance_start` | **yes** | number ≥ 0 (corridor-absolute **metres**) | **Not typed by the worker.** Computed from field_location (§6.3) and shown read-only in the preview | Must equal the converted value (≤1 mm) |
| `distance_end` | **yes** | number > `distance_start` | Computed, read-only | As above |
| `workers_min` | **yes** | int ≥ 1 | Number stepper, default **2** | Required |
| `workers_max` | **yes** | int ≥ `workers_min` | Number stepper, default **4** | Required, ≥ min |
| `description` | **yes** | 1–1000 chars | Textarea with counter | Required, trimmed non-empty |
| `severity` | no (API) | enum `NONE`, `MINOR`, `MODERATE`, `CRITICAL` | Radio group | **UI makes it required.** If omitted, the scorer falls back to the nearest synthetic asset's stored condition, which is worse for the story |
| `evidence_reference` | no | 1–200 chars | Text input + helper "Reference only; no upload" | If present, 1–200 |
| `corridor_id` | no | string; must equal the deployment's corridor or `400` | Hidden, always `CORR-NDLS-AGC` | Defence in depth: a backend on the wrong corridor fails closed |
| `field_location` | no (API) | `{from_station_id, toward_station_id, offset_start_m ≥ 0, offset_end_m > 0 and > start}`, `extra="forbid"` | Location group (§6.3) | **UI always sends it** (location becomes `FIELD_LOCATION_VERIFIED`) |
| `idempotency_key` | no | 1–128 of `A-Za-z0-9._:-` | Hidden | §6.4 |
| Inspector / actor | headers | `X-Actor-Id` + `X-Actor-Role: WORKER` | Taken from the persona; shown read-only "Reporting as WORKER-042 (declared, not verified)" | Always sent |
| Timestamp | **server-assigned** | `created_at` (UTC, microseconds) | Not an input. Shown after submit as "Recorded at … (system clock)" | The API has no client-supplied report time. Do not add one |

### 6.2 Example body (the scripted live intake)

```json
{
  "corridor_id": "CORR-NDLS-AGC",
  "track_id": "DOWN-1",
  "job_type": "EMERGENCY_REPAIR",
  "distance_start": 138700,
  "distance_end": 139000,
  "workers_min": 2,
  "workers_max": 4,
  "description": "Rail fracture found on patrol; caution order issued.",
  "severity": "CRITICAL",
  "evidence_reference": "synthetic-demo/inspection/fracture-mtj-rkm.jpg",
  "field_location": {
    "from_station_id": "MTJ",
    "toward_station_id": "RKM",
    "offset_start_m": 5700,
    "offset_end_m": 6000
  },
  "idempotency_key": "intake-3f9c2a1e-…"
}
```

### 6.3 Location: the `LOCATION_INPUT_CONFLICT` requirement

The v1 contract keeps `distance_start` / `distance_end` **required** and the **only
stored location**. `field_location` is a **cross-check**. The backend converts it
through the section registry and refuses the request with
**`400 LOCATION_INPUT_CONFLICT`** when either converted endpoint differs from the
declared distance by more than **1 mm**. It also refuses (`400 FIELD_LOCATION_INVALID`)
when the field location resolves to a different section than the distances do.

**There is no API endpoint for corridor topology.** The frontend therefore carries a
static, SYNTHETIC-labelled copy of the six sections from
`pashupatastra_realistic_dataset.json`, as registered by the backend (verified at
`cc4fae2`):

| section_id | start station | end station | km_start | km_end | length (m) |
|---|---|---|---|---|---|
| `NDLS-NZM` | NDLS New Delhi | NZM Hazrat Nizamuddin | 0.0 | 7.0 | 7 000 |
| `NZM-FDB` | NZM | FDB Faridabad | 7.0 | 32.0 | 25 000 |
| `FDB-PWL` | FDB | PWL Palwal | 32.0 | 66.0 | 34 000 |
| `PWL-MTJ` | PWL | MTJ Mathura Junction | 66.0 | 133.0 | 67 000 |
| `MTJ-RKM` | MTJ | RKM Raja Ki Mandi | 133.0 | 190.0 | 57 000 |
| `RKM-AGC` | RKM | AGC Agra Cantt | 190.0 | 195.0 | 5 000 |

Tracks `UP-1` and `DOWN-1` both span km 0–195 and serve every section.

**Conversion.** It mirrors `backend/app/jobs/field_location.convert_field_location`
exactly. Implement it as one pure function with unit tests.

```
section = the ONE section whose {start, end} == {from, toward}   // adjacent stations only
length  = (km_end - km_start) * 1000
require 0 <= offset_start < offset_end <= length
if from == section.start:
    distance_start = km_start*1000 + offset_start
    distance_end   = km_start*1000 + offset_end
else:                                  // measured from the far (higher-km) end
    distance_start = km_end*1000 - offset_end
    distance_end   = km_end*1000 - offset_start
round both to 6 decimal places
// half-open sections [km_start, km_end): the resolver refuses a span whose end
// lands exactly on km_end (it would belong to the next section)
require km_start*1000 <= distance_start  and  distance_end < km_end*1000
```

**UI validation that prevents `LOCATION_INPUT_CONFLICT`:**

1. The worker never types `distance_*`. The form derives them from the field location with the function above and sends both. They agree by construction, exactly as the seed's `job_request()` does.
2. The *toward* picker offers only the **adjacent** stations of *from* (the section table). No other pair can resolve.
3. Offsets are metres. Show the section length as the max. Block submit when `offset_end ≤ offset_start`, when `offset_end > length`, or when the derived span touches the far boundary (use the inline error *"End must be short of the next station"*).
4. Show a live read-only preview: *"Section MTJ-RKM · 138 700 – 139 000 m corridor chainage (km 138.700–139.000) · track DOWN-1 · SYNTHETIC topology."*
5. If the server still returns `LOCATION_INPUT_CONFLICT` or `FIELD_LOCATION_INVALID`, show `detail` verbatim under the Location group and keep the form values.
6. Other location-related refusals to render inline:
   - `400 INVALID_REQUEST` ("Unknown track_id", resolution failure; this can occur *before* field-location checks);
   - `400 ASSET_ASSOCIATION_FAILED` (no synthetic asset on that track within 20 km of the span; the report is refused, never attached to a distant asset).

### 6.4 Idempotency behaviour

- Generate `idempotency_key = "intake-" + uuid` when the form is **opened**. Keep it for every retry of **that** submission (double-click, network retry). Generate a new one only after a successful submit or an explicit "New report".
- A retry of the same request under the same key and actor returns **201 with the same job**. The backend also sets `Idempotent-Replayed: true`, but **browsers cannot read it**: CORS middleware sets no `expose_headers`. The UI must not depend on the header. Same `job_id` means it was a replay.
- `409 IDEMPOTENCY_KEY_CONFLICT`: same key, different body. Tell the user *"This report was already submitted with different values"*, then regenerate the key on "Submit as new report".
- The key requires actor headers (`400` without). The UI always sends them.
- The submit button is disabled while in flight. Show a spinner. On success, navigate to Job Detail.

### 6.5 After submit (confirmation panel)

Show `job_id`, `status: reported`, `priority_score`, `risk_score`, the section
(from `block_candidate.section_id`, non-contract), "Location cross-check passed"
(`block_candidate.metadata.location_source == "FIELD_LOCATION_VERIFIED"`), the
evidence reference, and `created_at` as *"Recorded 25 Sep 2026, 14:03 IST (system
clock)"*. Also show the advisory duplicate block if
`block_candidate.metadata.duplicate_detection.possible_duplicate` is true (P1). It is
advisory only: the job **is** created.

---

## 7. Maintenance Job Queue

**Source:** `GET /v1/jobs?limit=200[&status=…]`, looping on `next_cursor` until it
is `null`. A short or empty page with a cursor is legal. Newest-first is the server
order; sorting is client-side.

### 7.1 Columns

| Column | Field | Contract? | Rendering |
|---|---|---|---|
| Job | `job_id` | ✅ | Mono, link to detail |
| Work type | `work_type` | ✅ | Readable label (e.g. "Emergency repair") + mono enum on hover |
| Severity | `block_candidate.metadata.reported_severity` | ⚠ non-contract | Chip. CRITICAL uses the error token. Hide the column gracefully if absent |
| Priority | `priority_score` | ✅ | `0.794` mono, right-aligned + band (≥0.75 HIGH, ≥0.50 MEDIUM, else LOW; scorer thresholds) |
| Risk | `risk_score` | ✅ | Same format |
| Status | `status` | ✅ | Status chip (§18.5), raw value in mono tooltip |
| Section | `block_candidate.section_id` | ⚠ non-contract | Mono, e.g. `MTJ-RKM` |
| Track | `track_id` | ✅ | `DOWN-1` |
| Location | `block_candidate.metadata.field_location` (non-contract) or `distance_start/end` | mixed | "MTJ +5.70–6.00 km → RKM", fallback "km 138.700–139.000" |
| Proposed / committed window | `schedule_start_minute`, `schedule_end_minute` | ✅ | Plan time IST, e.g. "11 Sep 00:16–01:46". Empty for `reported` |
| Run | `proposal_run_id` | ✅ | Short mono `…5C9A6D`, tooltip full |
| Last solver status | `last_solver_status` | ✅ | Small chip (`OPTIMAL` etc.). `null` = "Never optimized" |
| Last decision reason | `last_refusal_reason` | ✅ | **Label it "Last decision reason", not "Optimizer refusal".** It carries the authority's postpone/reject reason as well as solver refusals |
| Obligation | `GET /v1/obligations` joined on `job_id` | ✅ derived | "AUTHORITY · WITHIN_SLA · due 16:48 IST" |
| Reported | `created_at` | ✅ | Wall clock IST, "(system clock)" in the header tooltip |

### 7.2 Sorting and filtering (only on available fields)

- Default sort: `priority_score` desc, then `risk_score` desc.
- Other sorts: `risk_score`, `created_at`, `schedule_start_minute` (nulls last), `status` in lifecycle order.
- **Server-side filter:** `status` (the only filter the API supports).
- **Client-side filters:** track, section, work type, severity, "has open obligation", "past due".
- Search: `job_id` prefix.
- **Persona presets:** Authority lands on `status=scheduled` ("Authority Review"); Worker on `notified` + `in_progress` ("Execution").

### 7.3 Row actions (by status; buttons shown only to the matching persona)

| Status | Action | Persona |
|---|---|---|
| `reported` | "Awaiting optimization" (no action), View | — |
| `scheduled` | **Review proposal** → Proposal Review | AUTHORITY |
| `notified` | **Start execution**; Release block (P1) | WORKER; AUTHORITY |
| `in_progress` | **Complete**; Report not completed (P1) | WORKER |
| `completed` | View audit | any |

---

## 8. Explainable Risk Scoring

**Name in the UI:** **"Explainable Risk Scoring"**, with a subtitle *"Deterministic
baseline scorer: fixed, hand-authored weights. Not a trained ML model."* The backend's
own identity is `scoring_model_type: BASELINE_DETERMINISTIC`,
`scoring_model_version: baseline-deterministic-1.0`. Show both verbatim. **Never** use
"AI model", "ML prediction", "trained", "neural" or "learned" anywhere in the UI.

**Source:** the `JOB_SCORED` event in `GET /v1/jobs/{id}/history`. The actor is
`SYSTEM:SCORER` (role `SYSTEM`, assurance `SYSTEM_INTERNAL`). `event.metadata` holds:

| Metadata key | Example (live intake, observed) | Render |
|---|---|---|
| `priority_score` | `0.7944` | Large mono number + band HIGH |
| `risk_score` | `0.7729` | Large mono number + band HIGH |
| `scoring_explanation` | `["Severe defect", "Maintenance overdue", "High asset criticality"]` | Ordered list of "top contributors" (up to 4, strongest first, always at least one). Caption: *"Ranked on the scorer's internally normalized features, which the API does not return. The raw inputs below cannot be compared directly against the ranking threshold."* |
| `scoring_features` | `{asset_criticality: 0.81, defect_severity: 1.0, days_overdue: 43, failure_probability: 0.581, train_impact: 0.592, maintenance_duration: 90, historical_failure_rate: 0.667}` | Table of **raw scorer inputs with units**. `days_overdue` is **days**, `maintenance_duration` is **minutes**, the rest are 0–1 values. **Do not render these as normalized bars or percentages. The API does not expose normalized values or weights, and the UI must not compute contributions.** |
| `scoring_model_type` / `scoring_model_version` | `BASELINE_DETERMINISTIC` / `baseline-deterministic-1.0` | Mono tags |
| `reported_severity` | `CRITICAL` | "Worker-reported severity (used as the defect severity input)" |
| `scorer` | `backend.app.ml.scorer.score_block` | Small mono, P1 |

Honest footnote on the panel: *"Asset inputs (criticality, failure probability,
historical failure rate, overdue days) come from the synthetic generated asset nearest
this location. Worker-reported severity overrides the asset's stored defect severity
for this job."* The asset link is in
`block_candidate.metadata.asset_association`. For the live intake it resolved to a
synthetic OHE mast 11.85 km away in section `PWL-MTJ` (`section_match: false`). The
Overview tab should state that plainly (P1), not hide it.

`event.metadata` is **not part of the frozen contract**. Parse every key defensively.
If `JOB_SCORED` or a key is missing, show "Explanation not recorded for this job" and
still show the two contract scores.

---

## 9. Optimization / block planning

### 9.1 Optimize action

- **Who:** ENGINEER persona (`X-Actor-Role: ENGINEER`, `X-Actor-Id: ENGINEER-007`).
- **Call:** `POST /v1/corridors/CORR-NDLS-AGC/optimize-jobs`, no body.
- **Selected corridor:** fixed `CORR-NDLS-AGC` (the deployment serves exactly one corridor). A mismatch returns `404 CORRIDOR_NOT_FOUND` with the served corridor named in `detail`. Show it as a setup error.
- **Planning horizon:** fixed, `2026-09-10T00:00:00+05:30` + 2880 min. Not a request parameter. Display "10 Sep 00:00 – 12 Sep 00:00 IST (2 days, synthetic timetable coverage)". The horizon start is also recorded in every `BLOCK_PROPOSED` event's `metadata.horizon_start` and in the run's `request_json`. The UI may assert they match its constant and warn if not (P1).
- **Confirm dialog:** "Run optimization over N active jobs on CORR-NDLS-AGC? Committed blocks stay pinned. Every open (unapproved) proposal will be re-proposed and gets a new run id. Authorities must review again."
- **In flight:** full-width progress bar on the Planning screen and disabled button. The solver budget is 10 s; the demo run takes about 0.05–0.2 s.

### 9.2 Run result panel (`JobOptimizationResponse`, all contract fields)

| Field | Observed run 1 | Render |
|---|---|---|
| `solver_status` | `OPTIMAL` | Status chip. `OPTIMAL` = "Optimal (solver proved)". `FEASIBLE` = "Feasible (time limit; not proven optimal)". `INFEASIBLE`/`NO_SOLUTION` = error styling |
| `optimization_run_id` | `RUN-…` | Mono, copyable; link to run audit (P1) |
| `solve_time_seconds` | `0.160` | "160 ms" |
| `counts` | `{considered 5, committed 0, scheduled 5, unscheduled 0}` | Four stat cells |
| `scheduled[]` | `{job_id, track_id, start_minute, end_minute, is_committed}` | Table: job, track, plan-time window, "Committed (pinned)" tag when `is_committed` |
| `unscheduled[]` | `{job_id, track_id, reason}` | Table with the **verbatim** solver reason. Never soften it |
| `infeasibility_reasons[]` | `[]` | Verbatim list, when non-empty |
| `uncovered_tracks[]` | `[]` | Warning list: "No possession window covers work on these tracks; the solver refused those blocks" |
| `possession_derivation` | `CANONICAL_TIMETABLE_DERIVED` | "Possession windows derived from train-free gaps in the (synthetic) timetable". Alternative value `GENERATED_STATIC_SLOTS` = "Fixed generated slots; no train data" |
| `possession_window_count` | `287` | "287 possession windows over the horizon" |
| `possession_source` | `GENERATED_STATIC` | **Legacy field; do not headline it.** It reads `GENERATED_STATIC` even when the derivation is timetable-derived. Show it only in a details drawer with the note "legacy label, derived from provenance.possession" |
| `provenance` | all five `SYNTHETIC` | Provenance strip (§17): topology · timetable · asset condition · possession → **effective SYNTHETIC** |
| `generated_at` | wall clock | "Recorded … (system clock)" |

### 9.3 Proposed blocks, placement, duration, committed blocks

- Build a per-job list from `scheduled[]` joined with `GET /v1/jobs` (work type, scores, section).
- **Placement:** plan time IST `start–end`, plus raw minutes in mono (`1456–1546`).
- **Duration:** `end - start` min. It always equals the work type's modelled duration.
- **Committed blocks:** `is_committed: true` rows get a "PINNED · APPROVED" tag. `counts.committed` is the number of committed blocks fed to the solver.
- **Horizon strip (P1):** a simple two-lane-per-section timeline (UP-1 / DOWN-1 × six sections) across the 48-hour horizon, with day boundary at 11 Sep 00:00. Proposed blocks are outlined; committed blocks are filled. This is **not a map**; it is a time × resource chart built from real placements.

### 9.4 Conflicting trains: what can honestly be shown

- Per proposal, the backend provides **either** `CONFLICTING_TRAINS_AVOIDED` (the corridor has train-level data) **or** `NO_TRAIN_LEVEL_DATA` (generated slots). Show the `detail` **verbatim**.
- On NDLS–AGC the detail reads *"No train movement was rejected as a conflict for this section in this run."* The list names only trains the timetable adapter **rejected**, which then withheld possession. It is **not** a list of trains that bound the window.
- **Do not** display "N trains avoided" or invent train names. The trains around a window are only present inside the run's `request_json.train_timetable`, an opaque, non-contract string. Rendering them is **P2**.

### 9.5 Failure states

| Response | Meaning | UI |
|---|---|---|
| `409 NO_ELIGIBLE_JOBS` | No active jobs | Neutral empty state: "Nothing to optimize". Link to Field Inspection |
| `409 POSSESSION_DATA_UNAVAILABLE` | No possession windows; nothing solved | Error panel with `detail`: "Refused to schedule without possession data (fail-closed)" |
| `409 TIMETABLE_COVERAGE_GAP` | Horizon touches a date the timetable doesn't cover | Error panel with `detail` |
| `409 COMMITTED_STATE_INCONSISTENT` / `EXECUTION_HISTORY_INCONSISTENT` | Stored state inconsistent; refusal recorded | Error: "Integrity refusal. Escalate to an engineer; do not retry" |
| `409 CONCURRENT_MODIFICATION` | Another write changed a job mid-run; batch rolled back | "Data changed during the run. Reload and run again" |
| `404 CORRIDOR_NOT_FOUND` | Backend serves another corridor | Setup error banner |
| `200` with `unscheduled.length > 0` | Partial success | Amber banner "k of n jobs could not be placed". Unscheduled table first. **Never** show it as a full success |
| `200` with `solver_status` `INFEASIBLE`/`NO_SOLUTION` | No placement | Error styling + `infeasibility_reasons` |

After **any** optimize response, re-fetch `GET /v1/jobs` and invalidate every cached
proposal. Run ids have changed.

---

## 10. Proposal review

**Source:** `GET /v1/jobs/{id}/proposal`. Call it only when `status == "scheduled"`.
For any other status the API returns **`409 NO_CURRENT_PROPOSAL`**. That is a
**state, not an error**: render its `detail` in a neutral panel. It says *why*, for
example *"the proposal was REJECTED by AUTHORITY 'AUTHORITY-017' … Covered by the
scheduled trolley inspection this week"* or *"already committed via notify; it is no
longer a pending proposal"*.

### 10.1 Proposal card layout

```
┌ PROPOSAL  PROP-RUN-…-JOB-BB61F951CE                     [PROPOSED · NOT COMMITTED] ┐
│ EMERGENCY_REPAIR · Job JOB-BB61F951CE · DOWN-1 · Section MTJ-RKM                   │
│                                                                                     │
│ PLAN TIME (synthetic horizon, IST)                                                  │
│   11 Sep 2026 00:16 → 01:46   (90 min)        raw minutes 1456–1546                │
│                                                                                     │
│ SCORES  priority 0.794 HIGH · risk 0.773 HIGH · objective 1.567                    │
│                                                                                     │
│ WHY THIS PLACEMENT (backend explanation, verbatim)                                  │
│  PLACEMENT                    Placed at minute 1456-1546.                           │
│  POSSESSION_WINDOW_REQUIRED   Requires a possession window from minute 1345 to 1546│
│                               → 10 Sep 22:25 – 11 Sep 01:46 IST                     │
│  RESOURCE_USED                Uses track 'DOWN-1', section 'MTJ-RKM'.               │
│  COMMITTED_BLOCKS_RESPECTED   No other committed work shares this resource.         │
│  CONFLICTING_TRAINS_AVOIDED   No train movement was rejected as a conflict for …    │
│                                                                                     │
│ BASIS  Run RUN-…5C9A6D · OPTIMAL · windows CANONICAL_TIMETABLE_DERIVED (287)        │
│ PROVENANCE  topology SYN · timetable SYN · assets SYN · possession SYN → SYNTHETIC  │
│ Generated 25 Sep 2026 05:48 IST (system clock)                                      │
│ OBLIGATION  APPROVAL_PENDING · owed by AUTHORITY · WITHIN_SLA · due 07:48 IST       │
│             (ASSUMED demo SLA sla-assumed-demo-1.0)                                 │
│                                                                                     │
│ [ Approve & commit ]   [ Postpone… ]   [ Reject… ]          (AUTHORITY persona)     │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

### 10.2 Fields (all from `BlockProposalResponse`, contract)

`proposal_id`, `job_id`, `optimization_run_id`, `corridor_id`, `track_id`,
`section_id`, `start_minute`, `end_minute`, `duration_minutes`, `work_type`,
`priority_score`, `risk_score`, `objective_score`, `is_committed` (always `false`
here; show "PROPOSED · NOT COMMITTED"), `explanation[] {code, detail}`,
`provenance {topology, timetable, asset_condition, possession, effective}`,
`generated_at`.

### 10.3 Explanation codes: render exactly these, no others

| `code` | When present | Render hint |
|---|---|---|
| `PLACEMENT` | Always when placed | Detail verbatim + converted plan time. **Do not** call it "earliest" or "optimal slot". The objective rewards scheduling, not position |
| `POSSESSION_WINDOW_REQUIRED` | When a covering window was identified | Detail verbatim + converted window times |
| `RESOURCE_USED` | Always | Verbatim |
| `COMMITTED_BLOCKS_RESPECTED` | Always | Verbatim (lists committed mates on the same resource, or says none) |
| `CONFLICTING_TRAINS_AVOIDED` | Corridor has train-level data (NDLS–AGC) | Verbatim (§9.4) |
| `NO_TRAIN_LEVEL_DATA` | Generated-slot corridors (not NDLS–AGC) | Verbatim. Style as an information notice |

Switch on `code`; `detail` wording may change. For any unknown code, render
`code: detail` generically. **Do not invent explanation fields.**
`COMMITTED_BLOCK_PRESERVED` is an **event type** (§12, §14), not a proposal
explanation code.

---

## 11. Authority actions

### 11.1 Common behaviour (Approve, Postpone, Reject)

1. The persona must be AUTHORITY. With another persona, show the buttons disabled with the tooltip *"Switch to the Authority persona (demo role gating; not server-enforced)."*
2. On click, **re-fetch** `GET /v1/jobs/{id}/proposal` and open the dialog with that `optimization_run_id`. Every open proposal gets a new run id after each optimize; a stale id returns `409 STALE_PROPOSAL`.
3. The dialog shows **before → after** state, the placement in plan time, the run id being acted on, and the acting identity: *"Acting as AUTHORITY-017 (AUTHORITY), declared, not verified."*
4. The confirm button names the action ("Approve and commit block", "Postpone proposal", "Reject proposal"). No generic "OK".
5. On `200`, close the dialog, show a toast with `message` ("Proposal approved" / "Proposal postponed" / "Proposal rejected"), re-fetch job, history and obligation, and update the queue.
6. Errors:
   - `409 STALE_PROPOSAL`: *"This proposal was superseded by a newer optimization run. The current proposal has been reloaded; review it again."* Re-fetch. The refusal is recorded in history as `TRANSITION_REJECTED`, which is honest; no need to hide it.
   - `409 NO_CURRENT_PROPOSAL`: render `detail`.
   - `409 CONCURRENT_MODIFICATION`: reload prompt.
   - `400 INVALID_TRANSITION`: show `detail` (for example, the actor is missing).

> **Demo limitation banner (in every decision dialog, small but visible):**
> *"Demo mode: role restrictions are not enforced by the server. The authority identity is declared via request headers and recorded as DECLARED_UNVERIFIED. This is not production authorization."*

### 11.2 Approve

- `POST /v1/jobs/{id}/proposal/approve` with `{"expected_proposal_run_id": "<run id>"}`. There is no reason field; the contract has none.
- Result: `scheduled` → **`notified`**. Display it as **"Approved · committed"**; the raw status value is `notified`, but **no notification is sent anywhere**. The block is pinned for every later optimization. Event: `BLOCK_COMMITTED`. Obligation: `EXECUTION_START_PENDING` owed by WORKER.
- `POST /v1/jobs/{id}/notify` is the equivalent legacy route. **The UI uses `/proposal/approve` only.**

### 11.3 Postpone

- `POST /v1/jobs/{id}/proposal/postpone` with `{expected_proposal_run_id, reason, selected_date}`.
- **reason:** required, 1–2000 chars, textarea with counter.
- **selected_date:** `YYYY-MM-DD`, meaning "not before 00:00 IST on this date". It is not a time of day.
  - The horizon covers minutes `[0, 2880)`, i.e. **2026-09-10 and 2026-09-11** only.
  - The control is a two-option segmented picker: `10 Sep 2026 (horizon day 1)` / `11 Sep 2026 (horizon day 2)`. Default is **11 Sep**.
  - Label: **"Not before (planning horizon date)"**. Helper: *"The demo planning horizon is 10–11 Sep 2026 (synthetic timetable). This is the plan date, not today's date."*
  - Choosing 10 Sep sets not-before to the horizon start. It is allowed but has no scheduling effect; show a hint.
  - **Never** offer dates outside the horizon. The server refuses them (`400 INVALID_TRANSITION`, "resolves to minute 2880 … only covers [0, 2880)") **and records a `TRANSITION_REJECTED` event**, so client-side prevention keeps the audit clean.
- Result: `scheduled` → `reported`, placement cleared, `block_candidate.earliest_start_minute = 1440` for 11 Sep, and `last_refusal_reason` = the reason. Event `PROPOSAL_POSTPONED` with metadata `selected_date`, `not_before_minute`, `original_start_minute`, `original_end_minute`, `original_proposal_run_id`. The job awaits the next optimization run (obligation `NONE` / `NO_OBLIGATION_AWAITING_OPTIMIZATION`).
- Post-action toast: *"Postponed. The job returns to the queue and will not be placed before 11 Sep 00:00 IST at the next optimization run."*

### 11.4 Reject

- `POST /v1/jobs/{id}/proposal/reject` with `{expected_proposal_run_id, reason}`. The reason is required, 1–2000 chars.
- Result: `scheduled` → `reported`, placement cleared, `last_refusal_reason` = the reason. Event `PROPOSAL_REJECTED` (metadata `rejected_start_minute`, `rejected_end_minute`).
- **Honest copy (mandatory, in the dialog):** *"Rejecting records your decision and reason and returns the job to the queue. The job stays eligible for the next optimization run. The optimizer does **not** search for an alternative window. It may propose the same placement again. To keep work out of a date, use Postpone."*
- Observed in rehearsal: the rejected inspection was re-proposed at the **identical** placement (2527–2572) by run 2. The re-optimization view must label this "Re-proposed · same placement as rejected".

### 11.5 Release (P1, not in the main story)

`POST /v1/jobs/{id}/proposal/release` with `{expected_proposal_run_id, reason}` on a
**`notified`** job whose execution has not started: `notified` → `reported`, event
`BLOCK_RELEASED`. Obligation becomes `REPLANNING_PENDING` (ENGINEER). The dialog
explains that release is for an approved block that cannot start: possession not
granted, crew or safety restriction.

---

## 12. Re-optimization

Screen: **Planning → Re-optimization comparison**. It appears after any optimize
when a previous run exists.

### 12.1 Sources (P0: session diff + history labels)

- **Primary:** the previous and current optimize responses held in the session, joined on `job_id` (`scheduled[]` start/end/`is_committed`, `unscheduled[]`).
- **Per-job classification, derived from each job's history** (works after a page reload):

| Classification | How to detect (history of the job, latest run) | Shown as |
|---|---|---|
| **Committed block preserved** | `COMMITTED_BLOCK_PRESERVED` event with `optimization_run_id == new run`; start/end unchanged | "PINNED · preserved" with a lock icon; before = after |
| **Moved (re-proposed)** | `BLOCK_REPROPOSED` with `metadata.placement_changed: true`; before = `metadata.previous_start_minute`/`previous_end_minute` (`previous_proposal_run_id`) | "MOVED" + before → after in plan time |
| **Unchanged (re-proposed)** | `BLOCK_REPROPOSED` with `placement_changed: false` | "Re-proposed · unchanged" |
| **Re-proposed after postpone** | Previous decision `PROPOSAL_POSTPONED` (`original_start_minute`/`original_end_minute`, `not_before_minute`, `selected_date`), then `BLOCK_PROPOSED` in the new run | "POSTPONED → now 11 Sep 15:15–17:15". Show the not-before date satisfied (✓ start ≥ not_before_minute) |
| **Re-proposed after reject** | `PROPOSAL_REJECTED` (`rejected_start_minute`/`rejected_end_minute`), then `BLOCK_PROPOSED` | "REJECTED → re-proposed" + "same placement" flag when equal |
| **Newly proposed** | `BLOCK_PROPOSED` with no earlier placement | "NEW" |
| **Not placed** | `OPTIMIZATION_REFUSED` / present in `unscheduled[]` | "UNSCHEDULED" + verbatim reason |
| **Withdrawn** | `PROPOSAL_INVALIDATED` | "Proposal withdrawn by system" |
| **Conflict** | `COMMITTED_BLOCK_CONFLICT` | Error row: "Committed work could not be honoured; commitment kept; engineer attention" (also `attention_required` on the obligation) |

### 12.2 Expected demo outcome (run 2)

| Job | Before (run 1) | After (run 2) | Label |
|---|---|---|---|
| Fracture (approved) | 11 Sep 00:16–01:46 | identical | **Committed block preserved** ✔ guaranteed |
| Ballast (postponed to 11 Sep) | 10 Sep 00:00–02:00 | 11 Sep 15:15–17:15 *(illustrative; day 2 guaranteed)* | Re-proposed after postpone ✔ day 2 |
| Inspection (rejected) | 11 Sep 18:07–18:52 | 11 Sep 18:07–18:52 *(illustrative)* | Re-proposed after reject · same placement |
| OHE | 11 Sep 17:15–18:45 | 10 Sep 08:34–10:04 *(illustrative)* | Moved |
| Signal | 11 Sep 13:41–15:11 | 11 Sep 13:39–15:09 *(illustrative)* | Moved |

Header summary: *"Run 2: OPTIMAL · 5 considered · 1 committed (pinned) · 5 scheduled ·
0 unscheduled."* Footnote: *"Unapproved proposals are re-proposed on every run and
may move. Only approved (committed) blocks are pinned. Each re-proposal restarts the
authority's approval clock."*

---

## 13. Execution

Screen: **Execution**. Lists jobs with `status ∈ {notified, in_progress}`. Detail is
also in the Job Detail → Execution tab. Actions are shown to the WORKER persona only.

### 13.1 Required states

| Action | Allowed from | To | Endpoint |
|---|---|---|---|
| Start execution | `notified` | `in_progress` | `POST /v1/jobs/{id}/execution/start` |
| Complete | `in_progress` (open `execution_id`) | `completed` (terminal) | `POST /v1/jobs/{id}/execution/complete` |
| Report not completed (P1) | `in_progress` | `reported` (commitment released for replanning) | `POST /v1/jobs/{id}/execution/not-completed` |

### 13.2 Start execution dialog

- Header: committed block. Shows job, section/track, **planned** plan-time window, `proposal_run_id`. The body uses `expected_proposal_run_id = job.proposal_run_id`.
- **Actual start** (`actual_start_at`): ISO-8601 **with explicit offset** (`+05:30`), not more than 5 minutes in the future (server clock).
  - **Default** = planned start + 1 minute, rendered in plan time: for the demo, `2026-09-11T00:17:00+05:30`. This is exactly what `rehearse()` does.
  - Label: *"Actual start (entered by crew; synthetic horizon time)."* Helper: *"The demo plan runs on a fixed synthetic horizon (10–11 Sep 2026). The server records its own recording time separately."*
  - The server never clamps observed times to the horizon. A wall-clock "now" would be accepted but would record a large deviation. Keep plan-time defaults for the demo.
- **Before-work evidence**: 1–10 items (§13.4). `captured_at` must be **≤ actual start**. Default `captured_at` = actual start.
- On success, show `execution_id` (`EXE-…`), `status: STARTED`, `started_by`, `started_recorded_at` (system clock), `actual_start_minute`, and the deviation flag `started_before_planned_start`.

### 13.3 Complete execution dialog

- `execution_id` comes from the start response or `GET /v1/jobs/{id}/execution` (the open record).
- **Actual end** (`actual_end_at`): explicit offset, ≥ actual start, not in the future. Default = planned end (`2026-09-11T01:46:00+05:30`).
- **After-work evidence**: 1–10 items. `captured_at` must be **≥ actual start**, and **no reference may repeat a before-work reference** of the same execution. Validate client-side against the before-work list.
- On success: `status: completed` (terminal), `execution.status: COMPLETED`, deviations `ended_after_planned_end` (false in the demo).

### 13.4 Evidence item editor (reference model, no upload)

| Field | Rule |
|---|---|
| `evidence_reference` | Required, 1–200 chars, unique within the set. Placeholder `synthetic-demo/execution/before-1.jpg` |
| `evidence_kind` | `PHOTO` \| `VIDEO` \| `DOCUMENT` \| `MEASUREMENT` |
| `captured_at` | ISO-8601 with explicit offset; ordering rules above |
| `latitude`/`longitude` | Optional; **both or neither**; ranges ±90/±180. Label "Range-checked only; no geofencing" |
| `note` | Optional, ≤ 500 |

Copy on the editor: *"Evidence is recorded as a reference (ID or path in another
system) with kind and capture time. This demo does not upload, store or open files."*
**No file picker, no thumbnails, no "upload" wording.**

### 13.5 Execution record view (`ExecutionResponse`)

Show `attempt_number`, `execution_id`, `optimization_run_id`, `proposal_id`,
planned vs actual (plan time + raw minutes), `committed_block_digest` (mono,
truncated, "integrity digest of the committed block"), `started_by` / `ended_by`
(actor id, role, assurance), `started_recorded_at` / `ended_recorded_at` (system
clock), evidence lists by phase with server `evidence_id`, `not_completed_reason`,
and `deviations`.

### 13.6 Timestamp handling (global rule)

| Kind | Examples | Display |
|---|---|---|
| **Plan time** (horizon-relative) | `start_minute`, `schedule_*_minute`, window minutes, `planned_*_minute`, `actual_*_minute` | `2026-09-10T00:00+05:30` + minutes → "11 Sep 00:16 IST", with tag **PLAN** |
| **Observed time** (entered by crew) | `actual_start_at`, `actual_end_at`, `captured_at` | As entered, IST, tag **OBSERVED** |
| **Recorded / system clock** | `created_at`, `updated_at`, event `occurred_at`, `generated_at`, `*_recorded_at`, obligation `clock_started_at`/`due_at`/`evaluated_at` | UTC from API → IST, tag **RECORDED** |

Judges will see both 10–11 Sep 2026 (plan) and 25–26 Sep 2026 (recorded). Every
timestamp needs its tag, and the About panel explains the two clocks.

---

## 14. Audit timeline

**Source:** `GET /v1/jobs/{id}/history` → `{job_id, events[]}`. The events are in
chronological order and append-only; the database refuses UPDATE and DELETE. **No
credentials are ever present.**

### 14.1 Per-event fields (contract `JobEventResponse`)

`sequence`, `event_id`, `schema_version`, `entity_type`, `job_id`, `event_type`,
`occurred_at`, `actor {actor_id, role, kind, assurance}`, `reason`,
`optimization_run_id`, `before_state`, `after_state` (each `{status, block_status,
is_committed, schedule_start_minute, schedule_end_minute, proposal_run_id}`),
`metadata` (non-contract internals).

### 14.2 Visual design (judge-readable)

- **Vertical timeline**, oldest at top. Events are **grouped by `optimization_run_id`** into a bracket labelled "Optimization run …5C9A6D (OPTIMAL)"; ungrouped events stand alone.
- Each event row has:
  1. an icon and event-type label (human text + mono code);
  2. an **actor pill** with id, role and assurance chip. Assurance text: `DECLARED_UNVERIFIED` → "declared, not verified"; `SYSTEM_INTERNAL` → "system"; `NONE` → "unidentified";
  3. a **state transition** `reported → scheduled`, drawn as two status chips with an arrow; unchanged state shown dimmed;
  4. the **placement change** (plan time) when the schedule minutes differ;
  5. the **reason** in a quote block (authority reasons, refusal messages);
  6. the recorded time (RECORDED tag);
  7. an expandable "Details" drawer with a curated metadata view per type (below), plus raw JSON in P1.
- **Colour by actor class, not by success.** Human declared actors use the neutral/primary token. `SYSTEM:*` uses the tertiary (violet) token. `TRANSITION_REJECTED` / `OPTIMIZATION_FAILED` / `COMMITTED_BLOCK_CONFLICT` use the error token.
- A header strip summarises: event count, first/last recorded time, current status, "Append-only · no edits possible".

### 14.3 Event types and what to surface

| Event | Actor | Surface from metadata |
|---|---|---|
| `JOB_CREATED` | Worker (declared) | Track, section, work type, span, crew size, description, location source, field location, asset association, duplicate detection, idempotency key |
| `JOB_SCORED` | `SYSTEM:SCORER` | **Score explanation**: priority, risk, `scoring_explanation`, model type/version, reported severity (§8) |
| `OPTIMIZATION_REQUESTED` | Engineer (declared) | corridor, trigger |
| `OPTIMIZATION_COMPLETED` | `SYSTEM:OPTIMIZER` | solver status, considered/scheduled/unscheduled |
| `OPTIMIZATION_FAILED` / `OPTIMIZATION_REFUSED` | System | reason / solver reason |
| `BLOCK_PROPOSED` / `BLOCK_REPROPOSED` | `SYSTEM:OPTIMIZER` | start/end, possession window, committed mates, trains avoided, objective score; for re-proposed also `previous_*`, `placement_changed` |
| `COMMITTED_BLOCK_PRESERVED` | `SYSTEM:OPTIMIZER` | "Approved block kept pinned in run …", start/end |
| `COMMITTED_BLOCK_CONFLICT` | System | conflict detail (error) |
| `PROPOSAL_INVALIDATED` | System | why withdrawn |
| `BLOCK_COMMITTED` | Authority | `expected_proposal_run_id`, start/end |
| `PROPOSAL_POSTPONED` | Authority | reason, `selected_date`, `not_before_minute`, original placement |
| `PROPOSAL_REJECTED` | Authority | reason, rejected placement |
| `BLOCK_RELEASED` | Authority | reason |
| `EXECUTION_STARTED` / `EXECUTION_COMPLETED` / `EXECUTION_NOT_COMPLETED` | Worker | execution id, actual times, evidence refs by phase, deviations, block digest |
| `TRANSITION_REJECTED` | Whoever attempted | `attempted_transition`, `error_type`, reason = refusal message. Show as "Refused attempt (state unchanged)" |
| `SCHEDULE_ASSIGNED`, `JOB_COMPLETED` | — | Legacy/unused in this flow; render generically |

Expected fracture-job timeline after the story: 11 events (§2.2 step 19). Other jobs
show `PROPOSAL_POSTPONED` / `PROPOSAL_REJECTED` / `BLOCK_REPROPOSED` and so on.

---

## 15. SLA / obligations

**Sources:**

- `GET /v1/obligations?state=&obligation_type=&role=&past_due=&attention=&limit=&cursor=`. Paging is over **candidate jobs**, so a page may be short or empty while `next_cursor` is set. Loop until `null`.
- `GET /v1/jobs/{id}/obligation`. This always returns `200`; the type is `NONE` when nothing is owed.

### 15.1 Panel columns (`ObligationResponse`)

| Column | Field | Render |
|---|---|---|
| Job | `job_id` | link |
| Obligation | `obligation_type` | `APPROVAL_PENDING` "Approval pending" · `EXECUTION_START_PENDING` "Execution start pending" · `COMPLETION_PENDING` "Completion pending" · `REPLANNING_PENDING` "Replanning pending" · `NONE` "Nothing owed" |
| Owner | `owed_role` | Role chip (**never a person**; the API has no person directory) |
| State | `state` | `WITHIN_SLA` · `DUE_SOON` · `OVERDUE` · `ESCALATED_L1/L2/L3` · `MOOT` · `NO_SLA_DEFINED` · `NONE` |
| Late? | `is_past_due` | "Past due" flag. **This is the lateness predicate**, not `state == OVERDUE` |
| Clock started | `clock_started_at`, `anchor_event_id` | RECORDED time; anchor event links to the audit row |
| Due | `due_at`, `sla_seconds` | RECORDED time + "2 h SLA" |
| Elapsed | `elapsed_seconds` | "12 min of 2 h" + thin progress bar (neutral; amber from 75%) |
| Escalation | `escalation_level` | L0–L3 |
| Reason | `reason_code` + `reason` | Code mono; prose on hover |
| Run | `optimization_run_id` | short mono |
| Attention | `attention_required`, `attention_reason_code`, `attention_role` | Error-token flag "Engineer attention: COMMITTED_BLOCK_CONFLICT" |
| Policy | `policy_version`, `policy_assumed` | Footer + per-row tooltip |

Page header: `evaluated_at` ("Evaluated at 14:05 IST, recomputed on every read").
**Mandatory banner:** *"SLA durations are ASSUMED DEMO ENGINEERING VALUES (policy
`sla-assumed-demo-1.0`), not Indian Railways policy and not reviewed by any railway
authority. Obligations are addressed to roles, never to individuals. No notification
is sent."*

Filters: role (AUTHORITY / WORKER / ENGINEER), type, "past due only" (`past_due=true`),
"needs attention" (`attention=true`), "open only" (client-side `is_open`).

### 15.2 Honest behaviour rules

- `WITHIN_SLA` is displayed as-is. **No fake escalation, no countdown animation, no alarm sounds, no "notified" claims.** The state is only "current as of `evaluated_at`". Refresh on user action and on every mutation. Background polling is optional (P1, ≥30 s).
- `MOOT` reads "Withdrawn by the system; not a breach".
- There are **no acknowledge, silence or escalate buttons** (no such routes, by design).
- Show the SLA table (P1, in an info popover):

| Reported severity | Approval | Execution start | Completion |
|---|---|---|---|
| CRITICAL | 2 h | 1 h | 4 h |
| MODERATE | 6 h | 4 h | 12 h |
| MINOR / NONE | 24 h | 12 h | 24 h |

`DUE_SOON` from 75%. L1/L2/L3 at 1×/2×/4×.

### 15.3 Expected demo states

| Moment | Fracture job | Others |
|---|---|---|
| After intake, before optimize | `NONE` / `NO_OBLIGATION_AWAITING_OPTIMIZATION` | same |
| After run 1 | `APPROVAL_PENDING` · AUTHORITY · `WITHIN_SLA` · SLA 2 h | MODERATE 6 h, MINOR 24 h |
| After approve | `EXECUTION_START_PENDING` · WORKER · 1 h | postponed/rejected → `NONE` (awaiting optimization) |
| After run 2 | (unchanged) | all four `APPROVAL_PENDING` again, with clocks restarted |
| After start | `COMPLETION_PENDING` · WORKER · 4 h | — |
| After complete | `NONE` / `NO_OBLIGATION_TERMINAL` | four `APPROVAL_PENDING`, `WITHIN_SLA` |

---

## 16. Operational simulation

### 16.1 What the backend supports today

| Capability | Supported? | Where |
|---|---|---|
| Stateless what-if solve of a posted request | ✅ | Legacy `POST /optimize`. It creates **no** `optimization_runs` row and **no** job events (D-3 boundary, test-guarded), so it never writes into the demo DB |
| Disruption applied to a posted request, then re-solved | ✅ | Legacy `POST /recover` with `disruption_type` ∈ `TRACK_UNAVAILABLE`, `EMERGENCY_WORK`, `POSSESSION_CURTAILMENT`, `ASSET_BREAKDOWN` (`backend/app/simulation/disruptions.py`) |
| Before/after comparison of the two plans | ✅ (frontend) | `frontend/src/lib/recoveryComparison.ts` |
| Data used | Synthetic `CORRIDOR_A` fixture (`frontend/src/data/corridor_a_blocks.json`), **not NDLS–AGC** | — |
| Scheduled train / simulated delay / changed ETA | ❌ | No `TRAIN_DELAY` type. The dataset's trains carry `delay_minutes`/`status`, but no HTTP route consumes or exposes them |
| Conflict detection against a delayed train | ❌ | — |
| Block impact on **persisted `/v1` jobs** + lifecycle re-optimization | ❌ as a disruption. The only lifecycle re-plan is Release / Not-completed followed by a normal optimize | — |

### 16.2 Decision for the Sep 26 freeze

- **P0:** keep the existing Plan / Disrupt / Recover screens, moved to `/simulation`. Wrap them with a persistent banner:
  > **SIMULATION**: stateless what-if on the synthetic *Corridor A* fixture. Not connected to the maintenance-job lifecycle, not NDLS–AGC, not a live feed. Nothing here is saved.
- Remove or rename any "LIVE" wording in the legacy views. The existing `dataStatus.ts` already separates connectivity from provenance; keep that.
- Legacy KPIs (priority captured, risk mitigated %, corridor capacity %) **appear only here**.
- **P2 (do not build before the freeze):** an NDLS–AGC train-delay simulation. It needs, at minimum:
  1. a `TRAIN_DELAY` disruption model over the canonical timetable;
  2. a non-persisting simulation endpoint that derives possession windows from the delayed timetable and re-solves a **copy** of the current `/v1` job set;
  3. a SIMULATION provenance value in the response.
  All of that is new backend work, and the backend is frozen.

---

## 17. Data provenance

### 17.1 Badge vocabulary (use these exact words)

| Badge | Meaning | Where it applies today |
|---|---|---|
| **VERIFIED** | Traced to an authoritative railway source. | **Nothing in this build qualifies.** The badge is defined so that its absence is explicit. **Do not** use it for the location cross-check; that is shown as "Location cross-check passed" (a validation, not a data source) |
| **SYNTHETIC** | Invented or illustrative data checked into the repo. | NDLS–AGC topology/chainage, timetable, possession windows, generated assets, seed jobs, evidence placeholder references |
| **SYSTEM DERIVED** | Computed by the backend from its inputs; inherits the inputs' weakest provenance. | Scores, proposals and explanations, optimization runs, obligations, execution records. Always render as **"SYSTEM DERIVED · from SYNTHETIC inputs"** |
| **DECLARED** | Entered by a person in this session; identity not verified. | Actor identities (`DECLARED_UNVERIFIED`), report text, reasons, observed times, evidence references typed in the UI |
| **SIMULATION** | What-if output that is not persisted and not part of the lifecycle. | Everything under `/simulation` (legacy `/optimize`, `/recover`, Corridor A fixture) |

The backend's own four-axis provenance (`topology`, `timetable`, `asset_condition`,
`possession`, `effective = weakest link`) is shown verbatim wherever the API returns
it: optimize response, proposal, run record. Today every axis is `SYNTHETIC`.

### 17.2 Placement rules

- **Global status bar:** `DATA: SYNTHETIC · NDLS–AGC illustrative scenario` (always visible) plus `IDENTITY: DECLARED (demo mode)`.
- **Screen headers:** a provenance chip matching the dominant content (Planning: SYSTEM DERIVED · SYNTHETIC inputs; Simulation: SIMULATION).
- **Values:** small inline tags only where a reader could mistake the kind of data (scores, proposals, obligations, evidence references).
- A reachable backend is **connectivity**, never provenance. Keep `BACKEND: CONNECTED` and `DATA: SYNTHETIC` as two separate badges. This rule already exists in `frontend/src/lib/dataStatus.ts`.
- Station names: show real station names (they are real codes) but label the corridor geometry "synthetic chainage".

---

## 18. Visual design direction for Stitch

### 18.1 Position

The earlier Stitch export ("Aether Void", `stitch/screen-*/DESIGN.md`) is
cinematic luxury-editorial: glassmorphism, volumetric glows, Bodoni display type,
pill everything, vast negative space. **Keep its colour tokens** (already in
`frontend/src/app/globals.css`, so no new brand colours) but **replace its style
rules** with an operations-console language. The goal is a safety-critical railway
control-room tool: dense, calm, legible and trustworthy.

### 18.2 Palette (existing tokens only)

| Role | Token (from `globals.css`) |
|---|---|
| App background | `surface` / `background` `#11131a` |
| Panels (tiers) | `surface-container-lowest` `#0c0e14` (sidebar), `surface-container-low` `#191b22` (panels), `surface-container` `#1d1f26` (cards), `surface-container-high` `#282a31` (table header, hover), `surface-container-highest` `#32353c` (selected) |
| Text | `on-surface` `#e1e2eb` (primary), `on-surface-variant` `#b9cacb` (secondary), `outline` `#849495` (tertiary/disabled) |
| Borders / dividers | `outline-variant` `#3b494b` (1 px) |
| Active / selected / committed / in-progress | `primary-container` `#00f0ff`, `primary-fixed-dim` `#00dbe9` (text on dark), `on-primary-container` for filled chips |
| Pending decision / due soon / attention / postponed | `secondary` `#ffb688`, `secondary-container` `#aa5200` |
| System actor / simulation / derived | `tertiary-fixed-dim` `#d4bbff`, `on-tertiary-container` `#6f48b6` |
| Refused / critical / overdue / error | `error` `#ffb4ab`, `error-container` `#93000a`, `on-error-container` `#ffdad6` |

There is no green token in the palette, and none should be invented. "Completed" uses
a neutral-bright chip with a check icon. **Status is always label + icon, never colour
alone.**

### 18.3 Layout

- Fixed **top status bar** (48 px): wordmark, corridor, horizon, backend connectivity, data provenance, identity mode, persona switcher.
- **Left navigation rail** (240 px, collapsible to 64 px icons) grouped OPERATE / ASSURANCE / SIMULATION.
- Content area: 12-column grid, 24 px gutters, max width 1600 px. Page header = title + one-line purpose + provenance chip + primary action on the right.
- Target recording viewport: **1920×1080** (also check 1440×900).

### 18.4 Typography

- **Manrope** for all UI text: page title 24/32 semibold; section title 16/24 semibold; body 14/20; table 13/18.
- **Space Mono** for identifiers, codes, timestamps, minutes, scores and enum values (12/16; uppercase + 0.08em tracking only for micro-labels).
- **Bodoni Moda**: wordmark only, or drop it. Never for data or headings.
- Numbers: tabular figures, right-aligned in tables.

### 18.5 Status indicators

| Backend status | Display label | Style |
|---|---|---|
| `reported` | Reported · awaiting optimization | Neutral outline chip |
| `scheduled` | Proposed · awaiting authority | Amber (secondary) outline chip |
| `notified` | **Approved · committed** (raw `notified`; no notification is sent) | Cyan filled chip + lock icon |
| `in_progress` | In progress | Cyan outline chip + play icon |
| `completed` | Completed | Neutral-bright chip + check icon |

Solver status: `OPTIMAL` (cyan), `FEASIBLE` (amber), `INFEASIBLE`/`NO_SOLUTION` (error).
Obligation state: `WITHIN_SLA` (neutral), `DUE_SOON` (amber), `OVERDUE`/`ESCALATED_*`
(error), `MOOT`/`NONE`/`NO_SLA_DEFINED` (dim). Severity: CRITICAL (error), MODERATE
(amber), MINOR/NONE (neutral).

### 18.6 Components

- **Cards/panels:** 8 px radius (not the 1 rem / pill of Aether Void), 1 px `outline-variant` border, no glow, no blur, no gradient fills. Use cards only for grouped facts, not decoration.
- **Tables:** 40 px rows, sticky header on `surface-container-high`, zebra off, hover row highlight, mono numerics, sortable headers with an explicit sort icon, empty/loading/error rows built in.
- **Timeline (audit):** left rail 2 px line, 24 px event icons, run-group brackets, state-transition chips, collapsible details.
- **Dialogs:** 560 px width. Title = action. Body = "What will change" (before → after) + required inputs + demo-limitation note. Footer = secondary "Cancel" + primary action named by verb. Destructive-intent actions (Reject, Release) use an error-outline button. **No auto-closing** and no confirm-on-enter for decisions.
- **Confirmation pattern:** single confirm dialog + result toast + inline state change. No double "Are you sure?".
- **Forms:** labels above fields, helper text below, inline validation on blur and on submit, errors from the server shown verbatim under the relevant group.
- **Buttons:** 36 px, 6 px radius. Primary = `primary-container` fill with `on-primary` text. Secondary = outline.
- **Icons:** Material Symbols Outlined (already loaded).
- **Motion:** 150 ms fades for state changes only. **No** particle backgrounds, radial glows, shimmer on data, pulsing alarms or decorative animation.
- **Forbidden:** fake maps, decorative gradients, stock hero imagery, "AI" sparkle icons, invented live counters, glassmorphism.

### 18.7 Responsive

- ≥1280 px: full layout. 1024–1279 px: nav collapses to icons, tables drop low-priority columns (Run, Last solver status).
- **Field Inspection must work at 390 px** (single column, large touch targets). A field worker's form is a mobile form.
- Below 768 px, tables become stacked cards. Timeline stays vertical.

---

## 19. Consolidated Stitch prompt

Copy everything inside the block below into Stitch.

```text
PRODUCT
Pashupatastra is a railway maintenance block-planning decision-support console for a single corridor, CORR-NDLS-AGC (New Delhi – Agra Cantt, stations NDLS, NZM, FDB, PWL, MTJ, RKM, AGC; tracks UP-1 and DOWN-1; six sections NDLS-NZM, NZM-FDB, FDB-PWL, PWL-MTJ, MTJ-RKM, RKM-AGC). It turns a field defect report into an explainably scored maintenance job, places it with a constraint optimizer (Google OR-Tools CP-SAT) inside possession windows (train-free gaps) without moving already-approved work, routes it to an authority for Approve / Postpone / Reject, records crew execution with evidence references, and keeps an append-only audit trail plus derived SLA obligations.
HONESTY RULES (must be visible in the design): all data is SYNTHETIC and illustrative (real station codes, synthetic chainage/timetable/assets); identity is DECLARED via demo personas, not authenticated; the scorer is a deterministic baseline with fixed weights, NOT a trained ML model; SLA durations are ASSUMED demo values, not Indian Railways policy; evidence is a reference string, there is NO file upload; no notifications are ever sent; nothing is live.

USERS / PERSONAS (switchable in the top bar, labelled "Demo persona – declared, not verified")
- Field Worker WORKER-042: reports defects, starts and completes execution with evidence references.
- Planning Engineer ENGINEER-007: runs optimization, reviews run results.
- Section Authority AUTHORITY-017: approves, postpones (with date + reason) or rejects (with reason) proposed blocks.
Buttons for actions not belonging to the current persona are shown disabled with a tooltip.

STYLE
Railway operations command center / safety-critical enterprise tool. Dark theme using exactly these colours: background #11131a; panel tiers #0c0e14, #191b22, #1d1f26, #282a31, #32353c; text #e1e2eb, secondary #b9cacb, muted #849495; 1px borders #3b494b; active/committed cyan #00f0ff (text #00dbe9); pending/attention amber #ffb688 (container #aa5200); system/simulation violet #d4bbff; error/critical #ffb4ab (container #93000a). No green. Typography: Manrope for all UI text (page title 24px semibold, section 16px semibold, body 14px, tables 13px); Space Mono for IDs, codes, timestamps, minutes and scores; no serif display type. 8px card radius, 6px buttons, 1px borders, no glassmorphism, no glow, no gradients, no particles, no decorative cards, no maps, no animation beyond 150ms fades. Information-dense but readable: 40px table rows, sticky headers, tabular right-aligned numbers. Status is always label + icon, never colour alone. Desktop 1920x1080 primary; Field Inspection form must also work at 390px wide.

GLOBAL LAYOUT
Top status bar (48px): "PASHUPATASTRA" wordmark | Corridor CORR-NDLS-AGC | "Planning horizon 10–11 Sep 2026 (synthetic)" | "BACKEND ● CONNECTED" | "DATA: SYNTHETIC" | "IDENTITY: DECLARED (demo mode)" | persona switcher.
Left nav (240px, collapsible): Command Center; OPERATE: Field Inspection, Maintenance Jobs, Planning, Authority Review, Execution; ASSURANCE: Audit Trail, Obligations (SLA); SIMULATION: Disruption Simulation; About this demo.
Every timestamp carries a tag: PLAN (synthetic horizon time, e.g. "11 Sep 00:16 IST"), OBSERVED (entered by crew), or RECORDED (system clock, e.g. "25 Sep 14:03 IST").

SCREENS
1. Command Center: KPI row (Active maintenance jobs with R/S/A/P breakdown; Critical – reported severity; Awaiting authority decision with soonest due time; Approved/in progress; Completed; Open obligations with past-due and attention counts and "ASSUMED DEMO SLA"; Last optimization: OPTIMAL chip, 5/5 scheduled, 160 ms, "possession windows: timetable-derived (synthetic)", run id). Below: "Critical & pending work" table (Job, Work type, Severity, Priority, Risk, Status, Section, Track, Window, Obligation) and "Obligations due soonest" list. A separate small tile links to "Disruption Simulation" with a violet SIMULATION tag. Setup banner state: "Demo database not seeded or backend on wrong corridor".
2. Field Inspection (form, mobile-friendly): read-only corridor; Location group: From station (select), Toward station (only adjacent stations), Track (UP-1 / DOWN-1 segmented), Offset start m, Offset end m, live preview line "Section MTJ-RKM · 138 700 – 139 000 m chainage · synthetic topology" with inline error "End must be short of the next station"; Defect group: Work type (Track renewal, Ballast tamping, OHE maintenance, Signalling & interlocking, Routine inspection, Emergency repair, each showing its modelled duration), Severity radio (None/Minor/Moderate/Critical), Description (1000 char counter), Crew size min/max steppers (default 2/4); Evidence group: "Evidence reference (ID or path in another system)" with helper "Reference only – no file is uploaded"; "Reporting as WORKER-042 (declared, not verified)"; small link "Load demo script values"; Submit button. Success panel: job id, status Reported, priority 0.794 HIGH, risk 0.773 HIGH, "Location cross-check passed", recorded time.
3. Maintenance Jobs (queue table): columns Job ID, Work type, Severity, Priority (0.794 + band), Risk, Status chip, Section, Track, Location ("MTJ +5.70–6.00 km → RKM"), Window (PLAN time), Run, Last solver status, Last decision reason, Obligation, Reported (RECORDED). Filter bar: status tabs (All / Reported / Proposed / Approved / In progress / Completed), track, section, work type, severity. Default sort priority descending. Row actions by status: "Review proposal" (Authority), "Start execution" (Worker), "Complete" (Worker), "View audit".
4. Job Detail with tabs Overview | Explainable Risk Scoring | Proposal | Execution | Audit | Obligation. Overview shows location (field location + chainage + section + track), report text, severity, evidence reference, crew size, nearest synthetic asset association (with distance and "different section" note), advisory duplicate check.
5. Explainable Risk Scoring panel: title "Explainable Risk Scoring" subtitle "Deterministic baseline scorer – fixed, hand-authored weights. Not a trained ML model." Two big scores (priority 0.7944 HIGH, risk 0.7729 HIGH); "Top contributors" ordered list: Severe defect, Maintenance overdue, High asset criticality; "Scorer inputs (raw)" table: asset criticality 0.81, defect severity 1.00 (worker-reported CRITICAL), days overdue 43 days, failure probability 0.581, train impact 0.592, maintenance duration 90 min, historical failure rate 0.667 – plain numbers, no bars; tags "BASELINE_DETERMINISTIC" "baseline-deterministic-1.0"; footnote about synthetic asset inputs.
6. Planning: header with corridor, horizon "10 Sep 00:00 – 12 Sep 00:00 IST (synthetic)", active job count, primary button "Run optimization" (Engineer) with confirm dialog. Run result panel: solver status chip OPTIMAL "solver proved", run id, solve time, counts (considered 5 / committed 0 / scheduled 5 / unscheduled 0), "287 possession windows – derived from train-free gaps in the synthetic timetable", uncovered tracks none, provenance strip (topology SYN · timetable SYN · assets SYN · possession SYN → effective SYNTHETIC). Scheduled table (job, track, section, PLAN window, duration, "PINNED · APPROVED" tag when committed) and Unscheduled table (verbatim solver reason). Partial-success amber banner state and error states (no eligible jobs; possession data unavailable – fail-closed; timetable coverage gap). Optional horizon strip chart: rows = section × track, x-axis 48h with a day divider at 11 Sep 00:00, proposed blocks outlined, committed filled.
7. Re-optimization comparison (on Planning after a second run): table Job | Before (run 1) | After (run 2) | Change label: "PINNED · committed block preserved" (fracture, identical 11 Sep 00:16–01:46), "Postponed → now 11 Sep 15:15–17:15 ✓ not before 11 Sep", "Rejected → re-proposed · same placement 11 Sep 18:07–18:52", "Moved 11 Sep 17:15–18:45 → 10 Sep 08:34–10:04", "Moved 13:41–15:11 → 13:39–15:09". Summary line "Run 2: OPTIMAL · 5 considered · 1 committed (pinned) · 5 scheduled · 0 unscheduled". Footnote "Unapproved proposals are re-proposed on every run and may move; only approved blocks are pinned."
8. Authority Review / Proposal card: header "PROPOSAL · PROPOSED – NOT COMMITTED"; job, work type, track DOWN-1, section MTJ-RKM; PLAN time "11 Sep 2026 00:16 → 01:46 (90 min)", raw minutes 1456–1546; scores and objective 1.567; "Why this placement" list of code + verbatim detail rows: PLACEMENT "Placed at minute 1456-1546."; POSSESSION_WINDOW_REQUIRED "Requires a possession window from minute 1345 to 1546." (→ 10 Sep 22:25 – 11 Sep 01:46 IST); RESOURCE_USED "Uses track 'DOWN-1', section 'MTJ-RKM'."; COMMITTED_BLOCKS_RESPECTED "No other committed work shares this resource."; CONFLICTING_TRAINS_AVOIDED "No train movement was rejected as a conflict for this section in this run."; basis line (run id, OPTIMAL, timetable-derived windows 287); provenance strip; obligation chip "APPROVAL_PENDING · owed by AUTHORITY · WITHIN_SLA · due 07:48 IST (assumed demo SLA)". Decision bar: "Approve & commit" (primary), "Postpone…", "Reject…" (error outline).
9. Dialogs: Approve ("What will change: Proposed → Approved · committed; the block will be pinned in future optimization runs"; acting as AUTHORITY-017 declared; demo-mode note "role restrictions are not enforced by the server; not production authorization"; button "Approve and commit block"). Postpone (reason textarea required; "Not before (planning horizon date)" segmented picker with only "10 Sep 2026 (day 1)" and "11 Sep 2026 (day 2)", default 11 Sep; helper "This is the plan date, not today"; button "Postpone proposal"). Reject (reason required; notice "The optimizer does not search for an alternative window and may propose the same placement again. Use Postpone to keep work out of a date."; button "Reject proposal"). Stale state inside dialog: "This proposal was superseded by a newer optimization run – reloaded, review again".
10. Execution: list of Approved and In-progress jobs. Start execution dialog: committed block summary (PLAN 11 Sep 00:16–01:46, run id), "Actual start (OBSERVED, synthetic horizon time)" datetime with +05:30 offset defaulting to 11 Sep 00:17, Before-work evidence repeater (reference, kind PHOTO/VIDEO/DOCUMENT/MEASUREMENT, captured at, optional latitude+longitude pair "range-checked only, no geofencing", note), helper "Evidence is recorded as a reference – no upload"; button "Start execution". Complete dialog: execution id, actual start, "Actual end" default 11 Sep 01:46, After-work evidence repeater (must not reuse a before-work reference); button "Complete execution". Execution record view: attempt 1, status, planned vs actual, deviations (started early? ended late?), committed block digest, started/ended by (WORKER-042 · declared), evidence by phase with server evidence ids.
11. Audit Trail: job picker + vertical timeline grouped by optimization run brackets. Each event: icon, label + mono code, actor pill (WORKER-042 · WORKER · declared, not verified / SYSTEM:SCORER · system / AUTHORITY-017 · AUTHORITY · declared), state transition chips (Reported → Proposed), PLAN placement change, reason quote, RECORDED time, expandable details. Show the fracture job: Job created → Job scored (score explanation inline) → Optimization requested → Optimization completed → Block proposed → Block committed (authority) → Optimization requested → Optimization completed → Committed block preserved → Execution started → Execution completed. Refused attempts render as "Refused attempt – state unchanged" in error colour. Header: "Append-only · 11 events · no edits possible".
12. Obligations (SLA): mandatory banner "SLA durations are ASSUMED DEMO ENGINEERING VALUES (policy sla-assumed-demo-1.0), not Indian Railways policy. Obligations are addressed to roles, never to individuals. No notification is sent." Evaluated-at time. Table: Job, Obligation (Approval pending / Execution start pending / Completion pending / Replanning pending / Nothing owed), Owner role chip, State (WITHIN_SLA etc.), Past due flag, Clock started (RECORDED), Due (RECORDED) + SLA length, Elapsed bar, Escalation L0–L3, Reason code, Attention flag. Filters: role, type, past due only, needs attention. No acknowledge/silence buttons.
13. Disruption Simulation: the existing Plan / Disrupt / Recover what-if screens, wrapped by a violet banner "SIMULATION – stateless what-if on the synthetic Corridor A fixture. Not connected to the maintenance-job lifecycle, not NDLS–AGC, not live. Nothing is saved." Disruption types: Track unavailable, Emergency work, Possession curtailment, Asset breakdown; before/after plan comparison.
14. About this demo: honesty statements, the two clocks (PLAN vs RECORDED), provenance legend (VERIFIED – none in this build; SYNTHETIC; SYSTEM DERIVED; DECLARED; SIMULATION), demo personas, environment.

KEY INTERACTIONS / DEMO STORY
Worker submits a CRITICAL rail-fracture report (MTJ→RKM 5700–6000 m, DOWN-1) → it appears in the queue with a HIGH score and explanation → Engineer runs optimization (OPTIMAL, 5/5 scheduled) → Authority opens the fracture proposal and approves it; postpones the ballast-tamping job (RKM-AGC) to 11 Sep with reason "Festival special trains on the RKM-AGC down line on 10 Sep"; rejects the routine inspection with reason "Covered by the scheduled trolley inspection this week" → Engineer re-runs optimization; comparison shows the approved block pinned, ballast moved to day 2, inspection re-proposed → Worker starts and completes execution with before/after evidence references → audit timeline and obligations panel show the full accountable history → optional Disruption Simulation.
Loading states: skeleton rows in tables, inline spinners on buttons; error states show the backend's error code and detail text verbatim; empty states explain the next step.
```

---

## 20. Claude Code integration plan (after Stitch, not now)

### 20.0 Ground rules

- `frontend/AGENTS.md`: **Next.js 16.3.3 has breaking changes.** Read the relevant guide in `frontend/node_modules/next/dist/docs/` before writing code, and heed deprecations.
- **Do not modify the backend, the seed, or any `jobs.db`.** Backend contract = `docs/openapi-v1.json`.
- Port Stitch HTML into React components using the **existing Tailwind v4 `@theme` tokens** in `globals.css` (token names match Stitch's). Replace Stitch's style values with §18 where they differ (radii, glows, fonts).
- **No new runtime dependencies before the freeze.** Fetch with the platform `fetch`; state with React hooks/context. Adding SWR/React Query/Zustand/Playwright is P2.

### 20.1 Files: replace, adapt, keep

| Current file | Action |
|---|---|
| `src/app/page.tsx`, `src/app/layout.tsx` | Adapt: `layout` renders the new shell (status bar + nav + persona provider). `page` becomes Command Center |
| `src/components/DashboardClient.tsx` | **Move** to `src/app/simulation/` as the legacy simulation root, wrapped with the SIMULATION banner |
| `src/components/PlanView.tsx`, `DisruptRecoverView.tsx` | Keep; used only under `/simulation`; relabel "live" wording |
| `src/components/CommandCenterView.tsx` | Keep only as the legacy overview inside `/simulation` (fixture KPIs); the new Command Center is new code |
| `src/components/AppHeader.tsx` | Replace with `StatusBar.tsx` + `SideNav.tsx` |
| `src/lib/data.ts`, `metrics.ts`, `recoveryComparison.ts`, `corridorGeometry.ts`, `stage.ts` | Keep for simulation only |
| `src/lib/dataStatus.ts` | Keep; extend with the §17 badge vocabulary |
| `src/types/contracts.ts` | Keep (legacy types) |
| **New** `src/types/v1.ts` | Hand-written TS types for every `/v1` schema in `openapi-v1.json` (`JobResponse`, `JobListResponse`, `JobCreateRequest`, `FieldLocation`, `BlockProposalResponse`, `ProposalExplanationItem`, `ProvenanceResponse`, `JobOptimizationResponse`, `JobEventResponse`, `ActorResponse`, `JobStateResponse`, `ExecutionResponse`, `EvidenceInput`, `RecordedEvidenceResponse`, `ObligationResponse`, `ObligationListResponse`, `OptimizationRunResponse`, `ApiError {code, detail}`) plus `block_candidate` / event-metadata **accessors typed as optional** |
| **New** `src/lib/api/client.ts` | `apiFetch(path, {method, body, persona})`. Base = `NEXT_PUBLIC_API_URL` (existing var, default `http://127.0.0.1:8000`). Always adds `Content-Type` and both actor headers. Parses `{code, detail}` into a typed `ApiError` (422 → `detail` list). Timeout via `AbortController` (8 s; optimize 20 s) |
| **New** `src/lib/api/v1.ts` | One function per route in §2.2 and §11–15, plus `listAllJobs()` / `listAllObligations()` that loop `next_cursor` until `null` |
| **New** `src/lib/persona.tsx` | Context with the 3 personas (§2.1). Persisted in `localStorage` (try/catch). Exposes `can(action)` for UI gating only |
| **New** `src/lib/horizon.ts` | `HORIZON_START = "2026-09-10T00:00:00+05:30"`, `HORIZON_MINUTES = 2880`, `planTime(min)`, `horizonDates()`, IST formatters, `recordedTime(iso)`, tag helpers |
| **New** `src/lib/corridorTopology.ts` | The §6.3 section table (SYNTHETIC) + `convertFieldLocation()` mirroring the backend, with unit-test vectors (MTJ→RKM 5700–6000 → 138700–139000; RKM→AGC 3000–3600 → 193000–193600; far-end direction; boundary refusal) |
| **New** `src/lib/reopt.ts` | Classification of §12.1 from history events |
| **New routes** | `/inspection`, `/jobs`, `/jobs/[jobId]`, `/planning`, `/review`, `/execution`, `/audit`, `/obligations`, `/simulation`, `/about` |
| **New components** | `StatusChip`, `ProvenanceStrip`, `TimeTag`, `KpiCard`, `JobsTable`, `ScorePanel`, `ProposalCard`, `DecisionDialog` (approve/postpone/reject variants), `OptimizeRunPanel`, `ReoptDiffTable`, `ExecutionDialog`, `EvidenceEditor`, `AuditTimeline`, `ObligationsTable`, `SetupBanner`, `SimulationBanner` |

### 20.2 State management

- Server state lives in the backend. Pages fetch on mount and after every mutation (**no optimistic updates** for lifecycle transitions; show the server's answer).
- A tiny `useResource(fetcher, deps)` hook provides `{data, error, loading, reload}`. A mutation calls `reload()` for the job, its history, its obligation, and the jobs list.
- Session state (React context): persona; `lastOptimize` and `previousOptimize` responses (for the §12 diff).
- **Proposal freshness:** never reuse a cached `optimization_run_id` for a decision. Re-fetch the proposal on dialog open. After any optimize, drop every cached proposal.

### 20.3 Error handling

- Switch on `code`, display `detail` verbatim. Map codes to placement: form-level (`LOCATION_INPUT_CONFLICT`, `FIELD_LOCATION_INVALID`, `ASSET_ASSOCIATION_FAILED`, `INVALID_REQUEST`, `VALIDATION_ERROR`, `IDEMPOTENCY_KEY_CONFLICT`), dialog-level (`STALE_PROPOSAL` → auto-reload proposal, `NO_CURRENT_PROPOSAL`, `INVALID_TRANSITION`, `EVIDENCE_INVALID`, `STALE_EXECUTION`, `JOB_TERMINAL`, `JOB_COMMITTED`, `CONCURRENT_MODIFICATION`), page-level (`NO_ELIGIBLE_JOBS`, `POSSESSION_DATA_UNAVAILABLE`, `TIMETABLE_COVERAGE_GAP`, `CORRIDOR_NOT_FOUND`), integrity (`COMMITTED_STATE_INCONSISTENT`, `EXECUTION_HISTORY_INCONSISTENT`, `OBLIGATION_STATE_INCONSISTENT`: "escalate, do not retry").
- `403 AUTHORIZATION_DENIED` is not produced in demo mode. Handle it generically anyway.

### 20.4 Loading, stale and offline

- `GET /health` on load and every 15 s drives the connectivity badge. **When offline, disable every mutation button** and show a banner. **`/v1` screens never fall back to fixtures.** Only `/simulation` has the legacy fixture fallback, labelled.
- Tables: skeleton rows on first load; a subtle "Refreshing…" on reload; "As of HH:MM IST" stamp (obligations use `evaluated_at`).
- Buttons: disabled + spinner while in flight; prevent double submit.

### 20.5 Demo environment wiring

- Backend and seed share `PASHUPAT_CORRIDOR_ID=CORR-NDLS-AGC` and `PASHUPAT_JOBS_DB=D:\Pashupatastra\demo\jobs.db` (the gitignored `demo/` dir the seed suggests). Unset every `PASHUPAT_AUTH_*` / `PASHUPAT_IDENTITY_*`.
- Frontend: `NEXT_PUBLIC_API_URL=http://127.0.0.1:8000`. It is inlined at **build** time for `npm run build`.
- "Load demo script values" prefill uses the constants in §2.1 (tagged DEMO SCRIPT in the UI).

### 20.6 Testing

- `npm run lint` and `npm run build` must pass.
- Unit tests for `convertFieldLocation`, `planTime`, and `reopt` classification are desirable. No test runner is installed in `frontend/`, so either a tiny `node --test` script with no dependency, or defer to P1.
- Backend regression is unchanged and out of scope: `python -m pytest backend` stays green because nothing there changes.
- `python scripts/seed_demo.py --verify` on the recording machine → `VERIFY PASS`.
- The browser acceptance test in §22 is the gate.

---

## 21. Critical path / deadline

One table; the counts in §23 are derived from it.

| ID | Capability | Priority |
|---|---|---|
| F-01 | `/v1` API client, types, `{code, detail}` error model, actor headers, cursor loops | **P0** |
| F-02 | Persona switcher (Worker / Engineer / Authority) with demo-mode disclosure | **P0** |
| F-03 | Global status bar (corridor, horizon, `/health` connectivity, DATA: SYNTHETIC, identity mode) | **P0** |
| F-04 | App shell + left nav + routes | **P0** |
| F-05 | Command Center on `/v1` (KPIs, critical & pending table, obligations due soonest, setup banner) | **P0** |
| F-06 | Field Inspection form: station/offset conversion + validation, idempotency key, demo prefill | **P0** |
| F-07 | Maintenance Jobs queue: status filter, priority sort, row actions | **P0** |
| F-08 | Job Detail with tabs (Overview essentials) | **P0** |
| F-09 | Explainable Risk Scoring panel (JOB_SCORED metadata, honest labels) | **P0** |
| F-10 | Optimize action + run result panel + failure states | **P0** |
| F-11 | Proposal Review card (explanation codes verbatim, safety basis, provenance, obligation) | **P0** |
| F-12 | Approve dialog | **P0** |
| F-13 | Postpone dialog (horizon-limited dates, reason) | **P0** |
| F-14 | Reject dialog (reason, honest re-proposal notice) | **P0** |
| F-15 | Re-optimization comparison (session diff + history classification) | **P0** |
| F-16 | Execution start dialog + evidence editor | **P0** |
| F-17 | Execution complete dialog | **P0** |
| F-18 | Audit timeline (grouped by run, actor/assurance, state transitions, reasons) | **P0** |
| F-19 | Obligations panel + per-job obligation chip + ASSUMED SLA banner | **P0** |
| F-20 | Legacy Plan/Disrupt/Recover moved to `/simulation` with SIMULATION labels | **P0** |
| F-21 | Time helpers with PLAN / OBSERVED / RECORDED tags | **P0** |
| F-22 | Stale-proposal refetch discipline, 409 handling, offline gating of mutations | **P0** |
| F-23 | Horizon strip chart (section × track × 48 h) | P1 |
| F-24 | Optimization run audit viewer (`GET /v1/optimization-runs/{id}`, raw request/result) | P1 |
| F-25 | Release committed block dialog | P1 |
| F-26 | Execution not-completed dialog | P1 |
| F-27 | Duplicate-detection advisory + asset-association display | P1 |
| F-28 | Obligation filters, SLA table popover, optional ≥30 s polling | P1 |
| F-29 | About-this-demo page (two clocks, provenance legend, env) | P1 |
| F-30 | Audit raw-JSON drawer + extended per-type metadata views | P1 |
| F-31 | Horizon constant cross-check against event `metadata.horizon_start` | P1 |
| F-32 | Tablet/mobile refinement (tables → cards; inspection form at 390 px) | P1 |
| F-33 | Score band chips, extra sorts, job-id search | P1 |
| F-34 | NDLS–AGC train-delay / ETA / conflict simulation (needs new backend) | P2 |
| F-35 | Trains around possession windows (from opaque `request_json.train_timetable`) | P2 |
| F-36 | Real authentication / login UI | P2 |
| F-37 | Evidence file upload / preview | P2 |
| F-38 | Notification centre / delivery | P2 |
| F-39 | Map / geographic view | P2 |
| F-40 | Multi-corridor selector | P2 |
| F-41 | Automated browser E2E suite (Playwright) | P2 |
| F-42 | Generated OpenAPI TS types (openapi-typescript) | P2 |

**Totals: P0 = 22, P1 = 11, P2 = 9.**

**Sequencing for Sep 25–26** (the story order is the build order; stop adding at any
point and the story still runs up to that point):

1. F-01, F-02, F-03, F-04, F-21
2. F-06, F-07, F-08, F-09
3. F-10, F-11, F-12, F-13, F-14, F-22
4. F-15
5. F-16, F-17
6. F-18, F-19, F-05
7. F-20
8. Only then P1, in order F-29, F-27, F-23, F-25, F-26

If time runs short, F-15 can fall back to showing the two run result panels side by
side, and F-05 can drop to four KPI cards. Never cut F-13/F-14 honesty copy, the
provenance badges, or the stale-proposal handling.

---

## 22. Final acceptance test (browser only, no Swagger)

Windows PowerShell, repo root `D:\Pashupatastra`. Two terminals. **Stop any running
backend before seeding** (Windows locks the DB file).

**Terminal 1: seed/reset + backend**

```powershell
cd D:\Pashupatastra
Remove-Item Env:PASHUPAT_AUTH_MODE -ErrorAction SilentlyContinue
$env:PASHUPAT_CORRIDOR_ID = "CORR-NDLS-AGC"
$env:PASHUPAT_JOBS_DB     = "D:\Pashupatastra\demo\jobs.db"
python scripts/seed_demo.py --verify      # expect: VERIFY PASS (temporary DB only)
python scripts/seed_demo.py               # expect: "Reset and seeded ..." + 4 jobs 'reported' + live-intake values
python -m uvicorn backend.app.api.main:app --host 127.0.0.1 --port 8000
```

**Terminal 2: frontend**

```powershell
cd D:\Pashupatastra\frontend
$env:NEXT_PUBLIC_API_URL = "http://127.0.0.1:8000"
npm run build
npm run start                              # http://localhost:3000
```

**Browser checklist** (record the pass/fail of each line):

| # | Step | Pass criterion |
|---|---|---|
| 1 | Open `http://localhost:3000` | Command Center loads. BACKEND ● CONNECTED; DATA: SYNTHETIC; corridor CORR-NDLS-AGC; horizon 10–11 Sep 2026; 4 active jobs, all Reported; no setup banner |
| 2 | Persona → Worker (WORKER-042) → Field Inspection → "Load demo script values" (or type them) | Preview shows section MTJ-RKM, 138 700–139 000 m |
| 3 | Submit | Lands on the new job: status Reported, priority 0.794 HIGH, risk 0.773 HIGH, "Location cross-check passed", RECORDED time |
| 4 | Scoring tab | "Explainable Risk Scoring", BASELINE_DETERMINISTIC, contributors list, raw inputs with units; no "ML" wording |
| 5 | Jobs queue | 5 jobs; the fracture job first by priority; all Reported |
| 6 | Persona → Engineer → Planning → Run optimization → confirm | OPTIMAL; 5 considered / 0 committed / 5 scheduled / 0 unscheduled; 287 windows, timetable-derived; provenance SYNTHETIC; all jobs Proposed |
| 7 | Persona → Authority → open the fracture proposal | Five explanation rows with verbatim details; PLAN 11 Sep 00:16–01:46 (illustrative); obligation APPROVAL_PENDING · AUTHORITY · WITHIN_SLA · ASSUMED |
| 8 | Approve | Status Approved · committed; obligation EXECUTION_START_PENDING (WORKER) |
| 9 | Ballast proposal → Postpone → 11 Sep + seed reason | Status Reported; last decision reason shown; date picker offered only 10/11 Sep |
| 10 | Inspection proposal → Reject + seed reason | Status Reported; dialog showed the "may propose the same placement again" notice; proposal tab shows the neutral NO_CURRENT_PROPOSAL state naming AUTHORITY-017 |
| 11 | Persona → Engineer → Run optimization | OPTIMAL; 1 committed; 5 scheduled; comparison: fracture "PINNED · preserved" with identical times; ballast now on 11 Sep (day 2) satisfying not-before; inspection re-proposed |
| 12 | **Rehearsal only, never on the recording DB; re-seed afterwards.** Open a proposal's Approve dialog in a second tab **before** step 11, then confirm it after step 11 | Stale message, proposal reloads, no crash. (This deliberately writes a permanent `TRANSITION_REJECTED` event, which is why it is rehearsal-only.) |
| 13 | Persona → Worker → Execution → fracture → Start (defaults) + 1 PHOTO before-work reference | Status In progress; execution id shown; obligation COMPLETION_PENDING |
| 14 | Complete (defaults) + 1 PHOTO after-work reference (different reference) | Status Completed; deviations false/false |
| 15 | Audit tab (fracture job) | 11 events, grouped by 2 runs; actors with roles and assurance; state transitions; COMMITTED_BLOCK_PRESERVED visible; score explanation on JOB_SCORED |
| 16 | Obligations page | 4 APPROVAL_PENDING (AUTHORITY, WITHIN_SLA) + fracture "Nothing owed"; ASSUMED SLA banner; evaluated-at time |
| 17 | Command Center | Counts reflect: 4 Proposed, 1 Completed; last optimization = run 2 OPTIMAL |
| 18 | Disruption Simulation | SIMULATION banner visible; Plan loads; trigger "Track unavailable" → recovery comparison renders; nothing in `/v1` screens changed |
| 19 | Stop backend → any `/v1` screen | Connectivity OFFLINE; mutation buttons disabled; **no fixture substitution** on lifecycle screens |
| 20 | Swagger | Not opened at any point |

Reset for another take: stop the backend, run `python scripts/seed_demo.py`, restart
the backend, refresh the browser.

---

## 23. Findings

### 23.1 Backend blockers

**None block the P0 frontend.** The whole story is reachable over `/v1` at
`cc4fae2` (verified by rehearsal on a temporary DB). The frontend must work around
these non-blocking limitations. **Do not change the backend for them before the
freeze.**

1. **No topology endpoint.** The frontend must embed the six-section table (§6.3) to compute `distance_*`. Drift risk: mitigate with the unit vectors in §20.1 and the setup banner.
2. **Several displayed facts are outside the frozen contract:** section id, reported severity, evidence reference, field location, asset association and duplicate detection (`block_candidate`), and the scoring explanation and all event `metadata`. Parse defensively.
3. **No "list optimization runs" route.** The last run is derived from job `proposal_run_id`s or from the session.
4. **The `Idempotent-Replayed` header is not CORS-exposed** (`CORSMiddleware` without `expose_headers`), so the browser cannot read it.
5. **`possession_source` reads `GENERATED_STATIC` while `possession_derivation` is `CANONICAL_TIMETABLE_DERIVED`.** It is a legacy field; the UI must headline the derivation.
6. **The horizon is not a field on job/proposal responses.** The frontend uses the constant `2026-09-10T00:00:00+05:30` + 2880 (also present in event metadata and run `request_json`).
7. **Refused attempts are recorded permanently** (`TRANSITION_REJECTED`): an out-of-horizon postpone date, a stale run id, a missing actor. The UI must prevent them client-side to keep the demo audit clean.
8. **Reject does not seek an alternative window.** It returns the job to the queue, and the next run can propose the identical placement, as observed. This is product behaviour, documented rather than hidden.
9. `seed_demo.rehearse()` optimizes as AUTHORITY, while the contract's intended optimize role is ENGINEER. It is harmless in demo mode (unenforced); the UI uses ENGINEER.

### 23.2 Data blockers

1. **Synthetic asset association is loose.** The nearest generated asset can be ~12 km away in a different section: the live intake resolves to an OHE mast in `PWL-MTJ` at 11.85 km (`section_match: false`), and the scorer's asset inputs come from it. Show it honestly (P1 Overview) and never claim a specific asset was inspected.
2. **Two different synthetic corridors are in the app:** NDLS–AGC for `/v1`, and Corridor A for the legacy simulation. Label them separately; never mix their KPIs.
3. **Two clocks.** The fixed plan horizon is 10–11 Sep 2026; recorded/SLA times are the real wall clock (25–26 Sep 2026). Every timestamp needs a tag.
4. **SLA clocks start at optimize, in wall-clock time.** The CRITICAL approval SLA is 2 h (DUE_SOON at about 90 min). Record promptly after the first optimize; re-seed between takes.
5. **Placement reproducibility depends on the OR-Tools version, solver configuration and creation order.** Run `--verify` on the recording machine; enter the live intake after the seed and before the first optimize, with the exact seed values including crew 2/4.
6. `scoring_features` are **raw, mixed-unit inputs** (days, minutes, 0–1 values). No weights or normalized contributions are exposed.

### 23.3 Assumptions

- The frontend remains the Next.js 16.3.3 / React 19 / Tailwind v4 app in `frontend/`. Stitch output is ported into it using the existing tokens.
- The engineer persona ID is `ENGINEER-007` (a UI choice; the seed has no engineer).
- The demo DB is `D:\Pashupatastra\demo\jobs.db`. The backend runs locally on `127.0.0.1:8000` with a single operator at a time (no concurrent sessions).
- The recording happens at 1920×1080 in Chromium. All times are displayed in IST.
- The UI makes `severity` required on intake and always sends `field_location` and `corridor_id`. The API treats all three as optional.
- The observed values in this document come from one rehearsal on this machine. Anything not in §2.3 is illustrative.

### 23.4 Do NOT implement before Sep 27

- Any backend, contract, CORS, seed or `jobs.db` change. That includes a topology endpoint, a list-runs endpoint, `expose_headers`, and a TRAIN_DELAY disruption.
- The NDLS–AGC train-delay / ETA / conflict simulation (F-34) and any train timetable view (F-35).
- Authentication/login UI, enforced RBAC claims, or anything that implies production authorization (F-36).
- Evidence file upload, previews or "attach photo" UI (F-37).
- Notifications, alerts, acknowledgement/silence/escalate buttons, countdown alarms (F-38).
- Maps, geographic or animated train visuals (F-39). Multi-corridor (F-40).
- New dependencies: Playwright, OpenAPI codegen, SWR/React Query, state libraries (F-41, F-42).
- Any wording that implies ML training, live data, real IR policy or real notifications.
