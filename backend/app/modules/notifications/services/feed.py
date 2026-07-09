"""Feed + badge + mark-read concern (docs/modules/notifications.md §6).

The caller's own in-app feed: the unread list (paginated, newest first), the
lightweight badge count, and the two mark-read writes. Every path is scoped to
the caller's own rows in their society (own-only — a caller can never read or
clear another user's notification). No audit (individual reads are not audited —
docs §5). All reads/writes go through :class:`NotificationRepository`.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.common.errors import NotFoundError
from app.common.pagination import PageParams
from app.common.time import utcnow
from app.modules.notifications.repository import NotificationRepository
from app.modules.notifications.schemas import (
    FeedOut,
    MarkReadResult,
    NotificationOut,
    UnreadCountOut,
)


class FeedService:
    def __init__(self, session: Session, repo: NotificationRepository) -> None:
        self._session = session
        self._repo = repo

    def get_feed(
        self, society_id: int, user_id: int, page: PageParams
    ) -> FeedOut:
        """A page of the caller's unread feed + the total unread badge (docs §6).

        The list is a single indexed select on the unread partial index; the
        ``unread_count`` is a separate COUNT (independent of the page) so the
        badge is correct even when the feed spans many pages.
        """
        rows = self._repo.list_unread(
            society_id, user_id, limit=page.limit, offset=page.offset
        )
        total_unread = self._repo.unread_count(society_id, user_id)
        return FeedOut(
            items=[NotificationOut.model_validate(r) for r in rows],
            unread_count=total_unread,
            page=page.page,
            page_size=page.page_size,
        )

    def get_unread_count(
        self, society_id: int, user_id: int
    ) -> UnreadCountOut:
        """The lightweight badge count only (docs §6)."""
        return UnreadCountOut(
            unread_count=self._repo.unread_count(society_id, user_id)
        )

    def mark_read(
        self, society_id: int, user_id: int, notification_id: int
    ) -> MarkReadResult:
        """Mark one notification read — clears it from the feed (docs §6).

        Own-only: a notification the caller does not own (wrong user / wrong
        society / nonexistent) is a 404 — no information leak about another
        society's ids. Re-reading an already-read own notification is an
        idempotent no-op (``cleared=0``), NOT a 404.
        """
        cleared = self._repo.mark_one_read(
            society_id, user_id, notification_id, now=utcnow()
        )
        if cleared == 0 and not self._repo.exists_owned(
            society_id, user_id, notification_id
        ):
            raise NotFoundError(
                "Notification not found.",
                details={"notification_id": notification_id},
            )
        return MarkReadResult(cleared=cleared)

    def mark_all_read(
        self, society_id: int, user_id: int
    ) -> MarkReadResult:
        """Mark ALL the caller's unread notifications read (docs §6)."""
        cleared = self._repo.mark_all_read(society_id, user_id, now=utcnow())
        return MarkReadResult(cleared=cleared)
