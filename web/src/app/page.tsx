import { PortfolioDashboard } from "@/components/portfolio-dashboard";
import { getAccountOverview, getCandidates } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function PortfolioPage() {
  const [overview, candidates] = await Promise.all([getAccountOverview(), getCandidates()]);
  return <PortfolioDashboard initialOverview={overview} initialCandidates={candidates} />;
}
