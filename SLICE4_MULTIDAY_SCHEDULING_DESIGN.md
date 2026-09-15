# Slice 4 — Multi-Day Scheduling & Postponement Semantics

**Architecture audit and design document. No production code, schema, or data was modified to produce this report.**

Baseline: `origin/main` @ `779f679` (Slice 3 committed and pushed, 619 backend tests passing).

---

## 0. Executive summary

The solver, the canonical contracts, and the horizon-anchoring primitive are **already multi-day-correct** — this was deliberately built in Sprint 3 Step 4/5/8 and is pinned by tests (`test_horizon_midnight_crossing.py`, `test_horizon_anchor_integration.py`). Nothing in `backend/app/optimizer/solver.py` or `contracts/schemas.py` assumes a single day.

The single-day constraint lives entirely in the **jobs pipeline layer** on top of that foundation:

- `JobService.OPTIMIZATION_HORIZON_MINUTES = 1440` (`backend/app/jobs/service.py:187`)
- `JobService.create_job` hardcodes `latest_end_minute=1440` (`backend/app/jobs/service.py:576`)
- The checked-in dataset (`pashupatastra_realistic_dataset.json`) has **exactly one `service_date`** (`2026-09-10`) across all 24 trains — verified directly, not assumed
- `JobService.postpone_proposal` / `lifecycle.plan_postpone` reject any postponement whose target falls at or beyond the job's own `latest_end_minute` (currently always 1440), producing the exact symptom described in the brief: postponing to any date after the horizon's single day fails closed with "minute 1440"

Widening `OPTIMIZATION_HORIZON_MINUTES` alone is mechanically simple — the solver and contracts need no change. **The reason this is not a trivial config flip is coverage**: `derive_section_possession_windows` (`backend/app/data/timetable_adapter.py:698`) derives possession purely from the *gaps between recorded train occupations*. It has no concept of "which calendar days does this timetable actually describe." Confirmed empirically at `backend/tests/test_horizon_anchor_integration.py:420-455` (`test_possession_derivation_from_over_24h_horizon_anchored_traversals`): a horizon spanning two calendar days, fed two trains on two different `service_date`s, correctly produces a `[90, 1490]` gap window spanning most of the empty second day — because both days genuinely had timetable data. But if the horizon is widened over the checked-in dataset (one `service_date` only) **with no code change at all**, the identical mechanism produces a single, multi-thousand-minute "TRAIN_GAP" possession window covering every day for which there is *no data whatsoever* — not "no trains," but "nobody checked." This is the one path in the whole codebase that would flip from fail-closed to fail-open under a naive horizon widening, and it is exactly what product requirement item **C** (Timetable Coverage) is asking to be designed against.

The generated-slots possession path (`CorridorDataGenerator.generate_possession_windows`, used by the three synthetic corridors with no timetable) fails in the *opposite*, safe direction: it emits exactly 3 fixed slots at day-1 minute offsets regardless of `horizon_minutes`, so any block whose earliest feasible placement falls on day 2+ finds zero possession windows and is refused by the solver's existing fail-closed possession rule. That path needs no urgent fix, only an explicit multi-day design decision (repeat slots daily, or leave day 2+ permanently unworkable and document it).

**Recommendation: APPROVE WITH CONDITIONS.** See §15.

---

## 1. Current architecture

### 1.1 Layering

```
JSON dataset (pashupatastra_realistic_dataset.json, 1 service_date)
        │
        ▼
CorridorDataset (backend/app/data/corridor_dataset.py)
   - CorridorTopology, SectionRegistry, opaque timetable_records
        │
        ▼
StaticTimetableProvider (backend/app/data/train_provider.py)
   - wraps convert_timetable(); horizon_start is a CONSTRUCTOR param
        │
        ▼
convert_timetable / convert_train (backend/app/data/timetable_adapter.py)
   - per-record: parse HH:MM, resolve midnight rollover (bounded),
     resolve explicit/implicit day_offset, anchor to horizon_start via
     horizon_relative_minutes() IF horizon_start is not None
   - malformed trains -> TrainRejection (isolated, section-withholding)
        │
        ▼
TrainDataSnapshot (canonical_train.py): CanonicalTrainState[] + rejections[]
        │
        ▼
derive_section_possession_windows() (timetable_adapter.py:698)
   - per (track_id, section_id): merge occupied intervals, buffer,
     emit the COMPLEMENT as PossessionWindow("TRAIN_GAP")
   - clips to [0, horizon_minutes); trailing gap -> horizon_minutes
        │
        ▼
JobService.possession_inputs() (jobs/service.py:1183)
   - branches: dataset-with-timetable -> canonical path above
              dataset-without-timetable -> CorridorDataGenerator 3 fixed slots
        │
        ▼
build_jobs_optimization_request() (jobs/optimization.py:100)
   - fails closed if possession_windows == [] entirely (not per-track)
        │
        ▼
OptimizationRequest (contracts/schemas.py) -- horizon_start + horizon_minutes
   - every *_minute field is an int, RELATIVE TO horizon_start, unbounded
        │
        ▼
solve() (backend/app/optimizer/solver.py) -- CP-SAT
   - already horizon-length-agnostic; clamps windows to [0, horizon_minutes)
   - possession control, no-overlap, committed pinning, dependencies, mutex
        │
        ▼
JobOptimizationService.optimize_corridor() (jobs/optimization.py:405)
   - wraps solve() in an immutable audit record (AuditService.record_run)
   - applies the outcome via lifecycle.plan_optimization_outcome inside
     ONE transaction (JobRepository.mutate_jobs), re-checking each job's
     status against the pre-solve snapshot (ConcurrentJobModificationError)
        │
        ▼
JobService.{approve,reject,postpone}_proposal (jobs/service.py:889-1023)
   - authority review of the CURRENT proposal, gated by
     expected_proposal_run_id (StaleProposalError on mismatch)
```

### 1.2 The canonical time model that already exists

`backend/app/data/horizon_anchor.py` is the **one** authoritative conversion, already fully general:

```
minute_from_horizon =
    (service_date_local_midnight + clock_minutes + day_offset * 1440)
    - horizon_start
```

