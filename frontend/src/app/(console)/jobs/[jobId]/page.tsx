"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import {
  JOB_TYPE_LABEL,
  api,
  bcMeta,
  scoreBand,
  sectionOf,
  severityOf,
  type Job,
} from "@/lib/api";
import { durationOf, locationText } from "@/lib/jobView";
import { duration, planWindow, recorded, shortId } from "@/lib/time";
import { useResource } from "@/lib/useResource";
import AuditTimeline from "@/components/AuditTimeline";
import DecisionBar from "@/components/DecisionDialogs";
import ExecutionActions, { ExecutionRecordView } from "@/components/ExecutionDialogs";
import ProposalCard, { ObligationLine } from "@/components/ProposalCard";
import ScoringPanel from "@/components/ScoringPanel";
import {
  CARD,
  Chip,
  Empty,
  ErrorBox,
  Kv,
  Page,
  PageBanner,
  ProvenanceChip,
  Score,
  SeverityChip,
  Skeleton,
  StatusChip,
  TimeTag,
} from "@/components/ui";

const TABS = ["Overview", "Scoring", "Proposal", "Execution", "Audit", "Obligation"] as const;
type Tab = (typeof TABS)[number];

export default function JobDetailPage() {
  const params = useParams<{ jobId: string }>();
  const jobId = decodeURIComponent(params.jobId);
  const [tab, setTab] = useState<Tab>("Overview");

  const job = useResource(() => api.getJob(jobId), [jobId]);
  const history = useResource(() => api.history(jobId), [jobId]);
  const obligation = useResource(() => api.obligation(jobId), [jobId]);
  const isScheduled = job.data?.status === "scheduled";
  const proposal = useResource(() => api.proposal(jobId), [jobId, job.data?.status, job.data?.proposal_run_id], isScheduled);
  const execs = useResource(() => api.executions(jobId), [jobId, job.data?.status]);

  if (job.error && !job.data) {
    return (
      <Page>
        <PageBanner eyebrow="MAINTENANCE JOB" title={jobId} />
        <ErrorBox error={job.error} title={job.error.code === "JOB_NOT_FOUND" ? "JOB NOT FOUND" : "COULD NOT LOAD JOB"} />
        <Link href="/jobs" className="font-label-mono text-label-mono text-primary underline">
          ← Maintenance Jobs
        </Link>
      </Page>
    );
  }
  const j = job.data;
  if (!j) {
    return (
      <Page>
        <PageBanner eyebrow="MAINTENANCE JOB" title={jobId} />
        <Skeleton rows={6} />
      </Page>
    );
  }

  return (
    <Page>
      <PageBanner
        eyebrow="MAINTENANCE JOB // DETAIL"
        title={
          <span className="flex flex-wrap items-center gap-space-sm">
            {JOB_TYPE_LABEL[j.work_type] ?? j.work_type}
            <span className="font-label-mono text-label-mono text-outline">{j.job_id}</span>
          </span>
        }
        subtitle={j.description}
        right={
          <>
            <StatusChip status={j.status} />
            <SeverityChip severity={severityOf(j)} />
            <ProvenanceChip />
          </>
        }
      />

      <div className="flex flex-wrap items-center justify-between gap-space-sm">
        <div role="tablist" aria-label="Job sections" className="flex flex-wrap gap-1 bg-surface-container-low p-1 rounded-DEFAULT">
          {TABS.map((t) => (
            <button
              key={t}
              type="button"
              role="tab"
              aria-selected={tab === t}
              onClick={() => setTab(t)}
              className={`px-3 py-1.5 rounded-DEFAULT font-label-mono text-label-mono transition-all ${tab === t ? "bg-primary-container text-on-primary-container font-medium" : "text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high"}`}
            >
              {t}
            </button>
          ))}
        </div>
        <div className="flex flex-col gap-2 items-end">
          {j.status === "scheduled" && <DecisionBar job={j} />}
          {j.status === "notified" && (
            <div className="flex flex-col gap-2 items-end">
              <ExecutionActions job={j} />
              <DecisionBar job={j} />
            </div>
          )}
          {j.status === "in_progress" && <ExecutionActions job={j} />}
        </div>
      </div>

      <div className={CARD}>
        {tab === "Overview" && <Overview job={j} />}
        {tab === "Scoring" &&
          (history.data ? (
            <ScoringPanel job={j} events={history.data.events} />
          ) : history.error ? (
            <ErrorBox error={history.error} />
          ) : (
            <Skeleton rows={4} />
          ))}
        {tab === "Proposal" && (
          <>
            {isScheduled ? (
              proposal.data ? (
                <ProposalCard proposal={proposal.data} obligation={obligation.data} />
              ) : proposal.error ? (
                <ErrorBox error={proposal.error} />
              ) : (
                <Skeleton rows={4} />
              )
            ) : (
              <NoProposal job={j} />
            )}
          </>
        )}
        {tab === "Execution" && (
          <div className="flex flex-col gap-space-md">
            {execs.data && execs.data.executions.length > 0 ? (
              execs.data.executions.map((x) => <ExecutionRecordView key={x.execution_id} x={x} />)
            ) : execs.error ? (
              <ErrorBox error={execs.error} />
            ) : (
              <Empty title="No execution recorded">
                {j.status === "notified"
                  ? "The block is approved. A worker can start execution."
                  : "Execution begins after an authority commits a block."}
              </Empty>
            )}
          </div>
        )}
        {tab === "Audit" &&
          (history.data ? (
            <AuditTimeline events={history.data.events} />
          ) : history.error ? (
            <ErrorBox error={history.error} />
          ) : (
            <Skeleton rows={5} />
          ))}
        {tab === "Obligation" && (
          <div className="flex flex-col gap-space-sm">
            {obligation.data ? (
              <>
                <ObligationLine o={obligation.data} />
                <div className="rounded-DEFAULT bg-surface-container-lowest px-space-md py-1">
                  <Kv k="OBLIGATION">{obligation.data.obligation_type}</Kv>
                  <Kv k="STATE">{obligation.data.state}</Kv>
                  <Kv k="OWED BY (ROLE)">{obligation.data.owed_role ?? "—"}</Kv>
                  <Kv k="PAST DUE">{String(obligation.data.is_past_due)}</Kv>
                  <Kv k="CLOCK STARTED">{recorded(obligation.data.clock_started_at)}</Kv>
                  <Kv k="DUE">{recorded(obligation.data.due_at)}</Kv>
                  <Kv k="SLA">{duration(obligation.data.sla_seconds)}</Kv>
                  <Kv k="ELAPSED">{duration(obligation.data.elapsed_seconds)}</Kv>
                  <Kv k="ESCALATION LEVEL">L{obligation.data.escalation_level ?? 0}</Kv>
                  <Kv k="REASON">
                    {obligation.data.reason_code} — {obligation.data.reason}
                  </Kv>
                  <Kv k="POLICY">
                    {obligation.data.policy_version} (assumed: {String(obligation.data.policy_assumed)})
                  </Kv>
                  <Kv k="EVALUATED">{recorded(obligation.data.evaluated_at)}</Kv>
                </div>
                <p className="font-label-mono text-[11px] text-outline">
                  ASSUMED demo engineering values — not Indian Railways policy. <TimeTag kind="RECORDED" />
                </p>
              </>
            ) : obligation.error ? (
              <ErrorBox error={obligation.error} />
            ) : (
              <Skeleton rows={3} />
            )}
          </div>
        )}
      </div>
    </Page>
  );
}

