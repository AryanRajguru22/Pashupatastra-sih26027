"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import {
  JOB_TYPE_LABEL,
  STATUS_LABEL,
  STATUS_ORDER,
  listAllJobs,
  listAllObligations,
  scoreBand,
  sectionOf,
  severityOf,
  type Job,
  type JobStatus,
  type Obligation,
} from "@/lib/api";
import { SECTION_IDS, TRACKS, stationName } from "@/lib/fieldLocation";
import { duration, planWindow, recorded, shortId } from "@/lib/time";
import { durationOf, locationText, obligationText } from "@/lib/jobView";
import { pick } from "@/lib/railwayData";
import { useResource } from "@/lib/useResource";
import {
  BTN_GHOST,
  CARD,
  Chip,
  Empty,
  ErrorBox,
  INPUT,
  Page,
  PageBanner,
  ProvenanceChip,
  Score,
  SeverityChip,
  Skeleton,
  StatusChip,
  TimeTag,
} from "@/components/ui";

type SortKey = "priority" | "risk" | "reported" | "window" | "status";

const SORTS: { key: SortKey; label: string }[] = [
  { key: "priority", label: "Priority ↓" },
  { key: "risk", label: "Risk ↓" },
  { key: "reported", label: "Newest reported" },
  { key: "window", label: "Planned window" },
  { key: "status", label: "Lifecycle order" },
];

