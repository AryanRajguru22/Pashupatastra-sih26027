"use client";

import { useMemo, useState } from "react";
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
  const [recoveryComparison, setRecoveryComparison] = useState<RecoveryComparison | null>(null);
  const [activeDisruption, setActiveDisruption] = useState<DisruptionEvent | null>(null);
  const [pendingDisruptionType, setPendingDisruptionType] =
    useState<DisruptionEvent["disruption_type"] | null>(null);

  const handleBlockSelect = (blockId: string | null) => setSelectedBlockId(blockId);

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

  // Real KM range, derived from actual asset metadata already in the request -
  // never fabricated, simply not shown if the data doesn't carry km_location.
  const kmRange = useMemo(() => {
    const kms = (data.request.candidates || [])
      .map((c) => c.metadata?.km_location)
      .filter((v): v is number => typeof v === "number");
    if (kms.length === 0) return null;
    const min = Math.min(...kms);
    const max = Math.max(...kms);
    return `KM ${min.toFixed(1)}–${max.toFixed(1)}`;
  }, [data.request.candidates]);

  return (
    <div className="flex-1 flex flex-col min-h-screen">
      {/* ── Top navigation ── */}
      <header className="relative z-40 w-full px-6 py-3.5 flex items-center justify-between flex-wrap gap-3 border-b border-white/[0.05] bg-black/40 backdrop-blur-md">
        <div className="flex items-center gap-4 flex-wrap">
          <div className="flex items-center gap-2.5">
            <span
              className={`w-2 h-2 rounded-full ${isLiveBackend ? "bg-accent-cyan animate-pulse" : "bg-accent-amber"}`}
            />
            <span className="font-display tracking-wide text-lg font-normal text-white">
              PASHUPATASTRA
            </span>
            <span className="font-mono-data text-[0.65rem] tracking-widest text-muted uppercase hidden sm:inline">
              Railway Operations Intelligence
            </span>
          </div>
          <StageIndicator
            stage={stage}
            onSelectPlan={isViewingRecovered ? handleReturnToOriginal : undefined}
          />
        </div>

        <div className="flex items-center gap-3 flex-wrap">
          <span
            className={`flex items-center gap-2 px-3 py-1 rounded-full border text-xs font-semibold ${
              isLiveBackend
                ? "bg-accent-cyan/10 text-accent-cyan border-accent-cyan/25"
                : "bg-accent-amber/10 text-accent-amber border-accent-amber/25"
            }`}
            title={data.apiEndpoint || undefined}
          >
            <span className={`w-1.5 h-1.5 rounded-full ${isLiveBackend ? "bg-accent-cyan animate-pulse" : "bg-accent-amber"}`} />
            {isLiveBackend ? "Live Backend Connected" : "Fixture Mode (Local Fallback)"}
          </span>
          {data.apiLatencyMs !== undefined && (
            <span className="font-mono-data text-[0.68rem] text-muted hidden md:inline">{data.apiLatencyMs}ms</span>
          )}
          <div className="text-right hidden sm:block">
            <div className="font-mono-data text-[0.75rem] text-white font-semibold tracking-wide">
              {data.result.corridor_id}
            </div>
            <div className="font-mono-data text-[0.65rem] text-muted">
              {horizonMinutes}m horizon{kmRange ? ` • ${kmRange}` : ""}
            </div>
          </div>
          <button
            onClick={handleReoptimize}
            disabled={isResolving}
            className="flex items-center gap-1.5 px-3.5 py-1.5 bg-accent-cyan hover:brightness-110 disabled:opacity-40 text-[#06222a] rounded-full text-xs font-bold transition-all active:scale-95"
          >
            <svg className={`w-3.5 h-3.5 ${isResolving ? "animate-spin" : ""}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            {isResolving ? "Solving…" : "Re-Run Optimization"}
          </button>
        </div>
      </header>

      {/* ── Main viewport: Current Plan + corridor timeline + Operational Intelligence, side by side ── */}
      <div className="flex-1 px-6 py-5">
        <div className="grid grid-cols-1 lg:grid-cols-[288px_1fr_320px] gap-4 items-start">
          <div className="order-2 lg:order-1">
            <KpiSummaryBar data={data} />
          </div>

          <div className="order-1 lg:order-2 lg:min-h-[520px]">
            <Timeline
              data={data}
              onBlockSelect={(id) => handleBlockSelect(selectedBlockId === id ? null : id)}
              selectedBlockId={selectedBlockId}
            />
          </div>

          <div className="order-3 lg:sticky lg:top-4 lg:max-h-[calc(100vh-140px)]">
            <ExplainabilityPanel
              data={data}
              selectedBlockId={selectedBlockId}
              onSelectBlock={handleBlockSelect}
            />
          </div>
        </div>
      </div>

      {/* ── Operational Disruption / Recovery ── */}
      <div className="px-6 pb-5">
        <DisruptionControls
          requestContext={baselineData.request}
          disabledReason={
            !isLiveBackend ? "Live backend required to simulate recovery (currently in fixture mode)." : null
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
      </div>

      {/* ── Summary footer ── */}
      <div className="flex flex-wrap items-center justify-between gap-4 text-[0.68rem] font-mono-data text-muted px-6 pb-5 pt-3 border-t border-white/[0.05]">
        <div className="flex items-center gap-6">
          <span>
            SCHEDULED: <span className="text-accent-cyan font-semibold">{data.result.scheduled_blocks?.length || 0}</span>
          </span>
          <span>
            REJECTED: <span className="text-red-300 font-semibold">{data.result.unscheduled_blocks?.length || 0}</span>
          </span>
          <span>
            CANDIDATES: <span className="text-white/70">{data.request.candidates?.length || 0}</span>
          </span>
          <span>
            STATUS:{" "}
            <span
              className={
                data.result.status === "OPTIMAL" || data.result.status === "FEASIBLE"
                  ? "text-accent-cyan font-semibold"
                  : "text-red-300 font-semibold"
              }
            >
              {data.result.status}
            </span>
          </span>
        </div>
        <span className="text-white/40">
          Google OR-Tools CP-SAT &middot; {Math.round((data.result.solve_time_seconds || 0) * 1000)}ms
        </span>
      </div>
    </div>
  );
}
