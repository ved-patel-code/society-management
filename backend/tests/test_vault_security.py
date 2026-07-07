"""Security + tenant-isolation tests for the Vault module."""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.platform.roles.repository import RoleRepository
from app.platform.societies.schemas import ModuleAllocation
from app.platform.societies.service import SocietyService
from tests.conftest import DEFAULT_MEMBER_PASSWORD
from tests._vault_helpers import (  # noqa: F401
    MODULE_KEY,
    _admin_bearer,
    _contents,
    _create_folder,
    _enable_vault,
    _grant_read_only,
    _resident_bearer,
    _second_society,
    _setup,
    _upload,
    _upload_raw,
    storage_override,
)

pytestmark = pytest.mark.usefixtures("storage_override")

_READ_ROUTES = [
    ("get", "/vault/folders/contents"),
    ("get", "/vault/folders/1/contents"),
    ("get", "/vault/documents/1/preview"),
    ("get", "/vault/documents/1/download"),
    ("get", "/vault/trash"),
    ("get", "/vault/usage"),
]

_MANAGE_ROUTES = [
    ("post", "/vault/folders", {"json": {"name": "X"}}),
    ("patch", "/vault/folders/1", {"json": {"name": "X"}}),
    ("delete", "/vault/folders/1", {}),
    ("post", "/vault/documents", {"data": {"folder_id": "1"}}),
    ("patch", "/vault/documents/1", {"json": {"filename": "x"}}),
    ("delete", "/vault/documents/1", {}),
    ("post", "/vault/trash/documents/1/restore", {}),
    ("post", "/vault/trash/empty", {}),
]


@pytest.mark.parametrize("method,path", _READ_ROUTES)
def test_all_read_routes_401_no_token(client, method, path):
    assert getattr(client, method)(path).status_code == 401


@pytest.mark.parametrize("method,path,kwargs", _MANAGE_ROUTES)
def test_all_manage_routes_401_no_token(client, method, path, kwargs):
    assert getattr(client, method)(path, **kwargs).status_code == 401


def test_resident_403_on_read(db, society, resident_user, superadmin, auth):
    hdr = _resident_bearer(db, society, resident_user, superadmin, auth)
    resp = auth.client.get("/vault/folders/contents", headers=hdr)
    assert resp.status_code == 403
    assert resp.json()["details"]["required_permission"] == "vault.read"


def test_resident_403_on_manage(db, society, resident_user, superadmin, auth):
    hdr = _resident_bearer(db, society, resident_user, superadmin, auth)
    resp = auth.client.post("/vault/folders", headers=hdr, json={"name": "X"})
    assert resp.status_code == 403
    assert resp.json()["details"]["required_permission"] == "vault.manage"


def test_readonly_principal_can_browse(db, society, admin_user, superadmin, auth):
    _grant_read_only(db, society, superadmin)
    hdr = _admin_bearer(auth, admin_user)
    assert auth.client.get("/vault/folders/contents", headers=hdr).status_code == 200
    assert auth.client.get("/vault/usage", headers=hdr).status_code == 200
    assert auth.client.get("/vault/trash", headers=hdr).status_code == 200


def test_readonly_principal_403_create_folder(db, society, admin_user, superadmin, auth):
    _grant_read_only(db, society, superadmin)
    hdr = _admin_bearer(auth, admin_user)
    resp = auth.client.post("/vault/folders", headers=hdr, json={"name": "X"})
    assert resp.status_code == 403
    assert resp.json()["details"]["required_permission"] == "vault.manage"


def test_readonly_principal_403_upload(db, society, admin_user, superadmin, auth):
    _grant_read_only(db, society, superadmin)
    hdr = _admin_bearer(auth, admin_user)
    resp = _upload_raw(auth, hdr, 1)
    assert resp.status_code == 403


@pytest.mark.parametrize(
    "method,path,kwargs",
    [
        ("delete", "/vault/folders/1", {}),
        ("delete", "/vault/documents/1", {}),
        ("post", "/vault/trash/documents/1/restore", {}),
        ("post", "/vault/trash/empty", {}),
    ],
)
def test_readonly_principal_403_delete_restore_empty(
    db, society, admin_user, superadmin, auth, method, path, kwargs
):
    _grant_read_only(db, society, superadmin)
    hdr = _admin_bearer(auth, admin_user)
    resp = getattr(auth.client, method)(path, headers=hdr, **kwargs)
    assert resp.status_code == 403


