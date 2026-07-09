"""Feed / badge / mark-read tests for Notifications (Module 7).

Drives the real engine end-to-end: notifications are NEVER created by a public
POST — they arrive only via domain events (docs §4.2/§6). So this suite fills
feeds the real way:

- an OWNER raising a complaint over HTTP → ``complaint_new`` lands in the
  society ADMINs' feeds (recipients = holders of ``complaints.read_all``), so
  the *admin* bearer reads its own feed;
- the ADMIN publishing a notice over HTTP → ``notice`` fans out to every current
  OWNER, so the *owner* bearer reads its own feed.

The handlers run in their OWN committed session (docs §4.2), so the rows are
visible to a subsequent HTTP read. Every assertion is ``== X, resp.text`` house
style; the harness truncates+reseeds per test so each is independent.

Coverage (per the task brief + docs §6):
- feed lists only UNREAD, newest first; ``unread_count`` is the TOTAL, page-
  independent (built >page_size deep, paginated);
- ``unread-count`` matches the feed's count;
- ``POST /{id}/read`` clears one (cleared=1), drops it from the feed, decrements
  the count; re-reading the same id is an idempotent no-op (cleared=0, 200, NOT
  404);
- ``POST /{id}/read`` on another user's / a nonexistent id → 404;
- ``read-all`` clears everything (feed empties, count 0); read-all with nothing
  unread → cleared=0.
"""
from __future__ import annotations

from tests._houses_helpers import _make_building_with_houses, _set_status
from tests._notifications_helpers import (
    admin_bearer,
    first_category_id,
    get_feed,
    get_unread_count,
    owned_house_for,
    owner_login_bearer,
    publish_notice_http,
    raise_complaint_http,
    resident_bearer,
    setup_notifications,
)


# ===========================================================================
# helpers — fill a caller's OWN feed the real (event-driven) way
# ===========================================================================


def _owner(auth, db, admin_hdr, *, email):
    """Provision an owner login on one owned house; return (bearer, user)."""
    owned_house_for(auth, admin_hdr, email=email)
    return owner_login_bearer(auth, db, email=email)


def _fill_admin_feed_via_complaints(auth, db, admin_hdr, *, n, email="owner@x.com"):
    """Have an owner raise ``n`` complaints → ``n`` ``complaint_new`` rows in the
    admin's own feed. Returns the owner bearer (for later interactions)."""
    o_hdr, _owner_u = _owner(auth, db, admin_hdr, email=email)
    cat = first_category_id(auth.client, o_hdr)
    for i in range(n):
        resp = raise_complaint_http(
            auth.client, o_hdr, category_id=cat, title=f"Leak {i}"
        )
        assert resp.status_code == 200, resp.text
    return o_hdr


def _owners_for_notice(auth, db, admin_hdr, *, email="owner@x.com"):
    """One owned house tied to an owner login; the owner is a notice recipient."""
    return _owner(auth, db, admin_hdr, email=email)


# ===========================================================================
# feed: unread only, newest first, page-independent count
# ===========================================================================


def test_feed_lists_only_unread_newest_first(auth, db, society, admin_user, superadmin):
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    o_hdr = _fill_admin_feed_via_complaints(auth, db, hdr, n=3)

    feed = get_feed(auth.client, hdr)
    assert feed["unread_count"] == 3
    assert len(feed["items"]) == 3
    # Newest first: ids strictly descending (created_at desc, id desc).
    ids = [it["id"] for it in feed["items"]]
    assert ids == sorted(ids, reverse=True)
    # All are complaint_new alerts for the admin.
    assert {it["type"] for it in feed["items"]} == {"complaint_new"}
    for it in feed["items"]:
        assert it["entity_type"] == "complaint"
        assert it["entity_id"] is not None
        assert it["title"] == "New complaint raised"

    # Clearing one drops it from the unread feed (no read rows ever listed).
    first_id = ids[0]
    resp = auth.client.post(f"/notifications/{first_id}/read", headers=hdr)
    assert resp.status_code == 200, resp.text
    feed2 = get_feed(auth.client, hdr)
    assert first_id not in [it["id"] for it in feed2["items"]]
    assert len(feed2["items"]) == 2
    assert o_hdr  # silence unused


def test_unread_count_is_total_independent_of_page(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    # More than one page worth (default page_size=20): raise 22 complaints.
    _fill_admin_feed_via_complaints(auth, db, hdr, n=22)

    # Page 1 (default size 20): 20 items, but the badge is the full 22.
    page1 = get_feed(auth.client, hdr)
    assert page1["unread_count"] == 22
    assert page1["page"] == 1
    assert page1["page_size"] == 20
    assert len(page1["items"]) == 20

    # Page 2: the remaining 2, count STILL 22 (page-independent).
    resp = auth.client.get(
        "/notifications", headers=hdr, params={"page": 2, "page_size": 20}
    )
    assert resp.status_code == 200, resp.text
    page2 = resp.json()
    assert page2["unread_count"] == 22
    assert page2["page"] == 2
    assert len(page2["items"]) == 2

    # No id appears on both pages (clean pagination).
    ids1 = {it["id"] for it in page1["items"]}
    ids2 = {it["id"] for it in page2["items"]}
    assert ids1.isdisjoint(ids2)
    # And page 2's ids are all smaller (older) than page 1's minimum.
    assert max(ids2) < min(ids1)


def test_unread_count_endpoint_matches_feed(auth, db, society, admin_user, superadmin):
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    _fill_admin_feed_via_complaints(auth, db, hdr, n=4)

    feed = get_feed(auth.client, hdr)
    badge = get_unread_count(auth.client, hdr)
    assert badge == feed["unread_count"] == 4


# ===========================================================================
# mark one read
# ===========================================================================


def test_mark_one_read_clears_and_decrements(auth, db, society, admin_user, superadmin):
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    _fill_admin_feed_via_complaints(auth, db, hdr, n=3)

    feed = get_feed(auth.client, hdr)
    target = feed["items"][0]["id"]
    assert get_unread_count(auth.client, hdr) == 3

    resp = auth.client.post(f"/notifications/{target}/read", headers=hdr)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"cleared": 1}

    # Removed from the feed, count decremented.
    feed2 = get_feed(auth.client, hdr)
    assert target not in [it["id"] for it in feed2["items"]]
    assert feed2["unread_count"] == 2
    assert get_unread_count(auth.client, hdr) == 2