- `horizon_start` is a mandatory, explicit-UTC-offset ISO-8601 anchor (naive datetimes rejected outright — `parse_horizon_start`).
- `service_date` is **required, per train record**, and is never invented, defaulted, or inferred from `horizon_start` — this is a documented, deliberate design decision (module docstring, "WHY service_date IS NOT INVENTED").
- The result is an unbounded signed integer minute: negative means "before the horizon," and there is no 1440 modulus anywhere. Midnight rollover within one train's own stop sequence is resolved separately and boundedly (`resolve_absolute_minutes`, `DEFAULT_MAX_ROLLOVER_DAYS=1`, `DEFAULT_MAX_LEG_MINUTES=12h`) before horizon anchoring is applied.
- `contracts.OptimizationRequest` carries `horizon_start` (str) + `horizon_minutes` (int, no upper bound, no 1440 default assumption anywhere in the solver). Every `*_minute` field downstream (`BlockCandidate.earliest_start_minute/latest_end_minute`, `PossessionWindow.start_minute/end_minute`, `ScheduledBlock.start_minute/end_minute`) is simply an integer offset from that one anchor.

This model needs **no redesign**. §3 below states it formally as the answer to product-requirement item A, because it already *is* the answer — Slice 4's job is to make the jobs pipeline actually use the multi-day range this model already supports, and to close the coverage gap describe in §0/§7.

### 1.3 Where "one day" is actually hardcoded today

| Location | What | Why it matters |
|---|---|---|
| `backend/app/jobs/service.py:186-187` | `OPTIMIZATION_HORIZON_START = DEFAULT_HORIZON_START`; `OPTIMIZATION_HORIZON_MINUTES = 1440` | The production horizon width, used by every `optimize_corridor()` call and by `possession_inputs()`'s default. |
| `backend/app/jobs/service.py:576` | `create_job` builds every new `BlockCandidate` with `latest_end_minute=1440` hardcoded | Every job's own schedulable window ends at horizon minute 1440, **independent of `OPTIMIZATION_HORIZON_MINUTES`** — these are two separate constants that happen to agree today. |
| `backend/app/jobs/lifecycle.py:1014-1015` | `plan_postpone` reads `latest_end = int(block.get("latest_end_minute", 1440))` and refuses `not_before_minute >= latest_end` | This is the job's own **stored** field, not a live read of the deployment horizon. Its docstring ("cannot silently create availability beyond what this deployment's optimization horizon supports") states an intent that is only true because the two constants above happen to be equal. |
| `backend/app/data/generator.py:228-285` | `generate_possession_windows` emits exactly 3 fixed-offset slots (30-300, 690-870, 1260-1410) per track, **ignoring `horizon_minutes` entirely** | Used for the 3 synthetic no-timetable corridors (`CORRIDOR_A/B_DENSE/C_DISRUPTED`, which is the **default deployment corridor**, `PASHUPAT_CORRIDOR_ID` default). |
| `pashupatastra_realistic_dataset.json` | Every one of 24 trains carries `service_date: "2026-09-10"` — no other date appears anywhere in the file | The *only* checked-in real-shaped timetable data describes one calendar day. This is a data-coverage fact, not a code defect, but the code has no way to detect or report it (see §7). |
| `contracts/schemas.py:159-171` | `BlockCandidate.latest_end_minute` **default** is `1440` | Documented as deliberately pinned: bumping the *default* "would silently change which candidates are window_infeasible for every checked-in fixture." This default must **not** change; only the jobs pipeline's explicit value should. |

---

## 2. Current limitations (summary)

