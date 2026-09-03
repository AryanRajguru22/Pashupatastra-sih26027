# PASHUPATASTRA — Railway Domain & Data Specification
**Role:** Darshini (Domain & Data Foundation Lead)  
**Target Audience:** Ayush (ML/Scoring), Tyagi (CP-SAT Optimizer), Mehta (API/Backend), Archit (Frontend Dashboard), Tirth (Simulation & Recovery), Aryan (Integration Lead)

---

## Executive Summary
This document provides the canonical domain definitions, mathematical data mappings, disruption event specifications, and master benchmark scenarios for **Pashupatastra** — an intelligent, disruption-resilient railway maintenance scheduling engine for Indian Railways (IR).

---

# SECTION 1: Railway Domain Definitions & Entity Architecture

### 1.1 Core Entities & Concepts

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             CORRIDOR (e.g. CORR-NDLS-AGC)                   │
│  ├── Zone: Northern Railway / NCR                                           │
│  ├── Division: Delhi / Agra                                                 │
│  └── Length: 195.0 km (Broad Gauge Trunk Route)                             │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ Contains multiple physical tracks
        ┌──────────────────────────────┴──────────────────────────────┐
        ▼                                                             ▼
┌──────────────────────────────────────┐  ┌──────────────────────────────────────┐
│       TRACK SEGMENT: UP-1 (North)    │  │      TRACK SEGMENT: DOWN-1 (South)   │
│  ├── Section: NDLS-Mathura Mainline  │  │  ├── Section: Mathura-NDLS Mainline  │
│  ├── Speed Limit: 160 km/h           │  │  ├── Speed Limit: 160 km/h           │
│  ├── Electrified: 25 kV AC OHE       │  │  ├── Electrified: 25 kV AC OHE       │
│  └── Train Density: 110 trains/day   │  │  └── Train Density: 115 trains/day   │
└──────────────────┬───────────────────┘  └──────────────────┬───────────────────┘
                   │ Hosts hundreds of assets                │ Hosts hundreds of assets
    ┌──────────────┴──────────────┐           ┌──────────────┴──────────────┐
    ▼                             ▼           ▼                             ▼
