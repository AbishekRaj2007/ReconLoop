"""Run the reconciliation pipeline over data/raw (plan.md §9 Phases 2 + 3).

Tier 1 + Tier 2 (deterministic) always run. Tier 3 + Tier 4 (LLM
adjudication) only run with --use-llm, since they need real GROQ_API_KEY /
ANTHROPIC_API_KEY credentials and cost real money per call — see CLAUDE.md:
"don't burn API calls in CI." Without --use-llm, records Tier 2 escalated
are reported as pending Tier 3/4, not guessed at.

The exception classifier (Phase 4) and persistence (Phase 6) don't exist
yet, so LOW_CONFIDENCE_AMBIGUOUS / NO_MATCH / LLM_OUTPUT_UNPARSEABLE are
reported as outcome counts here, not yet bucketed into a named exception
report.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.core.config import MatchingConfig, load_matching_config
from app.core.matcher.exact_match import match_exact
from app.core.matcher.fuzzy_match import match_fuzzy
from app.core.matcher.llm_adjudicator import (
    AdjudicationOutcome,
    LLMClient,
    adjudicate,
    build_adjudication_request,
)

SIDES = ("bank", "gateway")
MATCHED_STATES = {"AUTO_MATCH", "MATCH"}


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


@dataclass(frozen=True)
class Escalation:
    ledger_id: str
    side: str
    candidate_row: dict
    fuzzy_score: float


def run_tier1_and_tier2(
    ledger_rows: list[dict],
    bank_rows: list[dict],
    gateway_rows: list[dict],
    config: MatchingConfig,
) -> tuple[int, dict[str, dict[str, str]], list[Escalation], dict[str, dict]]:
    """Returns (tier1_matched_count, side_status_by_ledger_id, escalations,
    ledger_row_by_id)."""
    tier1 = match_exact(ledger_rows, bank_rows, gateway_rows)

    consumed_bank_ids = {bid for m in tier1.matches for bid in m.bank_ids}
    consumed_gateway_ids = {gid for m in tier1.matches for gid in m.gateway_ids}
    pools = {
        "bank": (
            [r for r in bank_rows if r["bank_id"] not in consumed_bank_ids],
            "bank_id",
        ),
        "gateway": (
            [r for r in gateway_rows if r["gateway_id"] not in consumed_gateway_ids],
            "gateway_id",
        ),
    }
    row_by_id = {
        side: {row[id_field]: row for row in rows}
        for side, (rows, id_field) in pools.items()
    }
    ledger_by_id = {row["ledger_id"]: row for row in ledger_rows}

    side_status: dict[str, dict[str, str]] = {}
    escalations: list[Escalation] = []

    for ledger_id in tier1.unmatched_ledger_ids:
        ledger_row = ledger_by_id[ledger_id]
        side_status[ledger_id] = {}
        for side, (pool_rows, id_field) in pools.items():
            result = match_fuzzy(
                ledger_row, pool_rows, side=side, id_field=id_field, config=config
            )
            side_status[ledger_id][side] = result.outcome
            if result.outcome == "ESCALATE_TIER3":
                escalations.append(
                    Escalation(
                        ledger_id=ledger_id,
                        side=side,
                        candidate_row=row_by_id[side][result.candidate_id],
                        fuzzy_score=result.score.total,
                    )
                )

    return len(tier1.matches), side_status, escalations, ledger_by_id


def _fully_resolved_count(side_status: dict[str, dict[str, str]]) -> int:
    return sum(
        1
        for statuses in side_status.values()
        if all(statuses.get(side) in MATCHED_STATES for side in SIDES)
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


def run_pipeline(
    raw_dir: Path,
    config_path: Path,
    use_llm: bool = False,
    tier3_client: LLMClient | None = None,
    tier4_client: LLMClient | None = None,
) -> dict:
    config = load_matching_config(config_path)
    ledger_rows = read_csv(raw_dir / "ledger.csv")
    bank_rows = read_csv(raw_dir / "bank_statement.csv")
    gateway_rows = read_csv(raw_dir / "gateway_settlement.csv")

    tier1_matched, side_status, escalations, ledger_by_id = run_tier1_and_tier2(
        ledger_rows, bank_rows, gateway_rows, config
    )
    tier2_fully_resolved = _fully_resolved_count(side_status)
    total = len(ledger_rows)

    report: dict = {
        "total": total,
        "tier1_matched": tier1_matched,
        "tier2_fully_resolved": tier2_fully_resolved,
        "tier2_escalate_sides": len(escalations),
        "tier2_no_candidate_sides": sum(
            1
            for statuses in side_status.values()
            for outcome in statuses.values()
            if outcome == "NO_CANDIDATE"
        ),
    }

    if not use_llm:
        resolved = tier1_matched + tier2_fully_resolved
        report["unresolved_after_tier2"] = len(side_status) - tier2_fully_resolved
        report["first_pass_match_rate"] = resolved / total if total else 0.0
        return report

    if tier3_client is None or tier4_client is None:
        from app.core.matcher.llm_clients import AnthropicLLMClient, GroqLLMClient

        tier3_client = tier3_client or GroqLLMClient(model=config.llm.tier3.model)
        tier4_client = tier4_client or AnthropicLLMClient(model=config.llm.tier4.model)

    llm_side_status = {lid: dict(statuses) for lid, statuses in side_status.items()}
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
        llm_side_status[outcome.ledger_id][outcome.side] = outcome.final_outcome
        if "TIER_4" in outcome.tiers_used:
            tier4_used += 1
        total_calls += len(outcome.audit_log)
        total_cost += sum((e.estimated_cost_usd for e in outcome.audit_log), Decimal(0))

    llm_newly_resolved = _fully_resolved_count(llm_side_status) - tier2_fully_resolved
    resolved = tier1_matched + tier2_fully_resolved + llm_newly_resolved

    report.update(
        {
            "llm_outcome_counts": outcome_counts,
            "tier4_used_count": tier4_used,
            "total_llm_calls": total_calls,
            "total_llm_cost_usd": total_cost,
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
        return

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


if __name__ == "__main__":
    main()
