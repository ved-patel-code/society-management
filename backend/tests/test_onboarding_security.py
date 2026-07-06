"""Phase-3 SECURITY + TENANT-ISOLATION gate for the Onboarding module.

Drives every assertion through the real HTTP stack (TestClient) and asserts exact
status codes plus, where relevant, DB state. Covers the authorization matrix
(with/without perms, resident lockout), module-disabled gating (require_module),
cross-tenant isolation (society-scoped lookups → 404, victim data untouched),
unauthenticated / tampered / expired tokens (401), the must_change lockout (403
then success), forged JWT claims (authz re-derived from the DB), and
SQL-injection literal-handling (parameterized, table survives, value round-trips).

Companion to test_onboarding_smoke.py / test_numbering.py /
test_onboarding_later_edits.py — no duplication of their scenarios.
"""
from __future__ import annotations

import time

import jwt as pyjwt
from sqlalchemy import text

from app.core.config import settings
from app.modules.onboarding.models import Building, House
from app.modules.onboarding.spec import MODULE_KEY, PERM_MANAGE, PERM_READ
from app.platform.roles.repository import RoleRepository
from app.platform.roles.service import RoleService
from app.platform.societies.schemas import ModuleAllocation, SocietyCreate
from app.platform.societies.service import SocietyService
from app.platform.users.provisioning import UserProvisioningService
from tests.conftest import DEFAULT_MEMBER_PASSWORD


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _enable_onboarding(db, society, superadmin):
    SocietyService(db).set_modules(
        society.id,
        [ModuleAllocation(module_key=MODULE_KEY, enabled=True, config={})],
        actor_user_id=superadmin.id,
    )
    db.commit()


def _activate_admin(auth, email, *, new_password="NewPass123"):
    """must_change → change-password → re-login. Returns a usable bearer header."""
    tokens = auth.login_ok(email, DEFAULT_MEMBER_PASSWORD)
    resp = auth.client.post(
        "/auth/change-password",
        headers=auth.bearer(tokens["access_token"]),
        json={"current_password": DEFAULT_MEMBER_PASSWORD, "new_password": new_password},
    )
    assert resp.status_code == 200, resp.text
    sess = auth.login_ok(email, new_password)
    return auth.bearer(sess["access_token"])


def _ready_admin(db, society, admin_user, superadmin, auth):
    """Enable module + get an activated admin bearer with a mapped building 'A'."""
    _enable_onboarding(db, society, superadmin)
    hdr = _activate_admin(auth, admin_user.email)
    r = auth.client.post("/onboarding/type", headers=hdr, json={"type": "building"})
    assert r.status_code == 200, r.text
    r = auth.client.post("/onboarding/buildings", headers=hdr, json={"names": ["A"]})
    assert r.status_code == 200, r.text
    bid = r.json()[0]["id"]
    r = auth.client.post(
        f"/onboarding/buildings/{bid}/map",
        headers=hdr,
        json={
            "floors": [{"level": 1, "houses_count": 2}],
            "numbering_config": {"mode": "auto", "count_pad": 2, "ground_prefix": "G"},
        },
    )
    assert r.status_code == 200, r.text
    house_ids = [h["id"] for h in r.json()]
    return hdr, bid, house_ids


def _second_society(db, superadmin, *, name="Society B", email="adminb@test.local"):
    """Create a second society, enable onboarding, provision + activate its admin."""
    soc = SocietyService(db).create_society(
        SocietyCreate(
            name=name,
            storage_limit_bytes=5 * 1024**3,
            default_member_password=DEFAULT_MEMBER_PASSWORD,
        ),
        actor_user_id=superadmin.id,
    )
    db.commit()
    db.refresh(soc)
    admin = UserProvisioningService(db).create_or_link_user(
        email=email,
        society_id=soc.id,
        role_key="society_admin",
        profile={"full_name": name + " Admin"},
        actor_user_id=superadmin.id,
    )
    db.commit()
    db.refresh(admin)
    SocietyService(db).set_modules(
        soc.id,
        [ModuleAllocation(module_key=MODULE_KEY, enabled=True, config={})],
        actor_user_id=superadmin.id,
    )
    db.commit()
    return soc, admin


