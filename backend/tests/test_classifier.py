"""Tests for the Phase 4 rule-based exception classifier (plan.md §12).

Uses small hand-crafted fixtures, including directly-constructed
FuzzyMatchResult/AdjudicationOutcome inputs (treating them as an opaque
upstream contract) so each classification rule can be tested in isolation
without needing to reproduce a fully realistic upstream pipeline state.
"""

from decimal import Decimal

from app.core.config import (
    AmountTolerance,
    ClassifierConfig,
    DateWindow,
    FeeSchedule,
    MatchingConfig,
    ScoreWeights,
    Thresholds,
)
from app.core.exceptions.classifier import classify_record
from app.core.matcher.fuzzy_match import FuzzyMatchResult, ScoreBreakdown
from app.core.matcher.llm_adjudicator import AdjudicationOutcome


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
        classifier=ClassifierConfig(duplicate_tie_epsilon=2.0),
    )


def ledger(**overrides) -> dict:
    row = {
        "ledger_id": "TXN-000001",
        "date": "2026-06-01",
        "amount": "1000.00",
        "narration": "Payment received from Acme - Invoice INV00001",
        "counterparty_name": "Acme",
    }
    row.update(overrides)
    return row


def bank(bank_id: str, **overrides) -> dict:
    row = {
        "bank_id": bank_id,
        "date": "2026-06-02",
        "amount": "1000.00",
        "narration": "NEFT/A/INV00001",
        "account_fragment": "1234",
    }
    row.update(overrides)
    return row


def gateway(gateway_id: str, **overrides) -> dict:
    row = {
        "gateway_id": gateway_id,
        "date": "2026-06-03",
        "amount": "1000.00",
        "narration": "Payout - MCHT0001 - Order ORD00001",
        "merchant_id": "MCHT0001",
    }
    row.update(overrides)
    return row


def no_candidate_result(
    side: str, score: ScoreBreakdown | None = None
) -> FuzzyMatchResult:
    return FuzzyMatchResult(
        ledger_id="TXN-000001",
        side=side,
        candidate_id=None if score is None else "placeholder",
        score=score,
        outcome="NO_CANDIDATE",
        reason="test fixture",
    )


def auto_match_result(side: str, candidate_id: str) -> FuzzyMatchResult:
    return FuzzyMatchResult(
        ledger_id="TXN-000001",
        side=side,
        candidate_id=candidate_id,
        score=ScoreBreakdown(
            amount_score=100, date_score=100, narration_score=100, total=100
        ),
        outcome="AUTO_MATCH",
        reason="test fixture",
    )


def classify_bank_only(
    tier2_result: FuzzyMatchResult,
    bank_pool: list[dict],
    llm_outcome: AdjudicationOutcome | None = None,
    ledger_row: dict | None = None,
):
    """classify_record with the gateway side already resolved, so only the
    bank side's classification logic is exercised."""
    return classify_record(
        ledger_row or ledger(),
        bank_pool=bank_pool,
        gateway_pool=[],
        bank_tier2=tier2_result,
        gateway_tier2=auto_match_result("gateway", "pay_00001"),
        bank_llm=llm_outcome,
        gateway_llm=None,
        config=make_config(),
    )


def test_missing_counterpart_when_pool_is_empty():
    result = classify_bank_only(no_candidate_result("bank"), bank_pool=[])

    assert result.exception_type == "MISSING_COUNTERPART"
    assert result.ledger_id == "TXN-000001"
    assert "TXN-000001" in result.affected_ids
    assert "window" in result.rationale


def test_amount_mismatch_beyond_tolerance():
    bad_score = ScoreBreakdown(
        amount_score=40.0, date_score=100.0, narration_score=100.0, total=76.0
    )
    tier2 = FuzzyMatchResult(
        ledger_id="TXN-000001",
        side="bank",
        candidate_id="0000009",
        score=bad_score,
        outcome="ESCALATE_TIER3",
        reason="test fixture",
    )

    result = classify_bank_only(tier2, bank_pool=[])

    assert result.exception_type == "AMOUNT_MISMATCH_BEYOND_TOLERANCE"
    assert "0000009" in result.affected_ids
    assert "40.0" in result.rationale


def test_zero_amount_score_is_missing_counterpart_not_amount_mismatch():
    """A candidate with amount_score exactly 0 (wildly different amount) is
    not "the right transaction with a slightly wrong amount" — it's more
    honestly no plausible candidate at all, e.g. an unrelated leftover row
    from a different transaction that happened to land in the date window."""
    zero_score = ScoreBreakdown(
        amount_score=0.0, date_score=80.0, narration_score=20.0, total=35.0
    )
    tier2 = FuzzyMatchResult(
        ledger_id="TXN-000001",
        side="bank",
        candidate_id="0000314",
        score=zero_score,
        outcome="NO_CANDIDATE",
        reason="test fixture",
    )

    result = classify_bank_only(tier2, bank_pool=[])

    assert result.exception_type == "MISSING_COUNTERPART"
    assert "0000314" not in result.affected_ids


def test_timing_drift_exceeded_when_exact_amount_match_found_outside_window():
    far_candidate = bank("0000099", date="2026-06-15", amount="1000.00")  # 14 days out

    result = classify_bank_only(no_candidate_result("bank"), bank_pool=[far_candidate])

    assert result.exception_type == "TIMING_DRIFT_EXCEEDED"
    assert "0000099" in result.affected_ids


def test_no_timing_drift_when_amount_also_differs_outside_window():
    """A far-away candidate with a mismatched amount shouldn't be treated as
    a confident timing-drift case — fall through to missing counterpart."""
    far_candidate = bank("0000099", date="2026-06-15", amount="500.00")

    result = classify_bank_only(no_candidate_result("bank"), bank_pool=[far_candidate])

    assert result.exception_type == "MISSING_COUNTERPART"


