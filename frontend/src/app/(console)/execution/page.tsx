"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import {
  JOB_TYPE_LABEL,
  api,
  listAllJobs,
  listAllObligations,
  sectionOf,
  severityOf,
  type Execution,
  type Job,
  type Obligation,
} from "@/lib/api";
import { locationText } from "@/lib/jobView";
import { planWindow } from "@/lib/time";
import { useQueryParam } from "@/lib/useQueryParam";
import { useResource } from "@/lib/useResource";
import ExecutionActions, { ExecutionRecordView } from "@/components/ExecutionDialogs";
import { ObligationLine } from "@/components/ProposalCard";
import {
  CARD,
  Chip,
  Empty,
  ErrorBox,
  Page,
  PageBanner,
  ProvenanceChip,
  SeverityChip,
  Skeleton,
  StatusChip,
  TimeTag,
} from "@/components/ui";

const STEPS = [
  { key: "APPROVED", label: "Approved", icon: "verified" },
  { key: "STARTED", label: "Execution started", icon: "play_circle" },
  { key: "EVIDENCE", label: "Evidence recorded", icon: "fact_check" },
  { key: "COMPLETED", label: "Completed", icon: "task_alt" },
] as const;

function stepIndex(job: Job, execs: Execution[]): number {
  if (job.status === "completed") return 3;
  if (job.status === "in_progress") {
    const cur = execs[execs.length - 1];
    return cur && cur.before_work_evidence.length > 0 ? 2 : 1;
  }
  if (job.status === "notified") return 0;
  return -1;
}

export default function ExecutionPage() {
  const wanted = useQueryParam("job");
  const jobs = useResource(() => listAllJobs(), []);
  const obls = useResource(() => listAllObligations(), []);
  const [picked, setPicked] = useState<string | null>(null);

  const all = useMemo(() => jobs.data ?? [], [jobs.data]);
  const live = useMemo(
    () => all.filter((j) => j.status === "notified" || j.status === "in_progress"),
    [all],
  );
  const done = all.filter((j) => j.status === "completed");

  const ids = useMemo(() => new Set(all.map((j) => j.job_id)), [all]);
  const selected =
    picked && ids.has(picked)
      ? picked
      : wanted && ids.has(wanted)
        ? wanted
        : (live[0]?.job_id ?? done[0]?.job_id ?? null);
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
        eyebrow="EXECUTION // EVIDENCE LEDGER"
        title="Crew Block Execution & Evidence Ledger"
        subtitle="Start an approved block, record before-work and after-work evidence references, and complete it. Evidence is a reference only; nothing is uploaded or stored."
        right={
          <>
            <ProvenanceChip />
            <Chip tone="violet">OBSERVED = crew-entered times</Chip>
          </>
        }
      />

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-space-lg items-start">
        <div className="xl:col-span-4 flex flex-col gap-space-md">
          <div className={CARD}>
            <div className="flex items-center justify-between mb-2">
              <h2 className="font-headline-sm text-headline-sm text-on-surface">Committed &amp; active blocks</h2>
              <Chip tone="cyan">{live.length}</Chip>
            </div>
            {jobs.loading && !jobs.data ? (
              <Skeleton rows={3} />
            ) : live.length === 0 && !jobs.error ? (
              <Empty title="No approved blocks">
                A block appears here after an authority approves it on{" "}
                <Link href="/review" className="text-primary underline">Authority Review</Link>.
              </Empty>
            ) : (
              <ul className="flex flex-col gap-2">
                {live.map((j) => (
                  <Row key={j.job_id} j={j} on={j.job_id === selected} onPick={() => setSelected(j.job_id)} />
                ))}
              </ul>
            )}
          </div>

          <div className={CARD}>
            <div className="flex items-center justify-between mb-2">
              <h2 className="font-headline-sm text-headline-sm text-on-surface">Completed ledger</h2>
              <Chip tone="neutral">{done.length}</Chip>
            </div>
            {done.length === 0 ? (
              <p className="font-body-sm text-body-sm text-outline">No completed executions yet.</p>
            ) : (
              <ul className="flex flex-col gap-2">
                {done.map((j) => (
                  <Row key={j.job_id} j={j} on={j.job_id === selected} onPick={() => setSelected(j.job_id)} />
                ))}
              </ul>
            )}
          </div>
        </div>

        <div className="xl:col-span-8 flex flex-col gap-space-md">
          {jobs.error && <ErrorBox error={jobs.error} />}
          {job ? (
            <Cockpit job={job} obligation={oblByJob.get(job.job_id)} />
          ) : (
            <div className={CARD}><Empty title="Select a block" /></div>
          )}
        </div>
      </div>
    </Page>
  );
}

