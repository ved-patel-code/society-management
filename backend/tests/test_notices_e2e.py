"""End-to-end lifecycle tests for Notice Board (Module 6) — the full-gate arc.

Drives a notice from draft through the whole legal state machine to archive via
the REAL HTTP API, asserting DB state, the ordered audit trail, the Vault
attachment folder placement, read-receipts, emitted events, and the pin/expiry
feed-position rules at each step. Complements (does not duplicate) the
per-wave basic-case files.
"""
from __future__ import annotations

from datetime import timedelta

from app.modules.notices.events import EVENT_MARK_READ, EVENT_POSTED
from app.modules.notices.models import NoticeRead
from app.modules.vault.models import VaultDocument

import pytest

from tests._notices_helpers import (
    add_attachment_http,
    audit_actions,
    capture_events,
    create_notice_http,
    frozen_today,
    owned_house_for,
    owner_login_bearer,
    setup_notices,
)
from tests._vault_helpers import storage_override  # noqa: F401  (fixture)

pytestmark = pytest.mark.usefixtures("storage_override")


def _trail(db, society_id, nid):
    return [
        a for (a, et, eid) in audit_actions(db, society_id)
        if et == "notice" and eid == nid
    ]


def test_full_notice_journey_draft_to_archive(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)

    # Three owners — the read-receipt denominator + broadcast audience.
    emails = ["o1@x.com", "o2@x.com", "o3@x.com"]
    from tests._houses_helpers import _make_building_with_houses, _set_status

    floors = [{"level": 1, "houses_count": 3}]
    houses = _make_building_with_houses(auth, hdr, floors=floors)
    for house, email in zip(houses, emails):
        resp = _set_status(
            auth, hdr, house["id"], "owned",
            {"full_name": f"Owner {email}", "email": email,
             "contact_number": "555-0001", "persons_living": 2},
        )
        assert resp.status_code == 200, resp.text
    owner_hdrs = [owner_login_bearer(auth, db, email=e) for e in emails]

    # --- create as DRAFT ---
    created = create_notice_http(auth.client, hdr, title="AGM", body="<p>agenda</p>")
    assert created.status_code == 200, created.text
    detail = created.json()
    nid = detail["id"]
    assert detail["status"] == "draft"
    # A resident does not see the draft on the feed.
    feed_reader = auth.client.get("/notices", headers=owner_hdrs[0][0]).json()
    assert nid not in {i["id"] for i in feed_reader["items"]}

    # --- edit title+body ---
    edit_resp = auth.client.patch(
        f"/notices/{nid}", headers=hdr, json={"title": "AGM 2026", "body": "<p>new agenda</p>"}
    )
    assert edit_resp.status_code == 200, edit_resp.text
    assert edit_resp.json()["last_edited_at"] is not None

    # --- publish, capturing exactly one EVENT_POSTED ---
    with capture_events(EVENT_POSTED) as posted:
        pub_resp = auth.client.post(f"/notices/{nid}/publish", headers=hdr)
    assert pub_resp.status_code == 200, pub_resp.text
    assert len(posted) == 1
    _name, payload = posted[0]
    assert payload["notice_id"] == nid
    assert payload["society_id"] == society.id
    assert payload["title"] == "AGM 2026"
    assert payload["published_at"] is not None

    # --- pin + set a future expiry (meta-only edit: last_edited_at unchanged) ---
    last_edited_before = pub_resp.json()["last_edited_at"]
    pin_resp = auth.client.patch(
        f"/notices/{nid}",
        headers=hdr,
        json={"is_pinned": True, "expires_at": "2099-01-01T00:00:00+00:00"},
    )
    assert pin_resp.status_code == 200, pin_resp.text
    assert pin_resp.json()["is_pinned"] is True
    assert pin_resp.json()["last_edited_at"] == last_edited_before

    # --- add an attachment ---
    att_resp = add_attachment_http(auth.client, hdr, nid, filename="flyer.png")
    assert att_resp.status_code == 200, att_resp.text
    att = att_resp.json()["attachments"][0]
    doc = db.query(VaultDocument).filter(
        VaultDocument.id == att["vault_document_id"]
    ).one()
    assert doc.source == "notice"
    assert doc.source_ref == nid

    # --- two owners open the detail; the third does not ---
    r1 = auth.client.get(f"/notices/{nid}", headers=owner_hdrs[0][0])
    assert r1.status_code == 200 and r1.json()["is_read"] is True
    r2 = auth.client.get(f"/notices/{nid}", headers=owner_hdrs[1][0])
    assert r2.status_code == 200 and r2.json()["is_read"] is True

    receipts = auth.client.get(f"/notices/{nid}/receipts", headers=hdr).json()
    assert receipts["read_count"] == 2
    assert receipts["unread_count"] == 1

    # --- withdraw ---
    wd_resp = auth.client.post(f"/notices/{nid}/withdraw", headers=hdr)
    assert wd_resp.status_code == 200, wd_resp.text

    archive = auth.client.get("/notices/archive", headers=hdr).json()
    assert nid in {i["id"] for i in archive["items"]}
    active = auth.client.get("/notices", headers=hdr).json()
    assert nid not in {i["id"] for i in active["items"]}


