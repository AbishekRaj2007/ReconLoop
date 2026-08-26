"""Typed loader for config/matching_config.yaml (see plan.md §11.1).

All matcher tolerances/thresholds live in that YAML file, never hardcoded in
code — this module is the one place that reads it, so Tier 2 (and later, the
LLM tiers) share a single typed view of it instead of re-parsing YAML.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml


@dataclass(frozen=True)
class ScoreWeights:
    amount: float
    date: float
    narration: float


@dataclass(frozen=True)
class Thresholds:
    auto_match: float
    llm_tier: float
    tier3_confidence_escalation: float


@dataclass(frozen=True)
class DateWindow:
    max_days: int
    candidate_search_days: int


@dataclass(frozen=True)
class FeeSchedule:
    percentage_fee: Decimal
    gst_on_fee: Decimal


@dataclass(frozen=True)
class AmountTolerance:
    exact_match_paise: int
    fee_schedule: FeeSchedule


@dataclass(frozen=True)
class LLMTierConfig:
    provider: str
    model: str
    cost_per_1k_input_tokens: Decimal
    cost_per_1k_output_tokens: Decimal


@dataclass(frozen=True)
class LLMConfig:
    tier3: LLMTierConfig
    tier4: LLMTierConfig
    max_malformed_retries: int


@dataclass(frozen=True)
class MatchingConfig:
    score_weights: ScoreWeights
    thresholds: Thresholds
    date_window: DateWindow
    amount_tolerance: AmountTolerance
    llm: LLMConfig | None = None


def _load_llm_tier_config(raw: dict) -> LLMTierConfig:
    return LLMTierConfig(
        provider=raw["provider"],
        model=raw["model"],
        cost_per_1k_input_tokens=Decimal(str(raw["cost_per_1k_input_tokens"])),
        cost_per_1k_output_tokens=Decimal(str(raw["cost_per_1k_output_tokens"])),
    )


def load_matching_config(path: Path) -> MatchingConfig:
    with path.open() as f:
        raw = yaml.safe_load(f)

    fee_schedule_raw = raw["amount_tolerance"]["fee_schedule"]
    llm_raw = raw["llm"]
    return MatchingConfig(
        score_weights=ScoreWeights(**raw["score_weights"]),
        thresholds=Thresholds(**raw["thresholds"]),
        date_window=DateWindow(**raw["date_window"]),
        amount_tolerance=AmountTolerance(
            exact_match_paise=raw["amount_tolerance"]["exact_match_paise"],
            fee_schedule=FeeSchedule(
                percentage_fee=Decimal(str(fee_schedule_raw["percentage_fee"])),
                gst_on_fee=Decimal(str(fee_schedule_raw["gst_on_fee"])),
            ),
        ),
        llm=LLMConfig(
            tier3=_load_llm_tier_config(llm_raw["tier3"]),
            tier4=_load_llm_tier_config(llm_raw["tier4"]),
            max_malformed_retries=llm_raw["max_malformed_retries"],
        ),
    )
