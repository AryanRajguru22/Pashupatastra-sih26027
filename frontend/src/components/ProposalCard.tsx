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

const MINUTES_RE = /minute\s+(\d+)\s*(?:to|-|–)\s*(\d+)/i;

/** Append plan-time conversion to a detail that quotes minute ranges. */
function withPlanTime(detail: string): string | null {
  const m = MINUTES_RE.exec(detail);
  if (!m) return null;
  return planWindow(Number(m[1]), Number(m[2]));
}

export function ProvenanceStrip({ p }: { p: Provenance }) {
  const items: [string, string][] = [
    ["topology", p.topology],
    ["timetable", p.timetable],
    ["assets", p.asset_condition],
    ["possession", p.possession],
  ];
  return (
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
          PLACEMENT (synthetic horizon, IST) <TimeTag kind="PLAN" />
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

