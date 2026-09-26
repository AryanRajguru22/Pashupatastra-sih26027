"use client";

/**
 * What the data on screen IS, layer by layer, with the honest class of each.
 *
 * Drawn only from the build-time projection of the verified offline snapshot
 * (see lib/railwayData.ts). It labels each layer with the most accurate class -
 * REAL_DATED_SNAPSHOT, DERIVED_FROM_REAL, DEMO_MAINTENANCE_INPUT ... - and never
 * "live": derived values are not shown as directly sourced railway data, the
 * possession windows are CANDIDATES, and the maintenance observations are demo
 * inputs grounded in real railway works.
 */

import { CARD, Chip } from "@/components/ui";
import { IS_REAL, SNAPSHOT } from "@/lib/railwayData";

type Tone = "neutral" | "cyan" | "amber" | "violet" | "red";

export const CLASS_TONE: Record<string, Tone> = {
  REAL: "cyan",
  REAL_DATED_SNAPSHOT: "cyan",
  DERIVED_FROM_REAL: "violet",
  ENGINEERING_ASSUMPTION: "amber",
  DEMO_MAINTENANCE_INPUT: "amber",
  UNAVAILABLE_PUBLICLY: "neutral",
};

export function ClassChip({ cls }: { cls: string }) {
  return <Chip tone={CLASS_TONE[cls] ?? "neutral"}>{cls}</Chip>;
}

const TAG = SNAPSHOT.timetable;

const LAYERS: { key: string; label: string; value: string; cls: string[]; note: string }[] = [
  {
    key: "corridor",
    label: "CORRIDOR",
    value: `NDLS → AGC · km 0–${SNAPSHOT.stations[SNAPSHOT.stations.length - 1].kmDisplay}`,
    cls: [],
    note: "Chainage is NDLS-relative kilometres, not the railway's own km-post.",
  },
  {
    key: "data",
    label: "DATA",
    value: `REAL PUBLIC SNAPSHOT · retrieved ${SNAPSHOT.retrievedAt} · offline`,
    cls: [],
    note: "Published documents downloaded once, hashed and stored. No live or confidential feed.",
  },
  {
    key: "topology",
    label: "TOPOLOGY",
    value: "Dated railway sources",
    cls: ["REAL_DATED_SNAPSHOT", "DERIVED_FROM_REAL"],
    note: "Station chainage is DERIVED from TAG-2026 and NCR signal interlocking plans (2012–2025); coordinates are OpenStreetMap.",
  },
  {
    key: "timetable",
    label: "TIMETABLE",
    value: `TAG-2026 · ${TAG.trainCount} trains`,
    cls: ["REAL_DATED_SNAPSHOT"],
    note: "Railway Board Trains at a Glance, effective 01-01-2026: a published schedule, not as-run movements.",
  },
  {
    key: "possession",
    label: "POSSESSION",
    value: "Derived candidate windows",
    cls: ["DERIVED_FROM_REAL"],
    note: "Freight and EMU services are not published, so a candidate window may overstate free track.",
  },
  {
    key: "maintenance",
    label: "MAINTENANCE",
    value: "Demo input grounded in real railway works",
    cls: ["DEMO_MAINTENANCE_INPUT"],
    note: "Real sanctioned work categories on real sections. The observation itself is not evidence of a defect.",
  },
];

export default function DataBasisPanel({ compact = false }: { compact?: boolean }) {
  if (!IS_REAL) {
    return (
      <div className={`${CARD} flex flex-col gap-1`}>
        <div className="flex items-center gap-2">
          <span className="font-label-caps text-label-caps text-secondary">DATA BASIS</span>
          <Chip tone="amber">SYNTHETIC FALLBACK</Chip>
        </div>
        <p className="font-body-sm text-body-sm text-on-surface-variant">
          The deterministic synthetic dataset: real station codes, invented topology, timetable and possession windows.
        </p>
      </div>
    );
  }

  return (
    <div className={`${CARD} flex flex-col gap-space-sm`} data-testid="data-basis">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-label-caps text-label-caps text-primary">DATA BASIS</span>
        <span className="font-label-mono text-label-mono text-outline">{SNAPSHOT.snapshotId}</span>
      </div>
      <dl className={`grid gap-x-space-lg gap-y-2 ${compact ? "grid-cols-1" : "grid-cols-1 md:grid-cols-2"}`}>
        {LAYERS.map((l) => (
          <div key={l.key} className="flex flex-col gap-0.5">
            <dt className="font-label-caps text-label-caps text-outline">{l.label}</dt>
            <dd className="font-body-sm text-body-sm text-on-surface flex flex-wrap items-center gap-1.5">
              <span>{l.value}</span>
              {l.cls.map((c) => (
                <ClassChip key={c} cls={c} />
              ))}
            </dd>
            {!compact && <p className="font-body-sm text-[12px] text-on-surface-variant">{l.note}</p>}
          </div>
        ))}
      </dl>
    </div>
  );
}
