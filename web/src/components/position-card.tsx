"use client";

import { useState } from "react";

import { closePosition } from "@/lib/api";
import { formatPct, formatSignedUsd, formatUsd } from "@/lib/format";
import type { Position } from "@/lib/types";

export function PositionCard({ position, onClosed }: { position: Position; onClosed: () => void }) {
  const [closing, setClosing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const pnl = position.unrealized_pnl ?? 0;
  const pnlPositive = pnl >= 0;
  const sideColor = position.side === "long" ? "text-long" : "text-short";

  async function handleClose() {
    setClosing(true);
    setError(null);
    try {
      await closePosition(position.position_id);
      onClosed();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Close failed");
      setClosing(false);
    }
  }

  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-mono text-sm font-semibold">{position.ticker}</span>
            <span className={`font-mono text-xs ${sideColor}`}>{position.side}</span>
          </div>
          <div className="mt-0.5 flex items-center gap-3 font-mono text-xs text-muted-foreground">
            <span>entry {formatUsd(position.entry_price)}</span>
            <span>opened {position.entry_date}</span>
            <span>{formatUsd(position.cost_basis, { compact: true })} basis</span>
          </div>
        </div>
        <div className="text-right">
          <div className={`font-mono text-sm font-semibold ${pnlPositive ? "text-long" : "text-short"}`}>
            {formatSignedUsd(pnl)}
          </div>
          {position.position_return_pct !== null && (
            <div className={`font-mono text-[11px] ${pnlPositive ? "text-long" : "text-short"}`}>
              {formatPct(position.position_return_pct, 1)}
            </div>
          )}
        </div>
      </div>

      {position.excess_return_pct !== null && (
        <div className="mt-1.5 text-[11px] text-muted-foreground">
          vs SPY (since entry): {position.excess_return_pct >= 0 ? "+" : ""}
          {formatPct(position.excess_return_pct, 1)}
          {position.mark_date && ` · as of ${position.mark_date} close`}
        </div>
      )}
      {position.mark_date === null && (
        <div className="mt-1.5 text-[11px] text-muted-foreground">Not yet marked — run end of day to update.</div>
      )}

      <div className="mt-2 flex items-center gap-2">
        <button
          onClick={handleClose}
          disabled={closing}
          className="rounded border border-border px-3 py-1 text-xs font-medium text-muted-foreground hover:bg-secondary disabled:opacity-50"
        >
          {closing ? "Closing…" : "Close position"}
        </button>
        {error && <span className="text-[11px] text-short">{error}</span>}
      </div>
    </div>
  );
}
