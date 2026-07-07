"""Happy-path tests for the House & Occupancy module (Module 2).

Full lifecycle transitions, filters, pagination, detail/history shapes, email
normalization, id_proof roundtrips, and new-account provisioning defaults.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.modules.houses.models import HouseOccupancy, HouseStatusHistory
from app.platform.models import AuditLog, User

from tests._houses_helpers import (
    _audit,
    _make_building_with_houses,
    _make_individual_houses,
    _make_vault_doc,
    _occ,
    _owner,
    _set_status,
    _setup,
    _tenant,
)


# ===========================================================================
# empty -> X transitions
# ===========================================================================

def test_empty_to_owned(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "owned", _owner(persons_living=3))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["house"]["status"] == "owned"
    assert body["owner"]["email"] == "owner1@x.com"
    assert body["owner"]["persons_living"] == 3


def test_empty_to_to_let(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "to_let", _owner())
    assert resp.status_code == 200, resp.text
    assert resp.json()["house"]["status"] == "to_let"
    assert resp.json()["owner"]["persons_living"] is None


def test_empty_to_for_sale(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "for_sale", _owner())
    assert resp.status_code == 200, resp.text
    assert resp.json()["house"]["status"] == "for_sale"


def test_empty_to_rented(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "rented", _owner(), _tenant())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["house"]["status"] == "rented"
    assert body["tenant"]["full_name"] == "Tenant One"


# ===========================================================================
# owned <-> rented
# ===========================================================================

def test_owned_to_rented_owner_retained_tenant_opened(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=2))
    resp = _set_status(auth, hdr, hid, "rented", _owner(persons_living=2), _tenant())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["owner"]["user_id"] is not None
    assert body["tenant"]["user_id"] is None


def test_rented_to_owned_tenant_closed(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "rented", _owner(), _tenant())
    resp = _set_status(auth, hdr, hid, "owned", _owner(persons_living=2))
    assert resp.status_code == 200, resp.text
    assert resp.json()["tenant"] is None

    db.expire_all()
    closed = _occ(db, hid, "tenant", current_only=False)
    assert len(closed) == 1
    assert closed[0].is_current is False
    assert closed[0].valid_to == date.today()


# ===========================================================================
# owned -> to_let -> owned : owner login + id_proof retained
# ===========================================================================

def test_owned_to_to_let_to_owned_owner_login_and_id_proof_retained(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    did = _make_vault_doc(db, society.id)
    _set_status(
        auth, hdr, hid, "owned",
        _owner(persons_living=2, id_proof_type="aadhaar", id_proof_document_id=did),
    )
    r1 = _set_status(auth, hdr, hid, "to_let", _owner())
    assert r1.status_code == 200, r1.text
    assert r1.json()["owner"]["id_proof_type"] == "aadhaar"
    assert r1.json()["owner"]["id_proof_document_id"] == did
    same_user_id = r1.json()["owner"]["user_id"]

    r2 = _set_status(auth, hdr, hid, "owned", _owner(persons_living=4))
    assert r2.status_code == 200, r2.text
    assert r2.json()["owner"]["user_id"] == same_user_id
    assert r2.json()["owner"]["id_proof_type"] == "aadhaar"
    assert r2.json()["owner"]["id_proof_document_id"] == did


# ===========================================================================
# PATCH edits
# ===========================================================================

def test_owner_same_email_patch_edit(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=2))
    resp = auth.client.patch(
        f"/houses/{hid}/occupancy/owner", headers=hdr, json={"contact_number": "999-8888"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["owner"]["contact_number"] == "999-8888"
    assert resp.json()["owner"]["email"] == "owner1@x.com"


def test_tenant_patch_edit(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "rented", _owner(), _tenant())
    resp = auth.client.patch(
        f"/houses/{hid}/occupancy/tenant", headers=hdr, json={"persons_living": 5}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["tenant"]["persons_living"] == 5


# ===========================================================================
# list filters
# ===========================================================================

def test_list_filter_by_status(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    _set_status(auth, hdr, houses[0]["id"], "owned", _owner(persons_living=1))
    resp = auth.client.get("/houses", headers=hdr, params={"status": "owned"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == houses[0]["id"]


def test_list_filter_by_building_id(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr, names=["A", "B"])
    bid_a = houses[0]["building_id"]
    resp = auth.client.get("/houses", headers=hdr, params={"building_id": bid_a})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert all(h["building_id"] == bid_a for h in body["items"])


def test_list_filter_by_floor_id(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(
        auth, hdr, floors=[{"level": 1, "houses_count": 2}, {"level": 2, "houses_count": 1}]
    )
    floor_ids = {h["floor_id"] for h in houses}
    fid = houses[0]["floor_id"]
    resp = auth.client.get("/houses", headers=hdr, params={"floor_id": fid})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert all(h["floor_id"] == fid for h in body["items"])
    assert len(floor_ids) == 2


def test_list_filter_by_number(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    number = houses[0]["number"]
    resp = auth.client.get("/houses", headers=hdr, params={"number": number})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["number"] == number


# ===========================================================================
# pagination
# ===========================================================================

def test_pagination_page1(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _make_building_with_houses(auth, hdr, floors=[{"level": 1, "houses_count": 3}])
    resp = auth.client.get("/houses", headers=hdr, params={"page": 1, "page_size": 2})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 2


def test_pagination_page2(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _make_building_with_houses(auth, hdr, floors=[{"level": 1, "houses_count": 3}])
    resp = auth.client.get("/houses", headers=hdr, params={"page": 2, "page_size": 2})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["page"] == 2


# ===========================================================================
# detail with owner + tenant
# ===========================================================================

def test_detail_with_owner_and_tenant(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "rented", _owner(), _tenant())
    resp = auth.client.get(f"/houses/{hid}", headers=hdr)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["owner"]["party_type"] == "owner"
    assert body["tenant"]["party_type"] == "tenant"


# ===========================================================================
# history: newest-first + snapshot
# ===========================================================================

def test_history_newest_first_with_snapshot(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    _set_status(auth, hdr, hid, "to_let", _owner())
    resp = auth.client.get(f"/houses/{hid}/history", headers=hdr)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 2
    assert body[0]["to_status"] == "to_let"
    assert body[1]["to_status"] == "owned"
    assert body[1]["from_status"] == "empty"
    assert body[0]["snapshot"]["owner"]["email"] == "owner1@x.com"


# ===========================================================================
# display codes
# ===========================================================================

def test_display_code_building_house(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr, floors=[{"level": 2, "houses_count": 1}])
    resp = auth.client.get(f"/houses/{houses[0]['id']}", headers=hdr)
    assert resp.status_code == 200, resp.text
    assert resp.json()["house"]["display_code"] == "A-201"


def test_display_code_individual_house(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_individual_houses(auth, hdr)
    resp = auth.client.get(f"/houses/{houses[0]['id']}", headers=hdr)
    assert resp.status_code == 200, resp.text
    assert resp.json()["house"]["display_code"] == "1"


# ===========================================================================
# email normalization
# ===========================================================================

def test_email_normalization_on_create(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(
        auth, hdr, hid, "owned", _owner(email="  Owner1@X.COM  ", persons_living=1)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["owner"]["email"] == "owner1@x.com"


def test_tenant_email_optional_none(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "rented", _owner(), _tenant())
    assert resp.status_code == 200, resp.text
    assert resp.json()["tenant"]["email"] is None


# ===========================================================================
# id_proof roundtrip
# ===========================================================================

def test_id_proof_roundtrip_owner(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    did = _make_vault_doc(db, society.id)
    resp = _set_status(
        auth, hdr, hid, "owned",
        _owner(persons_living=1, id_proof_type="pan", id_proof_document_id=did),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["owner"]["id_proof_type"] == "pan"
    assert resp.json()["owner"]["id_proof_document_id"] == did


# ===========================================================================
# same-email repost updates persons_living (occupancy_updated, no new history)
# ===========================================================================

def test_same_email_repost_updates_persons_living_no_history(
    db, society, admin_user, superadmin, auth
):
    """empty->owned is a real transition (1 history row + 1 status_changed audit).
    The SECOND same-status owned->owned repost must add no further history/status_changed,
    only an occupancy_updated for the changed persons_living."""
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=2))
    resp = _set_status(auth, hdr, hid, "owned", _owner(persons_living=5))
    assert resp.status_code == 200, resp.text
    assert resp.json()["owner"]["persons_living"] == 5

    db.expire_all()
    history = db.execute(
        select(HouseStatusHistory).where(HouseStatusHistory.house_id == hid)
    ).scalars().all()
    # Exactly 1 (from the initial empty->owned transition); the repost added none.
    assert len(history) == 1
    assert len(_audit(db, "house.status_changed", society_id=society.id, entity_id=hid)) == 1
    assert len(_audit(db, "house.occupancy_updated", society_id=society.id, entity_id=hid)) == 1


def test_to_let_to_for_sale(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "to_let", _owner())
    resp = _set_status(auth, hdr, hid, "for_sale", _owner())
    assert resp.status_code == 200, resp.text
    assert resp.json()["house"]["status"] == "for_sale"


# ===========================================================================
# new-account defaults: must_change, user.created + role.assigned audits
# ===========================================================================

def test_new_owner_account_defaults_and_audits(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "owned", _owner(email="newowner@x.com", persons_living=1))
    assert resp.status_code == 200, resp.text
    user_id = resp.json()["owner"]["user_id"]

    db.expire_all()
    user = db.get(User, user_id)
    assert user.password_state == "must_change"
    assert user.is_active is True

    assert len(_audit(db, "user.created", entity_id=user_id)) == 1
    assert len(_audit(db, "role.assigned", entity_id=user_id)) >= 1


def test_individual_house_empty_to_owned(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_individual_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "owned", _owner(persons_living=2))
    assert resp.status_code == 200, resp.text
    assert resp.json()["house"]["status"] == "owned"
    assert resp.json()["house"]["building_id"] is None


# ===========================================================================
# current_owner_user_ids: returns / excludes-closed
# ===========================================================================

def test_current_owner_user_ids_returns_current_owner(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    user_id = resp.json()["owner"]["user_id"]

    from app.modules.houses.service import HouseService

    db.expire_all()
    ids = HouseService(db).current_owner_user_ids(society.id)
    assert user_id in ids


def test_current_owner_user_ids_excludes_closed_owner(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    r1 = _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    old_user_id = r1.json()["owner"]["user_id"]
    # replace owner via a different email
    _set_status(auth, hdr, hid, "owned", _owner(email="newer@x.com", persons_living=1))

    from app.modules.houses.service import HouseService

    db.expire_all()
    ids = HouseService(db).current_owner_user_ids(society.id)
    assert old_user_id not in ids


# ===========================================================================
# same-status owned different-email? no — that's owner_replaced (edge file).
# Here: rented same-status repost (both owner + tenant reconciled, no history).
# ===========================================================================

def test_rented_same_status_repost_no_history(db, society, admin_user, superadmin, auth):
    """empty->rented seeds 1 history row; the rented->rented repost adds no more."""
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "rented", _owner(), _tenant(persons_living=1))
    resp = _set_status(auth, hdr, hid, "rented", _owner(), _tenant(persons_living=3))
    assert resp.status_code == 200, resp.text
    assert resp.json()["tenant"]["persons_living"] == 3

    db.expire_all()
    history = db.execute(
        select(HouseStatusHistory).where(HouseStatusHistory.house_id == hid)
    ).scalars().all()
    assert len(history) == 1


# ===========================================================================
# Perf guard: list_houses must NOT be N+1 on the building lookup. A page of
# houses spanning many buildings issues a constant, bounded number of SELECTs
# (count + page + one batched building fetch) — never one-per-house.
# ===========================================================================

def test_list_houses_no_n_plus_one_on_buildings(db, society, admin_user, superadmin, auth):
    from sqlalchemy import event

    from app.core.db import SessionLocal
    from app.modules.houses.service import HouseService

    hdr = _setup(db, society, admin_user, superadmin, auth)
    # 4 buildings, 3 houses each = 12 houses across 4 distinct buildings.
    for name in ("A", "B", "C", "D"):
        _make_building_with_houses(
            auth, hdr, floors=[{"level": 1, "houses_count": 3}], names=[name]
        )

    # Measure on a FRESH session so the count reflects only list_houses' own
    # queries (no fixture-session identity-map state leaking in).
    measure = SessionLocal()
    counter = {"selects": 0}

    def _hook(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            counter["selects"] += 1

    event.listen(measure.bind, "before_cursor_execute", _hook)
    try:
        items, total = HouseService(measure).list_houses(
            society.id,
            status=None,
            building_id=None,
            floor_id=None,
            number=None,
            offset=0,
            limit=20,
        )
    finally:
        event.remove(measure.bind, "before_cursor_execute", _hook)
        measure.close()

    assert total == 12
    assert len(items) == 12
    # Distinct display codes prove buildings resolved correctly.
    assert len({i.display_code for i in items}) == 12
    # Exactly count + page + one batched IN(...) building fetch = 3. Constant
    # regardless of building count (verified 2/4/8/16 buildings → 3); a per-row
    # building lookup would grow with the number of distinct buildings.
    assert counter["selects"] <= 3, f"N+1 regression: {counter['selects']} SELECTs"
