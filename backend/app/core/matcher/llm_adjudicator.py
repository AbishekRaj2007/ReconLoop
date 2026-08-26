"""Tier 3 + Tier 4 — LLM adjudication for records Tier 2 couldn't resolve on
its own (plan.md §9 Phase 3, §11.2).

The model never sees raw unprocessed rows in isolation — it gets the ledger
record, its candidate, and pre-computed deltas (the deterministic layer does
the arithmetic; the model does the judgment call) — and must answer with
structured JSON, never free text. A confidence below threshold never forces
a match, at either tier: that's the single most important guardrail here
(CLAUDE.md Golden Rule #2).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol

from pydantic import BaseModel, Field, ValidationError

from app.core.config import LLMTierConfig, MatchingConfig
from app.core.matcher.fuzzy_match import score_narration
from app.core.matcher.normalize import normalize_amount_paise, normalize_date


@dataclass(frozen=True)
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int


class LLMClient(Protocol):
    model: str

    def complete(self, prompt: str) -> LLMResponse: ...


@dataclass(frozen=True)
class AdjudicationRequest:
    ledger_id: str
    side: str
    candidate_id: str
    ledger_amount: str
    ledger_date: str
    ledger_narration: str
    candidate_amount: str
    candidate_date: str
    candidate_narration: str
    amount_diff_paise: int
    date_diff_days: int
    narration_similarity: float
    fuzzy_score: float


def build_adjudication_request(
    ledger_row: dict,
    candidate_row: dict,
    side: str,
    id_field: str,
    fuzzy_score: float,
) -> AdjudicationRequest:
    ledger_paise = normalize_amount_paise(ledger_row["amount"])
    candidate_paise = normalize_amount_paise(candidate_row["amount"])
    ledger_date = normalize_date(ledger_row["date"])
    candidate_date = normalize_date(candidate_row["date"])
    return AdjudicationRequest(
        ledger_id=ledger_row["ledger_id"],
        side=side,
        candidate_id=candidate_row[id_field],
        ledger_amount=ledger_row["amount"],
        ledger_date=ledger_row["date"],
        ledger_narration=ledger_row["narration"],
        candidate_amount=candidate_row["amount"],
        candidate_date=candidate_row["date"],
        candidate_narration=candidate_row["narration"],
        amount_diff_paise=abs(ledger_paise - candidate_paise),
        date_diff_days=abs((candidate_date - ledger_date).days),
        narration_similarity=score_narration(
            ledger_row["narration"], candidate_row["narration"]
        ),
        fuzzy_score=fuzzy_score,
    )


def build_prompt(request: AdjudicationRequest) -> str:
    return (
        "You are reconciling financial transactions across a ledger and a "
        f"{request.side} statement. Decide whether the candidate record below "
        "is the same underlying transaction as the ledger record.\n\n"
        f"Ledger record {request.ledger_id}:\n"
        f"  amount: {request.ledger_amount}\n"
        f"  date: {request.ledger_date}\n"
        f"  narration: {request.ledger_narration}\n\n"
        f"Candidate {request.side} record {request.candidate_id}:\n"
        f"  amount: {request.candidate_amount}\n"
        f"  date: {request.candidate_date}\n"
        f"  narration: {request.candidate_narration}\n\n"
        "Pre-computed deltas (already accounted for by the deterministic "
        "matcher — do not recompute):\n"
        f"  amount_diff_paise: {request.amount_diff_paise}\n"
        f"  date_diff_days: {request.date_diff_days}\n"
        f"  narration_similarity: {request.narration_similarity:.1f}\n"
        f"  tier_2_fuzzy_score: {request.fuzzy_score:.1f}\n\n"
        "Respond with strict JSON only, no other text, matching exactly this "
        'shape: {"decision": "MATCH" | "NO_MATCH" | "NEEDS_HUMAN_REVIEW", '
        '"confidence": <0-100 number>, "rationale": "<one sentence>"}.'
    )


class LLMDecision(BaseModel):
    decision: Literal["MATCH", "NO_MATCH", "NEEDS_HUMAN_REVIEW"]
    confidence: float = Field(ge=0, le=100)
    rationale: str


def parse_llm_response(raw_text: str) -> LLMDecision | None:
    try:
        data = json.loads(raw_text)
        return LLMDecision.model_validate(data)
    except (json.JSONDecodeError, ValidationError, TypeError):
        return None


def estimate_cost(
    input_tokens: int, output_tokens: int, tier_config: LLMTierConfig
) -> Decimal:
    return (Decimal(input_tokens) / 1000) * tier_config.cost_per_1k_input_tokens + (
        Decimal(output_tokens) / 1000
    ) * tier_config.cost_per_1k_output_tokens


@dataclass(frozen=True)
class AuditLogEntry:
    tier: str
    model: str
    prompt: str
    raw_response: str
    latency_ms: float
    estimated_cost_usd: Decimal
    decision: str | None
    confidence: float | None


def call_tier(
    client: LLMClient,
    tier_name: str,
    request: AdjudicationRequest,
    tier_config: LLMTierConfig,
    max_retries: int,
) -> tuple[LLMDecision | None, list[AuditLogEntry]]:
    """Call one LLM tier, rejecting and retrying once on malformed output
    (plan.md §11.2). Still malformed after `max_retries` retries -> caller
    treats it as LLM_OUTPUT_UNPARSEABLE."""
    prompt = build_prompt(request)
    audit_log: list[AuditLogEntry] = []

    for _ in range(max_retries + 1):
        start = time.perf_counter()
        response = client.complete(prompt)
        latency_ms = (time.perf_counter() - start) * 1000
        decision = parse_llm_response(response.text)
        audit_log.append(
            AuditLogEntry(
                tier=tier_name,
                model=client.model,
                prompt=prompt,
                raw_response=response.text,
                latency_ms=latency_ms,
                estimated_cost_usd=estimate_cost(
                    response.input_tokens, response.output_tokens, tier_config
                ),
                decision=decision.decision if decision else None,
                confidence=decision.confidence if decision else None,
            )
        )
        if decision is not None:
            return decision, audit_log

    return None, audit_log


def should_escalate_to_tier4(decision: LLMDecision, threshold: float) -> bool:
    """Escalate if the model asked for review, or its own confidence doesn't
    clear the bar — even a MATCH doesn't get to skip this."""
    return decision.decision == "NEEDS_HUMAN_REVIEW" or decision.confidence < threshold