export default function JobsPage() {
  const router = useRouter();
  const jobs = useResource(() => listAllJobs(), []);
  const obls = useResource(() => listAllObligations(), []);

  const [status, setStatus] = useState<JobStatus | "ALL">("ALL");
  const [track, setTrack] = useState("ALL");
  const [section, setSection] = useState("ALL");
  const [type, setType] = useState("ALL");
  const [sev, setSev] = useState("ALL");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<SortKey>("priority");

  const all = useMemo(() => jobs.data ?? [], [jobs.data]);
  const oblByJob = useMemo(() => {
    const m = new Map<string, Obligation>();
    for (const o of obls.data?.items ?? []) m.set(o.job_id, o);
    return m;
  }, [obls.data]);

  const counts = useMemo(() => {
    const c: Record<string, number> = { ALL: all.length };
    for (const s of STATUS_ORDER) c[s] = 0;
    for (const j of all) c[j.status] = (c[j.status] ?? 0) + 1;
    return c;
  }, [all]);

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const out = all.filter((j) => {
      if (status !== "ALL" && j.status !== status) return false;
      if (track !== "ALL" && j.track_id !== track) return false;
      if (section !== "ALL" && sectionOf(j) !== section) return false;
      if (type !== "ALL" && j.work_type !== type) return false;
      if (sev !== "ALL" && severityOf(j) !== sev) return false;
      if (needle) {
        const hay = `${j.job_id} ${sectionOf(j) ?? ""} ${j.work_type} ${JOB_TYPE_LABEL[j.work_type] ?? ""}`.toLowerCase();
        if (!hay.includes(needle)) return false;
      }
      return true;
    });
    const by = (a: Job, b: Job): number => {
      switch (sort) {
        case "risk":
          return b.risk_score - a.risk_score;
        case "reported":
          return b.created_at.localeCompare(a.created_at);
        case "window": {
          const x = a.schedule_start_minute ?? Number.POSITIVE_INFINITY;
          const y = b.schedule_start_minute ?? Number.POSITIVE_INFINITY;
          return x - y;
        }
        case "status":
          return STATUS_ORDER.indexOf(a.status) - STATUS_ORDER.indexOf(b.status);
        default:
          return (
            b.priority_score - a.priority_score || b.risk_score - a.risk_score
          );
      }
    };
    return [...out].sort(by);
  }, [all, status, track, section, type, sev, q, sort]);

  const filtersActive =
    status !== "ALL" ||
    track !== "ALL" ||
    section !== "ALL" ||
    type !== "ALL" ||
    sev !== "ALL" ||
    q !== "";

  const open = all.filter((j) => j.status !== "completed");
  const critical = open.filter((j) => severityOf(j) === "CRITICAL").length;
  const totalMin = open.reduce((n, j) => n + (durationOf(j) ?? 0), 0);

  return (
    <Page>
      <PageBanner
        eyebrow="MAINTENANCE PIPELINE // OPERATE"
        title={
          <span className="flex items-center gap-space-sm">
            Maintenance work queue
            <span className="font-label-caps text-label-caps tracking-widest px-2 py-0.5 rounded bg-surface-container-highest text-secondary">
              CORR-NDLS-AGC
            </span>
          </span>
        }
        subtitle={pick(
          "Deterministic risk scoring (fixed-weight baseline scorer, not a trained model) over demo maintenance inputs on the NDLS–AGC public-data snapshot.",
          "Deterministic risk scoring (fixed-weight baseline scorer, not a trained model) over the synthetic NDLS–AGC scenario.",
        )}
        right={
          <>
            <ProvenanceChip />
            <button
              type="button"
              className={BTN_GHOST}
              onClick={() => {
                jobs.reload();
                obls.reload();
              }}
            >
              <span className="material-symbols-outlined text-[15px]">refresh</span>
              Refresh
            </button>
          </>
        }
      />

      <div className="grid grid-cols-3 gap-2 bg-surface-container-low p-2 rounded-DEFAULT shadow-md max-w-2xl">
        <Metric k="ACTIVE JOBS" v={String(open.length)} />
        <Metric k="CRITICAL (REPORTED)" v={String(critical)} tone="text-secondary" />
        <Metric
          k="MODELLED WORK"
          v={totalMin ? `${totalMin} MIN` : "—"}
          hint="Sum of modelled block durations for active jobs"
        />
      </div>

      {/* filters */}
      <div className="flex flex-col gap-space-sm bg-surface-container-low p-space-sm rounded-DEFAULT shadow-md">
        <div className="flex items-center gap-1 overflow-x-auto">
          {(["ALL", ...STATUS_ORDER] as const).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setStatus(s)}
              className={`px-3 py-1.5 rounded-DEFAULT font-label-mono text-label-mono transition-all flex items-center gap-1.5 whitespace-nowrap ${status === s ? "bg-primary-container text-on-primary-container shadow-sm font-medium" : "text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high"}`}
            >
              <span>{s === "ALL" ? "ALL" : STATUS_LABEL[s].toUpperCase()}</span>
              <span className="px-1.5 rounded-full text-[10px] bg-surface-container-highest text-primary font-bold">
                {counts[s] ?? 0}
              </span>
            </button>
          ))}
        </div>
        <div className="grid grid-cols-2 md:grid-cols-6 gap-space-xs">
          <div className="col-span-2 relative flex items-center">
            <span className="material-symbols-outlined absolute left-3 text-[18px] text-outline pointer-events-none">
              manage_search
            </span>
            <input
              className={`${INPUT} pl-9`}
              placeholder="Search job ID, section or work type"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              aria-label="Search jobs"
            />
          </div>
          <Select label="TRACK" value={track} onChange={setTrack} options={[["ALL", "All tracks"], ...TRACKS.map((t) => [t, t] as [string, string])]} />
          <Select label="SECTION" value={section} onChange={setSection} options={[["ALL", "All sections"], ...SECTION_IDS.map((s) => [s, s] as [string, string])]} />
          <Select label="WORK" value={type} onChange={setType} options={[["ALL", "All work types"], ...Object.entries(JOB_TYPE_LABEL)]} />
          <Select label="SEVERITY" value={sev} onChange={setSev} options={[["ALL", "All severities"], ["CRITICAL", "Critical"], ["MODERATE", "Moderate"], ["MINOR", "Minor"], ["NONE", "None"]]} />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-label-caps text-label-caps text-outline">SORT</span>
          {SORTS.map((s) => (
            <button
              key={s.key}
              type="button"
              onClick={() => setSort(s.key)}
              className={`px-2.5 py-1 rounded-full font-label-mono text-label-mono ${sort === s.key ? "bg-surface-bright text-primary" : "text-on-surface-variant hover:text-on-surface"}`}
            >
              {s.label}
            </button>
          ))}
          {filtersActive && (
            <button
              type="button"
              className="ml-auto font-label-mono text-label-mono text-secondary hover:underline"
              onClick={() => {
                setStatus("ALL");
                setTrack("ALL");
                setSection("ALL");
                setType("ALL");
                setSev("ALL");
                setQ("");
              }}
            >
              Clear filters
            </button>
          )}
        </div>
      </div>

      {jobs.error && <ErrorBox error={jobs.error} title="COULD NOT LOAD JOBS" />}

      <div className="flex flex-col bg-surface-container-low rounded-DEFAULT shadow-xl overflow-hidden">
        <div className="hidden lg:grid grid-cols-12 items-center bg-surface-container px-space-sm py-space-xs font-label-caps text-label-caps text-outline tracking-wider select-none">
          <div className="col-span-2">JOB</div>
          <div className="col-span-3">WORK · LOCATION</div>
          <div className="col-span-2">SEVERITY · SCORES</div>
          <div className="col-span-2">SECTION · TRACK</div>
          <div className="col-span-2">STATUS · WINDOW</div>
          <div className="col-span-1 text-right">OPEN</div>
        </div>
        {jobs.loading && !jobs.data ? (
          <div className="p-space-md">
            <Skeleton rows={5} />
          </div>
        ) : rows.length === 0 && !jobs.error ? (
          <div className="p-space-lg">
            <Empty title={all.length === 0 ? "No jobs in the backend" : "No jobs match these filters"}>
              {all.length === 0 ? (
                <>
                  The demo database may not be seeded, or the backend was not
                  started with PASHUPAT_CORRIDOR_ID=CORR-NDLS-AGC. See the demo
                  runbook. You can also{" "}
                  <Link href="/inspection" className="text-primary underline">
                    report a defect
                  </Link>
                  .
                </>
              ) : (
                "Adjust or clear the filters above."
              )}
            </Empty>
          </div>
        ) : (
          rows.map((j) => {
            const o = oblByJob.get(j.job_id);
            const s = severityOf(j);
            return (
              <div
                key={j.job_id}
                role="link"
                tabIndex={0}
                aria-label={`Open job ${j.job_id}`}
                onClick={() => router.push(`/jobs/${j.job_id}`)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") router.push(`/jobs/${j.job_id}`);
                }}
                className={`group grid grid-cols-12 items-center gap-y-1 px-space-sm py-2.5 hover:bg-surface-container cursor-pointer transition-all border-l-2 ${s === "CRITICAL" ? "border-error/60" : "border-transparent"} hover:border-primary-container border-b border-outline-variant/10`}
              >
                <div className="col-span-6 lg:col-span-2 flex flex-col min-w-0">
                  <span className="font-label-mono text-label-mono font-bold text-primary tracking-wider group-hover:text-primary-container">
                    {j.job_id}
                  </span>
                  <span
                    className="font-label-mono text-[10px] text-outline"
                    title={`Proposal run ${j.proposal_run_id ?? "none"}`}
                  >
                    run {shortId(j.proposal_run_id)} ·{" "}
                    {j.last_solver_status ?? "never optimized"}
                  </span>
                </div>
                <div className="col-span-6 lg:col-span-3 flex flex-col min-w-0 pr-2">
                  <span className="font-body-sm text-body-sm font-semibold text-on-surface truncate">
                    {JOB_TYPE_LABEL[j.work_type] ?? j.work_type}
                  </span>
                  <span className="font-label-mono text-[10px] text-on-surface-variant truncate">
                    {locationText(j)}
                  </span>
                </div>
                <div className="col-span-6 lg:col-span-2 flex flex-col">
                  <div className="flex items-center gap-1.5">
                    <SeverityChip severity={s} />
                  </div>
                  <span className="font-label-mono text-[10px] text-on-surface-variant mt-0.5">
                    P <Score value={j.priority_score} /> {scoreBand(j.priority_score)[0]} · R{" "}
                    <Score value={j.risk_score} />
                  </span>
                </div>
                <div className="col-span-6 lg:col-span-2 flex flex-col">
                  <span className="font-label-mono text-label-mono text-on-surface">
                    {sectionOf(j) ?? "—"}
                  </span>
                  <span className="font-label-caps text-[10px] text-outline">
                    {j.track_id}
                  </span>
                </div>
                <div className="col-span-8 lg:col-span-2 flex flex-col gap-0.5 items-start">
                  <StatusChip status={j.status} />
                  <span className="font-label-mono text-[10px] text-secondary">
                    {j.schedule_start_minute != null && j.schedule_end_minute != null
                      ? planWindow(j.schedule_start_minute, j.schedule_end_minute)
                      : "no window yet"}
                  </span>
                  <span className="font-label-mono text-[10px] text-outline">
                    {obligationText(o)}
                    {o?.is_past_due ? " · PAST DUE" : ""}
                  </span>
                </div>
                <div className="col-span-4 lg:col-span-1 flex justify-end">
                  <RowAction job={j} />
                </div>
              </div>
            );
          })
        )}
        <div className="flex flex-wrap items-center justify-between gap-2 px-space-sm py-space-xs bg-surface-container font-label-mono text-[11px] text-outline">
          <span>
            {rows.length} of {all.length} jobs · window times are <TimeTag kind="PLAN" /> · reported{" "}
            <TimeTag kind="RECORDED" />
          </span>
          {all[0] && <span>newest report {recorded(all[0].created_at)}</span>}
        </div>
      </div>

      <div className={`${CARD} grid md:grid-cols-3 gap-space-md`}>
        <Note k="Last decision reason" v="Authority postpone/reject reasons and solver refusals are shown on the job detail page." />
        <Note k="Duration" v={`Modelled block length from the work type (e.g. ${duration(90 * 60)} for an emergency repair).`} />
        <Note k="Stations" v={pick(`Sections use the real stations (${stationName("MTJ")}, ${stationName("RKM")} …) at dated public-snapshot chainage. The observations are demo inputs grounded in real railway work categories.`, `Sections use real station codes (${stationName("MTJ")}, ${stationName("RKM")} …) on a synthetic corridor.`)} />
      </div>
    </Page>
  );
}

