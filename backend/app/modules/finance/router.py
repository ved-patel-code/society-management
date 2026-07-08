"""Finance routes (docs/modules/finance.md §6), prefix ``/finance``.

Society comes from the JWT (``TenantContext.society_id``) — never a path id. Every
route gates on ``require_module('finance')`` + a permission (docs §2):
``finance.read`` (analytics/reads), ``finance.manage_rate``,
``finance.record_payment``, ``finance.manage_expenses``, ``finance.manage_reserve``.
The router stays thin: resolve tenant → call ``FinanceService`` → shape response.
"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.common.errors import PermissionDeniedError, ValidationError
from app.common.pagination import PageParams
from app.core.db import get_session
from app.core.deps import (
    AuthContext,
    TenantContext,
    get_tenant_context,
    require_module,
    require_permission,
)
from app.modules.finance.schemas import (
    ArrearsOut,
    CollectionSummaryOut,
    ExpenseCategoryCreateRequest,
    ExpenseCategoryOut,
    ExpenseCreateRequest,
    ExpenseListOut,
    ExpenseOut,
    ExpenseVoidRequest,
    ExpensesAnalyticsOut,
    HouseDuesOut,
    IncomeAnalyticsOut,
    LedgerEntryOut,
    PaymentOut,
    PaymentRecordRequest,
    PaymentVoidRequest,
    PrepaidRecordRequest,
    RateHistoryOut,
    RateOut,
    RatePreviewOut,
    RateSetRequest,
    ReserveEntryCreateRequest,
    ReserveOut,
    ReserveReconcileRequest,
    TrendsOut,
)
from app.modules.finance.service import FinanceService

router = APIRouter(prefix="/finance", tags=["finance"])

_MODULE = require_module("finance")


def _gate(perm: str) -> list:
    """Both gates for a permission: module enabled + the permission held."""
    return [Depends(_MODULE), Depends(require_permission(perm))]


_READ = _gate("finance.read")
_RATE = _gate("finance.manage_rate")
_PAY = _gate("finance.record_payment")
_EXPENSE = _gate("finance.manage_expenses")
_RESERVE = _gate("finance.manage_reserve")


def _society_id(tenant: TenantContext) -> int:
    if tenant.society_id is None:
        raise ValidationError("No active society for this request.")
    return tenant.society_id


# ============================ Rate ===========================================


@router.get("/rate", response_model=RateHistoryOut, dependencies=_READ)
def get_rate(
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> RateHistoryOut:
    """Current rate + history (docs §6)."""
    return FinanceService(session).rates.get_rate(_society_id(tenant))


@router.post("/rate", response_model=RateOut, dependencies=_RATE)
def set_rate(
    body: RateSetRequest,
    auth: AuthContext = Depends(require_permission("finance.manage_rate")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> RateOut:
    """Set a new effective-dated rate (docs §6)."""
    return FinanceService(session).rates.set_rate(
        _society_id(tenant), body, actor_user_id=auth.user_id
    )


@router.get("/rate/preview", response_model=RatePreviewOut, dependencies=_READ)
def preview_rate(
    amount: Decimal = Query(..., gt=0),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> RatePreviewOut:
    """Projected monthly collection at a proposed rate (docs §4/§6)."""
    return FinanceService(session).rates.preview(_society_id(tenant), amount)


# ============================ Collection =====================================


@router.get(
    "/houses/{house_id}/dues", response_model=HouseDuesOut, dependencies=_READ
)
def house_dues(
    house_id: int,
    auth: AuthContext = Depends(require_permission("finance.read")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> HouseDuesOut:
    """Outstanding months + total + history for a house (docs finance §2/§6).

    Data-driven scope, so future roles work with no code change:
    - ``finance.read_all`` (society_admin, super_admin, any future finance-staff
      role) → read ANY house's dues.
    - ``finance.read`` only (a resident) → restricted to a house they currently
      occupy; otherwise 403.
    """
    society_id = _society_id(tenant)
    # super_admin bypasses (platform operator, consistent with core/deps gates);
    # otherwise the cross-house view is the finance.read_all capability — never a
    # hardcoded role/permission list (docs/02 §4: roles are data-driven).
    if not (auth.is_super_admin or auth.has_permission("finance.read_all")):
        from app.modules.houses.service import HouseService

        if not HouseService(session).is_current_occupant(
            society_id, auth.user_id, house_id
        ):
            raise PermissionDeniedError(
                "You may only view dues for your own house."
            )
    return FinanceService(session).collection.get_house_dues(
        society_id, house_id
    )


@router.post(
    "/houses/{house_id}/payments", response_model=PaymentOut, dependencies=_PAY
)
def record_payment(
    house_id: int,
    body: PaymentRecordRequest,
    auth: AuthContext = Depends(require_permission("finance.record_payment")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> PaymentOut:
    """Settle N oldest months (or all) for a house (docs §6)."""
    return FinanceService(session).collection.record_payment(
        _society_id(tenant), house_id, body, actor_user_id=auth.user_id
    )


@router.post(
    "/houses/{house_id}/prepaid", response_model=PaymentOut, dependencies=_PAY
)
def record_prepaid(
    house_id: int,
    body: PrepaidRecordRequest,
    auth: AuthContext = Depends(require_permission("finance.record_payment")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> PaymentOut:
    """Buy a prepaid block (3/6/9/12 months, arrears-first) (docs §6)."""
    return FinanceService(session).collection.record_prepaid(
        _society_id(tenant), house_id, body, actor_user_id=auth.user_id
    )


@router.post(
    "/payments/{payment_id}/void", response_model=PaymentOut, dependencies=_PAY
)
def void_payment(
    payment_id: int,
    body: PaymentVoidRequest,
    auth: AuthContext = Depends(require_permission("finance.record_payment")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> PaymentOut:
    """Void a payment: re-opens dues + posts a reversing ledger entry (docs §6)."""
    return FinanceService(session).collection.void_payment(
        _society_id(tenant), payment_id, body, actor_user_id=auth.user_id
    )


# ============================ Expenses =======================================


@router.get(
    "/expense-categories",
    response_model=list[ExpenseCategoryOut],
    dependencies=_READ,
)
def list_categories(
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> list[ExpenseCategoryOut]:
    """List expense categories (seeds system defaults on first access) (docs §6)."""
    return FinanceService(session).expenses.list_categories(_society_id(tenant))


@router.post(
    "/expense-categories",
    response_model=ExpenseCategoryOut,
    dependencies=_EXPENSE,
)
def add_category(
    body: ExpenseCategoryCreateRequest,
    auth: AuthContext = Depends(require_permission("finance.manage_expenses")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> ExpenseCategoryOut:
    """Add a society expense category (docs §6)."""
    return FinanceService(session).expenses.add_category(
        _society_id(tenant), body, actor_user_id=auth.user_id
    )


@router.get("/expenses", response_model=ExpenseListOut, dependencies=_READ)
def list_expenses(
    page: PageParams = Depends(),
    include_voided: bool = Query(default=True),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> ExpenseListOut:
    """Paginated expense list + total, newest-first (docs §6). ``include_voided``
    defaults True — voided expenses stay visible in reports (spec §4)."""
    items, total = FinanceService(session).expenses.list_expenses(
        _society_id(tenant),
        offset=page.offset,
        limit=page.limit,
        include_voided=include_voided,
    )
    return ExpenseListOut(items=items, total=total)


@router.post("/expenses", response_model=ExpenseOut, dependencies=_EXPENSE)
def record_expense(
    body: ExpenseCreateRequest,
    auth: AuthContext = Depends(require_permission("finance.manage_expenses")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> ExpenseOut:
    """Record an expense (posts an outflow ledger entry) (docs §6)."""
    return FinanceService(session).expenses.record_expense(
        _society_id(tenant), body, actor_user_id=auth.user_id
    )


@router.post(
    "/expenses/{expense_id}/void", response_model=ExpenseOut, dependencies=_EXPENSE
)
def void_expense(
    expense_id: int,
    body: ExpenseVoidRequest,
    auth: AuthContext = Depends(require_permission("finance.manage_expenses")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> ExpenseOut:
    """Void an expense: posts a reversal (both stay visible) (docs §6)."""
    return FinanceService(session).expenses.void_expense(
        _society_id(tenant), expense_id, body, actor_user_id=auth.user_id
    )


# ============================ Reserve ========================================


@router.get("/reserve", response_model=ReserveOut, dependencies=_READ)
def get_reserve(
    page: PageParams = Depends(),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> ReserveOut:
    """Computed balance + ledger history (incl. reversals) (docs §6)."""
    return FinanceService(session).reserve.get_reserve(
        _society_id(tenant), offset=page.offset, limit=page.limit
    )


@router.post(
    "/reserve/entries", response_model=LedgerEntryOut, dependencies=_RESERVE
)
def post_reserve_entry(
    body: ReserveEntryCreateRequest,
    auth: AuthContext = Depends(require_permission("finance.manage_reserve")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> LedgerEntryOut:
    """Post a dated reserve entry (deposit/interest/resale/income/adjustment)."""
    return FinanceService(session).reserve.post_entry(
        _society_id(tenant), body, actor_user_id=auth.user_id
    )


@router.post(
    "/reserve/entries/{entry_id}/reverse",
    response_model=LedgerEntryOut,
    dependencies=_RESERVE,
)
def reverse_reserve_entry(
    entry_id: int,
    auth: AuthContext = Depends(require_permission("finance.manage_reserve")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> LedgerEntryOut:
    """Reverse a reserve entry (posts a negating entry; both visible) (docs §6)."""
    return FinanceService(session).reserve.reverse_entry(
        _society_id(tenant), entry_id, actor_user_id=auth.user_id
    )


@router.post(
    "/reserve/reconcile", response_model=LedgerEntryOut, dependencies=_RESERVE
)
def reconcile_reserve(
    body: ReserveReconcileRequest,
    auth: AuthContext = Depends(require_permission("finance.manage_reserve")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> LedgerEntryOut:
    """Reconcile-to-bank: post an adjustment for the difference (docs §6)."""
    return FinanceService(session).reserve.reconcile(
        _society_id(tenant), body, actor_user_id=auth.user_id
    )


# ============================ Analytics ======================================


@router.get(
    "/analytics/collection",
    response_model=CollectionSummaryOut,
    dependencies=_READ,
)
def analytics_collection(
    year: int | None = Query(default=None, ge=2000, le=9999),
    month: int | None = Query(default=None, ge=1, le=12),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> CollectionSummaryOut:
    """Expected vs collected vs outstanding (society + per house) (docs §6)."""
    return FinanceService(session).analytics.collection(
        _society_id(tenant), year=year, month=month
    )


@router.get("/analytics/arrears", response_model=ArrearsOut, dependencies=_READ)
def analytics_arrears(
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> ArrearsOut:
    """Houses in arrears with totals + oldest period (docs §6)."""
    return FinanceService(session).analytics.arrears(_society_id(tenant))


@router.get(
    "/analytics/expenses",
    response_model=ExpensesAnalyticsOut,
    dependencies=_READ,
)
def analytics_expenses(
    year: int | None = Query(default=None, ge=2000, le=9999),
    month: int | None = Query(default=None, ge=1, le=12),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> ExpensesAnalyticsOut:
    """Expense-by-category + total for recorded expenses (docs §6)."""
    return FinanceService(session).analytics.expenses(
        _society_id(tenant), year=year, month=month
    )


@router.get(
    "/analytics/income", response_model=IncomeAnalyticsOut, dependencies=_READ
)
def analytics_income(
    year: int | None = Query(default=None, ge=2000, le=9999),
    month: int | None = Query(default=None, ge=1, le=12),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> IncomeAnalyticsOut:
    """Income + collection − expense = net (docs §6)."""
    return FinanceService(session).analytics.income(
        _society_id(tenant), year=year, month=month
    )


@router.get("/analytics/trends", response_model=TrendsOut, dependencies=_READ)
def analytics_trends(
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> TrendsOut:
    """Month-over-month collected / expense / net (docs §6)."""
    return FinanceService(session).analytics.trends(_society_id(tenant))


# ============================ Worker trigger =================================


@router.post("/dues/generate", response_model=dict, dependencies=_RATE)
def trigger_due_cycle(
    auth: AuthContext = Depends(require_permission("finance.manage_rate")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> dict:
    """On-demand dues generation for this society (docs §9 — callable on demand).

    Complements the daily worker scan; idempotent. Gated on ``finance.manage_rate``
    (a rate-management operation). Returns the count created.
    """
    created = FinanceService(session).generate_due_cycle(
        _society_id(tenant), actor_user_id=auth.user_id
    )
    return {"created": created}
