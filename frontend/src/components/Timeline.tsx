"use client";

import React, { useMemo, useState } from "react";
import type {
  DashboardData,
  EnrichedScheduledBlock,
  EnrichedUnscheduledBlock,
} from "@/types/contracts";

// ── Color palette matching canonical railway WorkTypes ──────────────

const WORK_TYPE_COLORS: Record<string, { fill: string; stroke: string; label: string }> = {
  TRACK_RENEWAL: { fill: "#d97706", stroke: "#f59e0b", label: "Track Renewal" },
  BALLAST_TAMPING: { fill: "#7c3aed", stroke: "#a78bfa", label: "Ballast Tamping" },
  OHE_MAINTENANCE: { fill: "#0891b2", stroke: "#22d3ee", label: "OHE Overhead" },
  SIGNALLING_INTERLOCKING: { fill: "#9333ea", stroke: "#c084fc", label: "Signalling & S&T" },
  ROUTINE_INSPECTION: { fill: "#2563eb", stroke: "#3b82f6", label: "Inspection" },
  EMERGENCY_REPAIR: { fill: "#e11d48", stroke: "#fb7185", label: "Emergency Repair" },
};

const DEFAULT_BLOCK_COLOR = { fill: "#475569", stroke: "#94a3b8", label: "Other" };

// ── Layout constants ─────────────────────────────────────────────────

const MARGIN = { top: 48, right: 36, bottom: 64, left: 110 };
const TRACK_HEIGHT = 76;
const TRACK_GAP = 20;
const BLOCK_PADDING = 8;
const BLOCK_RADIUS = 5;
const REJECTED_ROW_HEIGHT = 44;

// ── Helpers ──────────────────────────────────────────────────────────

