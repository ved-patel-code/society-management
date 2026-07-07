"""End-to-end tests for the House & Occupancy module.

Full building lifecycle journeys, audit completeness, DELETE guards (onboarding
delete blocked by occupancy — cross-module contract), and orphan deactivation
driven fully through HTTP.
"""
from __future__ import annotations

from sqlalchemy import select

from app.modules.houses.models import HouseOccupancy, HouseStatusHistory
from app.platform.models import User

from tests._houses_helpers import (
    _audit,
    _make_building_with_houses,
    _make_individual_houses,
    _owner,
    _set_status,
    _setup,
    _tenant,
)


def test_full_building_lifecycle_four_statuses(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]

    r1 = _set_status(auth, hdr, hid, "owned", _owner(persons_living=2))
    assert r1.status_code == 200 and r1.json()["house"]["status"] == "owned"

    r2 = _set_status(auth, hdr, hid, "to_let", _owner())
    assert r2.status_code == 200 and r2.json()["house"]["status"] == "to_let"

    r3 = _set_status(auth, hdr, hid, "rented", _owner(), _tenant())
    assert r3.status_code == 200 and r3.json()["house"]["status"] == "rented"

    r4 = _set_status(auth, hdr, hid, "for_sale", _owner())
    assert r4.status_code == 200 and r4.json()["house"]["status"] == "for_sale"


def test_filter_by_status_across_journey(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr, floors=[{"level": 1, "houses_count": 3}])
    _set_status(auth, hdr, houses[0]["id"], "owned", _owner(persons_living=1))
    _set_status(auth, hdr, houses[1]["id"], "to_let", _owner())

    resp_owned = auth.client.get("/houses", headers=hdr, params={"status": "owned"})
    resp_empty = auth.client.get("/houses", headers=hdr, params={"status": "empty"})
    assert resp_owned.json()["total"] == 1
    assert resp_empty.json()["total"] == 1


