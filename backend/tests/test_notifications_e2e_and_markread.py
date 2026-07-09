"""Clear-on-read (§4.4) + full lifecycle e2e for the Notifications engine.

Asserts the two clear paths and the end-to-end flows:
- clear-on-read via the SOURCE item: opening the complaint/notice clears the
  opener's pending notifications for that entity (``mark_read_for`` hook).
- direct read: ``POST /notifications/{id}/read`` clears one row independently.
- full e2e: publish notice → owner feed shows it → open clears it; raise
  complaint → admin feed shows it → resolve → owner gets complaint_update.
- reopen edge: a LATER status change is a NEW notification even after the
  earlier one was read.
"""
from __future__ import annotations

from app.modules.notifications.schemas import (
    TYPE_COMPLAINT_NEW,
    TYPE_COMPLAINT_UPDATE,
    TYPE_NOTICE,
)

from tests._notifications_helpers import (
    db_notifications,
    first_category_id,
    get_feed,
    get_unread_count,
    owned_house_for,
    owner_login_bearer,
    publish_notice_http,
    raise_complaint_http,
    setup_notifications,
)


def _feed_ids(client, hdr) -> set[int]:
    return {item["id"] for item in get_feed(client, hdr)["items"]}


# ===========================================================================
# clear-on-read via the source item (mark_read_for)
# ===========================================================================


def test_admin_open_complaint_clears_complaint_new(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="owner-m1@notif.local")
    o_hdr, _owner = owner_login_bearer(auth, db, email="owner-m1@notif.local")
    cat = first_category_id(auth.client, o_hdr)

    cid = raise_complaint_http(auth.client, o_hdr, category_id=cat).json()["id"]

    # admin has a pending complaint_new for this complaint
    rows = db_notifications(
        db, society.id, user_id=admin_user.id, type_=TYPE_COMPLAINT_NEW
    )
    assert len(rows) == 1 and rows[0].read_at is None
    notif_id = rows[0].id
    assert notif_id in _feed_ids(auth.client, hdr)

    # admin OPENS the complaint → clear-on-read fires
    resp = auth.client.get(f"/complaints/{cid}", headers=hdr)
    assert resp.status_code == 200, resp.text

    db.expire_all()
    cleared = db_notifications(
        db, society.id, user_id=admin_user.id, type_=TYPE_COMPLAINT_NEW
    )[0]
    assert cleared.read_at is not None, "opening the complaint must clear the alert"
    assert notif_id not in _feed_ids(auth.client, hdr)


def test_owner_open_complaint_clears_complaint_update(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="owner-m2@notif.local")
    o_hdr, owner = owner_login_bearer(auth, db, email="owner-m2@notif.local")
    cat = first_category_id(auth.client, o_hdr)

    cid = raise_complaint_http(auth.client, o_hdr, category_id=cat).json()["id"]
    auth.client.post(
        f"/complaints/{cid}/status", headers=hdr, json={"to_status": "in_progress"}
    )

    rows = db_notifications(
        db, society.id, user_id=owner.id, type_=TYPE_COMPLAINT_UPDATE
    )
    assert len(rows) == 1 and rows[0].read_at is None

    # owner opens their complaint → complaint_update cleared
    resp = auth.client.get(f"/complaints/{cid}", headers=o_hdr)
    assert resp.status_code == 200, resp.text

    db.expire_all()
    assert (
        db_notifications(
            db, society.id, user_id=owner.id, type_=TYPE_COMPLAINT_UPDATE
        )[0].read_at
        is not None
    )


def test_owner_open_notice_clears_notice(auth, db, society, admin_user, superadmin):
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="owner-m3@notif.local")
    o_hdr, owner = owner_login_bearer(auth, db, email="owner-m3@notif.local")

    notice_id = publish_notice_http(auth.client, hdr, title="Read me").json()["id"]

    rows = db_notifications(db, society.id, user_id=owner.id, type_=TYPE_NOTICE)
    assert len(rows) == 1 and rows[0].read_at is None

    # owner opens the notice → the notice notification is cleared
    resp = auth.client.get(f"/notices/{notice_id}", headers=o_hdr)
    assert resp.status_code == 200, resp.text

    db.expire_all()
    assert (
        db_notifications(db, society.id, user_id=owner.id, type_=TYPE_NOTICE)[0].read_at
        is not None
    )


# ===========================================================================
# direct read via POST /notifications/{id}/read
# ===========================================================================


