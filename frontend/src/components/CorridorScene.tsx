"use client";

/**
 * Spatial corridor visual for the Command Center (ported from the approved
 * Stitch "Corridor Synchrony" scene): orbital rings, luminous rails, laser
 * pulses and star dust are DECORATIVE. The stations and job markers are real:
 * stations sit at their synthetic chainage and each active job is drawn on its
 * own track at its stored corridor chainage.
 */

import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { JOB_TYPE_LABEL, severityOf, type Job } from "@/lib/api";
import { STATIONS } from "@/lib/fieldLocation";

export type SceneMode = "SPATIAL" | "LINEAR";

const KM: Record<string, number> = {
  NDLS: 0,
  NZM: 7,
  FDB: 32,
  PWL: 66,
  MTJ: 133,
  RKM: 190,
  AGC: 195,
};
const TOTAL_KM = 195;

const PATHS: Record<SceneMode, Record<string, string>> = {
  SPATIAL: {
    "UP-1": "M 50 450 C 340 435, 520 315, 720 260 S 1060 180, 1150 155",
    "DOWN-1": "M 65 470 C 355 455, 535 330, 735 275 S 1075 195, 1165 170",
  },
  LINEAR: {
    "UP-1": "M 80 235 L 1120 235",
    "DOWN-1": "M 80 300 L 1120 300",
  },
};

interface Pt {
  x: number;
  y: number;
}

const STATUS_COLOR: Record<string, string> = {
  reported: "#ffb688",
  scheduled: "#7df4ff",
  notified: "#00f0ff",
  in_progress: "#d4bbff",
  completed: "#5b6b73",
};

const STAR_DUST: [number, number, number, string, number][] = [
  [160, 90, 1.5, "#7df4ff", 3.2],
  [280, 150, 1.2, "#00dbe9", 4.1],
  [430, 70, 2, "#ffffff", 2.7],
  [560, 115, 1.2, "#ffb688", 3.8],
  [750, 85, 1.8, "#7df4ff", 3.4],
  [890, 130, 1.4, "#00f0ff", 4.6],
  [1040, 80, 1.6, "#ffb688", 3.1],
  [1110, 240, 1.2, "#7df4ff", 4.8],
  [220, 380, 1.5, "#00f0ff", 3.6],
  [980, 420, 1.3, "#ffb688", 4.3],
];

