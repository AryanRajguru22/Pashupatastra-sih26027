"use client";

import type { Obligation, Proposal, Provenance } from "@/lib/api";
import { JOB_TYPE_LABEL, scoreBand } from "@/lib/api";
import {
  duration,
  planWindow,
  recorded,
  shortId,
} from "@/lib/time";
import { Chip, ProvenanceChip, Score, TimeTag } from "@/components/ui";
import { ClassChip } from "@/components/DataBasisPanel";
import { IS_REAL, dataBasisMismatch, pick } from "@/lib/railwayData";

const MINUTES_RE = /minute\s+(\d+)\s*(?:to|-|–)\s*(\d+)/i;

/** Append plan-time conversion to a detail that quotes minute ranges. */
function withPlanTime(detail: string): string | null {
  const m = MINUTES_RE.exec(detail);
  if (!m) return null;
  return planWindow(Number(m[1]), Number(m[2]));
}

/**
 * The four canonical provenance axes of a run. With the real snapshot each axis
 * is shown with the accurate data class (the coarse backend enum cannot say
 * "derived" or "demo input"), and the overall result stays SYNTHETIC because the
 * demo asset condition is one of the inputs (weakest-link rule).
 */
const REAL_AXIS_LABEL: Record<string, [string, string]> = {
  topology: ["dated public snapshot", "REAL_DATED_SNAPSHOT"],
  timetable: ["TAG-2026 published schedule", "REAL_DATED_SNAPSHOT"],
  assets: ["demo asset condition", "DEMO_MAINTENANCE_INPUT"],
  possession: ["candidate windows derived by rule (freight/EMU not included)", "DERIVED_FROM_REAL"],
  maintenance: ["demo observations grounded in real railway works", "DEMO_MAINTENANCE_INPUT"],
};

