import { CrowdingChart } from "@/components/crowding-chart";
import { Badge } from "@/components/ui/badge";
import { FactorBar } from "@/components/ui/factor-bar";
import { getFactors } from "@/lib/api";
import { formatPct } from "@/lib/format";

export const dynamic = "force-dynamic";

const FACTOR_ORDER = [
  "momentum",
  "value",
  "quality",
  "growth",
  "estimate_revisions",
  "insider_activity",
  "institutional_flow",
  "congressional",
];

export default async function FactorsPage() {
  const factors = await getFactors();
  const holdings = [...factors.holdings].sort((a, b) => (b.composite_score ?? -Infinity) - (a.composite_score ?? -Infinity));

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="font-mono text-lg font-semibold">Factor Research</h1>
        <p className="text-xs text-muted-foreground">As of {factors.as_of} · per-stock sector-neutral z-scores</p>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[2fr_1fr]">
        <section className="rounded-lg border border-border bg-card p-4">
          <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Factor Crowding
          </h2>
          <div className="mb-3 flex items-center gap-2">
            <Badge tone={factors.crowding.crowded ? "warning" : "long"}>
              {factors.crowding.crowded ? "Crowded" : "Not crowded"}
            </Badge>
            {factors.crowding.dominant_factor && (
              <span className="text-xs text-muted-foreground">
                dominant factor: <span className="font-mono text-foreground">{factors.crowding.dominant_factor}</span>
              </span>
            )}
            <span className="text-xs text-muted-foreground">
              threshold {formatPct(factors.crowding.threshold, 0)}
              {factors.crowding.num_tickers_used ? ` · ${factors.crowding.num_tickers_used} tickers` : ""}
            </span>
          </div>
          <CrowdingChart contributions={factors.crowding.contributions} dominantFactor={factors.crowding.dominant_factor} />
        </section>

        <section className="rounded-lg border border-border bg-card p-4">
          <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Factor Coverage
          </h2>
          <div className="flex flex-col gap-2">
            {FACTOR_ORDER.map((name) => {
              const coverage = factors.coverage[name];
              const dropped = factors.dropped_factors.includes(name);
              return (
                <div key={name} className="flex items-center justify-between text-xs">
                  <span className={`font-mono ${dropped ? "text-muted-foreground line-through" : ""}`}>{name}</span>
                  <span className={dropped ? "text-warning" : "text-muted-foreground"}>
                    {coverage !== undefined ? formatPct(coverage, 0) : "n/a"}
                  </span>
                </div>
              );
            })}
          </div>
          {factors.dropped_factors.length > 0 && (
            <p className="mt-3 text-[11px] text-muted-foreground">
              Struck-through factors were dropped from this period&rsquo;s composite for thin coverage and had their
              weight renormalized across the rest.
            </p>
          )}
        </section>
      </div>

      <section className="overflow-x-auto rounded-lg border border-border bg-card">
        <table className="w-full min-w-[900px] border-collapse text-xs">
          <thead>
            <tr className="border-b border-border text-left text-muted-foreground">
              <th className="px-3 py-2 font-medium">Ticker</th>
              <th className="px-3 py-2 font-medium">Sector</th>
              <th className="px-3 py-2 font-medium">Composite</th>
              <th className="px-3 py-2 font-medium">Rank</th>
              {FACTOR_ORDER.map((name) => (
                <th key={name} className="px-3 py-2 font-medium">
                  {name.replace("_", " ")}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {holdings.map((holding) => (
              <tr key={holding.ticker} className="border-b border-border/60 last:border-0">
                <td className="px-3 py-2 font-mono font-semibold">{holding.ticker}</td>
                <td className="px-3 py-2 text-muted-foreground">{holding.sector ?? "—"}</td>
                <td
                  className={`px-3 py-2 font-mono tabular-nums ${
                    (holding.composite_score ?? 0) >= 0 ? "text-long" : "text-short"
                  }`}
                >
                  {holding.composite_score !== null ? holding.composite_score.toFixed(2) : "—"}
                </td>
                <td className="px-3 py-2 font-mono tabular-nums text-muted-foreground">
                  {holding.rank !== null ? formatPct(holding.rank, 0) : "—"}
                </td>
                {FACTOR_ORDER.map((name) => (
                  <td key={name} className="px-3 py-2">
                    <FactorBar value={holding.factors[name] ?? null} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
