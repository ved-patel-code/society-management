"""Complaints public inter-module contract (docs/modules/complaints.md §7, docs/05).

The ONLY surface other modules import. Currently a single optional, read-only
provider: ``open_complaint_count`` for a future house-profile / resale view. No
built module consumes it yet; it is exposed now so the contract stays stable.
Consumers NEVER touch complaints tables directly.

Each call takes the caller's request-scoped ``Session`` so a read joins the
caller's transaction. Thin delegator over :class:`ComplaintsService`; no logic.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.complaints.service import ComplaintsService


def open_complaint_count(
    session: Session, society_id: int, house_id: int
) -> int:
    """A house's open (non-terminal, non-archived) complaint count (docs §7)."""
    return ComplaintsService(session).open_complaint_count(society_id, house_id)
