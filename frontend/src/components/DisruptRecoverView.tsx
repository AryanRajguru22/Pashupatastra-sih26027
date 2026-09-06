"use client";

import { useMemo, useState } from "react";
import type {
  BlockCandidate,
  DashboardData,
  DisruptionEvent,
  DisruptionType,
  EnrichedScheduledBlock,
  OptimizationRequest,
} from "@/types/contracts";
import type { BlockChange, RecoveryComparison } from "@/lib/recoveryComparison";
import { LANE_CURVES, bezierPoint, pathD, toViewBoxPct } from "@/lib/corridorGeometry";
import { formatHHMM } from "@/lib/format";

interface DisruptRecoverViewProps {
  requestContext: OptimizationRequest;
  data: DashboardData;
  disabledReason: string | null;
  isLoading: boolean;
  error: string | null;
  comparison: RecoveryComparison | null;
  activeDisruption: DisruptionEvent | null;
  solveTimeMs: number;
  onTrigger: (disruption: DisruptionEvent) => void;
  onReset: () => void;
  onReturnToOriginal: () => void;
  onTypeSelect: (type: DisruptionType | null) => void;
}

const DISRUPTION_OPTIONS: { type: DisruptionType; label: string; effect: string }[] = [
  { type: "ASSET_BREAKDOWN", label: "Asset Breakdown", effect: "Removes all remaining blocks tied to the failed asset." },
  { type: "TRACK_UNAVAILABLE", label: "Track Unavailable", effect: "Removes all remaining blocks on the affected track for the day." },
  { type: "EMERGENCY_WORK", label: "Emergency Work", effect: "Injects an urgent repair block the plan must fit in." },
  { type: "POSSESSION_CURTAILMENT", label: "Possession Curtailment", effect: "Removes maintenance windows overlapping the curtailed interval." },
];

