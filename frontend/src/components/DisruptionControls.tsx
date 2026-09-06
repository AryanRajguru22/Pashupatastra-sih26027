"use client";

import { useMemo, useState } from "react";
import type {
  BlockCandidate,
  DisruptionEvent,
  DisruptionType,
  OptimizationRequest,
} from "@/types/contracts";
import type { RecoveryComparison } from "@/lib/recoveryComparison";

interface DisruptionControlsProps {
  requestContext: OptimizationRequest;
  disabledReason: string | null;
  isLoading: boolean;
  error: string | null;
  comparison: RecoveryComparison | null;
  activeDisruption: DisruptionEvent | null;
  onTrigger: (disruption: DisruptionEvent) => void;
  onReset: () => void;
  onReturnToOriginal: () => void;
  /** Reports the in-progress type selection, purely for the PLAN/DISRUPT/RECOVER stage indicator. */
  onTypeSelect?: (type: DisruptionType | null) => void;
}

const DISRUPTION_OPTIONS: {
  type: DisruptionType;
  label: string;
  effect: string;
}[] = [
  {
    type: "ASSET_BREAKDOWN",
    label: "Asset Breakdown",
    effect: "Removes all remaining blocks tied to the failed asset.",
  },
  {
    type: "TRACK_UNAVAILABLE",
    label: "Track Unavailable",
    effect: "Removes all remaining blocks on the affected track for the day.",
  },
  {
    type: "EMERGENCY_WORK",
    label: "Emergency Work",
    effect: "Injects an urgent repair block the plan must fit in.",
  },
  {
    type: "POSSESSION_CURTAILMENT",
    label: "Possession Curtailment",
    effect: "Removes maintenance windows overlapping the curtailed interval.",
  },
];

