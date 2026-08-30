/**
 * Data-fetching layer for the Pashupatastra dashboard.
 *
 * Currently loads static JSON fixtures. When the backend API is ready,
 * replace the body of `fetchOptimizationData()` with a fetch() call —
 * no other code in the app should need to change.
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

/**
 * Fetch and enrich the optimization data.
 *
 * Today: returns static JSON.
 * Tomorrow: `fetch("http://127.0.0.1:8000/optimize", { method: "POST", ... })`
 */
export async function fetchOptimizationData(): Promise<DashboardData> {
  // Static import — will be replaced with API call
  const result = resultJson as unknown as OptimizationResult;
  const request = requestJson as unknown as OptimizationRequest;

  return enrichData(result, request);
}

/**
 * Join result blocks with their source BlockCandidate data and
 * explainability entries.
 */
function enrichData(
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
