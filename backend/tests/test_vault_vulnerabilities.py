"""Vulnerability-hardening tests for the Vault module: path traversal, quota races,
content-type spoofing, denylist bypass attempts.
"""
from __future__ import annotations

import re

import pytest

from tests._vault_helpers import (  # noqa: F401
    _admin_bearer,
    _contents,
    _create_folder,
    _second_society,
    _set_limit,
    _setup,
    _upload,
    _upload_raw,
    storage_override,
)

pytestmark = pytest.mark.usefixtures("storage_override")


def test_path_traversal_posix_sanitized(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    resp = _upload_raw(
        auth, hdr, bills["id"], filename="../../etc/passwd", content_type="text/plain"
    )
    assert resp.status_code == 200, resp.text
    doc = resp.json()
    assert doc["filename"] == "passwd"
    from app.modules.vault.models import VaultDocument

    row = db.get(VaultDocument, doc["id"])
    assert re.match(rf"^societies/{society.id}/\d+__passwd$", row.storage_key)


def test_path_traversal_windows_backslash(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    resp = _upload_raw(
        auth,
        hdr,
        bills["id"],
        filename="..\\..\\Windows\\System32\\evil.dll",
        content_type="application/octet-stream",
    )
    assert resp.status_code == 415


def test_absolute_path_sanitized(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    resp = _upload_raw(
        auth, hdr, bills["id"], filename="/etc/shadow", content_type="text/plain"
    )
    assert resp.status_code == 200, resp.text
    doc = resp.json()
    assert doc["filename"] == "shadow"
    from app.modules.vault.models import VaultDocument

    row = db.get(VaultDocument, doc["id"])
    assert row.storage_key.startswith(f"societies/{society.id}/")


def test_dotdot_only_filename_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    resp = _upload_raw(auth, hdr, bills["id"], filename="..", content_type="text/plain")
    assert resp.status_code == 422
    assert resp.json()["message"] == "Invalid filename."


def test_storage_key_no_client_path_segments(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    resp = _upload_raw(
        auth,
        hdr,
        bills["id"],
        filename="a/b\\c/../weird.txt",
        content_type="text/plain",
    )
    assert resp.status_code == 200, resp.text
    doc = resp.json()
    from app.modules.vault.models import VaultDocument

    row = db.get(VaultDocument, doc["id"])
    assert row.storage_key.startswith(f"societies/{society.id}/")
    assert row.storage_key.count("/") == 2


def test_presigned_url_not_obtainable_cross_society(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    soc_b, admin_b = _second_society(db, superadmin)
    hdr_b = _admin_bearer(auth, admin_b)
    fb = _create_folder(auth, hdr_b, "Fb")
    doc_b = _upload(auth, hdr_b, fb["id"])
    resp = auth.client.get(f"/vault/documents/{doc_b['id']}/preview", headers=hdr)
    assert resp.status_code == 404


def test_content_type_spoof_safe_ext_allowed(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    resp = _upload_raw(
        auth,
        hdr,
        bills["id"],
        filename="photo.jpg",
        content_type="application/x-msdownload",
    )
    assert resp.status_code == 415


def test_content_type_benign_mismatch_allowed(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    resp = _upload_raw(
        auth, hdr, bills["id"], filename="report.pdf", content_type="image/png"
    )
    assert resp.status_code == 200, resp.text


def test_quota_not_bypassed_by_sequential_uploads(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _set_limit(db, society.id, 30)
    bills = _create_folder(auth, hdr, "Bills")
    resp1 = _upload_raw(auth, hdr, bills["id"], filename="a.pdf", data=b"x" * 20)
    assert resp1.status_code == 200, resp1.text
    resp2 = _upload_raw(auth, hdr, bills["id"], filename="b.pdf", data=b"x" * 20)
    assert resp2.status_code == 413
    usage = auth.client.get("/vault/usage", headers=hdr).json()
    assert usage["used_bytes"] == 20


def test_quota_lock_path_no_double_count(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    data = b"x" * 20
    _set_limit(db, society.id, len(data))
    bills = _create_folder(auth, hdr, "Bills")
    resp1 = _upload_raw(auth, hdr, bills["id"], filename="a.pdf", data=data)
    assert resp1.status_code == 200, resp1.text
    resp2 = _upload_raw(auth, hdr, bills["id"], filename="b.pdf", data=b"x")
    assert resp2.status_code == 413
    usage = auth.client.get("/vault/usage", headers=hdr).json()
    assert usage["used_bytes"] <= len(data)


def test_denylist_cannot_bypass_via_rename(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    doc = _upload(auth, hdr, bills["id"], filename="ok.pdf")
    resp = auth.client.patch(
        f"/vault/documents/{doc['id']}", headers=hdr, json={"filename": "evil.exe"}
    )
    assert resp.status_code == 415


def test_denylist_cannot_bypass_via_uppercase_ext(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    resp = _upload_raw(
        auth, hdr, bills["id"], filename="EVIL.ExE", content_type="application/octet-stream"
    )
    assert resp.status_code == 415
