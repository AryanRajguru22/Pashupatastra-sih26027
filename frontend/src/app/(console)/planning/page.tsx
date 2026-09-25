"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import {
  ApiError,
  CORRIDOR_ID,
  JOB_TYPE_LABEL,
  api,
  listAllJobs,
  sectionOf,
  type Job,
  type OptimizationResponse,
} from "@/lib/api";
import { SECTIONS, TRACKS } from "@/lib/fieldLocation";
import { REOPT_LABEL, classify, type ReoptRow } from "@/lib/reopt";
import {
  HORIZON_MINUTES,
  duration,
  planTime,
  planWindow,
  recorded,
  shortId,
} from "@/lib/time";
import { useSession } from "@/lib/session";
import { useLastRun } from "@/lib/useLastRun";
import { useResource } from "@/lib/useResource";
import { ProvenanceStrip } from "@/components/ProposalCard";
import {
  BTN_GHOST,
  CARD,
  Chip,
  Empty,
  ErrorBox,
  Kv,
  Modal,
  Page,
  ProvenanceChip,
  Skeleton,
  Spinner,
  TimeTag,
  useToast,
} from "@/components/ui";

const WORK_ABBR: Record<string, string> = {
  TRACK_RENEWAL: "RENEW",
  BALLAST_TAMPING: "BALLAST",
  OHE_MAINTENANCE: "OHE",
  SIGNALLING_INTERLOCKING: "SIGNAL",
  ROUTINE_INSPECTION: "INSPECT",
  EMERGENCY_REPAIR: "REPAIR",
};

const PAGE_ERRORS: Record<string, string> = {
  NO_ELIGIBLE_JOBS: "Nothing to optimize: there are no active jobs.",
  POSSESSION_DATA_UNAVAILABLE:
    "Refused to schedule without possession data (fail-closed).",
  TIMETABLE_COVERAGE_GAP:
    "The horizon touches a date the (synthetic) timetable does not cover.",
  COMMITTED_STATE_INCONSISTENT:
    "Integrity refusal. Escalate to an engineer; do not retry.",
  EXECUTION_HISTORY_INCONSISTENT:
    "Integrity refusal. Escalate to an engineer; do not retry.",
  CONCURRENT_MODIFICATION:
    "Data changed during the run. Reload and run again.",
  CORRIDOR_NOT_FOUND:
    "Setup error: the backend serves a different corridor than CORR-NDLS-AGC.",
};

type BlockKind = "committed" | "proposed";

interface Block {
  job: Job;
  start: number;
  end: number;
  kind: BlockKind;
}

function solverText(s: string): string {
  if (s === "OPTIMAL") return "Optimal (solver proved)";
  if (s === "FEASIBLE") return "Feasible (time limit; not proven optimal)";
  return s;
}

