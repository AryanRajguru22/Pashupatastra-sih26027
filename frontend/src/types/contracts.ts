/**
 * TypeScript interfaces matching the canonical contracts in contracts/schemas.py.
 *
 * Source: contracts/schemas.py (Pashupatastra Stage 1-3 canonical contracts)
 */

// ── Enums from contracts/schemas.py ──────────────────────────────────

export type WorkType =
  | "TRACK_RENEWAL"
  | "BALLAST_TAMPING"
  | "OHE_MAINTENANCE"
  | "SIGNALLING_INTERLOCKING"
  | "ROUTINE_INSPECTION"
  | "EMERGENCY_REPAIR";

export type DisruptionType =
  | "TRACK_UNAVAILABLE"
  | "EMERGENCY_WORK"
  | "POSSESSION_CURTAILMENT"
  | "ASSET_BREAKDOWN";

export type SolverStatus =
  | "OPTIMAL"
  | "FEASIBLE"
  | "INFEASIBLE"
  | "NO_SOLUTION";

export type BlockStatus =
  | "PLANNED"
  | "SCHEDULED"
  | "COMMITTED"
  | "AFFECTED"
  | "UNSCHEDULED"
  | "CANCELLED";

// ── ML Scoring Features Model ────────────────────────────────────────

export interface ScoringFeatures {
  asset_criticality?: number;
  defect_severity?: number;
  defect_severity_name?: string;
  days_overdue?: number;
  days_overdue_norm?: number;
  failure_probability?: number;
  train_impact?: number;
  maintenance_duration?: number;
  maintenance_duration_norm?: number;
  historical_failure_rate?: number;
  baseline_risk_score?: number;
  baseline_priority_score?: number;
  [key: string]: unknown;
}

export interface CandidateMetadata {
  asset_name?: string;
  km_location?: number;
  defect_severity?: string;
  scoring_features?: ScoringFeatures;
  [key: string]: unknown;
}

// ── Core Contracts from contracts/schemas.py ─────────────────────────

export interface PossessionWindow {
  window_id: string;
  track_id: string;
  start_minute: number;
  end_minute: number;
  window_type?: string;
}

export interface BlockCandidate {
  block_id: string;
  asset_id: string;
  track_id: string;
  work_type: WorkType | string;
  duration_minutes: number;
  earliest_start_minute: number;
  latest_end_minute: number;
  priority_score: number;
  risk_score: number;
  dependencies: string[];
  mutual_exclusion_group: string | null;
  is_committed: boolean;
  status: BlockStatus | string;
  metadata?: CandidateMetadata;
}

export interface ScheduledBlock {
  block_id: string;
  track_id: string;
  start_minute: number;
  end_minute: number;
  work_type: WorkType | string;
  priority_score: number;
  risk_score: number;
  is_committed: boolean;
  status: BlockStatus | string;
}

export interface OptimizationRequest {
  corridor_id: string;
  horizon_minutes: number;
  tracks: string[];
  candidates: BlockCandidate[];
  possession_windows: PossessionWindow[];
  existing_committed_blocks: BlockCandidate[];
  min_headway_minutes: number;
  train_timetable?: Record<string, unknown>[];
}

export interface OptimizationResult {
  corridor_id: string;
  status: SolverStatus | string;
  scheduled_blocks: ScheduledBlock[];
  unscheduled_blocks: BlockCandidate[];
  total_priority_scheduled: number;
  total_risk_mitigated: number;
  solve_time_seconds: number;
  infeasibility_reasons: string[];
  rejection_reasons: Record<string, string>;
}

export interface DisruptionEvent {
  disruption_id: string;
  disruption_type: DisruptionType | string;
  corridor_id: string;
  track_id?: string | null;
  start_minute: number;
  end_minute: number;
  affected_asset_id?: string | null;
  new_candidate?: BlockCandidate | null;
  description?: string;
}

// ── POST /recover contracts, matching contracts.RecoveryRequest /
//    contracts.RecoveryResponse in contracts/schemas.py ────────────────

export interface RecoveryRequest {
  request: OptimizationRequest;
  disruption: DisruptionEvent;
}

export interface RecoveryResponse {
  disruption: DisruptionEvent;
  updated_request: OptimizationRequest;
  recovery_result: OptimizationResult;
}

// ── Enriched Views for Frontend Command Center ───────────────────────

export interface EnrichedScheduledBlock extends ScheduledBlock {
  candidate: BlockCandidate | null;
  rejectionReason: string | null;
}

export interface EnrichedUnscheduledBlock extends BlockCandidate {
  candidate: BlockCandidate;
  rejectionReason: string;
}

/**
 * Whether the optimizer backend answered. This says nothing whatsoever
 * about where the DATA came from.
 *
 * ONLINE   - the backend responded and solved the request.
 * DEGRADED - the backend was reachable but the operation failed
 *            (non-2xx), so the displayed plan is not its answer.
 * OFFLINE  - the backend could not be reached at all.
 */
export type ConnectivityStatus = "ONLINE" | "DEGRADED" | "OFFLINE";

/**
 * Where the DATA being displayed came from. This is a property of the
 * input, which the frontend owns: it is the frontend that chooses to
 * POST a checked-in fixture, so only the frontend can honestly label
 * provenance. The backend receives an OptimizationRequest and has no
 * way to know whether its contents are real, so it must never be asked
 * to assert this.
 *
 * SYNTHETIC_FIXTURE - generated demo data checked into the repository.
 *                     Currently the ONLY truthful value: every request
 *                     the dashboard sends is built from
 *                     src/data/corridor_a_blocks.json.
 * REALISTIC_STATIC  - transcribed from published, cited sources.
 *                     Not yet available; reserved.
 * LIVE_FEED         - a live operational feed. Not yet available;
 *                     reserved. Do not use until one actually exists.
 */
export type DataProvenance =
  | "SYNTHETIC_FIXTURE"
  | "REALISTIC_STATIC"
  | "LIVE_FEED";

/**
 * NOTE: there is deliberately no single combined "data source" field.
 *
 * A `DataSourceType` of "LIVE_API" | "FIXTURE" used to stand in for
 * both facts at once, which is how a reachable backend serving a
 * checked-in synthetic fixture came to be rendered as "LIVE BACKEND:
 * CONNECTED". Connectivity and provenance are independent and are kept
 * as separate fields below; do not reintroduce a merged field.
 */
export interface DashboardData {
  result: OptimizationResult;
  request: OptimizationRequest;
  enrichedScheduled: EnrichedScheduledBlock[];
  enrichedUnscheduled: EnrichedUnscheduledBlock[];

  /** Did the backend answer? */
  connectivity: ConnectivityStatus;

  /** Where did the displayed data come from? */
  dataProvenance: DataProvenance;

  /**
   * When the displayed data was produced. For a checked-in fixture
   * there is no meaningful generation time, so this is undefined and
   * the UI must show "UNKNOWN" rather than inventing "now".
   */
  dataGeneratedAt?: string;

  /** Why connectivity is DEGRADED/OFFLINE, when it is. */
  connectivityDetail?: string;

  apiLatencyMs?: number;
  apiEndpoint?: string;
}
