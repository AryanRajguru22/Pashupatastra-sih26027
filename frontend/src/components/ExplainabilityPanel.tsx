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
  TRACK_RENEWAL: { bg: "bg-accent-amber/10", text: "text-accent-amber", border: "border-accent-amber/30", label: "Track Renewal" },
  BALLAST_TAMPING: { bg: "bg-accent-violet/10", text: "text-accent-violet", border: "border-accent-violet/30", label: "Ballast Tamping" },
  OHE_MAINTENANCE: { bg: "bg-accent-cyan/10", text: "text-accent-cyan", border: "border-accent-cyan/30", label: "OHE Maintenance" },
  SIGNALLING_INTERLOCKING: { bg: "bg-accent-violet/10", text: "text-accent-violet", border: "border-accent-violet/30", label: "Signalling & S&T" },
  ROUTINE_INSPECTION: { bg: "bg-accent-cyan/10", text: "text-accent-cyan", border: "border-accent-cyan/30", label: "Inspection" },
  EMERGENCY_REPAIR: { bg: "bg-red-400/10", text: "text-red-300", border: "border-red-400/30", label: "Emergency Repair" },
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
  const scheduledBlock: EnrichedScheduledBlock | undefined =
    data.enrichedScheduled.find((b) => b.block_id === selectedBlockId);

  const unscheduledBlock: EnrichedUnscheduledBlock | undefined =
    data.enrichedUnscheduled.find((b) => b.block_id === selectedBlockId);

  const isSelected = Boolean(scheduledBlock || unscheduledBlock);
  const isScheduled = Boolean(scheduledBlock);
  const candidate = scheduledBlock?.candidate || unscheduledBlock?.candidate;

  const workTypeKey = String(candidate?.work_type || scheduledBlock?.work_type || "");
  const workTypeStyle = WORK_TYPE_STYLES[workTypeKey] ?? {
    bg: "bg-white/5",
    text: "text-muted",
    border: "border-white/10",
    label: workTypeKey || "General",
  };

  const scoringFeatures: ScoringFeatures | undefined = candidate?.metadata?.scoring_features;

  return (
    <div className="glass-panel rounded-2xl flex flex-col h-full overflow-hidden">
      <div className="px-5 py-3.5 border-b border-white/[0.06] flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span
            className={`w-2 h-2 rounded-full ${isSelected ? workTypeStyle.text.replace("text-", "bg-") : "bg-accent-amber"}`}
          />
          <span className="font-mono-data text-[0.65rem] tracking-widest text-accent-amber uppercase font-bold">
            Operational Intelligence
          </span>
        </div>
        {isSelected ? (
          <button
            onClick={() => onSelectBlock(null)}
            className="text-[0.65rem] font-mono-data text-muted hover:text-white px-2 py-0.5 rounded bg-white/5 hover:bg-white/10 transition-colors"
          >
            Clear &times;
          </button>
        ) : (
          <span className="font-mono-data text-[0.65rem] text-muted">{selectedBlockId ?? "--"}</span>
        )}
      </div>

      <div className="p-5 overflow-y-auto flex-1 flex flex-col gap-4 text-xs">
        {!isSelected ? (
          <div className="flex flex-col gap-3">
            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3 text-muted">
              <span className="text-white font-semibold block mb-1">Select a block to inspect</span>
              Click any block on the corridor, or an entry below, to see its canonical ML
              features, solver assignment, and reasoning.
            </div>

            {data.result.infeasibility_reasons && data.result.infeasibility_reasons.length > 0 && (
              <div className="rounded-xl border border-red-400/30 bg-red-400/10 p-3 text-red-300">
                <span className="font-bold block mb-1">Infeasibility flags</span>
                <ul className="list-disc list-inside space-y-0.5">
                  {data.result.infeasibility_reasons.map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
              </div>
            )}

            {data.enrichedUnscheduled.length > 0 && (
              <div>
                <span className="text-[0.65rem] font-mono-data uppercase tracking-wider text-red-300/90 block mb-1.5">
                  Solver exclusions ({data.enrichedUnscheduled.length})
                </span>
                <div className="space-y-1.5">
                  {data.enrichedUnscheduled.map((ub) => (
                    <div
                      key={ub.block_id}
                      onClick={() => onSelectBlock(ub.block_id)}
                      className="cursor-pointer rounded-lg border border-red-400/15 bg-red-400/[0.04] hover:bg-red-400/[0.08] p-2 flex items-start justify-between gap-2 transition-colors"
                    >
                      <div>
                        <span className="font-mono-data font-bold text-red-300">{ub.block_id}</span>
                        <span className="text-[0.65rem] text-muted ml-2">{ub.track_id}</span>
                        <p className="text-[0.7rem] text-red-300/80 mt-0.5">{ub.rejectionReason}</p>
                      </div>
                      <span className="text-[0.65rem] font-mono-data text-muted">
                        P:{ub.priority_score.toFixed(2)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div>
              <span className="text-[0.65rem] font-mono-data uppercase tracking-wider text-accent-cyan/90 block mb-1.5">
                Scheduled blocks ({data.enrichedScheduled.length})
              </span>
              <div className="space-y-1">
                {data.enrichedScheduled.map((sb) => (
                  <div
                    key={sb.block_id}
                    onClick={() => onSelectBlock(sb.block_id)}
                    className="cursor-pointer rounded-lg border border-white/[0.05] bg-white/[0.02] hover:bg-white/[0.05] p-2 flex items-center justify-between transition-colors"
                  >
                    <div className="flex items-center gap-2">
                      <span className="font-mono-data font-bold text-white">{sb.block_id}</span>
                      <span className="text-[0.65rem] font-mono-data text-accent-cyan">
                        {formatMinuteToHHMM(sb.start_minute)} &rarr; {formatMinuteToHHMM(sb.end_minute)}
                      </span>
                      <span className="text-[0.65rem] text-muted">[{sb.track_id}]</span>
                    </div>
                    <span className="text-[0.65rem] font-mono-data text-muted">
                      P:{sb.priority_score.toFixed(2)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <div className="space-y-3.5">
            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3.5">
              <div className="flex items-center justify-between gap-2 mb-1">
                <span className="font-mono-data text-[0.65rem] text-muted">{selectedBlockId}</span>
                <span
                  className={`text-[0.62rem] font-bold uppercase px-2 py-0.5 rounded-full border ${
                    isScheduled
                      ? "bg-accent-cyan/10 text-accent-cyan border-accent-cyan/30"
                      : "bg-red-400/10 text-red-300 border-red-400/30"
                  }`}
                >
                  {isScheduled ? "Scheduled" : "Unscheduled"}
                </span>
              </div>
              <h3 className="font-display text-2xl text-white font-normal">
                {workTypeStyle.label}
              </h3>
              <p className="font-mono-data text-[0.7rem] text-muted mt-0.5">
                {(candidate?.track_id || scheduledBlock?.track_id) ?? "--"}
                {candidate?.metadata?.asset_name ? ` • ${String(candidate.metadata.asset_name)}` : ""}
              </p>

              <div className="grid grid-cols-2 gap-2 text-[0.7rem] mt-3 pt-3 border-t border-white/[0.05]">
                <div>
                  <span className="text-muted block">Asset ID</span>
                  <span className="font-mono-data text-white/90 text-[0.65rem]">
                    {candidate?.asset_id || "N/A"}
                  </span>
                </div>
                <div>
                  <span className="text-muted block">Duration</span>
                  <span className="font-mono-data text-white/90">
                    {candidate?.duration_minutes ||
                      (scheduledBlock ? scheduledBlock.end_minute - scheduledBlock.start_minute : 0)}{" "}
                    min
                  </span>
                </div>
              </div>
            </div>

            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3.5">
              {isScheduled ? (
                <>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-[0.62rem] font-mono-data uppercase tracking-wider text-muted">
                      Scheduled window
                    </span>
                    <span className="font-mono-data text-accent-cyan font-bold text-sm">
                      {formatMinuteToHHMM(scheduledBlock?.start_minute || 0)} &rarr;{" "}
                      {formatMinuteToHHMM(scheduledBlock?.end_minute || 0)}
                    </span>
                  </div>
                  <span className="text-[0.62rem] font-mono-data uppercase tracking-wider text-muted block mb-1">
                    Why this decision?
                  </span>
                  <p className="text-white/85 text-[0.75rem] leading-relaxed">
                    Scheduled on track{" "}
                    <strong className="font-mono-data text-white">{scheduledBlock?.track_id}</strong> from{" "}
                    <strong className="font-mono-data text-white">
                      {formatMinuteToHHMM(scheduledBlock?.start_minute || 0)}
                    </strong>{" "}
                    to{" "}
                    <strong className="font-mono-data text-white">
                      {formatMinuteToHHMM(scheduledBlock?.end_minute || 0)}
                    </strong>
                    . Hard non-overlap and headway constraints satisfied.
                  </p>
                </>
              ) : (
                <>
                  <span className="text-[0.62rem] font-mono-data uppercase tracking-wider text-red-300 block mb-1">
                    Rejection reason
                  </span>
                  <p className="text-red-300/90 text-[0.75rem] leading-relaxed">
                    {unscheduledBlock?.rejectionReason}
                  </p>
                </>
              )}

              {candidate?.mutual_exclusion_group && (
                <div className="mt-3 pt-2.5 border-t border-white/[0.05] text-[0.7rem] text-muted">
                  <span>Shared machine/crew group: </span>
                  <span className="text-accent-amber font-mono-data font-medium">
                    {candidate.mutual_exclusion_group}
                  </span>
                </div>
              )}
            </div>

            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3.5">
              <div className="flex items-center justify-between mb-3">
                <span className="text-[0.62rem] font-mono-data uppercase tracking-wider text-accent-cyan">
                  ML scorer &mdash; 7-feature pipeline
                </span>
                <span className="text-[0.62rem] font-mono-data text-muted">score_block()</span>
              </div>

              <div className="grid grid-cols-2 gap-2 text-[0.7rem] mb-3">
                <div className="rounded-lg border border-white/[0.05] bg-black/20 p-2">
                  <span className="text-muted block text-[0.62rem]">Priority Score</span>
                  <span className="text-lg font-bold font-mono-data text-accent-cyan">
                    {(candidate?.priority_score ?? scheduledBlock?.priority_score ?? 0).toFixed(3)}
                  </span>
                </div>
                <div className="rounded-lg border border-white/[0.05] bg-black/20 p-2">
                  <span className="text-muted block text-[0.62rem]">Risk Score</span>
                  <span className="text-lg font-bold font-mono-data text-accent-amber">
                    {(candidate?.risk_score ?? scheduledBlock?.risk_score ?? 0).toFixed(3)}
                  </span>
                </div>
              </div>

              {scoringFeatures ? (
                <div className="space-y-1.5">
                  {[
                    ["Asset Criticality", scoringFeatures.asset_criticality?.toFixed(2)],
                    ["Defect Severity", scoringFeatures.defect_severity_name ?? scoringFeatures.defect_severity],
                    ["Days Overdue", scoringFeatures.days_overdue != null ? `${scoringFeatures.days_overdue} days` : undefined],
                    [
                      "Failure Probability",
                      scoringFeatures.failure_probability != null
                        ? `${(scoringFeatures.failure_probability * 100).toFixed(1)}%`
                        : undefined,
                    ],
                    ["Train Impact", scoringFeatures.train_impact?.toFixed(2)],
                    ["Maintenance Duration", scoringFeatures.maintenance_duration != null ? `${scoringFeatures.maintenance_duration} min` : undefined],
                    ["Historical Failure Rate", scoringFeatures.historical_failure_rate?.toFixed(2)],
                  ].map(([label, value]) => (
                    <div key={label} className="flex justify-between py-1 border-b border-white/[0.04] text-[0.68rem]">
                      <span className="text-muted">{label}</span>
                      <span className="font-mono-data text-white/90">{value ?? "—"}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-[0.68rem] text-muted">Scoring features calculated dynamically via canonical scorer.</p>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