# ===========================================================================
# 1. Authorization matrix — with / without the required permission
# ===========================================================================


def test_read_only_user_cannot_call_manage_endpoint(
    db, society, admin_user, superadmin, auth
):
    """A user holding only onboarding.read is 403 on a manage (write) route."""
    _enable_onboarding(db, society, superadmin)
    # Strip manage from society_admin: keep only onboarding.read.
    role = RoleRepository(db).society_role_by_key(society.id, "society_admin")
    manage = db.execute(
        text("SELECT id FROM permissions WHERE key=:k"), {"k": PERM_MANAGE}
    ).scalar_one()
    db.execute(
        text("DELETE FROM role_permissions WHERE role_id=:r AND permission_id=:p"),
        {"r": role.id, "p": manage},
    )
    db.commit()

    hdr = _activate_admin(auth, admin_user.email)
    # read route works…
    assert auth.client.get("/onboarding/state", headers=hdr).status_code == 200
    # …manage route is blocked.
    r = auth.client.post("/onboarding/type", headers=hdr, json={"type": "building"})
    assert r.status_code == 403
    assert r.json()["details"]["required_permission"] == PERM_MANAGE


def test_user_with_no_onboarding_perms_blocked(
    db, society, admin_user, superadmin, auth
):
    """Strip BOTH onboarding perms → read and manage routes both 403."""
    _enable_onboarding(db, society, superadmin)
    role = RoleRepository(db).society_role_by_key(society.id, "society_admin")
    ids = db.execute(
        text("SELECT id FROM permissions WHERE key IN (:a,:b)"),
        {"a": PERM_MANAGE, "b": PERM_READ},
    ).scalars().all()
    db.execute(
        text("DELETE FROM role_permissions WHERE role_id=:r AND permission_id = ANY(:p)"),
        {"r": role.id, "p": list(ids)},
    )
    db.commit()

    hdr = _activate_admin(auth, admin_user.email)
    assert auth.client.get("/onboarding/state", headers=hdr).status_code == 403
    r = auth.client.post("/onboarding/type", headers=hdr, json={"type": "building"})
    assert r.status_code == 403


def test_resident_cannot_use_any_onboarding_write_route(
    db, society, admin_user, resident_user, superadmin, auth
):
    """A resident (no onboarding perms) is 403 on every /onboarding write route.

    Module is enabled (via the admin flow) so the block is the permission gate,
    not require_module — confirming residents are barred even on an active module.
    """
    _enable_onboarding(db, society, superadmin)
    hdr = _activate_admin(auth, resident_user.email, new_password="ResPass123")

    writes = [
        ("post", "/onboarding/type", {"type": "building"}),
        ("put", "/onboarding/draft", {"draft": {}}),
        ("post", "/onboarding/buildings", {"names": ["X"]}),
        ("post", "/onboarding/buildings/1/map",
         {"floors": [{"level": 1, "houses_count": 1}],
          "numbering_config": {"mode": "auto"}}),
        ("post", "/onboarding/buildings/1/floors",
         {"floors": [{"level": 2, "houses_count": 1}]}),
        ("patch", "/onboarding/buildings/1", {"name": "Z"}),
        ("post", "/onboarding/rows",
         {"rows": [{"display_order": 1, "houses_count": 1,
                    "numbering_config": {"mode": "sequential"}}]}),
        ("patch", "/onboarding/houses/1", {"number": "9"}),
        ("post", "/onboarding/complete", {}),
        ("delete", "/onboarding/buildings/1", None),
        ("delete", "/onboarding/floors/1", None),
        ("delete", "/onboarding/houses/1", None),
    ]
    for method, path, body in writes:
        fn = getattr(auth.client, method)
        resp = fn(path, headers=hdr) if body is None else fn(path, headers=hdr, json=body)
        assert resp.status_code == 403, f"{method.upper()} {path} → {resp.status_code}"
    # A read route is likewise denied to residents.
    assert auth.client.get("/onboarding/state", headers=hdr).status_code == 403


