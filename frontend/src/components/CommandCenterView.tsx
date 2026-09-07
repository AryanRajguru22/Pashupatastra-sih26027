"use client";

import { useMemo } from "react";
import type { DashboardData, DisruptionEvent } from "@/types/contracts";
import type { RecoveryComparison } from "@/lib/recoveryComparison";
import { corridorCapacityPct, riskNeutralizedPct, workTypeBreakdown } from "@/lib/metrics";
import { formatWorkType } from "@/lib/format";

interface CommandCenterViewProps {
  data: DashboardData;
  isLiveBackend: boolean;
  solveTimeMs: number;
  activeDisruption: DisruptionEvent | null;
  recoveryComparison: RecoveryComparison | null;
  onEnterPlan: () => void;
  onEnterDisrupt: () => void;
}

/**
 * Real-data operational overview (Screen 1 / "Command Center" in the Stitch
 * export). Every value here comes from OptimizationRequest/OptimizationResult
 * already fetched by DashboardClient - no live train tracking, SCADA,
 * multi-corridor picker, job lifecycle, persistence, or interlocking commit,
 * since none of those exist in the backend. This is a landing/overview page
 * that links into the real PLAN and DISRUPT/RECOVER workflows.
 */
export default function CommandCenterView({
  data,
  isLiveBackend,
  solveTimeMs,
  activeDisruption,
  recoveryComparison,
  onEnterPlan,
  onEnterDisrupt,
}: CommandCenterViewProps) {
  const { result, request } = data;
  const totalCandidates = request.candidates?.length || 0;
  const scheduledCount = result.scheduled_blocks?.length || 0;
  const unscheduledCount = data.enrichedUnscheduled.length;

  const capacityPct = useMemo(() => corridorCapacityPct(request, result), [request, result]);
  const riskPct = useMemo(() => riskNeutralizedPct(request, result), [request, result]);
  const breakdown = useMemo(() => workTypeBreakdown(request, result), [request, result]);
  const maxBreakdown = Math.max(1, ...breakdown.map((b) => b.total));

  const kpis = [
    {
      label: "SLOTTED SECTORS",
      value: `${scheduledCount}/${totalCandidates}`,
      unit: "BLOCKS SCHEDULED",
      icon: "check_circle",
      accent: "text-primary",
    },
    {
      label: "PRIORITY CAPTURED",
      value: (result.total_priority_scheduled || 0).toFixed(1),
      unit: "SCORE PTS",
      icon: "speed",
      accent: "text-secondary",
    },
    {
      label: "RISK MITIGATED",
      value: `${riskPct.toFixed(1)}%`,
      unit: "OF POTENTIAL RISK",
      icon: "shield",
      accent: "text-primary-fixed-dim",
    },
    {
      label: "CORRIDOR CAPACITY",
      value: `${capacityPct.toFixed(1)}%`,
      unit: "TRACK-TIME FREE",
      icon: "hub",
      accent: "text-primary",
    },
  ];

  return (
    <div className="relative w-full overflow-hidden px-gutter-mobile md:px-gutter-desktop pb-space-3xl animate-fade-in">
      <div className="absolute top-1/4 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[900px] h-[450px] bg-gradient-to-b from-primary-container/10 via-surface-tint/5 to-transparent blur-[120px] rounded-full pointer-events-none" />
      <div className="absolute bottom-12 right-1/4 w-[600px] h-[320px] bg-gradient-to-tl from-secondary-container/15 via-secondary/5 to-transparent blur-[140px] rounded-full pointer-events-none" />

      <div className="relative z-20 pt-space-md flex flex-col gap-space-2xs animate-fade-in-up">
        <div className="flex items-center gap-space-xs font-label-mono text-label-mono">
          <span
            className={`w-1.5 h-1.5 rounded-full ${
              isLiveBackend ? "bg-primary-container animate-pulse" : "bg-secondary"
            }`}
          />
          <span className="text-on-surface-variant">
            {isLiveBackend ? "LIVE BACKEND CONNECTED" : "FIXTURE MODE"} &middot; {request.corridor_id}
          </span>
        </div>
        <h1 className="font-headline-lg text-headline-lg text-primary tracking-tight font-normal">
          <span className="italic">Command</span>{" "}
          <span className="not-italic text-on-surface font-light">Center</span>
        </h1>
        <p className="font-body-sm text-body-sm text-on-surface-variant max-w-xl">
          Operational overview sourced entirely from the live CP-SAT optimizer result — no
          simulated telemetry, no fabricated metrics.
        </p>
      </div>

      <div className="relative z-20 grid grid-cols-2 lg:grid-cols-4 gap-space-md mt-space-xl">
        {kpis.map((kpi, i) => (
          <div
            key={kpi.label}
            className="p-space-lg rounded-xl bg-surface-container-low/60 backdrop-blur-2xl shadow-lg flex flex-col gap-space-xs animate-fade-in-up hover:bg-surface-container-low/90 hover:-translate-y-0.5 transition-all duration-300"
            style={{ animationDelay: `${i * 80}ms` }}
          >
            <div className="flex items-center justify-between">
              <span className="font-label-caps text-label-caps text-on-surface-variant">
                {kpi.label}
              </span>
              <span className={`material-symbols-outlined text-lg ${kpi.accent}`}>{kpi.icon}</span>
            </div>
            <span className={`font-headline-md text-headline-md font-semibold ${kpi.accent}`}>
              {kpi.value}
            </span>
            <span className="font-label-mono text-[10px] text-on-surface-variant/70">
              {kpi.unit}
            </span>
          </div>
        ))}
      </div>

      <div className="relative z-20 grid grid-cols-1 lg:grid-cols-12 gap-space-lg mt-space-lg">
        <div
          className="lg:col-span-4 p-space-lg rounded-xl bg-surface-container-low/60 backdrop-blur-2xl shadow-lg flex flex-col gap-space-sm animate-fade-in-up"
          style={{ animationDelay: "320ms" }}
        >
          <span className="font-label-caps text-label-caps text-primary tracking-widest">
            SYSTEM STATUS
          </span>
          <div className="flex flex-col gap-space-xs font-label-mono text-label-mono text-on-surface-variant">
            <div className="flex justify-between">
              <span>DATA SOURCE</span>
              <span className="text-on-surface">{data.dataSource}</span>
            </div>
            <div className="flex justify-between">
              <span>SOLVER</span>
              <span className="text-on-surface">OR-TOOLS CP-SAT</span>
            </div>
            <div className="flex justify-between">
              <span>SOLVE TIME</span>
              <span className="text-on-surface">{solveTimeMs}ms</span>
            </div>
            <div className="flex justify-between">
              <span>RESULT STATUS</span>
              <span className="text-on-surface">{result.status}</span>
            </div>
            {data.apiLatencyMs != null && (
              <div className="flex justify-between">
                <span>API LATENCY</span>
                <span className="text-on-surface">{data.apiLatencyMs}ms</span>
              </div>
            )}
            <div className="pt-space-2xs break-all text-[10px] text-outline">
              {data.apiEndpoint}
            </div>
          </div>
        </div>

        <div
          className="lg:col-span-4 p-space-lg rounded-xl bg-surface-container-low/60 backdrop-blur-2xl shadow-lg flex flex-col gap-space-sm animate-fade-in-up"
          style={{ animationDelay: "400ms" }}
        >
          <div className="flex items-center justify-between">
            <span className="font-label-caps text-label-caps text-error tracking-widest">
              ATTENTION REQUIRED
            </span>
            <span className="font-label-mono text-label-mono text-on-surface-variant">
              {unscheduledCount}
            </span>
          </div>
          <div className="flex flex-col gap-space-2xs max-h-56 overflow-y-auto">
            {unscheduledCount === 0 ? (
              <div className="p-space-sm rounded bg-surface-container-lowest/60 font-label-mono text-label-mono text-on-surface-variant">
                All candidates scheduled. No exceptions.
              </div>
            ) : (
              data.enrichedUnscheduled.slice(0, 6).map((ub, i) => (
                <div
                  key={ub.block_id}
                  className="p-space-xs rounded bg-surface-container-lowest/60 flex flex-col gap-0.5 animate-fade-in-up"
                  style={{ animationDelay: `${460 + i * 40}ms` }}
                >
                  <div className="flex items-center justify-between font-label-mono text-label-mono">
                    <span className="text-on-surface">{ub.block_id}</span>
                    <span className="text-outline">{formatWorkType(ub.work_type)}</span>
                  </div>
                  <span className="font-label-mono text-[10px] text-on-surface-variant/80 truncate">
                    {ub.rejectionReason}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>

        <div
          className="lg:col-span-4 p-space-lg rounded-xl bg-surface-container-low/60 backdrop-blur-2xl shadow-lg flex flex-col gap-space-sm animate-fade-in-up"
          style={{ animationDelay: "480ms" }}
        >
          <span className="font-label-caps text-label-caps text-secondary tracking-widest">
            WORK TYPE BREAKDOWN
          </span>
          <div className="flex flex-col gap-space-xs">
            {breakdown.length === 0 && (
              <div className="p-space-sm rounded bg-surface-container-lowest/60 font-label-mono text-label-mono text-on-surface-variant">
                No candidate blocks in this request.
              </div>
            )}
            {breakdown.map((b) => (
              <div key={b.workType} className="flex flex-col gap-0.5">
                <div className="flex justify-between font-label-mono text-[10px] text-on-surface-variant">
                  <span>{formatWorkType(b.workType)}</span>
                  <span>
                    {b.scheduled}/{b.total}
                  </span>
                </div>
                <div className="w-full h-1.5 rounded-full bg-surface-container-high overflow-hidden">
                  <div
                    className="h-full rounded-full bg-primary-container transition-all duration-700"
                    style={{ width: `${(b.total / maxBreakdown) * 100}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div
        className="relative z-20 mt-space-lg p-space-lg rounded-xl bg-surface-container-low/60 backdrop-blur-2xl shadow-lg animate-fade-in-up"
        style={{ animationDelay: "560ms" }}
      >
        <span className="font-label-caps text-label-caps text-primary tracking-widest">
          SESSION ACTIVITY
        </span>
        {activeDisruption && recoveryComparison ? (
          <div className="mt-space-xs flex flex-col gap-space-2xs font-label-mono text-label-mono text-on-surface-variant">
            <span className="text-on-surface">{activeDisruption.description}</span>
            <span>
              {recoveryComparison.before_scheduled_count} &rarr;{" "}
              {recoveryComparison.after_scheduled_count} scheduled &middot;{" "}
              {recoveryComparison.preservedCount} preserved &middot; {recoveryComparison.movedCount}{" "}
              moved &middot; {recoveryComparison.droppedCount} dropped
            </span>
          </div>
        ) : (
          <p className="mt-space-xs font-label-mono text-label-mono text-on-surface-variant">
            No disruption simulated this session.
          </p>
        )}
      </div>

      <div className="relative z-20 grid grid-cols-1 md:grid-cols-2 gap-space-lg mt-space-xl">
        <button
          onClick={onEnterPlan}
          className="group text-left p-space-xl rounded-xl bg-surface-container-low/50 backdrop-blur-2xl shadow-lg hover:shadow-[0_0_32px_rgba(0,240,255,0.2)] hover:-translate-y-1 transition-all duration-300 animate-fade-in-up"
          style={{ animationDelay: "640ms" }}
        >
          <div className="flex items-center justify-between">
            <span className="font-label-caps text-label-caps text-primary tracking-widest">
              01 &mdash; PLAN
            </span>
            <span className="material-symbols-outlined text-primary group-hover:translate-x-1 transition-transform">
              arrow_forward
            </span>
          </div>
          <h3 className="font-headline-sm text-headline-sm text-on-surface mt-space-xs">
            Maintenance Planning
          </h3>
          <p className="font-body-sm text-body-sm text-on-surface-variant mt-space-2xs">
            Inspect the solver-verified corridor schedule, block-level ML scoring, and constraint
            status.
          </p>
        </button>
        <button
          onClick={onEnterDisrupt}
          className="group text-left p-space-xl rounded-xl bg-surface-container-low/50 backdrop-blur-2xl shadow-lg hover:shadow-[0_0_32px_rgba(255,182,136,0.2)] hover:-translate-y-1 transition-all duration-300 animate-fade-in-up"
          style={{ animationDelay: "700ms" }}
        >
          <div className="flex items-center justify-between">
            <span className="font-label-caps text-label-caps text-secondary tracking-widest">
              02 &mdash; DISRUPT / RECOVER
            </span>
            <span className="material-symbols-outlined text-secondary group-hover:translate-x-1 transition-transform">
              bolt
            </span>
          </div>
          <h3 className="font-headline-sm text-headline-sm text-on-surface mt-space-xs">
            Disruption Simulation
          </h3>
          <p className="font-body-sm text-body-sm text-on-surface-variant mt-space-2xs">
            Trigger a real disruption and watch the live CP-SAT solver recover the schedule end to
            end.
          </p>
        </button>
      </div>
    </div>
  );
}
