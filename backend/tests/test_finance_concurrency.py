"""Concurrency / idempotency tests (test-gate matrix §6).

True parallel DB races are hard in this harness (truncate-per-test, mostly a
single connection); these assert the FOR-UPDATE path's sequential correctness +
idempotency at a broader scale than the wave tests, plus the worker's due-day
no-op guard. A genuine two-session lock race is skip-guarded (P3, matrix-noted).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

import pytest

from app.modules.finance.models import HouseDue, LedgerEntry, Payment
from app.modules.finance.service import FinanceService
from app.modules.finance.schemas import RateSetRequest
from app.modules.finance.services.jobs import _run_for_societies

from tests._finance_helpers import owned_house, setup_finance
from tests._houses_helpers import _make_building_with_houses, _owner, _set_status


def _set_rate_db(db, society_id, amount, valid_from, *, actor):
    FinanceService(db).rates.set_rate(
        society_id,
        RateSetRequest(amount=Decimal(str(amount)), valid_from=valid_from),
        actor_user_id=actor,
    )
    db.commit()


def test_generate_due_cycle_twice_no_duplicates(db, society, admin_user, superadmin, auth):
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(
        auth, hdr, floors=[{"level": 1, "houses_count": 5}]
    )
    for h in houses:
        resp = _set_status(
            auth, hdr, h["id"], "owned", _owner(email=f"o{h['id']}@x.com", persons_living=1)
        )
        assert resp.status_code == 200, resp.text

    from app.modules.onboarding.models import House

    # Backdate a couple houses so multiple months backfill (more rows at risk).
    house0 = db.get(House, houses[0]["id"])
    house0.first_left_empty_on = date(2026, 4, 1)
    db.commit()

    _set_rate_db(db, society.id, "1000.00", date(2026, 1, 1), actor=admin_user.id)

    first = FinanceService(db).generate_due_cycle(society.id, as_of=date(2026, 7, 8))
    db.commit()
    assert first > 0

    rows_before = list(
        db.execute(select(HouseDue).where(HouseDue.society_id == society.id)).scalars()
    )

    second = FinanceService(db).generate_due_cycle(society.id, as_of=date(2026, 7, 8))
    db.commit()
    assert second == 0

    rows_after = list(
        db.execute(select(HouseDue).where(HouseDue.society_id == society.id)).scalars()
    )
    assert len(rows_after) == len(rows_before)

    # UNIQUE(society, house, period) never violated: no duplicate (house, period).
    seen = set()
    for r in rows_after:
        key = (r.house_id, r.period_year, r.period_month)
        assert key not in seen
        seen.add(key)


def test_worker_creates_nothing_when_due_day_ne_today(
    db, society, admin_user, superadmin, auth
):
    from app.platform.societies.schemas import ModuleAllocation
    from app.platform.societies.service import SocietyService

    SocietyService(db).set_modules(
        society.id,
        [
            ModuleAllocation(module_key="onboarding", enabled=True, config={}),
            ModuleAllocation(module_key="houses", enabled=True, config={}),
            ModuleAllocation(
                module_key="finance", enabled=True, config={"maintenance_due_day": 15}
            ),
        ],
        actor_user_id=superadmin.id,
    )
    db.commit()

    result = _run_for_societies(db, [society.id], date(2026, 7, 10))
    db.commit()
    assert result == {"societies_processed": 0, "dues_created": 0}
    assert (
        list(db.execute(select(HouseDue).where(HouseDue.society_id == society.id)).scalars())
        == []
    )


def test_double_void_returns_409(db, society, admin_user, superadmin, auth):
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    hid = owned_house(auth, hdr)
    _set_rate_db(db, society.id, "1000.00", date(2026, 1, 1), actor=admin_user.id)
    gen = auth.client.post("/finance/dues/generate", headers=hdr)
    assert gen.status_code == 200, gen.text

    pay = auth.client.post(
        f"/finance/houses/{hid}/payments", headers=hdr, json={"method": "cash", "pay_all": True}
    )
    assert pay.status_code == 200, pay.text
    pid = pay.json()["id"]

    void1 = auth.client.post(f"/finance/payments/{pid}/void", headers=hdr, json={"reason": "a"})
    assert void1.status_code == 200, void1.text
    void2 = auth.client.post(f"/finance/payments/{pid}/void", headers=hdr, json={"reason": "b"})
    assert void2.status_code == 409, void2.text

    db.expire_all()
    reversals = list(
        db.execute(
            select(LedgerEntry).where(
                LedgerEntry.society_id == society.id, LedgerEntry.entry_type == "reversal"
            )
        ).scalars()
    )
    assert len(reversals) == 1

    due = db.execute(
        select(HouseDue).where(HouseDue.society_id == society.id, HouseDue.house_id == hid)
    ).scalars().all()
    assert all(d.status == "outstanding" for d in due)


def test_second_payment_cannot_resettle_same_month(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    assert resp.status_code == 200, resp.text

    for (y, m) in [(2026, 1), (2026, 2)]:
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

    pay1 = auth.client.post(
        f"/finance/houses/{hid}/payments", headers=hdr, json={"method": "cash", "months": 1}
    )
    assert pay1.status_code == 200, pay1.text
    assert pay1.json()["allocations"][0]["period_month"] == 1

    pay2 = auth.client.post(
        f"/finance/houses/{hid}/payments", headers=hdr, json={"method": "cash", "months": 1}
    )
    assert pay2.status_code == 200, pay2.text
    # The second payment settles M2, NOT M1 again.
    assert pay2.json()["allocations"][0]["period_month"] == 2

    db.expire_all()
    dues = list(
        db.execute(
            select(HouseDue)
            .where(HouseDue.society_id == society.id, HouseDue.house_id == hid)
            .order_by(HouseDue.period_month)
        ).scalars()
    )
    assert all(d.status == "paid" for d in dues)
    # M1 has exactly one allocation (never double-collected).
    from app.modules.finance.models import PaymentAllocation

    m1 = next(d for d in dues if d.period_month == 1)
    allocs_m1 = list(
        db.execute(
            select(PaymentAllocation).where(PaymentAllocation.house_due_id == m1.id)
        ).scalars()
    )
    assert len(allocs_m1) == 1

    pay3 = auth.client.post(
        f"/finance/houses/{hid}/payments", headers=hdr, json={"method": "cash", "months": 1}
    )
    assert pay3.status_code == 422, pay3.text


@pytest.mark.skip(
    reason=(
        "Two-session lock race: the harness's per-worker DB uses a single "
        "SessionLocal per test via the `db` fixture, and the app's own request "
        "session (via TestClient) truncates/reset per test — holding two live, "
        "independently-committing connections against the SAME row across threads "
        "in this harness would need a second raw SessionLocal driven from a "
        "background thread, racing against xdist's per-test truncate/reset "
        "lifecycle. That's a genuine infra investment beyond this test-gate pass; "
        "the sequential FOR UPDATE correctness is covered by "
        "test_second_payment_cannot_resettle_same_month instead (P3, matrix-noted)."
    )
)
def test_concurrent_settle_same_month_two_sessions():
    pass
