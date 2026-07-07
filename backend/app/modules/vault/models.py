"""Vault tables (docs/modules/vault.md §3).

Three module-owned tables on top of the frozen foundation schema. They live here
(NOT in ``app.platform.models``) and are imported by ``alembic/env.py`` so
autogenerate + the test-harness truncate see them.

Rules honored (docs/03 §3/§5):
- BIGINT identity PK + ``created_at``/``updated_at`` come from ``Base``.
- DB holds ONLY integrity constraints (PK/FK/NOT NULL/UNIQUE) — every enum-like
  domain (``system_key``, ``source``) and every business rule lives in the
  service layer.
- Every tenant table carries ``society_id``; composite indexes lead with it.

``vault_folders.notice_id`` is a nullable BIGINT with NO foreign key yet — the
``notices`` table does not exist until the Notice Board module is built (mirrors
the ``house_occupancies.id_proof_document_id`` skeleton-then-wire pattern). The
Notice Board migration will add the FK later.
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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# --- Enum-like string domains (enforced in the service layer, not the DB) ---
# vault_folders.system_key:
#   houses_root | house | house_proof | house_complaints | notices_root | notice
# vault_documents.source:  manual | id_proof | complaint | notice


class VaultFolder(Base):
    """A folder in a society's vault tree (docs §3/§4).

    ``parent_id IS NULL`` marks a root-level folder. ``is_system`` folders (the
    ``Houses``/``Notices`` roots and their auto-created subtrees) are protected:
    the service refuses to rename/move/delete them. System folders link to their
    subject by id (``house_id`` / ``notice_id``) so a building/notice rename never
    desyncs — the display name is DERIVED from the subject, never stored here for
    system rows (regular folders store their literal ``name``).

    NOTE: the partial-unique index below does NOT constrain root-level siblings
    (SQL treats ``parent_id IS NULL`` as distinct), so the service enforces
    name-collision for root folders explicitly.
    """

    __tablename__ = "vault_folders"
    __table_args__ = (
        # Sibling folder names are unique among LIVE (non-trashed) folders. Root
        # siblings (parent_id NULL) are additionally guarded in the service.
        Index(
            "uq_vault_folders_parent_name",
            "society_id",
            "parent_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_vault_folders_society_parent", "society_id", "parent_id"),
        Index("ix_vault_folders_house", "house_id"),
    )

    society_id: Mapped[int] = mapped_column(
        ForeignKey("societies.id"), nullable=False
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("vault_folders.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    system_key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # System folders link to their subject by id (rename-safe display name).
    house_id: Mapped[int | None] = mapped_column(
        ForeignKey("houses.id"), nullable=True
    )
    # Nullable, NO FK yet — notices table does not exist (Notice Board TODO).
    notice_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class VaultDocument(Base):
    """A stored file (docs §3/§4).

    The bytes live in MinIO under ``storage_key``; this row is the metadata +
    quota unit. ``storage_key`` embeds the document id
    (``societies/{society_id}/{id}__{filename}``) so rename/move is DB-only — the
    object is never touched. Soft-delete sets ``deleted_at`` (Trash); the bytes +
    row survive until permanent purge, and still count toward the quota.
    """

    __tablename__ = "vault_documents"
    __table_args__ = (
        # Listing a folder's live documents.
        Index(
            "ix_vault_documents_society_folder",
            "society_id",
            "folder_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Trash purge scans by deletion time.
        Index("ix_vault_documents_deleted_at", "deleted_at"),
        # One object key per document.
        Index("uq_vault_documents_storage_key", "storage_key", unique=True),
    )

    society_id: Mapped[int] = mapped_column(
        ForeignKey("societies.id"), nullable=False
    )
    folder_id: Mapped[int] = mapped_column(
        ForeignKey("vault_folders.id"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # manual | id_proof | complaint | notice
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="manual"
    )
    # occupancy / complaint / notice id the file originated from (source != manual).
    source_ref: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    uploaded_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )


class SocietyStorageUsage(Base):
    """Running byte total for a society's vault (docs §3/§4).

    One row per society (``society_id`` UNIQUE). Counts LIVE and trashed bytes;
    decremented only on permanent delete. A nightly worker reconciles drift by
    re-summing ``vault_documents``.
    """

    __tablename__ = "society_storage_usage"
    __table_args__ = (
        Index("uq_society_storage_usage_society", "society_id", unique=True),
    )

    society_id: Mapped[int] = mapped_column(
        ForeignKey("societies.id"), nullable=False
    )
    used_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
