"""Tests for Phase 2 Tier 2 fuzzy matching.

Uses small hand-crafted fixtures (not the full synthetic dataset) and, for
composite/routing tests, deliberately lopsided config weights that isolate
one scoring channel at a time so assertions don't depend on rapidfuzz's
internal fuzzy-matching behavior for threshold boundaries.
"""

from datetime import date
from decimal import Decimal

from app.core.config import (
    AmountTolerance,
    DateWindow,
    FeeSchedule,
    MatchingConfig,
    ScoreWeights,
    Thresholds,
)
from app.core.matcher.fuzzy_match import (
    composite_score,
    generate_candidates,
    match_fuzzy,
    score_amount,
    score_date,
    score_narration,
)

FEE_SCHEDULE = FeeSchedule(percentage_fee=Decimal("0.02"), gst_on_fee=Decimal("0.18"))


def make_config(amount: float, date_: float, narration: float) -> MatchingConfig:
    return MatchingConfig(
        score_weights=ScoreWeights(amount=amount, date=date_, narration=narration),
        thresholds=Thresholds(
            auto_match=90, llm_tier=60, tier3_confidence_escalation=70
        ),
        date_window=DateWindow(max_days=5, candidate_search_days=5),
        amount_tolerance=AmountTolerance(
            exact_match_paise=0, fee_schedule=FEE_SCHEDULE
        ),
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
        "date": "2026-06-01",
        "amount": "1000.00",
        "narration": "Payment received from Acme - Invoice INV00001",
        "account_fragment": "1234",
    }
    row.update(overrides)
    return row


# --- score_amount ---


def test_score_amount_exact_match_is_100():
    assert score_amount(100000, 100000, FEE_SCHEDULE) == 100.0


def test_score_amount_fee_adjusted_match_is_100_not_penalized():
    # 1000.00 - 2% fee (20.00) - 18% GST on fee (3.60) = 976.40
    assert score_amount(100000, 97640, FEE_SCHEDULE) == 100.0


def test_score_amount_deviation_decays_linearly():
    # 950.00 vs 1000.00 is a 5% deviation -> 100 - 5*10 = 50
    assert score_amount(100000, 95000, FEE_SCHEDULE) == 50.0


def test_score_amount_never_goes_negative():
    assert score_amount(100000, 0, FEE_SCHEDULE) == 0.0


# --- score_date ---


def test_score_date_same_day_is_100():
    assert score_date(date(2026, 6, 1), date(2026, 6, 1), max_days=5) == 100.0


def test_score_date_decays_linearly_within_window():
    assert score_date(date(2026, 6, 1), date(2026, 6, 2), max_days=5) == 80.0


def test_score_date_at_window_edge_is_zero():
    assert score_date(date(2026, 6, 1), date(2026, 6, 6), max_days=5) == 0.0


def test_score_date_beyond_window_is_zero():
    assert score_date(date(2026, 6, 1), date(2026, 6, 20), max_days=5) == 0.0


# --- score_narration ---


def test_score_narration_identical_is_100():
    assert score_narration("NEFT/AC/INV00001", "NEFT/AC/INV00001") == 100.0


def test_score_narration_token_reorder_is_still_100():
    assert (
        score_narration(
            "Payment from Acme Invoice INV1", "Invoice INV1 Payment from Acme"
        )
        == 100.0
    )


def test_score_narration_dissimilar_strings_score_lower():
    assert score_narration("NEFT/AC/INV00001", "completely different text") < 50.0


# --- composite_score ---


def test_composite_score_is_the_weighted_sum():
    config = make_config(amount=0.40, date_=0.35, narration=0.25)
    ledger_row = ledger(date="2026-06-01", amount="1000.00", narration="same narration")
    candidate_row = bank(
        "0000001", date="2026-06-02", amount="1000.00", narration="same narration"
    )

    result = composite_score(ledger_row, candidate_row, config)

    assert result.amount_score == 100.0
    assert result.date_score == 80.0
    assert result.narration_score == 100.0
    assert result.total == 0.40 * 100.0 + 0.35 * 80.0 + 0.25 * 100.0


