/**
 * TypeScript interfaces matching the Pydantic contracts in contracts/.
 *
 * These are the EXACT shapes from the Python models — no fields added,
 * renamed, or removed. If the contracts change, update here to match.
 *
 * Source files:
 *   contracts/common.py
 *   contracts/block_candidate.py
 *   contracts/optimization_request.py
 *   contracts/optimization_result.py
 *   contracts/disruption_event.py
 */

// ── contracts/common.py ──────────────────────────────────────────────

export type WorkType = "RENEWAL" | "INSPECTION" | "REPAIR" | "PREVENTIVE";

export type OptimizationStatus =
  | "OPTIMAL"
  | "FEASIBLE"
  | "INFEASIBLE"
  | "TIMEOUT";

export type DisruptionType =
  | "ASSET_FAILURE"
  | "WEATHER"
  | "EMERGENCY_BLOCK_REQUEST"
  | "TRAIN_DELAY"
  | "BLOCK_OVERRUN";

export type ReoptimizationScope =
  | "FULL_HORIZON"
  | "ROLLING_WINDOW"
  | "AFFECTED_SEGMENT_ONLY";

export interface TimeWindow {
  start: string; // ISO 8601 datetime
  end: string;
}

export interface TrainService {
  train_id: string;
  corridor_id: string;
  section_id: string;
  track_id: string;
  departure: string;
  arrival: string;
}

export interface ScheduledBlock {
  block_id: string;
  track_id: string;
  start: string;
  end: string;
}

export interface ObjectiveWeights {
  risk_reduction_weight: number;
  asset_availability_weight: number;
  blocks_completed_weight: number;
}

export interface ConstraintsConfig {
  max_concurrent_blocks_per_corridor: number;
  min_headway_minutes: number;
}

export interface ExplanationEntry {
  block_id: string;
  reason: string;
  binding_constraints: string[];
}

// ── contracts/block_candidate.py ─────────────────────────────────────

export interface BlockCandidate {
  block_id: string;
  asset_id: string;
  corridor_id: string;
  section_id: string;
  track_id: string;

  work_type: WorkType;
  duration_minutes: number;
  earliest_start: string;
  latest_finish: string;
  preferred_windows: TimeWindow[];

  priority_score: number; // 0–1, from ML subsystem
  risk_score: number; // 0–1, from ML subsystem

  requires_full_block: boolean;
  min_gap_before_next_train_minutes: number;

  dependencies: string[];
  mutually_exclusive_with: string[];
}

// ── contracts/optimization_request.py ────────────────────────────────

export interface OptimizationRequest {
  request_id: string;
  corridor_id: string;
  planning_horizon: TimeWindow;

  block_candidates: BlockCandidate[];
  train_timetable: TrainService[];
  existing_committed_blocks: ScheduledBlock[];

  objective_weights: ObjectiveWeights;
  constraints_config: ConstraintsConfig;
}

// ── contracts/optimization_result.py ─────────────────────────────────

export interface UnscheduledBlock {
  block_id: string;
  reason: string;
}

export interface OptimizationKPIs {
  asset_availability_pct: number; // 0–100
  trains_affected: number;
  blocks_scheduled: number;
  risk_reduction_score: number;
}

export interface OptimizationResult {
  request_id: string;
  status: OptimizationStatus;

  scheduled_blocks: ScheduledBlock[];
  unscheduled_blocks: UnscheduledBlock[];

  kpis: OptimizationKPIs;
  explainability: ExplanationEntry[];

  solve_time_ms: number;
  generated_at: string;
}

// ── contracts/disruption_event.py ────────────────────────────────────

export interface DisruptionImpact {
  unavailable_asset_ids: string[];
  invalidated_block_ids: string[];
  newly_required_block_ids: string[];
}

export interface DisruptionEvent {
  event_id: string;
  event_type: DisruptionType;

  affected_asset_id: string | null;
  affected_block_id: string | null;
  affected_corridor_id: string | null;

  timestamp: string;
  description: string;

  impact: DisruptionImpact;

  triggers_reoptimization: boolean;
  reoptimization_scope: ReoptimizationScope;
}

// ── Enriched view for the frontend ───────────────────────────────────

/**
 * A ScheduledBlock joined with its BlockCandidate source data.
 * This is a frontend-only convenience type — NOT a new contract.
 */
export interface EnrichedScheduledBlock extends ScheduledBlock {
  candidate: BlockCandidate | null;
  explanation: ExplanationEntry | null;
}

/**
 * An UnscheduledBlock joined with its BlockCandidate source data.
 */
export interface EnrichedUnscheduledBlock extends UnscheduledBlock {
  candidate: BlockCandidate | null;
}

/**
 * The complete data the dashboard needs, pre-joined from result + request.
 */
export interface DashboardData {
  result: OptimizationResult;
  request: OptimizationRequest;
  enrichedScheduled: EnrichedScheduledBlock[];
  enrichedUnscheduled: EnrichedUnscheduledBlock[];
}
