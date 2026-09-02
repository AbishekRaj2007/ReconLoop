import type { ScoreResponse } from "../lib/types";

function pct(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

export default function SummaryCards({ score }: { score: ScoreResponse }) {
  const matchRate = score.total_records > 0 ? score.matched / score.total_records : 0;

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <div className="rounded-lg border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
        <p className="text-sm font-medium text-zinc-500 dark:text-zinc-400">Match rate</p>
        <p className="mt-1 text-3xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          {pct(matchRate)}
        </p>
        <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
          {score.matched} / {score.total_records} records &middot; F1 {score.f1.toFixed(3)}
        </p>
      </div>

      <div className="rounded-lg border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
        <p className="text-sm font-medium text-zinc-500 dark:text-zinc-400">Throughput</p>
        <p className="mt-1 text-3xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          {score.records_per_second.toFixed(1)}
          <span className="ml-1 text-base font-normal text-zinc-500 dark:text-zinc-400">rec/s</span>
        </p>
        <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
          {score.wall_clock_seconds.toFixed(2)}s total &middot; {score.total_llm_calls} LLM calls &middot; $
          {score.total_llm_cost_usd.toFixed(4)}
        </p>
      </div>

      <div className="rounded-lg border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
        <p className="text-sm font-medium text-zinc-500 dark:text-zinc-400">Exceptions</p>
        <p className="mt-1 text-3xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          {score.exception_count}
        </p>
        <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
          Missed-match rate {pct(score.missed_match_rate)}
        </p>
      </div>

      <div className="rounded-lg border-2 border-amber-300 bg-amber-50 p-5 dark:border-amber-700 dark:bg-amber-950/40">
        <p className="text-sm font-medium text-amber-800 dark:text-amber-300">False-match rate</p>
        <p className="mt-1 text-3xl font-semibold tracking-tight text-amber-900 dark:text-amber-200">
          {pct(score.false_match_rate)}
        </p>
        <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">
          Precision {score.precision.toFixed(3)} &middot; a wrong match is worse than a missed one
        </p>
      </div>
    </div>
  );
}
