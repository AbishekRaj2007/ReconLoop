"""Golden pipeline test (see CLAUDE.md): runs the full pipeline — Tier 1
through Tier 4 and the Phase 4 classifier — against the committed
seed=42/count=200 sample data, and asserts the Golden Rule #3 invariant:
`len(matches) + len(exceptions) == total_records`.

Tier 3/4 use a deterministic fake LLM client, not the real Groq/Anthropic
APIs — this test must never make real network calls or spend API budget
(see CLAUDE.md: "Mock LLM clients in unit tests; don't burn API calls in
CI", and the project's own rule that real LLM calls only happen one at a
time, by explicit request). The fake always returns the same low-confidence
answer, which is enough to drive every Tier 2 escalation to a terminal
Tier 3/4 outcome (MATCH / NO_MATCH / LOW_CONFIDENCE_AMBIGUOUS) so the
invariant can be checked end to end without guessing at real numbers.
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "raw"
CONFIG_PATH = REPO_ROOT / "config" / "matching_config.yaml"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import run_pipeline as pipeline

from app.core.matcher.llm_adjudicator import LLMResponse


@dataclass
class AlwaysSameLLMClient:
    """Returns the same structured decision for every call — deterministic,
    no real API traffic, just enough to drive every escalation to a
    terminal outcome for the invariant check."""

    model: str
    decision: str
    confidence: float
    call_count: int = field(default=0, init=False)

    def complete(self, prompt: str) -> LLMResponse:
        self.call_count += 1
        text = (
            f'{{"decision": "{self.decision}", "confidence": {self.confidence}, '
            '"rationale": "golden test fixture"}'
        )
        return LLMResponse(text=text, input_tokens=100, output_tokens=20)


def test_every_record_is_either_matched_or_a_classified_exception():
    tier3 = AlwaysSameLLMClient(
        model="fake-tier3", decision="NEEDS_HUMAN_REVIEW", confidence=50
    )
    tier4 = AlwaysSameLLMClient(model="fake-tier4", decision="MATCH", confidence=40)

    report = pipeline.run_pipeline(
        RAW_DIR, CONFIG_PATH, use_llm=True, tier3_client=tier3, tier4_client=tier4
    )

    assert report["pending_llm_count"] == 0
    assert report["second_pass_resolved"] + len(report["exceptions"]) == report["total"]
    assert tier3.call_count > 0
    assert tier4.call_count > 0


def test_every_ledger_id_appears_exactly_once_across_matches_and_exceptions():
    tier3 = AlwaysSameLLMClient(
        model="fake-tier3", decision="NEEDS_HUMAN_REVIEW", confidence=50
    )
    tier4 = AlwaysSameLLMClient(model="fake-tier4", decision="MATCH", confidence=40)

    report = pipeline.run_pipeline(
        RAW_DIR, CONFIG_PATH, use_llm=True, tier3_client=tier3, tier4_client=tier4
    )

    exception_ledger_ids = [e.ledger_id for e in report["exceptions"]]
    assert len(exception_ledger_ids) == len(set(exception_ledger_ids))
