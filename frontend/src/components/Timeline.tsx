"use client";

import React, { useMemo, useState } from "react";
import type {
  DashboardData,
  EnrichedScheduledBlock,
  EnrichedUnscheduledBlock,
} from "@/types/contracts";

// ── Color palette matching canonical railway WorkTypes (Aether Void, toned down) ──

const WORK_TYPE_COLORS: Record<string, { fill: string; stroke: string; label: string }> = {
  TRACK_RENEWAL: { fill: "#c98a4a", stroke: "#e8a15c", label: "Track Renewal" },
  BALLAST_TAMPING: { fill: "#8064c9", stroke: "#a389e8", label: "Ballast Tamping" },
  OHE_MAINTENANCE: { fill: "#1fa8b0", stroke: "#2dd4dd", label: "OHE Overhead" },
  SIGNALLING_INTERLOCKING: { fill: "#9660c9", stroke: "#b98ce8", label: "Signalling & S&T" },
  ROUTINE_INSPECTION: { fill: "#3f7fc9", stroke: "#5fa0e8", label: "Inspection" },
  EMERGENCY_REPAIR: { fill: "#c9584f", stroke: "#e07d74", label: "Emergency Repair" },
};

const DEFAULT_BLOCK_COLOR = { fill: "#4a5164", stroke: "#7a8296", label: "Other" };

const MARGIN = { top: 44, right: 32, bottom: 56, left: 104 };
const TRACK_HEIGHT = 76;
const TRACK_GAP = 22;
const BLOCK_PADDING = 8;
const BLOCK_RADIUS = 7;
const REJECTED_ROW_HEIGHT = 42;

