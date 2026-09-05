import { fetchOptimizationData } from "@/lib/data";
import DashboardClient from "@/components/DashboardClient";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  const data = await fetchOptimizationData();

  const isLive = data.dataSource === "LIVE_API";
  const solveTimeMs = Math.round((data.result.solve_time_seconds || 0) * 1000);

  return (
    <main className="flex-1 flex flex-col min-h-screen">
      {/* Top Header Bar */}
      <header className="flex flex-wrap items-center justify-between gap-3 px-6 py-3 border-b border-[#1e293b] bg-[#0c1120]">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <div
              className={`w-2.5 h-2.5 rounded-full ${
                isLive ? "bg-emerald-500 animate-pulse" : "bg-amber-400"
              }`}
            />
            <h1 className="text-base font-semibold tracking-tight text-[#e2e8f0]">
              PASHUPATASTRA
            </h1>
          </div>
          <span className="text-[11px] text-[#475569] border-l border-[#1e293b] pl-3 font-mono">
            BLOCK PLANNING COMMAND CENTER
          </span>
          <span
            className={`text-[10px] font-mono px-2 py-0.5 rounded border ${
              isLive
                ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/30 font-semibold"
                : "bg-amber-500/10 text-amber-400 border-amber-500/30"
            }`}
          >
            {isLive ? "LIVE BACKEND (FastAPI)" : "FIXTURE DEMO MODE"}
          </span>
        </div>

        <div className="flex items-center gap-4 text-[11px] font-mono text-[#64748b]">
          <span>
            CORRIDOR:{" "}
            <span className="text-[#94a3b8] font-bold">
              {data.result.corridor_id}
            </span>
          </span>
          <span>
            HORIZON:{" "}
            <span className="text-[#94a3b8]">
              {data.request.horizon_minutes || 1440}m
            </span>
          </span>
          <span>
            SOLVER:{" "}
            <span className="text-[#94a3b8]">
              {solveTimeMs}ms
            </span>
          </span>
        </div>
      </header>

      {/* Dashboard Body */}
      <DashboardClient initialData={data} />
    </main>
  );
}
