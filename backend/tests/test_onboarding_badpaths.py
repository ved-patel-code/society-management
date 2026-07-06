"""Phase-3 BAD-PATH / EDGE-CASE / EXCEPTION coverage for the Onboarding module.

Drives every rejection path through the REAL HTTP stack (TestClient) and asserts:
  * the HTTP status code,
  * the ``{code, message, details}`` error body (offending numbers where the spec
    promises them), and
  * that NO partial DB writes leaked — for clash/rollback scenarios the house
    COUNT is asserted unchanged before/after (one-transaction-per-batch rollback),
    since ``get_session`` only commits on a clean 2xx return.

Complements the happy-path suites (test_onboarding_smoke / test_numbering /
test_onboarding_later_edits) — no scenario is duplicated here.
"""
from __future__ import annotations

import pytest

from app.modules.onboarding.models import Building, Floor, House, Row
from app.platform.models import Society, User
from app.platform.societies.schemas import ModuleAllocation, SocietyCreate
from app.platform.societies.service import SocietyService
from app.platform.users.provisioning import UserProvisioningService
from tests.conftest import DEFAULT_MEMBER_PASSWORD


# ---------------------------------------------------------------------------
# Auth / driver helpers
# ---------------------------------------------------------------------------

def _enable_onboarding(db, society, superadmin) -> None:
    SocietyService(db).set_modules(
        society.id,
        [ModuleAllocation(module_key="onboarding", enabled=True, config={})],
        actor_user_id=superadmin.id,
    )
    db.commit()


def _admin_headers(auth, admin_user) -> dict[str, str]:
    """Log the must_change admin in, rotate the password, return a usable bearer."""
    tokens = auth.login_ok(admin_user.email, DEFAULT_MEMBER_PASSWORD)
    resp = auth.client.post(
        "/auth/change-password",
        headers=auth.bearer(tokens["access_token"]),
        json={"current_password": DEFAULT_MEMBER_PASSWORD, "new_password": "NewPass123"},
    )
    assert resp.status_code == 200, resp.text
    sess = auth.login_ok(admin_user.email, "NewPass123")
    return auth.bearer(sess["access_token"])


@pytest.fixture
def hdr(db, society, admin_user, superadmin, auth):
    """Enabled module + an authenticated society_admin bearer for /onboarding/*."""
    _enable_onboarding(db, society, superadmin)
    return _admin_headers(auth, admin_user)


def _select_type(auth, hdr, type_value: str) -> None:
    resp = auth.client.post("/onboarding/type", headers=hdr, json={"type": type_value})
    assert resp.status_code == 200, resp.text


def _house_count(db, society_id: int) -> int:
    db.expire_all()
    return db.query(House).filter(House.society_id == society_id).count()


def _bcfg(**over) -> dict:
    cfg = {
        "mode": "auto",
        "count_pad": 2,
        "ground_prefix": "G",
        "sequential_scope": "per_building",
    }
    cfg.update(over)
    return cfg


# ---------------------------------------------------------------------------
# Cross-tenant fixture: a SECOND society + its own building/floor/house so
# cross-tenant deletes/overrides can be checked (must 404, never touch tenant A).
# ---------------------------------------------------------------------------

@pytest.fixture
def other_society(db, superadmin):
    soc = SocietyService(db).create_society(
        SocietyCreate(
            name="Other Society",
            storage_limit_bytes=5 * 1024**3,
            default_member_password=DEFAULT_MEMBER_PASSWORD,
        ),
        actor_user_id=superadmin.id,
    )
    db.commit()
    db.refresh(soc)
    return soc


@pytest.fixture
def other_house(db, other_society, superadmin):
    """A building/floor/house owned by ``other_society`` (foreign to the caller)."""
    soc = db.get(Society, other_society.id)
    soc.type = "building"
    b = Building(society_id=soc.id, name="OA", display_order=1, numbering_config=_bcfg())
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
    return {"society": soc, "building": b, "floor": f, "house": h}


# ===========================================================================
# Type selection — invalid value + wrong-type structure creation
# ===========================================================================

def test_type_invalid_value_is_422(auth, hdr):
    resp = auth.client.post("/onboarding/type", headers=hdr, json={"type": "castle"})
    assert resp.status_code == 422
    assert resp.json()["details"].get("field") == "type"


def test_create_rows_before_type_is_422(auth, hdr, db, society):
    """Rows require an individual_houses type; with no type chosen → 422, no writes."""
    resp = auth.client.post(
        "/onboarding/rows",
        headers=hdr,
        json={"rows": [{
            "display_order": 1, "houses_count": 2,
            "numbering_config": {"mode": "sequential"},
        }]},
    )
    assert resp.status_code == 422
    assert _house_count(db, society.id) == 0
    db.expire_all()
    assert db.query(Row).filter(Row.society_id == society.id).count() == 0


