import { bc, bcMeta, type Job, type Obligation } from "@/lib/api";

/** Human location: "MTJ +5.70–6.00 km → RKM" from field_location, else chainage. */
export function locationText(job: Job): string {
  const fl = bcMeta(job).field_location as
    | {
        from_station_id?: string;
        toward_station_id?: string;
        offset_start_m?: number;
        offset_end_m?: number;
      }
    | undefined;
  if (
    fl &&
    typeof fl.offset_start_m === "number" &&
    typeof fl.offset_end_m === "number"
  ) {
    return `${fl.from_station_id} +${(fl.offset_start_m / 1000).toFixed(2)}–${(fl.offset_end_m / 1000).toFixed(2)} km → ${fl.toward_station_id}`;
  }
  return `km ${(job.distance_start / 1000).toFixed(3)}–${(job.distance_end / 1000).toFixed(3)}`;
}

export function obligationText(o?: Obligation): string {
  if (!o) return "—";
  if (o.obligation_type === "NONE") return "Nothing owed";
  return `${o.owed_role ?? "?"} · ${o.state}`;
}

/** Modelled block duration in minutes (block_candidate is non-contract). */
export function durationOf(job: Job): number | null {
  const d = bc(job).duration_minutes;
  return typeof d === "number" ? d : null;
}
