"""Smoke tests for the House & Occupancy module (Module 2).

Registry registration, module-enable dependency gate, empty/list/detail/history
response shapes, and the blanket unauthenticated-401 sweep. Companion to the
happy/badpaths/security/edge/e2e files — no scenario duplicated here.
"""
from __future__ import annotations

import pytest

from app.common.errors import DependencyError
from app.core.registry import MODULE_REGISTRY
from app.modules.houses.spec import (
    MODULE_KEY,
    PERM_MANAGE_OCCUPANCY,
    PERM_READ,
    PERM_UPDATE_STATUS,
)
from app.platform.societies.schemas import ModuleAllocation
from app.platform.societies.service import SocietyService

from tests._houses_helpers import (
    _admin_bearer,
    _enable_houses,
    _make_building_with_houses,
    _setup,
)


def test_houses_module_registered_with_three_permissions():
    spec = MODULE_REGISTRY.get(MODULE_KEY)
    assert spec is not None
    keys = {p.key for p in spec.permissions}
    assert keys == {PERM_READ, PERM_UPDATE_STATUS, PERM_MANAGE_OCCUPANCY}


def test_houses_depends_on_onboarding():
    spec = MODULE_REGISTRY.get(MODULE_KEY)
    assert spec.depends_on == ["onboarding"]


def test_default_role_permissions_grants_admin_all_three_residents_none():
    spec = MODULE_REGISTRY.get(MODULE_KEY)
    assert set(spec.default_role_permissions["society_admin"]) == {
        PERM_READ,
        PERM_UPDATE_STATUS,
        PERM_MANAGE_OCCUPANCY,
    }
    assert "resident" not in spec.default_role_permissions


def test_enable_houses_without_onboarding_raises_dependency_error(db, society, superadmin):
    """Enabling houses alone (onboarding not enabled) → DependencyError at the
    service layer (not an HTTP round trip)."""
    with pytest.raises(DependencyError):
        SocietyService(db).set_modules(
            society.id,
            [ModuleAllocation(module_key="houses", enabled=True, config={})],
            actor_user_id=superadmin.id,
        )


def test_enable_both_onboarding_and_houses_succeeds(db, society, superadmin):
    _enable_houses(db, society, superadmin)
    db.expire_all()
    from sqlalchemy import text

    rows = db.execute(
        text(
            "SELECT module_key, enabled FROM society_modules WHERE society_id=:s"
        ),
        {"s": society.id},
    ).all()
    enabled = {r[0]: r[1] for r in rows}
    assert enabled.get("onboarding") is True
    assert enabled.get("houses") is True


def test_empty_house_list_shape(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = auth.client.get("/houses", headers=hdr)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"items": [], "total": 0, "page": 1, "page_size": 20}


def test_list_shape_with_houses(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    resp = auth.client.get("/houses", headers=hdr)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == len(houses)
    assert body["page"] == 1 and body["page_size"] == 20
    item = body["items"][0]
    assert set(item.keys()) >= {
        "id", "society_id", "building_id", "floor_id", "row_id",
        "position_in_row", "number", "status", "first_left_empty_on",
        "display_code",
    }


def test_detail_shape(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    resp = auth.client.get(f"/houses/{houses[0]['id']}", headers=hdr)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body.keys()) == {"house", "owner", "tenant"}
    assert body["owner"] is None and body["tenant"] is None


def test_history_shape_empty(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    resp = auth.client.get(f"/houses/{houses[0]['id']}/history", headers=hdr)
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_all_routes_401_without_auth(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]

    assert auth.client.get("/houses").status_code == 401
    assert auth.client.get(f"/houses/{hid}").status_code == 401
    assert auth.client.get(f"/houses/{hid}/history").status_code == 401
    assert auth.client.post(
        f"/houses/{hid}/status",
        json={"to_status": "owned", "owner": {
            "full_name": "X", "email": "x@x.com", "contact_number": "1",
            "persons_living": 1,
        }},
    ).status_code == 401
    assert auth.client.patch(
        f"/houses/{hid}/occupancy/owner", json={"full_name": "Y"}
    ).status_code == 401
