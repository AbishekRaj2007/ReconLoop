"""Tier 2 — fuzzy matching for records unresolved after Tier 1 (plan.md §9
Phase 2, §11.1).

For each ledger record still unmatched after exact-ID matching, generate
candidates from one other source within a date window, score each candidate
on amount/date/narration similarity, and route by the composite score:
auto-match (>=90), escalate to Tier 3 with the candidate attached (60-89), or
no plausible candidate (<60) — all logged with the score breakdown, not just
a boolean, per CLAUDE.md's Phase 2 definition of done.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_cls
from decimal import Decimal

from rapidfuzz import fuzz

from app.core.config import FeeSchedule, MatchingConfig
from app.core.matcher.normalize import normalize_amount_paise, normalize_date


@dataclass(frozen=True)
class ScoreBreakdown:
    amount_score: float
    date_score: float
    narration_score: float
    total: float


@dataclass(frozen=True)
class FuzzyMatchResult:
    ledger_id: str
    side: str
    candidate_id: str | None
    score: ScoreBreakdown | None
    outcome: str
    reason: str


def _fee_adjusted_paise(ledger_paise: int, fee_schedule: FeeSchedule) -> int:
    """The gateway amount the fee schedule predicts for this ledger amount —
    same rounding as the generator's `money()` so real fee-adjusted rows
    compare bit-exact, not just within tolerance."""
    ledger_rupees = Decimal(ledger_paise) / 100
    fee = ledger_rupees * fee_schedule.percentage_fee
    gst_on_fee = fee * fee_schedule.gst_on_fee
    return normalize_amount_paise(ledger_rupees - fee - gst_on_fee)


def score_amount(
    ledger_paise: int, candidate_paise: int, fee_schedule: FeeSchedule
) -> float:
    """100 if exact or fee-schedule-adjusted match (a known fee deduction
    must not tank the score); otherwise scaled down by % deviation."""
    if candidate_paise == ledger_paise:
        return 100.0
    if ledger_paise == 0:
        return 0.0
    if candidate_paise == _fee_adjusted_paise(ledger_paise, fee_schedule):
        return 100.0
    percent_diff = abs(ledger_paise - candidate_paise) / ledger_paise
    return max(0.0, 100.0 - percent_diff * 1000.0)


def score_date(ledger_date: date_cls, candidate_date: date_cls, max_days: int) -> float:
    """100 if same day, linearly decaying to 0 at `max_days`."""
    diff = abs((candidate_date - ledger_date).days)
    if diff >= max_days:
        return 0.0
    return 100.0 * (1 - diff / max_days)


def score_narration(ledger_narration: str, candidate_narration: str) -> float:
    return float(fuzz.token_sort_ratio(ledger_narration, candidate_narration))


def composite_score(
    ledger_row: dict, candidate_row: dict, config: MatchingConfig
) -> ScoreBreakdown:
    """score = (amount_weight * amount) + (date_weight * date) +
    (narration_weight * narration), per plan.md §11.1."""
    ledger_paise = normalize_amount_paise(ledger_row["amount"])
    candidate_paise = normalize_amount_paise(candidate_row["amount"])
    amount = score_amount(
        ledger_paise, candidate_paise, config.amount_tolerance.fee_schedule
    )
    date_score = score_date(
        normalize_date(ledger_row["date"]),
        normalize_date(candidate_row["date"]),
        config.date_window.max_days,
    )
    narration = score_narration(ledger_row["narration"], candidate_row["narration"])
    weights = config.score_weights
    total = (
        weights.amount * amount
        + weights.date * date_score
        + weights.narration * narration
    )
    return ScoreBreakdown(
        amount_score=amount,
        date_score=date_score,
        narration_score=narration,
        total=total,
    )


def generate_candidates(
    ledger_row: dict, pool_rows: list[dict], max_days: int
) -> list[dict]:
    """Same-side records within `max_days` of the ledger row's date."""
    ledger_date = normalize_date(ledger_row["date"])
    return [
        row
        for row in pool_rows
        if abs((normalize_date(row["date"]) - ledger_date).days) <= max_days
    ]


def match_fuzzy(
    ledger_row: dict,
    pool_rows: list[dict],
    side: str,
    id_field: str,
    config: MatchingConfig,
) -> FuzzyMatchResult:
    """Run Tier 2 for one ledger record against one other source's unresolved
    pool (bank or gateway)."""
    ledger_id = ledger_row["ledger_id"]
    candidates = generate_candidates(
        ledger_row, pool_rows, config.date_window.candidate_search_days
    )

    if not candidates:
        return FuzzyMatchResult(
            ledger_id=ledger_id,
            side=side,
            candidate_id=None,
            score=None,
            outcome="NO_CANDIDATE",
            reason=(
                f"Tier 2: no {side} record found within "
                f"{config.date_window.candidate_search_days}-day window of "
                f"{ledger_row['date']}."
            ),
        )

    best_row, best_score = max(
        ((row, composite_score(ledger_row, row, config)) for row in candidates),
        key=lambda item: item[1].total,
    )
    candidate_id = best_row[id_field]
    thresholds = config.thresholds

    if best_score.total >= thresholds.auto_match:
        outcome = "AUTO_MATCH"
        reason = (
            f"Tier 2 auto-match: {side} candidate {candidate_id} scored "
            f"{best_score.total:.1f} (amount={best_score.amount_score:.1f}, "
            f"date={best_score.date_score:.1f}, "
            f"narration={best_score.narration_score:.1f}), "
            f">= auto-match threshold {thresholds.auto_match}."
        )
    elif best_score.total >= thresholds.llm_tier:
        outcome = "ESCALATE_TIER3"
        reason = (
            f"Tier 2 ambiguous: best {side} candidate {candidate_id} scored "
            f"{best_score.total:.1f}, in the Tier 3 escalation band "
            f"[{thresholds.llm_tier}, {thresholds.auto_match})."
        )
    else:
        outcome = "NO_CANDIDATE"
        reason = (
            f"Tier 2: best {side} candidate {candidate_id} scored "
            f"{best_score.total:.1f}, below the Tier 3 threshold "
            f"{thresholds.llm_tier} — no plausible candidate."
        )

    return FuzzyMatchResult(
        ledger_id=ledger_id,
        side=side,
        candidate_id=candidate_id,
        score=best_score,
        outcome=outcome,
        reason=reason,
    )
