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

from app.common.errors import NotFoundError
from app.common.time import utcnow
from app.modules.notices.repository import NoticeRepository
from app.modules.notices.schemas import (
    NoticeListOut,
    NoticeReceiptsOut,
    ReceiptUserOut,
)
from app.modules.notices.services import support


class ReceiptsService:
    def __init__(self, session: Session, repo: NoticeRepository) -> None:
        self._session = session
        self._repo = repo

    def read_all(self, society_id: int, *, caller_user_id: int) -> int:
        """Mark every active notice read for the caller (§6). Idempotent.

        One active-ids query, then an idempotent (``ON CONFLICT DO NOTHING``)
        insert per active notice — a second call for the same caller is a
        harmless no-op. Reads are NOT audited (docs §4). Returns the number of
        currently-active notices (the caller now has a read row for each); the
        route discards it (204).
        """
        now = utcnow()
        active_ids = self._repo.active_notice_ids(society_id, now=now)
        for notice_id in active_ids:
            self._repo.mark_read(society_id, notice_id, caller_user_id, at=now)
        return len(active_ids)

    def receipts(
        self, society_id: int, notice_id: int
    ) -> NoticeReceiptsOut:
        """Read vs unread owners for a notice (admin) (§4/§6).

        Denominator = the society's CURRENT owners (docs §4): current owners
        LEFT JOIN ``notice_reads``. Both sides are fetched ONCE (the owner set +
        every reader's ``read_at``) and the split is built in memory — no
        per-owner query. An owner provisioned AFTER the notice was posted is in
        the owner set but not in the reads → unread (broadcast, not a frozen
        snapshot). A reader who is no longer a current owner is NOT in the
        denominator, so they never appear. Lists are sorted by ``user_id`` for a
        deterministic response. Nonexistent notice → 404. Not audited.
        """
        notice = self._repo.get_notice(society_id, notice_id)
        if notice is None:
            raise NotFoundError(
                "Notice not found.", details={"notice_id": notice_id}
            )

        owners = support.current_owner_ids(self._session, society_id)
        reads = self._repo.reads_for_notice(society_id, notice_id)

        read: list[ReceiptUserOut] = []
        unread: list[ReceiptUserOut] = []
        for user_id in sorted(owners):
            read_at = reads.get(user_id)
            if read_at is not None:
                read.append(ReceiptUserOut(user_id=user_id, read_at=read_at))
            else:
                unread.append(ReceiptUserOut(user_id=user_id))

        return NoticeReceiptsOut(
            notice_id=notice.id,
            total_owners=len(owners),
            read_count=len(read),
            unread_count=len(unread),
            read=read,
            unread=unread,
        )

    def archive(
        self, society_id: int, *, offset: int, limit: int
    ) -> NoticeListOut:
        """Expired + withdrawn history (admin) (§6).

        Pinned-first then newest (the repository's archive filter), paginated.
        ``is_read`` is not meaningful for the archive view (there is no caller
        read state to project), so items carry ``is_read=False`` and the
        envelope ``unread_count=0``. Attachment counts are batched per page (no
        N+1). Route-gated ``notices.read_receipts`` (admin-only).
        """
        rows, total = self._repo.list_notices(
            society_id,
            archive_only=True,
            now=utcnow(),
            offset=offset,
            limit=limit,
        )

        page_ids = [n.id for n in rows]
        attachment_counts = self._repo.attachment_counts_for(society_id, page_ids)
        items = [
            support.assemble_list_item(
                n,
                attachment_count=attachment_counts.get(n.id, 0),
                is_read=False,
            )
            for n in rows
        ]
        return NoticeListOut(items=items, total=total, unread_count=0)
