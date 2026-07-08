"""Finance Pydantic contracts + enum-like domains (docs/modules/finance.md).

FROZEN request/response models the router and the inter-module ``api`` speak, plus
the string domains every layer shares (enforced in the service — the DB stores raw
strings, docs/03 §3). Wave sub-agents implement service logic against THESE names;
they add fields only additively.

Money is carried as ``Decimal`` (never float) and validated to 2 dp; periods are
``(year, month)`` pairs. Shared validators live here so no layer re-derives them.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --- Domains (allowed string values; service-enforced) -----------------------
# Only the sets actually consumed by validators/services live here; the remaining
# column domains (due status/source, payment status/provider, expense status) are
# documented at their model columns in ``models.py`` and set via literals in the
# service — keeping a second unused copy here would only risk drift.

PAYMENT_METHODS = frozenset({"cash", "cheque", "bank_transfer", "online", "other"})

# Ledger entry_types an ADMIN may post directly (docs §6). collection/expense/
# reversal are posted internally by their flows, never via the reserve endpoint.
RESERVE_POSTABLE_ENTRY_TYPES = frozenset(
    {"opening", "deposit", "interest", "resale_transfer", "income", "adjustment"}
)
LEDGER_DIRECTIONS = frozenset({"inflow", "outflow"})
# entry_type → its natural direction (adjustment can be either — caller supplies).
ENTRY_TYPE_DIRECTION = {
    "opening": "inflow",
    "deposit": "inflow",
    "interest": "inflow",
    "resale_transfer": "inflow",
    "income": "inflow",
    "collection": "inflow",
    "expense": "outflow",
}

# Ledger source_type values.
LEDGER_SOURCE_TYPES = frozenset({"payment", "expense", "prepaid", "house"})

# Prepaid block sizes (docs §8 config default; validated per-society).
DEFAULT_PREPAID_BLOCKS = [3, 6, 9, 12]

# Config (docs §8): society_modules.config for finance.
DEFAULT_MAINTENANCE_DUE_DAY = 1
MIN_DUE_DAY = 1
MAX_DUE_DAY = 28  # month-safe (docs §8: 1–28)

# Seeded system expense categories (docs §3).
DEFAULT_EXPENSE_CATEGORIES = [
    "Electricity",
    "Water",
    "Housekeeping",
    "Security",
    "Repairs",
    "Salaries",
    "Misc",
]

# Money guardrails.
MONEY_QUANT = Decimal("0.01")
MAX_MONEY = Decimal("9999999999.99")  # NUMERIC(12,2) ceiling.


def quantize_money(value: Decimal | int | str) -> Decimal:
    """THE money rounding rule: coerce to a 2 dp Decimal (ROUND_HALF_UP).

    Single source of truth — the service-layer ``money()`` helper delegates here so
    rounding is defined once and can never drift between the schema and service
    layers (docs/03 §1 — shared logic lives in one place).
    """
    return Decimal(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


class _Base(BaseModel):
    """ORM-friendly base for response models."""

    model_config = ConfigDict(from_attributes=True)


# --- shared field validators -------------------------------------------------


def _validate_positive_money(v: Decimal) -> Decimal:
    if v <= 0:
        raise ValueError("amount must be positive.")
    if v > MAX_MONEY:
        raise ValueError("amount exceeds the maximum allowed value.")
    # Reject sub-cent precision rather than silently rounding money input.
    if v != v.quantize(MONEY_QUANT):
        raise ValueError("amount must have at most 2 decimal places.")
    return v


# ============================ Rates ==========================================


class RateSetRequest(BaseModel):
    """Set a new effective-dated rate (docs §6). ``valid_from`` month-aligned."""

    amount: Decimal = Field(...)
    valid_from: date

    _amt = field_validator("amount")(staticmethod(_validate_positive_money))

    @field_validator("valid_from")
    @classmethod
    def _month_aligned(cls, v: date) -> date:
        if v.day != 1:
            raise ValueError("valid_from must be the first day of a month.")
        return v


class RateOut(_Base):
    id: int
    amount: Decimal
    valid_from: date
    created_at: datetime


class RateHistoryOut(BaseModel):
    current: RateOut | None
    history: list[RateOut]


class RatePreviewOut(BaseModel):
    """Rate-change projection (docs §4/§6) — nothing persisted."""

    proposed_amount: Decimal
    dues_owing_houses: int
    projected_monthly_collection: Decimal
    current_amount: Decimal | None
    current_monthly_collection: Decimal | None
    delta: Decimal | None


# ============================ Dues / collection ==============================


class HouseDueOut(_Base):
    id: int
    house_id: int
    period_year: int
    period_month: int
    amount_due: Decimal
    due_date: date
    status: str
    source: str
    locked_rate: Decimal | None
    paid_at: datetime | None
    is_overdue: bool = False


class HouseDuesOut(BaseModel):
    """The "enter house number → see dues" response (docs §4/§6)."""

    house_id: int
    outstanding: list[HouseDueOut]
    outstanding_total: Decimal
    history: list[HouseDueOut]


class PaymentRecordRequest(BaseModel):
    """Record a payment settling N oldest months or all (docs §4/§6).

    Exactly one of ``months`` (settle the N oldest outstanding months) or
    ``pay_all`` (settle every outstanding month) selects the scope. No
    partial-within-month: the amount is derived from the months settled.
    """

    method: str
    reference: str | None = None
    paid_at: date | None = None
    months: int | None = Field(default=None, ge=1)
    pay_all: bool = False

    @field_validator("method")
    @classmethod
    def _method(cls, v: str) -> str:
        if v not in PAYMENT_METHODS:
            raise ValueError(f"method must be one of {sorted(PAYMENT_METHODS)}.")
        return v


class PrepaidRecordRequest(BaseModel):
    """Buy a prepaid block (docs §4/§6). Arrears must be cleared first."""

    months_count: int
    method: str
    reference: str | None = None
    paid_at: date | None = None

    @field_validator("method")
    @classmethod
    def _method(cls, v: str) -> str:
        if v not in PAYMENT_METHODS:
            raise ValueError(f"method must be one of {sorted(PAYMENT_METHODS)}.")
        return v


class PaymentVoidRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=1000)


class PaymentAllocationOut(_Base):
    id: int
    house_due_id: int
    amount_applied: Decimal
    period_year: int | None = None
    period_month: int | None = None


class PaymentOut(_Base):
    id: int
    house_id: int
    amount: Decimal
    method: str
    reference: str | None
    provider: str
    status: str
    paid_at: datetime
    voided_at: datetime | None
    void_reason: str | None
    allocations: list[PaymentAllocationOut] = []


# ============================ Expenses / income ==============================


class ExpenseCategoryOut(_Base):
    id: int
    name: str
    is_system: bool


class ExpenseCategoryCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


class ExpenseCreateRequest(BaseModel):
    category_id: int
    amount: Decimal = Field(...)
    description: str | None = Field(default=None, max_length=2000)
    incurred_on: date

    _amt = field_validator("amount")(staticmethod(_validate_positive_money))


class ExpenseVoidRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=1000)


class ExpenseOut(_Base):
    id: int
    category_id: int
    amount: Decimal
    description: str | None
    incurred_on: date
    status: str
    voided_at: datetime | None
    void_reason: str | None


class ExpenseListOut(BaseModel):
    """Paginated expense list envelope (carries the total for client paging)."""

    items: list[ExpenseOut]
    total: int


# ============================ Reserve ledger =================================


class ReserveEntryCreateRequest(BaseModel):
    """Post a manual reserve entry (docs §4/§6).

    ``entry_type`` must be one of :data:`RESERVE_POSTABLE_ENTRY_TYPES`. Direction
    is derived for fixed types; ``adjustment`` requires an explicit ``direction``.
    """

    entry_type: str
    amount: Decimal = Field(...)
    occurred_on: date
    description: str | None = Field(default=None, max_length=2000)
    direction: str | None = None
    # Optional link (e.g. resale_transfer tied to a house).
    source_type: str | None = None
    source_id: int | None = None

    _amt = field_validator("amount")(staticmethod(_validate_positive_money))

    @field_validator("entry_type")
    @classmethod
    def _entry_type(cls, v: str) -> str:
        if v not in RESERVE_POSTABLE_ENTRY_TYPES:
            raise ValueError(
                f"entry_type must be one of {sorted(RESERVE_POSTABLE_ENTRY_TYPES)}."
            )
        return v

    @field_validator("direction")
    @classmethod
    def _direction(cls, v: str | None) -> str | None:
        if v is not None and v not in LEDGER_DIRECTIONS:
            raise ValueError(f"direction must be one of {sorted(LEDGER_DIRECTIONS)}.")
        return v


class ReserveReconcileRequest(BaseModel):
    """Reconcile-to-bank: post an ``adjustment`` for (actual − computed) (docs §4)."""

    actual_balance: Decimal = Field(...)
    occurred_on: date
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("actual_balance")
    @classmethod
    def _bounded(cls, v: Decimal) -> Decimal:
        if abs(v) > MAX_MONEY:
            raise ValueError("actual_balance exceeds the maximum allowed value.")
        if v != v.quantize(MONEY_QUANT):
            raise ValueError("actual_balance must have at most 2 decimal places.")
        return v


class LedgerEntryOut(_Base):
    id: int
    entry_type: str
    direction: str
    amount: Decimal
    description: str | None
    occurred_on: date
    source_type: str | None
    source_id: int | None
    reverses_entry_id: int | None
    is_reversed: bool
    created_at: datetime


class ReserveOut(BaseModel):
    """Computed reserve balance + full ledger (reversals visible) (docs §4/§6)."""

    balance: Decimal
    entries: list[LedgerEntryOut]
    total: int


# ============================ Analytics ======================================


class CollectionLineOut(BaseModel):
    house_id: int
    expected: Decimal
    collected: Decimal
    outstanding: Decimal


class CollectionSummaryOut(BaseModel):
    period_year: int | None
    period_month: int | None
    expected: Decimal
    collected: Decimal
    outstanding: Decimal
    per_house: list[CollectionLineOut]


class ArrearsLineOut(BaseModel):
    house_id: int
    outstanding_total: Decimal
    oldest_period_year: int
    oldest_period_month: int
    months_outstanding: int


class ArrearsOut(BaseModel):
    total_outstanding: Decimal
    houses: list[ArrearsLineOut]


class ExpenseCategoryBreakdownOut(BaseModel):
    category_id: int
    category_name: str
    total: Decimal


class ExpensesAnalyticsOut(BaseModel):
    period_year: int | None
    period_month: int | None
    total_expense: Decimal
    by_category: list[ExpenseCategoryBreakdownOut]


class IncomeAnalyticsOut(BaseModel):
    period_year: int | None
    period_month: int | None
    total_income: Decimal
    total_collection: Decimal
    total_expense: Decimal
    net: Decimal


class TrendPointOut(BaseModel):
    period_year: int
    period_month: int
    collected: Decimal
    expense: Decimal
    net: Decimal


class TrendsOut(BaseModel):
    points: list[TrendPointOut]


# ============================ Config =========================================


class FinanceConfig(BaseModel):
    """Validated view of ``society_modules.config`` for finance (docs §8)."""

    maintenance_due_day: int = Field(
        default=DEFAULT_MAINTENANCE_DUE_DAY, ge=MIN_DUE_DAY, le=MAX_DUE_DAY
    )
    prepaid_blocks: list[int] = Field(default_factory=lambda: list(DEFAULT_PREPAID_BLOCKS))

    @field_validator("prepaid_blocks")
    @classmethod
    def _positive_blocks(cls, v: list[int]) -> list[int]:
        if not v or any(m <= 0 for m in v):
            raise ValueError("prepaid_blocks must be positive month counts.")
        return v
