"""Phase-0 green-gate smoke tests for the Onboarding module core.

Proves the frozen core wires end-to-end BEFORE the wave sub-agents fill in the
generation logic: permissions seed, the module enables + auto-grants
onboarding.* to society_admin, type selection works through the real HTTP stack,
and /me reports the blocking-wizard flag. Deeper coverage (numbering, resume,
overrides, completion, security matrix, e2e) lands in the Phase-3 test gate.
"""
from __future__ import annotations

from app.core.registry import MODULE_REGISTRY
from app.modules.onboarding.spec import MODULE_KEY, PERM_MANAGE, PERM_READ
from app.platform.models import Permission, Society
from app.platform.societies.service import SocietyService
from app.platform.societies.schemas import ModuleAllocation
from tests.conftest import DEFAULT_MEMBER_PASSWORD


def _enable_onboarding(db, society, superadmin):
    SocietyService(db).set_modules(
        society.id,
        [ModuleAllocation(module_key=MODULE_KEY, enabled=True, config={})],
        actor_user_id=superadmin.id,
    )
    db.commit()


# --- registry / seeding ----------------------------------------------------

def test_onboarding_spec_registered_with_permissions():
    spec = MODULE_REGISTRY.get(MODULE_KEY)
    assert spec is not None
    keys = {p.key for p in spec.permissions}
    assert keys == {PERM_MANAGE, PERM_READ}
    # Default grant targets the society_admin role only (onboarding is admin-only).
    assert spec.default_role_permissions == {"society_admin": [PERM_MANAGE, PERM_READ]}


def test_permissions_seeded(db):
    keys = {k for (k,) in db.query(Permission.key).all()}
    assert {PERM_MANAGE, PERM_READ} <= keys


# --- auto-grant on enable --------------------------------------------------

def test_enabling_module_grants_default_perms_to_admin(
    db, society, admin_user, superadmin, auth
):
    _enable_onboarding(db, society, superadmin)

    tokens = auth.login_ok(admin_user.email, DEFAULT_MEMBER_PASSWORD)
    # Fresh admin is must_change — change password to get a usable session.
    resp = auth.client.post(
        "/auth/change-password",
        headers=auth.bearer(tokens["access_token"]),
        json={"current_password": DEFAULT_MEMBER_PASSWORD, "new_password": "NewPass123"},
    )
    assert resp.status_code == 200, resp.text

    me_tokens = auth.login_ok(admin_user.email, "NewPass123")
    me = auth.client.get(
        "/me", headers=auth.bearer(me_tokens["access_token"])
    ).json()
    assert PERM_MANAGE in me["permissions"]
    assert PERM_READ in me["permissions"]


def test_enable_is_idempotent_no_duplicate_grants(db, society, admin_user, superadmin):
    _enable_onboarding(db, society, superadmin)
    _enable_onboarding(db, society, superadmin)  # second enable = no-op grant
    # society_admin should hold exactly the two onboarding perms, no dupes.
    from app.platform.roles.repository import RoleRepository

    role = RoleRepository(db).society_role_by_key(society.id, "society_admin")
    keys = RoleRepository(db).role_permission_keys(role.id)
    assert sorted(keys) == [PERM_MANAGE, PERM_READ]


# --- blocking wizard signal in /me -----------------------------------------

def test_me_reports_onboarding_required_while_onboarding(
    db, society, admin_user, superadmin, auth
):
    _enable_onboarding(db, society, superadmin)
    tokens = auth.login_ok(admin_user.email, DEFAULT_MEMBER_PASSWORD)
    auth.client.post(
        "/auth/change-password",
        headers=auth.bearer(tokens["access_token"]),
        json={"current_password": DEFAULT_MEMBER_PASSWORD, "new_password": "NewPass123"},
    )
    me_tokens = auth.login_ok(admin_user.email, "NewPass123")
    me = auth.client.get("/me", headers=auth.bearer(me_tokens["access_token"])).json()
    assert me["onboarding_required"] is True


# --- type selection through the real stack ---------------------------------

def test_select_type_sets_type_and_advances_step(
    db, society, admin_user, superadmin, auth
):
    _enable_onboarding(db, society, superadmin)
    tokens = auth.login_ok(admin_user.email, DEFAULT_MEMBER_PASSWORD)
    auth.client.post(
        "/auth/change-password",
        headers=auth.bearer(tokens["access_token"]),
        json={"current_password": DEFAULT_MEMBER_PASSWORD, "new_password": "NewPass123"},
    )
    sess = auth.login_ok(admin_user.email, "NewPass123")
    hdr = auth.bearer(sess["access_token"])

    resp = auth.client.post("/onboarding/type", headers=hdr, json={"type": "building"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["type"] == "building"

    db.expire_all()
    assert db.get(Society, society.id).type == "building"


def test_select_type_rejects_invalid(db, society, admin_user, superadmin, auth):
    _enable_onboarding(db, society, superadmin)
    tokens = auth.login_ok(admin_user.email, DEFAULT_MEMBER_PASSWORD)
    auth.client.post(
        "/auth/change-password",
        headers=auth.bearer(tokens["access_token"]),
        json={"current_password": DEFAULT_MEMBER_PASSWORD, "new_password": "NewPass123"},
    )
    sess = auth.login_ok(admin_user.email, "NewPass123")
    resp = auth.client.post(
        "/onboarding/type",
        headers=auth.bearer(sess["access_token"]),
        json={"type": "castle"},
    )
    assert resp.status_code == 422


def test_onboarding_route_blocked_without_module(
    db, society, admin_user, superadmin, auth
):
    # Module NOT enabled → require_module('onboarding') must block even the admin.
    tokens = auth.login_ok(admin_user.email, DEFAULT_MEMBER_PASSWORD)
    auth.client.post(
        "/auth/change-password",
        headers=auth.bearer(tokens["access_token"]),
        json={"current_password": DEFAULT_MEMBER_PASSWORD, "new_password": "NewPass123"},
    )
    sess = auth.login_ok(admin_user.email, "NewPass123")
    resp = auth.client.post(
        "/onboarding/type",
        headers=auth.bearer(sess["access_token"]),
        json={"type": "building"},
    )
    assert resp.status_code == 403