# ===========================================================================
# 2. Module DISABLED — require_module blocks even a fully-permissioned admin
# ===========================================================================


def test_module_disabled_blocks_all_routes_even_with_perms(
    db, society, admin_user, superadmin, auth
):
    """Grant onboarding.* directly but leave the module OFF → every route 403.

    Uses grant_default_module_permissions so the caller genuinely holds both perms;
    the sole remaining gate is require_module, proving it is enforced independently.
    """
    RoleService(db).grant_default_module_permissions(
        society.id,
        {"society_admin": [PERM_MANAGE, PERM_READ]},
        actor_user_id=superadmin.id,
    )
    db.commit()
    # Confirm the module row is absent / disabled.
    enabled = db.execute(
        text("SELECT enabled FROM society_modules WHERE society_id=:s AND module_key=:k"),
        {"s": society.id, "k": MODULE_KEY},
    ).scalar_one_or_none()
    assert not enabled

    hdr = _activate_admin(auth, admin_user.email)
    # Caller really has the perms (sanity via /me).
    me = auth.client.get("/me", headers=hdr).json()
    assert PERM_MANAGE in me["permissions"] and PERM_READ in me["permissions"]

    routes = [
        ("get", "/onboarding/state", None),
        ("post", "/onboarding/type", {"type": "building"}),
        ("post", "/onboarding/buildings", {"names": ["X"]}),
        ("get", "/onboarding/buildings/1/preview", None),
        ("post", "/onboarding/complete", {}),
        ("delete", "/onboarding/buildings/1", None),
    ]
    for method, path, body in routes:
        fn = getattr(auth.client, method)
        resp = fn(path, headers=hdr) if body is None else fn(path, headers=hdr, json=body)
        assert resp.status_code == 403, f"{method.upper()} {path} → {resp.status_code}"
        assert resp.json()["details"]["module_key"] == MODULE_KEY


# ===========================================================================
# 3. CROSS-TENANT isolation — admin A cannot touch society B's rows (→ 404)
# ===========================================================================


def test_cross_tenant_building_access_is_404(
    db, society, admin_user, superadmin, auth
):
    """Admin A map/rename/preview/delete of B's building id → 404; B untouched."""
    hdr_a, _bid_a, _ = _ready_admin(db, society, admin_user, superadmin, auth)
    soc_b, _admin_b = _second_society(db, superadmin)

    # Build a real building in B (via service, as superadmin actor).
    from app.modules.onboarding.service import OnboardingService
    from app.modules.onboarding.schemas import (
        BuildingsCreateRequest,
        BuildingMapRequest,
    )

    svc = OnboardingService(db)
    svc.select_type(soc_b.id, "building", actor_user_id=superadmin.id)
    db.flush()
    [b_building] = svc.create_buildings(
        soc_b.id, BuildingsCreateRequest(names=["B-Tower"]), actor_user_id=superadmin.id
    )
    db.flush()
    svc.map_building(
        soc_b.id, b_building.id,
        BuildingMapRequest.model_validate({
            "floors": [{"level": 1, "houses_count": 2}],
            "numbering_config": {"mode": "auto", "count_pad": 2, "ground_prefix": "G"},
        }),
        actor_user_id=superadmin.id,
    )
    db.commit()
    b_bid = b_building.id
    b_name_before = b_building.name

    # Admin A tries to reach B's building by its path id.
    assert auth.client.get(
        f"/onboarding/buildings/{b_bid}/preview", headers=hdr_a
    ).status_code == 404
    assert auth.client.patch(
        f"/onboarding/buildings/{b_bid}", headers=hdr_a, json={"name": "HACKED"}
    ).status_code == 404
    assert auth.client.post(
        f"/onboarding/buildings/{b_bid}/map", headers=hdr_a,
        json={"floors": [{"level": 9, "houses_count": 9}],
              "numbering_config": {"mode": "auto"}},
    ).status_code == 404
    assert auth.client.post(
        f"/onboarding/buildings/{b_bid}/floors", headers=hdr_a,
        json={"floors": [{"level": 5, "houses_count": 1}]},
    ).status_code == 404
    # Delete is guarded by empty-status too, but cross-tenant lookup must win first.
    assert auth.client.delete(
        f"/onboarding/buildings/{b_bid}", headers=hdr_a
    ).status_code == 404

    # B's data is completely untouched.
    db.expire_all()
    b_after = db.get(Building, b_bid)
    assert b_after is not None and b_after.name == b_name_before
    assert b_after.society_id == soc_b.id
    houses_b = db.query(House).filter(House.society_id == soc_b.id).all()
    assert len(houses_b) == 2  # not 9 — the cross-tenant map never ran


