"use client";

import { useCallback, useState } from "react";

import { getAccountOverview, getCandidates, resetAccount, runEndOfDay } from "@/lib/api";
import { formatSignedPct, formatUsd } from "@/lib/format";
import type { AccountOverviewResponse, CandidatesResponse } from "@/lib/types";

import { CandidateCard } from "./candidate-card";
import { PositionCard } from "./position-card";
import { DepositForm } from "./deposit-form";
import { StatTile, StatTileGrid } from "./ui/stat-tile";

interface PortfolioDashboardProps {
  initialOverview: AccountOverviewResponse;
  initialCandidates: CandidatesResponse;
}

export function PortfolioDashboard({ initialOverview, initialCandidates }: PortfolioDashboardProps) {
  const [overview, setOverview] = useState(initialOverview);
  const [candidates, setCandidates] = useState(initialCandidates);
  const [refreshing, setRefreshing] = useState(false);
  const [eodRunning, setEodRunning] = useState(false);
  const [resetting, setResetting] = useState(false);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const [nextOverview, nextCandidates] = await Promise.all([getAccountOverview(), getCandidates()]);
      setOverview(nextOverview);
      setCandidates(nextCandidates);
    } finally {
      setRefreshing(false);
    }
  }, []);

  async function handleRunEndOfDay() {
    setEodRunning(true);
    try {
      await runEndOfDay();
      await refresh();
    } finally {
      setEodRunning(false);
    }
  }

  async function handleReset() {
    if (!window.confirm("Reset the entire virtual account? This clears all deposits, positions, and performance history.")) {
      return;
    }
    setResetting(true);
    try {
      await resetAccount();
      await refresh();
    } finally {
      setResetting(false);
    }
  }

  const { balance, positions, circuit_breaker: circuitBreaker } = overview;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-mono text-lg font-semibold">Portfolio</h1>
          <p className="text-xs text-muted-foreground">
            {overview.reconciled_today ? `Reconciled through ${overview.latest_snapshot_date}` : "Not yet reconciled today"}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <DepositForm onDeposited={refresh} />
          <button
            onClick={handleRunEndOfDay}
            disabled={eodRunning}
            className="rounded border border-border bg-secondary px-3 py-1.5 text-xs font-medium hover:bg-secondary/60 disabled:opacity-50"
          >
            {eodRunning ? "Running…" : "Run End of Day"}
          </button>
          <button
            onClick={handleReset}
            disabled={resetting}
            className="rounded border border-short/40 px-3 py-1.5 text-xs font-medium text-short hover:bg-short/10 disabled:opacity-50"
          >
            {resetting ? "Resetting…" : "Reset Account"}
          </button>
        </div>
      </div>

      {circuitBreaker?.tripped && (
        <div className="rounded-lg border border-short/40 bg-short/5 p-3 text-xs text-short">
          <span className="font-semibold uppercase tracking-wider">Circuit breaker halted — </span>
          {circuitBreaker.reason}. New executes are blocked; closing positions is still allowed.
        </div>
      )}

      <StatTileGrid>
        <StatTile label="Total Deposited" value={formatUsd(balance.total_deposited)} simulated />
        <StatTile label="Free Cash" value={formatUsd(balance.free_cash)} simulated />
        <StatTile label="Cash Tied Up" value={formatUsd(balance.cash_tied_up)} sublabel={`${positions.open.length} open position(s)`} />
        <StatTile
          label="Realized P&L"
          value={formatSignedPct(balance.total_deposited ? balance.realized_pnl / balance.total_deposited : 0, 2)}
          sign={balance.realized_pnl > 0 ? "positive" : balance.realized_pnl < 0 ? "negative" : "neutral"}
          sublabel={formatUsd(balance.realized_pnl)}
        />
      </StatTileGrid>

      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Today&rsquo;s Candidates
          </h2>
          <span className="text-[11px] text-muted-foreground">as of {candidates.as_of}</span>
        </div>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <div className="flex flex-col gap-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-long">
              Long candidates ({candidates.longs.length})
            </span>
            {candidates.longs.map((candidate) => (
              <CandidateCard key={candidate.ticker} candidate={candidate} freeCash={balance.free_cash} onExecuted={refresh} />
            ))}
          </div>
          <div className="flex flex-col gap-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-short">
              Short candidates ({candidates.shorts.length})
            </span>
            {candidates.shorts.map((candidate) => (
              <CandidateCard key={candidate.ticker} candidate={candidate} freeCash={balance.free_cash} onExecuted={refresh} />
            ))}
          </div>
        </div>
      </section>

      <section className="flex flex-col gap-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Open Positions ({positions.open.length})
        </h2>
        {positions.open.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border p-4 text-xs text-muted-foreground">
            No open positions. Execute a candidate above to get started.
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
            {positions.open.map((position) => (
              <PositionCard key={position.position_id} position={position} onClosed={refresh} />
            ))}
          </div>
        )}
      </section>

      {refreshing && <p className="text-[11px] text-muted-foreground">Refreshing…</p>}
    </div>
  );
}
