"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { listAllObligations, type Obligation } from "@/lib/api";
import { duration, recorded, shortId } from "@/lib/time";
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
  Skeleton,
  TimeTag,
} from "@/components/ui";

const TYPE_LABEL: Record<string, string> = {
  APPROVAL_PENDING: "Approval pending",
  EXECUTION_START_PENDING: "Execution start pending",
  COMPLETION_PENDING: "Completion pending",
  REPLANNING_PENDING: "Replanning pending",
  NONE: "Nothing owed",
};

const STATE_TONE: Record<string, "cyan" | "amber" | "red" | "neutral" | "violet"> = {
  WITHIN_SLA: "cyan",
  DUE_SOON: "amber",
  OVERDUE: "red",
  ESCALATED_L1: "red",
  ESCALATED_L2: "red",
  ESCALATED_L3: "red",
  MOOT: "violet",
  NO_SLA_DEFINED: "neutral",
  NONE: "neutral",
};

function ratio(o: Obligation): number {
  if (!o.sla_seconds || o.elapsed_seconds == null) return 0;
  return o.elapsed_seconds / o.sla_seconds;
}

export default function ObligationsPage() {
  const obls = useResource(() => listAllObligations(), []);
  const [role, setRole] = useState("ALL");
  const [type, setType] = useState("ALL");
  const [pastDue, setPastDue] = useState(false);
  const [attention, setAttention] = useState(false);
  const [openOnly, setOpenOnly] = useState(false);
  const [showSla, setShowSla] = useState(false);

  const all = useMemo(() => obls.data?.items ?? [], [obls.data]);
  const rows = useMemo(
    () =>
      all
        .filter((o) => (role === "ALL" ? true : o.owed_role === role))
        .filter((o) => (type === "ALL" ? true : o.obligation_type === type))
        .filter((o) => (pastDue ? o.is_past_due : true))
        .filter((o) => (attention ? !!o.attention_required : true))
        .filter((o) => (openOnly ? o.is_open : true))
        .sort((a, b) => {
          const ao = a.is_open ? 0 : 1;
          const bo = b.is_open ? 0 : 1;
          if (ao !== bo) return ao - bo;
          return (a.due_at ?? "9").localeCompare(b.due_at ?? "9");
        }),
    [all, role, type, pastDue, attention, openOnly],
  );

  const open = all.filter((o) => o.is_open);
  const within = open.filter((o) => o.state === "WITHIN_SLA").length;
  const soon = open.filter((o) => o.state === "DUE_SOON").length;
  const late = open.filter((o) => o.is_past_due).length;
  const attn = all.filter((o) => o.attention_required).length;

  return (
    <Page>
      <PageBanner
        serif
        eyebrow="ASSURANCE // ACCOUNTABILITY"
        title="Operational SLA Obligations"
        subtitle="Who currently owes the next action on each job, since when, and by when. Obligations are derived when read; nothing here sends, decides or escalates anything."
        right={
          <button type="button" className={BTN_GHOST} onClick={() => setShowSla((v) => !v)} aria-expanded={showSla}>
            <span className="material-symbols-outlined text-[15px]">info</span>
            {showSla ? "Hide SLA table" : "Show SLA table"}
          </button>
        }
      />

      <div className="rounded-DEFAULT border border-secondary/50 bg-secondary-container/15 px-space-md py-space-sm font-body-sm text-body-sm text-secondary">
        <strong>ASSUMED DEMO SLA{obls.data ? ` — policy ${obls.data.policy_version}` : ""}.</strong>{" "}
        These are demo engineering values chosen to demonstrate the accountability model. They are not Indian Railways policy and were not reviewed by any railway authority. All durations are wall-clock; there is no calendar, shift or holiday model.
      </div>

      {showSla && (
        <div className={CARD}>
          <div className="font-label-caps text-label-caps text-outline mb-2">ASSUMED SLA DURATIONS BY REPORTED SEVERITY</div>
          <table className="w-full font-label-mono text-label-mono">
            <thead>
              <tr className="text-outline text-left">
                <th className="py-1">Severity</th><th>Approval</th><th>Execution start</th><th>Completion</th>
              </tr>
            </thead>
            <tbody className="text-on-surface-variant">
              <tr><td className="py-1">CRITICAL</td><td>2 h</td><td>1 h</td><td>4 h</td></tr>
              <tr><td className="py-1">MODERATE</td><td>6 h</td><td>4 h</td><td>12 h</td></tr>
              <tr><td className="py-1">MINOR / NONE</td><td>24 h</td><td>12 h</td><td>24 h</td></tr>
            </tbody>
          </table>
          <p className="font-label-mono text-[11px] text-outline mt-2">
            DUE_SOON begins at 75% of the SLA; escalation levels L1/L2/L3 begin at 1×, 2× and 4×. Time never changes the lifecycle: a late proposal stays committable.
          </p>
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <Kpi k="OPEN OBLIGATIONS" v={open.length} />
        <Kpi k="WITHIN SLA" v={within} tone="text-primary-fixed" />
        <Kpi k="DUE SOON" v={soon} tone="text-secondary" />
        <Kpi k="PAST DUE" v={late} tone={late ? "text-error" : "text-on-surface"} />
        <Kpi k="NEEDS ATTENTION" v={attn} tone={attn ? "text-error" : "text-on-surface"} />
      </div>

      <div className="flex flex-wrap items-center gap-space-sm bg-surface-container-low p-space-sm rounded-DEFAULT">
        <label className="flex items-center gap-2">
          <span className="font-label-caps text-label-caps text-outline">OWNER ROLE</span>
          <select className={INPUT} value={role} onChange={(e) => setRole(e.target.value)} aria-label="Owner role">
            <option value="ALL">All roles</option>
            <option value="AUTHORITY">AUTHORITY</option>
            <option value="WORKER">WORKER</option>
            <option value="ENGINEER">ENGINEER</option>
          </select>
        </label>
        <label className="flex items-center gap-2">
          <span className="font-label-caps text-label-caps text-outline">TYPE</span>
          <select className={INPUT} value={type} onChange={(e) => setType(e.target.value)} aria-label="Obligation type">
            <option value="ALL">All types</option>
            {Object.entries(TYPE_LABEL).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
        </label>
        <Toggle on={openOnly} set={setOpenOnly} label="Open only" />
        <Toggle on={pastDue} set={setPastDue} label="Past due only" />
        <Toggle on={attention} set={setAttention} label="Needs attention" />
        <button type="button" className={`${BTN_GHOST} ml-auto`} onClick={() => obls.reload()}>
          <span className="material-symbols-outlined text-[15px]">refresh</span> Refresh
        </button>
      </div>

      {obls.error && <ErrorBox error={obls.error} />}

      <div className="flex flex-col bg-surface-container-low rounded-DEFAULT shadow-xl overflow-hidden">
        <div className="hidden lg:grid grid-cols-12 gap-2 bg-surface-container px-space-sm py-space-xs font-label-caps text-label-caps text-outline tracking-wider">
          <div className="col-span-2">JOB</div>
          <div className="col-span-2">OBLIGATION</div>
          <div className="col-span-1">OWNER</div>
          <div className="col-span-2">STATE</div>
          <div className="col-span-3">ELAPSED / TARGET</div>
          <div className="col-span-2">DUE (RECORDED)</div>
        </div>
        {obls.loading && !obls.data ? (
          <div className="p-space-md"><Skeleton rows={4} /></div>
        ) : rows.length === 0 ? (
          <div className="p-space-lg"><Empty title={all.length === 0 ? "No obligations" : "No obligations match these filters"} /></div>
        ) : (
          rows.map((o) => {
            const r = ratio(o);
            return (
              <div key={o.job_id} className="grid grid-cols-12 gap-2 px-space-sm py-2.5 border-b border-outline-variant/10 hover:bg-surface-container items-center">
                <Link href={`/jobs/${o.job_id}`} className="col-span-6 lg:col-span-2 font-label-mono text-label-mono font-bold text-primary hover:underline">{o.job_id}</Link>
                <div className="col-span-6 lg:col-span-2 font-body-sm text-body-sm text-on-surface" title={o.reason}>
                  {TYPE_LABEL[o.obligation_type] ?? o.obligation_type}
                  <div className="font-label-mono text-[10px] text-outline">{o.reason_code}</div>
                </div>
                <div className="col-span-4 lg:col-span-1">{o.owed_role ? <Chip tone="neutral">{o.owed_role}</Chip> : <span className="text-outline">—</span>}</div>
                <div className="col-span-8 lg:col-span-2 flex flex-wrap items-center gap-1">
                  <Chip tone={STATE_TONE[o.state] ?? "neutral"}>{o.state}</Chip>
                  {o.is_past_due && <Chip tone="red">PAST DUE</Chip>}
                  {o.state === "MOOT" && <span className="font-label-mono text-[10px] text-outline">Withdrawn by the system; not a breach</span>}
                  {o.attention_required && <Chip tone="red" title={`Attention role: ${o.attention_role ?? "—"}`}>ATTENTION: {o.attention_reason_code}</Chip>}
                </div>
                <div className="col-span-12 lg:col-span-3">
                  {o.sla_seconds ? (
                    <>
                      <div className="font-label-mono text-[11px] text-on-surface-variant">
                        {duration(o.elapsed_seconds)} of {duration(o.sla_seconds)} · L{o.escalation_level ?? 0}
                      </div>
                      <div className="h-1.5 mt-1 rounded-full bg-surface-container-highest overflow-hidden" role="progressbar" aria-valuenow={Math.round(Math.min(r, 1) * 100)} aria-valuemin={0} aria-valuemax={100}>
                        <div className={`h-full ${r >= 1 ? "bg-error" : r >= 0.75 ? "bg-secondary" : "bg-primary-container"}`} style={{ width: `${Math.min(100, r * 100)}%` }} />
                      </div>
                    </>
                  ) : (
                    <span className="font-label-mono text-[11px] text-outline">—</span>
                  )}
                </div>
                <div className="col-span-12 lg:col-span-2 font-label-mono text-[11px] text-on-surface-variant">
                  {recorded(o.due_at)}
                  <div className="text-outline">run {shortId(o.optimization_run_id)}</div>
                </div>
              </div>
            );
          })
        )}
        <div className="flex flex-wrap items-center justify-between gap-2 px-space-sm py-space-xs bg-surface-container font-label-mono text-[11px] text-outline">
          <span>
            {rows.length} of {all.length} · evaluated {recorded(obls.data?.evaluated_at)} <TimeTag kind="RECORDED" /> · policy {obls.data?.policy_version ?? "—"} (assumed: {String(obls.data?.policy_assumed ?? true)})
          </span>
          <span>Current only as of the evaluation moment. Refresh to re-read; there is no background timer.</span>
        </div>
      </div>
    </Page>
  );
}

function Kpi({ k, v, tone = "text-primary" }: { k: string; v: number; tone?: string }) {
  return (
    <div className="p-4 rounded bg-surface-container/60 backdrop-blur-md border border-outline-variant/20 shadow-md">
      <div className="font-label-caps text-label-caps text-outline">{k}</div>
      <div className={`font-headline-md text-headline-md tabular-nums ${tone}`}>{v}</div>
    </div>
  );
}

function Toggle({ on, set, label }: { on: boolean; set: (v: boolean) => void; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      onClick={() => set(!on)}
      className={`px-3 py-1.5 rounded-full font-label-mono text-label-mono transition-all ${on ? "bg-primary-container text-on-primary-container font-medium" : "bg-surface-container-high text-on-surface-variant hover:text-on-surface"}`}
    >
      {label}
    </button>
  );
}
