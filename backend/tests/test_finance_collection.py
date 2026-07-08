"""Wave C tests — Finance collection, prepaid, and payment void (docs §4/§6).

Money-correctness suite for ``CollectionService.record_payment`` /
``record_prepaid`` / ``void_payment``. Dues generation (Wave B) is a separate
stub, so these tests materialize ``house_dues`` directly via the session to set
up outstanding months, then drive the writes through the HTTP API (real gates +
tenant context) and assert against the DB.

Coverage: pay one/several/all oldest-first; amount = Σ months; dues flip to
paid; collection inflow posted + reserve rises; bad inputs (N>outstanding,
neither/both selectors, nothing outstanding) → 422; prepaid 3/6/9/12 at locked
rate, arrears → 409, bad block → 422, later rate rise doesn't change prepaid
months; void re-opens dues + posts a visible reversal + reserve returns,
double-void → 409; permission 403; cross-society isolation.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.common.time import utcnow
from app.modules.finance.models import (
    HouseDue,
    LedgerEntry,
    MaintenanceRate,
    Payment,
    PaymentAllocation,
    PrepaidBlock,
)
from app.modules.finance.periods import add_months, period_of
from app.modules.finance.repository import FinanceRepository
from app.platform.models import AuditLog
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

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

RATE = Decimal("1000.00")


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
    """Enable modules + an activated admin bearer header."""
    _enable_finance(db, society, superadmin, config=config)
    return _admin_bearer(auth, admin_user)


def _owned_house(auth, hdr) -> int:
    """A building house moved to ``owned`` (non-empty → it owes dues)."""
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "owned", _owner(persons_living=2))
    assert resp.status_code == 200, resp.text
    return hid


def _set_rate(db, society_id, amount=RATE, valid_from=date(2024, 1, 1)) -> None:
    db.add(
        MaintenanceRate(
            society_id=society_id, amount=Decimal(amount), valid_from=valid_from
        )
    )
    db.commit()


def _add_dues(db, society_id, house_id, periods, *, amount=RATE) -> list[int]:
    """Materialize outstanding ``house_dues`` for ``(year, month)`` periods."""
    ids: list[int] = []
    for (y, m) in periods:
        due = HouseDue(
            society_id=society_id,
            house_id=house_id,
            period_year=y,
            period_month=m,
            amount_due=Decimal(amount),
            due_date=date(y, m, 1),
            status="outstanding",
            source="accrued",
        )
        db.add(due)
        db.flush()
        ids.append(due.id)
    db.commit()
    return ids


def _reserve(db, society_id) -> Decimal:
    return FinanceRepository(db).reserve_balance(society_id)


def _pay(auth, hdr, hid, **body):
    return auth.client.post(
        f"/finance/houses/{hid}/payments", headers=hdr, json=body
    )


def _prepaid(auth, hdr, hid, **body):
    return auth.client.post(
        f"/finance/houses/{hid}/prepaid", headers=hdr, json=body
    )


def _void(auth, hdr, pid, reason="mistake"):
    return auth.client.post(
        f"/finance/payments/{pid}/void", headers=hdr, json={"reason": reason}
    )


def _clear_current_arrears(auth, hdr, hid) -> None:
    """Settle whatever current/past dues ``record_prepaid`` will materialize.

    ``record_prepaid`` now runs ``generate_due_cycle`` FIRST, so a non-empty
    house (``_owned_house``) always has the current month's due materialized
    before the arrears check. Trigger that same generation up front (via the
    on-demand ``/finance/dues/generate`` endpoint) and pay it off, so the
    prepaid call proceeds to the "clear" branch and its window lands in the
    FUTURE.
    """
    gen = auth.client.post("/finance/dues/generate", headers=hdr)
    assert gen.status_code == 200, gen.text
    resp = _pay(auth, hdr, hid, method="cash", pay_all=True)
    assert resp.status_code == 200, resp.text


# ===========================================================================
# record_payment — happy paths
# ===========================================================================


def test_pay_one_month_oldest_first(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    hid = _owned_house(auth, hdr)
    _set_rate(db, society.id)
    ids = _add_dues(db, society.id, hid, [(2024, 1), (2024, 2), (2024, 3)])

    before = _reserve(db, society.id)
    resp = _pay(auth, hdr, hid, method="cash", months=1)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["amount"] == "1000.00"
    assert body["status"] == "recorded"
    assert len(body["allocations"]) == 1
    # Oldest first: the Jan due is the one settled.
    assert body["allocations"][0]["house_due_id"] == ids[0]
    assert body["allocations"][0]["period_month"] == 1

    db.expire_all()
    assert db.get(HouseDue, ids[0]).status == "paid"
    assert db.get(HouseDue, ids[1]).status == "outstanding"
    # A collection inflow posted; reserve rose by exactly the amount.
    assert _reserve(db, society.id) == before + Decimal("1000.00")


def test_pay_several_months(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    hid = _owned_house(auth, hdr)
    _set_rate(db, society.id)
    ids = _add_dues(db, society.id, hid, [(2024, 1), (2024, 2), (2024, 3)])

    resp = _pay(auth, hdr, hid, method="online", months=2)
    assert resp.status_code == 200, resp.text
    assert resp.json()["amount"] == "2000.00"
    assert len(resp.json()["allocations"]) == 2

    db.expire_all()
    assert db.get(HouseDue, ids[0]).status == "paid"
    assert db.get(HouseDue, ids[1]).status == "paid"
    assert db.get(HouseDue, ids[2]).status == "outstanding"


def test_pay_all(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    hid = _owned_house(auth, hdr)
    _set_rate(db, society.id)
    ids = _add_dues(db, society.id, hid, [(2024, 1), (2024, 2), (2024, 3)])

    before = _reserve(db, society.id)
    resp = _pay(auth, hdr, hid, method="bank_transfer", pay_all=True)
    assert resp.status_code == 200, resp.text
    assert resp.json()["amount"] == "3000.00"
    assert len(resp.json()["allocations"]) == 3

    db.expire_all()
    for i in ids:
        assert db.get(HouseDue, i).status == "paid"
    assert _reserve(db, society.id) == before + Decimal("3000.00")
    # Nothing left outstanding.
    resp = auth.client.get(f"/finance/houses/{hid}/dues", headers=hdr)
    assert resp.json()["outstanding_total"] == "0.00"


def test_payment_recorded_audit(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    hid = _owned_house(auth, hdr)
    _set_rate(db, society.id)
    _add_dues(db, society.id, hid, [(2024, 1), (2024, 2)])

    resp = _pay(auth, hdr, hid, method="cash", months=1)
    pid = resp.json()["id"]
    rows = (
        db.query(AuditLog)
        .filter(
            AuditLog.action == "finance.payment_recorded",
            AuditLog.entity_id == pid,
        )
        .all()
    )
    assert len(rows) == 1
    assert rows[0].after["amount"] == "1000.00"
    assert len(rows[0].after["allocations"]) == 1


# ===========================================================================
# record_payment — bad paths
# ===========================================================================


def test_pay_more_than_outstanding_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    hid = _owned_house(auth, hdr)
    _set_rate(db, society.id)
    _add_dues(db, society.id, hid, [(2024, 1)])
    resp = _pay(auth, hdr, hid, method="cash", months=5)
    assert resp.status_code == 422, resp.text


def test_pay_neither_selector_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    hid = _owned_house(auth, hdr)
    _set_rate(db, society.id)
    _add_dues(db, society.id, hid, [(2024, 1)])
    resp = _pay(auth, hdr, hid, method="cash")
    assert resp.status_code == 422, resp.text


def test_pay_both_selectors_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    hid = _owned_house(auth, hdr)
    _set_rate(db, society.id)
    _add_dues(db, society.id, hid, [(2024, 1)])
    resp = _pay(auth, hdr, hid, method="cash", months=1, pay_all=True)
    assert resp.status_code == 422, resp.text


def test_pay_nothing_outstanding_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    hid = _owned_house(auth, hdr)
    _set_rate(db, society.id)
    resp = _pay(auth, hdr, hid, method="cash", pay_all=True)
    assert resp.status_code == 422, resp.text


# ===========================================================================
# record_prepaid
# ===========================================================================


def test_prepaid_3_months_locked_rate(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    hid = _owned_house(auth, hdr)
    _set_rate(db, society.id)
    # record_prepaid materializes + requires the current month's due be cleared
    # first (arrears-first, docs §4); settle it before attempting the block.
    _clear_current_arrears(auth, hdr, hid)

    current = period_of(utcnow().date())
    before = _reserve(db, society.id)
    resp = _prepaid(auth, hdr, hid, months_count=3, method="online")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["amount"] == "3000.00"
    assert len(body["allocations"]) == 3

    db.expire_all()
    block = db.execute(
        select(PrepaidBlock).where(PrepaidBlock.house_id == hid)
    ).scalar_one()
    assert block.months_count == 3
    assert block.rate_locked == Decimal("1000.00")

    dues = db.execute(
        select(HouseDue).where(HouseDue.house_id == hid, HouseDue.source == "prepaid")
    ).scalars().all()
    assert len(dues) == 3
    expected_months = [add_months(*current, i) for i in (1, 2, 3)]
    got_months = sorted((d.period_year, d.period_month) for d in dues)
    assert got_months == sorted(expected_months)
    for d in dues:
        assert d.status == "paid"
        assert d.source == "prepaid"
        assert d.locked_rate == Decimal("1000.00")
        # The block covers FUTURE months only, never the (already-cleared) current one.
        assert (d.period_year, d.period_month) > current
    assert _reserve(db, society.id) == before + Decimal("3000.00")


def test_prepaid_6_9_12_blocks(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _set_rate(db, society.id)  # rate is society-wide
    current = period_of(utcnow().date())
    for n, expected in ((6, "6000.00"), (9, "9000.00"), (12, "12000.00")):
        houses = _make_building_with_houses(auth, hdr, names=[f"B{n}"])
        hid = houses[0]["id"]
        _set_status(auth, hdr, hid, "owned", _owner(email=f"o{n}@x.com", persons_living=1))
        # Each new owned house owes the current month once record_prepaid
        # materializes it — clear it first so the block window is future-only.
        _clear_current_arrears(auth, hdr, hid)
        resp = _prepaid(auth, hdr, hid, months_count=n, method="cash")
        assert resp.status_code == 200, resp.text
        assert resp.json()["amount"] == expected
        assert len(resp.json()["allocations"]) == n

        db.expire_all()
        dues = db.execute(
            select(HouseDue).where(
                HouseDue.house_id == hid, HouseDue.source == "prepaid"
            )
        ).scalars().all()
        assert len(dues) == n
        expected_months = [add_months(*current, i) for i in range(1, n + 1)]
        got_months = sorted((d.period_year, d.period_month) for d in dues)
        assert got_months == sorted(expected_months)
        for d in dues:
            assert d.status == "paid"
            assert (d.period_year, d.period_month) > current


def test_prepaid_arrears_present_409(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    hid = _owned_house(auth, hdr)
    _set_rate(db, society.id)
    _add_dues(db, society.id, hid, [(2024, 1)])  # an outstanding arrear
    resp = _prepaid(auth, hdr, hid, months_count=3, method="cash")
    assert resp.status_code == 409, resp.text


def test_prepaid_block_not_in_config_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    hid = _owned_house(auth, hdr)
    _set_rate(db, society.id)
    resp = _prepaid(auth, hdr, hid, months_count=5, method="cash")
    assert resp.status_code == 422, resp.text


def test_prepaid_no_rate_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    hid = _owned_house(auth, hdr)
    resp = _prepaid(auth, hdr, hid, months_count=3, method="cash")
    assert resp.status_code == 422, resp.text


def test_prepaid_locks_rate_against_later_rise(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    hid = _owned_house(auth, hdr)
    _set_rate(db, society.id, amount=Decimal("1000.00"))
    # Clear the current month's due (materialized by record_prepaid's
    # generate_due_cycle call) so arrears don't block the block purchase.
    _clear_current_arrears(auth, hdr, hid)

    resp = _prepaid(auth, hdr, hid, months_count=3, method="cash")
    assert resp.status_code == 200, resp.text

    # A later rate rise must NOT change the prepaid months (rate was locked).
    db.add(
        MaintenanceRate(
            society_id=society.id,
            amount=Decimal("5000.00"),
            valid_from=date(2030, 1, 1),
        )
    )
    db.commit()
    db.expire_all()
    dues = db.execute(
        select(HouseDue).where(HouseDue.house_id == hid, HouseDue.source == "prepaid")
    ).scalars().all()
    assert len(dues) == 3
    for d in dues:
        assert d.amount_due == Decimal("1000.00")
        assert d.locked_rate == Decimal("1000.00")


def test_prepaid_config_custom_blocks(db, society, admin_user, superadmin, auth):
    """A society whose config restricts blocks rejects a default-but-unlisted size."""
    hdr = _setup(
        db, society, admin_user, superadmin, auth,
        config={"prepaid_blocks": [6]},
    )
    hid = _owned_house(auth, hdr)
    _set_rate(db, society.id)
    # 3 is a global default but not in THIS society's config → 422 (validated
    # before the arrears/materialize step, so no need to clear dues for this one).
    assert _prepaid(auth, hdr, hid, months_count=3, method="cash").status_code == 422

    # The current month's due is materialized by generate_due_cycle regardless
    # of the rejected attempt above (months_count validation is a separate,
    # earlier check) — clear it before the valid 6-month block can succeed.
    _clear_current_arrears(auth, hdr, hid)
    resp = _prepaid(auth, hdr, hid, months_count=6, method="cash")
    assert resp.status_code == 200, resp.text

    db.expire_all()
    dues = db.execute(
        select(HouseDue).where(HouseDue.house_id == hid, HouseDue.source == "prepaid")
    ).scalars().all()
    assert len(dues) == 6
    current = period_of(utcnow().date())
    for d in dues:
        assert (d.period_year, d.period_month) > current


# ===========================================================================
# void_payment
# ===========================================================================


def test_void_reopens_dues_and_reverses(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    hid = _owned_house(auth, hdr)
    _set_rate(db, society.id)
    ids = _add_dues(db, society.id, hid, [(2024, 1), (2024, 2)])

    prior = _reserve(db, society.id)
    pay = _pay(auth, hdr, hid, method="cash", pay_all=True)
    pid = pay.json()["id"]
    assert _reserve(db, society.id) == prior + Decimal("2000.00")

    resp = _void(auth, hdr, pid, reason="entered twice")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "voided"
    assert resp.json()["void_reason"] == "entered twice"

    db.expire_all()
    # Dues re-opened (paid → outstanding, paid_at cleared).
    for i in ids:
        d = db.get(HouseDue, i)
        assert d.status == "outstanding"
        assert d.paid_at is None
    # Payment kept (not deleted); allocations kept.
    assert db.get(Payment, pid).status == "voided"
    allocs = db.execute(
        select(PaymentAllocation).where(PaymentAllocation.payment_id == pid)
    ).scalars().all()
    assert len(allocs) == 2
    # Reserve returns to prior (reversal netted the collection).
    assert _reserve(db, society.id) == prior


def test_void_original_and_reversal_both_visible(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    hid = _owned_house(auth, hdr)
    _set_rate(db, society.id)
    _add_dues(db, society.id, hid, [(2024, 1)])

    pid = _pay(auth, hdr, hid, method="cash", months=1).json()["id"]
    _void(auth, hdr, pid).status_code == 200

    db.expire_all()
    entries = db.execute(
        select(LedgerEntry).where(LedgerEntry.society_id == society.id)
    ).scalars().all()
    collection = [e for e in entries if e.entry_type == "collection"]
    reversal = [e for e in entries if e.entry_type == "reversal"]
    assert len(collection) == 1
    assert len(reversal) == 1
    # Both stay visible; original flagged reversed; reversal points back + opposite.
    assert collection[0].is_reversed is True
    assert reversal[0].direction == "outflow"
    assert reversal[0].amount == collection[0].amount
    assert reversal[0].reverses_entry_id == collection[0].id


def test_double_void_409(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    hid = _owned_house(auth, hdr)
    _set_rate(db, society.id)
    _add_dues(db, society.id, hid, [(2024, 1)])
    pid = _pay(auth, hdr, hid, method="cash", months=1).json()["id"]
    assert _void(auth, hdr, pid).status_code == 200
    assert _void(auth, hdr, pid).status_code == 409


def test_void_unknown_payment_404(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    assert _void(auth, hdr, 999999).status_code == 404


def test_void_audit(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    hid = _owned_house(auth, hdr)
    _set_rate(db, society.id)
    _add_dues(db, society.id, hid, [(2024, 1)])
    pid = _pay(auth, hdr, hid, method="cash", months=1).json()["id"]
    _void(auth, hdr, pid, reason="dup")
    rows = (
        db.query(AuditLog)
        .filter(
            AuditLog.action == "finance.payment_voided",
            AuditLog.entity_id == pid,
        )
        .all()
    )
    assert len(rows) == 1
    assert rows[0].after["reason"] == "dup"


# ===========================================================================
# security
# ===========================================================================


def test_record_payment_without_permission_403(
    db, society, admin_user, superadmin, auth, resident_user
):
    """A resident holds finance.read but NOT finance.record_payment."""
    hdr = _setup(db, society, admin_user, superadmin, auth)
    hid = _owned_house(auth, hdr)
    _set_rate(db, society.id)
    _add_dues(db, society.id, hid, [(2024, 1)])

    # Activate the resident and drive the endpoint with their bearer.
    tokens = auth.login_ok(resident_user.email, DEFAULT_MEMBER_PASSWORD)
    resp = auth.client.post(
        "/auth/change-password",
        headers=auth.bearer(tokens["access_token"]),
        json={"current_password": DEFAULT_MEMBER_PASSWORD, "new_password": "NewPass123"},
    )
    assert resp.status_code == 200, resp.text
    sess = auth.login_ok(resident_user.email, "NewPass123")
    rhdr = auth.bearer(sess["access_token"])

    resp = _pay(auth, rhdr, hid, method="cash", months=1)
    assert resp.status_code == 403, resp.text


def test_cross_society_payment_isolation(
    db, society, admin_user, superadmin, auth
):
    """A payment in society A is invisible to society B (void → 404)."""
    hdr_a = _setup(db, society, admin_user, superadmin, auth)
    hid_a = _owned_house(auth, hdr_a)
    _set_rate(db, society.id)
    _add_dues(db, society.id, hid_a, [(2024, 1)])
    pid_a = _pay(auth, hdr_a, hid_a, method="cash", months=1).json()["id"]

    # Build a second society + its admin, enable finance, get a bearer.
    soc_b = SocietyService(db).create_society(
        SocietyCreate(
            name="Society B",
            storage_limit_bytes=5 * 1024**3,
            default_member_password=DEFAULT_MEMBER_PASSWORD,
        ),
        actor_user_id=superadmin.id,
    )
    db.commit()
    admin_b = UserProvisioningService(db).create_or_link_user(
        email="admin-b@test.local",
        society_id=soc_b.id,
        role_key="society_admin",
        profile={"full_name": "Admin B"},
        actor_user_id=superadmin.id,
    )
    db.commit()
    _enable_finance(db, soc_b, superadmin)
    hdr_b = _admin_bearer(auth, admin_b)

    # Society B cannot see / void society A's payment.
    assert _void(auth, hdr_b, pid_a).status_code == 404

    db.expire_all()
    assert db.get(Payment, pid_a).status == "recorded"
