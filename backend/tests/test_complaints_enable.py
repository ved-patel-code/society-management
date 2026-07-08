"""Enable/disable lifecycle tests for Complaints (Module 5).

Covers the module-enable contract: the six ``complaints.*`` permissions are
seeded, the default role grants (resident vs admin) match spec.py, the houses
``depends_on`` guard, and the module-gated / vault-gated 403s when the module is
absent, disabled, or vault is off.
"""
from __future__ import annotations

from app.platform.models import Permission
from app.platform.roles.repository import RoleRepository
from app.platform.societies.schemas import ModuleAllocation
from app.platform.societies.service import SocietyService

from tests._complaints_helpers import admin_bearer, owned_house_for, setup_complaints

ALL_COMPLAINTS_PERMS = {
    "complaints.create",
    "complaints.read",
    "complaints.read_all",
    "complaints.update_status",
    "complaints.manage_categories",
    "complaints.configure",
}


def _role_perm_keys(db, society_id, role_key) -> set[str]:
    role = RoleRepository(db).society_role_by_key(society_id, role_key)
    from sqlalchemy import text

    rows = db.execute(
        text(
            "SELECT p.key FROM role_permissions rp "
            "JOIN permissions p ON p.id = rp.permission_id "
            "WHERE rp.role_id = :r"
        ),
        {"r": role.id},
    ).scalars().all()
    return set(rows)


def test_enable_seeds_six_permissions(db, society, admin_user, superadmin, auth):
    setup_complaints(db, society, admin_user, superadmin, auth)
    keys = {
        p.key
        for p in db.query(Permission).filter(Permission.key.like("complaints.%")).all()
    }
    assert keys == ALL_COMPLAINTS_PERMS


def test_enable_grants_resident_defaults(db, society, admin_user, superadmin, auth):
    setup_complaints(db, society, admin_user, superadmin, auth)
    perms = _role_perm_keys(db, society.id, "resident")
    assert perms & ALL_COMPLAINTS_PERMS == {"complaints.create", "complaints.read"}


def test_enable_grants_admin_defaults(db, society, admin_user, superadmin, auth):
    setup_complaints(db, society, admin_user, superadmin, auth)
    perms = _role_perm_keys(db, society.id, "society_admin")
    assert perms & ALL_COMPLAINTS_PERMS == {
        "complaints.read",
        "complaints.read_all",
        "complaints.update_status",
        "complaints.manage_categories",
        "complaints.configure",
    }


def test_enable_without_houses_dependency_error(db, society, superadmin):
    """complaints depends_on houses; enabling it alone -> DependencyError (409)."""
    with_error = False
    try:
        SocietyService(db).set_modules(
            society.id,
            [ModuleAllocation(module_key="complaints", enabled=True, config={})],
            actor_user_id=superadmin.id,
        )
    except Exception as exc:  # noqa: BLE001 — assert the domain error shape
        with_error = True
        assert getattr(exc, "status_code", None) == 409
        assert getattr(exc, "code", None) == "dependency_error"
    assert with_error, "expected a DependencyError when houses is not enabled"


def test_absent_module_routes_403(db, society, admin_user, superadmin, auth):
    """Complaints not enabled at all -> every route 403 for an ordinary caller.

    NOTE (adjusted from the matrix): ``require_module`` explicitly documents and
    implements a super-admin BYPASS ("platform ops are not society-scoped",
    app/core/deps.py ``require_module``) — a super-admin acting within a society
    is NOT gated by module-enablement. So "even super-admin" does not hold for
    this codebase; that half of the matrix assumption is corrected here rather
    than asserted as written. The ordinary-caller 403s are exercised in full.
    """
    from app.platform.societies.schemas import ModuleAllocation as MA

    SocietyService(db).set_modules(
        society.id,
        [MA(module_key="onboarding", enabled=True, config={})],
        actor_user_id=superadmin.id,
    )
    db.commit()
    hdr = admin_bearer(auth, admin_user)

    assert auth.client.get("/complaints/categories", headers=hdr).status_code == 403
    assert auth.client.get("/complaints", headers=hdr).status_code == 403
    assert auth.client.get("/complaints/config", headers=hdr).status_code == 403
    assert auth.client.post(
        "/complaints", headers=hdr, json={"category_id": 1, "title": "x", "description": "x"}
    ).status_code == 403
    assert auth.client.get("/complaints/1", headers=hdr).status_code == 403

    # Confirm the documented super-admin bypass explicitly (not a bug — by
    # design in require_module), so this deviation from the matrix is pinned.
    admin_user.is_platform_super_admin = True
    db.add(admin_user)
    db.commit()
    assert auth.client.get("/complaints/categories", headers=hdr).status_code == 200


def test_disabled_module_routes_403(db, society, admin_user, superadmin, auth):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    assert auth.client.get("/complaints/categories", headers=hdr).status_code == 200

    SocietyService(db).set_modules(
        society.id,
        [ModuleAllocation(module_key="complaints", enabled=False, config={})],
        actor_user_id=superadmin.id,
    )
    db.commit()

    assert auth.client.get("/complaints/categories", headers=hdr).status_code == 403
    assert auth.client.get("/complaints", headers=hdr).status_code == 403


def test_vault_off_image_and_resolve_403_text_ok(
    db, society, admin_user, superadmin, auth
):
    """Complaints enabled WITHOUT vault: image/resolve routes 403; text CRUD OK."""
    SocietyService(db).set_modules(
        society.id,
        [
            ModuleAllocation(module_key="onboarding", enabled=True, config={}),
            ModuleAllocation(module_key="houses", enabled=True, config={}),
            ModuleAllocation(module_key="complaints", enabled=True, config={}),
        ],
        actor_user_id=superadmin.id,
    )
    db.commit()
    hdr = admin_bearer(auth, admin_user)
    hid = owned_house_for(auth, hdr, email="raiser@x.com")
    from tests._complaints_helpers import owner_login_bearer

    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")

    cats = auth.client.get("/complaints/categories", headers=r_hdr)
    assert cats.status_code == 200, cats.text
    cat_id = cats.json()[0]["id"]

    created = auth.client.post(
        "/complaints",
        headers=r_hdr,
        json={"category_id": cat_id, "title": "No vault", "description": "text only"},
    )
    assert created.status_code == 200, created.text
    cid = created.json()["id"]

    # Image route is vault-gated -> 403.
    img_resp = auth.client.post(
        f"/complaints/{cid}/images",
        headers=r_hdr,
        files={"file": ("x.jpg", b"x", "image/jpeg")},
    )
    assert img_resp.status_code == 403, img_resp.text

    # Resolve route is vault-gated -> 403.
    resolve_resp = auth.client.post(
        f"/complaints/{cid}/resolve", headers=hdr, data={"note": "x"}
    )
    assert resolve_resp.status_code == 403, resolve_resp.text
