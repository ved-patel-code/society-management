"""Happy-path tests for the Vault module: folders, uploads, trash, usage."""
from __future__ import annotations

import pytest

from tests._vault_helpers import (  # noqa: F401
    _audit,
    _contents,
    _create_folder,
    _setup,
    _upload,
    storage_override,
)

pytestmark = pytest.mark.usefixtures("storage_override")


def test_create_root_folder(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    folder = _create_folder(auth, hdr, "Bills")
    assert folder["parent_id"] is None
    assert folder["is_system"] is False
    assert folder["system_key"] is None
    assert _audit(db, "vault.folder_created", society_id=society.id, entity_id=folder["id"])


def test_create_nested_folders(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    y2026 = _create_folder(auth, hdr, "2026", parent_id=bills["id"])
    q1 = _create_folder(auth, hdr, "Q1", parent_id=y2026["id"])
    assert y2026["parent_id"] == bills["id"]
    assert q1["parent_id"] == y2026["id"]


def test_list_contents_and_breadcrumb(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    y2026 = _create_folder(auth, hdr, "2026", parent_id=bills["id"])
    q1 = _create_folder(auth, hdr, "Q1", parent_id=y2026["id"])
    body = _contents(auth, hdr, q1["id"])
    assert body["folder"]["id"] == q1["id"]
    ids = [b["id"] for b in body["breadcrumb"]]
    names = [b["name"] for b in body["breadcrumb"]]
    assert ids == [None, bills["id"], y2026["id"], q1["id"]]
    assert names == ["Vault", "Bills", "2026", "Q1"]


def test_upload_increments_usage(db, society, admin_user, superadmin, auth, storage_override):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    doc = _upload(auth, hdr, bills["id"], data=b"hello world")
    resp = auth.client.get("/vault/usage", headers=hdr)
    body = resp.json()
    assert body["used_bytes"] == 11
    assert body["available_bytes"] == body["limit_bytes"] - 11
    from app.modules.vault.models import VaultDocument

    row = db.get(VaultDocument, doc["id"])
    assert storage_override.exists(row.storage_key)
    assert storage_override.get(row.storage_key) == b"hello world"
    assert doc["size_bytes"] == 11
    assert doc["source"] == "manual"
    assert _audit(db, "vault.document_uploaded", society_id=society.id, entity_id=doc["id"])


def test_upload_lists_in_folder(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    doc = _upload(auth, hdr, bills["id"], data=b"hello world")
    body = _contents(auth, hdr, bills["id"])
    assert any(d["id"] == doc["id"] for d in body["documents"])
    assert body["total"] == 1


def test_preview_returns_url(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    doc = _upload(auth, hdr, bills["id"], data=b"hello world")
    resp = auth.client.get(f"/vault/documents/{doc['id']}/preview", headers=hdr)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    from app.modules.vault.models import VaultDocument

    row = db.get(VaultDocument, doc["id"])
    assert "inline=True" in body["url"]
    assert row.storage_key in body["url"]
    assert body["expires_in"] == 300


def test_download_returns_url(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    doc = _upload(auth, hdr, bills["id"], data=b"hello world")
    resp = auth.client.get(f"/vault/documents/{doc['id']}/download", headers=hdr)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "inline=False" in body["url"]
    assert body["expires_in"] == 300


def test_rename_folder(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    resp = auth.client.patch(
        f"/vault/folders/{bills['id']}", headers=hdr, json={"name": "Invoices"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Invoices"
    assert _audit(db, "vault.folder_renamed", society_id=society.id, entity_id=bills["id"])


def test_move_folder(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    a = _create_folder(auth, hdr, "A")
    b = _create_folder(auth, hdr, "B")
    resp = auth.client.patch(
        f"/vault/folders/{a['id']}",
        headers=hdr,
        json={"parent_id": b["id"], "move": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["parent_id"] == b["id"]
    assert _audit(db, "vault.folder_moved", society_id=society.id, entity_id=a["id"])


def test_rename_document(db, society, admin_user, superadmin, auth, storage_override):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    doc = _upload(auth, hdr, bills["id"])
    from app.modules.vault.models import VaultDocument

    original_key = db.get(VaultDocument, doc["id"]).storage_key
    resp = auth.client.patch(
        f"/vault/documents/{doc['id']}", headers=hdr, json={"filename": "renamed.pdf"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["filename"] == "renamed.pdf"
    assert storage_override.exists(original_key)
    assert _audit(db, "vault.document_renamed", society_id=society.id, entity_id=doc["id"])


def test_move_document(db, society, admin_user, superadmin, auth, storage_override):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    x = _create_folder(auth, hdr, "X")
    y = _create_folder(auth, hdr, "Y")
    doc = _upload(auth, hdr, x["id"])
    from app.modules.vault.models import VaultDocument

    original_key = db.get(VaultDocument, doc["id"]).storage_key
    resp = auth.client.patch(
        f"/vault/documents/{doc['id']}", headers=hdr, json={"folder_id": y["id"]}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["folder_id"] == y["id"]
    db.expire_all()
    assert db.get(VaultDocument, doc["id"]).storage_key == original_key
    assert storage_override.exists(original_key)
    assert _audit(db, "vault.document_moved", society_id=society.id, entity_id=doc["id"])


def test_soft_delete_document_then_trash_lists(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    doc = _upload(auth, hdr, bills["id"], data=b"hello world")
    resp = auth.client.delete(f"/vault/documents/{doc['id']}", headers=hdr)
    assert resp.status_code == 204
    trash = auth.client.get("/vault/trash", headers=hdr).json()
    assert len(trash) == 1
    item = trash[0]
    assert item["type"] == "document"
    assert item["original_path"].startswith("/Bills/")
    assert item["size_bytes"] == 11
    body = _contents(auth, hdr, bills["id"])
    assert body["documents"] == []
    usage = auth.client.get("/vault/usage", headers=hdr).json()
    assert usage["used_bytes"] == 11


def test_restore_document(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    doc = _upload(auth, hdr, bills["id"])
    auth.client.delete(f"/vault/documents/{doc['id']}", headers=hdr)
    resp = auth.client.post(
        f"/vault/trash/documents/{doc['id']}/restore", headers=hdr
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["restored_to_folder_id"] == bills["id"]
    body = _contents(auth, hdr, bills["id"])
    assert any(d["id"] == doc["id"] for d in body["documents"])
    assert auth.client.get("/vault/trash", headers=hdr).json() == []


def test_soft_delete_folder_cascades_and_lists(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    parent = _create_folder(auth, hdr, "Parent")
    sub = _create_folder(auth, hdr, "Sub", parent_id=parent["id"])
    _upload(auth, hdr, parent["id"])
    resp = auth.client.delete(f"/vault/folders/{parent['id']}", headers=hdr)
    assert resp.status_code == 204
    trash = auth.client.get("/vault/trash", headers=hdr).json()
    assert any(i["type"] == "folder" and i["id"] == parent["id"] for i in trash)
    body = _contents(auth, hdr, None)
    assert all(f["id"] != parent["id"] for f in body["folders"])
    records = _audit(db, "vault.folder_deleted", society_id=society.id, entity_id=parent["id"])
    assert len(records) >= 1


def test_restore_folder_brings_subtree(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    parent = _create_folder(auth, hdr, "Parent")
    sub = _create_folder(auth, hdr, "Sub", parent_id=parent["id"])
    doc = _upload(auth, hdr, sub["id"])
    auth.client.delete(f"/vault/folders/{parent['id']}", headers=hdr)
    resp = auth.client.post(
        f"/vault/trash/folders/{parent['id']}/restore", headers=hdr
    )
    assert resp.status_code == 200, resp.text
    body = _contents(auth, hdr, None)
    assert any(f["id"] == parent["id"] for f in body["folders"])
    sub_contents = _contents(auth, hdr, sub["id"])
    assert any(d["id"] == doc["id"] for d in sub_contents["documents"])


def test_empty_trash_frees_usage(db, society, admin_user, superadmin, auth, storage_override):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    doc = _upload(auth, hdr, bills["id"], data=b"hello world")
    from app.modules.vault.models import VaultDocument

    storage_key = db.get(VaultDocument, doc["id"]).storage_key
    auth.client.delete(f"/vault/documents/{doc['id']}", headers=hdr)
    usage = auth.client.get("/vault/usage", headers=hdr).json()
    assert usage["used_bytes"] == 11
    resp = auth.client.post("/vault/trash/empty", headers=hdr)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted_count"] >= 1
    assert body["freed_bytes"] == 11
    usage2 = auth.client.get("/vault/usage", headers=hdr).json()
    assert usage2["used_bytes"] == 0
    assert not storage_override.exists(storage_key)
    assert _audit(db, "vault.trash_emptied", society_id=society.id)


def test_usage_math_after_two_uploads(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    _upload(auth, hdr, bills["id"], filename="a.pdf", data=b"x" * 11)
    _upload(auth, hdr, bills["id"], filename="b.pdf", data=b"x" * 20)
    usage = auth.client.get("/vault/usage", headers=hdr).json()
    assert usage["used_bytes"] == 31


def test_upload_collision_autorenames(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    doc1 = _upload(auth, hdr, bills["id"], filename="report.pdf")
    doc2 = _upload(auth, hdr, bills["id"], filename="report.pdf")
    assert doc1["filename"] == "report.pdf"
    assert doc2["filename"] == "report (1).pdf"
    body = _contents(auth, hdr, bills["id"])
    names = {d["filename"] for d in body["documents"]}
    assert names == {"report.pdf", "report (1).pdf"}


def test_pagination_documents(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    for i in range(3):
        _upload(auth, hdr, bills["id"], filename=f"f{i}.pdf")
    resp = auth.client.get(
        f"/vault/folders/{bills['id']}/contents?page_size=2&page=1", headers=hdr
    )
    body = resp.json()
    assert len(body["documents"]) == 2
    assert body["total"] == 3
    resp2 = auth.client.get(
        f"/vault/folders/{bills['id']}/contents?page_size=2&page=2", headers=hdr
    )
    body2 = resp2.json()
    assert len(body2["documents"]) == 1
