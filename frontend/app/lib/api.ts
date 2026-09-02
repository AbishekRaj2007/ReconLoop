import type { ExceptionListResponse, ReconcileResponse, ScoreResponse } from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    throw new Error(`${path} failed: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export function fetchScore(): Promise<ScoreResponse> {
  return apiFetch<ScoreResponse>("/score");
}

export function fetchExceptions(page = 1, pageSize = 100): Promise<ExceptionListResponse> {
  return apiFetch<ExceptionListResponse>(`/exceptions?page=${page}&page_size=${pageSize}`);
}

export function triggerReconcile(useLlm: boolean): Promise<ReconcileResponse> {
  return apiFetch<ReconcileResponse>("/reconcile", {
    method: "POST",
    body: JSON.stringify({ use_llm: useLlm }),
  });
}
