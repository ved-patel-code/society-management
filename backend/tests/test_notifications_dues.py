"""Notifications (Module 7) — SCHEDULED dues path (docs §4.3 / §9).

Cadence (``is_fire_day`` pure math), CONSOLIDATION (one row per owner across all
unpaid months), IDEMPOTENCY (dedupe per house-per-day), AUTO-STOP (paid / no dues
→ nothing), MULTI-OWNER fan-out, non-fire-day no-op, and the READ-PURGE worker.

Builds on the shared harness (``tests/_notifications_helpers``) and drives real
Finance dues over HTTP so the rule consumes the actual Finance interface, not a
hand-inserted stand-in. Where a specific calendar day is needed the anchor
``due_date`` is read back from Finance and the fire day is derived from it — so
the tests are independent of the society's configured due day.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.modules.finance.schemas import PaymentRecordRequest
from app.modules.finance.service import FinanceService
from app.modules.notifications.models import Notification
from app.modules.notifications.schemas import (
    NotificationsConfig,
    TYPE_MAINTENANCE_DUE,
)
from app.modules.notifications.services import dues_rule, jobs

from tests._notifications_helpers import (
    db_notifications,
    owned_house_for,
    owner_login_bearer,
    set_rate_http,
    setup_notifications,
)


# ===========================================================================
# helpers
# ===========================================================================


def _cfg(*, advance=3, interval=5, retention=30) -> NotificationsConfig:
    return NotificationsConfig(
        dues_advance_days=advance,
        dues_reminder_interval_days=interval,
        read_retention_days=retention,
    )


def _make_house_owing_two_months(auth, hdr, db, society_id, hid):
    """Make ``hid`` owe TWO months of maintenance and return its anchor (the most
    recent outstanding) due_date. Sets a rate effective early, backdates
    ``first_left_empty_on`` to two months back, then generates the due cycle.
    """
    from app.modules.onboarding.models import House

    set_rate_http(auth, hdr, "1500.00", date(2026, 1, 1))
    house = db.get(House, hid)
    # June + July 2026 → exactly two outstanding months at as_of 2026-07-08.
    house.first_left_empty_on = date(2026, 6, 1)
    db.commit()
    FinanceService(db).generate_due_cycle(society_id, as_of=date(2026, 7, 8))
    db.commit()

    dues = auth.client.get(f"/finance/houses/{hid}/dues", headers=hdr).json()
    assert len(dues["outstanding"]) == 2, dues
    anchor = max(date.fromisoformat(o["due_date"]) for o in dues["outstanding"])
    total = sum(Decimal(o["amount_due"]) for o in dues["outstanding"])
    return anchor, total


def _run_worker_on(monkeypatch, fire_day: date) -> dict:
    """Drive ``run_daily_dues_reminders`` with ``utcnow`` frozen at ``fire_day``."""
    monkeypatch.setattr(
        jobs,
        "utcnow",
        lambda: datetime.combine(fire_day, datetime.min.time(), tzinfo=timezone.utc),
    )
    return jobs.run_daily_dues_reminders()


# ===========================================================================
# is_fire_day — pure cadence math (docs §4.3)
# ===========================================================================


def test_is_fire_day_advance_dueday_and_recurring():
    cfg = _cfg(advance=3, interval=5)
    anchor = date(2026, 7, 1)

    # advance heads-up (X=3 before), due-day, recurring (+5, +10).
    assert dues_rule.is_fire_day(anchor, cfg, anchor - timedelta(days=3)) is True
    assert dues_rule.is_fire_day(anchor, cfg, anchor) is True
    assert dues_rule.is_fire_day(anchor, cfg, anchor + timedelta(days=5)) is True
    assert dues_rule.is_fire_day(anchor, cfg, anchor + timedelta(days=10)) is True

    # non-fire days.
    assert dues_rule.is_fire_day(anchor, cfg, anchor - timedelta(days=1)) is False
    assert dues_rule.is_fire_day(anchor, cfg, anchor + timedelta(days=1)) is False
    assert dues_rule.is_fire_day(anchor, cfg, anchor + timedelta(days=3)) is False


def test_is_fire_day_advance_zero_fires_on_due_date():
    cfg = _cfg(advance=0, interval=5)
    anchor = date(2026, 7, 1)
    # advance==0 collapses the advance day onto the due day; no separate day before.
    assert dues_rule.is_fire_day(anchor, cfg, anchor) is True
    assert dues_rule.is_fire_day(anchor, cfg, anchor - timedelta(days=1)) is False


def test_is_fire_day_recurring_interval_multiples():
    cfg = _cfg(advance=3, interval=5)
    anchor = date(2026, 7, 1)
    # every N=5 days past due fires; the between days do not.
    for k in (1, 2, 3, 4, 5, 6):
        day = anchor + timedelta(days=5 * k)
        assert dues_rule.is_fire_day(anchor, cfg, day) is True, day
    for delta in (2, 7, 11, 13, 14):
        day = anchor + timedelta(days=delta)
        assert dues_rule.is_fire_day(anchor, cfg, day) is False, day


# ===========================================================================
# build_for_house — consolidation, idempotency, auto-stop, multi-owner
# ===========================================================================


def test_build_for_house_consolidates_two_months_into_one_row(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_notifications(
        db, society, admin_user, superadmin, auth,
        config={"dues_advance_days": 3, "dues_reminder_interval_days": 5},
    )
    hid = owned_house_for(auth, hdr, email="dues1@notif.local")
    _o_hdr, owner = owner_login_bearer(auth, db, email="dues1@notif.local")

    anchor, total = _make_house_owing_two_months(auth, hdr, db, society.id, hid)

    created = dues_rule.build_for_house(
        db, society_id=society.id, house_id=hid, cfg=_cfg(), today=anchor
    )
    db.commit()
    assert created == 1  # ONE consolidated row for the single owner, not per-month

    rows = db_notifications(db, society.id, user_id=owner.id, type_=TYPE_MAINTENANCE_DUE)
    assert len(rows) == 1
    row = rows[0]
    assert row.payload["months_outstanding"] == 2
    assert Decimal(row.payload["outstanding_total"]) == total
    assert row.entity_type == "house"
    assert row.entity_id == hid


def test_build_for_house_idempotent_same_day(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    hid = owned_house_for(auth, hdr, email="dues2@notif.local")
    _o_hdr, owner = owner_login_bearer(auth, db, email="dues2@notif.local")
    anchor, _total = _make_house_owing_two_months(auth, hdr, db, society.id, hid)

    first = dues_rule.build_for_house(
        db, society_id=society.id, house_id=hid, cfg=_cfg(), today=anchor
    )
    db.commit()
    second = dues_rule.build_for_house(
        db, society_id=society.id, house_id=hid, cfg=_cfg(), today=anchor
    )
    db.commit()

    assert first == 1
    assert second == 0  # dedupe_key dues:{house}:{day} → no double-post
    rows = db_notifications(db, society.id, user_id=owner.id, type_=TYPE_MAINTENANCE_DUE)
    assert len(rows) == 1


def test_build_for_house_auto_stops_when_fully_paid(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    hid = owned_house_for(auth, hdr, email="dues3@notif.local")
    _o_hdr, owner = owner_login_bearer(auth, db, email="dues3@notif.local")
    anchor, _total = _make_house_owing_two_months(auth, hdr, db, society.id, hid)

    # Pay everything off → no outstanding dues → the reminder auto-stops.
    FinanceService(db).record_payment(
        society.id, hid, PaymentRecordRequest(method="cash", pay_all=True),
        actor_user_id=admin_user.id,
    )
    db.commit()

    created = dues_rule.build_for_house(
        db, society_id=society.id, house_id=hid, cfg=_cfg(), today=anchor
    )
    db.commit()
    assert created == 0
    rows = db_notifications(db, society.id, user_id=owner.id, type_=TYPE_MAINTENANCE_DUE)
    assert rows == []


def test_build_for_house_no_dues_at_all_is_noop(
    auth, db, society, admin_user, superadmin
):
    # A house with no dues generated at all → nothing to consolidate.
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    hid = owned_house_for(auth, hdr, email="dues4@notif.local")
    created = dues_rule.build_for_house(
        db, society_id=society.id, house_id=hid, cfg=_cfg(), today=date(2026, 7, 1)
    )
    db.commit()
    assert created == 0


def test_build_for_house_not_a_fire_day_is_noop(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    hid = owned_house_for(auth, hdr, email="dues5@notif.local")
    _o_hdr, owner = owner_login_bearer(auth, db, email="dues5@notif.local")
    anchor, _total = _make_house_owing_two_months(auth, hdr, db, society.id, hid)

    # anchor + 1 is NOT a fire day (advance=3, interval=5): 1 % 5 != 0.
    non_fire = anchor + timedelta(days=1)
    assert dues_rule.is_fire_day(anchor, _cfg(), non_fire) is False
    created = dues_rule.build_for_house(
        db, society_id=society.id, house_id=hid, cfg=_cfg(), today=non_fire
    )
    db.commit()
    assert created == 0
    assert db_notifications(
        db, society.id, user_id=owner.id, type_=TYPE_MAINTENANCE_DUE
    ) == []


def test_build_for_house_multiple_owners_each_one_consolidated_row(
    auth, db, society, admin_user, superadmin
):
    """Two owners of one house → each owner gets exactly ONE consolidated row,
    and a re-run adds nothing (per-recipient dedupe: the engine suffixes
    ``:{user_id}`` so each owner is idempotent independently).

    The data model allows only one CURRENT owner occupancy per house, so the two
    owner logins are supplied via the rule's pre-resolved ``owners`` set (the
    exact path the worker uses when it batch-resolves owners) — this is what
    drives the per-recipient fan-out + dedupe under test.
    """
    from app.platform.users.provisioning import UserProvisioningService

    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    hid = owned_house_for(auth, hdr, email="coowner-a@notif.local")
    _a_hdr, owner_a = owner_login_bearer(auth, db, email="coowner-a@notif.local")

    owner_b = UserProvisioningService(db).create_or_link_user(
        email="coowner-b@notif.local",
        society_id=society.id,
        role_key="resident",
        profile={"full_name": "Co Owner B"},
        actor_user_id=superadmin.id,
    )
    db.commit()
    db.refresh(owner_b)

    anchor, _total = _make_house_owing_two_months(auth, hdr, db, society.id, hid)
    owners = {owner_a.id, owner_b.id}
    created = dues_rule.build_for_house(
        db, society_id=society.id, house_id=hid, cfg=_cfg(), today=anchor,
        owners=owners,
    )
    db.commit()
    assert created == 2  # one consolidated row per owner

    # Re-run same day → per-recipient dedupe → no new rows for either owner.
    again = dues_rule.build_for_house(
        db, society_id=society.id, house_id=hid, cfg=_cfg(), today=anchor,
        owners=owners,
    )
    db.commit()
    assert again == 0

    for owner in (owner_a, owner_b):
        rows = db_notifications(
            db, society.id, user_id=owner.id, type_=TYPE_MAINTENANCE_DUE
        )
        assert len(rows) == 1, f"owner {owner.id} must get exactly one row"
        assert rows[0].payload["months_outstanding"] == 2


def test_worker_run_is_idempotent_across_runs(
    auth, db, society, admin_user, superadmin, monkeypatch
):
    """The full worker loop on a fire day, run twice → still exactly one row."""
    hdr = setup_notifications(
        db, society, admin_user, superadmin, auth,
        config={"dues_advance_days": 3, "dues_reminder_interval_days": 5},
    )
    hid = owned_house_for(auth, hdr, email="dues6@notif.local")
    _o_hdr, owner = owner_login_bearer(auth, db, email="dues6@notif.local")
    anchor, _total = _make_house_owing_two_months(auth, hdr, db, society.id, hid)

    first = _run_worker_on(monkeypatch, anchor)
    assert first["reminders_created"] == 1
    second = _run_worker_on(monkeypatch, anchor)
    assert second["reminders_created"] == 0

    rows = db_notifications(db, society.id, user_id=owner.id, type_=TYPE_MAINTENANCE_DUE)
    assert len(rows) == 1


# ===========================================================================
# read-purge worker (docs §9)
# ===========================================================================


def _insert_read_notification(db, society_id, user_id, *, read_at):
    n = Notification(
        society_id=society_id,
        user_id=user_id,
        type="notice",
        title="t",
        body="b",
        payload={},
        entity_type="notice",
        entity_id=1,
        dedupe_key=None,
        read_at=read_at,
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


def test_read_purge_deletes_old_read_keeps_recent_and_unread(
    auth, db, society, admin_user, superadmin, monkeypatch
):
    hdr = setup_notifications(
        db, society, admin_user, superadmin, auth,
        config={"read_retention_days": 30},
    )
    owner = admin_user
    now = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)
    old_read = _insert_read_notification(
        db, society.id, owner.id, read_at=now - timedelta(days=60)
    )
    recent_read = _insert_read_notification(
        db, society.id, owner.id, read_at=now - timedelta(days=5)
    )
    never_read = _insert_read_notification(
        db, society.id, owner.id, read_at=None
    )

    monkeypatch.setattr(jobs, "utcnow", lambda: now)
    result = jobs.run_daily_read_purge()
    assert result["notifications_deleted"] >= 1

    remaining = {n.id for n in db_notifications(db, society.id, user_id=owner.id)}
    assert old_read.id not in remaining, "read older than retention must be purged"
    assert recent_read.id in remaining, "recently-read is kept"
    assert never_read.id in remaining, "unread is NEVER purged"
