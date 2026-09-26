/**
 * The railway data the UI is drawn from: the offline public-data snapshot
 * (default) or the synthetic fallback.
 *
 * The frozen v1 API serves no topology, so station/chainage tables come from a
 * BUILD-TIME projection of the verified snapshot
 * (src/data/ndlsAgcSnapshot.generated.ts, produced by
 * scripts/real_data/build_frontend_snapshot.py). No request is made for it and no
 * website is contacted: it is compiled into the bundle.
 *
 * NEXT_PUBLIC_RAILWAY_DATA=synthetic builds the console for the deterministic
 * synthetic fallback dataset; run the backend with the matching
 * PASHUPAT_RAILWAY_DATA setting (unset = synthetic). The backend reports which
 * dataset produced a plan (possession_source), and the UI warns when the two
 * disagree (see dataBasisMismatch).
 *
 * Distance model: the DOMAIN is kilometres (this file); the frozen API and the
 * database carry METRES (corridor-absolute). Conversions are explicit at the
 * edges (fieldLocation.ts) and nowhere else.
 */

import { SNAPSHOT } from "@/data/ndlsAgcSnapshot.generated";

export type DataMode = "real" | "synthetic";

export const DATA_MODE: DataMode =
  process.env.NEXT_PUBLIC_RAILWAY_DATA === "synthetic" ? "synthetic" : "real";

export const IS_REAL = DATA_MODE === "real";

/** Real-data wording where the mode is real, the original synthetic wording otherwise. */
export function pick<T>(real: T, synthetic: T): T {
  return IS_REAL ? real : synthetic;
}

export interface Station {
  id: string;
  name: string;
  /** NDLS-relative chainage in km (domain unit). */
  km: number;
  /** How the chainage may be displayed without claiming false precision. */
  kmDisplay: string;
  lat?: number;
  lon?: number;
}

export interface Section {
  id: string;
  start: string;
  end: string;
  kmStart: number;
  kmEnd: number;
  zone?: string;
  division?: string;
}

const SYNTHETIC_STATIONS: Station[] = [
  { id: "NDLS", name: "New Delhi", km: 0, kmDisplay: "0" },
  { id: "NZM", name: "Hazrat Nizamuddin", km: 7, kmDisplay: "7" },
  { id: "FDB", name: "Faridabad", km: 32, kmDisplay: "32" },
  { id: "PWL", name: "Palwal", km: 66, kmDisplay: "66" },
  { id: "MTJ", name: "Mathura Junction", km: 133, kmDisplay: "133" },
  { id: "RKM", name: "Raja Ki Mandi", km: 190, kmDisplay: "190" },
  { id: "AGC", name: "Agra Cantt", km: 195, kmDisplay: "195" },
];

const SYNTHETIC_SECTIONS: Section[] = [
  { id: "NDLS-NZM", start: "NDLS", end: "NZM", kmStart: 0, kmEnd: 7 },
  { id: "NZM-FDB", start: "NZM", end: "FDB", kmStart: 7, kmEnd: 32 },
  { id: "FDB-PWL", start: "FDB", end: "PWL", kmStart: 32, kmEnd: 66 },
  { id: "PWL-MTJ", start: "PWL", end: "MTJ", kmStart: 66, kmEnd: 133 },
  { id: "MTJ-RKM", start: "MTJ", end: "RKM", kmStart: 133, kmEnd: 190 },
  { id: "RKM-AGC", start: "RKM", end: "AGC", kmStart: 190, kmEnd: 195 },
];

const REAL_STATIONS: Station[] = SNAPSHOT.stations.map((s) => ({
  id: s.id,
  name: s.name,
  km: s.km,
  kmDisplay: s.kmDisplay,
  lat: s.lat,
  lon: s.lon,
}));

const REAL_SECTIONS: Section[] = SNAPSHOT.sections.map((s) => ({
  id: s.id,
  start: s.start,
  end: s.end,
  kmStart: s.kmStart,
  kmEnd: s.kmEnd,
  zone: s.zone,
  division: s.division,
}));

export const STATIONS: Station[] = IS_REAL ? REAL_STATIONS : SYNTHETIC_STATIONS;
export const SECTIONS: Section[] = IS_REAL ? REAL_SECTIONS : SYNTHETIC_SECTIONS;

/** Total corridor length in km (chainage of the last station). */
export const TOTAL_KM: number = STATIONS[STATIONS.length - 1].km;

export function stationKm(id: string): number {
  return STATIONS.find((s) => s.id === id)?.km ?? 0;
}

/** The chainage figure alone ("7", "~54.8"), for tight layouts. */
export function stationKmDisplay(id: string): string {
  return STATIONS.find((s) => s.id === id)?.kmDisplay ?? "";
}

/** "~54.8 km" / "141 km" - never more precision than the source supports. */
export function stationKmLabel(id: string): string {
  const s = STATIONS.find((x) => x.id === id);
  return s ? `${s.kmDisplay} km` : "";
}

/**
 * A corridor-absolute distance in METRES shown in km. One decimal with a "~",
 * because the chainage it is measured on is itself derived (whole km or one
 * decimal): more digits would claim precision no source has.
 */
export function absMetresToKm(m: number): string {
  return IS_REAL ? `~${(m / 1000).toFixed(1)}` : (m / 1000).toFixed(3);
}

export function absSpanKm(startM: number, endM: number): string {
  return `${absMetresToKm(startM)}–${absMetresToKm(endM).replace("~", "")} km`;
}

/** The zone / division a section belongs to (real data only). */
export function sectionAdmin(sectionId: string): string | null {
  const s = REAL_SECTIONS.find((x) => x.id === sectionId);
  return IS_REAL && s ? `${s.zone} · ${s.division}` : null;
}

export { SNAPSHOT };

/**
 * The backend states which dataset planned a run (the timetable axis of the run's
 * provenance: REAL_SCHEDULED for the real snapshot, SYNTHETIC otherwise). When it
 * disagrees with the tables compiled into this console (e.g. a real-data build
 * talking to a backend started without PASHUPAT_RAILWAY_DATA=real) return a
 * warning: the displayed chainage would not belong to the plan.
 */
export function dataBasisMismatch(timetableAxis: string | undefined): string | null {
  if (!timetableAxis) return null;
  const backendReal = timetableAxis === "REAL_SCHEDULED";
  if (IS_REAL && !backendReal) {
    return "This console shows the real public snapshot, but the backend reports a synthetic plan. Start the backend with PASHUPAT_RAILWAY_DATA=real.";
  }
  if (!IS_REAL && backendReal) {
    return "This console was built for the synthetic fallback, but the backend reports the real public snapshot. Build the console without NEXT_PUBLIC_RAILWAY_DATA=synthetic.";
  }
  return null;
}
