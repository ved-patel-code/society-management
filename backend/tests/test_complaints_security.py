"""Adversarial / permission-boundary tests for Complaints (Module 5).

Covers: every admin-only action rejected for a resident (403), the caller with
NO complaints perms at all, unauthenticated calls (401), the super-admin
read_all bypass, resident vs read_all visibility scoping (including the
house_id filter cannot be used to widen a resident's own view), cross-society
crafted JWTs, and manage_categories gating every category CUD route.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from tests._complaints_helpers import (
    admin_bearer,
    crafted_bearer,
    owned_house_for,
    owner_login_bearer,
    raise_complaint,
    resident_bearer,
    second_society_with_complaints,
    setup_complaints,
)
from tests._houses_helpers import _make_building_with_houses, _set_status


def _category_id(auth, hdr, name="Plumbing") -> int:
    resp = auth.client.get("/complaints/categories", headers=hdr)
    assert resp.status_code == 200, resp.text
    for c in resp.json():
        if c["name"] == name:
            return c["id"]
    raise AssertionError(f"category {name!r} not seeded")


def _strip_permission(db, society_id, perm_key) -> None:
    role = None
    from app.platform.roles.repository import RoleRepository

    role = RoleRepository(db).society_role_by_key(society_id, "society_admin")
    perm_id = db.execute(
        text("SELECT id FROM permissions WHERE key=:k"), {"k": perm_key}
    ).scalar_one()
    db.execute(
        text("DELETE FROM role_permissions WHERE role_id=:r AND permission_id=:p"),
        {"r": role.id, "p": perm_id},
    )
    db.commit()


def _strip_all_complaints_perms(db, society_id, role_key) -> None:
    from app.platform.roles.repository import RoleRepository

    role = RoleRepository(db).society_role_by_key(society_id, role_key)
    db.execute(
        text(
            "DELETE FROM role_permissions WHERE role_id=:r AND permission_id IN "
            "(SELECT id FROM permissions WHERE key LIKE 'complaints.%')"
        ),
        {"r": role.id},
    )
    db.commit()


# ===========================================================================
# resident forbidden on every admin-only action
# ===========================================================================


def test_resident_forbidden_change_status(db, society, admin_user, resident_user, superadmin, auth):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    hid = owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = raise_complaint(auth, r_hdr, category_id=cat, title="x", description="y")["id"]

    resp = auth.client.post(
        f"/complaints/{cid}/status", headers=r_hdr, json={"to_status": "in_progress"}
    )
    assert resp.status_code == 403, resp.text


def test_resident_forbidden_resolve(db, society, admin_user, superadmin, auth):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = raise_complaint(auth, r_hdr, category_id=cat, title="x", description="y")["id"]

    resp = auth.client.post(
        f"/complaints/{cid}/resolve", headers=r_hdr, data={"note": "x"}
    )
    assert resp.status_code == 403, resp.text


def test_resident_forbidden_create_category(db, society, admin_user, superadmin, auth):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    resp = auth.client.post(
        "/complaints/categories", headers=r_hdr, json={"name": "New Cat"}
    )
    assert resp.status_code == 403, resp.text


def test_resident_forbidden_patch_category(db, society, admin_user, superadmin, auth):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    cat = _category_id(auth, hdr)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    resp = auth.client.patch(
        f"/complaints/categories/{cat}", headers=r_hdr, json={"name": "Hijack"}
    )
    assert resp.status_code == 403, resp.text


def test_resident_forbidden_delete_category(db, society, admin_user, superadmin, auth):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    cat = _category_id(auth, hdr)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    resp = auth.client.delete(f"/complaints/categories/{cat}", headers=r_hdr)
    assert resp.status_code == 403, resp.text


def test_resident_forbidden_read_config(db, society, admin_user, superadmin, auth):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    resp = auth.client.get("/complaints/config", headers=r_hdr)
    assert resp.status_code == 403, resp.text


def test_resident_forbidden_write_config(db, society, admin_user, superadmin, auth):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    resp = auth.client.put(
        "/complaints/config", headers=r_hdr, json={"auto_archive_days": 30}
    )
    assert resp.status_code == 403, resp.text


# ===========================================================================
# caller with NO complaints perms at all
# ===========================================================================


def test_caller_with_no_complaints_perms_403(
    db, society, admin_user, resident_user, superadmin, auth
):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    _strip_all_complaints_perms(db, society.id, "resident")
    r_hdr = resident_bearer(auth, resident_user)

    assert auth.client.get("/complaints", headers=r_hdr).status_code == 403
    assert auth.client.get("/complaints/categories", headers=r_hdr).status_code == 403
    assert auth.client.post(
        "/complaints", headers=r_hdr, json={"category_id": 1, "title": "x", "description": "y"}
    ).status_code == 403


# ===========================================================================
# unauthenticated -> 401
# ===========================================================================


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/complaints"),
        ("GET", "/complaints/1"),
        ("POST", "/complaints"),
        ("GET", "/complaints/categories"),
        ("POST", "/complaints/categories"),
        ("GET", "/complaints/config"),
        ("PUT", "/complaints/config"),
        ("POST", "/complaints/1/status"),
        ("POST", "/complaints/1/resolve"),
        ("POST", "/complaints/1/withdraw"),
    ],
)
def test_unauthenticated_401(db, society, admin_user, superadmin, auth, method, path):
    setup_complaints(db, society, admin_user, superadmin, auth)
    resp = auth.client.request(method, path)
    assert resp.status_code == 401, resp.text


# ===========================================================================
# super-admin read_all bypass
# ===========================================================================


def test_super_admin_bypass_read_all(db, society, admin_user, superadmin, auth):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    raise_complaint(auth, r_hdr, category_id=cat, title="x", description="y")

    # Strip read_all from society_admin; promote it to platform super-admin so
    # is_super_admin (not the permission) drives the bypass.
    _strip_permission(db, society.id, "complaints.read_all")
    admin_user.is_platform_super_admin = True
    db.add(admin_user)
    db.commit()

    resp = auth.client.get("/complaints", headers=hdr)
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 1


# ===========================================================================
# read vs read_all resident-scoped list
# ===========================================================================


def test_read_vs_read_all_resident_scoped_list(db, society, admin_user, superadmin, auth):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid_a, hid_b = houses[0]["id"], houses[1]["id"]
    owner_a = {"full_name": "A", "email": "a@x.com", "contact_number": "1", "persons_living": 1}
    owner_b = {"full_name": "B", "email": "b@x.com", "contact_number": "2", "persons_living": 1}
    assert _set_status(auth, hdr, hid_a, "owned", owner_a).status_code == 200
    assert _set_status(auth, hdr, hid_b, "owned", owner_b).status_code == 200
    a_hdr, _a = owner_login_bearer(auth, db, email="a@x.com")
    b_hdr, _b = owner_login_bearer(auth, db, email="b@x.com")
    cat = _category_id(auth, hdr)

    ca = raise_complaint(auth, a_hdr, category_id=cat, title="a-issue", description="y")
    cb = raise_complaint(auth, b_hdr, category_id=cat, title="b-issue", description="y")

    resp_a = auth.client.get("/complaints", headers=a_hdr)
    assert resp_a.json()["total"] == 1
    assert [it["id"] for it in resp_a.json()["items"]] == [ca["id"]]

    resp_admin = auth.client.get("/complaints", headers=hdr)
    assert resp_admin.json()["total"] == 2


def test_resident_cannot_get_other_house_complaint_by_id_403(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid_a, hid_b = houses[0]["id"], houses[1]["id"]
    owner_a = {"full_name": "A", "email": "a@x.com", "contact_number": "1", "persons_living": 1}
    owner_b = {"full_name": "B", "email": "b@x.com", "contact_number": "2", "persons_living": 1}
    assert _set_status(auth, hdr, hid_a, "owned", owner_a).status_code == 200
    assert _set_status(auth, hdr, hid_b, "owned", owner_b).status_code == 200
    a_hdr, _a = owner_login_bearer(auth, db, email="a@x.com")
    b_hdr, _b = owner_login_bearer(auth, db, email="b@x.com")
    cat = _category_id(auth, hdr)
    ca = raise_complaint(auth, a_hdr, category_id=cat, title="a-issue", description="y")

    resp = auth.client.get(f"/complaints/{ca['id']}", headers=b_hdr)
    assert resp.status_code == 403, resp.text


def test_resident_house_id_filter_cannot_widen(db, society, admin_user, superadmin, auth):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid_a, hid_b = houses[0]["id"], houses[1]["id"]
    owner_a = {"full_name": "A", "email": "a@x.com", "contact_number": "1", "persons_living": 1}
    owner_b = {"full_name": "B", "email": "b@x.com", "contact_number": "2", "persons_living": 1}
    assert _set_status(auth, hdr, hid_a, "owned", owner_a).status_code == 200
    assert _set_status(auth, hdr, hid_b, "owned", owner_b).status_code == 200
    a_hdr, _a = owner_login_bearer(auth, db, email="a@x.com")
    b_hdr, _b = owner_login_bearer(auth, db, email="b@x.com")
    cat = _category_id(auth, hdr)
    raise_complaint(auth, b_hdr, category_id=cat, title="b-issue", description="y")

    # A tries to widen their view by asking for B's house_id explicitly.
    resp = auth.client.get("/complaints", headers=a_hdr, params={"house_id": hid_b})
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 0
    assert resp.json()["items"] == []


def test_admin_read_all_sees_any_house(db, society, admin_user, superadmin, auth):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid_a, hid_b = houses[0]["id"], houses[1]["id"]
    owner_a = {"full_name": "A", "email": "a@x.com", "contact_number": "1", "persons_living": 1}
    owner_b = {"full_name": "B", "email": "b@x.com", "contact_number": "2", "persons_living": 1}
    assert _set_status(auth, hdr, hid_a, "owned", owner_a).status_code == 200
    assert _set_status(auth, hdr, hid_b, "owned", owner_b).status_code == 200
    a_hdr, _a = owner_login_bearer(auth, db, email="a@x.com")
    b_hdr, _b = owner_login_bearer(auth, db, email="b@x.com")
    cat = _category_id(auth, hdr)
    raise_complaint(auth, a_hdr, category_id=cat, title="a-issue", description="y")
    raise_complaint(auth, b_hdr, category_id=cat, title="b-issue", description="y")

    resp = auth.client.get("/complaints", headers=hdr, params={"house_id": hid_b})
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["title"] == "b-issue"


# ===========================================================================
# crafted / cross-society token
# ===========================================================================


def test_cross_society_token_cannot_act(
    db, society, admin_user, superadmin, auth, make_token
):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = raise_complaint(auth, r_hdr, category_id=cat, title="x", description="y")["id"]

    # Society B, independent.
    soc_b, _admin_b, _hdr_b = second_society_with_complaints(db, superadmin, auth)

    # A crafted token: raiser's real user_id but claiming active_society_id=B,
    # with NO roles (role_ids=[]) — should fail either as 401 (bad claim) or 403
    # (no permission / module scope mismatch).
    bad_hdr = crafted_bearer(
        make_token, user_id=raiser.id, society_id=soc_b.id, role_ids=[]
    )
    resp = auth.client.get(f"/complaints/{cid}", headers=bad_hdr)
    assert resp.status_code in (401, 403), resp.text

    # And it certainly cannot see A's complaint via B's scope even if some route
    # accepted the token (society-scoped lookup would 404 it, not leak it):
    resp2 = auth.client.get("/complaints", headers=bad_hdr)
    assert resp2.status_code in (401, 403, 200), resp2.text
    if resp2.status_code == 200:
        assert resp2.json()["total"] == 0


# ===========================================================================
# manage_categories required on ALL category CUD
# ===========================================================================


def test_manage_categories_permission_required_on_all_cud(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    cat = _category_id(auth, hdr)
    _strip_permission(db, society.id, "complaints.manage_categories")

    assert auth.client.post(
        "/complaints/categories", headers=hdr, json={"name": "New"}
    ).status_code == 403
    assert auth.client.patch(
        f"/complaints/categories/{cat}", headers=hdr, json={"name": "New"}
    ).status_code == 403
    assert auth.client.delete(
        f"/complaints/categories/{cat}", headers=hdr
    ).status_code == 403
