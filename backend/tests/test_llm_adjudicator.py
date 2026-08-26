"""Tests for Phase 3 LLM adjudication (Tier 3 + Tier 4).

Every test uses FakeLLMClient — no real network calls, no API keys needed,
per CLAUDE.md: "Mock LLM clients in unit tests; don't burn API calls in CI."
"""

from dataclasses import dataclass, field
from decimal import Decimal

from app.core.config import (
    AmountTolerance,
    DateWindow,
    FeeSchedule,
    LLMConfig,
    LLMTierConfig,
    MatchingConfig,
    ScoreWeights,
    Thresholds,
)
from app.core.matcher.llm_adjudicator import (
    LLMDecision,
    LLMResponse,
    adjudicate,
    build_adjudication_request,
    build_prompt,
    call_tier,
    estimate_cost,
    parse_llm_response,
    passes_confidence_gate,
    should_escalate_to_tier4,
)


@dataclass
class FakeLLMClient:
    """Returns queued responses in order; records every prompt it receives."""

    model: str
    responses: list[str] = field(default_factory=list)
    prompts_received: list[str] = field(default_factory=list)

    def complete(self, prompt: str) -> LLMResponse:
        self.prompts_received.append(prompt)
        text = self.responses[len(self.prompts_received) - 1]
        return LLMResponse(text=text, input_tokens=1000, output_tokens=1000)


TIER3_PRICING = LLMTierConfig(
    provider="groq",
    model="llama-3.3-70b-versatile",
    cost_per_1k_input_tokens=Decimal("0.00059"),
    cost_per_1k_output_tokens=Decimal("0.00079"),
)
TIER4_PRICING = LLMTierConfig(
    provider="anthropic",
    model="claude-sonnet-4-5",
    cost_per_1k_input_tokens=Decimal("0.003"),
    cost_per_1k_output_tokens=Decimal("0.015"),
)


def make_config() -> MatchingConfig:
    return MatchingConfig(
        score_weights=ScoreWeights(amount=0.40, date=0.35, narration=0.25),
        thresholds=Thresholds(
            auto_match=90, llm_tier=60, tier3_confidence_escalation=70
        ),
        date_window=DateWindow(max_days=5, candidate_search_days=5),
        amount_tolerance=AmountTolerance(
            exact_match_paise=0,
            fee_schedule=FeeSchedule(
                percentage_fee=Decimal("0.02"), gst_on_fee=Decimal("0.18")
            ),
        ),
        llm=LLMConfig(
            tier3=TIER3_PRICING, tier4=TIER4_PRICING, max_malformed_retries=1
        ),
    )


def decision_json(
    decision: str, confidence: float, rationale: str = "Looks consistent."
) -> str:
    return f'{{"decision": "{decision}", "confidence": {confidence}, "rationale": "{rationale}"}}'


def ledger_row() -> dict:
    return {
        "ledger_id": "TXN-000005",
        "date": "2026-06-29",
        "amount": "21295.83",
        "narration": "Payment received from Chandan Logistics - Invoice INV00005",
    }


def candidate_row() -> dict:
    return {
        "bank_id": "1000005",
        "date": "2026-06-30",
        "amount": "21295.83",
        "narration": "NEFT/CL/INV00005",
    }


# --- build_adjudication_request ---


def test_build_adjudication_request_computes_deltas():
    request = build_adjudication_request(
        ledger_row(), candidate_row(), side="bank", id_field="bank_id", fuzzy_score=74.1
    )

    assert request.ledger_id == "TXN-000005"
    assert request.candidate_id == "1000005"
    assert request.side == "bank"
    assert request.amount_diff_paise == 0
    assert request.date_diff_days == 1
    assert request.fuzzy_score == 74.1


# --- build_prompt ---


def test_build_prompt_includes_record_fields_and_output_contract():
    request = build_adjudication_request(
        ledger_row(), candidate_row(), side="bank", id_field="bank_id", fuzzy_score=74.1
    )
    prompt = build_prompt(request)

    assert "TXN-000005" in prompt
    assert "1000005" in prompt
    assert "Chandan Logistics" in prompt
    assert "NEFT/CL/INV00005" in prompt
    assert "MATCH" in prompt
    assert "NO_MATCH" in prompt
    assert "NEEDS_HUMAN_REVIEW" in prompt
    assert "confidence" in prompt
    assert "rationale" in prompt


# --- parse_llm_response ---


def test_parse_llm_response_valid_json():
    decision = parse_llm_response(decision_json("MATCH", 92, "Amount and date align."))

    assert decision is not None
    assert decision.decision == "MATCH"
    assert decision.confidence == 92
    assert decision.rationale == "Amount and date align."


