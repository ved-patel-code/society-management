"""Complaints tables (docs/modules/complaints.md §3).

Five module-owned tables on top of the shared ``houses`` registry (Onboarding),
the ``vault_documents`` store (Vault), and ``users``/``societies`` (Foundation).
They live here (NOT in ``app.platform.models``, which is frozen) and are imported
by ``alembic/env.py`` so autogenerate + the test-harness truncate see them.

Rules honored (docs/03 §3/§5):
- BIGINT identity PK + ``created_at``/``updated_at`` come from ``Base``.
- DB holds ONLY integrity constraints (PK/FK/NOT NULL/UNIQUE) — every enum-like
  domain (``status``, image ``kind``) and every business rule (transition table,
  image caps, ownership) lives in the service layer.
- Every tenant table carries ``society_id``; composite indexes lead with it and
  match each feature's common query (docs/03 §5).

Design notes:
- ``complaints.reference`` (``C-000123``) is allocated per society from
  ``complaint_reference_counters`` under a FOR-UPDATE row lock (see
  ``repository.allocate_reference``); the partial UNIQUE below is the backstop.
- ``complaint_status_history`` is the append-only status timeline AND the home of
  admin notes (docs §3). ``changed_by = NULL`` marks a system/worker transition
  (auto-archive).
- ``complaint_images`` is the now-wired link to the Vault: it stores the
  ``vault_document_id`` returned by ``vault.api.store_document`` (docs §7).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
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

# --- Enum-like string domains (enforced in the service layer, not the DB) -----
# complaints.status:            open | in_progress | resolved | closed
#                               | archived | withdrawn
# complaint_status_history.*_status: same set (from_status NULL = initial create)
# complaint_images.kind:        report | proof


class ComplaintCategory(Base):
    """A complaint category for a society (docs §3/§4).

    Seeded defaults (``is_system=true``: Plumbing, Electrical, Common Area,
    Security, Cleaning, Other) + admin-added (``is_system=false``). Categories are
    never hard-deleted — deactivation (``is_active=false``) keeps them attached to
    historical complaints but hides them from new-complaint choices. No two ACTIVE
    categories share a name (partial unique below); a deactivated name may be
    reused by a new active category.
    """

    __tablename__ = "complaint_categories"
    __table_args__ = (
        # No two ACTIVE categories share a name per society (docs §3). A
        # deactivated row is excluded, so the name frees up for reuse.
        Index(
            "uq_complaint_categories_society_active_name",
            "society_id",
            "name",
            unique=True,
            postgresql_where=text("is_active = true"),
        ),
        # Active-category listing (the create form) scans by (society, is_active).
        Index("ix_complaint_categories_society_active", "society_id", "is_active"),
    )

    society_id: Mapped[int] = mapped_column(
        ForeignKey("societies.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    # Seeded defaults are flagged system (renamable but recommended kept).
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )


class Complaint(Base):
    """A complaint raised by an owner, tied to their house (docs §3/§4).

    ``house_id`` is NOT NULL — every complaint (including a common-area issue,
    which is just a category) attaches to the raiser's house; data follows the
    house (docs §2). ``status`` drives the workflow; the ``*_at`` timestamps are
    stamped on entry to their state by ``support.record_transition`` and drive the
    auto-archive worker (``closed_at``). Overdue/age are computed, never stored.
    """

    __tablename__ = "complaints"
    __table_args__ = (
        # Human reference unique per society (allocator backstop).
        Index(
            "uq_complaints_society_reference",
            "society_id",
            "reference",
            unique=True,
        ),
        # Admin list filtered by status.
        Index("ix_complaints_society_status", "society_id", "status"),
        # Resident's own list + house profile.
        Index("ix_complaints_society_house", "society_id", "house_id"),
        # Category filter.
        Index("ix_complaints_society_category", "society_id", "category_id"),
        # The auto-archive worker scan: closed complaints past their close date.
        Index(
            "ix_complaints_status_closed_at",
            "status",
            "closed_at",
            postgresql_where=text("status = 'closed'"),
        ),
    )

    society_id: Mapped[int] = mapped_column(
        ForeignKey("societies.id"), nullable=False
    )
    reference: Mapped[str] = mapped_column(String(16), nullable=False)
    house_id: Mapped[int] = mapped_column(
        ForeignKey("houses.id"), nullable=False
    )
    raised_by: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("complaint_categories.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # open | in_progress | resolved | closed | archived | withdrawn
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="open"
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ComplaintStatusHistory(Base):
    """The append-only status timeline + admin notes (docs §3/§4).

    One row per transition, written in the same transaction as the change (via
    ``support.record_transition``). ``from_status = NULL`` marks the initial create
    (``NULL → open``); ``changed_by = NULL`` marks a system/worker transition
    (auto-archive). ``note`` carries the admin's optional per-transition note (and
    the solution note on resolve).
    """

    __tablename__ = "complaint_status_history"
    __table_args__ = (
        Index(
            "ix_complaint_status_history_complaint_created",
            "complaint_id",
            "created_at",
        ),
    )

    society_id: Mapped[int] = mapped_column(
        ForeignKey("societies.id"), nullable=False
    )
    complaint_id: Mapped[int] = mapped_column(
        ForeignKey("complaints.id"), nullable=False
    )
    # NULL = initial create (NULL -> open).
    from_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_status: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # NULL = system/worker (auto-archive).
    changed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )


class ComplaintImage(Base):
    """A report or proof photo, filed in the Vault (docs §3/§4/§7).

    ``kind='report'`` — the resident's issue photos (≤ ``max_report_images``, added
    while the complaint is ``open``). ``kind='proof'`` — the admin's resolution
    photos (≤ ``max_proof_images``, attached ONLY during the resolve transition,
    locked after). ``vault_document_id`` is the document Vault stored under
    ``Houses/<house>/Complaints/<reference>/``; removing an image soft-deletes the
    Vault document and drops this row (docs §4).
    """

    __tablename__ = "complaint_images"
    __table_args__ = (
        Index("ix_complaint_images_complaint_kind", "complaint_id", "kind"),
    )

    society_id: Mapped[int] = mapped_column(
        ForeignKey("societies.id"), nullable=False
    )
    complaint_id: Mapped[int] = mapped_column(
        ForeignKey("complaints.id"), nullable=False
    )
    # report | proof
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    vault_document_id: Mapped[int] = mapped_column(
        ForeignKey("vault_documents.id"), nullable=False
    )
    added_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )


class ComplaintReferenceCounter(Base):
    """Per-society running counter for complaint references (docs §3).

    A singleton row per society holding the last-allocated value. The allocator
    takes a ``SELECT ... FOR UPDATE`` on this row and increments it inside the
    create transaction (mirrors the vault storage-usage lock idiom), so references
    are unique per society and gap-tolerant (a rolled-back create may burn a
    number — acceptable per docs §3). A DISTINCT table (not a column on some hot
    table) so the reference lock never blocks unrelated complaint reads/writes.
    """

    __tablename__ = "complaint_reference_counters"
    __table_args__ = (
        Index(
            "uq_complaint_reference_counters_society",
            "society_id",
            unique=True,
        ),
    )

    society_id: Mapped[int] = mapped_column(
        ForeignKey("societies.id"), nullable=False
    )
    # Last allocated value; next reference = next_value + 1.
    next_value: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
