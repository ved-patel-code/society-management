"""Scale + resilience gate for Notifications (plan §D — the user's explicit bar).

Proves: a notice to many owners is ONE batched insert (no N+1); a dues scan spans
multiple societies; one society's failure never aborts the rest; a same-day
re-run creates nothing (crash / restart → exactly-once); one bad event subscriber
never breaks the emitter or the real handler (handler-failure containment).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import event

from app.common import events as event_bus
from app.modules.notifications.models import Notification
from app.modules.notifications.schemas import (
    TYPE_MAINTENANCE_DUE,
    TYPE_NOTICE,
)
from app.modules.notifications.services import jobs

from tests._notifications_helpers import (
    admin_bearer,
    db_notifications,
    many_owned_houses,
    owned_house_for,
    owner_login_bearer,
    publish_notice_http,
    set_rate_http,
    setup_notifications,
    second_society_with_notifications,
)


# ---------------------------------------------------------------------------
# SCALE — one batched INSERT for N owners (no N+1)
# ---------------------------------------------------------------------------


def test_notice_fanout_is_one_batched_insert(auth, db, society, admin_user, superadmin):
    """A notice to many owners issues exactly ONE INSERT into notifications
    (the batched multi-row fan-out), not one-per-owner."""
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    owners = many_owned_houses(auth, hdr, count=12)
    assert len(owners) == 12

    # Count INSERT statements that target the notifications table during publish.
    from app.core.db import engine

    inserts: list[str] = []

    def _before(conn, cursor, statement, params, context, executemany):
        s = statement.lower()
        if "insert into notifications" in s:
            inserts.append(statement)

    event.listen(engine, "before_cursor_execute", _before)
    try:
        resp = publish_notice_http(auth.client, hdr, title="Broadcast")
        assert resp.status_code == 200, resp.text
    finally:
        event.remove(engine, "before_cursor_execute", _before)

    # Exactly one INSERT statement for the whole fan-out.
    assert len(inserts) == 1, f"expected 1 batched insert, got {len(inserts)}"

    # And exactly one notification row per owner.
    rows = db_notifications(db, society.id, type_=TYPE_NOTICE)
    assert len(rows) == 12
    assert len({r.user_id for r in rows}) == 12


# ---------------------------------------------------------------------------
# MULTI-SOCIETY dues scan
# ---------------------------------------------------------------------------


def _owe_on(auth, hdr, hid, *, rate_from=date(2026, 6, 1)):
    """Give a house an outstanding due; return its outstanding due_date."""
    set_rate_http(auth, hdr, "1500.00", rate_from)
    gen = auth.client.post("/finance/dues/generate", headers=hdr)
    assert gen.status_code in (200, 201), gen.text
    dues = auth.client.get(f"/finance/houses/{hid}/dues", headers=hdr).json()
    assert dues["outstanding"], dues
    return date.fromisoformat(dues["outstanding"][-1]["due_date"])


def _freeze_worker_date(monkeypatch, when: date):
    monkeypatch.setattr(
        jobs, "utcnow",
        lambda: datetime.combine(when, datetime.min.time(), tzinfo=timezone.utc),
    )


def test_dues_scan_spans_multiple_societies(
    auth, db, society, admin_user, superadmin, monkeypatch
):
    """Two societies each with an owing house on a fire day → each owner gets one
    consolidated reminder; the scan reports both societies processed."""
    hdr_a = setup_notifications(
        db, society, admin_user, superadmin, auth,
        config={"dues_advance_days": 0, "dues_reminder_interval_days": 5},
    )
    hid_a = owned_house_for(auth, hdr_a, email="msowner-a@notif.local")
    _, owner_a = owner_login_bearer(auth, db, email="msowner-a@notif.local")
    due_a = _owe_on(auth, hdr_a, hid_a)

    soc_b, admin_b, hdr_b = second_society_with_notifications(db, superadmin, auth)
    # society B needs finance enabled too (second_society enables notifications +
    # deps incl. finance via the helper). Give it an owing house on the same day.
    hid_b = owned_house_for(auth, hdr_b, email="msowner-b@notif.local")
    _, owner_b = owner_login_bearer(auth, db, email="msowner-b@notif.local")
    due_b = _owe_on(auth, hdr_b, hid_b)

    # Both due on the same day (due-day fire, advance=0). Run the worker then.
    assert due_a == due_b
    _freeze_worker_date(monkeypatch, due_a)
    result = jobs.run_daily_dues_reminders()

    assert result["societies_processed"] == 2, result
    assert len(db_notifications(db, society.id, user_id=owner_a.id, type_=TYPE_MAINTENANCE_DUE)) == 1
    assert len(db_notifications(db, soc_b.id, user_id=owner_b.id, type_=TYPE_MAINTENANCE_DUE)) == 1


# ---------------------------------------------------------------------------
# PER-SOCIETY FAILURE ISOLATION
# ---------------------------------------------------------------------------


def test_one_society_failure_does_not_abort_others(
    auth, db, society, admin_user, superadmin, monkeypatch
):
    """If evaluating one society raises, the OTHER society still gets its
    reminder (the failure is contained + skipped)."""
    hdr_a = setup_notifications(
        db, society, admin_user, superadmin, auth,
        config={"dues_advance_days": 0},
    )
    hid_a = owned_house_for(auth, hdr_a, email="isoowner-a@notif.local")
    _, owner_a = owner_login_bearer(auth, db, email="isoowner-a@notif.local")
    due_a = _owe_on(auth, hdr_a, hid_a)

    soc_b, admin_b, hdr_b = second_society_with_notifications(db, superadmin, auth)
    hid_b = owned_house_for(auth, hdr_b, email="isoowner-b@notif.local")
    _, owner_b = owner_login_bearer(auth, db, email="isoowner-b@notif.local")
    _owe_on(auth, hdr_b, hid_b)

    # Make the dues rule raise ONLY for society B, leaving A healthy.
    from app.modules.notifications.services import dues_rule as dr
    real_build = dr.build_for_house

    def _explode(session, *, society_id, house_id, cfg, today, owners=None):
        if society_id == soc_b.id:
            raise RuntimeError("boom for society B")
        return real_build(
            session, society_id=society_id, house_id=house_id, cfg=cfg,
            today=today, owners=owners,
        )

    monkeypatch.setattr(dr, "build_for_house", _explode)
    _freeze_worker_date(monkeypatch, due_a)

    result = jobs.run_daily_dues_reminders()

    # Society A processed + notified; society B skipped, not aborting the run.
    assert len(db_notifications(db, society.id, user_id=owner_a.id, type_=TYPE_MAINTENANCE_DUE)) == 1
    assert len(db_notifications(db, soc_b.id, user_id=owner_b.id, type_=TYPE_MAINTENANCE_DUE)) == 0
    assert result["societies_processed"] == 1


# ---------------------------------------------------------------------------
# CRASH / IDEMPOTENT REPLAY
# ---------------------------------------------------------------------------


def test_worker_rerun_is_exactly_once(
    auth, db, society, admin_user, superadmin, monkeypatch
):
    """A same-day re-run (simulating a worker restart) creates NO new rows —
    exactly-once via the dedupe key."""
    hdr = setup_notifications(
        db, society, admin_user, superadmin, auth,
        config={"dues_advance_days": 0},
    )
    hid = owned_house_for(auth, hdr, email="replayowner@notif.local")
    _, owner = owner_login_bearer(auth, db, email="replayowner@notif.local")
    due = _owe_on(auth, hdr, hid)

    _freeze_worker_date(monkeypatch, due)
    jobs.run_daily_dues_reminders()
    first = db_notifications(db, society.id, user_id=owner.id, type_=TYPE_MAINTENANCE_DUE)
    assert len(first) == 1

    # Re-run same day → dedupe → still exactly one.
    r2 = jobs.run_daily_dues_reminders()
    second = db_notifications(db, society.id, user_id=owner.id, type_=TYPE_MAINTENANCE_DUE)
    assert len(second) == 1, "re-run must be exactly-once (dedupe)"
    assert r2["reminders_created"] == 0


# ---------------------------------------------------------------------------
# HANDLER-FAILURE CONTAINMENT
# ---------------------------------------------------------------------------


def test_bad_subscriber_does_not_break_emitter_or_real_handler(
    auth, db, society, admin_user, superadmin
):
    """A buggy extra subscriber on notice_posted raises — the publish still
    succeeds (emitter unaffected) AND the real handler still delivers to owners
    (one bad subscriber cannot stop the others)."""
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    hid = owned_house_for(auth, hdr, email="containowner@notif.local")
    _, owner = owner_login_bearer(auth, db, email="containowner@notif.local")

    def _bad_handler(payload):
        raise RuntimeError("subscriber boom")

    event_bus.subscribe("notice_posted", _bad_handler)
    try:
        resp = publish_notice_http(auth.client, hdr, title="Still delivered")
        # Emitter unaffected: the publish HTTP call succeeds.
        assert resp.status_code == 200, resp.text
    finally:
        event_bus.unsubscribe("notice_posted", _bad_handler)

    # The real handler still created the owner's notification.
    rows = db_notifications(db, society.id, user_id=owner.id, type_=TYPE_NOTICE)
    assert len(rows) == 1