export default function CorridorScene({
  jobs,
  mode,
  showJobs,
  showStations,
  selectedId,
  onSelect,
}: {
  jobs: Job[];
  mode: SceneMode;
  showJobs: boolean;
  showStations: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const upRef = useRef<SVGPathElement>(null);
  const downRef = useRef<SVGPathElement>(null);
  const [pts, setPts] = useState<{ up1: (f: number) => Pt; down1: (f: number) => Pt } | null>(null);

  useLayoutEffect(() => {
    const up = upRef.current;
    const down = downRef.current;
    if (!up || !down) return;
    const at = (el: SVGPathElement) => (f: number): Pt => {
      const p = el.getPointAtLength(Math.max(0, Math.min(1, f)) * el.getTotalLength());
      return { x: p.x, y: p.y };
    };
    const upAt = at(up);
    const downAt = at(down);
    setPts({ up1: upAt, down1: downAt });
  }, [mode]);

  const active = useMemo(() => jobs.filter((j) => j.status !== "completed"), [jobs]);

  const stationPoints = useMemo(() => {
    if (!pts) return [];
    return STATIONS.map((s) => ({
      id: s.id,
      up: pts.up1(KM[s.id] / TOTAL_KM),
      down: pts.down1(KM[s.id] / TOTAL_KM),
    }));
  }, [pts]);

  const markers = useMemo(() => {
    if (!pts) return [];
    return active.map((j) => {
      const mid = (j.distance_start + j.distance_end) / 2 / 1000;
      const f = mid / TOTAL_KM;
      const p = j.track_id === "UP-1" ? pts.up1(f) : pts.down1(f);
      return { job: j, p };
    });
  }, [pts, active]);

  return (
    <svg
      className="w-full h-full"
      fill="none"
      viewBox="0 0 1200 540"
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label="Synthetic NDLS to AGC corridor with active maintenance jobs"
    >
      <defs>
        <linearGradient id="cyanTrackGlow" x1="0%" x2="100%" y1="0%" y2="100%">
          <stop offset="0%" stopColor="#00f0ff" stopOpacity="0.3" />
          <stop offset="45%" stopColor="#00f0ff" stopOpacity="1" />
          <stop offset="80%" stopColor="#7df4ff" stopOpacity="0.9" />
          <stop offset="100%" stopColor="#00dbe9" stopOpacity="0.4" />
        </linearGradient>
        <radialGradient cx="50%" cy="50%" id="haloGlow" r="50%">
          <stop offset="0%" stopColor="#00f0ff" stopOpacity="0.35" />
          <stop offset="60%" stopColor="#00dbe9" stopOpacity="0.15" />
          <stop offset="100%" stopColor="#00dbe9" stopOpacity="0" />
        </radialGradient>
        <filter height="200%" id="cinematicBloom" width="200%" x="-50%" y="-50%">
          <feGaussianBlur in="SourceGraphic" result="blur1" stdDeviation="5" />
          <feGaussianBlur in="SourceGraphic" result="blur2" stdDeviation="12" />
          <feMerge>
            <feMergeNode in="blur2" />
            <feMergeNode in="blur1" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      {/* star dust (decorative) */}
      <g opacity="0.6" aria-hidden>
        {STAR_DUST.map(([x, y, r, c, d], i) => (
          <circle key={i} cx={x} cy={y} r={r} fill={c}>
            <animate attributeName="opacity" dur={`${d}s`} repeatCount="indefinite" values="0.2;0.9;0.2" />
          </circle>
        ))}
      </g>

      <circle cx="600" cy="270" fill="url(#haloGlow)" opacity="0.35" r="390" pointerEvents="none" />

      {/* orbital rings (decorative) */}
      <g pointerEvents="none" aria-hidden>
        <g>
          <ellipse cx="600" cy="270" filter="url(#cinematicBloom)" opacity="0.42" rx="550" ry="185" stroke="#00f0ff" strokeDasharray="10 18 3 18" strokeWidth="1.2" />
          <circle cx="600" cy="85" fill="#7df4ff" filter="url(#cinematicBloom)" r="4" />
          <circle cx="600" cy="85" fill="none" opacity="0.8" r="8" stroke="#00f0ff" strokeWidth="1">
            <animate attributeName="r" dur="2s" repeatCount="indefinite" values="4;14;4" />
            <animate attributeName="opacity" dur="2s" repeatCount="indefinite" values="0.8;0;0.8" />
          </circle>
          <animateTransform attributeName="transform" dur="60s" from="0 600 270" repeatCount="indefinite" to="360 600 270" type="rotate" />
        </g>
        <g>
          <ellipse cx="600" cy="270" filter="url(#cinematicBloom)" opacity="0.5" rx="460" ry="150" stroke="#ffb688" strokeDasharray="14 14" strokeWidth="1.4" />
          <circle cx="140" cy="270" fill="#ff8c42" filter="url(#cinematicBloom)" r="4.5" />
          <circle cx="1060" cy="270" fill="#ffdbc7" r="3" />
          <animateTransform attributeName="transform" dur="90s" from="360 600 270" repeatCount="indefinite" to="0 600 270" type="rotate" />
        </g>
        <g>
          <ellipse cx="600" cy="265" opacity="0.55" rx="330" ry="105" stroke="#00dbe9" strokeDasharray="6 22 1 22" strokeWidth="1" />
          <circle cx="600" cy="160" fill="#00f0ff" filter="url(#cinematicBloom)" r="3.5" />
          <animateTransform attributeName="transform" dur="36s" from="0 600 265" repeatCount="indefinite" to="360 600 265" type="rotate" />
        </g>
        <g opacity="0.25" stroke="#00dbe9" strokeWidth="0.8">
          <line strokeDasharray="4 8" x1="600" x2="600" y1="60" y2="480" />
          <line strokeDasharray="4 8" x1="120" x2="1080" y1="270" y2="270" />
          <circle cx="600" cy="270" fill="none" r="18" />
        </g>
      </g>

      {/* ground grid (decorative) */}
      {mode === "SPATIAL" && (
        <g opacity="0.32" stroke="#2a3f4e" strokeWidth="0.75" aria-hidden>
          <line x1="50" x2="480" y1="520" y2="310" />
          <line x1="230" x2="540" y1="520" y2="310" />
          <line x1="450" x2="600" y1="520" y2="310" />
          <line x1="670" x2="660" y1="520" y2="310" />
          <line x1="890" x2="730" y1="520" y2="310" />
          <line x1="1130" x2="800" y1="520" y2="310" />
          <path d="M 80 500 Q 600 480 1120 500" fill="none" opacity="0.2" stroke="#00dbe9" strokeWidth="0.5" />
          <path d="M 200 430 Q 600 415 1000 430" fill="none" opacity="0.25" stroke="#00dbe9" strokeWidth="0.5" />
          <path d="M 320 370 Q 600 360 880 370" fill="none" opacity="0.3" stroke="#00dbe9" strokeWidth="0.5" />
        </g>
      )}

      {/* the two real tracks */}
      <path ref={upRef} d={PATHS[mode]["UP-1"]} fill="none" stroke="#415664" strokeLinecap="round" strokeWidth="3" />
      <path d={PATHS[mode]["DOWN-1"]} fill="none" filter="url(#cinematicBloom)" opacity="0.65" stroke="#00f0ff" strokeLinecap="round" strokeWidth="6" />
      <path ref={downRef} d={PATHS[mode]["DOWN-1"]} fill="none" stroke="url(#cyanTrackGlow)" strokeLinecap="round" strokeWidth="4.5" />
      <path className="animate-laser-fast" d={PATHS[mode]["DOWN-1"]} fill="none" stroke="#dbfcff" strokeLinecap="round" strokeWidth="2" opacity="0.9" aria-hidden />
      <path className="animate-laser-counter" d={PATHS[mode]["UP-1"]} fill="none" stroke="#ffb688" strokeLinecap="round" strokeWidth="1.6" opacity="0.7" aria-hidden />
      <text x={mode === "SPATIAL" ? 40 : 40} y={mode === "SPATIAL" ? 438 : 230} fill="#849495" fontSize="11" fontFamily="var(--font-label-mono)">UP-1</text>
      <text x={mode === "SPATIAL" ? 40 : 40} y={mode === "SPATIAL" ? 488 : 305} fill="#00dbe9" fontSize="11" fontFamily="var(--font-label-mono)">DOWN-1</text>

      {/* stations at their synthetic chainage */}
      {showStations &&
        stationPoints.map((s) => (
          <g key={s.id}>
            <circle cx={s.down.x} cy={s.down.y} r="5" fill="#0c0e14" stroke="#7df4ff" strokeWidth="1.5" />
            <text x={s.down.x} y={s.down.y + (mode === "SPATIAL" ? 24 : 26)} textAnchor="middle" fill="#b9cacb" fontSize="11" fontFamily="var(--font-label-mono)">
              {s.id}
            </text>
            <text x={s.down.x} y={s.down.y + (mode === "SPATIAL" ? 37 : 39)} textAnchor="middle" fill="#849495" fontSize="9" fontFamily="var(--font-label-mono)">
              km {KM[s.id]}
            </text>
          </g>
        ))}

      {/* real job markers */}
      {showJobs &&
        markers.map(({ job, p }) => {
          const c = STATUS_COLOR[job.status] ?? "#ffb688";
          const critical = severityOf(job) === "CRITICAL";
          const sel = job.job_id === selectedId;
          const label = `${job.job_id} · ${JOB_TYPE_LABEL[job.work_type] ?? job.work_type} · ${job.status}`;
          return (
            <g
              key={job.job_id}
              transform={`translate(${p.x} ${p.y})`}
              role="button"
              tabIndex={0}
              aria-label={label}
              aria-pressed={sel}
              className="cursor-pointer outline-none"
              onClick={() => onSelect(job.job_id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") onSelect(job.job_id);
              }}
            >
              <title>{label}</title>
              {critical && (
                <circle r="10" fill="none" stroke="#ff8c42" strokeWidth="1.5">
                  <animate attributeName="r" dur="1.8s" repeatCount="indefinite" values="9;22;9" />
                  <animate attributeName="opacity" dur="1.8s" repeatCount="indefinite" values="0.9;0;0.9" />
                </circle>
              )}
              {sel && <circle r="15" fill="none" stroke="#dbfcff" strokeWidth="1.2" strokeDasharray="3 3" />}
              <circle r="9" fill={c} opacity="0.25" filter="url(#cinematicBloom)" />
              <circle r={job.status === "scheduled" ? 5.5 : 6.5} fill={job.status === "scheduled" ? "#0c0e14" : c} stroke={c} strokeWidth="2" />
            </g>
          );
        })}
    </svg>
  );
}
