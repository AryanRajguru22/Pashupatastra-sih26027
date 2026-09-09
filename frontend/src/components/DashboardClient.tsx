"use client";

import { useState } from "react";
import type {
  ConnectivityStatus,
  DashboardData,
  DisruptionEvent,
} from "@/types/contracts";
import AppHeader from "@/components/AppHeader";
import CommandCenterView from "@/components/CommandCenterView";
import PlanView from "@/components/PlanView";
import DisruptRecoverView from "@/components/DisruptRecoverView";
import { API_BASE_URL, enrichData, fetchOptimizationData, triggerRecovery } from "@/lib/data";
import { compareSchedules, type RecoveryComparison } from "@/lib/recoveryComparison";
import type { AppView, OperationalStage } from "@/lib/stage";

interface DashboardClientProps {
  initialData: DashboardData;
}

export default function DashboardClient({ initialData }: DashboardClientProps) {
  // Command Center is the presentation landing screen; "Maintenance
  // Planning" / "Disruption Simulation" switch into the existing
  // PLAN/DISRUPT/RECOVER workspace (still governed by `stage` below).
  const [view, setView] = useState<AppView>("COMMAND");
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
        // The backend just failed. Downgrade the CONNECTIVITY badge from
        // stale page-load state until a fresh fetch proves otherwise.
        // Provenance is untouched: a connection failure says nothing
        // about where the data came from, and the data has not changed.
        const nextConnectivity: ConnectivityStatus =
          outcome.reason === "unreachable" ? "OFFLINE" : "DEGRADED";
        setData((prev) => ({
          ...prev,
          connectivity: nextConnectivity,
          connectivityDetail: outcome.message,
        }));
        setBaselineData((prev) => ({
          ...prev,
          connectivity: nextConnectivity,
          connectivityDetail: outcome.message,
        }));
        return;
      }
      // Recovery succeeded, so the backend is ONLINE. The recovered plan
      // was computed from the same synthetic input, so provenance is
      // carried over unchanged rather than upgraded.
      const recoveredData = enrichData(
        outcome.data.recovery_result,
        outcome.data.updated_request,
        "ONLINE",
        baselineData.dataProvenance,
        {
          apiEndpoint: `${API_BASE_URL}/recover`,
          dataGeneratedAt: baselineData.dataGeneratedAt,
        }
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

  const handleNavCommand = () => setView("COMMAND");

  const handleNavPlan = () => {
    handleReturnToOriginal();
    setView("WORKSPACE");
  };

  const handleNavDisrupt = () => {
    setViewingDisruptScreen(true);
    setView("WORKSPACE");
  };

  // Whether the BACKEND answered. Deliberately not named "isLive": it
  // gates live actions (recovery needs a working backend), never the
  // provenance of the displayed data.
  const isBackendOnline = data.connectivity === "ONLINE";
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
        activeView={view}
        connectivity={data.connectivity}
        dataProvenance={data.dataProvenance}
        solveTimeMs={solveTimeMs}
        onNavCommand={handleNavCommand}
        onNavPlan={handleNavPlan}
        onNavDisrupt={handleNavDisrupt}
      />

      <main className="relative z-10 w-full pt-20 bg-transparent min-h-screen">
        <div className="flex flex-col w-full">
          {view === "COMMAND" ? (
            <CommandCenterView
              key="command"
              data={data}
              solveTimeMs={solveTimeMs}
              activeDisruption={activeDisruption}
              recoveryComparison={recoveryComparison}
              onEnterPlan={handleNavPlan}
              onEnterDisrupt={handleNavDisrupt}
            />
          ) : stage === "PLAN" ? (
            <PlanView
              key="plan"
              data={data}
              selectedBlockId={selectedBlockId}
              onSelectBlock={setSelectedBlockId}
              onReoptimize={handleReoptimize}
              onGoToDisrupt={handleNavDisrupt}
              isResolving={isResolving}
            />
          ) : (
            <DisruptRecoverView
              key="disrupt-recover"
              requestContext={baselineData.request}
              data={data}
              disabledReason={
                !isBackendOnline
                  ? "A reachable backend is required to simulate recovery (currently showing a pre-computed fixture result)."
                  : null
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
