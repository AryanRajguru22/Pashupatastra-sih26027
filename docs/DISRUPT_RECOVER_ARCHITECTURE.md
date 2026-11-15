# DISRUPT → RECOVER: persisted disruption handling on the jobs pipeline

Status: implemented and tested (`backend/tests/test_disruption_recovery.py`, 23 tests;
`seed_demo.py --verify` rehearses it in synthetic and real-data modes).

## What it is, and what it is not

A **disruption** is a *declared, simulated* planning input: "section `FDB-PWL` on
`DOWN-1` cannot be used from 02:00 to 04:00". It changes what the optimizer may do and
tells the authority which approved work it invalidates.

It is **not** an observed railway event. There is no live feed of closures, no
possession-grant system, no signalling input. Every record carries
`basis = SIMULATED_DECLARED` and the declaring actor, and the console says so.

Before this work, disruption existed only as the stateless `/recover` route over a
separate synthetic fixture (still available as the "Synthetic Sandbox"). This is the
persisted workflow over the real job queue.

## Model

| Item | Definition |
|---|---|
| Kinds | `TRACK_UNAVAILABLE` (a track; optionally one section) and `POSSESSION_CURTAILMENT` (one section, required) |
| Span | `starts_at` / `ends_at`, ISO-8601 with explicit offset. Converted to minutes from the horizon anchor, **start rounded down and end rounded up**, so conversion can only widen a disruption |
| Storage | `disruption_events`, append-only (SQL `BEFORE UPDATE/DELETE` triggers, the same guard as `job_events`), created with `CREATE TABLE IF NOT EXISTS` so an older `jobs.db` still opens |
| State | Folded from events: `REGISTERED`, optionally `CLEARED`, any number of `RECOVERY_PLANNED` / `RECOVERY_REFUSED`. Nothing is updated or deleted |

## How a disruption affects planning

One rule, applied in one place (`subtract_disruptions`, called from
`JobOptimizationService`): after the timetable adapter has derived the possession windows
and before the solver runs, each active disruption's span is **subtracted** from the
windows of the (track, section) it names. A cut window is replaced by its remaining
fragments (`<window_id>~d<n>`). The result is always a subset of the input possession, so a
disruption can never make a plan *less* safe than the undisrupted one (property-tested).
Derivation still lives only in `data/timetable_adapter.py`.

Every run records `active_disruption_ids` and `disruption_basis` in its provenance
snapshot, and `possession_derivation` names the subtraction.

### Fail-closed rules

1. **All possession removed.** If subtraction would leave no window at all, the run is
   refused (`ALL_POSSESSION_REMOVED`). An empty list would otherwise mean "possession is
   not modelled" and switch the solver's possession rule *off*.
2. **Approved work is never silently moved or kept.** An approved (`notified`) or started
   (`in_progress`) block that overlaps an active disruption is pinned by the optimizer, but
   its possession no longer exists, so the model would be infeasible and take every
   unrelated proposal with it. Instead the optimization is **refused before the solver
   runs** (`RECOVERY_BLOCKED`, 409). No run row is written and no proposal is written or
   withdrawn. The detail names each job and the action that resolves it: the authority
   releases a `notified` block; the crew reports an `in_progress` block not-completed. The
   refusal is recorded on each blocking job and on the disruption.
3. **A disrupted proposal cannot be approved.** `notify`/`approve` on a `scheduled` job
   whose placement overlaps an active disruption returns `PROPOSAL_DISRUPTED`; the refusal
   is recorded in the job's history. The check and the commit run under the same lifecycle
   lock as disruption registration, so a concurrent registration is either seen or waits.
4. **Clearing** a disruption ends its effect on later runs. It does not restore withdrawn
   proposals; a normal optimization re-plans.

## Recovery

`POST /v1/corridors/{id}/disruptions/{disruption_id}/recover` is a real CP-SAT run through
`JobOptimizationService.optimize_corridor(trigger="recovery")`: same model, same hard
constraints, same single-worker seeded solver, with approved blocks still pinned.
Under one lifecycle-lock hold it

1. snapshots the plan the recovery starts from (current placements, plus the previous
   placement of any block released/not-completed because of this disruption),
2. runs the optimization,
3. snapshots the result and computes the comparison (`unchanged`, `moved`,
   `newly_unscheduled` with the solver's reason, `newly_scheduled`),
4. appends `RECOVERY_PLANNED` (run id, solver status, counts) to the disruption.

New proposals still require authority approval; recovery commits nothing.

## Authorization

| Action | Roles | Scope |
|---|---|---|
| `REGISTER_DISRUPTION`, `CLEAR_DISRUPTION` | ENGINEER | corridor-complete |
| `READ_DISRUPTION` | ENGINEER, AUTHORITY | corridor-complete |
| recovery | ENGINEER, as `REQUEST_OPTIMIZATION` | corridor-complete |

These pass through the same `authorize` + `authorize_corridor` seam as every other route,
so the enforced (NovaForge) mode covers them. In demo mode the policy is unenforced, as for
every route (README, trust model); the enforcing behaviour is proven at service level
(`test_enforcing_policy_gates_every_disruption_call`).

## Honest limits

- Disruptions are declared, not detected. Kinds are limited to track/section
  unavailability and curtailment; asset breakdown and emergency work remain in the
  synthetic sandbox only.
- The span must lie in the fixed two-day horizon (10–11 Sep 2026).
- The solver's objective rewards *which* jobs are scheduled, not *when*; recovery therefore
  preserves a valid, maximum-value set, not a minimum-change plan. Unaffected uncommitted
  proposals may move; committed blocks outside the disruption never do.
- Multi-process deployments are not serialized beyond SQLite's write lock (the same limit
  as the rest of the lifecycle).
