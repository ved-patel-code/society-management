"""Roles/permissions, request gates, and user-provisioning (incl. dual-role) tests.

Covers docs/PF §5 (roles/perms/union), §5.1 (dual-role/portals), §8
(provisioning, create_or_link_user, one-society-per-user, revoke_house_access),
§14.8. Uses the shared harness (truncate-and-reseed per test); asserts on status
codes, response bodies, AND the resulting DB state.

Endpoints are driven over HTTP (client + auth); pure service/edge behaviour with
no endpoint (revoke_house_access, remove_role last-admin warn) is driven through
the services directly on the ``db`` session, then committed.
"""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.common.errors import DomainError
from app.core.deps import AuthContext, require_permission
from app.core.security import create_access_token
from app.platform.models import (
    AuditLog,
    Permission,
    RefreshToken,
    Role,
    RoleModuleVisibility,
    RolePermission,
    Society,
    SocietyModule,
    User,
    UserRole,
)
from app.platform.roles.service import RoleService
from app.platform.societies.schemas import ModuleAllocation, SocietyCreate
from app.platform.societies.service import SocietyService
from app.platform.users.provisioning import UserProvisioningService
from tests.conftest import (
    DEFAULT_MEMBER_PASSWORD,
    SUPERADMIN_EMAIL,
    SUPERADMIN_PASSWORD,
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _super_headers(auth) -> dict[str, str]:
    """Log the seeded super-admin in and return a Bearer header."""
    tokens = auth.login_ok(SUPERADMIN_EMAIL, SUPERADMIN_PASSWORD)
    return auth.bearer(tokens["access_token"])


def _add_permission(db, key: str, module_key: str = "testmod") -> Permission:
    """Insert a Permission row (the foundation seed creates none) + commit."""
    perm = Permission(key=key, module_key=module_key, description=f"perm {key}")
    db.add(perm)
    db.commit()
    db.refresh(perm)
    return perm


def _activate_password(auth, email: str) -> None:
    """Take a provisioned (must_change) user to password_state=active.

    Logs in with the default member password, then changes it. After a change
    all sessions are revoked, so callers re-login for a fresh pair.
    """
    tokens = auth.login_ok(email, DEFAULT_MEMBER_PASSWORD)
    resp = auth.client.post(
        "/auth/change-password",
        headers=auth.bearer(tokens["access_token"]),
        json={
            "current_password": DEFAULT_MEMBER_PASSWORD,
            "new_password": "NewPass456",
        },
    )
    assert resp.status_code == 200, resp.text


# --------------------------------------------------------------------------- #
# 1. Create custom role (+ set permissions, audit, auth)
# --------------------------------------------------------------------------- #


def test_super_admin_creates_society_scoped_custom_role(client, auth, db, society):
    headers = _super_headers(auth)
    resp = client.post(
        f"/admin/societies/{society.id}/roles",
        headers=headers,
        json={"key": "tenant", "name": "Tenant", "portal": "resident"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["key"] == "tenant"
    assert body["portal"] == "resident"
    assert body["society_id"] == society.id
    assert body["is_system"] is False
    assert body["scope"] == "society"
    assert body["permission_keys"] == []

    # DB: society-scoped row exists (society_id set, not a global template).
    role = db.execute(
        select(Role).where(Role.society_id == society.id, Role.key == "tenant")
    ).scalar_one()
    assert role.society_id == society.id
    assert role.is_system is False


def test_create_role_non_super_admin_forbidden(client, auth, db, society, admin_user):
    _activate_password(auth, admin_user.email)
    tokens = auth.login_ok(admin_user.email, "NewPass456")
    resp = client.post(
        f"/admin/societies/{society.id}/roles",
        headers=auth.bearer(tokens["access_token"]),
        json={"key": "tenant", "name": "Tenant", "portal": "resident"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "permission_denied"


def test_create_role_no_auth_unauthorized(client, society):
    resp = client.post(
        f"/admin/societies/{society.id}/roles",
        json={"key": "tenant", "name": "Tenant", "portal": "resident"},
    )
    assert resp.status_code == 401


def test_set_role_permissions_updates_and_audits(client, auth, db, society):
    headers = _super_headers(auth)
    # Synthetic key not owned by any registered module — the baseline seed now
    # inserts every registered module's permissions (incl. houses.*), so a real
    # module key would clash with the seed on this fresh-society insert.
    _add_permission(db, "testmod.assignable", module_key="testmod")

    role_resp = client.post(
        f"/admin/societies/{society.id}/roles",
        headers=headers,
        json={"key": "warden", "name": "Warden", "portal": "admin"},
    )
    assert role_resp.status_code == 201, role_resp.text
    role_id = role_resp.json()["id"]

    resp = client.put(
        f"/admin/roles/{role_id}/permissions",
        headers=headers,
        json={"permission_keys": ["testmod.assignable"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["permission_keys"] == ["testmod.assignable"]

    # DB: role_permissions row written.
    perm = db.execute(
        select(Permission).where(Permission.key == "testmod.assignable")
    ).scalar_one()
    rp = db.execute(
        select(RolePermission).where(
            RolePermission.role_id == role_id,
            RolePermission.permission_id == perm.id,
        )
    ).scalar_one_or_none()
    assert rp is not None

    # Audit: permission.set_changed with before (empty) / after (the new set).
    audit = db.execute(
        select(AuditLog)
        .where(
            AuditLog.action == "permission.set_changed",
            AuditLog.entity_id == role_id,
        )
        .order_by(AuditLog.id.desc())
    ).scalars().first()
    assert audit is not None
    assert audit.before == {"permission_keys": []}
    assert audit.after == {"permission_keys": ["testmod.assignable"]}


def test_set_role_permissions_unknown_key_422(client, auth, db, society):
    headers = _super_headers(auth)
    role_resp = client.post(
        f"/admin/societies/{society.id}/roles",
        headers=headers,
        json={"key": "warden", "name": "Warden", "portal": "admin"},
    )
    role_id = role_resp.json()["id"]
    resp = client.put(
        f"/admin/roles/{role_id}/permissions",
        headers=headers,
        json={"permission_keys": ["does.not_exist"]},
    )
    assert resp.status_code == 422, resp.text
    assert "does.not_exist" in resp.json()["details"]["unknown_permission_keys"]


# --------------------------------------------------------------------------- #
# 2. Provision new user
# --------------------------------------------------------------------------- #


def test_provision_new_user(client, auth, db, society):
    headers = _super_headers(auth)
    resp = client.post(
        f"/admin/societies/{society.id}/users",
        headers=headers,
        json={"email": "newadmin@test.local", "role_key": "society_admin"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == "newadmin@test.local"
    assert body["password_state"] == "must_change"
    # Response never leaks a password / hash.
    assert "password" not in body
    assert "password_hash" not in body

    user = db.execute(
        select(User).where(User.email == "newadmin@test.local")
    ).scalar_one()
    # Password hash was copied verbatim from the society default (not re-hashed).
    assert user.password_hash == society.default_member_password_hash
    assert user.password_state == "must_change"

    # user_role exists for society_admin in this society.
    admin_role = db.execute(
        select(Role).where(
            Role.society_id == society.id, Role.key == "society_admin"
        )
    ).scalar_one()
    ur = db.execute(
        select(UserRole).where(
            UserRole.user_id == user.id,
            UserRole.society_id == society.id,
            UserRole.role_id == admin_role.id,
        )
    ).scalar_one_or_none()
    assert ur is not None


def test_provision_user_defaults_to_society_admin(client, auth, db, society):
    headers = _super_headers(auth)
    resp = client.post(
        f"/admin/societies/{society.id}/users",
        headers=headers,
        json={"email": "defaultrole@test.local"},
    )
    assert resp.status_code == 201, resp.text
    user = db.execute(
        select(User).where(User.email == "defaultrole@test.local")
    ).scalar_one()
    admin_role = db.execute(
        select(Role).where(
            Role.society_id == society.id, Role.key == "society_admin"
        )
    ).scalar_one()
    ur = db.execute(
        select(UserRole).where(
            UserRole.user_id == user.id, UserRole.role_id == admin_role.id
        )
    ).scalar_one_or_none()
    assert ur is not None


def test_provision_non_super_forbidden(client, auth, society, admin_user):
    _activate_password(auth, admin_user.email)
    tokens = auth.login_ok(admin_user.email, "NewPass456")
    resp = client.post(
        f"/admin/societies/{society.id}/users",
        headers=auth.bearer(tokens["access_token"]),
        json={"email": "x@test.local"},
    )
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# 3. Dual-role (link existing email in same society)
# --------------------------------------------------------------------------- #


def test_dual_role_links_same_account(client, auth, db, society):
    headers = _super_headers(auth)
    email = "dual@test.local"

    r1 = client.post(
        f"/admin/societies/{society.id}/users",
        headers=headers,
        json={"email": email, "role_key": "society_admin"},
    )
    assert r1.status_code == 201, r1.text
    user_id = r1.json()["id"]

    r2 = client.post(
        f"/admin/societies/{society.id}/users",
        headers=headers,
        json={"email": email, "role_key": "resident"},
    )
    assert r2.status_code == 201, r2.text
    # SAME user id — no duplicate login.
    assert r2.json()["id"] == user_id

    # Exactly one users row for that email.
    users = db.execute(select(User).where(User.email == email)).scalars().all()
    assert len(users) == 1

    # Two user_roles: society_admin AND resident.
    roles = db.execute(
        select(Role.key)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id, UserRole.society_id == society.id)
    ).scalars().all()
    assert set(roles) == {"society_admin", "resident"}


# --------------------------------------------------------------------------- #
# 4. One-society-per-user
# --------------------------------------------------------------------------- #


def test_one_society_per_user_conflict(client, auth, db, society, superadmin):
    headers = _super_headers(auth)
    email = "roamer@test.local"
    r1 = client.post(
        f"/admin/societies/{society.id}/users",
        headers=headers,
        json={"email": email, "role_key": "society_admin"},
    )
    assert r1.status_code == 201, r1.text

    # A second society (created via the service so its roles are copied).
    second = SocietyService(db).create_society(
        SocietyCreate(
            name="Second Society",
            storage_limit_bytes=1_000_000,
            default_member_password=DEFAULT_MEMBER_PASSWORD,
        ),
        actor_user_id=superadmin.id,
    )
    db.commit()

    r2 = client.post(
        f"/admin/societies/{second.id}/users",
        headers=headers,
        json={"email": email, "role_key": "society_admin"},
    )
    assert r2.status_code == 409, r2.text
    assert r2.json()["code"] == "conflict"


# --------------------------------------------------------------------------- #
# 5. assign_role endpoint (adds + idempotent)
# --------------------------------------------------------------------------- #


def test_assign_role_endpoint_adds_and_is_idempotent(client, auth, db, society, admin_user):
    headers = _super_headers(auth)

    # admin_user starts with society_admin only; add resident.
    r1 = client.post(
        f"/admin/users/{admin_user.id}/roles",
        headers=headers,
        json={"society_id": society.id, "role_key": "resident"},
    )
    assert r1.status_code == 201, r1.text

    resident_role = db.execute(
        select(Role).where(Role.society_id == society.id, Role.key == "resident")
    ).scalar_one()
    count = db.execute(
        select(UserRole).where(
            UserRole.user_id == admin_user.id,
            UserRole.role_id == resident_role.id,
        )
    ).scalars().all()
    assert len(count) == 1

    # Re-assign the same role: idempotent — still one row, no error.
    r2 = client.post(
        f"/admin/users/{admin_user.id}/roles",
        headers=headers,
        json={"society_id": society.id, "role_key": "resident"},
    )
    assert r2.status_code == 201, r2.text
    count2 = db.execute(
        select(UserRole).where(
            UserRole.user_id == admin_user.id,
            UserRole.role_id == resident_role.id,
        )
    ).scalars().all()
    assert len(count2) == 1


def test_assign_role_unknown_role_404(client, auth, society, admin_user):
    headers = _super_headers(auth)
    resp = client.post(
        f"/admin/users/{admin_user.id}/roles",
        headers=headers,
        json={"society_id": society.id, "role_key": "ghost"},
    )
    assert resp.status_code == 404, resp.text


# --------------------------------------------------------------------------- #
# 6. Deactivate (revokes tokens; reactivation rejected)
# --------------------------------------------------------------------------- #


def test_deactivate_revokes_refresh_tokens(client, auth, db, society, admin_user):
    # Get the admin to active state, then log in for a live session.
    _activate_password(auth, admin_user.email)
    tokens = auth.login_ok(admin_user.email, "NewPass456")
    refresh = tokens["refresh_token"]

    # Sanity: the refresh works before deactivation.
    pre = client.post("/auth/refresh", json={"refresh_token": refresh})
    assert pre.status_code == 200, pre.text
    refresh = pre.json()["refresh_token"]  # rotated

    headers = _super_headers(auth)
    resp = client.patch(
        f"/admin/users/{admin_user.id}",
        headers=headers,
        json={"is_active": False},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is False

    db.expire_all()
    user = db.get(User, admin_user.id)
    assert user.is_active is False

    # All refresh tokens revoked → subsequent refresh is rejected.
    post = client.post("/auth/refresh", json={"refresh_token": refresh})
    assert post.status_code == 401, post.text


def test_reactivation_rejected_422(client, auth, admin_user):
    headers = _super_headers(auth)
    resp = client.patch(
        f"/admin/users/{admin_user.id}",
        headers=headers,
        json={"is_active": True},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["details"]["field"] == "is_active"


# --------------------------------------------------------------------------- #
# 7. Effective permissions = union across two roles (require_permission gate)
# --------------------------------------------------------------------------- #


def _guarded_app(perm_key: str) -> FastAPI:
    """Throwaway app with one route gated by require_permission(perm_key)."""
    app = FastAPI()

    @app.exception_handler(DomainError)
    async def _handler(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    @app.get("/guarded")
    def _guarded(auth: AuthContext = Depends(require_permission(perm_key))):
        return {"ok": True, "user_id": auth.user_id}

    return app


def test_effective_permissions_are_union_of_two_roles(
    db, society, superadmin, make_token
):
    # Two permissions in the catalog.
    _add_permission(db, "alpha.do")
    _add_permission(db, "beta.do")

    roles = RoleService(db)
    role_a = roles.create_role(
        society_id=society.id, key="role_a", name="Role A", portal="admin",
        scope="society", permission_keys=["alpha.do"], actor_user_id=superadmin.id,
    )
    role_b = roles.create_role(
        society_id=society.id, key="role_b", name="Role B", portal="admin",
        scope="society", permission_keys=["beta.do"], actor_user_id=superadmin.id,
    )

    holder = UserProvisioningService(db).create_or_link_user(
        email="union@test.local", society_id=society.id, role_key="role_a",
        profile={}, actor_user_id=superadmin.id,
    )
    # Add the second role to the same user (union of alpha + beta).
    UserProvisioningService(db).assign_role(
        user_id=holder.id, society_id=society.id, role_key="role_b",
        actor_user_id=superadmin.id,
    )
    db.commit()

    token = make_token(
        user_id=holder.id, active_society_id=society.id,
        role_ids=[role_a.id, role_b.id],
    )

    # A route gated on beta.do (held only via role_b) admits the union holder.
    c_beta = TestClient(_guarded_app("beta.do"), raise_server_exceptions=False)
    resp_beta = c_beta.get("/guarded", headers={"Authorization": f"Bearer {token}"})
    assert resp_beta.status_code == 200, resp_beta.text

    # A route gated on alpha.do (held only via role_a) also admits — proving union.
    c_alpha = TestClient(_guarded_app("alpha.do"), raise_server_exceptions=False)
    resp_alpha = c_alpha.get("/guarded", headers={"Authorization": f"Bearer {token}"})
    assert resp_alpha.status_code == 200, resp_alpha.text

    # A non-holder (only role_a) is denied on the beta.do route.
    loner = UserProvisioningService(db).create_or_link_user(
        email="loner@test.local", society_id=society.id, role_key="role_a",
        profile={}, actor_user_id=superadmin.id,
    )
    db.commit()
    loner_token = make_token(
        user_id=loner.id, active_society_id=society.id, role_ids=[role_a.id],
    )
    resp_denied = c_beta.get(
        "/guarded", headers={"Authorization": f"Bearer {loner_token}"}
    )
    assert resp_denied.status_code == 403, resp_denied.text
    assert resp_denied.json()["details"]["required_permission"] == "beta.do"


# --------------------------------------------------------------------------- #
# 8. require_module gate
# --------------------------------------------------------------------------- #


def test_require_module_denies_then_passes_and_super_bypasses(
    db, society, superadmin, make_token
):
    from app.common.errors import ModuleDisabledError
    from app.core.deps import require_module

    # A user with a role so they have an active society context.
    user = UserProvisioningService(db).create_or_link_user(
        email="mod@test.local", society_id=society.id, role_key="resident",
        profile={}, actor_user_id=superadmin.id,
    )
    db.commit()
    resident_role = db.execute(
        select(Role).where(Role.society_id == society.id, Role.key == "resident")
    ).scalar_one()

    auth_ctx = AuthContext(
        user=user, user_id=user.id, active_society_id=society.id,
        role_ids=[resident_role.id], password_state="active",
        is_super_admin=False, permission_keys=set(),
    )

    dep = require_module("platform")

    # No SocietyModule row for 'platform' → denied.
    with pytest.raises(ModuleDisabledError) as exc:
        dep(auth=auth_ctx, session=db)
    assert exc.value.status_code == 403
    assert exc.value.details["module_key"] == "platform"

    # Enable 'platform' via the real service path, then it passes.
    SocietyService(db).set_modules(
        society.id,
        [ModuleAllocation(module_key="platform", enabled=True)],
        actor_user_id=superadmin.id,
    )
    db.commit()
    returned = dep(auth=auth_ctx, session=db)
    assert returned is auth_ctx

    # super_admin bypasses even with no society / no module row.
    super_ctx = AuthContext(
        user=superadmin, user_id=superadmin.id, active_society_id=None,
        role_ids=[], password_state="active", is_super_admin=True,
        permission_keys=set(),
    )
    assert require_module("anything")(auth=super_ctx, session=db) is super_ctx


# --------------------------------------------------------------------------- #
# 9. Portals view-only
# --------------------------------------------------------------------------- #


def test_single_portal_and_invalid_portal_fallback(
    client, auth, db, society, superadmin
):
    # A resident-only user → available_portals == ['resident'].
    UserProvisioningService(db).create_or_link_user(
        email="ronly@test.local", society_id=society.id, role_key="resident",
        profile={}, actor_user_id=superadmin.id,
    )
    db.commit()
    _activate_password(auth, "ronly@test.local")
    tokens = auth.login_ok("ronly@test.local", "NewPass456")
    headers = auth.bearer(tokens["access_token"])

    # Invalid portal query → falls back to the sole portal (resident).
    resp = client.get("/me?portal=bogus", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available_portals"] == ["resident"]
    assert body["active_portal"] == "resident"


def test_dual_portal_user_union_permissions_unchanged_across_portals(
    client, auth, db, society, superadmin
):
    # One account: society_admin AND resident (two portals).
    email = "dualportal@test.local"
    UserProvisioningService(db).create_or_link_user(
        email=email, society_id=society.id, role_key="society_admin",
        profile={}, actor_user_id=superadmin.id,
    )
    UserProvisioningService(db).create_or_link_user(
        email=email, society_id=society.id, role_key="resident",
        profile={}, actor_user_id=superadmin.id,
    )
    db.commit()

    _activate_password(auth, email)
    tokens = auth.login_ok(email, "NewPass456")
    headers = auth.bearer(tokens["access_token"])
    assert set(tokens["available_portals"]) == {"admin", "resident"}

    resp_admin = client.get("/me?portal=admin", headers=headers).json()
    resp_resident = client.get("/me?portal=resident", headers=headers).json()

    assert resp_admin["active_portal"] == "admin"
    assert resp_resident["active_portal"] == "resident"
    assert set(resp_admin["available_portals"]) == {"admin", "resident"}

    # Landing differs by portal (view concept), permissions do NOT (union unchanged).
    assert resp_admin["landing"] != resp_resident["landing"]
    assert resp_admin["permissions"] == resp_resident["permissions"]


# --------------------------------------------------------------------------- #
# 10. revoke_house_access (service-level skeleton)
# --------------------------------------------------------------------------- #


def test_revoke_house_access_keeps_user_with_remaining_role(
    auth, db, society, superadmin
):
    # A user who KEEPS a role → tokens revoked but NOT deactivated.
    email = "keeprole@test.local"
    user = UserProvisioningService(db).create_or_link_user(
        email=email, society_id=society.id, role_key="resident",
        profile={}, actor_user_id=superadmin.id,
    )
    db.commit()

    _activate_password(auth, email)
    tokens = auth.login_ok(email, "NewPass456")
    refresh = tokens["refresh_token"]

    UserProvisioningService(db).revoke_house_access(
        user_id=user.id, house_id=999, actor_user_id=superadmin.id
    )
    db.commit()

    db.expire_all()
    reloaded = db.get(User, user.id)
    assert reloaded.is_active is True  # still has the resident role → not orphaned

    # Refresh tokens revoked.
    active = db.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)
        )
    ).scalars().all()
    assert active == []
    post = auth.client.post("/auth/refresh", json={"refresh_token": refresh})
    assert post.status_code == 401

    # Audit house.access_revoked, orphaned False.
    audit = db.execute(
        select(AuditLog).where(
            AuditLog.action == "house.access_revoked",
            AuditLog.entity_id == user.id,
        )
    ).scalars().first()
    assert audit is not None
    assert audit.after["orphaned"] is False
    assert audit.after["deactivated"] is False


def test_revoke_house_access_deactivates_orphaned_user(db, society, superadmin):
    # A user with NO remaining roles → orphaned → deactivated.
    user = User(
        email="orphan@test.local",
        password_hash=society.default_member_password_hash,
        password_state="active",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    UserProvisioningService(db).revoke_house_access(
        user_id=user.id, house_id=1, actor_user_id=superadmin.id
    )
    db.commit()

    db.expire_all()
    reloaded = db.get(User, user.id)
    assert reloaded.is_active is False  # orphaned → deactivated

    audit = db.execute(
        select(AuditLog).where(
            AuditLog.action == "house.access_revoked",
            AuditLog.entity_id == user.id,
        )
    ).scalars().first()
    assert audit is not None
    assert audit.after["orphaned"] is True
    assert audit.after["deactivated"] is True


# --------------------------------------------------------------------------- #
# 11. Last-admin warn (H2) — remove_role empties the admin set
# --------------------------------------------------------------------------- #


def test_removing_sole_admin_records_admin_emptied(db, society, superadmin):
    # Provision a sole society_admin, then remove that role at the service level.
    admin = UserProvisioningService(db).create_or_link_user(
        email="soleadmin@test.local", society_id=society.id, role_key="society_admin",
        profile={}, actor_user_id=superadmin.id,
    )
    db.commit()

    UserProvisioningService(db).remove_role(
        user_id=admin.id, society_id=society.id, role_key="society_admin",
        actor_user_id=superadmin.id,
    )
    db.commit()

    # role.removed audit exists AND the emptied-admin warn fired.
    emptied = db.execute(
        select(AuditLog).where(
            AuditLog.action == "society.admin_emptied",
            AuditLog.society_id == society.id,
        )
    ).scalars().first()
    assert emptied is not None
    assert emptied.after["active_holders"] == 0

    # The role really was removed.
    admin_role = db.execute(
        select(Role).where(
            Role.society_id == society.id, Role.key == "society_admin"
        )
    ).scalar_one()
    ur = db.execute(
        select(UserRole).where(
            UserRole.user_id == admin.id, UserRole.role_id == admin_role.id
        )
    ).scalar_one_or_none()
    assert ur is None
