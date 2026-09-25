/**
 * Client for the frozen Pashupatastra /v1 lifecycle API.
 *
 * Types mirror docs/openapi-v1.json. `block_candidate` and event `metadata`
 * are outside the frozen contract, so they are typed as loose records and
 * must always be parsed defensively by callers.
 *
 * Identity is DECLARED, not authenticated (demo mode): every call carries
 * X-Actor-Id + X-Actor-Role taken from the persona chosen in the shell.
 */

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

export const CORRIDOR_ID = "CORR-NDLS-AGC";

export type JobStatus =
  | "reported"
  | "scheduled"
  | "notified"
  | "in_progress"
  | "completed";

export type JobType =
  | "TRACK_RENEWAL"
  | "BALLAST_TAMPING"
  | "OHE_MAINTENANCE"
  | "SIGNALLING_INTERLOCKING"
  | "ROUTINE_INSPECTION"
  | "EMERGENCY_REPAIR";

export type Severity = "NONE" | "MINOR" | "MODERATE" | "CRITICAL";
export type Role = "WORKER" | "ENGINEER" | "AUTHORITY";

export interface Persona {
  id: string;
  role: Role;
  label: string;
}

export const PERSONAS: Persona[] = [
  { id: "WORKER-042", role: "WORKER", label: "Field worker / crew" },
  { id: "ENGINEER-007", role: "ENGINEER", label: "Planning engineer" },
  { id: "AUTHORITY-017", role: "AUTHORITY", label: "Section authority" },
];

export interface Job {
  job_id: string;
  track_id: string;
  work_type: JobType | string;
  distance_start: number;
  distance_end: number;
  workers_min: number;
  workers_max: number;
  description: string;
  status: JobStatus;
  priority_score: number;
  risk_score: number;
  schedule_start_minute?: number | null;
  schedule_end_minute?: number | null;
  created_at: string;
  updated_at?: string | null;
  last_solver_status?: string | null;
  last_refusal_reason?: string | null;
  proposal_run_id?: string | null;
  /** Outside the frozen contract: parse defensively. */
  block_candidate: Record<string, unknown>;
}

export interface JobList {
  items: Job[];
  next_cursor?: string | null;
}

export interface Actor {
  actor_id: string;
  role: string;
  kind: string;
  assurance: string;
}

export interface JobState {
  status: string;
  block_status: string;
  is_committed: boolean;
  proposal_run_id?: string | null;
  schedule_start_minute?: number | null;
  schedule_end_minute?: number | null;
}

export interface JobEvent {
  sequence: number;
  event_id: string;
  schema_version: number;
  entity_type: string;
  job_id: string;
  event_type: string;
  occurred_at: string;
  actor: Actor;
  reason?: string | null;
  optimization_run_id?: string | null;
  before_state?: JobState | null;
  after_state?: JobState | null;
  /** Outside the frozen contract: parse defensively. */
  metadata: Record<string, unknown>;
}

export interface JobHistory {
  job_id: string;
  events: JobEvent[];
}

export interface Provenance {
  topology: string;
  timetable: string;
  asset_condition: string;
  possession: string;
  effective: string;
}

export interface ExplanationItem {
  code: string;
  detail: string;
}

export interface Proposal {
  proposal_id: string;
  job_id: string;
  optimization_run_id: string;
  corridor_id: string;
  track_id: string;
  section_id?: string | null;
  start_minute: number;
  end_minute: number;
  duration_minutes: number;
  work_type: string;
  priority_score: number;
  risk_score: number;
  objective_score: number;
  is_committed: boolean;
  explanation: ExplanationItem[];
  provenance: Provenance;
  generated_at: string;
}