def test_create_rows_on_building_type_is_422(auth, hdr, db, society):
    _select_type(auth, hdr, "building")
    resp = auth.client.post(
        "/onboarding/rows",
        headers=hdr,
        json={"rows": [{
            "display_order": 1, "houses_count": 2,
            "numbering_config": {"mode": "sequential"},
        }]},
    )
    assert resp.status_code == 422
    assert resp.json()["details"].get("type") == "building"
    assert db.query(Row).filter(Row.society_id == society.id).count() == 0


def test_create_buildings_on_individual_type_is_422(auth, hdr, db, society):
    _select_type(auth, hdr, "individual_houses")
    resp = auth.client.post(
        "/onboarding/buildings", headers=hdr, json={"names": ["A"]}
    )
    assert resp.status_code == 422
    assert resp.json()["details"].get("type") == "individual_houses"
    db.expire_all()
    assert db.query(Building).filter(Building.society_id == society.id).count() == 0


def test_create_buildings_before_type_is_422(auth, hdr, db, society):
    resp = auth.client.post(
        "/onboarding/buildings", headers=hdr, json={"names": ["A"]}
    )
    assert resp.status_code == 422
    assert db.query(Building).filter(Building.society_id == society.id).count() == 0


def test_empty_building_name_is_422(auth, hdr, db, society):
    _select_type(auth, hdr, "building")
    resp = auth.client.post(
        "/onboarding/buildings", headers=hdr, json={"names": ["   "]}
    )
    assert resp.status_code == 422
    assert db.query(Building).filter(Building.society_id == society.id).count() == 0


# ===========================================================================
# Duplicate building names — in-request + against existing → 409
# ===========================================================================

def test_duplicate_building_names_in_request_is_409(auth, hdr, db, society):
    _select_type(auth, hdr, "building")
    resp = auth.client.post(
        "/onboarding/buildings", headers=hdr, json={"names": ["A", "A"]}
    )
    assert resp.status_code == 409
    assert resp.json()["details"].get("name") == "A"
    # Whole batch rolled back — neither "A" persisted.
    db.expire_all()
    assert db.query(Building).filter(Building.society_id == society.id).count() == 0


def test_duplicate_building_name_against_existing_is_409(auth, hdr, db, society):
    _select_type(auth, hdr, "building")
    ok = auth.client.post("/onboarding/buildings", headers=hdr, json={"names": ["A"]})
    assert ok.status_code == 200, ok.text
    resp = auth.client.post("/onboarding/buildings", headers=hdr, json={"names": ["A"]})
    assert resp.status_code == 409
    assert resp.json()["details"].get("name") == "A"
    db.expire_all()
    assert db.query(Building).filter(Building.society_id == society.id).count() == 1


# ===========================================================================
# Number clashes on MANUAL map — 422 + offenders + NO houses persisted
# ===========================================================================

def _one_building(auth, hdr) -> int:
    resp = auth.client.post("/onboarding/buildings", headers=hdr, json={"names": ["A"]})
    assert resp.status_code == 200, resp.text
    return resp.json()[0]["id"]


def test_manual_map_duplicate_numbers_across_floors_is_422_no_writes(
    auth, hdr, db, society
):
    _select_type(auth, hdr, "building")
    bid = _one_building(auth, hdr)
    before = _house_count(db, society.id)
    resp = auth.client.post(
        f"/onboarding/buildings/{bid}/map",
        headers=hdr,
        json={
            "numbering_config": _bcfg(mode="manual"),
            "floors": [
                {"level": 1, "houses_count": 1, "manual_numbers": ["X1"]},
                {"level": 2, "houses_count": 1, "manual_numbers": ["X1"]},
            ],
        },
    )
    assert resp.status_code == 422, resp.text
    assert "X1" in resp.json()["details"]["clashes"]
    # Whole batch rolled back: no houses AND no floors persisted.
    assert _house_count(db, society.id) == before
    db.expire_all()
    assert db.query(Floor).filter(Floor.society_id == society.id).count() == 0


