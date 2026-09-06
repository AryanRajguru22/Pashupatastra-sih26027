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
      return "bg-accent-amber/10 text-accent-amber border-accent-amber/30";
    case "DROPPED":
      return "bg-red-400/10 text-red-300 border-red-400/30";
    case "NEWLY_SCHEDULED":
      return "bg-accent-cyan/10 text-accent-cyan border-accent-cyan/30";
    default:
      return "bg-white/5 text-muted border-white/10";
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
  const [selectedType, setSelectedType] = useState<DisruptionType | null>(null);
  const [trackId, setTrackId] = useState<string>("");
  const [assetId, setAssetId] = useState<string>("");
  const [startMinute, setStartMinute] = useState<number>(600);
  const [endMinute, setEndMinute] = useState<number>(900);

  const tracks = requestContext.tracks || [];

  const assets = useMemo(() => {
    const seen = new Map<string, string>();
    for (const c of requestContext.candidates || []) {
      if (!seen.has(c.asset_id)) {
        seen.set(c.asset_id, (c.metadata?.asset_name as string | undefined) || c.asset_id);
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

  // ── Result view: BEFORE -> DISRUPTION -> AFTER ──────────────────────────
  if (comparison) {
    const interestingChanges = comparison.changes.filter((c) => c.category !== "PRESERVED");

    return (
      <div className="glass-panel rounded-2xl p-5 flex flex-col gap-4">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <span className="font-mono-data text-[0.65rem] tracking-widest text-accent-amber uppercase font-bold">
            Recovery Result
          </span>
          <span className="text-[0.7rem] text-muted">{activeDisruption?.description}</span>
        </div>

        {/* Original -> Disruption -> Recovered strip */}
        <div className="grid grid-cols-3 gap-3 items-center">
          <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3 text-center">
            <div className="text-[0.6rem] font-mono-data uppercase tracking-wider text-muted mb-1">Original Plan</div>
            <div className="font-display text-3xl text-white">{comparison.before_scheduled_count}</div>
            <div className="text-[0.65rem] text-muted">scheduled &middot; {comparison.before_status}</div>
          </div>
          <div className="rounded-xl border border-red-400/25 bg-red-400/[0.06] p-3 text-center">
            <div className="text-[0.6rem] font-mono-data uppercase tracking-wider text-red-300 mb-1">Disruption</div>
            <div className="text-[0.72rem] text-red-300/90 leading-snug px-1">{activeDisruption?.description}</div>
          </div>
          <div className="rounded-xl border border-accent-cyan/25 bg-accent-cyan/[0.06] p-3 text-center">
            <div className="text-[0.6rem] font-mono-data uppercase tracking-wider text-accent-cyan mb-1">Recovered Plan</div>
            <div className="font-display text-3xl text-white">{comparison.after_scheduled_count}</div>
            <div className="text-[0.65rem] text-muted">scheduled &middot; {comparison.after_status}</div>
          </div>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <div className="rounded-lg border border-white/[0.06] bg-black/20 px-3 py-2">
            <div className="text-[0.6rem] uppercase tracking-wide text-muted mb-0.5">Unscheduled</div>
            <div className="font-mono-data text-xs text-white/90">
              {comparison.before_unscheduled_count} &rarr;{" "}
              <span className="text-red-300 font-semibold">{comparison.after_unscheduled_count}</span>
            </div>
          </div>
          <div className="rounded-lg border border-white/[0.06] bg-black/20 px-3 py-2">
            <div className="text-[0.6rem] uppercase tracking-wide text-muted mb-0.5">Preserved</div>
            <div className="font-mono-data text-xs text-white/90">{comparison.preservedCount} unchanged</div>
          </div>
          <div className="rounded-lg border border-white/[0.06] bg-black/20 px-3 py-2">
            <div className="text-[0.6rem] uppercase tracking-wide text-muted mb-0.5">Moved</div>
            <div className="font-mono-data text-xs text-accent-amber">{comparison.movedCount}</div>
          </div>
          <div className="rounded-lg border border-white/[0.06] bg-black/20 px-3 py-2">
            <div className="text-[0.6rem] uppercase tracking-wide text-muted mb-0.5">Dropped / New</div>
            <div className="font-mono-data text-xs">
              <span className="text-red-300">{comparison.droppedCount}</span> /{" "}
              <span className="text-accent-cyan">{comparison.newlyScheduledCount}</span>
            </div>
          </div>
        </div>

        {interestingChanges.length > 0 && (
          <div className="max-h-40 overflow-y-auto flex flex-col gap-1 border-t border-white/[0.06] pt-3">
            {interestingChanges.map((c) => (
              <div
                key={c.block_id}
                className="flex items-center justify-between text-[0.7rem] font-mono-data rounded-lg border border-white/[0.05] bg-black/15 px-2.5 py-1.5"
              >
                <div className="flex items-center gap-2">
                  <span className={`px-1.5 py-0.5 rounded-full border text-[0.58rem] font-semibold uppercase ${badgeClass(c.category)}`}>
                    {c.category.replace("_", " ")}
                  </span>
                  <span className="text-white/90">{c.block_id}</span>
                </div>
                <div className="text-muted">
                  {c.category === "MOVED" &&
                    `${formatMinuteToHHMM(c.old_start_minute!)}-${formatMinuteToHHMM(
                      c.old_end_minute!
                    )} → ${formatMinuteToHHMM(c.new_start_minute!)}-${formatMinuteToHHMM(c.new_end_minute!)}`}
                  {c.category === "NEWLY_SCHEDULED" &&
                    `${c.track_id} @ ${formatMinuteToHHMM(c.new_start_minute!)}-${formatMinuteToHHMM(c.new_end_minute!)}`}
                  {c.category === "DROPPED" && c.reason}
                </div>
              </div>
            ))}
          </div>
        )}

        <div className="flex items-center gap-2 pt-1">
          <button
            onClick={handleReset}
            className="px-3.5 py-1.5 rounded-full bg-white/5 hover:bg-white/10 border border-white/10 text-white/90 text-xs font-semibold transition-all"
          >
            Trigger Another Disruption
          </button>
          <button
            onClick={onReturnToOriginal}
            className="px-3.5 py-1.5 rounded-full bg-accent-cyan text-[#06222a] hover:brightness-110 text-xs font-bold transition-all"
          >
            Return to Original Plan
          </button>
        </div>
      </div>
    );
  }

  // ── Form view: pick a disruption type and its minimal inputs ────────────
  return (
    <div className="glass-panel rounded-2xl p-5 flex flex-col gap-3.5">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <span className="font-mono-data text-[0.65rem] tracking-widest text-accent-amber uppercase font-bold">
          Operational Disruption
        </span>
        <span className="text-[0.7rem] text-muted">Simulated against the current optimized plan</span>
      </div>

      {disabledReason && (
        <div className="text-[0.72rem] text-accent-amber bg-accent-amber/10 border border-accent-amber/25 rounded-xl px-3 py-2">
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
            className={`px-3.5 py-1.5 rounded-full text-xs font-semibold border transition-all disabled:opacity-40 disabled:cursor-not-allowed ${
              selectedType === opt.type
                ? "bg-red-400/15 border-red-400/40 text-red-200"
                : "bg-white/[0.03] border-white/10 text-muted hover:text-white hover:border-white/20"
            }`}
          >
            {opt.label}
          </button>
        ))}
      </div>

      {selectedType && (
        <div className="flex flex-wrap items-end gap-3 border-t border-white/[0.06] pt-3.5">
          <p className="w-full text-[0.72rem] text-muted">
            {DISRUPTION_OPTIONS.find((o) => o.type === selectedType)?.effect}
          </p>

          {(selectedType === "TRACK_UNAVAILABLE" ||
            selectedType === "EMERGENCY_WORK" ||
            selectedType === "POSSESSION_CURTAILMENT") && (
            <label className="flex flex-col gap-1 text-[0.7rem] text-muted">
              Track
              <select
                value={trackId}
                onChange={(e) => setTrackId(e.target.value)}
                className="bg-black/30 border border-white/10 rounded-full px-3 py-1.5 text-xs text-white/90 font-mono-data"
              >
                <option value="">Select track&hellip;</option>
                {tracks.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </label>
          )}

          {selectedType === "ASSET_BREAKDOWN" && (
            <label className="flex flex-col gap-1 text-[0.7rem] text-muted">
              Asset
              <select
                value={assetId}
                onChange={(e) => setAssetId(e.target.value)}
                className="bg-black/30 border border-white/10 rounded-full px-3 py-1.5 text-xs text-white/90 font-mono-data min-w-[180px]"
              >
                <option value="">Select asset&hellip;</option>
                {assets.map(([id, label]) => (
                  <option key={id} value={id}>{label}</option>
                ))}
              </select>
            </label>
          )}

          {selectedType === "POSSESSION_CURTAILMENT" && (
            <>
              <label className="flex flex-col gap-1 text-[0.7rem] text-muted">
                Start (min)
                <input
                  type="number" min={0} max={horizon} value={startMinute}
                  onChange={(e) => setStartMinute(Number(e.target.value))}
                  className="bg-black/30 border border-white/10 rounded-full px-3 py-1.5 text-xs text-white/90 font-mono-data w-24"
                />
              </label>
              <label className="flex flex-col gap-1 text-[0.7rem] text-muted">
                End (min)
                <input
                  type="number" min={0} max={horizon} value={endMinute}
                  onChange={(e) => setEndMinute(Number(e.target.value))}
                  className="bg-black/30 border border-white/10 rounded-full px-3 py-1.5 text-xs text-white/90 font-mono-data w-24"
                />
              </label>
            </>
          )}

          <button
            onClick={handleSubmit}
            disabled={!canSubmit}
            className="flex items-center gap-1.5 px-4 py-1.5 rounded-full bg-red-400/90 hover:bg-red-400 disabled:bg-red-400/20 disabled:text-red-300/40 disabled:cursor-not-allowed text-[#1a0906] font-bold text-xs transition-all"
          >
            <svg className={`w-3.5 h-3.5 ${isLoading ? "animate-spin" : ""}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            {isLoading ? "Simulating…" : "Apply Disruption & Recover"}
          </button>
        </div>
      )}

      {error && (
        <div className="text-[0.72rem] text-red-300 bg-red-400/10 border border-red-400/25 rounded-xl px-3 py-2">
          Recovery failed: {error}
        </div>
      )}
    </div>
  );
}