def test_cross_tenant_house_override_and_delete_is_404(
    db, society, admin_user, superadmin, auth
):
    """Admin A override/delete of B's house id → 404; B's house value preserved."""
    hdr_a, _, _ = _ready_admin(db, society, admin_user, superadmin, auth)
    soc_b, _admin_b = _second_society(db, superadmin)

    from app.modules.onboarding.service import OnboardingService
    from app.modules.onboarding.schemas import (
        BuildingsCreateRequest,
        BuildingMapRequest,
    )

    svc = OnboardingService(db)
    svc.select_type(soc_b.id, "building", actor_user_id=superadmin.id)
    db.flush()
    [bb] = svc.create_buildings(
        soc_b.id, BuildingsCreateRequest(names=["B"]), actor_user_id=superadmin.id
    )
    db.flush()
    houses = svc.map_building(
        soc_b.id, bb.id,
        BuildingMapRequest.model_validate({
            "floors": [{"level": 1, "houses_count": 1}],
            "numbering_config": {"mode": "auto", "count_pad": 2, "ground_prefix": "G"},
        }),
        actor_user_id=superadmin.id,
    )
    db.commit()
    b_house_id = houses[0].id
    b_house_num = houses[0].number

    assert auth.client.patch(
        f"/onboarding/houses/{b_house_id}", headers=hdr_a, json={"number": "666"}
    ).status_code == 404
    assert auth.client.delete(
        f"/onboarding/houses/{b_house_id}", headers=hdr_a
    ).status_code == 404

    db.expire_all()
    h_after = db.get(House, b_house_id)
    assert h_after is not None
    assert h_after.number == b_house_num
    assert h_after.number_overridden is False
    assert h_after.society_id == soc_b.id


def test_cross_tenant_floor_delete_is_404(
    db, society, admin_user, superadmin, auth
):
    """Admin A deleting B's floor id → 404; the floor still exists."""
    hdr_a, _, _ = _ready_admin(db, society, admin_user, superadmin, auth)
    soc_b, _admin_b = _second_society(db, superadmin)

    from app.modules.onboarding.service import OnboardingService
    from app.modules.onboarding.schemas import (
        BuildingsCreateRequest,
        BuildingMapRequest,
    )
    from app.modules.onboarding.models import Floor

    svc = OnboardingService(db)
    svc.select_type(soc_b.id, "building", actor_user_id=superadmin.id)
    db.flush()
    [bb] = svc.create_buildings(
        soc_b.id, BuildingsCreateRequest(names=["B"]), actor_user_id=superadmin.id
    )
    db.flush()
    svc.map_building(
        soc_b.id, bb.id,
        BuildingMapRequest.model_validate({
            "floors": [{"level": 1, "houses_count": 1}],
            "numbering_config": {"mode": "auto"},
        }),
        actor_user_id=superadmin.id,
    )
    db.commit()
    floor = db.query(Floor).filter(Floor.society_id == soc_b.id).one()
    floor_id = floor.id

    assert auth.client.delete(
        f"/onboarding/floors/{floor_id}", headers=hdr_a
    ).status_code == 404

    db.expire_all()
    assert db.get(Floor, floor_id) is not None