def test_duplicate_candidate_when_two_candidates_tie():
    # Narration matches the ledger's exactly on both, so both score high enough
    # (>= llm_tier) to count as a genuine plausible tie, not two weak guesses.
    same_narration = ledger()["narration"]
    tied_a = bank(
        "0000001", date="2026-06-02", amount="1000.00", narration=same_narration
    )
    tied_b = bank(
        "0000002", date="2026-06-02", amount="1000.00", narration=same_narration
    )

    result = classify_bank_only(no_candidate_result("bank"), bank_pool=[tied_a, tied_b])

    assert result.exception_type == "DUPLICATE_CANDIDATE"
    assert "0000001" in result.affected_ids
    assert "0000002" in result.affected_ids


def test_split_settlement_unresolved_when_two_candidates_sum_to_ledger_amount():
    part_a = gateway("pay_00001A", amount="600.00")
    part_b = gateway("pay_00001B", amount="400.00")

    result = classify_record(
        ledger(),
        bank_pool=[],
        gateway_pool=[part_a, part_b],
        bank_tier2=auto_match_result("bank", "0000001"),
        gateway_tier2=no_candidate_result("gateway"),
        bank_llm=None,
        gateway_llm=None,
        config=make_config(),
    )

    assert result.exception_type == "SPLIT_SETTLEMENT_UNRESOLVED"
    assert "pay_00001A" in result.affected_ids
    assert "pay_00001B" in result.affected_ids


def test_low_confidence_ambiguous_passes_through_from_llm_outcome():
    llm_outcome = AdjudicationOutcome(
        ledger_id="TXN-000001",
        side="bank",
        candidate_id="0000009",
        final_outcome="LOW_CONFIDENCE_AMBIGUOUS",
        confidence=45.0,
        rationale="Neither tier was confident.",
        tiers_used=["TIER_3", "TIER_4"],
        audit_log=[],
    )
    escalated = FuzzyMatchResult(
        ledger_id="TXN-000001",
        side="bank",
        candidate_id="0000009",
        score=ScoreBreakdown(
            amount_score=80, date_score=80, narration_score=30, total=68
        ),
        outcome="ESCALATE_TIER3",
        reason="test fixture",
    )

    result = classify_bank_only(escalated, bank_pool=[], llm_outcome=llm_outcome)

    assert result.exception_type == "LOW_CONFIDENCE_AMBIGUOUS"
    assert "0000009" in result.affected_ids
    assert "Neither tier was confident." in result.rationale


def test_llm_output_unparseable_passes_through_from_llm_outcome():
    llm_outcome = AdjudicationOutcome(
        ledger_id="TXN-000001",
        side="bank",
        candidate_id="0000009",
        final_outcome="LLM_OUTPUT_UNPARSEABLE",
        confidence=None,
        rationale="TIER_3 returned malformed output 2 times in a row.",
        tiers_used=["TIER_3"],
        audit_log=[],
    )
    escalated = FuzzyMatchResult(
        ledger_id="TXN-000001",
        side="bank",
        candidate_id="0000009",
        score=ScoreBreakdown(
            amount_score=80, date_score=80, narration_score=30, total=68
        ),
        outcome="ESCALATE_TIER3",
        reason="test fixture",
    )

    result = classify_bank_only(escalated, bank_pool=[], llm_outcome=llm_outcome)

    assert result.exception_type == "LLM_OUTPUT_UNPARSEABLE"


def test_missing_counterpart_notes_confident_llm_rejection():
    llm_outcome = AdjudicationOutcome(
        ledger_id="TXN-000001",
        side="bank",
        candidate_id="0000009",
        final_outcome="NO_MATCH",
        confidence=90.0,
        rationale="Different invoice number in the narration.",
        tiers_used=["TIER_3"],
        audit_log=[],
    )
    escalated = FuzzyMatchResult(
        ledger_id="TXN-000001",
        side="bank",
        candidate_id="0000009",
        score=ScoreBreakdown(
            amount_score=80, date_score=80, narration_score=30, total=68
        ),
        outcome="ESCALATE_TIER3",
        reason="test fixture",
    )

    result = classify_bank_only(escalated, bank_pool=[], llm_outcome=llm_outcome)

    assert result.exception_type == "MISSING_COUNTERPART"
    assert "Different invoice number" in result.rationale


def test_classify_record_prioritizes_split_settlement_over_amount_mismatch():
    bad_score = ScoreBreakdown(
        amount_score=40.0, date_score=100.0, narration_score=100.0, total=76.0
    )
    bank_tier2 = FuzzyMatchResult(
        ledger_id="TXN-000001",
        side="bank",
        candidate_id="0000009",
        score=bad_score,
        outcome="ESCALATE_TIER3",
        reason="test fixture",
    )
    part_a = gateway("pay_00001A", amount="600.00")
    part_b = gateway("pay_00001B", amount="400.00")

    result = classify_record(
        ledger(),
        bank_pool=[],
        gateway_pool=[part_a, part_b],
        bank_tier2=bank_tier2,
        gateway_tier2=no_candidate_result("gateway"),
        bank_llm=None,
        gateway_llm=None,
        config=make_config(),
    )

    assert result.exception_type == "SPLIT_SETTLEMENT_UNRESOLVED"


def test_classify_record_skips_already_matched_side():
    result = classify_record(
        ledger(),
        bank_pool=[],
        gateway_pool=[],
        bank_tier2=auto_match_result("bank", "0000001"),
        gateway_tier2=no_candidate_result("gateway"),
        bank_llm=None,
        gateway_llm=None,
        config=make_config(),
    )

    assert result.exception_type == "MISSING_COUNTERPART"
    assert "0000001" not in result.affected_ids
