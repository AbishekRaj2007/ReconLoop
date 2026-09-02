"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const TIER_LABELS: Record<string, string> = {
  TIER_1: "Tier 1 · Exact",
  TIER_2: "Tier 2 · Fuzzy",
  TIER_3: "Tier 3 · Fast LLM",
  TIER_4: "Tier 4 · Strong LLM",
};

export default function TierChart({ tierAttribution }: { tierAttribution: Record<string, number> }) {
  const data = Object.entries(tierAttribution)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([tier, share]) => ({
      tier: TIER_LABELS[tier] ?? tier,
      percent: Number((share * 100).toFixed(1)),
    }));

  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
      <p className="text-sm font-medium text-zinc-500 dark:text-zinc-400">Match attribution by tier</p>
      <div className="mt-4 h-64">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ left: 12, right: 24 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="currentColor" className="text-zinc-200 dark:text-zinc-800" horizontal={false} />
            <XAxis type="number" domain={[0, 100]} unit="%" stroke="currentColor" className="text-zinc-500" fontSize={12} />
            <YAxis type="category" dataKey="tier" width={120} stroke="currentColor" className="text-zinc-500" fontSize={12} />
            <Tooltip
              formatter={(value) => [`${value}%`, "Records"]}
              contentStyle={{ background: "var(--background)", border: "1px solid #d4d4d8", borderRadius: 8, fontSize: 12 }}
            />
            <Bar dataKey="percent" fill="#3b82f6" radius={[0, 4, 4, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
