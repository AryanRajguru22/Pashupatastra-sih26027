import { fetchOptimizationData } from "@/lib/data";
import DashboardClient from "@/components/DashboardClient";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  const data = await fetchOptimizationData();

  return (
    <main className="flex-1 flex flex-col min-h-screen relative">
      <div
        className="fixed inset-0 pointer-events-none z-0"
        style={{
          background:
            "radial-gradient(ellipse 80% 50% at 50% 0%, rgba(45,212,221,0.05) 0%, transparent 60%)",
        }}
      />
      <div className="relative z-10 flex-1 flex flex-col">
        <DashboardClient initialData={data} />
      </div>
    </main>
  );
}
