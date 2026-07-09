"""Notifications service facade (docs/03 §2).

Thin per-request facade the router calls: constructs the repository + concern
services against the request session and delegates. Owns NO logic itself and NEVER
commits (``get_session`` commits once at request end). Mirrors the Finance/Notices
facade split.

FROZEN in Phase A; Wave W3 (feed) and Wave W4 (config) fill their concern bodies.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.common.pagination import PageParams
from app.modules.notifications.repository import NotificationRepository
from app.modules.notifications.schemas import (
    ConfigOut,
    ConfigUpdateRequest,
    FeedOut,
    MarkReadResult,
    UnreadCountOut,
)
from app.modules.notifications.services.config_svc import ConfigService
from app.modules.notifications.services.feed import FeedService


class NotificationsService:
    """Facade over the notifications concerns for one request session."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = NotificationRepository(session)
        self._feed = FeedService(session, self._repo)
        self._config = ConfigService(session, self._repo)

    # --- feed (Wave W3) ----------------------------------------------------

    def get_feed(
        self, society_id: int, user_id: int, page: PageParams
    ) -> FeedOut:
        return self._feed.get_feed(society_id, user_id, page)

    def get_unread_count(
        self, society_id: int, user_id: int
    ) -> UnreadCountOut:
        return self._feed.get_unread_count(society_id, user_id)

    def mark_read(
        self, society_id: int, user_id: int, notification_id: int
    ) -> MarkReadResult:
        return self._feed.mark_read(society_id, user_id, notification_id)

    def mark_all_read(
        self, society_id: int, user_id: int
    ) -> MarkReadResult:
        return self._feed.mark_all_read(society_id, user_id)

    # --- config (Wave W4) --------------------------------------------------

    def get_config(self, society_id: int) -> ConfigOut:
        return self._config.get_config(society_id)

    def update_config(
        self, society_id: int, req: ConfigUpdateRequest, *, actor_user_id: int
    ) -> ConfigOut:
        return self._config.update_config(
            society_id, req, actor_user_id=actor_user_id
        )
