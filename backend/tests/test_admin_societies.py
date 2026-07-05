"""QA suite for SUPER-ADMIN society + module-allocation endpoints (docs/PF §3/§6/§14).

Covers happy paths, validation, duplicate names, auth/permission gating, list
pagination, get/patch, and module allocation with depends_on enforcement + the
M5 idempotent re-PUT audit fix. Asserts status codes, response bodies, AND DB
state (societies, roles, society_modules, audit_log).
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.platform.models import AuditLog, Role, Society, SocietyModule
from app.platform.users.provisioning import UserProvisioningService
from tests.conftest import (
    DEFAULT_MEMBER_PASSWORD,
    SUPERADMIN_EMAIL,
    SUPERADMIN_PASSWORD,
)


# --- helpers ---------------------------------------------------------------


def _su_headers(auth) -> dict[str, str]:
    tok = auth.login_ok(SUPERADMIN_EMAIL, SUPERADMIN_PASSWORD)["access_token"]
    return auth.bearer(tok)


def _create_body(**overrides) -> dict:
    body = {
        "name": "Acme Residency",
        "storage_limit_bytes": 5 * 1024**3,
        "default_member_password": DEFAULT_MEMBER_PASSWORD,
    }
    body.update(overrides)
    return body


# --- create: happy ---------------------------------------------------------


def test_create_society_happy(client, auth, db):
    resp = client.post(
        "/admin/societies", json=_create_body(name="Green Meadows"),
        headers=_su_headers(auth),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    # Response shape: type null, status onboarding, NO password field.
    assert body["type"] is None
    assert body["status"] == "onboarding"
    assert body["name"] == "Green Meadows"
    assert "default_member_password" not in body
    assert "default_member_password_hash" not in body
    assert "password" not in body

    # DB: Argon2id default password hash stored.
    soc = db.get(Society, body["id"])
    assert soc is not None
    assert soc.default_member_password_hash.startswith("$argon2")

    # Roles-by-copy: society_admin + resident copied as society-scoped rows;
    # super_admin NOT copied into the society.
    society_roles = db.execute(
        select(Role).where(Role.society_id == soc.id)
    ).scalars().all()
    keys = {r.key for r in society_roles}
    assert keys == {"society_admin", "resident"}
    for r in society_roles:
        assert r.society_id == soc.id
    assert "super_admin" not in keys


def test_create_society_writes_created_audit(client, auth, db):
    resp = client.post(
        "/admin/societies", json=_create_body(), headers=_su_headers(auth)
    )
    sid = resp.json()["id"]
    row = db.execute(
        select(AuditLog).where(
            AuditLog.action == "society.created", AuditLog.entity_id == sid
        )
    ).scalar_one()
    assert row.entity_type == "society"
    assert row.after["status"] == "onboarding"


# --- create: validation ----------------------------------------------------


def test_create_missing_password_422(client, auth):
    body = _create_body()
    del body["default_member_password"]
    resp = client.post("/admin/societies", json=body, headers=_su_headers(auth))
    assert resp.status_code == 422, resp.text


def test_create_weak_password_rejected(client, auth):
    # 'abc' fails policy (too short + no digit). Service raises ValidationError -> 422.
    resp = client.post(
        "/admin/societies",
        json=_create_body(default_member_password="abc"),
        headers=_su_headers(auth),
    )
    assert resp.status_code in (400, 422), resp.text


def test_create_nonpositive_storage_422(client, auth):
    resp = client.post(
        "/admin/societies",
        json=_create_body(storage_limit_bytes=0),
        headers=_su_headers(auth),
    )
    assert resp.status_code == 422, resp.text


def test_create_empty_name_422(client, auth):
    resp = client.post(
        "/admin/societies", json=_create_body(name=""), headers=_su_headers(auth)
    )
    assert resp.status_code == 422, resp.text


# --- create: duplicate names allowed ---------------------------------------


def test_create_duplicate_names_allowed(client, auth):
    headers = _su_headers(auth)
    r1 = client.post(
        "/admin/societies", json=_create_body(name="Twin Towers"), headers=headers
    )
    r2 = client.post(
        "/admin/societies", json=_create_body(name="Twin Towers"), headers=headers
    )
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 201, r2.text
    assert r1.json()["id"] != r2.json()["id"]


# --- create: auth / permission ---------------------------------------------


def test_create_no_token_401(client):
    resp = client.post("/admin/societies", json=_create_body())
    assert resp.status_code == 401, resp.text


def test_create_non_super_admin_403(client, auth, db, society, superadmin):
    # Provision a society_admin, activate them (bypass must_change lockout), log in.
    user = UserProvisioningService(db).create_or_link_user(
        email="notsuper@test.local",
        society_id=society.id,
        role_key="society_admin",
        profile={"full_name": "Regular Admin"},
        actor_user_id=superadmin.id,
    )
    from app.core.security import hash_password

    user.password_state = "active"
    user.password_hash = hash_password("Passw0rd123")
    db.commit()

    token = auth.login_ok("notsuper@test.local", "Passw0rd123")["access_token"]
    resp = client.post(
        "/admin/societies", json=_create_body(), headers=auth.bearer(token)
    )
    assert resp.status_code == 403, resp.text


# --- list ------------------------------------------------------------------


def test_list_societies_paginated_newest_first(client, auth):
    headers = _su_headers(auth)
    ids = []
    for i in range(3):
        r = client.post(
            "/admin/societies", json=_create_body(name=f"Soc {i}"), headers=headers
        )
        ids.append(r.json()["id"])

    resp = client.get("/admin/societies", headers=headers)
    assert resp.status_code == 200, resp.text
    page = resp.json()
    assert page["total"] >= 3
    assert page["page"] == 1
    # Newest first: the last-created id appears before earlier ones.
    returned = [item["id"] for item in page["items"]]
    assert returned == sorted(returned, reverse=True)
    assert returned[0] == max(ids)


def test_list_respects_page_size(client, auth):
    headers = _su_headers(auth)
    for i in range(3):
        client.post(
            "/admin/societies", json=_create_body(name=f"P {i}"), headers=headers
        )
    resp = client.get(
        "/admin/societies", params={"page": 1, "page_size": 2}, headers=headers
    )
    assert resp.status_code == 200
    page = resp.json()
    assert page["page_size"] == 2
    assert len(page["items"]) == 2
    assert page["total"] >= 3


def test_list_no_token_401(client):
    assert client.get("/admin/societies").status_code == 401


# --- get -------------------------------------------------------------------


def test_get_society_ok(client, auth, society):
    resp = client.get(f"/admin/societies/{society.id}", headers=_su_headers(auth))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == society.id
    assert body["name"] == society.name


def test_get_society_unknown_404(client, auth):
    resp = client.get("/admin/societies/999999", headers=_su_headers(auth))
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "not_found"


# --- patch -----------------------------------------------------------------


def test_patch_society_reflects_change_and_audits(client, auth, db, society):
    headers = _su_headers(auth)
    resp = client.patch(
        f"/admin/societies/{society.id}",
        json={
            "name": "Renamed Society",
            "currency": "USD",
            "timezone": "UTC",
            "status": "active",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Renamed Society"
    assert body["currency"] == "USD"
    assert body["timezone"] == "UTC"
    assert body["status"] == "active"

    # Audit society.updated with before/after diff.
    row = db.execute(
        select(AuditLog).where(
            AuditLog.action == "society.updated",
            AuditLog.entity_id == society.id,
        )
    ).scalar_one()
    assert row.before["name"] == "Test Society"
    assert row.after["name"] == "Renamed Society"
    assert row.after["status"] == "active"
    assert row.before["status"] == "onboarding"


def test_patch_invalid_status_rejected(client, auth, society):
    resp = client.patch(
        f"/admin/societies/{society.id}",
        json={"status": "frozen"},
        headers=_su_headers(auth),
    )
    assert resp.status_code in (400, 422), resp.text


def test_patch_unknown_id_404(client, auth):
    resp = client.patch(
        "/admin/societies/999999", json={"name": "X"}, headers=_su_headers(auth)
    )
    assert resp.status_code == 404, resp.text


def test_patch_non_super_admin_403(client, auth, db, society, superadmin):
    user = UserProvisioningService(db).create_or_link_user(
        email="pa@test.local",
        society_id=society.id,
        role_key="society_admin",
        profile={},
        actor_user_id=superadmin.id,
    )
    from app.core.security import hash_password

    user.password_state = "active"
    user.password_hash = hash_password("Passw0rd123")
    db.commit()
    token = auth.login_ok("pa@test.local", "Passw0rd123")["access_token"]
    resp = client.patch(
        f"/admin/societies/{society.id}",
        json={"name": "Nope"},
        headers=auth.bearer(token),
    )
    assert resp.status_code == 403, resp.text


# --- modules: depends_on ---------------------------------------------------


def test_modules_unknown_key_409(client, auth, society):
    resp = client.put(
        f"/admin/societies/{society.id}/modules",
        json={"modules": [{"module_key": "finance", "enabled": True}]},
        headers=_su_headers(auth),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "dependency_error"


def test_modules_enable_platform_success(client, auth, db, society, superadmin):
    resp = client.put(
        f"/admin/societies/{society.id}/modules",
        json={"modules": [{"module_key": "platform", "enabled": True}]},
        headers=_su_headers(auth),
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()
    platform = next(m for m in items if m["module_key"] == "platform")
    assert platform["enabled"] is True
    assert platform["enabled_by"] is not None
    assert platform["enabled_at"] is not None

    row = db.execute(
        select(SocietyModule).where(
            SocietyModule.society_id == society.id,
            SocietyModule.module_key == "platform",
        )
    ).scalar_one()
    assert row.enabled is True
    assert row.enabled_by == superadmin.id
    assert row.enabled_at is not None

    # module.allocated audit written for the new row.
    audit = db.execute(
        select(AuditLog).where(
            AuditLog.action == "module.allocated",
            AuditLog.society_id == society.id,
        )
    ).scalar_one()
    assert audit.entity_type == "society_module"
    assert audit.after["module_key"] == "platform"


def test_modules_idempotent_reput_no_new_audit(client, auth, db, society):
    headers = _su_headers(auth)
    payload = {"modules": [{"module_key": "platform", "enabled": True}]}

    client.put(f"/admin/societies/{society.id}/modules", json=payload, headers=headers)

    def _count(action: str) -> int:
        return db.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.action == action, AuditLog.society_id == society.id
            )
        ).scalar_one()

    allocated_after_first = _count("module.allocated")
    toggled_after_first = _count("module.toggled")

    # Re-PUT identical allocation -> M5 fix: no new module.toggled row.
    r2 = client.put(
        f"/admin/societies/{society.id}/modules", json=payload, headers=headers
    )
    assert r2.status_code == 200, r2.text
    assert _count("module.allocated") == allocated_after_first
    assert _count("module.toggled") == toggled_after_first == 0


def test_modules_config_change_writes_toggled(client, auth, db, society):
    headers = _su_headers(auth)
    client.put(
        f"/admin/societies/{society.id}/modules",
        json={"modules": [{"module_key": "platform", "enabled": True}]},
        headers=headers,
    )

    def _toggled_count() -> int:
        return db.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.action == "module.toggled",
                AuditLog.society_id == society.id,
            )
        ).scalar_one()

    assert _toggled_count() == 0

    # Actually change config -> module.toggled written.
    r = client.put(
        f"/admin/societies/{society.id}/modules",
        json={
            "modules": [
                {"module_key": "platform", "enabled": True, "config": {"x": 1}}
            ]
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert _toggled_count() == 1


# --- modules: auth ---------------------------------------------------------


def test_modules_no_token_401(client, society):
    resp = client.put(
        f"/admin/societies/{society.id}/modules",
        json={"modules": [{"module_key": "platform", "enabled": True}]},
    )
    assert resp.status_code == 401, resp.text


def test_modules_non_super_admin_403(client, auth, db, society, superadmin):
    user = UserProvisioningService(db).create_or_link_user(
        email="modpa@test.local",
        society_id=society.id,
        role_key="society_admin",
        profile={},
        actor_user_id=superadmin.id,
    )
    from app.core.security import hash_password

    user.password_state = "active"
    user.password_hash = hash_password("Passw0rd123")
    db.commit()
    token = auth.login_ok("modpa@test.local", "Passw0rd123")["access_token"]
    resp = client.put(
        f"/admin/societies/{society.id}/modules",
        json={"modules": [{"module_key": "platform", "enabled": True}]},
        headers=auth.bearer(token),
    )
    assert resp.status_code == 403, resp.text
