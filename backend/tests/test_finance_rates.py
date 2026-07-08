"""Tests for the Finance RATES write + reads (docs/modules/finance.md §4/§6).

Covers ``set_rate`` (Wave A): a happy set + read-back via ``GET /finance/rate``,
duplicate ``valid_from`` → 409, non-month-aligned ``valid_from`` → 422, rate
history ordering (newest-first), and the preview endpoint math
(proposed × dues-owing houses). Finance depends_on houses (which depends_on
onboarding); enabling the module seeds ``society_admin`` all finance perms.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select

from app.modules.finance.models import MaintenanceRate
from app.modules.finance.schemas import RateSetRequest
from app.platform.models import AuditLog
from app.platform.societies.schemas import ModuleAllocation
from app.platform.societies.service import SocietyService

from tests._houses_helpers import (
    _admin_bearer,
    _make_building_with_houses,
    _owner,
    _set_status,
    _tenant,
)

# Reuse the house harness for the onboarding→houses mapping the preview needs.


def _enable_finance(db, society, superadmin) -> None:
    """Enable onboarding + houses + finance (finance depends_on houses).

    ``set_modules`` seeds each module's ``default_role_permissions`` — so this
    grants ``society_admin`` all five ``finance.*`` perms (incl. manage_rate +
    read) automatically (docs §2, spec.py ``default_role_permissions``).
    """
    SocietyService(db).set_modules(
        society.id,
        [
            ModuleAllocation(module_key="onboarding", enabled=True, config={}),
            ModuleAllocation(module_key="houses", enabled=True, config={}),
            ModuleAllocation(module_key="finance", enabled=True, config={}),
        ],
        actor_user_id=superadmin.id,
    )
    db.commit()


def _setup(db, society, admin_user, superadmin, auth) -> dict[str, str]:
    """Enable finance (+ deps) and return an activated admin bearer header."""
    _enable_finance(db, society, superadmin)
    return _admin_bearer(auth, admin_user)


def _set_rate(auth, hdr, amount, valid_from):
    return auth.client.post(
        "/finance/rate",
        headers=hdr,
        json={"amount": str(amount), "valid_from": valid_from},
    )


# ===========================================================================
# happy set + read-back
# ===========================================================================

def test_set_rate_happy_and_read_back(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = _set_rate(auth, hdr, "1500.00", "2026-07-01")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert Decimal(body["amount"]) == Decimal("1500.00")
    assert body["valid_from"] == "2026-07-01"
    assert body["id"] > 0

    # A new row was inserted (never an edit of history).
    db.expire_all()
    rows = db.execute(
        select(MaintenanceRate).where(MaintenanceRate.society_id == society.id)
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].amount == Decimal("1500.00")
    assert rows[0].created_by == admin_user.id

    # Read-back via GET /finance/rate: current + history.
    read = auth.client.get("/finance/rate", headers=hdr)
    assert read.status_code == 200, read.text
    rb = read.json()
    assert Decimal(rb["current"]["amount"]) == Decimal("1500.00")
    assert rb["current"]["valid_from"] == "2026-07-01"
    assert len(rb["history"]) == 1


def test_set_rate_audits_rate_set(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = _set_rate(auth, hdr, "1200.00", "2026-06-01")
    assert resp.status_code == 200, resp.text
    rate_id = resp.json()["id"]

    db.expire_all()
    audits = db.query(AuditLog).filter(
        AuditLog.action == "finance.rate_set",
        AuditLog.society_id == society.id,
        AuditLog.entity_id == rate_id,
    ).all()
    assert len(audits) == 1
    # Money serialized as a string for JSON-safety (docs §5).
    assert audits[0].after == {"amount": "1200.00", "valid_from": "2026-06-01"}
    assert audits[0].actor_user_id == admin_user.id


# ===========================================================================
# duplicate valid_from → 409
# ===========================================================================

def test_set_rate_duplicate_valid_from_conflicts(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    first = _set_rate(auth, hdr, "1000.00", "2026-05-01")
    assert first.status_code == 200, first.text

    dup = _set_rate(auth, hdr, "1800.00", "2026-05-01")
    assert dup.status_code == 409, dup.text
    assert dup.json()["code"] == "conflict"

    # No second row was written — history is untouched.
    db.expire_all()
    rows = db.execute(
        select(MaintenanceRate).where(MaintenanceRate.society_id == society.id)
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].amount == Decimal("1000.00")


# ===========================================================================
# non-month-aligned valid_from → 422
# ===========================================================================

def test_set_rate_non_month_aligned_rejected(db):
    """``valid_from`` must be the first of a month (docs §4) — schema-enforced.

    Asserted at the ``RateSetRequest`` schema (the source of this validation,
    which the router binds before the service runs). NOTE: driving this via the
    HTTP endpoint would surface a pre-existing foundation defect — the app's
    ``RequestValidationError`` handler feeds ``exc.errors()`` (whose ``ctx``
    holds the raw ``ValueError``/``date``) straight to ``json.dumps`` and 500s
    for ANY custom ``field_validator`` (all finance money/method validators
    included), so month-alignment is verified here rather than over HTTP.
    """
    with pytest.raises(PydanticValidationError) as exc:
        RateSetRequest(amount="1500.00", valid_from=date(2026, 7, 15))
    assert "first day of a month" in str(exc.value)
    # A month-aligned value is accepted unchanged.
    ok = RateSetRequest(amount="1500.00", valid_from=date(2026, 7, 1))
    assert ok.valid_from == date(2026, 7, 1)


# ===========================================================================
# rate history ordering (newest valid_from first)
# ===========================================================================

def test_rate_history_ordering_newest_first(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    # Insert out of chronological order to prove ordering is by valid_from.
    assert _set_rate(auth, hdr, "1000.00", "2026-01-01").status_code == 200
    assert _set_rate(auth, hdr, "1400.00", "2026-09-01").status_code == 200
    assert _set_rate(auth, hdr, "1200.00", "2026-05-01").status_code == 200

    read = auth.client.get("/finance/rate", headers=hdr)
    assert read.status_code == 200, read.text
    body = read.json()
    valid_froms = [r["valid_from"] for r in body["history"]]
    assert valid_froms == ["2026-09-01", "2026-05-01", "2026-01-01"]
    # Current = the latest-effective rate (highest valid_from).
    assert body["current"]["valid_from"] == "2026-09-01"
    assert Decimal(body["current"]["amount"]) == Decimal("1400.00")


# ===========================================================================
# preview math: proposed × dues-owing houses vs current
# ===========================================================================

def test_rate_preview_math(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)

    # Two houses; make both non-empty (dues-owing); a third stays empty.
    houses = _make_building_with_houses(
        auth, hdr, floors=[{"level": 1, "houses_count": 3}]
    )
    _set_status(auth, hdr, houses[0]["id"], "owned", _owner(persons_living=2))
    _set_status(
        auth, hdr, houses[1]["id"], "rented",
        _owner(email="o2@x.com"), _tenant(),
    )
    # houses[2] left empty → never owes.

    # A current rate so the projection reports current vs proposed + delta.
    assert _set_rate(auth, hdr, "1000.00", "2026-07-01").status_code == 200

    resp = auth.client.get(
        "/finance/rate/preview", headers=hdr, params={"amount": "1500"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dues_owing_houses"] == 2
    assert Decimal(body["proposed_amount"]) == Decimal("1500")
    assert Decimal(body["projected_monthly_collection"]) == Decimal("3000")
    assert Decimal(body["current_amount"]) == Decimal("1000.00")
    assert Decimal(body["current_monthly_collection"]) == Decimal("2000.00")
    assert Decimal(body["delta"]) == Decimal("1000.00")


def test_rate_preview_no_current_rate(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(
        auth, hdr, floors=[{"level": 1, "houses_count": 1}]
    )
    _set_status(auth, hdr, houses[0]["id"], "owned", _owner(persons_living=1))

    resp = auth.client.get(
        "/finance/rate/preview", headers=hdr, params={"amount": "1200"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dues_owing_houses"] == 1
    assert Decimal(body["projected_monthly_collection"]) == Decimal("1200")
    # No rate set yet → nothing to compare against.
    assert body["current_amount"] is None
    assert body["current_monthly_collection"] is None
    assert body["delta"] is None
