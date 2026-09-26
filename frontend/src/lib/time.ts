/**
 * Time helpers. Three clocks are never mixed:
 *
 *   PLAN      horizon-relative minutes (fixed planning horizon, IST)
 *   OBSERVED  times entered by the crew (explicit offset, shown as entered)
 *   RECORDED  server wall-clock timestamps (UTC in the API, shown in IST)
 */

export const HORIZON_START_ISO = "2026-09-10T00:00:00+05:30";
export const HORIZON_MINUTES = 2880;
export const HORIZON_DATES = ["2026-09-10", "2026-09-11"] as const;

const IST_OFFSET_MIN = 330;
const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

// 2026-09-10T00:00:00+05:30 expressed in epoch ms
const HORIZON_START_MS = Date.parse(HORIZON_START_ISO);

const p2 = (n: number) => String(n).padStart(2, "0");

/** Break an epoch-ms instant into IST wall-clock fields. */
function istParts(ms: number) {
  const d = new Date(ms + IST_OFFSET_MIN * 60_000);
  return {
    y: d.getUTCFullYear(),
    mo: d.getUTCMonth(),
    d: d.getUTCDate(),
    h: d.getUTCHours(),
    mi: d.getUTCMinutes(),
    s: d.getUTCSeconds(),
  };
}

export function planMs(minute: number): number {
  return HORIZON_START_MS + minute * 60_000;
}

/** "11 Sep 00:16" */
export function planTime(minute: number): string {
  const t = istParts(planMs(minute));
  return `${t.d} ${MONTHS[t.mo]} ${p2(t.h)}:${p2(t.mi)}`;
}

/** "11 Sep 00:16–01:46" (drops the second date part when same day) */
export function planWindow(start: number, end: number): string {
  const a = istParts(planMs(start));
  const b = istParts(planMs(end));
  const left = `${a.d} ${MONTHS[a.mo]} ${p2(a.h)}:${p2(a.mi)}`;
  const right =
    a.d === b.d && a.mo === b.mo
      ? `${p2(b.h)}:${p2(b.mi)}`
      : `${b.d} ${MONTHS[b.mo]} ${p2(b.h)}:${p2(b.mi)}`;
  return `${left}–${right}`;
}

/** Plan minute -> ISO-8601 with explicit +05:30 offset. */
export function planMinuteToIso(minute: number): string {
  const t = istParts(planMs(minute));
  return `${t.y}-${p2(t.mo + 1)}-${p2(t.d)}T${p2(t.h)}:${p2(t.mi)}:${p2(t.s)}+05:30`;
}

/** Plan-day (0 or 1) that a minute falls in; -1 outside the horizon. */
export function planDay(minute: number): number {
  if (minute < 0 || minute >= HORIZON_MINUTES) return -1;
  return Math.floor(minute / 1440);
}

/** Parse an API timestamp. Naive timestamps are treated as UTC. */
export function parseApiTime(iso: string): number {
  const hasZone = /(Z|[+-]\d{2}:?\d{2})$/.test(iso);
  return Date.parse(hasZone ? iso : `${iso}Z`);
}

/** RECORDED time in IST: "25 Sep 2026, 14:03 IST". */
export function recorded(iso?: string | null): string {
  if (!iso) return "—";
  const ms = parseApiTime(iso);
  if (Number.isNaN(ms)) return iso;
  const t = istParts(ms);
  return `${t.d} ${MONTHS[t.mo]} ${t.y}, ${p2(t.h)}:${p2(t.mi)} IST`;
}

/** RECORDED time-of-day only: "14:03:08". */
export function recordedClock(iso?: string | null): string {
  if (!iso) return "—";
  const ms = parseApiTime(iso);
  if (Number.isNaN(ms)) return iso;
  const t = istParts(ms);
  return `${p2(t.h)}:${p2(t.mi)}:${p2(t.s)}`;
}

export function nowClockIst(): string {
  const t = istParts(Date.now());
  return `${p2(t.h)}:${p2(t.mi)}:${p2(t.s)}`;
}

/** OBSERVED time: show the value as entered, IST-normalised for readability. */
export function observed(iso?: string | null): string {
  if (!iso) return "—";
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return iso;
  const t = istParts(ms);
  return `${t.d} ${MONTHS[t.mo]} ${t.y}, ${p2(t.h)}:${p2(t.mi)} IST`;
}

/** "12 min", "2 h", "1 h 30 min" */
export function duration(seconds?: number | null): string {
  if (seconds === null || seconds === undefined) return "—";
  const m = Math.round(seconds / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const r = m % 60;
  return r ? `${h} h ${r} min` : `${h} h`;
}

export function shortId(id?: string | null, n = 8): string {
  if (!id) return "—";
  return id.length <= n ? id : `…${id.slice(-n)}`;
}

export const PLAN_LABEL = "PLAN";
export const RECORDED_LABEL = "RECORDED";
export const OBSERVED_LABEL = "OBSERVED";