export default function DisruptRecoverView({
  requestContext,
  data,
  disabledReason,
  isLoading,
  error,
  comparison,
  activeDisruption,
  solveTimeMs,
  onTrigger,
  onReset,
  onReturnToOriginal,
  onTypeSelect,
}: DisruptRecoverViewProps) {
  const [selectedType, setSelectedType] = useState<DisruptionType | null>(null);
  const [trackId, setTrackId] = useState("");
  const [assetId, setAssetId] = useState("");
  const [startMinute, setStartMinute] = useState(600);
  const [endMinute, setEndMinute] = useState(900);

  const tracks = requestContext.tracks || [];
  const horizon = requestContext.horizon_minutes || 1440;
  const assets = useMemo(() => {
    const seen = new Map<string, string>();
    for (const c of requestContext.candidates || []) {
      if (!seen.has(c.asset_id)) seen.set(c.asset_id, (c.metadata?.asset_name as string) || c.asset_id);
    }
    return Array.from(seen.entries());
  }, [requestContext.candidates]);

  const isRecovered = comparison !== null;

  // Real corridor geometry for the hero backdrop - same track curves and
  // block positions PlanView draws, so DISRUPT/RECOVER never goes to an
  // empty stage: it shows the actual current schedule (baseline pre-trigger,
  // recovered post-trigger), with real changes layered on top.
  const trackIds = useMemo(
    () => (requestContext.tracks && requestContext.tracks.length > 0 ? [...requestContext.tracks].sort() : []),
    [requestContext.tracks]
  );
  const blocksByTrack = useMemo(() => {
    const map = new Map<string, EnrichedScheduledBlock[]>();
    trackIds.forEach((t) => map.set(t, []));
    data.enrichedScheduled.forEach((b) => {
      const list = map.get(b.track_id);
      if (list) list.push(b);
    });
    return map;
  }, [data.enrichedScheduled, trackIds]);
  const changeByBlockId = useMemo(() => {
    const map = new Map<string, BlockChange>();
    (comparison?.changes || []).forEach((c) => map.set(c.block_id, c));
    return map;
  }, [comparison]);
  // Stagger dropped-block badges that share a track so nearby-in-time drops
  // don't render on top of each other (same technique PlanView uses for
  // labels that land close together on the same curve).
  const droppedChanges = useMemo(() => {
    const perTrackCount = new Map<string, number>();
    return (comparison?.changes || [])
      .filter((c) => c.category === "DROPPED")
      .slice(0, 6)
      .map((c) => {
        const key = c.track_id || "";
        const offsetIdx = perTrackCount.get(key) || 0;
        perTrackCount.set(key, offsetIdx + 1);
        return { ...c, offsetIdx };
      });
  }, [comparison]);
  const movedChanges = useMemo(() => {
    const perTrackCount = new Map<string, number>();
    return (comparison?.changes || [])
      .filter((c) => c.category === "MOVED")
      .slice(0, 4)
      .map((c) => {
        const key = c.track_id || "";
        const offsetIdx = perTrackCount.get(key) || 0;
        perTrackCount.set(key, offsetIdx + 1);
        return { ...c, offsetIdx };
      });
  }, [comparison]);
  const kmLabels = useMemo(() => {
    const withKm = (requestContext.candidates || [])
      .filter((c) => typeof c.metadata?.km_location === "number")
      .sort((a, b) => (a.metadata!.km_location as number) - (b.metadata!.km_location as number));
    if (withKm.length === 0) return [];
    const first = withKm[0];
    const last = withKm[withKm.length - 1];
    const mid = withKm[Math.floor(withKm.length / 2)];
    return [first, mid, last].filter((c, i, arr) => arr.findIndex((x) => x.block_id === c.block_id) === i);
  }, [requestContext.candidates]);

  function trackCurve(trackId: string) {
    const idx = trackIds.indexOf(trackId);
    return LANE_CURVES[(idx < 0 ? 0 : idx) % LANE_CURVES.length];
  }

  function changePoint(c: BlockChange, which: "old" | "new"): [number, number] | null {
    const curve = trackCurve(c.track_id || trackIds[0] || "");
    const startMin = which === "old" ? c.old_start_minute : c.new_start_minute;
    const endMin = which === "old" ? c.old_end_minute : c.new_end_minute;
    if (startMin == null || endMin == null || horizon <= 0) return null;
    const t = (startMin + endMin) / 2 / horizon;
    return bezierPoint(t, curve.p0, curve.p1, curve.p2, curve.p3);
  }

  const canSubmit =
    !disabledReason &&
    !isLoading &&
    selectedType !== null &&
    (selectedType === "ASSET_BREAKDOWN"
      ? assetId !== ""
      : selectedType === "EMERGENCY_WORK" || selectedType === "TRACK_UNAVAILABLE"
      ? trackId !== ""
      : trackId !== "" && startMinute < endMinute);

  const handleSelectType = (t: DisruptionType) => {
    setSelectedType(t);
    onTypeSelect(t);
  };

  const handleSubmit = () => {
    if (!canSubmit || !selectedType) return;
    const base = {
      disruption_id: `DISR-${Date.now()}`,
      disruption_type: selectedType,
      corridor_id: requestContext.corridor_id,
      start_minute: 0,
      end_minute: horizon,
    };
    let event: DisruptionEvent;
    if (selectedType === "ASSET_BREAKDOWN") {
      event = { ...base, affected_asset_id: assetId, description: `Asset ${assetId} breakdown - unavailable for maintenance.` };
    } else if (selectedType === "TRACK_UNAVAILABLE") {
      event = { ...base, track_id: trackId, description: `${trackId} closed for emergency civil works.` };
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
      event = { ...base, track_id: trackId, new_candidate: emergencyCandidate, description: `Emergency repair required on ${trackId}.` };
    } else {
      event = {
        ...base,
        track_id: trackId,
        start_minute: startMinute,
        end_minute: endMinute,
        description: `${trackId} possession window curtailed ${formatHHMM(startMinute)}-${formatHHMM(endMinute)}.`,
      };
    }
    onTrigger(event);
  };

  const handleReset = () => {
    setSelectedType(null);
    setTrackId("");
    setAssetId("");
    onTypeSelect(null);
    onReset();
  };

  const interestingChanges = comparison ? comparison.changes.filter((c) => c.category !== "PRESERVED") : [];

  const efficiencyPct = comparison
    ? comparison.before_scheduled_count > 0
      ? Math.round((comparison.after_scheduled_count / comparison.before_scheduled_count) * 100)
      : 0
    : null;

  return (
    <div className="relative w-full overflow-hidden px-gutter-mobile md:px-gutter-desktop pb-space-3xl">
      <div className="absolute top-1/4 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[900px] h-[550px] bg-gradient-to-b from-primary-container/10 via-secondary-container/10 to-transparent blur-[140px] pointer-events-none rounded-full" />
      <div className="absolute top-1/2 right-10 w-[500px] h-[400px] bg-error/10 blur-[160px] pointer-events-none rounded-full" />

      <div className="relative z-20 flex flex-col md:flex-row md:items-end justify-between gap-space-lg pt-space-md mb-space-xl">
        <div className="space-y-space-2xs">
          <div className="flex items-center gap-space-sm flex-wrap">
            {isRecovered ? (
              <span className="inline-flex items-center gap-space-2xs px-space-sm py-space-2xs rounded-full bg-primary-container/20 text-primary font-label-caps text-label-caps tracking-widest uppercase">
                <span className="w-1.5 h-1.5 rounded-full bg-primary-container animate-ping" />
                Recovery Applied
              </span>
            ) : (
              <span className="inline-flex items-center gap-space-2xs px-space-sm py-space-2xs rounded-full bg-error-container/30 text-error font-label-caps text-label-caps tracking-widest uppercase">
                <span className="w-1.5 h-1.5 rounded-full bg-error animate-ping" />
                Awaiting Disruption
              </span>
            )}
            <span className="font-label-mono text-label-mono text-outline">{requestContext.corridor_id}</span>
          </div>
          <h1 className="font-headline-lg text-headline-lg text-primary tracking-tight">
            <span className="italic">Operational Disruption</span>
            <br />
            <span className="not-italic text-on-surface font-light">&amp; Recovery</span>
          </h1>
          <p className="font-body-md text-body-md text-on-surface-variant max-w-2xl font-light">
            Real disruption applied to the live optimization request, re-solved end to end by the
            same CP-SAT solver.
          </p>
        </div>

        <div className="flex items-center gap-space-xs p-space-2xs rounded-full bg-surface-container-low/70 backdrop-blur-2xl self-start md:self-auto flex-wrap">
          <div className="flex items-center gap-space-xs px-space-md py-space-xs rounded-full text-on-surface-variant font-label-mono text-label-mono">
            <span className="material-symbols-outlined text-sm">visibility</span>
            <span>01 OBSERVE</span>
          </div>
          <span className="text-outline-variant font-label-mono text-xs">/</span>
          <div className="flex items-center gap-space-xs px-space-md py-space-xs rounded-full text-on-surface-variant font-label-mono text-label-mono">
            <span className="material-symbols-outlined text-sm">schema</span>
            <span>02 PLAN</span>
          </div>
          <span className="text-outline-variant font-label-mono text-xs">/</span>
          <div
            className={`flex items-center gap-space-xs px-space-md py-space-xs rounded-full font-label-mono text-label-mono ${
              !isRecovered
                ? "bg-error-container/40 text-error shadow-[0_0_18px_rgba(147,0,10,0.4)]"
                : "bg-error-container/15 text-error/70"
            }`}
          >
            <span className={`w-1.5 h-1.5 rounded-full ${!isRecovered ? "bg-error animate-pulse" : "bg-error/50"}`} />
            <span>03 DISRUPT</span>
            {isRecovered && <span className="text-[9px] opacity-70">(APPLIED)</span>}
          </div>
          <span className="text-outline-variant font-label-mono text-xs">/</span>
          <div
            className={`flex items-center gap-space-xs px-space-md py-space-xs rounded-full font-label-mono text-label-mono ${
              isRecovered
                ? "bg-primary-container text-on-primary-container shadow-[0_0_20px_rgba(0,240,255,0.35)]"
                : "text-on-surface-variant"
            }`}
          >
            <span className="material-symbols-outlined text-sm">bolt</span>
            <span className="font-semibold">04 RECOVER</span>
          </div>
        </div>
      </div>

      {/* Scenario selector + live convergence pill */}
      <div className="relative z-20 flex flex-wrap items-center justify-between gap-space-md mb-space-lg">
        <div className="inline-flex flex-wrap p-space-2xs rounded-full bg-surface-container-lowest/80 backdrop-blur-xl">
          {DISRUPTION_OPTIONS.map((opt) => (
            <button
              key={opt.type}
              onClick={() => handleSelectType(opt.type)}
              disabled={!!disabledReason || isRecovered}
              className={`px-space-md py-space-xs rounded-full font-label-mono text-label-mono transition-all flex items-center gap-space-xs disabled:opacity-40 ${
                selectedType === opt.type
                  ? "bg-surface-container-high text-primary shadow-[0_0_12px_rgba(0,240,255,0.15)]"
                  : "text-on-surface-variant hover:text-on-surface"
              }`}
            >
              <span className="w-2 h-2 rounded-full bg-error" />
              {opt.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-space-md px-space-md py-space-xs rounded-full bg-surface-container-low/70 backdrop-blur-xl">
          <div className="flex items-center gap-space-xs font-label-mono text-label-mono text-primary">
            <span className="material-symbols-outlined text-sm text-primary-fixed-dim animate-spin" style={{ animationDuration: "6s" }}>
              cyclone
            </span>
            <span>{solveTimeMs}ms CP-SAT CONVERGENCE</span>
          </div>
          <div className="w-1 h-3 bg-outline-variant/60 rounded-full" />
          <div className="flex items-center gap-space-xs font-label-mono text-label-mono text-on-surface">
            <span className="material-symbols-outlined text-sm text-primary-container">verified_user</span>
            <span>0 HEADWAY CONFLICTS</span>
          </div>
        </div>
      </div>

      {disabledReason && (
        <div className="mb-space-md p-space-md rounded-DEFAULT bg-error-container/20 text-error font-label-mono text-label-mono">
          {disabledReason}
        </div>
      )}

      {/* Real disruption fields for the selected type */}
      {selectedType && !isRecovered && (
        <div className="relative z-20 mb-space-lg p-space-md rounded-DEFAULT bg-surface-container-low/60 backdrop-blur-xl flex flex-wrap items-end gap-space-md">
          <p className="w-full font-body-sm text-body-sm text-on-surface-variant">
            {DISRUPTION_OPTIONS.find((o) => o.type === selectedType)?.effect}
          </p>
          {(selectedType === "TRACK_UNAVAILABLE" || selectedType === "EMERGENCY_WORK" || selectedType === "POSSESSION_CURTAILMENT") && (
            <label className="flex flex-col gap-space-2xs font-label-caps text-label-caps text-on-surface-variant">
              Track
              <select value={trackId} onChange={(e) => setTrackId(e.target.value)} className="bg-surface-container-lowest border border-outline-variant/40 rounded-full px-space-md py-space-xs font-label-mono text-label-mono text-on-surface">
                <option value="">Select…</option>
                {tracks.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </label>
          )}
          {selectedType === "ASSET_BREAKDOWN" && (
            <label className="flex flex-col gap-space-2xs font-label-caps text-label-caps text-on-surface-variant">
              Asset
              <select value={assetId} onChange={(e) => setAssetId(e.target.value)} className="bg-surface-container-lowest border border-outline-variant/40 rounded-full px-space-md py-space-xs font-label-mono text-label-mono text-on-surface min-w-[180px]">
                <option value="">Select…</option>
                {assets.map(([id, label]) => (
                  <option key={id} value={id}>{label}</option>
                ))}
              </select>
            </label>
          )}
          {selectedType === "POSSESSION_CURTAILMENT" && (
            <>
              <label className="flex flex-col gap-space-2xs font-label-caps text-label-caps text-on-surface-variant">
                Start (min)
                <input type="number" min={0} max={horizon} value={startMinute} onChange={(e) => setStartMinute(Number(e.target.value))} className="bg-surface-container-lowest border border-outline-variant/40 rounded-full px-space-md py-space-xs font-label-mono text-label-mono text-on-surface w-24" />
              </label>
              <label className="flex flex-col gap-space-2xs font-label-caps text-label-caps text-on-surface-variant">
                End (min)
                <input type="number" min={0} max={horizon} value={endMinute} onChange={(e) => setEndMinute(Number(e.target.value))} className="bg-surface-container-lowest border border-outline-variant/40 rounded-full px-space-md py-space-xs font-label-mono text-label-mono text-on-surface w-24" />
              </label>
            </>
          )}
          <button
            onClick={handleSubmit}
            disabled={!canSubmit}
            className="px-space-lg py-space-xs rounded-full bg-error text-on-error font-label-mono text-label-mono font-semibold disabled:opacity-30 transition-all"
          >
            {isLoading ? "SIMULATING…" : "APPLY DISRUPTION & RECOVER"}
          </button>
        </div>
      )}

      {error && (
        <div className="relative z-20 mb-space-md p-space-md rounded-DEFAULT bg-error-container/20 text-error font-label-mono text-label-mono">
          Recovery failed: {error}
        </div>
      )}

      {/* Hero stage */}
      <div className="relative w-full rounded-xl bg-surface-container-lowest/60 backdrop-blur-3xl overflow-hidden p-space-md md:p-space-xl shadow-[0_24px_80px_rgba(0,0,0,0.7)]">
        <div className="absolute top-4 left-4 font-label-mono text-label-caps text-outline flex items-center gap-space-xs">
          <span>SECTOR</span>
          <span className="text-outline-variant">|</span>
          <span>{requestContext.corridor_id}</span>
        </div>
        <div className="absolute top-4 right-4 font-label-mono text-label-caps text-primary-fixed-dim flex items-center gap-space-xs">
          <span className="w-2 h-2 rounded-full bg-primary-container animate-ping" />
          <span>TELEMETRIC RESOLUTION: {isRecovered ? comparison!.after_status : "STANDBY"}</span>
        </div>

        <div className="relative w-full h-[480px] md:h-[580px] flex items-center justify-center overflow-hidden">
          <svg className="absolute inset-0 w-full h-full" preserveAspectRatio="none" viewBox="0 0 1280 500">
            <defs>
              <linearGradient id="rail-a-dr" x1="0" x2="1280" y1="0" y2="0" gradientUnits="userSpaceOnUse">
                <stop offset="0%" stopColor="#00f0ff" stopOpacity="0.2" />
                <stop offset="35%" stopColor="#00f0ff" stopOpacity="0.85" />
                <stop offset="70%" stopColor="#00dbe9" stopOpacity="0.9" />
                <stop offset="100%" stopColor="#00f0ff" stopOpacity="0.3" />
              </linearGradient>
              <linearGradient id="rail-b-dr" x1="0" x2="1280" y1="0" y2="0" gradientUnits="userSpaceOnUse">
                <stop offset="0%" stopColor="#ffb688" stopOpacity="0.2" />
                <stop offset="45%" stopColor="#ffb688" stopOpacity="0.8" />
                <stop offset="100%" stopColor="#aa5200" stopOpacity="0.3" />
              </linearGradient>
              <radialGradient cx="50%" cy="50%" id="glow-moved-dr" r="50%">
                <stop offset="0%" stopColor="#ffb688" stopOpacity="0.45" />
                <stop offset="80%" stopColor="#aa5200" stopOpacity="0.15" />
                <stop offset="100%" stopColor="#11131a" stopOpacity="0" />
              </radialGradient>
              <radialGradient cx="50%" cy="50%" id="glow-new-dr" r="50%">
                <stop offset="0%" stopColor="#00f0ff" stopOpacity="0.45" />
                <stop offset="80%" stopColor="#006970" stopOpacity="0.15" />
                <stop offset="100%" stopColor="#11131a" stopOpacity="0" />
              </radialGradient>
            </defs>

            {/* Ambient background rings - same decorative depth as the Plan stage */}
            <circle cx="640" cy="250" r="430" fill="none" stroke="rgba(0,240,255,0.035)" strokeWidth={1} />
            <circle cx="640" cy="250" r="510" fill="none" stroke="rgba(255,182,136,0.03)" strokeWidth={1} />

            {/* Real corridor: same track curves + current schedule PlanView draws */}
            {trackIds.map((trackId, idx) => {
              const curve = trackCurve(trackId);
              const gradId = idx % 2 === 0 ? "rail-a-dr" : "rail-b-dr";
              const baseStroke = idx % 2 === 0 ? "#00f0ff" : "#ffb688";
              return (
                <g key={trackId}>
                  <path d={pathD(curve)} stroke="rgba(255,255,255,0.07)" strokeDasharray="2 8" strokeWidth={20} />
                  <path
                    d={pathD(curve)}
                    stroke={`url(#${gradId})`}
                    strokeLinecap="round"
                    strokeWidth={3}
                    opacity={isRecovered ? 0.5 : 0.85}
                  />
                  <text fill="#849495" fontFamily="'Space Mono', monospace" fontSize={9} letterSpacing={1} x={curve.p0[0]} y={curve.p0[1] - 12}>
                    {trackId}
                  </text>

                  {(blocksByTrack.get(trackId) || []).map((b) => {
                    const change = changeByBlockId.get(b.block_id);
                    const category = change?.category;
                    const isMoved = category === "MOVED";
                    const isNew = category === "NEWLY_SCHEDULED";
                    const emphasize = isMoved || isNew;
                    const markStroke = isMoved ? "#ffb688" : isNew ? "#00f0ff" : baseStroke;
                    const tMid = (b.start_minute + b.end_minute) / 2 / horizon;
                    const [mx, my] = bezierPoint(tMid, curve.p0, curve.p1, curve.p2, curve.p3);
                    const [sx, sy] = bezierPoint(b.start_minute / horizon, curve.p0, curve.p1, curve.p2, curve.p3);
                    const [ex, ey] = bezierPoint(b.end_minute / horizon, curve.p0, curve.p1, curve.p2, curve.p3);
                    return (
                      <g key={b.block_id}>
                        {emphasize && (
                          <ellipse cx={mx} cy={my} fill={`url(#${isNew ? "glow-new-dr" : "glow-moved-dr"})`} opacity={0.7} rx={70} ry={26} />
                        )}
                        <path d={`M ${sx} ${sy} L ${ex} ${ey}`} stroke={markStroke} strokeLinecap="round" strokeWidth={emphasize ? 5 : 3} opacity={emphasize ? 1 : 0.55} />
                        <circle cx={sx} cy={sy} r={3} fill={markStroke} />
                        <circle cx={ex} cy={ey} r={3} fill={markStroke} />
                      </g>
                    );
                  })}
                </g>
              );
            })}

            {/* Dotted reroute paths for moved blocks - old position to new position */}
            {isRecovered &&
              movedChanges.map((c) => {
                const oldPt = changePoint(c, "old");
                const newPt = changePoint(c, "new");
                if (!oldPt || !newPt) return null;
                return (
                  <path
                    key={`reroute-${c.block_id}`}
                    d={`M ${oldPt[0]} ${oldPt[1]} L ${newPt[0]} ${newPt[1]}`}
                    stroke="#ffb688"
                    strokeWidth={1.5}
                    strokeDasharray="4 4"
                    opacity={0.6}
                  />
                );
              })}

            {/* Exact rupture/reroute points (badges above may be staggered off these when several land close together) */}
            {isRecovered &&
              droppedChanges.map((c) => {
                const pt = changePoint(c, "old");
                if (!pt) return null;
                return <circle key={`rupture-${c.block_id}`} cx={pt[0]} cy={pt[1]} r={4} fill="none" stroke="#ffb4ab" strokeWidth={1.5} strokeDasharray="2 2" />;
              })}
            {isRecovered &&
              movedChanges.map((c) => {
                const pt = changePoint(c, "new");
                if (!pt) return null;
                return <circle key={`reroute-pt-${c.block_id}`} cx={pt[0]} cy={pt[1]} r={4} fill="none" stroke="#ffb688" strokeWidth={1.5} strokeDasharray="2 2" />;
              })}
          </svg>

          {kmLabels.length > 0 && (
            <div className="absolute bottom-3 left-4 right-4 flex justify-between items-center text-on-surface-variant font-label-mono text-[10px] opacity-70 pointer-events-none">
              {kmLabels.map((c) => (
                <div key={c.block_id} className="flex flex-col items-center">
                  <span className="text-primary font-bold">KM {(c.metadata!.km_location as number).toFixed(1)}</span>
                  <span>{String(c.metadata?.asset_name ?? c.asset_id)}</span>
                </div>
              ))}
            </div>
          )}

          {!isRecovered && (
            <div className="absolute top-[42%] left-1/2 -translate-x-1/2 -translate-y-1/2 flex flex-col items-center">
              <div className="w-16 h-16 rounded-full border border-dashed border-error/50 animate-spin flex items-center justify-center p-space-xs" style={{ animationDuration: "12s" }}>
                <div className="w-10 h-10 rounded-full bg-error/20 flex items-center justify-center">
                  <span className="material-symbols-outlined text-error text-xl animate-bounce">warning</span>
                </div>
              </div>
              <div className="mt-space-xs px-space-md py-space-sm rounded-lg bg-surface-container-lowest/90 backdrop-blur-2xl shadow-[0_8px_32px_rgba(147,0,10,0.4)] text-center min-w-[220px]">
                <div className="font-label-caps text-label-caps text-error tracking-widest uppercase">Select a disruption scenario</div>
                <div className="font-label-mono text-label-mono text-outline-variant mt-0.5">
                  {activeDisruption?.description ?? "Pick a type above, fill its real fields, then apply."}
                </div>
              </div>
            </div>
          )}

          {isRecovered &&
            droppedChanges.map((c) => {
              const pt = changePoint(c, "old");
              if (!pt) return null;
              const pos = toViewBoxPct(pt);
              return (
                <div
                  key={c.block_id}
                  className="absolute flex flex-col items-center"
                  style={{
                    left: pos.left,
                    top: pos.top,
                    transform: `translate(calc(-50% + ${c.offsetIdx * 95}px), -50%)`,
                  }}
                >
                  <div className="w-12 h-12 rounded-full border border-dashed border-error/50 flex items-center justify-center p-space-xs">
                    <div className="w-8 h-8 rounded-full bg-error/20 flex items-center justify-center">
                      <span className="material-symbols-outlined text-error text-base">block</span>
                    </div>
                  </div>
                  <div className="mt-space-2xs px-space-sm py-space-2xs rounded-lg bg-surface-container-lowest/90 backdrop-blur-2xl shadow-[0_8px_32px_rgba(147,0,10,0.4)] text-center min-w-[160px]">
                    <div className="font-label-caps text-[10px] text-error tracking-widest uppercase">DROPPED: {c.block_id}</div>
                    <div className="font-label-mono text-[10px] text-outline-variant mt-0.5">{c.reason}</div>
                  </div>
                </div>
              );
            })}

          {isRecovered &&
            movedChanges.map((c) => {
              const pt = changePoint(c, "new");
              if (!pt) return null;
              const pos = toViewBoxPct(pt);
              return (
                <div
                  key={c.block_id}
                  className="absolute flex flex-col items-start"
                  style={{
                    left: pos.left,
                    top: pos.top,
                    transform: `translate(calc(-50% + ${c.offsetIdx * 160}px), -100%)`,
                  }}
                >
                  <div className="flex items-center gap-space-xs px-space-sm py-space-2xs rounded-full bg-primary-container text-on-primary-container font-label-mono text-[10px] shadow-[0_0_24px_rgba(0,240,255,0.4)]">
                    <span className="w-2 h-2 rounded-full bg-on-primary-container animate-ping" />
                    <span className="font-bold">MOVED: {c.block_id}</span>
                  </div>
                  <div className="mt-space-2xs px-space-sm py-space-2xs rounded-md bg-surface-container/80 backdrop-blur-md font-label-mono text-[10px] text-primary flex items-center gap-space-2xs">
                    <span className="material-symbols-outlined text-xs">alt_route</span>
                    <span>
                      {formatHHMM(c.old_start_minute!)}&ndash;{formatHHMM(c.old_end_minute!)}
                      {" → "}
                      {formatHHMM(c.new_start_minute!)}&ndash;{formatHHMM(c.new_end_minute!)}
                    </span>
                  </div>
                </div>
              );
            })}

          <div className="absolute bottom-6 right-6 hidden xl:flex items-center gap-space-md p-space-md rounded-xl bg-surface-container-lowest/80 backdrop-blur-3xl">
            <div className="relative w-12 h-12 flex items-center justify-center">
              <svg className="w-12 h-12 -rotate-90" viewBox="0 0 48 48">
                <circle cx="24" cy="24" fill="none" opacity={0.3} r="20" stroke="#3b494b" strokeWidth={3} />
                <circle
                  cx="24" cy="24" fill="none" r="20" stroke="#00f0ff" strokeLinecap="round" strokeWidth={3}
                  strokeDasharray="125.6"
                  strokeDashoffset={125.6 - (125.6 * (efficiencyPct ?? 100)) / 100}
                />
              </svg>
              <span className="absolute font-label-mono text-label-caps text-primary font-bold">
                {efficiencyPct != null ? `${efficiencyPct}%` : "—"}
              </span>
            </div>
            <div className="space-y-0.5">
              <div className="font-label-caps text-label-caps text-on-surface-variant">RECOVERY RATIO</div>
              <div className="font-label-mono text-label-mono text-on-surface">
                {comparison ? `${comparison.preservedCount} Preserved • ${comparison.movedCount} Rescheduled` : "Awaiting disruption"}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Before/after grid - only meaningful once a real recovery exists */}
      {isRecovered && comparison && (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-space-lg mt-space-xl">
          <div className="lg:col-span-7 flex flex-col justify-between p-space-xl rounded-xl bg-surface-container-low/50 backdrop-blur-2xl">
            <div>
              <div className="flex items-center justify-between gap-space-md mb-space-md flex-wrap">
                <div className="flex items-center gap-space-xs">
                  <span className="font-label-caps text-label-caps text-primary uppercase tracking-widest">Spatial Convergence Profile</span>
                  <span className="px-space-xs py-0.5 rounded text-[10px] font-label-mono bg-primary-container/20 text-primary">CP-SAT VERIFIED</span>
                </div>
              </div>
              <h3 className="font-headline-sm text-headline-sm text-on-surface mb-space-sm">
                {comparison.after_status === "OPTIMAL" || comparison.after_status === "FEASIBLE"
                  ? "Recovered Schedule Verified Feasible"
                  : "Recovery Could Not Reach a Feasible Schedule"}
              </h3>
              <p className="font-body-md text-body-md text-on-surface-variant font-light max-w-xl mb-space-lg">
                {activeDisruption?.description} The solver re-ran against the disrupted request:{" "}
                {comparison.before_scheduled_count} scheduled blocks became {comparison.after_scheduled_count}, with{" "}
                {comparison.preservedCount} preserved unchanged, {comparison.movedCount} moved, {comparison.droppedCount} dropped,
                and {comparison.newlyScheduledCount} newly scheduled.
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-space-md">
                <div className="p-space-md rounded bg-surface-container-lowest/70">
                  <div className="font-label-caps text-label-caps text-outline mb-space-2xs">SCHEDULED BLOCKS</div>
                  <div className="font-headline-md text-headline-md text-primary font-light">
                    {comparison.before_scheduled_count} <span className="text-sm font-label-mono text-on-surface-variant">&rarr; {comparison.after_scheduled_count}</span>
                  </div>
                </div>
                <div className="p-space-md rounded bg-surface-container-lowest/70">
                  <div className="font-label-caps text-label-caps text-outline mb-space-2xs">PRESERVED</div>
                  <div className="font-headline-md text-headline-md text-secondary-fixed-dim font-light">
                    {comparison.preservedCount} <span className="text-sm font-label-mono text-on-surface-variant">UNCHANGED</span>
                  </div>
                </div>
                <div className="p-space-md rounded bg-surface-container-lowest/70">
                  <div className="font-label-caps text-label-caps text-outline mb-space-2xs">SOLVE TIME</div>
                  <div className="font-headline-md text-headline-md text-primary font-light">
                    {solveTimeMs} <span className="text-sm font-label-mono text-on-surface-variant">MS</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div className="lg:col-span-5 flex flex-col justify-between p-space-xl rounded-xl bg-surface-container-low/50 backdrop-blur-2xl">
            <div>
              <div className="flex items-center justify-between mb-space-md">
                <div className="font-label-caps text-label-caps text-secondary-fixed tracking-widest uppercase">Recovery Change Log</div>
                <span className="font-label-mono text-label-mono text-primary">{interestingChanges.length} CHANGED</span>
              </div>
              <div className="space-y-space-xs max-h-64 overflow-y-auto">
                {interestingChanges.length === 0 && (
                  <div className="p-space-sm rounded bg-surface-container-lowest/60 font-label-mono text-label-mono text-on-surface-variant">
                    No changes beyond the disruption itself.
                  </div>
                )}
                {interestingChanges.map((c) => (
                  <div key={c.block_id} className="flex items-center justify-between p-space-sm rounded bg-surface-container-lowest/60">
                    <div className="flex items-center gap-space-sm">
                      <span
                        className={`w-1.5 h-1.5 rounded-full ${
                          c.category === "DROPPED" ? "bg-error" : c.category === "MOVED" ? "bg-secondary-fixed" : "bg-primary-container"
                        }`}
                      />
                      <div>
                        <div className="font-label-mono text-label-mono text-on-surface">{c.block_id}</div>
                        <div className="font-label-caps text-label-caps text-outline">
                          {c.category === "DROPPED" && c.reason}
                          {c.category === "MOVED" &&
                            `${formatHHMM(c.old_start_minute!)}–${formatHHMM(c.old_end_minute!)} → ${formatHHMM(c.new_start_minute!)}–${formatHHMM(c.new_end_minute!)}`}
                          {c.category === "NEWLY_SCHEDULED" && `${c.track_id} @ ${formatHHMM(c.new_start_minute!)}`}
                        </div>
                      </div>
                    </div>
                    <span
                      className={`px-space-xs py-space-2xs rounded font-label-mono text-label-mono ${
                        c.category === "DROPPED" ? "text-error" : c.category === "MOVED" ? "text-secondary" : "text-primary"
                      }`}
                    >
                      {c.category.replace("_", " ")}
                    </span>
                  </div>
                ))}
              </div>
            </div>
            <div className="mt-space-lg pt-space-md flex items-center justify-between font-label-mono text-label-mono text-outline">
              <span>UNSCHEDULED: {comparison.before_unscheduled_count} &rarr; {comparison.after_unscheduled_count}</span>
            </div>
          </div>
        </div>
      )}

      {/* Action dock */}
      {isRecovered && (
        <div className="sticky bottom-6 z-40 mt-space-2xl w-full">
          <div className="w-full max-w-5xl mx-auto p-space-sm rounded-full bg-surface-container-lowest/90 backdrop-blur-3xl shadow-[0_12px_48px_rgba(0,0,0,0.8)] flex flex-col sm:flex-row items-center justify-between gap-space-md">
            <div className="flex items-center gap-space-sm pl-space-md">
              <div className="relative flex items-center justify-center">
                <span className="w-3 h-3 rounded-full bg-primary-container animate-ping absolute" />
                <span className="w-2.5 h-2.5 rounded-full bg-primary" />
              </div>
              <div>
                <div className="font-headline-sm text-sm text-primary tracking-tight">Recovery Result: {comparison!.after_status}</div>
                <div className="font-label-mono text-label-mono text-on-surface-variant">{activeDisruption?.description}</div>
              </div>
            </div>
            <div className="flex items-center gap-space-sm w-full sm:w-auto justify-end">
              <button
                onClick={handleReset}
                className="px-space-md py-space-xs rounded-full bg-surface-container-high hover:bg-surface-container-highest text-on-surface font-label-mono text-label-mono transition-all flex items-center gap-space-xs"
              >
                <span className="material-symbols-outlined text-sm">restart_alt</span>
                <span>Trigger Another Disruption</span>
              </button>
              <button
                onClick={onReturnToOriginal}
                className="px-space-lg py-space-sm rounded-full bg-primary-container hover:bg-primary-fixed-dim text-on-primary-container font-label-mono text-label-mono font-semibold shadow-[0_0_24px_rgba(0,240,255,0.4)] transition-all flex items-center gap-space-xs"
              >
                <span className="material-symbols-outlined text-base">undo</span>
                <span>Return to Original Plan</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
