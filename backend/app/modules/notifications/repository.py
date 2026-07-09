"""Notifications queries (docs/03 §2) — pure DB access, ``society_id``-scoped.

No business rules here; the service (engine + handlers + worker) decides, the
repository writes/fetches. Every query is tenant-scoped by ``society_id``
(cross-tenant isolation — docs/PF §7); the feed/mark-read paths are additionally
scoped by ``user_id`` so a caller can only ever touch their OWN rows.

FROZEN interface: wave sub-agents implement service logic against these
signatures but must not change them. Two performance-critical guarantees live
here once and are reused everywhere:

- **Batched fan-out** (``insert_many``): a whole notice broadcast to N owners is
  ONE multi-row ``INSERT ... ON CONFLICT DO NOTHING`` — no per-recipient round
  trip, no N+1. ``ON CONFLICT`` (on the partial-unique ``dedupe_key`` index)
  makes the write idempotent so a re-fire can't double-post and a re-run is safe.
- **Indexed reads**: the unread feed/badge, the mark-read lookup, and the purge
  each hit a dedicated partial index (see ``models.py``), so none of them scan.

psycopg3 rejects a raw ``IN :tuple`` — id/tuple lists use ``.in_()`` (``= ANY``).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.notifications.models import Notification

# The partial-unique index the idempotent insert conflicts on (docs §3). ON
# CONFLICT against a PARTIAL index MUST repeat the index's WHERE predicate as
# ``index_where`` so Postgres can match it — otherwise it raises
# "no unique or exclusion constraint matching the ON CONFLICT specification".
_DEDUPE_INDEX_WHERE = text("dedupe_key IS NOT NULL")


class NotificationRepository:
    """Queries over the ``notifications`` table, all ``society_id``-scoped."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # --- writes (engine choke point) --------------------------------------

    def insert_many(self, rows: list[dict[str, Any]]) -> int:
        """Batched idempotent insert of notification rows — the fan-out primitive.

        ONE ``INSERT ... VALUES (...), (...) ON CONFLICT DO NOTHING`` for every
        row (docs §3/§4 — batched, no N+1). The conflict target is the partial
        UNIQUE ``(society_id, dedupe_key)`` index, so a row whose ``dedupe_key``
        already exists is skipped (scheduled-fire idempotency; a worker re-run
        inserts nothing new). Rows with ``dedupe_key IS NULL`` never conflict —
        the partial index excludes them — so event fan-outs always insert.

        Returns the number of rows actually inserted (``len(rows)`` minus any
        deduped away). An empty ``rows`` is a no-op returning 0 (an empty
        recipient set must not error — docs §4 edge case).
        """
        if not rows:
            return 0
        stmt = (
            pg_insert(Notification)
            .values(rows)
            .on_conflict_do_nothing(
                index_elements=["society_id", "dedupe_key"],
                index_where=_DEDUPE_INDEX_WHERE,
            )
            .returning(Notification.id)
        )
        result = self._session.execute(stmt)
        # RETURNING yields a row only for each ACTUALLY-inserted row (conflicts
        # are skipped), so the count is the number inserted (not attempted).
        return len(result.all())

    # --- feed / badge reads (own-scoped) ----------------------------------

    def list_unread(
        self, society_id: int, user_id: int, *, limit: int, offset: int
    ) -> list[Notification]:
        """A page of the caller's UNREAD feed, newest first (docs §6).

        Scoped to the caller's own rows in the society; hits the
        ``ix_notifications_user_unread`` partial index (``read_at IS NULL``).
        """
        stmt = (
            select(Notification)
            .where(
                Notification.society_id == society_id,
                Notification.user_id == user_id,
                Notification.read_at.is_(None),
            )
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self._session.execute(stmt).scalars().all())

    def unread_count(self, society_id: int, user_id: int) -> int:
        """The caller's total unread count — the badge (docs §6).

        A single ``COUNT`` over the same partial index; independent of the page.
        """
        stmt = select(func.count()).where(
            Notification.society_id == society_id,
            Notification.user_id == user_id,
            Notification.read_at.is_(None),
        )
        return int(self._session.execute(stmt).scalar_one())

    def exists_owned(
        self, society_id: int, user_id: int, notification_id: int
    ) -> bool:
        """Whether the caller owns a notification with this id (read or unread).

        Lets the service distinguish 404 (not yours / doesn't exist) from a
        no-op re-read (yours, already read) so ``POST /{id}/read`` returns the
        right status.
        """
        stmt = select(Notification.id).where(
            Notification.society_id == society_id,
            Notification.user_id == user_id,
            Notification.id == notification_id,
        )
        return self._session.execute(stmt).scalar_one_or_none() is not None

    # --- mark-read writes (own-scoped) ------------------------------------

    def mark_one_read(
        self,
        society_id: int,
        user_id: int,
        notification_id: int,
        *,
        now: datetime,
    ) -> int:
        """Set ``read_at`` on one unread, caller-owned notification (docs §6).

        Returns rows affected (0 if not owned / already read). Scoped hard to
        (society, user, id) so it can never clear another user's row.
        """
        stmt = (
            update(Notification)
            .where(
                Notification.society_id == society_id,
                Notification.user_id == user_id,
                Notification.id == notification_id,
                Notification.read_at.is_(None),
            )
            .values(read_at=now)
        )
        return int(self._session.execute(stmt).rowcount or 0)

    def mark_all_read(
        self, society_id: int, user_id: int, *, now: datetime
    ) -> int:
        """Set ``read_at`` on ALL the caller's unread notifications (docs §6).

        Returns the number cleared. Own-scoped; hits the unread partial index.
        """
        stmt = (
            update(Notification)
            .where(
                Notification.society_id == society_id,
                Notification.user_id == user_id,
                Notification.read_at.is_(None),
            )
            .values(read_at=now)
        )
        return int(self._session.execute(stmt).rowcount or 0)

    def mark_entity_read(
        self,
        user_id: int,
        entity_type: str,
        entity_id: int,
        *,
        now: datetime,
    ) -> int:
        """Clear-on-read: set ``read_at`` on the user's pending notifications for
        one entity (docs §4.4 — ``mark_read_for``).

        Keyed by (user, entity_type, entity_id) across societies is unnecessary —
        an entity id is society-local, but a user only has rows in their own
        society for it, so scoping by user + entity is exact and hits the
        ``ix_notifications_user_entity_unread`` partial index. Returns rows
        cleared (0 when nothing pending — a safe no-op).
        """
        stmt = (
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.entity_type == entity_type,
                Notification.entity_id == entity_id,
                Notification.read_at.is_(None),
            )
            .values(read_at=now)
        )
        return int(self._session.execute(stmt).rowcount or 0)

    # --- purge (worker) ---------------------------------------------------

    def delete_read_before(self, cutoff: datetime) -> int:
        """Delete read notifications whose ``read_at`` is older than ``cutoff``.

        The daily read-purge (docs §9). Hits ``ix_notifications_read_at``
        (``read_at IS NOT NULL``). Returns rows deleted. NOT society-scoped: the
        worker runs the retention per society and computes the cutoff from that
        society's config, but the delete itself may be issued per society (see the
        job) — this primitive deletes by time only for the ids it is handed.
        """
        stmt = delete(Notification).where(
            Notification.read_at.is_not(None),
            Notification.read_at < cutoff,
        )
        return int(self._session.execute(stmt).rowcount or 0)

    def delete_read_before_for_society(
        self, society_id: int, cutoff: datetime
    ) -> int:
        """Society-scoped variant of the purge (per-society retention, docs §9)."""
        stmt = delete(Notification).where(
            Notification.society_id == society_id,
            Notification.read_at.is_not(None),
            Notification.read_at < cutoff,
        )
        return int(self._session.execute(stmt).rowcount or 0)

    # --- test/introspection helper ----------------------------------------

    def societies_with_any(self) -> Iterable[int]:
        """Distinct society ids that have at least one notification row.

        Small helper the purge worker can use to bound its per-society sweep to
        societies that actually have rows.
        """
        stmt = select(Notification.society_id).distinct()
        return [int(r) for r in self._session.execute(stmt).scalars().all()]
