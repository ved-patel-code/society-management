"""Robustness / failure-injection tests for Complaints (Module 5).

Covers Vault-forced 413 (tiny quota) / 415 (denied extension) on both the
report-image and resolve-with-proof paths, confirming no orphan rows/state
survive a rejected write; malformed create payloads (422); 404s across every
resource-scoped route; and double-withdraw / double-resolve conflict handling.
"""
from __future__ import annotations

from sqlalchemy import select

from app.modules.complaints.models import Complaint, ComplaintImage
from app.modules.vault.models import VaultDocument

from tests._complaints_helpers import (
    EXE_BYTES,
    PNG_BYTES,
    owned_house_for,
    owner_login_bearer,
    raise_complaint,
    resolve_http,
    setup_complaints,
    society_with_tiny_quota,
)
from tests._vault_helpers import storage_override  # noqa: F401  (fixture)

import pytest

pytestmark = pytest.mark.usefixtures("storage_override")


def _category_id(auth, hdr, name="Plumbing") -> int:
    resp = auth.client.get("/complaints/categories", headers=hdr)
    assert resp.status_code == 200, resp.text
    for c in resp.json():
        if c["name"] == name:
            return c["id"]
    raise AssertionError(f"category {name!r} not seeded")


def test_report_image_vault_quota_413_no_orphan_row(db, superadmin, auth):
    soc, admin, hdr = society_with_tiny_quota(db, superadmin, auth, limit_bytes=8)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = raise_complaint(auth, r_hdr, category_id=cat, title="x", description="y")["id"]

    resp = auth.client.post(
        f"/complaints/{cid}/images",
        headers=r_hdr,
        files={"file": ("big.png", PNG_BYTES, "image/png")},
    )
    assert resp.status_code == 413, resp.text

    rows = db.execute(
        select(ComplaintImage).where(ComplaintImage.complaint_id == cid)
    ).scalars().all()
    assert rows == []
    docs = db.execute(
        select(VaultDocument).where(VaultDocument.source_ref == cid)
    ).scalars().all()
    assert docs == []


def test_report_image_denied_type_415_no_orphan_row(db, superadmin, auth, society, admin_user):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = raise_complaint(auth, r_hdr, category_id=cat, title="x", description="y")["id"]

    resp = auth.client.post(
        f"/complaints/{cid}/images",
        headers=r_hdr,
        files={"file": ("malware.exe", EXE_BYTES, "application/octet-stream")},
    )
    assert resp.status_code == 415, resp.text

    rows = db.execute(
        select(ComplaintImage).where(ComplaintImage.complaint_id == cid)
    ).scalars().all()
    assert rows == []
    docs = db.execute(
        select(VaultDocument).where(VaultDocument.source_ref == cid)
    ).scalars().all()
    assert docs == []


def test_resolve_vault_quota_413_rolls_back_status(db, superadmin, auth):
    soc, admin, hdr = society_with_tiny_quota(db, superadmin, auth, limit_bytes=8)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = raise_complaint(auth, r_hdr, category_id=cat, title="x", description="y")["id"]
    auth.client.post(
        f"/complaints/{cid}/status", headers=hdr, json={"to_status": "in_progress"}
    )

    resp = resolve_http(
        auth, hdr, cid, note="fixed", files=[("proof.png", PNG_BYTES, "image/png")]
    )
    assert resp.status_code == 413, resp.text

    row = db.query(Complaint).filter(Complaint.id == cid).one()
    assert row.status == "in_progress"
    assert row.resolved_at is None
    imgs = db.execute(
        select(ComplaintImage).where(ComplaintImage.complaint_id == cid)
    ).scalars().all()
    assert imgs == []


