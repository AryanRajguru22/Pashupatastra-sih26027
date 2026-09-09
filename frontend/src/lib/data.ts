/**
 * Data-fetching layer for the Pashupatastra command center dashboard.
 *
 * Connects directly to the canonical FastAPI optimizer backend:
 *   POST http://127.0.0.1:8000/optimize
 *
 * TWO INDEPENDENT FACTS, NEVER CONFLATED:
 *
 *   connectivity    ONLINE / DEGRADED / OFFLINE - did the backend answer?
 *   dataProvenance  where the DATA came from.
 *
 * These used to be one field, and a reachable backend was labelled
 * "LIVE_API", which the header rendered as "LIVE BACKEND: CONNECTED"
 * while the request body was a checked-in synthetic fixture. A live
 * connection is not live data.
 *
 * Provenance is decided here, on the frontend, because the frontend is
 * what chooses the input: every request below is built from
 * src/data/corridor_a_blocks.json, so the only honest provenance value
 * this module can currently emit is SYNTHETIC_FIXTURE - whether or not
 * the backend responds. The backend is never asked to assert
 * provenance; it receives an OptimizationRequest and cannot know
 * whether its contents are real.
 */

import type {
  OptimizationResult,
  OptimizationRequest,
  ConnectivityStatus,
  DataProvenance,
  DashboardData,
  EnrichedScheduledBlock,
  EnrichedUnscheduledBlock,
  DisruptionEvent,
  RecoveryResponse,
} from "@/types/contracts";

import fixtureResult from "@/data/milestone1_result.json";
import fixtureRequest from "@/data/corridor_a_blocks.json";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

/**
 * Provenance of everything this module currently sends to the optimizer.
 *
 * The dashboard's request body is always the checked-in fixture below,
 * so this is a statement of fact, not a default. When a real or
 * realistic-static corridor dataset is introduced, provenance must be
 * carried on that dataset and threaded through here - it must not be
 * inferred from whether the backend replied.
 */
const DASHBOARD_INPUT_PROVENANCE: DataProvenance = "SYNTHETIC_FIXTURE";

/**
 * Checked-in fixtures have no meaningful generation timestamp, so
 * freshness is genuinely UNKNOWN and is reported as such rather than
 * being filled in with the current time.
 */
const FIXTURE_GENERATED_AT: string | undefined = undefined;

/**
 * Fetch optimization data: tries the FastAPI backend first, falls back
 * to the canonical fixture result if it is unreachable or errors.
 *
 * Returns connectivity reflecting what the backend actually did, and
 * provenance reflecting what was actually sent - never one inferred
 * from the other.
 */
