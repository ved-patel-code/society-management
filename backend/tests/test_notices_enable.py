"""Enable/disable lifecycle tests for Notice Board (Module 6).

Covers the module-enable contract: the three ``notices.*`` permissions are
seeded, the default role grants (resident vs admin) match ``spec.py``, the
houses ``depends_on`` guard, the module-gated / vault-gated 403s when the
module is absent, disabled, or vault is off, and enable-idempotency (no
duplicate ``role_permissions`` rows on a repeat enable).

Mirrors ``test_complaints_enable.py``'s conventions and its documented
super-admin bypass correction (``require_module`` explicitly bypasses for
platform ops — this is by design, not a bug, so it is asserted as a 200, never
an "even super-admin -> 403").
"""
from __future__ import annotations

from sqlalchemy import text

from app.platform.models import Permission
from app.platform.roles.repository import RoleRepository
from app.platform.societies.schemas import ModuleAllocation
from app.platform.societies.service import SocietyService

from tests._notices_helpers import (
    add_attachment_http,
    admin_bearer,
    create_notice_http,
    enable_notices,
    setup_notices,
)

ALL_NOTICES_PERMS = {
    "notices.read",
    "notices.publish",
    "notices.read_receipts",
}


def _role_perm_keys(db, society_id, role_key) -> set[str]:
    role = RoleRepository(db).society_role_by_key(society_id, role_key)
    rows = db.execute(
        text(
            "SELECT p.key FROM role_permissions rp "
            "JOIN permissions p ON p.id = rp.permission_id "
            "WHERE rp.role_id = :r"
        ),
        {"r": role.id},
    ).scalars().all()
    return set(rows)


def test_enable_seeds_exactly_three_permissions(
    db, society, admin_user, superadmin, auth
):
    setup_notices(db, society, admin_user, superadmin, auth)
    keys = {
        p.key
        for p in db.query(Permission).filter(Permission.key.like("notices.%")).all()
    }
    assert keys == ALL_NOTICES_PERMS


def test_enable_grants_resident_default_read_only(
    db, society, admin_user, superadmin, auth
):
    setup_notices(db, society, admin_user, superadmin, auth)
    perms = _role_perm_keys(db, society.id, "resident")
    assert perms & ALL_NOTICES_PERMS == {"notices.read"}


def test_enable_grants_admin_all_three(db, society, admin_user, superadmin, auth):
    setup_notices(db, society, admin_user, superadmin, auth)
    perms = _role_perm_keys(db, society.id, "society_admin")
    assert perms & ALL_NOTICES_PERMS == ALL_NOTICES_PERMS


def test_enable_without_houses_dependency_error(db, society, superadmin):
    """notices depends_on houses; enabling it alone -> DependencyError (409)."""
    with_error = False
    try:
        SocietyService(db).set_modules(
            society.id,
            [ModuleAllocation(module_key="notices", enabled=True, config={})],
            actor_user_id=superadmin.id,
        )
    except Exception as exc:  # noqa: BLE001 — assert the domain error shape
        with_error = True
        assert getattr(exc, "status_code", None) == 409
        assert getattr(exc, "code", None) == "dependency_error"
    assert with_error, "expected a DependencyError when houses is not enabled"


def test_absent_module_all_routes_403(db, society, admin_user, superadmin, auth):
    """Notices not enabled at all -> every route 403 for an ordinary caller.

    ``require_module`` documents + implements an explicit super-admin BYPASS
    (platform ops are not society-scoped) — asserted here as the by-design 200,
    not "even super-admin -> 403".
    """
    SocietyService(db).set_modules(
        society.id,
        [
            ModuleAllocation(module_key="onboarding", enabled=True, config={}),
            ModuleAllocation(module_key="houses", enabled=True, config={}),
        ],
        actor_user_id=superadmin.id,
    )
    db.commit()
    hdr = admin_bearer(auth, admin_user)

    assert auth.client.get("/notices", headers=hdr).status_code == 403
    assert auth.client.get("/notices/archive", headers=hdr).status_code == 403
    assert auth.client.get("/notices/1", headers=hdr).status_code == 403
    assert auth.client.post(
        "/notices", headers=hdr, json={"title": "x", "body": "x"}
    ).status_code == 403
    assert auth.client.patch(
        "/notices/1", headers=hdr, json={"title": "y"}
    ).status_code == 403
    assert auth.client.post("/notices/1/publish", headers=hdr).status_code == 403
    assert auth.client.post("/notices/1/withdraw", headers=hdr).status_code == 403
    assert (
        add_attachment_http(auth.client, hdr, 1, filename="x.png").status_code == 403
    )
    assert (
        auth.client.delete("/notices/1/attachments/1", headers=hdr).status_code == 403
    )
    assert auth.client.get("/notices/1/receipts", headers=hdr).status_code == 403
    assert auth.client.post("/notices/read-all", headers=hdr).status_code == 403

    # Confirm the documented super-admin bypass explicitly (not a bug — by
    # design in require_module), so this deviation from a naive "always 403"
    # expectation is pinned as intended behavior.
    admin_user.is_platform_super_admin = True
    db.add(admin_user)
    db.commit()
    assert auth.client.get("/notices", headers=hdr).status_code == 200


def test_disabled_module_routes_403(db, society, admin_user, superadmin, auth):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    assert auth.client.get("/notices", headers=hdr).status_code == 200

    SocietyService(db).set_modules(
        society.id,
        [ModuleAllocation(module_key="notices", enabled=False, config={})],
        actor_user_id=superadmin.id,
    )
    db.commit()

    assert auth.client.get("/notices", headers=hdr).status_code == 403
    assert auth.client.get("/notices/archive", headers=hdr).status_code == 403


def test_vault_off_attachment_routes_403_text_works(
    db, society, admin_user, superadmin, auth
):
    """Notices enabled WITHOUT vault: text notice CRUD works; attachment
    routes gate ``require_module('vault')`` -> 403."""
    enable_notices(db, society, superadmin, with_vault=False)
    hdr = admin_bearer(auth, admin_user)

    created = create_notice_http(
        auth.client, hdr, title="Text only", body="<p>hi</p>", publish=True
    )
    assert created.status_code == 200, created.text
    nid = created.json()["id"]

    resp = add_attachment_http(auth.client, hdr, nid, filename="x.png")
    assert resp.status_code == 403, resp.text

    del_resp = auth.client.delete(f"/notices/{nid}/attachments/1", headers=hdr)
    assert del_resp.status_code == 403, del_resp.text


def test_enable_is_idempotent_no_duplicate_perms(
    db, society, admin_user, superadmin, auth
):
    enable_notices(db, society, superadmin)
    enable_notices(db, society, superadmin)  # second enable — must self-heal, not duplicate

    keys = (
        db.query(Permission).filter(Permission.key.like("notices.%")).all()
    )
    assert len(keys) == 3

    role = RoleRepository(db).society_role_by_key(society.id, "society_admin")
    count = db.execute(
        text(
            "SELECT COUNT(*) FROM role_permissions rp "
            "JOIN permissions p ON p.id = rp.permission_id "
            "WHERE rp.role_id = :r AND p.key LIKE 'notices.%'"
        ),
        {"r": role.id},
    ).scalar_one()
    assert count == 3