┌────────────────────────┐ ┌─────────────┐ ┌────────────────────────┐ ┌─────────────┐
│ Asset: Rail Section    │ │ Asset: OHE  │ │ Asset: Rail Section    │ │ Asset: Point │
│ AST-UP1-RAI-102 (KM 14)│ │ AST-UP1-OHE │ │ AST-DN1-RAI-045 (KM 08)│ │ AST-DN1-TUR │
└────────────────────────┘ └─────────────┘ └────────────────────────┘ └─────────────┘
```

#### 1. Corridor (`Corridor`)
- **Definition:** A contiguous geographic and operational railway trunk line spanning multiple stations, block sections, and administrative divisions (e.g. *New Delhi – Agra Cantt Section*, 195 km, NCR/NR Zone).
- **Attributes:** `corridor_id`, `name`, `zone`, `division`, `tracks: List[TrackSegment]`, `assets: List[Asset]`.

#### 2. Track (`TrackSegment`)
- **Definition:** A dedicated, physical unidirectional or bi-directional railway line on a corridor along which trains travel and on which engineering possessions are granted.
- **Directional Standard:**
  - **`UP` Tracks (e.g., `UP-1`, `UP-2`):** Trains travelling towards the zonal/divisional headquarters or capital (e.g., Agra towards New Delhi).
  - **`DOWN` Tracks (e.g., `DOWN-1`, `DOWN-2`):** Trains travelling away from headquarters (e.g., New Delhi towards Agra).
- **Attributes:** `track_id`, `corridor_id`, `section_name`, `direction`, `km_start`, `km_end`, `speed_limit_kmh`, `electrified`, `daily_train_density`.

#### 3. Asset (`Asset`)
- **Definition:** A physical piece of railway infrastructure installed at a specific kilometric location along or above a track.
- **Asset Categories:**
  - **`RAIL_SECTION`:** 52kg/60kg 90-UTS continuously welded rails (CWR), sleepers, and ballast bed.
  - **`TURNOUT_POINT`:** Points, switches, crossings, and tongue rails connecting diverging tracks at interlockings.
  - **`OHE_MAST`:** Overhead Equipment 25 kV AC masts, cantilevers, contact wires, catenary wires, and droppers.
  - **`SIGNAL_POST`:** Multi-aspect colour light signalling (MACLS) posts, LED aspect units, and automatic signalling boundaries.
  - **`TRACK_CIRCUIT`:** Audio frequency track circuits (AFTC) or DC track circuits detecting train occupancy.
- **Attributes:** `asset_id`, `name`, `asset_type`, `track_id`, `km_location`, `criticality` ($0.0 - 1.0$), `condition_score` ($0.0 - 1.0$), `last_maintained_days_ago`, `defect_severity`.

#### 4. Maintenance Block (`BlockCandidate`)
- **Definition:** A formal request for an exclusive time interval on a designated track during which revenue train operations are suspended to permit safe execution of civil, electrical, or signalling maintenance.
- **Attributes:** `block_id`, `asset_id`, `track_id`, `work_type`, `duration_minutes`, `earliest_start_minute`, `latest_end_minute`, `priority_score`, `risk_score`, `dependencies`, `mutual_exclusion_group`, `is_committed`.

#### 5. Committed Block (`is_committed: true`)
- **Definition:** A maintenance block that has already been formally sanctioned by the Chief Controller, where engineering work trains (BCM/CSM/Tower Wagons), material, and gangs are already stationed on site or actively executing work.
- **Operational Invariant:** **Committed blocks are immutable.** During disruption re-optimization, the CP-SAT solver must freeze their assigned `track_id`, `start_minute`, and `end_minute`. They can never be shifted, rescheduled, or deferred unless the disruption explicitly cancels them.

#### 6. Disruption (`DisruptionEvent`)
- **Definition:** An unplanned real-time event (e.g., rail fracture, overhead wire parting, machine failure, traffic congestion curtailing a block window) that violates the active maintenance schedule and forces a dynamic re-optimization.

---

### 1.2 The Asset vs. Track Relationship: Multi-Asset Track Topology

> [!IMPORTANT]
> **Can a single track host multiple assets? YES.**
> A single track segment (e.g., `UP-1` between KM 0.0 and KM 50.0) is a physical corridor line containing:
> - **50+ Rail Sections** (one per km block / continuous welded rail stretch).
> - **12+ Turnout Points & Crossings** (at station yard throats and crossovers).
> - **700+ OHE Masts & Cantilevers** (spaced every 60–70 meters).
> - **25+ Signal Posts & Track Circuits** (spaced at 1–2 km automatic block intervals).

#### Spatial & Temporal Rules for Maintenance:
1. **Track-Level Possession:** Every maintenance block targets an `asset_id`, but demands exclusive occupancy of its hosting `track_id`.
2. **No Unsafe Same-Track Overlap:** Two maintenance tasks on the same `track_id` cannot overlap in time unless they share a coordinated compound possession window with certified spatial separation.
3. **Safety Headway Buffer:** Adjacent maintenance blocks on the same track require a minimum safety clearance buffer (default: 15 minutes) for work train mobilization and safety flag clearing.
4. **Heavy Machinery Sharing:** Tasks requiring the same specialised track machine (e.g. Tie-Tamper `CSM_01` or Tower Wagon `TW_01`) cannot be scheduled concurrently anywhere on the corridor.

---

# SECTION 2: 7-Feature ML Scoring Domain Data Mapping

Ayush's deterministic scoring engine / ML scoring model (`score_block()`) consumes 7 standardized numerical features to produce `risk_score` ($0.0 - 1.0$) and `priority_score` ($0.0 - 1.0$).

Below is the domain mapping, physical representation, real Indian Railways IT source, and fallback heuristic for each feature:

| # | Feature Name | Domain Meaning | Valid Range & Type | Real Indian Railways Data Source | Demo Fallback Heuristic / Assumption |
|---|---|---|---|---|---|
| 1 | `asset_criticality` | Operational importance of the track/asset based on route classification, line speed, and passenger vs freight throughput. | `float` $[0.0, 1.0]$ | **TMS (Track Management System)** — Route Grouping (Group A high speed trunk = 0.95, Group B = 0.80, Group D branch = 0.40, Sidings = 0.15). | Calculated from track speed potential and line classification: $\text{criticality} = 0.5 \times \frac{\text{speed}}{160} + 0.5 \times \frac{\text{density}}{120}$. |
| 2 | `defect_severity` | The severity level of structural or electrical defects detected during latest engineering inspections. | `float` $[0.0, 1.0]$ mapped from `DefectSeverity` enum: `NONE=0.0`, `MINOR=0.25`, `MODERATE=0.60`, `CRITICAL=1.00`. | **USFD Flaw Detection Registers**, **Track Recording Car (TRC / OMS-2000)** acceleration peaks, **OHE Current Collection Test Reports**. | Categorical defect severity recorded in inspection logs mapped directly to ordinal float values. |
| 3 | `days_overdue` | Number of days elapsed past the statutory maintenance due date prescribed by the Indian Railways Permanent Way / OHE Manual. | `int` $\ge 0$ (Normalized $[0.0, 1.0]$ via $\min(1.0, \frac{\text{days}}{90})$) | **TMS Scheduled Overhaul Schedule (POH/IOH)** and **TRD Maintenance Ledgers**. | Difference between current planning date and `last_maintained_days_ago + cycle_days`. |
| 4 | `failure_probability` | Estimated statistical probability that the asset will suffer an in-service failure within the next 7 days if maintenance is deferred. | `float` $[0.0, 1.0]$ | **Weibull Asset Degradation Model** fitted on cumulative Gross Million Tonnes (GMT), age, weather stress, and vibration logs. | $\text{failure\_prob} = 1 - \text{condition\_score} + 0.3 \times \text{defect\_severity}$. Normalized to $[0.02, 0.98]$. |
| 5 | `train_impact` | Quantitative index of train operational disruption caused by blocking this track (number of affected mail/express/freight trains and potential passenger delay minutes). | `float` $[0.0, 1.0]$ | **COA (Control Office Application)** & **ICMS (Integrated Coaching Management System)** timetable traffic density. | Ratio of trains scheduled through this track during requested window: $\text{impact} = \min(1.0, \frac{\text{conflicting trains}}{8})$. |
| 6 | `maintenance_duration` | Required duration in minutes to execute the standard operating procedure (SOP), including setup and track restoration. | `int` (Minutes: $30 - 360$, Normalized $[0.0, 1.0]$ via $\frac{\text{duration}}{360}$) | **Indian Railways Engineering Code & Standard Time Norms (IRPWM Chapter 5)**. | Lookup table by `WorkType`: `ROUTINE_INSPECTION` (45m), `OHE_MAINTENANCE` (90m), `BALLAST_TAMPING` (120m), `TRACK_RENEWAL` (240m). |
| 7 | `historical_failure_rate` | Historical frequency of unscheduled breakdowns, weld fractures, or signal trips on this asset/section over the past 36 months. | `float` $[0.0, 1.0]$ | **SIMS (Safety Information Management System)** & **Rail Madad / Punctuality Incident Logs**. | Historical incident count normalized by section age: $\text{rate} = \min(1.0, \frac{\text{historical incidents in 3 yrs}}{6})$. |

---

### 2.1 Formulaic Integration for Scorer Baseline

For Phase 1 deterministic scoring (unblocking Ayush and Tyagi):

$$\text{risk\_score} = 0.30 \cdot \text{defect\_severity} + 0.30 \cdot \text{failure\_probability} + 0.20 \cdot \text{days\_overdue\_norm} + 0.20 \cdot \text{historical\_failure\_rate}$$

$$\text{priority\_score} = 0.35 \cdot \text{risk\_score} + 0.25 \cdot \text{asset\_criticality} + 0.20 \cdot \text{train\_impact} + 0.20 \cdot (1.0 - \text{duration\_norm})$$

---

# SECTION 3: Standard Work Types & Resource Machine Modeling

### 3.1 Standard Work Types (`WorkType`)

| WorkType | Description | Standard Duration | Heavy Machinery / Mutual Exclusion Group | Precedence Typical Rule |
|---|---|---|---|---|
| `TRACK_RENEWAL` | Complete Track Renewal (CTR), rail/sleeper replacement | 180 – 360 min | `PQRS_GANG_01`, `BCM_CLEANER_01` | Must precede `BALLAST_TAMPING` |
| `BALLAST_TAMPING` | Track packing, dynamic track alignment | 90 – 180 min | `CSM_TAMPER_01`, `DUOMATIC_01` | Requires 15m safety buffer after Renewal |
| `OHE_MAINTENANCE` | 25kV catenary/contact wire inspection, insulator washing | 60 – 150 min | `TOWER_WAGON_01` | Requires Electrical Power Block |
| `SIGNALLING_INTERLOCKING` | Point machine overhaul, track circuit calibration | 45 – 120 min | `S_T_GANG_01` | Approach locking isolation |
| `ROUTINE_INSPECTION` | Ultrasonic Flaw Detection (USFD), track geometry check | 30 – 90 min | None (Manual Gang / Trolley) | Standalone |
| `EMERGENCY_REPAIR` | Rail fracture clamp, weld restoration, OHE break fix | 60 – 180 min | Assigned dynamically | Urgent priority, pre-empts routine slots |

---

# SECTION 4: 5 Standardized Disruption Scenarios

These 5 deterministic scenarios are designed for Tirth's simulation engine and provide guaranteed reproducibility for hackathon judging and UI validation.

```
       NORMAL PLAN                                           DISRUPTED & RECOVERED PLAN
