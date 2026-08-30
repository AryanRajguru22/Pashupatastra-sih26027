"use client";

import { useState } from "react";
import type { DashboardData } from "@/types/contracts";
import Timeline from "@/components/Timeline";
import KpiSummaryBar from "@/components/KpiSummaryBar";
import ExplainabilityPanel from "@/components/ExplainabilityPanel";

interface DashboardClientProps {
  data: DashboardData;
}

export default function DashboardClient({ data }: DashboardClientProps) {
  const [selectedBlockId, setSelectedBlockId] = useState<string | null>(null);

  const handleBlockSelect = (blockId: string | null) => {
    setSelectedBlockId(blockId);
  };

  return (
    <div className="flex-1 flex flex-col px-6 py-4 gap-5">
      {/* Stage 2: KPI Summary Bar */}
      <section aria-label="Operational KPI Summary">
        <KpiSummaryBar data={data} />
      </section>

      {/* Main Operations Grid: Timeline (left) + Explainability Panel (right) */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 items-start">
        {/* Timeline Column */}
        <div className="lg:col-span-8 xl:col-span-8 flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <h2 className="text-sm font-semibold text-[#cbd5e1] uppercase tracking-wider">
                Schedule Timeline
              </h2>
              <span className="text-[11px] font-mono text-[#475569] bg-[#0f1520] border border-[#1e293b] rounded px-2 py-0.5">
                {new Date(data.request.planning_horizon.start).toLocaleDateString(
                  "en-IN",
                  {
                    weekday: "short",
                    day: "2-digit",
                    month: "short",
                    year: "numeric",
                  }
                )}
                {"  "}
                {new Date(data.request.planning_horizon.start).toLocaleTimeString(
                  "en-IN",
                  { hour: "2-digit", minute: "2-digit", hour12: false }
                )}
                {" → "}
                {new Date(data.request.planning_horizon.end).toLocaleTimeString(
                  "en-IN",
                  { hour: "2-digit", minute: "2-digit", hour12: false }
                )}
              </span>
            </div>
            {selectedBlockId && (
              <span className="text-[11px] font-mono text-blue-400 bg-blue-500/10 border border-blue-500/30 px-2 py-0.5 rounded">
                Active Inspect: {selectedBlockId}
              </span>
            )}
          </div>

          <div className="bg-[#0a0e17] border border-[#1e293b] rounded-lg p-4">
            <Timeline
              data={data}
              onBlockSelect={(id) =>
                handleBlockSelect(selectedBlockId === id ? null : id)
              }
              selectedBlockId={selectedBlockId}
            />
          </div>
        </div>

        {/* Explainability & Decision Audit Column */}
        <div className="lg:col-span-4 xl:col-span-4 flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-[#cbd5e1] uppercase tracking-wider">
              Decision Audit
            </h2>
            <span className="text-[11px] font-mono text-[#64748b]">
              CP-SAT + ML Audit Trail
            </span>
          </div>

          <div className="h-[560px]">
            <ExplainabilityPanel
              data={data}
              selectedBlockId={selectedBlockId}
              onSelectBlock={handleBlockSelect}
            />
          </div>
        </div>
      </div>

      {/* Summary footer */}
      <div className="flex items-center gap-6 text-[11px] font-mono text-[#64748b] px-1">
        <span>
          SCHEDULED:{" "}
          <span className="text-emerald-400 font-semibold">
            {data.result.scheduled_blocks.length}
          </span>
        </span>
        <span>
          REJECTED:{" "}
          <span className="text-red-400 font-semibold">
            {data.result.unscheduled_blocks.length}
          </span>
        </span>
        <span>
          TOTAL CANDIDATES:{" "}
          <span className="text-[#94a3b8]">
            {data.request.block_candidates.length}
          </span>
        </span>
        <span>
          STATUS:{" "}
          <span
            className={
              data.result.status === "OPTIMAL" ||
              data.result.status === "FEASIBLE"
                ? "text-emerald-400 font-semibold"
                : "text-red-400 font-semibold"
            }
          >
            {data.result.status}
          </span>
        </span>
      </div>
    </div>
  );
}
