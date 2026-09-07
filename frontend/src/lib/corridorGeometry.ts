/**
 * Shared corridor-curve geometry, ported from stitch/screen-2/code.html and
 * stitch/screen-3/code.html's "Dynamic Multi-Track Vector Layer" (viewBox
 * 0 0 1280 500). Both screens use the identical 4 lane-curve shapes; this
 * module is the single source of truth so PlanView and DisruptRecoverView
 * render the same corridor for the same track_id instead of diverging.
 */

export interface CurveControlPoints {
  p0: [number, number];
  p1: [number, number];
  p2: [number, number];
  p3: [number, number];
}

export const LANE_CURVES: CurveControlPoints[] = [
  { p0: [60, 170], p1: [400, 130], p2: [800, 240], p3: [1220, 190] },
  { p0: [60, 285], p1: [400, 245], p2: [800, 355], p3: [1220, 305] },
  { p0: [60, 215], p1: [400, 175], p2: [800, 285], p3: [1220, 235] },
  { p0: [60, 330], p1: [400, 290], p2: [800, 400], p3: [1220, 350] },
];

export function bezierPoint(
  t: number,
  p0: [number, number],
  p1: [number, number],
  p2: [number, number],
  p3: [number, number]
): [number, number] {
  const mt = 1 - t;
  const x = mt ** 3 * p0[0] + 3 * mt ** 2 * t * p1[0] + 3 * mt * t ** 2 * p2[0] + t ** 3 * p3[0];
  const y = mt ** 3 * p0[1] + 3 * mt ** 2 * t * p1[1] + 3 * mt * t ** 2 * p2[1] + t ** 3 * p3[1];
  return [x, y];
}

export function pathD(c: CurveControlPoints): string {
  return `M ${c.p0[0]} ${c.p0[1]} C ${c.p1[0]} ${c.p1[1]}, ${c.p2[0]} ${c.p2[1]}, ${c.p3[0]} ${c.p3[1]}`;
}

/** viewBox is always 1280x500 (see above) - converts an SVG-space point to a CSS % position. */
export function toViewBoxPct([x, y]: [number, number]): { left: string; top: string } {
  return { left: `${(x / 1280) * 100}%`, top: `${(y / 500) * 100}%` };
}
