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
|       +-------------------+--------------------+--------------------+             |
|       |                   |                    |                    |             |
|       v                   v                    v                    v             |
|  [ KPI Bar ]      [ Multi-Track ]       [ Decision Audit ]   [ Disruption Panel ] |
|  (`KpiSummaryBar`)   (`Timeline`)       (`Explainability`)  (`DisruptionControls`)|
+-----------------------------------------------------------------------------------+
```

---

## 📦 Key Components

1. **`src/components/Timeline.tsx`**
   - Interactive SVG-rendered multi-track possession timeline.
   - Color-coded by work type (`RENEWAL`, `INSPECTION`, `REPAIR`, `PREVENTIVE`).
   - Visualizes scheduled block slots, track assignments, and rejected/deferred blocks.
   - Interactive hover tooltips and selection linking with the audit panel.

2. **`src/components/KpiSummaryBar.tsx`**
   - High-level operational metrics:
     - **Asset Availability %**
     - **Scheduled Blocks Count & Scheduling Rate**
     - **Rejected / Deferred Candidate Count**
     - **Operational Impact & Dynamic Track Overlap Feasibility Verification**
     - **Estimated Risk Addressed Score (Baseline Objective Weights)**

3. **`src/components/ExplainabilityPanel.tsx`**
   - Deep inspection for selected block candidates.
   - Displays CP-SAT solver verdicts, active binding constraints, pre-solve priority/risk attributes, possession windows, and precedence dependencies.

4. **`src/components/DisruptionControls.tsx`**
   - Compact operational-disruption trigger: the four backend-supported
     disruption types (Asset Breakdown, Track Unavailable, Emergency
     Work, Possession Curtailment), each with only the inputs that
     actually affect the disruption logic.
   - Calls the live `POST /recover` endpoint and renders the resulting
     BEFORE → AFTER comparison (status, scheduled/unscheduled counts,
     preserved count, and a list of moved/dropped/newly-scheduled
     blocks) with a "Return to Original Plan" action.

5. **`src/types/contracts.ts`**
   - TypeScript mirror of the Python dataclass contracts in `contracts/`:
     - `BlockCandidate`
     - `ScheduledBlock`
     - `OptimizationRequest`
     - `OptimizationResult`
     - `DisruptionEvent`
     - `RecoveryRequest` / `RecoveryResponse`

6. **`src/lib/data.ts`** / **`src/lib/recoveryComparison.ts`**
   - Data access layer supporting both canonical static fixtures
     (`corridor_a_blocks.json`, `milestone1_result.json`) and live
     FastAPI endpoint queries (`POST /optimize`, `POST /recover`).
   - `recoveryComparison.ts` computes the client-side before/after
     block classification from the two real results returned by
     `/recover` — never fabricated, and reusing real solver rejection
     reasons where a block is dropped by the solver rather than by the
     disruption itself.

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