def test_individual_rows_duplicate_numbers_is_422_no_writes(auth, hdr, db, society):
    _select_type(auth, hdr, "individual_houses")
    before = _house_count(db, society.id)
    resp = auth.client.post(
        "/onboarding/rows",
        headers=hdr,
        json={"rows": [
            {"display_order": 1, "houses_count": 2,
             "numbering_config": {"mode": "manual"}, "manual_numbers": ["7", "7"]},
        ]},
    )
    assert resp.status_code == 422, resp.text
    assert "7" in resp.json()["details"]["clashes"]
    assert _house_count(db, society.id) == before
    db.expire_all()
    assert db.query(Row).filter(Row.society_id == society.id).count() == 0


def test_individual_rows_clash_across_rows_is_422_no_writes(auth, hdr, db, society):
    """Two custom rows with the same prefix collide → 422, entire batch rolled back."""
    _select_type(auth, hdr, "individual_houses")
    before = _house_count(db, society.id)
    resp = auth.client.post(
        "/onboarding/rows",
        headers=hdr,
        json={"rows": [
            {"display_order": 1, "houses_count": 2,
             "numbering_config": {"mode": "custom", "prefix": "H", "pad": 0}},
            {"display_order": 2, "houses_count": 2,
             "numbering_config": {"mode": "custom", "prefix": "H", "pad": 0}},
        ]},
    )
    assert resp.status_code == 422, resp.text
    assert set(resp.json()["details"]["clashes"]) == {"H1", "H2"}
    assert _house_count(db, society.id) == before
    db.expire_all()
    assert db.query(Row).filter(Row.society_id == society.id).count() == 0


# ===========================================================================
# Floor validation — two grounds, dup upper, missing counts
# ===========================================================================

def test_two_ground_floors_is_422(auth, hdr, db, society):
    _select_type(auth, hdr, "building")
    bid = _one_building(auth, hdr)
    resp = auth.client.post(
        f"/onboarding/buildings/{bid}/map",
        headers=hdr,
        json={
            "numbering_config": _bcfg(),
            "floors": [
                {"level": 0, "is_ground": True, "houses_count": 1},
                {"level": 0, "is_ground": True, "houses_count": 1},
            ],
        },
    )
    assert resp.status_code == 422
    assert _house_count(db, society.id) == 0


def test_duplicate_upper_level_is_422(auth, hdr, db, society):
    _select_type(auth, hdr, "building")
    bid = _one_building(auth, hdr)
    resp = auth.client.post(
        f"/onboarding/buildings/{bid}/map",
        headers=hdr,
        json={
            "numbering_config": _bcfg(),
            "floors": [
                {"level": 2, "houses_count": 1},
                {"level": 2, "houses_count": 1},
            ],
        },
    )
    assert resp.status_code == 422
    assert resp.json()["details"].get("level") == 2
    assert _house_count(db, society.id) == 0


def test_floor_missing_count_and_default_is_422(auth, hdr, db, society):
    _select_type(auth, hdr, "building")
    bid = _one_building(auth, hdr)
    resp = auth.client.post(
        f"/onboarding/buildings/{bid}/map",
        headers=hdr,
        json={"numbering_config": _bcfg(), "floors": [{"level": 1}]},
    )
    assert resp.status_code == 422
    assert resp.json()["details"].get("field") == "houses_count"
    assert _house_count(db, society.id) == 0


def test_ground_floor_wrong_level_is_422(auth, hdr, db, society):
    _select_type(auth, hdr, "building")
    bid = _one_building(auth, hdr)
    resp = auth.client.post(
        f"/onboarding/buildings/{bid}/map",
        headers=hdr,
        json={
            "numbering_config": _bcfg(),
            "floors": [{"level": 3, "is_ground": True, "houses_count": 1}],
        },
    )
    assert resp.status_code == 422
    assert _house_count(db, society.id) == 0


# ===========================================================================
# MANUAL count mismatch — manual_numbers length != houses_count → 422
# ===========================================================================

def test_manual_count_mismatch_is_422_no_writes(auth, hdr, db, society):
    _select_type(auth, hdr, "building")
    bid = _one_building(auth, hdr)
    before = _house_count(db, society.id)
    resp = auth.client.post(
        f"/onboarding/buildings/{bid}/map",
        headers=hdr,
        json={
            "numbering_config": _bcfg(mode="manual"),
            # houses_count=3 but only 2 numbers typed.
            "floors": [{"level": 1, "houses_count": 3, "manual_numbers": ["A", "B"]}],
        },
    )
    assert resp.status_code == 422, resp.text
    assert _house_count(db, society.id) == before
    db.expire_all()
    assert db.query(Floor).filter(Floor.society_id == society.id).count() == 0