def test_mark_one_read_idempotent_noop(auth, db, society, admin_user, superadmin):
    """Re-reading an already-read OWN notification is a 200 no-op (cleared=0),
    never a 404 (docs §6)."""
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    _fill_admin_feed_via_complaints(auth, db, hdr, n=1)
    target = get_feed(auth.client, hdr)["items"][0]["id"]

    first = auth.client.post(f"/notifications/{target}/read", headers=hdr)
    assert first.status_code == 200, first.text
    assert first.json() == {"cleared": 1}

    second = auth.client.post(f"/notifications/{target}/read", headers=hdr)
    assert second.status_code == 200, second.text
    assert second.json() == {"cleared": 0}

    assert get_unread_count(auth.client, hdr) == 0


def test_mark_read_nonexistent_id_404(auth, db, society, admin_user, superadmin):
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    _fill_admin_feed_via_complaints(auth, db, hdr, n=1)

    resp = auth.client.post("/notifications/999999/read", headers=hdr)
    assert resp.status_code == 404, resp.text


def test_mark_read_not_owned_by_caller_404(auth, db, society, admin_user, superadmin):
    """A notification belonging to another user in the SAME society is a 404 for
    the caller (own-only; no cross-user leak) — NOT a 403."""
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    # Owner's own feed: publish a notice → the owner gets a ``notice`` row.
    o_hdr, _owner_u = _owners_for_notice(auth, db, hdr, email="owner@x.com")
    resp = publish_notice_http(auth.client, hdr, title="AGM")
    assert resp.status_code == 200, resp.text

    owner_feed = get_feed(auth.client, o_hdr)
    assert owner_feed["unread_count"] == 1
    owner_notif_id = owner_feed["items"][0]["id"]

    # The ADMIN (a different user) cannot mark the OWNER's notification read.
    bad = auth.client.post(f"/notifications/{owner_notif_id}/read", headers=hdr)
    assert bad.status_code == 404, bad.text

    # The owner's row is still unread (the admin's attempt did nothing).
    assert get_unread_count(auth.client, o_hdr) == 1


# ===========================================================================
# read-all
# ===========================================================================


def test_read_all_clears_everything(auth, db, society, admin_user, superadmin):
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    _fill_admin_feed_via_complaints(auth, db, hdr, n=5)
    assert get_unread_count(auth.client, hdr) == 5

    resp = auth.client.post("/notifications/read-all", headers=hdr)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"cleared": 5}

    feed = get_feed(auth.client, hdr)
    assert feed["items"] == []
    assert feed["unread_count"] == 0
    assert get_unread_count(auth.client, hdr) == 0


def test_read_all_when_nothing_unread_is_noop(auth, db, society, admin_user, superadmin):
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    # No events fired → empty feed.
    assert get_unread_count(auth.client, hdr) == 0

    resp = auth.client.post("/notifications/read-all", headers=hdr)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"cleared": 0}


def test_read_all_only_affects_own_feed(auth, db, society, admin_user, superadmin):
    """read-all clears the caller's rows only — another user's feed is untouched."""
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    # Admin feed via complaints (owner raises); owner feed via a notice.
    o_hdr = _fill_admin_feed_via_complaints(auth, db, hdr, n=2, email="owner@x.com")
    resp = publish_notice_http(auth.client, hdr, title="AGM")
    assert resp.status_code == 200, resp.text
    assert get_unread_count(auth.client, o_hdr) == 1
    assert get_unread_count(auth.client, hdr) == 2

    # Admin clears its own; the owner's notice survives.
    cleared = auth.client.post("/notifications/read-all", headers=hdr)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json() == {"cleared": 2}
    assert get_unread_count(auth.client, hdr) == 0
    assert get_unread_count(auth.client, o_hdr) == 1


def test_notice_fanout_to_multiple_owners(auth, db, society, admin_user, superadmin):
    """One published notice fans out one row per current owner (docs §4.2). Each
    owner sees exactly one ``notice`` in their OWN feed."""
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    for i, house in enumerate(houses[:2]):
        owner = {
            "full_name": f"Owner {i}",
            "email": f"owner{i}@x.com",
            "contact_number": "555-0001",
            "persons_living": 2,
        }
        assert _set_status(auth, hdr, house["id"], "owned", owner).status_code == 200
    o0_hdr, _u0 = owner_login_bearer(auth, db, email="owner0@x.com")
    o1_hdr, _u1 = owner_login_bearer(auth, db, email="owner1@x.com")

    resp = publish_notice_http(auth.client, hdr, title="Water cut")
    assert resp.status_code == 200, resp.text

    for o_hdr in (o0_hdr, o1_hdr):
        feed = get_feed(auth.client, o_hdr)
        assert feed["unread_count"] == 1
        assert feed["items"][0]["type"] == "notice"
        assert feed["items"][0]["entity_type"] == "notice"
        assert feed["items"][0]["title"] == "New notice"