function NoProposal({ job }: { job: Job }) {
  // A non-scheduled job answers 409 NO_CURRENT_PROPOSAL, whose detail says
  // WHY (decision by whom, or a solver outcome). That is a state, not a failure.
  const res = useResource(() => api.proposal(job.job_id), [job.job_id, job.status]);
  const state = res.error;
  return (
    <div className="flex flex-col gap-space-sm">
      <Empty title="No pending proposal">
        A proposal exists only while a job is <code className="font-label-mono">scheduled</code>. Current status: <StatusChip status={job.status} />
      </Empty>
      {state && (
        <div className="rounded-DEFAULT bg-surface-container-lowest px-space-md py-space-sm">
          <div className="font-label-caps text-label-caps text-outline">{state.code}</div>
          <p className="font-body-sm text-body-sm text-on-surface-variant">{state.detail}</p>
        </div>
      )}
    </div>
  );
}

function Overview({ job }: { job: Job }) {
  const m = bcMeta(job);
  const assoc = m.asset_association as
    | { asset_id?: string; distance_km?: number; asset_section_id?: string; job_section_id?: string; section_match?: boolean }
    | undefined;
  const dup = m.duplicate_detection as { possible_duplicate?: boolean; candidate_jobs?: unknown[] } | undefined;
  const dur = durationOf(job);
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-space-lg">
      <div>
        <h3 className="font-headline-sm text-headline-sm text-primary mb-1">Report</h3>
        <div className="rounded-DEFAULT bg-surface-container-lowest px-space-md py-1">
          <Kv k="JOB">{job.job_id}</Kv>
          <Kv k="WORK TYPE">{JOB_TYPE_LABEL[job.work_type] ?? job.work_type}</Kv>
          <Kv k="SEVERITY (WORKER-REPORTED)">
            <SeverityChip severity={severityOf(job)} />
          </Kv>
          <Kv k="LOCATION">{locationText(job)}</Kv>
          <Kv k="SECTION · TRACK">
            {sectionOf(job) ?? "—"} · {job.track_id}
          </Kv>
          <Kv k="CORRIDOR CHAINAGE">
            {Math.round(job.distance_start)}–{Math.round(job.distance_end)} m
          </Kv>
          <Kv k="LOCATION CHECK">
            {m.location_source === "FIELD_LOCATION_VERIFIED"
              ? "Location cross-check passed"
              : String(m.location_source ?? "—")}
          </Kv>
          <Kv k="CREW">
            {job.workers_min}–{job.workers_max}
          </Kv>
          <Kv k="MODELLED DURATION">{dur != null ? `${dur} min` : "—"}</Kv>
          <Kv k="EVIDENCE REFERENCE">{String(m.evidence_reference ?? "—")}</Kv>
          <Kv k="REPORTED">
            {recorded(job.created_at)} <TimeTag kind="RECORDED" />
          </Kv>
          <Kv k="LAST UPDATED">{recorded(job.updated_at)}</Kv>
        </div>
      </div>
      <div className="flex flex-col gap-space-md">
        <div>
          <h3 className="font-headline-sm text-headline-sm text-primary mb-1">Planning state</h3>
          <div className="rounded-DEFAULT bg-surface-container-lowest px-space-md py-1">
            <Kv k="STATUS">
              <StatusChip status={job.status} />
            </Kv>
            <Kv k="PRIORITY">
              <Score value={job.priority_score} /> {scoreBand(job.priority_score)}
            </Kv>
            <Kv k="RISK">
              <Score value={job.risk_score} /> {scoreBand(job.risk_score)}
            </Kv>
            <Kv k="WINDOW (PLAN)">
              {job.schedule_start_minute != null && job.schedule_end_minute != null
                ? planWindow(job.schedule_start_minute, job.schedule_end_minute)
                : "no window yet"}
            </Kv>
            <Kv k="PROPOSAL RUN">
              <span title={job.proposal_run_id ?? ""}>{shortId(job.proposal_run_id, 12)}</span>
            </Kv>
            <Kv k="LAST SOLVER STATUS">{job.last_solver_status ?? "Never optimized"}</Kv>
            <Kv k="LAST DECISION REASON" mono={false}>
              {job.last_refusal_reason ?? "—"}
            </Kv>
          </div>
        </div>
        <div>
          <h3 className="font-headline-sm text-headline-sm text-primary mb-1">Asset association</h3>
          {assoc ? (
            <div className="rounded-DEFAULT bg-surface-container-lowest px-space-md py-1">
              <Kv k="NEAREST SYNTHETIC ASSET">{assoc.asset_id}</Kv>
              <Kv k="DISTANCE">{assoc.distance_km} km</Kv>
              <Kv k="ASSET SECTION">{assoc.asset_section_id}</Kv>
              <Kv k="SAME SECTION AS JOB">
                {String(assoc.section_match)}
                {assoc.section_match === false ? " — nearest asset is in another section" : ""}
              </Kv>
              <p className="font-label-mono text-[11px] text-outline pt-1">
                Derived, synthetic. This does not mean the named asset was inspected.
              </p>
            </div>
          ) : (
            <p className="font-body-sm text-body-sm text-outline">Not recorded.</p>
          )}
        </div>
        {dup?.possible_duplicate && (
          <div className="rounded-DEFAULT bg-secondary-container/20 px-space-md py-space-sm font-body-sm text-body-sm text-secondary">
            Advisory: possible duplicate of {(dup.candidate_jobs ?? []).length} open job(s). Nothing was merged.
          </div>
        )}
        <Chip tone="neutral">Duplicate detection is advisory only</Chip>
      </div>
    </div>
  );
}
