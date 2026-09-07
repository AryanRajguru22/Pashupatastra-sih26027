/**
 * Pure Command Center metric calculations, derived only from real
 * OptimizationRequest/OptimizationResult fields (contracts/schemas.py).
 * Mirrors the corridor-capacity / risk-neutralized formulas already used
 * in PlanView.tsx, kept as a separate module so PlanView's own working
 * calculation is not touched.
 */

import type { OptimizationRequest, OptimizationResult } from "@/types/contracts";

export function corridorCapacityPct(
  request: OptimizationRequest,
  result: OptimizationResult
): number {
  const horizon = request.horizon_minutes || 1440;
  const trackCount = request.tracks?.length || 1;
  const totalCorridorTrackMinutes = horizon * trackCount;
  const totalMaintenanceMinutes = (result.scheduled_blocks || []).reduce(
    (sum, sb) => sum + Math.max(0, sb.end_minute - sb.start_minute),
    0
  );
  return totalCorridorTrackMinutes > 0
    ? ((totalCorridorTrackMinutes - totalMaintenanceMinutes) / totalCorridorTrackMinutes) * 100
    : 100;
}

export function riskNeutralizedPct(
  request: OptimizationRequest,
  result: OptimizationResult
): number {
  const totalPotentialRisk = (request.candidates || []).reduce(
    (sum, c) => sum + (c.risk_score || 0),
    0
  );
  return totalPotentialRisk > 0
    ? Math.min(100, ((result.total_risk_mitigated || 0) / totalPotentialRisk) * 100)
    : 0;
}

export interface WorkTypeStat {
  workType: string;
  total: number;
  scheduled: number;
}

export function workTypeBreakdown(
  request: OptimizationRequest,
  result: OptimizationResult
): WorkTypeStat[] {
  const scheduledByType = new Map<string, number>();
  for (const sb of result.scheduled_blocks || []) {
    scheduledByType.set(sb.work_type, (scheduledByType.get(sb.work_type) || 0) + 1);
  }
  const totalByType = new Map<string, number>();
  for (const c of request.candidates || []) {
    totalByType.set(c.work_type, (totalByType.get(c.work_type) || 0) + 1);
  }
  return Array.from(totalByType.entries())
    .map(([workType, total]) => ({
      workType,
      total,
      scheduled: scheduledByType.get(workType) || 0,
    }))
    .sort((a, b) => b.total - a.total);
}
