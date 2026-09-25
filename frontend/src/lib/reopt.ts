import type {
  JobEvent,
  OptimizationResponse,
} from "@/lib/api";

/**
 * Re-optimization comparison: classify what the latest run did to each job.
 * The primary source is the previous/current optimize responses held in the
 * session (placements); the label is refined from each job's own history.
 */

export type ReoptKind =
  | "PRESERVED"
  | "MOVED"
  | "UNCHANGED"
  | "POSTPONED_REPROPOSED"
  | "REJECTED_REPROPOSED"
  | "NEW"
  | "UNSCHEDULED"
  | "WITHDRAWN"
  | "CONFLICT";

export interface ReoptRow {
  jobId: string;
  kind: ReoptKind;
  before: { start: number; end: number } | null;
  after: { start: number; end: number } | null;
  note?: string;
  samePlacementAsRejected?: boolean;
  notBeforeMinute?: number | null;
  notBeforeSatisfied?: boolean | null;
}

function num(v: unknown): number | null {
  return typeof v === "number" ? v : null;
}

export function classify(
  jobId: string,
  events: JobEvent[],
  last: OptimizationResponse,
  prev: OptimizationResponse | null,
): ReoptRow {
  const runId = last.optimization_run_id;
  const after = last.scheduled.find((s) => s.job_id === jobId) ?? null;
  const prevSched = prev?.scheduled.find((s) => s.job_id === jobId) ?? null;
  const unsched = last.unscheduled.find((u) => u.job_id === jobId) ?? null;
  const afterP = after ? { start: after.start_minute, end: after.end_minute } : null;
  const beforeP = prevSched
    ? { start: prevSched.start_minute, end: prevSched.end_minute }
    : null;

  const sorted = [...events].sort((a, b) => a.sequence - b.sequence);
  const inRun = sorted.filter((e) => e.optimization_run_id === runId);
  const placeEvt = inRun.find((e) =>
    [
      "BLOCK_PROPOSED",
      "BLOCK_REPROPOSED",
      "COMMITTED_BLOCK_PRESERVED",
    ].includes(e.event_type),
  );

  if (unsched) {
    return {
      jobId,
      kind: "UNSCHEDULED",
      before: beforeP,
      after: null,
      note: unsched.reason,
    };
  }
  if (inRun.some((e) => e.event_type === "COMMITTED_BLOCK_CONFLICT")) {
    return { jobId, kind: "CONFLICT", before: beforeP, after: afterP };
  }
  if (inRun.some((e) => e.event_type === "PROPOSAL_INVALIDATED")) {
    return { jobId, kind: "WITHDRAWN", before: beforeP, after: null };
  }
  if (after?.is_committed || inRun.some((e) => e.event_type === "COMMITTED_BLOCK_PRESERVED")) {
    return { jobId, kind: "PRESERVED", before: beforeP ?? afterP, after: afterP };
  }

  // Latest authority decision recorded before this run's placement.
  const before = placeEvt
    ? sorted.filter((e) => e.sequence < placeEvt.sequence)
    : sorted;
  const decision = [...before]
    .reverse()
    .find((e) => e.event_type === "PROPOSAL_POSTPONED" || e.event_type === "PROPOSAL_REJECTED");
  const laterPlacement = decision
    ? before.some(
        (e) =>
          e.sequence > decision.sequence &&
          ["BLOCK_PROPOSED", "BLOCK_REPROPOSED"].includes(e.event_type),
      )
    : false;

  if (decision && !laterPlacement) {
    const m = decision.metadata ?? {};
    if (decision.event_type === "PROPOSAL_POSTPONED") {
      const nb = num(m.not_before_minute);
      return {
        jobId,
        kind: "POSTPONED_REPROPOSED",
        before: {
          start: num(m.original_start_minute) ?? beforeP?.start ?? 0,
          end: num(m.original_end_minute) ?? beforeP?.end ?? 0,
        },
        after: afterP,
        notBeforeMinute: nb,
        notBeforeSatisfied: nb != null && afterP ? afterP.start >= nb : null,
        note: decision.reason ?? undefined,
      };
    }
    const rs = num(m.rejected_start_minute);
    const re = num(m.rejected_end_minute);
    return {
      jobId,
      kind: "REJECTED_REPROPOSED",
      before: rs != null && re != null ? { start: rs, end: re } : beforeP,
      after: afterP,
      samePlacementAsRejected:
        rs != null && re != null && afterP ? afterP.start === rs && afterP.end === re : false,
      note: decision.reason ?? undefined,
    };
  }

  const changed = placeEvt?.metadata?.placement_changed;
  if (placeEvt?.event_type === "BLOCK_REPROPOSED") {
    const ps = num(placeEvt.metadata.previous_start_minute);
    const pe = num(placeEvt.metadata.previous_end_minute);
    return {
      jobId,
      kind: changed === false ? "UNCHANGED" : "MOVED",
      before: ps != null && pe != null ? { start: ps, end: pe } : beforeP,
      after: afterP,
    };
  }
  if (beforeP && afterP) {
    return {
      jobId,
      kind:
        beforeP.start === afterP.start && beforeP.end === afterP.end
          ? "UNCHANGED"
          : "MOVED",
      before: beforeP,
      after: afterP,
    };
  }
  return { jobId, kind: "NEW", before: null, after: afterP };
}

export const REOPT_LABEL: Record<ReoptKind, string> = {
  PRESERVED: "PINNED · preserved",
  MOVED: "MOVED",
  UNCHANGED: "Re-proposed · unchanged",
  POSTPONED_REPROPOSED: "POSTPONED → re-proposed",
  REJECTED_REPROPOSED: "REJECTED → re-proposed",
  NEW: "NEW",
  UNSCHEDULED: "UNSCHEDULED",
  WITHDRAWN: "Proposal withdrawn by system",
  CONFLICT: "Committed block conflict",
};
