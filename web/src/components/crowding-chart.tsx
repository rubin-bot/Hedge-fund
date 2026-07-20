"use client";

import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

interface CrowdingChartProps {
  contributions: Record<string, number>;
  dominantFactor: string | null;
}

export function CrowdingChart({ contributions, dominantFactor }: CrowdingChartProps) {
  const data = Object.entries(contributions)
    .map(([factor, share]) => ({ factor, share: share * 100 }))
    .sort((a, b) => b.share - a.share);

  if (data.length === 0) {
    return <p className="text-xs text-muted-foreground">No crowding data for the current universe.</p>;
  }

  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, bottom: 4, left: 4 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" horizontal={false} />
        <XAxis
          type="number"
          domain={[0, "dataMax"]}
          tickFormatter={(v: number) => `${v.toFixed(0)}%`}
          tick={{ fill: "var(--color-muted-foreground)", fontSize: 11 }}
          stroke="var(--color-border)"
        />
        <YAxis
          type="category"
          dataKey="factor"
          width={110}
          tick={{ fill: "var(--color-muted-foreground)", fontSize: 11 }}
          stroke="var(--color-border)"
        />
        <Tooltip
          formatter={(value) => `${Number(value).toFixed(1)}% of composite variance`}
          contentStyle={{ background: "var(--color-card)", border: "1px solid var(--color-border)", fontSize: 12 }}
        />
        <Bar dataKey="share" radius={[0, 2, 2, 0]} isAnimationActive={false}>
          {data.map((entry) => (
            <Cell key={entry.factor} fill={entry.factor === dominantFactor ? "var(--color-warning)" : "var(--color-ai-accent)"} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
