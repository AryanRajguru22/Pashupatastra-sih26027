import { fetchOptimizationData } from "@/lib/data";
import DashboardClient from "@/components/DashboardClient";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  const data = await fetchOptimizationData();
  return <DashboardClient initialData={data} />;
}
