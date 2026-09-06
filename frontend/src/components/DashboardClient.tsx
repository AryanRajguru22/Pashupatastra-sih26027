"use client";

import { useState } from "react";
import type { DashboardData, DisruptionEvent } from "@/types/contracts";
import Timeline from "@/components/Timeline";
import KpiSummaryBar from "@/components/KpiSummaryBar";
import ExplainabilityPanel from "@/components/ExplainabilityPanel";
import DisruptionControls from "@/components/DisruptionControls";
import StageIndicator, { type OperationalStage } from "@/components/StageIndicator";
import { API_BASE_URL, enrichData, fetchOptimizationData, triggerRecovery } from "@/lib/data";
import { compareSchedules, type RecoveryComparison } from "@/lib/recoveryComparison";

interface DashboardClientProps {
  initialData: DashboardData;
}

export default function DashboardClient({ initialData }: DashboardClientProps) {
  const [data, setData] = useState<DashboardData>(initialData);
  const [baselineData, setBaselineData] = useState<DashboardData>(initialData);
  const [selectedBlockId, setSelectedBlockId] = useState<string | null>(null);
  const [isResolving, setIsResolving] = useState(false);

  const [isRecovering, setIsRecovering] = useState(false);
  const [recoveryError, setRecoveryError] = useState<string | null>(null);
  const [recoveryComparison, setRecoveryComparison] =
    useState<RecoveryComparison | null>(null);
  const [activeDisruption, setActiveDisruption] =
    useState<DisruptionEvent | null>(null);
  const [pendingDisruptionType, setPendingDisruptionType] =
    useState<DisruptionEvent["disruption_type"] | null>(null);

  const handleBlockSelect = (blockId: string | null) => {
    setSelectedBlockId(blockId);
  };

  const handleReoptimize = async () => {
    setIsResolving(true);
    try {
      const refreshed = await fetchOptimizationData();
      setData(refreshed);
      setBaselineData(refreshed);
      setRecoveryComparison(null);
      setActiveDisruption(null);
      setPendingDisruptionType(null);
      setRecoveryError(null);
    } catch (err) {
      console.error("Failed to re-optimize:", err);
    } finally {
      setIsResolving(false);
    }
  };

  const handleTriggerDisruption = async (disruption: DisruptionEvent) => {
    setIsRecovering(true);
    setRecoveryError(null);
    try {
      const outcome = await triggerRecovery(baselineData.request, disruption);
      if (!outcome.ok) {
        setRecoveryError(outcome.message);
        return;
      }

      const recoveredData = enrichData(
        outcome.data.recovery_result,
        outcome.data.updated_request,
        baselineData.dataSource,
        undefined,
        `${API_BASE_URL}/recover`
      );

      setRecoveryComparison(compareSchedules(baselineData, recoveredData));
      setActiveDisruption(outcome.data.disruption);
      setData(recoveredData);
      setSelectedBlockId(null);
    } finally {
      setIsRecovering(false);
    }
  };

  const handleResetDisruption = () => {
    setRecoveryComparison(null);
    setActiveDisruption(null);
    setPendingDisruptionType(null);
    setRecoveryError(null);
  };

  const handleReturnToOriginal = () => {
    setData(baselineData);
    setSelectedBlockId(null);
    handleResetDisruption();
  };

  const horizonMinutes = data.request.horizon_minutes || 1440;
  const isLiveBackend = data.dataSource === "LIVE_API";
  const isViewingRecovered = recoveryComparison !== null;

  const stage: OperationalStage = isViewingRecovered
    ? "RECOVER"
    : isRecovering || pendingDisruptionType
    ? "DISRUPT"
    : "PLAN";

  return (
    <div className="flex-1 flex flex-col px-6 py-4 gap-5">
      {/* Top Status & Controls Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 bg-[#0a0f1d] border border-[#1e293b] px-4 py-2.5 rounded-lg shadow-sm">
        <div className="flex items-center gap-3">
          {/* Data Source Badge */}
          <div
            className={`flex items-center gap-2 px-2.5 py-1 rounded-full border text-xs font-semibold ${
              isLiveBackend
                ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/30"
                : "bg-amber-500/10 text-amber-400 border-amber-500/30"
            }`}
          >
            <span
              className={`w-2 h-2 rounded-full ${
                isLiveBackend ? "bg-emerald-400 animate-pulse" : "bg-amber-400"
              }`}
            />
            <span>
              {isLiveBackend
                ? "Live Backend API Connected"
                : "Fixture Mode (Local Fallback)"}
            </span>
          </div>

          {data.apiLatencyMs !== undefined && (
            <span className="text-[11px] font-mono text-[#94a3b8] bg-[#101726] px-2 py-0.5 rounded border border-[#1e293b]">
              Latency: {data.apiLatencyMs}ms
            </span>
          )}

          <span className="text-[11px] font-mono text-[#64748b]">
            Endpoint: {data.apiEndpoint || "/optimize"}
          </span>

          <span className="border-l border-[#1e293b] pl-3">
            <StageIndicator stage={stage} />
          </span>
        </div>

        <div className="flex items-center gap-3">
          <span className="text-[11px] font-mono text-[#64748b]">
            Corridor: <strong className="text-[#cbd5e1]">{data.result.corridor_id}</strong> (
            {horizonMinutes}m horizon)
          </span>

          <button
            onClick={handleReoptimize}
            disabled={isResolving}
            className="flex items-center gap-1.5 px-3 py-1 bg-blue-600 hover:bg-blue-500 disabled:bg-blue-800/40 text-white rounded text-xs font-semibold transition-all shadow hover:shadow-blue-500/20 active:scale-95"
          >
            <svg
              className={`w-3.5 h-3.5 ${isResolving ? "animate-spin" : ""}`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
              />
            </svg>
            <span>{isResolving ? "Solving..." : "Re-Run Optimization"}</span>
          </button>
        </div>
      </div>

      {/* Operational Disruption & Recovery Control */}
      <section aria-label="Operational Disruption and Recovery">
        <DisruptionControls
          requestContext={baselineData.request}
          disabledReason={
            !isLiveBackend
              ? "Live backend required to simulate recovery (currently in fixture mode)."
              : null
          }
          isLoading={isRecovering}
          error={recoveryError}
          comparison={recoveryComparison}
          activeDisruption={activeDisruption}
          onTrigger={handleTriggerDisruption}
          onReset={handleResetDisruption}
          onReturnToOriginal={handleReturnToOriginal}
          onTypeSelect={setPendingDisruptionType}
        />
      </section>

      {/* Stage 2: KPI Summary Bar */}
      <section aria-label="Operational KPI Summary">
        <KpiSummaryBar data={data} />
      </section>

      {/* Main Operations Grid: Timeline (left) + Explainability Panel (right) */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 items-start">
        {/* Timeline Column */}
        <div className="lg:col-span-8 xl:col-span-8 flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <h2 className="text-sm font-semibold text-[#cbd5e1] uppercase tracking-wider">
                Schedule Timeline
              </h2>
              <span className="text-[11px] font-mono text-[#475569] bg-[#0f1520] border border-[#1e293b] rounded px-2 py-0.5">
                00:00 → 24:00 (1440 mins)
              </span>
            </div>
            {selectedBlockId && (
              <span className="text-[11px] font-mono text-blue-400 bg-blue-500/10 border border-blue-500/30 px-2 py-0.5 rounded">
                Active Inspect: {selectedBlockId}
              </span>
            )}
          </div>

          <div className="bg-[#0a0e17] border border-[#1e293b] rounded-lg p-4">
            <Timeline
              data={data}
              onBlockSelect={(id) =>
                handleBlockSelect(selectedBlockId === id ? null : id)
              }
              selectedBlockId={selectedBlockId}
            />
          </div>
        </div>

        {/* Explainability & Decision Audit Column */}
        <div className="lg:col-span-4 xl:col-span-4 flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-[#cbd5e1] uppercase tracking-wider">
              Decision Audit
            </h2>
            <span className="text-[11px] font-mono text-[#64748b]">
              Canonical ML & CP-SAT Audit Trail
            </span>
          </div>

          <div className="h-[620px]">
            <ExplainabilityPanel
              data={data}
              selectedBlockId={selectedBlockId}
              onSelectBlock={handleBlockSelect}
            />
          </div>
        </div>
      </div>

      {/* Summary Footer */}
      <div className="flex flex-wrap items-center justify-between gap-4 text-[11px] font-mono text-[#64748b] px-1 pt-2 border-t border-[#1e293b]/50">
        <div className="flex items-center gap-6">
          <span>
            SCHEDULED:{" "}
            <span className="text-emerald-400 font-semibold">
              {data.result.scheduled_blocks?.length || 0}
            </span>
          </span>
          <span>
            REJECTED:{" "}
            <span className="text-red-400 font-semibold">
              {data.result.unscheduled_blocks?.length || 0}
            </span>
          </span>
          <span>
            TOTAL CANDIDATES:{" "}
            <span className="text-[#94a3b8]">
              {data.request.candidates?.length || 0}
            </span>
          </span>
          <span>
            STATUS:{" "}
            <span
              className={
                data.result.status === "OPTIMAL" ||
                data.result.status === "FEASIBLE"
                  ? "text-emerald-400 font-semibold"
                  : "text-red-400 font-semibold"
              }
            >
              {data.result.status}
            </span>
          </span>
        </div>
        <div className="flex items-center gap-2 text-[10px] text-[#475569]">
          <span>SOLVER:</span>
          <span className="text-[#64748b] bg-[#0c1120] border border-[#1e293b] px-2 py-0.5 rounded">
            Google OR-Tools CP-SAT | {Math.round((data.result.solve_time_seconds || 0) * 1000)}ms
          </span>
        </div>
      </div>
    </div>
  );
}