def test_e2e_ordered_audit_trail(auth, db, society, admin_user, superadmin):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    created = create_notice_http(auth.client, hdr, title="x", body="<p>a</p>")
    nid = created.json()["id"]

    auth.client.patch(f"/notices/{nid}", headers=hdr, json={"title": "y"})
    auth.client.post(f"/notices/{nid}/publish", headers=hdr)
    add_attachment_http(auth.client, hdr, nid, filename="a.png")
    auth.client.post(f"/notices/{nid}/withdraw", headers=hdr)

    assert _trail(db, society.id, nid) == [
        "notice.created",
        "notice.edited",
        "notice.published",
        "notice.attachment_added",
        "notice.withdrawn",
    ]


def test_e2e_mark_read_event_on_open(auth, db, society, admin_user, superadmin):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="reader@x.com")
    r_hdr, reader = owner_login_bearer(auth, db, email="reader@x.com")
    nid = create_notice_http(
        auth.client, hdr, title="x", body="<p>a</p>", publish=True
    ).json()["id"]

    with capture_events(EVENT_MARK_READ) as marked:
        resp = auth.client.get(f"/notices/{nid}", headers=r_hdr)
    assert resp.status_code == 200, resp.text
    assert len(marked) == 1
    assert marked[0][1] == {
        "user_id": reader.id,
        "entity_type": "notice",
        "entity_id": nid,
    }

    # Second open: the DB row count stays 1 (idempotent insert), but the event
    # DOES fire again (the code emits mark_read_for on EVERY open — real
    # behavior, no dedup guard in NoticesCrudService.get_detail).
    with capture_events(EVENT_MARK_READ) as marked2:
        resp2 = auth.client.get(f"/notices/{nid}", headers=r_hdr)
    assert resp2.status_code == 200, resp2.text
    assert len(marked2) == 1  # fires again

    db.expire_all()
    count = (
        db.query(NoticeRead)
        .filter(NoticeRead.notice_id == nid, NoticeRead.user_id == reader.id)
        .count()
    )
    assert count == 1


def test_e2e_pin_and_expiry_feed_position(auth, db, society, admin_user, superadmin):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)

    oldest = create_notice_http(
        auth.client, hdr, title="oldest", body="<p>a</p>", publish=True
    ).json()["id"]
    older_unpinned = create_notice_http(
        auth.client, hdr, title="older", body="<p>a</p>", publish=True
    ).json()["id"]
    newest_unpinned = create_notice_http(
        auth.client, hdr, title="newest", body="<p>a</p>", publish=True
    ).json()["id"]

    pin_resp = auth.client.patch(
        f"/notices/{oldest}", headers=hdr, json={"is_pinned": True}
    )
    assert pin_resp.status_code == 200, pin_resp.text

    feed = auth.client.get("/notices", headers=hdr).json()
    ids = [i["id"] for i in feed["items"]]
    assert ids == [oldest, newest_unpinned, older_unpinned]

    # Set a future expiry, then flip to a past instant -> drops to archive.
    future = auth.client.patch(
        f"/notices/{oldest}", headers=hdr, json={"expires_at": "2099-01-01T00:00:00+00:00"}
    )
    assert future.status_code == 200, future.text
    still_active = auth.client.get("/notices", headers=hdr).json()
    assert oldest in {i["id"] for i in still_active["items"]}

    past = auth.client.patch(
        f"/notices/{oldest}", headers=hdr, json={"expires_at": "2000-01-01T00:00:00+00:00"}
    )
    assert past.status_code == 200, past.text
    after = auth.client.get("/notices", headers=hdr).json()
    assert oldest not in {i["id"] for i in after["items"]}
    archive = auth.client.get("/notices/archive", headers=hdr).json()
    assert oldest in {i["id"] for i in archive["items"]}


