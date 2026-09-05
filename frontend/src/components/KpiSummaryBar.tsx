"use client";

import React, { useMemo } from "react";
import type { DashboardData } from "@/types/contracts";

interface KpiSummaryBarProps {
  data: DashboardData;
}

export default function KpiSummaryBar({ data }: KpiSummaryBarProps) {
  const { result, request, dataSource } = data;

  const totalCandidates = request.candidates?.length || 0;
  const scheduledCount = result.scheduled_blocks?.length || 0;
  const rejectedCount = result.unscheduled_blocks?.length || 0;

  // Track availability calculation based on horizon minutes and tracks
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
    const availableMins = Math.max(
      0,
      totalCorridorTrackMinutes - totalMaintenanceMinutes
    );
    return (availableMins / totalCorridorTrackMinutes) * 100;
  }, [totalCorridorTrackMinutes, totalMaintenanceMinutes]);

  // Priority and Risk aggregations
  const totalPotentialPriority = useMemo(() => {
    return (request.candidates || []).reduce(
      (sum, c) => sum + (c.priority_score || 0),
      0
    );
  }, [request.candidates]);

  const totalPotentialRisk = useMemo(() => {
    return (request.candidates || []).reduce(
      (sum, c) => sum + (c.risk_score || 0),
      0
    );
  }, [request.candidates]);

  const priorityScoreScheduled = result.total_priority_scheduled || 0;
  const riskMitigated = result.total_risk_mitigated || 0;

  const priorityPct =
    totalPotentialPriority > 0
      ? Math.min(100, (priorityScoreScheduled / totalPotentialPriority) * 100)
      : 0;

  const riskMitigatedPct =
    totalPotentialRisk > 0
      ? Math.min(100, (riskMitigated / totalPotentialRisk) * 100)
      : 0;

  // Pairwise track collision audit (Hard constraint check: should always be 0)
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

  return (
    <div className="w-full grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
      {/* 1. Track Availability KPI */}
      <div className="bg-[#0c1120] border border-[#1e293b] hover:border-[#2a3c5a] transition-all rounded-lg p-3.5 flex flex-col justify-between shadow-sm relative overflow-hidden group">
        <div className="absolute top-0 right-0 w-24 h-24 bg-emerald-500/5 rounded-full blur-xl pointer-events-none" />
        <div>
          <div className="flex items-center justify-between text-[#64748b] mb-1.5">
            <span className="text-[10px] font-semibold tracking-wider uppercase">
              Track Availability
            </span>
            <svg
              className="w-4 h-4 text-emerald-400"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={1.8}
                d="M13 10V3L4 14h7v7l9-11h-7z"
              />
            </svg>
          </div>
          <div className="flex items-baseline gap-1.5">
            <span className="text-2xl font-bold font-mono text-[#f8fafc]">
              {assetAvailabilityPct.toFixed(1)}
            </span>
            <span className="text-sm font-mono text-emerald-400 font-semibold">
              %
            </span>
          </div>
        </div>

        <div className="mt-3 pt-2.5 border-t border-[#1e293b]/70 flex items-center justify-between text-[11px] text-[#64748b]">
          <span>Maint. windows</span>
          <span className="text-[#94a3b8] font-mono">
            {totalMaintenanceMinutes} min / {totalCorridorTrackMinutes} min
          </span>
        </div>
      </div>

      {/* 2. Priority Scheduled */}
      <div className="bg-[#0c1120] border border-[#1e293b] hover:border-[#2a3c5a] transition-all rounded-lg p-3.5 flex flex-col justify-between shadow-sm relative overflow-hidden group">
        <div className="absolute top-0 right-0 w-24 h-24 bg-blue-500/5 rounded-full blur-xl pointer-events-none" />
        <div>
          <div className="flex items-center justify-between text-[#64748b] mb-1.5">
            <span className="text-[10px] font-semibold tracking-wider uppercase">
              Priority Captured
            </span>
            <svg
              className="w-4 h-4 text-blue-400"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={1.8}
                d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"
              />
            </svg>
          </div>
          <div className="flex items-baseline gap-1.5">
            <span className="text-2xl font-bold font-mono text-[#f8fafc]">
              {priorityScoreScheduled.toFixed(2)}
            </span>
            <span className="text-xs font-mono text-[#64748b]">
              / {totalPotentialPriority.toFixed(2)}
            </span>
          </div>
        </div>

        <div className="mt-3 pt-2.5 border-t border-[#1e293b]/70 flex items-center justify-between text-[11px] text-[#64748b]">
          <span>Efficiency rate</span>
          <span className="text-blue-400 font-mono font-medium">
            {priorityPct.toFixed(0)}% captured
          </span>
        </div>
      </div>

      {/* 3. Risk Mitigated */}
      <div className="bg-[#0c1120] border border-[#1e293b] hover:border-[#2a3c5a] transition-all rounded-lg p-3.5 flex flex-col justify-between shadow-sm relative overflow-hidden group">
        <div className="absolute top-0 right-0 w-24 h-24 bg-amber-500/5 rounded-full blur-xl pointer-events-none" />
        <div>
          <div className="flex items-center justify-between text-[#64748b] mb-1.5">
            <span className="text-[10px] font-semibold tracking-wider uppercase">
              Risk Mitigated
            </span>
            <svg
              className="w-4 h-4 text-amber-400"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={1.8}
                d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
              />
            </svg>
          </div>
          <div className="flex items-baseline gap-1.5">
            <span className="text-2xl font-bold font-mono text-[#f8fafc]">
              {riskMitigated.toFixed(2)}
            </span>
            <span className="text-xs font-mono text-amber-400 font-medium">
              pts
            </span>
          </div>
        </div>

        <div className="mt-3 pt-2.5 border-t border-[#1e293b]/70 flex items-center justify-between text-[11px] text-[#64748b]">
          <span>Total corridor risk</span>
          <span className="text-amber-400 font-mono font-medium">
            {riskMitigatedPct.toFixed(0)}% resolved
          </span>
        </div>
      </div>

      {/* 4. Blocks Scheduled vs Rejected */}
      <div className="bg-[#0c1120] border border-[#1e293b] hover:border-[#2a3c5a] transition-all rounded-lg p-3.5 flex flex-col justify-between shadow-sm relative overflow-hidden group">
        <div className="absolute top-0 right-0 w-24 h-24 bg-cyan-500/5 rounded-full blur-xl pointer-events-none" />
        <div>
          <div className="flex items-center justify-between text-[#64748b] mb-1.5">
            <span className="text-[10px] font-semibold tracking-wider uppercase">
              Scheduled Blocks
            </span>
            <span
              className={`text-[10px] font-mono px-1.5 py-0.2 rounded font-semibold uppercase ${
                result.status === "OPTIMAL" || result.status === "FEASIBLE"
                  ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                  : "bg-red-500/10 text-red-400 border border-red-500/20"
              }`}
            >
              {result.status}
            </span>
          </div>
          <div className="flex items-baseline gap-1.5">
            <span className="text-2xl font-bold font-mono text-emerald-400">
              {scheduledCount}
            </span>
            <span className="text-xs font-mono text-[#64748b]">
              / {totalCandidates} candidates
            </span>
          </div>
        </div>

        <div className="mt-3 pt-2.5 border-t border-[#1e293b]/70 flex items-center justify-between text-[11px] text-[#64748b]">
          <span>Unscheduled / Excluded</span>
          <span className="text-red-400 font-mono font-medium">
            {rejectedCount} blocks
          </span>
        </div>
      </div>

      {/* 5. Solver Audit & Data Connection */}
      <div className="bg-[#0c1120] border border-[#1e293b] hover:border-[#2a3c5a] transition-all rounded-lg p-3.5 flex flex-col justify-between shadow-sm relative overflow-hidden group">
        <div
          className={`absolute top-0 right-0 w-24 h-24 ${
            dataSource === "LIVE_API" ? "bg-emerald-500/5" : "bg-amber-500/5"
          } rounded-full blur-xl pointer-events-none`}
        />
        <div>
          <div className="flex items-center justify-between text-[#64748b] mb-1.5">
            <span className="text-[10px] font-semibold tracking-wider uppercase">
              Pipeline Integration
            </span>
            <div className="flex items-center gap-1.5">
              <div
                className={`w-2 h-2 rounded-full ${
                  dataSource === "LIVE_API"
                    ? "bg-emerald-400 animate-pulse"
                    : "bg-amber-400"
                }`}
              />
              <span
                className={`text-[10px] font-mono font-semibold ${
                  dataSource === "LIVE_API"
                    ? "text-emerald-400"
                    : "text-amber-400"
                }`}
              >
                {dataSource === "LIVE_API" ? "LIVE BACKEND" : "FIXTURE"}
              </span>
            </div>
          </div>
          <div className="flex items-baseline gap-1.5">
            <span className="text-2xl font-bold font-mono text-[#f8fafc]">
              {solveTimeMs}
            </span>
            <span className="text-xs font-mono text-[#64748b]">ms solve</span>
          </div>
        </div>

        <div className="mt-3 pt-2.5 border-t border-[#1e293b]/70 flex items-center justify-between text-[11px] text-[#64748b]">
          <span>Safety conflicts</span>
          <span
            className={`font-mono font-medium ${
              trackConflictCount === 0 ? "text-emerald-400" : "text-red-400"
            }`}
          >
            {trackConflictCount === 0 ? "0 (Constraint Safe)" : `${trackConflictCount} VIOLATIONS`}
          </span>
        </div>
      </div>
    </div>
  );
}
