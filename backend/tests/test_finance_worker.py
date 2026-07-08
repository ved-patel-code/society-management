"""Finance worker-job tests (Module 4, Wave G) — docs/modules/finance.md §9.

Covers the daily dues-generation scan (:mod:`app.modules.finance.services.jobs`):

- generates dues for a finance-enabled society when ``as_of.day`` == its
  configured ``maintenance_due_day``; a second run is idempotent (0 created);
- a society whose due day != today's day gets nothing that day;
- finance-disabled societies are skipped (never appear in the enabled set);
- multi-society failure isolation: one society without a rate (→ 0 dues, no
  crash) does not stop another society from being billed;
- the worker helper and the public ``api.generate_due_cycle`` agree.

The worker job opens its OWN ``SessionLocal`` and commits per society, exactly
like the vault job tests (``test_vault_e2e``). We drive the pure helper
``_run_for_societies`` with a chosen ``as_of`` so the test is date-stable, and
also exercise ``run_daily_dues_generation`` (which reads today's UTC date).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.modules.finance import api as finance_api
from app.modules.finance.models import HouseDue
from app.modules.finance.service import FinanceService
from app.modules.finance.schemas import RateSetRequest
from app.modules.finance.services.jobs import (
    _enabled_finance_society_ids,
    _run_for_societies,
    run_daily_dues_generation,
)
from app.platform.societies.schemas import ModuleAllocation, SocietyCreate
from app.platform.societies.service import SocietyService
from app.platform.users.provisioning import UserProvisioningService

from tests._houses_helpers import (
    _admin_bearer,
    _make_building_with_houses,
    _owner,
    _set_status,
)
from tests.conftest import DEFAULT_MEMBER_PASSWORD

# The fixture default calendar day the whole module pins ``as_of`` to.
AS_OF = date(2026, 7, 8)


# --- helpers ----------------------------------------------------------------

def _enable_finance(db, society_id, superadmin, *, config=None) -> None:
    """Enable onboarding + houses + finance (finance depends_on houses)."""
    SocietyService(db).set_modules(
        society_id,
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


def _set_rate(db, society_id, amount, valid_from, *, actor) -> None:
    FinanceService(db).rates.set_rate(
        society_id,
        RateSetRequest(amount=Decimal(str(amount)), valid_from=valid_from),
        actor_user_id=actor,
    )
    db.commit()


def _dues(db, society_id):
    return list(
        db.execute(
            select(HouseDue).where(HouseDue.society_id == society_id)
        ).scalars()
    )


def _make_second_society(db, superadmin, *, name, admin_email):
    """A second fully independent society + its own activated admin bearer."""
    soc = SocietyService(db).create_society(
        SocietyCreate(
            name=name,
            storage_limit_bytes=5 * 1024**3,
            default_member_password=DEFAULT_MEMBER_PASSWORD,
        ),
        actor_user_id=superadmin.id,
    )
    db.commit()
    db.refresh(soc)
    admin = UserProvisioningService(db).create_or_link_user(
        email=admin_email,
        society_id=soc.id,
        role_key="society_admin",
        profile={"full_name": "Admin " + name},
        actor_user_id=superadmin.id,
    )
    db.commit()
    db.refresh(admin)
    return soc, admin


def _owing_house(auth, hdr):
    """Build a building, move one house off empty so it owes; return its id."""
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=2))
    return hid


# ===========================================================================
# happy path: today == due_day → dues generated; idempotent second run
# ===========================================================================

def test_worker_generates_when_today_is_due_day(
    db, society, admin_user, superadmin, auth
):
    # due day 8 == AS_OF.day.
    _enable_finance(db, society.id, superadmin, config={"maintenance_due_day": 8})
    hdr = _admin_bearer(auth, admin_user)
    _set_rate(db, society.id, "1500.00", date(2026, 1, 1), actor=admin_user.id)
    _owing_house(auth, hdr)

    result = _run_for_societies(db, [society.id], AS_OF)
    db.commit()

    assert result == {"societies_processed": 1, "dues_created": 1}
    assert len(_dues(db, society.id)) == 1

    # Idempotent: a second run on the same day creates nothing.
    again = _run_for_societies(db, [society.id], AS_OF)
    db.commit()
    assert again == {"societies_processed": 1, "dues_created": 0}
    assert len(_dues(db, society.id)) == 1


# ===========================================================================
# due_day != today's day → skipped entirely (not even "processed")
# ===========================================================================

def test_worker_skips_when_not_due_day(
    db, society, admin_user, superadmin, auth
):
    # due day 15 != AS_OF.day (8).
    _enable_finance(db, society.id, superadmin, config={"maintenance_due_day": 15})
    hdr = _admin_bearer(auth, admin_user)
    _set_rate(db, society.id, "1500.00", date(2026, 1, 1), actor=admin_user.id)
    _owing_house(auth, hdr)

    result = _run_for_societies(db, [society.id], AS_OF)
    db.commit()

    assert result == {"societies_processed": 0, "dues_created": 0}
    assert _dues(db, society.id) == []


# ===========================================================================
# finance-disabled societies never enter the enabled set
# ===========================================================================

def test_disabled_finance_society_is_skipped(
    db, society, admin_user, superadmin, auth
):
    # Enable only onboarding + houses (finance NOT enabled).
    SocietyService(db).set_modules(
        society.id,
        [
            ModuleAllocation(module_key="onboarding", enabled=True, config={}),
            ModuleAllocation(module_key="houses", enabled=True, config={}),
        ],
        actor_user_id=superadmin.id,
    )
    db.commit()

    assert society.id not in _enabled_finance_society_ids(db)

    # Even if the scan were driven with this id, nothing is created — but the
    # whole-scan entry point must simply not see it.
    result = run_daily_dues_generation()
    assert result == {"societies_processed": 0, "dues_created": 0}
    assert _dues(db, society.id) == []


# ===========================================================================
# multi-society: one society without a rate (0 dues, no crash) does not stop
# another society from being billed
# ===========================================================================

def test_multi_society_failure_isolation(
    db, society, admin_user, superadmin, auth
):
    # Society A: fully set up, due day 8, has a rate → will be billed.
    _enable_finance(db, society.id, superadmin, config={"maintenance_due_day": 8})
    hdr_a = _admin_bearer(auth, admin_user)
    _set_rate(db, society.id, "1500.00", date(2026, 1, 1), actor=admin_user.id)
    _owing_house(auth, hdr_a)

    # Society B: due day 8, an owing house but NO rate → generate returns 0
    # gracefully (not a crash). It must not stop A.
    soc_b, admin_b = _make_second_society(
        db, superadmin, name="Second Society", admin_email="admin.b@test.local"
    )
    _enable_finance(db, soc_b.id, superadmin, config={"maintenance_due_day": 8})
    hdr_b = _admin_bearer(auth, admin_b)
    _owing_house(auth, hdr_b)  # owing, but no rate set for B

    # Drive both (order: B first so a B failure would precede A).
    result = _run_for_societies(db, [soc_b.id, society.id], AS_OF)
    db.commit()

    # Both societies matched the due day and were processed; only A produced dues.
    assert result == {"societies_processed": 2, "dues_created": 1}
    assert len(_dues(db, society.id)) == 1
    assert _dues(db, soc_b.id) == []


# ===========================================================================
# whole-scan entry point (reads today's UTC date) agrees with helper + api
# ===========================================================================

def test_run_daily_scan_matches_helper_and_api(
    db, society, admin_user, superadmin, auth
):
    from app.common.time import utcnow

    today = utcnow().date()
    # Configure the society's due day to TODAY so the real daily scan fires.
    _enable_finance(
        db, society.id, superadmin, config={"maintenance_due_day": today.day}
    )
    hdr = _admin_bearer(auth, admin_user)
    _set_rate(db, society.id, "1000.00", date(2026, 1, 1), actor=admin_user.id)
    _owing_house(auth, hdr)

    # The public/on-demand API and the worker helper must agree on the count for
    # the same run date — assert via a dry idempotency handshake:
    # 1) real daily scan (its own session, commits) creates the dues,
    result = run_daily_dues_generation()
    db.expire_all()
    assert result["societies_processed"] == 1
    assert result["dues_created"] == 1
    assert len(_dues(db, society.id)) == 1

    # 2) the on-demand api path, same run date, is now idempotent (0 created),
    api_created = finance_api.generate_due_cycle(
        db, society.id, as_of=today, actor_user_id=admin_user.id
    )
    db.commit()
    assert api_created == 0

    # 3) and the helper likewise sees nothing left to do.
    helper = _run_for_societies(db, [society.id], today)
    db.commit()
    assert helper == {"societies_processed": 1, "dues_created": 0}
