"""Tests for Phase 2 field-level normalization primitives."""

from datetime import date, datetime, timezone
from decimal import Decimal

from app.core.matcher.normalize import (
    normalize_amount_paise,
    normalize_date,
    normalize_reference_id,
)


def test_normalize_amount_paise_from_decimal():
    assert normalize_amount_paise(Decimal("18266.92")) == 1826692


def test_normalize_amount_paise_from_string():
    assert normalize_amount_paise("100.50") == 10050


def test_normalize_amount_paise_from_whole_rupee_string():
    assert normalize_amount_paise("500") == 50000


def test_normalize_date_from_iso_string():
    assert normalize_date("2026-06-01") == date(2026, 6, 1)


def test_normalize_date_passes_through_date_object():
    assert normalize_date(date(2026, 6, 1)) == date(2026, 6, 1)


def test_normalize_date_drops_time_from_datetime():
    assert normalize_date(datetime(2026, 6, 1, 23, 59, tzinfo=timezone.utc)) == date(
        2026, 6, 1
    )


def test_normalize_reference_id_strips_ledger_prefix_and_leading_zeros():
    assert normalize_reference_id("TXN-000123") == "123"


def test_normalize_reference_id_strips_bank_leading_zeros_no_prefix():
    assert normalize_reference_id("0000123") == "123"


def test_normalize_reference_id_strips_gateway_prefix():
    assert normalize_reference_id("pay_00123") == "123"


def test_normalize_reference_id_preserves_split_settlement_suffix():
    """A split-settlement gateway id like pay_00123A must NOT normalize to the
    same id as the base transaction "123" — that would silently "solve" split
    settlements by accident instead of leaving them for candidate/exception
    handling (see plan.md §12 SPLIT_SETTLEMENT_UNRESOLVED)."""
    assert normalize_reference_id("pay_00123A") == "123A"
    assert normalize_reference_id("pay_00123A") != normalize_reference_id("TXN-000123")


def test_normalize_reference_id_all_zeros_defaults_to_zero():
    assert normalize_reference_id("0000000") == "0"