def passes_confidence_gate(decision: LLMDecision, threshold: float) -> bool:
    """A MATCH only stands if confidence clears the threshold. Never forces a
    match for NO_MATCH/NEEDS_HUMAN_REVIEW regardless of confidence."""
    return decision.decision == "MATCH" and decision.confidence >= threshold


@dataclass(frozen=True)
class AdjudicationOutcome:
    ledger_id: str
    side: str
    candidate_id: str
    final_outcome: str
    confidence: float | None
    rationale: str
    tiers_used: list[str]
    audit_log: list[AuditLogEntry]


def adjudicate(
    request: AdjudicationRequest,
    tier3_client: LLMClient,
    tier4_client: LLMClient,
    config: MatchingConfig,
) -> AdjudicationOutcome:
    llm_config = config.llm
    threshold = config.thresholds.tier3_confidence_escalation
    tiers_used = ["TIER_3"]

    decision, audit_log = call_tier(
        tier3_client,
        "TIER_3",
        request,
        llm_config.tier3,
        llm_config.max_malformed_retries,
    )

    if decision is not None and should_escalate_to_tier4(decision, threshold):
        tiers_used.append("TIER_4")
        decision, tier4_audit = call_tier(
            tier4_client,
            "TIER_4",
            request,
            llm_config.tier4,
            llm_config.max_malformed_retries,
        )
        audit_log = audit_log + tier4_audit

    if decision is None:
        return AdjudicationOutcome(
            ledger_id=request.ledger_id,
            side=request.side,
            candidate_id=request.candidate_id,
            final_outcome="LLM_OUTPUT_UNPARSEABLE",
            confidence=None,
            rationale=(
                f"{tiers_used[-1]} returned malformed output "
                f"{llm_config.max_malformed_retries + 1} times in a row."
            ),
            tiers_used=tiers_used,
            audit_log=audit_log,
        )

    if passes_confidence_gate(decision, threshold):
        final_outcome = "MATCH"
    elif decision.decision == "NO_MATCH" and decision.confidence >= threshold:
        final_outcome = "NO_MATCH"
    else:
        final_outcome = "LOW_CONFIDENCE_AMBIGUOUS"

    return AdjudicationOutcome(
        ledger_id=request.ledger_id,
        side=request.side,
        candidate_id=request.candidate_id,
        final_outcome=final_outcome,
        confidence=decision.confidence,
        rationale=decision.rationale,
        tiers_used=tiers_used,
        audit_log=audit_log,
    )
