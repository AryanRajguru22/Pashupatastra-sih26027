/**
 * Client-side BEFORE -> AFTER schedule comparison for the recovery UX.
 *
 * The backend POST /recover endpoint returns the disrupted request and its
 * recovery OptimizationResult, but does not (yet) classify individual
 * block changes - that comparison is computed here from the two real
 * results, never fabricated. A block is DROPPED either because the
 * disruption itself removed it (no longer a candidate at all) or because
 * the solver could not fit it back in (a real rejection reason is reused
 * from the already-computed enrichedUnscheduled list, never invented).
 */

import type {
  DashboardData,
  ScheduledBlock,
} from "@/types/contracts";

export type BlockChangeCategory =
  | "PRESERVED"
  | "MOVED"
  | "NEWLY_SCHEDULED"
  | "DROPPED";

export interface BlockChange {
  block_id: string;
  category: BlockChangeCategory;
  track_id?: string;
  old_start_minute?: number;
  old_end_minute?: number;
  new_start_minute?: number;
  new_end_minute?: number;
  reason?: string;
}

export interface RecoveryComparison {
  before_status: string;
  after_status: string;
  before_scheduled_count: number;
  after_scheduled_count: number;
  before_unscheduled_count: number;
  after_unscheduled_count: number;
  changes: BlockChange[];
  preservedCount: number;
  movedCount: number;
  newlyScheduledCount: number;
  droppedCount: number;
}

export function compareSchedules(
  before: DashboardData,
  after: DashboardData
): RecoveryComparison {
  const beforeMap = new Map<string, ScheduledBlock>(
    before.result.scheduled_blocks.map((b) => [b.block_id, b])
  );
  const afterMap = new Map<string, ScheduledBlock>(
    after.result.scheduled_blocks.map((b) => [b.block_id, b])
  );
  const afterCandidateIds = new Set(
    after.request.candidates.map((c) => c.block_id)
  );
  const afterUnscheduledReason = new Map<string, string>(
    after.enrichedUnscheduled.map((ub) => [ub.block_id, ub.rejectionReason])
  );

  const changes: BlockChange[] = [];

  for (const [blockId, beforeBlock] of beforeMap) {
    const afterBlock = afterMap.get(blockId);

    if (!afterBlock) {
      const reason = afterCandidateIds.has(blockId)
        ? afterUnscheduledReason.get(blockId) ??
          "Excluded by CP-SAT solver: capacity or objective trade-off"
        : "Removed by disruption";

      changes.push({
        block_id: blockId,
        category: "DROPPED",
        track_id: beforeBlock.track_id,
        old_start_minute: beforeBlock.start_minute,
        old_end_minute: beforeBlock.end_minute,
        reason,
      });
      continue;
    }

    const moved =
      beforeBlock.track_id !== afterBlock.track_id ||
      beforeBlock.start_minute !== afterBlock.start_minute ||
      beforeBlock.end_minute !== afterBlock.end_minute;

    changes.push({
      block_id: blockId,
      category: moved ? "MOVED" : "PRESERVED",
      track_id: afterBlock.track_id,
      old_start_minute: beforeBlock.start_minute,
      old_end_minute: beforeBlock.end_minute,
      new_start_minute: afterBlock.start_minute,
      new_end_minute: afterBlock.end_minute,
    });
  }

  for (const [blockId, afterBlock] of afterMap) {
    if (!beforeMap.has(blockId)) {
      changes.push({
        block_id: blockId,
        category: "NEWLY_SCHEDULED",
        track_id: afterBlock.track_id,
        new_start_minute: afterBlock.start_minute,
        new_end_minute: afterBlock.end_minute,
      });
    }
  }

  const count = (cat: BlockChangeCategory) =>
    changes.filter((c) => c.category === cat).length;

  return {
    before_status: before.result.status,
    after_status: after.result.status,
    before_scheduled_count: before.result.scheduled_blocks.length,
    after_scheduled_count: after.result.scheduled_blocks.length,
    before_unscheduled_count: before.result.unscheduled_blocks.length,
    after_unscheduled_count: after.result.unscheduled_blocks.length,
    changes,
    preservedCount: count("PRESERVED"),
    movedCount: count("MOVED"),
    newlyScheduledCount: count("NEWLY_SCHEDULED"),
    droppedCount: count("DROPPED"),
  };
}
