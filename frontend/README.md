# Pashupatastra frontend

Next.js 16 (App Router) + React 19 + Tailwind 4 console for the frozen
Pashupatastra backend. **Stitch is the visual source of truth; the backend is
the functional source of truth.** The frontend adapts to the backend and never
invents backend behaviour.

All data is **synthetic and illustrative** (NDLS–AGC demo corridor, synthetic
planning horizon 10–11 Sep 2026). Identity is declared through request headers
and recorded as `DECLARED_UNVERIFIED`; there is no login. The risk scorer is a
deterministic baseline, not a trained model.

## Run

```powershell
# backend + seed (repo root, see docs/demo-runbook.md and scripts/seed_demo.py)
$env:PASHUPAT_CORRIDOR_ID = "CORR-NDLS-AGC"
$env:PASHUPAT_JOBS_DB     = "D:\Pashupatastra\demo\jobs.db"   # or any dedicated demo DB
python scripts/seed_demo.py
python -m uvicorn backend.app.api.main:app --host 127.0.0.1 --port 8000

# frontend
cd frontend
$env:NEXT_PUBLIC_API_URL = "http://127.0.0.1:8000"   # inlined at build time
npm run build ; npm run start                         # http://localhost:3000
```

`npm run lint` and `npx tsc --noEmit` must stay clean.

## Screens

| Route | Screen | Backend it uses |
|---|---|---|
| `/` | Command Center | `GET /v1/jobs`, `/v1/obligations`, `/v1/optimization-runs/{id}` |
| `/inspection` | Field Inspection | `POST /v1/jobs` (idempotency key, `field_location` cross-check) |
| `/jobs`, `/jobs/[jobId]` | Maintenance Jobs, job detail (overview, scoring, proposal, execution, audit, obligation) | `GET /v1/jobs*`, `/history`, `/proposal`, `/execution`, `/obligation` |
| `/planning` | Planning (CP-SAT) | `POST /v1/corridors/CORR-NDLS-AGC/optimize-jobs`, `GET /v1/optimization-runs/{id}` |
| `/review` | Authority Review | `POST /v1/jobs/{id}/proposal/{approve,postpone,reject,release}` |
| `/execution` | Execution | `POST /v1/jobs/{id}/execution/{start,complete,not-completed}` |
| `/audit` | Audit Trail | `GET /v1/jobs/{id}/history` |
| `/obligations` | Obligations (SLA) | `GET /v1/obligations`, `/v1/jobs/{id}/obligation` |
| `/simulation` | Disruption Simulation | legacy stateless `POST /optimize`, `POST /recover` (Corridor A fixture) |
| `/about` | About This Demo | none |

## Backend capability → screen → control

| Backend capability | Screen | Control |
|---|---|---|
| Health | shell | connectivity pill (15 s poll); offline disables every mutation |
| Field intake / job creation | Field Inspection | Submit report |
| Idempotent replay | Field Inspection | key kept across retries; "Submit as new report" on conflict |
| Job list / retrieval | Jobs, Command Center, Job detail | queue, filters, row click |
| Deterministic scoring | Job detail → Scoring, Jobs, Field Inspection result | scores, ranked contributors, raw inputs |
| Optimization | Planning | RUN OPTIMIZATION (confirm dialog) |
| Proposal + explanation | Authority Review, Job detail → Proposal | proposal card (codes verbatim) |
| Approve / postpone / reject / release | Authority Review, Job detail | decision bar + dialogs (proposal re-fetched, stale-run protected) |
| Re-optimization, committed preservation | Planning | re-optimization comparison (session diff + job history) |
| Execution start / complete / not-completed | Execution, Job detail | dialogs with evidence-reference editor |
| Evidence | Execution | reference, kind, capture time, optional coordinates (no upload) |
| History / audit | Audit Trail, Job detail → Audit | grouped timeline, per-event details, raw JSON |
| Obligations / SLA | Obligations, Command Center, Job detail | table, filters, assumed-SLA disclosure |
| Optimization run record | Planning | Run audit record |
| Legacy disruption simulation | Simulation | four disruption types + baseline/recovered comparison |

Backend capabilities with no user-facing purpose are deliberately not exposed
(`POST /v1/jobs/{id}/notify` duplicates approve; there is no notification API).

## Stitch elements removed or reworded (unsupported by the backend)

Fake live train counts and on-time percentages, GPS/elevation/gradient
telemetry, "4-track trunk", cryptographic digest / Merkle / tamper-proof audit
claims, ML/AI verification claims, geodetic anchors, radar telemetry nodes,
"SEC-LEVEL", "commit recovered plan to operations", profile avatar (implied
login), the CRIS/IR badge in the logo (emblem only is kept). The orbital rings,
glow and laser pulses are retained as decoration and are labelled as such.

## Notes

- `src/lib/api.ts` mirrors `docs/openapi-v1.json`; `block_candidate` and event
  `metadata` are outside the frozen contract and are always parsed defensively.
- `src/lib/fieldLocation.ts` mirrors `backend/app/jobs/field_location.py`
  (the backend exposes no topology endpoint). It was checked against the
  backend converter on 735 station/offset cases with zero mismatches.
- Three clocks are never mixed: PLAN (horizon minutes), OBSERVED (crew-entered)
  and RECORDED (server wall clock), each tagged in the UI.
