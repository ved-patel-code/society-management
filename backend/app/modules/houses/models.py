"""House & Occupancy tables (docs/modules/house-occupancy.md §3).

Two module-owned tables on top of the shared ``houses`` registry that Onboarding
created. They live here (NOT in ``app.platform.models``, which is frozen, and NOT
in ``onboarding.models``) and are imported by ``alembic/env.py`` so autogenerate +
the test-harness truncate see them.

Rules honored (docs/03 §3/§5):
- BIGINT identity PK + ``created_at``/``updated_at`` come from ``Base``.
- DB holds ONLY integrity constraints (PK/FK/NOT NULL/UNIQUE) — every enum-like
  domain (``party_type``, house ``status``) and every business rule lives in the
  service layer.
- Every tenant table carries ``society_id``; composite indexes lead with it.

Shared ``houses`` table: this module WRITES ``status`` + ``first_left_empty_on``
(created by Onboarding's migration); Onboarding owns the structure columns. The
``houses`` model is therefore NOT redeclared here — see ``onboarding.models.House``.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# --- Enum-like string domains (enforced in the service layer, not the DB) ---
# house_occupancies.party_type:  owner | tenant
# houses.status (shared table):  empty | owned | rented | to_let | for_sale


class HouseOccupancy(Base):
    """One owner/tenant record for a house, current or historical (docs §3/§4).

    Occupancy follows a validity window: the current record has ``is_current=true``
    and ``valid_to IS NULL``; closing it sets ``is_current=false`` + ``valid_to``.
    At most one CURRENT owner and one CURRENT tenant per house — enforced by the
    partial unique index below.

    ``email`` is stored lower-normalized by the service so "same email = same
    owner" comparisons match the case-insensitive semantics of ``users.email``.

    ``user_id`` links to the provisioned login (owner only in v1; tenant login is
    deferred, so tenant rows keep ``user_id=NULL``).

    ``id_proof_document_id`` links a stored ID-proof image in the vault. The FK to
    ``vault_documents.id`` (ON DELETE SET NULL) was added by the Vault migration
    (0004) — this column started as a bare BIGINT while Vault did not exist yet
    (docs §3/§7 "wired when Vault built").
    """

    __tablename__ = "house_occupancies"
    __table_args__ = (
        # At most one current owner + one current tenant per house.
        Index(
            "uq_house_occupancy_current",
            "house_id",
            "party_type",
            unique=True,
            postgresql_where=text("is_current = true"),
        ),
        Index("ix_house_occupancies_society_house", "society_id", "house_id"),
        Index("ix_house_occupancies_user", "user_id"),
    )

    society_id: Mapped[int] = mapped_column(
        ForeignKey("societies.id"), nullable=False
    )
    house_id: Mapped[int] = mapped_column(
        ForeignKey("houses.id"), nullable=False
    )
    party_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # Login link (foundation provisions). NULL for tenants (login deferred).
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Owner: required (login identity). Tenant: optional. Lower-normalized by service.
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    contact_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Captured for owned (owner) + rented (tenant); not for to_let/for_sale.
    persons_living: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Optional ID proof (docs §3/§4 — OPTIONAL everywhere).
    id_proof_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    # FK to the vault document holding the ID-proof image (wired by Vault 0004).
    id_proof_document_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("vault_documents.id", ondelete="SET NULL"),
        nullable=True,
    )

    is_current: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)


class HouseStatusHistory(Base):
    """Append-only log of a house's status changes (docs §3/§4).

    One row per real status transition, written in the same transaction as the
    change (alongside the ``audit_log`` entry). ``snapshot`` captures the target's
    occupancy payload at the moment of change for a full audit trail.
    """

    __tablename__ = "house_status_history"
    __table_args__ = (
        Index("ix_house_status_history_society_house", "society_id", "house_id"),
    )

    society_id: Mapped[int] = mapped_column(
        ForeignKey("societies.id"), nullable=False
    )
    house_id: Mapped[int] = mapped_column(
        ForeignKey("houses.id"), nullable=False
    )
    from_status: Mapped[str] = mapped_column(String(16), nullable=False)
    to_status: Mapped[str] = mapped_column(String(16), nullable=False)
    changed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    # Distinct from Base.created_at so the domain event time is explicit.
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