function formatMinuteToHHMM(minute: number): string {
  const h = Math.floor(minute / 60);
  const m = minute % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

function badgeClass(category: string): string {
  switch (category) {
    case "MOVED":
      return "bg-amber-500/10 text-amber-400 border-amber-500/30";
    case "DROPPED":
      return "bg-red-500/10 text-red-400 border-red-500/30";
    case "NEWLY_SCHEDULED":
      return "bg-emerald-500/10 text-emerald-400 border-emerald-500/30";
    default:
      return "bg-[#1e293b] text-[#94a3b8] border-[#2a3c5a]";
  }
}

export default function DisruptionControls({
  requestContext,
  disabledReason,
  isLoading,
  error,
  comparison,
  activeDisruption,
  onTrigger,
  onReset,
  onReturnToOriginal,
  onTypeSelect,
}: DisruptionControlsProps) {
  const [selectedType, setSelectedType] = useState<DisruptionType | null>(
    null
  );
  const [trackId, setTrackId] = useState<string>("");
  const [assetId, setAssetId] = useState<string>("");
  const [startMinute, setStartMinute] = useState<number>(600);
  const [endMinute, setEndMinute] = useState<number>(900);

  const tracks = requestContext.tracks || [];

  const assets = useMemo(() => {
    const seen = new Map<string, string>();
    for (const c of requestContext.candidates || []) {
      if (!seen.has(c.asset_id)) {
        seen.set(
          c.asset_id,
          (c.metadata?.asset_name as string | undefined) || c.asset_id
        );
      }
    }
    return Array.from(seen.entries());
  }, [requestContext.candidates]);

  const horizon = requestContext.horizon_minutes || 1440;

  const canSubmit =
    !disabledReason &&
    !isLoading &&
    selectedType !== null &&
    (selectedType === "ASSET_BREAKDOWN"
      ? assetId !== ""
      : selectedType === "EMERGENCY_WORK" || selectedType === "TRACK_UNAVAILABLE"
      ? trackId !== ""
      : selectedType === "POSSESSION_CURTAILMENT"
      ? trackId !== "" && startMinute < endMinute
      : false);

  const handleSubmit = () => {
    if (!canSubmit || !selectedType) return;

    const disruptionId = `DISR-${Date.now()}`;
    const base = {
      disruption_id: disruptionId,
      disruption_type: selectedType,
      corridor_id: requestContext.corridor_id,
      start_minute: 0,
      end_minute: horizon,
    };

    let event: DisruptionEvent;

    if (selectedType === "ASSET_BREAKDOWN") {
      event = {
        ...base,
        affected_asset_id: assetId,
        description: `Asset ${assetId} breakdown - unavailable for maintenance.`,
      };
    } else if (selectedType === "TRACK_UNAVAILABLE") {
      event = {
        ...base,
        track_id: trackId,
        description: `${trackId} closed for emergency civil works.`,
      };
    } else if (selectedType === "EMERGENCY_WORK") {
      const emergencyCandidate: BlockCandidate = {
        block_id: `EMERGENCY-${Date.now()}`,
        asset_id: `EMERGENCY-ASSET-${trackId}`,
        track_id: trackId,
        work_type: "EMERGENCY_REPAIR",
        duration_minutes: 60,
        earliest_start_minute: 0,
        latest_end_minute: horizon,
        priority_score: 0.95,
        risk_score: 0.9,
        dependencies: [],
        mutual_exclusion_group: null,
        is_committed: false,
        status: "PLANNED",
      };
      event = {
        ...base,
        track_id: trackId,
        new_candidate: emergencyCandidate,
        description: `Emergency repair required on ${trackId}.`,
      };
    } else {
      event = {
        ...base,
        track_id: trackId,
        start_minute: startMinute,
        end_minute: endMinute,
        description: `${trackId} possession window curtailed ${formatMinuteToHHMM(
          startMinute
        )}-${formatMinuteToHHMM(endMinute)}.`,
      };
    }

    onTrigger(event);
  };

  const handleReset = () => {
    setSelectedType(null);
    onTypeSelect?.(null);
    setTrackId("");
    setAssetId("");
    onReset();
  };

  // ── Result view: show BEFORE -> AFTER once a recovery has completed ──
  if (comparison) {
    const interestingChanges = comparison.changes.filter(
      (c) => c.category !== "PRESERVED"
    );

    return (
      <div className="bg-[#0a0f1d] border border-[#1e293b] rounded-lg p-4 flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-[#cbd5e1] uppercase tracking-wider">
            Recovery Result
          </h2>
          <span className="text-[11px] font-mono text-[#64748b]">
            {activeDisruption?.description}
          </span>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <div className="bg-[#0c1120] border border-[#1e293b] rounded px-3 py-2">
            <div className="text-[10px] uppercase tracking-wide text-[#64748b] mb-1">
              Status
            </div>
            <div className="font-mono text-xs text-[#cbd5e1]">
              {comparison.before_status}
              <span className="text-[#475569] mx-1">→</span>
              <span
                className={
                  comparison.after_status === "OPTIMAL" ||
                  comparison.after_status === "FEASIBLE"
                    ? "text-emerald-400 font-semibold"
                    : "text-red-400 font-semibold"
                }
              >
                {comparison.after_status}
              </span>
            </div>
          </div>
          <div className="bg-[#0c1120] border border-[#1e293b] rounded px-3 py-2">
            <div className="text-[10px] uppercase tracking-wide text-[#64748b] mb-1">
              Scheduled
            </div>
            <div className="font-mono text-xs text-[#cbd5e1]">
              {comparison.before_scheduled_count}
              <span className="text-[#475569] mx-1">→</span>
              <span className="text-emerald-400 font-semibold">
                {comparison.after_scheduled_count}
              </span>
            </div>
          </div>
          <div className="bg-[#0c1120] border border-[#1e293b] rounded px-3 py-2">
            <div className="text-[10px] uppercase tracking-wide text-[#64748b] mb-1">
              Unscheduled
            </div>
            <div className="font-mono text-xs text-[#cbd5e1]">
              {comparison.before_unscheduled_count}
              <span className="text-[#475569] mx-1">→</span>
              <span className="text-red-400 font-semibold">
                {comparison.after_unscheduled_count}
              </span>
            </div>
          </div>
          <div className="bg-[#0c1120] border border-[#1e293b] rounded px-3 py-2">
            <div className="text-[10px] uppercase tracking-wide text-[#64748b] mb-1">
              Preserved
            </div>
            <div className="font-mono text-xs text-[#94a3b8]">
              {comparison.preservedCount} unchanged
            </div>
          </div>
        </div>

        {interestingChanges.length > 0 && (
          <div className="max-h-40 overflow-y-auto flex flex-col gap-1 border-t border-[#1e293b]/70 pt-2">
            {interestingChanges.map((c) => (
              <div
                key={c.block_id}
                className="flex items-center justify-between text-[11px] font-mono bg-[#0c1120] border border-[#1e293b]/70 rounded px-2 py-1"
              >
                <div className="flex items-center gap-2">
                  <span
                    className={`px-1.5 py-0.5 rounded border text-[9px] font-semibold uppercase ${badgeClass(
                      c.category
                    )}`}
                  >
                    {c.category.replace("_", " ")}
                  </span>
                  <span className="text-[#cbd5e1]">{c.block_id}</span>
                </div>
                <div className="text-[#64748b]">
                  {c.category === "MOVED" &&
                    `${formatMinuteToHHMM(
                      c.old_start_minute!
                    )}-${formatMinuteToHHMM(
                      c.old_end_minute!
                    )} → ${formatMinuteToHHMM(
                      c.new_start_minute!
                    )}-${formatMinuteToHHMM(c.new_end_minute!)}`}
                  {c.category === "NEWLY_SCHEDULED" &&
                    `${c.track_id} @ ${formatMinuteToHHMM(
                      c.new_start_minute!
                    )}-${formatMinuteToHHMM(c.new_end_minute!)}`}
                  {c.category === "DROPPED" && c.reason}
                </div>
              </div>
            ))}
          </div>
        )}

        <div className="flex items-center gap-2 pt-1">
          <button
            onClick={handleReset}
            className="px-3 py-1 bg-[#1e293b] hover:bg-[#2a3c5a] text-[#cbd5e1] rounded text-xs font-semibold transition-all"
          >
            Trigger Another Disruption
          </button>
          <button
            onClick={onReturnToOriginal}
            className="px-3 py-1 bg-blue-600 hover:bg-blue-500 text-white rounded text-xs font-semibold transition-all"
          >
            Return to Original Plan
          </button>
        </div>
      </div>
    );
  }

  // ── Form view: pick a disruption type and its minimal inputs ──────────
  return (
    <div className="bg-[#0a0f1d] border border-[#1e293b] rounded-lg p-4 flex flex-col gap-3">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h2 className="text-sm font-semibold text-[#cbd5e1] uppercase tracking-wider">
          Operational Disruption
        </h2>
        <span className="text-[11px] font-mono text-[#64748b]">
          Simulated against the current optimized plan
        </span>
      </div>

      {disabledReason && (
        <div className="text-[11px] text-amber-400 bg-amber-500/10 border border-amber-500/30 rounded px-2.5 py-1.5">
          {disabledReason}
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {DISRUPTION_OPTIONS.map((opt) => (
          <button
            key={opt.type}
            onClick={() => {
              setSelectedType(opt.type);
              onTypeSelect?.(opt.type);
            }}
            disabled={!!disabledReason}
            className={`px-3 py-1.5 rounded text-xs font-semibold border transition-all disabled:opacity-40 disabled:cursor-not-allowed ${
              selectedType === opt.type
                ? "bg-blue-600 border-blue-500 text-white"
                : "bg-[#0c1120] border-[#1e293b] text-[#94a3b8] hover:border-[#2a3c5a]"
            }`}
          >
            {opt.label}
          </button>
        ))}
      </div>

      {selectedType && (
        <div className="flex flex-wrap items-end gap-3 border-t border-[#1e293b]/70 pt-3">
          <p className="w-full text-[11px] text-[#64748b]">
            {DISRUPTION_OPTIONS.find((o) => o.type === selectedType)?.effect}
          </p>

          {(selectedType === "TRACK_UNAVAILABLE" ||
            selectedType === "EMERGENCY_WORK" ||
            selectedType === "POSSESSION_CURTAILMENT") && (
            <label className="flex flex-col gap-1 text-[11px] text-[#64748b]">
              Track
              <select
                value={trackId}
                onChange={(e) => setTrackId(e.target.value)}
                className="bg-[#0c1120] border border-[#1e293b] rounded px-2 py-1 text-xs text-[#cbd5e1] font-mono"
              >
                <option value="">Select track…</option>
                {tracks.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>
          )}

          {selectedType === "ASSET_BREAKDOWN" && (
            <label className="flex flex-col gap-1 text-[11px] text-[#64748b]">
              Asset
              <select
                value={assetId}
                onChange={(e) => setAssetId(e.target.value)}
                className="bg-[#0c1120] border border-[#1e293b] rounded px-2 py-1 text-xs text-[#cbd5e1] font-mono min-w-[180px]"
              >
                <option value="">Select asset…</option>
                {assets.map(([id, label]) => (
                  <option key={id} value={id}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          )}

          {selectedType === "POSSESSION_CURTAILMENT" && (
            <>
              <label className="flex flex-col gap-1 text-[11px] text-[#64748b]">
                Start (min)
                <input
                  type="number"
                  min={0}
                  max={horizon}
                  value={startMinute}
                  onChange={(e) => setStartMinute(Number(e.target.value))}
                  className="bg-[#0c1120] border border-[#1e293b] rounded px-2 py-1 text-xs text-[#cbd5e1] font-mono w-24"
                />
              </label>
              <label className="flex flex-col gap-1 text-[11px] text-[#64748b]">
                End (min)
                <input
                  type="number"
                  min={0}
                  max={horizon}
                  value={endMinute}
                  onChange={(e) => setEndMinute(Number(e.target.value))}
                  className="bg-[#0c1120] border border-[#1e293b] rounded px-2 py-1 text-xs text-[#cbd5e1] font-mono w-24"
                />
              </label>
            </>
          )}

          <button
            onClick={handleSubmit}
            disabled={!canSubmit}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-red-600 hover:bg-red-500 disabled:bg-red-900/30 disabled:text-red-400/40 disabled:cursor-not-allowed text-white rounded text-xs font-semibold transition-all"
          >
            <svg
              className={`w-3.5 h-3.5 ${isLoading ? "animate-spin" : ""}`}
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
            {isLoading ? "Simulating…" : "Apply Disruption & Recover"}
          </button>
        </div>
      )}

      {error && (
        <div className="text-[11px] text-red-400 bg-red-500/10 border border-red-500/30 rounded px-2.5 py-1.5">
          Recovery failed: {error}
        </div>
      )}
    </div>
  );
}
