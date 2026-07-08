"""Read-all + receipts + archive concern — WAVE D (docs/modules/notice-board.md §4/§6).

Owns the read-state / admin-visibility endpoints:
- ``POST /notices/read-all``       mark every active notice read for the caller.
- ``GET  /notices/{id}/receipts``  read vs unread lists + counts (admin).
- ``GET  /notices/archive``        expired + withdrawn history (admin).

Business rules Wave D enforces (docs §4/§6):
- read-all: idempotently insert a read row for the caller for every currently
  ACTIVE notice (``repo.active_notice_ids`` + ``repo.mark_read``). Not audited.
- receipts (``notices.read_receipts``): denominator = the society's CURRENT
  owners (``support.current_owner_ids``); LEFT JOIN their read rows
  (``repo.reads_for_notice``) → read vs unread lists + counts. Owners provisioned
  AFTER the notice was posted count as unread (broadcast, not a frozen snapshot).
  Build the read/unread split in-memory from the two batched fetches — NO
  per-owner query loop. Nonexistent notice → 404. Not audited.
- archive (``notices.read_receipts``): expired (published + past ``expires_at``)
  + withdrawn, pinned-first then newest (``repo.list_notices(archive_only=True)``).

FROZEN STUBS: Wave D fills the bodies, editing only THIS file + its own test file.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.notices.repository import NoticeRepository
from app.modules.notices.schemas import NoticeListOut, NoticeReceiptsOut


class ReceiptsService:
    def __init__(self, session: Session, repo: NoticeRepository) -> None:
        self._session = session
        self._repo = repo

    def read_all(self, society_id: int, *, caller_user_id: int) -> int:
        """Mark every active notice read for the caller; return how many were
        newly (or already) covered (§6). Idempotent."""
        raise NotImplementedError

    def receipts(
        self, society_id: int, notice_id: int
    ) -> NoticeReceiptsOut:
        """Read vs unread owners for a notice (admin) (§4/§6)."""
        raise NotImplementedError

    def archive(
        self, society_id: int, *, offset: int, limit: int
    ) -> NoticeListOut:
        """Expired + withdrawn history (admin) (§6)."""
        raise NotImplementedError
