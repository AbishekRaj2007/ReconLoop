"""Compare pipeline output against ground_truth_matches.json (plan.md §9
Phase 5, §13). Proves the match rate is real, not cherry-picked: every
number here is derived from an explicit confusion matrix against the answer
key, never self-reported by the matcher.

Records the pipeline hasn't reached a final disposition for (still pending
Tier 3/4) are simply absent from `matches` and scored as "not yet matched" —
consistent with the rest of the project's refusal to guess at an outcome
that hasn't actually happened (CLAUDE.md Golden Rule #2).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Protocol


class _MatchLike(Protocol):
    ledger_id: str
    bank_ids: list[str]
    gateway_ids: list[str]
    tier: str


@dataclass(frozen=True)
class ConfusionCounts:
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int

    @property
    def precision(self) -> float:
        denom = self.true_positives + self.false_positives
        return self.true_positives / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.true_positives + self.false_negatives
        return self.true_positives / denom if denom else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


@dataclass(frozen=True)
class ScoringResult:
    total_records: int
    overall: ConfusionCounts
    per_source_pair: dict[str, ConfusionCounts]
    false_match_rate: float
    missed_match_rate: float
    tier_attribution: dict[str, float]

    @property
    def precision(self) -> float:
        return self.overall.precision

    @property
    def recall(self) -> float:
        return self.overall.recall

    @property
    def f1(self) -> float:
        return self.overall.f1


def _confusion_for_field(
    ground_truth: list[dict[str, Any]],
    matches_by_ledger_id: dict[str, _MatchLike],
    field_name: str,
) -> ConfusionCounts:
    tp = fp = fn = tn = 0
    for g in ground_truth:
        expected = set(g[field_name])
        match = matches_by_ledger_id.get(g["ledger_id"])
        actual = set(getattr(match, field_name)) if match is not None else set()
        if expected:
            if actual == expected:
                tp += 1
            elif actual:
                # Matched something, but not the right thing: both a false
                # match (wrong pairing made) and a missed match (the correct
                # pairing was never found).
                fp += 1
                fn += 1
            else:
                fn += 1
        else:
            if actual:
                fp += 1
            else:
                tn += 1
    return ConfusionCounts(tp, fp, fn, tn)


def _confusion_overall(
    ground_truth: list[dict[str, Any]],
    matches_by_ledger_id: dict[str, _MatchLike],
) -> ConfusionCounts:
    tp = fp = fn = tn = 0
    for g in ground_truth:
        match = matches_by_ledger_id.get(g["ledger_id"])
        if g["expected_outcome"] == "MATCH":
            correct = (
                match is not None
                and set(match.bank_ids) == set(g["bank_ids"])
                and set(match.gateway_ids) == set(g["gateway_ids"])
            )
            if correct:
                tp += 1
            elif match is not None:
                fp += 1
                fn += 1
            else:
                fn += 1
        else:  # expected UNRESOLVED
            if match is not None:
                fp += 1
            else:
                tn += 1
    return ConfusionCounts(tp, fp, fn, tn)


def score_pipeline(
    ground_truth: list[dict[str, Any]],
    matches: list[_MatchLike],
    total_records: int,
) -> ScoringResult:
    matches_by_ledger_id = {m.ledger_id: m for m in matches}

    overall = _confusion_overall(ground_truth, matches_by_ledger_id)
    per_source_pair = {
        "ledger_bank": _confusion_for_field(
            ground_truth, matches_by_ledger_id, "bank_ids"
        ),
        "ledger_gateway": _confusion_for_field(
            ground_truth, matches_by_ledger_id, "gateway_ids"
        ),
    }

    tier_counts = Counter(m.tier for m in matches)
    tier_attribution = (
        {tier: count / total_records for tier, count in tier_counts.items()}
        if total_records
        else {}
    )

    return ScoringResult(
        total_records=total_records,
        overall=overall,
        per_source_pair=per_source_pair,
        false_match_rate=(
            overall.false_positives / total_records if total_records else 0.0
        ),
        missed_match_rate=(
            overall.false_negatives / total_records if total_records else 0.0
        ),
        tier_attribution=tier_attribution,
    )
