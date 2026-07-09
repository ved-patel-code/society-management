"""Smoke: the Notifications wiring works end-to-end (helper self-check).

Not the full gate — just proves the helper + the event/worker paths function, so
the exhaustive per-concern suites can build on a known-good harness.
"""
from __future__ import annotations

from datetime import date

from app.modules.notifications.schemas import (
    TYPE_COMPLAINT_NEW,
    TYPE_MAINTENANCE_DUE,
    TYPE_NOTICE,
)
from app.modules.notifications.services import jobs

from tests._notifications_helpers import (
    admin_bearer,
    db_notifications,
    first_category_id,
    get_unread_count,
    owned_house_for,
    owner_login_bearer,
    publish_notice_http,
    raise_complaint_http,
    set_rate_http,
    setup_notifications,
)


def test_complaint_raised_notifies_admin(auth, db, society, admin_user, superadmin):
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    # an owner login on a house
    hid = owned_house_for(auth, hdr, email="owner1@notif.local")
    o_hdr, owner = owner_login_bearer(auth, db, email="owner1@notif.local")
    cat = first_category_id(auth.client, o_hdr)

    resp = raise_complaint_http(auth.client, o_hdr, category_id=cat)
    assert resp.status_code == 200, resp.text

    # admin (holds complaints.read_all) gets a complaint_new notification
    rows = db_notifications(db, society.id, user_id=admin_user.id, type_=TYPE_COMPLAINT_NEW)
    assert len(rows) == 1
    assert rows[0].entity_type == "complaint"
    assert get_unread_count(auth.client, hdr) >= 1


def test_notice_published_notifies_owner(auth, db, society, admin_user, superadmin):
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    hid = owned_house_for(auth, hdr, email="owner2@notif.local")
    o_hdr, owner = owner_login_bearer(auth, db, email="owner2@notif.local")

    resp = publish_notice_http(auth.client, hdr, title="Water outage")
    assert resp.status_code == 200, resp.text

    rows = db_notifications(db, society.id, user_id=owner.id, type_=TYPE_NOTICE)
    assert len(rows) == 1
    assert rows[0].entity_type == "notice"


def test_dues_reminder_worker_fires(auth, db, society, admin_user, superadmin, monkeypatch):
    # Config: advance 3 days. Owner owes; run worker on the advance day.
    hdr = setup_notifications(
        db, society, admin_user, superadmin, auth,
        config={"dues_advance_days": 3, "dues_reminder_interval_days": 5},
    )
    hid = owned_house_for(auth, hdr, email="owner3@notif.local")
    o_hdr, owner = owner_login_bearer(auth, db, email="owner3@notif.local")

    # set a rate + generate a due cycle so the house owes
    set_rate_http(auth, hdr, "1500.00", date(2026, 6, 1))
    gen = auth.client.post("/finance/dues/generate", headers=hdr)
    assert gen.status_code in (200, 201), gen.text

    # find the due_date of the outstanding due, run the worker on due_date - 3
    dues = auth.client.get(f"/finance/houses/{hid}/dues", headers=hdr).json()
    assert dues["outstanding"], dues
    due_date = date.fromisoformat(dues["outstanding"][-1]["due_date"])
    fire_day = due_date.fromordinal(due_date.toordinal() - 3)

    # freeze the worker's utcnow to the advance day
    import app.modules.notifications.services.jobs as jobs_mod
    from datetime import datetime, timezone
    monkeypatch.setattr(
        jobs_mod, "utcnow",
        lambda: datetime.combine(fire_day, datetime.min.time(), tzinfo=timezone.utc),
    )
    result = jobs.run_daily_dues_reminders()
    assert result["reminders_created"] >= 1, result

    rows = db_notifications(db, society.id, user_id=owner.id, type_=TYPE_MAINTENANCE_DUE)
    assert len(rows) == 1
    # idempotent: a re-run creates nothing new
    result2 = jobs.run_daily_dues_reminders()
    rows2 = db_notifications(db, society.id, user_id=owner.id, type_=TYPE_MAINTENANCE_DUE)
    assert len(rows2) == 1, "dues reminder must be idempotent per (house, day)"
