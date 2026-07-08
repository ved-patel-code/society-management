"""Module-enable / config / permissions-seeding tests (test-gate matrix §3).

Verifies the enable -> seed -> login chain, ``depends_on``, config-override
behavior, and the module-disabled 403 on every route — none of which the wave
files (which assume finance is already enabled with perms in place) cover.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.common.errors import DependencyError
from app.platform.models import AuditLog
from app.platform.societies.schemas import ModuleAllocation
from app.platform.societies.service import SocietyService

from tests._finance_helpers import (
    enable_finance,
    finance_admin_bearer,
    owned_house,
    resident_bearer,
    set_rate_http,
    setup_finance,
)
from tests._houses_helpers import _admin_bearer


def test_enable_finance_requires_houses_dependency(db, society, superadmin):
    with pytest.raises(DependencyError):
        SocietyService(db).set_modules(
            society.id,
            [ModuleAllocation(module_key="finance", enabled=True, config={})],
            actor_user_id=superadmin.id,
        )
    db.rollback()

    # Enabling houses (+onboarding) + finance together succeeds.
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


def test_enable_seeds_admin_all_five_perms_via_login(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_finance(db, society, admin_user, superadmin, auth)

    rate_resp = set_rate_http(auth, hdr, "1000.00", date(2026, 1, 1))
    assert rate_resp.status_code == 200, rate_resp.text

    hid = owned_house(auth, hdr)
    gen = auth.client.post("/finance/dues/generate", headers=hdr)
    assert gen.status_code == 200, gen.text

    pay = auth.client.post(
        f"/finance/houses/{hid}/payments", headers=hdr, json={"method": "cash", "pay_all": True}
    )
    assert pay.status_code == 200, pay.text

    cats = auth.client.get("/finance/expense-categories", headers=hdr).json()
    exp = auth.client.post(
        "/finance/expenses",
        headers=hdr,
        json={"category_id": cats[0]["id"], "amount": "100.00", "incurred_on": "2026-07-01"},
    )
    assert exp.status_code == 200, exp.text

    entry = auth.client.post(
        "/finance/reserve/entries",
        headers=hdr,
        json={"entry_type": "deposit", "amount": "500.00", "occurred_on": "2026-07-01"},
    )
    assert entry.status_code == 200, entry.text

    analytics = auth.client.get("/finance/analytics/collection", headers=hdr)
    assert analytics.status_code == 200, analytics.text


def test_enable_seeds_resident_read_only_via_login(
    db, society, admin_user, resident_user, superadmin, auth
):
    enable_finance(db, society, superadmin)
    rhdr = resident_bearer(auth, resident_user)

    reserve = auth.client.get("/finance/reserve", headers=rhdr)
    assert reserve.status_code == 200, reserve.text

    rate_resp = set_rate_http(auth, rhdr, "1000.00", date(2026, 1, 1))
    assert rate_resp.status_code == 403, rate_resp.text

    pay = auth.client.post(
        "/finance/houses/1/payments", headers=rhdr, json={"method": "cash", "pay_all": True}
    )
    assert pay.status_code == 403, pay.text


_FINANCE_ROUTES = [
    ("GET", "/finance/rate", None),
    ("POST", "/finance/rate", {"amount": "1000.00", "valid_from": "2026-01-01"}),
    ("GET", "/finance/houses/1/dues", None),
    ("POST", "/finance/houses/1/payments", {"method": "cash", "pay_all": True}),
    ("POST", "/finance/houses/1/prepaid", {"months_count": 3, "method": "cash"}),
    ("POST", "/finance/payments/1/void", {"reason": "x"}),
    ("GET", "/finance/expense-categories", None),
    ("POST", "/finance/expense-categories", {"name": "Test Cat"}),
    ("GET", "/finance/expenses", None),
    ("POST", "/finance/expenses", {"category_id": 1, "amount": "1.00", "incurred_on": "2026-01-01"}),
    ("POST", "/finance/expenses/1/void", {"reason": "x"}),
    ("GET", "/finance/reserve", None),
    (
        "POST",
        "/finance/reserve/entries",
        {"entry_type": "deposit", "amount": "1.00", "occurred_on": "2026-01-01"},
    ),
    ("POST", "/finance/reserve/entries/1/reverse", None),
    ("POST", "/finance/reserve/reconcile", {"actual_balance": "1.00", "occurred_on": "2026-01-01"}),
    ("GET", "/finance/analytics/collection", None),
    ("GET", "/finance/analytics/arrears", None),
    ("GET", "/finance/analytics/expenses", None),
    ("GET", "/finance/analytics/income", None),
    ("GET", "/finance/analytics/trends", None),
    ("POST", "/finance/dues/generate", None),
]


@pytest.mark.parametrize("method,path,body", _FINANCE_ROUTES)
def test_module_disabled_403_on_every_finance_route(
    db, society, admin_user, superadmin, auth, method, path, body
):
    """Onboarding+houses enabled, finance NOT enabled — every route 403s
    module_disabled BEFORE any permission check fires."""
    SocietyService(db).set_modules(
        society.id,
        [
            ModuleAllocation(module_key="onboarding", enabled=True, config={}),
            ModuleAllocation(module_key="houses", enabled=True, config={}),
        ],
        actor_user_id=superadmin.id,
    )
    db.commit()
    hdr = _admin_bearer(auth, admin_user)

    resp = auth.client.request(method, path, headers=hdr, json=body)
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "module_disabled"


def test_disable_finance_revokes_route_access(
    db, society, admin_user, superadmin, auth
):
    setup_finance(db, society, admin_user, superadmin, auth)

    SocietyService(db).set_modules(
        society.id,
        [ModuleAllocation(module_key="finance", enabled=False, config={})],
        actor_user_id=superadmin.id,
    )
    db.commit()

    # Re-login (a fresh token still carries the same role_ids; the disabled
    # module gate reads current society_modules, so 403 fires live).
    hdr = _re_login(auth, admin_user)
    resp = auth.client.get("/finance/reserve", headers=hdr)
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "module_disabled"


def _re_login(auth, user):
    from tests._houses_helpers import NEWPASS

    sess = auth.login_ok(user.email, NEWPASS)
    return auth.bearer(sess["access_token"])


def test_config_custom_due_day_changes_generation(
    db, society, admin_user, superadmin, auth, monkeypatch
):
    from tests._finance_helpers import freeze_utcnow

    freeze_utcnow(monkeypatch)
    hdr = setup_finance(
        db, society, admin_user, superadmin, auth, config={"maintenance_due_day": 15}
    )
    hid = owned_house(auth, hdr)
    set_rate_http(auth, hdr, "1000.00", date(2026, 1, 1))

    gen = auth.client.post("/finance/dues/generate", headers=hdr)
    assert gen.status_code == 200, gen.text
    assert gen.json()["created"] == 1

    dues = auth.client.get(f"/finance/houses/{hid}/dues", headers=hdr).json()
    assert dues["outstanding"][0]["due_date"].endswith("-15")


def test_config_custom_prepaid_blocks_changes_acceptance(
    db, society, admin_user, superadmin, auth, monkeypatch
):
    from tests._finance_helpers import freeze_utcnow

    freeze_utcnow(monkeypatch)
    hdr = setup_finance(
        db, society, admin_user, superadmin, auth, config={"prepaid_blocks": [6]}
    )
    hid = owned_house(auth, hdr)
    set_rate_http(auth, hdr, "1000.00", date(2026, 1, 1))
    gen = auth.client.post("/finance/dues/generate", headers=hdr)
    assert gen.status_code == 200, gen.text
    pay = auth.client.post(
        f"/finance/houses/{hid}/payments", headers=hdr, json={"method": "cash", "pay_all": True}
    )
    assert pay.status_code == 200, pay.text

    bad = auth.client.post(
        f"/finance/houses/{hid}/prepaid",
        headers=hdr,
        json={"months_count": 3, "method": "cash"},
    )
    assert bad.status_code == 422, bad.text

    ok = auth.client.post(
        f"/finance/houses/{hid}/prepaid",
        headers=hdr,
        json={"months_count": 6, "method": "cash"},
    )
    assert ok.status_code == 200, ok.text


def test_reenable_is_idempotent_no_duplicate_grants(
    db, society, admin_user, superadmin, auth
):
    from app.platform.models import RolePermission, Role

    enable_finance(db, society, superadmin)
    admin_role = db.query(Role).filter(
        Role.society_id == society.id, Role.key == "society_admin"
    ).one()
    first_count = (
        db.query(RolePermission).filter(RolePermission.role_id == admin_role.id).count()
    )

    # Re-enable with the same config — a no-op for permissions.
    enable_finance(db, society, superadmin)
    second_count = (
        db.query(RolePermission).filter(RolePermission.role_id == admin_role.id).count()
    )
    assert second_count == first_count

    module_toggled = (
        db.query(AuditLog)
        .filter(AuditLog.society_id == society.id, AuditLog.action == "module.toggled")
        .count()
    )
    # module.allocated fires once per genuinely new allocation, not per re-enable
    # call — a duplicate identical enable must not spam toggled/allocated rows.
    allocated = (
        db.query(AuditLog)
        .filter(AuditLog.society_id == society.id, AuditLog.action == "module.allocated")
        .count()
    )
    assert allocated == 4  # onboarding, houses, vault, finance — once each
    assert module_toggled == 0
