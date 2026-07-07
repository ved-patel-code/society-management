"""Shared test harness for the Vault (Module 3) test suite.

Mirrors the patterns in tests/_houses_helpers.py: must-change dance, module
enable, crafted tokens, cross-society setup. Import from this module in every
test_vault_*.py file (DRY) — except test_vault_storage_realminio.py, which uses
the real MinIO backend directly (no in-memory override).
"""
from __future__ import annotations

import pytest

from app.core.storage.memory_storage import InMemoryStorage
from app.core.storage.provider import reset_storage_override, set_storage_override
from app.platform.models import AuditLog
from app.platform.roles.repository import RoleRepository
from app.platform.societies.schemas import ModuleAllocation, SocietyCreate
from app.platform.societies.service import SocietyService
from app.platform.users.provisioning import UserProvisioningService
from tests.conftest import DEFAULT_MEMBER_PASSWORD

MODULE_KEY = "vault"
NEWPASS = "NewPass123"
PERM_READ = "vault.read"
PERM_MANAGE = "vault.manage"


def _enable_vault(db, society, superadmin) -> None:
    """Enable onboarding + vault (vault depends_on onboarding)."""
    SocietyService(db).set_modules(
        society.id,
        [
            ModuleAllocation(module_key="onboarding", enabled=True, config={}),
            ModuleAllocation(module_key="vault", enabled=True, config={}),
        ],
        actor_user_id=superadmin.id,
    )
    db.commit()


def _admin_bearer(auth, user) -> dict[str, str]:
    """must_change -> change-password -> re-login. Returns a usable bearer header."""
    tokens = auth.login_ok(user.email, DEFAULT_MEMBER_PASSWORD)
    resp = auth.client.post(
        "/auth/change-password",
        headers=auth.bearer(tokens["access_token"]),
        json={"current_password": DEFAULT_MEMBER_PASSWORD, "new_password": NEWPASS},
    )
    assert resp.status_code == 200, resp.text
    sess = auth.login_ok(user.email, NEWPASS)
    return auth.bearer(sess["access_token"])


def _setup(db, society, admin_user, superadmin, auth) -> dict[str, str]:
    """Enable vault + return an activated admin bearer header."""
    _enable_vault(db, society, superadmin)
    return _admin_bearer(auth, admin_user)


def _resident_bearer(db, society, resident_user, superadmin, auth) -> dict[str, str]:
    """Enable vault, activate the resident's password, return resident bearer."""
    _enable_vault(db, society, superadmin)
    return _admin_bearer(auth, resident_user)


@pytest.fixture
def storage_override():
    """Install an InMemoryStorage backend for the duration of a test.

    MUST be autouse in every non-realminio vault test module so no test hits
    real MinIO. Always reset the process-global override on teardown.
    """
    s = InMemoryStorage()
    set_storage_override(s)
    yield s
    reset_storage_override()


def _create_folder(auth, hdr, name, parent_id=None) -> dict:
    resp = auth.client.post(
        "/vault/folders", headers=hdr, json={"name": name, "parent_id": parent_id}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _upload_raw(
    auth,
    hdr,
    folder_id,
    *,
    filename="doc.pdf",
    content_type="application/pdf",
    data=b"x",
):
    """POST /vault/documents — raw response (caller asserts status)."""
    return auth.client.post(
        "/vault/documents",
        headers=hdr,
        files={"file": (filename, data, content_type)},
        data={"folder_id": str(folder_id)},
    )


def _upload(
    auth,
    hdr,
    folder_id,
    *,
    filename="doc.pdf",
    content_type="application/pdf",
    data=b"x",
) -> dict:
    """POST /vault/documents — assert 200 and return the parsed body."""
    resp = _upload_raw(
        auth, hdr, folder_id, filename=filename, content_type=content_type, data=data
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _contents(auth, hdr, folder_id=None) -> dict:
    if folder_id is None:
        resp = auth.client.get("/vault/folders/contents", headers=hdr)
    else:
        resp = auth.client.get(f"/vault/folders/{folder_id}/contents", headers=hdr)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _second_society(db, superadmin, *, enable_vault=True):
    """A fresh society + society_admin, vault enabled by default."""
    soc = SocietyService(db).create_society(
        SocietyCreate(
            name="Society B",
            storage_limit_bytes=5 * 1024**3,
            default_member_password=DEFAULT_MEMBER_PASSWORD,
        ),
        actor_user_id=superadmin.id,
    )
    db.commit()
    db.refresh(soc)
    admin_b = UserProvisioningService(db).create_or_link_user(
        email="adminb@test.local",
        society_id=soc.id,
        role_key="society_admin",
        profile={"full_name": "Admin B"},
        actor_user_id=superadmin.id,
    )
    db.commit()
    db.refresh(admin_b)
    if enable_vault:
        _enable_vault(db, soc, superadmin)
    return soc, admin_b


def _set_limit(db, society_id, n) -> None:
    from sqlalchemy import text

    db.execute(
        text("UPDATE societies SET storage_limit_bytes=:n WHERE id=:i"),
        {"n": n, "i": society_id},
    )
    db.commit()


def _grant_read_only(db, society, superadmin) -> None:
    """Enable vault, then strip vault.manage from society_admin (read-only)."""
    from sqlalchemy import text

    _enable_vault(db, society, superadmin)
    role = RoleRepository(db).society_role_by_key(society.id, "society_admin")
    perm_id = db.execute(
        text("SELECT id FROM permissions WHERE key=:k"), {"k": PERM_MANAGE}
    ).scalar_one()
    db.execute(
        text("DELETE FROM role_permissions WHERE role_id=:r AND permission_id=:p"),
        {"r": role.id, "p": perm_id},
    )
    db.commit()


def _audit(db, action, society_id=None, entity_id=None):
    q = db.query(AuditLog).filter(AuditLog.action == action)
    if society_id is not None:
        q = q.filter(AuditLog.society_id == society_id)
    if entity_id is not None:
        q = q.filter(AuditLog.entity_id == entity_id)
    return q.all()


def _make_house(auth, hdr, floors=None, names=None) -> list[dict]:
    """building type -> one building mapped AUTO -> returns the houses JSON.

    Default: floors=[{"level":1,"houses_count":2}] -> numbers "101","102",
    display codes "A-101"/"A-102".
    """
    if floors is None:
        floors = [{"level": 1, "houses_count": 2}]
    if names is None:
        names = ["A"]
    r = auth.client.post("/onboarding/type", headers=hdr, json={"type": "building"})
    assert r.status_code == 200, r.text
    r = auth.client.post("/onboarding/buildings", headers=hdr, json={"names": names})
    assert r.status_code == 200, r.text
    building = r.json()[0]
    r = auth.client.post(
        f"/onboarding/buildings/{building['id']}/map",
        headers=hdr,
        json={
            "floors": floors,
            "numbering_config": {"mode": "auto", "count_pad": 2, "ground_prefix": "G"},
        },
    )
    assert r.status_code == 200, r.text
    return r.json()