def test_parse_llm_response_rejects_non_json():
    assert parse_llm_response("that's a match, I'm confident") is None


def test_parse_llm_response_rejects_missing_key():
    assert parse_llm_response('{"decision": "MATCH", "confidence": 90}') is None


def test_parse_llm_response_rejects_invalid_decision_value():
    assert parse_llm_response(decision_json("PROBABLY", 90)) is None


def test_parse_llm_response_rejects_out_of_range_confidence():
    assert parse_llm_response(decision_json("MATCH", 150)) is None


# --- estimate_cost ---


def test_estimate_cost_uses_tier_pricing():
    cost = estimate_cost(
        input_tokens=1000, output_tokens=1000, tier_config=TIER3_PRICING
    )

    assert cost == Decimal("0.00059") + Decimal("0.00079")


# --- should_escalate_to_tier4 / passes_confidence_gate ---


def test_escalates_when_model_explicitly_asks_for_review_despite_high_confidence():
    decision = LLMDecision(
        decision="NEEDS_HUMAN_REVIEW", confidence=99, rationale="Unsure."
    )
    assert should_escalate_to_tier4(decision, threshold=70) is True


def test_escalates_when_confidence_below_threshold():
    decision = LLMDecision(decision="MATCH", confidence=50, rationale="Weak signal.")
    assert should_escalate_to_tier4(decision, threshold=70) is True


def test_does_not_escalate_confident_decision():
    decision = LLMDecision(decision="MATCH", confidence=80, rationale="Strong signal.")
    assert should_escalate_to_tier4(decision, threshold=70) is False


def test_confidence_gate_passes_confident_match():
    decision = LLMDecision(decision="MATCH", confidence=80, rationale="Strong signal.")
    assert passes_confidence_gate(decision, threshold=70) is True


def test_confidence_gate_blocks_low_confidence_match():
    decision = LLMDecision(decision="MATCH", confidence=50, rationale="Weak signal.")
    assert passes_confidence_gate(decision, threshold=70) is False


def test_confidence_gate_blocks_no_match_regardless_of_confidence():
    decision = LLMDecision(
        decision="NO_MATCH", confidence=95, rationale="Different invoice."
    )
    assert passes_confidence_gate(decision, threshold=70) is False


# --- call_tier ---


def test_call_tier_parses_valid_first_response():
    request = build_adjudication_request(
        ledger_row(), candidate_row(), side="bank", id_field="bank_id", fuzzy_score=74.1
    )
    client = FakeLLMClient(
        model="llama-3.3-70b-versatile", responses=[decision_json("MATCH", 92)]
    )

    decision, audit_log = call_tier(
        client, "TIER_3", request, TIER3_PRICING, max_retries=1
    )

    assert decision.decision == "MATCH"
    assert len(audit_log) == 1
    entry = audit_log[0]
    assert entry.tier == "TIER_3"
    assert entry.model == "llama-3.3-70b-versatile"
    assert entry.decision == "MATCH"
    assert entry.confidence == 92
    assert entry.estimated_cost_usd == Decimal("0.00059") + Decimal("0.00079")
    assert entry.raw_response == decision_json("MATCH", 92)


def test_call_tier_retries_once_on_malformed_output():
    request = build_adjudication_request(
        ledger_row(), candidate_row(), side="bank", id_field="bank_id", fuzzy_score=74.1
    )
    client = FakeLLMClient(
        model="llama-3.3-70b-versatile",
        responses=["not json", decision_json("MATCH", 88)],
    )

    decision, audit_log = call_tier(
        client, "TIER_3", request, TIER3_PRICING, max_retries=1
    )

    assert decision.decision == "MATCH"
    assert len(audit_log) == 2
    assert audit_log[0].decision is None
    assert audit_log[1].decision == "MATCH"


def test_call_tier_gives_up_after_max_retries():
    request = build_adjudication_request(
        ledger_row(), candidate_row(), side="bank", id_field="bank_id", fuzzy_score=74.1
    )
    client = FakeLLMClient(
        model="llama-3.3-70b-versatile", responses=["not json", "still not json"]
    )

    decision, audit_log = call_tier(
        client, "TIER_3", request, TIER3_PRICING, max_retries=1
    )

    assert decision is None
    assert len(audit_log) == 2
    assert all(entry.decision is None for entry in audit_log)


# --- adjudicate (end-to-end orchestration) ---


