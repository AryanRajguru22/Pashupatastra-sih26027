# DOMAIN REVIEW — PASHUPATASTRA
**Workstream:** Domain Modeling & Synthetic Data  
**Owner:** Darshini (`feature/domain-data`)  
**Project:** Pashupatastra — AI-Assisted Railway Maintenance Scheduling & Disruption Recovery (SIH 2026)  
**Document Status:** Ready for Team Review & Domain Freeze Checkpoint  

---

## Executive Summary

Pashupatastra solves a critical real-world problem for Indian Railways (IR): **how to optimize, schedule, and dynamically re-schedule maintenance blocks (possessions) across busy railway corridors while respecting operational safety, asset conditions, precedence dependencies, and train timetables.**

This domain review formalizes the railway domain semantics, identifies limitations in initial assumptions, and establishes recommendations for the **Domain Freeze** checkpoint before downstream workstreams (Optimizer, ML Scorer, Simulation, API, and Frontend) build further.

---

## 1. Track Modeling & Corridor Representation

### Current Assumption
- Tracks are identified by simple flat strings: `track_id = "UP-1"`, `track_id = "DOWN-1"`.

### Problem Identified
1. **Lacks Corridor Context:** A flat string does not convey which corridor, section, or kilometer range the track belongs to.
2. **Over-Engineering Risk:** Introducing a full GIS graph or switch-and-crossing topological network adds unnecessary mathematical complexity to the CP-SAT solver without adding demo value.

### Recommendation
- **Retain `track_id: str` as the primary key/unique identifier** in shared contracts (e.g. `"UP-1"`, `"DOWN-1"`, or `"SEC01-UP-1"`).
- In the Domain Data layer, represent tracks with structured metadata:
  ```json
  {
    "track_id": "UP-1",
    "corridor_id": "CORRIDOR_A",
    "section_name": "New Delhi - Ghaziabad Section 01",
    "direction": "UP",
    "km_start": 10.0,
    "km_end": 35.0,
    "speed_limit_kmh": 130,
    "electrified": true
  }
  ```

### Reason & Railway Operational Grounding
- In Indian Railways, maintenance blocks are granted per **block section** along a directional line (e.g., Up Main Line, Down Main Line, Up Loop, Down Loop).
- Preserving a clean string identifier (`track_id`) keeps CP-SAT interval constraints simple (no overlapping intervals on the same `track_id`), while metadata enables rich UI display on the Frontend dashboard.

### Impact on Downstream Modules
- **Optimizer (Tyagi):** Zero breaking changes. CP-SAT continues to use `track_id: str`.
- **Frontend (Archit):** Can group tracks by direction (UP / DOWN) and display realistic corridor mileage.
- **ML Scorer (Ayush):** Can use speed limit and traffic density metadata to evaluate operational impact.

---

## 2. Maintenance Work Types (`WorkType`)

### Current Assumption
- Generic categories: `RENEWAL`, `INSPECTION`, `REPAIR`, `MAINTENANCE`.

### Problem Identified
- Categories are too generic and do not reflect Indian Railways maintenance practices (e.g., Track Machine Operations, OHE Power Blocks, S&T Interlocking).
- Fails to capture why certain tasks have strict durations, why some require heavy machinery, and why some require complete electrical isolation (power block).

### Recommendation
Standardize the `WorkType` enum into 6 concrete railway maintenance categories:

