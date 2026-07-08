"""Edge-case tests for Notice Board (Module 6): status-transition boundaries,
exact expiry-instant semantics (``is_expired`` uses ``<=``), pin ordering with
distinct ``published_at`` values, unread-count math across read/read-all/new
publishes, feed pagination, and empty-state responses.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from tests._notices_helpers import (
    create_notice_http,
    freeze_utcnow,
    owned_house_for,
    owner_login_bearer,
    setup_notices,
)


def _create(auth, hdr, **kw):
    resp = create_notice_http(auth.client, hdr, **kw)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ===========================================================================
# transition matrix
# ===========================================================================


def test_transition_matrix_from_draft(auth, db, society, admin_user, superadmin):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)

    d1 = _create(auth, hdr, title="d1", body="<p>a</p>")["id"]
    pub = auth.client.post(f"/notices/{d1}/publish", headers=hdr)
    assert pub.status_code == 200, pub.text

    d2 = _create(auth, hdr, title="d2", body="<p>a</p>")["id"]
    wd = auth.client.post(f"/notices/{d2}/withdraw", headers=hdr)
    assert wd.status_code == 200, wd.text
    assert wd.json()["status"] == "withdrawn"


def test_withdraw_from_published_then_publish_409(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    nid = _create(auth, hdr, title="x", body="<p>a</p>", publish=True)["id"]

    wd = auth.client.post(f"/notices/{nid}/withdraw", headers=hdr)
    assert wd.status_code == 200, wd.text

    republish = auth.client.post(f"/notices/{nid}/publish", headers=hdr)
    assert republish.status_code == 409, republish.text


def test_edit_published_notice_still_editable(auth, db, society, admin_user, superadmin):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    nid = _create(auth, hdr, title="x", body="<p>a</p>", publish=True)["id"]

    resp = auth.client.patch(
        f"/notices/{nid}", headers=hdr, json={"body": "<p>updated</p>"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["last_edited_at"] is not None
    assert "updated" in resp.json()["body"]


# ===========================================================================
# exact expiry-instant semantics
# ===========================================================================


def test_expiry_exactly_equal_now_is_expired(
    auth, db, society, admin_user, superadmin, monkeypatch
):
    """is_expired uses ``expires_at <= now`` — an expiry exactly at the frozen
    instant IS expired (off the feed, in the archive)."""
    freeze_utcnow(monkeypatch)
    hdr = setup_notices(db, society, admin_user, superadmin, auth)

    frozen_iso = "2026-07-08T00:00:00+00:00"
    nid = _create(
        auth, hdr, title="x", body="<p>a</p>", publish=True, expires_at=frozen_iso
    )["id"]

    feed = auth.client.get("/notices", headers=hdr).json()
    assert nid not in {i["id"] for i in feed["items"]}
    archive = auth.client.get("/notices/archive", headers=hdr).json()
    assert nid in {i["id"] for i in archive["items"]}


def test_expiry_just_before_now_expired_off_feed(
    auth, db, society, admin_user, superadmin, monkeypatch
):
    freeze_utcnow(monkeypatch)
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="reader@x.com")
    r_hdr, _reader = owner_login_bearer(auth, db, email="reader@x.com")

    just_before = "2026-07-07T23:59:59+00:00"
    nid = _create(
        auth, hdr, title="x", body="<p>a</p>", publish=True, expires_at=just_before
    )["id"]

    feed = auth.client.get("/notices", headers=r_hdr).json()
    assert nid not in {i["id"] for i in feed["items"]}
    assert feed["unread_count"] == 0
    archive = auth.client.get("/notices/archive", headers=hdr).json()
    assert nid in {i["id"] for i in archive["items"]}


def test_expiry_just_after_now_active_on_feed(
    auth, db, society, admin_user, superadmin, monkeypatch
):
    freeze_utcnow(monkeypatch)
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="reader2@x.com")
    r_hdr, _reader = owner_login_bearer(auth, db, email="reader2@x.com")

    just_after = "2026-07-08T00:00:01+00:00"
    nid = _create(
        auth, hdr, title="x", body="<p>a</p>", publish=True, expires_at=just_after
    )["id"]

    feed = auth.client.get("/notices", headers=r_hdr).json()
    assert nid in {i["id"] for i in feed["items"]}
    assert feed["unread_count"] == 1
    archive = auth.client.get("/notices/archive", headers=hdr).json()
    assert nid not in {i["id"] for i in archive["items"]}


# ===========================================================================
# pin ordering with distinct published_at (real wall-clock, no freeze)
# ===========================================================================


def test_multiple_pins_ordered_by_published_at_desc(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)

    first = _create(auth, hdr, title="p1", body="<p>a</p>", publish=True)["id"]
    time.sleep(0.05)
    second = _create(auth, hdr, title="p2", body="<p>a</p>", publish=True)["id"]
    time.sleep(0.05)
    third = _create(auth, hdr, title="p3", body="<p>a</p>", publish=True)["id"]

    for nid in (first, second, third):
        resp = auth.client.patch(f"/notices/{nid}", headers=hdr, json={"is_pinned": True})
        assert resp.status_code == 200, resp.text

    unpinned = _create(auth, hdr, title="u1", body="<p>a</p>", publish=True)["id"]

    feed = auth.client.get("/notices", headers=hdr).json()
    ids = [i["id"] for i in feed["items"]]
    # All 3 pins come first (desc published_at), then the unpinned one.
    assert ids[:3] == [third, second, first]
    assert ids[3] == unpinned


# ===========================================================================
# unread-count math across read / read-all / new publishes
# ===========================================================================


def test_unread_count_across_read_readall_and_new_publish(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="reader3@x.com")
    r_hdr, _reader = owner_login_bearer(auth, db, email="reader3@x.com")

    n1 = _create(auth, hdr, title="n1", body="<p>a</p>", publish=True)["id"]
    n2 = _create(auth, hdr, title="n2", body="<p>a</p>", publish=True)["id"]
    assert auth.client.get("/notices", headers=r_hdr).json()["unread_count"] == 2

    auth.client.get(f"/notices/{n1}", headers=r_hdr)
    assert auth.client.get("/notices", headers=r_hdr).json()["unread_count"] == 1

    _create(auth, hdr, title="n3", body="<p>a</p>", publish=True)
    assert auth.client.get("/notices", headers=r_hdr).json()["unread_count"] == 2

    assert auth.client.post("/notices/read-all", headers=r_hdr).status_code == 204
    assert auth.client.get("/notices", headers=r_hdr).json()["unread_count"] == 0

    _create(auth, hdr, title="n4", body="<p>a</p>", publish=True)
    assert auth.client.get("/notices", headers=r_hdr).json()["unread_count"] == 1


def test_owner_provisioned_after_publish_counts_unread_in_feed(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    _create(auth, hdr, title="x", body="<p>a</p>", publish=True)["id"]

    owned_house_for(auth, hdr, email="late@x.com")
    r_hdr, _reader = owner_login_bearer(auth, db, email="late@x.com")

    feed = auth.client.get("/notices", headers=r_hdr).json()
    assert feed["unread_count"] >= 1
    assert any(i["is_read"] is False for i in feed["items"])


# ===========================================================================
# empty state
# ===========================================================================


def test_empty_feed_and_zero_unread(auth, db, society, admin_user, superadmin):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="empty@x.com")
    r_hdr, _reader = owner_login_bearer(auth, db, email="empty@x.com")

    feed = auth.client.get("/notices", headers=r_hdr).json()
    assert feed["items"] == []
    assert feed["total"] == 0
    assert feed["unread_count"] == 0


# ===========================================================================
# pagination
# ===========================================================================


def test_pagination_total_vs_page_and_no_overlap(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    for i in range(7):
        _create(auth, hdr, title=f"n{i}", body="<p>a</p>", publish=True)

    seen_ids: list[int] = []
    for page, expected_count in ((1, 3), (2, 3), (3, 1)):
        resp = auth.client.get(
            "/notices", headers=hdr, params={"page": page, "page_size": 3}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 7
        assert len(body["items"]) == expected_count
        seen_ids.extend(i["id"] for i in body["items"])

    assert len(seen_ids) == len(set(seen_ids)) == 7
