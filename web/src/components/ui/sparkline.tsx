"use client";

import { Line, LineChart, ResponsiveContainer, YAxis } from "recharts";

interface SparklineProps {
  data: number[];
  color?: string;
  height?: number;
}

// A bare trend line with no axes/grid/tooltip -- per the ui-ux-pro-max
// chart-domain guidance, a sparkline's job is to show shape at a glance
// inside a compact card, not to be a full interactive chart.
export function Sparkline({ data, color = "var(--color-muted-foreground)", height = 32 }: SparklineProps) {
  if (data.length < 2) {
    return <div style={{ height }} className="flex items-center text-xs text-muted-foreground">—</div>;
  }

  const points = data.map((value, index) => ({ index, value }));
  const [min, max] = [Math.min(...data), Math.max(...data)];
  const padding = (max - min) * 0.1 || 1;

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={points} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
        <YAxis domain={[min - padding, max + padding]} hide />
        <Line type="monotone" dataKey="value" stroke={color} strokeWidth={1.5} dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}
