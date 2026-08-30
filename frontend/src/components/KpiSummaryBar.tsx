"use client";

import React, { useMemo } from "react";
import type { DashboardData } from "@/types/contracts";

interface KpiSummaryBarProps {
  data: DashboardData;
}

export default function KpiSummaryBar({ data }: KpiSummaryBarProps) {
  const { result, request } = data;
  const kpis = result.kpis;
  const totalCandidates = request.block_candidates.length;
  const scheduledCount = result.scheduled_blocks.length;
  const rejectedCount = result.unscheduled_blocks.length;

  // Calculate track-level scheduled counts
  const trackCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    result.scheduled_blocks.forEach((sb) => {
      counts[sb.track_id] = (counts[sb.track_id] || 0) + 1;
    });
    return counts;
  }, [result.scheduled_blocks]);

  // Calculate total risk score available from candidates vs resolved
  const totalRiskScore = useMemo(() => {
    return request.block_candidates.reduce(
      (sum, c) => sum + (c.risk_score || 0),
      0
    );
  }, [request.block_candidates]);

  const riskReductionPct =
    totalRiskScore > 0
      ? Math.min(100, (kpis.risk_reduction_score / totalRiskScore) * 100)
      : 100;

  const scheduledPct =
    totalCandidates > 0
      ? Math.round((scheduledCount / totalCandidates) * 100)
      : 0;

  // Calculate pairwise track overlap conflicts dynamically from scheduled blocks
  const trackConflictCount = useMemo(() => {
    let conflicts = 0;
    const blocks = result.scheduled_blocks;
    for (let i = 0; i < blocks.length; i++) {
      for (let j = i + 1; j < blocks.length; j++) {
        if (blocks[i].track_id === blocks[j].track_id) {
          const startA = new Date(blocks[i].start).getTime();
          const endA = new Date(blocks[i].end).getTime();
          const startB = new Date(blocks[j].start).getTime();
          const endB = new Date(blocks[j].end).getTime();
          if (Math.max(startA, startB) < Math.min(endA, endB)) {
            conflicts++;
          }
        }
      }
    }
    return conflicts;
  }, [result.scheduled_blocks]);

  const isSolverFeasible =
    result.status === "OPTIMAL" || result.status === "FEASIBLE";

  return (
    <div className="w-full grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
      {/* 1. Asset Availability KPI */}
      <div className="bg-[#0c1120] border border-[#1e293b] hover:border-[#2a3c5a] transition-all rounded-lg p-3.5 flex flex-col justify-between shadow-sm relative overflow-hidden group">
        <div className="absolute top-0 right-0 w-24 h-24 bg-emerald-500/5 rounded-full blur-xl pointer-events-none" />
        <div>
          <div className="flex items-center justify-between text-[#64748b] mb-1.5">
            <span className="text-[10px] font-semibold tracking-wider uppercase">
              Asset Availability
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
              {kpis.asset_availability_pct.toFixed(1)}
            </span>
            <span className="text-sm font-mono text-emerald-400 font-semibold">
              %
            </span>
          </div>
        </div>

        <div className="mt-3">
          <div className="w-full bg-[#1e293b] h-1.5 rounded-full overflow-hidden">
            <div
              className="bg-gradient-to-r from-emerald-500 to-teal-400 h-full rounded-full transition-all duration-500"
              style={{ width: `${Math.min(100, kpis.asset_availability_pct)}%` }}
            />
          </div>
          <div className="flex items-center justify-between mt-1.5 text-[10px] font-mono text-[#64748b]">
            <span>Corridor-wide</span>
            <span className="text-[#94a3b8]">
              {(100 - kpis.asset_availability_pct).toFixed(1)}% possession
            </span>
          </div>
        </div>
      </div>

      {/* 2. Scheduled Blocks KPI */}
      <div className="bg-[#0c1120] border border-[#1e293b] hover:border-[#2a3c5a] transition-all rounded-lg p-3.5 flex flex-col justify-between shadow-sm relative overflow-hidden group">
        <div className="absolute top-0 right-0 w-24 h-24 bg-blue-500/5 rounded-full blur-xl pointer-events-none" />
        <div>
          <div className="flex items-center justify-between text-[#64748b] mb-1.5">
            <span className="text-[10px] font-semibold tracking-wider uppercase">
              Scheduled Blocks
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
                d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4"
              />
            </svg>
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-2xl font-bold font-mono text-[#f8fafc]">
              {scheduledCount}
            </span>
            <span className="text-xs font-mono text-[#64748b]">
              / {totalCandidates} candidate{totalCandidates !== 1 ? "s" : ""}
            </span>
          </div>
        </div>

        <div className="mt-3 flex items-center justify-between pt-2 border-t border-[#1e293b]/70 text-[10px] font-mono">
          <div className="flex items-center gap-1.5">
            <span className="text-[#64748b]">Track:</span>
            {Object.entries(trackCounts).map(([track, count]) => (
              <span
                key={track}
                className="bg-[#151c2c] text-[#cbd5e1] px-1.5 py-0.5 rounded border border-[#223049]"
              >
                {track}: {count}
              </span>
            ))}
          </div>
          <span className="text-blue-400 font-semibold">
            {scheduledPct}% rate
          </span>
        </div>
      </div>

      {/* 3. Rejected / Deferred Blocks KPI */}
      <div className="bg-[#0c1120] border border-[#1e293b] hover:border-[#2a3c5a] transition-all rounded-lg p-3.5 flex flex-col justify-between shadow-sm relative overflow-hidden group">
        <div className="absolute top-0 right-0 w-24 h-24 bg-red-500/5 rounded-full blur-xl pointer-events-none" />
        <div>
          <div className="flex items-center justify-between text-[#64748b] mb-1.5">
            <span className="text-[10px] font-semibold tracking-wider uppercase">
              Rejected / Excluded
            </span>
            <svg
              className="w-4 h-4 text-red-400"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={1.8}
                d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z"
              />
            </svg>
          </div>
          <div className="flex items-baseline gap-2">
            <span
              className={`text-2xl font-bold font-mono ${
                rejectedCount > 0 ? "text-amber-400" : "text-[#f8fafc]"
              }`}
            >
              {rejectedCount}
            </span>
            <span className="text-xs font-mono text-[#64748b]">
              deferred work
            </span>
          </div>
        </div>

        <div className="mt-3 flex items-center justify-between pt-2 border-t border-[#1e293b]/70 text-[10px] font-mono">
          <span className="text-[#64748b] truncate max-w-[120px]">
            {rejectedCount > 0 ? "Capacity / Priority" : "Zero exclusions"}
          </span>
          <span
            className={`px-1.5 py-0.5 rounded font-semibold ${
              rejectedCount > 0
                ? "bg-amber-500/10 text-amber-400 border border-amber-500/20"
                : "bg-emerald-500/10 text-emerald-400"
            }`}
          >
            {rejectedCount > 0 ? "Infeasible / Cut" : "All Feasible"}
          </span>
        </div>
      </div>

      {/* 4. Operational Conflicts / Train Disruption KPI */}
      <div className="bg-[#0c1120] border border-[#1e293b] hover:border-[#2a3c5a] transition-all rounded-lg p-3.5 flex flex-col justify-between shadow-sm relative overflow-hidden group">
        <div className="absolute top-0 right-0 w-24 h-24 bg-emerald-500/5 rounded-full blur-xl pointer-events-none" />
        <div>
          <div className="flex items-center justify-between text-[#64748b] mb-1.5">
            <span className="text-[10px] font-semibold tracking-wider uppercase">
              Operational Impact
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
                d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"
              />
            </svg>
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-2xl font-bold font-mono text-emerald-400">
              {kpis.trains_affected}
            </span>
            <span className="text-xs font-mono text-[#64748b]">
              trains affected
            </span>
          </div>
        </div>

        <div className="mt-3 flex items-center justify-between pt-2 border-t border-[#1e293b]/70 text-[10px] font-mono">
          <span className="text-[#64748b]">Constraint Feasibility</span>
          {isSolverFeasible && trackConflictCount === 0 ? (
            <span className="text-emerald-400 font-semibold flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 inline-block" />
              0 Track Overlaps ({result.status})
            </span>
          ) : (
            <span className="text-red-400 font-semibold flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-red-400 inline-block" />
              {trackConflictCount > 0
                ? `${trackConflictCount} Overlap Conflicts`
                : `Status: ${result.status}`}
            </span>
          )}
        </div>
      </div>

      {/* 5. Risk Reduction / Priority Score KPI */}
      <div className="bg-[#0c1120] border border-[#1e293b] hover:border-[#2a3c5a] transition-all rounded-lg p-3.5 flex flex-col justify-between shadow-sm relative overflow-hidden group">
        <div className="absolute top-0 right-0 w-24 h-24 bg-purple-500/5 rounded-full blur-xl pointer-events-none" />
        <div>
          <div className="flex items-center justify-between text-[#64748b] mb-1.5">
            <span className="text-[10px] font-semibold tracking-wider uppercase">
              Risk Mitigation
            </span>
            <svg
              className="w-4 h-4 text-purple-400"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={1.8}
                d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"
              />
            </svg>
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-2xl font-bold font-mono text-purple-400">
              {kpis.risk_reduction_score.toFixed(2)}
            </span>
            <span className="text-xs font-mono text-[#64748b]">
              / {totalRiskScore.toFixed(2)} pts
            </span>
          </div>
        </div>

        <div className="mt-3">
          <div className="w-full bg-[#1e293b] h-1.5 rounded-full overflow-hidden">
            <div
              className="bg-gradient-to-r from-purple-500 to-indigo-400 h-full rounded-full transition-all duration-500"
              style={{ width: `${riskReductionPct}%` }}
            />
          </div>
          <div className="flex items-center justify-between mt-1.5 text-[10px] font-mono text-[#64748b]">
            <span>Estimated Risk Addressed</span>
            <span className="text-purple-300 font-semibold">
              {riskReductionPct.toFixed(0)}%
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
