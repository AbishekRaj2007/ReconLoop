"""Persist one run_pipeline() report into the database (plan.md §9 Phase 6).

Kept separate from run_pipeline.py itself so the matching engine has zero
dependency on the DB/ORM layer — scripts/run_pipeline.py and run_scoring.py
keep working exactly as before, unaware persistence exists.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Exception as ExceptionModel
from app.models import LLMAuditLog, Match, ReconciliationRun


def persist_pipeline_report(db: Session, report: dict, use_llm: bool) -> ReconciliationRun:
    run = ReconciliationRun(
        used_llm=use_llm,
        total_records=report["total"],
        wall_clock_seconds=report["wall_clock_seconds"],
        records_per_second=report["records_per_second"],
        total_llm_calls=report.get("total_llm_calls", 0),
        total_llm_cost_usd=report.get("total_llm_cost_usd", Decimal(0)),
    )
    db.add(run)
    db.flush()

    for match in report["matches"]:
        db.add(
            Match(
                run_id=run.id,
                ledger_id=match.ledger_id,
                bank_ids=match.bank_ids,
                gateway_ids=match.gateway_ids,
                tier=match.tier,
            )
        )

    for exc in report["exceptions"]:
        db.add(
            ExceptionModel(
                run_id=run.id,
                ledger_id=exc.ledger_id,
                exception_type=exc.exception_type,
                affected_ids=exc.affected_ids,
                rationale=exc.rationale,
                suggested_action=exc.suggested_action,
            )
        )

    for outcome in report.get("llm_outcomes", []):
        for entry in outcome.audit_log:
            db.add(
                LLMAuditLog(
                    run_id=run.id,
                    ledger_id=outcome.ledger_id,
                    side=outcome.side,
                    tier=entry.tier,
                    model=entry.model,
                    prompt=entry.prompt,
                    raw_response=entry.raw_response,
                    latency_ms=entry.latency_ms,
                    estimated_cost_usd=entry.estimated_cost_usd,
                    decision=entry.decision,
                    confidence=entry.confidence,
                )
            )

    db.commit()
    db.refresh(run)
    return run
