import { Badge } from "@/components/ui/badge";
import { getPositions } from "@/lib/api";
import { formatNumber, formatSignedUsd, formatUsd } from "@/lib/format";
import type { Position } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function ExecutionPage() {
  const { open, closed } = await getPositions();
  const positions = [...open, ...closed];

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="font-mono text-lg font-semibold">Simulated Execution Log</h1>
        <p className="text-xs text-muted-foreground">
          {open.length} open · {closed.length} closed · every row is a position you actually executed
        </p>
      </div>

      <section className="overflow-x-auto rounded-lg border border-border bg-card">
        <table className="w-full min-w-[900px] border-collapse text-xs">
          <thead>
            <tr className="border-b border-border text-left text-muted-foreground">
              <th className="px-3 py-2 font-medium">Ticker</th>
              <th className="px-3 py-2 font-medium">Side</th>
              <th className="px-3 py-2 font-medium">Status</th>
              <th className="px-3 py-2 font-medium">Entry</th>
              <th className="px-3 py-2 font-medium text-right">Entry price</th>
              <th className="px-3 py-2 font-medium">Exit</th>
              <th className="px-3 py-2 font-medium text-right">Exit price</th>
              <th className="px-3 py-2 font-medium text-right">Shares</th>
              <th className="px-3 py-2 font-medium text-right">Cost basis</th>
              <th className="px-3 py-2 font-medium text-right">P&amp;L</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((position) => (
              <PositionRow key={position.position_id} position={position} />
            ))}
          </tbody>
        </table>
        {positions.length === 0 && (
          <p className="p-6 text-center text-xs text-muted-foreground">
            No executed positions yet. Positions appear here once you execute a candidate from the Portfolio view.
          </p>
        )}
      </section>
    </div>
  );
}

function PositionRow({ position }: { position: Position }) {
  const pnl = position.status === "closed" ? position.realized_pnl : position.unrealized_pnl;
  const sideColor = position.side === "long" ? "text-long" : "text-short";

  return (
    <tr className="border-b border-border/60 font-mono last:border-0">
      <td className="px-3 py-2 font-semibold">{position.ticker}</td>
      <td className={`px-3 py-2 ${sideColor}`}>{position.side}</td>
      <td className="px-3 py-2">
        <Badge tone={position.status === "open" ? "long" : "neutral"}>{position.status}</Badge>
      </td>
      <td className="px-3 py-2 text-muted-foreground">{position.entry_date}</td>
      <td className="px-3 py-2 text-right text-muted-foreground">{formatUsd(position.entry_price)}</td>
      <td className="px-3 py-2 text-muted-foreground">{position.exit_date ?? "—"}</td>
      <td className="px-3 py-2 text-right text-muted-foreground">
        {position.exit_price !== null ? formatUsd(position.exit_price) : "—"}
      </td>
      <td className="px-3 py-2 text-right">{formatNumber(position.shares, 2)}</td>
      <td className="px-3 py-2 text-right text-muted-foreground">{formatUsd(position.cost_basis)}</td>
      <td className={`px-3 py-2 text-right ${pnl !== null && pnl >= 0 ? "text-long" : "text-short"}`}>
        {pnl !== null ? formatSignedUsd(pnl) : "—"}
      </td>
    </tr>
  );
}
