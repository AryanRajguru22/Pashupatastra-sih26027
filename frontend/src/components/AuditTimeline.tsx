"use client";

import { useState } from "react";
import type { Actor, JobEvent, JobState } from "@/lib/api";
import { planWindow, recorded, shortId } from "@/lib/time";
import { Chip, StatusChip, TimeTag } from "@/components/ui";

const EVENT_LABEL: Record<string, { label: string; icon: string }> = {
  JOB_CREATED: { label: "Job created", icon: "add_task" },
  JOB_SCORED: { label: "Risk scored", icon: "analytics" },
  OPTIMIZATION_REQUESTED: { label: "Optimization requested", icon: "play_circle" },
  OPTIMIZATION_COMPLETED: { label: "Optimization completed", icon: "task_alt" },
  OPTIMIZATION_FAILED: { label: "Optimization failed", icon: "error" },
  OPTIMIZATION_REFUSED: { label: "Optimization refused", icon: "block" },
  BLOCK_PROPOSED: { label: "Block proposed", icon: "event_available" },
  BLOCK_REPROPOSED: { label: "Block re-proposed", icon: "published_with_changes" },
  COMMITTED_BLOCK_PRESERVED: { label: "Committed block preserved", icon: "lock" },
  COMMITTED_BLOCK_CONFLICT: { label: "Committed block conflict", icon: "warning" },
  PROPOSAL_INVALIDATED: { label: "Proposal withdrawn by system", icon: "cancel" },
  BLOCK_COMMITTED: { label: "Approved · block committed", icon: "verified" },
  PROPOSAL_POSTPONED: { label: "Proposal postponed", icon: "schedule" },
  PROPOSAL_REJECTED: { label: "Proposal rejected", icon: "thumb_down" },
  BLOCK_RELEASED: { label: "Block released", icon: "lock_open" },
  EXECUTION_STARTED: { label: "Execution started", icon: "engineering" },
  EXECUTION_COMPLETED: { label: "Execution completed", icon: "check_circle" },
  EXECUTION_NOT_COMPLETED: { label: "Reported not completed", icon: "report" },
  TRANSITION_REJECTED: { label: "Refused attempt (state unchanged)", icon: "gpp_bad" },
  SCHEDULE_ASSIGNED: { label: "Schedule assigned", icon: "event" },
  JOB_COMPLETED: { label: "Job completed", icon: "done_all" },
};

const ERROR_EVENTS = new Set([
  "TRANSITION_REJECTED",
  "OPTIMIZATION_FAILED",
  "COMMITTED_BLOCK_CONFLICT",
]);

export function assuranceText(a: string): string {
  if (a === "DECLARED_UNVERIFIED") return "declared, not verified";
  if (a === "SYSTEM_INTERNAL") return "system";
  if (a === "NONE") return "unidentified";
  return a.toLowerCase();
}

