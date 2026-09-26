/**
 * Field-location -> corridor chainage conversion.
 *
 * Mirrors backend/app/jobs/field_location.convert_field_location. The backend
 * exposes no topology endpoint (the v1 API is frozen), so the station/section
 * table comes from the build-time projection of the offline public snapshot
 * (see railwayData.ts) - or the synthetic fallback table when the console is
 * built for the synthetic dataset.
 *
 * UNITS. The domain (stations, sections, chainage) is KILOMETRES. The frozen API
 * and the database carry METRES: distance_* are corridor-absolute metres and are
 * the only stored location; field_location offsets are metres from a station and
 * are a cross-check that must agree within 1 mm. The km -> m conversion happens
 * here, once, and the UI presents the result in km.
 */

import {
  SECTIONS,
  STATIONS,
  type Section,
  type Station,
} from "@/lib/railwayData";

export { SECTIONS, STATIONS };
export type { Section, Station };

export const TRACKS = ["UP-1", "DOWN-1"] as const;

export const SECTION_IDS = SECTIONS.map((s) => s.id);

export function stationName(id: string): string {
  return STATIONS.find((s) => s.id === id)?.name ?? id;
}

/** Stations adjacent to `from` (the only valid "toward" choices). */
export function adjacentStations(from: string): string[] {
  const out: string[] = [];
  for (const s of SECTIONS) {
    if (s.start === from) out.push(s.end);
    else if (s.end === from) out.push(s.start);
  }
  return out;
}

export function findSection(a: string, b: string): Section | null {
  return (
    SECTIONS.find(
      (s) => (s.start === a && s.end === b) || (s.start === b && s.end === a),
    ) ?? null
  );
}

export function sectionLengthM(s: Section): number {
  // km -> m, rounded to a micrometre so 54.8 - 28 does not leave float noise.
  return Math.round((s.kmEnd - s.kmStart) * 1e9) / 1e6;
}

export interface Conversion {
  ok: true;
  section: Section;
  lengthM: number;
  distanceStart: number;
  distanceEnd: number;
}
export interface ConversionError {
  ok: false;
  error: string;
}

const round6 = (n: number) => Math.round(n * 1e6) / 1e6;

export function convertFieldLocation(
  from: string,
  toward: string,
  offsetStart: number,
  offsetEnd: number,
): Conversion | ConversionError {
  if (!from || !toward) return { ok: false, error: "Choose both stations." };
  const section = findSection(from, toward);
  if (!section) {
    return {
      ok: false,
      error: "These stations are not adjacent; pick the neighbouring station.",
    };
  }
  const length = sectionLengthM(section);
  if (!Number.isFinite(offsetStart) || !Number.isFinite(offsetEnd)) {
    return { ok: false, error: "Enter both offsets in metres." };
  }
  if (offsetStart < 0) {
    return { ok: false, error: "Start offset cannot be negative." };
  }
  if (offsetEnd <= offsetStart) {
    return { ok: false, error: "End offset must be greater than start offset." };
  }
  if (offsetEnd > length) {
    return {
      ok: false,
      error: `End offset exceeds the section length (${length} m).`,
    };
  }
  let ds: number;
  let de: number;
  if (from === section.start) {
    ds = section.kmStart * 1000 + offsetStart;
    de = section.kmStart * 1000 + offsetEnd;
  } else {
    ds = section.kmEnd * 1000 - offsetEnd;
    de = section.kmEnd * 1000 - offsetStart;
  }
  ds = round6(ds);
  de = round6(de);
  // Sections are half-open [km_start, km_end): the span may not end on km_end.
  if (!(section.kmStart * 1000 <= ds && de < section.kmEnd * 1000)) {
    return { ok: false, error: "End must be short of the next station." };
  }
  return {
    ok: true,
    section,
    lengthM: length,
    distanceStart: ds,
    distanceEnd: de,
  };
}

/** "138 700" style thousands grouping for metres. */
export function fmtMetres(m: number): string {
  return Math.round(m)
    .toString()
    .replace(/\B(?=(\d{3})+(?!\d))/g, " ");
}
