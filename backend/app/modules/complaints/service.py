"""Complaints service facade (docs/modules/complaints.md §4).

Thin ``ComplaintsService`` over the concern-split internals (``services/``).
Routers and the inter-module ``api`` talk to this one class; it constructs the
shared :class:`ComplaintRepository` once per request session and exposes each
concern (``categories``, ``crud``, ``status``, ``images``, ``config``) plus a few
façade-level shortcuts the cross-module contract needs. The service NEVER commits
(``get_session`` commits once at request end — docs/03 §2); concerns flush where
an id is needed.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.complaints.repository import ComplaintRepository
from app.modules.complaints.services.categories import CategoriesService
from app.modules.complaints.services.complaints_crud import ComplaintsCrudService
from app.modules.complaints.services.config_svc import ConfigService
from app.modules.complaints.services.images import ImagesService
from app.modules.complaints.services.status import StatusService


class ComplaintsService:
    """Orchestration facade over the complaints concerns (one per request)."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = ComplaintRepository(session)
        self.categories = CategoriesService(session, self._repo)
        self.crud = ComplaintsCrudService(session, self._repo)
        self.status = StatusService(session, self._repo)
        self.images = ImagesService(session, self._repo)
        self.config = ConfigService(session, self._repo)

    # --- inter-module contract shortcuts (docs §7) -------------------------

    def open_complaint_count(self, society_id: int, house_id: int) -> int:
        """Public contract: a house's open (non-terminal) complaint count (§7).

        Read-only helper for a future house-profile / resale view. Not required by
        any built module yet; exposed now to keep the contract stable.
        """
        return self._repo.open_complaint_count(society_id, house_id)
