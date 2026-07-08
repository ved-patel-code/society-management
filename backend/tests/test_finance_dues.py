"""Dues generation tests for the Finance module (Module 4, Wave B).

Covers :meth:`DuesService.generate_due_cycle` end to end: only dues-owing
(non-empty) houses accrue; idempotent re-runs; backfill from
``first_left_empty_on`` across months; no-rate months skipped; the configured
due day; and the on-demand ``POST /finance/dues/generate`` endpoint plus the
public ``app.modules.finance.api.generate_due_cycle`` contract.

Reuses the House & Occupancy harness (``tests._houses_helpers``) to build houses
and move them off ``empty`` so they owe (which stamps ``first_left_empty_on``).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.modules.finance import api as finance_api
from app.modules.finance.models import HouseDue
from app.modules.finance.schemas import RateSetRequest
from app.modules.finance.service import FinanceService
from app.platform.societies.schemas import ModuleAllocation
from app.platform.societies.service import SocietyService

from tests._houses_helpers import (
    _admin_bearer,
    _make_building_with_houses,
    _owner,
    _set_status,
)


# --- setup: enable onboarding + houses + finance, activate admin ------------

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
    """Set an effective-dated rate via the service (Wave A), then commit."""
    FinanceService(db).rates.set_rate(
        society_id,
        RateSetRequest(amount=Decimal(str(amount)), valid_from=valid_from),
        actor_user_id=actor,
    )
    db.commit()


def _dues(db, society_id, house_id=None):
    stmt = select(HouseDue).where(HouseDue.society_id == society_id)
    if house_id is not None:
        stmt = stmt.where(HouseDue.house_id == house_id)
    return list(
        db.execute(stmt.order_by(HouseDue.period_year, HouseDue.period_month))
        .scalars()
        .all()
    )


# ===========================================================================
# happy path: only owing (non-empty) houses accrue; empty houses get none
# ===========================================================================

def test_generation_creates_dues_for_owing_houses_only(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)  # 2 houses, both empty
    owing_hid = houses[0]["id"]
    empty_hid = houses[1]["id"]

    _set_rate(db, society.id, "1500.00", date(2026, 1, 1), actor=admin_user.id)
    # Move one house off empty (stamps first_left_empty_on = today = 2026-07-08).
    _set_status(auth, hdr, owing_hid, "owned", _owner(persons_living=2))

    created = FinanceService(db).generate_due_cycle(society.id)
    db.commit()

    assert created == 1  # only the current period for the one owing house
    dues = _dues(db, society.id)
    assert len(dues) == 1
    assert dues[0].house_id == owing_hid
    assert dues[0].amount_due == Decimal("1500.00")
    assert dues[0].status == "outstanding"
    assert dues[0].source == "accrued"
    # The still-empty house never owes.
    assert _dues(db, society.id, empty_hid) == []


# ===========================================================================
# idempotent: a second run creates nothing
# ===========================================================================

def test_generation_is_idempotent(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_rate(db, society.id, "1000.00", date(2026, 1, 1), actor=admin_user.id)
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))

    first = FinanceService(db).generate_due_cycle(society.id)
    db.commit()
    assert first == 1

    second = FinanceService(db).generate_due_cycle(society.id)
    db.commit()
    assert second == 0
    assert len(_dues(db, society.id)) == 1


# ===========================================================================
# backfill from first_left_empty_on across multiple months
# ===========================================================================

def test_backfill_from_first_left_empty_on_across_months(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_rate(db, society.id, "800.00", date(2026, 1, 1), actor=admin_user.id)
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))

    # Backdate first_left_empty_on to April so 4 months (Apr..Jul) backfill.
    from app.modules.onboarding.models import House

    house = db.get(House, hid)
    house.first_left_empty_on = date(2026, 4, 15)
    db.commit()

    # as_of pinned so the test is date-stable: current period = 2026-07.
    created = FinanceService(db).generate_due_cycle(
        society.id, as_of=date(2026, 7, 8)
    )
    db.commit()

    assert created == 4
    periods = [(d.period_year, d.period_month) for d in _dues(db, society.id, hid)]
    assert periods == [(2026, 4), (2026, 5), (2026, 6), (2026, 7)]


# ===========================================================================
# a month with no effective rate is skipped (no crash)
# ===========================================================================

def test_month_without_rate_is_skipped(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    # Rate only becomes effective from June 2026.
    _set_rate(db, society.id, "900.00", date(2026, 6, 1), actor=admin_user.id)
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))

    from app.modules.onboarding.models import House

    house = db.get(House, hid)
    house.first_left_empty_on = date(2026, 4, 10)  # Apr, May have no rate
    db.commit()

    created = FinanceService(db).generate_due_cycle(
        society.id, as_of=date(2026, 7, 8)
    )
    db.commit()

    # Apr + May skipped (no rate); Jun + Jul billed at 900.
    assert created == 2
    periods = [(d.period_year, d.period_month) for d in _dues(db, society.id, hid)]
    assert periods == [(2026, 6), (2026, 7)]
    assert all(d.amount_due == Decimal("900.00") for d in _dues(db, society.id, hid))


# ===========================================================================
# due_date honors the configured maintenance_due_day
# ===========================================================================

def test_due_date_matches_configured_due_day(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(
        db, society, admin_user, superadmin, auth,
        config={"maintenance_due_day": 15},
    )
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_rate(db, society.id, "1200.00", date(2026, 1, 1), actor=admin_user.id)
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))

    FinanceService(db).generate_due_cycle(society.id, as_of=date(2026, 7, 8))
    db.commit()

    dues = _dues(db, society.id, hid)
    assert len(dues) == 1
    assert dues[0].due_date == date(2026, 7, 15)


def test_no_owing_houses_returns_zero(
    db, society, admin_user, superadmin, auth
):
    _setup(db, society, admin_user, superadmin, auth)
    _set_rate(db, society.id, "1000.00", date(2026, 1, 1), actor=admin_user.id)
    # No houses created at all → nothing owes.
    created = FinanceService(db).generate_due_cycle(society.id)
    db.commit()
    assert created == 0
    assert _dues(db, society.id) == []


def test_no_rate_returns_zero_without_crash(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    # An owing house but NO rate anywhere.
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))

    created = FinanceService(db).generate_due_cycle(society.id)
    db.commit()
    assert created == 0
    assert _dues(db, society.id) == []


# ===========================================================================
# public api contract: app.modules.finance.api.generate_due_cycle
# ===========================================================================

def test_public_api_generate_due_cycle(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_rate(db, society.id, "1100.00", date(2026, 1, 1), actor=admin_user.id)
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))

    created = finance_api.generate_due_cycle(
        db, society.id, actor_user_id=admin_user.id
    )
    db.commit()
    assert created == 1
    assert len(_dues(db, society.id)) == 1


# ===========================================================================
# on-demand HTTP endpoint: POST /finance/dues/generate → {"created": N}
# ===========================================================================

def test_on_demand_endpoint_returns_created_count(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_rate(db, society.id, "1300.00", date(2026, 1, 1), actor=admin_user.id)
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))

    resp = auth.client.post("/finance/dues/generate", headers=hdr)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"created": 1}

    # Idempotent over HTTP too.
    resp2 = auth.client.post("/finance/dues/generate", headers=hdr)
    assert resp2.status_code == 200, resp2.text
    assert resp2.json() == {"created": 0}
