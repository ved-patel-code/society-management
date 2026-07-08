"""Cross-module CONTRACT surface tests (test-gate matrix §2).

Calls the ``app.modules.finance.api`` delegators DIRECTLY on a ``db`` session, the
way Notifications/Onboarding/worker/gateway will — never over HTTP. The wave dues
test touches ``generate_due_cycle``/one ``outstanding_dues`` path; this exercises
the rest of the contract surface: outstanding totals, ``has_dues``,
``maintenance_due_day``, the caller's-transaction join, ``as_of``/actor plumbing,
the overdue signal, and the config validation the contract depends on.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.modules.finance import api as finance_api
from app.modules.finance.models import HouseDue
from app.modules.finance.schemas import FinanceConfig, PaymentRecordRequest
from app.modules.finance.services.support import load_config

from tests._finance_helpers import (
    freeze_utcnow,
    owned_house,
    set_rate_http,
    setup_finance,
)
from tests._houses_helpers import _make_building_with_houses, _owner, _set_status


def test_api_outstanding_dues_and_total_match_reads(
    db, society, admin_user, superadmin, auth, monkeypatch
):
    freeze_utcnow(monkeypatch)
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    hid = owned_house(auth, hdr)
    set_rate_http(auth, hdr, "1000.00", date(2026, 1, 1))
    auth.client.post("/finance/dues/generate", headers=hdr)

    db.expire_all()
    dues_out = finance_api.outstanding_dues(db, society.id, hid)
    total = finance_api.outstanding_total(db, society.id, hid)

    assert dues_out.outstanding_total == total
    manual_sum = sum((d.amount_due for d in dues_out.outstanding), Decimal("0"))
    assert dues_out.outstanding_total == manual_sum


def test_api_has_dues_backs_onboarding_delete_guard(
    db, society, admin_user, superadmin, auth, monkeypatch
):
    freeze_utcnow(monkeypatch)
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    hid = owned_house(auth, hdr)
    set_rate_http(auth, hdr, "1000.00", date(2026, 1, 1))
    auth.client.post("/finance/dues/generate", headers=hdr)

    db.expire_all()
    assert finance_api.has_dues(db, society.id, hid) is True

    pay = auth.client.post(
        f"/finance/houses/{hid}/payments", headers=hdr, json={"method": "cash", "pay_all": True}
    )
    assert pay.status_code == 200, pay.text
    db.expire_all()
    assert finance_api.has_dues(db, society.id, hid) is False

    # A prepaid-only house (no arrears) also reads False.
    houses = _make_building_with_houses(auth, hdr, names=["P"])
    hid2 = houses[0]["id"]
    resp = _set_status(auth, hdr, hid2, "owned", _owner(email="p@x.com", persons_living=1))
    assert resp.status_code == 200, resp.text
    gen = auth.client.post("/finance/dues/generate", headers=hdr)
    assert gen.status_code == 200, gen.text
    pay2 = auth.client.post(
        f"/finance/houses/{hid2}/payments", headers=hdr, json={"method": "cash", "pay_all": True}
    )
    assert pay2.status_code == 200, pay2.text
    prepaid = auth.client.post(
        f"/finance/houses/{hid2}/prepaid",
        headers=hdr,
        json={"months_count": 3, "method": "cash"},
    )
    assert prepaid.status_code == 200, prepaid.text
    db.expire_all()
    assert finance_api.has_dues(db, society.id, hid2) is False


def test_api_maintenance_due_day_reads_config(db, society, admin_user, superadmin, auth):
    setup_finance(db, society, admin_user, superadmin, auth)
    assert finance_api.maintenance_due_day(db, society.id) == 1

    # Re-enable with an override — a second set_modules call updates config.
    from app.platform.societies.schemas import ModuleAllocation
    from app.platform.societies.service import SocietyService

    SocietyService(db).set_modules(
        society.id,
        [ModuleAllocation(module_key="finance", enabled=True, config={"maintenance_due_day": 15})],
        actor_user_id=superadmin.id,
    )
    db.commit()
    assert finance_api.maintenance_due_day(db, society.id) == 15


def test_api_record_payment_joins_caller_txn(
    db, society, admin_user, superadmin, auth, monkeypatch
):
    freeze_utcnow(monkeypatch)
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    hid = owned_house(auth, hdr)
    set_rate_http(auth, hdr, "1000.00", date(2026, 1, 1))
    auth.client.post("/finance/dues/generate", headers=hdr)

    db.expire_all()
    req = PaymentRecordRequest(method="cash", months=1)
    finance_api.record_payment(db, society.id, hid, req, actor_user_id=admin_user.id)
    # Same session, NOT committed yet: the due is paid within this txn.
    due = (
        db.query(HouseDue)
        .filter(HouseDue.society_id == society.id, HouseDue.house_id == hid)
        .one()
    )
    assert due.status == "paid"

    db.rollback()

    from app.core.db import SessionLocal

    fresh = SessionLocal()
    try:
        due_fresh = (
            fresh.query(HouseDue)
            .filter(HouseDue.society_id == society.id, HouseDue.house_id == hid)
            .one()
        )
        assert due_fresh.status == "outstanding"
    finally:
        fresh.close()


def test_api_generate_due_cycle_as_of_and_actor(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    hid = owned_house(auth, hdr)
    set_rate_http(auth, hdr, "1000.00", date(2026, 1, 1))

    as_of = date(2026, 7, 8)
    first = finance_api.generate_due_cycle(
        db, society.id, as_of=as_of, actor_user_id=None
    )
    db.commit()
    assert first == 1

    second = finance_api.generate_due_cycle(
        db, society.id, as_of=as_of, actor_user_id=None
    )
    db.commit()
    assert second == 0


def test_overdue_signal_standalone(db, society, admin_user, superadmin, auth, monkeypatch):
    freeze_utcnow(monkeypatch)
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    hid = owned_house(auth, hdr)
    set_rate_http(auth, hdr, "1000.00", date(2026, 1, 1))
    auth.client.post("/finance/dues/generate", headers=hdr)  # current month, past due_date=day 1

    db.expire_all()
    dues_out = finance_api.outstanding_dues(db, society.id, hid)
    assert len(dues_out.outstanding) == 1
    assert dues_out.outstanding[0].is_overdue is True

    # A future due date is not overdue.
    from app.modules.finance.models import HouseDue as HD

    future = HD(
        society_id=society.id,
        house_id=hid,
        period_year=2030,
        period_month=1,
        amount_due=Decimal("1000.00"),
        due_date=date(2030, 1, 1),
        status="outstanding",
        source="accrued",
    )
    db.add(future)
    db.commit()
    db.expire_all()
    dues_out2 = finance_api.outstanding_dues(db, society.id, hid)
    future_row = next(d for d in dues_out2.outstanding if d.period_year == 2030)
    assert future_row.is_overdue is False


def test_config_maintenance_due_day_range_validation():
    with pytest.raises(PydanticValidationError):
        FinanceConfig(maintenance_due_day=0)
    with pytest.raises(PydanticValidationError):
        FinanceConfig(maintenance_due_day=29)
    cfg = FinanceConfig(maintenance_due_day=15)
    assert cfg.maintenance_due_day == 15


def test_config_prepaid_blocks_validation():
    with pytest.raises(PydanticValidationError):
        FinanceConfig(prepaid_blocks=[])
    with pytest.raises(PydanticValidationError):
        FinanceConfig(prepaid_blocks=[0, 3])
    cfg = FinanceConfig(prepaid_blocks=[6])
    assert cfg.prepaid_blocks == [6]
