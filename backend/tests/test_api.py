"""Integration test for the FastAPI service (plan.md §9 Phase 6 deliverable):
spin up a test DB, POST /reconcile, assert response shape on all four
endpoints, on a fresh database.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_fixture_data(raw_dir: Path, gt_path: Path) -> None:
    """One clean Tier-1 match (TXN-000001) and one genuinely unresolvable
    ledger-only record (TXN-000002), mirroring test_run_scoring.py's fixture."""
    raw_dir.mkdir(parents=True, exist_ok=True)
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

    gt_path.parent.mkdir(parents=True, exist_ok=True)
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


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    raw_dir = tmp_path / "raw"
    gt_path = tmp_path / "gt" / "ground_truth_matches.json"
    build_fixture_data(raw_dir, gt_path)

    # Imported lazily (not at module scope) so DATABASE_URL is set before
    # app.db.session's module-level engine is created for the first time.
    import app.api.routes.reconcile as reconcile_module
    import app.api.routes.score as score_module
    from app.db.session import Base, engine
    from app.main import app

    monkeypatch.setattr(reconcile_module, "RAW_DIR", raw_dir)
    monkeypatch.setattr(score_module, "GROUND_TRUTH_PATH", gt_path)

    # engine/Base are module-level singletons bound at first import, so each
    # test gets a fresh set of empty tables rather than reusing another
    # test's rows (DATABASE_URL alone can't rebind an already-created engine).
    Base.metadata.drop_all(engine)

    with TestClient(app) as test_client:
        yield test_client


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_reconcile_matches_exceptions_and_score_roundtrip(client):
    reconcile_response = client.post("/reconcile", json={"use_llm": False})
    assert reconcile_response.status_code == 200
    body = reconcile_response.json()
    assert body["total_records"] == 2
    assert body["matched"] == 1
    assert body["exception_count"] == 1
    run_id = body["run_id"]

    matches_response = client.get("/matches", params={"run_id": run_id})
    assert matches_response.status_code == 200
    matches_body = matches_response.json()
    assert matches_body["total"] == 1
    assert matches_body["items"][0]["ledger_id"] == "TXN-000001"
    assert matches_body["items"][0]["tier"] == "TIER_1"

    exceptions_response = client.get("/exceptions", params={"run_id": run_id})
    assert exceptions_response.status_code == 200
    exceptions_body = exceptions_response.json()
    assert exceptions_body["total"] == 1
    assert exceptions_body["items"][0]["ledger_id"] == "TXN-000002"

    score_response = client.get("/score", params={"run_id": run_id})
    assert score_response.status_code == 200
    score_body = score_response.json()
    assert score_body["run_id"] == run_id
    assert score_body["precision"] == 1.0
    assert score_body["recall"] == 1.0
    assert score_body["f1"] == 1.0
    assert score_body["false_match_rate"] == 0.0
    assert score_body["missed_match_rate"] == 0.0


def test_score_without_any_run_returns_404(client):
    response = client.get("/score")
    assert response.status_code == 404
