"""Bad-path / validation / conflict tests for the House & Occupancy module.

Covers illegal transitions, required-field gaps, out-of-range values, missing
resources, and pagination validation. Asserts status code + (where useful) that
no partial DB writes leaked.
"""
from __future__ import annotations

from app.modules.houses.models import HouseOccupancy, HouseStatusHistory
from sqlalchemy import select

from tests._houses_helpers import (
    _make_building_with_houses,
    _owner,
    _set_status,
    _setup,
    _tenant,
)


# ===========================================================================
# -> empty from any non-empty status -> 409
# ===========================================================================

def test_owned_to_empty_is_409(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    resp = auth.client.post(
        f"/houses/{hid}/status", headers=hdr,
        json={"to_status": "empty", "owner": _owner()},
    )
    assert resp.status_code == 409


def test_to_let_to_empty_is_409(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "to_let", _owner())
    resp = auth.client.post(
        f"/houses/{hid}/status", headers=hdr,
        json={"to_status": "empty", "owner": _owner()},
    )
    assert resp.status_code == 409


def test_for_sale_to_empty_is_409(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "for_sale", _owner())
    resp = auth.client.post(
        f"/houses/{hid}/status", headers=hdr,
        json={"to_status": "empty", "owner": _owner()},
    )
    assert resp.status_code == 409


def test_rented_to_empty_is_409(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "rented", _owner(), _tenant())
    resp = auth.client.post(
        f"/houses/{hid}/status", headers=hdr,
        json={"to_status": "empty", "owner": _owner()},
    )
    assert resp.status_code == 409


def test_empty_to_empty_is_409(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = auth.client.post(
        f"/houses/{hid}/status", headers=hdr,
        json={"to_status": "empty", "owner": _owner()},
    )
    assert resp.status_code == 409


# ===========================================================================
# unknown target status -> 422
# ===========================================================================

def test_unknown_to_status_is_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = auth.client.post(
        f"/houses/{hid}/status", headers=hdr,
        json={"to_status": "leased", "owner": _owner()},
    )
    assert resp.status_code == 422


# ===========================================================================
# required-field gates
# ===========================================================================

def test_owned_missing_persons_living_is_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "owned", _owner())
    assert resp.status_code == 422


def test_to_let_with_persons_living_is_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "to_let", _owner(persons_living=1))
    assert resp.status_code == 422


def test_for_sale_with_persons_living_is_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "for_sale", _owner(persons_living=1))
    assert resp.status_code == 422


def test_rented_missing_tenant_is_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "rented", _owner())
    assert resp.status_code == 422


def test_rented_tenant_missing_persons_living_is_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(
        auth, hdr, hid, "rented", _owner(),
        _tenant(persons_living=None),
    )
    assert resp.status_code == 422


def test_tenant_for_owned_target_is_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "owned", _owner(persons_living=1), _tenant())
    assert resp.status_code == 422


def test_tenant_for_to_let_target_is_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "to_let", _owner(), _tenant())
    assert resp.status_code == 422


# ===========================================================================
# owner payload field validation
# ===========================================================================

def test_owner_missing_full_name_is_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    body = _owner(persons_living=1)
    del body["full_name"]
    resp = auth.client.post(
        f"/houses/{hid}/status", headers=hdr,
        json={"to_status": "owned", "owner": body},
    )
    assert resp.status_code == 422


def test_owner_missing_email_is_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    body = _owner(persons_living=1)
    del body["email"]
    resp = auth.client.post(
        f"/houses/{hid}/status", headers=hdr,
        json={"to_status": "owned", "owner": body},
    )
    assert resp.status_code == 422


def test_owner_missing_contact_number_is_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    body = _owner(persons_living=1)
    del body["contact_number"]
    resp = auth.client.post(
        f"/houses/{hid}/status", headers=hdr,
        json={"to_status": "owned", "owner": body},
    )
    assert resp.status_code == 422


def test_owner_empty_full_name_is_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "owned", _owner(full_name="", persons_living=1))
    assert resp.status_code == 422


def test_owner_empty_email_is_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "owned", _owner(email="", persons_living=1))
    assert resp.status_code == 422


def test_owner_persons_living_negative_is_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "owned", _owner(persons_living=-1))
    assert resp.status_code == 422


def test_owner_email_too_long_is_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    long_email = "a" * 315 + "@x.com"  # > 320 chars total
    assert len(long_email) > 320
    resp = _set_status(auth, hdr, hid, "owned", _owner(email=long_email, persons_living=1))
    assert resp.status_code == 422


