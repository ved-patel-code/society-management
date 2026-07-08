"""Notice Board service facade (docs/modules/notice-board.md §4).

Thin ``NoticesService`` over the concern-split internals (``services/``). The
router and the inter-module ``api`` talk to this one class; it constructs the
shared :class:`NoticeRepository` once per request session and exposes each concern
(``crud``, ``lifecycle``, ``attachments``, ``receipts``) plus a façade-level
shortcut the cross-module contract needs. The service NEVER commits
(``get_session`` commits once at request end — docs/03 §2); concerns flush where
an id is needed.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.common.time import utcnow
from app.modules.notices.repository import NoticeRepository
from app.modules.notices.services.attachments import AttachmentsService
from app.modules.notices.services.lifecycle import LifecycleService
from app.modules.notices.services.notices_crud import NoticesCrudService
from app.modules.notices.services.receipts import ReceiptsService


class NoticesService:
    """Orchestration facade over the notices concerns (one per request)."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = NoticeRepository(session)
        self.crud = NoticesCrudService(session, self._repo)
        self.lifecycle = LifecycleService(session, self._repo)
        self.attachments = AttachmentsService(session, self._repo)
        self.receipts = ReceiptsService(session, self._repo)

    # --- inter-module contract shortcuts (docs §7) -------------------------

    def active_notice_count(self, society_id: int) -> int:
        """Public contract: the society's current active-notice count (§7).

        Read-only helper for a future dashboard / portal badge. Not required by
        any built module yet; exposed now to keep the contract stable.
        """
        return self._repo.active_notice_count(society_id, now=utcnow())
