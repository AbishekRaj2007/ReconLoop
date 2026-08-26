"""Run the reconciliation pipeline over data/raw (plan.md §9 Phases 2-4).

Tier 1 + Tier 2 (deterministic) and the exception classifier always run.
Tier 3 + Tier 4 (LLM adjudication) only run with --use-llm, since they need
real GROQ_API_KEY / ANTHROPIC_API_KEY credentials and cost real money per
call — see CLAUDE.md: "don't burn API calls in CI."

Without --use-llm, records Tier 2 escalated are reported as pending Tier
3/4, not force-classified — the invariant `matches + exceptions == total`
only holds once every escalation has actually reached a terminal Tier 3/4
outcome (real or, in tests, a deterministic fake).
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.core.config import MatchingConfig, load_matching_config
from app.core.exceptions.classifier import ExceptionRecord, classify_record
from app.core.matcher.exact_match import match_exact
from app.core.matcher.fuzzy_match import FuzzyMatchResult, match_fuzzy
from app.core.matcher.llm_adjudicator import (
    AdjudicationOutcome,
    LLMClient,
    adjudicate,
    build_adjudication_request,
)

SIDES = ("bank", "gateway")
TIER_RANK = {"TIER_1": 1, "TIER_2": 2, "TIER_3": 3, "TIER_4": 4}


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


@dataclass(frozen=True)
class Escalation:
    ledger_id: str
    side: str
    candidate_row: dict
    fuzzy_score: float


@dataclass(frozen=True)
class MatchRecord:
    ledger_id: str
    bank_ids: list[str]
    gateway_ids: list[str]
    tier: str  # "TIER_1" | "TIER_2" | "TIER_3" | "TIER_4"


def run_tier1_and_tier2(
    ledger_rows: list[dict],
    bank_rows: list[dict],
    gateway_rows: list[dict],
    config: MatchingConfig,
) -> tuple[
    list[MatchRecord],
    dict[str, dict[str, FuzzyMatchResult]],
    list[Escalation],
    dict[str, dict],
    list[dict],
    list[dict],
]:
    """Returns (tier1_matches, tier2_results_by_ledger_id, escalations,
    ledger_row_by_id, remaining_bank_rows, remaining_gateway_rows)."""
    tier1 = match_exact(ledger_rows, bank_rows, gateway_rows)
    tier1_matches = [
        MatchRecord(m.ledger_id, m.bank_ids, m.gateway_ids, "TIER_1")
        for m in tier1.matches
    ]

    consumed_bank_ids = {bid for m in tier1.matches for bid in m.bank_ids}
    consumed_gateway_ids = {gid for m in tier1.matches for gid in m.gateway_ids}
    remaining_bank_rows = [
        r for r in bank_rows if r["bank_id"] not in consumed_bank_ids
    ]
    remaining_gateway_rows = [
        r for r in gateway_rows if r["gateway_id"] not in consumed_gateway_ids
    ]
    pools = {
        "bank": (remaining_bank_rows, "bank_id"),
        "gateway": (remaining_gateway_rows, "gateway_id"),
    }
    row_by_id = {
        side: {row[id_field]: row for row in rows}
        for side, (rows, id_field) in pools.items()
    }
    ledger_by_id = {row["ledger_id"]: row for row in ledger_rows}

    tier2_results: dict[str, dict[str, FuzzyMatchResult]] = {}
    escalations: list[Escalation] = []

    for ledger_id in tier1.unmatched_ledger_ids:
        ledger_row = ledger_by_id[ledger_id]
        tier2_results[ledger_id] = {}
        for side, (pool_rows, id_field) in pools.items():
            result = match_fuzzy(
                ledger_row, pool_rows, side=side, id_field=id_field, config=config
            )
            tier2_results[ledger_id][side] = result
            if result.outcome == "ESCALATE_TIER3":
                escalations.append(
                    Escalation(
                        ledger_id=ledger_id,
                        side=side,
                        candidate_row=row_by_id[side][result.candidate_id],
                        fuzzy_score=result.score.total,
                    )
                )

    return (
        tier1_matches,
        tier2_results,
        escalations,
        ledger_by_id,
        remaining_bank_rows,
        remaining_gateway_rows,
    )


def run_llm_tiers(
    escalations: list[Escalation],
    ledger_by_id: dict[str, dict],
    config: MatchingConfig,
    tier3_client: LLMClient,
    tier4_client: LLMClient,
) -> list[AdjudicationOutcome]:
    outcomes = []
    for escalation in escalations:
        request = build_adjudication_request(
            ledger_by_id[escalation.ledger_id],
            escalation.candidate_row,
            side=escalation.side,
            id_field=f"{escalation.side}_id",
            fuzzy_score=escalation.fuzzy_score,
        )
        outcomes.append(adjudicate(request, tier3_client, tier4_client, config))
    return outcomes


def _side_resolution(
    result: FuzzyMatchResult, llm_outcome: AdjudicationOutcome | None
) -> tuple[str, str] | None:
    """Returns (matched_id, tier) if this side resolved, else None."""
    if result.outcome == "AUTO_MATCH":
        return result.candidate_id, "TIER_2"
    if llm_outcome is not None and llm_outcome.final_outcome == "MATCH":
        tier = "TIER_4" if "TIER_4" in llm_outcome.tiers_used else "TIER_3"
        return llm_outcome.candidate_id, tier
    return None


def partition_records(
    tier2_results: dict[str, dict[str, FuzzyMatchResult]],
    llm_outcomes: dict[str, dict[str, AdjudicationOutcome]],
    ledger_by_id: dict[str, dict],
    remaining_bank_rows: list[dict],
    remaining_gateway_rows: list[dict],
    config: MatchingConfig,
) -> tuple[list[MatchRecord], int, list[ExceptionRecord]]:
    """Partitions every Tier-1-unmatched ledger record into resolved (both
    sides matched, whether by Tier 2 or Tier 3/4 — recorded with the highest
    tier either side needed), pending (still waiting on an escalation that
    hasn't been adjudicated), or a classified exception.

    Returns (matches, pending_count, exceptions).
    """
    matches: list[MatchRecord] = []
    pending_count = 0
    exceptions: list[ExceptionRecord] = []

    for ledger_id, sides in tier2_results.items():
        bank_result = sides["bank"]
        gateway_result = sides["gateway"]
        bank_llm = llm_outcomes.get(ledger_id, {}).get("bank")
        gateway_llm = llm_outcomes.get(ledger_id, {}).get("gateway")

        bank_resolution = _side_resolution(bank_result, bank_llm)
        gateway_resolution = _side_resolution(gateway_result, gateway_llm)

        if bank_resolution is not None and gateway_resolution is not None:
            bank_id, bank_tier = bank_resolution
            gateway_id, gateway_tier = gateway_resolution
            tier = max(bank_tier, gateway_tier, key=TIER_RANK.get)
            matches.append(MatchRecord(ledger_id, [bank_id], [gateway_id], tier))
            continue

        bank_pending = bank_result.outcome == "ESCALATE_TIER3" and bank_llm is None
        gateway_pending = (
            gateway_result.outcome == "ESCALATE_TIER3" and gateway_llm is None
        )
        if bank_pending or gateway_pending:
            pending_count += 1
            continue

        exceptions.append(
            classify_record(
                ledger_by_id[ledger_id],
                remaining_bank_rows,
                remaining_gateway_rows,
                bank_result,
                gateway_result,
                bank_llm,
                gateway_llm,
                config,
            )
        )

    return matches, pending_count, exceptions


def run_pipeline(
    raw_dir: Path,
    config_path: Path,
    use_llm: bool = False,
    tier3_client: LLMClient | None = None,
    tier4_client: LLMClient | None = None,
) -> dict:
    start_time = time.perf_counter()
    config = load_matching_config(config_path)
    ledger_rows = read_csv(raw_dir / "ledger.csv")
    bank_rows = read_csv(raw_dir / "bank_statement.csv")
    gateway_rows = read_csv(raw_dir / "gateway_settlement.csv")

    (
        tier1_matches,
        tier2_results,
        escalations,
        ledger_by_id,
        remaining_bank_rows,
        remaining_gateway_rows,
    ) = run_tier1_and_tier2(ledger_rows, bank_rows, gateway_rows, config)
    tier1_matched = len(tier1_matches)

    tier2_fully_resolved = sum(
        1
        for sides in tier2_results.values()
        if all(sides[side].outcome == "AUTO_MATCH" for side in SIDES)
    )
    total = len(ledger_rows)

    llm_outcomes: dict[str, dict[str, AdjudicationOutcome]] = {}
    llm_stats: dict | None = None

    if use_llm:
        if tier3_client is None or tier4_client is None:
            from app.core.matcher.llm_clients import AnthropicLLMClient, GroqLLMClient

            tier3_client = tier3_client or GroqLLMClient(model=config.llm.tier3.model)
            tier4_client = tier4_client or AnthropicLLMClient(
                model=config.llm.tier4.model
            )

        outcome_counts = {
            "MATCH": 0,
            "NO_MATCH": 0,
            "LOW_CONFIDENCE_AMBIGUOUS": 0,
            "LLM_OUTPUT_UNPARSEABLE": 0,
        }
        tier4_used = 0
        total_cost = Decimal(0)
        total_calls = 0

        for outcome in run_llm_tiers(
            escalations, ledger_by_id, config, tier3_client, tier4_client
        ):
            outcome_counts[outcome.final_outcome] += 1
            llm_outcomes.setdefault(outcome.ledger_id, {})[outcome.side] = outcome
            if "TIER_4" in outcome.tiers_used:
                tier4_used += 1
            total_calls += len(outcome.audit_log)
            total_cost += sum(
                (e.estimated_cost_usd for e in outcome.audit_log), Decimal(0)
            )

        llm_stats = {
            "llm_outcome_counts": outcome_counts,
            "tier4_used_count": tier4_used,
            "total_llm_calls": total_calls,
            "total_llm_cost_usd": total_cost,
        }

    tier2plus_matches, pending_count, exceptions = partition_records(
        tier2_results,
        llm_outcomes,
        ledger_by_id,
        remaining_bank_rows,
        remaining_gateway_rows,
        config,
    )
    matches = tier1_matches + tier2plus_matches
    resolved = len(matches)
    wall_clock_seconds = time.perf_counter() - start_time

    report: dict = {
        "total": total,
        "tier1_matched": tier1_matched,
        "tier2_fully_resolved": tier2_fully_resolved,
        "tier2_escalate_sides": len(escalations),
        "tier2_no_candidate_sides": sum(
            1
            for sides in tier2_results.values()
            for result in sides.values()
            if result.outcome == "NO_CANDIDATE"
        ),
        "pending_llm_count": pending_count,
        "matches": matches,
        "exceptions": exceptions,
        "exception_type_counts": Counter(e.exception_type for e in exceptions),
        "tier_attribution": Counter(m.tier for m in matches),
        "wall_clock_seconds": wall_clock_seconds,
        "records_per_second": total / wall_clock_seconds if wall_clock_seconds else 0.0,
    }

    if not use_llm:
        report["unresolved_after_tier2"] = len(tier2_results) - tier2_fully_resolved
        report["first_pass_match_rate"] = resolved / total if total else 0.0
        return report

    report.update(llm_stats)
    report.update(
        {
            "second_pass_resolved": resolved,
            "unresolved_after_tier4": total - resolved,
            "second_pass_match_rate": resolved / total if total else 0.0,
        }
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=REPO_ROOT / "data" / "raw")
    parser.add_argument(
        "--config", type=Path, default=REPO_ROOT / "config" / "matching_config.yaml"
    )
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="Run Tier 3/4 on escalated records. Requires GROQ_API_KEY and "
        "ANTHROPIC_API_KEY (see .env.example) and makes real, billed API calls.",
    )
    args = parser.parse_args()

    if args.use_llm:
        from dotenv import load_dotenv

        load_dotenv(REPO_ROOT / ".env")

    try:
        report = run_pipeline(args.raw_dir, args.config, use_llm=args.use_llm)
    except Exception as exc:
        if args.use_llm:
            raise SystemExit(
                "--use-llm requires GROQ_API_KEY and ANTHROPIC_API_KEY to be set "
                f"(see .env.example). Underlying error: {exc}"
            ) from exc
        raise

    print(f"Total records:                {report['total']}")
    print(f"Tier 1 exact matches:         {report['tier1_matched']}")
    print(f"Tier 2 fully resolved:        {report['tier2_fully_resolved']}")
    print(
        f"Tier 2 escalated to Tier 3:   {report['tier2_escalate_sides']} (side-attempts)"
    )
    print(
        f"Tier 2 no plausible candidate:{report['tier2_no_candidate_sides']} (side-attempts)"
    )

    if not args.use_llm:
        print(f"Unresolved after Tier 2:      {report['unresolved_after_tier2']}")
        print(f"First-pass match rate:        {report['first_pass_match_rate']:.1%}")
        print(
            f"Pending Tier 3/4 (run with --use-llm to resolve): {report['pending_llm_count']}"
        )
    else:
        counts = report["llm_outcome_counts"]
        print("--- LLM tiers (Tier 3 + Tier 4) ---")
        print(f"LLM calls made:               {report['total_llm_calls']}")
        print(f"Tier 4 escalations:           {report['tier4_used_count']}")
        print(f"Estimated LLM cost (USD):     ${report['total_llm_cost_usd']:.4f}")
        print(f"MATCH:                        {counts['MATCH']}")
        print(f"NO_MATCH:                     {counts['NO_MATCH']}")
        print(f"LOW_CONFIDENCE_AMBIGUOUS:     {counts['LOW_CONFIDENCE_AMBIGUOUS']}")
        print(f"LLM_OUTPUT_UNPARSEABLE:       {counts['LLM_OUTPUT_UNPARSEABLE']}")
        print(f"Unresolved after Tier 4:      {report['unresolved_after_tier4']}")
        print(f"Second-pass match rate:       {report['second_pass_match_rate']:.1%}")

    print("--- Exceptions (Phase 4 classifier) ---")
    print(f"Total exceptions:             {len(report['exceptions'])}")
    for exception_type, count in report["exception_type_counts"].most_common():
        print(f"  {exception_type:<32} {count}")


if __name__ == "__main__":
    main()