export function ProvenanceStrip({ p }: { p: Provenance }) {
  const items: [string, string][] = [
    ["topology", p.topology],
    ["timetable", p.timetable],
    ["assets", p.asset_condition],
    ["possession", p.possession],
  ];
  const mismatch = dataBasisMismatch(p.timetable);
  // The real snapshot is recognised by its topology/timetable axes. The possession
  // axis stays on the conservative coarse tier (SYNTHETIC: a derived window is
  // never a real possession); its true class is shown from the mapping below.
  const realSnapshot = IS_REAL && p.topology === "REAL_STATIC" && p.timetable === "REAL_SCHEDULED";

  if (realSnapshot) {
    return (
      <div className="flex flex-col gap-1 font-label-mono text-[11px] text-outline" data-testid="provenance-strip">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          {[...items.map(([k]) => k), "maintenance"].map((k) => (
            <span key={k} className="flex items-center gap-1">
              {k} <span className="text-on-surface-variant">{REAL_AXIS_LABEL[k][0]}</span>
              <ClassChip cls={REAL_AXIS_LABEL[k][1]} />
            </span>
          ))}
        </div>
        <span>
          → effective <span className="text-secondary font-bold">{p.effective}</span>{" "}
          (weakest link: the demo asset condition). Possession windows are derived candidates, not a real or scheduled possession.
        </span>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-2 font-label-mono text-[11px] text-outline">
        {items.map(([k, v]) => (
          <span key={k}>
            {k} <span className="text-secondary">{v}</span>
          </span>
        ))}
        <span>
          → effective{" "}
          <span className="text-secondary font-bold">{p.effective}</span>
        </span>
      </div>
      {mismatch && (
        <div role="alert" className="font-label-mono text-[11px] text-error">
          {mismatch}
        </div>
      )}
    </div>
  );
}

export function ObligationLine({ o }: { o?: Obligation | null }) {
  if (!o) return null;
  if (o.obligation_type === "NONE")
    return (
      <div className="font-label-mono text-label-mono text-on-surface-variant">
        Nothing owed — {o.reason_code}
      </div>
    );
  return (
    <div className="font-label-mono text-label-mono text-on-surface-variant flex flex-wrap items-center gap-2">
      <Chip tone="amber">{o.obligation_type}</Chip>
      <span>owed by {o.owed_role}</span>
      <Chip tone={o.is_past_due ? "red" : "cyan"}>{o.state}</Chip>
      <span>
        due {recorded(o.due_at)} <TimeTag kind="RECORDED" />
      </span>
      <span className="text-outline">
        ASSUMED demo SLA {o.policy_version} — not Indian Railways policy
      </span>
    </div>
  );
}

export default function ProposalCard({
  proposal,
  obligation,
}: {
  proposal: Proposal;
  obligation?: Obligation | null;
}) {
  return (
    <div className="flex flex-col gap-space-md">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="font-label-mono text-label-mono text-outline break-all">
          {proposal.proposal_id}
        </div>
        <Chip tone="amber">PROPOSED · NOT COMMITTED</Chip>
      </div>

      <div className="flex flex-wrap items-center gap-2 font-headline-sm text-body-md text-on-surface">
        {JOB_TYPE_LABEL[proposal.work_type] ?? proposal.work_type}
        <span className="text-outline">·</span>
        <span className="font-label-mono">{proposal.job_id}</span>
        <span className="text-outline">·</span>
        <span className="font-label-mono">{proposal.track_id}</span>
        <span className="text-outline">·</span>
        <span className="font-label-mono">Section {proposal.section_id ?? "—"}</span>
      </div>

      <div className="rounded-DEFAULT bg-surface-container-lowest px-space-md py-space-sm">
        <div className="font-label-caps text-label-caps text-outline flex items-center gap-2">
          {pick("PLACEMENT (plan horizon 10–11 Sep 2026, IST)", "PLACEMENT (synthetic horizon, IST)")} <TimeTag kind="PLAN" />
        </div>
        <div className="font-headline-md text-headline-md text-primary tabular-nums">
          {planWindow(proposal.start_minute, proposal.end_minute)}
        </div>
        <div className="font-label-mono text-label-mono text-on-surface-variant">
          {duration(proposal.duration_minutes * 60)} · raw minutes{" "}
          {proposal.start_minute}–{proposal.end_minute}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-space-md font-label-mono text-label-mono">
        <span>
          priority <Score value={proposal.priority_score} />{" "}
          <Chip tone="amber">{scoreBand(proposal.priority_score)}</Chip>
        </span>
        <span>
          risk <Score value={proposal.risk_score} />{" "}
          <Chip tone="amber">{scoreBand(proposal.risk_score)}</Chip>
        </span>
        <span>
          objective <Score value={proposal.objective_score} />
        </span>
      </div>

      <div>
        <div className="font-label-caps text-label-caps text-outline mb-1">
          WHY THIS PLACEMENT (backend explanation, verbatim)
        </div>
        {IS_REAL && (
          <p className="font-label-mono text-[11px] text-secondary mb-1" data-testid="candidate-window-note">
            Any &quot;possession window&quot; named below is a CANDIDATE window derived from the public passenger
            timetable — not a real or scheduled possession.
          </p>
        )}
        <div className="rounded-DEFAULT bg-surface-container-lowest divide-y divide-outline-variant/15">
          {proposal.explanation.map((x, i) => {
            const conv =
              x.code === "PLACEMENT" || x.code === "POSSESSION_WINDOW_REQUIRED"
                ? withPlanTime(x.detail)
                : null;
            const info = x.code === "NO_TRAIN_LEVEL_DATA";
            return (
              <div
                key={`${x.code}-${i}`}
                className="grid grid-cols-12 gap-2 px-space-md py-2"
              >
                <div
                  className={`col-span-4 font-label-mono text-[11px] ${info ? "text-tertiary" : "text-primary-fixed"}`}
                >
                  {x.code}
                </div>
                <div className="col-span-8 font-body-sm text-body-sm text-on-surface-variant">
                  {x.detail}
                  {conv && (
                    <div className="font-label-mono text-[11px] text-secondary mt-0.5">
                      → {conv} IST <TimeTag kind="PLAN" />
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <div className="font-label-mono text-[11px] text-outline">
          Basis: run{" "}
          <span title={proposal.optimization_run_id}>
            {shortId(proposal.optimization_run_id, 10)}
          </span>
          {" · "}generated {recorded(proposal.generated_at)}{" "}
          <TimeTag kind="RECORDED" />
        </div>
        <ProvenanceStrip p={proposal.provenance} />
        <ProvenanceChip />
      </div>

      <ObligationLine o={obligation} />
    </div>
  );
}