export async function fetchOptimizationData(): Promise<DashboardData> {
  const startTime = Date.now();
  const request = fixtureRequest as unknown as OptimizationRequest;

  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 4000);

    const res = await fetch(`${API_BASE_URL}/optimize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
      cache: "no-store",
      signal: controller.signal,
    });

    clearTimeout(timeoutId);

    if (res.ok) {
      const result: OptimizationResult = await res.json();
      const latencyMs = Date.now() - startTime;
      // The backend answered, so connectivity is ONLINE. The data it
      // solved is still the synthetic fixture we posted, so provenance
      // is unchanged.
      return enrichData(
        result,
        request,
        "ONLINE",
        DASHBOARD_INPUT_PROVENANCE,
        {
          apiLatencyMs: latencyMs,
          apiEndpoint: `${API_BASE_URL}/optimize`,
          dataGeneratedAt: FIXTURE_GENERATED_AT,
        }
      );
    }

    console.warn(
      `Backend API responded with status ${res.status}. Falling back to canonical fixture.`
    );

    // Reachable but the operation failed: the displayed plan is a
    // pre-computed fixture result, not this backend's answer.
    return enrichData(
      fixtureResult as unknown as OptimizationResult,
      request,
      "DEGRADED",
      DASHBOARD_INPUT_PROVENANCE,
      {
        apiEndpoint: "local-fixture: milestone1_result.json",
        dataGeneratedAt: FIXTURE_GENERATED_AT,
        connectivityDetail: `Backend responded with status ${res.status}; showing pre-computed fixture result.`,
      }
    );
  } catch (err) {
    console.warn(
      "Live backend optimizer unreachable or timed out. Using canonical fixture data.",
      err instanceof Error ? err.message : err
    );

    return enrichData(
      fixtureResult as unknown as OptimizationResult,
      request,
      "OFFLINE",
      DASHBOARD_INPUT_PROVENANCE,
      {
        apiEndpoint: "local-fixture: milestone1_result.json",
        dataGeneratedAt: FIXTURE_GENERATED_AT,
        connectivityDetail:
          err instanceof Error
            ? err.message
            : "Backend unreachable or timed out.",
      }
    );
  }
}

/** Optional context recorded alongside an enriched dashboard payload. */
export interface EnrichmentContext {
  apiLatencyMs?: number;
  apiEndpoint?: string;
  dataGeneratedAt?: string;
  connectivityDetail?: string;
}

/**
 * Join optimization result blocks with request candidates and rejection
 * audit trail, and record the connectivity/provenance/freshness facts
 * the UI renders.
 */
export function enrichData(
  result: OptimizationResult,
  request: OptimizationRequest,
  connectivity: ConnectivityStatus,
  dataProvenance: DataProvenance,
  context: EnrichmentContext = {}
): DashboardData {
  const candidateMap = new Map(
    (request.candidates || []).map((c) => [c.block_id, c])
  );

  const enrichedScheduled: EnrichedScheduledBlock[] = (
    result.scheduled_blocks || []
  ).map((sb) => ({
    ...sb,
    candidate: candidateMap.get(sb.block_id) ?? null,
    rejectionReason: null,
  }));

  const enrichedUnscheduled: EnrichedUnscheduledBlock[] = (
    result.unscheduled_blocks || []
  ).map((ub) => {
    const candidate = candidateMap.get(ub.block_id) ?? ub;
    const rejectionReason =
      result.rejection_reasons?.[ub.block_id] ||
      "Excluded by CP-SAT solver: capacity or objective trade-off";

    return {
      ...candidate,
      candidate,
      rejectionReason,
    };
  });

  return {
    result,
    request,
    enrichedScheduled,
    enrichedUnscheduled,
    connectivity,
    dataProvenance,
    dataGeneratedAt: context.dataGeneratedAt,
    connectivityDetail: context.connectivityDetail,
    apiLatencyMs: context.apiLatencyMs,
    apiEndpoint: context.apiEndpoint,
  };
}

/**
 * Outcome of a POST /recover call. Recovery has no fixture fallback: it is
 * an explicitly live "what-if" action, so an unreachable/erroring backend
 * must be reported to the caller rather than silently substituted.
 */
export type RecoveryOutcome =
  | { ok: true; data: RecoveryResponse }
  | { ok: false; reason: "unreachable"; message: string }
  | { ok: false; reason: "api_error"; message: string };

/**
 * Apply a disruption to `request` and recover via the real backend
 * POST /recover endpoint. Never mocked and never falls back to fixture
 * data - a failure here is reported as-is.
 */
export async function triggerRecovery(
  request: OptimizationRequest,
  disruption: DisruptionEvent
): Promise<RecoveryOutcome> {
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 8000);

    const res = await fetch(`${API_BASE_URL}/recover`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request, disruption }),
      cache: "no-store",
      signal: controller.signal,
    });

    clearTimeout(timeoutId);

    if (res.ok) {
      const data: RecoveryResponse = await res.json();
      return { ok: true, data };
    }

    let message = `Recovery request failed with status ${res.status}.`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") {
        message = body.detail;
      }
    } catch {
      // Response body wasn't JSON; keep the generic status message.
    }
    return { ok: false, reason: "api_error", message };
  } catch (err) {
    return {
      ok: false,
      reason: "unreachable",
      message:
        err instanceof Error
          ? err.message
          : "Live backend unreachable or timed out.",
    };
  }
}