UP-1   [ BLK-001 (OHE) ]  [ BLK-004 (Tamp) ]     UP-1   [ BLK-001 (COMMITTED-LOCKED) ]  [ ─── TRACK BLOCKED ─── ]
DOWN-1 [ BLK-002 (Tamp) ] [ BLK-003 (OHE)  ]     DOWN-1 [ BLK-002 (Tamp) ]  [ BLK-004 (Rescheduled from UP-1) ]
```

---

### Scenario 1: Asset / Track Unavailable (`TRACK_UNAVAILABLE`)
- **Trigger:** Heavy rainfall or rail surface defect causes track `UP-1` to be blocked between minute 60 and minute 240.
- **Disruption Payload:**
  ```json
  {
    "disruption_id": "DISR-001-TRK-UNAVAIL",
    "disruption_type": "TRACK_UNAVAILABLE",
    "corridor_id": "CORR-NDLS-AGC",
    "track_id": "UP-1",
    "start_minute": 60,
    "end_minute": 240,
    "description": "Track UP-1 closed due to emergency rail defect between KM 12 and 18."
  }
  ```
- **Expected System Action:**
  1. Identify candidate blocks scheduled on `UP-1` overlapping $[60, 240]$.
  2. If block is `is_committed: true`, flag severe operational conflict (or hold at original time if already started).
  3. If block is non-committed, CP-SAT reschedules it to a later possession window (e.g. Afternoon slot $[690, 870]$) or shifts to parallel track if applicable; if no slot fits, defers to `unscheduled_blocks` with reason `TRACK_UNAVAILABLE_IN_WINDOW`.

---

### Scenario 2: Emergency Maintenance Insertion (`EMERGENCY_WORK`)
- **Trigger:** USFD flaw detector identifies an alarming transverse rail crack at KM 14.2 on `DOWN-1`. An immediate 90-minute emergency block must be scheduled.
- **Disruption Payload:**
  ```json
  {
    "disruption_id": "DISR-002-EMG-WORK",
    "disruption_type": "EMERGENCY_WORK",
    "corridor_id": "CORR-NDLS-AGC",
    "track_id": "DOWN-1",
    "new_candidate": {
      "block_id": "BLK-EMG-999",
      "asset_id": "AST-DN1-RAI-014",
      "track_id": "DOWN-1",
      "work_type": "EMERGENCY_REPAIR",
      "duration_minutes": 90,
      "earliest_start_minute": 30,
      "latest_end_minute": 300,
      "priority_score": 0.995,
      "risk_score": 0.980,
      "is_committed": false,
      "metadata": { "defect_severity": "CRITICAL", "defect_type": "TRANSVERSE_FATIGUE_FRACTURE" }
    },
    "description": "Urgent rail fracture repair on DOWN-1 at KM 14.2."
  }
  ```
- **Expected System Action:**
  1. CP-SAT inserts `BLK-EMG-999` in the highest-priority prime night window $[30, 300]$.
  2. Lower-priority routine inspections on `DOWN-1` are pushed into later windows or deferred.
  3. Committed blocks remain locked in place.

---

### Scenario 3: Possession Window Curtailment (`POSSESSION_CURTAILMENT`)
- **Trigger:** Due to high-priority Vande Bharat special running behind schedule, the afternoon possession window on `UP-1` ($[690, 870]$, 180 min) is curtailed to $[750, 840]$ (90 min).
- **Disruption Payload:**
  ```json
  {
    "disruption_id": "DISR-003-WIN-CURTAIL",
    "disruption_type": "POSSESSION_CURTAILMENT",
    "corridor_id": "CORR-NDLS-AGC",
    "track_id": "UP-1",
    "start_minute": 690,
    "end_minute": 750,
    "description": "Afternoon possession delayed by 60 min due to late running Express train."
  }
  ```
- **Expected System Action:**
  1. Blocks exceeding 90 min (e.g. 120m Ballast Tamping) can no longer fit in the truncated window.
  2. Solver re-evaluates: fits shorter 45m routine inspection block instead, and moves long tasks to evening window $[1260, 1410]$.

---

### Scenario 4: Asset Condition Deterioration (`ASSET_CONDITION_DETERIORATION`)
- **Trigger:** Sensor monitoring a turnout point at Mathura Yard detects sudden temperature-induced point machine motor resistance. Defect severity jumps from `MINOR` to `CRITICAL`.
- **Disruption Payload:**
  ```json
  {
    "disruption_id": "DISR-004-COND-DEGRAD",
    "disruption_type": "ASSET_CONDITION_DETERIORATION",
    "corridor_id": "CORR-NDLS-AGC",
    "affected_asset_id": "AST-DN1-TUR-009",
    "description": "Turnout Point motor resistance critical anomaly detected."
  }
  ```
- **Expected System Action:**
  1. `feature_adapter` recalculates `risk_score` from $0.34 \to 0.94$ and `priority_score` from $0.41 \to 0.96$.
  2. In re-optimization, this block is promoted from unscheduled/deferred status into the prime night schedule.

---

### Scenario 5: Infeasible Scenario / Conflict Rejection (`INFEASIBLE_SCENARIO`)
- **Trigger:** Two committed blocks on the same track are forced into an overlapping time interval due to contradictory external possession cancellations, with zero bypass tracks available.
- **Disruption Payload:**
  ```json
  {
    "disruption_id": "DISR-005-INFEASIBLE",
    "disruption_type": "INFEASIBLE_SCENARIO",
    "corridor_id": "CORR-NDLS-AGC",
    "track_id": "UP-1",
    "start_minute": 0,
    "end_minute": 1440,
    "description": "All possession windows revoked while two committed blocks remain active."
  }
  ```
- **Expected System Action:**
  1. CP-SAT solver returns status `INFEASIBLE` (or `FEASIBLE` with explicit rejections).
  2. System provides structured `infeasibility_reasons` without crash, outputting clear reasons to the UI (e.g. `COMMITTED_BLOCK_WINDOW_REVOKED`).

---

# SECTION 5: The Golden Scenario Master Blueprint

The **Golden Scenario** is the master end-to-end benchmark dataset that demonstrates the entire Pashupatastra pipeline across all 6 roles.

### 5.1 Scenario Parameters
- **Corridor:** `CORR-NDLS-AGC` (New Delhi – Agra Trunk Line, Northern / North Central Railway)
- **Tracks:** `UP-1` (Direction: UP, 160 km/h, 110 trains/day), `DOWN-1` (Direction: DOWN, 160 km/h, 115 trains/day)
- **Possession Windows (per track):**
  - **Window 1 (Night Traffic Block):** Minute $30 \to 300$ (270 min capacity)
  - **Window 2 (Midday Maintenance Slot):** Minute $690 \to 870$ (180 min capacity)
  - **Window 3 (Evening Off-Peak):** Minute $1260 \to 1410$ (150 min capacity)
  - *Total daily possession capacity per track = 600 minutes.*

---

### 5.2 The 12 Candidate Blocks Table

| Block ID | Asset ID | Track | Work Type | Duration | Priority | Risk | Machine Group | Dependencies | Committed? | Expected Initial Outcome |
|---|---|---|---|---|---|---|---|---|---|---|
| `BLK-001` | `AST-DN1-OHE-001` | `DOWN-1` | `OHE_MAINTENANCE` | 120 min | 0.820 | 0.710 | `TOWER_WAGON_01` | None | No $\to$ **Committed** | **SCHEDULED** (Night: 30–150) |
| `BLK-002` | `AST-DN1-RAI-002` | `DOWN-1` | `BALLAST_TAMPING` | 105 min | 0.740 | 0.650 | `CSM_TAMPER_01` | None | No | **SCHEDULED** (Night: 165–270) |
| `BLK-003` | `AST-UP1-OHE-003` | `UP-1` | `OHE_MAINTENANCE` | 90 min | 0.880 | 0.780 | `TOWER_WAGON_01` | None | No $\to$ **Committed** | **SCHEDULED** (Midday: 690–780) |
| `BLK-004` | `AST-UP1-RAI-004` | `UP-1` | `BALLAST_TAMPING` | 90 min | 0.810 | 0.690 | `CSM_TAMPER_01` | None | No | **SCHEDULED** (Night: 30–120) |
| `BLK-005` | `AST-UP1-RAI-005` | `UP-1` | `TRACK_RENEWAL` | 150 min | 0.910 | 0.840 | None | None | No | **SCHEDULED** (Night: 135–285) |
| `BLK-006` | `AST-UP1-OHE-006` | `UP-1` | `OHE_MAINTENANCE` | 90 min | 0.990 | 0.890 | `TOWER_WAGON_01` | None | No | **SCHEDULED** (Evening: 1260–1350) |
| `BLK-007` | `AST-DN1-OHE-007` | `DOWN-1` | `OHE_MAINTENANCE` | 120 min | 0.850 | 0.720 | `TOWER_WAGON_01` | None | No | **SCHEDULED** (Midday: 690–810) |
| `BLK-008` | `AST-UP1-RAI-008` | `UP-1` | `ROUTINE_INSPECTION`| 45 min | 0.310 | 0.220 | None | None | No | **SCHEDULED** (Midday: 795–840) |
| `BLK-009` | `AST-DN1-RAI-009` | `DOWN-1` | `BALLAST_TAMPING` | 120 min | 0.580 | 0.510 | `CSM_TAMPER_01` | None | No | **DEFERRED** (Machine / Time limit) |
| `BLK-010` | `AST-UP1-RAI-010` | `UP-1` | `BALLAST_TAMPING` | 135 min | 0.520 | 0.480 | `CSM_TAMPER_01` | None | No | **DEFERRED** (Headway capacity) |
| `BLK-011` | `AST-DN1-SIG-011` | `DOWN-1` | `SIGNALLING_INTERLOCKING`| 90 min | 0.440 | 0.380 | `S_T_GANG_01` | None | No | **DEFERRED** (Lower priority) |
| `BLK-012` | `AST-DN1-TUR-012` | `DOWN-1` | `ROUTINE_INSPECTION`| 60 min | 0.390 | 0.310 | None | None | No | **DEFERRED** (Window full) |

---

### 5.3 Step-by-Step Golden Journey

```
STEP 1: Raw Maintenance Data (Darshini)
   │ 12 Candidate Blocks with full 7-feature metadata across UP-1 & DOWN-1
   ▼