function ActorPill({ actor }: { actor: Actor }) {
  const system = actor.role === "SYSTEM";
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full font-label-mono text-[11px] ${system ? "bg-tertiary-container/20 text-tertiary" : "bg-surface-container-high text-primary-fixed"}`}
      title={`${actor.actor_id} · role ${actor.role} · kind ${actor.kind} · assurance ${actor.assurance}`}
    >
      <span className="material-symbols-outlined text-[13px]">
        {system ? "memory" : "person"}
      </span>
      {actor.actor_id}
      <span className="text-outline">
        {actor.role} · {assuranceText(actor.assurance)}
      </span>
    </span>
  );
}

function stateChanged(a?: JobState | null, b?: JobState | null): boolean {
  if (!a || !b) return false;
  return a.status !== b.status;
}

function placementChanged(a?: JobState | null, b?: JobState | null): boolean {
  if (!a || !b) return false;
  return (
    a.schedule_start_minute !== b.schedule_start_minute ||
    a.schedule_end_minute !== b.schedule_end_minute
  );
}

function placementText(s?: JobState | null): string {
  if (!s || s.schedule_start_minute == null || s.schedule_end_minute == null)
    return "none";
  return planWindow(s.schedule_start_minute, s.schedule_end_minute);
}

function fmtVal(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

interface Group {
  runId: string | null;
  events: JobEvent[];
}

function groupByRun(events: JobEvent[]): Group[] {
  const out: Group[] = [];
  for (const e of events) {
    const run = e.optimization_run_id ?? null;
    const last = out[out.length - 1];
    if (last && run && last.runId === run) last.events.push(e);
    else out.push({ runId: run, events: [e] });
  }
  return out;
}

export default function AuditTimeline({
  events,
  highlightSystemActors = true,
}: {
  events: JobEvent[];
  highlightSystemActors?: boolean;
}) {
  const [openId, setOpenId] = useState<string | null>(null);
  const [rawId, setRawId] = useState<string | null>(null);
  void highlightSystemActors;
  const sorted = [...events].sort((a, b) => a.sequence - b.sequence);
  const groups = groupByRun(sorted);
  const first = sorted[0];
  const last = sorted[sorted.length - 1];

  return (
    <div className="flex flex-col gap-space-md">
      <div className="flex flex-wrap items-center gap-space-sm font-label-mono text-label-mono text-on-surface-variant">
        <Chip tone="cyan">{sorted.length} EVENTS</Chip>
        {first && <span>first {recorded(first.occurred_at)}</span>}
        {last && <span>· last {recorded(last.occurred_at)}</span>}
        <TimeTag kind="RECORDED" />
        <Chip tone="neutral" title="The backend refuses UPDATE and DELETE on job events">
          Append-only · no edits possible
        </Chip>
      </div>

      <ol className="relative flex flex-col gap-space-md pl-6 before:absolute before:left-[11px] before:top-2 before:bottom-2 before:w-px before:bg-gradient-to-b before:from-primary-container/60 before:via-outline-variant/40 before:to-transparent">
        {groups.map((g, gi) => (
          <li key={gi} className="flex flex-col gap-space-sm">
            {g.runId && (
              <div
                className="font-label-caps text-label-caps text-tertiary flex items-center gap-2"
                title={g.runId}
              >
                <span className="material-symbols-outlined text-[14px]">
                  hub
                </span>
                OPTIMIZATION RUN {shortId(g.runId, 10)}
              </div>
            )}
            <ul
              className={`flex flex-col gap-space-sm ${g.runId ? "border-l border-tertiary/30 pl-3 ml-1" : ""}`}
            >
              {g.events.map((e) => {
                const meta = EVENT_LABEL[e.event_type] ?? {
                  label: e.event_type,
                  icon: "circle",
                };
                const err = ERROR_EVENTS.has(e.event_type);
                const open = openId === e.event_id;
                const changedState = stateChanged(e.before_state, e.after_state);
                return (
                  <li key={e.event_id} className="relative">
                    <span
                      className={`absolute -left-[26px] top-2.5 w-3 h-3 rounded-full ring-4 ring-background ${err ? "bg-error" : e.actor.role === "SYSTEM" ? "bg-tertiary" : "bg-primary-container shadow-[0_0_10px_#00f0ff]"}`}
                    />
                    <button
                      type="button"
                      aria-expanded={open}
                      onClick={() => setOpenId(open ? null : e.event_id)}
                      className={`w-full text-left rounded-DEFAULT px-space-md py-space-sm transition-all ${err ? "bg-error-container/20 hover:bg-error-container/30" : "bg-surface-container-low hover:bg-surface-container"} ${open ? "ring-1 ring-primary-container/40" : ""}`}
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex items-center gap-2 min-w-0">
                          <span
                            className={`material-symbols-outlined text-[18px] ${err ? "text-error" : "text-primary-fixed-dim"}`}
                          >
                            {meta.icon}
                          </span>
                          <span className="font-headline-sm text-body-md text-on-surface">
                            {meta.label}
                          </span>
                          <span className="font-label-mono text-[10px] text-outline">
                            #{e.sequence} · {e.event_type}
                          </span>
                        </div>
                        <span className="font-label-mono text-[11px] text-outline">
                          {recorded(e.occurred_at)}
                        </span>
                      </div>
                      <div className="mt-1.5 flex flex-wrap items-center gap-2">
                        <ActorPill actor={e.actor} />
                        {changedState && e.before_state && e.after_state && (
                          <span className="inline-flex items-center gap-1.5">
                            <StatusChip status={e.before_state.status} />
                            <span className="material-symbols-outlined text-[14px] text-outline">
                              arrow_forward
                            </span>
                            <StatusChip status={e.after_state.status} />
                          </span>
                        )}
                        {placementChanged(e.before_state, e.after_state) && (
                          <span className="font-label-mono text-[11px] text-secondary">
                            {placementText(e.before_state)} →{" "}
                            {placementText(e.after_state)}{" "}
                            <TimeTag kind="PLAN" />
                          </span>
                        )}
                      </div>
                      {e.reason && (
                        <blockquote className="mt-2 border-l-2 border-secondary/60 pl-2 font-body-sm text-body-sm text-on-surface-variant">
                          {e.reason}
                        </blockquote>
                      )}
                    </button>
                    {open && (
                      <div className="mt-1 rounded-DEFAULT bg-surface-container-lowest px-space-md py-space-sm">
                        <div className="font-label-caps text-label-caps text-outline mb-1">
                          EVENT DETAILS (backend record)
                        </div>
                        <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-space-lg">
                          <Row k="event_id" v={e.event_id} />
                          <Row k="entity_type" v={e.entity_type} />
                          <Row
                            k="optimization_run_id"
                            v={e.optimization_run_id ?? "—"}
                          />
                          <Row
                            k="before"
                            v={
                              e.before_state
                                ? `${e.before_state.status} · ${e.before_state.block_status} · committed ${e.before_state.is_committed}`
                                : "—"
                            }
                          />
                          <Row
                            k="after"
                            v={
                              e.after_state
                                ? `${e.after_state.status} · ${e.after_state.block_status} · committed ${e.after_state.is_committed}`
                                : "—"
                            }
                          />
                          {Object.entries(e.metadata ?? {}).map(([k, v]) => (
                            <Row key={k} k={k} v={fmtVal(v)} />
                          ))}
                        </dl>
                        <button
                          type="button"
                          className="mt-2 font-label-mono text-label-mono text-primary hover:underline"
                          onClick={() =>
                            setRawId(rawId === e.event_id ? null : e.event_id)
                          }
                        >
                          {rawId === e.event_id ? "Hide raw JSON" : "Show raw JSON"}
                        </button>
                        {rawId === e.event_id && (
                          <pre className="mt-2 max-h-72 overflow-auto rounded bg-surface-container p-2 font-label-mono text-[11px] text-on-surface-variant">
                            {JSON.stringify(e, null, 2)}
                          </pre>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          </li>
        ))}
      </ol>
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex gap-2 py-0.5 border-b border-outline-variant/10 min-w-0">
      <dt className="font-label-mono text-[11px] text-outline shrink-0 w-44 truncate" title={k}>
        {k}
      </dt>
      <dd className="font-label-mono text-[11px] text-on-surface break-all min-w-0">
        {v}
      </dd>
    </div>
  );
}
