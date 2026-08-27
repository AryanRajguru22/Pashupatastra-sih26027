# Architecture

## Pipeline

```
DATA (domain model + synthetic corridor/asset/timetable data)
  -> AI/ML priority & risk scoring        (backend/app/ml)
  -> CP-SAT deterministic optimization    (backend/app/optimizer)
  -> optimized maintenance block plan
  -> command-center visualization         (frontend)
  -> DISRUPT                              (backend/app/simulation)
  -> automatic RE-OPTIMIZE                (same optimizer, new request)
  -> RECOVER
  -> explainability + audit               (OptimizationResult.explainability)
```

## Non-negotiable boundary

AI/ML output (`priority_score`, `risk_score` on `BlockCandidate`) is an
**objective-function input only**. It can change which feasible schedule
the optimizer prefers; it can never let the optimizer violate a hard
constraint (track no-overlap, headway, dependency ordering, mutual
exclusion, time windows). If this boundary blurs anywhere in the code,
that's a bug, not a design choice.

## Why a single Python backend for optimizer + ML + domain + simulation

CP-SAT's most mature bindings are Python; the ML scorer is a simple
tabular model that's naturally Python too. Keeping optimizer, ML, domain
model, and simulation in one language avoids cross-process serialization
and lets each owner iterate against the same in-process contracts
without standing up extra infrastructure.

## Why no database yet

The demo runs against synthetic fixtures and in-memory state. SQLite is
the fallback if persisting scenarios across restarts becomes necessary;
Postgres/Docker/auth are explicitly out of scope for the prototype
timeline.

## Milestones

1. **CP-SAT feasibility proof** (this state) — standalone script, no API,
   no frontend. Proves the highest-risk assumption: CP-SAT can solve this
   problem shape under real constraints.
2. **Minimal API + real frontend against fixtures** — FastAPI `/optimize`
   endpoint; frontend timeline/KPI panel built against a static
   `OptimizationResult.json` before the two are wired together.
3. **Full PLAN -> DISRUPT -> RECOVER loop** — disruption injection calls
   re-optimization, frontend updates live, explainability/audit surfaced
   in the UI.

See the repository README for how to run Milestone 1 today.