| WorkType | Description | Typical Duration | Machinery / Resource Note |
|---|---|---|---|
| `TRACK_RENEWAL` | Complete Track Renewal (CTR), Through Sleeper Renewal (TSR), Through Rail Renewal (TRR) | 180 – 360 min | Heavy possession (BCM, PQRS, T-28), total track closure |
| `BALLAST_TAMPING` | Track packing and alignment using Tie-Tamping machines (CSM, Duomatic, Unimat) | 90 – 180 min | Requires safety headway buffer after track renewal |
| `OHE_MAINTENANCE` | Overhead Equipment inspection, contact wire replacement, insulator washing | 60 – 150 min | Requires Electrical Power Block (OHE isolation); Tower Wagon |
| `SIGNALLING_INTERLOCKING` | Point machine overhaul, track circuit testing, axle counter maintenance | 45 – 120 min | Affects interlocking and station approaches; Signal Gang |
| `ROUTINE_INSPECTION` | Ultrasonic Flaw Detection (USFD), foot patrol, track geometry recording | 30 – 90 min | Minimal disruption; manual trolley or inspection unit |
| `EMERGENCY_REPAIR` | Rail fracture, weld failure, OHE wire parting, point failure | 60 – 180 min | Unscheduled, pre-emptive, highest urgency score |

### Reason & Railway Operational Grounding
- These categories map directly to the Indian Railways Permanent Way (P-Way), Electrical (TRD/OHE), and S&T departments.
- They justify real precedence dependencies (e.g., Ballast Cleaning $\to$ Tamping $\to$ Dynamic Stabilization).

### Impact on Downstream Modules
- **Optimizer (Tyagi):** Can enforce realistic precedence dependencies between related maintenance blocks.
- **ML Scorer (Ayush):** Generates distinct risk profiles (e.g., `EMERGENCY_REPAIR` has higher urgency than `ROUTINE_INSPECTION`).
- **Simulation (Tirth):** Enables realistic scenarios like emergency insertion or machine failure.

---

## 3. Disruption Types (`DisruptionType`)

### Current Assumption
- Ad-hoc disruption triggers without standardized types.

### Problem Identified
- Without clear disruption definitions, disruption testing becomes non-deterministic and hard to demonstrate to judges.

### Recommendation
Define 4 standardized, deterministic disruption types for the **PLAN $\to$ DISRUPT $\to$ RECOVER** demo:

1. **`TRACK_UNAVAILABLE` (Scenario 1 — Track Blockage)**
   - *Description:* A track segment becomes unusable from time $t_1$ to $t_2$ (e.g., rail defect, emergency speed restriction, external obstruction).
   - *Impact:* Blocks currently scheduled on that track during that interval become `AFFECTED` and must be rescheduled to another track or moved in time.

2. **`EMERGENCY_WORK` (Scenario 2 — Critical Unscheduled Work Insertion)**
   - *Description:* An urgent emergency repair block (e.g. detected rail fracture) appears at time $t$ with maximum priority.
   - *Impact:* CP-SAT must schedule the emergency block immediately, shifting or dropping lower-priority routine blocks while protecting already committed blocks.

3. **`POSSESSION_CURTAILMENT` (Scenario 3 — Time Window Shortened)**
   - *Description:* Due to delayed express train movements, an available possession window (e.g. 08:00–12:00) is curtailed to (09:30–11:30).
   - *Impact:* Maintenance tasks that no longer fit must be compressed, rescheduled to later slots, or marked unscheduled with clear reason codes.

4. **`ASSET_BREAKDOWN` (Scenario 4 — Machine / Equipment Failure)**
   - *Description:* A heavy track maintenance machine (e.g. BCM) breaks down mid-block, extending work duration or halting all tasks sharing that machine group.

### Reason & Railway Operational Grounding
- Demonstrates how real Indian Railways Section Controllers and Engineering Controllers handle daily field emergencies.

---

## 4. Resource & Machinery Constraints

### Current Assumption
- Complex multi-resource models (`crew_id`, `crew_type`, `equipment_id`, `crew_capacity`).

### Problem Identified
- Full resource leveling and crew rostering turns the solver into a 2-stage multi-mode resource-constrained project scheduling problem (RCPSP), risking slow solve times (>10s) and artificial infeasibility during a live hackathon demo.

