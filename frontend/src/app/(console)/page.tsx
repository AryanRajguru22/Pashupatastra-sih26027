"use client";

import Link from "next/link";
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
} from "@/lib/api";
import { SECTION_IDS } from "@/lib/fieldLocation";
import { planWindow, recorded, shortId } from "@/lib/time";
import { useSession } from "@/lib/session";
import { useLastRun } from "@/lib/useLastRun";
import { useResource } from "@/lib/useResource";
import CorridorScene, { type SceneMode } from "@/components/CorridorScene";
import {
  BTN_GHOST,
  CARD,
  Chip,
  Empty,
  ErrorBox,
  Kv,
  ProvenanceChip,
  Score,
  SeverityChip,
  Skeleton,
  StatusChip,
  TimeTag,
} from "@/components/ui";

export default function CommandCenter() {
  const { lastOptimize } = useSession();
  const jobs = useResource(() => listAllJobs(), []);
  const obls = useResource(() => listAllObligations(), []);
  const [mode, setMode] = useState<SceneMode>("SPATIAL");
  const [showJobs, setShowJobs] = useState(true);
  const [showStations, setShowStations] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);

  const all = useMemo(() => jobs.data ?? [], [jobs.data]);
  const active = useMemo(() => all.filter((j) => j.status !== "completed"), [all]);
  const byStatus = useMemo(() => {
    const c: Record<string, number> = {};
    for (const s of STATUS_ORDER) c[s] = 0;
    for (const j of all) c[j.status] = (c[j.status] ?? 0) + 1;
    return c;
  }, [all]);
  const critical = active.filter((j) => severityOf(j) === "CRITICAL");
  const top = useMemo(
    () => [...active].sort((a, b) => b.priority_score - a.priority_score).slice(0, 5),
    [active],
  );

  // Last optimization: the session response if present, else the newest run
  // referenced by any job (there is no "list runs" endpoint).
  const derivedRun = useLastRun(jobs.data);

  const openObl = (obls.data?.items ?? []).filter((o) => o.is_open);
  const pastDue = openObl.filter((o) => o.is_past_due).length;
  const attention = (obls.data?.items ?? []).filter((o) => o.attention_required).length;
  const dueSoonest = [...openObl]
    .filter((o) => o.due_at)
    .sort((a, b) => (a.due_at as string).localeCompare(b.due_at as string))
    .slice(0, 5);
  const approvals = openObl.filter((o) => o.obligation_type === "APPROVAL_PENDING");
  const soonestApproval = [...approvals]
    .filter((o) => o.due_at)
    .sort((a, b) => (a.due_at as string).localeCompare(b.due_at as string))[0];

  const setup =
    jobs.data !== null &&
    (all.length === 0 ||
      all.some((j) => {
        const s = sectionOf(j);
        return !!s && !SECTION_IDS.includes(s);
      }));

  const sel = all.find((j) => j.job_id === (selected ?? top[0]?.job_id)) ?? null;

  const solver = lastOptimize
    ? {
        status: lastOptimize.solver_status,
        ms: Math.round(lastOptimize.solve_time_seconds * 1000),
        run: lastOptimize.optimization_run_id,
        counts: `${lastOptimize.counts.scheduled}/${lastOptimize.counts.considered} scheduled`,
        when: lastOptimize.generated_at,
        windows: lastOptimize.possession_window_count,
        derivation: lastOptimize.possession_derivation,
      }
    : derivedRun
      ? {
          status: derivedRun.solver_status,
          ms:
            derivedRun.solve_time_seconds != null
              ? Math.round(derivedRun.solve_time_seconds * 1000)
              : null,
          run: derivedRun.run_id,
          counts: null as string | null,
          when: derivedRun.completed_at,
          windows: null as number | null,
          derivation: null as string | null,
        }
      : null;

  return (
    <div className="relative flex flex-col w-full text-on-surface pt-space-md pb-space-2xl">
      {setup && (
        <div role="alert" className="mb-4 rounded-DEFAULT border border-secondary/60 bg-secondary-container/20 px-space-md py-space-sm font-body-md text-body-md text-secondary">
          Demo database not seeded, or the backend was not started with
          PASHUPAT_CORRIDOR_ID=CORR-NDLS-AGC. See the demo runbook.
        </div>
      )}

      {/* HERO */}
      <section className="relative w-full rounded-2xl bg-surface-container-lowest overflow-hidden shadow-2xl p-6 lg:p-8 mb-6 border border-outline-variant/30">
        <div aria-hidden className="absolute -top-32 left-1/3 w-[620px] h-[320px] rounded-full bg-primary-container/10 blur-[90px] pointer-events-none" />
        <div aria-hidden className="absolute bottom-0 right-1/4 w-[480px] h-[240px] rounded-full bg-secondary/10 blur-[80px] pointer-events-none" />
        <div className="relative z-10 flex flex-col lg:flex-row lg:items-end justify-between gap-6 pb-6 border-b border-outline-variant/20">
          <div>
            <div className="flex flex-wrap items-center gap-2 mb-3">
              <span className="w-2 h-2 rounded-full bg-primary-container animate-ping" />
              <span className="font-label-caps text-label-caps text-primary tracking-widest">
                COMMAND CENTER // NDLS–AGC SYNTHETIC CORRIDOR
              </span>
              <ProvenanceChip label="ILLUSTRATIVE VISUAL · SYNTHETIC DATA" />
            </div>
            <h1 className="font-headline-lg text-headline-lg lg:text-display-xl text-primary font-light leading-tight tracking-tight">
              Corridor Synchrony
            </h1>
            <p className="font-body-md text-body-md text-on-surface-variant max-w-2xl mt-1">
              Decision support for railway maintenance block planning: every job
              in the pipeline, from field report to committed possession and
              completion, over a synthetic 10–11 Sep 2026 planning horizon.
              The scene below is a stylised view; it is not live train data.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <div className="px-4 py-2 rounded-full bg-surface-container-low backdrop-blur-xl flex items-center gap-3">
              <div className="flex flex-col text-right">
                <span className="font-label-caps text-label-caps text-outline">LAST OPTIMIZATION</span>
                <span className="font-label-mono text-label-mono text-primary font-bold">
                  {solver
                    ? `CP-SAT ${solver.status}${solver.ms != null ? ` (${solver.ms} ms)` : ""}`
                    : "NO RUN YET"}
                </span>
              </div>
              <div className="w-8 h-8 rounded-full bg-primary-container/10 flex items-center justify-center text-primary-container">
                <span className="material-symbols-outlined text-lg" style={{ fontVariationSettings: "'FILL' 1" }}>insights</span>
              </div>
            </div>
            <Link
              href="/planning"
              className="px-5 py-2.5 rounded-full bg-primary text-on-primary font-label-mono text-label-mono font-medium hover:bg-primary-fixed transition-all shadow-[0_0_20px_rgba(0,219,233,0.3)] flex items-center gap-2"
            >
              OPEN PLANNING
              <span className="material-symbols-outlined text-sm">arrow_forward</span>
            </Link>
          </div>
        </div>

        {/* spatial canvas */}
        <div className="relative w-full h-[520px] lg:h-[560px] mt-6 rounded-xl bg-[#090d16] overflow-hidden border border-[#00f0ff]/40 shadow-[0_0_35px_rgba(0,219,233,0.12),inset_0_0_30px_rgba(0,219,233,0.05)] flex flex-col p-4">
          <div className="relative z-30 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3 bg-[#0c121f]/90 backdrop-blur-xl px-3.5 py-1.5 rounded-lg border border-[#00dbe9]/40">
              <span className="font-label-mono text-label-mono text-primary-fixed font-bold tracking-wider">
                NDLS ⇄ AGC · KM 0–195
              </span>
              <span className="font-label-caps text-label-caps text-secondary hidden md:inline">
                SYNTHETIC TOPOLOGY · 2 TRACKS · {active.length} ACTIVE JOB{active.length === 1 ? "" : "S"}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <div role="radiogroup" aria-label="Scene layout" className="flex items-center bg-surface-container-lowest/90 backdrop-blur-xl p-0.5 rounded-lg border border-outline-variant/30">
                {(
                  [
                    ["SPATIAL", "view_in_ar", "Spatial"],
                    ["LINEAR", "schema", "Linear chainage"],
                  ] as const
                ).map(([k, icon, label]) => (
                  <button
                    key={k}
                    type="button"
                    role="radio"
                    aria-checked={mode === k}
                    onClick={() => setMode(k)}
                    className={`px-2.5 py-1 rounded font-label-mono text-xs flex items-center gap-1.5 transition-all ${mode === k ? "text-primary-fixed bg-surface-container-high shadow-sm" : "text-on-surface-variant hover:text-on-surface"}`}
                  >
                    <span className="material-symbols-outlined text-sm">{icon}</span>
                    {label}
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-1 bg-surface-container-lowest/90 backdrop-blur-xl px-2 py-1 rounded-lg border border-outline-variant/30 font-label-mono text-xs">
                <button type="button" aria-pressed={showJobs} onClick={() => setShowJobs((v) => !v)} className={`px-2 py-0.5 rounded ${showJobs ? "text-primary" : "text-outline"} hover:bg-surface-container-high`}>
                  Jobs
                </button>
                <span>·</span>
                <button type="button" aria-pressed={showStations} onClick={() => setShowStations((v) => !v)} className={`px-2 py-0.5 rounded ${showStations ? "text-primary" : "text-outline"} hover:bg-surface-container-high`}>
                  Stations
                </button>
              </div>
            </div>
          </div>

          {/* selected-job telebox (real data) */}
          {sel && (
            <div className="absolute top-16 right-4 z-20 w-72 p-3.5 rounded-xl bg-[#0d1424]/90 backdrop-blur-2xl border border-[#00f0ff]/40 flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <span className="font-label-caps text-label-caps text-primary-fixed tracking-wider font-bold">
                  {selected ? "SELECTED JOB" : "TOP-PRIORITY JOB"}
                </span>
                <StatusChip status={sel.status} />
              </div>
              <div className="font-label-mono text-label-mono text-primary">{sel.job_id}</div>
              <div className="font-body-sm text-body-sm text-on-surface">{JOB_TYPE_LABEL[sel.work_type] ?? sel.work_type}</div>
              <div className="grid grid-cols-2 gap-2 text-xs font-label-mono">
                <div className="bg-surface-container-high/50 p-2 rounded border border-outline-variant/20">
                  <div className="text-outline text-[10px] tracking-wider">PRIORITY</div>
                  <div className="text-primary font-bold text-sm">{sel.priority_score.toFixed(3)}</div>
                </div>
                <div className="bg-surface-container-high/50 p-2 rounded border border-outline-variant/20">
                  <div className="text-outline text-[10px] tracking-wider">RISK</div>
                  <div className="text-secondary font-bold text-sm">{sel.risk_score.toFixed(3)}</div>
                </div>
              </div>
              <div className="flex items-center justify-between font-label-mono text-xs text-outline border-t border-outline-variant/20 pt-1">
                <span>{sectionOf(sel) ?? "—"} · {sel.track_id}</span>
                <span className="text-secondary font-bold">
                  {sel.schedule_start_minute != null && sel.schedule_end_minute != null
                    ? planWindow(sel.schedule_start_minute, sel.schedule_end_minute)
                    : "no window yet"}
                </span>
              </div>
              <Link href={`/jobs/${sel.job_id}`} className="font-label-mono text-label-mono text-primary underline">
                Open job →
              </Link>
            </div>
          )}

          <div className="relative w-full flex-1 min-h-0 my-1">
            <CorridorScene
              jobs={all}
              mode={mode}
              showJobs={showJobs}
              showStations={showStations}
              selectedId={selected ?? top[0]?.job_id ?? null}
              onSelect={setSelected}
            />
          </div>

          <div className="relative z-30 flex flex-wrap items-center gap-4 font-label-mono text-[11px] text-on-surface-variant">
            {STATUS_ORDER.map((s) => (
              <span key={s} className="flex items-center gap-1.5">
                <span className="w-2.5 h-2.5 rounded-full" style={{ background: { reported: "#ffb688", scheduled: "#7df4ff", notified: "#00f0ff", in_progress: "#d4bbff", completed: "#5b6b73" }[s] }} />
                {STATUS_LABEL[s]}
              </span>
            ))}
            <span className="ml-auto text-outline">Markers sit at each job&apos;s stored chainage. Decorative rings and pulses are not data.</span>
          </div>
        </div>
      </section>

      {jobs.error && <div className="mb-4"><ErrorBox error={jobs.error} title="COULD NOT LOAD JOBS" /></div>}

      {/* KPI STRIP */}
      <section className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-3 mb-6" aria-label="Pipeline summary">
        <Kpi label="ACTIVE JOBS" value={active.length} sub={`R ${byStatus.reported} · P ${byStatus.scheduled} · A ${byStatus.notified} · X ${byStatus.in_progress}`} href="/jobs" />
        <Kpi label="CRITICAL (REPORTED)" value={critical.length} sub="CRITICAL, not completed" tone={critical.length ? "text-error" : undefined} href="/jobs" />
        <Kpi label="AWAITING AUTHORITY" value={byStatus.scheduled} sub={soonestApproval ? `soonest due ${recorded(soonestApproval.due_at)}` : "no proposals pending"} tone="text-secondary" href="/review" />
        <Kpi label="COMMITTED / IN PROGRESS" value={byStatus.notified + byStatus.in_progress} sub={`${byStatus.notified} approved · ${byStatus.in_progress} executing`} href="/execution" />
        <Kpi label="COMPLETED" value={byStatus.completed} sub="terminal state" href="/audit" />
        <Kpi label="OPEN OBLIGATIONS" value={openObl.length} sub={`past due ${pastDue} · attention ${attention} · ASSUMED SLA`} tone={pastDue || attention ? "text-error" : undefined} href="/obligations" />
        <Kpi label="LAST OPTIMIZATION" value={solver ? solver.status : "—"} small sub={solver ? `${solver.counts ?? "run " + shortId(solver.run)}${solver.windows != null ? ` · ${solver.windows} windows` : ""}` : "No optimization run yet"} href="/planning" />
      </section>

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6 items-start">
        <div className={`xl:col-span-8 ${CARD} flex flex-col gap-space-md`}>
          <div className="flex items-center justify-between">
            <h2 className="font-headline-sm text-headline-sm text-on-surface">Critical &amp; pending work</h2>
            <span className="font-label-caps text-label-caps text-outline">TOP 5 BY PRIORITY</span>
          </div>
          {jobs.loading && !jobs.data ? (
            <Skeleton rows={4} />
          ) : top.length === 0 ? (
            <Empty title="No active jobs">
              <Link href="/inspection" className="text-primary underline">Report a defect</Link> to create one.
            </Empty>
          ) : (
            <div className="flex flex-col">
              {top.map((j) => (
                <button
                  key={j.job_id}
                  type="button"
                  onClick={() => setSelected(j.job_id)}
                  className="grid grid-cols-12 gap-2 items-center text-left px-space-sm py-2.5 border-b border-outline-variant/10 hover:bg-surface-container transition-all"
                >
                  <span className="col-span-4 font-label-mono text-label-mono font-bold text-primary">{j.job_id}</span>
                  <span className="col-span-3 font-body-sm text-body-sm text-on-surface truncate">{JOB_TYPE_LABEL[j.work_type] ?? j.work_type}</span>
                  <span className="col-span-2"><SeverityChip severity={severityOf(j)} /></span>
                  <span className="col-span-2 font-label-mono text-[11px] text-on-surface-variant">P <Score value={j.priority_score} /> {scoreBand(j.priority_score)[0]}</span>
                  <span className="col-span-1 justify-self-end"><StatusChip status={j.status} /></span>
                </button>
              ))}
              <Link href="/jobs" className="self-start mt-2 font-label-mono text-label-mono text-primary underline">
                All maintenance jobs →
              </Link>
            </div>
          )}
        </div>

        <div className="xl:col-span-4 flex flex-col gap-6">
          <div className={`${CARD} flex flex-col gap-space-sm`}>
            <h2 className="font-headline-sm text-headline-sm text-on-surface">Obligations due soonest</h2>
            {dueSoonest.length === 0 ? (
              <p className="font-body-sm text-body-sm text-outline">No open obligations. Obligations start when a proposal exists.</p>
            ) : (
              <ul className="flex flex-col gap-1">
                {dueSoonest.map((o) => (
                  <li key={o.job_id} className="flex flex-col border-b border-outline-variant/10 pb-1">
                    <div className="flex items-center justify-between gap-2">
                      <Link href={`/jobs/${o.job_id}`} className="font-label-mono text-label-mono text-primary hover:underline">{o.job_id}</Link>
                      <Chip tone={o.is_past_due ? "red" : "cyan"}>{o.state}</Chip>
                    </div>
                    <span className="font-label-mono text-[11px] text-outline">
                      {o.obligation_type} · {o.owed_role} · due {recorded(o.due_at)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            <span className="font-label-mono text-[10px] text-outline">
              ASSUMED demo SLA ({obls.data?.policy_version ?? "—"}); not Indian Railways policy. <TimeTag kind="RECORDED" />
            </span>
          </div>

          <div className={`${CARD} flex flex-col gap-space-sm border border-tertiary/30`}>
            <div className="flex items-center justify-between">
              <h2 className="font-headline-sm text-headline-sm text-on-surface">Disruption simulation</h2>
              <span className="font-label-caps text-label-caps px-1.5 py-0.5 rounded bg-on-tertiary text-tertiary font-bold">SIMULATION</span>
            </div>
            <p className="font-body-sm text-body-sm text-on-surface-variant">
              A stateless what-if on a separate synthetic fixture (Corridor A). It is not connected to the maintenance jobs above and nothing is saved.
            </p>
            <Link href="/simulation" className={BTN_GHOST}>Open simulation</Link>
          </div>

          {solver && (
            <div className={`${CARD}`}>
              <div className="font-label-caps text-label-caps text-outline mb-1">LAST OPTIMIZATION RUN</div>
              <Kv k="STATUS">{solver.status}</Kv>
              <Kv k="RUN">{shortId(solver.run, 12)}</Kv>
              {solver.derivation && <Kv k="WINDOWS">{solver.derivation} ({solver.windows})</Kv>}
              <Kv k="RECORDED">{recorded(solver.when)}</Kv>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Kpi({
  label,
  value,
  sub,
  tone,
  href,
  small,
}: {
  label: string;
  value: number | string;
  sub: string;
  tone?: string;
  href: string;
  small?: boolean;
}) {
  return (
    <Link
      href={href}
      className="p-4 rounded-DEFAULT bg-surface-container/60 backdrop-blur-md border border-outline-variant/20 shadow-md hover:bg-surface-container-high/70 transition-all flex flex-col gap-1"
    >
      <span className="font-label-caps text-label-caps text-outline">{label}</span>
      <span className={`${small ? "font-headline-sm text-headline-sm" : "font-headline-md text-headline-md"} tabular-nums ${tone ?? "text-primary"}`}>{value}</span>
      <span className="font-label-mono text-[11px] text-on-surface-variant break-words">{sub}</span>
    </Link>
  );
}