# --- generate_candidates ---


def test_generate_candidates_keeps_rows_within_window():
    ledger_row = ledger(date="2026-06-10")
    pool = [
        bank("A", date="2026-06-05"),  # 5 days before, within window
        bank("B", date="2026-06-10"),  # same day
        bank("C", date="2026-06-13"),  # 3 days after, within window
        bank("D", date="2026-06-16"),  # 6 days after, outside window
    ]

    candidates = generate_candidates(ledger_row, pool, max_days=5)

    assert [row["bank_id"] for row in candidates] == ["A", "B", "C"]


def test_generate_candidates_empty_pool_returns_empty():
    assert generate_candidates(ledger(), [], max_days=5) == []


# --- match_fuzzy (threshold routing) ---


def test_match_fuzzy_auto_matches_above_threshold():
    config = make_config(amount=1.0, date_=0.0, narration=0.0)
    ledger_row = ledger(amount="1000.00")
    pool = [bank("0000001", amount="1000.00")]

    result = match_fuzzy(
        ledger_row, pool, side="bank", id_field="bank_id", config=config
    )

    assert result.outcome == "AUTO_MATCH"
    assert result.candidate_id == "0000001"
    assert result.score.total == 100.0


def test_match_fuzzy_escalates_ambiguous_score_to_tier3():
    config = make_config(amount=1.0, date_=0.0, narration=0.0)
    ledger_row = ledger(amount="1000.00")
    # 3% deviation -> amount_score 70, inside [60, 90) escalation band
    pool = [bank("0000002", amount="970.00")]

    result = match_fuzzy(
        ledger_row, pool, side="bank", id_field="bank_id", config=config
    )

    assert result.outcome == "ESCALATE_TIER3"
    assert result.candidate_id == "0000002"
    assert result.score.total == 70.0


def test_match_fuzzy_below_threshold_has_no_plausible_candidate():
    config = make_config(amount=1.0, date_=0.0, narration=0.0)
    ledger_row = ledger(amount="1000.00")
    # 5% deviation -> amount_score 50, below the llm_tier threshold of 60
    pool = [bank("0000003", amount="950.00")]

    result = match_fuzzy(
        ledger_row, pool, side="bank", id_field="bank_id", config=config
    )

    assert result.outcome == "NO_CANDIDATE"
    assert result.score.total == 50.0


def test_match_fuzzy_no_rows_in_pool_is_no_candidate_with_distinct_reason():
    config = make_config(amount=1.0, date_=0.0, narration=0.0)

    result = match_fuzzy(ledger(), [], side="bank", id_field="bank_id", config=config)

    assert result.outcome == "NO_CANDIDATE"
    assert result.score is None
    assert result.candidate_id is None
    assert "window" in result.reason


def test_match_fuzzy_picks_the_best_of_multiple_candidates():
    config = make_config(amount=1.0, date_=0.0, narration=0.0)
    ledger_row = ledger(amount="1000.00")
    pool = [
        bank("worse", amount="950.00"),  # score 50
        bank("better", amount="1000.00"),  # score 100
    ]

    result = match_fuzzy(
        ledger_row, pool, side="bank", id_field="bank_id", config=config
    )

    assert result.candidate_id == "better"
    assert result.outcome == "AUTO_MATCH"


def test_match_fuzzy_reason_names_the_tier_and_score():
    config = make_config(amount=1.0, date_=0.0, narration=0.0)
    ledger_row = ledger(amount="1000.00")
    pool = [bank("0000001", amount="1000.00")]

    result = match_fuzzy(
        ledger_row, pool, side="bank", id_field="bank_id", config=config
    )

    assert "Tier 2" in result.reason
    assert "0000001" in result.reason
