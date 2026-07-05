"""End-to-end bootstrap flow — the canonical Platform Foundation sequence (docs/PF §2).

Drives the full "cold start → runnable society_admin" journey over HTTP where
possible, asserting status + response body + DB state + the audit trail at each
step:

1. super_admin logs in (available_portals == ['platform']).
2. POST /admin/societies with a default password → 201 (type null, onboarding,
   society_admin + resident roles copied).
3. PUT /admin/societies/{id}/modules enables 'platform' → ok; a bad module → 409.
4. POST /admin/societies/{id}/users creates a society_admin → 201.
5. That admin logs in with the DEFAULT password → must_change; /me blocked (403);
   change-password → active; re-login with the new password → ok; portals==['admin'].
6. Refresh rotation works; reuse of a rotated token → 401 + whole chain revoked
   + auth.token_reuse_detected audited.
7. The audit_log carries the expected foundation rows across the whole flow.

Uses only the shared harness fixtures (conftest).
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.platform.models import (
    AuditLog,
    RefreshToken,
    Role,
    Society,
    SocietyModule,
    User,
    UserRole,
)
from tests.conftest import (
    DEFAULT_MEMBER_PASSWORD,
    SUPERADMIN_EMAIL,
    SUPERADMIN_PASSWORD,
)

_ADMIN_EMAIL = "society-admin@e2e.local"
_NEW_PASSWORD = "MyFreshPass123"


def _audit_count(db, action: str) -> int:
    return db.execute(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == action)
    ).scalar_one()


def test_full_bootstrap_flow(client, auth, db):
    # -- Step 1: super_admin logs in --------------------------------------
    su_login = auth.login_ok(SUPERADMIN_EMAIL, SUPERADMIN_PASSWORD)
    assert su_login["password_state"] == "active"
    assert su_login["available_portals"] == ["platform"]
    su = auth.bearer(su_login["access_token"])

    # -- Step 2: create a society -----------------------------------------
    create = client.post(
        "/admin/societies",
        json={
            "name": "Bootstrap Gardens",
            "storage_limit_bytes": 5 * 1024**3,
            "default_member_password": DEFAULT_MEMBER_PASSWORD,
        },
        headers=su,
    )
    assert create.status_code == 201, create.text
    soc_body = create.json()
    sid = soc_body["id"]
    assert soc_body["type"] is None
    assert soc_body["status"] == "onboarding"
    assert soc_body["name"] == "Bootstrap Gardens"
    assert "default_member_password" not in soc_body
    assert "default_member_password_hash" not in soc_body

    # DB: hashed default password, roles copied (society_admin + resident only).
    soc = db.get(Society, sid)
    assert soc.default_member_password_hash.startswith("$argon2")
    role_keys = {
        k for (k,) in db.execute(
            select(Role.key).where(Role.society_id == sid)
        ).all()
    }
    assert role_keys == {"society_admin", "resident"}

    # -- Step 3: enable 'platform' module; a bad module → 409 -------------
    bad = client.put(
        f"/admin/societies/{sid}/modules",
        json={"modules": [{"module_key": "finance", "enabled": True}]},
        headers=su,
    )
    assert bad.status_code == 409, bad.text
    assert bad.json()["code"] == "dependency_error"

    good = client.put(
        f"/admin/societies/{sid}/modules",
        json={"modules": [{"module_key": "platform", "enabled": True}]},
        headers=su,
    )
    assert good.status_code == 200, good.text
    platform = next(m for m in good.json() if m["module_key"] == "platform")
    assert platform["enabled"] is True
    assert platform["enabled_by"] is not None
    assert platform["enabled_at"] is not None

    module_row = db.execute(
        select(SocietyModule).where(
            SocietyModule.society_id == sid,
            SocietyModule.module_key == "platform",
        )
    ).scalar_one()
    assert module_row.enabled is True

    # -- Step 4: create the society_admin ---------------------------------
    create_admin = client.post(
        f"/admin/societies/{sid}/users",
        json={"email": _ADMIN_EMAIL, "full_name": "Society Admin"},
        headers=su,
    )
    assert create_admin.status_code == 201, create_admin.text
    admin_body = create_admin.json()
    assert admin_body["email"] == _ADMIN_EMAIL
    assert admin_body["password_state"] == "must_change"
    assert admin_body["is_active"] is True
    assert admin_body["is_platform_super_admin"] is False
    admin_id = admin_body["id"]

    # DB: user_role links admin to the society's society_admin role.
    admin_role_id = db.execute(
        select(Role.id).where(
            Role.society_id == sid, Role.key == "society_admin"
        )
    ).scalar_one()
    assert (
        db.execute(
            select(func.count())
            .select_from(UserRole)
            .where(
                UserRole.user_id == admin_id,
                UserRole.society_id == sid,
                UserRole.role_id == admin_role_id,
            )
        ).scalar_one()
        == 1
    )

    # -- Step 5: admin first login → must_change → change → re-login ------
    first = auth.login_ok(_ADMIN_EMAIL, DEFAULT_MEMBER_PASSWORD)
    assert first["password_state"] == "must_change"
    assert first["available_portals"] == ["admin"]
    first_hdr = auth.bearer(first["access_token"])

    # /me blocked while must_change.
    me_blocked = client.get("/me", headers=first_hdr)
    assert me_blocked.status_code == 403, me_blocked.text
    assert me_blocked.json()["code"] == "permission_denied"

    # change-password (new != default) → active.
    cp = client.post(
        "/auth/change-password",
        headers=first_hdr,
        json={
            "current_password": DEFAULT_MEMBER_PASSWORD,
            "new_password": _NEW_PASSWORD,
        },
    )
    assert cp.status_code == 200, cp.text
    db.expire_all()
    assert db.get(User, admin_id).password_state == "active"

    # Old default password no longer works.
    assert auth.login(_ADMIN_EMAIL, DEFAULT_MEMBER_PASSWORD).status_code == 401

    # Re-login with the new password → active, portals == ['admin'], /me works.
    relogin = auth.login_ok(_ADMIN_EMAIL, _NEW_PASSWORD)
    assert relogin["password_state"] == "active"
    assert relogin["available_portals"] == ["admin"]
    relogin_hdr = auth.bearer(relogin["access_token"])

    me_ok = client.get("/me", headers=relogin_hdr)
    assert me_ok.status_code == 200, me_ok.text
    me_view = me_ok.json()
    assert me_view["user"]["email"] == _ADMIN_EMAIL
    assert me_view["active_society_id"] == sid
    assert me_view["available_portals"] == ["admin"]
    assert me_view["active_portal"] == "admin"

    # -- Step 6: refresh rotation + reuse-is-theft ------------------------
    old_refresh = relogin["refresh_token"]
    rot = client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert rot.status_code == 200, rot.text
    new_refresh = rot.json()["refresh_token"]
    assert new_refresh != old_refresh
    assert rot.json()["access_token"]

    # Reuse of the rotated-away token → 401 + theft response.
    reuse = client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert reuse.status_code == 401, reuse.text

    # Whole chain revoked: even the freshly-minted token is now dead.
    dead = client.post("/auth/refresh", json={"refresh_token": new_refresh})
    assert dead.status_code == 401, dead.text

    db.expire_all()
    live = db.execute(
        select(func.count())
        .select_from(RefreshToken)
        .where(
            RefreshToken.user_id == admin_id,
            RefreshToken.revoked_at.is_(None),
        )
    ).scalar_one()
    assert live == 0

    # NOTE: reusing old_refresh trips theft (revokes the chain); the follow-up
    # attempt with new_refresh is ALSO a reuse of a now-revoked token, so a second
    # theft row is expected. Assert at least one, all with the right reason.
    theft_rows = db.execute(
        select(AuditLog).where(
            AuditLog.action == "auth.token_reuse_detected",
            AuditLog.actor_user_id == admin_id,
        )
    ).scalars().all()
    assert len(theft_rows) >= 1
    assert all(r.after["reason"] == "refresh_token_reuse" for r in theft_rows)

    # -- Step 7: audit trail carries the expected rows --------------------
    # society.created (1), role.created (2 — society_admin + resident copied),
    # user.created (1), role.assigned (1), module.allocated (1),
    # user.password_changed (1).
    assert _audit_count(db, "society.created") == 1
    assert _audit_count(db, "role.created") == 2
    assert _audit_count(db, "user.created") == 1
    assert _audit_count(db, "role.assigned") == 1
    assert _audit_count(db, "module.allocated") == 1
    assert _audit_count(db, "user.password_changed") == 1

    # The society.created audit references the right entity + status.
    soc_created = db.execute(
        select(AuditLog).where(
            AuditLog.action == "society.created", AuditLog.entity_id == sid
        )
    ).scalar_one()
    assert soc_created.entity_type == "society"
    assert soc_created.after["status"] == "onboarding"

    # The two role.created rows are BOTH scoped to this society and cover both keys.
    role_created = db.execute(
        select(AuditLog).where(
            AuditLog.action == "role.created", AuditLog.society_id == sid
        )
    ).scalars().all()
    assert {r.after["key"] for r in role_created} == {"society_admin", "resident"}

    # user.password_changed is attributed to the admin acting on themselves.
    pw_changed = db.execute(
        select(AuditLog).where(AuditLog.action == "user.password_changed")
    ).scalar_one()
    assert pw_changed.actor_user_id == admin_id
    assert pw_changed.entity_id == admin_id