def test_readonly_principal_can_preview_download(db, society, admin_user, superadmin, auth):
    hdr_full = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr_full, "Bills")
    doc = _upload(auth, hdr_full, bills["id"])
    # Strip vault.manage from society_admin now (after seeding), keeping the
    # SAME already-activated bearer — re-running the must-change dance would
    # fail (the password was already changed).
    role = RoleRepository(db).society_role_by_key(society.id, "society_admin")
    perm_id = db.execute(
        text("SELECT id FROM permissions WHERE key=:k"), {"k": "vault.manage"}
    ).scalar_one()
    db.execute(
        text("DELETE FROM role_permissions WHERE role_id=:r AND permission_id=:p"),
        {"r": role.id, "p": perm_id},
    )
    db.commit()
    hdr = hdr_full
    assert (
        auth.client.get(f"/vault/documents/{doc['id']}/preview", headers=hdr).status_code
        == 200
    )
    assert (
        auth.client.get(f"/vault/documents/{doc['id']}/download", headers=hdr).status_code
        == 200
    )


def test_module_disabled_blocks_read(db, society, admin_user, superadmin, auth):
    from app.platform.roles.service import RoleService

    RoleService(db).grant_default_module_permissions(
        society.id, {"society_admin": ["vault.read", "vault.manage"]},
        actor_user_id=superadmin.id,
    )
    db.commit()
    hdr = _admin_bearer(auth, admin_user)
    resp = auth.client.get("/vault/folders/contents", headers=hdr)
    assert resp.status_code == 403
    assert resp.json()["code"] == "module_disabled"