STEP 2: Scoring Engine (Ayush)
   │ Extracts features via feature_adapter.py -> calculates risk_score & priority_score
   ▼
STEP 3: CP-SAT Optimization (Tyagi)
   │ Solves in < 0.2s -> 8 Scheduled (Total Priority: 6.31), 4 Deferred (Clear rejection reasons)
   ▼
STEP 4: Section Controller Locks Critical Works
   │ Controller marks BLK-001 (DOWN-1) and BLK-003 (UP-1) as is_committed: true
   ▼
STEP 5: Disruption Triggered (Tirth)
   │ DisruptionEvent: Track UP-1 UNAVAILABLE between min 60 and 240 (Night Window crippled)
   ▼
STEP 6: Re-Optimization Engine (Tyagi)
   │ - Committed BLK-003 on UP-1 preserved at [690, 780]
   │ - Affected BLK-004 & BLK-005 on UP-1 cannot stay at [30, 285]
   │ - CP-SAT reschedules BLK-004 to Evening slot [1260, 1350]
   │ - Defers lower-priority BLK-008
   │ - Generates Recovery Schedule without single constraint violation
   ▼
STEP 7: API Layer (Mehta)
   │ Exposes POST /optimize and POST /disrupt returning compliant JSON
   ▼
STEP 8: Frontend Dashboard (Archit)
   │ Interactive Before vs After timeline, KPI deltas, and plain-English explainability cards
```

---

# SECTION 6: Team Handoff Summary

| Team Member | Module | What Darshini Provides Directly |
|---|---|---|
| **Ayush** | ML / Scorer | `backend/app/data/feature_adapter.py` mapping domain records $\to$ the 7 scoring inputs with clear formulas and normalization bounds. |
| **Tyagi** | CP-SAT Optimizer | `backend/app/data/fixtures/golden_scenario.json` and `backend/app/data/fixtures/corridor_a_blocks.json` with valid windows, machine groups, and dependencies. |
| **Tirth** | Simulation & Recovery | `backend/app/data/fixtures/disruption_scenarios.json` containing 5 deterministic disruption payloads and expected recovery rules. |
| **Mehta** | API Backend | Verified schemas and sample request/response payloads in `contracts/schemas.py`. |
| **Archit** | Frontend | Clear visual lifecycle definitions (Scheduled, Committed, Deferred, Disrupted, Recovered) and Golden Scenario Before/After timeline expectations. |
