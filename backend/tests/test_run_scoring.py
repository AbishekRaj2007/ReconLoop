"""Integration test for scripts/run_scoring.py — Phase 5's deliverable of a
single command producing precision/recall/F1 etc. against ground truth, run
as a subprocess exactly as a user would invoke it from the README.
"""

import csv
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCORING_SCRIPT = REPO_ROOT / "scripts" / "run_scoring.py"
CONFIG_PATH = REPO_ROOT / "config" / "matching_config.yaml"


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """One clean Tier-1 match (TXN-000001) and one genuinely unresolvable
    ledger-only record (TXN-000002) whose ground truth says UNRESOLVED."""
    raw_dir = tmp_path / "raw"
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
            },
            {
                "ledger_id": "TXN-000002",
                "date": "2026-06-01",
                "amount": "2000.00",
                "narration": "Payment received from Beta - Invoice INV00002",
                "counterparty_name": "Beta",
            },
        ],
    )
    write_csv(
        raw_dir / "bank_statement.csv",
        ["bank_id", "date", "amount", "narration", "account_fragment"],
        [
            {
                "bank_id": "0000001",
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

    gt_dir = tmp_path / "gt"
    gt_dir.mkdir()
    gt_path = gt_dir / "ground_truth_matches.json"
    gt_path.write_text(
        json.dumps(
            [
                {
                    "true_transaction_id": "GT-0001",
                    "ledger_id": "TXN-000001",
                    "bank_ids": ["0000001"],
                    "gateway_ids": ["pay_00001"],
                    "expected_outcome": "MATCH",
                    "mismatch_type": None,
                    "notes": "clean match",
                },
                {
                    "true_transaction_id": "GT-0002",
                    "ledger_id": "TXN-000002",
                    "bank_ids": [],
                    "gateway_ids": [],
                    "expected_outcome": "UNRESOLVED",
                    "mismatch_type": "missing_counterpart",
                    "notes": "genuinely pending",
                },
            ]
        )
    )
    return raw_dir, gt_path


def test_run_scoring_reports_perfect_score_on_a_clean_fixture(tmp_path):
    raw_dir, gt_path = build_fixture(tmp_path)
    json_out = tmp_path / "report.json"
    metrics_log = tmp_path / "metrics_log.md"

    result = subprocess.run(
        [
            sys.executable,
            str(SCORING_SCRIPT),
            "--raw-dir",
            str(raw_dir),
            "--ground-truth",
            str(gt_path),
            "--config",
            str(CONFIG_PATH),
            "--json-out",
            str(json_out),
            "--metrics-log",
            str(metrics_log),
        ],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert "Precision" in result.stdout or "precision" in result.stdout.lower()

    report = json.loads(json_out.read_text())
    assert report["total_records"] == 2
    assert report["precision"] == 1.0
    assert report["recall"] == 1.0
    assert report["f1"] == 1.0
    assert report["false_match_rate"] == 0.0
    assert report["missed_match_rate"] == 0.0

    assert metrics_log.exists()
    assert (
        "Precision" in metrics_log.read_text()
        or "precision" in metrics_log.read_text().lower()
    )


def test_run_scoring_metrics_log_is_append_only(tmp_path):
    raw_dir, gt_path = build_fixture(tmp_path)
    metrics_log = tmp_path / "metrics_log.md"
    metrics_log.write_text("# Existing content\n\nFirst entry.\n")

    subprocess.run(
        [
            sys.executable,
            str(SCORING_SCRIPT),
            "--raw-dir",
            str(raw_dir),
            "--ground-truth",
            str(gt_path),
            "--config",
            str(CONFIG_PATH),
            "--metrics-log",
            str(metrics_log),
        ],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    content = metrics_log.read_text()
    assert "Existing content" in content
    assert "First entry." in content
