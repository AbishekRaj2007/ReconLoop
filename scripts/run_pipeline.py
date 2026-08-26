"""Run Tier 1 + Tier 2 of the reconciliation pipeline over data/raw (plan.md
§9 Phase 2 deliverable: "Running the pipeline on the Phase 1 data resolves a
first-pass match rate").

LLM adjudication (Tier 3/4, Phase 3), the exception classifier (Phase 4), and
persistence (Phase 6) don't exist yet — this reports what the deterministic
tiers alone can resolve, honestly labeling everything else as escalated or
unresolved rather than guessing.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.core.config import load_matching_config
from app.core.matcher.exact_match import match_exact
from app.core.matcher.fuzzy_match import match_fuzzy


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def run_pipeline(raw_dir: Path, config_path: Path) -> dict:
    config = load_matching_config(config_path)
    ledger_rows = read_csv(raw_dir / "ledger.csv")
    bank_rows = read_csv(raw_dir / "bank_statement.csv")
    gateway_rows = read_csv(raw_dir / "gateway_settlement.csv")

    tier1 = match_exact(ledger_rows, bank_rows, gateway_rows)

    consumed_bank_ids = {bid for m in tier1.matches for bid in m.bank_ids}
    consumed_gateway_ids = {gid for m in tier1.matches for gid in m.gateway_ids}
    remaining_bank_rows = [
        r for r in bank_rows if r["bank_id"] not in consumed_bank_ids
    ]
    remaining_gateway_rows = [
        r for r in gateway_rows if r["gateway_id"] not in consumed_gateway_ids
    ]

    ledger_by_id = {row["ledger_id"]: row for row in ledger_rows}

    tier2_outcomes: dict[str, list] = {
        "AUTO_MATCH": [],
        "ESCALATE_TIER3": [],
        "NO_CANDIDATE": [],
    }
    fully_resolved_tier2 = 0
    for ledger_id in tier1.unmatched_ledger_ids:
        ledger_row = ledger_by_id[ledger_id]
        bank_result = match_fuzzy(
            ledger_row,
            remaining_bank_rows,
            side="bank",
            id_field="bank_id",
            config=config,
        )
        gateway_result = match_fuzzy(
            ledger_row,
            remaining_gateway_rows,
            side="gateway",
            id_field="gateway_id",
            config=config,
        )
        tier2_outcomes[bank_result.outcome].append(bank_result)
        tier2_outcomes[gateway_result.outcome].append(gateway_result)
        if (
            bank_result.outcome == "AUTO_MATCH"
            and gateway_result.outcome == "AUTO_MATCH"
        ):
            fully_resolved_tier2 += 1

    total = len(ledger_rows)
    tier1_matched = len(tier1.matches)
    resolved = tier1_matched + fully_resolved_tier2

    return {
        "total": total,
        "tier1_matched": tier1_matched,
        "tier2_fully_resolved": fully_resolved_tier2,
        "tier2_escalate_sides": len(tier2_outcomes["ESCALATE_TIER3"]),
        "tier2_no_candidate_sides": len(tier2_outcomes["NO_CANDIDATE"]),
        "unresolved_after_tier2": len(tier1.unmatched_ledger_ids)
        - fully_resolved_tier2,
        "first_pass_match_rate": resolved / total if total else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=REPO_ROOT / "data" / "raw")
    parser.add_argument(
        "--config", type=Path, default=REPO_ROOT / "config" / "matching_config.yaml"
    )
    args = parser.parse_args()

    report = run_pipeline(args.raw_dir, args.config)

    print(f"Total records:                {report['total']}")
    print(f"Tier 1 exact matches:         {report['tier1_matched']}")
    print(f"Tier 2 fully resolved:        {report['tier2_fully_resolved']}")
    print(
        f"Tier 2 escalated to Tier 3:   {report['tier2_escalate_sides']} (side-attempts)"
    )
    print(
        f"Tier 2 no plausible candidate:{report['tier2_no_candidate_sides']} (side-attempts)"
    )
    print(f"Unresolved after Tier 2:      {report['unresolved_after_tier2']}")
    print(f"First-pass match rate:        {report['first_pass_match_rate']:.1%}")


if __name__ == "__main__":
    main()
