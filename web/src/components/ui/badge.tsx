import type { ReactNode } from "react";

type BadgeTone = "neutral" | "long" | "short" | "warning" | "ai";

const TONE_CLASSES: Record<BadgeTone, string> = {
  neutral: "bg-secondary text-muted-foreground border-border",
  long: "bg-long/10 text-long border-long/30",
  short: "bg-short/10 text-short border-short/30",
  warning: "bg-warning/10 text-warning border-warning/30",
  ai: "bg-ai-accent/10 text-ai-accent border-ai-accent/30",
};

export function Badge({ tone = "neutral", children }: { tone?: BadgeTone; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs font-medium tracking-wide ${TONE_CLASSES[tone]}`}
    >
      {children}
    </span>
  );
}

// Pinned in the app header on every view -- see CLAUDE.md-adjacent product
// requirement: never let a monetary/performance figure read as real money.
export function SimulatedBadge() {
  return (
    <span className="inline-flex items-center gap-1.5 rounded border border-warning/40 bg-warning/10 px-2.5 py-1 text-xs font-semibold uppercase tracking-wider text-warning">
      <span className="h-1.5 w-1.5 rounded-full bg-warning" />
      Simulated — Paper Trading
    </span>
  );
}