1. **Jobs-pipeline horizon is fixed at exactly one day**, coupled across three independent constants (`OPTIMIZATION_HORIZON_MINUTES`, `create_job`'s `latest_end_minute=1440`, and the admissibility check inside `plan_postpone`) that are not actually derived from one shared source — they merely agree by accident today.
2. **Postponement cannot express "later than tomorrow."** `plan_postpone` fails closed the instant `not_before_minute >= latest_end_minute`, and `latest_end_minute` is always 1440 for every job today (pinned by `test_postpone_beyond_horizon_fails_closed`).
3. **Postponement does not widen the job's own admissible window.** `plan_postpone` (`backend/app/jobs/lifecycle.py:1030-1035`) raises `earliest_start_minute` but leaves `latest_end_minute` untouched. Even after the horizon is widened, postponing a job to a date near the end of a wide horizon will make `latest_start = latest_end - duration < earliest_start`, and the solver's own `_clamp_window` (`solver.py:147-160`) will mark it `window_infeasible` — a **silent dead job**, not a schedulability failure a human would recognize as "postponed too far." This directly threatens the product requirement "job remains schedulable."
4. **No timetable coverage signal exists.** `derive_section_possession_windows` cannot distinguish "we confirmed no trains run this section on this day" from "we have zero data for this day." Both currently render as a wide-open `TRAIN_GAP` possession window. This is safe today only because the horizon never extends past the one day the dataset actually describes.
5. **The generated-slots possession path silently stops granting maintenance opportunity past day 1** (not a safety defect, but an unannounced product limitation once multi-day horizons exist for the default corridor).
6. **No explicit "postpone beyond deployment horizon" vs. "no timetable coverage for this date" distinction in the error surface.** Today both would currently be indistinguishable 400s from the caller's point of view once multi-day is introduced without further work.
7. **Repeated postponement has no defined monotonicity rule.** `plan_postpone`'s only bound is `0 <= not_before_minute < latest_end_minute`; nothing stops postponing a job to a date *earlier* than a previous postponement's `not_before_minute` (there is currently no way to reach this today only because there is exactly one valid target date).
8. **`possession_windows()`/`possession_inputs()` defaults are coupled to `OPTIMIZATION_HORIZON_MINUTES`**, so widening the constant automatically widens what `possession_inputs()` asks the canonical/generated paths to cover — this is correct plumbing, but it means the coverage gap in item 4 activates the moment the constant changes, with no code in between to catch it.

---

## 3. Proposed canonical multi-day time model (product item A)

**No new model is proposed.** The existing `horizon_anchor.horizon_relative_minutes` model (§1.2) is adopted as-is and stated formally here as the single canonical answer, because the audit found it already correct, already tested, and already load-bearing for the solver/contract layer:

- **Absolute anchor.** `OptimizationRequest.horizon_start` is a single explicit-UTC-offset timestamp. It is not required to be a calendar midnight (Sprint 3 Step 6, Case C/D/E) — a horizon may start at 06:00 — but the production jobs-pipeline deployment always anchors it at local midnight (`DEFAULT_HORIZON_START = "2026-09-10T00:00:00+05:30"`), and this document does not propose changing that.
- **Relative axis.** Every solver-facing time value downstream is `minutes_since(horizon_start)`, a signed integer, unbounded above and below. A value `>= 1440` is simply "day 2 or later"; a value `< 0` is "before the horizon" (used today for trains that started before minute 0 but still occupy a section inside it — see `occupied_intervals_by_section`'s negative-interval handling).
- **Midnight/day boundaries carry no special meaning downstream of the anchor.** They exist only at the anchoring step (`horizon_relative_minutes`) and inside per-train rollover resolution (`resolve_absolute_minutes`), both of which are bounded and already tested for overnight trains, delayed trains, and non-midnight horizon starts.
- **`service_date` is the only source of "which calendar day."** It is required on every timetable record used in anchored mode, is never invented, and is cross-checked against inferred rollover rather than silently trusted (`convert_train`'s explicit-vs-inferred day_offset agreement check).
- **Postponement is expressed as a minute, not a date, past the anchoring step.** `selected_date` (a calendar date the authority picks in the UI) is converted **exactly once**, through `horizon_relative_minutes(selected_date, clock_minutes=0, day_offset=0, horizon_start)`, i.e. "local midnight of `selected_date`." This is already implemented exactly this way (`JobService.postpone_proposal`, `jobs/service.py:1003-1005`) and this document proposes **no change** to that conversion — only to the bounds it is checked against (§8).

This model already satisfies every sub-case product item A lists (midnight, multiple days, overnight trains, delayed trains, non-midnight horizon starts, jobs postponed several days, timestamps before the horizon) and each has a dedicated passing test today. The work in Slice 4 is not inventing a new model; it is **removing the single-day ceiling the jobs pipeline imposes on top of a model that never had one**, and **adding the coverage signal (§7) the model was never asked to provide before because nothing exercised it past one day**.

---

## 4. Proposed multi-day horizon design (product item B)

### 4.1 Horizon start/end semantics

- **Start**: unchanged — `horizon_start` anchors minute 0, explicit UTC offset, never naive. No change proposed.
- **End**: `horizon_minutes` remains **exclusive** at the top (`[0, horizon_minutes)`), exactly as today (`solver.py`'s `_clamp_window`, `derive_section_possession_windows`'s clipping). No change proposed — this is already correct and tested for the 48h case.
- **Boundary at day seams**: no special handling needed or proposed. A block or window is free to straddle a day boundary (`23:30 -> 04:30` is already `1410 -> 1710`, tested).

### 4.2 Where horizon length should live

**Recommendation: promote `OPTIMIZATION_HORIZON_MINUTES` from a module constant to a `JobService` constructor parameter with the current value as its default**, rather than adding it to the wire-level `JobCreateRequest`/API surface. Reasoning:

- This is a **deployment-wide** planning-window decision (how many days ahead this corridor plans), not a per-job or per-optimization-request decision. Every existing analogous constant (`OPTIMIZATION_HORIZON_START`, `DEFAULT_MIN_HEADWAY_MINUTES`) is deployment-level, not request-level, and this should follow the same shape for consistency.
- `contracts.OptimizationRequest.horizon_minutes` **already exists and is per-request** at the solver-contract layer — nothing there needs to change. Only the jobs-pipeline layer that currently hardcodes it needs a deployment-level override point.
- Keeping it out of `JobCreateRequest` avoids a worker being able to request an arbitrarily long horizon per job, which would need its own validation against the deployment maximum anyway — simpler to have exactly one knob.

### 4.3 Validation rules

- `horizon_minutes` must be a positive multiple of `1440` (whole calendar days) for the production deployment, to keep the "postpone to local midnight of a date" UX meaningful — a horizon ending mid-day would make "postpone to the last representable date" ambiguous. (The underlying contract itself does not require this — `test_48_hour_horizon_schedules_both_days_without_clipping` uses exactly 2880 — this is a jobs-pipeline-level policy choice, not a solver constraint.)
- **Maximum safe horizon**: bounded by CP-SAT tractability, not by the time model. The solver already runs a fixed 10-second wall-clock budget (`_SOLVE_TIME_LIMIT_SECONDS`) with `num_search_workers=1` for reproducibility (`solver.py:36-48`); a much longer horizon means more candidate blocks and possession windows in one model, which is a **performance** question, not a correctness one. Recommend starting the configurable maximum at **7 days (10080 minutes)** — enough to cover the product's "postponed several days" case with headroom — and treating anything larger as an explicit, separately-reviewed change once real corridor-scale block/window counts are known. This is not derived from a measurement in this audit; it is a conservative starting bound pending a load test (see §11).
- **Behavior when a requested postponement exceeds the horizon**: must remain a distinct, explicit failure from "no timetable coverage for that date" (§7) and from "malformed date." See §8.4 for the exact three-way error taxonomy this document proposes.

---

## 5. Proposed data/model changes

Deliberately minimal — most of the contract layer needs **no change**:

| Component | Change | Why |
|---|---|---|
| `contracts/schemas.py: BlockCandidate.latest_end_minute` default | **No change.** Stays `1440`. | Explicitly pinned by the module's own docstring against every checked-in fixture. The jobs pipeline must pass an explicit value, never rely on the default widening. |
| `contracts/schemas.py: OptimizationRequest` | **No change.** `horizon_minutes` is already unbounded int. | Already correct. |
| `backend/app/jobs/service.py: JobService.__init__` | **Add** `horizon_minutes: int = OPTIMIZATION_HORIZON_MINUTES` constructor parameter, threaded to `possession_inputs()`'s default and to a new `latest_end_minute` used by `create_job`. | Single deployment-level knob (§4.2); backward compatible via default. |
| `backend/app/jobs/service.py: create_job` | Replace hardcoded `latest_end_minute=1440` with `self.horizon_minutes`. | Couples job admissibility to the actual deployment horizon instead of a second, independently-hardcoded constant (limitation #1). |
| `backend/app/jobs/lifecycle.py: plan_postpone` | Change the admissibility check from reading the job's **stored** `latest_end_minute` to accepting an explicit `horizon_minutes` parameter passed in by the caller (`JobService.postpone_proposal`), which passes its own `self.horizon_minutes`. `not_before_minute` is validated against `[0, horizon_minutes)`, **and** the job's `latest_end_minute` is **widened** to `horizon_minutes` as part of the same mutation (not just `earliest_start_minute` raised) — see §8.2. | Fixes limitation #2 and #3 in one change: postponement now checks against the true deployment horizon (not a field that happens to mirror it), and a postponed job keeps a non-empty admissible window inside the widened horizon instead of becoming permanently `window_infeasible`. |
| `backend/app/data/timetable_adapter.py` | **Add** a coverage-reporting function (new, additive — does not change `derive_section_possession_windows`'s existing signature/behavior) — see §7.2. | Closes the coverage gap (§0, §7) without touching the already-correct, already-tested possession-derivation arithmetic. |
| `backend/app/jobs/models.py: JobOptimizationResponse` | **Add** an optional `coverage_gaps` field (or similar) surfacing which (track/section, day) ranges had no timetable data at all, distinct from `uncovered_tracks` (which already reports possession-window absence). | Makes the new coverage signal visible to the authority UI/API, not just enforced internally. |
| `backend/app/data/generator.py: generate_possession_windows` | **Either** (a) repeat the 3 daily slots for every day in `[0, horizon_minutes)` when `horizon_minutes > 1440`, **or** (b) leave as-is and document explicitly that the generated corridors' possession stops after day 1. Recommend **(a)**, since it is a ~10-line change and keeps the three synthetic corridors usable for multi-day demos; (b) is the safe fallback if (a) is deferred. | Currently silently caps at day 1 (limitation #5); the solver already fails closed on the uncovered days either way, so this is a product-completeness fix, not a safety fix. |

No change is proposed to `PossessionWindow`, `ScheduledBlock`, `CanonicalTrainState`, `SectionTraversal`, `CorridorTopology`, or `SectionRegistry` — all are already day-boundary-agnostic by construction (confirmed by reading each; section identity in particular is deliberately time-independent, §1.2 of `section_registry.py`).

---

## 6. Proposed API/service changes

- `JobService`: add `horizon_minutes` constructor parameter (§5). No route signature changes required for job creation or listing.
- `JobService.postpone_proposal`: no change to its public signature (`selected_date`, `expected_proposal_run_id`, `reason`). Internally, it must pass `self.horizon_minutes` (not the job's stored `latest_end_minute`) into `plan_postpone`, and `plan_postpone`'s widened admissible-window behavior (§5, §8.2) becomes the actual fix visible to callers.
- `JobOptimizationResponse` / `POST /corridors/{id}/optimize-jobs`: add the coverage-gap surface from §5/§7.2 as an additive, optional field — existing clients ignoring unknown fields are unaffected.
- `PostponeProposalRequest`/route: **no wire-format change**. `selected_date` stays a plain `YYYY-MM-DD` string; the only behavior change is what dates are now *accepted* (up to the widened horizon) versus *refused* (beyond it, or where coverage is missing — see §8.4's three-way taxonomy).
- No change proposed to `notify`, `approve_proposal`, `reject_proposal`, `complete`, or any GET route.

---

## 7. Timetable/possession coverage model (product item C — the core of this design)

### 7.1 The problem, restated precisely

`derive_section_possession_windows` computes possession as **the complement of recorded train occupation** within `[0, horizon_minutes)`. This is correct *only* under the implicit assumption that every day in `[0, horizon_minutes)` is a day the timetable input actually attempted to describe. That assumption holds today by construction (the dataset has one day; the horizon is one day), and holds under Slice 3's own multi-day *tests* (which deliberately supply two distinct `service_date`s to cover a 2-day horizon — `test_horizon_anchor_integration.py:420`). It does **not** hold the moment `OPTIMIZATION_HORIZON_MINUTES` is widened over the *actual checked-in dataset*, which has exactly one `service_date`.

The system today has **no representation at all** of "which calendar dates does this timetable input claim to cover." `TrainDataSnapshot` carries `trains` and `rejections`, not a coverage range. This must be added; it cannot be inferred after the fact from the occupied intervals themselves, because "no trains recorded for this day" and "no attempt made to supply this day's timetable" are observationally identical once you're only looking at derived occupation.

### 7.2 Proposed design: explicit coverage input, checked before derivation

Add a **coverage declaration**, supplied by the same caller that supplies `timetable_records` (i.e. `CorridorDataset`), stating which calendar dates the timetable data is asserted to cover:

```python
@dataclass(frozen=True)
class TimetableCoverage:
    """Which calendar dates a timetable input actually attempted to describe."""
    covered_service_dates: frozenset[date]   # e.g. {date(2026,9,10)}
```

- For the checked-in dataset, this is **derived mechanically** from the distinct `service_date` values actually present in `timetable_records` (no new data entry needed) — `CorridorDataset` gains a `coverage: TimetableCoverage` field computed at load time in `load_corridor_dataset`.
- A **new, additive** function in `timetable_adapter.py`, e.g. `uncovered_horizon_days(coverage, horizon_start, horizon_minutes) -> list[date]`, returns every calendar date inside `[horizon_start, horizon_start + horizon_minutes)` that is **not** in `covered_service_dates`.
- `JobService.possession_inputs()` (the one boundary that already selects canonical-vs-generated, `jobs/service.py:1183`) calls this **before** invoking `possession_windows_from_provider`, and if any day in the horizon is uncovered:
  - **Fail closed for exactly those days**, not the whole request: derive possession windows normally for the covered portion of the horizon, and additionally emit **zero possession windows** (i.e., no `TRAIN_GAP` at all) for any `(track, section)` during an uncovered day-range. Concretely, this is implemented by clipping `derive_section_possession_windows`'s effective horizon per uncovered gap — the simplest correct mechanism is to call the existing derivation once per **maximal covered sub-range** of `[0, horizon_minutes)` and take the union, never emitting a window that spans an uncovered date.
  - Record the uncovered date ranges on the optimization result (`coverage_gaps`, §5/§6) so a human sees *why* nothing was schedulable on those days — mirroring exactly how `uncovered_possession_tracks` already explains a per-track refusal today, but at the day-coverage level instead of the track level.
- This function is a **pure addition**: `derive_section_possession_windows` itself does not change signature or behavior; the new coverage check runs **before** it and constrains what gets asked of it, exactly as `uncovered_possession_tracks` already runs **after** solving and explains, not prevents. The two mechanisms are complementary: coverage-gaps prevent windows from being fabricated for undescribed days; `uncovered_possession_tracks` continues to explain refusals for days that *are* described but have no matching window for a given track.

### 7.3 Real vs. synthetic, missing vs. partial

- **Real scheduled data** vs **synthetic scheduled data**: already modeled exactly right by `TrainDataProvenance.REAL_SCHEDULED` / `SYNTHETIC_SCHEDULED` (`canonical_train.py:52-66`) and the four-axis `ProvenanceProfile` (`backend/app/data/provenance.py`, read via its call sites). Coverage is an **orthogonal axis** to provenance — a day can be REAL_SCHEDULED and fully covered, REAL_SCHEDULED and missing, SYNTHETIC and covered, etc. This design does not conflate the two; `coverage_gaps` is reported alongside, never folded into, `provenance`.
- **Missing coverage** (item C's "missing"): a calendar date inside the horizon with **zero** timetable records for any train — the case just designed for.
- **Partial coverage** (item C's "partial"): a calendar date with **some** timetable records but not a provably complete description of every train that ran (e.g. a subset of services). This system has **no way to detect partial coverage from data alone today**, and this document does **not** propose inventing a completeness heuristic — that would require an authoritative "this is the full train count for this date" signal the dataset does not carry (there is no `expected_train_count` or similar field, and inventing one would be fabricating exactly the kind of assumption this codebase's own conventions (see `timetable_adapter.py`'s and `corridor_dataset.py`'s docstrings) explicitly refuse to make). **This is called out as explicitly out of scope (§13)** rather than half-solved: `covered_service_dates` asserts "a caller vouches this date's data is present," not "this date's data is complete." Completeness remains an operational/data-governance concern outside this system's verification boundary, exactly as `TrainDataProvenance` already refuses to promote SYNTHETIC data to REAL_* automatically.

### 7.4 Generated-slots path

No coverage concept applies — it has no timetable to be incomplete. §5's proposed fix (repeat the 3 slots daily) or explicit non-fix (leave capped at day 1, solver already fails closed) are the two options; this document recommends the repeat-daily fix as low-risk and low-effort but flags it as separable/deferrable from the coverage-gate work above, which is the actual safety-relevant item.

---

## 8. Postponement semantics (product item E) and reoptimization (product item F)

### 8.1 Formal meaning of `postpone(selected_date)`

Unchanged from what is already implemented and tested:

```
not_before_minute = horizon_relative_minutes(selected_date, clock_minutes=0, day_offset=0, horizon_start)
```

i.e. **local midnight of `selected_date`, in the deployment's own horizon-relative minute axis.** This is exactly product item E's proposed definition (`start_minute >= selected_date local midnight`) and needs no change — only its **bounds check** and its **effect on `latest_end_minute`** need to change (§8.2).

### 8.2 The fix: postponement must widen the admissible window, not just raise its floor

Today (`lifecycle.py:1030-1035`):

```python
withdrawn = _withdrawn(...)                       # clears placement, block.status -> PLANNED
postponed_block["earliest_start_minute"] = not_before_minute
# latest_end_minute is left untouched -- STILL 1440 for every job today
```

**Proposed change**: `plan_postpone` also sets `postponed_block["latest_end_minute"] = horizon_minutes` (the deployment horizon passed in by the caller, §5), whenever `not_before_minute + block's original duration_minutes` would otherwise not fit inside the job's current `latest_end_minute`. Concretely, the simplest correct rule that preserves intent without ever *shrinking* a job's window: `latest_end_minute = max(current_latest_end_minute, horizon_minutes)`. This guarantees `latest_start = latest_end - duration >= not_before_minute` whenever `not_before_minute < horizon_minutes` (which is exactly what the admissibility check below enforces), so the job is provably not `window_infeasible` purely as an artifact of postponement — it remains genuinely schedulable by the very next optimization run, satisfying the product requirement "job remains schedulable."

### 8.3 Admissibility bound

Replace the stored-field read with the deployment horizon passed explicitly:

```python
def plan_postpone(..., horizon_minutes: int, ...):
    ...
    if not_before_minute < 0 or not_before_minute >= horizon_minutes:
        raise InvalidTransitionError(...)   # "beyond supported horizon" / "into the past"
```

`JobService.postpone_proposal` passes `self.horizon_minutes` (§5). This makes the check's own docstring claim ("this deployment's optimization horizon") literally true, instead of true-by-coincidence.

### 8.4 Three-way failure taxonomy at postpone time

Today there is one failure mode (`InvalidTransitionError`, "beyond horizon" or "into the past"). Once multi-day + coverage gating exist, a postpone request can fail for three **distinct** reasons that a caller/UI needs to tell apart:

1. **`selected_date` resolves outside `[0, horizon_minutes)`** — genuinely beyond (or before) what this deployment plans at all. Stays `InvalidTransitionError`, unchanged shape.
2. **`selected_date` is inside the horizon, but the timetable has no coverage for it** (§7) — the postpone itself should **still succeed** (the job's constraint is valid data), but the **next optimization run** will correctly refuse to schedule it there and should say why via `coverage_gaps`, not a generic "capacity" reason. This is a deliberate design choice: postponement expresses *intent* ("not before this date"), and intent should not be blocked by a data-completeness gap that may be filled before the next optimization run. Coupling the postpone-time check to coverage would make postponement fragile to unrelated data-loading order.
3. **Stale proposal** (`expected_proposal_run_id` mismatch) — unchanged, `StaleProposalError`, already correctly implemented and tested extensively (`test_slice3_proposal_review.py`'s concurrency tests).

### 8.5 Interaction with existing proposal states

All of the following are **already correctly implemented** and require **no change** — verified directly against `lifecycle.py` and `test_slice3_proposal_review.py`:

- **Withdrawn proposal**: `plan_postpone` reuses `_withdrawn()`, the same mutation a solver refusal or a human reject applies — a postpone always clears the current placement and returns the job to `reported`. No change.
- **Reoptimization**: the postponed job is a `reported` job with a raised `earliest_start_minute` (and, after §8.2, a possibly-raised `latest_end_minute`) — it is picked up by `JobService.active_block_candidates()`/`optimization_snapshot()` like any other reported job and gets a genuinely new `BLOCK_PROPOSED` event under a new `optimization_run_id` on the next `optimize_corridor()` call. Already exactly right (`test_rejected_job_can_be_reoptimized_into_a_genuinely_new_proposal` proves the analogous reject path; postpone shares the same `_withdrawn` mechanism).
- **Committed jobs**: untouched — postpone only operates on `status == SCHEDULED` (`plan_postpone`'s own guard); a `NOTIFIED` job cannot reach this path at all (`InvalidTransitionError` if attempted).
- **Rejection**: postpone and reject are mutually exclusive terminal actions on one proposal — both are guarded by `expected_proposal_run_id` and the `SCHEDULED` precondition; once one fires, the proposal is gone and the other fails closed on the stale/invalid-state check (`test_postpone_then_approve_same_proposal_fails_closed` and siblings already prove this class of race for all three action pairs).
- **Stale proposal protection**: `expected_proposal_run_id` is checked inside the same transaction as the mutation (`JobRepository.mutate_jobs`, `BEGIN IMMEDIATE`), and the lifecycle lock additionally serializes postpone against a concurrently-running optimization at the process level (`test_postpone_blocks_on_a_running_optimization_and_cannot_postpone_stale_data` proves genuine blocking, not just sequential-ordering luck). **No change needed.**

### 8.6 Repeated postponement — a decision this document makes explicitly (item E asks for one)

**Proposed rule: postponement is not required to be monotonic.** A job may be postponed to an earlier not-before date than a previous postponement set, subject only to the ordinary `[0, horizon_minutes)` bound and the ordinary stale-proposal check. Reasoning: each postponement is a fresh authority decision made against the CURRENT proposal (gated by `expected_proposal_run_id`, which is only ever issued after a fresh optimization run), so "postpone earlier than last time" is not stale data — it's a legitimate new decision (e.g., the reason the job was postponed no longer applies). Forbidding it would require tracking a "high-water mark" not-before value across proposal lifetimes, which is state this system does not otherwise keep and which is not motivated by any invariant this audit found broken. This is a judgment call, not a mechanical finding — flagged explicitly per the audit's instructions, and worth a one-line confirmation before implementation.

---

## 9. Reoptimization (product item F) — verified, no change required

All seven checks item F asks for were traced directly against `lifecycle.py`, `optimization.py`, and their tests:

| Check | Status | Evidence |
|---|---|---|
| Old proposal cannot remain authoritative | ✅ Already true | `_withdrawn()` clears placement + `PROPOSAL_INVALIDATED`/`PROPOSAL_POSTPONED`/`PROPOSAL_REJECTED` on every path off `SCHEDULED` |
| Job remains schedulable | ⚠️ **Currently broken past the horizon** (see limitation #3) | Fixed by §8.2 |
| New optimization run is created | ✅ Already true | `new_run_id()` per `optimize_corridor()` call, immutable `optimization_runs` row per run |
| New run respects the new lower bound | ✅ Already true | Solver's `_clamp_window` reads `earliest_start_minute` unconditionally; no special-casing needed |
| New proposal references the new run | ✅ Already true | `BLOCK_PROPOSED`/`BLOCK_REPROPOSED.optimization_run_id = attempt.run_id`; `BlockProposal.proposal_id` embeds `run_id` |
| Old proposal/run history remains immutable | ✅ Already true, doubly enforced | Append-only `job_events` + SQL-trigger-guarded `optimization_runs` (`install_append_only_guards`) |
| Committed jobs remain pinned | ✅ Already true | `protect_committed_and_terminal_state` + solver's committed-pinning (`solver.py:276-314`); explicitly tested past minute 1440 (`test_multi_day_horizon_preserves_committed_block_pinning_past_1440`) |
| Unrelated jobs not corrupted | ✅ Already true | `mutate_jobs` is one transaction over exactly the job_ids considered; `test_chronological_history_has_no_cross_job_contamination` |

No structural change to the reoptimization state machine is proposed. This is the strongest part of the existing architecture for Slice 4's purposes.

---

## 10. Safety/failure enumeration (product item G)

| Case | Current behavior | Slice 4 behavior |
|---|---|---|
| Missing future timetable coverage | **Fails OPEN** once horizon > dataset's covered dates (§7.1) — this is the one real gap | Fails closed per-day via `coverage_gaps` (§7.2) |
| Missing future possession coverage (per-track, day IS covered) | Already fails closed (`possession_uncovered`, `uncovered_possession_tracks`) | Unchanged |
| Horizon exceeded (postpone target beyond deployment horizon) | Fails closed (`InvalidTransitionError`) | Unchanged mechanism, bound now correctly sourced (§8.3) |
| Invalid date (`selected_date` malformed) | Fails closed, 422 at the Pydantic boundary (`PostponeProposalRequest.validate_selected_date`) | Unchanged |
| Stale proposal | Fails closed (`StaleProposalError`), transactionally race-proof | Unchanged |
| Concurrent optimization | Serialized in-process (`lifecycle_lock`); cross-process fails closed (`ConcurrentJobModificationError` via snapshot re-check in `mutate_jobs`) | Unchanged |
| Concurrent postpone/approve/reject | Fails closed, extensively tested for every pairing | Unchanged |
| Impossible job (duration doesn't fit any window) | Fails closed (`window_infeasible`, explicit rejection reason) | Unchanged; §8.2 specifically prevents postponement from *creating* this case as a side effect |
| Committed resource conflict | Fails closed, commitment preserved, conflict surfaced (`COMMITTED_BLOCK_CONFLICT`) | Unchanged |
| Malformed multi-day train traversal | Fails closed per-train (`TrainRejection`, section-withholding — `withheld_section_ids`) | Unchanged; already exercised for multi-day in `test_horizon_anchor_integration.py` |

---

## 11. Backward compatibility (product item H)

- **`BlockCandidate.latest_end_minute` default stays 1440.** No existing fixture, generated corridor, or single-day test changes behavior from this alone.
- **`OPTIMIZATION_HORIZON_MINUTES` default stays 1440** at the `JobService` constructor level (§4.2) — existing callers that construct `JobService()` with no arguments are unaffected; only a deployment that explicitly opts into a wider horizon sees new behavior.
- **The checked-in dataset is unchanged** — it continues to validly describe exactly one day. A deployment that widens the horizon over this dataset **without adding coverage data** will (correctly, post-§7) see `coverage_gaps` reported for every day past day 1, and zero possession synthesized for those days — this is the intended fail-closed behavior, not a regression, but it does mean **multi-day demos need either a widened dataset or an explicit acknowledgment that only day 1 is schedulable**. This should be a conscious call before Slice 4 ships a demo scenario, not a surprise found at demo time.
- **Existing solver/contract tests are entirely unaffected** — none of them go through the jobs pipeline's hardcoded constants; they construct `OptimizationRequest` directly.
- **One existing test's premise inverts and must be intentionally updated, not silently left red**: `test_postpone_beyond_horizon_fails_closed` (`test_slice3_proposal_review.py:417`) asserts that `selected_date="2026-09-11"` (the day after `OPTIMIZATION_HORIZON_START`'s date) fails with `match=r"minute 1440"`. Under a widened horizon this date becomes **valid**. This test must be repointed at a date past the *new* horizon (e.g. horizon + 1 day) — call this out explicitly as an intentional behavior change in the Slice 4 PR description, not a silently-adjusted assertion.
- Two more tests exist **only because** of the current one-day ceiling and should be revisited (not necessarily removed) once multi-day lands:
  - `test_postpone_future_date_within_supported_horizon` and `test_postpone_exact_horizon_relative_minutes_calculation` both override `horizon_start` on `postpone_proposal` specifically because, under midnight anchoring + a 1-day horizon, no real future date is otherwise expressible. Once the horizon is genuinely multi-day, the override becomes unnecessary for demonstrating the same property — worth adding a **new** test that exercises a real future date against the *production* `OPTIMIZATION_HORIZON_START` with no override, since that is the actual multi-day guarantee product item E cares about. Keep the existing override-based tests too; they still validate the conversion arithmetic in isolation.
- **No frontend contract change is in scope.** `frontend/src/types/contracts.ts` was not inspected in depth (out of scope per §13) but nothing in this design changes any wire shape callers already depend on except the additive `coverage_gaps` field (§5/§6), which is safe for any client ignoring unknown JSON fields.

---

## 12. Test strategy

New coverage needed, organized by the same seven areas as §7-10:

1. **Coverage-gate unit tests** (new module, e.g. `test_timetable_coverage.py`): `uncovered_horizon_days` against 1-day-covered/2-day-horizon, fully-covered multi-day, and boundary dates (exactly `horizon_start`'s date, exactly the last date before `horizon_start + horizon_minutes`).
2. **Coverage-gate integration test**: run `JobService.possession_inputs()` with the real checked-in dataset and a horizon widened to e.g. 3 days; assert `coverage_gaps` names days 2-3 and that zero `TRAIN_GAP` windows are emitted spanning any part of those days (extend `test_jobs_canonical_timetable_integration.py`).
3. **Postpone-widens-window test**: postpone a job to a `not_before_minute` near the end of a wide horizon, then assert `latest_end_minute` was raised and the very next `optimize_corridor()` call successfully re-schedules it (new test in `test_slice3_proposal_review.py`, or a new `test_slice4_multiday_postponement.py`).
4. **Postpone-beyond-widened-horizon still fails closed**: re-run the spirit of `test_postpone_beyond_horizon_fails_closed` against the *new* horizon boundary.
5. **Repeated-postponement test**: postpone to date N, then postpone again (after a fresh optimization run/new `expected_proposal_run_id`) to a date earlier than N; assert it succeeds per §8.6's explicit rule.
6. **End-to-end multi-day worker→optimizer→authority cycle**: the exact flow in the product requirement — new job → scoring → optimizer → proposal → authority postpones to day 3 → optimizer re-runs → new proposal on/after day 3 → authority approves — as one integration test, mirroring the shape of `test_full_reject_reoptimize_approve_cycle` but spanning days.
7. **Generated-slots multi-day test** (if §5's repeat-daily fix is taken): assert day-2/day-3 slots exist at the expected offsets for `CORRIDOR_A` under a widened horizon.
8. **Regression**: full existing suite (619 tests) must stay green except the one intentionally-updated test named in §11.

---

## 13. Explicitly out of scope

- **Partial-coverage detection** (§7.3) — no completeness signal exists in the data model; inventing one would fabricate authority the source data does not carry.
- **A live/real-time timetable feed** — `TrainDataProvenance` deliberately has no `LIVE` member; nothing in this design changes that.
- **DST-aware timezone conversion** — the horizon-anchoring model is explicitly fixed-offset arithmetic valid only because Asia/Kolkata has no DST (`horizon_anchor.py`'s own docstring); out of scope unless the domain changes.
- **CP-SAT performance tuning for large multi-day models** — flagged as a risk (§14) but not designed here; needs a load test against realistic multi-day candidate/window counts before the "maximum safe horizon" in §4.3 is finalized.
- **Frontend changes** — not inspected; any UI work to let an authority pick a `selected_date` beyond tomorrow, or to render `coverage_gaps`, is separate follow-on work.
- **Changing `BlockCandidate.latest_end_minute`'s contract-level default** — explicitly rejected in §5, per the contract's own pinned-fixture warning.
- **Multi-corridor / multi-timezone deployments** — everything here assumes the existing single-corridor, single-timezone (`+05:30`) deployment shape.
- **Authentication/authorization changes** — `UnenforcedPolicy` and the actor model are unchanged; this document assumes whatever authorization exists today continues to gate `POSTPONE_PROPOSAL` etc. unchanged.

---

## 14. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Coverage-gate logic itself has a bug that fails **open** (the one failure mode that would be worse than today) | Test it exhaustively at boundaries (§12.1) before wiring it into `possession_inputs()`; keep the gate as a small, isolated, independently-reviewable function rather than inline logic inside `derive_section_possession_windows` |
| Widening the horizon blows up CP-SAT solve time past the 10s budget for real corridor-scale data | Load-test with realistic candidate/window counts before finalizing the "maximum safe horizon" (§4.3); the existing single-worker/fixed-seed reproducibility guarantee (`solver.py:38-48`) is unaffected by horizon length itself, only by problem size |
| `latest_end_minute` widening in `plan_postpone` (§8.2) interacts unexpectedly with `_clamp_window`'s `latest_start = latest_end - duration` for a very short remaining horizon | Covered by §12.3's test; the `max(current, horizon_minutes)` rule never *shrinks* a window, so the only new case is verifying it doesn't over-widen past what the solver can still use sensibly — low risk given `_clamp_window` already clamps to `horizon_minutes` regardless |
| Demo/stakeholder expectation that multi-day "just works" against the existing one-day dataset | Call this out explicitly in the Slice 4 kickoff (§11) — either extend the dataset with additional `service_date`s before the multi-day demo, or demo with an intentionally-partial-coverage corridor to *show* the fail-closed behavior working correctly |
| `coverage_gaps` field added to `JobOptimizationResponse` breaks strict frontend schema validation if the frontend uses a closed (non-additive) JSON schema | Verify `frontend/src/types/contracts.ts`'s validation strictness before shipping (flagged, not resolved, since frontend is out of scope per §13) |
| Two independently-maintained "how wide is the horizon" values (`JobService.horizon_minutes` and a hypothetical future per-corridor override) drift apart | Keep exactly one source (`JobService.horizon_minutes`, threaded everywhere) — do not introduce a second config point; this document's own constants review (§1.3) found the current bugs came from exactly this kind of duplication |

---

## 15. Recommended implementation sequence

1. **Coverage-gate first** (§7.2), landed and tested in isolation against the existing one-day dataset (where it should be a no-op — every day inside the current 1-day horizon is covered). This is the precondition the rest depends on, not a parallel workstream.
2. **`JobService.horizon_minutes` constructor parameter** (§5), defaulted to 1440, threaded to `create_job` and `possession_inputs()`. Still a no-op at the default.
3. **`plan_postpone` horizon-parameter + window-widening fix** (§8.2/§8.3), which fixes the schedulability guarantee. Update the one inverting test (§11) as part of this same change, with the update called out explicitly in the PR description.
4. **Wire the coverage gate into `possession_inputs()`** (§7.2's third bullet) and add `coverage_gaps` to the optimization response (§5/§6).
5. **Widen the deployment default** (or add an explicit opt-in flag/env var) only after 1-4 are landed and tested against a horizon actually wider than the dataset's coverage — i.e., prove the fail-closed coverage gate works *before* relying on it in a live wider-horizon deployment.
6. **Generated-slots daily-repeat fix** (§5/§7.4) — lowest risk, can land any time, does not gate anything else.
7. **Dataset/demo decision** (§14) — extend `pashupatastra_realistic_dataset.json` with additional `service_date`s, or explicitly scope the Slice 4 demo to show one covered day + one intentionally-uncovered day's fail-closed behavior.
8. **Load test** to finalize the maximum safe horizon (§4.3) before it is exposed as a large configurable value in production.

---

## 16. Recommendation

# APPROVE WITH CONDITIONS

**Why not plain APPROVE:** the canonical time model, the solver, and the contract layer are genuinely ready — this is the strongest possible starting position, and it means Slice 4 is primarily a jobs-pipeline and coverage-modeling change, not a solver rewrite. But shipping a horizon widening *without* the coverage gate (§7) first would silently convert the one fail-closed path this audit found needing attention into a fail-open one: `derive_section_possession_windows` would manufacture multi-day maintenance opportunity out of the simple absence of timetable data, with no signal anywhere in the system that this happened. That is a safety regression relative to the system's own stated design principles (this codebase's docstrings repeatedly and explicitly commit to "fail closed, never invent"), so it cannot be approved as a simple config change.

**Why not BLOCK:** every other product requirement item (A, B, D, E, F, G, H) is either already correctly implemented (A, D's underlying mechanisms, F, most of G) or has a small, well-understood, low-risk fix (B's horizon plumbing, E's postpone-window-widening, H's one test update). Nothing found in this audit requires a redesign of the solver, the contracts, or the reoptimization state machine.

**Conditions for approval:**
1. The coverage-gate (§7.2) must land and be tested **before** any change to `OPTIMIZATION_HORIZON_MINUTES`'s default value or before any production deployment sets a wider horizon.
2. `plan_postpone`'s window-widening fix (§8.2) must land in the same change as any horizon widening — shipping the horizon widening without it would silently make far-postponed jobs permanently unschedulable, which is the opposite of the stated product requirement.
3. `contracts.BlockCandidate.latest_end_minute`'s **default** stays `1440`, per its own pinned-fixture documentation — only the jobs pipeline's explicit value changes.
4. The one test whose premise inverts (`test_postpone_beyond_horizon_fails_closed`) must be updated intentionally and called out explicitly, not silently patched.
5. A conscious decision on the dataset/demo question (§14) is made before any multi-day demo is presented, so "multi-day scheduling" isn't demoed against a dataset that can only ever produce coverage gaps.

Follow the sequence in §15; do not widen the production horizon default until steps 1-4 of that sequence are complete and tested.

---

## Status note — Slice 4 Step 7 (2026-09-15): two-day synthetic demo dataset

The findings above describe the baseline at `779f679` and are left as written. Since then, Step 7 resolved the §14 dataset/demo decision:

- `pashupatastra_realistic_dataset.json` now carries **48** synthetic train records across **two** service dates, `2026-09-10` and `2026-09-11` (24 each). Each `2026-09-11` record is a deterministic copy of its `2026-09-10` counterpart with only `service_date` changed; clock strings stay local clock times, and the canonical adapter's horizon anchoring places day-2 traversals exactly +1440 minutes later.
- The dataset remains `"synthetic": true` (`dataset_version` 1.1). Station codes/names are public identifiers; chainages, platforms, timings, delays and maintenance data are illustrative and were **not** verified against an official source. No `REAL_*` provenance is claimed; the weakest-link provenance model is unchanged.
- Coverage-gate behavior over this dataset: 1440 and 2880 pass; 4320 fails closed with exactly `2026-09-12` uncovered. Two-day coverage means timetable records exist for both dates, not that the track is free: possession is still derived from train occupation.
- The legacy day-1 sections (`possession_windows`, `maintenance_jobs`, `conflict_pairs`, `disruption_scenarios`, `validation`) were not extended; the canonical jobs pipeline does not read them.
