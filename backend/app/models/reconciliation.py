"""SQLAlchemy ORM models for persisted pipeline output (plan.md §9 Phase 6).

One `ReconciliationRun` per `POST /reconcile` call; every `Match`,
`Exception`, and `LLMAuditLog` row belongs to exactly one run, so results
from different runs never mix in the `/matches`, `/exceptions`, or `/score`
endpoints.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    used_llm: Mapped[bool] = mapped_column(default=False)
    total_records: Mapped[int] = mapped_column(default=0)
    wall_clock_seconds: Mapped[float] = mapped_column(default=0.0)
    records_per_second: Mapped[float] = mapped_column(default=0.0)
    total_llm_calls: Mapped[int] = mapped_column(default=0)
    total_llm_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), default=Decimal(0)
    )

    matches: Mapped[list[Match]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    exceptions: Mapped[list[Exception]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    llm_audit_logs: Mapped[list[LLMAuditLog]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class Transaction(Base):
    """A raw source record ingested for a run — one row per ledger/bank/
    gateway line, kept so /matches and /exceptions can show original field
    values (amount, date, narration) without re-reading CSVs."""

    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("reconciliation_runs.id"))
    source: Mapped[str] = mapped_column(String)  # "ledger" | "bank" | "gateway"
    source_record_id: Mapped[str] = mapped_column(String)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    date: Mapped[str] = mapped_column(String)
    narration: Mapped[str] = mapped_column(Text)


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("reconciliation_runs.id"))
    ledger_id: Mapped[str] = mapped_column(String)
    bank_ids: Mapped[list[str]] = mapped_column(JSON)
    gateway_ids: Mapped[list[str]] = mapped_column(JSON)
    tier: Mapped[str] = mapped_column(
        String
    )  # "TIER_1" | "TIER_2" | "TIER_3" | "TIER_4"

    run: Mapped[ReconciliationRun] = relationship(back_populates="matches")


class Exception(Base):
    __tablename__ = "exceptions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("reconciliation_runs.id"))
    ledger_id: Mapped[str] = mapped_column(String)
    exception_type: Mapped[str] = mapped_column(String)
    affected_ids: Mapped[list[str]] = mapped_column(JSON)
    rationale: Mapped[str] = mapped_column(Text)
    suggested_action: Mapped[str] = mapped_column(Text)

    run: Mapped[ReconciliationRun] = relationship(back_populates="exceptions")


class LLMAuditLog(Base):
    """One row per LLM call, at either tier (CLAUDE.md Golden Rule #4: every
    call is audited, no exceptions)."""

    __tablename__ = "llm_audit_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("reconciliation_runs.id"))
    ledger_id: Mapped[str] = mapped_column(String)
    side: Mapped[str] = mapped_column(String)  # "bank" | "gateway"
    tier: Mapped[str] = mapped_column(String)  # "TIER_3" | "TIER_4"
    model: Mapped[str] = mapped_column(String)
    prompt: Mapped[str] = mapped_column(Text)
    raw_response: Mapped[str] = mapped_column(Text)
    latency_ms: Mapped[float] = mapped_column()
    estimated_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    decision: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)

    run: Mapped[ReconciliationRun] = relationship(back_populates="llm_audit_logs")
