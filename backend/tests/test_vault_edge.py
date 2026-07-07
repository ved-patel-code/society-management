"""Edge-case tests for the Vault module: deep nesting, cycles, quota/denylist boundaries."""
from __future__ import annotations

from sqlalchemy import text

import pytest

from tests._vault_helpers import (  # noqa: F401
    _admin_bearer,
    _contents,
    _create_folder,
    _set_limit,
    _setup,
    _upload,
    _upload_raw,
    storage_override,
)

pytestmark = pytest.mark.usefixtures("storage_override")


def test_deep_nesting(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    parent_id = None
    last = None
    for i in range(10):
        last = _create_folder(auth, hdr, f"L{i}", parent_id=parent_id)
        parent_id = last["id"]
    body = _contents(auth, hdr, last["id"])
    assert len(body["breadcrumb"]) == 11


def test_move_into_own_descendant_rejected(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    a = _create_folder(auth, hdr, "A")
    b = _create_folder(auth, hdr, "B", parent_id=a["id"])
    resp = auth.client.patch(
        f"/vault/folders/{a['id']}", headers=hdr, json={"parent_id": b["id"], "move": True}
    )
    assert resp.status_code == 409


def test_move_into_self_rejected(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    a = _create_folder(auth, hdr, "A")
    resp = auth.client.patch(
        f"/vault/folders/{a['id']}", headers=hdr, json={"parent_id": a["id"], "move": True}
    )
    assert resp.status_code == 422


def test_move_folder_to_root(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    a = _create_folder(auth, hdr, "A")
    b = _create_folder(auth, hdr, "B", parent_id=a["id"])
    resp = auth.client.patch(
        f"/vault/folders/{b['id']}", headers=hdr, json={"parent_id": None, "move": True}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["parent_id"] is None


def _houses_root(auth, hdr, db, society, superadmin):
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
    return next(f for f in root["folders"] if f["system_key"] == "houses_root")


def test_move_regular_folder_into_system_folder_allowed(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses_root = _houses_root(auth, hdr, db, society, superadmin)
    r = _create_folder(auth, hdr, "R")
    resp = auth.client.patch(
        f"/vault/folders/{r['id']}",
        headers=hdr,
        json={"parent_id": houses_root["id"], "move": True},
    )
    assert resp.status_code == 200, resp.text


def test_create_folder_colliding_with_house_derived_name_409(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses_root = _houses_root(auth, hdr, db, society, superadmin)
    # The per-house system folder shows the house's DERIVED display code as its
    # name (not the stored value). Creating a regular sibling with that exact
    # derived name must collide (409).
    house_body = _contents(auth, hdr, houses_root["id"])
    house_folder = next(f for f in house_body["folders"] if f["system_key"] == "house")
    derived_code = house_folder["name"]
    resp = auth.client.post(
        "/vault/folders",
        headers=hdr,
        json={"parent_id": houses_root["id"], "name": derived_code},
    )
    assert resp.status_code == 409, resp.text


def test_cascade_delete_then_restore_full_subtree(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    a = _create_folder(auth, hdr, "A")
    b = _create_folder(auth, hdr, "B", parent_id=a["id"])
    c = _create_folder(auth, hdr, "C", parent_id=b["id"])
    doc = _upload(auth, hdr, c["id"])
    auth.client.delete(f"/vault/folders/{a['id']}", headers=hdr)
    resp = auth.client.post(f"/vault/trash/folders/{a['id']}/restore", headers=hdr)
    assert resp.status_code == 200, resp.text
    assert _contents(auth, hdr, None)["folders"]
    assert any(f["id"] == b["id"] for f in _contents(auth, hdr, a["id"])["folders"])
    assert any(f["id"] == c["id"] for f in _contents(auth, hdr, b["id"])["folders"])
    assert any(d["id"] == doc["id"] for d in _contents(auth, hdr, c["id"])["documents"])


def test_restore_name_collision_appends_restored(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    x = _create_folder(auth, hdr, "X")
    auth.client.delete(f"/vault/folders/{x['id']}", headers=hdr)
    x2 = _create_folder(auth, hdr, "X")
    resp = auth.client.post(f"/vault/trash/folders/{x['id']}/restore", headers=hdr)
    assert resp.status_code == 200, resp.text
    body = _contents(auth, hdr, None)
    names = {f["name"] for f in body["folders"]}
    assert "X (restored)" in names
    assert "X" in names


def test_restore_name_collision_loops(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    x = _create_folder(auth, hdr, "X")
    auth.client.delete(f"/vault/folders/{x['id']}", headers=hdr)
    _create_folder(auth, hdr, "X")
    _create_folder(auth, hdr, "X (restored)")
    resp = auth.client.post(f"/vault/trash/folders/{x['id']}/restore", headers=hdr)
    assert resp.status_code == 200, resp.text
    body = _contents(auth, hdr, None)
    names = {f["name"] for f in body["folders"]}
    assert "X (restored 2)" in names


def test_restore_document_name_collision(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    doc = _upload(auth, hdr, bills["id"], filename="a.pdf")
    auth.client.delete(f"/vault/documents/{doc['id']}", headers=hdr)
    _upload(auth, hdr, bills["id"], filename="a.pdf")
    resp = auth.client.post(f"/vault/trash/documents/{doc['id']}/restore", headers=hdr)
    assert resp.status_code == 200, resp.text
    body = _contents(auth, hdr, bills["id"])
    names = {d["filename"] for d in body["documents"]}
    assert "a.pdf (restored)" in names
    assert "a.pdf" in names


def test_trashed_bytes_counted_until_empty(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    doc = _upload(auth, hdr, bills["id"], data=b"x" * 50)
    auth.client.delete(f"/vault/documents/{doc['id']}", headers=hdr)
    usage = auth.client.get("/vault/usage", headers=hdr).json()
    assert usage["used_bytes"] == 50
    auth.client.post("/vault/trash/empty", headers=hdr)
    usage2 = auth.client.get("/vault/usage", headers=hdr).json()
    assert usage2["used_bytes"] == 0


def test_quota_exact_limit_allowed(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    data = b"x" * 25
    _set_limit(db, society.id, len(data))
    bills = _create_folder(auth, hdr, "Bills")
    resp = _upload_raw(auth, hdr, bills["id"], data=data)
    assert resp.status_code == 200, resp.text


def test_quota_over_by_one_rejected(db, society, admin_user, superadmin, auth, storage_override):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    data = b"x" * 25
    _set_limit(db, society.id, len(data) - 1)
    bills = _create_folder(auth, hdr, "Bills")
    resp = _upload_raw(auth, hdr, bills["id"], data=data)
    assert resp.status_code == 413
    usage = auth.client.get("/vault/usage", headers=hdr).json()
    assert usage["used_bytes"] == 0
    body = _contents(auth, hdr, bills["id"])
    assert body["documents"] == []


def test_denylist_exe_blocked(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    resp = _upload_raw(
        auth, hdr, bills["id"], filename="evil.exe", content_type="application/octet-stream"
    )
    assert resp.status_code == 415


def test_denylist_case_insensitive(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    resp = _upload_raw(
        auth, hdr, bills["id"], filename="EVIL.EXE", content_type="application/octet-stream"
    )
    assert resp.status_code == 415


def test_denylist_double_extension(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    resp = _upload_raw(
        auth, hdr, bills["id"], filename="evil.pdf.exe", content_type="application/octet-stream"
    )
    assert resp.status_code == 415


def test_denylist_trailing_dot(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    resp = _upload_raw(
        auth, hdr, bills["id"], filename="evil.exe.", content_type="application/octet-stream"
    )
    assert resp.status_code == 415


def test_denylist_trailing_space(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    resp = _upload_raw(
        auth, hdr, bills["id"], filename="evil.exe ", content_type="application/octet-stream"
    )
    assert resp.status_code == 415


def test_no_extension_allowed(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    resp = _upload_raw(auth, hdr, bills["id"], filename="README", content_type="text/plain")
    assert resp.status_code == 200, resp.text


def test_restore_document_missing_parent_conflict(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    p = _create_folder(auth, hdr, "P")
    doc = _upload(auth, hdr, p["id"])
    auth.client.delete(f"/vault/documents/{doc['id']}", headers=hdr)
    # Simulate an orphaned document (a data-repair gap): the parent folder row
    # is gone, but the document itself survives. The FK
    # (vault_documents_folder_id_fkey) is NO ACTION with no deferrable clause,
    # so we disable that one trigger for this session just long enough to force
    # the row into the orphaned state the restore path must defend against.
    db.execute(text("ALTER TABLE vault_folders DISABLE TRIGGER ALL"))
    try:
        db.execute(text("DELETE FROM vault_folders WHERE id=:i"), {"i": p["id"]})
    finally:
        db.execute(text("ALTER TABLE vault_folders ENABLE TRIGGER ALL"))
    db.commit()
    resp = auth.client.post(f"/vault/trash/documents/{doc['id']}/restore", headers=hdr)
    assert resp.status_code == 409
    assert "parent folder no longer exists" in resp.json()["message"]


def test_effective_denylist_config_override(db, society, admin_user, superadmin, auth):
    from app.platform.societies.schemas import ModuleAllocation
    from app.platform.societies.service import SocietyService

    SocietyService(db).set_modules(
        society.id,
        [
            ModuleAllocation(module_key="onboarding", enabled=True, config={}),
            ModuleAllocation(
                module_key="vault", enabled=True, config={"denylist_extensions": [".pdf"]}
            ),
        ],
        actor_user_id=superadmin.id,
    )
    db.commit()
    hdr = _admin_bearer(auth, admin_user)
    bills = _create_folder(auth, hdr, "Bills")
    resp = _upload_raw(auth, hdr, bills["id"], filename="x.pdf", content_type="application/pdf")
    assert resp.status_code == 415
