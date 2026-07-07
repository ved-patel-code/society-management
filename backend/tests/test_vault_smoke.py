"""Smoke tests for the Vault module: registration, enable grants, empty state."""
from __future__ import annotations

import pytest

from app.core.registry import MODULE_REGISTRY
from tests._vault_helpers import (  # noqa: F401
    PERM_MANAGE,
    PERM_READ,
    _enable_vault,
    _setup,
    storage_override,
)

pytestmark = pytest.mark.usefixtures("storage_override")


def test_vault_spec_registered():
    spec = MODULE_REGISTRY.get("vault")
    assert spec is not None
    perm_keys = {p.key for p in spec.permissions}
    assert perm_keys == {"vault.read", "vault.manage"}
    assert spec.depends_on == ["onboarding"]
    assert spec.is_core is False


def test_enable_grants_admin_both_perms(db, society, admin_user, superadmin, auth):
    from app.platform.roles.repository import RoleRepository

    _enable_vault(db, society, superadmin)
    role = RoleRepository(db).society_role_by_key(society.id, "society_admin")
    perms = RoleRepository(db).role_permission_keys(role.id)
    assert PERM_READ in perms
    assert PERM_MANAGE in perms


def test_enable_grants_resident_nothing(db, society, resident_user, superadmin, auth):
    from app.platform.roles.repository import RoleRepository

    _enable_vault(db, society, superadmin)
    role = RoleRepository(db).society_role_by_key(society.id, "resident")
    perms = RoleRepository(db).role_permission_keys(role.id)
    assert PERM_READ not in perms
    assert PERM_MANAGE not in perms


def test_root_contents_empty(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = auth.client.get("/vault/folders/contents", headers=hdr)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["folder"] is None
    assert body["breadcrumb"] == [{"id": None, "name": "Vault"}]
    assert body["folders"] == []
    assert body["documents"] == []
    assert body["total"] == 0


def test_usage_zero_initial(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = auth.client.get("/vault/usage", headers=hdr)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["used_bytes"] == 0
    assert body["limit_bytes"] == 5 * 1024**3
    assert body["available_bytes"] == body["limit_bytes"]


def test_trash_empty_initial(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = auth.client.get("/vault/trash", headers=hdr)
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


@pytest.mark.parametrize(
    "method,path,kwargs",
    [
        ("get", "/vault/folders/contents", {}),
        ("get", "/vault/folders/1/contents", {}),
        ("post", "/vault/folders", {"json": {"name": "X"}}),
        ("patch", "/vault/folders/1", {"json": {"name": "X"}}),
        ("delete", "/vault/folders/1", {}),
        ("post", "/vault/documents", {"data": {"folder_id": "1"}}),
        ("get", "/vault/documents/1/preview", {}),
        ("get", "/vault/documents/1/download", {}),
        ("patch", "/vault/documents/1", {"json": {"filename": "x"}}),
        ("delete", "/vault/documents/1", {}),
        ("get", "/vault/trash", {}),
        ("post", "/vault/trash/documents/1/restore", {}),
        ("post", "/vault/trash/empty", {}),
        ("get", "/vault/usage", {}),
    ],
)
def test_all_routes_401_no_token(client, method, path, kwargs):
    resp = getattr(client, method)(path, **kwargs)
    assert resp.status_code == 401
