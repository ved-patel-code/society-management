"""Security + tenant-isolation tests for the House & Occupancy module.

Permission matrix, module-disabled gating, cross-society isolation, forged
claims, must_change lockout, and unauthenticated writes.
"""
from __future__ import annotations

from sqlalchemy import text

from app.modules.houses.spec import (
    MODULE_KEY,
    PERM_MANAGE_OCCUPANCY,
    PERM_READ,
    PERM_UPDATE_STATUS,
)
from app.platform.roles.repository import RoleRepository
from app.platform.roles.service import RoleService
from app.platform.societies.schemas import ModuleAllocation, SocietyCreate
from app.platform.societies.service import SocietyService
from app.platform.users.provisioning import UserProvisioningService
from tests.conftest import DEFAULT_MEMBER_PASSWORD

from tests._houses_helpers import (
    _admin_bearer,
    _enable_houses,
    _make_building_with_houses,
    _owner,
    _set_status,
    _setup,
    _tenant,
)


def _strip_permission(db, society, perm_key: str) -> None:
    role = RoleRepository(db).society_role_by_key(society.id, "society_admin")
    perm_id = db.execute(
        text("SELECT id FROM permissions WHERE key=:k"), {"k": perm_key}
    ).scalar_one()
    db.execute(
        text("DELETE FROM role_permissions WHERE role_id=:r AND permission_id=:p"),
        {"r": role.id, "p": perm_id},
    )
    db.commit()


def _second_society_with_house(db, superadmin):
    """A second society, houses enabled, one mapped house."""
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
    _enable_houses(db, soc, superadmin)

    from app.modules.onboarding.models import Building, Floor, House

    b = Building(society_id=soc.id, name="B", display_order=1, numbering_config={"mode": "auto"})
    db.add(b)
    db.flush()
    f = Floor(society_id=soc.id, building_id=b.id, level=1, is_ground=False, houses_count=1)
    db.add(f)
    db.flush()
    h = House(
        society_id=soc.id, building_id=b.id, floor_id=f.id,
        number="101", numbering_mode="auto", number_overridden=False, status="empty",
    )
    db.add(h)
    db.commit()
    return soc, admin_b, h


# ===========================================================================
# 1. permission matrix
# ===========================================================================

