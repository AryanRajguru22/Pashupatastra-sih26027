export function formatWorkType(workType: string): string {
  return workType
    .split("_")
    .map((w) => w.charAt(0) + w.slice(1).toLowerCase())
    .join(" ");
}

/**
 * Format a horizon-relative minute value as a wall-clock HH:MM.
 *
 * `minute` is an integer offset from OptimizationRequest.horizon_start,
 * not minutes-from-midnight - it may exceed 1440 on a multi-day
 * horizon (see contracts.ts). The clock face wraps every 24h (mod 24),
 * same as any wall clock; this display intentionally drops which
 * calendar day a value falls on rather than redesigning the lane
 * visualizations to show a day indicator.
 */
export function formatHHMM(minute: number): string {
  const totalMinutes = Math.floor(minute);
  const h = Math.floor(totalMinutes / 60) % 24;
  const m = totalMinutes % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}