def test_adjudicate_accepts_confident_tier3_match_without_escalating():
    config = make_config()
    request = build_adjudication_request(
        ledger_row(), candidate_row(), side="bank", id_field="bank_id", fuzzy_score=74.1
    )
    tier3 = FakeLLMClient(
        model="llama-3.3-70b-versatile", responses=[decision_json("MATCH", 95)]
    )
    tier4 = FakeLLMClient(model="claude-sonnet-4-5", responses=[])

    outcome = adjudicate(request, tier3, tier4, config)

    assert outcome.final_outcome == "MATCH"
    assert outcome.tiers_used == ["TIER_3"]
    assert len(outcome.audit_log) == 1
    assert tier4.prompts_received == []


def test_adjudicate_escalates_on_explicit_review_request():
    config = make_config()
    request = build_adjudication_request(
        ledger_row(), candidate_row(), side="bank", id_field="bank_id", fuzzy_score=74.1
    )
    tier3 = FakeLLMClient(
        model="llama-3.3-70b-versatile",
        responses=[decision_json("NEEDS_HUMAN_REVIEW", 99)],
    )
    tier4 = FakeLLMClient(
        model="claude-sonnet-4-5", responses=[decision_json("MATCH", 92)]
    )

    outcome = adjudicate(request, tier3, tier4, config)

    assert outcome.final_outcome == "MATCH"
    assert outcome.tiers_used == ["TIER_3", "TIER_4"]
    assert len(outcome.audit_log) == 2


def test_adjudicate_escalates_on_low_confidence_and_resolves_at_tier4():
    config = make_config()
    request = build_adjudication_request(
        ledger_row(), candidate_row(), side="bank", id_field="bank_id", fuzzy_score=74.1
    )
    tier3 = FakeLLMClient(
        model="llama-3.3-70b-versatile", responses=[decision_json("MATCH", 40)]
    )
    tier4 = FakeLLMClient(
        model="claude-sonnet-4-5", responses=[decision_json("MATCH", 85)]
    )

    outcome = adjudicate(request, tier3, tier4, config)

    assert outcome.final_outcome == "MATCH"
    assert outcome.confidence == 85
    assert outcome.tiers_used == ["TIER_3", "TIER_4"]


def test_adjudicate_never_forces_a_match_when_tier4_is_also_low_confidence():
    config = make_config()
    request = build_adjudication_request(
        ledger_row(), candidate_row(), side="bank", id_field="bank_id", fuzzy_score=74.1
    )
    tier3 = FakeLLMClient(
        model="llama-3.3-70b-versatile", responses=[decision_json("MATCH", 40)]
    )
    tier4 = FakeLLMClient(
        model="claude-sonnet-4-5", responses=[decision_json("MATCH", 50)]
    )

    outcome = adjudicate(request, tier3, tier4, config)

    assert outcome.final_outcome == "LOW_CONFIDENCE_AMBIGUOUS"


def test_adjudicate_accepts_confident_no_match_without_escalating():
    config = make_config()
    request = build_adjudication_request(
        ledger_row(), candidate_row(), side="bank", id_field="bank_id", fuzzy_score=74.1
    )
    tier3 = FakeLLMClient(
        model="llama-3.3-70b-versatile", responses=[decision_json("NO_MATCH", 90)]
    )
    tier4 = FakeLLMClient(model="claude-sonnet-4-5", responses=[])

    outcome = adjudicate(request, tier3, tier4, config)

    assert outcome.final_outcome == "NO_MATCH"
    assert outcome.tiers_used == ["TIER_3"]


def test_adjudicate_reports_unparseable_when_tier3_never_returns_valid_json():
    config = make_config()
    request = build_adjudication_request(
        ledger_row(), candidate_row(), side="bank", id_field="bank_id", fuzzy_score=74.1
    )
    tier3 = FakeLLMClient(
        model="llama-3.3-70b-versatile", responses=["nope", "still nope"]
    )
    tier4 = FakeLLMClient(model="claude-sonnet-4-5", responses=[])

    outcome = adjudicate(request, tier3, tier4, config)

    assert outcome.final_outcome == "LLM_OUTPUT_UNPARSEABLE"
    assert outcome.tiers_used == ["TIER_3"]
    assert tier4.prompts_received == []


def test_adjudicate_reports_unparseable_when_tier4_never_returns_valid_json():
    config = make_config()
    request = build_adjudication_request(
        ledger_row(), candidate_row(), side="bank", id_field="bank_id", fuzzy_score=74.1
    )
    tier3 = FakeLLMClient(
        model="llama-3.3-70b-versatile", responses=[decision_json("MATCH", 40)]
    )
    tier4 = FakeLLMClient(model="claude-sonnet-4-5", responses=["nope", "still nope"])

    outcome = adjudicate(request, tier3, tier4, config)

    assert outcome.final_outcome == "LLM_OUTPUT_UNPARSEABLE"
    assert outcome.tiers_used == ["TIER_3", "TIER_4"]
    assert len(outcome.audit_log) == 3
