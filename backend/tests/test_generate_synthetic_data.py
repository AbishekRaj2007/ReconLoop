"""Tests for the Phase 1 synthetic data generator.

Runs the generator as a subprocess (not imported directly) so these tests exercise
exactly what a user invokes from the README, and so generator runs used for testing
never touch the real data/raw or data/ground_truth output committed to the repo.
"""

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPO_ROOT / "data" / "generator" / "generate_synthetic_data.py"


def run_generator(
    tmp_path: Path, seed: int, count: int, name: str
) -> tuple[Path, Path]:
    raw_dir = tmp_path / name / "raw"
    gt_dir = tmp_path / name / "gt"
    subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--seed",
            str(seed),
            "--count",
            str(count),
            "--raw-dir",
            str(raw_dir),
            "--gt-dir",
            str(gt_dir),
        ],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
    )
    return raw_dir, gt_dir


def test_generator_is_byte_identical_across_runs_with_same_seed(tmp_path):
    raw1, gt1 = run_generator(tmp_path, seed=42, count=50, name="run1")
    raw2, gt2 = run_generator(tmp_path, seed=42, count=50, name="run2")

    for filename in ("ledger.csv", "bank_statement.csv", "gateway_settlement.csv"):
        assert (raw1 / filename).read_bytes() == (raw2 / filename).read_bytes()

    assert (gt1 / "ground_truth_matches.json").read_bytes() == (
        gt2 / "ground_truth_matches.json"
    ).read_bytes()


def test_different_seeds_produce_different_data(tmp_path):
    raw1, _ = run_generator(tmp_path, seed=1, count=50, name="seed1")
    raw2, _ = run_generator(tmp_path, seed=2, count=50, name="seed2")
    assert (raw1 / "ledger.csv").read_bytes() != (raw2 / "ledger.csv").read_bytes()


def test_ground_truth_covers_every_transaction_exactly_once(tmp_path):
    _, gt_dir = run_generator(tmp_path, seed=42, count=200, name="run")
    ground_truth = json.loads((gt_dir / "ground_truth_matches.json").read_text())

    assert len(ground_truth) == 200
    ids = [g["true_transaction_id"] for g in ground_truth]
    assert len(ids) == len(set(ids))


def test_injection_rates_match_config(tmp_path):
    _, gt_dir = run_generator(tmp_path, seed=42, count=200, name="run")
    ground_truth = json.loads((gt_dir / "ground_truth_matches.json").read_text())

    counts = Counter(g["mismatch_type"] for g in ground_truth)
    assert counts[None] == 110  # clean_exact_match
    assert counts["id_format_drift"] == 30
    assert counts["timing_drift"] == 20
    assert counts["fee_adjusted"] == 16
    assert counts["missing_counterpart"] == 10
    assert counts["duplicate_entry"] == 6
    assert counts["split_settlement"] == 8


def test_missing_counterpart_records_are_unresolved_with_no_ids(tmp_path):
    _, gt_dir = run_generator(tmp_path, seed=42, count=200, name="run")
    ground_truth = json.loads((gt_dir / "ground_truth_matches.json").read_text())

    missing = [g for g in ground_truth if g["mismatch_type"] == "missing_counterpart"]
    assert len(missing) == 10
    for g in missing:
        assert g["expected_outcome"] == "UNRESOLVED"
        assert g["bank_ids"] == []
        assert g["gateway_ids"] == []


def test_bank_ids_are_all_unique(tmp_path):
    """id_format_drift corruption must never collide with another record's
    canonical bank_id or with another corrupted id — see unique_transposed_id."""
    raw_dir, _ = run_generator(tmp_path, seed=42, count=200, name="run")
    import csv

    bank_ids = [
        r["bank_id"] for r in csv.DictReader((raw_dir / "bank_statement.csv").open())
    ]
    assert len(bank_ids) == len(set(bank_ids))


def test_split_settlement_amounts_sum_to_ledger_amount(tmp_path):
    raw_dir, gt_dir = run_generator(tmp_path, seed=42, count=200, name="run")
    import csv
    from decimal import Decimal

    ledger = {
        r["ledger_id"]: Decimal(r["amount"])
        for r in csv.DictReader((raw_dir / "ledger.csv").open())
    }
    gateway = {
        r["gateway_id"]: Decimal(r["amount"])
        for r in csv.DictReader((raw_dir / "gateway_settlement.csv").open())
    }
    ground_truth = json.loads((gt_dir / "ground_truth_matches.json").read_text())

    splits = [g for g in ground_truth if g["mismatch_type"] == "split_settlement"]
    assert len(splits) == 8
    for g in splits:
        total = sum(gateway[gid] for gid in g["gateway_ids"])
        assert total == ledger[g["ledger_id"]]
