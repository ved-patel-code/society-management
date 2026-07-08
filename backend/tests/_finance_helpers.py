"""Shared test harness for the Finance Phase-3 test-gate suite (docs/build-log/
finance/test-gate-matrix.md).

Consolidates the idioms the 7 wave files each re-declare (``_enable_finance``,
``_setup``, ``_set_rate``, second-society bootstrap) into ONE reusable module for
the cross-cutting/E2E specs, which walk onboarding→houses→finance(+vault) as a
real journey rather than hand-inserting rows.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal

from app.platform.models import AuditLog
from app.platform.societies.schemas import ModuleAllocation, SocietyCreate
from app.platform.societies.service import SocietyService
from app.platform.users.provisioning import UserProvisioningService

from tests._houses_helpers import (
    _admin_bearer as _houses_admin_bearer,
    _make_building_with_houses,
    _owner,
    _set_status,
    _tenant,
)
from tests.conftest import DEFAULT_MEMBER_PASSWORD

NEWPASS = "NewPass123"

# A stable frozen "today" for date-deterministic specs (§4/§6). Arbitrary but
# fixed; tests that need a different anchor pass their own ``as_of`` instead.
FROZEN_TODAY = date(2026, 7, 8)


# ===========================================================================
# module enable + bearer helpers
# ===========================================================================


def enable_finance(db, society, superadmin, *, config=None) -> None:
    """Enable onboarding + houses + vault + finance in one ``set_modules`` call.

    Finance ``depends_on: houses``; vault is included so E2E specs can exercise
    vault+finance coexisting on the same society (§1). Commits.
    """
    SocietyService(db).set_modules(
        society.id,
        [
            ModuleAllocation(module_key="onboarding", enabled=True, config={}),
            ModuleAllocation(module_key="houses", enabled=True, config={}),
            ModuleAllocation(module_key="vault", enabled=True, config={}),
            ModuleAllocation(
                module_key="finance", enabled=True, config=config or {}
            ),
        ],
        actor_user_id=superadmin.id,
    )
    db.commit()


def finance_admin_bearer(auth, admin_user) -> dict[str, str]:
    """must_change -> change-password -> re-login. Returns a usable bearer header."""
    return _houses_admin_bearer(auth, admin_user)


def resident_bearer(auth, resident_user) -> dict[str, str]:
    """Same must-change dance for a resident login."""
    tokens = auth.login_ok(resident_user.email, DEFAULT_MEMBER_PASSWORD)
    resp = auth.client.post(
        "/auth/change-password",
        headers=auth.bearer(tokens["access_token"]),
        json={"current_password": DEFAULT_MEMBER_PASSWORD, "new_password": NEWPASS},
    )
    assert resp.status_code == 200, resp.text
    sess = auth.login_ok(resident_user.email, NEWPASS)
    return auth.bearer(sess["access_token"])


def setup_finance(db, society, admin_user, superadmin, auth, *, config=None):
    """Enable finance (+deps+vault) and return an activated admin bearer header."""
    enable_finance(db, society, superadmin, config=config)
    return finance_admin_bearer(auth, admin_user)


# ===========================================================================
# houses
# ===========================================================================


def owned_house(auth, hdr, **owner_over) -> int:
    """Onboard a building house and move it to ``owned``. Returns the house id."""
    owner_over.setdefault("persons_living", 2)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "owned", _owner(**owner_over))
    assert resp.status_code == 200, resp.text
    return hid


def rented_house(auth, hdr, *, owner_over=None, tenant_over=None) -> int:
    """Onboard a building house and move it to ``rented``. Returns the house id."""
    resp_owner = dict(owner_over or {})
    resp_owner.setdefault("persons_living", 2)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(
        auth,
        hdr,
        hid,
        "rented",
        _owner(**resp_owner),
        _tenant(**(tenant_over or {})),
    )
    assert resp.status_code == 200, resp.text
    return hid


# ===========================================================================
# rate
# ===========================================================================


def set_rate_http(auth, hdr, amount, valid_from):
    """POST /finance/rate — a real write, not a DB insert (E2E exercises it)."""
    return auth.client.post(
        "/finance/rate",
        headers=hdr,
        json={"amount": str(amount), "valid_from": str(valid_from)},
    )


# ===========================================================================
# second society
# ===========================================================================


def second_society_with_finance(db, superadmin, auth, *, email="admin-b@test.local"):
    """A second, fully independent society with finance enabled + an activated
    admin bearer. Returns ``(society_b, admin_b, hdr_b)``."""
    soc_b = SocietyService(db).create_society(
        SocietyCreate(
            name="Society B",
            storage_limit_bytes=5 * 1024**3,
            default_member_password=DEFAULT_MEMBER_PASSWORD,
        ),
        actor_user_id=superadmin.id,
    )
    db.commit()
    db.refresh(soc_b)
    admin_b = UserProvisioningService(db).create_or_link_user(
        email=email,
        society_id=soc_b.id,
        role_key="society_admin",
        profile={"full_name": "Admin B"},
        actor_user_id=superadmin.id,
    )
    db.commit()
    db.refresh(admin_b)
    enable_finance(db, soc_b, superadmin)
    hdr_b = finance_admin_bearer(auth, admin_b)
    return soc_b, admin_b, hdr_b


# ===========================================================================
# audit / reserve
# ===========================================================================


def audit_actions(db, society_id) -> list[tuple[str, str, int]]:
    """``[(action, entity_type, entity_id), ...]`` for a society, oldest-first."""
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.society_id == society_id)
        .order_by(AuditLog.id)
        .all()
    )
    return [(r.action, r.entity_type, r.entity_id) for r in rows]


def reserve_balance(db, society_id) -> Decimal:
    from app.modules.finance.repository import FinanceRepository

    return FinanceRepository(db).reserve_balance(society_id)


# ===========================================================================
# date determinism — monkeypatch ``utcnow`` where it's USED
# ===========================================================================

# ``utcnow`` is imported into each module as ``from app.common.time import
# utcnow`` and called as ``utcnow()`` — a plain module-level name binding, not
# an attribute lookup through ``app.common.time`` at call time. Patching
# ``app.common.time.utcnow`` therefore does NOT affect already-imported call
# sites; each consumer module's OWN ``utcnow`` name must be patched.
_UTCNOW_CONSUMERS = (
    "app.modules.finance.services.dues.utcnow",
    "app.modules.finance.services.collection.utcnow",
    "app.modules.finance.services.expenses.utcnow",
    "app.modules.finance.services.jobs.utcnow",
    "app.modules.houses.service.utcnow",
)


def freeze_utcnow(monkeypatch, frozen_date: date = FROZEN_TODAY) -> None:
    """Patch every finance (+houses) call site's ``utcnow`` to a fixed instant.

    ``frozen_date`` at UTC midnight. Use inside a test via the ``monkeypatch``
    fixture directly, or via :func:`frozen_today` as a context manager.
    """
    frozen_dt = datetime.combine(frozen_date, datetime.min.time(), tzinfo=timezone.utc)
    for target in _UTCNOW_CONSUMERS:
        monkeypatch.setattr(target, lambda: frozen_dt, raising=False)


@contextmanager
def frozen_today(frozen_date: date = FROZEN_TODAY):
    """Context-manager form of :func:`freeze_utcnow` for tests not using the
    ``monkeypatch`` fixture directly (uses its own throwaway MonkeyPatch)."""
    import pytest

    mp = pytest.MonkeyPatch()
    try:
        freeze_utcnow(mp, frozen_date)
        yield
    finally:
        mp.undo()
