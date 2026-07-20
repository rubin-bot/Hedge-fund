import type { ReactNode } from "react";

import { Sparkline } from "./sparkline";

interface StatTileProps {
  label: string;
  value: string;
  sublabel?: string;
  sign?: "positive" | "negative" | "neutral";
  sparklineData?: number[];
  simulated?: boolean;
}

const SIGN_CLASSES: Record<NonNullable<StatTileProps["sign"]>, string> = {
  positive: "text-long",
  negative: "text-short",
  neutral: "text-foreground",
};

export function StatTile({ label, value, sublabel, sign = "neutral", sparklineData, simulated }: StatTileProps) {
  const sparklineColor = sign === "positive" ? "var(--color-long)" : sign === "negative" ? "var(--color-short)" : undefined;

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border bg-card p-4">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">{label}</span>
        {simulated && <span className="text-[10px] uppercase tracking-wider text-warning">sim</span>}
      </div>
      <span className={`font-mono text-2xl font-semibold tabular-nums ${SIGN_CLASSES[sign]}`}>{value}</span>
      {sublabel && <span className="text-xs text-muted-foreground">{sublabel}</span>}
      {sparklineData && sparklineData.length > 1 && <Sparkline data={sparklineData} color={sparklineColor} />}
    </div>
  );
}

export function StatTileGrid({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{children}</div>;
}
