"use client";

import { useMemo } from "react";
import { api, type Job, type OptimizationRun } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useResource } from "@/lib/useResource";

/**
 * The most recent optimization run this UI can identify. There is no "list
 * runs" endpoint, so after a reload it is the newest run referenced by any
 * job's proposal_run_id (fetched via GET /v1/optimization-runs/{id}). While
 * the session still holds an optimize response, that response is preferred by
 * callers and this hook stays idle.
 */
export function useLastRun(jobs: Job[] | null): OptimizationRun | null {
  const { lastOptimize } = useSession();
  const runIds = useMemo(
    () => [
      ...new Set(
        (jobs ?? [])
          .map((j) => j.proposal_run_id)
          .filter((x): x is string => !!x),
      ),
    ],
    [jobs],
  );
  const res = useResource<OptimizationRun | null>(
    async () => {
      if (runIds.length === 0) return null;
      const runs = await Promise.all(
        runIds.map((id) => api.run(id).catch(() => null)),
      );
      const ok = runs.filter((r): r is OptimizationRun => !!r);
      ok.sort((a, b) => b.completed_at.localeCompare(a.completed_at));
      return ok[0] ?? null;
    },
    [runIds.join("|")],
    !lastOptimize && jobs !== null,
  );
  return res.data;
}
