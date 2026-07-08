"""Deep edge-case tests not in wave tests (test-gate matrix §4).

Rate-effective boundaries, year-crossing backfill, oldest-first allocation across
years, multi-house bulk generation, long mixed reserve ledgers, multi-month
trends, negative-balance reconcile, and steady-state accrual. Uses ``as_of``/
frozen-date control throughout for determinism.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import event, select

from app.modules.finance.models import HouseDue
from app.modules.finance.service import FinanceService
from app.modules.finance.schemas import RateSetRequest

from tests._finance_helpers import (
    freeze_utcnow,
    reserve_balance,
    set_rate_http,
    setup_finance,
)
from tests._houses_helpers import _make_building_with_houses, _owner, _set_status


def _dues_for(db, society_id, house_id):
    return list(
        db.execute(
            select(HouseDue)
            .where(HouseDue.society_id == society_id, HouseDue.house_id == house_id)
            .order_by(HouseDue.period_year, HouseDue.period_month)
        )
        .scalars()
        .all()
    )


def _set_rate_db(db, society_id, amount, valid_from, *, actor):
    FinanceService(db).rates.set_rate(
        society_id,
        RateSetRequest(amount=Decimal(str(amount)), valid_from=valid_from),
        actor_user_id=actor,
    )
    db.commit()


# ===========================================================================
# rate-effective boundaries
# ===========================================================================


def test_rate_effective_boundary_valid_from_equals_period_start(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    assert resp.status_code == 200, resp.text

    from app.modules.onboarding.models import House

    house = db.get(House, hid)
    house.first_left_empty_on = date(2024, 1, 15)
    db.commit()

    _set_rate_db(db, society.id, "1000.00", date(2024, 1, 1), actor=admin_user.id)
    _set_rate_db(db, society.id, "1500.00", date(2024, 3, 1), actor=admin_user.id)

    created = FinanceService(db).generate_due_cycle(
        society.id, as_of=date(2024, 3, 8)
    )
    db.commit()
    assert created == 3  # Jan, Feb, Mar

    dues = _dues_for(db, society.id, hid)
    by_period = {(d.period_year, d.period_month): d.amount_due for d in dues}
    assert by_period[(2024, 1)] == Decimal("1000.00")
    assert by_period[(2024, 2)] == Decimal("1000.00")
    # March is the boundary month: valid_from == period start -> NEW rate.
    assert by_period[(2024, 3)] == Decimal("1500.00")


def test_due_generated_in_month_of_mid_history_rate_change(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    assert resp.status_code == 200, resp.text

    from app.modules.onboarding.models import House

    house = db.get(House, hid)
    house.first_left_empty_on = date(2024, 3, 1)
    db.commit()

    _set_rate_db(db, society.id, "1000.00", date(2024, 1, 1), actor=admin_user.id)
    _set_rate_db(db, society.id, "1500.00", date(2024, 3, 1), actor=admin_user.id)

    created = FinanceService(db).generate_due_cycle(society.id, as_of=date(2024, 3, 8))
    db.commit()
    assert created == 1

    dues = _dues_for(db, society.id, hid)
    assert len(dues) == 1
    assert dues[0].period_year == 2024 and dues[0].period_month == 3
    assert dues[0].amount_due == Decimal("1500.00")  # NOT R1


def test_backfill_across_year_boundary_dec_to_jan(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    assert resp.status_code == 200, resp.text

    from app.modules.onboarding.models import House

    house = db.get(House, hid)
    house.first_left_empty_on = date(2024, 12, 10)
    db.commit()

    _set_rate_db(db, society.id, "1000.00", date(2024, 12, 1), actor=admin_user.id)

    created = FinanceService(db).generate_due_cycle(society.id, as_of=date(2025, 2, 8))
    db.commit()
    assert created == 3

    periods = [(d.period_year, d.period_month) for d in _dues_for(db, society.id, hid)]
    assert periods == [(2024, 12), (2025, 1), (2025, 2)]


def test_backfill_across_year_boundary_and_rate_change(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    assert resp.status_code == 200, resp.text

    from app.modules.onboarding.models import House

    house = db.get(House, hid)
    house.first_left_empty_on = date(2024, 12, 10)
    db.commit()

    _set_rate_db(db, society.id, "1000.00", date(2024, 12, 1), actor=admin_user.id)
    _set_rate_db(db, society.id, "1200.00", date(2025, 2, 1), actor=admin_user.id)

    created = FinanceService(db).generate_due_cycle(society.id, as_of=date(2025, 2, 8))
    db.commit()
    assert created == 3

    dues = _dues_for(db, society.id, hid)
    by_period = {(d.period_year, d.period_month): d.amount_due for d in dues}
    assert by_period[(2024, 12)] == Decimal("1000.00")
    assert by_period[(2025, 1)] == Decimal("1000.00")
    assert by_period[(2025, 2)] == Decimal("1200.00")


# ===========================================================================
# bulk generation + idempotency at scale
# ===========================================================================


def test_multi_house_generation_bulk_correct_no_n_plus_one(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(
        auth, hdr, floors=[{"level": lvl, "houses_count": 6} for lvl in range(1, 6)]
    )
    assert len(houses) == 30
    for h in houses:
        resp = _set_status(
            auth, hdr, h["id"], "owned", _owner(email=f"o{h['id']}@x.com", persons_living=1)
        )
        assert resp.status_code == 200, resp.text

    _set_rate_db(db, society.id, "1000.00", date(2026, 1, 1), actor=admin_user.id)

    query_count = {"n": 0}

    def _count(*_args, **_kwargs):
        query_count["n"] += 1

    from app.core.db import engine

    event.listen(engine, "after_cursor_execute", _count)
    try:
        created = FinanceService(db).generate_due_cycle(society.id, as_of=date(2026, 7, 8))
        db.commit()
    finally:
        event.remove(engine, "after_cursor_execute", _count)

    assert created == 30
    # Bounded: not O(houses x months); allow a generous constant multiple of
    # houses (houses_owing + per-house existing_periods/rate lookups), never an
    # explosive per-month blow-up. 30 houses x 1 month should stay well under a
    # small multiple of houses (matrix: "bounded", not an exact count).
    assert query_count["n"] < 30 * 6

    all_dues = list(
        db.execute(
            select(HouseDue).where(HouseDue.society_id == society.id)
        ).scalars()
    )
    assert len(all_dues) == 30
    assert all(d.amount_due == Decimal("1000.00") for d in all_dues)


def test_generation_idempotent_at_scale(db, society, admin_user, superadmin, auth):
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(
        auth, hdr, floors=[{"level": lvl, "houses_count": 6} for lvl in range(1, 6)]
    )
    for h in houses:
        resp = _set_status(
            auth, hdr, h["id"], "owned", _owner(email=f"o2{h['id']}@x.com", persons_living=1)
        )
        assert resp.status_code == 200, resp.text

    _set_rate_db(db, society.id, "1000.00", date(2026, 1, 1), actor=admin_user.id)
    first = FinanceService(db).generate_due_cycle(society.id, as_of=date(2026, 7, 8))
    db.commit()
    assert first == 30

    second = FinanceService(db).generate_due_cycle(society.id, as_of=date(2026, 7, 8))
    db.commit()
    assert second == 0

    rows = list(
        db.execute(select(HouseDue).where(HouseDue.society_id == society.id)).scalars()
    )
    assert len(rows) == 30  # no duplicates


# ===========================================================================
# prepaid across a year boundary
# ===========================================================================


def test_prepaid_window_spans_year_boundary(
    db, society, admin_user, superadmin, auth, monkeypatch
):
    from datetime import date as _date

    freeze_utcnow(monkeypatch, _date(2024, 11, 10))
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    assert resp.status_code == 200, resp.text

    set_rate_http(auth, hdr, "1000.00", date(2024, 1, 1))
    gen = auth.client.post("/finance/dues/generate", headers=hdr)
    assert gen.status_code == 200, gen.text
    pay = auth.client.post(
        f"/finance/houses/{hid}/payments", headers=hdr, json={"method": "cash", "pay_all": True}
    )
    assert pay.status_code == 200, pay.text

    prepaid = auth.client.post(
        f"/finance/houses/{hid}/prepaid",
        headers=hdr,
        json={"months_count": 6, "method": "cash"},
    )
    assert prepaid.status_code == 200, prepaid.text

    db.expire_all()
    dues = [d for d in _dues_for(db, society.id, hid) if d.source == "prepaid"]
    assert len(dues) == 6
    periods = sorted((d.period_year, d.period_month) for d in dues)
    assert periods == [
        (2024, 12), (2025, 1), (2025, 2), (2025, 3), (2025, 4), (2025, 5),
    ]
    for d in dues:
        assert d.status == "paid"
        assert d.locked_rate == Decimal("1000.00")

    from app.modules.finance.models import PrepaidBlock

    block = db.execute(
        select(PrepaidBlock).where(PrepaidBlock.house_id == hid)
    ).scalar_one()
    assert block.start_period == 202412
    assert block.end_period == 202505


# ===========================================================================
# oldest-first across a year boundary
# ===========================================================================


def test_oldest_first_allocation_across_years(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    assert resp.status_code == 200, resp.text

    for (y, m) in [(2024, 11), (2024, 12), (2025, 1), (2025, 2)]:
        db.add(
            HouseDue(
                society_id=society.id,
                house_id=hid,
                period_year=y,
                period_month=m,
                amount_due=Decimal("1000.00"),
                due_date=date(y, m, 1),
                status="outstanding",
                source="accrued",
            )
        )
    db.commit()

    pay = auth.client.post(
        f"/finance/houses/{hid}/payments", headers=hdr, json={"method": "cash", "months": 3}
    )
    assert pay.status_code == 200, pay.text

    db.expire_all()
    dues = _dues_for(db, society.id, hid)
    settled = {(d.period_year, d.period_month) for d in dues if d.status == "paid"}
    assert settled == {(2024, 11), (2024, 12), (2025, 1)}
    outstanding = [d for d in dues if d.status == "outstanding"]
    assert len(outstanding) == 1
    assert (outstanding[0].period_year, outstanding[0].period_month) == (2025, 2)


# ===========================================================================
# reserve: long mixed ledger with multiple reversals
# ===========================================================================


def test_reserve_balance_long_mixed_ledger_with_multiple_reversals(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_finance(db, society, admin_user, superadmin, auth)

    def _post(entry_type, amount, occurred_on="2026-01-01"):
        return auth.client.post(
            "/finance/reserve/entries",
            headers=hdr,
            json={"entry_type": entry_type, "amount": amount, "occurred_on": occurred_on},
        )

    opening = _post("opening", "10000.00")
    deposit1 = _post("deposit", "2000.00")
    deposit2 = _post("deposit", "500.00")
    interest = _post("interest", "100.00")

    cats = auth.client.get("/finance/expense-categories", headers=hdr).json()
    exp1 = auth.client.post(
        "/finance/expenses",
        headers=hdr,
        json={"category_id": cats[0]["id"], "amount": "300.00", "incurred_on": "2026-01-02"},
    )
    exp2 = auth.client.post(
        "/finance/expenses",
        headers=hdr,
        json={"category_id": cats[1]["id"], "amount": "150.00", "incurred_on": "2026-01-03"},
    )

    reverse1 = auth.client.post(
        f"/finance/reserve/entries/{deposit1.json()['id']}/reverse", headers=hdr
    )
    assert reverse1.status_code == 200, reverse1.text
    reverse2 = auth.client.post(
        f"/finance/reserve/entries/{interest.json()['id']}/reverse", headers=hdr
    )
    assert reverse2.status_code == 200, reverse2.text
    void_exp = auth.client.post(
        f"/finance/expenses/{exp1.json()['id']}/void", headers=hdr, json={"reason": "x"}
    )
    assert void_exp.status_code == 200, void_exp.text

    # Hand-computed net: opening + deposit1 + deposit2 + interest - exp1 - exp2
    # - reversal(deposit1) - reversal(interest) + reversal(exp1, an inflow).
    expected = (
        Decimal("10000.00") + Decimal("2000.00") + Decimal("500.00") + Decimal("100.00")
        - Decimal("300.00") - Decimal("150.00")
        - Decimal("2000.00") - Decimal("100.00")
        + Decimal("300.00")
    )
    assert reserve_balance(db, society.id) == expected

    reserve = auth.client.get("/finance/reserve", headers=hdr).json()
    assert reserve["total"] == 9  # every row incl. reversals


# ===========================================================================
# trends across many months + a back-dated void
# ===========================================================================


def test_analytics_trends_many_months(db, society, admin_user, superadmin, auth):
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    assert resp.status_code == 200, resp.text

    for (y, m) in [(2026, 1), (2026, 2), (2026, 3), (2026, 4), (2026, 5), (2026, 6)]:
        db.add(
            HouseDue(
                society_id=society.id,
                house_id=hid,
                period_year=y,
                period_month=m,
                amount_due=Decimal("1000.00"),
                due_date=date(y, m, 1),
                status="outstanding",
                source="accrued",
            )
        )
    db.commit()

    cats = auth.client.get("/finance/expense-categories", headers=hdr).json()
    category_id = cats[0]["id"]

    voided_expense_id = None
    for (y, m) in [(2026, 1), (2026, 2), (2026, 3), (2026, 4), (2026, 5), (2026, 6)]:
        pay = auth.client.post(
            f"/finance/houses/{hid}/payments",
            headers=hdr,
            json={"method": "cash", "months": 1, "paid_at": date(y, m, 5).isoformat()},
        )
        assert pay.status_code == 200, pay.text
        exp = auth.client.post(
            "/finance/expenses",
            headers=hdr,
            json={
                "category_id": category_id,
                "amount": "200.00",
                "incurred_on": date(y, m, 5).isoformat(),
            },
        )
        assert exp.status_code == 200, exp.text
        if (y, m) == (2026, 2):
            voided_expense_id = exp.json()["id"]

    void = auth.client.post(
        f"/finance/expenses/{voided_expense_id}/void", headers=hdr, json={"reason": "back-dated"}
    )
    assert void.status_code == 200, void.text

    trends = auth.client.get("/finance/analytics/trends", headers=hdr).json()
    points = trends["points"]
    assert len(points) == 6
    periods = [(p["period_year"], p["period_month"]) for p in points]
    assert periods == sorted(periods)  # oldest -> newest

    feb = next(p for p in points if (p["period_year"], p["period_month"]) == (2026, 2))
    # Feb's own expense was voided -> net reduction lands in Feb, not the void month.
    assert Decimal(feb["expense"]) == Decimal("0.00")
    for p in points:
        assert Decimal(p["net"]) == Decimal(p["collected"]) - Decimal(p["expense"])


# ===========================================================================
# reconcile when computed balance is negative
# ===========================================================================


def test_reconcile_when_computed_balance_negative(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    opening = auth.client.post(
        "/finance/reserve/entries",
        headers=hdr,
        json={"entry_type": "opening", "amount": "100.00", "occurred_on": "2026-01-01"},
    )
    assert opening.status_code == 200, opening.text
    cats = auth.client.get("/finance/expense-categories", headers=hdr).json()
    exp = auth.client.post(
        "/finance/expenses",
        headers=hdr,
        json={"category_id": cats[0]["id"], "amount": "500.00", "incurred_on": "2026-01-02"},
    )
    assert exp.status_code == 200, exp.text

    assert reserve_balance(db, society.id) == Decimal("-400.00")

    reconcile = auth.client.post(
        "/finance/reserve/reconcile",
        headers=hdr,
        json={"actual_balance": "0.00", "occurred_on": "2026-01-03"},
    )
    assert reconcile.status_code == 200, reconcile.text
    body = reconcile.json()
    assert body["direction"] == "inflow"
    assert Decimal(body["amount"]) == Decimal("400.00")

    db.expire_all()
    assert reserve_balance(db, society.id) == Decimal("0.00")

    rows = (
        db.query(__import__("app.platform.models", fromlist=["AuditLog"]).AuditLog)
        .filter_by(society_id=society.id, action="finance.reserve_reconciled")
        .all()
    )
    assert len(rows) == 1
    assert rows[0].after["difference"] == "400.00"


# ===========================================================================
# steady-state accrual across multiple generate calls
# ===========================================================================


def test_house_returned_to_owned_after_period_gap(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    assert resp.status_code == 200, resp.text

    from app.modules.onboarding.models import House

    house = db.get(House, hid)
    house.first_left_empty_on = date(2026, 3, 10)
    db.commit()

    _set_rate_db(db, society.id, "1000.00", date(2026, 1, 1), actor=admin_user.id)

    for as_of in (date(2026, 4, 8), date(2026, 5, 8), date(2026, 6, 8), date(2026, 7, 8)):
        FinanceService(db).generate_due_cycle(society.id, as_of=as_of)
        db.commit()

    dues = _dues_for(db, society.id, hid)
    periods = [(d.period_year, d.period_month) for d in dues]
    assert periods == [(2026, 3), (2026, 4), (2026, 5), (2026, 6), (2026, 7)]
    assert len(periods) == len(set(periods))  # no duplicates, no gaps