def test_e2e_multiple_owners_receipts_reflect_reads(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    from tests._houses_helpers import _make_building_with_houses, _set_status

    emails = ["p1@x.com", "p2@x.com", "p3@x.com"]
    floors = [{"level": 1, "houses_count": 3}]
    houses = _make_building_with_houses(auth, hdr, floors=floors)
    for house, email in zip(houses, emails):
        _set_status(
            auth, hdr, house["id"], "owned",
            {"full_name": f"Owner {email}", "email": email,
             "contact_number": "555-0001", "persons_living": 2},
        )
    owner_bearers = [owner_login_bearer(auth, db, email=e) for e in emails]

    nid = create_notice_http(
        auth.client, hdr, title="x", body="<p>a</p>", publish=True
    ).json()["id"]

    auth.client.get(f"/notices/{nid}", headers=owner_bearers[0][0])
    auth.client.get(f"/notices/{nid}", headers=owner_bearers[1][0])
    auth.client.post("/notices/read-all", headers=owner_bearers[2][0])

    receipts = auth.client.get(f"/notices/{nid}/receipts", headers=hdr).json()
    assert receipts["read_count"] == 3
    assert receipts["unread_count"] == 0
    read_ids = [u["user_id"] for u in receipts["read"]]
    assert read_ids == sorted(read_ids)


def test_e2e_attachment_vault_folder_path(auth, db, society, admin_user, superadmin):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    nid = create_notice_http(
        auth.client, hdr, title="x", body="<p>a</p>", publish=True
    ).json()["id"]

    a1 = add_attachment_http(auth.client, hdr, nid, filename="one.png")
    a2 = add_attachment_http(auth.client, hdr, nid, filename="two.png")
    assert a1.status_code == 200 and a2.status_code == 200

    att1 = a1.json()["attachments"][0]
    att2 = a2.json()["attachments"][-1]

    doc1 = db.query(VaultDocument).filter(VaultDocument.id == att1["vault_document_id"]).one()
    doc2 = db.query(VaultDocument).filter(VaultDocument.id == att2["vault_document_id"]).one()
    assert doc1.folder_id == doc2.folder_id
    assert doc1.source == "notice" and doc1.source_ref == nid
    assert doc2.source == "notice" and doc2.source_ref == nid
    assert doc1.deleted_at is None and doc2.deleted_at is None

    detail_before = auth.client.get(f"/notices/{nid}", headers=hdr).json()
    assert len(detail_before["attachments"]) == 2

    att1_id = [a["id"] for a in detail_before["attachments"] if a["vault_document_id"] == doc1.id][0]
    del_resp = auth.client.delete(
        f"/notices/{nid}/attachments/{att1_id}", headers=hdr
    )
    assert del_resp.status_code == 204, del_resp.text

    db.expire_all()
    doc1_after = db.query(VaultDocument).filter(VaultDocument.id == doc1.id).one()
    doc2_after = db.query(VaultDocument).filter(VaultDocument.id == doc2.id).one()
    assert doc1_after.deleted_at is not None
    assert doc2_after.deleted_at is None

    detail_after = auth.client.get(f"/notices/{nid}", headers=hdr).json()
    assert len(detail_after["attachments"]) == 1


def test_e2e_text_only_notice_vault_disabled(auth, db, society, admin_user, superadmin):
    from tests._notices_helpers import enable_notices, admin_bearer

    enable_notices(db, society, superadmin, with_vault=False)
    hdr = admin_bearer(auth, admin_user)

    created = create_notice_http(auth.client, hdr, title="Text", body="<p>a</p>")
    assert created.status_code == 200, created.text
    nid = created.json()["id"]

    edit = auth.client.patch(f"/notices/{nid}", headers=hdr, json={"title": "Text 2"})
    assert edit.status_code == 200, edit.text

    pub = auth.client.post(f"/notices/{nid}/publish", headers=hdr)
    assert pub.status_code == 200, pub.text

    att = add_attachment_http(auth.client, hdr, nid, filename="x.png")
    assert att.status_code == 403, att.text

    receipts = auth.client.get(f"/notices/{nid}/receipts", headers=hdr)
    assert receipts.status_code == 200, receipts.text

    feed = auth.client.get("/notices", headers=hdr).json()
    assert nid in {i["id"] for i in feed["items"]}

    read_all = auth.client.post("/notices/read-all", headers=hdr)
    assert read_all.status_code == 204, read_all.text

    wd = auth.client.post(f"/notices/{nid}/withdraw", headers=hdr)
    assert wd.status_code == 200, wd.text
    archive = auth.client.get("/notices/archive", headers=hdr).json()
    assert nid in {i["id"] for i in archive["items"]}


def test_e2e_published_then_expired_openable_by_id_off_feed(
    auth, db, society, admin_user, superadmin, monkeypatch
):
    with frozen_today():
        hdr = setup_notices(db, society, admin_user, superadmin, auth)
        owned_house_for(auth, hdr, email="reader2@x.com")
        r_hdr, _reader = owner_login_bearer(auth, db, email="reader2@x.com")

        # A frozen "now" (FROZEN_TODAY midnight UTC); expires just past that.
        just_past = "2026-07-08T00:00:01+00:00"
        created = create_notice_http(
            auth.client, hdr, title="x", body="<p>a</p>", publish=True,
            expires_at=just_past,
        )
        assert created.status_code == 200, created.text
        # NOTE: frozen "now" is exactly midnight; expires_at (00:00:01) is AFTER
        # now at creation time, so use a moment already <= frozen now instead to
        # guarantee expiry under the frozen clock.
        nid = created.json()["id"]

        # Force expiry via edit (explicit expires_at <= frozen now).
        past = auth.client.patch(
            f"/notices/{nid}", headers=hdr, json={"expires_at": "2020-01-01T00:00:00+00:00"}
        )
        assert past.status_code == 200, past.text

        # Not on the resident feed / unread count.
        feed = auth.client.get("/notices", headers=r_hdr).json()
        assert nid not in {i["id"] for i in feed["items"]}
        assert feed["unread_count"] == 0

        # But GET by id still 200s (an expired notice remains openable by link).
        detail = auth.client.get(f"/notices/{nid}", headers=r_hdr)
        assert detail.status_code == 200, detail.text

        # Admin archive contains it.
        archive = auth.client.get("/notices/archive", headers=hdr).json()
        assert nid in {i["id"] for i in archive["items"]}
