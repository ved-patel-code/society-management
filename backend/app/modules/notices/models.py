"""Notice Board tables (docs/modules/notice-board.md §3).

Three module-owned tables on top of ``users``/``societies`` (Foundation) and the
``vault_documents`` store (Vault). They live here (NOT in the frozen
``app.platform.models``) and are imported by ``alembic/env.py`` so autogenerate +
the test-harness truncate see them.

Rules honored (docs/03 §3/§5):
- BIGINT identity PK + ``created_at``/``updated_at`` come from ``Base``.
- DB holds ONLY integrity constraints (PK/FK/NOT NULL/UNIQUE) — the ``status``
  domain, the transition table, expiry/pin behavior, and read/receipt logic all
  live in the service layer. ``expired`` is COMPUTED at query time (published +
  ``expires_at < now``) and is NEVER a stored status.
- Every tenant table carries ``society_id``; composite indexes lead with it and
  match the feed's common query (docs/03 §5).

Design notes:
- ``notices`` is society-scoped — a broadcast to the whole society, no
  ``house_id`` (docs §3). The two indexes serve the admin filter view and the hot
  active-feed path (published, pinned-first, newest-first) respectively.
- ``notice_attachments`` links a notice to Vault documents (``store_document``
  returns the id); NO count cap — bounded only by the society's Vault quota.
- ``notice_reads`` is the per-user read state: one idempotent row per reader
  (``UNIQUE(notice_id, user_id)``), driving unread badges + admin read receipts.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# --- Enum-like string domain (enforced in the service layer, not the DB) ------
# notices.status: draft | published | withdrawn
#   - ``expired`` is COMPUTED (published + expires_at < now), never stored.


class Notice(Base):
    """A society-wide broadcast notice (docs §3/§4).

    ``status`` drives the lifecycle (``draft → published → withdrawn``).
    ``published_at`` is stamped on publish (and orders the feed); ``expires_at``
    (optional) drops the notice off the active feed at query time; ``is_pinned``
    floats it to the top; ``last_edited_at`` is stamped only when the CONTENT
    (title/body) is edited after publish (drives the UI "edited · <date>"
    marker). ``withdrawn_at``/``withdrawn_by`` record a soft-withdraw. ``body`` is
    rich text stored ALREADY SANITIZED (docs §4 — see ``common/html_sanitizer``).
    """

    __tablename__ = "notices"
    __table_args__ = (
        # Admin filter view + the general feed ordering (published, pinned-first,
        # newest-first). Leads with society_id (tenant scope, docs/03 §5).
        Index(
            "ix_notices_society_status_pinned_published",
            "society_id",
            "status",
            "is_pinned",
            "published_at",
        ),
        # The HOT active-feed path: only published rows, pinned-first then newest.
        # Partial so it stays small and serves the resident landing page fast.
        Index(
            "ix_notices_active_feed",
            "society_id",
            "is_pinned",
            "published_at",
            postgresql_where=text("status = 'published'"),
        ),
    )

    society_id: Mapped[int] = mapped_column(
        ForeignKey("societies.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # draft | published | withdrawn
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="draft"
    )
    is_pinned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_edited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    withdrawn_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )


class NoticeAttachment(Base):
    """A file attached to a notice, filed in the Vault (docs §3/§4/§7).

    ``vault_document_id`` is the document Vault stored under the notice's
    ``Notices/<notice id>/`` system folder (via ``vault.api.store_document`` with
    ``source='notice'``). NO count cap — bounded only by the society's Vault
    quota (Vault enforces type denylist + quota). Removing an attachment
    soft-deletes the Vault document and drops this row (docs §4).
    """

    __tablename__ = "notice_attachments"
    __table_args__ = (Index("ix_notice_attachments_notice", "notice_id"),)

    society_id: Mapped[int] = mapped_column(
        ForeignKey("societies.id"), nullable=False
    )
    notice_id: Mapped[int] = mapped_column(
        ForeignKey("notices.id"), nullable=False
    )
    vault_document_id: Mapped[int] = mapped_column(
        ForeignKey("vault_documents.id"), nullable=False
    )
    added_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )


class NoticeRead(Base):
    """Per-user read state for a notice (docs §3/§4).

    One idempotent row per reader, inserted on first open (and by mark-all-read).
    Drives the resident's unread badge and the admin read-receipt view (current
    owners LEFT JOIN these rows). ``UNIQUE(notice_id, user_id)`` is the DB
    backstop for the idempotent insert. Reads are NOT audited (high-volume, not
    an admin state-change — docs §4/§5).
    """

    __tablename__ = "notice_reads"
    __table_args__ = (
        # One read row per (notice, user) — the idempotent-insert backstop.
        Index(
            "uq_notice_reads_notice_user",
            "notice_id",
            "user_id",
            unique=True,
        ),
        # Receipts join scans a notice's readers.
        Index("ix_notice_reads_notice", "notice_id"),
    )

    society_id: Mapped[int] = mapped_column(
        ForeignKey("societies.id"), nullable=False
    )
    notice_id: Mapped[int] = mapped_column(
        ForeignKey("notices.id"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
