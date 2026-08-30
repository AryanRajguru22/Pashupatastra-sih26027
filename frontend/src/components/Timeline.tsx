"use client";

import React, { useMemo, useState } from "react";
import type {
  DashboardData,
  EnrichedScheduledBlock,
  EnrichedUnscheduledBlock,
  WorkType,
} from "@/types/contracts";

// ── Color palette ────────────────────────────────────────────────────

const WORK_TYPE_COLORS: Record<WorkType, { fill: string; stroke: string }> = {
  RENEWAL: { fill: "#d97706", stroke: "#f59e0b" }, // amber
  INSPECTION: { fill: "#2563eb", stroke: "#3b82f6" }, // blue
  REPAIR: { fill: "#dc2626", stroke: "#ef4444" }, // red
  PREVENTIVE: { fill: "#059669", stroke: "#10b981" }, // emerald
};

const DEFAULT_BLOCK_COLOR = { fill: "#6366f1", stroke: "#818cf8" };

// ── Layout constants ─────────────────────────────────────────────────

const MARGIN = { top: 56, right: 32, bottom: 80, left: 110 };
const TRACK_HEIGHT = 72;
const TRACK_GAP = 16;
const BLOCK_PADDING = 6;
const BLOCK_RADIUS = 4;
const REJECTED_ROW_HEIGHT = 40;

// ── Helpers ──────────────────────────────────────────────────────────

function parseTime(iso: string): number {
  return new Date(iso).getTime();
}

