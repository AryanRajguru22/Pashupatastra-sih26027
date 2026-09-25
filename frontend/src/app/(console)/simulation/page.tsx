"use client";

/**
 * Disruption Simulation (legacy, stateless).
 *
 * Uses ONLY the legacy POST /optimize and POST /recover on the synthetic
 * Corridor A fixture. Nothing here is persisted, it is not connected to the
 * /v1 maintenance-job lifecycle and it is not NDLS–AGC or a live feed.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import type {
  BlockCandidate,
  DashboardData,
  DisruptionEvent,
} from "@/types/contracts";
import { enrichData, fetchOptimizationData, triggerRecovery } from "@/lib/data";
import { compareSchedules, type RecoveryComparison } from "@/lib/recoveryComparison";
import { corridorCapacityPct, riskNeutralizedPct } from "@/lib/metrics";
import {
  BTN_GHOST,
  BTN_PRIMARY,
  CARD,
  Chip,
  ErrorBox,
  INPUT,
  LABEL,
  Skeleton,
  Spinner,
} from "@/components/ui";

type DType =
  | "TRACK_UNAVAILABLE"
  | "ASSET_BREAKDOWN"
  | "EMERGENCY_WORK"
  | "POSSESSION_CURTAILMENT";

const TYPES: { key: DType; label: string; icon: string; blurb: string }[] = [
  { key: "TRACK_UNAVAILABLE", label: "Track unavailable", icon: "block", blurb: "A track is closed for the whole horizon." },
  { key: "ASSET_BREAKDOWN", label: "Asset breakdown", icon: "build_circle", blurb: "One asset is unavailable for maintenance." },
  { key: "EMERGENCY_WORK", label: "Emergency work", icon: "emergency", blurb: "An urgent repair block joins the plan." },
  { key: "POSSESSION_CURTAILMENT", label: "Possession curtailment", icon: "content_cut", blurb: "A possession window is cut short." },
];

const hhmm = (m: number) =>
  `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;

const CAT_STYLE: Record<string, string> = {
  PRESERVED: "bg-surface-container-high text-on-surface-variant",
  MOVED: "bg-secondary-container/30 text-secondary",
  NEWLY_SCHEDULED: "bg-primary-container/15 text-primary-fixed",
  DROPPED: "bg-error-container/50 text-error",
};

export default function SimulationPage() {
  const [baseline, setBaseline] = useState<DashboardData | null>(null);
  const [recovered, setRecovered] = useState<DashboardData | null>(null);
  const [comparison, setComparison] = useState<RecoveryComparison | null>(null);
  const [event, setEvent] = useState<DisruptionEvent | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [type, setType] = useState<DType | null>(null);
  const [track, setTrack] = useState("");
  const [asset, setAsset] = useState("");
  const [from, setFrom] = useState(0);
  const [to, setTo] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const d = await fetchOptimizationData();
      setBaseline(d);
      setRecovered(null);
      setComparison(null);
      setEvent(null);
      setTrack((cur) => cur || d.request.tracks?.[0] || "");
      setTo(d.request.horizon_minutes || 0);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial baseline load
    void load();
  }, [load]);

  const req = baseline?.request ?? null;
  const horizon = req?.horizon_minutes || 1440;
  const tracks = req?.tracks ?? [];
  const assets = useMemo(
    () => [...new Set((req?.candidates ?? []).map((c) => c.asset_id))].sort(),
    [req],
  );

  const canRun =
    !!baseline &&
    !running &&
    type !== null &&
    (type === "ASSET_BREAKDOWN"
      ? asset !== ""
      : type === "POSSESSION_CURTAILMENT"
        ? track !== "" && from < to
        : track !== "");

  const run = async () => {
    if (!baseline || !req || !type) return;
    const base = {
      disruption_id: `DISR-${Date.now()}`,
      disruption_type: type,
      corridor_id: req.corridor_id,
      start_minute: 0,
      end_minute: horizon,
    };
    let ev: DisruptionEvent;
    if (type === "ASSET_BREAKDOWN") {
      ev = { ...base, affected_asset_id: asset, description: `Asset ${asset} breakdown - unavailable for maintenance.` };
    } else if (type === "TRACK_UNAVAILABLE") {
      ev = { ...base, track_id: track, description: `${track} closed for emergency civil works.` };
    } else if (type === "EMERGENCY_WORK") {
      const cand: BlockCandidate = {
        block_id: `EMERGENCY-${Date.now()}`,
        asset_id: `EMERGENCY-ASSET-${track}`,
        track_id: track,
        work_type: "EMERGENCY_REPAIR",
        duration_minutes: 60,
        earliest_start_minute: 0,
        latest_end_minute: horizon,
        priority_score: 0.95,
        risk_score: 0.9,
        dependencies: [],
        mutual_exclusion_group: null,
        is_committed: false,
        status: "PLANNED",
      };
      ev = { ...base, track_id: track, new_candidate: cand, description: `Emergency repair required on ${track}.` };
    } else {
      ev = { ...base, track_id: track, start_minute: from, end_minute: to, description: `${track} possession window curtailed ${hhmm(from)}-${hhmm(to)}.` };
    }
    setRunning(true);
    setError(null);
    const out = await triggerRecovery(baseline.request, ev);
    if (out.ok) {
      const rec = enrichData(out.data.recovery_result, out.data.updated_request, "ONLINE", "SYNTHETIC_FIXTURE", {
        apiEndpoint: "POST /recover",
      });
      setRecovered(rec);
      setComparison(compareSchedules(baseline, rec));
      setEvent(out.data.disruption);
    } else {
      setError(`${out.reason === "unreachable" ? "Backend unreachable" : "Recovery refused"}: ${out.message}`);
    }
    setRunning(false);
  };

  const offline = baseline && baseline.connectivity !== "ONLINE";

  return (
    <div className="relative flex flex-col w-full pt-space-md pb-space-2xl gap-space-lg">
      {/* persistent SIMULATION banner */}
      <div role="note" className="rounded-DEFAULT border border-tertiary/60 bg-on-tertiary/40 px-space-md py-space-sm flex flex-wrap items-center gap-3">
        <span className="font-label-caps text-label-caps px-2 py-0.5 rounded bg-on-tertiary text-tertiary font-bold tracking-widest">SIMULATION</span>
        <span className="font-body-sm text-body-sm text-tertiary-container">
          Stateless what-if on the synthetic <em>Corridor A</em> fixture. Not connected to the maintenance-job lifecycle, not NDLS–AGC, not a live feed. Nothing here is saved.
        </span>
      </div>

      <section className="relative overflow-hidden rounded-2xl bg-surface-container-lowest border border-outline-variant/30 shadow-2xl p-6 lg:p-8">
        <div aria-hidden className="absolute -top-24 left-1/4 w-[640px] h-[320px] rounded-full bg-error/10 blur-[110px] pointer-events-none" />
        <div aria-hidden className="absolute bottom-0 right-10 w-[520px] h-[300px] rounded-full bg-primary-container/10 blur-[110px] pointer-events-none" />
        <svg aria-hidden className="absolute inset-0 w-full h-full opacity-30 pointer-events-none" viewBox="0 0 1200 400" preserveAspectRatio="none">
          <g className="orbit-slow"><ellipse cx="900" cy="200" rx="330" ry="140" fill="none" stroke="#ffb688" strokeDasharray="6 10" strokeWidth="1" /></g>
          <g className="orbit-slower"><ellipse cx="900" cy="200" rx="230" ry="90" fill="none" stroke="#00dbe9" strokeDasharray="2 12" strokeWidth="1" /></g>
          <path className="animate-laser-fast" d="M 0 320 Q 400 180, 800 230 T 1200 120" fill="none" stroke="#7df4ff" strokeWidth="2" />
        </svg>
        <div className="relative z-10 flex flex-col gap-2">
          <span className="font-label-caps text-label-caps text-primary tracking-widest">
            DISRUPTION WHAT-IF // CORRIDOR A (SYNTHETIC FIXTURE)
          </span>
          <h1 className="font-headline-lg text-headline-lg lg:text-display-xl text-primary font-light leading-tight tracking-tight">
            Disruption &amp; Operational Recovery
          </h1>
          <p className="font-body-md text-body-md text-on-surface-variant max-w-3xl">
            Apply a hypothetical disruption to the baseline maintenance plan and let the CP-SAT solver rebuild it. The comparison shows what stays, what moves and what cannot be placed. It illustrates the recovery method on synthetic data; it does not represent live railway operations.
          </p>
        </div>
      </section>

      {offline && (
        <div role="alert" className="rounded-DEFAULT border border-secondary/60 bg-secondary-container/20 px-space-md py-space-sm font-body-sm text-body-sm text-secondary">
          The backend did not solve the baseline: showing the checked-in fixture result instead ({baseline?.connectivityDetail}). Recovery needs the backend and will not be substituted.
        </div>
      )}
      {error && <ErrorBox error={error} title="SIMULATION ERROR" />}

      {loading || !baseline ? (
        <Skeleton rows={5} />
      ) : (
        <>
          {/* controls */}
          <div className={`${CARD} flex flex-col gap-space-md`}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="font-headline-sm text-headline-sm text-on-surface">1 · Choose a disruption</h2>
              <Chip tone="violet">BASELINE: {baseline.result.status} · {baseline.result.scheduled_blocks.length} scheduled</Chip>
            </div>
            <div role="radiogroup" aria-label="Disruption type" className="grid grid-cols-2 lg:grid-cols-4 gap-space-sm">
              {TYPES.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  role="radio"
                  aria-checked={type === t.key}
                  onClick={() => setType(t.key)}
                  className={`text-left rounded-DEFAULT px-space-md py-space-sm transition-all ${type === t.key ? "bg-surface-container-high ring-1 ring-error/60 shadow-[0_0_20px_rgba(255,180,171,0.15)]" : "bg-surface-container-low hover:bg-surface-container"}`}
                >
                  <div className="flex items-center gap-2 font-headline-sm text-body-md text-on-surface">
                    <span className="material-symbols-outlined text-error text-[18px]">{t.icon}</span>
                    {t.label}
                  </div>
                  <div className="font-body-sm text-[12px] text-outline">{t.blurb}</div>
                </button>
              ))}
            </div>

            {type && (
              <div className="grid grid-cols-1 md:grid-cols-4 gap-space-md items-end">
                {type === "ASSET_BREAKDOWN" ? (
                  <label className="flex flex-col gap-space-2xs md:col-span-2">
                    <span className={LABEL}>Affected asset</span>
                    <select className={INPUT} value={asset} onChange={(e) => setAsset(e.target.value)}>
                      <option value="">Select asset…</option>
                      {assets.map((a) => <option key={a}>{a}</option>)}
                    </select>
                  </label>
                ) : (
                  <label className="flex flex-col gap-space-2xs">
                    <span className={LABEL}>Track</span>
                    <select className={INPUT} value={track} onChange={(e) => setTrack(e.target.value)}>
                      {tracks.map((t) => <option key={t}>{t}</option>)}
                    </select>
                  </label>
                )}
                {type === "POSSESSION_CURTAILMENT" && (
                  <>
                    <label className="flex flex-col gap-space-2xs">
                      <span className={LABEL}>Window from (min)</span>
                      <input type="number" min={0} max={horizon} className={INPUT} value={from} onChange={(e) => setFrom(Number(e.target.value))} />
                    </label>
                    <label className="flex flex-col gap-space-2xs">
                      <span className={LABEL}>Window to (min)</span>
                      <input type="number" min={0} max={horizon} className={INPUT} value={to} onChange={(e) => setTo(Number(e.target.value))} />
                    </label>
                  </>
                )}
                <div className="flex gap-space-sm md:col-span-1">
                  <button type="button" className={BTN_PRIMARY} disabled={!canRun} onClick={run}>
                    {running && <Spinner />}
                    {running ? "Recovering…" : "Simulate & recover"}
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* lanes */}
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-space-lg">
            <Plan title="Baseline plan" tone="cyan" data={baseline} changes={null} disrupted={null} />
            {recovered && comparison ? (
              <Plan title="Recovered plan" tone="amber" data={recovered} changes={comparison} disrupted={event} />
            ) : (
              <div className={`${CARD} flex items-center justify-center min-h-[240px]`}>
                <span className="font-body-md text-body-md text-outline text-center">
                  Choose a disruption and press <strong>Simulate &amp; recover</strong> to see the recovered plan.
                </span>
              </div>
            )}
          </div>

          {comparison && event && recovered && (
            <div className={`${CARD} flex flex-col gap-space-md`}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="font-headline-sm text-headline-sm text-on-surface">Recovery changes</h2>
                <div className="flex flex-wrap gap-2">
                  <Chip tone="neutral">{comparison.preservedCount} preserved</Chip>
                  <Chip tone="amber">{comparison.movedCount} moved</Chip>
                  <Chip tone="cyan">{comparison.newlyScheduledCount} newly scheduled</Chip>
                  <Chip tone="red">{comparison.droppedCount} dropped</Chip>
                </div>
              </div>
              <p className="font-body-sm text-body-sm text-on-surface-variant">
                Disruption: {event.description} Solver: {comparison.before_status} → {comparison.after_status} ({comparison.before_scheduled_count} → {comparison.after_scheduled_count} scheduled, {comparison.before_unscheduled_count} → {comparison.after_unscheduled_count} unscheduled).
              </p>
              <div className="rounded-DEFAULT bg-surface-container-lowest divide-y divide-outline-variant/15">
                {comparison.changes.map((c) => (
                  <div key={c.block_id} className="grid grid-cols-12 gap-2 px-space-md py-2 items-center">
                    <span className="col-span-3 font-label-mono text-label-mono text-primary">{c.block_id}</span>
                    <span className="col-span-1 font-label-mono text-[11px] text-outline">{c.track_id}</span>
                    <span className="col-span-3 font-label-mono text-[11px] text-on-surface-variant">
                      {c.old_start_minute != null ? `${hhmm(c.old_start_minute)}–${hhmm(c.old_end_minute ?? 0)}` : "—"}
                    </span>
                    <span className="col-span-3 font-label-mono text-[11px] text-secondary">
                      {c.new_start_minute != null ? `${hhmm(c.new_start_minute)}–${hhmm(c.new_end_minute ?? 0)}` : "—"}
                    </span>
                    <span className="col-span-2 flex flex-col items-end gap-0.5">
                      <span className={`px-2 py-0.5 rounded font-label-caps text-label-caps ${CAT_STYLE[c.category]}`}>{c.category.replace("_", " ")}</span>
                    </span>
                    {c.reason && <span className="col-span-12 font-body-sm text-[12px] text-outline">{c.reason}</span>}
                  </div>
                ))}
              </div>
              <div className="flex gap-space-sm">
                <button type="button" className={BTN_GHOST} onClick={() => void load()}>
                  <span className="material-symbols-outlined text-[15px]">refresh</span>
                  Reset &amp; simulate another
                </button>
              </div>
              <p className="font-label-mono text-[11px] text-outline">
                Times are minutes from the fixture&apos;s own horizon start, shown as HH:MM. The recovered plan is a what-if and cannot be committed anywhere.
              </p>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function Plan({
  title,
  tone,
  data,
  changes,
  disrupted,
}: {
  title: string;
  tone: "cyan" | "amber";
  data: DashboardData;
  changes: RecoveryComparison | null;
  disrupted: DisruptionEvent | null;
}) {
  const horizon = data.request.horizon_minutes || 1440;
  const tracks = data.request.tracks ?? [];
  const cap = corridorCapacityPct(data.request, data.result);
  const risk = riskNeutralizedPct(data.request, data.result);
  const movedIds = new Set(changes?.changes.filter((c) => c.category === "MOVED").map((c) => c.block_id));
  const newIds = new Set(changes?.changes.filter((c) => c.category === "NEWLY_SCHEDULED").map((c) => c.block_id));
  return (
    <div className={`${CARD} flex flex-col gap-space-md`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-headline-sm text-headline-sm text-primary">{title}</h2>
        <Chip tone={tone}>{data.result.status}</Chip>
      </div>
      <div className="grid grid-cols-3 gap-2 font-label-mono text-label-mono">
        <Mini k="SCHEDULED" v={data.result.scheduled_blocks.length} />
        <Mini k="UNSCHEDULED" v={data.result.unscheduled_blocks.length} />
        <Mini k="SOLVE" v={`${Math.round((data.result.solve_time_seconds || 0) * 1000)} ms`} />
        <Mini k="CAPACITY LEFT" v={`${cap.toFixed(0)}%`} />
        <Mini k="RISK ADDRESSED" v={`${risk.toFixed(0)}%`} />
        <Mini k="PRIORITY PLACED" v={(data.result.total_priority_scheduled || 0).toFixed(2)} />
      </div>
      <div className="rounded-DEFAULT bg-surface-container-lowest p-space-sm flex flex-col gap-2 overflow-x-auto">
        <div className="flex justify-between font-label-mono text-[10px] text-outline pl-14">
          <span>00:00</span><span>{hhmm(Math.round(horizon / 2))}</span><span>{hhmm(horizon)}</span>
        </div>
        {tracks.map((t) => {
          const hit = disrupted && (disrupted.track_id === t || disrupted.disruption_type === "TRACK_UNAVAILABLE" && disrupted.track_id === t);
          return (
            <div key={t} className="flex items-center gap-2">
              <span className={`w-12 font-label-mono text-[11px] ${hit ? "text-error font-bold" : "text-outline"}`}>{t}</span>
              <div className={`relative flex-1 h-8 rounded border ${hit ? "border-error/60 bg-error-container/20 shadow-[0_0_14px_rgba(255,180,171,0.2)]" : "border-outline-variant/20 bg-surface-container-low/70"}`}>
                {data.result.scheduled_blocks.filter((b) => b.track_id === t).map((b) => {
                  const left = (b.start_minute / horizon) * 100;
                  const width = Math.max(1, ((b.end_minute - b.start_minute) / horizon) * 100);
                  const moved = movedIds.has(b.block_id);
                  const fresh = newIds.has(b.block_id);
                  return (
                    <div
                      key={b.block_id}
                      title={`${b.block_id} · ${b.work_type} · ${hhmm(b.start_minute)}–${hhmm(b.end_minute)}`}
                      className={`absolute top-0.5 h-7 rounded text-[9px] font-label-mono flex items-center justify-center overflow-hidden ${fresh ? "bg-primary-container text-on-primary-container" : moved ? "bg-secondary-container/80 text-on-secondary-container border border-secondary" : "bg-surface-container-high text-primary border border-primary/50"}`}
                      style={{ left: `${left}%`, width: `${width}%` }}
                    >
                      {b.block_id.replace("BLK-", "")}
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
      {data.result.unscheduled_blocks.length > 0 && (
        <div>
          <div className="font-label-caps text-label-caps text-secondary mb-1">UNSCHEDULED</div>
          <ul className="flex flex-col gap-1">
            {data.enrichedUnscheduled.map((u) => (
              <li key={u.block_id} className="font-label-mono text-[11px] text-on-surface-variant">
                <span className="text-primary">{u.block_id}</span> — {u.rejectionReason}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function Mini({ k, v }: { k: string; v: number | string }) {
  return (
    <div className="rounded bg-surface-container-lowest px-space-sm py-1">
      <div className="font-label-caps text-label-caps text-outline">{k}</div>
      <div className="text-primary tabular-nums">{v}</div>
    </div>
  );
}