def test_replace_owner_mid_journey(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    _set_status(auth, hdr, hid, "owned", _owner(email="replaced@x.com", persons_living=2))
    resp = _set_status(auth, hdr, hid, "to_let", _owner(email="replaced@x.com"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["owner"]["email"] == "replaced@x.com"


def test_current_owner_user_ids_after_journey_four_distinct(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr, floors=[{"level": 1, "houses_count": 4}])
    from app.modules.houses.service import HouseService

    for i, h in enumerate(houses):
        _set_status(auth, hdr, h["id"], "owned", _owner(email=f"owner{i}@x.com", persons_living=1))

    db.expire_all()
    ids = HouseService(db).current_owner_user_ids(society.id)
    assert len(ids) == 4


def test_audit_completeness_across_journey(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    # empty->owned (status_changed #1, occupancy_created owner #1)
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    # owned->owned different email (owner_replaced #1, no status_changed)
    _set_status(auth, hdr, hid, "owned", _owner(email="second@x.com", persons_living=1))
    # owned->rented (status_changed #2, occupancy_created tenant #1, occupancy_updated owner)
    _set_status(
        auth, hdr, hid, "rented", _owner(email="second@x.com", persons_living=1), _tenant()
    )
    # rented->to_let (status_changed #3, tenant closed)
    _set_status(auth, hdr, hid, "to_let", _owner(email="second@x.com"))
    # to_let->for_sale (status_changed #4)
    _set_status(auth, hdr, hid, "for_sale", _owner(email="second@x.com"))

    db.expire_all()
    status_changed = _audit(db, "house.status_changed", society_id=society.id, entity_id=hid)
    occupancy_created = _audit(db, "house.occupancy_created", society_id=society.id, entity_id=hid)
    owner_replaced = _audit(db, "house.owner_replaced", society_id=society.id, entity_id=hid)

    assert len(status_changed) == 4
    assert len(occupancy_created) == 2  # owner (empty->owned) + tenant (owned->rented)
    assert len(owner_replaced) == 1


def test_history_reflects_journey_one_row_for_same_status_replacement(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    # same-status replace (owner_replaced, no NEW history row)
    _set_status(auth, hdr, hid, "owned", _owner(email="repl@x.com", persons_living=1))
    _set_status(auth, hdr, hid, "to_let", _owner(email="repl@x.com"))

    resp = auth.client.get(f"/houses/{hid}/history", headers=hdr)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # 2 real transitions: empty->owned, owned->to_let. The same-status replace
    # added no 3rd row.
    assert len(body) == 2
    assert body[0]["to_status"] == "to_let"
    assert body[1]["to_status"] == "owned"


def test_individual_house_society_flow(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_individual_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "rented", _owner(), _tenant())
    assert resp.status_code == 200, resp.text
    assert resp.json()["house"]["building_id"] is None
    assert resp.json()["house"]["display_code"] == houses[0]["number"]


def test_complete_onboarding_then_operate_houses(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    complete = auth.client.post("/onboarding/complete", headers=hdr)
    assert complete.status_code == 200, complete.text

    resp = _set_status(auth, hdr, houses[0]["id"], "owned", _owner(persons_living=1))
    assert resp.status_code == 200, resp.text


# ===========================================================================
# DELETE guards (onboarding delete blocked by houses occupancy)
# ===========================================================================

def test_delete_house_blocked_after_owned(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    resp = auth.client.delete(f"/onboarding/houses/{hid}", headers=hdr)
    assert resp.status_code == 409


def test_delete_house_blocked_by_occupancy_even_if_status_forced_empty(
    db, society, admin_user, superadmin, auth
):
    """Force the house.status column back to 'empty' directly (bypassing the
    service, which normally forbids it) — the occupancy guard (defense-in-depth)
    must still block the delete since a current occupancy row survives."""
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))

    from app.modules.onboarding.models import House

    house = db.get(House, hid)
    house.status = "empty"
    db.commit()

    resp = auth.client.delete(f"/onboarding/houses/{hid}", headers=hdr)
    assert resp.status_code == 409
    assert resp.json()["message"] == "Cannot delete: houses have active occupancy."


def test_delete_floor_blocked_by_occupancy(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    fid = houses[0]["floor_id"]
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    resp = auth.client.delete(f"/onboarding/floors/{fid}", headers=hdr)
    assert resp.status_code == 409


def test_delete_building_blocked_by_occupancy(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    bid = houses[0]["building_id"]
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    resp = auth.client.delete(f"/onboarding/buildings/{bid}", headers=hdr)
    assert resp.status_code == 409


def test_delete_empty_house_no_occupancy_succeeds(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = auth.client.delete(f"/onboarding/houses/{hid}", headers=hdr)
    assert resp.status_code == 204


def test_delete_building_all_empty_succeeds(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    bid = houses[0]["building_id"]
    resp = auth.client.delete(f"/onboarding/buildings/{bid}", headers=hdr)
    assert resp.status_code == 204


def test_owner_replacement_through_http_keeps_resident_role_not_deactivated(
    db, society, admin_user, superadmin, auth
):
    """Full e2e: an owner replaced on a house via HTTP keeps their resident role
    (create_or_link_user's role attach is not undone by revoke_house_access, which
    only closes the occupancy) -> access_revoked fires but orphaned/deactivated
    are both False. (The true orphan-deactivation path additionally requires the
    role to be removed — covered directly at the DB layer in test_houses_edge.py,
    since there is no HTTP route to strip a single role from a user in this API.)
    """
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]

    r1 = _set_status(auth, hdr, hid, "owned", _owner(email="first@x.com", persons_living=1))
    first_user_id = r1.json()["owner"]["user_id"]

    r2 = _set_status(auth, hdr, hid, "owned", _owner(email="second@x.com", persons_living=1))
    assert r2.status_code == 200, r2.text

    db.expire_all()
    first_user = db.get(User, first_user_id)
    assert first_user.is_active is True

    revoked = _audit(db, "house.access_revoked", entity_id=first_user_id)
    assert len(revoked) == 1
    assert revoked[0].after["orphaned"] is False
    assert revoked[0].after["deactivated"] is False