export interface OptimizationResponse {
  corridor_id: string;
  solver_status: string;
  optimization_run_id: string;
  solve_time_seconds: number;
  counts: {
    considered: number;
    committed: number;
    scheduled: number;
    unscheduled: number;
  };
  scheduled: {
    job_id: string;
    track_id: string;
    start_minute: number;
    end_minute: number;
    is_committed: boolean;
  }[];
  unscheduled: { job_id: string; track_id: string; reason: string }[];
  infeasibility_reasons: string[];
  uncovered_tracks: string[];
  possession_derivation: string;
  possession_window_count: number;
  possession_source: string;
  provenance: Provenance;
  generated_at: string;
}

export interface OptimizationRun {
  run_id: string;
  actor: string;
  trigger: string;
  corridor_id?: string | null;
  requested_at: string;
  completed_at: string;
  solver_status: string;
  solve_time_seconds?: number | null;
  error?: string | null;
  request_json: string;
  result_json?: string | null;
  provenance_snapshot_json?: string | null;
}

export interface Evidence {
  evidence_reference: string;
  evidence_kind: "PHOTO" | "VIDEO" | "DOCUMENT" | "MEASUREMENT";
  captured_at: string;
  latitude?: number | null;
  longitude?: number | null;
  note?: string | null;
}

export interface RecordedEvidence extends Evidence {
  evidence_id: string;
  phase: string;
}

export interface Execution {
  execution_id: string;
  job_id: string;
  attempt_number: number;
  status: string;
  optimization_run_id: string;
  proposal_id: string;
  section_id?: string | null;
  track_id: string;
  planned_start_minute: number;
  planned_end_minute: number;
  actual_start_at: string;
  actual_start_minute: number;
  actual_end_at?: string | null;
  actual_end_minute?: number | null;
  committed_block_digest: string;
  started_by: Actor;
  ended_by?: Actor | null;
  started_recorded_at: string;
  ended_recorded_at?: string | null;
  before_work_evidence: RecordedEvidence[];
  after_work_evidence?: RecordedEvidence[];
  failure_evidence?: RecordedEvidence[];
  not_completed_reason?: string | null;
  deviations: {
    started_before_planned_start: boolean;
    ended_after_planned_end?: boolean | null;
  };
}

export interface Obligation {
  job_id: string;
  obligation_type: string;
  state: string;
  owed_role?: string | null;
  is_open: boolean;
  is_past_due: boolean;
  clock_started_at?: string | null;
  anchor_event_id?: string | null;
  due_at?: string | null;
  sla_seconds?: number | null;
  elapsed_seconds?: number | null;
  escalation_level?: number;
  reason_code: string;
  reason: string;
  optimization_run_id?: string | null;
  attention_required?: boolean;
  attention_reason_code?: string | null;
  attention_role?: string | null;
  attention_event_id?: string | null;
  policy_version: string;
  policy_assumed: boolean;
  evaluated_at: string;
}

export interface ObligationList {
  items: Obligation[];
  next_cursor?: string | null;
  evaluated_at: string;
  policy_version: string;
  policy_assumed: boolean;
}

export interface FieldLocationBody {
  from_station_id: string;
  toward_station_id: string;
  offset_start_m: number;
  offset_end_m: number;
}

export interface JobCreateBody {
  corridor_id: string;
  track_id: string;
  job_type: JobType;
  distance_start: number;
  distance_end: number;
  workers_min: number;
  workers_max: number;
  description: string;
  severity: Severity;
  evidence_reference?: string;
  field_location: FieldLocationBody;
  idempotency_key: string;
}

