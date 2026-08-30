import { fetchOptimizationData } from "@/lib/data";
import DashboardClient from "@/components/DashboardClient";

export default async function DashboardPage() {
  const data = await fetchOptimizationData();

  return (
    <main className="flex-1 flex flex-col min-h-screen">
      {/* Top bar */}
      <header className="flex flex-wrap items-center justify-between gap-3 px-6 py-3 border-b border-[#1e293b] bg-[#0c1120]">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
            <h1 className="text-base font-semibold tracking-tight text-[#e2e8f0]">
              PASHUPATASTRA
            </h1>
          </div>
          <span className="text-[11px] text-[#475569] border-l border-[#1e293b] pl-3 font-mono">
            BLOCK PLANNING COMMAND CENTER
          </span>
          <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-amber-500/10 text-amber-400 border border-amber-500/20">
            STAGE 1–3 FIXTURE DEMO
          </span>
        </div>
        <div className="flex items-center gap-4 text-[11px] font-mono text-[#64748b]">
          <span>
            REQ:{" "}
            <span className="text-[#94a3b8]">{data.result.request_id}</span>
          </span>
          <span>
            CORRIDOR:{" "}
            <span className="text-[#94a3b8]">
              {data.request.corridor_id}
            </span>
          </span>
          <span>
            SOLVED:{" "}
            <span className="text-[#94a3b8]">
              {data.result.solve_time_ms}ms
            </span>
          </span>
        </div>
      </header>

      {/* Dashboard body */}
      <DashboardClient data={data} />
    </main>
  );
}