### Recommendation
- **For Phase 1 (v1 Prototype):** Do NOT model individual human workers or complex shift rosters.
- **Model Shared Heavy Machinery via `mutual_exclusion_group: Optional[str]`:**
  - Example: Block 1 (`BALLAST_CLEANING`) and Block 4 (`BALLAST_CLEANING`) both require `mutual_exclusion_group: "BCM_01"`.
  - The CP-SAT solver simply enforces:
    $$\text{Interval}(\text{Block}_1) \cap \text{Interval}(\text{Block}_4) = \emptyset$$
  - This guarantees that the single available BCM machine is not scheduled in two places simultaneously, without bloating the solver model.

---

## 5. Train Timetable & Operational Window Representation

### Current Assumption
- `train_timetable` field exists in the contract but lacks clear solver constraints.

### Problem Identified
- Running a microscopic train movement simulation inside CP-SAT would overwhelm the solver.
- Ignoring train operations makes the maintenance schedule ungrounded.

### Recommendation
- Represent train traffic as **Available Possession Windows** (or **Forbidden Shadow Intervals**) per track:
  ```json
  [
    {"track_id": "UP-1", "start_minute": 60, "end_minute": 300, "window_type": "NIGHT_TRAFFIC_BLOCK"},
    {"track_id": "UP-1", "start_minute": 660, "end_minute": 840, "window_type": "AFTERNOON_MAINTENANCE_SLOT"},
    {"track_id": "DOWN-1", "start_minute": 90, "end_minute": 330, "window_type": "NIGHT_TRAFFIC_BLOCK"}
  ]
  ```
- **Solver Constraint:** Every scheduled block $[s_i, e_i]$ on track $T$ must be entirely contained within at least one valid possession window:
  $$\exists W \in \text{PossessionWindows}(T) \quad \text{such that} \quad W.\text{start} \le s_i \quad \text{and} \quad e_i \le W.\text{end}$$

---

## 6. Precedence Dependencies & Sequential Chains

### Railway Realism
Maintenance operations in Indian Railways often follow strict sequential engineering protocols:
$$\text{Track Renewal (CTR)} \xrightarrow{\text{precedes}} \text{Deep Ballast Screening (BCM)} \xrightarrow{\text{precedes}} \text{Tie Tamping (CSM)} \xrightarrow{\text{precedes}} \text{Speed Normalization}$$

### Contract Model
- `dependencies: list[str]` on `BlockCandidate`:
  - If Block $B$ has `dependencies: ["Block_A"]`, the solver enforces:
    $$\text{start\_time}(B) \ge \text{end\_time}(A) + \text{buffer}$$

---

## 7. Synthetic Data Generator Architecture

To provide deterministic and reproducible datasets for the entire team, the generator in `backend/app/data/generator.py` supports:

1. **Seed-Driven Reproducibility:**
   - `generate_corridor(seed=42, ...)` always produces identical asset layouts, candidates, and possession windows.
2. **Pre-packaged Benchmark Scenarios:**
   - **Scenario A (Baseline Mainline):** 12 blocks, 2 tracks (UP-1, DOWN-1), clean feasible solution with 2 dependency pairs.
   - **Scenario B (High Density Corridor):** 24 blocks, 4 tracks (UP-1, UP-2, DOWN-1, DOWN-2), shared equipment groups, tight possession windows.
   - **Scenario C (Disruption & Infeasibility Testbed):** 18 blocks with pre-committed historical blocks, over-subscribed possession windows to test infeasibility reason codes.

---

## 8. Summary Checklist for Domain Freeze

- [x] **Track Modeling:** String `track_id` preserved for solver; metadata dictionary available for UI.
- [x] **Work Types:** 6 standardized railway maintenance categories agreed.
- [x] **Disruption Types:** 4 deterministic disruption types defined.
- [x] **Resources:** Simplified to `mutual_exclusion_group` for heavy machines.
- [x] **Train Timetable:** Modeled as discrete `PossessionWindow` intervals.
- [x] **Synthetic Generator:** Seed-driven implementation ready for downstream integration.