/** Stable-code error shape of every v1 4xx. */
export class ApiError extends Error {
  status: number;
  code: string;
  detail: string;
  constructor(status: number, code: string, detail: string) {
    super(`${code}: ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

function detailToString(detail: unknown): string {
  if (typeof detail === "string") return detail;
  try {
    return JSON.stringify(detail);
  } catch {
    return String(detail);
  }
}

async function request<T>(
  method: "GET" | "POST",
  path: string,
  persona: Persona | null,
  body?: unknown,
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (persona) {
    headers["X-Actor-Id"] = persona.id;
    headers["X-Actor-Role"] = persona.role;
  }
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    throw new ApiError(
      0,
      "BACKEND_UNREACHABLE",
      `Could not reach the backend at ${API_BASE_URL}.`,
    );
  }
  if (!res.ok) {
    let code = "HTTP_ERROR";
    let detail = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      if (j && typeof j === "object") {
        if (typeof j.code === "string") code = j.code;
        if (j.detail !== undefined) detail = detailToString(j.detail);
      }
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, code, detail);
  }
  return (await res.json()) as T;
}

const enc = encodeURIComponent;

export const api = {
  health: async (): Promise<boolean> => {
    try {
      const r = await fetch(`${API_BASE_URL}/health`, { cache: "no-store" });
      if (!r.ok) return false;
      const j = await r.json();
      return j?.status === "healthy";
    } catch {
      return false;
    }
  },

  createJob: (p: Persona, body: JobCreateBody) =>
    request<Job>("POST", "/v1/jobs", p, body),
  getJob: (id: string) => request<Job>("GET", `/v1/jobs/${enc(id)}`, null),
  history: (id: string) =>
    request<JobHistory>("GET", `/v1/jobs/${enc(id)}/history`, null),
  proposal: (id: string) =>
    request<Proposal>("GET", `/v1/jobs/${enc(id)}/proposal`, null),
  executions: (id: string) =>
    request<{ job_id: string; executions: Execution[] }>(
      "GET",
      `/v1/jobs/${enc(id)}/execution`,
      null,
    ),
  obligation: (id: string) =>
    request<Obligation>("GET", `/v1/jobs/${enc(id)}/obligation`, null),
  run: (runId: string) =>
    request<OptimizationRun>(
      "GET",
      `/v1/optimization-runs/${enc(runId)}`,
      null,
    ),

  optimize: (p: Persona) =>
    request<OptimizationResponse>(
      "POST",
      `/v1/corridors/${CORRIDOR_ID}/optimize-jobs`,
      p,
    ),

  approve: (p: Persona, id: string, runId: string) =>
    request<{ job: Job; message: string }>(
      "POST",
      `/v1/jobs/${enc(id)}/proposal/approve`,
      p,
      { expected_proposal_run_id: runId },
    ),
  reject: (p: Persona, id: string, runId: string, reason: string) =>
    request<{ job: Job; message: string }>(
      "POST",
      `/v1/jobs/${enc(id)}/proposal/reject`,
      p,
      { expected_proposal_run_id: runId, reason },
    ),
  postpone: (
    p: Persona,
    id: string,
    runId: string,
    reason: string,
    selectedDate: string,
  ) =>
    request<{ job: Job; message: string }>(
      "POST",
      `/v1/jobs/${enc(id)}/proposal/postpone`,
      p,
      {
        expected_proposal_run_id: runId,
        reason,
        selected_date: selectedDate,
      },
    ),
  release: (p: Persona, id: string, runId: string, reason: string) =>
    request<{ job: Job; message: string }>(
      "POST",
      `/v1/jobs/${enc(id)}/proposal/release`,
      p,
      { expected_proposal_run_id: runId, reason },
    ),

  startExecution: (
    p: Persona,
    id: string,
    runId: string,
    actualStartAt: string,
    before: Evidence[],
  ) =>
    request<{ job: Job; execution: Execution; message: string }>(
      "POST",
      `/v1/jobs/${enc(id)}/execution/start`,
      p,
      {
        expected_proposal_run_id: runId,
        actual_start_at: actualStartAt,
        before_work_evidence: before,
      },
    ),
  completeExecution: (
    p: Persona,
    id: string,
    executionId: string,
    actualEndAt: string,
    after: Evidence[],
  ) =>
    request<{ job: Job; execution: Execution; message: string }>(
      "POST",
      `/v1/jobs/${enc(id)}/execution/complete`,
      p,
      {
        execution_id: executionId,
        actual_end_at: actualEndAt,
        after_work_evidence: after,
      },
    ),
  notCompleted: (
    p: Persona,
    id: string,
    executionId: string,
    actualEndAt: string,
    reason: string,
    evidence: Evidence[],
  ) =>
    request<{ job: Job; execution: Execution; message: string }>(
      "POST",
      `/v1/jobs/${enc(id)}/execution/not-completed`,
      p,
      {
        execution_id: executionId,
        actual_end_at: actualEndAt,
        reason,
        evidence,
      },
    ),
};

/** Follow next_cursor until null (short/empty pages are legal). */
export async function listAllJobs(status?: JobStatus): Promise<Job[]> {
  const out: Job[] = [];
  let cursor: string | null | undefined = null;
  do {
    const qs = new URLSearchParams({ limit: "200" });
    if (status) qs.set("status", status);
    if (cursor) qs.set("cursor", cursor);
    const page: JobList = await request<JobList>(
      "GET",
      `/v1/jobs?${qs.toString()}`,
      null,
    );
    out.push(...page.items);
    cursor = page.next_cursor;
  } while (cursor);
  return out;
}

export interface AllObligations {
  items: Obligation[];
  evaluated_at: string;
  policy_version: string;
  policy_assumed: boolean;
}

export async function listAllObligations(): Promise<AllObligations> {
  const items: Obligation[] = [];
  let cursor: string | null | undefined = null;
  let meta = { evaluated_at: "", policy_version: "", policy_assumed: true };
  do {
    const qs = new URLSearchParams({ limit: "200" });
    if (cursor) qs.set("cursor", cursor);
    const page: ObligationList = await request<ObligationList>(
      "GET",
      `/v1/obligations?${qs.toString()}`,
      null,
    );
    items.push(...page.items);
    meta = {
      evaluated_at: page.evaluated_at,
      policy_version: page.policy_version,
      policy_assumed: page.policy_assumed,
    };
    cursor = page.next_cursor;
  } while (cursor);
  return { items, ...meta };
}

/* ---------- defensive readers for non-contract fields ---------- */

export function bc(job: Job): Record<string, unknown> {
  return job.block_candidate ?? {};
}

export function bcMeta(job: Job): Record<string, unknown> {
  const m = bc(job).metadata;
  return m && typeof m === "object" ? (m as Record<string, unknown>) : {};
}

export function sectionOf(job: Job): string | null {
  const s = bc(job).section_id;
  return typeof s === "string" ? s : null;
}

export function severityOf(job: Job): Severity | null {
  const s = bcMeta(job).reported_severity;
  return s === "NONE" || s === "MINOR" || s === "MODERATE" || s === "CRITICAL"
    ? s
    : null;
}

export const JOB_TYPE_LABEL: Record<string, string> = {
  TRACK_RENEWAL: "Track renewal",
  BALLAST_TAMPING: "Ballast tamping",
  OHE_MAINTENANCE: "OHE maintenance",
  SIGNALLING_INTERLOCKING: "Signalling / interlocking",
  ROUTINE_INSPECTION: "Routine inspection",
  EMERGENCY_REPAIR: "Emergency repair",
};

export const JOB_TYPE_DEFAULT_MIN: Record<string, number> = {
  TRACK_RENEWAL: 240,
  BALLAST_TAMPING: 120,
  OHE_MAINTENANCE: 90,
  SIGNALLING_INTERLOCKING: 90,
  ROUTINE_INSPECTION: 45,
  EMERGENCY_REPAIR: 90,
};

/** Display names for lifecycle statuses. `notified` is NOT a sent notification. */
export const STATUS_LABEL: Record<JobStatus, string> = {
  reported: "Reported",
  scheduled: "Proposed",
  notified: "Approved · committed",
  in_progress: "In progress",
  completed: "Completed",
};

export const STATUS_ORDER: JobStatus[] = [
  "reported",
  "scheduled",
  "notified",
  "in_progress",
  "completed",
];

export function scoreBand(v: number): "HIGH" | "MEDIUM" | "LOW" {
  return v >= 0.75 ? "HIGH" : v >= 0.5 ? "MEDIUM" : "LOW";
}
