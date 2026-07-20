// Compact div-based bar for a single factor z-score inside a table cell --
// deliberately not a Recharts chart, since a table with 8 tiny bar charts
// per row would be both heavier and harder to align than plain CSS.
// Clamped to +/-3 (a typical sector-neutral z-score range) so one outlier
// doesn't compress every other bar to invisible.
const CLAMP = 3;

export function FactorBar({ value }: { value: number | null }) {
  if (value === null) {
    return <span className="text-xs text-muted-foreground">—</span>;
  }

  const clamped = Math.max(-CLAMP, Math.min(CLAMP, value));
  const widthPct = (Math.abs(clamped) / CLAMP) * 50;
  const isPositive = value >= 0;

  return (
    <div className="flex items-center gap-1.5">
      <div className="relative h-3 w-16 overflow-hidden rounded-sm bg-secondary">
        <div className="absolute inset-y-0 left-1/2 w-px bg-border" />
        <div
          className={`absolute inset-y-0 ${isPositive ? "bg-long" : "bg-short"}`}
          style={
            isPositive
              ? { left: "50%", width: `${widthPct}%` }
              : { right: "50%", width: `${widthPct}%` }
          }
        />
      </div>
      <span className="w-9 font-mono text-[11px] tabular-nums text-muted-foreground">{value.toFixed(2)}</span>
    </div>
  );
}