def test_read_only_perm_cannot_change_status(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _strip_permission(db, society, PERM_UPDATE_STATUS)
    resp = _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    assert resp.status_code == 403
    assert resp.json()["details"]["required_permission"] == PERM_UPDATE_STATUS


def test_read_only_perm_cannot_patch(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    _strip_permission(db, society, PERM_MANAGE_OCCUPANCY)
    resp = auth.client.patch(
        f"/houses/{hid}/occupancy/owner", headers=hdr, json={"contact_number": "1"}
    )
    assert resp.status_code == 403
    assert resp.json()["details"]["required_permission"] == PERM_MANAGE_OCCUPANCY


def test_update_status_perm_cannot_patch(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    _strip_permission(db, society, PERM_MANAGE_OCCUPANCY)
    resp = auth.client.patch(
        f"/houses/{hid}/occupancy/owner", headers=hdr, json={"contact_number": "2"}
    )
    assert resp.status_code == 403


def test_manage_occupancy_perm_cannot_change_status(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _strip_permission(db, society, PERM_UPDATE_STATUS)
    resp = _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    assert resp.status_code == 403


def test_no_perms_cannot_read(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _strip_permission(db, society, PERM_READ)
    resp = auth.client.get("/houses", headers=hdr)
    assert resp.status_code == 403
    assert resp.json()["details"]["required_permission"] == PERM_READ


def test_resident_cannot_read(db, society, admin_user, resident_user, superadmin, auth):
    _enable_houses(db, society, superadmin)
    hdr = _admin_bearer(auth, resident_user)
    assert auth.client.get("/houses", headers=hdr).status_code == 403


def test_resident_cannot_write(db, society, admin_user, resident_user, superadmin, auth):
    hdr_admin = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr_admin)
    hid = houses[0]["id"]
    hdr = _admin_bearer(auth, resident_user)
    resp = _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    assert resp.status_code == 403


# ===========================================================================
# 2. module-disabled gate (perms granted directly, module OFF)
# ===========================================================================

def test_module_disabled_blocks_read(db, society, admin_user, superadmin, auth):
    RoleService(db).grant_default_module_permissions(
        society.id, {"society_admin": [PERM_READ, PERM_UPDATE_STATUS, PERM_MANAGE_OCCUPANCY]},
        actor_user_id=superadmin.id,
    )
    db.commit()
    hdr = _admin_bearer(auth, admin_user)
    resp = auth.client.get("/houses", headers=hdr)
    assert resp.status_code == 403
    assert resp.json()["details"]["module_key"] == MODULE_KEY


def test_module_disabled_blocks_status_change(db, society, admin_user, superadmin, auth):
    RoleService(db).grant_default_module_permissions(
        society.id, {"society_admin": [PERM_READ, PERM_UPDATE_STATUS, PERM_MANAGE_OCCUPANCY]},
        actor_user_id=superadmin.id,
    )
    db.commit()
    hdr = _admin_bearer(auth, admin_user)
    resp = _set_status(auth, hdr, 1, "owned", _owner(persons_living=1))
    assert resp.status_code == 403
    assert resp.json()["details"]["module_key"] == MODULE_KEY


def test_module_disabled_blocks_patch(db, society, admin_user, superadmin, auth):
    RoleService(db).grant_default_module_permissions(
        society.id, {"society_admin": [PERM_READ, PERM_UPDATE_STATUS, PERM_MANAGE_OCCUPANCY]},
        actor_user_id=superadmin.id,
    )
    db.commit()
    hdr = _admin_bearer(auth, admin_user)
    resp = auth.client.patch("/houses/1/occupancy/owner", headers=hdr, json={"full_name": "X"})
    assert resp.status_code == 403
    assert resp.json()["details"]["module_key"] == MODULE_KEY


def test_module_disabled_blocks_detail_and_history(db, society, admin_user, superadmin, auth):
    RoleService(db).grant_default_module_permissions(
        society.id, {"society_admin": [PERM_READ, PERM_UPDATE_STATUS, PERM_MANAGE_OCCUPANCY]},
        actor_user_id=superadmin.id,
    )
    db.commit()
    hdr = _admin_bearer(auth, admin_user)
    assert auth.client.get("/houses/1", headers=hdr).status_code == 403
    assert auth.client.get("/houses/1/history", headers=hdr).status_code == 403


def test_onboarding_only_enabled_still_blocks_houses(db, society, admin_user, superadmin, auth):
    """Onboarding enabled but houses NOT enabled -> houses routes still 403 module_disabled,
    even though depends_on is satisfied (enabling is a separate opt-in)."""
    SocietyService(db).set_modules(
        society.id,
        [ModuleAllocation(module_key="onboarding", enabled=True, config={})],
        actor_user_id=superadmin.id,
    )
    db.commit()
    RoleService(db).grant_default_module_permissions(
        society.id, {"society_admin": [PERM_READ]}, actor_user_id=superadmin.id,
    )
    db.commit()
    hdr = _admin_bearer(auth, admin_user)
    resp = auth.client.get("/houses", headers=hdr)
    assert resp.status_code == 403
    assert resp.json()["details"]["module_key"] == MODULE_KEY


# ===========================================================================
# 3. cross-tenant isolation
# ===========================================================================

def test_cross_society_cannot_read_house(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _soc_b, _admin_b, house_b = _second_society_with_house(db, superadmin)
    resp = auth.client.get(f"/houses/{house_b.id}", headers=hdr)
    assert resp.status_code == 404


def test_cross_society_cannot_change_status(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _soc_b, _admin_b, house_b = _second_society_with_house(db, superadmin)
    resp = _set_status(auth, hdr, house_b.id, "owned", _owner(persons_living=1))
    assert resp.status_code == 404
    db.expire_all()
    from app.modules.onboarding.models import House

    assert db.get(House, house_b.id).status == "empty"


def test_cross_society_cannot_patch(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _soc_b, _admin_b, house_b = _second_society_with_house(db, superadmin)
    resp = auth.client.patch(
        f"/houses/{house_b.id}/occupancy/owner", headers=hdr, json={"full_name": "X"}
    )
    assert resp.status_code == 404


def test_cross_society_cannot_read_history(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _soc_b, _admin_b, house_b = _second_society_with_house(db, superadmin)
    resp = auth.client.get(f"/houses/{house_b.id}/history", headers=hdr)
    assert resp.status_code == 404


def test_list_only_returns_own_society(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _make_building_with_houses(auth, hdr)
    _soc_b, _admin_b, _house_b = _second_society_with_house(db, superadmin)
    resp = auth.client.get("/houses", headers=hdr)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2  # only society A's 2 houses, not B's


# ===========================================================================
# 4. forged / crafted tokens
# ===========================================================================

def test_crafted_foreign_active_society_id_is_403_permission_denied(
    db, society, admin_user, superadmin, auth, make_token
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    soc_b, _admin_b, _house_b = _second_society_with_house(db, superadmin)
    db.execute(
        text("UPDATE users SET password_state='active' WHERE id=:i"),
        {"i": admin_user.id},
    )
    db.commit()
    forged = make_token(
        user_id=admin_user.id, active_society_id=soc_b.id, role_ids=[], password_state="active",
    )
    hdr_forged = auth.bearer(forged)
    resp = auth.client.get("/houses", headers=hdr_forged)
    assert resp.status_code == 403
    assert resp.json()["code"] == "permission_denied"


def test_forged_role_ids_grants_nothing(db, society, resident_user, superadmin, auth, make_token):
    _enable_houses(db, society, superadmin)
    admin_role = RoleRepository(db).society_role_by_key(society.id, "society_admin")
    db.execute(
        text("UPDATE users SET password_state='active' WHERE id=:i"), {"i": resident_user.id},
    )
    db.commit()
    forged = make_token(
        user_id=resident_user.id, active_society_id=society.id,
        role_ids=[admin_role.id], password_state="active",
    )
    hdr = auth.bearer(forged)
    assert auth.client.get("/houses", headers=hdr).status_code == 403


def test_house_id_path_cannot_smuggle_cross_tenant(db, society, admin_user, superadmin, auth):
    """A crafted house_id belonging to another society in the path can't be reached
    via the caller's own (legit) society scope -> 404, no leak."""
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _soc_b, _admin_b, house_b = _second_society_with_house(db, superadmin)
    resp = auth.client.get(f"/houses/{house_b.id}", headers=hdr)
    assert resp.status_code == 404


def test_token_active_society_none_non_super_is_403_module_disabled(
    db, society, admin_user, superadmin, auth, make_token
):
    _enable_houses(db, society, superadmin)
    db.execute(
        text("UPDATE users SET password_state='active' WHERE id=:i"), {"i": admin_user.id},
    )
    db.commit()
    forged = make_token(
        user_id=admin_user.id, active_society_id=None, role_ids=[], password_state="active",
    )
    hdr = auth.bearer(forged)
    resp = auth.client.get("/houses", headers=hdr)
    assert resp.status_code == 403
    assert resp.json()["code"] == "module_disabled"


# ===========================================================================
# 5. super-admin gate derivations
# ===========================================================================

def test_super_admin_no_active_society_is_422(db, society, superadmin, auth, make_token):
    _enable_houses(db, society, superadmin)
    token = make_token(
        user_id=superadmin.id, active_society_id=None, role_ids=[], password_state="active",
    )
    hdr = auth.bearer(token)
    resp = auth.client.get("/houses", headers=hdr)
    assert resp.status_code == 422
    assert resp.json()["message"] == "No active society for this request."


def test_super_admin_with_active_society_is_200(db, society, superadmin, auth, make_token):
    _enable_houses(db, society, superadmin)
    token = make_token(
        user_id=superadmin.id, active_society_id=society.id, role_ids=[], password_state="active",
    )
    hdr = auth.bearer(token)
    resp = auth.client.get("/houses", headers=hdr)
    assert resp.status_code == 200, resp.text


# ===========================================================================
# 6. must_change lockout
# ===========================================================================

def test_must_change_admin_is_403_permission_denied(db, society, admin_user, superadmin, auth):
    _enable_houses(db, society, superadmin)
    tokens = auth.login_ok(admin_user.email, DEFAULT_MEMBER_PASSWORD)
    locked = auth.bearer(tokens["access_token"])
    resp = auth.client.get("/houses", headers=locked)
    assert resp.status_code == 403
    assert resp.json()["details"]["password_state"] == "must_change"


def test_must_change_owner_login_is_403(db, society, admin_user, superadmin, auth):
    """A newly-provisioned owner login is must_change; logging in and hitting
    /houses is blocked until password change."""
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "owned", _owner(email="freshowner@x.com", persons_living=1))
    assert resp.status_code == 200, resp.text

    owner_tokens = auth.login_ok("freshowner@x.com", "Welcome123")
    owner_hdr = auth.bearer(owner_tokens["access_token"])
    resp2 = auth.client.get("/houses", headers=owner_hdr)
    assert resp2.status_code == 403
    assert resp2.json()["details"]["password_state"] == "must_change"


# ===========================================================================
# 7. unauthenticated writes
# ===========================================================================

def test_write_routes_401_without_bearer(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = auth.client.post(
        f"/houses/{hid}/status",
        json={"to_status": "owned", "owner": _owner(persons_living=1)},
    )
    assert resp.status_code == 401
    resp2 = auth.client.patch(f"/houses/{hid}/occupancy/owner", json={"full_name": "X"})
    assert resp2.status_code == 401
