"""GET /exceptions — paginated exception list with type + rationale
(plan.md §9 Phase 6)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Exception as ExceptionModel
from app.schemas import ExceptionListResponse, ExceptionOut

router = APIRouter()


@router.get("/exceptions", response_model=ExceptionListResponse)
def list_exceptions(
    run_id: str | None = None,
    exception_type: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> ExceptionListResponse:
    stmt = select(ExceptionModel)
    if run_id is not None:
        stmt = stmt.where(ExceptionModel.run_id == run_id)
    if exception_type is not None:
        stmt = stmt.where(ExceptionModel.exception_type == exception_type)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(ExceptionModel.ledger_id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    return ExceptionListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[ExceptionOut.model_validate(row) for row in rows],
    )
