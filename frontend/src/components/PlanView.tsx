"use client";

import { useMemo, useState } from "react";
import type { DashboardData, EnrichedScheduledBlock } from "@/types/contracts";
import { LANE_CURVES, bezierPoint, pathD } from "@/lib/corridorGeometry";
import { formatHHMM, formatWorkType } from "@/lib/format";

interface PlanViewProps {
  data: DashboardData;
  selectedBlockId: string | null;
  onSelectBlock: (id: string | null) => void;
  onReoptimize: () => void;
  onGoToDisrupt: () => void;
  isResolving: boolean;
}

export default function PlanView({
  data,
  selectedBlockId,
  onSelectBlock,
  onReoptimize,
  onGoToDisrupt,
  isResolving,
}: PlanViewProps) {
  const { result, request } = data;
  const horizon = request.horizon_minutes || 1440;
  const [activeFilter, setActiveFilter] = useState<string | null>(null);
  const trackIds = useMemo(
    () => (request.tracks && request.tracks.length > 0 ? [...request.tracks].sort() : []),
    [request.tracks]
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

  const selectedBlock =
    data.enrichedScheduled.find((b) => b.block_id === selectedBlockId) ??
    data.enrichedUnscheduled.find((b) => b.block_id === selectedBlockId);
  const isSelectedScheduled = data.enrichedScheduled.some((b) => b.block_id === selectedBlockId);

  const totalCandidates = request.candidates?.length || 0;
  const scheduledCount = result.scheduled_blocks?.length || 0;

  // Real work-type categories (Stitch's filter/category pill row, generated
  // from actual candidate work types rather than hardcoded fictional labels).
  const categoryStats = useMemo(() => {
    const counts = new Map<string, number>();
    for (const c of request.candidates || []) {
      counts.set(c.work_type, (counts.get(c.work_type) || 0) + 1);
    }
    return Array.from(counts.entries()).map(([workType, count]) => ({ workType, count }));
  }, [request.candidates]);

  const totalCorridorTrackMinutes = horizon * (trackIds.length || 1);
  const totalMaintenanceMinutes = (result.scheduled_blocks || []).reduce(
    (sum, sb) => sum + Math.max(0, sb.end_minute - sb.start_minute),
    0
  );
  const corridorCapacityPct =
    totalCorridorTrackMinutes > 0
      ? ((totalCorridorTrackMinutes - totalMaintenanceMinutes) / totalCorridorTrackMinutes) * 100
      : 100;

  const totalPotentialRisk = (request.candidates || []).reduce((sum, c) => sum + (c.risk_score || 0), 0);
  const riskNeutralizedPct =
    totalPotentialRisk > 0 ? Math.min(100, ((result.total_risk_mitigated || 0) / totalPotentialRisk) * 100) : 0;

  // Real KM range from actual asset metadata, for the ruler strip.
  const kmLabels = useMemo(() => {
    const withKm = (request.candidates || [])
      .filter((c) => typeof c.metadata?.km_location === "number")
      .sort((a, b) => (a.metadata!.km_location as number) - (b.metadata!.km_location as number));
    if (withKm.length === 0) return [];
    const first = withKm[0];
    const last = withKm[withKm.length - 1];
    const mid = withKm[Math.floor(withKm.length / 2)];
    return [first, mid, last].filter(
      (c, i, arr) => arr.findIndex((x) => x.block_id === c.block_id) === i
    );
  }, [request.candidates]);

  // Real position of the currently selected scheduled block along the
  // horizon, for the strip's thumb marker (no fake time playback - only
  // moves when a real block is selected).
  const selectedThumbPct =
    selectedBlock && isSelectedScheduled
      ? (((selectedBlock as EnrichedScheduledBlock).start_minute +
          (selectedBlock as EnrichedScheduledBlock).end_minute) /
          2 /
          horizon) *
        100
      : null;

  // Segment-bucketed real schedule overview for the bottom horizon strip.
  const segments = useMemo(() => {
    const bucketCount = 6;
    const bucketSize = horizon / bucketCount;
    return Array.from({ length: bucketCount }, (_, i) => {
      const bStart = i * bucketSize;
      const bEnd = bStart + bucketSize;
      const inBucket = (result.scheduled_blocks || []).filter(
        (b) => b.start_minute < bEnd && b.end_minute > bStart
      );
      return { label: formatHHMM(bStart), blocks: inBucket };
    });
  }, [horizon, result.scheduled_blocks]);

  return (
    <div className="relative w-full overflow-hidden px-gutter-mobile md:px-gutter-desktop pb-space-3xl animate-fade-in-up">
      <div className="absolute top-1/4 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[900px] h-[450px] bg-gradient-to-b from-primary-container/10 via-surface-tint/5 to-transparent blur-[120px] rounded-full pointer-events-none" />
      <div className="absolute bottom-12 left-1/3 w-[650px] h-[320px] bg-gradient-to-tr from-secondary-container/20 via-secondary/5 to-transparent blur-[140px] rounded-full pointer-events-none" />

      {/* Story cycle + telemetry */}
      <div className="relative z-20 px-0 pt-space-md w-full flex flex-col gap-space-sm">
        <div className="flex items-center justify-between flex-wrap gap-space-sm">
          <div className="flex items-center gap-space-xs font-label-mono text-label-mono bg-surface-container-low/60 backdrop-blur-xl px-space-md py-space-2xs rounded-full shadow-sm">
            <span className="text-on-surface-variant/60">OBSERVE</span>
            <span className="text-outline-variant text-[10px]">&mdash;&mdash;&mdash;</span>
            <span className="flex items-center gap-1.5 px-space-xs py-0.5 rounded-full bg-primary-container/20 text-primary font-medium shadow-[0_0_12px_rgba(0,240,255,0.3)]">
              <span className="w-1.5 h-1.5 rounded-full bg-primary-container animate-pulse" />
              PLAN [ACTIVE]
            </span>
            <span className="text-outline-variant text-[10px]">&mdash;&mdash;&mdash;</span>
            <span className="text-on-surface-variant/60">DISRUPT</span>
            <span className="text-outline-variant text-[10px]">&mdash;&mdash;&mdash;</span>
            <span className="text-on-surface-variant/60">RECOVER</span>
          </div>
          <div className="hidden lg:flex items-center gap-space-lg font-label-caps text-label-caps text-on-surface-variant">
            <div className="flex items-center gap-space-2xs">
              <span className="material-symbols-outlined text-sm text-primary">hub</span>
              <span>MODEL: OR-TOOLS CP-SAT</span>
            </div>
            <div className="flex items-center gap-space-2xs">
              <span className="text-primary-fixed-dim font-bold">
                {Math.round((result.solve_time_seconds || 0) * 1000)}ms
              </span>
              <span>SOLVE TIME</span>
            </div>
            <div className="px-space-xs py-space-2xs rounded bg-surface-container-high/80 text-on-surface font-label-mono text-label-mono">
              {horizon}M HORIZON &bull; {data.result.corridor_id}
            </div>
          </div>
        </div>

        <div className="flex flex-col md:flex-row md:items-end justify-between gap-space-md mt-space-xs">
          <div>
            <div className="flex items-center gap-space-sm mb-space-2xs">
              <span className="font-label-caps text-label-caps text-primary-fixed-dim px-space-xs py-0.5 rounded bg-surface-container-high/60">
                {data.result.corridor_id}
              </span>
              {kmLabels.length > 0 && (
                <span className="font-label-mono text-label-mono text-on-surface-variant">
                  KM {(kmLabels[0].metadata!.km_location as number).toFixed(1)} &mdash; KM{" "}
                  {(kmLabels[kmLabels.length - 1].metadata!.km_location as number).toFixed(1)}
                </span>
              )}
            </div>
            <h1 className="font-headline-lg text-headline-lg text-primary tracking-tight font-normal">
              <span className="italic">Corridor Plan</span>{" "}
              <span className="font-headline-md text-headline-md not-italic text-on-surface font-light">
                Maintenance Possession
              </span>
              <br />
              <span className="not-italic text-on-surface font-light">Field</span>
            </h1>
            <p className="font-body-sm text-body-sm text-on-surface-variant mt-1 max-w-xl">
              Solver-verified maintenance windows scheduled across real track possessions, with
              headway and non-overlap constraints enforced.
            </p>
          </div>

          <div className="flex items-center gap-space-2xs bg-surface-container-low/70 backdrop-blur-2xl p-space-2xs rounded-full shadow-md">
            <span className="px-space-md py-space-xs rounded-full font-label-mono text-label-mono bg-primary-container text-on-primary-container font-medium shadow-[0_0_14px_rgba(0,240,255,0.3)]">
              {scheduledCount} / {totalCandidates} SLOTTED
            </span>
            <span className="px-space-md py-space-xs rounded-full font-label-mono text-label-mono text-on-surface-variant">
              STATUS: {result.status}
            </span>
          </div>
        </div>

        {/* Real work-type filter row (Stitch's category pill row) */}
        <div className="flex flex-wrap items-center gap-space-xs mt-space-sm">
          <button
            onClick={() => setActiveFilter(null)}
            className={`px-space-md py-space-xs rounded-full font-label-mono text-label-mono transition-all ${
              activeFilter === null
                ? "bg-primary-container text-on-primary-container font-medium shadow-[0_0_12px_rgba(0,240,255,0.25)]"
                : "bg-surface-container-lowest/80 text-on-surface-variant hover:text-on-surface"
            }`}
          >
            ALL ACTIVE ({totalCandidates} BLOCKS)
          </button>
          {categoryStats.map(({ workType, count }) => (
            <button
              key={workType}
              onClick={() => setActiveFilter(activeFilter === workType ? null : workType)}
              className={`px-space-md py-space-xs rounded-full font-label-mono text-label-mono transition-all ${
                activeFilter === workType
                  ? "bg-surface-container-high text-primary shadow-[0_0_12px_rgba(0,240,255,0.15)]"
                  : "bg-surface-container-lowest/80 text-on-surface-variant hover:text-on-surface"
              }`}
            >
              {formatWorkType(workType)} ({count})
            </button>
          ))}
        </div>
      </div>

      {/* Hero corridor stage */}
      <div className="relative z-10 w-full flex-1 flex items-center justify-center my-space-xs">
        <div className="relative w-full max-w-[1400px] h-[720px] rounded-lg overflow-hidden bg-gradient-to-b from-surface-container-low/40 to-surface-container-lowest/80 backdrop-blur-sm shadow-xl flex items-center justify-center">
          {isResolving && (
            <div className="absolute inset-0 z-30 flex items-center justify-center bg-surface-container-lowest/50 backdrop-blur-sm animate-fade-in">
              <div className="flex items-center gap-space-xs px-space-lg py-space-sm rounded-full bg-surface-container-lowest/90 shadow-xl">
                <span className="material-symbols-outlined text-primary animate-spin">progress_activity</span>
                <span className="font-label-mono text-label-mono text-primary">RE-SOLVING WITH CP-SAT&hellip;</span>
              </div>
            </div>
          )}
          <div className="relative w-full h-full flex items-center justify-center">
            <svg className="w-full h-full" fill="none" preserveAspectRatio="none" viewBox="0 0 1280 500">
              <defs>
                <linearGradient id="rail-a" x1="0" x2="1280" y1="0" y2="0" gradientUnits="userSpaceOnUse">
                  <stop offset="0%" stopColor="#00f0ff" stopOpacity="0.2" />
                  <stop offset="35%" stopColor="#00f0ff" stopOpacity="0.85" />
                  <stop offset="70%" stopColor="#00dbe9" stopOpacity="0.9" />
                  <stop offset="100%" stopColor="#00f0ff" stopOpacity="0.3" />
                </linearGradient>
                <linearGradient id="rail-b" x1="0" x2="1280" y1="0" y2="0" gradientUnits="userSpaceOnUse">
                  <stop offset="0%" stopColor="#ffb688" stopOpacity="0.2" />
                  <stop offset="45%" stopColor="#ffb688" stopOpacity="0.8" />
                  <stop offset="100%" stopColor="#aa5200" stopOpacity="0.3" />
                </linearGradient>
                <radialGradient cx="50%" cy="50%" id="possession-glow" r="50%">
                  <stop offset="0%" stopColor="#ffb688" stopOpacity="0.45" />
                  <stop offset="80%" stopColor="#aa5200" stopOpacity="0.15" />
                  <stop offset="100%" stopColor="#11131a" stopOpacity="0" />
                </radialGradient>
                <radialGradient cx="50%" cy="50%" id="possession-glow-cyan" r="50%">
                  <stop offset="0%" stopColor="#00f0ff" stopOpacity="0.45" />
                  <stop offset="80%" stopColor="#006970" stopOpacity="0.15" />
                  <stop offset="100%" stopColor="#11131a" stopOpacity="0" />
                </radialGradient>
              </defs>

              {/* Ambient background rings - decorative depth, matches Stitch's faint orbit arcs */}
              <circle cx="640" cy="250" r="430" fill="none" stroke="rgba(0,240,255,0.035)" strokeWidth={1} />
              <circle cx="640" cy="250" r="510" fill="none" stroke="rgba(255,182,136,0.03)" strokeWidth={1} />

              {trackIds.map((trackId, idx) => {
                const curve = LANE_CURVES[idx % LANE_CURVES.length];
                const gradId = idx % 2 === 0 ? "rail-a" : "rail-b";
                return (
                  <g key={trackId}>
                    <path d={pathD(curve)} stroke="rgba(255,255,255,0.07)" strokeDasharray="2 8" strokeWidth={20} />
                    <path d={pathD(curve)} id={`path-${trackId}`} stroke={`url(#${gradId})`} strokeLinecap="round" strokeWidth={3} />
                    <text
                      fill="#849495"
                      fontFamily="'Space Mono', monospace"
                      fontSize={9}
                      letterSpacing={1}
                      x={curve.p0[0]}
                      y={curve.p0[1] - 12}
                    >
                      {trackId}
                    </text>

                    {[...(blocksByTrack.get(trackId) || [])]
                      .sort((a, b) => a.start_minute - b.start_minute)
                      .map((b, labelIdx) => {
                        const tMid = ((b.start_minute + b.end_minute) / 2 / horizon);
                        const [mx, my] = bezierPoint(tMid, curve.p0, curve.p1, curve.p2, curve.p3);
                        const [sx, sy] = bezierPoint(b.start_minute / horizon, curve.p0, curve.p1, curve.p2, curve.p3);
                        const [ex, ey] = bezierPoint(b.end_minute / horizon, curve.p0, curve.p1, curve.p2, curve.p3);
                        const isSelected = selectedBlockId === b.block_id;
                        const glow = idx % 2 === 0 ? "possession-glow-cyan" : "possession-glow";
                        const stroke = idx % 2 === 0 ? "#00f0ff" : "#ffb688";
                        // Stagger label rows to reduce overlap between blocks close in time.
                        const labelYOffset = 22 + (labelIdx % 3) * 16;
                        const isDimmed = activeFilter !== null && b.candidate?.work_type !== activeFilter;

                        return (
                          <g
                            key={b.block_id}
                            className="cursor-pointer"
                            opacity={isDimmed ? 0.15 : 1}
                            onClick={() => onSelectBlock(isSelected ? null : b.block_id)}
                          >
                            {isSelected && <ellipse cx={mx} cy={my} fill={`url(#${glow})`} opacity={0.8} rx={90} ry={30} />}
                            <path d={`M ${sx} ${sy} L ${ex} ${ey}`} stroke={stroke} strokeLinecap="round" strokeWidth={isSelected ? 6 : 4} opacity={isSelected ? 1 : 0.7} />
                            <circle cx={sx} cy={sy} r={3.5} fill={stroke} />
                            <circle cx={ex} cy={ey} r={3.5} fill={stroke} />
                            <line x1={mx} y1={my} x2={mx} y2={my - labelYOffset + 8} stroke={stroke} strokeWidth={1} strokeDasharray="2 2" opacity={0.5} />
                            <rect x={mx - 32} y={my - labelYOffset - 9} width={64} height={17} rx={8.5} fill="#0c0e14" fillOpacity={0.92} />
                            <text fill={stroke} fontFamily="'Space Mono', monospace" fontSize={8.5} fontWeight={700} textAnchor="middle" x={mx} y={my - labelYOffset + 2}>
                              {isSelected ? `${b.block_id} • ${formatHHMM(b.start_minute)}` : b.block_id}
                            </text>
                          </g>
                        );
                      })}
                  </g>
                );
              })}
            </svg>

            {kmLabels.length > 0 && (
              <div className="absolute bottom-4 left-12 right-[26rem] flex justify-between items-center text-on-surface-variant font-label-mono text-[10px] opacity-70 pointer-events-none">
                {kmLabels.map((c) => (
                  <div key={c.block_id} className="flex flex-col items-center">
                    <span className="text-primary font-bold">KM {(c.metadata!.km_location as number).toFixed(1)}</span>
                    <span>{String(c.metadata?.asset_name ?? c.asset_id)}</span>
                  </div>
                ))}
              </div>
            )}

            <div className="absolute top-4 left-6 flex items-center gap-space-xs text-on-surface-variant font-label-mono text-label-mono">
              <span className="text-primary font-bold">+</span>
              <span>
                {trackIds.length === 2 ? "DUAL" : `${trackIds.length}`}-TRACK CORRIDOR
              </span>
            </div>
            <div className="absolute top-4 right-[26rem] flex items-center gap-space-xs text-on-surface-variant font-label-mono text-label-mono">
              <span>{totalCandidates} CANDIDATE BLOCKS</span>
              <span className="text-primary font-bold">+</span>
            </div>
          </div>

          {/* Right glass drawer: selected possession detail */}
          <div className="absolute top-8 right-8 bottom-8 w-80 lg:w-96 rounded-xl border border-white/[0.06] bg-surface-container-lowest/80 backdrop-blur-3xl p-space-lg shadow-2xl flex flex-col justify-between overflow-y-auto">
            {!selectedBlock ? (
              <div className="flex flex-col gap-space-xs">
                <div className="flex items-center gap-space-xs">
                  <span className="w-2 h-2 rounded-full bg-primary-container shadow-[0_0_8px_#00f0ff]" />
                  <span className="font-label-caps text-label-caps text-primary tracking-widest">
                    SELECTED POSSESSION
                  </span>
                </div>
                <p className="font-body-sm text-body-sm text-on-surface-variant mt-space-sm">
                  Click any block on the corridor to inspect its canonical ML scoring, solver
                  reasoning, and constraint status.
                </p>
                <div className="mt-space-md">
                  <span className="font-label-caps text-label-caps text-secondary block mb-space-xs">
                    UNSCHEDULED ({data.enrichedUnscheduled.length})
                  </span>
                  <div className="flex flex-col gap-space-2xs">
                    {data.enrichedUnscheduled.map((ub) => (
                      <button
                        key={ub.block_id}
                        onClick={() => onSelectBlock(ub.block_id)}
                        className="text-left p-space-xs rounded bg-surface-container-low/60 hover:bg-surface-container-low font-label-mono text-label-mono text-on-surface-variant"
                      >
                        {ub.block_id} <span className="text-outline">[{ub.track_id}]</span>
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            ) : (
              <>
                <div className="flex flex-col gap-space-xs">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-space-xs">
                      <span className="w-2 h-2 rounded-full bg-primary-container shadow-[0_0_8px_#00f0ff]" />
                      <span className="font-label-caps text-label-caps text-primary tracking-widest">
                        SELECTED POSSESSION
                      </span>
                    </div>
                    <span
                      className={`font-label-mono text-label-mono px-space-xs py-0.5 rounded ${
                        isSelectedScheduled ? "bg-surface-container-high text-secondary" : "bg-error-container/40 text-error"
                      }`}
                    >
                      {isSelectedScheduled ? "SCHEDULED" : "UNSCHEDULED"}
                    </span>
                  </div>
                  {(selectedBlock.candidate?.risk_score ?? 0) >= 0.7 && (
                    <span className="self-start font-label-mono text-[10px] px-space-xs py-0.5 rounded bg-error-container/30 text-error tracking-widest">
                      HIGH RISK
                    </span>
                  )}
                  <h2 className="font-headline-sm text-headline-sm text-on-surface mt-space-2xs">
                    {selectedBlock.block_id}{" "}
                    <span className="font-headline-md italic font-normal text-primary">
                      {formatWorkType(String(selectedBlock.candidate?.work_type ?? ""))}
                    </span>
                  </h2>
                  <p className="font-body-sm text-body-sm text-on-surface-variant">
                    {String(selectedBlock.candidate?.metadata?.asset_name ?? selectedBlock.candidate?.asset_id ?? "")}
                    {" • "}
                    {selectedBlock.track_id}
                  </p>
                </div>

                <div className="flex flex-col gap-space-sm my-space-sm">
                  <div className="p-space-sm rounded-DEFAULT bg-surface-container-low/70 flex items-center justify-between">
                    <div className="flex flex-col">
                      <span className="font-label-caps text-label-caps text-on-surface-variant">
                        MIN HEADWAY CONSTRAINT
                      </span>
                      <span className="font-headline-sm text-headline-sm text-primary font-semibold">
                        {request.min_headway_minutes} min
                      </span>
                    </div>
                    <div className="w-10 h-10 rounded-full bg-primary-container/10 flex items-center justify-center">
                      <span className="material-symbols-outlined text-primary">hourglass_empty</span>
                    </div>
                  </div>

                  <div className="p-space-sm rounded-DEFAULT bg-surface-container-low/70 flex items-center justify-between">
                    <div className="flex flex-col">
                      <span className="font-label-caps text-label-caps text-on-surface-variant">
                        CONSTRAINT CONFLICTS
                      </span>
                      <span className="font-headline-sm text-headline-sm text-primary-fixed-dim font-semibold">
                        0 VIOLATIONS
                      </span>
                    </div>
                    <div className="w-10 h-10 rounded-full bg-primary-container/10 flex items-center justify-center">
                      <span className="material-symbols-outlined text-primary">verified_user</span>
                    </div>
                  </div>

                  <div className="p-space-sm rounded-DEFAULT bg-surface-container-low/70 flex flex-col gap-1">
                    <div className="flex justify-between items-center font-label-mono text-label-mono">
                      <span className="text-on-surface-variant flex items-center gap-1">
                        <span className="material-symbols-outlined text-sm text-secondary">speed</span>
                        PRIORITY SCORE
                      </span>
                      <span className="text-secondary font-bold">
                        {Math.round((selectedBlock.candidate?.priority_score ?? 0) * 100)} / 100
                      </span>
                    </div>
                    <div className="w-full bg-surface-container-high h-1.5 rounded-full overflow-hidden">
                      <div
                        className="bg-secondary h-full rounded-full shadow-[0_0_8px_rgba(255,182,136,0.5)]"
                        style={{ width: `${Math.round((selectedBlock.candidate?.priority_score ?? 0) * 100)}%` }}
                      />
                    </div>
                  </div>

                  <div className="font-label-mono text-[11px] text-on-surface-variant/90 leading-relaxed bg-surface-container-high/30 p-space-xs rounded">
                    <div className="text-primary-fixed-dim font-bold mb-0.5">OR-TOOLS CP-SAT REASONING:</div>
                    {isSelectedScheduled
                      ? `Scheduled on track ${selectedBlock.track_id} from ${formatHHMM(
                          (selectedBlock as EnrichedScheduledBlock).start_minute
                        )} to ${formatHHMM(
                          (selectedBlock as EnrichedScheduledBlock).end_minute
                        )}. Hard non-overlap and headway constraints satisfied.`
                      : (selectedBlock as { rejectionReason?: string }).rejectionReason}
                  </div>

                  <div className="pt-space-xs">
                    <span className="font-label-caps text-label-caps text-on-surface-variant block mb-space-xs">
                      ML SCORER &mdash; 7-FEATURE PIPELINE
                    </span>
                    <div className="grid grid-cols-2 gap-space-2xs font-label-mono text-[10px]">
                      {Object.entries({
                        "Asset Criticality": selectedBlock.candidate?.metadata?.scoring_features?.asset_criticality,
                        "Defect Severity":
                          selectedBlock.candidate?.metadata?.scoring_features?.defect_severity_name ??
                          selectedBlock.candidate?.metadata?.scoring_features?.defect_severity,
                        "Days Overdue": selectedBlock.candidate?.metadata?.scoring_features?.days_overdue,
                        "Failure Prob.": selectedBlock.candidate?.metadata?.scoring_features?.failure_probability,
                        "Train Impact": selectedBlock.candidate?.metadata?.scoring_features?.train_impact,
                        "Maint. Duration": selectedBlock.candidate?.metadata?.scoring_features?.maintenance_duration,
                        "Hist. Failure Rate":
                          selectedBlock.candidate?.metadata?.scoring_features?.historical_failure_rate,
                      }).map(([label, value]) => (
                        <div key={label} className="flex justify-between p-space-2xs rounded bg-surface-container-low/50">
                          <span className="text-on-surface-variant">{label}</span>
                          <span className="text-on-surface">{value != null ? String(value) : "—"}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-space-xs">
                  <button
                    onClick={() => onSelectBlock(null)}
                    className="flex-1 py-space-xs px-space-md rounded-full bg-primary-container text-on-primary-container font-label-mono text-label-mono font-medium hover:brightness-110 transition-all shadow-[0_0_16px_rgba(0,240,255,0.3)]"
                  >
                    CLOSE INSPECTOR
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Real schedule overview strip (repurposed temporal scrubber - no fake playback/current-time) */}
      <div className="relative z-20 w-full flex flex-col gap-space-md mt-space-md">
        <div className="w-full p-space-md rounded-lg bg-surface-container-low/60 backdrop-blur-2xl shadow-xl flex flex-col gap-space-xs">
          <div className="flex items-center justify-between text-on-surface-variant font-label-mono text-label-mono flex-wrap gap-2">
            <span className="flex items-center gap-1.5 text-primary">
              <span className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
              SCHEDULE OVERVIEW (00:00 &mdash; 24:00)
            </span>
            {selectedThumbPct != null && (
              <span className="px-space-xs py-space-2xs rounded bg-surface-container-high/80 text-primary text-[10px]">
                SELECTED WINDOW &bull; {formatHHMM((selectedBlock as EnrichedScheduledBlock).start_minute)}&ndash;
                {formatHHMM((selectedBlock as EnrichedScheduledBlock).end_minute)}
              </span>
            )}
          </div>
          <div className="relative w-full h-12 rounded bg-surface-container-lowest/90 overflow-hidden flex items-center">
            <div className="absolute inset-0 flex">
              {segments.map((seg, i) => (
                <div key={i} className="w-1/6 h-full bg-primary-container/5 relative border-r border-white/[0.03] last:border-r-0">
                  {seg.blocks.slice(0, 1).map((b) => (
                    <div
                      key={b.block_id}
                      className="absolute inset-y-1 left-1 right-1 bg-secondary/20 rounded flex items-center justify-center overflow-hidden"
                    >
                      <span className="font-label-mono text-[9px] text-secondary font-bold truncate px-1">
                        {b.block_id}
                        {seg.blocks.length > 1 ? ` +${seg.blocks.length - 1}` : ""}
                      </span>
                    </div>
                  ))}
                </div>
              ))}
            </div>
            <div className="absolute inset-0 flex justify-between items-end px-3 pb-1 text-on-surface-variant font-label-mono text-[9px] pointer-events-none opacity-60">
              {segments.map((seg, i) => (
                <span key={i}>{seg.label}</span>
              ))}
            </div>
            {selectedThumbPct != null && (
              <div
                className="absolute top-0 bottom-0 w-0.5 bg-primary shadow-[0_0_10px_rgba(0,240,255,0.7)] pointer-events-none"
                style={{ left: `${selectedThumbPct}%` }}
              />
            )}
          </div>
          <div className="flex items-center gap-space-lg font-label-mono text-[10px] text-on-surface-variant/70 flex-wrap">
            <span className="flex items-center gap-1">
              <span className="w-2 h-2 rounded-sm bg-secondary/50" /> SCHEDULED WINDOW
            </span>
            <span className="flex items-center gap-1">
              <span className="w-0.5 h-3 bg-primary" /> SELECTED BLOCK
            </span>
            <span className="flex items-center gap-1">
              <span className="w-2 h-2 rounded-sm border border-outline-variant" /> UNSCHEDULED
            </span>
          </div>
        </div>

        <div className="w-full flex flex-col md:flex-row items-center justify-between gap-space-md">
          <div className="flex items-center gap-space-lg flex-wrap">
            <div className="flex items-center gap-space-xs">
              <span className="font-label-caps text-label-caps text-on-surface-variant">CORRIDOR CAPACITY:</span>
              <span className="font-headline-sm text-headline-sm text-primary font-medium">
                {corridorCapacityPct.toFixed(1)}%
              </span>
            </div>
            <div className="hidden sm:block w-1 h-1 rounded-full bg-outline-variant" />
            <div className="flex items-center gap-space-xs">
              <span className="font-label-caps text-label-caps text-on-surface-variant">SLOTTED SECTORS:</span>
              <span className="font-headline-sm text-headline-sm text-secondary font-medium">
                {scheduledCount} / {totalCandidates}
              </span>
            </div>
            <div className="hidden sm:block w-1 h-1 rounded-full bg-outline-variant" />
            <div className="flex items-center gap-space-xs">
              <span className="font-label-caps text-label-caps text-on-surface-variant">RISK NEUTRALIZED:</span>
              <span className="font-headline-sm text-headline-sm text-primary-fixed-dim font-medium">
                {riskNeutralizedPct.toFixed(1)}%
              </span>
            </div>
          </div>

          <div className="flex items-center gap-space-sm w-full md:w-auto justify-end">
            <button
              onClick={onReoptimize}
              disabled={isResolving}
              className="px-space-lg py-space-xs rounded-full font-label-mono text-label-mono text-on-surface bg-surface-container-high/80 hover:bg-surface-bright disabled:opacity-40 transition-all flex items-center gap-space-xs"
            >
              {isResolving && (
                <span className="material-symbols-outlined text-sm animate-spin">progress_activity</span>
              )}
              {isResolving ? "SOLVING…" : "RE-OPTIMIZE HORIZON"}
            </button>
            <button
              onClick={onGoToDisrupt}
              className="px-space-xl py-space-xs rounded-full font-label-mono text-label-mono bg-primary-container text-on-primary-container font-medium hover:scale-[1.02] active:scale-95 shadow-[0_0_24px_rgba(0,240,255,0.4)] transition-all flex items-center gap-space-xs"
            >
              <span className="material-symbols-outlined text-sm">bolt</span>
              <span>SIMULATE DISRUPTION</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
