import { Badge } from "@/components/ui/badge";
import { getRisk } from "@/lib/api";
import { formatPct, formatSignedPct } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function RiskPage() {
  const risk = await getRisk();
  const cb = risk.circuit_breaker;
  const decomp = risk.decomposition;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-mono text-lg font-semibold">Risk Controls</h1>
          <p className="text-xs text-muted-foreground">As of {risk.as_of} · analyzes your actual open positions</p>
        </div>
        <Badge tone={risk.has_open_positions ? "long" : "neutral"}>
          {risk.has_open_positions ? "Analyzing open positions" : "No open positions"}
        </Badge>
      </div>

      {cb ? (
        <section
          className={`flex items-center gap-4 rounded-lg border p-4 ${
            cb.status === "HALTED" ? "border-short/40 bg-short/5" : "border-long/40 bg-long/5"
          }`}
        >
          <span className="relative flex h-3 w-3 shrink-0">
            <span
              className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-60 ${
                cb.status === "HALTED" ? "bg-short" : "bg-long"
              }`}
            />
            <span className={`relative inline-flex h-3 w-3 rounded-full ${cb.status === "HALTED" ? "bg-short" : "bg-long"}`} />
          </span>
          <div className="flex-1">
            <div className="flex items-center gap-2">
              <span className={`font-mono text-sm font-semibold ${cb.status === "HALTED" ? "text-short" : "text-long"}`}>
                Circuit Breaker: {cb.status}
              </span>
            </div>
            <p className="mt-0.5 text-xs text-muted-foreground">{cb.reason}</p>
          </div>
          <div className="flex gap-6 font-mono text-xs">
            <div>
              <div className="text-muted-foreground">Daily return</div>
              <div className={cb.latest_daily_return >= 0 ? "text-long" : "text-short"}>
                {formatSignedPct(cb.latest_daily_return)}
              </div>
            </div>
            <div>
              <div className="text-muted-foreground">Drawdown</div>
              <div className={cb.latest_drawdown >= 0 ? "text-long" : "text-short"}>{formatSignedPct(cb.latest_drawdown)}</div>
            </div>
          </div>
        </section>
      ) : (
        <section className="rounded-lg border border-border bg-card p-4 text-xs text-muted-foreground">
          Circuit breaker: not enough end-of-day history yet (needs at least 2 reconciliation runs).
        </section>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <section className="rounded-lg border border-border bg-card p-4">
          <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Factor vs. Specific Risk
          </h2>
          {decomp.factor_pct !== null && decomp.specific_pct !== null ? (
            <>
              <div className="flex h-4 overflow-hidden rounded-sm bg-secondary">
                <div className="bg-ai-accent" style={{ width: `${decomp.factor_pct * 100}%` }} />
                <div className="bg-muted-foreground/40" style={{ width: `${decomp.specific_pct * 100}%` }} />
              </div>
              <div className="mt-2 flex justify-between text-xs">
                <span className="text-ai-accent">Factor {formatPct(decomp.factor_pct, 0)}</span>
                <span className="text-muted-foreground">Specific {formatPct(decomp.specific_pct, 0)}</span>
              </div>
              <p className="mt-3 text-[11px] text-muted-foreground">
                Target: specific risk {decomp.target_specific_pct !== null ? formatPct(decomp.target_specific_pct, 0) : "—"}{" "}
                &plusmn; {decomp.tolerance !== null ? formatPct(decomp.tolerance, 0) : "—"}.{" "}
                {decomp.flagged ? (
                  <span className="text-warning">Outside target band.</span>
                ) : (
                  <span className="text-long">Within target band.</span>
                )}
              </p>
            </>
          ) : (
            <p className="text-xs text-muted-foreground">{decomp.reason}</p>
          )}
        </section>

        <section className="rounded-lg border border-border bg-card p-4">
          <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Correlation Flags (&gt; 0.85)
          </h2>
          {risk.correlation_flags.length === 0 ? (
            <p className="text-xs text-muted-foreground">No position pairs above the correlation threshold.</p>
          ) : (
            <div className="flex flex-col gap-1.5">
              {risk.correlation_flags.map((flag) => (
                <div key={`${flag.ticker_a}-${flag.ticker_b}`} className="flex items-center justify-between text-xs">
                  <span className="font-mono">
                    {flag.ticker_a} &harr; {flag.ticker_b}
                  </span>
                  <span className="font-mono text-warning">{flag.correlation.toFixed(2)}</span>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      <section>
        <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Stress Test — Historical Replay
        </h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {risk.stress_tests.map((scenario) => (
            <div key={scenario.scenario} className="rounded-lg border border-border bg-card p-4">
              <div className="font-mono text-xs font-semibold">{scenario.scenario.replace(/_/g, " ")}</div>
              <div className="text-[11px] text-muted-foreground">
                {scenario.window_start} &rarr; {scenario.window_end}
              </div>
              <div
                className={`mt-2 font-mono text-xl font-semibold ${
                  scenario.portfolio_return >= 0 ? "text-long" : "text-short"
                }`}
              >
                {formatSignedPct(scenario.portfolio_return)}
              </div>
              <div className="mt-1 text-[11px] text-muted-foreground">
                {formatPct(scenario.coverage, 0)} coverage
                {scenario.tickers_missing_data.length > 0 && ` · missing: ${scenario.tickers_missing_data.join(", ")}`}
              </div>
            </div>
          ))}
        </div>
      </section>

      {(Object.keys(risk.position_limit_breaches).length > 0 || Object.keys(risk.sector_limit_breaches).length > 0) && (
        <section className="rounded-lg border border-warning/30 bg-warning/5 p-4">
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-warning">Limit Breaches</h2>
          {Object.keys(risk.position_limit_breaches).length > 0 && (
            <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs">
              {Object.entries(risk.position_limit_breaches).map(([ticker, weight]) => (
                <span key={ticker} className={weight >= 0 ? "text-long" : "text-short"}>
                  {ticker} {formatSignedPct(weight, 1)}
                </span>
              ))}
            </div>
          )}
          {Object.keys(risk.sector_limit_breaches).length > 0 && (
            <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs text-warning">
              {Object.entries(risk.sector_limit_breaches).map(([sector, exposure]) => (
                <span key={sector}>
                  {sector} {formatPct(exposure, 1)}
                </span>
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
