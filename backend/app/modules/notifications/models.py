"""Notifications table (docs/modules/notifications.md §3).

A single module-owned table on top of ``users``/``societies`` (Foundation). It
lives here (NOT in the frozen ``app.platform.models``) and is imported by
``alembic/env.py`` so autogenerate + the test-harness truncate see it.

Rules honored (docs/03 §3/§5):
- BIGINT identity PK + ``created_at``/``updated_at`` come from ``Base``.
- DB holds ONLY integrity constraints (PK/FK/NOT NULL/UNIQUE) + the indexes the
  common queries need — the ``type`` domain, recipient resolution, dedupe cadence,
  and clear-on-read all live in the service layer.
- Every tenant table carries ``society_id``; the indexes match the engine's hot
  paths (docs/03 §5): the unread feed/badge, the mark-read lookup, the purge scan,
  and the dedupe uniqueness backstop.

Design notes:
- **One row per recipient per event** (docs §3). A notice fan-out inserts one row
  per current owner (batched); a complaint update inserts one row for the raiser.
- ``payload`` (JSONB) carries the data a client needs to render + deep-link (and
  is exactly what a FUTURE push/WebSocket frame would carry — the channel seam is
  in the ``notify`` service, not here).
- ``entity_type``/``entity_id`` are the deep-link target AND the key the
  clear-on-read hook (``mark_read_for``) matches on.
- ``dedupe_key`` (nullable) makes scheduled fires idempotent: the partial UNIQUE
  index means a worker re-run inserts nothing new.
- ``read_at`` NULL == in the feed (unread). Setting it removes the row from the
  feed; the daily purge deletes rows whose ``read_at`` is older than retention.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# --- Enum-like string domain (extensible; enforced in the service layer) ------
# notifications.type: complaint_update | complaint_new | complaint_withdrawn
#                     | notice | maintenance_due | … (a new type is just a string)


class Notification(Base):
    """One in-app notification for one recipient (docs §3/§4).

    Created ONLY by the engine (event handlers + the reminder worker) — there is
    no public create endpoint. The recipient reads it via the feed and clears it
    by opening it or by opening the underlying item (``mark_read_for``).
    """

    __tablename__ = "notifications"
    __table_args__ = (
        # Idempotency backstop for scheduled fires: at most one row per
        # (society, dedupe_key) when a key is set (docs §3 — "dues:{house}:{day}").
        Index(
            "uq_notifications_society_dedupe",
            "society_id",
            "dedupe_key",
            unique=True,
            postgresql_where=text("dedupe_key IS NOT NULL"),
        ),
        # The HOT path: the caller's unread feed (newest first) + badge count.
        # Partial (unread only) so it stays small and the badge query is O(index).
        Index(
            "ix_notifications_user_unread",
            "user_id",
            "created_at",
            postgresql_where=text("read_at IS NULL"),
        ),
        # Clear-on-read: a user's pending notifications for one entity.
        Index(
            "ix_notifications_user_entity_unread",
            "user_id",
            "entity_type",
            "entity_id",
            postgresql_where=text("read_at IS NULL"),
        ),
        # The daily read-purge scan (only rows that have been read).
        Index(
            "ix_notifications_read_at",
            "read_at",
            postgresql_where=text("read_at IS NOT NULL"),
        ),
    )

    society_id: Mapped[int] = mapped_column(
        ForeignKey("societies.id"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    # complaint_update | complaint_new | complaint_withdrawn | notice
    # | maintenance_due | … (extensible string domain, service-enforced)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    entity_type: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    entity_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
