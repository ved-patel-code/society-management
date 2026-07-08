"""Wave C — Notice attachment add/remove via Vault (docs/modules/notice-board.md §4/§7).

Covers the happy paths (add a PNG → it appears in detail with a signed
``preview_url``; add multiple with no cap; remove → gone + Vault doc soft-deleted),
the 404 edges (nonexistent notice / attachment), the Vault failure-injection paths
(denied type 415 + tiny-quota 413 → NO orphan ``notice_attachments`` row survives),
the already-trashed-doc removal (Vault's live-document guard → clean 404), and the
authorization gates (resident lacks ``notices.publish`` → 403; vault-off society →
add route 403). Audit rows are asserted for the added/removed actions.

Uses the in-memory storage override (like the complaints robustness suite) so the
Vault put/delete is deterministic and never touches real MinIO.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.modules.notices.models import NoticeAttachment
from app.modules.vault.models import VaultDocument

from tests._notices_helpers import (
    EXE_BYTES,
    PNG_BYTES,
    add_attachment_http,
    admin_bearer,
    audit_actions,
    create_notice_http,
    enable_notices,
    owned_house_for,
    owner_login_bearer,
    setup_notices,
    society_with_tiny_quota as _society_with_tiny_quota,
)
from tests._vault_helpers import storage_override  # noqa: F401  (fixture)

pytestmark = pytest.mark.usefixtures("storage_override")


# ---------------------------------------------------------------------------
# small local helpers
# ---------------------------------------------------------------------------


def _published_notice_id(auth, hdr) -> int:
    resp = create_notice_http(auth.client, hdr, title="With files", publish=True)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _get_detail(auth, hdr, notice_id) -> dict:
    resp = auth.client.get(f"/notices/{notice_id}", headers=hdr)
    assert resp.status_code == 200, resp.text
    return resp.json()


# NOTE: ``_society_with_tiny_quota`` has been promoted to
# ``tests._notices_helpers.society_with_tiny_quota`` (imported above, aliased to
# this historical local name) per the Phase-3 gate matrix — kept as a thin alias
# so this file's existing call sites are undisturbed.


# ---------------------------------------------------------------------------
# happy paths
# ---------------------------------------------------------------------------


def test_add_attachment_appears_in_detail_with_preview_url(
    db, superadmin, auth, society, admin_user
):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    nid = _published_notice_id(auth, hdr)

    resp = add_attachment_http(auth.client, hdr, nid, filename="flyer.png")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == nid
    assert len(body["attachments"]) == 1
    att = body["attachments"][0]
    assert att["vault_document_id"] > 0
    assert att["preview_url"]  # a signed inline URL was produced
    assert att["download_url"]

    # Re-fetching detail also shows the attachment.
    detail = _get_detail(auth, hdr, nid)
    assert len(detail["attachments"]) == 1

    # The Vault document was written under source='notice', source_ref=notice.
    doc = db.execute(
        select(VaultDocument).where(VaultDocument.source_ref == nid)
    ).scalar_one()
    assert doc.source == "notice"
    assert doc.deleted_at is None

    assert ("notice.attachment_added", "notice", nid) in audit_actions(
        db, society.id
    )


def test_add_multiple_attachments_no_cap(
    db, superadmin, auth, society, admin_user
):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    nid = _published_notice_id(auth, hdr)

    for i in range(5):
        resp = add_attachment_http(auth.client, hdr, nid, filename=f"f{i}.png")
        assert resp.status_code == 200, resp.text

    detail = _get_detail(auth, hdr, nid)
    assert len(detail["attachments"]) == 5

    rows = db.execute(
        select(NoticeAttachment).where(NoticeAttachment.notice_id == nid)
    ).scalars().all()
    assert len(rows) == 5


def test_remove_attachment_gone_from_detail_and_vault_soft_deleted(
    db, superadmin, auth, society, admin_user
):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    nid = _published_notice_id(auth, hdr)

    add_resp = add_attachment_http(auth.client, hdr, nid, filename="doc.png")
    assert add_resp.status_code == 200, add_resp.text
    att = add_resp.json()["attachments"][0]
    att_id = att["id"]
    doc_id = att["vault_document_id"]

    del_resp = auth.client.delete(
        f"/notices/{nid}/attachments/{att_id}", headers=hdr
    )
    assert del_resp.status_code == 204, del_resp.text

    # Gone from detail.
    detail = _get_detail(auth, hdr, nid)
    assert detail["attachments"] == []

    # Local link row dropped.
    rows = db.execute(
        select(NoticeAttachment).where(NoticeAttachment.id == att_id)
    ).scalars().all()
    assert rows == []

    # Vault document soft-deleted (moved to Trash), NOT hard-deleted.
    doc = db.execute(
        select(VaultDocument).where(VaultDocument.id == doc_id)
    ).scalar_one()
    assert doc.deleted_at is not None

    actions = audit_actions(db, society.id)
    assert ("notice.attachment_removed", "notice", nid) in actions


# ---------------------------------------------------------------------------
# 404 edges
# ---------------------------------------------------------------------------


def test_add_to_nonexistent_notice_404(
    db, superadmin, auth, society, admin_user
):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    resp = add_attachment_http(auth.client, hdr, 999999, filename="x.png")
    assert resp.status_code == 404, resp.text


def test_remove_nonexistent_attachment_404(
    db, superadmin, auth, society, admin_user
):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    nid = _published_notice_id(auth, hdr)
    resp = auth.client.delete(
        f"/notices/{nid}/attachments/999999", headers=hdr
    )
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Vault failure injection — no orphan row survives a rejected add
# ---------------------------------------------------------------------------


def test_add_denied_type_415_no_orphan_row(
    db, superadmin, auth, society, admin_user
):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    nid = _published_notice_id(auth, hdr)

    resp = add_attachment_http(
        auth.client,
        hdr,
        nid,
        data=EXE_BYTES,
        filename="malware.exe",
        content_type="application/octet-stream",
    )
    assert resp.status_code == 415, resp.text

    rows = db.execute(
        select(NoticeAttachment).where(NoticeAttachment.notice_id == nid)
    ).scalars().all()
    assert rows == []
    docs = db.execute(
        select(VaultDocument).where(VaultDocument.source_ref == nid)
    ).scalars().all()
    assert docs == []
    # A rejected add was NOT audited (the whole txn rolled back).
    assert ("notice.attachment_added", "notice", nid) not in audit_actions(
        db, society.id
    )


def test_add_quota_413_no_orphan_row(db, superadmin, auth):
    soc, _admin, hdr = _society_with_tiny_quota(
        db, superadmin, auth, limit_bytes=8
    )
    nid = _published_notice_id(auth, hdr)

    resp = add_attachment_http(auth.client, hdr, nid, filename="big.png")
    assert resp.status_code == 413, resp.text

    rows = db.execute(
        select(NoticeAttachment).where(NoticeAttachment.notice_id == nid)
    ).scalars().all()
    assert rows == []
    docs = db.execute(
        select(VaultDocument).where(VaultDocument.source_ref == nid)
    ).scalars().all()
    assert docs == []


# ---------------------------------------------------------------------------
# already-trashed Vault doc → clean 404 on removal
# ---------------------------------------------------------------------------


def test_remove_when_vault_doc_already_trashed_404(
    db, superadmin, auth, society, admin_user
):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    nid = _published_notice_id(auth, hdr)

    add_resp = add_attachment_http(auth.client, hdr, nid, filename="doc.png")
    att = add_resp.json()["attachments"][0]
    att_id = att["id"]
    doc_id = att["vault_document_id"]

    # Trash the backing Vault document out-of-band (simulate a prior Vault delete).
    from tests._complaints_helpers import trash_vault_document

    trash_vault_document(db, doc_id)

    # Vault's live-document guard rejects a second soft-delete → clean 404, and
    # the whole removal rolls back (the local link row must survive).
    resp = auth.client.delete(
        f"/notices/{nid}/attachments/{att_id}", headers=hdr
    )
    assert resp.status_code == 404, resp.text

    rows = db.execute(
        select(NoticeAttachment).where(NoticeAttachment.id == att_id)
    ).scalars().all()
    assert len(rows) == 1  # rolled back — no orphan drop


# ---------------------------------------------------------------------------
# authorization gates
# ---------------------------------------------------------------------------


def test_resident_cannot_add_or_remove_403(
    db, superadmin, auth, society, admin_user
):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    nid = _published_notice_id(auth, hdr)

    # A published attachment to try to remove as a resident.
    add_resp = add_attachment_http(auth.client, hdr, nid, filename="doc.png")
    att_id = add_resp.json()["attachments"][0]["id"]

    # A provisioned owner (resident) — holds notices.read, not notices.publish.
    owned_house_for(auth, hdr, email="resident@notices.local")
    r_hdr, _user = owner_login_bearer(auth, db, email="resident@notices.local")

    add_403 = add_attachment_http(auth.client, r_hdr, nid, filename="x.png")
    assert add_403.status_code == 403, add_403.text

    del_403 = auth.client.delete(
        f"/notices/{nid}/attachments/{att_id}", headers=r_hdr
    )
    assert del_403.status_code == 403, del_403.text


def test_vault_off_society_add_route_403(
    db, superadmin, auth, society, admin_user
):
    # notices enabled but vault module OFF → attachment routes gate 403.
    enable_notices(db, society, superadmin, with_vault=False)
    hdr = admin_bearer(auth, admin_user)
    nid = _published_notice_id(auth, hdr)

    resp = add_attachment_http(auth.client, hdr, nid, filename="x.png")
    assert resp.status_code == 403, resp.text

    del_resp = auth.client.delete(
        f"/notices/{nid}/attachments/1", headers=hdr
    )
    assert del_resp.status_code == 403, del_resp.text
