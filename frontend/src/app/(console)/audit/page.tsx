"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { JOB_TYPE_LABEL, api, listAllJobs } from "@/lib/api";
import { useQueryParam } from "@/lib/useQueryParam";
import { useResource } from "@/lib/useResource";
import AuditTimeline from "@/components/AuditTimeline";
import {
  CARD,
  Chip,
  Empty,
  ErrorBox,
  Page,
  PageBanner,
  Skeleton,
  StatusChip,
} from "@/components/ui";

export default function AuditPage() {
  const wanted = useQueryParam("job");
  const jobs = useResource(() => listAllJobs(), []);
  const [picked, setPicked] = useState<string | null>(null);
  const [q, setQ] = useState("");

  const all = useMemo(
    () => [...(jobs.data ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at)),
    [jobs.data],
  );

  const ids = useMemo(() => new Set(all.map((j) => j.job_id)), [all]);
  const selected =
    picked && ids.has(picked)
      ? picked
      : wanted && ids.has(wanted)
        ? wanted
        : (all[0]?.job_id ?? null);
  const setSelected = setPicked;

  const history = useResource(
    () => api.history(selected as string),
    [selected],
    !!selected,
  );
  const job = all.find((j) => j.job_id === selected);
  const shown = all.filter((j) => j.job_id.toLowerCase().includes(q.trim().toLowerCase()));

  return (
    <Page>
      <PageBanner
        serif
        eyebrow="ASSURANCE // JOB HISTORY"
        title="Operational Audit Trail"
        subtitle="Every lifecycle step recorded by the backend for a job: who acted, in what declared role, the state before and after, and the reason. Events are append-only."
        right={
          <Chip tone="neutral" title="Identities are declared through request headers and recorded as DECLARED_UNVERIFIED.">
            ACTORS: DECLARED, NOT VERIFIED
          </Chip>
        }
      />

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-space-lg items-start">
        <div className="xl:col-span-3">
          <div className={CARD}>
            <div className="font-label-caps text-label-caps text-outline mb-2">SELECT A JOB</div>
            <input
              aria-label="Filter jobs by id"
              className="w-full mb-2 bg-surface-container-lowest text-on-surface px-space-md py-space-sm rounded-DEFAULT font-label-mono text-label-mono border border-outline-variant/30 focus:outline-none focus:border-primary-container/60"
              placeholder="Filter by job id"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            {jobs.error && <ErrorBox error={jobs.error} title="COULD NOT LOAD JOBS" />}
            {jobs.loading && !jobs.data ? (
              <Skeleton rows={4} />
            ) : shown.length === 0 && !jobs.error ? (
              <Empty title="No jobs" />
            ) : (
              <ul className="flex flex-col gap-1">
                {shown.map((j) => (
                  <li key={j.job_id}>
                    <button
                      type="button"
                      aria-pressed={j.job_id === selected}
                      onClick={() => setSelected(j.job_id)}
                      className={`w-full text-left rounded-DEFAULT px-space-sm py-2 ${j.job_id === selected ? "bg-surface-container-high ring-1 ring-primary-container/50" : "bg-surface-container-low hover:bg-surface-container"}`}
                    >
                      <div className="font-label-mono text-label-mono text-primary">{j.job_id}</div>
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-body-sm text-[12px] text-on-surface-variant truncate">
                          {JOB_TYPE_LABEL[j.work_type] ?? j.work_type}
                        </span>
                        <StatusChip status={j.status} />
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        <div className="xl:col-span-9">
          <div className={CARD}>
            {job && (
              <div className="flex flex-wrap items-center justify-between gap-2 mb-space-md">
                <h2 className="font-headline-sm text-headline-sm text-primary">
                  {job.job_id} · {JOB_TYPE_LABEL[job.work_type] ?? job.work_type}
                </h2>
                <Link href={`/jobs/${job.job_id}`} className="font-label-mono text-label-mono text-primary underline">
                  Job detail →
                </Link>
              </div>
            )}
            {history.error && <ErrorBox error={history.error} />}
            {history.loading && !history.data ? (
              <Skeleton rows={6} />
            ) : history.data ? (
              <AuditTimeline events={history.data.events} />
            ) : !selected ? (
              <Empty title="No job selected" />
            ) : null}
          </div>
        </div>
      </div>
    </Page>
  );
}
