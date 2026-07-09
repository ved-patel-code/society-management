"""Wave A tests — Notice Board CRUD + feed (create / edit / list / detail).

Drives ``NoticesCrudService`` through the real HTTP API (module + permission
gates, tenant context from the JWT) and asserts against the DB + ``audit_log``,
per docs/modules/notice-board.md §4/§5/§6.

The admin (``society_admin``) holds ``notices.read`` + ``notices.publish`` +
``notices.read_receipts`` so it may compose, filter, and see drafts; an owner
login is a plain reader (``notices.read`` only), the read-receipt denominator and
broadcast recipient. Expiry is query-time, so specs that exercise it freeze
``utcnow`` for determinism (``freeze_utcnow``).

Coverage:
- create: draft (invisible on the resident feed) vs publish-on-create (emits
  ``notice_posted`` once) + the ``notice.created`` audit;
- sanitization: a ``<script>``/``onclick`` payload is stripped from the stored +
  returned body, on both create and edit;
- edit: ``last_edited_at`` stamped on content change only (not pin/expiry-only),
  empty request → 422, withdrawn → 409, nonexistent → 404, explicit-null clears
  expiry while omitted keeps it, the ``notice.edited`` audit;
- feed: pinned-first then newest ordering; residents see only ACTIVE (no drafts,
  no expired, no withdrawn); admin status/scope filters + drafts visible;
  ``unread_count`` math; pagination;
- detail: marks read (``is_read`` flips, a read row appears) + emits
  ``notice.mark_read``; a resident opening a draft → 404 (no existence leak).
"""
from __future__ import annotations

import pytest

from app.modules.notices.events import EVENT_MARK_READ, EVENT_POSTED
from app.modules.notices.models import Notice, NoticeRead

from tests._notices_helpers import (
    audit_actions,
    capture_events,
    create_notice_http,
    freeze_utcnow,
    owned_house_for,
    owner_login_bearer,
    resident_bearer,
    setup_notices,
)


# ===========================================================================
# helpers
# ===========================================================================


def _create(auth, hdr, **kwargs):
    """POST /notices asserting 200; returns the created detail JSON."""
    resp = create_notice_http(auth.client, hdr, **kwargs)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _feed(auth, hdr, **params):
    """GET /notices asserting 200; returns the list envelope JSON."""
    resp = auth.client.get("/notices", headers=hdr, params=params or None)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ===========================================================================
# create
# ===========================================================================


def test_create_draft_not_on_resident_feed(auth, db, society, admin_user, superadmin):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="reader@x.com")
    r_hdr, _reader = owner_login_bearer(auth, db, email="reader@x.com")

    detail = _create(auth, hdr, title="Draft one", body="<p>secret</p>")
    assert detail["status"] == "draft"
    assert detail["published_at"] is None
    assert detail["is_read"] is False

    # A resident never sees a draft on the active feed.
    feed = _feed(auth, r_hdr)
    assert feed["items"] == []
    assert feed["total"] == 0


def test_publish_on_create_emits_posted_once(auth, db, society, admin_user, superadmin):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)

    with capture_events(EVENT_POSTED) as posted:
        detail = _create(auth, hdr, title="Big news", body="<p>hi</p>", publish=True)

    assert detail["status"] == "published"
    assert detail["published_at"] is not None
    # Emitted exactly ONCE, with the doc payload.
    assert len(posted) == 1
    name, payload = posted[0]
    assert name == EVENT_POSTED
    assert payload["notice_id"] == detail["id"]
    assert payload["society_id"] == society.id
    assert payload["title"] == "Big news"
    assert payload["published_at"] is not None


def test_create_writes_audit(auth, db, society, admin_user, superadmin):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    detail = _create(auth, hdr, title="Audited", body="<p>x</p>", publish=True)

    actions = audit_actions(db, society.id)
    assert ("notice.created", "notice", detail["id"]) in actions


