"use client";

import React from "react";
import type {
  DashboardData,
  EnrichedScheduledBlock,
  EnrichedUnscheduledBlock,
  ScoringFeatures,
} from "@/types/contracts";

interface ExplainabilityPanelProps {
  data: DashboardData;
  selectedBlockId: string | null;
  onSelectBlock: (blockId: string | null) => void;
}

const WORK_TYPE_STYLES: Record<
  string,
  { bg: string; text: string; border: string; label: string }
> = {
  TRACK_RENEWAL: {
    bg: "bg-amber-500/10",
    text: "text-amber-400",
    border: "border-amber-500/30",
    label: "Track Renewal",
  },
  BALLAST_TAMPING: {
    bg: "bg-purple-500/10",
    text: "text-purple-400",
    border: "border-purple-500/30",
    label: "Ballast Tamping",
  },
  OHE_MAINTENANCE: {
    bg: "bg-cyan-500/10",
    text: "text-cyan-400",
    border: "border-cyan-500/30",
    label: "OHE Maintenance",
  },
  SIGNALLING_INTERLOCKING: {
    bg: "bg-fuchsia-500/10",
    text: "text-fuchsia-400",
    border: "border-fuchsia-500/30",
    label: "Signalling & S&T",
  },
  ROUTINE_INSPECTION: {
    bg: "bg-blue-500/10",
    text: "text-blue-400",
    border: "border-blue-500/30",
    label: "Inspection",
  },
  EMERGENCY_REPAIR: {
    bg: "bg-rose-500/10",
    text: "text-rose-400",
    border: "border-rose-500/30",
    label: "Emergency Repair",
  },
};