# ===========================================================================
# 4. Unauthenticated / bad token → 401
# ===========================================================================


def test_no_bearer_is_401(db, society, admin_user, superadmin, auth):
    _enable_onboarding(db, society, superadmin)
    assert auth.client.get("/onboarding/state").status_code == 401
    assert auth.client.post(
        "/onboarding/type", json={"type": "building"}
    ).status_code == 401


def test_tampered_token_is_401(db, society, admin_user, superadmin, auth):
    _enable_onboarding(db, society, superadmin)
    hdr = _activate_admin(auth, admin_user.email)
    good = hdr["Authorization"].split(" ", 1)[1]
    # Flip the signature: corrupt the last segment.
    head, payload, sig = good.split(".")
    tampered = f"{head}.{payload}.{sig[:-4]}XXXX"
    r = auth.client.get(
        "/onboarding/state", headers={"Authorization": f"Bearer {tampered}"}
    )
    assert r.status_code == 401


def test_wrong_secret_token_is_401(db, society, admin_user, superadmin, auth):
    """A well-formed token signed with the WRONG secret is rejected (401)."""
    _enable_onboarding(db, society, superadmin)
    forged = pyjwt.encode(
        {"user_id": admin_user.id, "active_society_id": society.id,
         "role_ids": [], "password_state": "active",
         "exp": int(time.time()) + 3600},
        "not-the-real-secret",
        algorithm="HS256",
    )
    r = auth.client.get(
        "/onboarding/state", headers={"Authorization": f"Bearer {forged}"}
    )
    assert r.status_code == 401


