"""GET /matches — paginated match list with tier (plan.md §9 Phase 6)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Match
from app.schemas import MatchListResponse, MatchOut

router = APIRouter()


@router.get("/matches", response_model=MatchListResponse)
def list_matches(
    run_id: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> MatchListResponse:
    stmt = select(Match)
    if run_id is not None:
        stmt = stmt.where(Match.run_id == run_id)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Match.ledger_id).offset((page - 1) * page_size).limit(page_size)
    ).all()

    return MatchListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[MatchOut.model_validate(row) for row in rows],
    )