function formatHour(date: Date): string {
  return date.toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function durationLabel(startIso: string, endIso: string): string {
  const mins = Math.round(
    (parseTime(endIso) - parseTime(startIso)) / 60000
  );
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
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

  return (
    <div
      className="absolute z-50 pointer-events-none"
      style={{
        left: data.x,
        top: data.y - 8,
        transform: "translate(-50%, -100%)",
      }}
    >
      <div className="bg-[#151c2c] border border-[#2a3548] rounded-md px-3 py-2 shadow-xl text-xs max-w-[280px]">
        <div className="flex items-center gap-2 mb-1.5">
          <span className="font-mono font-bold text-[#e2e8f0] text-sm">
            {block.block_id}
          </span>
          {candidate && (
            <span
              className="px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide"
              style={{
                backgroundColor:
                  (
                    WORK_TYPE_COLORS[candidate.work_type] ?? DEFAULT_BLOCK_COLOR
                  ).fill + "33",
                color: (
                  WORK_TYPE_COLORS[candidate.work_type] ?? DEFAULT_BLOCK_COLOR
                ).stroke,
              }}
            >
              {candidate.work_type}
            </span>
          )}
        </div>

        {isScheduled && "start" in block && "end" in block && (
          <div className="text-[#94a3b8] mb-1">
            <span className="text-[#cbd5e1]">
              {formatHour(new Date((block as EnrichedScheduledBlock).start))}
            </span>
            {" → "}
            <span className="text-[#cbd5e1]">
              {formatHour(new Date((block as EnrichedScheduledBlock).end))}
            </span>
            <span className="text-[#64748b] ml-1.5">
              (
              {durationLabel(
                (block as EnrichedScheduledBlock).start,
                (block as EnrichedScheduledBlock).end
              )}
              )
            </span>
          </div>
        )}

        {!isScheduled && (
          <div className="text-[#f87171] text-[11px] mb-1">
            ✕ {(block as EnrichedUnscheduledBlock).reason}
          </div>
        )}

        {candidate && (
          <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 mt-1 text-[11px]">
            <div className="text-[#64748b]">Priority</div>
            <div className="text-[#e2e8f0] font-mono">
              {candidate.priority_score.toFixed(2)}
            </div>
            <div className="text-[#64748b]">Risk</div>
            <div className="text-[#e2e8f0] font-mono">
              {candidate.risk_score.toFixed(2)}
            </div>
            <div className="text-[#64748b]">Asset</div>
            <div className="text-[#e2e8f0] font-mono text-[10px]">
              {candidate.asset_id}
            </div>
            <div className="text-[#64748b]">Track</div>
            <div className="text-[#e2e8f0] font-mono">
              {candidate.track_id}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main Timeline component ──────────────────────────────────────────

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

  // Derive time range from the request planning horizon
  const { horizonStart, horizonEnd, totalMs } = useMemo(() => {
    const s = parseTime(data.request.planning_horizon.start);
    const e = parseTime(data.request.planning_horizon.end);
    return { horizonStart: s, horizonEnd: e, totalMs: e - s };
  }, [data.request.planning_horizon]);

  // Collect unique track IDs
  const trackIds = useMemo(() => {
    const tracks = new Set<string>();
    data.enrichedScheduled.forEach((b) => tracks.add(b.track_id));
    // Also include tracks from unscheduled blocks via candidate data
    data.enrichedUnscheduled.forEach((b) => {
      if (b.candidate) tracks.add(b.candidate.track_id);
    });
    return Array.from(tracks).sort();
  }, [data.enrichedScheduled, data.enrichedUnscheduled]);

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

  // SVG dimensions
  const chartWidth = 1200;
  const innerWidth = chartWidth - MARGIN.left - MARGIN.right;
  const trackAreaHeight =
    trackIds.length * TRACK_HEIGHT +
    (trackIds.length - 1) * TRACK_GAP;
  const rejectedAreaHeight =
    data.enrichedUnscheduled.length > 0
      ? 44 + data.enrichedUnscheduled.length * REJECTED_ROW_HEIGHT
      : 0;
  const chartHeight =
    MARGIN.top + trackAreaHeight + rejectedAreaHeight + MARGIN.bottom;

  // x scale: time → pixel
  const xScale = (time: number) =>
    MARGIN.left + ((time - horizonStart) / totalMs) * innerWidth;

  // Generate tick marks
  const { majorTicks, minorTicks } = useMemo(() => {
    const major: number[] = [];
    const minor: number[] = [];
    const startDate = new Date(horizonStart);
    startDate.setMinutes(0, 0, 0);
    let tick = startDate.getTime();
    if (tick < horizonStart) tick += 3600000;
    let idx = 0;
    while (tick <= horizonEnd) {
      const hour = new Date(tick).getHours();
      if (hour % 2 === 0) {
        major.push(tick);
      } else {
        minor.push(tick);
      }
      tick += 3600000;
      idx++;
    }
    return { majorTicks: major, minorTicks: minor };
  }, [horizonStart, horizonEnd]);

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
    <div className="w-full">
      {/* Legend */}
      <div className="flex items-center gap-5 mb-3 px-2">
        <span className="text-[11px] text-[#64748b] uppercase tracking-wider font-semibold mr-2">
          Work Type
        </span>
        {(Object.keys(WORK_TYPE_COLORS) as WorkType[]).map((wt) => (
          <div key={wt} className="flex items-center gap-1.5">
            <div
              className="w-3 h-3 rounded-sm"
              style={{ backgroundColor: WORK_TYPE_COLORS[wt].fill }}
            />
            <span className="text-[11px] text-[#94a3b8] capitalize">
              {wt.toLowerCase()}
            </span>
          </div>
        ))}
        <div className="flex items-center gap-1.5 ml-4">
          <div className="w-3 h-3 rounded-sm bg-[#334155] border border-dashed border-[#64748b]" />
          <span className="text-[11px] text-[#64748b]">Rejected</span>
        </div>
      </div>

      {/* Chart */}
      <div className="relative w-full overflow-x-auto">
        <svg
          viewBox={`0 0 ${chartWidth} ${chartHeight}`}
          className="w-full min-w-[800px]"
          style={{ height: "auto" }}
        >
          {/* Background */}
          <rect width={chartWidth} height={chartHeight} fill="#0a0e17" rx="8" />

          {/* Minor grid lines (hourly, no labels) */}
          {minorTicks.map((tick) => {
            const x = xScale(tick);
            return (
              <line
                key={`minor-${tick}`}
                x1={x}
                y1={MARGIN.top - 4}
                x2={x}
                y2={MARGIN.top + trackAreaHeight + 4}
                stroke="#151c2c"
                strokeWidth={1}
              />
            );
          })}

          {/* Major grid lines & time axis labels (every 2h) */}
          {majorTicks.map((tick) => {
            const x = xScale(tick);
            return (
              <g key={`major-${tick}`}>
                <line
                  x1={x}
                  y1={MARGIN.top - 8}
                  x2={x}
                  y2={MARGIN.top + trackAreaHeight + 8}
                  stroke="#1e293b"
                  strokeWidth={1}
                />
                <text
                  x={x}
                  y={MARGIN.top - 16}
                  textAnchor="middle"
                  className="fill-[#64748b] text-[11px]"
                  fontFamily="'JetBrains Mono', monospace"
                >
                  {formatHour(new Date(tick))}
                </text>
              </g>
            );
          })}

          {/* Track lanes */}
          {trackIds.map((trackId, i) => {
            const y = MARGIN.top + i * (TRACK_HEIGHT + TRACK_GAP);
            const blocks = blocksByTrack.get(trackId) ?? [];

            return (
              <g key={trackId}>
                {/* Lane background */}
                <rect
                  x={MARGIN.left}
                  y={y}
                  width={innerWidth}
                  height={TRACK_HEIGHT}
                  fill="#0f1520"
                  rx="4"
                  stroke="#1e293b"
                  strokeWidth={1}
                />

                {/* Track label */}
                <text
                  x={MARGIN.left - 12}
                  y={y + TRACK_HEIGHT / 2}
                  textAnchor="end"
                  dominantBaseline="central"
                  className="text-[13px] font-semibold"
                  fontFamily="'JetBrains Mono', monospace"
                  fill="#cbd5e1"
                >
                  {trackId}
                </text>
                <text
                  x={MARGIN.left - 12}
                  y={y + TRACK_HEIGHT / 2 + 16}
                  textAnchor="end"
                  dominantBaseline="central"
                  className="text-[10px]"
                  fill="#475569"
                >
                  TRACK
                </text>

                {/* Scheduled blocks */}
                {blocks.map((block) => {
                  const x1 = xScale(parseTime(block.start));
                  const x2 = xScale(parseTime(block.end));
                  const w = x2 - x1;
                  const colors =
                    block.candidate
                      ? WORK_TYPE_COLORS[block.candidate.work_type] ??
                        DEFAULT_BLOCK_COLOR
                      : DEFAULT_BLOCK_COLOR;
                  const isSelected = selectedBlockId === block.block_id;

                  return (
                    <g
                      key={block.block_id}
                      className="cursor-pointer"
                      onClick={() => onBlockSelect?.(block.block_id)}
                      onMouseMove={(e) => handleBlockHover(e, block, true)}
                      onMouseLeave={() => setTooltip(null)}
                    >
                      {/* Selection glow */}
                      {isSelected && (
                        <rect
                          x={x1 + BLOCK_PADDING - 3}
                          y={y + BLOCK_PADDING - 3}
                          width={w - BLOCK_PADDING * 2 + 6}
                          height={TRACK_HEIGHT - BLOCK_PADDING * 2 + 6}
                          rx={BLOCK_RADIUS + 2}
                          fill="none"
                          stroke={colors.stroke}
                          strokeWidth={2}
                          opacity={0.6}
                          className="animate-pulse"
                        />
                      )}

                      {/* Block bar */}
                      <rect
                        x={x1 + BLOCK_PADDING}
                        y={y + BLOCK_PADDING}
                        width={Math.max(w - BLOCK_PADDING * 2, 2)}
                        height={TRACK_HEIGHT - BLOCK_PADDING * 2}
                        rx={BLOCK_RADIUS}
                        fill={colors.fill}
                        stroke={colors.stroke}
                        strokeWidth={isSelected ? 2 : 1}
                        opacity={0.85}
                      />

                      {/* Block label */}
                      {w > 50 && (
                        <text
                          x={x1 + BLOCK_PADDING + 8}
                          y={y + TRACK_HEIGHT / 2 - 6}
                          dominantBaseline="central"
                          className="text-[11px] font-bold"
                          fontFamily="'JetBrains Mono', monospace"
                          fill="#fff"
                        >
                          {block.block_id}
                        </text>
                      )}

                      {/* Duration sublabel */}
                      {w > 80 && (
                        <text
                          x={x1 + BLOCK_PADDING + 8}
                          y={y + TRACK_HEIGHT / 2 + 10}
                          dominantBaseline="central"
                          className="text-[9px]"
                          fill="rgba(255,255,255,0.6)"
                          fontFamily="'JetBrains Mono', monospace"
                        >
                          {durationLabel(block.start, block.end)}
                        </text>
                      )}
                    </g>
                  );
                })}
              </g>
            );
          })}

          {/* Rejected / Unscheduled section */}
          {data.enrichedUnscheduled.length > 0 && (
            <g>
              <text
                x={MARGIN.left}
                y={MARGIN.top + trackAreaHeight + 32}
                className="text-[11px] font-semibold uppercase tracking-wider"
                fill="#64748b"
              >
                Rejected / Unscheduled ({data.enrichedUnscheduled.length})
              </text>

              {data.enrichedUnscheduled.map((block, i) => {
                const y =
                  MARGIN.top + trackAreaHeight + 44 + i * REJECTED_ROW_HEIGHT;

                return (
                  <g
                    key={block.block_id}
                    className="cursor-pointer"
                    onClick={() => onBlockSelect?.(block.block_id)}
                    onMouseMove={(e) => handleBlockHover(e, block, false)}
                    onMouseLeave={() => setTooltip(null)}
                  >
                    <rect
                      x={MARGIN.left}
                      y={y}
                      width={innerWidth}
                      height={REJECTED_ROW_HEIGHT - 4}
                      fill="#0f1520"
                      rx="4"
                      stroke="#1e293b"
                      strokeWidth={1}
                      strokeDasharray="4 4"
                    />
                    <text
                      x={MARGIN.left + 12}
                      y={y + (REJECTED_ROW_HEIGHT - 4) / 2}
                      dominantBaseline="central"
                      className="text-[12px] font-mono font-bold"
                      fill="#ef4444"
                    >
                      ✕ {block.block_id}
                    </text>
                    <text
                      x={MARGIN.left + 110}
                      y={y + (REJECTED_ROW_HEIGHT - 4) / 2}
                      dominantBaseline="central"
                      className="text-[11px]"
                      fill="#64748b"
                    >
                      {block.reason}
                    </text>
                    {block.candidate && (
                      <text
                        x={chartWidth - MARGIN.right - 8}
                        y={y + (REJECTED_ROW_HEIGHT - 4) / 2}
                        dominantBaseline="central"
                        textAnchor="end"
                        className="text-[10px] font-mono"
                        fill="#475569"
                      >
                        {block.candidate.work_type} · P:
                        {block.candidate.priority_score} · R:
                        {block.candidate.risk_score}
                      </text>
                    )}
                  </g>
                );
              })}
            </g>
          )}
        </svg>

        {/* Tooltip overlay */}
        {tooltip && <BlockTooltip data={tooltip} />}
      </div>
    </div>
  );
}
