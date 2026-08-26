"""Tests for the Phase 5 scoring/validation harness (plan.md §9 Phase 5,
§13). Hand-crafted ground-truth + match fixtures, not the full synthetic
batch, so each confusion-matrix case is independently verifiable by hand.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from run_pipeline import MatchRecord

from app.core.scoring.evaluator import score_pipeline


def gt(ledger_id, bank_ids, gateway_ids, expected_outcome="MATCH"):
    return {
        "ledger_id": ledger_id,
        "bank_ids": bank_ids,
        "gateway_ids": gateway_ids,
        "expected_outcome": expected_outcome,
    }


def match(ledger_id, bank_ids, gateway_ids, tier="TIER_1"):
    return MatchRecord(ledger_id, bank_ids, gateway_ids, tier)


def test_correct_match_is_a_true_positive():
    result = score_pipeline(
        ground_truth=[gt("TXN-1", ["B1"], ["G1"])],
        matches=[match("TXN-1", ["B1"], ["G1"])],
        total_records=1,
    )

    assert result.overall.true_positives == 1
    assert result.overall.false_positives == 0
    assert result.overall.false_negatives == 0
    assert result.precision == 1.0
    assert result.recall == 1.0
    assert result.f1 == 1.0


def test_missed_match_is_a_false_negative():
    result = score_pipeline(
        ground_truth=[gt("TXN-1", ["B1"], ["G1"])],
        matches=[],
        total_records=1,
    )

    assert result.overall.false_negatives == 1
    assert result.overall.true_positives == 0
    assert result.missed_match_rate == 1.0


def test_wrong_match_is_both_a_false_positive_and_false_negative():
    """Matching the wrong candidate is worse than missing it: it's a false
    match AND the correct pairing was never found."""
    result = score_pipeline(
        ground_truth=[gt("TXN-1", ["B1"], ["G1"])],
        matches=[match("TXN-1", ["B999"], ["G1"])],
        total_records=1,
    )

    assert result.overall.false_positives == 1
    assert result.overall.false_negatives == 1
    assert result.overall.true_positives == 0


def test_correctly_left_unresolved_is_a_true_negative():
    result = score_pipeline(
        ground_truth=[gt("TXN-1", [], [], expected_outcome="UNRESOLVED")],
        matches=[],
        total_records=1,
    )

    assert result.overall.true_negatives == 1
    assert result.false_match_rate == 0.0


def test_forcing_a_match_on_an_unresolvable_record_is_a_false_positive():
    result = score_pipeline(
        ground_truth=[gt("TXN-1", [], [], expected_outcome="UNRESOLVED")],
        matches=[match("TXN-1", ["B1"], ["G1"])],
        total_records=1,
    )

    assert result.overall.false_positives == 1
    assert result.false_match_rate == 1.0


def test_false_match_rate_and_missed_match_rate_are_fractions_of_total_records():
    result = score_pipeline(
        ground_truth=[
            gt("TXN-1", ["B1"], ["G1"]),
            gt("TXN-2", ["B2"], ["G2"]),
            gt("TXN-3", [], [], expected_outcome="UNRESOLVED"),
            gt("TXN-4", ["B4"], ["G4"]),
        ],
        matches=[
            match("TXN-1", ["B1"], ["G1"]),  # TP
            match("TXN-3", ["B3"], ["G3"]),  # FP (should be unresolved)
            # TXN-2 missing entirely -> FN
            # TXN-4 missing entirely -> FN
        ],
        total_records=4,
    )

    assert result.false_match_rate == 0.25  # 1 FP / 4
    assert result.missed_match_rate == 0.5  # 2 FN / 4


def test_precision_recall_f1_with_a_mix_of_outcomes():
    result = score_pipeline(
        ground_truth=[
            gt("TXN-1", ["B1"], ["G1"]),
            gt("TXN-2", ["B2"], ["G2"]),
            gt("TXN-3", ["B3"], ["G3"]),
        ],
        matches=[
            match("TXN-1", ["B1"], ["G1"]),  # TP
            match("TXN-2", ["BX"], ["G2"]),  # FP + FN (wrong bank id)
        ],
        total_records=3,
    )

    # TP=1, FP=1, FN=2 (TXN-2 wrong + TXN-3 missing)
    assert result.overall.true_positives == 1
    assert result.overall.false_positives == 1
    assert result.overall.false_negatives == 2
    assert result.precision == 0.5  # 1 / (1 + 1)
    assert result.recall == pytest.approx(1 / 3)
    assert result.f1 == pytest.approx(2 * 0.5 * (1 / 3) / (0.5 + 1 / 3))


def test_per_source_pair_confusion_is_independent_of_overall():
    """Bank side is correct, gateway side is wrong: overall is a false
    match/missed match, but the bank-pair confusion should show a TP."""
    result = score_pipeline(
        ground_truth=[gt("TXN-1", ["B1"], ["G1"])],
        matches=[match("TXN-1", ["B1"], ["GX"])],
        total_records=1,
    )

    assert result.per_source_pair["ledger_bank"].true_positives == 1
    assert result.per_source_pair["ledger_gateway"].false_positives == 1
    assert result.per_source_pair["ledger_gateway"].false_negatives == 1
    assert result.overall.true_positives == 0
    assert result.overall.false_positives == 1


def test_tier_attribution_reports_percent_of_total_records():
    result = score_pipeline(
        ground_truth=[
            gt("TXN-1", ["B1"], ["G1"]),
            gt("TXN-2", ["B2"], ["G2"]),
        ],
        matches=[
            match("TXN-1", ["B1"], ["G1"], tier="TIER_1"),
            match("TXN-2", ["B2"], ["G2"], tier="TIER_3"),
        ],
        total_records=4,  # 2 more records exist but aren't matched
    )

    assert result.tier_attribution["TIER_1"] == 0.25
    assert result.tier_attribution["TIER_3"] == 0.25
    assert "TIER_2" not in result.tier_attribution


def test_split_settlement_multiple_ids_must_match_as_a_set():
    result = score_pipeline(
        ground_truth=[gt("TXN-1", ["B1"], ["G1A", "G1B"])],
        matches=[match("TXN-1", ["B1"], ["G1B", "G1A"])],  # order-independent
        total_records=1,
    )

    assert result.overall.true_positives == 1
