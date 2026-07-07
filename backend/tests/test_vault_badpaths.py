"""Bad-path / error-shape tests for the Vault module."""
from __future__ import annotations

import pytest

from tests._vault_helpers import (  # noqa: F401
    _contents,
    _create_folder,
    _setup,
    _upload,
    _upload_raw,
    storage_override,
)

pytestmark = pytest.mark.usefixtures("storage_override")


def test_create_in_nonexistent_parent(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = auth.client.post(
        "/vault/folders", headers=hdr, json={"name": "X", "parent_id": 999999}
    )
    assert resp.status_code == 404


def test_create_in_trashed_parent(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    p = _create_folder(auth, hdr, "P")
    auth.client.delete(f"/vault/folders/{p['id']}", headers=hdr)
    resp = auth.client.post(
        "/vault/folders", headers=hdr, json={"name": "Sub", "parent_id": p["id"]}
    )
    assert resp.status_code == 404


def test_duplicate_sibling_root(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _create_folder(auth, hdr, "Bills")
    resp = auth.client.post("/vault/folders", headers=hdr, json={"name": "Bills"})
    assert resp.status_code == 409


def test_duplicate_sibling_nested(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    parent = _create_folder(auth, hdr, "Parent")
    _create_folder(auth, hdr, "Sub", parent_id=parent["id"])
    resp = auth.client.post(
        "/vault/folders", headers=hdr, json={"name": "Sub", "parent_id": parent["id"]}
    )
    assert resp.status_code == 409


def test_upload_to_nonexistent_folder(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = _upload_raw(auth, hdr, 999999)
    assert resp.status_code == 404


def test_upload_to_trashed_folder(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    p = _create_folder(auth, hdr, "P")
    auth.client.delete(f"/vault/folders/{p['id']}", headers=hdr)
    resp = _upload_raw(auth, hdr, p["id"])
    assert resp.status_code == 404


def _houses_root(auth, hdr, db, society, admin_user, superadmin):
    """Enable houses too, upload an id-proof to force-create the Houses system root."""
    from tests._houses_helpers import _enable_houses, _make_building_with_houses

    _enable_houses(db, society, superadmin)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = auth.client.post(
        f"/houses/{hid}/status",
        headers=hdr,
        json={"to_status": "owned", "owner": {
            "full_name": "Owner One", "email": "owner1@x.com",
            "contact_number": "555-0001", "persons_living": 1,
        }},
    )
    assert resp.status_code == 200, resp.text
    resp2 = auth.client.post(
        f"/houses/{hid}/occupancy/owner/id-proof",
        headers=hdr,
        files={"file": ("idproof.jpg", b"abc", "image/jpeg")},
    )
    assert resp2.status_code == 200, resp2.text
    root = _contents(auth, hdr, None)
    houses_root = next(f for f in root["folders"] if f["system_key"] == "houses_root")
    return houses_root


def test_rename_system_folder_conflict(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses_root = _houses_root(auth, hdr, db, society, admin_user, superadmin)
    resp = auth.client.patch(
        f"/vault/folders/{houses_root['id']}", headers=hdr, json={"name": "Renamed"}
    )
    assert resp.status_code == 409


def test_move_system_folder_conflict(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses_root = _houses_root(auth, hdr, db, society, admin_user, superadmin)
    other = _create_folder(auth, hdr, "Other")
    resp = auth.client.patch(
        f"/vault/folders/{houses_root['id']}",
        headers=hdr,
        json={"parent_id": other["id"], "move": True},
    )
    assert resp.status_code == 409


def test_delete_system_folder_conflict(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses_root = _houses_root(auth, hdr, db, society, admin_user, superadmin)
    resp = auth.client.delete(f"/vault/folders/{houses_root['id']}", headers=hdr)
    assert resp.status_code == 409


def test_restore_not_in_trash_document(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    doc = _upload(auth, hdr, bills["id"])
    resp = auth.client.post(
        f"/vault/trash/documents/{doc['id']}/restore", headers=hdr
    )
    assert resp.status_code == 409
    assert "not in the trash" in resp.json()["message"]


def test_restore_not_in_trash_folder(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    resp = auth.client.post(
        f"/vault/trash/folders/{bills['id']}/restore", headers=hdr
    )
    assert resp.status_code == 409


def test_restore_nonexistent(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = auth.client.post("/vault/trash/documents/999999/restore", headers=hdr)
    assert resp.status_code == 404


def test_unknown_trash_item_type(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = auth.client.post("/vault/trash/widgets/1/restore", headers=hdr)
    assert resp.status_code == 422
    assert resp.json()["details"]["item_type"] == "widgets"


def test_bad_pagination_page_zero(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = auth.client.get("/vault/folders/contents?page=0", headers=hdr)
    assert resp.status_code == 422


def test_bad_pagination_page_size_over_max(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = auth.client.get("/vault/folders/contents?page_size=101", headers=hdr)
    assert resp.status_code == 422


def test_patch_folder_no_change(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    resp = auth.client.patch(f"/vault/folders/{bills['id']}", headers=hdr, json={})
    assert resp.status_code == 422


def test_patch_document_no_change(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    doc = _upload(auth, hdr, bills["id"])
    resp = auth.client.patch(f"/vault/documents/{doc['id']}", headers=hdr, json={})
    assert resp.status_code == 422


def test_update_document_move_to_nonexistent_folder(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    doc = _upload(auth, hdr, bills["id"])
    resp = auth.client.patch(
        f"/vault/documents/{doc['id']}", headers=hdr, json={"folder_id": 999999}
    )
    assert resp.status_code == 404


def test_empty_folder_name(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = auth.client.post("/vault/folders", headers=hdr, json={"name": "   "})
    assert resp.status_code == 422


def test_folder_name_with_slash(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = auth.client.post("/vault/folders", headers=hdr, json={"name": "a/b"})
    assert resp.status_code == 422