def test_individual_manual_count_mismatch_is_422(auth, hdr, db, society):
    _select_type(auth, hdr, "individual_houses")
    resp = auth.client.post(
        "/onboarding/rows",
        headers=hdr,
        json={"rows": [
            {"display_order": 1, "houses_count": 3,
             "numbering_config": {"mode": "manual"}, "manual_numbers": ["1", "2"]},
        ]},
    )
    assert resp.status_code == 422, resp.text
    assert _house_count(db, society.id) == 0


# ===========================================================================
# Override edge cases
# ===========================================================================

def _mapped_building_houses(auth, hdr) -> list[dict]:
    """building type → one building mapped AUTO (101,102) → returns the houses JSON."""
    _select_type(auth, hdr, "building")
    bid = _one_building(auth, hdr)
    resp = auth.client.post(
        f"/onboarding/buildings/{bid}/map",
        headers=hdr,
        json={"numbering_config": _bcfg(), "floors": [{"level": 1, "houses_count": 2}]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_override_to_existing_number_in_same_building_is_422(auth, hdr, db, society):
    houses = _mapped_building_houses(auth, hdr)  # 101, 102
    target = houses[0]  # 101
    resp = auth.client.patch(
        f"/onboarding/houses/{target['id']}", headers=hdr, json={"number": "102"}
    )
    assert resp.status_code == 422
    assert resp.json()["details"]["clashes"] == ["102"]
    # Unchanged in the DB.
    db.expire_all()
    assert db.get(House, target["id"]).number == "101"


def test_override_to_whitespace_number_is_422(auth, hdr, db):
    houses = _mapped_building_houses(auth, hdr)
    target = houses[0]
    resp = auth.client.patch(
        f"/onboarding/houses/{target['id']}", headers=hdr, json={"number": "   "}
    )
    assert resp.status_code == 422
    assert resp.json()["details"].get("field") == "number"
    db.expire_all()
    assert db.get(House, target["id"]).number == "101"


def test_override_to_own_current_number_no_false_clash(auth, hdr, db):
    """Overriding to the house's OWN current number is not a clash (excluded)."""
    houses = _mapped_building_houses(auth, hdr)
    target = houses[0]  # 101
    resp = auth.client.patch(
        f"/onboarding/houses/{target['id']}", headers=hdr, json={"number": "101"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["number"] == "101"
    assert body["number_overridden"] is True


def test_override_house_of_another_society_is_404(auth, hdr, db, other_house):
    """The caller (society A) cannot override society B's house → 404, untouched."""
    foreign_id = other_house["house"].id
    resp = auth.client.patch(
        f"/onboarding/houses/{foreign_id}", headers=hdr, json={"number": "999"}
    )
    assert resp.status_code == 404
    db.expire_all()
    assert db.get(House, foreign_id).number == "101"


def test_override_nonexistent_house_is_404(auth, hdr):
    resp = auth.client.patch(
        "/onboarding/houses/999999", headers=hdr, json={"number": "5"}
    )
    assert resp.status_code == 404


# ===========================================================================
# Re-map guard — mapping an already-mapped building again → 409
# ===========================================================================

def test_remap_already_mapped_building_is_409(auth, hdr, db, society):
    _select_type(auth, hdr, "building")
    bid = _one_building(auth, hdr)
    ok = auth.client.post(
        f"/onboarding/buildings/{bid}/map",
        headers=hdr,
        json={"numbering_config": _bcfg(), "floors": [{"level": 1, "houses_count": 2}]},
    )
    assert ok.status_code == 200, ok.text
    before = _house_count(db, society.id)
    resp = auth.client.post(
        f"/onboarding/buildings/{bid}/map",
        headers=hdr,
        json={"numbering_config": _bcfg(), "floors": [{"level": 2, "houses_count": 2}]},
    )
    assert resp.status_code == 409
    assert resp.json()["details"].get("building_id") == bid
    # No second batch written.
    assert _house_count(db, society.id) == before


def test_map_nonexistent_building_is_404(auth, hdr):
    _select_type(auth, hdr, "building")
    resp = auth.client.post(
        "/onboarding/buildings/999999/map",
        headers=hdr,
        json={"numbering_config": _bcfg(), "floors": [{"level": 1, "houses_count": 1}]},
    )
    assert resp.status_code == 404


def test_preview_nonexistent_building_is_404(auth, hdr):
    _select_type(auth, hdr, "building")
    resp = auth.client.get("/onboarding/buildings/999999/preview", headers=hdr)
    assert resp.status_code == 404


# ===========================================================================
# Delete guards — status != empty blocked; empty deletes with cascade + audit;
# cross-tenant 404
# ===========================================================================

def _set_house_status(db, house_id: int, status: str) -> None:
    h = db.get(House, house_id)
    h.status = status
    db.commit()


def test_delete_house_blocked_when_not_empty_is_409(auth, hdr, db):
    houses = _mapped_building_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_house_status(db, hid, "owned")
    resp = auth.client.delete(f"/onboarding/houses/{hid}", headers=hdr)
    assert resp.status_code == 409
    assert resp.json()["details"].get("status") == "owned"
    db.expire_all()
    assert db.get(House, hid) is not None


def test_delete_floor_blocked_when_house_not_empty_is_409(auth, hdr, db):
    houses = _mapped_building_houses(auth, hdr)
    fid = houses[0]["floor_id"]
    _set_house_status(db, houses[0]["id"], "rented")
    resp = auth.client.delete(f"/onboarding/floors/{fid}", headers=hdr)
    assert resp.status_code == 409
    db.expire_all()
    assert db.get(Floor, fid) is not None


def test_delete_building_blocked_when_house_not_empty_is_409(auth, hdr, db):
    houses = _mapped_building_houses(auth, hdr)
    bid = houses[0]["building_id"]
    _set_house_status(db, houses[0]["id"], "for_sale")
    resp = auth.client.delete(f"/onboarding/buildings/{bid}", headers=hdr)
    assert resp.status_code == 409
    db.expire_all()
    assert db.get(Building, bid) is not None


def test_delete_empty_house_succeeds_204_with_audit(auth, hdr, db, society):
    from app.platform.models import AuditLog

    houses = _mapped_building_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = auth.client.delete(f"/onboarding/houses/{hid}", headers=hdr)
    assert resp.status_code == 204
    db.expire_all()
    assert db.get(House, hid) is None
    audit = db.query(AuditLog).filter(
        AuditLog.society_id == society.id,
        AuditLog.action == "onboarding.house_deleted",
        AuditLog.entity_id == hid,
    ).all()
    assert len(audit) == 1


def test_delete_empty_building_cascades_and_audits(auth, hdr, db, society):
    from app.platform.models import AuditLog

    houses = _mapped_building_houses(auth, hdr)
    bid = houses[0]["building_id"]
    resp = auth.client.delete(f"/onboarding/buildings/{bid}", headers=hdr)
    assert resp.status_code == 204
    db.expire_all()
    # Cascade: building, its floors, and its houses all gone.
    assert db.get(Building, bid) is None
    assert db.query(Floor).filter(Floor.building_id == bid).count() == 0
    assert db.query(House).filter(House.building_id == bid).count() == 0
    audit = db.query(AuditLog).filter(
        AuditLog.society_id == society.id,
        AuditLog.action == "onboarding.building_deleted",
    ).all()
    assert len(audit) == 1


def test_delete_building_of_another_society_is_404(auth, hdr, db, other_house):
    bid = other_house["building"].id
    resp = auth.client.delete(f"/onboarding/buildings/{bid}", headers=hdr)
    assert resp.status_code == 404
    db.expire_all()
    assert db.get(Building, bid) is not None  # untouched


def test_delete_floor_of_another_society_is_404(auth, hdr, db, other_house):
    fid = other_house["floor"].id
    resp = auth.client.delete(f"/onboarding/floors/{fid}", headers=hdr)
    assert resp.status_code == 404
    db.expire_all()
    assert db.get(Floor, fid) is not None


def test_delete_nonexistent_house_is_404(auth, hdr):
    resp = auth.client.delete("/onboarding/houses/999999", headers=hdr)
    assert resp.status_code == 404


# ===========================================================================
# Complete validation — before houses → 422; twice → 409
# ===========================================================================

def test_complete_before_any_houses_is_422(auth, hdr, db, society):
    _select_type(auth, hdr, "building")
    resp = auth.client.post("/onboarding/complete", headers=hdr)
    assert resp.status_code == 422
    assert resp.json()["details"].get("missing") == "houses"
    db.expire_all()
    assert db.get(Society, society.id).status == "onboarding"


def test_complete_before_type_is_422(auth, hdr, db, society):
    resp = auth.client.post("/onboarding/complete", headers=hdr)
    assert resp.status_code == 422
    assert resp.json()["details"].get("missing") == "type"


def test_complete_twice_is_409(auth, hdr, db, society):
    _mapped_building_houses(auth, hdr)
    first = auth.client.post("/onboarding/complete", headers=hdr)
    assert first.status_code == 200, first.text
    db.expire_all()
    assert db.get(Society, society.id).status == "active"
    second = auth.client.post("/onboarding/complete", headers=hdr)
    assert second.status_code == 409
    assert db.get(Society, society.id).status == "active"