function formatMinuteToHHMM(minute: number): string {
  const h = Math.floor(minute / 60);
  const m = minute % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

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

  const workTypeKey = String(
    candidate?.work_type || scheduledBlock?.work_type || ""
  );
  const workTypeStyle = WORK_TYPE_STYLES[workTypeKey] ?? {
    bg: "bg-slate-500/10",
    text: "text-slate-400",
    border: "border-slate-500/30",
    label: workTypeKey || "General",
  };

  const scoringFeatures: ScoringFeatures | undefined =
    candidate?.metadata?.scoring_features;

  return (
    <div className="bg-[#0c1120] border border-[#1e293b] rounded-lg flex flex-col h-full overflow-hidden shadow-lg">
      {/* Panel Header */}
      <div className="px-4 py-3 border-b border-[#1e293b] flex items-center justify-between bg-[#0f1524]">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-blue-400" />
          <h3 className="text-xs font-bold uppercase tracking-wider text-[#e2e8f0]">
            Decision Explainability & ML Audit
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
          /* Default state when no block selected: audit list */
          <div className="flex flex-col gap-3">
            <div className="bg-[#0a0f1d] border border-[#1e293b] p-3 rounded-lg text-[#94a3b8]">
              <span className="text-[#e2e8f0] font-semibold block mb-1">
                Select a Block to Inspect
              </span>
              Click any block in the timeline or click an entry below to inspect
              its canonical ML features, solver assignment, and rejection reasons.
            </div>

            {/* Infeasibility reasons alert if any */}
            {data.result.infeasibility_reasons &&
              data.result.infeasibility_reasons.length > 0 && (
                <div className="bg-red-500/10 border border-red-500/30 p-3 rounded-lg text-red-400">
                  <span className="font-bold block mb-1">
                    Infeasibility Flags:
                  </span>
                  <ul className="list-disc list-inside space-y-0.5">
                    {data.result.infeasibility_reasons.map((r, i) => (
                      <li key={i}>{r}</li>
                    ))}
                  </ul>
                </div>
              )}

            {/* Unscheduled list */}
            {data.enrichedUnscheduled.length > 0 && (
              <div>
                <span className="text-[10px] font-bold uppercase tracking-wider text-red-400 block mb-1.5">
                  Solver Exclusions ({data.enrichedUnscheduled.length})
                </span>
                <div className="space-y-1.5">
                  {data.enrichedUnscheduled.map((ub) => (
                    <div
                      key={ub.block_id}
                      onClick={() => onSelectBlock(ub.block_id)}
                      className="cursor-pointer bg-[#140c12] hover:bg-[#20111a] border border-[#3b1c24] rounded p-2 flex items-start justify-between gap-2 transition-colors"
                    >
                      <div>
                        <span className="font-mono font-bold text-red-300">
                          {ub.block_id}
                        </span>
                        <span className="text-[10px] text-[#94a3b8] ml-2">
                          {ub.track_id}
                        </span>
                        <p className="text-[11px] text-red-400/90 mt-0.5">
                          {ub.rejectionReason}
                        </p>
                      </div>
                      <span className="text-[10px] font-mono text-[#64748b]">
                        P:{ub.priority_score.toFixed(2)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Scheduled summary */}
            <div>
              <span className="text-[10px] font-bold uppercase tracking-wider text-emerald-400 block mb-1.5">
                Scheduled Blocks ({data.enrichedScheduled.length})
              </span>
              <div className="space-y-1">
                {data.enrichedScheduled.map((sb) => (
                  <div
                    key={sb.block_id}
                    onClick={() => onSelectBlock(sb.block_id)}
                    className="cursor-pointer bg-[#0c1322] hover:bg-[#15213b] border border-[#1e293b] rounded p-2 flex items-center justify-between transition-colors"
                  >
                    <div className="flex items-center gap-2">
                      <span className="font-mono font-bold text-white">
                        {sb.block_id}
                      </span>
                      <span className="text-[10px] font-mono text-emerald-400">
                        {formatMinuteToHHMM(sb.start_minute)} → {formatMinuteToHHMM(sb.end_minute)}
                      </span>
                      <span className="text-[10px] text-[#64748b]">
                        [{sb.track_id}]
                      </span>
                    </div>
                    <span className="text-[10px] font-mono text-[#94a3b8]">
                      P:{sb.priority_score.toFixed(2)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        ) : (
          /* Detailed block inspection when selected */
          <div className="space-y-3.5">
            {/* Block identity header */}
            <div className="bg-[#0f172a] border border-[#273752] rounded-lg p-3">
              <div className="flex items-center justify-between gap-2 mb-2">
                <span className="text-base font-bold font-mono text-white">
                  {selectedBlockId}
                </span>
                <span
                  className={`text-[10px] font-bold uppercase px-2 py-0.5 rounded border ${
                    isScheduled
                      ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/30"
                      : "bg-red-500/10 text-red-400 border-red-500/30"
                  }`}
                >
                  {isScheduled ? "SCHEDULED" : "UNSCHEDULED"}
                </span>
              </div>

              <div className="grid grid-cols-2 gap-2 text-[11px]">
                <div>
                  <span className="text-[#64748b] block">Track</span>
                  <span className="font-mono font-semibold text-[#e2e8f0]">
                    {candidate?.track_id || scheduledBlock?.track_id}
                  </span>
                </div>
                <div>
                  <span className="text-[#64748b] block">Work Type</span>
                  <span
                    className={`font-semibold px-1.5 py-0.2 rounded inline-block text-[10px] ${workTypeStyle.bg} ${workTypeStyle.text} border ${workTypeStyle.border}`}
                  >
                    {workTypeStyle.label}
                  </span>
                </div>
                <div>
                  <span className="text-[#64748b] block">Asset ID</span>
                  <span className="font-mono text-[#94a3b8] text-[10px]">
                    {candidate?.asset_id || "N/A"}
                  </span>
                </div>
                <div>
                  <span className="text-[#64748b] block">Duration</span>
                  <span className="font-mono text-[#e2e8f0]">
                    {candidate?.duration_minutes ||
                      (scheduledBlock
                        ? scheduledBlock.end_minute - scheduledBlock.start_minute
                        : 0)}{" "}
                    mins
                  </span>
                </div>
              </div>

              {candidate?.metadata?.asset_name && (
                <div className="mt-2 pt-2 border-t border-[#1e293b] text-[11px]">
                  <span className="text-[#64748b]">Location: </span>
                  <span className="text-[#cbd5e1]">
                    {String(candidate.metadata.asset_name)}
                  </span>
                </div>
              )}
            </div>

            {/* Solver Decision & Rejection Reason */}
            <div className="bg-[#0f172a] border border-[#273752] rounded-lg p-3">
              <span className="text-[10px] font-bold uppercase tracking-wider text-[#94a3b8] block mb-1">
                Optimization Decision
              </span>
              {isScheduled ? (
                <p className="text-emerald-400 text-[11px] leading-relaxed">
                  Scheduled on track{" "}
                  <strong className="font-mono">
                    {scheduledBlock?.track_id}
                  </strong>{" "}
                  from{" "}
                  <strong className="font-mono">
                    {formatMinuteToHHMM(scheduledBlock?.start_minute || 0)}
                  </strong>{" "}
                  to{" "}
                  <strong className="font-mono">
                    {formatMinuteToHHMM(scheduledBlock?.end_minute || 0)}
                  </strong>
                  . Hard non-overlap and headway constraints satisfied.
                </p>
              ) : (
                <div className="bg-red-500/10 border border-red-500/20 p-2.5 rounded text-red-400 text-[11px] leading-relaxed">
                  <span className="font-semibold block mb-0.5">
                    Rejection Reason:
                  </span>
                  {unscheduledBlock?.rejectionReason}
                </div>
              )}

              {candidate?.mutual_exclusion_group && (
                <div className="mt-2 pt-2 border-t border-[#1e293b] text-[11px] text-[#94a3b8]">
                  <span>Mutual Exclusion Gang: </span>
                  <span className="text-amber-400 font-mono font-medium">
                    {candidate.mutual_exclusion_group}
                  </span>
                </div>
              )}
            </div>

            {/* 7-Feature Canonical ML Scoring Breakdown */}
            <div className="bg-[#0f172a] border border-[#273752] rounded-lg p-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-[10px] font-bold uppercase tracking-wider text-blue-400">
                  ML Scorer Features (7-Feature Pipeline)
                </span>
                <span className="text-[10px] font-mono text-[#94a3b8]">
                  score_block()
                </span>
              </div>

              <div className="grid grid-cols-2 gap-2 text-[11px]">
                <div className="bg-[#090d17] p-2 rounded border border-[#1e293b]">
                  <span className="text-[#64748b] block text-[10px]">
                    Priority Score
                  </span>
                  <span className="text-base font-bold font-mono text-blue-400">
                    {(candidate?.priority_score ?? scheduledBlock?.priority_score ?? 0).toFixed(3)}
                  </span>
                </div>
                <div className="bg-[#090d17] p-2 rounded border border-[#1e293b]">
                  <span className="text-[#64748b] block text-[10px]">
                    Risk Score
                  </span>
                  <span className="text-base font-bold font-mono text-amber-400">
                    {(candidate?.risk_score ?? scheduledBlock?.risk_score ?? 0).toFixed(3)}
                  </span>
                </div>
              </div>

              {scoringFeatures ? (
                <div className="mt-2.5 space-y-1.5 text-[10px]">
                  <div className="flex justify-between py-0.5 border-b border-[#1e293b]">
                    <span className="text-[#94a3b8]">Asset Criticality</span>
                    <span className="font-mono text-[#e2e8f0]">
                      {scoringFeatures.asset_criticality?.toFixed(2) ?? "—"}
                    </span>
                  </div>
                  <div className="flex justify-between py-0.5 border-b border-[#1e293b]">
                    <span className="text-[#94a3b8]">Defect Severity</span>
                    <span className="font-mono text-[#e2e8f0]">
                      {scoringFeatures.defect_severity_name ?? scoringFeatures.defect_severity ?? "—"}
                    </span>
                  </div>
                  <div className="flex justify-between py-0.5 border-b border-[#1e293b]">
                    <span className="text-[#94a3b8]">Days Overdue</span>
                    <span className="font-mono text-[#e2e8f0]">
                      {scoringFeatures.days_overdue ?? "—"} days
                    </span>
                  </div>
                  <div className="flex justify-between py-0.5 border-b border-[#1e293b]">
                    <span className="text-[#94a3b8]">Failure Probability</span>
                    <span className="font-mono text-[#e2e8f0]">
                      {scoringFeatures.failure_probability != null
                        ? `${(scoringFeatures.failure_probability * 100).toFixed(1)}%`
                        : "—"}
                    </span>
                  </div>
                  <div className="flex justify-between py-0.5 border-b border-[#1e293b]">
                    <span className="text-[#94a3b8]">Train Impact Factor</span>
                    <span className="font-mono text-[#e2e8f0]">
                      {scoringFeatures.train_impact?.toFixed(2) ?? "—"}
                    </span>
                  </div>
                  <div className="flex justify-between py-0.5 border-b border-[#1e293b]">
                    <span className="text-[#94a3b8]">Historical Failure Rate</span>
                    <span className="font-mono text-[#e2e8f0]">
                      {scoringFeatures.historical_failure_rate?.toFixed(2) ?? "—"}
                    </span>
                  </div>
                </div>
              ) : (
                <p className="text-[10px] text-[#64748b] mt-2">
                  Scoring features calculated dynamically via canonical scorer.
                </p>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