def test_expired_token_is_401(db, society, admin_user, superadmin, auth):
    """A token signed with the REAL secret but already expired → 401."""
    _enable_onboarding(db, society, superadmin)
    expired = pyjwt.encode(
        {"user_id": admin_user.id, "active_society_id": society.id,
         "role_ids": [], "password_state": "active",
         "exp": int(time.time()) - 10, "iat": int(time.time()) - 3600,
         "type": "access"},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    r = auth.client.get(
        "/onboarding/state", headers={"Authorization": f"Bearer {expired}"}
    )
    assert r.status_code == 401


# ===========================================================================
# 5. must_change lockout — blocked until password changed, then allowed
# ===========================================================================


def test_must_change_admin_locked_out_then_allowed(
    db, society, admin_user, superadmin, auth
):
    _enable_onboarding(db, society, superadmin)
    # Fresh admin: still must_change. Login returns a must_change access token.
    tokens = auth.login_ok(admin_user.email, DEFAULT_MEMBER_PASSWORD)
    locked = auth.bearer(tokens["access_token"])

    r = auth.client.get("/onboarding/state", headers=locked)
    assert r.status_code == 403
    assert r.json()["details"]["password_state"] == "must_change"

    r = auth.client.post("/onboarding/type", headers=locked, json={"type": "building"})
    assert r.status_code == 403
    assert r.json()["details"]["password_state"] == "must_change"

    # Change the password, re-login → onboarding now reachable.
    ok = auth.client.post(
        "/auth/change-password",
        headers=locked,
        json={"current_password": DEFAULT_MEMBER_PASSWORD, "new_password": "NewPass123"},
    )
    assert ok.status_code == 200, ok.text
    sess = auth.login_ok(admin_user.email, "NewPass123")
    active = auth.bearer(sess["access_token"])
    assert auth.client.get("/onboarding/state", headers=active).status_code == 200
    assert auth.client.post(
        "/onboarding/type", headers=active, json={"type": "building"}
    ).status_code == 200


# ===========================================================================
# 6. Forged claims — authorization is re-derived from the DB, not the JWT
# ===========================================================================


def test_forged_role_ids_claim_grants_nothing(
    db, society, resident_user, superadmin, auth, make_token
):
    """Mint a token whose role_ids claim names the society_admin role the resident
    does NOT hold. require_permission re-derives perms from user_roles in the DB,
    so the forged claim grants no onboarding access (403)."""
    _enable_onboarding(db, society, superadmin)
    admin_role = RoleRepository(db).society_role_by_key(society.id, "society_admin")

    # Flip resident to active so must_change doesn't mask the perm check.
    db.execute(
        text("UPDATE users SET password_state='active' WHERE id=:i"),
        {"i": resident_user.id},
    )
    db.commit()

    forged = make_token(
        user_id=resident_user.id,
        active_society_id=society.id,
        role_ids=[admin_role.id],  # a role the resident is NOT assigned in user_roles
        password_state="active",
    )
    hdr = auth.bearer(forged)
    # No real onboarding perm → 403 on manage and read.
    assert auth.client.post(
        "/onboarding/type", headers=hdr, json={"type": "building"}
    ).status_code == 403
    assert auth.client.get("/onboarding/state", headers=hdr).status_code == 403


def test_forged_active_society_id_for_foreign_society_grants_nothing(
    db, society, admin_user, superadmin, auth, make_token
):
    """Admin A mints a token claiming active_society_id = society B (where A has no
    roles). Effective perms are derived from user_roles for THAT society → empty →
    403. Confirms the society claim can't smuggle in cross-tenant authority."""
    _enable_onboarding(db, society, superadmin)
    soc_b, _admin_b = _second_society(db, superadmin)

    db.execute(
        text("UPDATE users SET password_state='active' WHERE id=:i"),
        {"i": admin_user.id},
    )
    db.commit()

    forged = make_token(
        user_id=admin_user.id,
        active_society_id=soc_b.id,  # admin_user has NO roles in B
        role_ids=[],
        password_state="active",
    )
    hdr = auth.bearer(forged)
    assert auth.client.post(
        "/onboarding/type", headers=hdr, json={"type": "building"}
    ).status_code == 403
    assert auth.client.get("/onboarding/state", headers=hdr).status_code == 403


# ===========================================================================
# 7. SQL-injection safety — metacharacters stored/handled as a literal
# ===========================================================================


def test_sql_injection_in_building_name_is_literal(
    db, society, admin_user, superadmin, auth
):
    """A building name with SQL metacharacters is stored verbatim (parameterized);
    the houses table survives and the value round-trips."""
    _enable_onboarding(db, society, superadmin)
    hdr = _activate_admin(auth, admin_user.email)
    auth.client.post("/onboarding/type", headers=hdr, json={"type": "building"})

    evil = "'; DROP TABLE houses;--"
    r = auth.client.post("/onboarding/buildings", headers=hdr, json={"names": [evil]})
    assert r.status_code == 200, r.text
    bid = r.json()[0]["id"]
    assert r.json()[0]["name"] == evil  # round-trips exactly

    # The houses table is intact (query would raise if it were dropped).
    assert db.execute(text("SELECT count(*) FROM houses")).scalar() == 0

    db.expire_all()
    assert db.get(Building, bid).name == evil

    # Rename to another injection payload — still a literal.
    evil2 = "x' OR '1'='1"
    r = auth.client.patch(f"/onboarding/buildings/{bid}", headers=hdr, json={"name": evil2})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(Building, bid).name == evil2


def test_sql_injection_in_house_number_override_is_literal(
    db, society, admin_user, superadmin, auth
):
    """A house-number override containing SQL metacharacters is stored as a literal;
    the value round-trips and the table survives."""
    hdr, _bid, house_ids = _ready_admin(db, society, admin_user, superadmin, auth)

    evil = "'; DROP TABLE houses;--"
    r = auth.client.patch(
        f"/onboarding/houses/{house_ids[0]}", headers=hdr, json={"number": evil}
    )
    # Either accepted as a literal (200) or rejected by validation (422) — never
    # executed. Both are safe outcomes; assert the table survives regardless.
    assert r.status_code in (200, 422), r.text
    assert db.execute(text("SELECT count(*) FROM houses")).scalar() == len(house_ids)

    if r.status_code == 200:
        assert r.json()["number"] == evil
        db.expire_all()
        h = db.get(House, house_ids[0])
        assert h.number == evil
        assert h.number_overridden is True
