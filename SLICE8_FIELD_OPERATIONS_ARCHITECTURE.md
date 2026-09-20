# SLICE 8 ARCHITECTURE GATE — FIELD OPERATIONS INTAKE

**Project:** Pashupatastra — SIH26027
**Audit date:** 2026-09-20
**Baseline:** `e4d4369465beb3b0583b86bf4663e0dd96331046` (`feat(backend): freeze frontend integration contract`)
**Mode:** READ-ONLY DESIGN GATE. No production code, test, schema, database, protected file, commit or migration was created or modified.
**Artifact added:** this file only (untracked).

---

## 1. EXECUTIVE VERDICT

**Field intake is not missing. It is under-specified in four places, and every one of them is fixable without a schema change, a new status, or a breaking contract change.**

`JobService.create_job` is already production-shaped in its *mechanics*: it validates the corridor and track, resolves the declared location to exactly one section fail-closed, scores synchronously under a `SYSTEM:SCORER` identity, and writes the job together with its `JOB_CREATED` + `JOB_SCORED` events in one transaction. That skeleton is correct and should not be redesigned.

What is missing is **fidelity between what the worker knows and what the system records**:

1. **The worker cannot say what they inspected.** `asset_id` is *derived* by `_nearest_asset` — nearest km to the job's midpoint on the same track, with **no distance bound**. A defect reported on a signal post can be filed against an OHE mast 40 km away, and nothing detects it.
2. **The worker cannot say where they are in terms they possess.** `distance_start/end` are **corridor-absolute metres**. A field worker knows a km post or "1.2 km past NZM toward AGC"; nobody in the field knows "1 000 000 metres from NDLS."
3. **The worker cannot say how long it will take or how many people it needs.** `duration_minutes` is table-driven from `WORK_TYPE_DEFAULT_DURATIONS[job_type]` and is not overridable; `workers_min/max` are recorded but constrain nothing.
4. **Two workers reporting the same defect create two competing jobs.** There is no duplicate detection of any kind, and no idempotency key, so a retried submit on a flaky connection also creates a second job.

A fifth item is real but out of scope: **no asset writeback on completion** (§24).

**The decisive constraint on this slice** is one Slice 7 created three days ago: `docs/openapi-v1.json` is frozen and guarded. Its drift test compares each schema's `required` list and `$ref` — **not its property list**. So an *optional* new field on `JobCreateRequest` is invisible to the guard and is a compatible, additive change; a *required* one changes `required` and fails loudly, as a breaking v1 change. This single mechanic determines every mandatory/optional answer below, and it is why **nothing Slice 8 adds may be mandatory**.

**Recommended Slice 8 is small:** optional additive intake fields, declared-asset validation, deterministic advisory duplicate candidates, and an intake idempotency key. Four things. No schema change. No new status. No new event type.

---

## 2. CURRENT CAPABILITIES ALREADY PRESENT

Verified by reading the implementation at this HEAD, not by trusting the prior gap list.

| # | Capability | Status | Evidence |
|---|---|---|---|
| 1 | `JobCreateRequest` | **Present** — 10 fields, `extra="forbid"`, cross-field validators for distance and worker ranges | `jobs/models.py:31` |
| 2 | Job persistence model | **Present** — 16 columns + `block_candidate_json` | `jobs/repository.py:_initialize` |
| 3 | `JobService.create_job` | **Present** — 6 validated stages | `jobs/service.py:647` |
| 4 | `JobRepository.create` | **Present** — job + creation events in one transaction | `jobs/repository.py:193` |
| 5 | Lifecycle / state machine | **Present, closed, frozen** | `lifecycle.py:149` |
| 6 | Actor model | **Present** — id/role/assurance, no `AUTHENTICATED` member | `identity/actor.py` |
| 7 | `JobAction.REPORT_JOB` | **Present**, checked before any read or write | `identity/authorization.py` |
| 8 | Append-only event history | **Present** — SQL triggers reject UPDATE/DELETE | `jobs/history.py` |
| 9 | Execution/evidence lifecycle | **Present** and evidence-gated | `jobs/execution.py` |
| 10 | `evidence_reference` at intake | **Present but thin** — optional string, 1–200 chars, stored in metadata only | `models.py:60` |
| 11 | Provenance (4-axis, weakest-link) | **Present**, no `LIVE` value | `data/provenance.py` |
| 12 | `SectionRegistry` | **Present**, authoritative, half-open `[km_start, km_end)` | `data/section_registry.py` |
| 13 | `resolve_job_resource` | **Present, fail-closed** — both endpoints must land in the *same* section | `jobs/resource_resolution.py` |
| 14 | Chainage fields | **Present** — corridor-absolute metres | `models.py:37` |
| 15 | `DefectSeverity` | **Present** — 4-member enum, optional at intake, overrides the asset for scoring | `data/models.py:20` |
| 16 | `JobType` (work type) | **Present** — 6-member enum | `models.py:12` |
| 17 | `workers_min/max`, `duration_minutes` | **Present but inert** — recorded; duration is table-derived; neither constrains the solver | `feature_adapter.py:38` |
| 18 | v1 API surface | **Present** — 15 operations, frozen | `docs/openapi-v1.json` |
| 19 | Validation utilities | **Present** — Pydantic + domain validators + `resolve_job_resource` | — |
| 20 | DB constraints | **Minimal** — PK on `job_id`, `NOT NULL`s; no FK, no CHECK, no index on location | `jobs/repository.py` |
| 21 | Test fixtures | **Present** — 1115 tests, isolated DB via `PASHUPAT_JOBS_DB` | `backend/tests/conftest.py` |
| 22 | Duplicate logic | **ABSENT** — the only `duplicate` code is evidence-reference uniqueness *within one execution* | `execution.py:514` |
| 23 | Notification hooks | **ABSENT** — `NOTIFIED` is a status name, nothing more | — |
| 24 | Audit read API | **Partial** — per-job history readable; `optimization_runs` has no HTTP route | — |
| 25 | Data provider architecture | **Present for timetable** (`TrainDataProvider` Protocol); **absent for assets and topology** | `data/train_provider.py` |

### Two findings that reframe the brief's assumptions

**An Asset domain model already exists, and it is rich.** `AssetType` (5 members: `RAIL_SECTION`, `TURNOUT_POINT`, `OHE_MAST`, `SIGNAL_POST`, `TRACK_CIRCUIT`), plus `criticality`, `condition_score`, `last_maintained_days_ago`, `defect_severity`, `gross_million_tonnes`, `historical_failure_count_3yr`, and a `defects: List[MaintenanceDefect]`. **Slice 8 must not design an asset model. One exists.**

**`MaintenanceDefect` exists and is completely dormant.** It already carries `defect_id`, `asset_id`, `detected_date`, `severity`, `defect_type`, `description`, `reported_by`. A repository-wide search shows it is **never constructed by any production code path** — only deserialized from fixtures. The defect taxonomy the brief asks to design is already modelled; nothing creates one.

---

## 3. EXACT GAPS

