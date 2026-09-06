# Pashupatastra

AI-powered automatic block planning to maximize asset availability for train
operations on Indian Railways — built for SIH 2026, Problem Statement
**SIH26027**.

## Core idea

```
DATA -> AI/ML priority & risk scoring -> deterministic CP-SAT optimization
     -> optimized maintenance block plan -> command-center visualization
     -> DISRUPT -> automatic RE-OPTIMIZE -> RECOVER (+ explainability/audit)
```

Demo narrative: **PLAN -> DISRUPT -> RECOVER**.

The AI/ML layer never generates a schedule directly — it only estimates
maintenance priority and risk per candidate block. The OR-Tools CP-SAT
optimizer is the sole authority that turns those scores, plus hard
safety/operational constraints, into a feasible schedule. Safety
constraints are never overridden by AI output.

Data is synthetic-by-design for the prototype; public/government railway
data is used only where genuinely useful and appropriate, and no real
Indian Railways performance claims are fabricated.

## Current status: full plan → disrupt → recover loop, live

The complete pipeline described above is implemented and wired
end-to-end, backend and frontend both live:

- **Domain data & synthetic fixtures** — a canonical railway corridor
  model (tracks, assets, possession windows, maintenance work) with
  deterministic generated scenarios (`backend/app/data/`).
- **ML priority/risk scoring** — a documented, deterministic 7-feature
  weighted scorer, `score_block()` (`backend/app/ml/`).
- **CP-SAT optimization** — Google OR-Tools solver enforcing hard
  constraints (track no-overlap + safety headway, possession-window
  containment, dependency ordering, mutual-exclusion for shared
  machines/crews, committed-block pinning) (`backend/app/optimizer/`).
- **Disruption & recovery simulation** — applies one of four supported
  disruption types (Asset Breakdown, Track Unavailable, Emergency Work,
  Possession Curtailment) to a plan and re-solves it
  (`backend/app/simulation/`).
- **FastAPI backend** — `GET /health`, `POST /optimize`, and
  `POST /recover` (`backend/app/api/`).
- **Command-center frontend** — live schedule timeline, KPI summary,
  decision-explainability audit trail, and an operational
  disruption/recovery control with a before/after comparison
  (`frontend/`).

84 backend tests cover the scorer, solver, simulation, and both API
endpoints — including regression tests that every checked-in fixture
score matches what the live scorer actually computes, so nothing on
screen is hand-typed.

## Quick start

See `docs/demo-runbook.md` for the exact presentation-day sequence.
Short version:

**1. Backend** (repo root):

```bash
pip install -r requirements.txt
python -m uvicorn backend.app.api.main:app --reload
```

Verify: `curl http://127.0.0.1:8000/health` → `{"status":"healthy"}`.
`POST` an `OptimizationRequest` (see
`backend/app/data/fixtures/corridor_a_blocks.json` for an example) to
`http://127.0.0.1:8000/optimize`, or a `RecoveryRequest`
(`{"request": ..., "disruption": ...}`) to
`http://127.0.0.1:8000/recover`. Interactive docs at
`http://127.0.0.1:8000/docs`.

**2. Frontend** (`frontend/`):

```bash
npm install
npm run dev
```

Open `http://localhost:3000`. The dashboard talks to the backend
above by default (`NEXT_PUBLIC_API_URL`, see `frontend/README.md`);
if the backend isn't reachable it falls back to canonical fixture
data and clearly labels itself as such — it never silently pretends
to be live.

**3. Tests:**

```bash
python -m pytest -q                 # full backend suite
cd frontend && npm run lint && npm run build
```

**Presentation script:** `docs/demo-script.md`.

Adding a new endpoint? See `docs/integration.md` for the router
pattern.

## Repository layout

```
contracts/        Shared data contracts (BlockCandidate, OptimizationRequest,
                   OptimizationResult, DisruptionEvent) — the single surface
                   every subsystem depends on. See docs/contracts.md.
backend/app/
  domain/          Canonical railway/maintenance model         (Darshini)
  data/            Synthetic data generators + fixtures         (Darshini)
  ml/              Priority/risk scoring                        (Ayush)
  optimizer/       CP-SAT scheduling engine                     (Tyagi)
  simulation/      Disruption generation + re-optimize trigger   (Tirth)
  api/             FastAPI routers wiring it together            (Aryan)
frontend/          Command-center UI (timeline, KPIs, disruption,
                   explainability panels)                       (Archit)
scripts/           Standalone runnable entry points, e.g. run_milestone1.py
docs/              Architecture, contracts, and demo-script notes
```

See `docs/architecture.md` for the fuller picture and
`docs/contracts.md` for the contract rationale and open questions.

## Team

| Area | Owner |
|---|---|
| Product, architecture, integration | Aryan |
| Optimization engine (CP-SAT) | Aryan Tyagi |
| Frontend / UI / visualization | Archit Singh |
| AI/ML intelligence | Ayush Mehta |
| Railway domain / data / synthetic data | Darshini |
| Simulation / testing / disruption engine | Tirth |
