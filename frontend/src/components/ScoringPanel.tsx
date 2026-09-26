"use client";

import type { Job, JobEvent } from "@/lib/api";
import { scoreBand } from "@/lib/api";
import { recorded } from "@/lib/time";
import { pick } from "@/lib/railwayData";
import { Chip, Kv, ProvenanceChip, Score } from "@/components/ui";

const FEATURE_UNIT: Record<string, string> = {
  days_overdue: "days",
  maintenance_duration: "min",
};

const FEATURE_LABEL: Record<string, string> = {
  asset_criticality: pick("Asset criticality (demo asset record)", "Asset criticality"),
  defect_severity: "Defect severity",
  days_overdue: "Days overdue",
  failure_probability: pick("Failure probability (demo asset input)", "Failure probability (asset input)"),
  train_impact: "Train impact",
  maintenance_duration: "Maintenance duration",
  historical_failure_rate: pick("Historical failure rate (demo asset record)", "Historical failure rate"),
};

export default function ScoringPanel({
  job,
  events,
}: {
  job: Job;
  events: JobEvent[];
}) {
  const scored = events.find((e) => e.event_type === "JOB_SCORED");
  const m = (scored?.metadata ?? {}) as Record<string, unknown>;
  const explanation = Array.isArray(m.scoring_explanation)
    ? (m.scoring_explanation as unknown[]).filter(
        (x): x is string => typeof x === "string",
      )
    : [];
  const features =
    m.scoring_features && typeof m.scoring_features === "object"
      ? (m.scoring_features as Record<string, unknown>)
      : null;
  const modelType = typeof m.scoring_model_type === "string" ? m.scoring_model_type : null;
  const modelVersion = typeof m.scoring_model_version === "string" ? m.scoring_model_version : null;
  const reported = typeof m.reported_severity === "string" ? m.reported_severity : null;

  return (
    <div className="flex flex-col gap-space-md">
      <div>
        <h3 className="font-headline-sm text-headline-sm text-primary">
          Deterministic Risk Scoring
        </h3>
        <p className="font-body-sm text-body-sm text-on-surface-variant">
          Deterministic baseline scorer: fixed, hand-authored weights. Not a
          trained ML model.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-space-md">
        <ScoreTile label="PRIORITY SCORE" value={job.priority_score} />
        <ScoreTile label="RISK SCORE" value={job.risk_score} />
      </div>

      {!scored ? (
        <p className="font-body-sm text-body-sm text-outline">
          Explanation not recorded for this job. The two contract scores above
          are still the backend&apos;s values.
        </p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            {modelType && <Chip tone="violet">{modelType}</Chip>}
            {modelVersion && <Chip tone="neutral">{modelVersion}</Chip>}
            <ProvenanceChip />
            <span className="font-label-mono text-[11px] text-outline">
              scored {recorded(scored.occurred_at)} by {scored.actor.actor_id}
            </span>
          </div>

          {explanation.length > 0 && (
            <div>
              <div className="font-label-caps text-label-caps text-outline mb-1">
                TOP CONTRIBUTORS (strongest first)
              </div>
              <ol className="list-decimal pl-5 font-body-md text-body-md text-on-surface">
                {explanation.map((x) => (
                  <li key={x}>{x}</li>
                ))}
              </ol>
              <p className="mt-1 font-label-mono text-[11px] text-outline">
                Ranked on the scorer&apos;s internally normalized features,
                which the API does not return. The raw inputs below cannot be
                compared directly against the ranking threshold.
              </p>
            </div>
          )}

          {features && (
            <div>
              <div className="font-label-caps text-label-caps text-outline mb-1">
                RAW SCORER INPUTS (mixed units, as recorded)
              </div>
              <div className="rounded-DEFAULT bg-surface-container-lowest px-space-md py-1">
                {Object.entries(features).map(([k, v]) => (
                  <Kv key={k} k={(FEATURE_LABEL[k] ?? k).toUpperCase()}>
                    {typeof v === "number" ? v : String(v)}
                    {FEATURE_UNIT[k] ? ` ${FEATURE_UNIT[k]}` : ""}
                  </Kv>
                ))}
              </div>
              <p className="mt-1 font-label-mono text-[11px] text-outline">
                Days and minutes appear as counts; other inputs are 0–1 values.
                No weights or normalized contributions are exposed by the API,
                so none are shown.
              </p>
            </div>
          )}

          {reported && (
            <Kv k="WORKER-REPORTED SEVERITY">
              {reported}{" "}
              <span className="text-outline">(used as the defect severity input)</span>
            </Kv>
          )}
        </>
      )}

      <p className="font-label-mono text-[11px] text-outline border-l-2 border-secondary/50 pl-2">
        Asset inputs (criticality, failure probability, historical failure
        rate, overdue days) come from the {pick("generated DEMO ASSET RECORD (not Indian Railways asset-condition data)", "synthetic generated asset")} nearest this
        location. Worker-reported severity overrides the asset&apos;s stored
        defect severity for this job.
      </p>
    </div>
  );
}

function ScoreTile({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-DEFAULT bg-surface-container-lowest px-space-md py-space-sm">
      <div className="font-label-caps text-label-caps text-outline">{label}</div>
      <div className="font-label-mono text-[28px] leading-8 text-primary tabular-nums">
        <Score value={value} />
      </div>
      <Chip tone="amber">{scoreBand(value)}</Chip>
    </div>
  );
}
