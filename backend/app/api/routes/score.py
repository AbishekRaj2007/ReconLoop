"""GET /score — latest scoring metrics for a persisted run (plan.md §9
Phase 6), computed the same way run_scoring.py does: score_pipeline()
against ground_truth_matches.json, never self-reported by the matcher."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import GROUND_TRUTH_PATH
from app.core.scoring.evaluator import score_pipeline
from app.db.session import get_db
from app.models import Exception as ExceptionModel
from app.models import Match, ReconciliationRun
from app.schemas import ScoreResponse

router = APIRouter()


@dataclass(frozen=True)
class _MatchRow:
    ledger_id: str
    bank_ids: list[str]
    gateway_ids: list[str]
    tier: str


@router.get("/score", response_model=ScoreResponse)
def latest_score(run_id: str | None = None, db: Session = Depends(get_db)) -> ScoreResponse:
    stmt = select(ReconciliationRun)
    if run_id is not None:
        stmt = stmt.where(ReconciliationRun.id == run_id)
    run = db.scalars(stmt.order_by(ReconciliationRun.created_at.desc())).first()
    if run is None:
        raise HTTPException(status_code=404, detail="No reconciliation run found")

    matches = [
        _MatchRow(m.ledger_id, m.bank_ids, m.gateway_ids, m.tier)
        for m in db.scalars(select(Match).where(Match.run_id == run.id)).all()
    ]
    ground_truth = json.loads(GROUND_TRUTH_PATH.read_text())
    scoring = score_pipeline(ground_truth, matches, run.total_records)
    exception_count = len(
        db.scalars(
            select(ExceptionModel).where(ExceptionModel.run_id == run.id)
        ).all()
    )

    return ScoreResponse(
        run_id=run.id,
        created_at=run.created_at,
        total_records=run.total_records,
        matched=len(matches),
        exception_count=exception_count,
        precision=scoring.precision,
        recall=scoring.recall,
        f1=scoring.f1,
        false_match_rate=scoring.false_match_rate,
        missed_match_rate=scoring.missed_match_rate,
        tier_attribution=scoring.tier_attribution,
        wall_clock_seconds=run.wall_clock_seconds,
        records_per_second=run.records_per_second,
        used_llm=run.used_llm,
        total_llm_calls=run.total_llm_calls,
        total_llm_cost_usd=float(run.total_llm_cost_usd or Decimal(0)),
    )
