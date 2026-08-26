"""Tests that scripts/run_pipeline.py's report["matches"] carries full match
detail (bank_ids, gateway_ids, and which tier resolved each side) — needed
by Phase 5's evaluator.py to score against ground truth. Direct function
calls with injected fake LLM clients where needed; no subprocess, no real
API calls.
"""

import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "matching_config.yaml"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import run_pipeline as pipeline

from app.core.matcher.llm_adjudicator import LLMResponse


@dataclass
class FakeLLMClient:
    model: str
    responses: list[str] = field(default_factory=list)
    prompts_received: list[str] = field(default_factory=list)

    def complete(self, prompt: str) -> LLMResponse:
        self.prompts_received.append(prompt)
        text = self.responses[len(self.prompts_received) - 1]
        return LLMResponse(text=text, input_tokens=100, output_tokens=20)


def decision_json(decision: str, confidence: float) -> str:
    return f'{{"decision": "{decision}", "confidence": {confidence}, "rationale": "r"}}'


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_dataset(
    raw_dir: Path,
    bank_id: str,
    bank_narration: str,
    gateway_narration: str,
    gateway_date: str = "2026-06-03",
) -> None:
    raw_dir.mkdir()
    write_csv(
        raw_dir / "ledger.csv",
        ["ledger_id", "date", "amount", "narration", "counterparty_name"],
        [
            {
                "ledger_id": "TXN-000001",
                "date": "2026-06-01",
                "amount": "1000.00",
                "narration": "Payment received from Acme - Invoice INV00001",
                "counterparty_name": "Acme",
            }
        ],
    )
    write_csv(
        raw_dir / "bank_statement.csv",
        ["bank_id", "date", "amount", "narration", "account_fragment"],
        [
            {
                "bank_id": bank_id,
                "date": "2026-06-02",
                "amount": "1000.00",
                "narration": bank_narration,
                "account_fragment": "1111",
            }
        ],
    )
    write_csv(
        raw_dir / "gateway_settlement.csv",
        ["gateway_id", "date", "amount", "narration", "merchant_id"],
        [
            {
                "gateway_id": "pay_00001",
                "date": gateway_date,
                "amount": "1000.00",
                "narration": gateway_narration,
                "merchant_id": "MCHT0001",
            }
        ],
    )


LEDGER_NARRATION = "Payment received from Acme - Invoice INV00001"


def test_tier1_match_is_recorded_with_tier1_attribution(tmp_path):
    raw_dir = tmp_path / "raw"
    build_dataset(
        raw_dir,
        bank_id="0000001",
        bank_narration="NEFT/A/INV00001",
        gateway_narration="Payout",
    )

    report = pipeline.run_pipeline(raw_dir, CONFIG_PATH)

    assert len(report["matches"]) == 1
    match = report["matches"][0]
    assert match.ledger_id == "TXN-000001"
    assert match.bank_ids == ["0000001"]
    assert match.gateway_ids == ["pay_00001"]
    assert match.tier == "TIER_1"


def test_tier2_auto_match_is_recorded_with_tier2_attribution(tmp_path):
    """Bank id is corrupted (so Tier 1 can't resolve it), but both sides'
    narration matches the ledger's exactly and both dates are 1 day out,
    giving both sides a Tier 2 score >= the auto-match threshold."""
    raw_dir = tmp_path / "raw"
    build_dataset(
        raw_dir,
        bank_id="0000010",  # corrupted: doesn't normalize to "1"
        bank_narration=LEDGER_NARRATION,
        gateway_narration=LEDGER_NARRATION,
        gateway_date="2026-06-02",
    )

    report = pipeline.run_pipeline(raw_dir, CONFIG_PATH)

    assert report["tier1_matched"] == 0
    assert len(report["matches"]) == 1
    match = report["matches"][0]
    assert match.bank_ids == ["0000010"]
    assert match.gateway_ids == ["pay_00001"]
    assert match.tier == "TIER_2"


def test_tier3_match_is_recorded_with_tier3_attribution(tmp_path):
    raw_dir = tmp_path / "raw"
    build_dataset(
        raw_dir,
        bank_id="0000010",
        bank_narration="NEFT/A/INV00001",
        gateway_narration="Payout",
    )

    tier3 = FakeLLMClient(
        model="fake-tier3",
        responses=[decision_json("MATCH", 95), decision_json("MATCH", 95)],
    )
    tier4 = FakeLLMClient(model="fake-tier4", responses=[])

    report = pipeline.run_pipeline(
        raw_dir, CONFIG_PATH, use_llm=True, tier3_client=tier3, tier4_client=tier4
    )

    assert len(report["matches"]) == 1
    assert report["matches"][0].tier == "TIER_3"


def test_tier4_match_is_recorded_when_either_side_needed_tier4(tmp_path):
    raw_dir = tmp_path / "raw"
    build_dataset(
        raw_dir,
        bank_id="0000010",
        bank_narration="NEFT/A/INV00001",
        gateway_narration="Payout",
    )

    # Bank side needs Tier 4 (low Tier 3 confidence); gateway resolves at Tier 3.
    tier3 = FakeLLMClient(
        model="fake-tier3",
        responses=[decision_json("MATCH", 40), decision_json("MATCH", 95)],
    )
    tier4 = FakeLLMClient(model="fake-tier4", responses=[decision_json("MATCH", 90)])

    report = pipeline.run_pipeline(
        raw_dir, CONFIG_PATH, use_llm=True, tier3_client=tier3, tier4_client=tier4
    )

    assert len(report["matches"]) == 1
    assert report["matches"][0].tier == "TIER_4"


def test_second_pass_resolved_equals_total_match_count(tmp_path):
    raw_dir = tmp_path / "raw"
    build_dataset(
        raw_dir,
        bank_id="0000001",
        bank_narration="NEFT/A/INV00001",
        gateway_narration="Payout",
    )

    report = pipeline.run_pipeline(raw_dir, CONFIG_PATH)

    assert report["tier1_matched"] + 0 == len(report["matches"])
