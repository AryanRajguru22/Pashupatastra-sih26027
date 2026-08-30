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
|       v (HTTP POST /optimize OR Stage-1 JSON Fixtures)                            |
|  +-----------------------------------------------------------------------------+  |
|  | Frontend Data Layer (`src/lib/data.ts`)                                     |  |
|  | - Mirrors backend Pydantic contracts (`src/types/contracts.ts`)             |  |
|  | - Enriches result with candidate metadata & solver explainability           |  |
|  +-----------------------------------------------------------------------------+  |
|       |                                                                           |
|       +-------------------+--------------------+--------------------+             |
|       |                   |                    |                    |             |
|       v                   v                    v                    v             |
|  [ KPI Bar ]      [ Multi-Track ]       [ Decision Audit ]   [ Disruption Panel ] |
|  (`KpiSummaryBar`)   (`Timeline`)       (`Explainability`)   (Milestone 3)        |
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

4. **`src/types/contracts.ts`**
   - Strict TypeScript mirror of the Python Pydantic models in `contracts/`:
     - `BlockCandidate`
     - `ScheduledBlock`
     - `UnscheduledBlock`
     - `OptimizationRequest`
     - `OptimizationResult`
     - `DisruptionEvent`

5. **`src/lib/data.ts`**
   - Seamless data access layer supporting both Stage 1–3 static fixtures (`corridor_a_blocks.json`, `milestone1_result.json`) and live FastAPI endpoint queries (`POST /optimize`).

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
- [ ] **Milestone 2 (Next)**: Live backend integration (`POST /optimize` wire-up with live FastAPI backend) & dynamic re-optimization dispatch.
- [ ] **Milestone 3**: Disruption injection simulator UI & real-time schedule recovery view.
