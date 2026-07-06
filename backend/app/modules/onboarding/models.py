"""Onboarding tables (docs/modules/onboarding.md §3).

Five module-owned tables added on top of the frozen foundation schema. They live
here (NOT in ``app.platform.models``, which is frozen) and are imported by
``alembic/env.py`` so autogenerate + the test-harness truncate see them.

Rules honored (docs/03 §3/§5):
- BIGINT identity PK + ``created_at``/``updated_at`` come from ``Base``.
- DB holds ONLY integrity constraints (PK/FK/NOT NULL/UNIQUE) — every enum-like
  domain and every business rule is enforced in the service layer.
- Every tenant table carries ``society_id``; composite uniques/indexes lead with it.

Shared ``houses`` table (docs/modules/onboarding.md §7): Onboarding OWNS the
structure columns (location/number/mode) and creates each row ``status='empty'``.
House & Occupancy later WRITES ``status`` + ``first_left_empty_on`` on the same
row — those columns are created here (this is the migration that introduces the
``houses`` table) but Onboarding only ever sets ``status='empty'``.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# --- Enum-like string domains (enforced in the service layer, not the DB) ---
# societies.type:        building | individual_houses   (foundation table)
# onboarding_progress.current_step: see onboarding.numbering / service state machine
# buildings.numbering_config.mode:  auto | sequential | manual
# rows.numbering_config.mode:       sequential | custom | manual
# houses.numbering_mode:            auto | sequential | manual  (custom rows store 'manual')
# houses.status:                    empty | owned | rented | to_let | for_sale


class OnboardingProgress(Base):
    """Wizard state for one society — draft + resume cursor (one row per society)."""

    __tablename__ = "onboarding_progress"
    __table_args__ = (
        UniqueConstraint("society_id", name="uq_onboarding_progress_society"),
    )

    society_id: Mapped[int] = mapped_column(
        ForeignKey("societies.id"), nullable=False
    )
    type_selected: Mapped[str | None] = mapped_column(String(32), nullable=True)
    current_step: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="type_selection"
    )
    current_building_index: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    # In-progress building's typed inputs, kept verbatim for exact resume.
    draft: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Last-used numbering mode/pad/ground-prefix, for prefill-repeat across buildings.
    numbering_defaults: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class Building(Base):
    """A tower/block in a ``building``-type society (docs §3)."""

    __tablename__ = "buildings"
    __table_args__ = (
        UniqueConstraint("society_id", "name", name="uq_buildings_society_name"),
        Index("ix_buildings_society", "society_id"),
    )

    society_id: Mapped[int] = mapped_column(
        ForeignKey("societies.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)
    # {mode, count_pad, ground_prefix, has_ground, sequential_scope, display_separator}
    numbering_config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )


class Floor(Base):
    """A floor within a building. Exactly one ground floor per building (docs §3)."""

    __tablename__ = "floors"
    __table_args__ = (
        UniqueConstraint("building_id", "level", name="uq_floors_building_level"),
        # Only one ground floor per building — a partial unique on is_ground=true.
        Index(
            "uq_floors_building_ground",
            "building_id",
            unique=True,
            postgresql_where=text("is_ground = true"),
        ),
        Index("ix_floors_building", "building_id"),
    )

    society_id: Mapped[int] = mapped_column(
        ForeignKey("societies.id"), nullable=False
    )
    building_id: Mapped[int] = mapped_column(
        ForeignKey("buildings.id"), nullable=False
    )
    # Upper floors 1..N; the ground floor stores level 0 and is_ground=true.
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    is_ground: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Per-floor override of houses-per-floor; NULL falls back to the building default.
    houses_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Row(Base):
    """A row/lane in an ``individual_houses``-type society (docs §3)."""

    __tablename__ = "rows"
    __table_args__ = (
        UniqueConstraint(
            "society_id", "display_order", name="uq_rows_society_order"
        ),
        Index("ix_rows_society", "society_id"),
    )

    society_id: Mapped[int] = mapped_column(
        ForeignKey("societies.id"), nullable=False
    )
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    houses_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # {mode, prefix, pad}
    numbering_config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    # Both-sides-of-row is schema-only for now (future — docs §3/§10).
    both_sides: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )


class House(Base):
    """A single house — the shared registry every later module depends on (docs §3/§7).

    Building type: ``building_id`` + ``floor_id`` set, ``row_id`` NULL.
    Individual type: ``row_id`` + ``position_in_row`` set, ``building_id`` NULL.

    Uniqueness is enforced by two PARTIAL indexes (one per society type) because a
    bare number is only unique WITHIN a building (building type) or within the
    society (individual type). The display code (e.g. ``A-201``) is DERIVED in the
    service, never stored, so renaming a building never drifts it.
    """

    __tablename__ = "houses"
    __table_args__ = (
        # Building type: number unique per (society, building).
        Index(
            "uq_houses_building_number",
            "society_id",
            "building_id",
            "number",
            unique=True,
            postgresql_where=text("building_id IS NOT NULL"),
        ),
        # Individual type: number unique per society.
        Index(
            "uq_houses_individual_number",
            "society_id",
            "number",
            unique=True,
            postgresql_where=text("building_id IS NULL"),
        ),
        # Status filter used by House & Occupancy / dashboards.
        Index("ix_houses_society_status", "society_id", "status"),
    )

    society_id: Mapped[int] = mapped_column(
        ForeignKey("societies.id"), nullable=False
    )
    # Building-type location (NULL for individual houses).
    building_id: Mapped[int | None] = mapped_column(
        ForeignKey("buildings.id"), nullable=True
    )
    floor_id: Mapped[int | None] = mapped_column(
        ForeignKey("floors.id"), nullable=True
    )
    # Individual-type location (NULL for building houses).
    row_id: Mapped[int | None] = mapped_column(
        ForeignKey("rows.id"), nullable=True
    )
    position_in_row: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Bare number (e.g. "201"); display code is derived, not stored.
    number: Mapped[str] = mapped_column(String(32), nullable=False)
    numbering_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    number_overridden: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    # --- Owned by House & Occupancy; created here, only ever 'empty' from Onboarding ---
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="empty"
    )
    first_left_empty_on: Mapped[date | None] = mapped_column(Date, nullable=True)