def test_owner_full_name_too_long_is_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(
        auth, hdr, hid, "owned", _owner(full_name="x" * 256, persons_living=1)
    )
    assert resp.status_code == 422


def test_tenant_persons_living_negative_is_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "rented", _owner(), _tenant(persons_living=-3))
    assert resp.status_code == 422


# ===========================================================================
# missing house -> 404
# ===========================================================================

def test_status_change_on_missing_house_is_404(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = _set_status(auth, hdr, 999999, "owned", _owner(persons_living=1))
    assert resp.status_code == 404


def test_detail_on_missing_house_is_404(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = auth.client.get("/houses/999999", headers=hdr)
    assert resp.status_code == 404


def test_history_on_missing_house_is_404(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = auth.client.get("/houses/999999/history", headers=hdr)
    assert resp.status_code == 404


# ===========================================================================
# PATCH occupancy bad paths
# ===========================================================================

def test_patch_unknown_party_is_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = auth.client.patch(
        f"/houses/{hid}/occupancy/landlord", headers=hdr, json={"full_name": "X"}
    )
    assert resp.status_code == 422


def test_patch_owner_when_none_current_is_404(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = auth.client.patch(
        f"/houses/{hid}/occupancy/owner", headers=hdr, json={"full_name": "X"}
    )
    assert resp.status_code == 404


def test_patch_tenant_when_none_current_is_404(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    resp = auth.client.patch(
        f"/houses/{hid}/occupancy/tenant", headers=hdr, json={"full_name": "X"}
    )
    assert resp.status_code == 404


def test_patch_owner_persons_living_on_to_let_is_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "to_let", _owner())
    resp = auth.client.patch(
        f"/houses/{hid}/occupancy/owner", headers=hdr, json={"persons_living": 2}
    )
    assert resp.status_code == 422


def test_patch_owner_persons_living_on_for_sale_is_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "for_sale", _owner())
    resp = auth.client.patch(
        f"/houses/{hid}/occupancy/owner", headers=hdr, json={"persons_living": 2}
    )
    assert resp.status_code == 422


def test_patch_missing_house_is_404(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = auth.client.patch(
        "/houses/999999/occupancy/owner", headers=hdr, json={"full_name": "X"}
    )
    assert resp.status_code == 404


def test_patch_negative_persons_living_is_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    resp = auth.client.patch(
        f"/houses/{hid}/occupancy/owner", headers=hdr, json={"persons_living": -5}
    )
    assert resp.status_code == 422


# ===========================================================================
# pagination validation
# ===========================================================================

def test_page_size_over_100_is_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = auth.client.get("/houses", headers=hdr, params={"page_size": 101})
    assert resp.status_code == 422


def test_page_size_zero_is_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = auth.client.get("/houses", headers=hdr, params={"page_size": 0})
    assert resp.status_code == 422


def test_page_less_than_1_is_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = auth.client.get("/houses", headers=hdr, params={"page": 0})
    assert resp.status_code == 422


def test_page_negative_is_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = auth.client.get("/houses", headers=hdr, params={"page": -1})
    assert resp.status_code == 422


def test_building_id_non_integer_is_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = auth.client.get("/houses", headers=hdr, params={"building_id": "abc"})
    assert resp.status_code == 422


def test_floor_id_non_integer_is_422(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = auth.client.get("/houses", headers=hdr, params={"floor_id": "abc"})
    assert resp.status_code == 422


# ===========================================================================
# failed status change persists nothing
# ===========================================================================

def test_failed_status_change_persists_nothing(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "owned", _owner())  # missing persons_living -> 422
    assert resp.status_code == 422

    db.expire_all()
    occs = db.execute(
        select(HouseOccupancy).where(HouseOccupancy.house_id == hid)
    ).scalars().all()
    assert len(occs) == 0
    history = db.execute(
        select(HouseStatusHistory).where(HouseStatusHistory.house_id == hid)
    ).scalars().all()
    assert len(history) == 0

    from app.modules.onboarding.models import House

    house = db.get(House, hid)
    assert house.status == "empty"


def test_rented_without_tenant_leaves_no_owner_write(db, society, admin_user, superadmin, auth):
    """rented target missing tenant -> 422; owner reconcile must not have persisted
    either (whole transaction rolled back by get_session on non-2xx)."""
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "rented", _owner())
    assert resp.status_code == 422

    db.expire_all()
    occs = db.execute(
        select(HouseOccupancy).where(HouseOccupancy.house_id == hid)
    ).scalars().all()
    assert len(occs) == 0
