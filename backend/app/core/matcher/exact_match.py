"""Tier 1 — exact reference-ID matching (Phase 2, plan.md §9 Phase 2).

Matches ledger, bank, and gateway rows purely on normalized reference ID via
a hash index — O(n), no nested loops. Amount/date/narration are never
consulted here; a ledger row either has a same-ID counterpart on both other
sides or it doesn't, and if it doesn't it's left for Tier 2 to try to resolve
by other evidence.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from app.core.matcher.normalize import normalize_reference_id


@dataclass(frozen=True)
class ExactMatch:
    ledger_id: str
    bank_ids: list[str]
    gateway_ids: list[str]
    tier: str
    reason: str


@dataclass(frozen=True)
class ExactMatchResult:
    matches: list[ExactMatch] = field(default_factory=list)
    unmatched_ledger_ids: list[str] = field(default_factory=list)


def _index_by_normalized_id(rows: list[dict], id_field: str) -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        index[normalize_reference_id(row[id_field])].append(row)
    return index


def match_exact(
    ledger_rows: list[dict],
    bank_rows: list[dict],
    gateway_rows: list[dict],
) -> ExactMatchResult:
    bank_index = _index_by_normalized_id(bank_rows, "bank_id")
    gateway_index = _index_by_normalized_id(gateway_rows, "gateway_id")

    matches: list[ExactMatch] = []
    unmatched_ledger_ids: list[str] = []

    for ledger_row in ledger_rows:
        normalized_id = normalize_reference_id(ledger_row["ledger_id"])
        bank_hits = bank_index.get(normalized_id, [])
        gateway_hits = gateway_index.get(normalized_id, [])

        if not bank_hits or not gateway_hits:
            unmatched_ledger_ids.append(ledger_row["ledger_id"])
            continue

        matches.append(
            ExactMatch(
                ledger_id=ledger_row["ledger_id"],
                bank_ids=[row["bank_id"] for row in bank_hits],
                gateway_ids=[row["gateway_id"] for row in gateway_hits],
                tier="TIER_1_EXACT_ID",
                reason=(
                    f"Tier 1 exact match: normalized reference ID '{normalized_id}' "
                    "found in ledger, bank, and gateway sources."
                ),
            )
        )

    return ExactMatchResult(matches=matches, unmatched_ledger_ids=unmatched_ledger_ids)
