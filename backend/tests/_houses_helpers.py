"""Shared test harness for the House & Occupancy (Module 2) test suite.

Mirrors the patterns in test_onboarding_*.py: must-change dance, ModuleAllocation
enable, crafted tokens, cross-society setup. Import from this module in every
test_houses_*.py file (DRY).
"""
from __future__ import annotations

from app.platform.models import AuditLog
from app.platform.societies.schemas import ModuleAllocation
from app.platform.societies.service import SocietyService
from tests.conftest import DEFAULT_MEMBER_PASSWORD

MODULE_KEY = "houses"
NEWPASS = "NewPass123"


def _enable_houses(db, society, superadmin) -> None:
    """Enable onboarding + houses (houses depends_on onboarding)."""
    SocietyService(db).set_modules(
        society.id,
        [
            ModuleAllocation(module_key="onboarding", enabled=True, config={}),
            ModuleAllocation(module_key="houses", enabled=True, config={}),
        ],
        actor_user_id=superadmin.id,
    )
    db.commit()


def _admin_bearer(auth, admin_user) -> dict[str, str]:
    """must_change → change-password → re-login. Returns a usable bearer header."""
    tokens = auth.login_ok(admin_user.email, DEFAULT_MEMBER_PASSWORD)
    resp = auth.client.post(
        "/auth/change-password",
        headers=auth.bearer(tokens["access_token"]),
        json={"current_password": DEFAULT_MEMBER_PASSWORD, "new_password": NEWPASS},
    )
    assert resp.status_code == 200, resp.text
    sess = auth.login_ok(admin_user.email, NEWPASS)
    return auth.bearer(sess["access_token"])


def _setup(db, society, admin_user, superadmin, auth) -> dict[str, str]:
    """Enable houses + return an activated admin bearer header."""
    _enable_houses(db, society, superadmin)
    return _admin_bearer(auth, admin_user)


def _make_building_with_houses(
    auth, hdr, floors=None, names=None
) -> list[dict]:
    """building type → one building 'A' mapped AUTO → returns the houses JSON.

    Default: floors=[{"level":1,"houses_count":2}] → numbers "101","102",
    display codes "A-101"/"A-102".
    """
    if floors is None:
        floors = [{"level": 1, "houses_count": 2}]
    if names is None:
        names = ["A"]
    r = auth.client.post("/onboarding/type", headers=hdr, json={"type": "building"})
    assert r.status_code == 200, r.text
    r = auth.client.post("/onboarding/buildings", headers=hdr, json={"names": names})
    assert r.status_code == 200, r.text
    building = r.json()[0]
    r = auth.client.post(
        f"/onboarding/buildings/{building['id']}/map",
        headers=hdr,
        json={
            "floors": floors,
            "numbering_config": {"mode": "auto", "count_pad": 2, "ground_prefix": "G"},
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _make_individual_houses(auth, hdr) -> list[dict]:
    """individual_houses type → 2 sequential houses "1","2", building_id None."""
    r = auth.client.post(
        "/onboarding/type", headers=hdr, json={"type": "individual_houses"}
    )
    assert r.status_code == 200, r.text
    r = auth.client.post(
        "/onboarding/rows",
        headers=hdr,
        json={
            "rows": [
                {
                    "display_order": 1,
                    "houses_count": 2,
                    "numbering_config": {"mode": "sequential"},
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _owner(**over) -> dict:
    body = {
        "full_name": "Owner One",
        "email": "owner1@x.com",
        "contact_number": "555-0001",
    }
    body.update(over)
    return body


def _tenant(**over) -> dict:
    body = {
        "full_name": "Tenant One",
        "contact_number": "555-9001",
        "persons_living": 2,
    }
    body.update(over)
    return body


def _set_status(auth, hdr, house_id, to_status, owner, tenant=None):
    body: dict = {"to_status": to_status, "owner": owner}
    if tenant is not None:
        body["tenant"] = tenant
    return auth.client.post(f"/houses/{house_id}/status", headers=hdr, json=body)


def _audit(db, action, *, society_id=None, entity_id=None):
    q = db.query(AuditLog).filter(AuditLog.action == action)
    if society_id is not None:
        q = q.filter(AuditLog.society_id == society_id)
    if entity_id is not None:
        q = q.filter(AuditLog.entity_id == entity_id)
    return q.all()


def _occ(db, house_id, party, current_only=True):
    from app.modules.houses.models import HouseOccupancy

    q = db.query(HouseOccupancy).filter(
        HouseOccupancy.house_id == house_id, HouseOccupancy.party_type == party
    )
    if current_only:
        q = q.filter(HouseOccupancy.is_current.is_(True))
    return q.all()
