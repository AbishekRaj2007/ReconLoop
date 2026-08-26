"""Tests for the typed matching_config.yaml loader.

Loads the real committed config/matching_config.yaml (not a fixture copy) so
a change to the file's shape is caught here rather than silently breaking
fuzzy_match.py at runtime.
"""

from decimal import Decimal
from pathlib import Path

from app.core.config import load_matching_config

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "matching_config.yaml"


def test_loads_score_weights():
    config = load_matching_config(CONFIG_PATH)

    assert config.score_weights.amount == 0.40
    assert config.score_weights.date == 0.35
    assert config.score_weights.narration == 0.25


def test_loads_thresholds():
    config = load_matching_config(CONFIG_PATH)

    assert config.thresholds.auto_match == 90
    assert config.thresholds.llm_tier == 60
    assert config.thresholds.tier3_confidence_escalation == 70


def test_loads_date_window():
    config = load_matching_config(CONFIG_PATH)

    assert config.date_window.max_days == 5
    assert config.date_window.candidate_search_days == 5


def test_loads_fee_schedule_as_decimal_not_float():
    config = load_matching_config(CONFIG_PATH)

    fee_schedule = config.amount_tolerance.fee_schedule
    assert fee_schedule.percentage_fee == Decimal("0.02")
    assert fee_schedule.gst_on_fee == Decimal("0.18")
    assert isinstance(fee_schedule.percentage_fee, Decimal)


def test_loads_llm_tier_provider_and_model():
    config = load_matching_config(CONFIG_PATH)

    assert config.llm.tier3.provider == "groq"
    assert config.llm.tier3.model == "llama-3.3-70b-versatile"
    assert config.llm.tier4.provider == "anthropic"
    assert config.llm.tier4.model == "claude-sonnet-4-5"


def test_loads_llm_max_malformed_retries():
    config = load_matching_config(CONFIG_PATH)

    assert config.llm.max_malformed_retries == 1


def test_loads_classifier_duplicate_tie_epsilon():
    config = load_matching_config(CONFIG_PATH)

    assert config.classifier.duplicate_tie_epsilon == 2.0


def test_loads_llm_pricing_as_decimal_not_float():
    config = load_matching_config(CONFIG_PATH)

    assert config.llm.tier3.cost_per_1k_input_tokens == Decimal("0.00059")
    assert config.llm.tier3.cost_per_1k_output_tokens == Decimal("0.00079")
    assert config.llm.tier4.cost_per_1k_input_tokens == Decimal("0.003")
    assert config.llm.tier4.cost_per_1k_output_tokens == Decimal("0.015")
    assert isinstance(config.llm.tier3.cost_per_1k_input_tokens, Decimal)
