"""SQLAlchemy engine/session setup (plan.md §9 Phase 6).

`DATABASE_URL` (see .env.example) is the single source of truth for which
database to talk to — Postgres in normal use, or a test-provided URL (e.g.
in-memory SQLite) for the API integration test, so tests don't require a
running Postgres instance.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def _database_url() -> str:
    return os.environ.get(
        "DATABASE_URL", "postgresql://reconloop:reconloop@localhost:5432/reconloop"
    )


def make_engine(database_url: str | None = None):
    url = database_url or _database_url()
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
