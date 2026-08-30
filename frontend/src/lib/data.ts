/**
 * Data-fetching layer for the Pashupatastra command center dashboard.
 *
 * Stage 1–3 Prototype Mode:
 * Loads static JSON fixtures (corridor_a_blocks.json + milestone1_result.json).
 *
 * Milestone 2 Live Backend Integration Mode:
 * Ready to invoke the FastAPI optimizer endpoint:
 *   POST http://127.0.0.1:8000/optimize with OptimizationRequest payload.
 */

import type {
  OptimizationResult,
  OptimizationRequest,
  DashboardData,
  EnrichedScheduledBlock,
  EnrichedUnscheduledBlock,
} from "@/types/contracts";

import resultJson from "@/data/milestone1_result.json";
import requestJson from "@/data/corridor_a_blocks.json";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

/**
 * Fetch optimization data from live FastAPI backend if available,
 * or fallback cleanly to Stage 1 static fixtures.
 */
export async function fetchOptimizationData(
  useLiveBackend: boolean = false
): Promise<DashboardData> {
  if (useLiveBackend) {
    try {
      const request = requestJson as unknown as OptimizationRequest;
      const res = await fetch(`${API_BASE_URL}/optimize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
        cache: "no-store",
      });

      if (res.ok) {
        const result: OptimizationResult = await res.json();
        return enrichData(result, request);
      }
      console.warn(
        `Backend API returned status ${res.status}. Falling back to Stage-1 fixtures.`
      );
    } catch (err) {
      console.warn(
        "Could not connect to live backend optimizer. Falling back to Stage-1 fixtures.",
        err
      );
    }
  }

  // Stage 1 Fixture mode (Default)
  const result = resultJson as unknown as OptimizationResult;
  const request = requestJson as unknown as OptimizationRequest;

  return enrichData(result, request);
}

/**
 * Join result blocks with their source BlockCandidate data and
 * explainability entries.
 */
export function enrichData(
  result: OptimizationResult,
  request: OptimizationRequest
): DashboardData {
  const candidateMap = new Map(
    request.block_candidates.map((c) => [c.block_id, c])
  );
  const explainMap = new Map(
    result.explainability.map((e) => [e.block_id, e])
  );

  const enrichedScheduled: EnrichedScheduledBlock[] =
    result.scheduled_blocks.map((sb) => ({
      ...sb,
      candidate: candidateMap.get(sb.block_id) ?? null,
      explanation: explainMap.get(sb.block_id) ?? null,
    }));

  const enrichedUnscheduled: EnrichedUnscheduledBlock[] =
    result.unscheduled_blocks.map((ub) => ({
      ...ub,
      candidate: candidateMap.get(ub.block_id) ?? null,
    }));

  return {
    result,
    request,
    enrichedScheduled,
    enrichedUnscheduled,
  };
}
