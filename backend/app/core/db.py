"""Database engine, session, and the declarative ``Base``.

Sync SQLAlchemy 2.x + psycopg v3 (confirmed decision). One request = one
transaction: ``get_session`` opens a session, commits on success, rolls back on
error, and always closes — so a state change and its ``audit_log`` row are
written atomically (docs/PF §12, docs/03 §7).
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, create_engine, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.orm import Session

from app.core.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,  # drop dead connections rather than erroring mid-request
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
)


class Base(DeclarativeBase):
    """Declarative base with the columns EVERY table carries (docs/03 §6).

    - ``id``  BIGINT identity PK (docs/03 §5 — not UUID).
    - ``created_at`` / ``updated_at`` server-defaulted timestamps.
    """

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


def get_session() -> Iterator[Session]:
    """FastAPI dependency: yields a session, commits/rolls back, then closes.

    The request handler (service layer) does its work inside this single
    transaction; it does NOT commit itself.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