def test_resolve_denied_type_415_rolls_back(db, superadmin, auth, society, admin_user):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = raise_complaint(auth, r_hdr, category_id=cat, title="x", description="y")["id"]
    auth.client.post(
        f"/complaints/{cid}/status", headers=hdr, json={"to_status": "in_progress"}
    )

    resp = resolve_http(
        auth,
        hdr,
        cid,
        note="fixed",
        files=[("malware.exe", EXE_BYTES, "application/octet-stream")],
    )
    assert resp.status_code == 415, resp.text

    row = db.query(Complaint).filter(Complaint.id == cid).one()
    assert row.status == "in_progress"
    assert row.resolved_at is None
    imgs = db.execute(
        select(ComplaintImage).where(ComplaintImage.complaint_id == cid)
    ).scalars().all()
    assert imgs == []


def test_malformed_create_422(db, superadmin, auth, society, admin_user):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)

    # Missing category_id.
    assert auth.client.post(
        "/complaints", headers=r_hdr, json={"title": "x", "description": "y"}
    ).status_code == 422

    # Blank title.
    assert auth.client.post(
        "/complaints",
        headers=r_hdr,
        json={"category_id": cat, "title": "   ", "description": "y"},
    ).status_code == 422

    # Title over 200 chars.
    assert auth.client.post(
        "/complaints",
        headers=r_hdr,
        json={"category_id": cat, "title": "x" * 201, "description": "y"},
    ).status_code == 422

    # Description over 5000 chars.
    assert auth.client.post(
        "/complaints",
        headers=r_hdr,
        json={"category_id": cat, "title": "x", "description": "y" * 5001},
    ).status_code == 422

    # Missing description.
    assert auth.client.post(
        "/complaints", headers=r_hdr, json={"category_id": cat, "title": "x"}
    ).status_code == 422


def test_nonexistent_ids_404(db, superadmin, auth, society, admin_user):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")

    assert auth.client.get("/complaints/999999", headers=hdr).status_code == 404
    assert auth.client.post(
        "/complaints/999999/status", headers=hdr, json={"to_status": "in_progress"}
    ).status_code == 404
    assert auth.client.post(
        "/complaints/999999/resolve", headers=hdr, data={"note": "x"}
    ).status_code == 404
    assert auth.client.post(
        "/complaints/999999/images",
        headers=r_hdr,
        files={"file": ("x.jpg", b"x", "image/jpeg")},
    ).status_code == 404
    assert auth.client.delete(
        "/complaints/999999/images/1", headers=r_hdr
    ).status_code == 404
    assert auth.client.patch(
        "/complaints/categories/999999", headers=hdr, json={"name": "x"}
    ).status_code == 404
    assert auth.client.delete(
        "/complaints/categories/999999", headers=hdr
    ).status_code == 404
    assert auth.client.post(
        "/complaints/999999/withdraw", headers=r_hdr
    ).status_code == 404
    assert auth.client.patch(
        "/complaints/999999", headers=r_hdr, json={"title": "x"}
    ).status_code == 404


def test_double_withdraw_and_double_resolve_conflict(db, superadmin, auth, society, admin_user):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)

    cid1 = raise_complaint(auth, r_hdr, category_id=cat, title="w1", description="y")["id"]
    first_withdraw = auth.client.post(f"/complaints/{cid1}/withdraw", headers=r_hdr)
    assert first_withdraw.status_code == 200, first_withdraw.text
    second_withdraw = auth.client.post(f"/complaints/{cid1}/withdraw", headers=r_hdr)
    assert second_withdraw.status_code == 409, second_withdraw.text

    cid2 = raise_complaint(auth, r_hdr, category_id=cat, title="r1", description="y")["id"]
    auth.client.post(
        f"/complaints/{cid2}/status", headers=hdr, json={"to_status": "in_progress"}
    )
    first_resolve = resolve_http(auth, hdr, cid2, note="fixed")
    assert first_resolve.status_code == 200, first_resolve.text
    second_resolve = resolve_http(auth, hdr, cid2, note="fixed again")
    assert second_resolve.status_code == 409, second_resolve.text
