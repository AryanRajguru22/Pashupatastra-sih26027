"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import {
  JOB_TYPE_LABEL,
  api,
  listAllJobs,
  listAllObligations,
  scoreBand,
  sectionOf,
  severityOf,
  type Job,
  type Obligation,
} from "@/lib/api";
import { locationText } from "@/lib/jobView";
import { planWindow, recorded } from "@/lib/time";
import { useQueryParam } from "@/lib/useQueryParam";
import { useResource } from "@/lib/useResource";
import DecisionBar from "@/components/DecisionDialogs";
import ProposalCard, { ObligationLine } from "@/components/ProposalCard";
import {
  CARD,
  Chip,
  Empty,
  ErrorBox,
  Page,
  PageBanner,
  ProvenanceChip,
  Score,
  SeverityChip,
  Skeleton,
  StatusChip,
} from "@/components/ui";

export default function ReviewPage() {
  const wanted = useQueryParam("job");
  const jobs = useResource(() => listAllJobs(), []);
  const obls = useResource(() => listAllObligations(), []);
  const [picked, setPicked] = useState<string | null>(null);

  const all = useMemo(() => jobs.data ?? [], [jobs.data]);
  const queue = useMemo(
    () =>
      all
        .filter((j) => j.status === "scheduled")
        .sort((a, b) => b.priority_score - a.priority_score),
    [all],
  );
  const committed = all.filter((j) => j.status === "notified");
  const decided = all.filter(
    (j) => j.status === "reported" && !!j.last_refusal_reason,
  );

  // Effective selection: the user's pick, else ?job=, else the first pending proposal.
  const ids = useMemo(() => new Set(all.map((j) => j.job_id)), [all]);
  const selected =
    picked && ids.has(picked)
      ? picked
      : wanted && ids.has(wanted)
        ? wanted
        : (queue[0]?.job_id ?? null);
  const setSelected = setPicked;

  const job = all.find((j) => j.job_id === selected) ?? null;
  const oblByJob = useMemo(() => {
    const m = new Map<string, Obligation>();
    for (const o of obls.data?.items ?? []) m.set(o.job_id, o);
    return m;
  }, [obls.data]);

  return (
    <Page>
      <PageBanner
        serif
        eyebrow="AUTHORITY // DECISION COCKPIT"
        title="Authority Review & Commitment"
        subtitle="Review a proposed possession block, then approve, postpone or reject it. Approved blocks are pinned across every later optimization."
        right={
          <>
            <ProvenanceChip />
            <Chip tone="neutral" title="Role gating is a presentation convenience; the server does not enforce roles in demo mode.">
              DEMO MODE · DECLARED IDENTITY
            </Chip>
          </>
        }
      />

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-space-lg items-start">
        {/* queue */}
        <div className="xl:col-span-4 flex flex-col gap-space-md">
          <div className={CARD}>
            <div className="flex items-center justify-between mb-2">
              <h2 className="font-headline-sm text-headline-sm text-on-surface">
                Queue of pending proposals
              </h2>
              <Chip tone="amber">{queue.length}</Chip>
            </div>
            {jobs.loading && !jobs.data ? (
              <Skeleton rows={4} />
            ) : queue.length === 0 ? (
              <Empty title="No proposals awaiting a decision">
                Proposals appear here after an engineer runs the optimizer on{" "}
                <Link href="/planning" className="text-primary underline">
                  Planning
                </Link>
                .
              </Empty>
            ) : (
              <ul className="flex flex-col gap-2">
                {queue.map((j) => {
                  const o = oblByJob.get(j.job_id);
                  const on = j.job_id === selected;
                  return (
                    <li key={j.job_id}>
                      <button
                        type="button"
                        aria-pressed={on}
                        onClick={() => setSelected(j.job_id)}
                        className={`w-full text-left rounded-DEFAULT px-space-md py-space-sm transition-all ${on ? "bg-surface-container-high ring-1 ring-primary-container/50 shadow-[inset_0_0_16px_rgba(0,219,233,0.1)]" : "bg-surface-container-low hover:bg-surface-container"}`}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-label-mono text-label-mono font-bold text-primary">{j.job_id}</span>
                          <SeverityChip severity={severityOf(j)} />
                        </div>
                        <div className="font-body-sm text-body-sm text-on-surface">
                          {JOB_TYPE_LABEL[j.work_type] ?? j.work_type}
                        </div>
                        <div className="font-label-mono text-[11px] text-secondary">
                          {j.schedule_start_minute != null && j.schedule_end_minute != null
                            ? planWindow(j.schedule_start_minute, j.schedule_end_minute)
                            : "—"}{" "}
                          · {sectionOf(j)} · {j.track_id}
                        </div>
                        <div className="font-label-mono text-[11px] text-outline">
                          P {j.priority_score.toFixed(3)} {scoreBand(j.priority_score)}
                          {o && o.obligation_type !== "NONE"
                            ? ` · ${o.state}${o.is_past_due ? " · PAST DUE" : ""}`
                            : ""}
                        </div>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          {(committed.length > 0 || decided.length > 0) && (
            <div className={CARD}>
              <div className="font-label-caps text-label-caps text-outline mb-2">
                RECENT DECISIONS
              </div>
              <ul className="flex flex-col gap-1">
                {committed.map((j) => (
                  <li key={j.job_id} className="flex items-center justify-between gap-2">
                    <button type="button" className="font-label-mono text-label-mono text-primary hover:underline" onClick={() => setSelected(j.job_id)}>
                      {j.job_id}
                    </button>
                    <StatusChip status={j.status} />
                  </li>
                ))}
                {decided.map((j) => (
                  <li key={j.job_id} className="flex flex-col">
                    <div className="flex items-center justify-between gap-2">
                      <button type="button" className="font-label-mono text-label-mono text-primary hover:underline" onClick={() => setSelected(j.job_id)}>
                        {j.job_id}
                      </button>
                      <StatusChip status={j.status} />
                    </div>
                    <span className="font-body-sm text-[12px] text-outline">{j.last_refusal_reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        {/* cockpit */}
        <div className="xl:col-span-8 flex flex-col gap-space-md">
          {jobs.error && <ErrorBox error={jobs.error} />}
          {!job ? (
            <div className={CARD}>
              <Empty title="Select a proposal">
                Choose a pending proposal from the queue to review it.
              </Empty>
            </div>
          ) : (
            <Cockpit job={job} obligation={oblByJob.get(job.job_id)} />
          )}
        </div>
      </div>
    </Page>
  );
}

function Cockpit({ job, obligation }: { job: Job; obligation?: Obligation }) {
  const scheduled = job.status === "scheduled";
  const proposal = useResource(() => api.proposal(job.job_id), [job.job_id, job.proposal_run_id, job.status], scheduled);
  return (
    <div className={`${CARD} flex flex-col gap-space-md`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="font-headline-md text-headline-md text-primary">
            {job.job_id} · {JOB_TYPE_LABEL[job.work_type] ?? job.work_type}
          </h2>
          <div className="font-label-mono text-label-mono text-on-surface-variant">
            {locationText(job)} · {sectionOf(job)} · {job.track_id} · reported {recorded(job.created_at)}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <StatusChip status={job.status} />
          <span className="font-label-mono text-label-mono">
            P <Score value={job.priority_score} /> · R <Score value={job.risk_score} />
          </span>
        </div>
      </div>

      {scheduled ? (
        proposal.data ? (
          <ProposalCard proposal={proposal.data} obligation={obligation} />
        ) : proposal.error ? (
          <ErrorBox error={proposal.error} />
        ) : (
          <Skeleton rows={5} />
        )
      ) : job.status === "notified" ? (
        <div className="rounded-DEFAULT border border-primary-container/40 bg-primary-container/10 px-space-md py-space-md">
          <div className="flex items-center gap-2 font-headline-sm text-headline-sm text-primary-fixed">
            <span className="material-symbols-outlined">lock</span>
            Block committed · pinned across all future solves
          </div>
          <p className="font-body-sm text-body-sm text-on-surface-variant mt-1">
            Committed window{" "}
            {job.schedule_start_minute != null && job.schedule_end_minute != null
              ? planWindow(job.schedule_start_minute, job.schedule_end_minute)
              : "—"}{" "}
            (plan time). The status value is <code className="font-label-mono">notified</code>, but no notification is sent anywhere.
          </p>
          <div className="mt-2"><ObligationLine o={obligation} /></div>
          <div className="mt-3 flex flex-wrap gap-2">
            <Link href={`/execution?job=${job.job_id}`} className="font-label-mono text-label-mono text-primary underline">Open in Execution</Link>
          </div>
        </div>
      ) : (
        <div className="rounded-DEFAULT bg-surface-container-lowest px-space-md py-space-md">
          <div className="font-body-md text-body-md text-on-surface-variant">
            This job has no pending proposal. Status: <StatusChip status={job.status} />
          </div>
          {job.last_refusal_reason && (
            <blockquote className="mt-2 border-l-2 border-secondary/60 pl-2 font-body-sm text-body-sm text-on-surface-variant">
              Last decision reason: {job.last_refusal_reason}
            </blockquote>
          )}
        </div>
      )}

      <DecisionBar job={job} />

      <Link href={`/jobs/${job.job_id}`} className="font-label-mono text-label-mono text-primary underline self-start">
        Full job detail, scoring and audit →
      </Link>
    </div>
  );
}
