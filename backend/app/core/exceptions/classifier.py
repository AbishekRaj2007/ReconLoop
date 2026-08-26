"""Rule-based exception classifier (plan.md §9 Phase 4, §12 taxonomy).

Every still-unresolved ledger record gets bucketed into exactly one named
exception type. Rule-based classification always runs first — an LLM
rationale is only used to *explain* a decision Tier 3/4 already made, never
to classify from scratch, per CLAUDE.md Golden Rule #1. Deterministic checks
are tried in order from most specific to least; the first side to raise a
credible hit anywhere wins, and the overall record priority order below
decides between hits on different sides.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import MatchingConfig
from app.core.matcher.fuzzy_match import (
    FuzzyMatchResult,
    ScoreBreakdown,
    composite_score,
    generate_candidates,
    score_amount,
)
from app.core.matcher.llm_adjudicator import AdjudicationOutcome
from app.core.matcher.normalize import normalize_amount_paise, normalize_date

PRIORITY = [
    "SPLIT_SETTLEMENT_UNRESOLVED",
    "DUPLICATE_CANDIDATE",
    "TIMING_DRIFT_EXCEEDED",
    "AMOUNT_MISMATCH_BEYOND_TOLERANCE",
    "LOW_CONFIDENCE_AMBIGUOUS",
    "LLM_OUTPUT_UNPARSEABLE",
    "MISSING_COUNTERPART",
    "UNCLASSIFIED",
]


@dataclass(frozen=True)
class ExceptionRecord:
    exception_type: str
    ledger_id: str
    affected_ids: list[str]
    rationale: str
    suggested_action: str


@dataclass(frozen=True)
class _ExceptionHit:
    exception_type: str
    affected_ids: list[str]
    rationale: str
    suggested_action: str


def _candidates_within_window(
    ledger_row: dict, pool: list[dict], config: MatchingConfig
) -> list[dict]:
    return generate_candidates(
        ledger_row, pool, config.date_window.candidate_search_days
    )


def _find_split_settlement_pair(
    ledger_row: dict, pool: list[dict], config: MatchingConfig
) -> tuple[dict, dict] | None:
    """Two candidates within the window whose amounts sum exactly to the
    ledger amount — evidence of a one-to-many settlement Tier 1/2 can't
    auto-link (see plan.md §10.2 split_settlement)."""
    candidates = _candidates_within_window(ledger_row, pool, config)
    ledger_paise = normalize_amount_paise(ledger_row["amount"])
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            a, b = candidates[i], candidates[j]
            total = normalize_amount_paise(a["amount"]) + normalize_amount_paise(
                b["amount"]
            )
            if total == ledger_paise:
                return a, b
    return None


def _find_tied_candidates(
    ledger_row: dict, pool: list[dict], config: MatchingConfig
) -> list[tuple[dict, ScoreBreakdown]]:
    """Two or more candidates within the window whose scores are within
    `duplicate_tie_epsilon` of the top score, and plausible enough (>= the
    llm_tier floor) to be a genuine tie rather than two weak guesses."""
    candidates = _candidates_within_window(ledger_row, pool, config)
    if len(candidates) < 2:
        return []
    scored = sorted(
        ((row, composite_score(ledger_row, row, config)) for row in candidates),
        key=lambda item: item[1].total,
        reverse=True,
    )
    top_score = scored[0][1].total
    if top_score < config.thresholds.llm_tier:
        return []
    tied = [
        item
        for item in scored
        if top_score - item[1].total <= config.classifier.duplicate_tie_epsilon
    ]
    return tied if len(tied) >= 2 else []


def _find_amount_match_outside_window(
    ledger_row: dict, pool: list[dict], config: MatchingConfig
) -> tuple[dict, int] | None:
    """The closest candidate anywhere in the pool whose amount matches
    exactly (or fee-schedule-adjusted) but which sits outside the configured
    date window — Tier 2 never saw it because it filters by date first."""
    ledger_paise = normalize_amount_paise(ledger_row["amount"])
    ledger_date = normalize_date(ledger_row["date"])
    window = config.date_window.candidate_search_days
    best: dict | None = None
    best_diff: int | None = None
    for row in pool:
        candidate_paise = normalize_amount_paise(row["amount"])
        amount_score = score_amount(
            ledger_paise, candidate_paise, config.amount_tolerance.fee_schedule
        )
        if amount_score < 100.0:
            continue
        diff = abs((normalize_date(row["date"]) - ledger_date).days)
        if diff <= window:
            continue
        if best_diff is None or diff < best_diff:
            best, best_diff = row, diff
    if best is None or best_diff is None:
        return None
    return best, best_diff


def _detect_side_exception(
    ledger_row: dict,
    side: str,
    id_field: str,
    pool: list[dict],
    tier2_result: FuzzyMatchResult,
    llm_outcome: AdjudicationOutcome | None,
    config: MatchingConfig,
) -> _ExceptionHit:
    ledger_id = ledger_row["ledger_id"]

    split = _find_split_settlement_pair(ledger_row, pool, config)
    if split is not None:
        a, b = split
        return _ExceptionHit(
            "SPLIT_SETTLEMENT_UNRESOLVED",
            [a[id_field], b[id_field]],
            f"Ledger record {ledger_id} (₹{ledger_row['amount']}) appears to be settled by "
            f"two {side} records — {a[id_field]} (₹{a['amount']}) and {b[id_field]} "
            f"(₹{b['amount']}) — whose amounts sum exactly to the ledger amount.",
            f"Confirm this is a split settlement and manually link {a[id_field]} and "
            f"{b[id_field]} to {ledger_id}; the matcher doesn't auto-link one-to-many "
            "splits yet.",
        )

    tied = _find_tied_candidates(ledger_row, pool, config)
    if tied:
        ids = [row[id_field] for row, _ in tied]
        scores = ", ".join(f"{score.total:.1f}" for _, score in tied)
        return _ExceptionHit(
            "DUPLICATE_CANDIDATE",
            ids,
            f"{len(tied)} {side} candidates for {ledger_id} scored within "
            f"{config.classifier.duplicate_tie_epsilon} points of each other ({scores}) "
            "— too close to auto-resolve safely.",
            f"Manually review candidates {', '.join(ids)} to determine which (if any) "
            "is correct.",
        )

    outside = _find_amount_match_outside_window(ledger_row, pool, config)
    if outside is not None:
        candidate, date_diff = outside
        return _ExceptionHit(
            "TIMING_DRIFT_EXCEEDED",
            [candidate[id_field]],
            f"{side.capitalize()} candidate {candidate[id_field]} matches {ledger_id}'s "
            f"amount exactly but is {date_diff} days away — beyond the "
            f"{config.date_window.candidate_search_days}-day matching window.",
            "Consider widening the date-matching window, or manually confirm this "
            "settlement.",
        )

    if (
        tier2_result.score is not None
        and tier2_result.score.amount_score < config.thresholds.llm_tier
    ):
        return _ExceptionHit(
            "AMOUNT_MISMATCH_BEYOND_TOLERANCE",
            [tier2_result.candidate_id],
            f"Best {side} candidate {tier2_result.candidate_id} for {ledger_id} has an "
            f"amount score of {tier2_result.score.amount_score:.1f} — its amount differs "
            "beyond what the configured fee schedule can explain.",
            "Check whether an additional or different fee was applied to this "
            "settlement.",
        )

    if llm_outcome is not None:
        if llm_outcome.final_outcome == "LOW_CONFIDENCE_AMBIGUOUS":
            return _ExceptionHit(
                "LOW_CONFIDENCE_AMBIGUOUS",
                [llm_outcome.candidate_id],
                f"{'/'.join(llm_outcome.tiers_used)} could not confidently decide on {side} "
                f"candidate {llm_outcome.candidate_id}: {llm_outcome.rationale}",
                "Route to human review; no tier was confident enough to auto-resolve.",
            )
        if llm_outcome.final_outcome == "LLM_OUTPUT_UNPARSEABLE":
            return _ExceptionHit(
                "LLM_OUTPUT_UNPARSEABLE",
                [llm_outcome.candidate_id],
                f"{llm_outcome.tiers_used[-1]} returned malformed output twice in a row "
                f"while adjudicating {side} candidate {llm_outcome.candidate_id} for "
                f"{ledger_id}.",
                "Retry manually or inspect the raw model output in the audit log.",
            )

    if _candidates_within_window(ledger_row, pool, config):
        rationale = (
            f"No {side} candidate for {ledger_id} could be confidently confirmed."
        )
    else:
        rationale = (
            f"No plausible {side} candidate was found for {ledger_id} within the "
            f"{config.date_window.candidate_search_days}-day search window "
            f"(ledger date {ledger_row['date']})."
        )
    if llm_outcome is not None and llm_outcome.final_outcome == "NO_MATCH":
        rationale += (
            f" The LLM explicitly reviewed candidate {llm_outcome.candidate_id} and "
            f"rejected it: {llm_outcome.rationale}"
        )

    return _ExceptionHit(
        "MISSING_COUNTERPART",
        [],
        rationale,
        f"Check the {side} statement/dashboard for a settlement of "
        f"₹{ledger_row['amount']} dated near {ledger_row['date']}; if none exists, "
        "this transaction is likely still pending.",
    )


def classify_record(
    ledger_row: dict,
    bank_pool: list[dict],
    gateway_pool: list[dict],
    bank_tier2: FuzzyMatchResult,
    gateway_tier2: FuzzyMatchResult,
    bank_llm: AdjudicationOutcome | None,
    gateway_llm: AdjudicationOutcome | None,
    config: MatchingConfig,
) -> ExceptionRecord:
    """Classify one still-unresolved ledger record into exactly one named
    exception type, considering both sides and their full evidence."""
    ledger_id = ledger_row["ledger_id"]
    hits: list[_ExceptionHit] = []

    if bank_tier2.outcome != "AUTO_MATCH" and not (
        bank_llm and bank_llm.final_outcome == "MATCH"
    ):
        hits.append(
            _detect_side_exception(
                ledger_row, "bank", "bank_id", bank_pool, bank_tier2, bank_llm, config
            )
        )
    if gateway_tier2.outcome != "AUTO_MATCH" and not (
        gateway_llm and gateway_llm.final_outcome == "MATCH"
    ):
        hits.append(
            _detect_side_exception(
                ledger_row,
                "gateway",
                "gateway_id",
                gateway_pool,
                gateway_tier2,
                gateway_llm,
                config,
            )
        )

    if not hits:
        return ExceptionRecord(
            exception_type="UNCLASSIFIED",
            ledger_id=ledger_id,
            affected_ids=[ledger_id],
            rationale=(
                f"{ledger_id} was sent to the exception classifier but both sides "
                "already appear resolved."
            ),
            suggested_action="Investigate pipeline wiring — this shouldn't happen.",
        )

    hits.sort(key=lambda hit: PRIORITY.index(hit.exception_type))
    best = hits[0]
    return ExceptionRecord(
        exception_type=best.exception_type,
        ledger_id=ledger_id,
        affected_ids=[ledger_id, *best.affected_ids],
        rationale=best.rationale,
        suggested_action=best.suggested_action,
    )
