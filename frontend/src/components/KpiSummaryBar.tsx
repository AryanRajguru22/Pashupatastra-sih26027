"use client";

import React, { useMemo } from "react";
import type { DashboardData } from "@/types/contracts";

interface KpiSummaryBarProps {
  data: DashboardData;
  onOpenBlockList?: () => void;
}

/**
 * "Current Plan" panel - a compact vertical summary of the active schedule.
 * Every value here is computed from the real OptimizationResult/Request,
 * identically to the previous 5-card KPI row this replaces.
 */
export default function KpiSummaryBar({ data, onOpenBlockList }: KpiSummaryBarProps) {
  const { result, request, dataSource } = data;

  const totalCandidates = request.candidates?.length || 0;
  const scheduledCount = result.scheduled_blocks?.length || 0;

  const horizonMinutes = request.horizon_minutes || 1440;
  const trackCount = request.tracks?.length || 1;
  const totalCorridorTrackMinutes = horizonMinutes * trackCount;

  const totalMaintenanceMinutes = useMemo(() => {
    return (result.scheduled_blocks || []).reduce(
      (sum, sb) => sum + Math.max(0, sb.end_minute - sb.start_minute),
      0
    );
  }, [result.scheduled_blocks]);

  const assetAvailabilityPct = useMemo(() => {
    if (totalCorridorTrackMinutes <= 0) return 100;
    const availableMins = Math.max(0, totalCorridorTrackMinutes - totalMaintenanceMinutes);
    return (availableMins / totalCorridorTrackMinutes) * 100;
  }, [totalCorridorTrackMinutes, totalMaintenanceMinutes]);

  const totalPotentialPriority = useMemo(() => {
    return (request.candidates || []).reduce((sum, c) => sum + (c.priority_score || 0), 0);
  }, [request.candidates]);

  const totalPotentialRisk = useMemo(() => {
    return (request.candidates || []).reduce((sum, c) => sum + (c.risk_score || 0), 0);
  }, [request.candidates]);

  const priorityScoreScheduled = result.total_priority_scheduled || 0;
  const riskMitigated = result.total_risk_mitigated || 0;

  const priorityPct =
    totalPotentialPriority > 0
      ? Math.min(100, (priorityScoreScheduled / totalPotentialPriority) * 100)
      : 0;

  const riskMitigatedPct =
    totalPotentialRisk > 0 ? Math.min(100, (riskMitigated / totalPotentialRisk) * 100) : 0;

  const trackConflictCount = useMemo(() => {
    let conflicts = 0;
    const blocks = result.scheduled_blocks || [];
    for (let i = 0; i < blocks.length; i++) {
      for (let j = i + 1; j < blocks.length; j++) {
        if (blocks[i].track_id === blocks[j].track_id) {
          const overlap =
            Math.max(blocks[i].start_minute, blocks[j].start_minute) <
            Math.min(blocks[i].end_minute, blocks[j].end_minute);
          if (overlap) conflicts++;
        }
      }
    }
    return conflicts;
  }, [result.scheduled_blocks]);

  const solveTimeMs = Math.round((result.solve_time_seconds || 0) * 1000);
  const isOptimal = result.status === "OPTIMAL" || result.status === "FEASIBLE";

  // Which committed blocks currently hold a possession, for the small pill row.
  const possessionPills = useMemo(
    () => (result.scheduled_blocks || []).filter((b) => b.is_committed).slice(0, 3),
    [result.scheduled_blocks]
  );

  return (
    <div className="glass-panel rounded-2xl p-5 w-72 flex flex-col">
      <div className="flex items-center justify-between pb-3 border-b border-white/[0.06]">
        <span className="font-mono-data text-[0.65rem] tracking-widest text-muted uppercase">
          Current Plan
        </span>
        <span
          className={`flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-[0.65rem] font-mono-data font-bold tracking-wider ${
            isOptimal
              ? "bg-accent-cyan/15 border-accent-cyan/30 text-accent-cyan"
              : "bg-red-400/15 border-red-400/30 text-red-300"
          }`}
        >
          <span className={`w-1.5 h-1.5 rounded-full ${isOptimal ? "bg-accent-cyan" : "bg-red-400"}`} />
          {result.status}
        </span>
      </div>

      <button
        onClick={onOpenBlockList}
        className="w-full mt-4 flex items-baseline gap-2 text-left group p-1.5 -mx-1.5 rounded-lg hover:bg-white/[0.03] transition"
      >
        <span className="font-display text-4xl text-white font-normal group-hover:text-accent-cyan transition">
          {scheduledCount}
        </span>
        <span className="font-mono-data text-base text-muted">/ {totalCandidates}</span>
        <span className="font-mono-data text-[0.65rem] text-muted ml-auto uppercase tracking-wide">
          slotted
        </span>
      </button>

      <div className="mt-3 space-y-2 pt-3 border-t border-white/[0.04] text-xs">
        <div className="flex items-center justify-between">
          <span className="text-muted">Priority Captured</span>
          <div className="flex items-center gap-1.5 font-mono-data text-accent-cyan font-semibold">
            <span>{priorityPct.toFixed(0)}%</span>
            <div className="w-12 h-1 rounded-full bg-white/10 overflow-hidden">
              <div className="h-full bg-accent-cyan rounded-full" style={{ width: `${priorityPct}%` }} />
            </div>
          </div>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-muted">Risk Mitigated</span>
          <div className="flex items-center gap-1.5 font-mono-data text-white font-semibold">
            <span>{riskMitigated.toFixed(2)} pts</span>
            <span className="text-[0.65rem] text-accent-cyan">({riskMitigatedPct.toFixed(0)}%)</span>
          </div>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-muted">Track Availability</span>
          <div className="flex items-center gap-1.5 font-mono-data text-accent-amber font-semibold">
            <span>{assetAvailabilityPct.toFixed(0)}%</span>
          </div>
        </div>
      </div>

      {possessionPills.length > 0 && (
        <div className="mt-3 pt-2 border-t border-white/[0.04] flex items-center flex-wrap gap-1.5">
          <span className="text-[0.62rem] font-mono-data text-muted">COMMITTED:</span>
          {possessionPills.map((b) => (
            <span
              key={b.block_id}
              className="px-1.5 py-0.5 rounded bg-accent-amber/15 text-accent-amber font-mono-data text-[0.65rem] font-bold"
            >
              {b.block_id}
            </span>
          ))}
        </div>
      )}

      <div className="mt-3 pt-3 border-t border-white/[0.06] flex items-center justify-between text-[0.65rem] font-mono-data text-muted">
        <span>OR-TOOLS CP-SAT</span>
        <span
          className={trackConflictCount === 0 ? "text-accent-cyan" : "text-red-300"}
          title={
            trackConflictCount === 0
              ? "No overlapping blocks on the same track"
              : `${trackConflictCount} overlap(s) detected`
          }
        >
          {solveTimeMs}ms &middot; {dataSource === "LIVE_API" ? "live" : "fixture"}
        </span>
      </div>
    </div>
  );
}
