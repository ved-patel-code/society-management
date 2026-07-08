"""Wave D tests for the Notice Board (Module 6): read-all + receipts + archive.

Exercises the three admin-visibility / read-state endpoints end-to-end over HTTP:
- ``POST /notices/read-all`` — idempotently marks every ACTIVE notice read for the
  caller; the unread badge (via ``GET /notices``) drops to zero.
- ``GET /notices/{id}/receipts`` — read vs unread against the CURRENT-owner
  denominator: initially all owners unread; one owner opens the notice → that owner
  reads, the rest unread; an owner provisioned AFTER publish counts as unread; a
  reader who is no longer a current owner is NOT in the denominator; nonexistent
  notice → 404.
- ``GET /notices/archive`` — expired (published + past ``expires_at``) + withdrawn
  notices appear; an active published notice does not.
- Both admin endpoints are gated ``notices.read_receipts`` — a plain resident is 403.

Owners are provisioned as real logins (``owned_house_for`` + ``owner_login_bearer``)
because they are the receipt denominator + the broadcast audience. ``freeze_utcnow``
pins expiry so the archive/active split is deterministic.
"""
from __future__ import annotations

import pytest

from tests._houses_helpers import _make_building_with_houses, _set_status
from tests._notices_helpers import (
    FROZEN_TODAY,
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


def _publish(client, hdr, **kw) -> int:
    """Publish a notice over HTTP; return its id."""
    resp = create_notice_http(client, hdr, publish=True, **kw)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _owners_on_building(auth, hdr, *, emails):
    """Move one building's houses to ``owned`` with the given owner emails.

    Builds a single building 'A' with as many houses as there are ``emails`` and
    moves each house to ``owned`` under a distinct owner login. Returns the list
    of house ids (parallel to ``emails``). Each email becomes a provisioned owner
    → a current-owner denominator entry + a broadcast recipient.
    """
    floors = [{"level": 1, "houses_count": len(emails)}]
    houses = _make_building_with_houses(auth, hdr, floors=floors)
    hids = []
    for house, email in zip(houses, emails):
        owner = {
            "full_name": f"Owner {email}",
            "email": email,
            "contact_number": "555-0001",
            "persons_living": 2,
        }
        resp = _set_status(auth, hdr, house["id"], "owned", owner)
        assert resp.status_code == 200, resp.text
        hids.append(house["id"])
    return hids


def _receipts(auth, hdr, notice_id):
    resp = auth.client.get(f"/notices/{notice_id}/receipts", headers=hdr)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _unread_count(auth, hdr) -> int:
    resp = auth.client.get("/notices", headers=hdr)
    assert resp.status_code == 200, resp.text
    return resp.json()["unread_count"]


# ===========================================================================
# receipts — denominator = current owners
# ===========================================================================


def test_receipts_all_owners_unread_initially(
    auth, db, society, admin_user, superadmin
):
    """A freshly published notice: every current owner is unread, read_count=0."""
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    emails = ["o1@x.com", "o2@x.com", "o3@x.com"]
    _owners_on_building(auth, hdr, emails=emails)
    owner_ids = {
        owner_login_bearer(auth, db, email=e)[1].id for e in emails
    }

    nid = _publish(auth.client, hdr, title="AGM")
    body = _receipts(auth, hdr, nid)

    assert body["notice_id"] == nid
    assert body["total_owners"] == 3
    assert body["read_count"] == 0
    assert body["unread_count"] == 3
    assert body["read"] == []
    assert {u["user_id"] for u in body["unread"]} == owner_ids
    # Unread entries carry no read_at.
    assert all(u["read_at"] is None for u in body["unread"])


def test_receipts_one_owner_opens_shows_read(
    auth, db, society, admin_user, superadmin
):
    """After one owner opens the notice, only they read; the rest stay unread."""
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    emails = ["o1@x.com", "o2@x.com", "o3@x.com"]
    _owners_on_building(auth, hdr, emails=emails)

    nid = _publish(auth.client, hdr, title="AGM")

    r1_hdr, owner1 = owner_login_bearer(auth, db, email="o1@x.com")
    # Opening the notice inserts the read row (idempotent, docs §4).
    assert auth.client.get(f"/notices/{nid}", headers=r1_hdr).status_code == 200

    body = _receipts(auth, hdr, nid)
    assert body["read_count"] == 1
    assert body["unread_count"] == 2
    assert [u["user_id"] for u in body["read"]] == [owner1.id]
    assert body["read"][0]["read_at"] is not None
    assert owner1.id not in {u["user_id"] for u in body["unread"]}


def test_receipts_lists_sorted_by_user_id(
    auth, db, society, admin_user, superadmin
):
    """Both lists are ordered by user_id for a deterministic response."""
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    emails = ["o1@x.com", "o2@x.com", "o3@x.com"]
    _owners_on_building(auth, hdr, emails=emails)
    nid = _publish(auth.client, hdr, title="AGM")

    unread_ids = [u["user_id"] for u in _receipts(auth, hdr, nid)["unread"]]
    assert unread_ids == sorted(unread_ids)


def test_receipts_owner_provisioned_after_publish_is_unread(
    auth, db, society, admin_user, superadmin
):
    """An owner onboarded AFTER a notice was posted is a current owner → counts
    as unread (whole-society broadcast, not a frozen snapshot, docs §4)."""
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    # One owner on house[0] of a two-house building; the second is added later.
    floors = [{"level": 1, "houses_count": 2}]
    houses = _make_building_with_houses(auth, hdr, floors=floors)
    _set_status(
        auth, hdr, houses[0]["id"], "owned",
        {"full_name": "Early", "email": "early@x.com",
         "contact_number": "555-0001", "persons_living": 2},
    )

    nid = _publish(auth.client, hdr, title="AGM")
    before = _receipts(auth, hdr, nid)
    assert before["total_owners"] == 1
    assert before["unread_count"] == 1

    # Provision the second owner AFTER publish.
    _set_status(
        auth, hdr, houses[1]["id"], "owned",
        {"full_name": "Late", "email": "late@x.com",
         "contact_number": "555-0002", "persons_living": 2},
    )
    _late_hdr, late = owner_login_bearer(auth, db, email="late@x.com")

    after = _receipts(auth, hdr, nid)
    assert after["total_owners"] == 2
    assert after["unread_count"] == 2
    assert late.id in {u["user_id"] for u in after["unread"]}
    assert late.id not in {u["user_id"] for u in after["read"]}


def test_receipts_excludes_reader_who_is_no_longer_owner(
    auth, db, society, admin_user, superadmin
):
    """A reader who has since been replaced as owner is NOT in the denominator —
    the denominator is CURRENT owners (docs §4). Their read row is ignored."""
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    floors = [{"level": 1, "houses_count": 1}]
    houses = _make_building_with_houses(auth, hdr, floors=floors)
    hid = houses[0]["id"]
    _set_status(
        auth, hdr, hid, "owned",
        {"full_name": "First", "email": "first@x.com",
         "contact_number": "555-0001", "persons_living": 2},
    )

    nid = _publish(auth.client, hdr, title="AGM")
    r_hdr, first = owner_login_bearer(auth, db, email="first@x.com")
    # The first owner reads the notice, then is replaced.
    assert auth.client.get(f"/notices/{nid}", headers=r_hdr).status_code == 200

    # Owner replacement (different email → old owner loses current ownership).
    _set_status(
        auth, hdr, hid, "owned",
        {"full_name": "Second", "email": "second@x.com",
         "contact_number": "555-0002", "persons_living": 2},
    )
    _s_hdr, second = owner_login_bearer(auth, db, email="second@x.com")

    body = _receipts(auth, hdr, nid)
    # Only the current owner counts; the former owner (a reader) is gone.
    assert body["total_owners"] == 1
    assert {u["user_id"] for u in body["read"] + body["unread"]} == {second.id}
    assert first.id not in {
        u["user_id"] for u in body["read"] + body["unread"]
    }
    # The current owner has not read → unread.
    assert body["read_count"] == 0
    assert body["unread_count"] == 1


def test_receipts_nonexistent_notice_404(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    resp = auth.client.get("/notices/999999/receipts", headers=hdr)
    assert resp.status_code == 404, resp.text


# ===========================================================================
# read-all
# ===========================================================================


def test_read_all_marks_all_active_and_drops_unread(
    auth, db, society, admin_user, superadmin
):
    """read-all covers every active notice for the caller → their unread badge
    drops to zero (observed via ``GET /notices``)."""
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    _owners_on_building(auth, hdr, emails=["o1@x.com"])
    r_hdr, _owner = owner_login_bearer(auth, db, email="o1@x.com")

    _publish(auth.client, hdr, title="One")
    _publish(auth.client, hdr, title="Two")
    _publish(auth.client, hdr, title="Three")

    # The owner has opened nothing yet — three unread.
    assert _unread_count(auth, r_hdr) == 3

    resp = auth.client.post("/notices/read-all", headers=r_hdr)
    assert resp.status_code == 204, resp.text
    assert _unread_count(auth, r_hdr) == 0


def test_read_all_reflected_in_receipts(
    auth, db, society, admin_user, superadmin
):
    """After an owner read-alls, receipts show them as read on each notice."""
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    _owners_on_building(auth, hdr, emails=["o1@x.com", "o2@x.com"])
    r_hdr, owner1 = owner_login_bearer(auth, db, email="o1@x.com")

    nid = _publish(auth.client, hdr, title="One")
    auth.client.post("/notices/read-all", headers=r_hdr)

    body = _receipts(auth, hdr, nid)
    assert owner1.id in {u["user_id"] for u in body["read"]}


def test_read_all_is_idempotent(auth, db, society, admin_user, superadmin):
    """Calling read-all twice is a harmless no-op (ON CONFLICT DO NOTHING)."""
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    _owners_on_building(auth, hdr, emails=["o1@x.com"])
    r_hdr, _owner = owner_login_bearer(auth, db, email="o1@x.com")
    _publish(auth.client, hdr, title="One")

    assert auth.client.post("/notices/read-all", headers=r_hdr).status_code == 204
    # A second call does not error and leaves unread at zero.
    assert auth.client.post("/notices/read-all", headers=r_hdr).status_code == 204
    assert _unread_count(auth, r_hdr) == 0


def test_read_all_no_active_notices_ok(
    auth, db, society, admin_user, superadmin
):
    """read-all with nothing active is a clean no-op (empty active-ids set)."""
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    _owners_on_building(auth, hdr, emails=["o1@x.com"])
    r_hdr, _owner = owner_login_bearer(auth, db, email="o1@x.com")
    assert auth.client.post("/notices/read-all", headers=r_hdr).status_code == 204


# ===========================================================================
# archive — expired + withdrawn
# ===========================================================================


def test_archive_contains_withdrawn_and_expired_not_active(
    auth, db, society, admin_user, superadmin, monkeypatch
):
    """The archive holds a withdrawn notice + an expired one, and excludes an
    active published notice (docs §6)."""
    freeze_utcnow(monkeypatch)
    hdr = setup_notices(db, society, admin_user, superadmin, auth)

    # An active published notice (no expiry) — stays OFF the archive.
    active_id = _publish(auth.client, hdr, title="Active")

    # A published notice that has already expired (expires_at before frozen now).
    expired_id = _publish(
        auth.client, hdr, title="Expired", expires_at="2020-01-01T00:00:00Z"
    )

    # A published notice we then withdraw.
    withdrawn_id = _publish(auth.client, hdr, title="ToWithdraw")
    assert (
        auth.client.post(
            f"/notices/{withdrawn_id}/withdraw", headers=hdr
        ).status_code
        == 200
    )

    resp = auth.client.get("/notices/archive", headers=hdr)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    archive_ids = {item["id"] for item in payload["items"]}

    assert expired_id in archive_ids
    assert withdrawn_id in archive_ids
    assert active_id not in archive_ids
    assert payload["total"] == 2
    # is_read is not meaningful in the archive; unread_count is zeroed.
    assert payload["unread_count"] == 0
    assert all(item["is_read"] is False for item in payload["items"])


def test_archive_empty_when_all_active(
    auth, db, society, admin_user, superadmin, monkeypatch
):
    freeze_utcnow(monkeypatch)
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    _publish(auth.client, hdr, title="Active")
    resp = auth.client.get("/notices/archive", headers=hdr)
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"] == []
    assert resp.json()["total"] == 0


# ===========================================================================
# security — notices.read_receipts gate
# ===========================================================================


def test_resident_cannot_read_receipts(
    auth, db, society, admin_user, resident_user, superadmin
):
    """A plain resident holds ``notices.read`` but NOT ``notices.read_receipts`` →
    403 on receipts."""
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    nid = _publish(auth.client, hdr, title="AGM")
    res_hdr = resident_bearer(auth, resident_user)

    resp = auth.client.get(f"/notices/{nid}/receipts", headers=res_hdr)
    assert resp.status_code == 403, resp.text


def test_resident_cannot_read_archive(
    auth, db, society, admin_user, resident_user, superadmin
):
    """Residents have no archive — active feed only (docs §6) → 403."""
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    res_hdr = resident_bearer(auth, resident_user)
    resp = auth.client.get("/notices/archive", headers=res_hdr)
    assert resp.status_code == 403, resp.text


def test_resident_can_read_all(
    auth, db, society, admin_user, resident_user, superadmin
):
    """read-all is gated ``notices.read`` (not read_receipts) — a resident can
    call it (204), confirming the gate split."""
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    res_hdr = resident_bearer(auth, resident_user)
    resp = auth.client.post("/notices/read-all", headers=res_hdr)
    assert resp.status_code == 204, resp.text
