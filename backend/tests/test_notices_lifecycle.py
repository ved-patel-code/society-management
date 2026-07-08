"""Lifecycle tests for the Notice Board (Module 6, Wave B): publish + withdraw.

Covers the two explicit status-edge endpoints (``POST /notices/{id}/publish`` and
``POST /notices/{id}/withdraw``) end-to-end over HTTP:
- publish a draft → published, ``published_at`` stamped, ``notice_posted`` emitted
  ONCE with the doc-specified payload, audit row present;
- the illegal publish edges (already-published / withdrawn) → 409, missing → 404;
- withdraw a draft (discard) and a published notice (soft-delete: ``withdrawn_at``/
  ``withdrawn_by`` set, gone from the active feed), double-withdraw → 409,
  missing → 404;
- the audit trail via ``audit_actions``.

The active feed is exercised through Wave A's ``GET /notices`` (a published notice
appears; a withdrawn one does not) so withdrawal is observed at the API boundary.
"""
from __future__ import annotations

from tests._notices_helpers import (
    audit_actions,
    capture_events,
    create_notice_http,
    setup_notices,
)
from app.modules.notices.events import EVENT_POSTED


def _draft(client, hdr, **kw):
    """Create a draft notice over HTTP; return its parsed body."""
    resp = create_notice_http(client, hdr, publish=False, **kw)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _feed_ids(client, hdr) -> set[int]:
    """The ids currently on the caller's active feed (``GET /notices``)."""
    resp = client.get("/notices", headers=hdr)
    assert resp.status_code == 200, resp.text
    return {item["id"] for item in resp.json()["items"]}


# =========================================================================
# publish
# =========================================================================


def test_publish_draft_sets_published_and_emits_once(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    notice = _draft(auth.client, hdr, title="AGM Notice", body="<p>agenda</p>")
    assert notice["status"] == "draft"
    assert notice["published_at"] is None

    with capture_events(EVENT_POSTED) as captured:
        resp = auth.client.post(f"/notices/{notice['id']}/publish", headers=hdr)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "published"
    assert body["published_at"] is not None

    # Exactly ONE notice_posted, with the doc-specified payload (§7).
    assert len(captured) == 1, captured
    name, payload = captured[0]
    assert name == EVENT_POSTED
    assert payload["notice_id"] == notice["id"]
    assert payload["society_id"] == society.id
    assert payload["title"] == "AGM Notice"
    assert payload["published_at"] is not None

    # A notice.published audit row landed for this notice.
    assert ("notice.published", "notice", notice["id"]) in audit_actions(
        db, society.id
    )


def test_publish_shows_notice_on_active_feed(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    notice = _draft(auth.client, hdr, title="On Feed")

    # A draft is NOT on the (admin) active feed; publishing puts it there.
    assert notice["id"] not in _feed_ids(auth.client, hdr)
    resp = auth.client.post(f"/notices/{notice['id']}/publish", headers=hdr)
    assert resp.status_code == 200, resp.text
    assert notice["id"] in _feed_ids(auth.client, hdr)


def test_publish_already_published_409(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    resp = create_notice_http(auth.client, hdr, publish=True)
    assert resp.status_code == 200, resp.text
    nid = resp.json()["id"]
    assert resp.json()["status"] == "published"

    # Re-publishing an already-published notice is an illegal edge → 409, and
    # emits NO second notice_posted.
    with capture_events(EVENT_POSTED) as captured:
        again = auth.client.post(f"/notices/{nid}/publish", headers=hdr)
    assert again.status_code == 409, again.text
    assert captured == []


def test_publish_withdrawn_409(auth, db, society, admin_user, superadmin):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    notice = _draft(auth.client, hdr)
    assert (
        auth.client.post(
            f"/notices/{notice['id']}/withdraw", headers=hdr
        ).status_code
        == 200
    )

    # withdrawn is terminal — publishing it is a 409.
    resp = auth.client.post(f"/notices/{notice['id']}/publish", headers=hdr)
    assert resp.status_code == 409, resp.text


def test_publish_nonexistent_404(auth, db, society, admin_user, superadmin):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    resp = auth.client.post("/notices/999999/publish", headers=hdr)
    assert resp.status_code == 404, resp.text


def test_publish_emits_exactly_one_event(
    auth, db, society, admin_user, superadmin
):
    """Publish fires notice_posted ONCE (not twice) — apply_publish is the sole
    emit site and the lifecycle service does not re-emit."""
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    notice = _draft(auth.client, hdr)
    with capture_events(EVENT_POSTED) as captured:
        resp = auth.client.post(f"/notices/{notice['id']}/publish", headers=hdr)
    assert resp.status_code == 200, resp.text
    assert len(captured) == 1, captured


# =========================================================================
# withdraw
# =========================================================================


def test_withdraw_draft_discards_it(auth, db, society, admin_user, superadmin):
    """Withdraw is legal from ``draft`` (discarding a draft, docs §3)."""
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    notice = _draft(auth.client, hdr)

    resp = auth.client.post(f"/notices/{notice['id']}/withdraw", headers=hdr)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "withdrawn"
    assert body["withdrawn_at"] is not None
    assert body["withdrawn_by"] == admin_user.id
    # A discarded draft never had a published_at.
    assert body["published_at"] is None

    assert ("notice.withdrawn", "notice", notice["id"]) in audit_actions(
        db, society.id
    )


def test_withdraw_published_soft_deletes_and_leaves_feed(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    resp = create_notice_http(auth.client, hdr, publish=True, title="Live")
    assert resp.status_code == 200, resp.text
    nid = resp.json()["id"]
    assert nid in _feed_ids(auth.client, hdr)

    withdrawn = auth.client.post(f"/notices/{nid}/withdraw", headers=hdr)
    assert withdrawn.status_code == 200, withdrawn.text
    body = withdrawn.json()
    assert body["status"] == "withdrawn"
    assert body["withdrawn_at"] is not None
    assert body["withdrawn_by"] == admin_user.id
    # The published_at stamp is retained (soft-delete, not a hard reset).
    assert body["published_at"] is not None

    # Gone from the active feed (residents AND admin).
    assert nid not in _feed_ids(auth.client, hdr)


def test_double_withdraw_409(auth, db, society, admin_user, superadmin):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    notice = _draft(auth.client, hdr)
    assert (
        auth.client.post(
            f"/notices/{notice['id']}/withdraw", headers=hdr
        ).status_code
        == 200
    )

    # withdrawn is terminal — a second withdraw is a 409.
    again = auth.client.post(f"/notices/{notice['id']}/withdraw", headers=hdr)
    assert again.status_code == 409, again.text


def test_withdraw_nonexistent_404(auth, db, society, admin_user, superadmin):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    resp = auth.client.post("/notices/999999/withdraw", headers=hdr)
    assert resp.status_code == 404, resp.text


def test_lifecycle_audit_trail(auth, db, society, admin_user, superadmin):
    """A create → publish → withdraw run leaves the expected audit actions in
    order (created, published, withdrawn) for the one notice."""
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    notice = _draft(auth.client, hdr)
    nid = notice["id"]
    assert (
        auth.client.post(f"/notices/{nid}/publish", headers=hdr).status_code
        == 200
    )
    assert (
        auth.client.post(f"/notices/{nid}/withdraw", headers=hdr).status_code
        == 200
    )

    actions = [
        a for (a, et, eid) in audit_actions(db, society.id)
        if et == "notice" and eid == nid
    ]
    assert actions == ["notice.created", "notice.published", "notice.withdrawn"]
