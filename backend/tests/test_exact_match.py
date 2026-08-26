"""Tests for Phase 2 Tier 1 exact-match (reference ID hash index).

Uses small hand-crafted fixtures rather than the full synthetic dataset, per
CLAUDE.md's testing convention, so failures are easy to debug.
"""

from app.core.matcher.exact_match import match_exact


def ledger(ledger_id: str, **overrides) -> dict:
    row = {
        "ledger_id": ledger_id,
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


def test_matches_when_all_three_sources_share_normalized_id():
    result = match_exact(
        [ledger("TXN-000001")], [bank("0000001")], [gateway("pay_00001")]
    )

    assert len(result.matches) == 1
    match = result.matches[0]
    assert match.ledger_id == "TXN-000001"
    assert match.bank_ids == ["0000001"]
    assert match.gateway_ids == ["pay_00001"]
    assert match.tier == "TIER_1_EXACT_ID"
    assert result.unmatched_ledger_ids == []


def test_ledger_with_no_bank_or_gateway_counterpart_is_unmatched():
    """Simulates the missing_counterpart scenario: ledger-only record."""
    result = match_exact([ledger("TXN-000002")], [], [])

    assert result.matches == []
    assert result.unmatched_ledger_ids == ["TXN-000002"]


def test_bank_present_but_gateway_absent_is_not_a_match():
    result = match_exact([ledger("TXN-000003")], [bank("0000003")], [])

    assert result.matches == []
    assert result.unmatched_ledger_ids == ["TXN-000003"]


def test_duplicate_gateway_rows_sharing_one_id_both_attach_to_the_match():
    """Simulates the duplicate_entry scenario: a webhook retry produces two
    gateway rows with the identical gateway_id."""
    result = match_exact(
        [ledger("TXN-000004")],
        [bank("0000004")],
        [gateway("pay_00004"), gateway("pay_00004")],
    )

    assert len(result.matches) == 1
    assert result.matches[0].gateway_ids == ["pay_00004", "pay_00004"]


def test_split_settlement_gateway_ids_do_not_collide_with_base_ledger_id():
    """Split-settlement gateway IDs (pay_00005A / pay_00005B) must not be
    treated as exact matches for TXN-000005 at Tier 1 — see
    normalize_reference_id's docstring for why."""
    result = match_exact(
        [ledger("TXN-000005")],
        [bank("0000005")],
        [gateway("pay_00005A"), gateway("pay_00005B")],
    )

    assert result.matches == []
    assert result.unmatched_ledger_ids == ["TXN-000005"]


def test_id_format_drift_bank_id_does_not_match_at_tier1():
    """A transposed bank_id is a genuinely different number and must not
    exact-match — it's Tier 2's job to recover it via fuzzy matching."""
    result = match_exact(
        [ledger("TXN-000006")], [bank("0000060")], [gateway("pay_00006")]
    )

    assert result.matches == []
    assert result.unmatched_ledger_ids == ["TXN-000006"]


def test_match_reason_explains_which_rule_and_normalized_id_matched():
    result = match_exact(
        [ledger("TXN-000007")], [bank("0000007")], [gateway("pay_00007")]
    )

    reason = result.matches[0].reason
    assert "7" in reason
    assert "Tier 1" in reason
