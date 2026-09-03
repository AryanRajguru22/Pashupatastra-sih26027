# Data Contracts Specification — Pashupatastra

This document specifies the shared data contracts between the data generator, ML scorer, CP-SAT optimizer, simulation engine, backend API, and frontend dashboard.

---

## 1. Enums

### WorkType
- `TRACK_RENEWAL`: Complete Track Renewal (CTR), Sleeper/Rail renewal (Heavy machine, strict duration).
- `BALLAST_TAMPING`: Tie-tamping & track geometry packing (CSM/Duomatic).
- `OHE_MAINTENANCE`: Overhead electrification inspection/power block.
- `SIGNALLING_INTERLOCKING`: Point machines, track circuits, axle counters.
- `ROUTINE_INSPECTION`: USFD ultrasonic rail flaw detection, foot patrol.
- `EMERGENCY_REPAIR`: Urgent rail fracture, weld failure, wire parting.

### DisruptionType
- `TRACK_UNAVAILABLE`: Specific track closed for a time window.
- `EMERGENCY_WORK`: Urgent high-priority block injected.
- `POSSESSION_CURTAILMENT`: Possession time window shortened.
- `ASSET_BREAKDOWN`: Equipment failure causing delay or cancellation.

### SolverStatus
- `OPTIMAL`: Optimal feasible schedule found.
- `FEASIBLE`: Feasible schedule found within time limit.
- `INFEASIBLE`: No schedule satisfies all hard constraints.
- `NO_SOLUTION`: Solver timed out with no solution.

---

## 2. Core Schemas

### `BlockCandidate`
Represents a maintenance job requested to be scheduled.
```json
{
  "block_id": "BLK-001",
  "asset_id": "AST-TRK-012",
  "track_id": "UP-1",
  "work_type": "BALLAST_TAMPING",
  "duration_minutes": 120,
  "earliest_start_minute": 60,
  "latest_end_minute": 360,
  "priority_score": 0.85,
  "risk_score": 0.72,
  "dependencies": ["BLK-000"],
  "mutual_exclusion_group": "CSM_TAMPER_01",
  "is_committed": false,
  "status": "PLANNED"
}
```

### `PossessionWindow`
Represents an available maintenance window on a track.
```json
{
  "window_id": "POS-UP1-01",
  "track_id": "UP-1",
  "start_minute": 60,
  "end_minute": 360,
  "window_type": "NIGHT_BLOCK"
}
```

### `OptimizationRequest`
Input payload sent to `solve(request)`.
```json
{
  "corridor_id": "CORRIDOR_A",
  "horizon_minutes": 1440,
  "tracks": ["UP-1", "DOWN-1"],
  "candidates": [...],
  "possession_windows": [...],
  "existing_committed_blocks": [...],
  "min_headway_minutes": 15
}
```

### `ScheduledBlock`
```json
{
  "block_id": "BLK-001",
  "track_id": "UP-1",
  "start_minute": 90,
  "end_minute": 210,
  "priority_score": 0.85,
  "risk_score": 0.72,
  "is_committed": false,
  "status": "SCHEDULED"
}
```

### `OptimizationResult`
Output payload returned by `solve(request)`.
```json
{
  "corridor_id": "CORRIDOR_A",
  "status": "OPTIMAL",
  "scheduled_blocks": [...],
  "unscheduled_blocks": [...],
  "total_priority_scheduled": 4.55,
  "total_risk_mitigated": 3.82,
  "solve_time_seconds": 0.08,
  "infeasibility_reasons": [],
  "rejection_reasons": {
    "BLK-009": "TIME_WINDOW_CONFLICT"
  }
}
```
