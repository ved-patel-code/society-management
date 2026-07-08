"""Finance tables (docs/modules/finance.md §3).

Eight module-owned tables on top of the shared ``houses`` registry (Onboarding)
and the ``societies`` config. They live here (NOT in ``app.platform.models``,
which is frozen) and are imported by ``alembic/env.py`` so autogenerate + the
test-harness truncate see them.

Rules honored (docs/03 §3/§5):
- BIGINT identity PK + ``created_at``/``updated_at`` come from ``Base``.
- Money is ``NUMERIC(12,2)`` everywhere (docs §3). Never float.
- DB holds ONLY integrity constraints (PK/FK/NOT NULL/UNIQUE) — every enum-like
  domain (``status``, ``source``, ``method``, ``entry_type``, ``direction``) and
  every business rule lives in the service layer.
- Every tenant table carries ``society_id``; composite indexes lead with it and
  match each feature's common query (docs/03 §5).

The reserve is a COMPUTED running ledger (``ledger_entries``): balance = Σ inflow
− Σ outflow. Corrections are void/reversal (audit-preserving) and stay VISIBLE in
reports (docs §4). No monetary row is ever hard-updated to "fix" a value.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# --- Enum-like string domains (enforced in the service layer, not the DB) -----
# house_dues.status:        outstanding | paid
# house_dues.source:        accrued | prepaid
# payments.method:          cash | cheque | bank_transfer | online | other
# payments.status:          recorded | voided
# payments.provider:        admin_manual | gateway
# expenses.status:          recorded | voided
# ledger_entries.entry_type:
#   opening | deposit | interest | resale_transfer | income | collection
#   | expense | adjustment | reversal
# ledger_entries.direction: inflow | outflow
# prepaid_blocks.months_count: 3 | 6 | 9 | 12

# Money precision (docs §3): NUMERIC(12,2).
_MONEY = Numeric(12, 2)


class MaintenanceRate(Base):
    """Effective-dated society-wide maintenance rate per house (docs §3/§4).

    Setting a new rate = a NEW row (history is never edited). The rate for a
    month M is the row with the latest ``valid_from <= first-of-M``. ``valid_from``
    is month-aligned (day 1) — enforced by the service, not the DB.
    """

    __tablename__ = "maintenance_rates"
    __table_args__ = (
        # One rate per effective month per society (idempotent set + fast lookup).
        Index(
            "uq_maintenance_rates_society_valid_from",
            "society_id",
            "valid_from",
            unique=True,
        ),
        # Rate-for-month resolution scans by society then newest valid_from.
        Index("ix_maintenance_rates_society_valid_from", "society_id", "valid_from"),
    )

    society_id: Mapped[int] = mapped_column(
        ForeignKey("societies.id"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )


class HouseDue(Base):
    """One materialized monthly due for a house (docs §3/§4).

    Created by the dues worker (or on-demand) for each dues-owing house on the
    society's due day, at the effective rate. ``status`` flips outstanding→paid
    when a payment (or prepaid block) settles the whole month; ``source``
    distinguishes normal accrual from a prepaid-covered month (which locks the
    rate at purchase time in ``locked_rate``). Overdue is COMPUTED (outstanding +
    ``due_date`` in the past), never stored.
    """

    __tablename__ = "house_dues"
    __table_args__ = (
        # One due per (house, period) — makes generation idempotent (docs §4).
        Index(
            "uq_house_dues_house_period",
            "society_id",
            "house_id",
            "period_year",
            "period_month",
            unique=True,
        ),
        # Society-wide outstanding scans (analytics, arrears).
        Index("ix_house_dues_society_status", "society_id", "status"),
        # Per-house dues lookup (collection "enter house number") + oldest-first.
        Index("ix_house_dues_house_status", "house_id", "status"),
    )

    society_id: Mapped[int] = mapped_column(
        ForeignKey("societies.id"), nullable=False
    )
    house_id: Mapped[int] = mapped_column(
        ForeignKey("houses.id"), nullable=False
    )
    period_year: Mapped[int] = mapped_column(Integer, nullable=False)
    period_month: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_due: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    # outstanding | paid
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="outstanding"
    )
    # accrued | prepaid
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="accrued"
    )
    # Set only for prepaid-covered months (the rate locked at purchase).
    locked_rate: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Payment(Base):
    """A recorded (or voided) payment against a house's dues (docs §3/§4).

    Admin-recorded in v1 behind a ``PaymentProvider`` interface
    (``provider=admin_manual``; a gateway is future). Settles whole months
    oldest-first via ``payment_allocations``; posts a ``collection`` inflow to the
    ledger. Voiding does NOT delete: it flips ``status=voided``, re-opens the
    settled dues, and posts a reversing ledger entry — the original stays visible
    (docs §4 transparency).
    """

    __tablename__ = "payments"
    __table_args__ = (
        Index("ix_payments_society_house", "society_id", "house_id"),
        Index("ix_payments_society_status", "society_id", "status"),
    )

    society_id: Mapped[int] = mapped_column(
        ForeignKey("societies.id"), nullable=False
    )
    house_id: Mapped[int] = mapped_column(
        ForeignKey("houses.id"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    # cash | cheque | bank_transfer | online | other
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # admin_manual | gateway (PaymentProvider interface — docs §7).
    provider: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="admin_manual"
    )
    provider_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # recorded | voided
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="recorded"
    )
    recorded_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    paid_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    voided_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    voided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    void_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class PaymentAllocation(Base):
    """Maps a payment to the specific month(s) it settles (docs §3/§4).

    One row per (payment, house_due) with the amount applied to that whole month
    (no partial-within-month — ``amount_applied`` equals the due's ``amount_due``
    in v1). Enables exact re-open on void and per-month audit of collection.
    """

    __tablename__ = "payment_allocations"
    __table_args__ = (
        Index("ix_payment_allocations_payment", "payment_id"),
        Index("ix_payment_allocations_house_due", "house_due_id"),
    )

    society_id: Mapped[int] = mapped_column(
        ForeignKey("societies.id"), nullable=False
    )
    payment_id: Mapped[int] = mapped_column(
        ForeignKey("payments.id"), nullable=False
    )
    house_due_id: Mapped[int] = mapped_column(
        ForeignKey("house_dues.id"), nullable=False
    )
    amount_applied: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)


class PrepaidBlock(Base):
    """A prepaid block covering the next N months at a locked rate (docs §3/§4).

    Requires arrears cleared first; pays 3/6/9/12 months at the locked current
    rate, materializing the covered ``house_dues`` as ``source=prepaid`` + paid.
    Tied to the HOUSE — if the owner is replaced mid-window, those months stay
    paid. ``start_period``/``end_period`` are ``YYYYMM`` ints (service-computed).
    """

    __tablename__ = "prepaid_blocks"
    __table_args__ = (
        Index("ix_prepaid_blocks_society_house", "society_id", "house_id"),
    )

    society_id: Mapped[int] = mapped_column(
        ForeignKey("societies.id"), nullable=False
    )
    house_id: Mapped[int] = mapped_column(
        ForeignKey("houses.id"), nullable=False
    )
    months_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rate_locked: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    payment_id: Mapped[int] = mapped_column(
        ForeignKey("payments.id"), nullable=False
    )
    # YYYYMM inclusive window (e.g. 202608 .. 202601 for 6 months from Aug).
    start_period: Mapped[int] = mapped_column(Integer, nullable=False)
    end_period: Mapped[int] = mapped_column(Integer, nullable=False)


class ExpenseCategory(Base):
    """An expense category (docs §3/§4).

    Seeded defaults (``is_system=true``: Electricity, Water, Housekeeping,
    Security, Repairs, Salaries, Misc) + society-added (``is_system=false``).
    Extendable; unique per society by name.
    """

    __tablename__ = "expense_categories"
    __table_args__ = (
        Index(
            "uq_expense_categories_society_name",
            "society_id",
            "name",
            unique=True,
        ),
    )

    society_id: Mapped[int] = mapped_column(
        ForeignKey("societies.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )


class Expense(Base):
    """A recorded (or voided) society expense (docs §3/§4).

    Posts an ``expense`` outflow to the ledger. Voiding posts a reversal and flips
    ``status=voided`` — both stay visible (docs §4). Non-monetary fields (e.g.
    ``description``) are editable; the amount is corrected only via void + re-post.
    """

    __tablename__ = "expenses"
    __table_args__ = (
        Index("ix_expenses_society_incurred", "society_id", "incurred_on"),
        Index("ix_expenses_society_category", "society_id", "category_id"),
    )

    society_id: Mapped[int] = mapped_column(
        ForeignKey("societies.id"), nullable=False
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("expense_categories.id"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    incurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    recorded_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    # recorded | voided
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="recorded"
    )
    voided_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    voided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    void_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class LedgerEntry(Base):
    """The reserve backbone + transparency log (docs §3/§4).

    Every money movement posts ONE entry: a recorded payment → ``collection``
    inflow; an expense → ``expense`` outflow; manual opening/deposit/interest/
    resale/income/adjustment → their own entry. A reversal posts a NEGATING entry
    that references the original (``reverses_entry_id``); both stay visible and the
    original is flagged ``is_reversed=true``. Reserve balance = Σ inflow − Σ
    outflow over ALL entries (computed, never stored).
    """

    __tablename__ = "ledger_entries"
    __table_args__ = (
        # Balance + history scan by society, ordered by occurrence.
        Index("ix_ledger_entries_society_occurred", "society_id", "occurred_on"),
        Index("ix_ledger_entries_reverses", "reverses_entry_id"),
    )

    society_id: Mapped[int] = mapped_column(
        ForeignKey("societies.id"), nullable=False
    )
    # opening|deposit|interest|resale_transfer|income|collection|expense
    # |adjustment|reversal
    entry_type: Mapped[str] = mapped_column(String(24), nullable=False)
    # inflow | outflow
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    # What produced the entry: payment | expense | prepaid | house | None.
    source_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    recorded_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    # A reversal points at the entry it negates; the original is flagged reversed.
    reverses_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("ledger_entries.id"), nullable=True
    )
    is_reversed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