def test_create_sanitizes_body(auth, db, society, admin_user, superadmin):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    payload = '<p onclick="steal()">hello</p><script>alert(1)</script>'
    detail = _create(auth, hdr, title="XSS", body=payload, publish=True)

    # The stored + returned body is already safe: no <script>, no event handler.
    assert "<script" not in detail["body"].lower()
    assert "onclick" not in detail["body"].lower()
    assert "hello" in detail["body"]

    row = db.query(Notice).filter(Notice.id == detail["id"]).one()
    assert "<script" not in row.body.lower()
    assert "onclick" not in row.body.lower()


def test_create_sanitizes_dangerous_urls_and_images(
    auth, db, society, admin_user, superadmin
):
    """A ``javascript:``/``data:`` href, an ``<img onerror>``, and a raw
    ``<iframe>`` are all stripped (spec §4 XSS policy) — locks the nh3 whitelist
    against future edits (``ALLOWED_URL_SCHEMES`` + no ``img``/``iframe``)."""
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    payload = (
        '<a href="javascript:steal()">js</a>'
        '<a href="data:text/html,evil">data</a>'
        '<a href="https://ok.example">safe</a>'
        '<img src="x" onerror="evil()">'
        "<iframe src=\"https://evil.example\"></iframe>"
        "<p>body text</p>"
    )
    detail = _create(auth, hdr, title="URLs", body=payload, publish=True)
    body = detail["body"].lower()

    # No dangerous scheme, image, iframe, or event handler survives.
    assert "javascript:" not in body
    assert "data:text/html" not in body
    assert "<img" not in body
    assert "<iframe" not in body
    assert "onerror" not in body
    # The one safe link + the text are preserved.
    assert "https://ok.example" in detail["body"]
    assert "body text" in body

    # Same guarantee at rest in the DB.
    row = db.query(Notice).filter(Notice.id == detail["id"]).one()
    rb = row.body.lower()
    assert "javascript:" not in rb and "<img" not in rb and "<iframe" not in rb


# ===========================================================================
# edit
# ===========================================================================