function Row({ j, on, onPick }: { j: Job; on: boolean; onPick: () => void }) {
  return (
    <li>
      <button
        type="button"
        aria-pressed={on}
        onClick={onPick}
        className={`w-full text-left rounded-DEFAULT px-space-md py-space-sm transition-all ${on ? "bg-surface-container-high ring-1 ring-primary-container/50" : "bg-surface-container-low hover:bg-surface-container"}`}
      >
        <div className="flex items-center justify-between gap-2">
          <span className="font-label-mono text-label-mono font-bold text-primary">{j.job_id}</span>
          <StatusChip status={j.status} />
        </div>
        <div className="font-body-sm text-body-sm text-on-surface">{JOB_TYPE_LABEL[j.work_type] ?? j.work_type}</div>
        <div className="font-label-mono text-[11px] text-secondary">
          {j.schedule_start_minute != null && j.schedule_end_minute != null
            ? planWindow(j.schedule_start_minute, j.schedule_end_minute)
            : "—"}{" "}
          · {sectionOf(j)} · {j.track_id}
        </div>
      </button>
    </li>
  );
}

function Cockpit({ job, obligation }: { job: Job; obligation?: Obligation }) {
  const execs = useResource(() => api.executions(job.job_id), [job.job_id, job.status]);
  const list = execs.data?.executions ?? [];
  const idx = stepIndex(job, list);
  return (
    <div className={`${CARD} flex flex-col gap-space-md`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="font-headline-md text-headline-md text-primary">
            {JOB_TYPE_LABEL[job.work_type] ?? job.work_type}
          </h2>
          <div className="font-label-mono text-label-mono text-on-surface-variant">
            {job.job_id} · {locationText(job)} · {sectionOf(job)} · {job.track_id}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <SeverityChip severity={severityOf(job)} />
          <StatusChip status={job.status} />
        </div>
      </div>

      {/* lifecycle stepper */}
      <ol className="grid grid-cols-2 md:grid-cols-4 gap-2" aria-label="Execution lifecycle">
        {STEPS.map((s, i) => {
          const reached = idx >= i;
          const current = idx === i;
          return (
            <li
              key={s.key}
              aria-current={current ? "step" : undefined}
              className={`rounded-DEFAULT px-space-md py-space-sm flex items-center gap-2 ${reached ? "bg-primary-container/15 text-primary-fixed" : "bg-surface-container-lowest text-outline"} ${current ? "ring-1 ring-primary-container/60 shadow-[0_0_16px_rgba(0,219,233,0.2)]" : ""}`}
            >
              <span className="material-symbols-outlined text-[18px]">{s.icon}</span>
              <span className="font-label-mono text-label-mono">{s.label}</span>
            </li>
          );
        })}
      </ol>

      <div className="rounded-DEFAULT bg-surface-container-lowest px-space-md py-space-sm">
        <div className="font-label-caps text-label-caps text-outline flex items-center gap-2">
          PLANNED BLOCK <TimeTag kind="PLAN" />
        </div>
        <div className="font-headline-md text-headline-md text-secondary tabular-nums">
          {job.schedule_start_minute != null && job.schedule_end_minute != null
            ? planWindow(job.schedule_start_minute, job.schedule_end_minute)
            : "no committed window"}
        </div>
        <ObligationLine o={obligation} />
      </div>

      <ExecutionActions job={job} />

      {execs.error && <ErrorBox error={execs.error} />}
      {list.length > 0 && (
        <div className="flex flex-col gap-space-sm">
          <div className="font-label-caps text-label-caps text-outline">EXECUTION RECORD{list.length > 1 ? "S" : ""}</div>
          {[...list].reverse().map((x) => (
            <ExecutionRecordView key={x.execution_id} x={x} />
          ))}
        </div>
      )}
      {job.status === "notified" && list.length === 0 && (
        <p className="font-body-sm text-body-sm text-on-surface-variant">
          Approved and awaiting the crew. Starting execution needs an actual start time and at least one before-work evidence reference.
        </p>
      )}
      <Link href={`/audit?job=${job.job_id}`} className="font-label-mono text-label-mono text-primary underline self-start">
        View this job&apos;s audit trail →
      </Link>
    </div>
  );
}
