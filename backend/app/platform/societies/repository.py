"""Society + module-allocation queries (docs/03 §2/§4).

Pure DB access: efficient, paginated, ``society_id``-scoped. No business rules
here — the service decides, the repository fetches (docs/03 §2).
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.platform.models import Society, SocietyModule


class SocietyRepository:
    """Queries over ``societies`` and ``society_modules``."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # --- societies ---------------------------------------------------------

    def add(self, society: Society) -> Society:
        """Stage a new society and flush so its ``id`` is assigned in-txn."""
        self._session.add(society)
        self._session.flush()
        return society

    def get(self, society_id: int) -> Society | None:
        return self._session.get(Society, society_id)

    def list_page(self, *, limit: int, offset: int) -> tuple[list[Society], int]:
        """Return one page of societies (newest first) plus the total count.

        Count is pushed to the DB; only the page's rows are materialized.
        """
        total = self._session.execute(
            select(func.count()).select_from(Society)
        ).scalar_one()
        rows = (
            self._session.execute(
                select(Society)
                .order_by(Society.id.desc())
                .limit(limit)
                .offset(offset)
            )
            .scalars()
            .all()
        )
        return list(rows), total

    # --- society_modules ---------------------------------------------------

    def list_modules(self, society_id: int) -> list[SocietyModule]:
        """All module rows for a society (used to compute enabled set + upsert)."""
        rows = (
            self._session.execute(
                select(SocietyModule)
                .where(SocietyModule.society_id == society_id)
                .order_by(SocietyModule.module_key)
            )
            .scalars()
            .all()
        )
        return list(rows)

    def add_module(self, module: SocietyModule) -> SocietyModule:
        self._session.add(module)
        self._session.flush()
        return module
