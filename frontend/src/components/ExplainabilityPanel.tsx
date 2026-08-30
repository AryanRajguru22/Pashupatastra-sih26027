"use client";

import React from "react";
import type {
  DashboardData,
  EnrichedScheduledBlock,
  EnrichedUnscheduledBlock,
  WorkType,
} from "@/types/contracts";

interface ExplainabilityPanelProps {
  data: DashboardData;
  selectedBlockId: string | null;
  onSelectBlock: (blockId: string | null) => void;
}

const WORK_TYPE_STYLES: Record<
  WorkType,
  { bg: string; text: string; border: string }
> = {
  RENEWAL: {
    bg: "bg-amber-500/10",
    text: "text-amber-400",
    border: "border-amber-500/30",
  },
  INSPECTION: {
    bg: "bg-blue-500/10",
    text: "text-blue-400",
    border: "border-blue-500/30",
  },
  REPAIR: {
    bg: "bg-red-500/10",
    text: "text-red-400",
    border: "border-red-500/30",
  },
  PREVENTIVE: {
    bg: "bg-emerald-500/10",
    text: "text-emerald-400",
    border: "border-emerald-500/30",
  },
};

export default function ExplainabilityPanel({
  data,
  selectedBlockId,
  onSelectBlock,
}: ExplainabilityPanelProps) {
  // Find selected block in scheduled or unscheduled list
  const scheduledBlock: EnrichedScheduledBlock | undefined =
    data.enrichedScheduled.find((b) => b.block_id === selectedBlockId);

  const unscheduledBlock: EnrichedUnscheduledBlock | undefined =
    data.enrichedUnscheduled.find((b) => b.block_id === selectedBlockId);

  const isSelected = Boolean(scheduledBlock || unscheduledBlock);
  const isScheduled = Boolean(scheduledBlock);
  const candidate = scheduledBlock?.candidate || unscheduledBlock?.candidate;
  const explanation = scheduledBlock?.explanation;

  const workType = candidate?.work_type;
  const workTypeStyle = workType
    ? WORK_TYPE_STYLES[workType]
    : {
        bg: "bg-slate-500/10",
        text: "text-slate-400",
        border: "border-slate-500/30",
      };

  return (
    <div className="bg-[#0c1120] border border-[#1e293b] rounded-lg flex flex-col h-full overflow-hidden shadow-lg">
      {/* Panel Header */}
      <div className="px-4 py-3 border-b border-[#1e293b] flex items-center justify-between bg-[#0f1524]">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-blue-400" />
          <h3 className="text-xs font-bold uppercase tracking-wider text-[#e2e8f0]">
            Decision Explainability & Audit
          </h3>
        </div>
        {isSelected && (
          <button
            onClick={() => onSelectBlock(null)}
            className="text-[11px] font-mono text-[#94a3b8] hover:text-white px-2 py-0.5 rounded bg-[#1e293b] hover:bg-[#2a3c5a] transition-colors"
          >
            Clear Selection ✕
          </button>
        )}
      </div>

      {/* Panel Body */}
      <div className="p-4 overflow-y-auto flex-1 flex flex-col gap-4 text-xs">
        {!isSelected ? (
          /* Empty / Default state when no block selected */
          <div className="flex flex-col items-center justify-center py-10 px-4 text-center text-[#64748b] gap-3">
            <div className="w-10 h-10 rounded-full bg-[#151c2c] border border-[#1e293b] flex items-center justify-center text-[#94a3b8]">
              <svg
                className="w-5 h-5"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={1.5}
                  d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
                />
              </svg>
            </div>
            <div>
              <p className="text-sm font-semibold text-[#cbd5e1]">
                No Block Selected
              </p>
              <p className="text-[11px] text-[#64748b] mt-1 max-w-[240px]">
                Click on any scheduled or rejected block in the timeline to
                inspect CP-SAT solver reasoning, ML scores, and binding
                constraints.
              </p>
            </div>

            {/* Quick selector list */}
            <div className="w-full mt-4 pt-4 border-t border-[#1e293b]/70 text-left">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-[#475569] block mb-2">
                Quick Inspect Candidates ({data.request.block_candidates.length})
              </span>
              <div className="flex flex-wrap gap-1.5 max-h-36 overflow-y-auto">
                {data.enrichedScheduled.map((b) => (
                  <button
                    key={b.block_id}
                    onClick={() => onSelectBlock(b.block_id)}
                    className="px-2 py-1 rounded text-[11px] font-mono font-medium bg-[#151c2c] hover:bg-[#1f2a40] text-emerald-400 border border-emerald-500/30 transition-colors"
                  >
                    ✓ {b.block_id}
                  </button>
                ))}
                {data.enrichedUnscheduled.map((b) => (
                  <button
                    key={b.block_id}
                    onClick={() => onSelectBlock(b.block_id)}
                    className="px-2 py-1 rounded text-[11px] font-mono font-medium bg-[#151c2c] hover:bg-[#1f2a40] text-red-400 border border-red-500/30 transition-colors"
                  >
                    ✕ {b.block_id}
                  </button>
                ))}
              </div>
            </div>
          </div>
        ) : (
          /* Detailed block explainability inspection view */
          <div className="flex flex-col gap-4">
            {/* Block identity bar */}
            <div className="flex items-center justify-between pb-3 border-b border-[#1e293b]">
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-base font-bold font-mono text-[#f8fafc]">
                    {selectedBlockId}
                  </span>
                  {workType && (
                    <span
                      className={`text-[10px] font-semibold px-2 py-0.5 rounded border uppercase tracking-wider ${workTypeStyle.bg} ${workTypeStyle.text} ${workTypeStyle.border}`}
                    >
                      {workType}
                    </span>
                  )}
                </div>
                <span className="text-[11px] font-mono text-[#64748b]">
                  Asset:{" "}
                  <span className="text-[#94a3b8]">
                    {candidate?.asset_id || "N/A"}
                  </span>{" "}
                  · Track:{" "}
                  <span className="text-[#94a3b8]">
                    {candidate?.track_id || scheduledBlock?.track_id}
                  </span>
                </span>
              </div>

              {/* Status Pill */}
              <div
                className={`px-2.5 py-1 rounded text-xs font-mono font-semibold flex items-center gap-1.5 border ${
                  isScheduled
                    ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/30"
                    : "bg-red-500/10 text-red-400 border-red-500/30"
                }`}
              >
                <span
                  className={`w-1.5 h-1.5 rounded-full ${
                    isScheduled ? "bg-emerald-400" : "bg-red-400"
                  }`}
                />
                {isScheduled ? "SCHEDULED" : "REJECTED"}
              </div>
            </div>

            {/* 1. Decision Reason (from contract explainability / reason) */}
            <div className="bg-[#151c2c] border border-[#223049] rounded-md p-3">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-[#64748b] block mb-1.5">
                Optimizer Decision Verdict
              </span>
              <p className="text-xs text-[#e2e8f0] leading-relaxed font-mono">
                {isScheduled
                  ? explanation?.reason || "Scheduled in feasible time window."
                  : unscheduledBlock?.reason ||
                    "Excluded due to optimizer objective trade-off."}
              </p>
            </div>

            {/* 2. Binding Constraints (Surfaced directly from ExplanationEntry) */}
            {isScheduled && (
              <div>
                <span className="text-[10px] font-semibold uppercase tracking-wider text-[#64748b] block mb-2">
                  Binding Constraints Enforced
                </span>
                {explanation?.binding_constraints &&
                explanation.binding_constraints.length > 0 ? (
                  <div className="flex flex-col gap-1.5">
                    {explanation.binding_constraints.map((constraint, idx) => (
                      <div
                        key={idx}
                        className="flex items-center gap-2 bg-[#101726] border border-[#1e293b] px-2.5 py-1.5 rounded text-[11px] font-mono text-[#cbd5e1]"
                      >
                        <span className="text-blue-400 font-bold">🔒</span>
                        <span>{constraint}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-[11px] text-[#64748b] italic font-mono bg-[#101726] p-2 rounded">
                    No active binding constraints
                  </div>
                )}
              </div>
            )}

            {/* 3. AI/ML Scoring Inputs (Priority & Risk) */}
            <div>
              <span className="text-[10px] font-semibold uppercase tracking-wider text-[#64748b] block mb-2">
                AI / ML Scorer Objective Inputs
              </span>
              <div className="grid grid-cols-2 gap-2">
                <div className="bg-[#101726] border border-[#1e293b] p-2.5 rounded">
                  <div className="text-[10px] text-[#64748b] uppercase font-semibold">
                    Priority Score
                  </div>
                  <div className="text-lg font-bold font-mono text-[#f8fafc] mt-0.5">
                    {candidate?.priority_score?.toFixed(2) ?? "0.00"}
                  </div>
                  <div className="w-full bg-[#1e293b] h-1 rounded-full mt-1.5 overflow-hidden">
                    <div
                      className="bg-blue-500 h-full rounded-full"
                      style={{
                        width: `${(candidate?.priority_score ?? 0) * 100}%`,
                      }}
                    />
                  </div>
                </div>

                <div className="bg-[#101726] border border-[#1e293b] p-2.5 rounded">
                  <div className="text-[10px] text-[#64748b] uppercase font-semibold">
                    Risk Score
                  </div>
                  <div className="text-lg font-bold font-mono text-purple-400 mt-0.5">
                    {candidate?.risk_score?.toFixed(2) ?? "0.00"}
                  </div>
                  <div className="w-full bg-[#1e293b] h-1 rounded-full mt-1.5 overflow-hidden">
                    <div
                      className="bg-purple-500 h-full rounded-full"
                      style={{
                        width: `${(candidate?.risk_score ?? 0) * 100}%`,
                      }}
                    />
                  </div>
                </div>
              </div>
            </div>

            {/* 4. Time Window & Possession Horizon */}
            <div className="bg-[#101726] border border-[#1e293b] p-3 rounded flex flex-col gap-2">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-[#64748b]">
                Window & Execution Limits
              </span>

              {isScheduled && scheduledBlock && (
                <div className="flex justify-between items-center text-[11px] font-mono">
                  <span className="text-[#64748b]">Scheduled Time:</span>
                  <span className="text-emerald-400 font-semibold">
                    {new Date(scheduledBlock.start).toLocaleTimeString(
                      "en-IN",
                      { hour: "2-digit", minute: "2-digit", hour12: false }
                    )}{" "}
                    →{" "}
                    {new Date(scheduledBlock.end).toLocaleTimeString("en-IN", {
                      hour: "2-digit",
                      minute: "2-digit",
                      hour12: false,
                    })}
                  </span>
                </div>
              )}

              {candidate && (
                <>
                  <div className="flex justify-between items-center text-[11px] font-mono">
                    <span className="text-[#64748b]">Duration:</span>
                    <span className="text-[#cbd5e1]">
                      {candidate.duration_minutes} minutes (
                      {(candidate.duration_minutes / 60).toFixed(1)}h)
                    </span>
                  </div>
                  <div className="flex justify-between items-center text-[11px] font-mono">
                    <span className="text-[#64748b]">Earliest Start:</span>
                    <span className="text-[#cbd5e1]">
                      {new Date(candidate.earliest_start).toLocaleTimeString(
                        "en-IN",
                        { hour: "2-digit", minute: "2-digit", hour12: false }
                      )}
                    </span>
                  </div>
                  <div className="flex justify-between items-center text-[11px] font-mono">
                    <span className="text-[#64748b]">Latest Finish:</span>
                    <span className="text-[#cbd5e1]">
                      {new Date(candidate.latest_finish).toLocaleTimeString(
                        "en-IN",
                        { hour: "2-digit", minute: "2-digit", hour12: false }
                      )}
                    </span>
                  </div>
                </>
              )}
            </div>

            {/* 5. Hard Constraint Dependencies & Mutual Exclusions */}
            {candidate &&
              (candidate.dependencies?.length > 0 ||
                candidate.mutually_exclusive_with?.length > 0) && (
                <div className="bg-[#101726] border border-[#1e293b] p-3 rounded flex flex-col gap-2">
                  <span className="text-[10px] font-semibold uppercase tracking-wider text-[#64748b]">
                    Safety & Inter-Block Rules
                  </span>

                  {candidate.dependencies?.length > 0 && (
                    <div>
                      <span className="text-[11px] text-[#64748b] block mb-1">
                        Precedent Dependencies:
                      </span>
                      <div className="flex flex-wrap gap-1">
                        {candidate.dependencies.map((dep) => (
                          <span
                            key={dep}
                            className="bg-[#151c2c] text-blue-300 border border-blue-500/30 px-2 py-0.5 rounded font-mono text-[10px]"
                          >
                            Must follow {dep}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {candidate.mutually_exclusive_with?.length > 0 && (
                    <div className="mt-1">
                      <span className="text-[11px] text-[#64748b] block mb-1">
                        Mutual Exclusions (Crew / Resource Conflict):
                      </span>
                      <div className="flex flex-wrap gap-1">
                        {candidate.mutually_exclusive_with.map((ex) => (
                          <span
                            key={ex}
                            className="bg-[#151c2c] text-amber-300 border border-amber-500/30 px-2 py-0.5 rounded font-mono text-[10px]"
                          >
                            Cannot overlap with {ex}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
          </div>
        )}
      </div>
    </div>
  );
}
