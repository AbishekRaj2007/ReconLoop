"""POST /reconcile — trigger a pipeline run on the current data batch and
persist the results (plan.md §9 Phase 6)."""

from __future__ import annotations

import sys
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import CONFIG_PATH, RAW_DIR, REPO_ROOT
from app.core.persistence import persist_pipeline_report
from app.db.session import get_db
from app.schemas import ReconcileRequest, ReconcileResponse

_scripts_dir = str(REPO_ROOT / "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

router = APIRouter()


@router.post("/reconcile", response_model=ReconcileResponse)
def reconcile(
    body: ReconcileRequest = ReconcileRequest(), db: Session = Depends(get_db)
) -> ReconcileResponse:
    import run_pipeline

    report = run_pipeline.run_pipeline(RAW_DIR, CONFIG_PATH, use_llm=body.use_llm)
    run = persist_pipeline_report(db, report, use_llm=body.use_llm)

    return ReconcileResponse(
        run_id=run.id,
        total_records=run.total_records,
        matched=len(report["matches"]),
        pending_llm_count=report.get("pending_llm_count", 0),
        exception_count=len(report["exceptions"]),
        wall_clock_seconds=run.wall_clock_seconds,
        records_per_second=run.records_per_second,
        used_llm=run.used_llm,
        total_llm_calls=run.total_llm_calls,
        total_llm_cost_usd=float(run.total_llm_cost_usd or Decimal(0)),
    )
