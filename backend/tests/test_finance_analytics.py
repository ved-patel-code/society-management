"""Analytics read-projection tests for the Finance module (Module 4, Wave F).

Covers :class:`AnalyticsService` end to end over the five read endpoints
(``GET /finance/analytics/{collection|arrears|expenses|income|trends}``):

- collection summary society-wide + per-house; expected/collected/outstanding
  arithmetic; the ``year``/``month`` period filter narrows correctly.
- arrears lists ONLY houses with outstanding dues, with correct totals, oldest
  period, and months count.
- expenses-by-category totals + period filter; a VOIDED expense is excluded.
- income/net = income + collection − expense, NET of reversals (a voided payment
  and a voided expense are reflected honestly).
- trends across ≥2 months, ordered oldest→newest.
- security: ``finance.read`` is required (403 without) and cross-society
  isolation (society B sees only its own data / zeros).
- analytics persist NOTHING (pure reads — row counts unchanged across a call).

Reuses the House & Occupancy harness to build houses that owe, plus the real
Finance service/HTTP flows (rate → generate dues → record payments/expenses/
reserve entries → void) to arrange ledger + dues state.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from app.modules.finance.models import (
    Expense,
    HouseDue,
    LedgerEntry,
    Payment,
    PaymentAllocation,
)
from app.modules.finance.schemas import (
    ExpenseCreateRequest,
    ExpenseVoidRequest,
    PaymentRecordRequest,
    PaymentVoidRequest,
    RateSetRequest,
    ReserveEntryCreateRequest,
)
from app.modules.finance.service import FinanceService
from app.platform.models import User, UserRole
from app.platform.roles.service import RoleService
from app.platform.societies.schemas import ModuleAllocation, SocietyCreate
from app.platform.societies.service import SocietyService

from tests.conftest import DEFAULT_MEMBER_PASSWORD
from tests._houses_helpers import (
    _admin_bearer,
    _make_building_with_houses,
    _owner,
    _set_status,
)


# --- setup helpers ----------------------------------------------------------

def _enable_finance(db, society, superadmin, *, config=None) -> None:
    """Enable onboarding + houses + finance (finance depends_on houses)."""
    SocietyService(db).set_modules(
        society.id,
        [
            ModuleAllocation(module_key="onboarding", enabled=True, config={}),
            ModuleAllocation(module_key="houses", enabled=True, config={}),
            ModuleAllocation(
                module_key="finance", enabled=True, config=config or {}
            ),
        ],
        actor_user_id=superadmin.id,
    )
    db.commit()


def _setup(db, society, admin_user, superadmin, auth, *, config=None):
    """Enable the three modules + return an activated admin bearer header."""
    _enable_finance(db, society, superadmin, config=config)
    return _admin_bearer(auth, admin_user)


def _set_rate(db, society_id, amount, valid_from, *, actor):
    FinanceService(db).rates.set_rate(
        society_id,
        RateSetRequest(amount=Decimal(str(amount)), valid_from=valid_from),
        actor_user_id=actor,
    )
    db.commit()


def _generate(db, society_id, *, as_of):
    n = FinanceService(db).generate_due_cycle(society_id, as_of=as_of)
    db.commit()
    return n


def _two_owing_houses(auth, hdr):
    """Build a 2-house building and move both off ``empty`` so they owe."""
    houses = _make_building_with_houses(
        auth, hdr, floors=[{"level": 1, "houses_count": 2}]
    )
    _set_status(auth, hdr, houses[0]["id"], "owned", _owner(persons_living=2))
    _set_status(
        auth, hdr, houses[1]["id"], "owned",
        _owner(email="o2@x.com", persons_living=1),
    )
    return houses[0]["id"], houses[1]["id"]


def _record_payment(db, society_id, house_id, *, actor, months=None, pay_all=False):
    p = FinanceService(db).collection.record_payment(
        society_id,
        house_id,
        PaymentRecordRequest(method="cash", months=months, pay_all=pay_all),
        actor_user_id=actor,
    )
    db.commit()
    return p


def _record_expense(db, society_id, category_id, amount, incurred_on, *, actor):
    e = FinanceService(db).expenses.record_expense(
        society_id,
        ExpenseCreateRequest(
            category_id=category_id,
            amount=Decimal(str(amount)),
            incurred_on=incurred_on,
        ),
        actor_user_id=actor,
    )
    db.commit()
    return e


def _first_category_id(db, society_id, *, actor):
    cats = FinanceService(db).expenses.list_categories(society_id)
    db.commit()
    return cats[0].id


def _post_income(db, society_id, amount, occurred_on, *, actor):
    FinanceService(db).reserve.post_entry(
        society_id,
        ReserveEntryCreateRequest(
            entry_type="income",
            amount=Decimal(str(amount)),
            occurred_on=occurred_on,
        ),
        actor_user_id=actor,
    )
    db.commit()


def _ledger_snapshot(db, society_id):
    """Row counts of the finance tables analytics reads (persistence guard)."""
    return {
        "ledger": db.execute(
            select(func.count()).select_from(LedgerEntry).where(
                LedgerEntry.society_id == society_id
            )
        ).scalar_one(),
        "dues": db.execute(
            select(func.count()).select_from(HouseDue).where(
                HouseDue.society_id == society_id
            )
        ).scalar_one(),
        "payments": db.execute(
            select(func.count()).select_from(Payment).where(
                Payment.society_id == society_id
            )
        ).scalar_one(),
        "expenses": db.execute(
            select(func.count()).select_from(Expense).where(
                Expense.society_id == society_id
            )
        ).scalar_one(),
    }


# ===========================================================================
# collection summary: society-wide + per house; arithmetic; period filter
# ===========================================================================

def test_collection_summary_society_and_per_house(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    h1, h2 = _two_owing_houses(auth, hdr)
    # Single-month rate so each house owes exactly one 1000.00 due for 2026-07.
    _set_rate(db, society.id, "1000.00", date(2026, 7, 1), actor=admin_user.id)
    # Backdate so only the current (July) period generates for both houses.
    from app.modules.onboarding.models import House

    for hid in (h1, h2):
        db.get(House, hid).first_left_empty_on = date(2026, 7, 2)
    db.commit()
    assert _generate(db, society.id, as_of=date(2026, 7, 8)) == 2

    # Pay house 1 fully; leave house 2 outstanding.
    _record_payment(db, society.id, h1, actor=admin_user.id, pay_all=True)

    resp = auth.client.get("/finance/analytics/collection", headers=hdr)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert Decimal(body["expected"]) == Decimal("2000.00")
    assert Decimal(body["collected"]) == Decimal("1000.00")
    assert Decimal(body["outstanding"]) == Decimal("1000.00")

    per = {row["house_id"]: row for row in body["per_house"]}
    assert Decimal(per[h1]["expected"]) == Decimal("1000.00")
    assert Decimal(per[h1]["collected"]) == Decimal("1000.00")
    assert Decimal(per[h1]["outstanding"]) == Decimal("0.00")
    assert Decimal(per[h2]["collected"]) == Decimal("0.00")
    assert Decimal(per[h2]["outstanding"]) == Decimal("1000.00")


def test_collection_summary_period_filter_narrows(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    h1, _h2 = _two_owing_houses(auth, hdr)
    _set_rate(db, society.id, "500.00", date(2026, 1, 1), actor=admin_user.id)
    from app.modules.onboarding.models import House

    # Backfill June + July for house 1.
    db.get(House, h1).first_left_empty_on = date(2026, 6, 1)
    db.commit()
    _generate(db, society.id, as_of=date(2026, 7, 8))

    # Society-wide (no filter) sees both months for both houses.
    allr = auth.client.get("/finance/analytics/collection", headers=hdr).json()
    assert Decimal(allr["expected"]) >= Decimal("1000.00")
    assert allr["period_year"] is None and allr["period_month"] is None

    # Filter to July → the July slice only.
    julr = auth.client.get(
        "/finance/analytics/collection",
        headers=hdr,
        params={"year": 2026, "month": 7},
    ).json()
    assert julr["period_year"] == 2026 and julr["period_month"] == 7
    # July expected < the all-time expected (June is excluded).
    assert Decimal(julr["expected"]) < Decimal(allr["expected"])


# ===========================================================================
# arrears: only outstanding houses; totals + oldest period + months count
# ===========================================================================

def test_arrears_lists_only_outstanding_houses(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    h1, h2 = _two_owing_houses(auth, hdr)
    _set_rate(db, society.id, "700.00", date(2026, 1, 1), actor=admin_user.id)
    from app.modules.onboarding.models import House

    # House 1 owes May..July (3 months); house 2 owes only July (1 month).
    db.get(House, h1).first_left_empty_on = date(2026, 5, 1)
    db.get(House, h2).first_left_empty_on = date(2026, 7, 1)
    db.commit()
    _generate(db, society.id, as_of=date(2026, 7, 8))

    # Fully settle house 2 → it drops out of arrears entirely.
    _record_payment(db, society.id, h2, actor=admin_user.id, pay_all=True)

    body = auth.client.get("/finance/analytics/arrears", headers=hdr).json()
    houses = {row["house_id"]: row for row in body["houses"]}
    assert set(houses) == {h1}  # only the still-outstanding house
    assert Decimal(body["total_outstanding"]) == Decimal("2100.00")  # 3 × 700
    line = houses[h1]
    assert Decimal(line["outstanding_total"]) == Decimal("2100.00")
    assert line["oldest_period_year"] == 2026
    assert line["oldest_period_month"] == 5
    assert line["months_outstanding"] == 3


# ===========================================================================
# expenses-by-category: totals + period filter + voided excluded
# ===========================================================================

def test_expenses_by_category_period_and_void(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    cat = _first_category_id(db, society.id, actor=admin_user.id)

    _record_expense(db, society.id, cat, "300.00", date(2026, 7, 5), actor=admin_user.id)
    _record_expense(db, society.id, cat, "200.00", date(2026, 7, 9), actor=admin_user.id)
    # A June expense — excluded by a July filter.
    _record_expense(db, society.id, cat, "999.00", date(2026, 6, 1), actor=admin_user.id)
    # A July expense we then VOID — must NOT count.
    voided = _record_expense(
        db, society.id, cat, "150.00", date(2026, 7, 15), actor=admin_user.id
    )
    FinanceService(db).expenses.void_expense(
        society.id, voided.id, ExpenseVoidRequest(reason="mistake"),
        actor_user_id=admin_user.id,
    )
    db.commit()

    # July filter: 300 + 200 recorded; June + voided excluded.
    jul = auth.client.get(
        "/finance/analytics/expenses",
        headers=hdr,
        params={"year": 2026, "month": 7},
    ).json()
    assert jul["period_year"] == 2026 and jul["period_month"] == 7
    assert Decimal(jul["total_expense"]) == Decimal("500.00")
    cats = {c["category_id"]: c for c in jul["by_category"]}
    assert Decimal(cats[cat]["total"]) == Decimal("500.00")

    # Society-wide (no filter): + the June 999 (voided still excluded).
    allr = auth.client.get("/finance/analytics/expenses", headers=hdr).json()
    assert Decimal(allr["total_expense"]) == Decimal("1499.00")


# ===========================================================================
# income / net = income + collection − expense, NET of reversals
# ===========================================================================

def test_income_net_reflects_reversals(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    h1, _h2 = _two_owing_houses(auth, hdr)
    _set_rate(db, society.id, "1000.00", date(2026, 7, 1), actor=admin_user.id)
    from app.modules.onboarding.models import House

    db.get(House, h1).first_left_empty_on = date(2026, 7, 2)
    db.commit()
    _generate(db, society.id, as_of=date(2026, 7, 8))

    # Collection inflow of 1000 (paying house 1's single month).
    pay = _record_payment(db, society.id, h1, actor=admin_user.id, pay_all=True)
    # Income inflow of 400 (reserve entry).
    _post_income(db, society.id, "400.00", date(2026, 7, 3), actor=admin_user.id)
    # Expense outflow of 250; category from the seeded defaults.
    cat = _first_category_id(db, society.id, actor=admin_user.id)
    exp = _record_expense(
        db, society.id, cat, "250.00", date(2026, 7, 4), actor=admin_user.id
    )

    # Before any void: income=400, collection=1000, expense=250, net=1150.
    b0 = auth.client.get("/finance/analytics/income", headers=hdr).json()
    assert Decimal(b0["total_income"]) == Decimal("400.00")
    assert Decimal(b0["total_collection"]) == Decimal("1000.00")
    assert Decimal(b0["total_expense"]) == Decimal("250.00")
    assert Decimal(b0["net"]) == Decimal("1150.00")

    # Void the payment (reverses the collection) AND the expense (reverses it).
    FinanceService(db).collection.void_payment(
        society.id, pay.id, PaymentVoidRequest(reason="wrong house"),
        actor_user_id=admin_user.id,
    )
    FinanceService(db).expenses.void_expense(
        society.id, exp.id, ExpenseVoidRequest(reason="dup"),
        actor_user_id=admin_user.id,
    )
    db.commit()

    # After voids: collection nets to 0, expense nets to 0, income unchanged.
    b1 = auth.client.get("/finance/analytics/income", headers=hdr).json()
    assert Decimal(b1["total_income"]) == Decimal("400.00")
    assert Decimal(b1["total_collection"]) == Decimal("0.00")
    assert Decimal(b1["total_expense"]) == Decimal("0.00")
    assert Decimal(b1["net"]) == Decimal("400.00")

    # And the net matches the computed reserve balance (income + collection −
    # expense, all net of reversals) — the two derivations agree.
    reserve = FinanceService(db).reserve.balance(society.id)
    assert Decimal(b1["net"]) == reserve


# ===========================================================================
# trends: ≥2 months, oldest→newest, net of reversals
# ===========================================================================

def test_trends_across_months_ordered(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    cat = _first_category_id(db, society.id, actor=admin_user.id)

    # June: expense 100. July: expense 300 + a voided expense 50 (nets out).
    _record_expense(db, society.id, cat, "100.00", date(2026, 6, 10), actor=admin_user.id)
    _record_expense(db, society.id, cat, "300.00", date(2026, 7, 10), actor=admin_user.id)
    voided = _record_expense(
        db, society.id, cat, "50.00", date(2026, 7, 20), actor=admin_user.id
    )
    FinanceService(db).expenses.void_expense(
        society.id, voided.id, ExpenseVoidRequest(reason="oops"),
        actor_user_id=admin_user.id,
    )
    db.commit()

    body = auth.client.get("/finance/analytics/trends", headers=hdr).json()
    pts = body["points"]
    # Oldest→newest ordering by (year, month).
    keys = [(p["period_year"], p["period_month"]) for p in pts]
    assert keys == sorted(keys)
    by_period = {(p["period_year"], p["period_month"]): p for p in pts}

    # NOTE: the void reversal is dated when the correction happened (today =
    # 2026-07), so July's expense nets 300 (350 posted − 50 reversal).
    assert Decimal(by_period[(2026, 6)]["expense"]) == Decimal("100.00")
    assert Decimal(by_period[(2026, 6)]["net"]) == Decimal("-100.00")
    assert Decimal(by_period[(2026, 7)]["expense"]) == Decimal("300.00")
    assert Decimal(by_period[(2026, 7)]["collected"]) == Decimal("0.00")
    assert Decimal(by_period[(2026, 7)]["net"]) == Decimal("-300.00")


# ===========================================================================
# analytics persist NOTHING (pure reads)
# ===========================================================================

def test_analytics_persist_nothing(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    h1, _h2 = _two_owing_houses(auth, hdr)
    _set_rate(db, society.id, "1000.00", date(2026, 7, 1), actor=admin_user.id)
    from app.modules.onboarding.models import House

    db.get(House, h1).first_left_empty_on = date(2026, 7, 2)
    db.commit()
    _generate(db, society.id, as_of=date(2026, 7, 8))
    _record_payment(db, society.id, h1, actor=admin_user.id, pay_all=True)
    cat = _first_category_id(db, society.id, actor=admin_user.id)
    _record_expense(db, society.id, cat, "50.00", date(2026, 7, 4), actor=admin_user.id)

    before = _ledger_snapshot(db, society.id)
    for path in (
        "/finance/analytics/collection",
        "/finance/analytics/arrears",
        "/finance/analytics/expenses",
        "/finance/analytics/income",
        "/finance/analytics/trends",
    ):
        assert auth.client.get(path, headers=hdr).status_code == 200
    db.expire_all()
    assert _ledger_snapshot(db, society.id) == before


# ===========================================================================
# security: finance.read required (403 without)
# ===========================================================================

def test_analytics_requires_finance_read(
    db, society, admin_user, superadmin, auth
):
    """A caller whose role lacks ``finance.read`` is denied (403) on every
    analytics endpoint, while the admin (who holds it) is admitted."""
    hdr = _setup(db, society, admin_user, superadmin, auth)

    # A society-scoped role with NO permissions, plus a user assigned ONLY it.
    role = RoleService(db).create_role(
        society_id=society.id,
        key="no_finance_read",
        name="No Finance Read",
        portal="admin",
        scope="society",
        permission_keys=[],
        actor_user_id=superadmin.id,
    )
    from app.core.security import hash_password

    user = User(
        email="noread@test.local",
        password_hash=hash_password(DEFAULT_MEMBER_PASSWORD),
        password_state="active",
        is_active=True,
        full_name="No Read",
    )
    db.add(user)
    db.flush()
    db.add(UserRole(user_id=user.id, society_id=society.id, role_id=role.id))
    db.commit()

    tokens = auth.login_ok(user.email, DEFAULT_MEMBER_PASSWORD)
    no_read = auth.bearer(tokens["access_token"])

    for path in (
        "/finance/analytics/collection",
        "/finance/analytics/arrears",
        "/finance/analytics/expenses",
        "/finance/analytics/income",
        "/finance/analytics/trends",
    ):
        denied = auth.client.get(path, headers=no_read)
        assert denied.status_code == 403, f"{path}: {denied.text}"
        assert denied.json()["code"] == "permission_denied"
        # The admin (holds finance.read) is admitted on the same path.
        assert auth.client.get(path, headers=hdr).status_code == 200


# ===========================================================================
# security: cross-society isolation (society B sees only its own / zeros)
# ===========================================================================

def test_cross_society_isolation(
    db, society, admin_user, superadmin, auth
):
    # Society A: build state (dues + a payment + an expense).
    hdr = _setup(db, society, admin_user, superadmin, auth)
    h1, _h2 = _two_owing_houses(auth, hdr)
    _set_rate(db, society.id, "1000.00", date(2026, 7, 1), actor=admin_user.id)
    from app.modules.onboarding.models import House

    db.get(House, h1).first_left_empty_on = date(2026, 7, 2)
    db.commit()
    _generate(db, society.id, as_of=date(2026, 7, 8))
    _record_payment(db, society.id, h1, actor=admin_user.id, pay_all=True)
    cat = _first_category_id(db, society.id, actor=admin_user.id)
    _record_expense(db, society.id, cat, "77.00", date(2026, 7, 4), actor=admin_user.id)

    # Society B: a fresh society with finance enabled + its own admin, NO data.
    soc_b = SocietyService(db).create_society(
        SocietyCreate(
            name="Society B",
            storage_limit_bytes=5 * 1024**3,
            default_member_password=DEFAULT_MEMBER_PASSWORD,
        ),
        actor_user_id=superadmin.id,
    )
    db.commit()
    from app.platform.users.provisioning import UserProvisioningService

    admin_b = UserProvisioningService(db).create_or_link_user(
        email="admin.b@test.local",
        society_id=soc_b.id,
        role_key="society_admin",
        profile={"full_name": "Admin B"},
        actor_user_id=superadmin.id,
    )
    db.commit()
    _enable_finance(db, soc_b, superadmin)
    hdr_b = _admin_bearer(auth, admin_b)

    # Society B's analytics are all empty/zero — it cannot see A's data.
    col_b = auth.client.get("/finance/analytics/collection", headers=hdr_b).json()
    assert Decimal(col_b["expected"]) == Decimal("0.00")
    assert Decimal(col_b["collected"]) == Decimal("0.00")
    assert col_b["per_house"] == []

    arr_b = auth.client.get("/finance/analytics/arrears", headers=hdr_b).json()
    assert arr_b["houses"] == []
    assert Decimal(arr_b["total_outstanding"]) == Decimal("0.00")

    exp_b = auth.client.get("/finance/analytics/expenses", headers=hdr_b).json()
    assert Decimal(exp_b["total_expense"]) == Decimal("0.00")

    inc_b = auth.client.get("/finance/analytics/income", headers=hdr_b).json()
    assert Decimal(inc_b["total_collection"]) == Decimal("0.00")
    assert Decimal(inc_b["net"]) == Decimal("0.00")

    trends_b = auth.client.get("/finance/analytics/trends", headers=hdr_b).json()
    assert trends_b["points"] == []

    # Society A still sees its own non-zero figures (proves scoping, not global).
    col_a = auth.client.get("/finance/analytics/collection", headers=hdr).json()
    assert Decimal(col_a["collected"]) == Decimal("1000.00")
    exp_a = auth.client.get("/finance/analytics/expenses", headers=hdr).json()
    assert Decimal(exp_a["total_expense"]) == Decimal("77.00")
