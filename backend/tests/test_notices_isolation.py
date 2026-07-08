"""Notice Board — cross-tenant isolation (code-review gate S1).

Society A can never see or act on society B's notices, attachments, reads, or
receipts, and a sequential cross-society id-guess returns 404 (no existence
leak). Every repository query is ``society_id``-scoped; these tests are the
guard-rail that a future refactor cannot silently drop that scope.
"""
from __future__ import annotations

from tests._notices_helpers import (
    add_attachment_http,
    create_notice_http,
    owned_house_for,
    owner_login_bearer,
    second_society_with_notices,
    setup_notices,
)


def _publish(auth, hdr, **kwargs):
    resp = create_notice_http(auth.client, hdr, publish=True, **kwargs)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_admin_b_cannot_read_or_act_on_society_a_notice(
    auth, db, society, admin_user, superadmin
):
    # Society A: an admin publishes a notice with an attachment.
    hdr_a = setup_notices(db, society, admin_user, superadmin, auth)
    notice_a = _publish(auth, hdr_a, title="A only", body="<p>a</p>")
    att_resp = add_attachment_http(auth.client, hdr_a, notice_a["id"])
    assert att_resp.status_code == 200, att_resp.text
    att_a_id = att_resp.json()["attachments"][0]["id"]

    # Society B: an independent admin.
    _soc_b, _admin_b, hdr_b = second_society_with_notices(db, superadmin, auth)

    nid = notice_a["id"]
    # B cannot read A's notice / receipts (cross-society id → 404, not 403).
    assert auth.client.get(f"/notices/{nid}", headers=hdr_b).status_code == 404
    assert (
        auth.client.get(f"/notices/{nid}/receipts", headers=hdr_b).status_code
        == 404
    )
    # B cannot add/remove attachments on A's notice.
    assert (
        add_attachment_http(auth.client, hdr_b, nid).status_code == 404
    )
    assert (
        auth.client.delete(
            f"/notices/{nid}/attachments/{att_a_id}", headers=hdr_b
        ).status_code
        == 404
    )
    # B cannot publish/withdraw/edit A's notice.
    assert (
        auth.client.post(f"/notices/{nid}/withdraw", headers=hdr_b).status_code
        == 404
    )
    assert (
        auth.client.patch(
            f"/notices/{nid}", headers=hdr_b, json={"title": "hijack"}
        ).status_code
        == 404
    )


def test_feed_and_archive_never_cross_societies(
    auth, db, society, admin_user, superadmin
):
    hdr_a = setup_notices(db, society, admin_user, superadmin, auth)
    a1 = _publish(auth, hdr_a, title="A active", body="<p>a1</p>")
    a2 = _publish(auth, hdr_a, title="A archived", body="<p>a2</p>")
    auth.client.post(f"/notices/{a2['id']}/withdraw", headers=hdr_a)

    _soc_b, _admin_b, hdr_b = second_society_with_notices(db, superadmin, auth)
    _publish(auth, hdr_b, title="B active", body="<p>b</p>")

    # B's active feed contains only B's notice — none of A's.
    feed_b = auth.client.get("/notices", headers=hdr_b).json()
    titles_b = {i["title"] for i in feed_b["items"]}
    assert titles_b == {"B active"}
    assert a1["id"] not in {i["id"] for i in feed_b["items"]}

    # B's archive never contains A's withdrawn notice.
    arch_b = auth.client.get("/notices/archive", headers=hdr_b).json()
    assert a2["id"] not in {i["id"] for i in arch_b["items"]}


def test_a_read_state_does_not_leak_into_b_receipts(
    auth, db, society, admin_user, superadmin
):
    # A publishes; an A owner reads it.
    hdr_a = setup_notices(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr_a, email="a-owner@x.com")
    ro_hdr, _ro = owner_login_bearer(auth, db, email="a-owner@x.com")
    notice_a = _publish(auth, hdr_a, title="A", body="<p>a</p>")
    assert (
        auth.client.get(f"/notices/{notice_a['id']}", headers=ro_hdr).status_code
        == 200
    )

    # B's receipts for its OWN notice are unaffected by A's reads/owners.
    _soc_b, _admin_b, hdr_b = second_society_with_notices(db, superadmin, auth)
    owned_house_for(auth, hdr_b, email="b-owner@x.com")
    notice_b = _publish(auth, hdr_b, title="B", body="<p>b</p>")

    rec_b = auth.client.get(
        f"/notices/{notice_b['id']}/receipts", headers=hdr_b
    ).json()
    # B's denominator counts only B's owners; A's owner never appears.
    assert rec_b["total_owners"] == 1
    assert rec_b["read_count"] == 0
    a_owner_id = _ro.id
    assert a_owner_id not in {u["user_id"] for u in rec_b["unread"]}
