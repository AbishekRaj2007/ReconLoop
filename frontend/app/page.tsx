"use client";

import { fetchScore } from "./lib/api";
import { useAsyncResource } from "./lib/useAsync";
import SummaryCards from "./components/SummaryCards";
import TierChart from "./components/TierChart";

export default function Home() {
  const { data: score, error, loading } = useAsyncResource(fetchScore);

  return (
    <div className="min-h-full bg-zinc-50 dark:bg-black">
      <main className="mx-auto flex max-w-6xl flex-col gap-8 px-6 py-10">
        <header>
          <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            ReconLoop
          </h1>
          <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
            Multi-source reconciliation — ledger, bank, gateway settlement
          </p>
        </header>

        {loading && (
          <p className="text-sm text-zinc-500 dark:text-zinc-400">Loading latest run&hellip;</p>
        )}

        {error && !loading && (
          <div className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-800 dark:border-red-800 dark:bg-red-950/40 dark:text-red-300">
            {score === null
              ? "No reconciliation run found yet. Trigger a run to see results."
              : `Failed to load: ${error}`}
          </div>
        )}

        {score && (
          <>
            <SummaryCards score={score} />
            <TierChart tierAttribution={score.tier_attribution} />
          </>
        )}
      </main>
    </div>
  );
}