def test_edit_content_stamps_last_edited_at(auth, db, society, admin_user, superadmin):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    nid = _create(auth, hdr, title="v1", body="<p>a</p>", publish=True)["id"]

    resp = auth.client.patch(
        f"/notices/{nid}", headers=hdr, json={"title": "v2"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["last_edited_at"] is not None
    assert resp.json()["title"] == "v2"

    # And the edit is audited.
    assert ("notice.edited", "notice", nid) in audit_actions(db, society.id)


def test_edit_pin_or_expiry_only_does_not_stamp_last_edited_at(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    nid = _create(auth, hdr, title="Pinme", body="<p>a</p>", publish=True)["id"]

    resp = auth.client.patch(
        f"/notices/{nid}", headers=hdr, json={"is_pinned": True}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_pinned"] is True
    # Pin-only change: the content marker must NOT move.
    assert body["last_edited_at"] is None


def test_edit_empty_request_422(auth, db, society, admin_user, superadmin):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    nid = _create(auth, hdr, title="x", body="<p>a</p>", publish=True)["id"]

    resp = auth.client.patch(f"/notices/{nid}", headers=hdr, json={})
    assert resp.status_code == 422, resp.text


def test_edit_nonexistent_404(auth, db, society, admin_user, superadmin):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    resp = auth.client.patch("/notices/999999", headers=hdr, json={"title": "x"})
    assert resp.status_code == 404, resp.text


def test_edit_withdrawn_409(auth, db, society, admin_user, superadmin):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    nid = _create(auth, hdr, title="x", body="<p>a</p>", publish=True)["id"]

    # Withdraw via the lifecycle route (Wave B owns it), then edit → 409.
    wd = auth.client.post(f"/notices/{nid}/withdraw", headers=hdr)
    assert wd.status_code == 200, wd.text

    resp = auth.client.patch(f"/notices/{nid}", headers=hdr, json={"title": "y"})
    assert resp.status_code == 409, resp.text


def test_edit_expiry_explicit_null_clears_but_omitted_keeps(
    auth, db, society, admin_user, superadmin, monkeypatch
):
    freeze_utcnow(monkeypatch)
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    nid = _create(
        auth,
        hdr,
        title="Expiring",
        body="<p>a</p>",
        publish=True,
        expires_at="2099-01-01T00:00:00+00:00",
    )["id"]

    # An omitted expires_at (edit only the title) must NOT clear the expiry.
    r1 = auth.client.patch(f"/notices/{nid}", headers=hdr, json={"title": "t2"})
    assert r1.status_code == 200, r1.text
    assert r1.json()["expires_at"] is not None

    # An explicit null clears it.
    r2 = auth.client.patch(
        f"/notices/{nid}", headers=hdr, json={"expires_at": None}
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["expires_at"] is None


def test_edit_sanitizes_body(auth, db, society, admin_user, superadmin):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    nid = _create(auth, hdr, title="x", body="<p>clean</p>", publish=True)["id"]

    resp = auth.client.patch(
        f"/notices/{nid}",
        headers=hdr,
        json={"body": '<p>ok</p><script>alert(1)</script>'},
    )
    assert resp.status_code == 200, resp.text
    assert "<script" not in resp.json()["body"].lower()
    row = db.query(Notice).filter(Notice.id == nid).one()
    assert "<script" not in row.body.lower()


# ===========================================================================
# feed / list
# ===========================================================================


def test_feed_pinned_first_then_newest(auth, db, society, admin_user, superadmin):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    older = _create(auth, hdr, title="older", body="<p>a</p>", publish=True)["id"]
    newer = _create(auth, hdr, title="newer", body="<p>a</p>", publish=True)["id"]
    pinned = _create(
        auth, hdr, title="pinned", body="<p>a</p>", publish=True, is_pinned=True
    )["id"]

    feed = _feed(auth, hdr)
    ids = [i["id"] for i in feed["items"]]
    # Pinned floats to the top; the rest are newest-first.
    assert ids[0] == pinned
    assert ids.index(newer) < ids.index(older)


def test_resident_sees_only_active(
    auth, db, society, admin_user, superadmin, monkeypatch
):
    """Residents never see drafts, expired, or withdrawn notices (docs §4/§6)."""
    freeze_utcnow(monkeypatch)
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="reader@x.com")
    r_hdr, _reader = owner_login_bearer(auth, db, email="reader@x.com")

    active = _create(auth, hdr, title="active", body="<p>a</p>", publish=True)["id"]
    _draft = _create(auth, hdr, title="draft", body="<p>a</p>")["id"]
    expired = _create(
        auth,
        hdr,
        title="expired",
        body="<p>a</p>",
        publish=True,
        expires_at="2000-01-01T00:00:00+00:00",  # before FROZEN_TODAY
    )["id"]
    withdrawn = _create(auth, hdr, title="wd", body="<p>a</p>", publish=True)["id"]
    assert auth.client.post(f"/notices/{withdrawn}/withdraw", headers=hdr).status_code == 200

    feed = _feed(auth, r_hdr)
    ids = {i["id"] for i in feed["items"]}
    assert ids == {active}


def test_admin_status_and_scope_filters(
    auth, db, society, admin_user, superadmin, monkeypatch
):
    freeze_utcnow(monkeypatch)
    hdr = setup_notices(db, society, admin_user, superadmin, auth)

    draft = _create(auth, hdr, title="draft", body="<p>a</p>")["id"]
    active = _create(auth, hdr, title="active", body="<p>a</p>", publish=True)["id"]
    expired = _create(
        auth,
        hdr,
        title="expired",
        body="<p>a</p>",
        publish=True,
        expires_at="2000-01-01T00:00:00+00:00",
    )["id"]

    # Admin can filter to drafts (invisible to residents).
    draft_only = {i["id"] for i in _feed(auth, hdr, status="draft")["items"]}
    assert draft_only == {draft}

    # scope=archive shows expired (+ withdrawn), never active/draft.
    archive = {i["id"] for i in _feed(auth, hdr, scope="archive")["items"]}
    assert expired in archive
    assert active not in archive
    assert draft not in archive

    # Default admin view is the active feed.
    default = {i["id"] for i in _feed(auth, hdr)["items"]}
    assert default == {active}


def test_admin_bad_status_and_scope_422(auth, db, society, admin_user, superadmin):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    assert auth.client.get(
        "/notices", headers=hdr, params={"status": "bogus"}
    ).status_code == 422
    assert auth.client.get(
        "/notices", headers=hdr, params={"scope": "bogus"}
    ).status_code == 422


def test_unread_count_math(
    auth, db, society, admin_user, superadmin, monkeypatch
):
    freeze_utcnow(monkeypatch)
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="reader@x.com")
    r_hdr, _reader = owner_login_bearer(auth, db, email="reader@x.com")

    ids = [
        _create(auth, hdr, title=f"n{i}", body="<p>a</p>", publish=True)["id"]
        for i in range(3)
    ]

    # Reader has read nothing yet → all 3 unread.
    assert _feed(auth, r_hdr)["unread_count"] == 3

    # Open one → unread drops to 2 (independent of the current page).
    assert auth.client.get(f"/notices/{ids[0]}", headers=r_hdr).status_code == 200
    assert _feed(auth, r_hdr)["unread_count"] == 2


def test_feed_pagination(auth, db, society, admin_user, superadmin):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    for i in range(5):
        _create(auth, hdr, title=f"n{i}", body="<p>a</p>", publish=True)

    page1 = _feed(auth, hdr, page=1, page_size=2)
    assert page1["total"] == 5
    assert len(page1["items"]) == 2

    page2 = _feed(auth, hdr, page=2, page_size=2)
    assert len(page2["items"]) == 2

    # No overlap across pages (stable ordering).
    assert not ({i["id"] for i in page1["items"]} & {i["id"] for i in page2["items"]})


# ===========================================================================
# detail
# ===========================================================================


def test_get_detail_marks_read_and_emits(auth, db, society, admin_user, superadmin):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="reader@x.com")
    r_hdr, reader = owner_login_bearer(auth, db, email="reader@x.com")
    nid = _create(auth, hdr, title="open me", body="<p>a</p>", publish=True)["id"]

    # No read row yet.
    assert (
        db.query(NoticeRead)
        .filter(NoticeRead.notice_id == nid, NoticeRead.user_id == reader.id)
        .count()
        == 0
    )

    with capture_events(EVENT_MARK_READ) as marked:
        resp = auth.client.get(f"/notices/{nid}", headers=r_hdr)
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_read"] is True

    # A read row now exists (idempotent insert).
    db.expire_all()
    assert (
        db.query(NoticeRead)
        .filter(NoticeRead.notice_id == nid, NoticeRead.user_id == reader.id)
        .count()
        == 1
    )
    # Clear-on-read fired for this (user, notice).
    assert len(marked) == 1
    _name, payload = marked[0]
    # The payload also carries the emitter's ``session`` (threaded so the
    # Notifications handler writes in the emitter's transaction — atomic). Assert
    # the data keys, ignoring that transport key.
    assert {
        k: v for k, v in payload.items() if k != "session"
    } == {
        "user_id": reader.id,
        "entity_type": "notice",
        "entity_id": nid,
    }


def test_resident_opening_draft_404(auth, db, society, admin_user, superadmin):
    """A resident id-guess for a draft → 404 (no existence leak, docs §6)."""
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="reader@x.com")
    r_hdr, _reader = owner_login_bearer(auth, db, email="reader@x.com")
    draft = _create(auth, hdr, title="hidden", body="<p>a</p>")["id"]

    # The admin (manage) can open the draft...
    assert auth.client.get(f"/notices/{draft}", headers=hdr).status_code == 200
    # ...but a resident gets the same 404 as a nonexistent id.
    assert auth.client.get(f"/notices/{draft}", headers=r_hdr).status_code == 404
    assert auth.client.get("/notices/999999", headers=r_hdr).status_code == 404


def test_get_detail_read_is_idempotent(auth, db, society, admin_user, superadmin):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="reader@x.com")
    r_hdr, reader = owner_login_bearer(auth, db, email="reader@x.com")
    nid = _create(auth, hdr, title="x", body="<p>a</p>", publish=True)["id"]

    assert auth.client.get(f"/notices/{nid}", headers=r_hdr).status_code == 200
    assert auth.client.get(f"/notices/{nid}", headers=r_hdr).status_code == 200

    db.expire_all()
    assert (
        db.query(NoticeRead)
        .filter(NoticeRead.notice_id == nid, NoticeRead.user_id == reader.id)
        .count()
        == 1
    )