function RowAction({ job }: { job: Job }) {
  const base =
    "px-2 py-1 rounded font-label-mono text-[11px] font-semibold transition-colors flex items-center gap-1";
  const stop = (e: React.MouseEvent) => e.stopPropagation();
  if (job.status === "scheduled")
    return (
      <Link
        href={`/review?job=${job.job_id}`}
        onClick={stop}
        className={`${base} bg-secondary-container text-on-secondary-container hover:bg-secondary`}
      >
        REVIEW
      </Link>
    );
  if (job.status === "notified" || job.status === "in_progress")
    return (
      <Link
        href={`/execution?job=${job.job_id}`}
        onClick={stop}
        className={`${base} bg-primary-container/20 text-primary-fixed hover:bg-primary-container/30`}
      >
        {job.status === "notified" ? "START" : "COMPLETE"}
      </Link>
    );
  if (job.status === "completed")
    return (
      <Link
        href={`/audit?job=${job.job_id}`}
        onClick={stop}
        className={`${base} bg-surface-container-high text-on-surface-variant hover:text-primary`}
      >
        AUDIT
      </Link>
    );
  return (
    <Link
      href={`/jobs/${job.job_id}`}
      onClick={stop}
      className={`${base} bg-surface-container-high text-on-surface-variant hover:text-primary`}
    >
      VIEW
    </Link>
  );
}

function Metric({
  k,
  v,
  tone = "text-primary",
  hint,
}: {
  k: string;
  v: string;
  tone?: string;
  hint?: string;
}) {
  return (
    <div className="px-space-sm py-1 bg-surface-container rounded flex flex-col" title={hint}>
      <span className="font-label-caps text-label-caps text-outline">{k}</span>
      <span className={`font-label-mono text-label-mono font-bold ${tone}`}>{v}</span>
    </div>
  );
}

function Select({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: [string, string][] | string[][];
}) {
  return (
    <label className="flex items-center gap-space-2xs">
      <span className="font-label-caps text-label-caps text-outline hidden xl:inline">
        {label}
      </span>
      <select
        aria-label={label}
        className={INPUT}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {options.map(([v, l]) => (
          <option key={v} value={v}>
            {l}
          </option>
        ))}
      </select>
    </label>
  );
}

function Note({ k, v }: { k: string; v: string }) {
  return (
    <div>
      <Chip tone="neutral">{k}</Chip>
      <p className="font-body-sm text-body-sm text-on-surface-variant mt-1">{v}</p>
    </div>
  );
}
