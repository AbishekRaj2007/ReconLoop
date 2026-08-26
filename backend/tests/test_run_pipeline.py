"""Integration test for scripts/run_pipeline.py — Phase 2's deliverable of a
first-pass match rate on real CSV files, run as a subprocess exactly as a
user would invoke it from the README.
"""

import csv
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_SCRIPT = REPO_ROOT / "scripts" / "run_pipeline.py"
CONFIG_PATH = REPO_ROOT / "config" / "matching_config.yaml"


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_pipeline_reports_tier1_match_and_one_unresolved_record(tmp_path):
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

    result = subprocess.run(
        [
            sys.executable,
            str(PIPELINE_SCRIPT),
            "--raw-dir",
            str(raw_dir),
            "--config",
            str(CONFIG_PATH),
        ],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert "Total records:                2" in result.stdout
    assert "Tier 1 exact matches:         1" in result.stdout
    assert "Unresolved after Tier 2:      1" in result.stdout
    assert "First-pass match rate:        50.0%" in result.stdout
