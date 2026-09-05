/**
 * Data-fetching layer for the Pashupatastra command center dashboard.
 *
 * Connects directly to the canonical FastAPI optimizer backend:
 *   POST http://127.0.0.1:8000/optimize
 *
 * If the live backend is available, optimizes in real time and sets dataSource = "LIVE_API".
 * If the backend is unavailable or offline, gracefully falls back to canonical fixture data
 * and sets dataSource = "FIXTURE".
 */

import type {
  OptimizationResult,
  OptimizationRequest,
  DashboardData,
  EnrichedScheduledBlock,
  EnrichedUnscheduledBlock,
  DataSourceType,
} from "@/types/contracts";

import fixtureResult from "@/data/milestone1_result.json";
import fixtureRequest from "@/data/corridor_a_blocks.json";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

/**
 * Fetch optimization data: tries live FastAPI backend first,
 * falls back to canonical fixture if unavailable.
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
      return enrichData(
        result,
        request,
        "LIVE_API",
        latencyMs,
        `${API_BASE_URL}/optimize`
      );
    }

    console.warn(
      `Backend API responded with status ${res.status}. Falling back to canonical fixture.`
    );
  } catch (err) {
    console.warn(
      "Live backend optimizer unreachable or timed out. Using canonical fixture data.",
      err instanceof Error ? err.message : err
    );
  }

  // Fallback to canonical fixture
  const fallbackResult = fixtureResult as unknown as OptimizationResult;
  return enrichData(
    fallbackResult,
    request,
    "FIXTURE",
    undefined,
    "local-fixture: corridor_a_blocks.json"
  );
}

/**
 * Join optimization result blocks with request candidates and rejection audit trail.
 */
export function enrichData(
  result: OptimizationResult,
  request: OptimizationRequest,
  dataSource: DataSourceType = "FIXTURE",
  apiLatencyMs?: number,
  apiEndpoint?: string
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
    dataSource,
    apiLatencyMs,
    apiEndpoint,
  };
}
