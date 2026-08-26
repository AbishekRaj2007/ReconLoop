"""Tests for the --use-llm path of scripts/run_pipeline.py, with fake LLM
clients injected directly (no subprocess, no real API calls, no keys
needed) — the CLI/dotenv wiring is exercised manually, not here.

The fixture ledger row has a corrupted bank_id, so Tier 1 (which requires
both bank AND gateway hits to call something a match) leaves the whole
record unresolved — both the bank side and the (otherwise clean) gateway
side end up escalated to Tier 3, in that order.
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


def build_id_format_drift_dataset(raw_dir: Path) -> None:
    """One record whose bank_id is corrupted: Tier 1 requires both bank AND
    gateway hits, so the whole record (both sides) escalates to Tier 2/3,
    even though the gateway ID alone would have matched."""
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
                "bank_id": "0000010",  # corrupted: doesn't normalize to "1"
                "date": "2026-06-02",
                "amount": "1000.00",
                "narration": "NEFT/A/INV00001",
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
                "date": "2026-06-03",
                "amount": "1000.00",
                "narration": "Payout - MCHT0001 - Order ORD00001",
                "merchant_id": "MCHT0001",
            }
        ],
    )


def test_use_llm_resolves_both_escalated_sides_via_tier3_match(tmp_path):
    raw_dir = tmp_path / "raw"
    build_id_format_drift_dataset(raw_dir)

    # Escalation order is bank, then gateway (see run_tier1_and_tier2).
    tier3 = FakeLLMClient(
        model="fake-tier3",
        responses=[decision_json("MATCH", 95), decision_json("MATCH", 95)],
    )
    tier4 = FakeLLMClient(model="fake-tier4", responses=[])

    report = pipeline.run_pipeline(
        raw_dir, CONFIG_PATH, use_llm=True, tier3_client=tier3, tier4_client=tier4
    )

    assert report["tier1_matched"] == 0
    assert report["tier2_escalate_sides"] == 2
    assert report["llm_outcome_counts"]["MATCH"] == 2
    assert report["second_pass_resolved"] == 1
    assert report["second_pass_match_rate"] == 1.0
    assert tier4.prompts_received == []


def test_use_llm_escalates_low_confidence_side_to_tier4_and_counts_it(tmp_path):
    raw_dir = tmp_path / "raw"
    build_id_format_drift_dataset(raw_dir)

    # Bank side: low confidence -> escalates to Tier 4. Gateway side:
    # confident at Tier 3 -> no escalation.
    tier3 = FakeLLMClient(
        model="fake-tier3",
        responses=[decision_json("MATCH", 40), decision_json("MATCH", 95)],
    )
    tier4 = FakeLLMClient(model="fake-tier4", responses=[decision_json("MATCH", 90)])

    report = pipeline.run_pipeline(
        raw_dir, CONFIG_PATH, use_llm=True, tier3_client=tier3, tier4_client=tier4
    )

    assert report["tier4_used_count"] == 1
    assert report["llm_outcome_counts"]["MATCH"] == 2
    assert report["total_llm_calls"] == 3  # bank tier3 + bank tier4 + gateway tier3
    assert report["total_llm_cost_usd"] > 0
    assert report["second_pass_resolved"] == 1


def test_use_llm_reports_unresolved_when_one_side_is_a_confident_no_match(tmp_path):
    raw_dir = tmp_path / "raw"
    build_id_format_drift_dataset(raw_dir)

    tier3 = FakeLLMClient(
        model="fake-tier3",
        responses=[decision_json("NO_MATCH", 90), decision_json("MATCH", 95)],
    )
    tier4 = FakeLLMClient(model="fake-tier4", responses=[])

    report = pipeline.run_pipeline(
        raw_dir, CONFIG_PATH, use_llm=True, tier3_client=tier3, tier4_client=tier4
    )

    assert report["llm_outcome_counts"]["NO_MATCH"] == 1
    assert report["llm_outcome_counts"]["MATCH"] == 1
    assert report["second_pass_resolved"] == 0
    assert report["unresolved_after_tier4"] == 1