export default function PlanningPage() {
  const { persona, online, lastOptimize, previousOptimize, recordOptimize, bump } =
    useSession();
  const toast = useToast();
  const jobs = useResource(() => listAllJobs(), []);
  const [confirm, setConfirm] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [showRun, setShowRun] = useState(false);

  const derivedRun = useLastRun(jobs.data);
  const isEngineer = persona.role === "ENGINEER";
  const all = useMemo(() => jobs.data ?? [], [jobs.data]);
  const active = all.filter((j) => j.status !== "completed");

  const blocks = useMemo<Block[]>(() => {
    const out: Block[] = [];
    for (const j of all) {
      if (j.schedule_start_minute == null || j.schedule_end_minute == null) continue;
      out.push({
        job: j,
        start: j.schedule_start_minute,
        end: j.schedule_end_minute,
        kind: j.status === "scheduled" ? "proposed" : "committed",
      });
    }
    return out;
  }, [all]);

  const postponed = all.filter((j) => {
    const nb = j.block_candidate.earliest_start_minute;
    return (
      j.status === "reported" &&
      typeof nb === "number" &&
      nb > 0 &&
      !!j.last_refusal_reason
    );
  });

  const run = async () => {
    setRunning(true);
    setError(null);
    try {
      const res = await api.optimize(persona);
      recordOptimize(res);
      bump();
      const bad = res.counts.unscheduled;
      toast(
        `Optimization ${res.solver_status}: ${res.counts.scheduled} of ${res.counts.considered} scheduled${bad ? `, ${bad} unscheduled` : ""}.`,
        bad ? "warn" : "ok",
      );
      setConfirm(false);
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, "UNKNOWN", String(e)));
      setConfirm(false);
    } finally {
      setRunning(false);
    }
  };

  const r = lastOptimize;
  const runRecord = useResource(
    () => api.run(r!.optimization_run_id),
    [r?.optimization_run_id],
    showRun && !!r,
  );

  const pageMsg = error ? PAGE_ERRORS[error.code] : null;

  return (
    <Page>
      {/* HERO: orbital engine panel */}
      <section className="relative w-full overflow-hidden rounded-lg bg-surface-container-lowest p-6 shadow-2xl">
        <div aria-hidden className="absolute -top-32 -left-20 w-96 h-96 rounded-full bg-[radial-gradient(circle,rgba(0,219,233,0.18)_0%,transparent_70%)] blur-2xl pointer-events-none" />
        <div aria-hidden className="absolute top-1/2 right-12 w-[30rem] h-[30rem] rounded-full bg-[radial-gradient(circle,rgba(255,182,136,0.12)_0%,transparent_75%)] blur-3xl pointer-events-none" />
        <svg aria-hidden className="absolute inset-0 w-full h-full pointer-events-none opacity-40 mix-blend-screen" preserveAspectRatio="none" viewBox="0 0 1200 480" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <linearGradient id="cyanGlowLine" x1="0%" x2="100%" y1="0%" y2="0%">
              <stop offset="0%" stopColor="#00dbe9" stopOpacity="0" />
              <stop offset="35%" stopColor="#00dbe9" stopOpacity="0.8" />
              <stop offset="70%" stopColor="#ffb688" stopOpacity="0.9" />
              <stop offset="100%" stopColor="#00dbe9" stopOpacity="0" />
            </linearGradient>
            <radialGradient cx="50%" cy="50%" id="ringCore" r="50%">
              <stop offset="0%" stopColor="#00f0ff" stopOpacity="0.3" />
              <stop offset="100%" stopColor="transparent" stopOpacity="0" />
            </radialGradient>
          </defs>
          <g className="orbit-slow"><circle cx="680" cy="240" fill="none" opacity="0.4" r="190" stroke="#849495" strokeDasharray="3 6" strokeWidth="0.75" /></g>
          <g className="orbit-slower"><circle cx="680" cy="240" fill="none" opacity="0.6" r="280" stroke="#3b494b" strokeDasharray="1 9" strokeWidth="0.9" /></g>
          <circle cx="680" cy="240" fill="url(#ringCore)" opacity="0.5" r="110" stroke="#00dbe9" strokeWidth="0.8" />
          <path d="M 0 340 Q 320 220, 680 240 T 1200 130" fill="none" stroke="url(#cyanGlowLine)" strokeWidth="2" />
          <path className="animate-laser-fast" d="M 0 340 Q 320 220, 680 240 T 1200 130" fill="none" stroke="#7df4ff" strokeWidth="2.5" />
          <path d="M 0 380 Q 420 390, 680 240 T 1200 90" fill="none" opacity="0.6" stroke="#ffb688" strokeDasharray="6 4" strokeWidth="1.2" />
          <path d="M 180 0 L 180 480 M 680 0 L 680 480 M 960 0 L 960 480" opacity="0.5" stroke="#3b494b" strokeDasharray="2 8" strokeWidth="0.4" />
          <path d="M 670 240 L 690 240 M 680 230 L 680 250" stroke="#00f0ff" strokeWidth="1" />
        </svg>

        <div className="relative z-10 flex flex-col gap-6">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex flex-wrap items-center gap-3">
              <span className="font-label-caps text-label-caps px-3 py-1 rounded-full bg-surface-container-high text-primary-fixed border border-outline-variant/40 tracking-widest">
                PLANNING // CP-SAT POSSESSION SCHEDULING
              </span>
              <div className="flex items-center gap-2 px-3 py-1 rounded-full bg-surface-container-low border border-outline-variant/30">
                <span className={`w-2 h-2 rounded-full ${r ? "bg-primary-container animate-pulse" : "bg-outline"}`} />
                <span className="font-label-mono text-label-mono text-on-surface-variant">
                  LAST RUN:{" "}
                  {r ? (
                    <span className={r.solver_status === "OPTIMAL" ? "text-primary-fixed font-bold" : "text-secondary font-bold"}>
                      {r.solver_status} ({Math.round(r.solve_time_seconds * 1000)} ms solve)
                    </span>
                  ) : derivedRun ? (
                    <span className="text-primary-fixed font-bold">
                      {derivedRun.solver_status}
                      {derivedRun.solve_time_seconds != null ? ` (${Math.round(derivedRun.solve_time_seconds * 1000)} ms solve)` : ""}{" "}
                      <span className="text-outline font-normal">· run {shortId(derivedRun.run_id)} · {recorded(derivedRun.completed_at)}</span>
                    </span>
                  ) : (
                    <span className="text-outline">no optimization run yet</span>
                  )}
                </span>
              </div>
            </div>
            <div className="flex items-center gap-3 font-label-mono text-label-mono">
              <span className="text-outline">
                ACTING AS <span className="text-on-surface">{persona.id}</span>
              </span>
              <span className="font-label-caps text-label-caps text-secondary font-semibold">
                PLANNING HORIZON: 10–11 SEP 2026 (SYNTHETIC)
              </span>
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-end">
            <div className="lg:col-span-8 flex flex-col gap-2">
              <span className="font-label-mono text-label-mono text-primary tracking-widest uppercase">
                {CORRIDOR_ID} · SYSTEM DERIVED from SYNTHETIC inputs
              </span>
              <h1 className="font-headline-lg text-headline-lg text-on-surface tracking-tight leading-none">
                Possession Scheduling Engine
              </h1>
              <p className="font-body-lg text-body-lg text-on-surface-variant max-w-2xl mt-2">
                Constraint programming (OR-Tools CP-SAT) places each active job
                inside a possession window derived from train-free gaps in the
                synthetic timetable. Approved blocks stay pinned; every open
                proposal is re-proposed on each run.
              </p>
            </div>
            <div className="lg:col-span-4 flex flex-col sm:flex-row lg:flex-col gap-3 justify-end items-stretch lg:items-end">
              <button
                type="button"
                disabled={!isEngineer || online === false || running || active.length === 0}
                onClick={() => setConfirm(true)}
                title={
                  !isEngineer
                    ? "Switch to the Engineer persona (demo role gating; not server-enforced)."
                    : active.length === 0
                      ? "No active jobs to optimize"
                      : undefined
                }
                className="group relative px-6 py-3.5 rounded-full bg-primary-container text-on-primary-container font-headline-sm text-headline-sm flex items-center justify-center gap-3 shadow-[0_0_28px_rgba(0,240,255,0.35)] hover:brightness-110 transition-all disabled:opacity-40 disabled:cursor-not-allowed disabled:shadow-none"
              >
                {running ? <Spinner /> : <span className="material-symbols-outlined text-xl transition-transform group-hover:rotate-180">play_arrow</span>}
                <span className="font-label-caps text-label-caps tracking-widest font-bold">RUN OPTIMIZATION</span>
              </button>
              <span className="font-label-mono text-label-mono text-outline text-right text-xs">
                {isEngineer
                  ? `${active.length} active job(s) will be considered`
                  : "Engineer action — switch persona to run"}
              </span>
            </div>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-2">
            <Bento label="POSSESSION WINDOWS" icon="calendar_month" value={r ? String(r.possession_window_count) : "—"} sub={r ? r.possession_derivation : "run to derive"} tone="text-primary" />
            <Bento label="CONSIDERED JOBS" icon="tune" value={r ? String(r.counts.considered) : String(active.length)} sub={r ? "in the last run" : "active now"} tone="text-secondary" />
            <Bento label="PINNED · COMMITTED" icon="lock" value={r ? String(r.counts.committed) : String(all.filter((j) => j.status === "notified" || j.status === "in_progress").length)} sub={r ? "fed to the solver" : "approved / in progress"} tone="text-primary-fixed" />
            <Bento label="UNSCHEDULED" icon={r && r.counts.unscheduled ? "warning" : "check_circle"} value={r ? String(r.counts.unscheduled) : "—"} sub={r ? (r.counts.unscheduled ? "see reasons below" : "all considered jobs placed") : "run to see"} tone={r && r.counts.unscheduled ? "text-secondary" : "text-on-surface"} />
          </div>
        </div>
      </section>

      {error && (
        <div className="flex flex-col gap-2">
          <ErrorBox error={error} title={pageMsg ? "OPTIMIZATION NOT PERFORMED" : "OPTIMIZATION REFUSED"} />
          {pageMsg && <p className="font-body-sm text-body-sm text-on-surface-variant">{pageMsg}</p>}
          {error.code === "NO_ELIGIBLE_JOBS" && (
            <Link href="/inspection" className="font-label-mono text-label-mono text-primary underline">
              Report a defect in Field Inspection
            </Link>
          )}
        </div>
      )}

      {r && r.counts.unscheduled > 0 && (
        <div className="rounded-DEFAULT bg-secondary-container/25 px-space-md py-space-sm font-body-md text-body-md text-secondary">
          {r.counts.unscheduled} of {r.counts.considered} jobs could not be placed. They are listed first below. This is a partial result, not a full success.
        </div>
      )}
      {r && (r.solver_status === "INFEASIBLE" || r.solver_status === "NO_SOLUTION") && (
        <ErrorBox error={`Solver status ${r.solver_status}. ${r.infeasibility_reasons.join(" ")}`} title="NO PLACEMENT" />
      )}

      {/* HORIZON MATRIX */}
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-3 px-1">
          <div className="flex items-center gap-3">
            <span className="w-2.5 h-2.5 rounded-full bg-primary-container shadow-[0_0_8px_#00f0ff]" />
            <h2 className="font-headline-sm text-headline-sm text-on-surface tracking-wide">
              Corridor horizon matrix (NDLS ↔ AGC)
            </h2>
            <span className="font-label-caps text-label-caps text-outline px-2 py-0.5 rounded bg-surface-container-high">
              48-HOUR PLAN GRID
            </span>
            <TimeTag kind="PLAN" />
          </div>
          <div className="flex items-center gap-4 text-xs font-label-mono">
            <Legend cls="bg-primary-container border-primary-fixed" label="Committed (pinned)" />
            <Legend cls="bg-surface-container-high border-dashed border-primary-fixed" label="Proposed (not committed)" />
            <Legend cls="bg-secondary-container/80 border-secondary" label="Postponed (see below)" />
          </div>
        </div>

        <div className="w-full bg-surface-container-lowest/90 rounded-lg p-5 border border-outline-variant/30 shadow-xl overflow-x-auto">
          <div className="min-w-[980px] flex flex-col gap-1">
            <div className="grid grid-cols-12 pb-2 border-b border-outline-variant/40">
              <div className="col-span-2 font-label-caps text-label-caps text-outline tracking-wider self-center">SECTION // TRACK</div>
              <div className="col-span-5 flex justify-between text-primary-fixed font-label-mono text-label-mono border-r border-dashed border-primary-container/40 pr-2">
                <span>DAY 1 · 10 SEP 00:00</span><span>12:00</span><span>23:59</span>
              </div>
              <div className="col-span-5 flex justify-between text-secondary font-label-mono text-label-mono pl-2">
                <span className="text-primary-container font-bold">DAY 2 · 11 SEP 00:00</span><span>12:00</span><span>23:59 IST</span>
              </div>
            </div>
            {SECTIONS.flatMap((s) =>
              TRACKS.map((t) => {
                const lane = blocks.filter(
                  (b) => sectionOf(b.job) === s.id && b.job.track_id === t,
                );
                return (
                  <div key={`${s.id}-${t}`} className="grid grid-cols-12 items-center py-1 hover:bg-surface-container-high/30 rounded px-1 transition-colors">
                    <div className="col-span-2 flex flex-col">
                      <span className="font-headline-sm text-body-md text-on-surface font-semibold">{s.start} – {s.end}</span>
                      <span className="font-label-mono text-xs text-outline">{t} · KM {s.kmStart}.0–{s.kmEnd}.0</span>
                    </div>
                    <div className="col-span-10 relative h-8 bg-surface-container-low/70 rounded border border-outline-variant/20">
                      <div className="absolute left-1/2 top-0 bottom-0 w-px border-r border-dashed border-primary-container/60 z-10" />
                      {lane.map((b) => {
                        const left = (b.start / HORIZON_MINUTES) * 100;
                        const width = Math.max(1.4, ((b.end - b.start) / HORIZON_MINUTES) * 100);
                        const label = `${b.job.job_id} · ${JOB_TYPE_LABEL[b.job.work_type] ?? b.job.work_type} · ${planWindow(b.start, b.end)} · ${b.kind === "committed" ? "committed" : "proposed"}`;
                        return (
                          <Link
                            key={b.job.job_id}
                            href={`/jobs/${b.job.job_id}`}
                            title={label}
                            aria-label={label}
                            className={`absolute top-0.5 h-7 min-w-[74px] rounded flex items-center justify-center overflow-hidden whitespace-nowrap font-label-mono text-[10px] transition-all hover:scale-y-110 hover:z-20 ${b.kind === "committed" ? "bg-primary-container text-on-primary-container border border-primary-fixed shadow-[0_0_12px_rgba(0,240,255,0.35)]" : "bg-surface-container-high text-primary border border-dashed border-primary"}`}
                            style={{ left: `${left}%`, width: `${width}%` }}
                          >
                            {WORK_ABBR[b.job.work_type] ?? "JOB"} {planTime(b.start).slice(-5)}
                          </Link>
                        );
                      })}
                    </div>
                  </div>
                );
              }),
            )}
          </div>
        </div>

        {jobs.loading && !jobs.data && <Skeleton rows={3} />}
        {jobs.error && <ErrorBox error={jobs.error} />}
        {jobs.data && blocks.length === 0 && (
          <Empty title="No placements yet">
            Run an optimization to propose possession blocks. Jobs without a
            window are not drawn.
          </Empty>
        )}

        {postponed.length > 0 && (
          <div className={CARD}>
            <div className="font-label-caps text-label-caps text-secondary mb-2">
              POSTPONED · WAITING FOR A LATER DATE
            </div>
            <ul className="flex flex-col gap-1">
              {postponed.map((j) => (
                <li key={j.job_id} className="font-label-mono text-label-mono flex flex-wrap gap-2 items-center">
                  <Link href={`/jobs/${j.job_id}`} className="text-primary hover:underline">{j.job_id}</Link>
                  <span className="text-on-surface-variant">
                    not before {planTime(j.block_candidate.earliest_start_minute as number)} IST <TimeTag kind="PLAN" />
                  </span>
                  <span className="text-outline">— {j.last_refusal_reason}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* RUN RESULT */}
      {r && <RunResult r={r} jobs={all} onShowRecord={() => setShowRun((v) => !v)} showRecord={showRun} />}
      {showRun && r && (
        <div className={CARD}>
          <div className="font-label-caps text-label-caps text-outline mb-2">RUN AUDIT RECORD (GET /v1/optimization-runs)</div>
          {runRecord.loading && <Skeleton rows={2} />}
          {runRecord.error && <ErrorBox error={runRecord.error} />}
          {runRecord.data && (
            <div className="rounded-DEFAULT bg-surface-container-lowest px-space-md py-1">
              <Kv k="RUN">{runRecord.data.run_id}</Kv>
              <Kv k="ACTOR · TRIGGER">{runRecord.data.actor} · {runRecord.data.trigger}</Kv>
              <Kv k="REQUESTED">{recorded(runRecord.data.requested_at)}</Kv>
              <Kv k="COMPLETED">{recorded(runRecord.data.completed_at)}</Kv>
              <Kv k="SOLVER STATUS">{runRecord.data.solver_status}</Kv>
              <Kv k="LEGACY possession_source">{r.possession_source} (legacy label; see derivation above)</Kv>
            </div>
          )}
        </div>
      )}

      {/* RE-OPTIMIZATION */}
      {r && previousOptimize && (
        <Reopt last={r} prev={previousOptimize} jobs={all} />
      )}
      {r && !previousOptimize && (
        <p className="font-label-mono text-[11px] text-outline">
          The re-optimization comparison appears after a second run in this session.
        </p>
      )}

      <Modal open={confirm} title="Run optimization" onClose={() => !running && setConfirm(false)} busy={running}>
        <p className="font-body-md text-body-md text-on-surface-variant">
          Run optimization over <strong>{active.length}</strong> active job(s) on {CORRIDOR_ID}? Committed blocks stay pinned. Every open (unapproved) proposal will be re-proposed and gets a new run id. Authorities must review again.
        </p>
        <p className="font-label-mono text-[11px] text-outline">
          Acting as {persona.id} ({persona.role}) — declared, not verified. Planning horizon 10 Sep 00:00 – 12 Sep 00:00 IST (synthetic).
        </p>
        <div className="flex justify-end gap-space-sm">
          <button type="button" className={BTN_GHOST} disabled={running} onClick={() => setConfirm(false)}>Cancel</button>
          <button type="button" onClick={run} disabled={running} className="px-5 py-2.5 rounded-full bg-primary-container text-on-primary-container font-label-mono text-label-mono font-bold hover:brightness-110 flex items-center gap-2 disabled:opacity-40">
            {running && <Spinner />} Run optimization
          </button>
        </div>
      </Modal>
    </Page>
  );
}

function Legend({ cls, label }: { cls: string; label: string }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className={`w-3 h-3 rounded-sm border ${cls}`} />
      <span className="text-on-surface-variant">{label}</span>
    </div>
  );
}

function Bento({ label, icon, value, sub, tone }: { label: string; icon: string; value: string; sub: string; tone: string }) {
  return (
    <div className="p-4 rounded bg-surface-container/60 backdrop-blur-md flex flex-col gap-1 border border-outline-variant/20 shadow-md">
      <div className="flex items-center justify-between">
        <span className="font-label-caps text-label-caps text-outline">{label}</span>
        <span className={`material-symbols-outlined text-sm ${tone}`}>{icon}</span>
      </div>
      <span className={`font-headline-md text-headline-md ${tone}`}>{value}</span>
      <span className="font-label-mono text-label-mono text-on-surface-variant break-words">{sub}</span>
    </div>
  );
}

function RunResult({ r, jobs, onShowRecord, showRecord }: { r: OptimizationResponse; jobs: Job[]; onShowRecord: () => void; showRecord: boolean }) {
  const byId = new Map(jobs.map((j) => [j.job_id, j]));
  const nm = (id: string) => JOB_TYPE_LABEL[byId.get(id)?.work_type ?? ""] ?? "";
  const derivation =
    r.possession_derivation === "CANONICAL_TIMETABLE_DERIVED"
      ? "Possession windows derived from train-free gaps in the (synthetic) timetable"
      : r.possession_derivation === "GENERATED_STATIC_SLOTS"
        ? "Fixed generated slots; no train data"
        : r.possession_derivation;
  return (
    <div className={`${CARD} flex flex-col gap-space-md`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-headline-sm text-headline-sm text-primary">Latest optimization result</h2>
        <div className="flex flex-wrap items-center gap-2">
          <Chip tone={r.solver_status === "OPTIMAL" ? "cyan" : r.solver_status === "FEASIBLE" ? "amber" : "red"}>{solverText(r.solver_status)}</Chip>
          <ProvenanceChip />
          <button type="button" className={BTN_GHOST} onClick={onShowRecord}>
            {showRecord ? "Hide run record" : "Run audit record"}
          </button>
        </div>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
        <Stat k="CONSIDERED" v={r.counts.considered} />
        <Stat k="COMMITTED (PINNED)" v={r.counts.committed} />
        <Stat k="SCHEDULED" v={r.counts.scheduled} />
        <Stat k="UNSCHEDULED" v={r.counts.unscheduled} warn={r.counts.unscheduled > 0} />
        <Stat k="SOLVE TIME" v={`${Math.round(r.solve_time_seconds * 1000)} ms`} />
      </div>
      <div className="font-label-mono text-[11px] text-on-surface-variant flex flex-col gap-1">
        <span>Run <span title={r.optimization_run_id}>{shortId(r.optimization_run_id, 12)}</span> · generated {recorded(r.generated_at)} <TimeTag kind="RECORDED" /></span>
        <span>{derivation} · {r.possession_window_count} possession windows over the horizon</span>
        <ProvenanceStrip p={r.provenance} />
      </div>

      {r.uncovered_tracks.length > 0 && (
        <div className="rounded-DEFAULT bg-secondary-container/25 px-space-md py-space-sm font-body-sm text-body-sm text-secondary">
          No possession window covers work on these tracks; the solver refused those blocks: {r.uncovered_tracks.join(", ")}
        </div>
      )}
      {r.infeasibility_reasons.length > 0 && (
        <ul className="list-disc pl-5 font-body-sm text-body-sm text-secondary">
          {r.infeasibility_reasons.map((x) => <li key={x}>{x}</li>)}
        </ul>
      )}

      {r.unscheduled.length > 0 && (
        <div>
          <div className="font-label-caps text-label-caps text-secondary mb-1">UNSCHEDULED (solver reason, verbatim)</div>
          <div className="rounded-DEFAULT bg-surface-container-lowest divide-y divide-outline-variant/15">
            {r.unscheduled.map((u) => (
              <div key={u.job_id} className="grid grid-cols-12 gap-2 px-space-md py-2">
                <Link href={`/jobs/${u.job_id}`} className="col-span-3 font-label-mono text-label-mono text-primary hover:underline">{u.job_id}</Link>
                <span className="col-span-2 font-label-mono text-label-mono text-outline">{u.track_id}</span>
                <span className="col-span-7 font-body-sm text-body-sm text-on-surface-variant">{u.reason}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div>
        <div className="font-label-caps text-label-caps text-outline mb-1">SCHEDULED</div>
        <div className="rounded-DEFAULT bg-surface-container-lowest divide-y divide-outline-variant/15">
          {r.scheduled.length === 0 && <div className="px-space-md py-2 font-body-sm text-body-sm text-outline">Nothing scheduled.</div>}
          {r.scheduled.map((s) => (
            <div key={s.job_id} className="grid grid-cols-12 gap-2 px-space-md py-2 items-center">
              <Link href={`/jobs/${s.job_id}`} className="col-span-3 font-label-mono text-label-mono text-primary hover:underline">{s.job_id}</Link>
              <span className="col-span-2 font-body-sm text-body-sm text-on-surface-variant">{nm(s.job_id)}</span>
              <span className="col-span-1 font-label-mono text-label-mono text-outline">{s.track_id}</span>
              <span className="col-span-4 font-label-mono text-label-mono text-secondary">
                {planWindow(s.start_minute, s.end_minute)} <span className="text-outline">({duration((s.end_minute - s.start_minute) * 60)})</span>
              </span>
              <span className="col-span-2 flex justify-end">
                {s.is_committed ? <Chip tone="cyan">PINNED · APPROVED</Chip> : <Chip tone="neutral">PROPOSED</Chip>}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function Stat({ k, v, warn }: { k: string; v: number | string; warn?: boolean }) {
  return (
    <div className="rounded bg-surface-container-lowest px-space-md py-space-sm">
      <div className="font-label-caps text-label-caps text-outline">{k}</div>
      <div className={`font-headline-sm text-headline-sm tabular-nums ${warn ? "text-secondary" : "text-primary"}`}>{v}</div>
    </div>
  );
}

function Reopt({ last, prev, jobs }: { last: OptimizationResponse; prev: OptimizationResponse; jobs: Job[] }) {
  const ids = useMemo(() => {
    const s = new Set<string>();
    last.scheduled.forEach((x) => s.add(x.job_id));
    last.unscheduled.forEach((x) => s.add(x.job_id));
    return [...s];
  }, [last]);
  const rows = useResource<ReoptRow[]>(
    async () =>
      Promise.all(
        ids.map(async (id) => {
          const h = await api.history(id);
          return classify(id, h.events, last, prev);
        }),
      ),
    [last.optimization_run_id, prev.optimization_run_id],
  );
  const byId = new Map(jobs.map((j) => [j.job_id, j]));
  return (
    <div className={`${CARD} flex flex-col gap-space-md`}>
      <div>
        <h2 className="font-headline-sm text-headline-sm text-primary">Re-optimization comparison</h2>
        <p className="font-label-mono text-label-mono text-on-surface-variant">
          Run {shortId(prev.optimization_run_id, 8)} ({prev.solver_status}) → run {shortId(last.optimization_run_id, 8)} ({last.solver_status}): {last.counts.considered} considered · {last.counts.committed} committed (pinned) · {last.counts.scheduled} scheduled · {last.counts.unscheduled} unscheduled
        </p>
      </div>
      {rows.loading && !rows.data && <Skeleton rows={3} />}
      {rows.error && <ErrorBox error={rows.error} />}
      {rows.data && (
        <div className="rounded-DEFAULT bg-surface-container-lowest divide-y divide-outline-variant/15">
          <div className="hidden md:grid grid-cols-12 gap-2 px-space-md py-2 font-label-caps text-label-caps text-outline">
            <span className="col-span-3">JOB</span>
            <span className="col-span-3">BEFORE (PLAN)</span>
            <span className="col-span-3">AFTER (PLAN)</span>
            <span className="col-span-3">OUTCOME</span>
          </div>
          {rows.data.map((x) => (
            <div key={x.jobId} className="grid grid-cols-12 gap-2 px-space-md py-2 items-start">
              <div className="col-span-12 md:col-span-3 flex flex-col">
                <Link href={`/jobs/${x.jobId}`} className="font-label-mono text-label-mono text-primary hover:underline">{x.jobId}</Link>
                <span className="font-body-sm text-body-sm text-on-surface-variant">{JOB_TYPE_LABEL[byId.get(x.jobId)?.work_type ?? ""] ?? ""}</span>
              </div>
              <div className="col-span-6 md:col-span-3 font-label-mono text-label-mono text-on-surface-variant">
                {x.before ? planWindow(x.before.start, x.before.end) : "—"}
              </div>
              <div className="col-span-6 md:col-span-3 font-label-mono text-label-mono text-secondary">
                {x.after ? planWindow(x.after.start, x.after.end) : "—"}
              </div>
              <div className="col-span-12 md:col-span-3 flex flex-col gap-1">
                <Chip tone={x.kind === "PRESERVED" ? "cyan" : x.kind === "UNSCHEDULED" || x.kind === "CONFLICT" ? "red" : x.kind === "MOVED" ? "amber" : "neutral"}>
                  {x.kind === "PRESERVED" && <span className="material-symbols-outlined text-[12px]">lock</span>}
                  {REOPT_LABEL[x.kind]}
                </Chip>
                {x.kind === "REJECTED_REPROPOSED" && x.samePlacementAsRejected && (
                  <span className="font-label-mono text-[11px] text-secondary">Re-proposed · same placement as rejected</span>
                )}
                {x.kind === "POSTPONED_REPROPOSED" && x.notBeforeMinute != null && (
                  <span className="font-label-mono text-[11px] text-on-surface-variant">
                    not before {planTime(x.notBeforeMinute)} {x.notBeforeSatisfied ? "✓ satisfied" : "✗ not satisfied"}
                  </span>
                )}
                {x.note && <span className="font-body-sm text-[12px] text-outline">{x.note}</span>}
              </div>
            </div>
          ))}
        </div>
      )}
      <p className="font-label-mono text-[11px] text-outline">
        Unapproved proposals are re-proposed on every run and may move. Only approved (committed) blocks are pinned. Each re-proposal restarts the authority&apos;s approval clock.
      </p>
    </div>
  );
}
