"use client";

import { useState } from "react";
import type { DashboardData, DisruptionEvent } from "@/types/contracts";
import AppHeader from "@/components/AppHeader";
import PlanView from "@/components/PlanView";
import DisruptRecoverView from "@/components/DisruptRecoverView";
import { API_BASE_URL, enrichData, fetchOptimizationData, triggerRecovery } from "@/lib/data";
import { compareSchedules, type RecoveryComparison } from "@/lib/recoveryComparison";
import type { OperationalStage } from "@/lib/stage";

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
  const [viewingDisruptScreen, setViewingDisruptScreen] = useState(false);

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
    setViewingDisruptScreen(false);
    handleResetDisruption();
  };

  const isLiveBackend = data.dataSource === "LIVE_API";
  const isViewingRecovered = recoveryComparison !== null;

  const stage: OperationalStage = isViewingRecovered
    ? "RECOVER"
    : isRecovering || pendingDisruptionType || viewingDisruptScreen
    ? "DISRUPT"
    : "PLAN";

  const solveTimeMs = Math.round((data.result.solve_time_seconds || 0) * 1000);

  return (
    <div className="bg-surface font-body-md text-body-md text-on-surface antialiased min-h-screen relative">
      <div className="fixed inset-0 pointer-events-none z-0 bg-[radial-gradient(ellipse_80%_50%_at_50%_-20%,rgba(0,240,255,0.06),transparent_70%)]" />

      <AppHeader
        stage={stage}
        isLiveBackend={isLiveBackend}
        solveTimeMs={solveTimeMs}
        onNavPlan={handleReturnToOriginal}
        onNavDisrupt={() => setViewingDisruptScreen(true)}
      />

      <main className="relative z-10 w-full pt-20 bg-transparent min-h-screen">
        <div className="flex flex-col w-full">
          {stage === "PLAN" ? (
            <PlanView
              data={data}
              selectedBlockId={selectedBlockId}
              onSelectBlock={setSelectedBlockId}
              onReoptimize={handleReoptimize}
              onGoToDisrupt={() => setViewingDisruptScreen(true)}
              isResolving={isResolving}
            />
          ) : (
            <DisruptRecoverView
              requestContext={baselineData.request}
              data={data}
              disabledReason={
                !isLiveBackend ? "Live backend required to simulate recovery (currently in fixture mode)." : null
              }
              isLoading={isRecovering}
              error={recoveryError}
              comparison={recoveryComparison}
              activeDisruption={activeDisruption}
              solveTimeMs={solveTimeMs}
              onTrigger={handleTriggerDisruption}
              onReset={handleResetDisruption}
              onReturnToOriginal={handleReturnToOriginal}
              onTypeSelect={setPendingDisruptionType}
            />
          )}
        </div>
      </main>

      <footer className="relative z-10 w-full bg-surface-container-lowest/90 backdrop-blur-xl mt-space-3xl py-space-xl">
        <div className="w-full px-gutter-desktop flex flex-col md:flex-row items-center justify-between gap-space-md font-label-mono text-label-mono text-on-surface-variant">
          <div className="flex items-center gap-space-md">
            <span className="text-primary">PASHUPATASTRA</span>
            <span>
              SCHEDULED: {data.result.scheduled_blocks?.length || 0} / {data.request.candidates?.length || 0}
            </span>
            <span>STATUS: {data.result.status}</span>
          </div>
          <div>GOOGLE OR-TOOLS CP-SAT &middot; {solveTimeMs}ms</div>
        </div>
      </footer>
    </div>
  );
}
