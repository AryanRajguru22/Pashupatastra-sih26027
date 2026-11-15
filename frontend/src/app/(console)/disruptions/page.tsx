"use client";

/**
 * Disruption & Recovery (persisted, on the real job queue).
 *
 * DISRUPT: an engineer declares a SIMULATED disruption (a track or section
 * span is unusable). The server records it append-only and reports which
 * jobs it hits. RECOVER: a real CP-SAT re-plan around every active
 * disruption. Approved work is never moved silently: recovery is refused
 * (RECOVERY_BLOCKED) until the authority releases the block, and the
 * before/after comparison is computed by the server from stored placements.
 *
 * Every disruption here is a declared planning input. Nothing on this page
 * is an observed railway event.
 */

import Link from "next/link";
import { useState } from "react";
import {
  ApiError,
  api,
  type AffectedJob,
  type Disruption,
  type DisruptionKind,
  type DisruptionRecovery,
} from "@/lib/api";
import { SECTIONS, TRACKS } from "@/lib/fieldLocation";
import {
  HORIZON_START_ISO,
  planWindow,
  recorded,
  shortId,
} from "@/lib/time";
import { useSession } from "@/lib/session";
import { useResource } from "@/lib/useResource";
import {
  BTN_GHOST,
  BTN_PRIMARY,
  BTN_WARN,
  CARD,
  Chip,
  DemoModeNote,
  Empty,
  ErrorBox,
  INPUT,
  LABEL,
  Modal,
  Page,
  PageBanner,
  Skeleton,
  Spinner,
  useToast,
} from "@/components/ui";

const KIND_LABEL: Record<DisruptionKind, string> = {
  TRACK_UNAVAILABLE: "Track unavailable",
  POSSESSION_CURTAILMENT: "Possession curtailment",
};

const IMPACT_LABEL: Record<AffectedJob["impact"], string> = {
  PROPOSAL_REPLAN: "Proposal will be re-planned",
  RELEASE_REQUIRED: "Authority must release",
  NOT_COMPLETED_REPORT_REQUIRED: "Crew must report not-completed",
};

const ERRORS: Record<string, string> = {
  RECOVERY_BLOCKED:
    "Recovery was refused before any solve: approved work overlaps a disruption. Release each blocking block (Authority Review) first.",
  ALL_POSSESSION_REMOVED:
    "The active disruptions remove every possession window. Clear one to plan again.",
  DISRUPTION_STATE_CONFLICT: "That disruption is already cleared.",
  NO_ELIGIBLE_JOBS: "Nothing to re-plan: there are no active jobs.",
};

/** Minutes on the plan horizon for an API ISO timestamp. */
function toPlanMinute(iso: string): number {
  return Math.round((Date.parse(iso) - Date.parse(HORIZON_START_ISO)) / 60000);
}

function spanText(d: Disruption): string {
  return planWindow(toPlanMinute(d.starts_at), toPlanMinute(d.ends_at));
}

function place(p: { start_minute: number; end_minute: number }): string {
  return planWindow(p.start_minute, p.end_minute);
}

