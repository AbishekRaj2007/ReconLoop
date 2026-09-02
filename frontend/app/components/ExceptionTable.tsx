"use client";

import { useMemo, useState } from "react";
import type { ExceptionOut } from "../lib/types";

export default function ExceptionTable({ exceptions }: { exceptions: ExceptionOut[] }) {
  const [typeFilter, setTypeFilter] = useState<string>("ALL");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const types = useMemo(
    () => Array.from(new Set(exceptions.map((e) => e.exception_type))).sort(),
    [exceptions]
  );

  const filtered =
    typeFilter === "ALL" ? exceptions : exceptions.filter((e) => e.exception_type === typeFilter);

  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex items-center justify-between gap-4">
        <p className="text-sm font-medium text-zinc-500 dark:text-zinc-400">
          Exceptions ({filtered.length})
        </p>
        <select
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          className="rounded-md border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-700 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200"
        >
          <option value="ALL">All types</option>
          {types.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </div>

      {filtered.length === 0 ? (
        <p className="mt-4 text-sm text-zinc-500 dark:text-zinc-400">No exceptions of this type.</p>
      ) : (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[560px] text-left text-sm">
            <thead>
              <tr className="border-b border-zinc-200 text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
                <th className="py-2 pr-4 font-medium">Ledger ID</th>
                <th className="py-2 pr-4 font-medium">Type</th>
                <th className="py-2 pr-4 font-medium">Affected IDs</th>
                <th className="py-2 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((exc) => {
                const isOpen = expandedId === exc.id;
                return (
                  <>
                    <tr
                      key={exc.id}
                      className="cursor-pointer border-b border-zinc-100 hover:bg-zinc-50 dark:border-zinc-800 dark:hover:bg-zinc-800/50"
                      onClick={() => setExpandedId(isOpen ? null : exc.id)}
                    >
                      <td className="py-2 pr-4 font-mono text-xs text-zinc-700 dark:text-zinc-300">
                        {exc.ledger_id}
                      </td>
                      <td className="py-2 pr-4">
                        <span className="rounded-full bg-zinc-100 px-2 py-0.5 text-xs font-medium text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
                          {exc.exception_type}
                        </span>
                      </td>
                      <td className="py-2 pr-4 font-mono text-xs text-zinc-500 dark:text-zinc-400">
                        {exc.affected_ids.length}
                      </td>
                      <td className="py-2 text-right text-xs text-zinc-400">
                        {isOpen ? "Hide" : "Details"}
                      </td>
                    </tr>
                    {isOpen && (
                      <tr key={`${exc.id}-detail`} className="border-b border-zinc-100 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-800/30">
                        <td colSpan={4} className="px-2 py-3 text-sm text-zinc-700 dark:text-zinc-300">
                          <p>
                            <span className="font-medium">Rationale: </span>
                            {exc.rationale}
                          </p>
                          <p className="mt-1">
                            <span className="font-medium">Suggested action: </span>
                            {exc.suggested_action}
                          </p>
                          <p className="mt-1 font-mono text-xs text-zinc-500 dark:text-zinc-400">
                            Affected: {exc.affected_ids.join(", ")}
                          </p>
                        </td>
                      </tr>
                    )}
                  </>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
