# Pashupatastra Frontend — Block Planning Command Center

**AI-powered automatic block planning and recovery system for Indian Railways (SIH 2026, Problem Statement SIH26027).**

This frontend is a Next.js (App Router) + React + TypeScript + Tailwind CSS command-center dashboard designed for railway section controllers and traffic planners.

---

## 🏗️ Architecture & Data Flow

```
+-----------------------------------------------------------------------------------+
|                                 DATA FLOW ARCHITECTURE                            |
+-----------------------------------------------------------------------------------+
|                                                                                   |
|  [ Track Infrastructure & Block Candidates ]  -->  [ Python Backend (FastAPI) ]   |
|                                                                 |                 |
|                                                                 v                 |
|                                                     [ OptimizationRequest ]       |
|                                                                 |                 |
|                                                                 v                 |
|                                                  [ OR-Tools CP-SAT Solver ]       |
|                                                  [ + Candidate Objective Weights ]|
|                                                                 |                 |
|                                                                 v                 |
|                                                     [ OptimizationResult ]        |
|                                                                 |                 |
|       +---------------------------------------------------------+                 |
|       |                                                                           |
|       v (HTTP POST /optimize, POST /recover, OR Canonical JSON Fixtures)          |
|  +-----------------------------------------------------------------------------+  |
|  | Frontend Data Layer (`src/lib/data.ts`, `src/lib/recoveryComparison.ts`)    |  |
|  | - Mirrors backend dataclass contracts (`src/types/contracts.ts`)            |  |
|  | - Enriches result with candidate metadata & solver explainability           |  |
|  | - Classifies recovery changes: PRESERVED / MOVED / NEW / DROPPED            |  |
|  +-----------------------------------------------------------------------------+  |
|       |                                                                           |
|       v                                                                           |
|  [ DashboardClient ] -- orchestrates stage (PLAN / DISRUPT / RECOVER) --          |
|       |                                                                           |
|       +-------------------+--------------------+                                 |
|       |                   |                    |                                 |
|       v                   v                    v                                 |
|  [ AppHeader ]      [ PlanView ]       [ DisruptRecoverView ]                    |
|  (nav + stage)   (corridor stage,    (disruption trigger +                       |
|                   block inspector,    before/after recovery                      |
|                   ML scoring)         comparison)                                |
+-----------------------------------------------------------------------------------+
```

---

## 📦 Key Components

The presentation layer is built to the Google Stitch "Aether Void" design
export (`stitch/screen-{1,2,3}/`), ported literally (Tailwind token system,
layout structure, typography) rather than reinterpreted — every visual
element that had no real backing (SCADA telemetry, live train tracking,
signaling/interlocking control, fake solver alternatives) was replaced with
real backend data in the same UI slot, or removed if there was no real
equivalent.

1. **`src/components/AppHeader.tsx`**
   - Shared top navigation: wordmark, build/version badge, the
     Maintenance Planning / Disruption Simulation nav pills (the only two
     real stages), and the live-backend/solver-time status pills.

2. **`src/components/PlanView.tsx`**
   - The PLAN stage: SVG dual-track corridor stage (real bezier lane
     curves, ported from the Stitch export, shared with
     `DisruptRecoverView` via `src/lib/corridorGeometry.ts`).
   - Real work-type filter row, KM ruler derived from real candidate
     asset metadata, and a right-hand inspector drawer showing a
     selected block's real headway constraint, constraint-conflict
     count, priority score, CP-SAT reasoning text, and all 7 real ML
     scoring features.
   - Bottom schedule-overview strip with a real selected-block time
     marker and corridor capacity/risk-neutralized stats.

3. **`src/components/DisruptRecoverView.tsx`**
   - The DISRUPT and RECOVER stages combined (mirroring the Stitch
     export's own dual-state screen): the four real disruption types
     (Asset Breakdown, Track Unavailable, Emergency Work, Possession
     Curtailment), each with only the inputs that actually affect the
     disruption logic.
   - Calls the live `POST /recover` endpoint. The hero corridor stage
     renders the actual current schedule at all times (baseline
     pre-trigger, recovered post-trigger) with real dropped/moved block
     markers and reroute paths layered on top — never an empty stage.
   - Renders the resulting BEFORE → AFTER comparison (status,
     scheduled/unscheduled counts, preserved count, and a list of
     moved/dropped/newly-scheduled blocks) with a "Return to Original
     Plan" action.

4. **`src/types/contracts.ts`**
   - TypeScript mirror of the Python dataclass contracts in `contracts/`:
     - `BlockCandidate`
     - `ScheduledBlock`
     - `OptimizationRequest`
     - `OptimizationResult`
     - `DisruptionEvent`
     - `RecoveryRequest` / `RecoveryResponse`

5. **`src/lib/data.ts`** / **`src/lib/recoveryComparison.ts`**
   - Data access layer supporting both canonical static fixtures
     (`corridor_a_blocks.json`, `milestone1_result.json`) and live
     FastAPI endpoint queries (`POST /optimize`, `POST /recover`).
   - `recoveryComparison.ts` computes the client-side before/after
     block classification from the two real results returned by
     `/recover` — never fabricated, and reusing real solver rejection
     reasons where a block is dropped by the solver rather than by the
     disruption itself.

6. **`src/lib/corridorGeometry.ts`** / **`src/lib/format.ts`** / **`src/lib/stage.ts`**
   - `corridorGeometry.ts`: the shared bezier lane-curve geometry (ported
     from the Stitch export) that `PlanView` and `DisruptRecoverView`
     both draw the corridor SVG from, so the same track renders
     identically in both stages.
   - `format.ts`: shared `formatHHMM` / `formatWorkType` display helpers.
   - `stage.ts`: the `OperationalStage` ("PLAN" | "DISRUPT" | "RECOVER")
     type shared by `DashboardClient` and `AppHeader`.

---

## 🚀 Getting Started

### Prerequisites
- Node.js 18+ (Node 20+ recommended)
- npm or yarn or pnpm

### Installation

```bash
# Navigate to frontend directory
cd frontend

# Install dependencies
npm install
```

### Development Server

```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

### Verification & Production Build

```bash
# Run linting
npm run lint

# Build production bundle
npm run build

# Start production server
npm run start
```

---

## ⚙️ Environment Configuration

| Variable | Default | Description |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://127.0.0.1:8000` | Base URL for the FastAPI backend optimizer service |

---

## 📋 Milestone Progression

- [x] **Stage 1**: TypeScript data contract layer & corridor fixtures.
- [x] **Stage 2**: Command-center UI, SVG Multi-track timeline, KPI metrics, and solver explainability audit panel.
- [x] **Milestone 2**: Live backend integration (`POST /optimize` wire-up with live FastAPI backend) & re-optimization dispatch.
- [x] **Milestone 3**: Disruption injection UI (`DisruptionControls`) & live `POST /recover` schedule recovery view, with before/after comparison.