function formatMinuteToHHMM(minute: number): string {
  const h = Math.floor(minute / 60);
  const m = minute % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

interface TooltipData {
  x: number;
  y: number;
  block: EnrichedScheduledBlock | EnrichedUnscheduledBlock;
  isScheduled: boolean;
}

function BlockTooltip({ data }: { data: TooltipData }) {
  const { block, isScheduled } = data;
  const candidate = block.candidate;
  const workType = (candidate?.work_type || block.work_type || "") as string;
  const colorSpec = WORK_TYPE_COLORS[workType] ?? DEFAULT_BLOCK_COLOR;

  const duration = isScheduled
    ? (block as EnrichedScheduledBlock).end_minute - (block as EnrichedScheduledBlock).start_minute
    : candidate?.duration_minutes ?? 0;

  return (
    <div
      className="absolute z-50 pointer-events-none"
      style={{ left: data.x, top: data.y - 10, transform: "translate(-50%, -100%)" }}
    >
      <div className="glass-panel rounded-xl px-3.5 py-2.5 text-xs max-w-[320px]">
        <div className="flex items-center justify-between gap-2 mb-1.5 pb-1.5 border-b border-white/[0.08]">
          <div className="flex items-center gap-2">
            <span className="font-mono-data font-bold text-white text-sm">{block.block_id}</span>
            <span
              className="px-1.5 py-0.5 rounded-full text-[0.62rem] font-semibold uppercase tracking-wide"
              style={{
                backgroundColor: colorSpec.fill + "33",
                color: colorSpec.stroke,
                border: `1px solid ${colorSpec.stroke}44`,
              }}
            >
              {colorSpec.label}
            </span>
          </div>
          <span className="text-[0.62rem] font-mono-data text-muted">{duration} min</span>
        </div>

        {isScheduled && (
          <div className="text-muted mb-1.5 font-mono-data text-[0.7rem] flex items-center justify-between">
            <span>
              <span className="text-white font-semibold">
                {formatMinuteToHHMM((block as EnrichedScheduledBlock).start_minute)}
              </span>
              {" → "}
              <span className="text-white font-semibold">
                {formatMinuteToHHMM((block as EnrichedScheduledBlock).end_minute)}
              </span>
            </span>
            <span className="text-accent-cyan font-semibold text-[0.62rem] uppercase">Scheduled</span>
          </div>
        )}

        {!isScheduled && (
          <div className="text-red-300 text-[0.7rem] mb-2 bg-red-400/10 border border-red-400/20 px-2 py-1 rounded-lg">
            &times; {(block as EnrichedUnscheduledBlock).rejectionReason}
          </div>
        )}

        {candidate && (
          <div className="grid grid-cols-2 gap-x-3 gap-y-1 mt-1 text-[0.7rem] pt-1 border-t border-white/[0.06]">
            <div className="text-muted">Asset</div>
            <div className="text-white/90 font-mono-data text-[0.65rem] truncate" title={candidate.asset_id}>
              {candidate.asset_id}
            </div>
            {candidate.metadata?.asset_name && (
              <>
                <div className="text-muted">Location</div>
                <div className="text-white/80 text-[0.65rem] truncate" title={String(candidate.metadata.asset_name)}>
                  {String(candidate.metadata.asset_name)}
                </div>
              </>
            )}
            <div className="text-muted">Priority / Risk</div>
            <div className="text-white/90 font-mono-data">
              P:{candidate.priority_score.toFixed(2)} | R:{candidate.risk_score.toFixed(2)}
            </div>
            {candidate.mutual_exclusion_group && (
              <>
                <div className="text-muted">Shared machine/crew</div>
                <div className="text-accent-amber font-mono-data text-[0.65rem]">
                  {candidate.mutual_exclusion_group}
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

interface TimelineProps {
  data: DashboardData;
  onBlockSelect?: (blockId: string) => void;
  selectedBlockId?: string | null;
}

export default function Timeline({ data, onBlockSelect, selectedBlockId }: TimelineProps) {
  const [tooltip, setTooltip] = useState<TooltipData | null>(null);

  const horizonMinutes = data.request.horizon_minutes || 1440;

  const trackIds = useMemo(() => {
    if (data.request.tracks && data.request.tracks.length > 0) {
      return [...data.request.tracks].sort();
    }
    const tracks = new Set<string>();
    data.enrichedScheduled.forEach((b) => tracks.add(b.track_id));
    return Array.from(tracks).sort();
  }, [data.request.tracks, data.enrichedScheduled]);

  const blocksByTrack = useMemo(() => {
    const map = new Map<string, EnrichedScheduledBlock[]>();
    trackIds.forEach((t) => map.set(t, []));
    data.enrichedScheduled.forEach((b) => {
      const list = map.get(b.track_id);
      if (list) list.push(b);
    });
    return map;
  }, [data.enrichedScheduled, trackIds]);

  const possessionWindows = data.request.possession_windows;
  const possessionWindowsByTrack = useMemo(() => {
    const map = new Map<string, typeof possessionWindows>();
    trackIds.forEach((t) => map.set(t, []));
    (possessionWindows || []).forEach((pw) => {
      const list = map.get(pw.track_id);
      if (list) list.push(pw);
    });
    return map;
  }, [possessionWindows, trackIds]);

  const chartWidth = 1180;
  const innerWidth = chartWidth - MARGIN.left - MARGIN.right;
  const trackAreaHeight = trackIds.length * TRACK_HEIGHT + (trackIds.length - 1) * TRACK_GAP;
  const rejectedAreaHeight =
    data.enrichedUnscheduled.length > 0 ? 44 + data.enrichedUnscheduled.length * REJECTED_ROW_HEIGHT : 0;
  const chartHeight = MARGIN.top + trackAreaHeight + rejectedAreaHeight + MARGIN.bottom;

  const xScale = (minute: number) =>
    MARGIN.left + (Math.max(0, Math.min(minute, horizonMinutes)) / horizonMinutes) * innerWidth;

  const hourTicks = useMemo(() => {
    const ticks: number[] = [];
    for (let m = 0; m <= horizonMinutes; m += 120) ticks.push(m);
    return ticks;
  }, [horizonMinutes]);

  const handleBlockHover = (
    e: React.MouseEvent,
    block: EnrichedScheduledBlock | EnrichedUnscheduledBlock,
    isScheduled: boolean
  ) => {
    const svgEl = (e.target as SVGElement).closest("svg");
    if (!svgEl) return;
    const rect = svgEl.getBoundingClientRect();
    setTooltip({ x: e.clientX - rect.left, y: e.clientY - rect.top, block, isScheduled });
  };

  const trackLabel = trackIds.length === 2 ? "Dual-Track Corridor" : `${trackIds.length}-Track Corridor`;

  return (
    <div className="w-full h-full relative flex flex-col">
      {/* Legend */}
      <div className="flex flex-wrap items-center gap-4 mb-3 px-1">
        <span className="text-[0.62rem] uppercase font-bold tracking-widest text-muted font-mono-data">
          {trackLabel}
        </span>
        {Object.entries(WORK_TYPE_COLORS).map(([typeKey, spec]) => (
          <div key={typeKey} className="flex items-center gap-1.5 text-muted">
            <span
              className="w-2.5 h-2.5 rounded-sm"
              style={{ backgroundColor: spec.fill, border: `1px solid ${spec.stroke}` }}
            />
            <span className="text-[0.68rem] font-medium">{spec.label}</span>
          </div>
        ))}
        <div className="flex items-center gap-1.5 text-muted ml-auto">
          <span className="w-2.5 h-2.5 rounded-sm bg-accent-cyan/10 border border-dashed border-accent-cyan/40" />
          <span className="text-[0.68rem]">Possession Window</span>
        </div>
      </div>

      <div className="overflow-x-auto rounded-2xl border border-white/[0.06] bg-black/25 relative flex-1">
        {tooltip && <BlockTooltip data={tooltip} />}

        <svg
          viewBox={`0 0 ${chartWidth} ${chartHeight}`}
          className="w-full h-auto select-none"
          style={{ minWidth: "900px" }}
        >
          <defs>
            <pattern id="possession-stripe" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <line x1="0" y1="0" x2="0" y2="8" stroke="#2dd4dd" strokeWidth="1.5" strokeOpacity="0.2" />
            </pattern>
            <filter id="softGlow" x="-40%" y="-40%" width="180%" height="180%">
              <feGaussianBlur stdDeviation="4" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>

          {hourTicks.map((tickMinute) => {
            const x = xScale(tickMinute);
            return (
              <g key={tickMinute}>
                <line
                  x1={x} y1={MARGIN.top - 8} x2={x} y2={MARGIN.top + trackAreaHeight}
                  stroke="#ffffff12" strokeWidth={1} strokeDasharray="3 3"
                />
                <text x={x} y={MARGIN.top - 16} textAnchor="middle" className="text-[10px] font-mono-data fill-white/40 font-medium">
                  {formatMinuteToHHMM(tickMinute)}
                </text>
              </g>
            );
          })}

          {trackIds.map((trackId, idx) => {
            const y = MARGIN.top + idx * (TRACK_HEIGHT + TRACK_GAP);
            const scheduledBlocks = blocksByTrack.get(trackId) || [];
            const pWindows = possessionWindowsByTrack.get(trackId) || [];

            return (
              <g key={trackId}>
                <rect
                  x={MARGIN.left} y={y} width={innerWidth} height={TRACK_HEIGHT} rx={10}
                  fill="#ffffff05" stroke="#ffffff10" strokeWidth={1}
                />

                <g transform={`translate(${MARGIN.left - 12}, ${y + TRACK_HEIGHT / 2})`}>
                  <rect x={-86} y={-14} width={82} height={28} rx={14} fill="#ffffff0a" stroke="#ffffff18" strokeWidth={1} />
                  <text x={-45} y={4} textAnchor="middle" className="text-xs font-mono-data font-bold fill-white/80">
                    {trackId}
                  </text>
                </g>

                {pWindows.map((pw) => {
                  const px1 = xScale(pw.start_minute);
                  const px2 = xScale(pw.end_minute);
                  const pWidth = Math.max(4, px2 - px1);
                  return (
                    <g key={pw.window_id}>
                      <rect
                        x={px1} y={y + 3} width={pWidth} height={TRACK_HEIGHT - 6}
                        fill="url(#possession-stripe)" stroke="#2dd4dd" strokeWidth={1} strokeOpacity={0.4} rx={6}
                      />
                      <text x={px1 + 6} y={y + 16} className="text-[9px] font-mono-data fill-accent-cyan/70 font-semibold">
                        {pw.window_id} ({pw.end_minute - pw.start_minute}m)
                      </text>
                    </g>
                  );
                })}

                {scheduledBlocks.map((sb) => {
                  const bx1 = xScale(sb.start_minute);
                  const bx2 = xScale(sb.end_minute);
                  const blockWidth = Math.max(8, bx2 - bx1);
                  const isSelected = selectedBlockId === sb.block_id;
                  const workTypeKey = String(sb.work_type);
                  const color = WORK_TYPE_COLORS[workTypeKey] ?? DEFAULT_BLOCK_COLOR;

                  return (
                    <g
                      key={sb.block_id}
                      className="cursor-pointer transition-transform duration-100"
                      onClick={() => onBlockSelect?.(sb.block_id)}
                      onMouseEnter={(e) => handleBlockHover(e, sb, true)}
                      onMouseLeave={() => setTooltip(null)}
                    >
                      <rect
                        x={bx1 + 1} y={y + BLOCK_PADDING + 8}
                        width={Math.max(6, blockWidth - 2)} height={TRACK_HEIGHT - BLOCK_PADDING * 2 - 8}
                        rx={BLOCK_RADIUS} fill={color.fill} fillOpacity={isSelected ? 1 : 0.85}
                        stroke={isSelected ? "#ffffff" : color.stroke} strokeWidth={isSelected ? 2.5 : 1.2}
                        filter={isSelected ? "url(#softGlow)" : undefined}
                      />
                      {blockWidth > 42 && (
                        <>
                          <text x={bx1 + 6} y={y + BLOCK_PADDING + 24} className="text-[11px] font-mono-data font-bold fill-white pointer-events-none">
                            {sb.block_id}
                          </text>
                          <text x={bx1 + 6} y={y + BLOCK_PADDING + 38} className="text-[9px] font-mono-data fill-white/75 pointer-events-none">
                            {sb.end_minute - sb.start_minute}m | P:{sb.priority_score.toFixed(2)}
                          </text>
                        </>
                      )}
                    </g>
                  );
                })}
              </g>
            );
          })}

          {data.enrichedUnscheduled.length > 0 && (
            <g transform={`translate(0, ${MARGIN.top + trackAreaHeight + 26})`}>
              <rect x={MARGIN.left} y={0} width={innerWidth} height={24} rx={6} fill="#e0665c14" stroke="#e0665c30" />
              <text x={MARGIN.left + 14} y={16} className="text-[10px] font-bold uppercase tracking-wider fill-red-300 font-mono-data">
                Solver Rejections ({data.enrichedUnscheduled.length})
              </text>

              {data.enrichedUnscheduled.map((ub, idx) => {
                const rowY = 30 + idx * REJECTED_ROW_HEIGHT;
                const isSelected = selectedBlockId === ub.block_id;
                const ex1 = xScale(ub.earliest_start_minute || 0);
                const ex2 = xScale(ub.latest_end_minute || horizonMinutes);
                const spanWidth = Math.max(12, ex2 - ex1);

                return (
                  <g
                    key={ub.block_id}
                    className="cursor-pointer"
                    onClick={() => onBlockSelect?.(ub.block_id)}
                    onMouseEnter={(e) => handleBlockHover(e, ub, false)}
                    onMouseLeave={() => setTooltip(null)}
                  >
                    <rect
                      x={ex1} y={rowY} width={spanWidth} height={REJECTED_ROW_HEIGHT - 6} rx={6}
                      fill="#1a121500" stroke={isSelected ? "#e0665c" : "#e0665c30"}
                      strokeWidth={isSelected ? 2 : 1} strokeDasharray="4 3"
                    />
                    <rect
                      x={ex1 + 2} y={rowY + 4} width={Math.min(spanWidth - 4, 180)} height={REJECTED_ROW_HEIGHT - 14}
                      rx={5} fill="#e0665c14" stroke="#e0665c50" strokeWidth={1}
                    />
                    <text x={ex1 + 8} y={rowY + 17} className="text-[10px] font-mono-data font-bold fill-red-300">
                      {ub.block_id} ({ub.track_id})
                    </text>
                    <text x={ex1 + 8} y={rowY + 27} className="text-[9px] fill-red-300/75 truncate">
                      {ub.rejectionReason}
                    </text>
                  </g>
                );
              })}
            </g>
          )}

          <g transform={`translate(0, ${chartHeight - MARGIN.bottom + 20})`}>
            <line x1={MARGIN.left} y1={0} x2={MARGIN.left + innerWidth} y2={0} stroke="#ffffff18" strokeWidth={1.5} />
            {hourTicks.map((tickMinute) => {
              const x = xScale(tickMinute);
              return (
                <g key={tickMinute}>
                  <line x1={x} y1={0} x2={x} y2={6} stroke="#ffffff28" strokeWidth={1.5} />
                  <text x={x} y={18} textAnchor="middle" className="text-[10px] font-mono-data fill-white/40">
                    {formatMinuteToHHMM(tickMinute)}
                  </text>
                </g>
              );
            })}
          </g>
        </svg>
      </div>
    </div>
  );
}
