"""Field-level normalization for cross-source reconciliation matching (Phase 2).

Tier 1 (exact_match.py) and Tier 2 (fuzzy_match.py) both build on these
primitives so "same transaction, different formatting per source" is handled
in exactly one place (see plan.md §10.1, §11.1).
"""

from __future__ import annotations

from datetime import date as date_cls
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

KNOWN_ID_PREFIXES = ("TXN-", "pay_")


def normalize_amount_paise(amount: Decimal | str | int) -> int:
    """Canonicalize a rupee amount to integer paise. Decimal-only — never
    float — per CLAUDE.md's "Amounts are always Decimal" rule."""
    value = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    return int((value * 100).to_integral_value(rounding=ROUND_HALF_UP))


def normalize_date(value: date_cls | datetime | str) -> date_cls:
    """Canonicalize to a date-only value, UTC-normalized at ingestion."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date_cls):
        return value
    return date_cls.fromisoformat(value)


def normalize_reference_id(raw_id: str) -> str:
    """Strip a known source prefix and leading zeros so the same underlying
    transaction hashes identically across ledger/bank/gateway reference ID
    formats (see plan.md §10.1). Any trailing non-digit suffix (e.g. the
    "A"/"B" gateway IDs used for split settlements) is preserved deliberately:
    a split settlement's two halves are not the same reference as the
    original, and collapsing them here would silently "solve" split
    settlements by accident instead of leaving them for candidate/exception
    handling (see plan.md §12 SPLIT_SETTLEMENT_UNRESOLVED)."""
    remainder = raw_id
    for prefix in KNOWN_ID_PREFIXES:
        if remainder.startswith(prefix):
            remainder = remainder[len(prefix) :]
            break
    return remainder.lstrip("0") or "0"
