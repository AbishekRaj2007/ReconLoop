export interface ScoreResponse {
  run_id: string;
  created_at: string;
  total_records: number;
  matched: number;
  exception_count: number;
  precision: number;
  recall: number;
  f1: number;
  false_match_rate: number;
  missed_match_rate: number;
  tier_attribution: Record<string, number>;
  wall_clock_seconds: number;
  records_per_second: number;
  used_llm: boolean;
  total_llm_calls: number;
  total_llm_cost_usd: number;
}

export interface ExceptionOut {
  id: string;
  run_id: string;
  ledger_id: string;
  exception_type: string;
  affected_ids: string[];
  rationale: string;
  suggested_action: string;
}

export interface ExceptionListResponse {
  total: number;
  page: number;
  page_size: number;
  items: ExceptionOut[];
}

export interface ReconcileResponse {
  run_id: string;
  total_records: number;
  matched: number;
  pending_llm_count: number;
  exception_count: number;
  wall_clock_seconds: number;
  records_per_second: number;
  used_llm: boolean;
  total_llm_calls: number;
  total_llm_cost_usd: number;
}
