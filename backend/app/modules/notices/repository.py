"""Notice Board queries (docs/03 §2) — pure DB access, ``society_id``-scoped.

No business rules here; the service decides, the repository fetches. Every query
is tenant-scoped by ``society_id`` (cross-tenant isolation — docs/PF §7).

FROZEN interface: wave sub-agents implement service logic against these
signatures but must not change them. The two performance-critical paths — the
batched attachment-count fetch and the batched caller-read set (both keyed by a
list of notice ids, so the feed has NO N+1) — are implemented here once and reused
by every list/detail path. ``= ANY(:ids)`` style ``.in_()`` is used for the id
lists (psycopg3 rejects a raw ``IN :tuple``).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.notices.models import Notice, NoticeAttachment, NoticeRead
from app.modules.notices.schemas import STATUS_PUBLISHED, STATUS_WITHDRAWN


class NoticeRepository:
    """Queries over the three notice tables, all ``society_id``-scoped."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # --- notices -----------------------------------------------------------

    def add_notice(self, notice: Notice) -> Notice:
        """Insert a notice and flush so its id is available."""
        self._session.add(notice)
        self._session.flush()
        return notice

    def get_notice(
        self, society_id: int, notice_id: int, *, lock: bool = False
    ) -> Notice | None:
        """One notice by id, scoped to the society. ``lock=True`` takes a
        ``SELECT ... FOR UPDATE`` row lock (serializes concurrent mutation of the
        same notice — e.g. attachment add/remove)."""
        stmt = select(Notice).where(
            Notice.society_id == society_id, Notice.id == notice_id
        )
        if lock:
            stmt = stmt.with_for_update()
        return self._session.execute(stmt).scalar_one_or_none()

    def list_notices(
        self,
        society_id: int,
        *,
        statuses: list[str] | None = None,
        active_only: bool = False,
        archive_only: bool = False,
        now: datetime | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Notice], int]:
        """List notices for a society, pinned-first then ``published_at`` DESC.

        Filters (all optional, combined with AND):
        - ``statuses`` — restrict to these status values.
        - ``active_only`` — the resident/admin ACTIVE feed: ``published`` AND
          (``expires_at IS NULL OR expires_at > now``). Requires ``now``.
        - ``archive_only`` — the admin archive: ``withdrawn`` OR (``published``
          AND ``expires_at <= now``) (i.e. expired). Requires ``now``.

        Ordering: ``is_pinned`` DESC, then ``published_at`` DESC NULLS LAST, then
        ``id`` DESC (stable). Returns ``(rows, total)`` where ``total`` is the
        unpaginated count for the same filter (client paging).
        """
        conds = [Notice.society_id == society_id]
        if statuses is not None:
            conds.append(Notice.status.in_(statuses))
        if active_only:
            conds.append(Notice.status == STATUS_PUBLISHED)
            conds.append(
                (Notice.expires_at.is_(None)) | (Notice.expires_at > now)
            )
        if archive_only:
            conds.append(
                (Notice.status == STATUS_WITHDRAWN)
                | (
                    (Notice.status == STATUS_PUBLISHED)
                    & (Notice.expires_at.isnot(None))
                    & (Notice.expires_at <= now)
                )
            )

        total = self._session.execute(
            select(func.count()).select_from(Notice).where(*conds)
        ).scalar_one()

        rows = (
            self._session.execute(
                select(Notice)
                .where(*conds)
                .order_by(
                    Notice.is_pinned.desc(),
                    Notice.published_at.desc().nullslast(),
                    Notice.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return list(rows), total

    # --- attachments -------------------------------------------------------

    def add_attachment(self, attachment: NoticeAttachment) -> NoticeAttachment:
        self._session.add(attachment)
        self._session.flush()
        return attachment

    def get_attachment(
        self, society_id: int, notice_id: int, attachment_id: int
    ) -> NoticeAttachment | None:
        return self._session.execute(
            select(NoticeAttachment).where(
                NoticeAttachment.society_id == society_id,
                NoticeAttachment.notice_id == notice_id,
                NoticeAttachment.id == attachment_id,
            )
        ).scalar_one_or_none()

    def list_attachments(
        self, society_id: int, notice_id: int
    ) -> list[NoticeAttachment]:
        return list(
            self._session.execute(
                select(NoticeAttachment)
                .where(
                    NoticeAttachment.society_id == society_id,
                    NoticeAttachment.notice_id == notice_id,
                )
                .order_by(NoticeAttachment.id.asc())
            )
            .scalars()
            .all()
        )

    def delete_attachment(self, attachment: NoticeAttachment) -> None:
        self._session.delete(attachment)
        self._session.flush()

    def attachment_counts_for(
        self, society_id: int, notice_ids: list[int]
    ) -> dict[int, int]:
        """Batch attachment count per notice (no N+1). Empty in → empty out."""
        if not notice_ids:
            return {}
        rows = self._session.execute(
            select(
                NoticeAttachment.notice_id, func.count(NoticeAttachment.id)
            )
            .where(
                NoticeAttachment.society_id == society_id,
                NoticeAttachment.notice_id.in_(notice_ids),
            )
            .group_by(NoticeAttachment.notice_id)
        ).all()
        return {notice_id: count for notice_id, count in rows}

    # --- reads -------------------------------------------------------------

    def mark_read(
        self, society_id: int, notice_id: int, user_id: int, *, at: datetime
    ) -> None:
        """Idempotently insert a read row for ``user_id`` on ``notice_id``.

        ``INSERT ... ON CONFLICT (notice_id, user_id) DO NOTHING`` so a second
        open (or a race between two opens) is a harmless no-op — the
        ``UNIQUE(notice_id, user_id)`` index backs it. ``read_at`` keeps the
        FIRST-open time (the conflict path does not overwrite it).
        """
        stmt = (
            pg_insert(NoticeRead)
            .values(
                society_id=society_id,
                notice_id=notice_id,
                user_id=user_id,
                read_at=at,
            )
            .on_conflict_do_nothing(index_elements=["notice_id", "user_id"])
        )
        self._session.execute(stmt)

    def read_notice_ids_for(
        self, society_id: int, user_id: int, notice_ids: list[int]
    ) -> set[int]:
        """The subset of ``notice_ids`` the user has already read (batch, no
        N+1). Empty in → empty out."""
        if not notice_ids:
            return set()
        rows = self._session.execute(
            select(NoticeRead.notice_id).where(
                NoticeRead.society_id == society_id,
                NoticeRead.user_id == user_id,
                NoticeRead.notice_id.in_(notice_ids),
            )
        ).all()
        return {notice_id for (notice_id,) in rows}

    def has_read(self, society_id: int, notice_id: int, user_id: int) -> bool:
        return (
            self._session.execute(
                select(NoticeRead.id).where(
                    NoticeRead.society_id == society_id,
                    NoticeRead.notice_id == notice_id,
                    NoticeRead.user_id == user_id,
                )
            ).scalar_one_or_none()
            is not None
        )

    def reads_for_notice(
        self, society_id: int, notice_id: int
    ) -> dict[int, datetime]:
        """Every reader of a notice → their ``read_at`` (drives receipts). Keyed
        by ``user_id`` so the service can LEFT JOIN against current owners."""
        rows = self._session.execute(
            select(NoticeRead.user_id, NoticeRead.read_at).where(
                NoticeRead.society_id == society_id,
                NoticeRead.notice_id == notice_id,
            )
        ).all()
        return {user_id: read_at for user_id, read_at in rows}

    def active_notice_ids(
        self, society_id: int, *, now: datetime
    ) -> list[int]:
        """Ids of all currently-active notices (published + not expired) — the
        set ``read-all`` marks read for the caller."""
        rows = self._session.execute(
            select(Notice.id).where(
                Notice.society_id == society_id,
                Notice.status == STATUS_PUBLISHED,
                (Notice.expires_at.is_(None)) | (Notice.expires_at > now),
            )
        ).all()
        return [notice_id for (notice_id,) in rows]

    def active_notice_count(self, society_id: int, *, now: datetime) -> int:
        """Count of currently-active notices (inter-module provider)."""
        return self._session.execute(
            select(func.count())
            .select_from(Notice)
            .where(
                Notice.society_id == society_id,
                Notice.status == STATUS_PUBLISHED,
                (Notice.expires_at.is_(None)) | (Notice.expires_at > now),
            )
        ).scalar_one()