function formatMinuteToHHMM(minute: number): string {
  const h = Math.floor(minute / 60);
  const m = minute % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

// ── Tooltip component ────────────────────────────────────────────────

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
    ? (block as EnrichedScheduledBlock).end_minute -
      (block as EnrichedScheduledBlock).start_minute
    : candidate?.duration_minutes ?? 0;

  return (
    <div
      className="absolute z-50 pointer-events-none"
      style={{
        left: data.x,
        top: data.y - 10,
        transform: "translate(-50%, -100%)",
      }}
    >
      <div className="bg-[#101726] border border-[#2a3c5a] rounded-lg px-3.5 py-2.5 shadow-2xl text-xs max-w-[320px] backdrop-blur-md">
        <div className="flex items-center justify-between gap-2 mb-1.5 pb-1.5 border-b border-[#1e293b]">
          <div className="flex items-center gap-2">
            <span className="font-mono font-bold text-white text-sm">
              {block.block_id}
            </span>
            <span
              className="px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide"
              style={{
                backgroundColor: colorSpec.fill + "33",
                color: colorSpec.stroke,
                border: `1px solid ${colorSpec.stroke}44`,
              }}
            >
              {colorSpec.label}
            </span>
          </div>
          <span className="text-[10px] font-mono text-[#94a3b8]">
            {duration} min
          </span>
        </div>

        {isScheduled && (
          <div className="text-[#94a3b8] mb-1.5 font-mono text-[11px] flex items-center justify-between">
            <span>
              <span className="text-[#cbd5e1] font-semibold">
                {formatMinuteToHHMM((block as EnrichedScheduledBlock).start_minute)}
              </span>
              {" → "}
              <span className="text-[#cbd5e1] font-semibold">
                {formatMinuteToHHMM((block as EnrichedScheduledBlock).end_minute)}
              </span>
            </span>
            <span className="text-emerald-400 font-semibold text-[10px] uppercase">
              Scheduled
            </span>
          </div>
        )}

        {!isScheduled && (
          <div className="text-red-400 text-[11px] mb-2 bg-red-500/10 border border-red-500/20 px-2 py-1 rounded">
            ✕ {(block as EnrichedUnscheduledBlock).rejectionReason}
          </div>
        )}

        {candidate && (
          <div className="grid grid-cols-2 gap-x-3 gap-y-1 mt-1 text-[11px] pt-1 border-t border-[#1e293b]">
            <div className="text-[#64748b]">Asset</div>
            <div className="text-[#e2e8f0] font-mono text-[10px] truncate" title={candidate.asset_id}>
              {candidate.asset_id}
            </div>

            {candidate.metadata?.asset_name && (
              <>
                <div className="text-[#64748b]">Location</div>
                <div className="text-[#cbd5e1] text-[10px] truncate" title={String(candidate.metadata.asset_name)}>
                  {String(candidate.metadata.asset_name)}
                </div>
              </>
            )}

            <div className="text-[#64748b]">Priority / Risk</div>
            <div className="text-[#e2e8f0] font-mono">
              P:{candidate.priority_score.toFixed(2)} | R:{candidate.risk_score.toFixed(2)}
            </div>

            {candidate.mutual_exclusion_group && (
              <>
                <div className="text-[#64748b]">Exclusion Gang</div>
                <div className="text-amber-400 font-mono text-[10px]">
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

// ── Main Timeline Component ──────────────────────────────────────────

interface TimelineProps {
  data: DashboardData;
  onBlockSelect?: (blockId: string) => void;
  selectedBlockId?: string | null;
}

export default function Timeline({
  data,
  onBlockSelect,
  selectedBlockId,
}: TimelineProps) {
  const [tooltip, setTooltip] = useState<TooltipData | null>(null);

  const horizonMinutes = data.request.horizon_minutes || 1440;

  // Tracks list from request or derived from blocks
  const trackIds = useMemo(() => {
    if (data.request.tracks && data.request.tracks.length > 0) {
      return [...data.request.tracks].sort();
    }
    const tracks = new Set<string>();
    data.enrichedScheduled.forEach((b) => tracks.add(b.track_id));
    return Array.from(tracks).sort();
  }, [data.request.tracks, data.enrichedScheduled]);

  // Group scheduled blocks by track
  const blocksByTrack = useMemo(() => {
    const map = new Map<string, EnrichedScheduledBlock[]>();
    trackIds.forEach((t) => map.set(t, []));
    data.enrichedScheduled.forEach((b) => {
      const list = map.get(b.track_id);
      if (list) list.push(b);
    });
    return map;
  }, [data.enrichedScheduled, trackIds]);

  // Group possession windows by track
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

  // SVG dimensions
  const chartWidth = 1180;
  const innerWidth = chartWidth - MARGIN.left - MARGIN.right;
  const trackAreaHeight =
    trackIds.length * TRACK_HEIGHT + (trackIds.length - 1) * TRACK_GAP;
  const rejectedAreaHeight =
    data.enrichedUnscheduled.length > 0
      ? 48 + data.enrichedUnscheduled.length * REJECTED_ROW_HEIGHT
      : 0;
  const chartHeight =
    MARGIN.top + trackAreaHeight + rejectedAreaHeight + MARGIN.bottom;

  // Scale: minute → pixel
  const xScale = (minute: number) =>
    MARGIN.left + (Math.max(0, Math.min(minute, horizonMinutes)) / horizonMinutes) * innerWidth;

  // Generate 2-hour interval tick marks (0, 120, 240... 1440)
  const hourTicks = useMemo(() => {
    const ticks: number[] = [];
    for (let m = 0; m <= horizonMinutes; m += 120) {
      ticks.push(m);
    }
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
    setTooltip({
      x: e.clientX - rect.left,
      y: e.clientY - rect.top,
      block,
      isScheduled,
    });
  };

  return (
    <div className="w-full relative">
      {/* Work Type Legend */}
      <div className="flex flex-wrap items-center gap-4 mb-3.5 px-2">
        <span className="text-[10px] uppercase font-bold tracking-wider text-[#64748b]">
          Work Types:
        </span>
        {Object.entries(WORK_TYPE_COLORS).map(([typeKey, spec]) => (
          <div key={typeKey} className="flex items-center gap-1.5 text-xs text-[#94a3b8]">
            <span
              className="w-3 h-3 rounded-sm"
              style={{
                backgroundColor: spec.fill,
                border: `1px solid ${spec.stroke}`,
              }}
            />
            <span className="text-[11px] font-medium">{spec.label}</span>
          </div>
        ))}
        <div className="flex items-center gap-1.5 text-xs text-[#64748b] ml-auto">
          <span className="w-3 h-3 rounded-sm bg-emerald-500/10 border border-dashed border-emerald-500/40" />
          <span className="text-[11px]">Possession Window</span>
        </div>
      </div>

      {/* SVG Canvas Container */}
      <div className="overflow-x-auto rounded-lg border border-[#1e293b] bg-[#070b14] shadow-inner relative">
        {tooltip && <BlockTooltip data={tooltip} />}

        <svg
          viewBox={`0 0 ${chartWidth} ${chartHeight}`}
          className="w-full h-auto select-none"
          style={{ minWidth: "900px" }}
        >
          <defs>
            <pattern
              id="possession-stripe"
              width="8"
              height="8"
              patternUnits="userSpaceOnUse"
              patternTransform="rotate(45)"
            >
              <line
                x1="0"
                y1="0"
                x2="0"
                y2="8"
                stroke="#10b981"
                strokeWidth="1.5"
                strokeOpacity="0.18"
              />
            </pattern>
          </defs>

          {/* Time Axis Grid Lines & Header */}
          {hourTicks.map((tickMinute) => {
            const x = xScale(tickMinute);
            return (
              <g key={tickMinute}>
                <line
                  x1={x}
                  y1={MARGIN.top - 8}
                  x2={x}
                  y2={MARGIN.top + trackAreaHeight}
                  stroke="#1e293b"
                  strokeWidth={1}
                  strokeDasharray="3 3"
                />
                <text
                  x={x}
                  y={MARGIN.top - 16}
                  textAnchor="middle"
                  className="text-[10px] font-mono fill-[#64748b] font-medium"
                >
                  {formatMinuteToHHMM(tickMinute)}
                </text>
              </g>
            );
          })}

          {/* Track Lanes */}
          {trackIds.map((trackId, idx) => {
            const y = MARGIN.top + idx * (TRACK_HEIGHT + TRACK_GAP);
            const scheduledBlocks = blocksByTrack.get(trackId) || [];
            const pWindows = possessionWindowsByTrack.get(trackId) || [];

            return (
              <g key={trackId}>
                {/* Track Lane Background */}
                <rect
                  x={MARGIN.left}
                  y={y}
                  width={innerWidth}
                  height={TRACK_HEIGHT}
                  rx={6}
                  fill="#0c1220"
                  stroke="#1b253b"
                  strokeWidth={1}
                />

                {/* Track Label Pill */}
                <g transform={`translate(${MARGIN.left - 12}, ${y + TRACK_HEIGHT / 2})`}>
                  <rect
                    x={-86}
                    y={-14}
                    width={82}
                    height={28}
                    rx={5}
                    fill="#151d30"
                    stroke="#273752"
                    strokeWidth={1}
                  />
                  <text
                    x={-45}
                    y={4}
                    textAnchor="middle"
                    className="text-xs font-mono font-bold fill-[#94a3b8]"
                  >
                    {trackId}
                  </text>
                </g>

                {/* Possession Windows (Subtle shaded slot where maintenance is granted) */}
                {pWindows.map((pw) => {
                  const px1 = xScale(pw.start_minute);
                  const px2 = xScale(pw.end_minute);
                  const pWidth = Math.max(4, px2 - px1);
                  return (
                    <g key={pw.window_id}>
                      <rect
                        x={px1}
                        y={y + 3}
                        width={pWidth}
                        height={TRACK_HEIGHT - 6}
                        fill="url(#possession-stripe)"
                        stroke="#10b981"
                        strokeWidth={1}
                        strokeOpacity={0.35}
                        rx={4}
                      />
                      <text
                        x={px1 + 6}
                        y={y + 16}
                        className="text-[9px] font-mono fill-emerald-500/70 font-semibold"
                      >
                        {pw.window_id} ({pw.end_minute - pw.start_minute}m)
                      </text>
                    </g>
                  );
                })}

                {/* Scheduled Blocks */}
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
                      {/* Block Rect */}
                      <rect
                        x={bx1 + 1}
                        y={y + BLOCK_PADDING + 8}
                        width={Math.max(6, blockWidth - 2)}
                        height={TRACK_HEIGHT - BLOCK_PADDING * 2 - 8}
                        rx={BLOCK_RADIUS}
                        fill={color.fill}
                        fillOpacity={isSelected ? 1 : 0.88}
                        stroke={isSelected ? "#ffffff" : color.stroke}
                        strokeWidth={isSelected ? 2.5 : 1.2}
                        filter={isSelected ? "drop-shadow(0px 0px 8px rgba(255,255,255,0.4))" : undefined}
                      />

                      {/* Block Content (if width permits) */}
                      {blockWidth > 42 && (
                        <>
                          <text
                            x={bx1 + 6}
                            y={y + BLOCK_PADDING + 24}
                            className="text-[11px] font-mono font-bold fill-white pointer-events-none"
                          >
                            {sb.block_id}
                          </text>
                          <text
                            x={bx1 + 6}
                            y={y + BLOCK_PADDING + 38}
                            className="text-[9px] font-mono fill-white/80 pointer-events-none"
                          >
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

          {/* Unscheduled / Rejection Area */}
          {data.enrichedUnscheduled.length > 0 && (
            <g transform={`translate(0, ${MARGIN.top + trackAreaHeight + 28})`}>
              {/* Header */}
              <rect
                x={MARGIN.left}
                y={0}
                width={innerWidth}
                height={26}
                rx={4}
                fill="#160e14"
                stroke="#3e1a22"
              />
              <text
                x={MARGIN.left + 14}
                y={17}
                className="text-[10px] font-bold uppercase tracking-wider fill-red-400"
              >
                Solver Rejections / Unscheduled Blocks ({data.enrichedUnscheduled.length})
              </text>

              {/* Unscheduled Block Rows */}
              {data.enrichedUnscheduled.map((ub, idx) => {
                const rowY = 32 + idx * REJECTED_ROW_HEIGHT;
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
                    {/* Background eligibility window */}
                    <rect
                      x={ex1}
                      y={rowY}
                      width={spanWidth}
                      height={REJECTED_ROW_HEIGHT - 6}
                      rx={4}
                      fill="#1a1215"
                      stroke={isSelected ? "#ef4444" : "#451a22"}
                      strokeWidth={isSelected ? 2 : 1}
                      strokeDasharray="4 3"
                    />

                    {/* Block pill on earliest start */}
                    <rect
                      x={ex1 + 2}
                      y={rowY + 4}
                      width={Math.min(spanWidth - 4, 180)}
                      height={REJECTED_ROW_HEIGHT - 14}
                      rx={3}
                      fill="#3a1017"
                      stroke="#ef4444"
                      strokeWidth={1}
                    />

                    <text
                      x={ex1 + 8}
                      y={rowY + 18}
                      className="text-[10px] font-mono font-bold fill-red-300"
                    >
                      {ub.block_id} ({ub.track_id})
                    </text>

                    <text
                      x={ex1 + 8}
                      y={rowY + 28}
                      className="text-[9px] fill-red-400/80 truncate"
                    >
                      {ub.rejectionReason}
                    </text>
                  </g>
                );
              })}
            </g>
          )}

          {/* Time axis footer */}
          <g transform={`translate(0, ${chartHeight - MARGIN.bottom + 20})`}>
            <line
              x1={MARGIN.left}
              y1={0}
              x2={MARGIN.left + innerWidth}
              y2={0}
              stroke="#273752"
              strokeWidth={1.5}
            />
            {hourTicks.map((tickMinute) => {
              const x = xScale(tickMinute);
              return (
                <g key={tickMinute}>
                  <line x1={x} y1={0} x2={x} y2={6} stroke="#384a6b" strokeWidth={1.5} />
                  <text
                    x={x}
                    y={18}
                    textAnchor="middle"
                    className="text-[10px] font-mono fill-[#64748b]"
                  >
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
