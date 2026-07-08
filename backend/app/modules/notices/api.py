"""Notice Board public inter-module contract (docs/modules/notice-board.md §7, docs/05).

The ONLY surface other modules import. Currently a single optional, read-only
provider: ``active_notice_count`` for a future portal badge / dashboard tile. No
built module consumes it yet; it is exposed now so the contract stays stable.
Consumers NEVER touch notices tables directly.

Each call takes the caller's request-scoped ``Session`` so a read joins the
caller's transaction. Thin delegator over :class:`NoticesService`; no logic.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.notices.service import NoticesService


def active_notice_count(session: Session, society_id: int) -> int:
    """The society's current active (published, non-expired) notice count (§7)."""
    return NoticesService(session).active_notice_count(society_id)
