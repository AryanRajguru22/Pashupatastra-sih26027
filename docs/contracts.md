# Shared data contracts (draft v0.1)

Defined as Pydantic models in `contracts/`. This is the one directory
every subsystem depends on — treat changes to it like an API version
bump: sync with the team before editing, don't silently rename or
remove fields others may already be building against.

## BlockCandidate (`contracts/block_candidate.py`)

One maintenance work request needing a track possession window.
`priority_score` and `risk_score` are written by the ML subsystem and
are objective-function inputs only — see `docs/architecture.md` for why
this boundary matters.

## OptimizationRequest (`contracts/optimization_request.py`)

The input the CP-SAT engine solves: a planning horizon, a set of
`BlockCandidate`s, the train timetable to protect, any already-committed
blocks (used to keep re-optimization after a disruption stable rather
than reshuffling everything), and objective weights / constraint config.

## OptimizationResult (`contracts/optimization_result.py`)

The optimizer's output: which blocks got scheduled and when, which were
excluded and why, corridor-level KPIs, and an `explainability` list —
the audit trail of binding constraints behind each scheduling decision.

## DisruptionEvent (`contracts/disruption_event.py`)

Something that invalidates part of a committed plan (asset failure,
weather, an emergency block request, a delay, a block overrun) and
usually triggers a re-optimization, scoped to the full horizon, a
rolling window, or just the affected segment.

## Open questions for domain review (Darshini)

These are flagged in the code as draft v0.1 and expected to change:

- Are the `work_type` / `event_type` enum values complete for the
  problem statement, or missing categories used in practice?
- Does a single-line vs multi-line section need a richer `track_id`
  model than a flat string?
- Is a `ResourceRequirement` (crew/equipment) needed on `BlockCandidate`
  for v1, or can mutual exclusion by block ID stand in for now?
- Is `train_timetable` on `OptimizationRequest` enough to represent
  train protection, or does it need explicit headway/blocking rules per
  train service?

Resolve these before wave 2 (API + real ML/domain implementations)
starts building heavily on top of the current shapes.
