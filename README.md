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

## Status: Milestone 1 — CP-SAT feasibility proof

A standalone script solves a small synthetic single-corridor problem
(12 maintenance blocks across two tracks, with dependencies, mutual
exclusions, and time windows) using CP-SAT — no API or frontend yet.

```bash
pip install -r requirements.txt
python scripts/run_milestone1.py
```

This prints the solved schedule, asserts no hard constraint (e.g. two
blocks overlapping on the same track) is violated, and writes the full
`OptimizationResult` to `scripts/milestone1_result.json`.

Run the optimizer correctness tests:

```bash
python -m pytest backend/tests/ -v
```

## Phase 1: backend API

A thin FastAPI layer wraps the optimizer as-is: `GET /health` and
`POST /optimize` (accepts an `OptimizationRequest`, returns an
`OptimizationResult` — no new schemas, no logic duplicated from
`backend/app/optimizer/solver.py`).

Start the dev server from the repo root:

```bash
python -m uvicorn backend.app.api.main:app --reload
```

Then `GET http://127.0.0.1:8000/health`, or `POST` an
`OptimizationRequest` JSON body (see
`backend/app/data/fixtures/corridor_a_blocks.json` for an example) to
`http://127.0.0.1:8000/optimize`. Interactive docs are at
`http://127.0.0.1:8000/docs`.

Run the API tests:

```bash
python -m pytest backend/tests/test_api.py -v
```

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