def test_module_disabled_blocks_manage(db, society, admin_user, superadmin, auth):
    from app.platform.roles.service import RoleService

    RoleService(db).grant_default_module_permissions(
        society.id, {"society_admin": ["vault.read", "vault.manage"]},
        actor_user_id=superadmin.id,
    )
    db.commit()
    hdr = _admin_bearer(auth, admin_user)
    resp = auth.client.post("/vault/folders", headers=hdr, json={"name": "X"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "module_disabled"


def test_onboarding_only_still_blocks_vault(db, society, admin_user, superadmin, auth):
    from app.platform.roles.service import RoleService

    SocietyService(db).set_modules(
        society.id,
        [ModuleAllocation(module_key="onboarding", enabled=True, config={})],
        actor_user_id=superadmin.id,
    )
    db.commit()
    RoleService(db).grant_default_module_permissions(
        society.id, {"society_admin": ["vault.read", "vault.manage"]},
        actor_user_id=superadmin.id,
    )
    db.commit()
    hdr = _admin_bearer(auth, admin_user)
    resp = auth.client.get("/vault/folders/contents", headers=hdr)
    assert resp.status_code == 403
    assert resp.json()["details"]["module_key"] == MODULE_KEY


def test_cross_society_cannot_read_folder_contents(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    soc_b, admin_b = _second_society(db, superadmin)
    hdr_b = _admin_bearer(auth, admin_b)
    fb = _create_folder(auth, hdr_b, "Fb")
    resp = auth.client.get(f"/vault/folders/{fb['id']}/contents", headers=hdr)
    assert resp.status_code == 404


def test_cross_society_cannot_rename_folder(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    soc_b, admin_b = _second_society(db, superadmin)
    hdr_b = _admin_bearer(auth, admin_b)
    fb = _create_folder(auth, hdr_b, "Fb")
    resp = auth.client.patch(
        f"/vault/folders/{fb['id']}", headers=hdr, json={"name": "Hacked"}
    )
    assert resp.status_code == 404


def test_cross_society_cannot_delete_folder(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    soc_b, admin_b = _second_society(db, superadmin)
    hdr_b = _admin_bearer(auth, admin_b)
    fb = _create_folder(auth, hdr_b, "Fb")
    resp = auth.client.delete(f"/vault/folders/{fb['id']}", headers=hdr)
    assert resp.status_code == 404
    body = _contents(auth, hdr_b, None)
    assert any(f["id"] == fb["id"] for f in body["folders"])


def test_cross_society_cannot_preview_document(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    soc_b, admin_b = _second_society(db, superadmin)
    hdr_b = _admin_bearer(auth, admin_b)
    fb = _create_folder(auth, hdr_b, "Fb")
    doc_b = _upload(auth, hdr_b, fb["id"])
    resp = auth.client.get(f"/vault/documents/{doc_b['id']}/preview", headers=hdr)
    assert resp.status_code == 404


def test_cross_society_cannot_download_document(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    soc_b, admin_b = _second_society(db, superadmin)
    hdr_b = _admin_bearer(auth, admin_b)
    fb = _create_folder(auth, hdr_b, "Fb")
    doc_b = _upload(auth, hdr_b, fb["id"])
    resp = auth.client.get(f"/vault/documents/{doc_b['id']}/download", headers=hdr)
    assert resp.status_code == 404


def test_cross_society_cannot_delete_document(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    soc_b, admin_b = _second_society(db, superadmin)
    hdr_b = _admin_bearer(auth, admin_b)
    fb = _create_folder(auth, hdr_b, "Fb")
    doc_b = _upload(auth, hdr_b, fb["id"])
    resp = auth.client.delete(f"/vault/documents/{doc_b['id']}", headers=hdr)
    assert resp.status_code == 404
    body = _contents(auth, hdr_b, fb["id"])
    assert any(d["id"] == doc_b["id"] for d in body["documents"])


def test_cross_society_cannot_restore_b_trash(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    soc_b, admin_b = _second_society(db, superadmin)
    hdr_b = _admin_bearer(auth, admin_b)
    fb = _create_folder(auth, hdr_b, "Fb")
    doc_b = _upload(auth, hdr_b, fb["id"])
    auth.client.delete(f"/vault/documents/{doc_b['id']}", headers=hdr_b)
    resp = auth.client.post(
        f"/vault/trash/documents/{doc_b['id']}/restore", headers=hdr
    )
    assert resp.status_code == 404


def test_trash_only_lists_own_society(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    soc_b, admin_b = _second_society(db, superadmin)
    hdr_b = _admin_bearer(auth, admin_b)

    fa = _create_folder(auth, hdr, "Fa")
    doc_a = _upload(auth, hdr, fa["id"])
    auth.client.delete(f"/vault/documents/{doc_a['id']}", headers=hdr)

    fb = _create_folder(auth, hdr_b, "Fb")
    doc_b = _upload(auth, hdr_b, fb["id"])
    auth.client.delete(f"/vault/documents/{doc_b['id']}", headers=hdr_b)

    trash_a = auth.client.get("/vault/trash", headers=hdr).json()
    assert len(trash_a) == 1
    assert trash_a[0]["id"] == doc_a["id"]


def test_usage_isolated_per_society(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    soc_b, admin_b = _second_society(db, superadmin)
    hdr_b = _admin_bearer(auth, admin_b)

    fa = _create_folder(auth, hdr, "Fa")
    _upload(auth, hdr, fa["id"], data=b"x" * 10)
    fb = _create_folder(auth, hdr_b, "Fb")
    _upload(auth, hdr_b, fb["id"], data=b"x" * 99)

    usage_a = auth.client.get("/vault/usage", headers=hdr).json()
    assert usage_a["used_bytes"] == 10


def test_crafted_foreign_active_society_403(
    db, society, admin_user, superadmin, auth, make_token
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    soc_b, admin_b = _second_society(db, superadmin)
    db.execute(
        text("UPDATE users SET password_state='active' WHERE id=:i"),
        {"i": admin_user.id},
    )
    db.commit()
    forged = make_token(
        user_id=admin_user.id, active_society_id=soc_b.id, role_ids=[], password_state="active",
    )
    resp = auth.client.get("/vault/folders/contents", headers=auth.bearer(forged))
    assert resp.status_code == 403
    assert resp.json()["code"] == "permission_denied"


def test_forged_role_ids_grants_nothing(
    db, society, resident_user, superadmin, auth, make_token
):
    _enable_vault(db, society, superadmin)
    admin_role = RoleRepository(db).society_role_by_key(society.id, "society_admin")
    db.execute(
        text("UPDATE users SET password_state='active' WHERE id=:i"),
        {"i": resident_user.id},
    )
    db.commit()
    forged = make_token(
        user_id=resident_user.id, active_society_id=society.id,
        role_ids=[admin_role.id], password_state="active",
    )
    resp = auth.client.get("/vault/folders/contents", headers=auth.bearer(forged))
    assert resp.status_code == 403


def test_active_society_none_non_super_module_disabled(
    db, society, admin_user, superadmin, auth, make_token
):
    _enable_vault(db, society, superadmin)
    db.execute(
        text("UPDATE users SET password_state='active' WHERE id=:i"),
        {"i": admin_user.id},
    )
    db.commit()
    forged = make_token(
        user_id=admin_user.id, active_society_id=None, role_ids=[], password_state="active",
    )
    resp = auth.client.get("/vault/folders/contents", headers=auth.bearer(forged))
    assert resp.status_code == 403
    assert resp.json()["code"] == "module_disabled"


def test_super_admin_no_active_society_422(db, society, superadmin, auth, make_token):
    _enable_vault(db, society, superadmin)
    token = make_token(
        user_id=superadmin.id, active_society_id=None, role_ids=[], password_state="active",
    )
    resp = auth.client.get("/vault/usage", headers=auth.bearer(token))
    assert resp.status_code == 422
    assert resp.json()["message"] == "No active society for this request."


def test_super_admin_with_active_society_200(db, society, superadmin, auth, make_token):
    _enable_vault(db, society, superadmin)
    token = make_token(
        user_id=superadmin.id, active_society_id=society.id, role_ids=[], password_state="active",
    )
    resp = auth.client.get("/vault/folders/contents", headers=auth.bearer(token))
    assert resp.status_code == 200, resp.text


def test_must_change_admin_blocked(db, society, admin_user, superadmin, auth):
    _enable_vault(db, society, superadmin)
    tokens = auth.login_ok(admin_user.email, DEFAULT_MEMBER_PASSWORD)
    locked = auth.bearer(tokens["access_token"])
    resp = auth.client.get("/vault/folders/contents", headers=locked)
    assert resp.status_code == 403
    assert resp.json()["details"]["password_state"] == "must_change"