def test_direct_read_clears_independently(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="owner-d1@notif.local")
    o_hdr, owner = owner_login_bearer(auth, db, email="owner-d1@notif.local")

    notice_id = publish_notice_http(auth.client, hdr, title="Direct").json()["id"]
    row = db_notifications(db, society.id, user_id=owner.id, type_=TYPE_NOTICE)[0]

    assert get_unread_count(auth.client, o_hdr) == 1
    resp = auth.client.post(f"/notifications/{row.id}/read", headers=o_hdr)
    assert resp.status_code == 200, resp.text

    db.expire_all()
    assert db_notifications(db, society.id, user_id=owner.id, type_=TYPE_NOTICE)[
        0
    ].read_at is not None
    assert get_unread_count(auth.client, o_hdr) == 0
    assert row.id not in _feed_ids(auth.client, o_hdr)


# ===========================================================================
# full e2e lifecycles
# ===========================================================================


def test_e2e_notice_publish_open_empties_feed(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="owner-e1@notif.local")
    o_hdr, _owner = owner_login_bearer(auth, db, email="owner-e1@notif.local")

    notice_id = publish_notice_http(auth.client, hdr, title="AGM").json()["id"]

    # owner feed shows the notice
    feed = get_feed(auth.client, o_hdr)
    assert feed["unread_count"] == 1
    assert any(i["entity_id"] == notice_id for i in feed["items"])

    # owner opens the notice → feed empties
    assert auth.client.get(f"/notices/{notice_id}", headers=o_hdr).status_code == 200
    feed2 = get_feed(auth.client, o_hdr)
    assert feed2["unread_count"] == 0
    assert feed2["items"] == []


def test_e2e_complaint_raise_admin_feed_resolve_owner_update(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="owner-e2@notif.local")
    o_hdr, owner = owner_login_bearer(auth, db, email="owner-e2@notif.local")
    cat = first_category_id(auth.client, o_hdr)

    cid = raise_complaint_http(auth.client, o_hdr, category_id=cat).json()["id"]

    # admin feed shows the new complaint alert
    admin_feed = get_feed(auth.client, hdr)
    assert any(
        i["type"] == TYPE_COMPLAINT_NEW and i["entity_id"] == cid
        for i in admin_feed["items"]
    )

    # admin progresses + resolves → owner gets complaint_update
    auth.client.post(
        f"/complaints/{cid}/status", headers=hdr, json={"to_status": "in_progress"}
    )
    assert (
        auth.client.post(
            f"/complaints/{cid}/resolve", headers=hdr, data={"note": "done"}
        ).status_code
        == 200
    )

    updates = db_notifications(
        db, society.id, user_id=owner.id, type_=TYPE_COMPLAINT_UPDATE
    )
    assert len(updates) == 2
    assert updates[-1].payload["to_status"] == "resolved"
    # they appear in the owner's live feed
    owner_feed = get_feed(auth.client, o_hdr)
    assert any(i["type"] == TYPE_COMPLAINT_UPDATE for i in owner_feed["items"])


# ===========================================================================
# reopen edge: a later event creates a NEW notification even after the
# earlier one was read
# ===========================================================================


def test_new_status_change_after_read_creates_new_notification(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="owner-r1@notif.local")
    o_hdr, owner = owner_login_bearer(auth, db, email="owner-r1@notif.local")
    cat = first_category_id(auth.client, o_hdr)

    cid = raise_complaint_http(auth.client, o_hdr, category_id=cat).json()["id"]

    # first transition → complaint_update #1
    auth.client.post(
        f"/complaints/{cid}/status", headers=hdr, json={"to_status": "in_progress"}
    )
    first = db_notifications(
        db, society.id, user_id=owner.id, type_=TYPE_COMPLAINT_UPDATE
    )
    assert len(first) == 1

    # owner reads it (opens the complaint → clears)
    auth.client.get(f"/complaints/{cid}", headers=o_hdr)
    db.expire_all()
    assert db_notifications(
        db, society.id, user_id=owner.id, type_=TYPE_COMPLAINT_UPDATE
    )[0].read_at is not None

    # a NEW status change (resolve) → a brand-new complaint_update row (unread)
    assert (
        auth.client.post(
            f"/complaints/{cid}/resolve", headers=hdr, data={"note": "fixed"}
        ).status_code
        == 200
    )
    db.expire_all()
    rows = db_notifications(
        db, society.id, user_id=owner.id, type_=TYPE_COMPLAINT_UPDATE
    )
    assert len(rows) == 2, "a later event is a new notification even if earlier read"
    assert rows[0].read_at is not None  # the first, already read
    assert rows[1].read_at is None  # the new one, unread
    assert rows[1].payload["to_status"] == "resolved"