| # | Gap | Type | Severity |
|---|---|---|---|
| **F1** | **Asset is derived, never declared, and unbounded.** `_nearest_asset` (`service.py:1967`) returns `min(candidates, key=|asset.km_location − midpoint_km|)` over all assets on the track. No maximum distance, no asset-type agreement, no check that the asset lies within the job's declared span. A worker reporting a signal defect gets whatever asset happens to be nearest — possibly an OHE mast tens of km away. The job is then scored off *that* asset's criticality, condition and failure history. | domain correctness | **High** |
| **F2** | **`asset_id` is a dangling reference.** Assets are **generated** (`CorridorDataGenerator(seed=42).generate_assets`) on *both* the generated-corridor and dataset paths — `pashupatastra_realistic_dataset.json` carries **no `assets` key**. Ids are deterministic (`AST-{track}-{TYP}-{NNN}`) only while seed, track list and `assets_per_track=8` are unchanged. There is no assets table and no referential integrity, so changing any of those silently re-points every stored `asset_id` at a *different physical asset*. Nothing detects it. | architecture | **High** |
| **F3** | **Location is unreportable by a human.** Corridor-absolute metres only. No station-relative or km-post intake. | UX / domain | **High** |
| **F4** | **No duplicate detection.** Two workers, same defect → two jobs competing for the same possession. | domain correctness | **High** |
| **F5** | **No idempotency.** A retried submit creates a second job. Distinct from F4 and more likely in the field. | architecture | **Medium** |
| **F6** | **Duration not reportable.** Table-driven per work type; a worker who knows the job needs 4 hours cannot say so. | domain | **Medium** |
| **F7** | **Manpower inert.** `workers_min/max` validated and stored, never constrains the solver. | domain | **Medium** (crew optimization is out of scope) |
| **F8** | **No defect type at intake.** `JobType` conflates *what work is needed* with *what is wrong*. `MaintenanceDefect.defect_type` exists but is never populated. | domain | **Medium** |
| **F9** | **No inspection timestamp or GPS at intake.** Coordinates exist only in *execution* evidence. The report's own observation time and place are unrecorded. | domain | **Medium** |
| **F10** | **`evidence_reference` is unvalidated and unresolvable.** Optional 1–200 char string; nothing stores, fetches or verifies it. | architecture | **Medium** (storage deferred) |
| **F11** | **Severity has no override path.** A worker sets it at intake; no route lets an engineer correct it. No `SEVERITY_CHANGED` event exists. | domain | **Low** |
| **F12** | **`map_defect_severity` fails open.** `DEFECT_SEVERITY_WEIGHTS.get(sev_upper, 0.25)` — an unrecognised severity silently scores as MINOR. The *worker* path is enum-validated so this is unreachable today; the *asset* path takes a free string from a fixture. Fail-open in a codebase that fails closed everywhere else. | safety (latent) | **Low** |

---

## 4. CANONICAL FIELD-MAINTENANCE JOB MODEL

### The governing rule

> **`docs/openapi-v1.json` is frozen. `test_committed_openapi_snapshot_matches_the_live_contract` compares each schema's `required` list and `$ref`, not its properties. An optional added field passes silently and is compatible. A required added field fails the guard and is a breaking v1 change.**
>
> **Therefore: every field Slice 8 adds is OPTIONAL.** Making any of them mandatory is a v2 decision requiring separate approval.

### Field disposition

| Field | Today | Slice 8 disposition |
|---|---|---|
| `track_id` | mandatory | **Keep mandatory** |
| `job_type` | mandatory enum | **Keep mandatory** |
| `distance_start` / `distance_end` | mandatory, corridor-absolute metres | **Keep mandatory.** Add an optional station-relative alternative (§7) that *converts into* these — never a second source of truth |
| `workers_min` / `workers_max` | mandatory | **Keep mandatory** |
| `description` | mandatory, 1–1000 | **Keep mandatory** |
| `severity` | optional enum | **Keep optional** (§9) |
| `evidence_reference` | optional string | **Keep**; supersede with optional `evidence[]` (§11) |
| `corridor_id` | optional, validated not routed | **Keep as is** — correct design |
| `asset_id` | **derived** by nearest-km | **ADD as optional declared field** (§6). When given: validated and authoritative. When absent: current derivation, now bounded |
| `defect_type` | absent | **ADD optional enum** (§8) |
| `estimated_duration_minutes` | absent (table-derived) | **ADD optional, advisory** (§10) |
| `observed_at` | absent | **ADD optional** — the inspection's own timestamp (§12) |
| `observed_latitude` / `observed_longitude` | absent | **ADD optional, paired, range-validated, NOT geofenced** (§12) |
| `station_reference` | absent | **ADD optional** — station-relative location (§7) |
| `idempotency_key` | absent | **ADD optional** (§19) |
| `provenance` | not a field | **Remain derived** — never caller-supplied (§15) |
| `corridor`/`section_id` | derived fail-closed | **Remain derived** — never caller-supplied |
| `priority_score`/`risk_score` | derived by scorer | **Remain derived** |
| `reported_by` actor | from headers | **Remain header-derived** |

**Deliberately NOT added:** worker/crew identity beyond the actor header (authentication is a later slice); asset sub-component; cost; photographs as bytes; weather; traffic block requests. None is needed for a safe, accountable field report.

---

## 5. FIELD INTAKE LIFECYCLE

**No change to the job lifecycle. `reported` remains the single intake status.**

```
WORKER submits field report  (POST /v1/jobs, actor headers)
      │
      ├─ 1. corridor check (if named)          ──► 400 INVALID_REQUEST
      ├─ 2. track exists                       ──► 400 INVALID_REQUEST
      ├─ 3. location resolution (fail-closed)  ──► 400 INVALID_REQUEST
      │      station_reference ─► absolute metres (NEW, optional)
      │      resolve_job_resource ─► exactly one section
      ├─ 4. asset resolution (NEW: declared > derived, now bounded)
      ├─ 5. duration: declared advisory OR table default
      ├─ 6. idempotency check (NEW)            ──► 200 existing job, no new row
      ├─ 7. duplicate candidates (NEW, advisory, never blocking)
      │
      ▼
  scorer (synchronous, SYSTEM:SCORER)
      │
      ▼
  ONE transaction: job row + JOB_CREATED + JOB_SCORED
      │
      ▼
  status = reported  ──► eligible for the next optimization run
```