export default function DisruptionsPage() {
  const { persona, online, bump } = useSession();
  const toast = useToast();
  const isEngineer = persona.role === "ENGINEER";

  const list = useResource(() => api.listDisruptions(persona), [persona.id]);
  const [selected, setSelected] = useState<string | null>(null);
  const items = list.data?.items ?? [];
  const current = items.find((d) => d.disruption_id === selected) ?? items[0] ?? null;
  const detail = useResource(
    () => api.getDisruption(persona, current!.disruption_id),
    [persona.id, current?.disruption_id],
    !!current,
  );

  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [recovery, setRecovery] = useState<DisruptionRecovery | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [clearReason, setClearReason] = useState("");

  const [form, setForm] = useState({
    kind: "POSSESSION_CURTAILMENT" as DisruptionKind,
    track: TRACKS[0] as string,
    section: SECTIONS[0]?.id ?? "",
    start: "2026-09-10T01:00",
    end: "2026-09-10T03:00",
    reason: "",
  });

  const act = async <T,>(fn: () => Promise<T>, ok: (r: T) => void) => {
    setBusy(true);
    setError(null);
    try {
      ok(await fn());
      bump();
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, "UNKNOWN", String(e)));
    } finally {
      setBusy(false);
    }
  };

  const register = () =>
    act(
      () =>
        api.registerDisruption(persona, {
          kind: form.kind,
          track_id: form.track,
          section_id:
            form.kind === "POSSESSION_CURTAILMENT" || form.section ? form.section : null,
          starts_at: `${form.start}:00+05:30`,
          ends_at: `${form.end}:00+05:30`,
          reason: form.reason,
        }),
      (d) => {
        setShowForm(false);
        setSelected(d.disruption_id);
        setRecovery(null);
        toast(
          d.blocking_recovery.length
            ? `Disruption recorded. ${d.blocking_recovery.length} approved block(s) must be released before recovery.`
            : "Disruption recorded.",
          d.blocking_recovery.length ? "warn" : "ok",
        );
      },
    );

  const recover = () =>
    act(
      () => api.recoverDisruption(persona, current!.disruption_id),
      (r) => {
        setRecovery(r);
        toast(
          `Recovery ${r.optimization.solver_status}: ${r.comparison.counts.moved} moved, ${r.comparison.counts.unchanged} unchanged.`,
        );
      },
    );

  const clear = () =>
    act(
      () => api.clearDisruption(persona, current!.disruption_id, clearReason),
      () => {
        setClearing(false);
        setClearReason("");
        setRecovery(null);
        toast("Disruption cleared. Plan with a normal optimization.");
      },
    );

  const d = detail.data ?? current;
  const blocking = d?.affected_jobs.filter((a) => a.impact !== "PROPOSAL_REPLAN") ?? [];
  const canRecover = isEngineer && online !== false && d?.status === "ACTIVE" && !busy;

  return (
    <Page>
      <PageBanner
        eyebrow="DISRUPT // RECOVER · SIMULATED INPUTS"
        title="Disruption & Recovery"
        subtitle="Declare a disruption against the real job queue and re-plan around it with the same CP-SAT model. Approved work is never moved silently: the authority releases it first. A disruption is a declared planning input, never an observed railway event."
        right={
          <button
            type="button"
            className={BTN_PRIMARY}
            disabled={!isEngineer || online === false}
            title={!isEngineer ? "Switch to the Planning engineer persona to declare a disruption." : undefined}
            onClick={() => setShowForm(true)}
          >
            <span className="material-symbols-outlined text-[18px]">bolt</span>
            Declare disruption
          </button>
        }
      />

      {error && (
        <ErrorBox error={error} title={ERRORS[error.code] ? "REFUSED" : undefined} />
      )}
      {error && ERRORS[error.code] && (
        <p className="font-body-sm text-body-sm text-secondary -mt-2">{ERRORS[error.code]}</p>
      )}
      <DemoModeNote />

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-space-lg">
        {/* LIST */}
        <section className={`${CARD} lg:col-span-4 flex flex-col gap-space-sm`}>
          <h2 className={LABEL}>Declared disruptions</h2>
          {list.loading && !list.data ? (
            <Skeleton rows={3} />
          ) : list.error ? (
            <ErrorBox error={list.error} />
          ) : items.length === 0 ? (
            <Empty title="No disruptions declared">
              The plan is running on the undisrupted possession windows.
            </Empty>
          ) : (
            <ul className="flex flex-col gap-2">
              {items.map((it) => {
                const active = it.disruption_id === d?.disruption_id;
                return (
                  <li key={it.disruption_id}>
                    <button
                      type="button"
                      onClick={() => {
                        setSelected(it.disruption_id);
                        setRecovery(null);
                      }}
                      className={`w-full text-left rounded-DEFAULT px-space-md py-space-sm border transition-colors ${active ? "border-primary-container/60 bg-surface-container-high" : "border-outline-variant/30 hover:bg-surface-container-high/60"}`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-label-mono text-label-mono text-on-surface">
                          {it.track_id} · {it.section_id ?? "all sections"}
                        </span>
                        <Chip tone={it.status === "ACTIVE" ? "amber" : "neutral"}>{it.status}</Chip>
                      </div>
                      <div className="font-label-mono text-label-mono text-outline mt-1">
                        {KIND_LABEL[it.kind]} · {spanText(it)}
                      </div>
                      {it.blocking_recovery.length > 0 && (
                        <div className="mt-1">
                          <Chip tone="red">{it.blocking_recovery.length} approved block(s) blocking</Chip>
                        </div>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        {/* DETAIL */}
        <section className={`${CARD} lg:col-span-8 flex flex-col gap-space-md`}>
          {!d ? (
            <Empty title="Select or declare a disruption" />
          ) : (
            <>
              <div className="flex flex-wrap items-start justify-between gap-space-md">
                <div className="flex flex-col gap-1 min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <Chip tone="violet" title="A declared planning input, not an observed railway event">
                      {d.basis}
                    </Chip>
                    <Chip tone={d.status === "ACTIVE" ? "amber" : "neutral"}>{d.status}</Chip>
                    <span className="font-label-mono text-label-mono text-outline">{d.disruption_id}</span>
                  </div>
                  <h2 className="font-headline-sm text-headline-sm text-on-surface">
                    {KIND_LABEL[d.kind]} · {d.track_id} · {d.section_id ?? "every section"}
                  </h2>
                  <p className="font-label-mono text-label-mono text-on-surface-variant">
                    {spanText(d)} <span className="text-outline">(plan time, IST)</span>
                  </p>
                  <p className="font-body-sm text-body-sm text-on-surface-variant">
                    “{d.reason}” — declared by {d.declared_by} ({d.declared_role}), {recorded(d.registered_at)}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    className={BTN_WARN}
                    disabled={!canRecover}
                    title={
                      !isEngineer
                        ? "Recovery is a planning-engineer action."
                        : blocking.length > 0
                          ? "The server will refuse this until the approved blocks listed below are released."
                          : undefined
                    }
                    onClick={recover}
                  >
                    {busy ? <Spinner /> : <span className="material-symbols-outlined text-[18px]">autorenew</span>}
                    Run recovery
                  </button>
                  <button
                    type="button"
                    className={BTN_GHOST}
                    disabled={!isEngineer || d.status !== "ACTIVE" || busy}
                    onClick={() => setClearing(true)}
                  >
                    Clear disruption
                  </button>
                </div>
              </div>

              {/* IMPACT */}
              <div>
                <h3 className={`${LABEL} mb-2`}>Affected jobs</h3>
                {d.status !== "ACTIVE" ? (
                  <p className="font-body-sm text-body-sm text-outline">
                    Cleared {recorded(d.cleared_at)} by {d.cleared_by}: “{d.clear_reason}”. It no longer affects planning.
                  </p>
                ) : d.affected_jobs.length === 0 ? (
                  <p className="font-body-sm text-body-sm text-outline">
                    No placed job overlaps this span. Recovery will still re-plan every open proposal around it.
                  </p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-left font-label-mono text-label-mono">
                      <thead className="text-outline">
                        <tr>
                          <th className="py-1 pr-3">Job</th>
                          <th className="py-1 pr-3">Status</th>
                          <th className="py-1 pr-3">Placement</th>
                          <th className="py-1 pr-3">Impact</th>
                        </tr>
                      </thead>
                      <tbody>
                        {d.affected_jobs.map((a) => (
                          <tr key={a.job_id} className="border-t border-outline-variant/20 align-top">
                            <td className="py-2 pr-3">
                              <Link href={`/jobs/${encodeURIComponent(a.job_id)}`} className="text-primary hover:underline">
                                {a.job_id}
                              </Link>
                            </td>
                            <td className="py-2 pr-3">{a.status}</td>
                            <td className="py-2 pr-3">{place(a)}</td>
                            <td className="py-2 pr-3">
                              <Chip tone={a.impact === "PROPOSAL_REPLAN" ? "cyan" : "red"}>
                                {IMPACT_LABEL[a.impact]}
                              </Chip>
                              <div className="text-outline mt-1 max-w-md whitespace-normal">{a.required_action}</div>
                              {a.impact === "RELEASE_REQUIRED" && (
                                <Link href={`/review?job=${encodeURIComponent(a.job_id)}`} className="text-primary hover:underline">
                                  Open Authority Review →
                                </Link>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              {/* RECOVERY RESULT */}
              {recovery && recovery.disruption.disruption_id === d.disruption_id && (
                <RecoveryResult r={recovery} />
              )}

              {/* AUDIT */}
              <div>
                <h3 className={`${LABEL} mb-2`}>Audit trail (append-only)</h3>
                {detail.data?.events?.length ? (
                  <ol className="flex flex-col gap-1">
                    {detail.data.events.map((e) => (
                      <li key={e.event_id} className="font-label-mono text-label-mono text-on-surface-variant">
                        <span className="text-outline">{recorded(e.occurred_at)}</span>{" "}
                        <Chip tone={e.event_type === "RECOVERY_REFUSED" ? "red" : "neutral"}>{e.event_type}</Chip>{" "}
                        {e.actor_id} ({e.actor_role}, {e.actor_assurance})
                        {typeof e.payload.optimization_run_id === "string" && (
                          <span className="text-outline"> · run {shortId(e.payload.optimization_run_id)} · {String(e.payload.solver_status)}</span>
                        )}
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="font-body-sm text-body-sm text-outline">Loading…</p>
                )}
              </div>
            </>
          )}
        </section>
      </div>

      {/* DECLARE */}
      <Modal open={showForm} title="Declare a simulated disruption" onClose={() => setShowForm(false)} busy={busy}>
        <div className="flex flex-col gap-space-sm">
          <label className="flex flex-col gap-1">
            <span className={LABEL}>Kind</span>
            <select
              className={INPUT}
              value={form.kind}
              onChange={(e) => setForm({ ...form, kind: e.target.value as DisruptionKind })}
            >
              {(Object.keys(KIND_LABEL) as DisruptionKind[]).map((k) => (
                <option key={k} value={k}>{KIND_LABEL[k]}</option>
              ))}
            </select>
          </label>
          <div className="grid grid-cols-2 gap-space-sm">
            <label className="flex flex-col gap-1">
              <span className={LABEL}>Track</span>
              <select className={INPUT} value={form.track} onChange={(e) => setForm({ ...form, track: e.target.value })}>
                {TRACKS.map((t) => <option key={t}>{t}</option>)}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className={LABEL}>Section{form.kind === "TRACK_UNAVAILABLE" ? " (optional)" : ""}</span>
              <select className={INPUT} value={form.section} onChange={(e) => setForm({ ...form, section: e.target.value })}>
                {form.kind === "TRACK_UNAVAILABLE" && <option value="">Every section</option>}
                {SECTIONS.map((s) => <option key={s.id} value={s.id}>{s.id}</option>)}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className={LABEL}>From (IST)</span>
              <input type="datetime-local" className={INPUT} value={form.start} onChange={(e) => setForm({ ...form, start: e.target.value })} />
            </label>
            <label className="flex flex-col gap-1">
              <span className={LABEL}>Until (IST)</span>
              <input type="datetime-local" className={INPUT} value={form.end} onChange={(e) => setForm({ ...form, end: e.target.value })} />
            </label>
          </div>
          <label className="flex flex-col gap-1">
            <span className={LABEL}>Reason (recorded)</span>
            <input className={INPUT} value={form.reason} maxLength={500} placeholder="Simulated: overhead-line fault" onChange={(e) => setForm({ ...form, reason: e.target.value })} />
          </label>
          <p className="font-label-mono text-label-mono text-outline">
            The planning horizon is 10–11 Sep 2026. The span is subtracted from the derived possession windows of this track and section.
          </p>
          <div className="flex justify-end gap-2">
            <button type="button" className={BTN_GHOST} onClick={() => setShowForm(false)} disabled={busy}>Cancel</button>
            <button type="button" className={BTN_PRIMARY} disabled={busy || !form.reason.trim() || !form.start || !form.end} onClick={register}>
              {busy && <Spinner />} Record disruption
            </button>
          </div>
        </div>
      </Modal>

      {/* CLEAR */}
      <Modal open={clearing} title="Clear disruption" onClose={() => setClearing(false)} busy={busy}>
        <div className="flex flex-col gap-space-sm">
          <label className="flex flex-col gap-1">
            <span className={LABEL}>Reason (recorded)</span>
            <input className={INPUT} value={clearReason} maxLength={500} onChange={(e) => setClearReason(e.target.value)} />
          </label>
          <div className="flex justify-end gap-2">
            <button type="button" className={BTN_GHOST} onClick={() => setClearing(false)} disabled={busy}>Cancel</button>
            <button type="button" className={BTN_PRIMARY} disabled={busy || !clearReason.trim()} onClick={clear}>
              {busy && <Spinner />} Clear
            </button>
          </div>
        </div>
      </Modal>
    </Page>
  );
}

function RecoveryResult({ r }: { r: DisruptionRecovery }) {
  const c = r.comparison;
  const o = r.optimization;
  return (
    <div className="rounded-DEFAULT bg-surface-container-lowest p-space-md flex flex-col gap-space-sm">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className={LABEL}>Recovery result</h3>
        <Chip tone={o.solver_status === "OPTIMAL" ? "cyan" : "amber"}>{o.solver_status}</Chip>
        <span className="font-label-mono text-label-mono text-outline">
          run {shortId(o.optimization_run_id)} · {r.active_disruption_ids.length} active disruption(s) applied · {o.possession_window_count} windows after subtraction
        </span>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 font-label-mono text-label-mono">
        <Stat label="Unchanged" n={c.counts.unchanged} />
        <Stat label="Moved" n={c.counts.moved} tone="amber" />
        <Stat label="Newly unscheduled" n={c.counts.newly_unscheduled} tone="red" />
        <Stat label="Newly scheduled" n={c.counts.newly_scheduled} tone="cyan" />
      </div>
      {c.moved.length > 0 && (
        <table className="w-full text-left font-label-mono text-label-mono">
          <thead className="text-outline">
            <tr><th className="py-1 pr-3">Job</th><th className="py-1 pr-3">Before</th><th className="py-1 pr-3">After</th></tr>
          </thead>
          <tbody>
            {c.moved.map((m) => (
              <tr key={m.job_id} className="border-t border-outline-variant/20">
                <td className="py-1 pr-3">{m.job_id}</td>
                <td className="py-1 pr-3">{place(m.before)} <span className="text-outline">({m.before.status})</span></td>
                <td className="py-1 pr-3">{place(m.after)} <span className="text-outline">({m.after.status})</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {c.newly_unscheduled.map((u) => (
        <p key={u.job_id} className="font-label-mono text-label-mono text-error">
          {u.job_id} could not be placed: {u.reason ?? "no reason recorded"}
        </p>
      ))}
      <p className="font-label-mono text-label-mono text-outline">
        New proposals still need authority approval. Review them under Authority Review.
      </p>
    </div>
  );
}

function Stat({ label, n, tone }: { label: string; n: number; tone?: "amber" | "red" | "cyan" }) {
  const color = tone === "amber" ? "text-secondary" : tone === "red" ? "text-error" : tone === "cyan" ? "text-primary-fixed" : "text-on-surface";
  return (
    <div className="rounded-DEFAULT bg-surface-container-low px-space-md py-space-sm">
      <div className="text-outline">{label}</div>
      <div className={`font-headline-sm text-headline-sm ${color}`}>{n}</div>
    </div>
  );
}
