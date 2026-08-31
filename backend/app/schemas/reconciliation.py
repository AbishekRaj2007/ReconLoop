"""Pydantic v2 request/response schemas for the reconciliation API
(plan.md §9 Phase 6) — typed boundary in/out, no raw dicts (CLAUDE.md)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ReconcileRequest(BaseModel):
    use_llm: bool = False


class ReconcileResponse(BaseModel):
    run_id: str
    total_records: int
    matched: int
    pending_llm_count: int
    exception_count: int
    wall_clock_seconds: float
    records_per_second: float
    used_llm: bool
    total_llm_calls: int
    total_llm_cost_usd: float


class MatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    ledger_id: str
    bank_ids: list[str]
    gateway_ids: list[str]
    tier: str


class MatchListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[MatchOut]


class ExceptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    ledger_id: str
    exception_type: str
    affected_ids: list[str]
    rationale: str
    suggested_action: str


class ExceptionListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ExceptionOut]


class ScoreResponse(BaseModel):
    run_id: str
    created_at: datetime
    total_records: int
    matched: int
    exception_count: int
    precision: float
    recall: float
    f1: float
    false_match_rate: float
    missed_match_rate: float
    tier_attribution: dict[str, float]
    wall_clock_seconds: float
    records_per_second: float
    used_llm: bool
    total_llm_calls: int
    total_llm_cost_usd: float