**Rejected: a draft/submit two-phase flow.** `PATCH /v1/jobs/{id}` and `POST /v1/jobs/{id}/submit` (floated in the brief's §N) require a `DRAFT` status. Adding a `JobStatus` is an explicit non-goal, and a draft that never enters optimization is a UI concern a frontend solves with local state. **Do not build it.**

---

## 6. ASSET MODEL

**An asset model exists and must not be redesigned.** `Asset` + `AssetType` (5 members) + `MaintenanceDefect` are already defined in `data/models.py`.

### The two real problems

**(a) Derivation is unsafe (F1).** `_nearest_asset` has no distance bound and no type agreement.

**(b) `asset_id` has no integrity (F2).** No assets table; ids are regenerated in memory at every `JobService()` construction from a fixed seed.

### Design

**Asset id class: internally generated, deterministically, with a provider seam for later.**

Today's ids (`AST-{track_id}-{TYP}-{NNN}`) are internally generated and stable *by construction*, not by storage. They are **not** opaque external ids and must not be presented as railway asset numbers. When an authorized Railway asset register becomes available, it arrives through an `AssetProvider` Protocol mirroring `TrainDataProvider` — and the `asset_condition` provenance axis moves off `SYNTHETIC` at that point, not before.

**Slice 8 implements only two things:**

1. **Optional declared `asset_id`.** When supplied, it must exist on the job's track and lie within the job's declared span (or within a bounded tolerance of it), else `400`. The worker's declaration is authoritative — never silently replaced by a nearest-match.
2. **Bound the fallback.** When `asset_id` is absent, keep `_nearest_asset`, but refuse when the nearest asset lies further than a configured tolerance from the job's span, and record `asset_resolution: "DECLARED" | "DERIVED"` in `JOB_CREATED` metadata so an auditor can tell which happened.

**Not in Slice 8:** an assets table, an asset CRUD API, asset history, sub-components, or populating `MaintenanceDefect` records. `MaintenanceDefect` stays dormant — Slice 8 records `defect_type` on the job (§8), not a defect entity with its own lifecycle.

---

## 7. LOCATION MODEL

**`SectionRegistry` and `resolve_job_resource` are sufficient. Neither needs changing.**

`resolve_job_resource` is already the strongest validation in the intake path: both endpoints resolved independently, both must land in the **same** section, track membership checked at both ends, boundary-straddling jobs refused outright with the reason stated. That is correct and stays untouched.

**The gap is purely at the boundary: the units are unusable by a human (F3).**

### Smallest extension — and it needs no registry change

`Section` already carries `start_station_id`, `end_station_id`, `km_start`, `km_end`. `SectionRegistry.resolve_between(A, B)` returns that record. **Station chainage is therefore already recoverable from the registry**, so station-relative intake converts to corridor-absolute metres by pure arithmetic in an intake-side helper:

```
station_reference = {
    from_station_id: "NZM",          # required within the object
    toward_station_id: "FDB",        # required: fixes the direction
    offset_start_m: 1200,            # ≥ 0, from from_station
    offset_end_m:   1600,            # > offset_start_m
}

    ↓  section = registry.resolve_between(from, toward)
    ↓  base_km = section.km_start if from == section.start_station_id
    ↓            else section.km_end   (with offsets applied in the
    ↓            direction of travel toward `toward_station_id`)
    ↓
distance_start/end (corridor-absolute metres)
    ↓
resolve_job_resource(...)   ← UNCHANGED
```

**Rules:**
- `station_reference` is **optional** and **mutually exclusive** with explicit `distance_start/end`. Supplying both is `400` — never silently prefer one.
- It is a *convenience converter*, not a second location model. Only corridor-absolute metres are stored, and the existing resolver remains the only authority.
- `resolve_between` already refuses an ambiguous station pair (parallel routes). That refusal propagates as `400`; nothing guesses.
- Because it is one object with its own internal requirements, adding it does not change `JobCreateRequest.required` — it passes the contract guard.

**Not in Slice 8:** km-post intake (needs a km-post register nobody has), GPS-to-chainage inference (needs surveyed track geometry — explicitly forbidden by the Step 11 brief), multi-section jobs.

---

## 8. WORK / DEFECT TAXONOMY

**Work type exists. Defect type does not, and the two are currently conflated.**

`JobType` (6 members) answers *what work is needed*: `TRACK_RENEWAL`, `BALLAST_TAMPING`, `OHE_MAINTENANCE`, `SIGNALLING_INTERLOCKING`, `ROUTINE_INSPECTION`, `EMERGENCY_REPAIR`. It does **not** answer *what is wrong* — which is what a field worker actually observes. A worker sees a cracked rail; deciding that the remedy is `TRACK_RENEWAL` is an engineering judgement they may not be qualified to make.

### Design

**Add `defect_type` as an optional controlled enum, deliberately small:**

```
TRACK_DEFECT        rail, sleeper, fastening, geometry
SIGNAL_DEFECT       signal, point, interlocking, track circuit
STRUCTURAL_DEFECT   bridge, culvert, formation, embankment
ELECTRICAL_DEFECT   OHE, traction, power supply
OTHER               with the mandatory description carrying specifics
```

Five members, matching the brief's own list. A controlled enum, not a free string — an unrecognised value is a `422` at the boundary, consistent with how `severity` and `job_type` already behave.

**Explicitly rejected:** an IR-standard defect catalogue (USFD flaw classes, TRC geometry codes). `MaintenanceDefect.defect_type` already hints at that vocabulary (`USFD_FLAW`, `OHE_STAGGER`, `TRACK_TWIST`) and it is the right long-term target — but it must come from an authorized Railway source, not be invented here. `OTHER` + the mandatory `description` carries the remainder honestly.

**`defect_type` does not affect scoring in Slice 8.** It is recorded for accountability and duplicate detection (§13) only. Wiring it into the scorer is a scorer change and is out of scope.

---

## 9. SEVERITY MODEL

**Present and largely correct.**

- **Representation:** `DefectSeverity` — `NONE`/`MINOR`/`MODERATE`/`CRITICAL`, mapped to `0.0/0.25/0.60/1.00`.
- **Validation:** typed as the enum on `JobCreateRequest`, so an unknown value is `422` at the boundary.
- **Who sets it:** any caller; optional. When supplied it is the worker's own assessment.
- **Is it trusted?** **Yes, for scoring** — `explicit_defect_severity` overrides the asset's stored `defect_severity`. This is defensible: the worker just looked at the asset; the stored record is older. It is also **auditable** — recorded as `reported_severity` in both the block metadata and the `JOB_SCORED` event, so a score can always be traced to the human claim behind it.
- **Weight:** 0.25 of risk, 0.20 of priority, plus an indirect contribution through `failure_probability`. A worker declaring `CRITICAL` materially moves the job up the queue.

### Two gaps

**F11 — no override path.** No route lets an engineer correct a mis-graded severity, and no `SEVERITY_CHANGED` event exists. **Deferred, not fixed in Slice 8:** an override is a *mutation of a reported job*, which needs an authorization model to be meaningful. With `UnenforcedPolicy`, an "engineer override" is just "anyone can change any severity" — worse than no override. **This is a hard dependency on the authentication/RBAC slice.** Until then the honest path is: reject or release with a reason, and re-report.

**F12 — `map_defect_severity` fails open.** Unreachable from the worker path today. Bounded, worth fixing while adjacent, but not a Slice 8 headline.

**The ML scorer is not redesigned.** `days_overdue` correctly derives from `asset.last_maintained_days_ago − 30` rather than from worker input — maintenance overdue-ness is an asset property, not a field observation. That is right as it stands.

---

## 10. DURATION / MANPOWER MODEL

### Duration (F6)

Today: `WORK_TYPE_DEFAULT_DURATIONS[job_type]`, six fixed values (45–240 min), not overridable. A worker who knows a job will take four hours cannot say so, and the optimizer schedules the table value.

**Design: add optional `estimated_duration_minutes`, advisory, bounded, and explicitly attributed.**

- Bounded to a sane range (e.g. 15 min ≤ d ≤ the deployment horizon) so a typo cannot create an unschedulable or possession-swallowing block.
- When supplied it becomes `BlockCandidate.duration_minutes`; when absent, the table default applies — unchanged.
- Record `duration_source: "DECLARED" | "WORK_TYPE_DEFAULT"` in `JOB_CREATED` metadata, so a placement can always be traced to whichever produced it.

This matters beyond convenience: `duration_minutes` already feeds `train_impact` in the scorer *and* is the block's actual width in the solver. A wrong duration is a wrong possession requirement.

### Manpower (F7)

`workers_min/max` are validated (`≥1`, `max ≥ min`) and stored in three places, and **constrain nothing** — `solver.py` contains no crew reference.

**Design the seam, do not implement crew optimization.**

The solver already has the mechanism: `mutual_exclusion_group` expresses "these blocks share a resource pool and cannot overlap." A future crew-capacity slice maps a crew pool onto that existing primitive. **Slice 8's only obligation is to keep `workers_min/max` accurate and recorded** — which it already does. No field, no constraint, no change.

**Explicitly out of scope:** crew rosters, shift models, skill matching, per-crew availability.

---

## 11. EVIDENCE ARCHITECTURE BOUNDARY

**No object storage in Slice 8.** The boundary below is the deliverable.

| Layer | Owns | Status |
|---|---|---|
| **Domain (here, now)** | The evidence *record*: `evidence_reference`, `evidence_kind`, `captured_at`, optional coordinates, optional note, server-minted `evidence_id`, phase. Bound to actor + job + execution by the carrying event. | **Present for execution, thin for intake** |
| **Application validation (here, now)** | Shape, length, enum membership, explicit UTC offset, ≤5 min future skew, ordering against `actual_start_at`/`actual_end_at`, uniqueness within a set, no before-reference reused as after. | **Present for execution** (`execution.py`) |
| **Object storage (DEFERRED)** | The bytes. Upload, content hash, MIME sniffing, virus scan, size limits, retention, encryption at rest, signed URLs, lifecycle. | **Absent — Slice 11** |
| **Infrastructure validation (DEFERRED)** | That a reference *resolves*; that stored bytes match their recorded hash. | **Absent — Slice 11** |

### What is immutable

Everything already is. Evidence lives inside `EXECUTION_*` event metadata in the append-only `job_events` table, protected by SQL triggers that reject `UPDATE` and `DELETE` from any connection. **No change needed.**

### Before/after representation

Already correct: `EvidencePhase` on the record, phase derived from the carrying transition, and `forbidden_references` structurally prevents re-submitting a before-work photo as after-work proof. **Do not touch.**

### The one Slice 8 change

Intake evidence is `evidence_reference: Optional[str]` — a bare string with no kind, no capture time, no coordinates, while *execution* evidence has all of it. **Add an optional `evidence: list[EvidenceInput]` to `JobCreateRequest`**, reusing the existing `EvidenceInput` model verbatim, validated by the existing `EvidenceItem` rules, recorded in `JOB_CREATED` metadata. Keep the legacy `evidence_reference` accepted for compatibility; refuse when both are supplied.

This is a *contract* alignment, not a storage system. **The report must keep saying plainly: the lifecycle requires evidence and validates its metadata; it does not store, fetch or verify any file.**

---

## 12. GPS / GEOLOCATION BOUNDARY

**Current support:** coordinates exist only on *execution* evidence — optional, paired (`latitude`/`longitude` together or neither), range-validated (`±90`/`±180`). **There is no geofencing anywhere, and this report does not claim any.** Coordinates in another state pass validation today.

### Design

- **Representation:** decimal degrees, WGS84, always a pair. Reuse the existing `EvidenceInput` validators exactly — do not invent a second coordinate model.
- **Optional, permanently.** GPS is unreliable in cuttings, tunnels and long sections of Indian Railways track. **Requiring it would make the system unusable exactly where maintenance is hardest.** A missing fix is recorded as absent, never as `(0, 0)`.
- **`observed_at`:** the device's own observation time, separate from server receipt time. Same rules as `captured_at` — explicit UTC offset required, bounded future skew, never invented when absent.
- **Relation to topology: none, deliberately.** Coordinates are recorded as *corroborating evidence*, not as a location source. The authoritative location stays `(track_id, section_id)` resolved from declared chainage.
- **Future geofence (deferred):** validating a coordinate against the job's section requires surveyed track geometry — a polyline per section with a tolerance corridor. No such geometry exists in this repository, and the Step 11 brief explicitly forbids inferring location from proxies. **When geometry arrives, a geofence check becomes advisory metadata on `JOB_CREATED` — never a hard rejection**, because a false negative would block a real defect report from a real worker standing at a real fault.

---

## 13. DUPLICATE DETECTION DESIGN

**Two different problems that the brief blends. They have different fixes.**

| | Retry duplicate | Genuine duplicate |
|---|---|---|
| Cause | One worker, flaky connection, submit sent twice | Two workers, same defect |
| Fix | **Idempotency key** (§19) | **Advisory detection** (below) |
| Outcome | No second job created at all | Both jobs exist; the second is flagged |

### Deterministic first version

On intake, after location resolution, query existing jobs and flag as candidates those where **all** hold:

1. same `section_id` (already resolved, fail-closed);
2. same `track_id`;
3. chainage spans **overlap** (`start_a < end_b AND start_b < end_a`);
4. `created_at` within a configured window (e.g. 72 h);
5. status is **not** terminal (a `completed` job is not a duplicate of new work);
6. same `defect_type` when both declare one (§8) — otherwise ignored, never used to *exclude*.

Every one of these reads a column that **already exists** on `maintenance_jobs` (`track_id`, `distance_start`, `distance_end`, `created_at`, `status`) or is already resolved in-request. **No new column, no new index, no schema change** — at demo and pilot scale this is a bounded scan.

Deliberately **not** used in v1: description text similarity (needs tuning nobody can validate yet), spatial proximity via GPS (optional field, unreliable), ML embedding similarity (the brief's own warning).

### The non-negotiable safety property

> **Duplicate detection NEVER deletes, merges, blocks or modifies a worker's report.**

The job is always created. Always. A worker who walked to a fault and filed a report must never have it silently discarded because an algorithm thought it looked familiar. Detection produces **advisory metadata only**.

### Where the flag lives — and why it needs no new event

Candidates are recorded in **`JOB_CREATED` event metadata**:

```
duplicate_candidates: ["JOB-AB12CD34EF", "JOB-99XY88ZW77"]
duplicate_rule_version: "deterministic-overlap-1.0"
```

This is exactly right architecturally: it is a **fact about creation**, computed once from the state at that moment, immutable, and already append-only. It cannot drift, and it cannot be retroactively rewritten.

### Why there is no `duplicate-review` endpoint

An authority **already** has the actions needed: reject with a mandatory reason, or postpone. What they lack is *visibility* of the candidates at review time — and that is a **read** concern, not a new action, a new event, or a new status.

**Surface `duplicate_candidates` on the existing `GET /v1/jobs/{id}/proposal` response and/or the job read**, and the brief's `POSSIBLE_DUPLICATE → references → human decision` flow is achieved with **zero new events, zero new statuses and zero new mutating routes.** The "human decision" is the existing reject-with-reason.

**Future ML plug-in point:** `duplicate_rule_version` is the seam. A later similarity model becomes `"ml-similarity-1.0"`, produces the same candidate list in the same metadata field, and every consumer is unchanged.

---

## 14. ACTOR / AUTHORIZATION BOUNDARY

**Honest current state: `X-Actor-Id` / `X-Actor-Role` are DECLARED_UNVERIFIED. `UnenforcedPolicy.enforcing = False` permits everything. No permission described below is enforced today, and this report does not claim otherwise.**

### Intended matrix (design now, enforce in the auth/RBAC slice)

| Action | `JobAction` | Intended role | Exists? |
|---|---|---|---|
| Create field job | `REPORT_JOB` | WORKER, ENGINEER | ✅ |
| Amend a report | — | — | **Not designed** — needs a draft status (rejected, §5) |
| Submit | — | — | **N/A** — creation *is* submission |
| Score | — | SYSTEM:SCORER | ✅ automatic, correctly attributed |
| Optimize | `REQUEST_OPTIMIZATION` | ENGINEER, SYSTEM | ✅ |
| Review proposal | `READ_BLOCK_PROPOSAL` | AUTHORITY, ENGINEER | ✅ |
| Approve / reject / postpone / release | 4 actions | AUTHORITY | ✅ |
| Execute | `START_EXECUTION`, `COMPLETE_JOB` | WORKER | ✅ |
| Override severity | — | ENGINEER, AUTHORITY | ❌ **deferred** (§9) |

**Slice 8 adds no new `JobAction`.** Every intake operation is `REPORT_JOB`; duplicate candidates are computed inside it; idempotency is a property of it. Adding actions that no policy enforces would be ceremony.

**The one real dependency:** severity override is *blocked* on authentication. Without it, "engineer override" means "anyone may rewrite any severity" — strictly worse than the current state. Correctly deferred.

---

## 15. PROVENANCE

**Use the existing four-axis model unchanged. Introduce nothing new.**

A field report's inputs land on the existing axes:

| Axis | Today | With Slice 8 |
|---|---|---|
| `topology` | `SYNTHETIC` | **unchanged** |
| `timetable` | `SYNTHETIC` | **unchanged** |
| `asset_condition` | `SYNTHETIC` (generated assets) | **unchanged** — a declared `asset_id` selects a synthetic asset more accurately; it does not make it real |
| `possession` | `SYNTHETIC` | **unchanged** |

**Critical point, stated plainly:** a genuine human observation of a real defect does **not** promote any axis. The worker's severity claim is real, but the asset record it attaches to, the topology it is located against and the timetable it is scheduled around remain synthetic. Under the weakest-link rule `effective` stays `SYNTHETIC`. **Adding a `REAL_FIELD_REPORT` level would be exactly the unjustified promotion the provenance module was built to prevent.**

The report's *honesty* is already carried correctly and separately by `IdentityAssurance` (`DECLARED_UNVERIFIED`) — the right axis for "a human claimed this and nobody verified it." Two systems already exist for two different questions; **do not merge them and do not add a third.**

---

## 16. AUDIT EVENTS

**Slice 8 adds NO new event type.** Every fact it introduces is a fact *about creation*, and `JOB_CREATED` already exists, is append-only, and carries arbitrary JSON metadata.

| Brief's proposed event | Verdict |
|---|---|
| report created | **`JOB_CREATED`** — exists |
| report submitted | **Not needed** — creation is submission |
| report amended | **Not needed** — no draft state (§5) |
| duplicate flagged | **`JOB_CREATED` metadata** (§13) — a creation-time fact, not a separate occurrence |
| duplicate reviewed | **Not needed** — the existing reject/postpone *is* the review |
| job created | Same as the first row |
| evidence attached | **`JOB_CREATED` metadata** at intake; `EXECUTION_*` during execution — both exist |
| severity changed | **Deferred** with the override itself (§9) |
| authority override | **Deferred** likewise |

### `JOB_CREATED` metadata extension (additive only)

Existing keys stay byte-identical. New optional keys:

```
asset_resolution        "DECLARED" | "DERIVED"
asset_distance_km       how far the resolved asset lay from the job span
defect_type             the declared enum, or null
duration_source         "DECLARED" | "WORK_TYPE_DEFAULT"
location_source         "ABSOLUTE_CHAINAGE" | "STATION_RELATIVE"
observed_at             the inspection's own timestamp, or null
observed_position       {latitude, longitude} or null  (NOT geofenced)
duplicate_candidates    [job_id, ...]
duplicate_rule_version  "deterministic-overlap-1.0"
idempotency_key         the caller's key, or null
```

All values are plain JSON, satisfying `JobEvent`'s canonical-serialization contract. `canonical_bytes()` remains valid for the future hash chain.

---

## 17. REQUIRED API CHANGES

**Preserve every Slice 7 principle: stable error codes, explicit state prerequisites, actor headers, no ambiguous mutation, stale protection, side-effect-free reads.**

### Routes — no new mutating route

| Route | Change |
|---|---|
| `POST /v1/jobs` | **Extended** — optional fields only. Adds `409 DUPLICATE_*`? **No** — duplicates never block. Adds `200` alongside `201` for an idempotent replay (§19) |
| `GET /v1/jobs/{id}` | **Unchanged shape**; `block_candidate` metadata gains the new keys (already free-form, not contract-frozen) |
| `GET /v1/jobs/{id}/history` | **Unchanged**; `JOB_CREATED.metadata` carries the new keys |
| `GET /v1/jobs/{id}/proposal` | **Optionally extended** — surface `duplicate_candidates` for authority review (§13) |
| `PATCH /v1/jobs/{id}` | **DO NOT BUILD** — needs a draft status |
| `POST /v1/jobs/{id}/submit` | **DO NOT BUILD** — creation is submission |
| `POST /v1/jobs/{id}/duplicate-review` | **DO NOT BUILD** — reject/postpone already is the review |

### Error codes

New codes are **additive** to `ErrorCode` and covered by the existing `test_error_code_vocabulary_is_frozen` (which must be updated deliberately, as intended):

```
ASSET_NOT_FOUND            declared asset_id unknown on this track
ASSET_LOCATION_MISMATCH    declared/derived asset too far from the job span
LOCATION_INPUT_CONFLICT    both station_reference and absolute chainage given
STATION_REFERENCE_INVALID  unknown station, ambiguous pair, or bad offsets
DURATION_OUT_OF_RANGE      declared duration outside the admissible band
IDEMPOTENCY_KEY_CONFLICT   same key, materially different payload
```

### Contract-guard impact — stated precisely

- Optional `JobCreateRequest` fields → `required` unchanged → **guard passes silently**, compatible.
- A `200` response added to `POST /v1/jobs` → `responses` list changes → **guard fails**, must be regenerated deliberately.
- `duplicate_candidates` on the proposal response → response *properties*, not `required` → **guard passes**.

**`docs/openapi-v1.json` and `docs/api-contract-v1.md` must both be regenerated as a deliberate, reviewed step** — the guard failing is the mechanism working, not a problem to route around.

---

## 18. SCHEMA DECISION

## **NO SCHEMA CHANGE IS REQUIRED FOR SLICE 8.**

Stated decisively, because this is a gate.

| Need | Satisfied by | Precedent |
|---|---|---|
| New intake fields | `block_candidate_json` metadata + `JOB_CREATED` event metadata | **Slice 2 did exactly this** for `reported_severity` and `evidence_reference` |
| Duplicate candidates | `JOB_CREATED` metadata, computed once at creation | Same |
| Duplicate *query* | Existing real columns: `track_id`, `distance_start`, `distance_end`, `created_at`, `status` | No new column, no new index needed at this scale |
| Idempotency | Existing `job_events` + `block_candidate` metadata (§19) | Event-derived, like `BlockProposal` and `ExecutionRecord` |
| Asset validation | In-memory `corridor.assets`, already loaded | — |
| Station-relative location | `Section.km_start`/`km_end`, already in the registry | — |

**Why event-derived is sufficient here:** the repository already derives two first-class read models from history alone — `BlockProposal` (`proposal.py`) and `ExecutionRecord` (`execution.py`), the latter explicitly noting *"a second, independently-written store would be a second source of truth."* Intake metadata is strictly simpler than either: written once, never mutated, never reconciled.

**The one honest caveat:** if duplicate detection ever needs to scan hundreds of thousands of jobs, an index on `(track_id, created_at)` becomes worthwhile. That is a **performance** change at pilot scale, not a correctness requirement, and it is a later, separately-approved decision.

---

## 19. CONCURRENCY / IDEMPOTENCY STRATEGY

### Concurrency — already correct, one honest gap

`create_job` deliberately does **not** take `lifecycle_lock`, documented as: *"a new job cannot conflict with a transition on an existing one."* That reasoning holds for the job's own state.

**Duplicate detection introduces the one new race:** two workers submitting overlapping reports simultaneously may each read before the other writes, so neither flags the other.

**Accept this, explicitly.** Duplicate detection is **advisory**; a missed flag costs an authority one extra review, while serializing all intake behind a global lock would make the field app's slowest path its most contended one. Record `duplicate_rule_version` so a later reconciliation pass can re-run detection over history if it ever matters. **Do not add a lock.**

### Idempotency (F5)

**Optional `idempotency_key` on `POST /v1/jobs`**, scoped to the actor.

- Absent → today's behaviour exactly. Every existing caller is unaffected.
- Present and unseen → create normally; store the key in `JOB_CREATED` metadata.
- Present and already seen **with the same payload** → return `200` with the **existing** job. No second row, no second event.
- Present and already seen **with a different payload** → `409 IDEMPOTENCY_KEY_CONFLICT`. Never silently overwrite.

Payload equality uses a canonical hash of the request, reusing the `canonical_json` helper already in `events.py` — not a second serialization scheme.

**Lookup without a new column:** `job_events` already has a `job_events_by_job` index and stores `metadata_json`; keys are scoped per actor and time-bounded (e.g. 24 h), so the scan is small. **If this proves too slow at pilot scale, that is the moment to request an index — with measurements, separately.**

The existing `BEGIN IMMEDIATE` write lock and the `event_id` `UNIQUE` constraint already prevent double-commit at the storage layer; the key prevents double-*intent*.

---

## 20. SAFETY / FAIL-CLOSED RULES

| Risk | Current behaviour | Slice 8 |
|---|---|---|
| **Wrong section** → possession covers a different stretch of line | **Fail-closed** — `resolve_job_resource` requires both endpoints in one section | Unchanged. Station-relative conversion feeds the *same* resolver |
| **Wrong track** → work scheduled on a line that cannot reach it | **Fail-closed** — track membership validated at both endpoints | Unchanged |
| **Boundary-straddling job** | **Fail-closed** — refused outright | Unchanged |
| **Wrong asset** → job scored off an unrelated asset's criticality | **FAILS OPEN** — nearest asset at any distance (F1) | **Fail closed** — bound the distance; refuse rather than guess |
| **Invalid duration** → possession-swallowing or unschedulable block | Not reachable (table-driven) | **Fail closed** — bound the declared value |
| **Invalid manpower** | **Fail-closed** — `≥1`, `max ≥ min` | Unchanged |
| **Missing evidence at intake** | Optional by design | **Unchanged** — intake evidence stays optional; *completion* evidence stays mandatory. That asymmetry is correct: a worker must be able to report a fault they are standing in front of without a working camera |
| **Duplicate work** | **Undetected** (F4) | **Detected, advisory, never blocking** |
| **Retry duplicate** | Creates a second job (F5) | **Idempotency key** |
| **Stale mutation** | **Fail-closed** — every commit/withdraw/start pins its run | Unchanged. Creation has no prior state to be stale against |
| **Terminal-state protection** | **Fail-closed** — `TERMINAL_STATUSES` checked on every write path | Unchanged |
| **Unknown severity string** | **FAILS OPEN** — silently 0.25 (F12) | Fail closed on the asset path too |
| **GPS absent** | N/A at intake | **Recorded as absent** — never `(0,0)`, never a rejection |

**Governing principle, unchanged from six prior slices: refuse rather than guess.** F1 and F12 are the two places intake currently guesses. Slice 8 closes both.

---

## 21. TEST PLAN

Not written here. Required coverage, mirroring the brief's list:

**Intake validity** — valid report with every new optional field; valid report with none (backward compatibility); each new field independently.

**Location** — station-relative conversion matches the equivalent absolute submission exactly; unknown station → `STATION_REFERENCE_INVALID`; ambiguous station pair → refused, never guessed; both location forms supplied → `LOCATION_INPUT_CONFLICT`; offsets crossing a section boundary → refused by the existing resolver.

**Asset** — declared `asset_id` honoured; unknown id → `ASSET_NOT_FOUND`; id on the wrong track → refused; declared asset outside tolerance → `ASSET_LOCATION_MISMATCH`; derived fallback beyond tolerance → refused (F1 regression); `asset_resolution` recorded correctly for both paths.

**Severity** — worker severity overrides the asset for scoring; unknown enum → `422`; unknown *asset* severity string → fails closed, not 0.25 (F12).

**Duration / manpower** — declared duration used; out of range → `DURATION_OUT_OF_RANGE`; absent → table default; `duration_source` recorded; `workers_min/max` validation unchanged.

**Duplicate detection** — overlapping recent same-section job flagged; **flagged job is still fully created** (the non-negotiable test); non-overlapping not flagged; outside the time window not flagged; terminal job not flagged; different `defect_type` not flagged; a missing `defect_type` never *excludes*; `duplicate_rule_version` recorded; candidates visible for authority review.

**Idempotency** — replay with same key + same payload → `200`, same `job_id`, no second row, no second event; same key + different payload → `409`; no key → unchanged behaviour.

**Audit** — every new metadata key present and plain-JSON; `JobEvent.canonical_bytes()` still deterministic; **no new `JobEventType` member**; history still rejects UPDATE/DELETE.

**Provenance** — a field report with full worker input still yields `effective = SYNTHETIC` on all four axes.

**Actor** — actor headers still required together; `SYSTEM` still `403`; `JOB_CREATED` still records `DECLARED_UNVERIFIED`.

**Regression (must not move)** — all 1115 existing tests; `ALLOWED_TRANSITIONS` and `JobStatus` byte-identical; no solver file changed; optimizer determinism unchanged; stale-proposal, committed-block and terminal-state protection unchanged; execution/evidence lifecycle unchanged; `jobs.db` hash unchanged.

**Contract** — OpenAPI snapshot regenerated *deliberately*; the drift test passes against the new snapshot; every new `ErrorCode` in the frozen vocabulary test.

---

## 22. EXACT SLICE 8 IMPLEMENTATION SCOPE

**Four items. Optional-additive only. No schema, no status, no event type, no solver change.**

| # | Item | Files | Size |
|---|---|---|---|
| **8.1** | **Declared asset + bounded derivation** (F1, F2) — optional `asset_id`; validate track membership and proximity; bound the `_nearest_asset` fallback; record `asset_resolution` | `models.py`, `service.py`, `errors.py` | Small |
| **8.2** | **Station-relative location** (F3) — optional `station_reference`; convert via `Section.km_start/km_end`; feed the existing resolver unchanged; mutually exclusive with absolute chainage | `models.py`, `service.py`, new intake helper, `errors.py` | Small |
| **8.3** | **Advisory duplicate candidates** (F4) — deterministic overlap + recency + section; record in `JOB_CREATED` metadata; surface for authority review; **never blocks** | `repository.py` (read query), `service.py`, `lifecycle.py` (metadata only) | Medium |
| **8.4** | **Intake idempotency key** (F5) — optional, actor-scoped, canonical-payload comparison, `200` replay / `409` conflict | `models.py`, `service.py`, `router.py`, `errors.py` | Medium |

**Optional fifth, if it stays genuinely small:** `defect_type`, `estimated_duration_minutes`, `observed_at`, `observed_position`, and `evidence[]` are each a few lines of optional field plus metadata recording (F6, F8, F9, F10). They carry no logic beyond validation. **Include them only if 8.1–8.4 land cleanly; drop them without hesitation otherwise.** F12 (severity fail-closed) is a two-line fix worth taking while adjacent.

**Model recommendation: Sonnet.** The specification is complete and mechanical, guarded by 1115 tests. **Escalate to Opus if 8.3's duplicate query or 8.4's idempotency lookup starts wanting a schema change** — that is the signal this slice has outgrown its boundary, and the correct response is to stop and re-approve, not to expand.

---

## 23. EXPLICIT NON-GOALS

Restated, because the brief asks for a boundary rather than a feature list:

- ❌ Redesign CP-SAT or any solver constraint
- ❌ Change the lifecycle or `ALLOWED_TRANSITIONS`
- ❌ Add any `JobStatus` (including `DRAFT` and `POSSIBLE_DUPLICATE`)
- ❌ Add any `JobEventType`
- ❌ Replace or extend `SectionRegistry`
- ❌ Any schema change, table, column, index or migration
- ❌ Object storage, file upload, media handling
- ❌ Geofencing
- ❌ Authentication, RBAC enforcement, NovaForge
- ❌ Notification delivery, SLA, escalation
- ❌ Crew optimization
- ❌ Live train tracking or Railway internal integrations
- ❌ An asset-management platform, asset CRUD, or populating `MaintenanceDefect`
- ❌ ML-based duplicate similarity
- ❌ Frontend
- ❌ Changing provenance architecture or multi-day horizon behaviour
- ❌ Modifying `jobs.db` or any protected file

---

## 24. DEPENDENCIES ON LATER PHASES

| Deferred item | Blocked on | Phase |
|---|---|---|
| Severity override + `SEVERITY_CHANGED` | **Authentication + RBAC** — an override is meaningless while `UnenforcedPolicy` permits everyone (§9) | Auth slice |
| Evidence storage, integrity hash, reference resolution | Object storage infrastructure (§11) | Slice 11 |
| Geofence validation | Surveyed per-section track geometry (§12) | Data integration |
| Real asset register, `REAL_*` on `asset_condition` | **Authorized Railway asset data** — category D | Blocked externally |
| IR-standard defect catalogue | Authorized Railway defect taxonomy (§8) | Blocked externally |
| Crew capacity as a solver constraint | Crew/roster model (§10) | Later domain slice |
| ML duplicate similarity | A labelled corpus that does not exist (§13) | Future |
| Index on `(track_id, created_at)` | Measured need at pilot scale (§18) | Deployment |

### One closed-loop defect, named and scoped out

**Completing maintenance never updates the asset.** `Asset.last_maintained_days_ago` and `condition_score` are set at generation and **never written back** — a repository search confirms no jobs-pipeline code touches either. Since `days_overdue = last_maintained_days_ago − 30` feeds both risk and priority, **an asset whose maintenance actually completed keeps climbing the priority queue forever.**

This is a genuine domain defect and it will matter in a pilot. It is **asset lifecycle, not field intake**, it needs a durable asset store to fix properly (which needs a schema change), and pulling it into Slice 8 would break this slice's boundary. **Classified out of scope. Recommended as the core of a later asset-lifecycle slice.**

---

## 25. ACCEPTANCE CRITERIA

1. All 1115 existing tests pass. **No test deleted, no assertion weakened.**
2. New tests cover every §21 item, including the non-negotiable "a flagged duplicate is still fully created."
3. `git diff --stat` touches **no** file under `backend/app/optimizer/` or `contracts/`.
4. `ALLOWED_TRANSITIONS`, `JobStatus` and `JobEventType` are **byte-identical** to baseline.
5. No `CREATE TABLE`, `ALTER TABLE` or `CREATE INDEX` anywhere in the diff.
6. `jobs.db` SHA-256 unchanged: `245fd9ff…52386`.
7. Every new `JobCreateRequest` field is **optional**; a request valid before Slice 8 remains valid and behaves identically.
8. `docs/openapi-v1.json` and `docs/api-contract-v1.md` regenerated **deliberately**, with the drift test passing against the new snapshot and every new `ErrorCode` in the frozen vocabulary test.
9. A field report with full worker input still reports `effective = SYNTHETIC` on all four provenance axes.
10. Duplicate detection never deletes, merges, blocks or mutates a report — proven by test.
11. No new `JobAction`, no new authorization behaviour, no claim of authentication.
12. `docs/api-contract-v1.md` still states plainly that evidence is a reference only and that geofencing does not exist.
13. One commit: `feat(backend): production-shaped field maintenance intake`.

---

# A. IMPLEMENT IN SLICE 8

1. **Declared `asset_id`** — optional; validated for track membership and proximity; authoritative when supplied *(F1)*
2. **Bounded `_nearest_asset` fallback** — refuse when the nearest asset lies beyond tolerance, instead of guessing *(F1)*
3. **`asset_resolution` in `JOB_CREATED` metadata** — `DECLARED` vs `DERIVED`, auditable *(F2)*
4. **Optional `station_reference`** — station-relative intake converted to absolute chainage, feeding the existing resolver unchanged *(F3)*
5. **Deterministic advisory duplicate candidates** — section + track + span overlap + recency; in `JOB_CREATED` metadata; **never blocking** *(F4)*
6. **`duplicate_candidates` surfaced for authority review** — a read change only, no new route, no new event *(F4)*
7. **Optional actor-scoped `idempotency_key`** — `200` replay, `409` on payload conflict *(F5)*
8. **New `ErrorCode` members** for the above, added to the frozen vocabulary
9. **Deliberate OpenAPI + contract-doc regeneration**

**If and only if 1–9 land cleanly:** optional `defect_type`, `estimated_duration_minutes`, `observed_at`, `observed_position`, `evidence[]` *(F6, F8, F9, F10)*, plus the two-line `map_defect_severity` fail-closed fix *(F12)*.

---

# B. DEFER TO LATER

1. **Severity override + `SEVERITY_CHANGED`** — hard dependency on authentication/RBAC *(F11)*
2. **Evidence object storage**, upload endpoints, content hashing, reference resolution *(F10)* — Slice 11
3. **Geofence validation** — needs surveyed track geometry; advisory when built, never a hard rejection
4. **Crew/resource capacity as a solver constraint** — the `mutual_exclusion_group` seam already exists *(F7)*
5. **Asset writeback on completion** — `last_maintained_days_ago` never resets; real defect, asset-lifecycle slice, needs a durable asset store
6. **Durable assets table + `AssetProvider` seam** — with an authorized asset register
7. **IR-standard defect catalogue** — populating `MaintenanceDefect` from an authorized taxonomy
8. **ML duplicate similarity** — `duplicate_rule_version` is the plug-in point
9. **Index on `(track_id, created_at)`** — only with measured need at pilot scale
10. **Optimization-run read API**, notifications, SLA, escalation

---

# C. DO NOT BUILD

1. **A new asset model** — a rich one already exists (`Asset`, `AssetType`, `MaintenanceDefect`)
2. **`DRAFT` status / `PATCH /v1/jobs/{id}` / `POST /jobs/{id}/submit`** — requires a new `JobStatus`; creation *is* submission; a draft is frontend local state
3. **`POSSIBLE_DUPLICATE` status** — duplicates are advisory metadata, never a lifecycle state
4. **`POST /v1/jobs/{id}/duplicate-review`** — reject/postpone with a mandatory reason already *is* the review
5. **Any new `JobEventType`** — `JOB_CREATED` metadata carries every new creation-time fact
6. **Any schema change, table, column, index or migration**
7. **Any new `JobAction`** — intake is `REPORT_JOB`; unenforced permissions are ceremony
8. **A new provenance level** (`REAL_FIELD_REPORT` or similar) — a human observation does not promote a synthetic axis; `IdentityAssurance` already carries report honesty
9. **A second location model** — corridor-absolute metres stay the single stored truth
10. **Mandatory GPS** — unusable in cuttings and tunnels, exactly where maintenance is hardest
11. **Mandatory intake evidence** — a worker must be able to report a fault without a working camera; *completion* evidence stays mandatory
12. **A global intake lock** — duplicate detection is advisory; the race is acceptable and documented
13. **Free-string defect taxonomy** — controlled enum with `OTHER` + description
14. **Any change to `resolve_job_resource`, `SectionRegistry`, the solver, or the lifecycle**

---

**Boundary in one line:** Slice 8 makes a field report *say what the worker actually knows* — which asset, where in human terms, how long, and whether someone already reported it — without adding a column, a status, an event, or a required field.

---

---

# 26. AS BUILT — SLICE 8 IMPLEMENTATION DECISIONS

**Implemented after this gate.** Four items, no schema change, no new `JobStatus`, no new
`JobEventType`, no `ALLOWED_TRANSITIONS` change, no solver/optimizer/`contracts/` change,
no new `JobAction`. Commit: `feat(backend): harden field maintenance intake`.

Where the build **differs from the gate above**, and why, is stated here so the gate is not
read as a description of the code.

## 26.1 Asset association bound (F1)

- `backend/app/jobs/asset_association.py`. `AssetAssociationPolicy(max_distance_km=20.0,
  require_same_section=False)`, injectable as `JobService(asset_policy=...)`.
- **Measured from the asset to the job's span** (0 inside it); selection among admissible
  assets is unchanged from before — nearest to the span **midpoint**, ties on
  `(km_location, asset_id)` — so a job that already had a valid asset keeps it and its score.
- **Why 20 km.** Sized against the checked-in synthetic corridor: 8 assets per track over
  195 km (~24 km apart), so the largest distance from any location to its nearest same-track
  asset is 18.05 km. Measured on that corridor: a 10 km bound leaves **23%** of its locations
  un-reportable, 17 km leaves 0.6%, 20 km leaves none. 20 km refuses associations that are
  not even the same stretch of line. **It is a plausibility
  ceiling on a sparse synthetic register, not a claim that a defect 20 km from an asset is
  "at" it.** A real asset register must tighten it by a deliberate decision.
- Outside the bound, no asset on the track, or only unusable assets (blank id, non-finite
  km) → `AssetAssociationError` → `400 ASSET_ASSOCIATION_FAILED`. Nothing is persisted.
- **Deviation from the gate:** the gate proposed an optional *declared* `asset_id`. The
  brief scoped Slice 8 to four items and did not include it; not built. Asset stays derived.
- **Sections (judgment call — flagged).** Tracks are always separated. Sections are **not**
  filtered by default: the existing tests contain three dataset-corridor jobs at km 7.0–8.3
  (section `NZM-FDB`) whose nearest asset is 1.6 km away across the station in `NDLS-NZM`;
  the nearest *in-section* asset is 17.8 km away. Forcing it would be the worse answer. Every
  association instead **records `asset_section_id` and `section_match`** (in the job and in
  `JOB_CREATED`), so a crossing is visible, never silent. `require_same_section=True` turns
  the record into a refusal for a deployment that wants the strict rule.
- Order in `create_job` changed: location is resolved **before** the asset is chosen, so the
  association is judged against the resolved section. Error precedence for a request that is
  wrong in two ways therefore now reports the location error first.
- `JobService._nearest_asset` (unbounded) was **removed**, not left as a dormant fail-open path.

## 26.2 Asset reference integrity (F2)

- `asset_fingerprint(corridor_id, asset)` = SHA-256 over `{corridor_id, asset_id, track_id,
  asset_type, km_location(3dp)}` — identity fields only, deliberately **not** condition,
  defect severity or maintenance age (those legitimately change).
- Recorded at creation in `block_candidate.metadata.asset_association.reference` and in
  `JOB_CREATED`, with `provenance: "SYNTHETIC"`.
- `JobService.verify_job_asset_reference(job | job_id)` validates a stored id against the
  **active** asset set and **fails closed** (`AssetReferenceError`) when the id is blank, not
  in the set, ambiguous, recorded against another corridor, or its fingerprint no longer
  matches (same id, different physical asset). It never returns a different asset. A job
  created before Slice 8 has no fingerprint and is reported as `RESOLVED_UNFINGERPRINTED` —
  existence checked only, never `VERIFIED`.
- **Where it is enforced today, honestly:** duplicate detection (a candidate whose asset
  reference cannot be verified is skipped and *reported*, not trusted). **Not** wired into the
  optimizer or the lifecycle: the solver never dereferences `asset_id` (verified), and several
  existing tests persist synthetic ids (`A1`, `AST-LEGACY`) that a gate there would reject.
  The `ASSET_REFERENCE_INVALID` code exists but no v1 route returns it yet.
- **Limitation, precisely:** this detects that a name no longer means what it meant. It does
  **not** make asset identity durable. Assets are still regenerated in memory
  (`CorridorDataGenerator(seed=42)`); the dataset carries no `assets` key. Durable identity
  needs an authorised asset register behind an `AssetProvider` seam — a later
  data-infrastructure slice. **Not claimed as solved.**

## 26.3 Human location intake (F3)

- `backend/app/jobs/field_location.py`. Optional `field_location {from_station_id,
  toward_station_id, offset_start_m, offset_end_m}` on `JobCreateRequest`. Offsets are metres
  from `from_station_id` toward `toward_station_id` along the one section joining them
  (`SectionRegistry.resolve_between`); converted using `Section.km_start/km_end`; then the
  **unchanged** `resolve_job_resource` decides track + section. `SectionRegistry` is untouched
  and no second location model is stored.
- **Deviation from the gate — it contradicts itself.** §7 says `station_reference` is mutually
  exclusive with `distance_start/end` *and* that adding it leaves `JobCreateRequest.required`
  unchanged. Both cannot hold: `distance_*` are required, so an alternative would make them
  optional and shrink `required`, which the drift test (`_contract_shape` compares
  `sorted(required)`) treats as a v1 break. The brief's hard constraint wins: `field_location`
  is **additive and redundant** — a **cross-check**. It must equal the declared metres
  (1 mm) else `400 LOCATION_INPUT_CONFLICT`; neither is preferred. **Station-only intake
  therefore is not delivered**; it needs `distance_*` relaxed — a v2 (or explicitly approved
  v1) decision.
- Refusals (`400 FIELD_LOCATION_INVALID`): unknown station, non-adjacent stations, same
  station, ambiguous pair (parallel routes), negative / non-finite / reversed offsets, offset
  beyond the section, track not serving the section (existing resolver). `UnknownSectionError`
  is a `KeyError` and would surface as **404 JOB_NOT_FOUND**; it is caught in the converter.
  A span ending exactly on the far station is refused (half-open convention inherited).
- No track-direction (UP/DOWN) validation: direction lives on `TrackSegment`, not `Section`,
  so there is nothing to validate it against. Not invented.

## 26.4A Duplicate detection (F4)

- `backend/app/jobs/duplicate_detection.py`, rule `deterministic-proximity-1.0`. A new report
  is flagged against every **non-terminal** job that matches on **all** of: same track, same
  section, same asset (reference verified), same work type, span gap ≤ **500 m**, created
  within **72 h** (both boundaries inclusive). Policy: `JobService(duplicate_policy=...)`.
- **Never blocks.** The job is always created; nothing is merged/rejected/edited; the first
  report's row and history are untouched (tested). Result is recorded in
  `block_candidate.metadata.duplicate_detection` and `JOB_CREATED` (`possible_duplicate`,
  `candidate_jobs`, per-candidate `signals`, `span_gap_m`, `minutes_apart`, `skipped`,
  thresholds). Candidates ordered `(created_at, job_id)`.
- **Deviation from the gate:** no `defect_type` (not one of the four items) — the work type
  is the signal. The asset is a required signal (the gate did not list it).
- Not built: text similarity, GPS, ML, a review route, a status, an event, an index. Accepted
  and documented: two *simultaneous* overlapping reports may not see each other (no intake
  lock); detection is advisory and re-runnable over history.

## 26.4B Idempotency (F5)

- `backend/app/jobs/idempotency.py`. Optional `idempotency_key`; requires actor headers.
- **Deviation from the gate — stronger, and simpler.** The gate proposed a metadata scan of
  `job_events`. Instead the `JOB_CREATED` event gets a **deterministic `event_id`**
  (`EVT-IDEM-` + SHA-256 of `(actor_id, key)`), so the *existing* `job_events.event_id UNIQUE`
  constraint refuses a second creation. The job row and events commit in one `BEGIN
  IMMEDIATE` transaction, so the loser's job row rolls back too. The loser then looks the
  winner up by event id (indexed point read, no scan) and replays or refuses. **No schema
  change, no scan, no window.**
- Same key + same request (SHA-256 of canonical request JSON, key excluded) → the existing
  job. Same key + different request → `409 IDEMPOTENCY_KEY_CONFLICT`. A key whose job no
  longer exists is refused, not reused.
- **Deviation from the gate:** a replay returns **201 + `Idempotent-Replayed: true`**, not
  `200`. Adding a `200` would change the `responses` set the drift guard compares; the
  header carries the distinction without a contract change.
- **Guarantee, exactly:** one job per `(actor_id, key)`, enforced by the database's UNIQUE
  constraint across threads and across processes sharing the SQLite file (tested with 10
  threads, 4 connections, and 4 OS processes), with no expiry.
- **Limits:** scoped to the *declared* actor id (unauthenticated); tied to the `job_events`
  table; SQLite-specific — a multi-writer/networked database must re-verify the constraint;
  a replay returns the job's *current* state; it protects intake only. **This is not a
  distributed idempotency service, and no stronger claim is made.**

## 26.5 API / contract impact

- `JobCreateRequest`: two **optional** fields. `required` unchanged; a pre-Slice-8 body is
  still accepted (tested).
- `docs/openapi-v1.json` regenerated **deliberately**: additive only — new `FieldLocation`
  schema, two optional properties, the route description. The snapshot drift test failed
  exactly as designed (a new schema component) and passes against the new snapshot.
  `docs/api-contract-v1.md` updated.
- Five new `ErrorCode`s, added deliberately to `test_error_code_vocabulary_is_frozen`
  (nothing renamed or removed): `ASSET_ASSOCIATION_FAILED`, `FIELD_LOCATION_INVALID`,
  `LOCATION_INPUT_CONFLICT`, `IDEMPOTENCY_KEY_CONFLICT`, `ASSET_REFERENCE_INVALID`.
- One pre-existing test fixture changed (assertions untouched):
  `test_job_section_resolution.py::test_job_on_multi_track_section_with_explicit_track`
  builds a corridor with 4 assets per 200 km (nearest asset 31 km from its job). It is about
  section/track resolution, so its service is built with
  `asset_policy=AssetAssociationPolicy(max_distance_km=50.0)`. Across the whole pre-existing
  suite, every job creation selects the **identical** asset as before and none is newly
  refused (measured over 592 creations); three cross-section associations are kept and now
  recorded.

## 26.6 Explicitly deferred (not built, not claimed)

Station-only intake (needs `distance_*` relaxed — v2); declared `asset_id`; `defect_type`,
`estimated_duration_minutes`, `observed_at`, GPS at intake, `evidence[]`; F12
(`map_defect_severity` fails open) and F11 (severity override); durable asset identity /
`AssetProvider`; asset writeback on completion; wiring reference verification into the
optimizer/lifecycle; a duplicate-review surface beyond the existing reject; index on
`(track_id, created_at)`; **no authentication, no real evidence storage, no geofencing, no
Railway live data, and no cross-process durability beyond SQLite's own.**
