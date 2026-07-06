"""Phase-3 HAPPY-PATH e2e tests for the Onboarding module (through the HTTP stack).

Every test drives the real routes with an authenticated ``society_admin`` bearer
(module enabled → onboarding.* auto-granted), asserting HTTP status + response
body + re-queried DB state + audit rows. These complement the pure-engine unit
tests (test_numbering.py) and the service-layer fix tests (test_onboarding_later_edits.py)
by pinning the FULL wizard flow: all three building modes, both sequential scopes,
manual numbering, per-floor overrides + building default, individual sequential/
custom/manual, display codes, prefill-repeat, resume, preview, add-floors, and
completion unblocking /me.
"""
from __future__ import annotations

from app.modules.onboarding.models import Building, Floor, House, OnboardingProgress, Row
from app.platform.models import AuditLog, Society
from app.platform.societies.schemas import ModuleAllocation
from app.platform.societies.service import SocietyService
from tests.conftest import DEFAULT_MEMBER_PASSWORD

MODULE_KEY = "onboarding"


# --- shared driver: enable module + authenticated admin bearer -------------

def _enable(db, society, superadmin):
    SocietyService(db).set_modules(
        society.id,
        [ModuleAllocation(module_key=MODULE_KEY, enabled=True, config={})],
        actor_user_id=superadmin.id,
    )
    db.commit()


def _admin_header(auth, admin_user):
    """Run the must-change dance and return a usable bearer header for onboarding."""
    tokens = auth.login_ok(admin_user.email, DEFAULT_MEMBER_PASSWORD)
    resp = auth.client.post(
        "/auth/change-password",
        headers=auth.bearer(tokens["access_token"]),
        json={"current_password": DEFAULT_MEMBER_PASSWORD, "new_password": "NewPass123"},
    )
    assert resp.status_code == 200, resp.text
    sess = auth.login_ok(admin_user.email, "NewPass123")
    return auth.bearer(sess["access_token"])


def _setup(db, society, admin_user, superadmin, auth):
    _enable(db, society, superadmin)
    return _admin_header(auth, admin_user)


def _select_type(auth, hdr, type_):
    r = auth.client.post("/onboarding/type", headers=hdr, json={"type": type_})
    assert r.status_code == 200, r.text
    return r


def _create_building(auth, hdr, names):
    r = auth.client.post("/onboarding/buildings", headers=hdr, json={"names": names})
    assert r.status_code == 200, r.text
    return r.json()


def _map(auth, hdr, building_id, body):
    return auth.client.post(
        f"/onboarding/buildings/{building_id}/map", headers=hdr, json=body
    )


def _audit_actions(db, society_id, action):
    return (
        db.query(AuditLog)
        .filter(AuditLog.society_id == society_id, AuditLog.action == action)
        .all()
    )


# ===========================================================================
# BUILDING — AUTO mode
# ===========================================================================

def test_building_auto_ground_upper_pad_and_floor_ten(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _select_type(auth, hdr, "building")
    [b] = _create_building(auth, hdr, ["A"])

    resp = _map(
        auth, hdr, b["id"],
        {
            "floors": [
                {"level": 0, "is_ground": True, "houses_count": 2},
                {"level": 2, "houses_count": 2},
                {"level": 10, "houses_count": 1},
            ],
            "numbering_config": {"mode": "auto"},
        },
    )
    assert resp.status_code == 200, resp.text
    houses = resp.json()
    numbers = [h["number"] for h in houses]
    # Ground G01/G02, floor 2 → 201/202 (pad 2), floor 10 → 1001.
    assert numbers == ["G01", "G02", "201", "202", "1001"]
    assert all(h["status"] == "empty" for h in houses)
    assert all(h["numbering_mode"] == "auto" for h in houses)
    assert all(h["number_overridden"] is False for h in houses)

    # DB state: 5 houses persisted for this building, all status empty.
    db.expire_all()
    db_houses = db.query(House).filter(House.building_id == b["id"]).all()
    assert sorted(h.number for h in db_houses) == ["1001", "201", "202", "G01", "G02"]
    assert {h.status for h in db_houses} == {"empty"}
    # 3 floors persisted, ground stored at level 0.
    floors = db.query(Floor).filter(Floor.building_id == b["id"]).all()
    assert sorted(f.level for f in floors) == [0, 2, 10]
    assert sum(1 for f in floors if f.is_ground) == 1

    # Audit: one houses_generated + one floor_added per floor.
    assert len(_audit_actions(db, society.id, "onboarding.houses_generated")) == 1
    assert len(_audit_actions(db, society.id, "onboarding.floor_added")) == 3

    # Display codes are exposed via the preview read (building name + "-" separator).
    preview = auth.client.get(
        f"/onboarding/buildings/{b['id']}/preview", headers=hdr
    ).json()
    codes = {h["number"]: h["display_code"] for h in preview}
    assert codes["201"] == "A-201"
    assert codes["G01"] == "A-G01"


# ===========================================================================
# BUILDING — SEQUENTIAL per_building vs continuous
# ===========================================================================

def test_building_sequential_per_building_resets_per_tower(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _select_type(auth, hdr, "building")
    [a, b] = _create_building(auth, hdr, ["A", "B"])

    cfg = {"mode": "sequential", "sequential_scope": "per_building"}
    floors = {"floors": [{"level": 1, "houses_count": 3}], "numbering_config": cfg}

    ra = _map(auth, hdr, a["id"], floors)
    rb = _map(auth, hdr, b["id"], floors)
    assert ra.status_code == 200 and rb.status_code == 200, (ra.text, rb.text)
    # Both towers restart at 1.
    assert [h["number"] for h in ra.json()] == ["1", "2", "3"]
    assert [h["number"] for h in rb.json()] == ["1", "2", "3"]

    db.expire_all()
    assert (
        db.query(House).filter(House.building_id == a["id"]).count() == 3
    )
    assert (
        db.query(House).filter(House.building_id == b["id"]).count() == 3
    )


def test_building_sequential_continuous_across_two_towers(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _select_type(auth, hdr, "building")
    [a, b] = _create_building(auth, hdr, ["A", "B"])

    cfg = {"mode": "sequential", "sequential_scope": "continuous"}
    ra = _map(
        auth, hdr, a["id"],
        {"floors": [{"level": 1, "houses_count": 2}], "numbering_config": cfg},
    )
    rb = _map(
        auth, hdr, b["id"],
        {"floors": [{"level": 1, "houses_count": 3}], "numbering_config": cfg},
    )
    assert ra.status_code == 200 and rb.status_code == 200, (ra.text, rb.text)
    # One running sequence spans both towers.
    assert [h["number"] for h in ra.json()] == ["1", "2"]
    assert [h["number"] for h in rb.json()] == ["3", "4", "5"]


# ===========================================================================
# BUILDING — MANUAL mode
# ===========================================================================

def test_building_manual_admin_typed_numbers_per_floor(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _select_type(auth, hdr, "building")
    [b] = _create_building(auth, hdr, ["A"])

    resp = _map(
        auth, hdr, b["id"],
        {
            "floors": [
                {"level": 1, "houses_count": 2, "manual_numbers": ["P1", "P2"]},
                {"level": 2, "houses_count": 1, "manual_numbers": ["Q9"]},
            ],
            "numbering_config": {"mode": "manual"},
        },
    )
    assert resp.status_code == 200, resp.text
    numbers = [h["number"] for h in resp.json()]
    assert numbers == ["P1", "P2", "Q9"]
    assert all(h["numbering_mode"] == "manual" for h in resp.json())

    db.expire_all()
    db_nums = sorted(
        h.number for h in db.query(House).filter(House.building_id == b["id"]).all()
    )
    assert db_nums == ["P1", "P2", "Q9"]


# ===========================================================================
# BUILDING — per-floor override + building default (mixed)
# ===========================================================================

def test_building_mixed_default_and_per_floor_override(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _select_type(auth, hdr, "building")
    [b] = _create_building(auth, hdr, ["A"])

    resp = _map(
        auth, hdr, b["id"],
        {
            # default 3; level 1 uses default, level 2 overrides to 1.
            "floors": [
                {"level": 1},
                {"level": 2, "houses_count": 1},
            ],
            "numbering_config": {"mode": "auto"},
            "default_houses_per_floor": 3,
        },
    )
    assert resp.status_code == 200, resp.text
    numbers = [h["number"] for h in resp.json()]
    assert numbers == ["101", "102", "103", "201"]

    db.expire_all()
    floors = {
        f.level: f.houses_count
        for f in db.query(Floor).filter(Floor.building_id == b["id"]).all()
    }
    assert floors[1] == 3  # building default applied
    assert floors[2] == 1  # per-floor override stored


# ===========================================================================
# INDIVIDUAL — SEQUENTIAL / CUSTOM / MANUAL
# ===========================================================================

def test_individual_sequential_continuous_across_rows(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _select_type(auth, hdr, "individual_houses")

    resp = auth.client.post(
        "/onboarding/rows",
        headers=hdr,
        json={
            "rows": [
                {"display_order": 1, "houses_count": 2,
                 "numbering_config": {"mode": "sequential"}},
                {"display_order": 2, "houses_count": 1,
                 "numbering_config": {"mode": "sequential"}},
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    houses = resp.json()
    assert [h["number"] for h in houses] == ["1", "2", "3"]
    assert all(h["building_id"] is None and h["row_id"] is not None for h in houses)

    db.expire_all()
    assert db.query(Row).filter(Row.society_id == society.id).count() == 2
    assert db.query(House).filter(House.society_id == society.id).count() == 3


def test_individual_custom_prefix_count_from_one_per_row(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _select_type(auth, hdr, "individual_houses")

    resp = auth.client.post(
        "/onboarding/rows",
        headers=hdr,
        json={
            "rows": [
                {"display_order": 1, "houses_count": 2,
                 "numbering_config": {"mode": "custom", "prefix": "A"}},
                {"display_order": 2, "houses_count": 3,
                 "numbering_config": {"mode": "custom", "prefix": "10"}},
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    # 'A' → A1,A2 ; '10' restarts count-from-1 → 101,102,103.
    assert [h["number"] for h in resp.json()] == ["A1", "A2", "101", "102", "103"]
    # CUSTOM houses stored with numbering_mode 'manual' (house domain has no 'custom').
    assert all(h["numbering_mode"] == "manual" for h in resp.json())


def test_individual_manual_admin_typed(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _select_type(auth, hdr, "individual_houses")

    resp = auth.client.post(
        "/onboarding/rows",
        headers=hdr,
        json={
            "rows": [
                {"display_order": 1, "houses_count": 2,
                 "numbering_config": {"mode": "manual"},
                 "manual_numbers": ["Villa-1", "Villa-2"]},
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    assert [h["number"] for h in resp.json()] == ["Villa-1", "Villa-2"]

    db.expire_all()
    nums = sorted(
        h.number for h in db.query(House).filter(House.society_id == society.id).all()
    )
    assert nums == ["Villa-1", "Villa-2"]

    # Individual display code is the bare number (via the cross-module registry read).
    from app.modules.onboarding.service import OnboardingService

    registry = {h["number"]: h["display_code"] for h in
                OnboardingService(db).list_houses(society.id)}
    assert registry == {"Villa-1": "Villa-1", "Villa-2": "Villa-2"}


# ===========================================================================
# PREVIEW
# ===========================================================================

def test_preview_building_returns_numbers_with_display_codes(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _select_type(auth, hdr, "building")
    [b] = _create_building(auth, hdr, ["A"])
    _map(
        auth, hdr, b["id"],
        {
            "floors": [{"level": 0, "is_ground": True, "houses_count": 1},
                       {"level": 2, "houses_count": 1}],
            "numbering_config": {"mode": "auto"},
        },
    )

    resp = auth.client.get(f"/onboarding/buildings/{b['id']}/preview", headers=hdr)
    assert resp.status_code == 200, resp.text
    houses = resp.json()
    codes = {h["number"]: h["display_code"] for h in houses}
    assert codes == {"G01": "A-G01", "201": "A-201"}


# ===========================================================================
# PREFILL-REPEAT — numbering_defaults exposed for the next building
# ===========================================================================

def test_prefill_repeat_exposes_numbering_defaults(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _select_type(auth, hdr, "building")
    [a, _b2] = _create_building(auth, hdr, ["A", "B"])

    _map(
        auth, hdr, a["id"],
        {
            "floors": [{"level": 1, "houses_count": 2}],
            "numbering_config": {"mode": "auto", "count_pad": 3, "ground_prefix": "LG"},
        },
    )

    state = auth.client.get("/onboarding/state", headers=hdr)
    assert state.status_code == 200, state.text
    defaults = state.json()["numbering_defaults"]
    assert defaults is not None
    # The just-used config is prefilled for building 2.
    assert defaults["mode"] == "auto"
    assert defaults["count_pad"] == 3
    assert defaults["ground_prefix"] == "LG"


# ===========================================================================
# RESUME — PUT /draft then GET /state
# ===========================================================================

def test_resume_draft_roundtrips_step_and_next_action(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _select_type(auth, hdr, "building")
    [a] = _create_building(auth, hdr, ["A"])

    draft = {
        "current_step": "structure_mapping",
        "current_building_index": 1,
        "wip": {"building_id": a["id"], "floors": [{"level": 1, "houses_count": 4}]},
    }
    put = auth.client.put("/onboarding/draft", headers=hdr, json={"draft": draft})
    assert put.status_code == 200, put.text
    assert put.json()["status"] == "saved"

    state = auth.client.get("/onboarding/state", headers=hdr).json()
    assert state["draft"] == draft
    assert state["current_step"] == "structure_mapping"
    assert state["current_building_index"] == 1
    # A building exists but is unmapped → next action is to map a building.
    assert state["next_action"] == "map_building"
    assert state["type"] == "building"

    db.expire_all()
    progress = (
        db.query(OnboardingProgress)
        .filter(OnboardingProgress.society_id == society.id)
        .one()
    )
    assert progress.draft == draft
    assert progress.current_step == "structure_mapping"


# ===========================================================================
# ADD-FLOORS to a mapped building
# ===========================================================================

def test_add_floors_generates_without_clash_and_audits(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _select_type(auth, hdr, "building")
    [b] = _create_building(auth, hdr, ["A"])
    _map(
        auth, hdr, b["id"],
        {
            "floors": [{"level": 1, "houses_count": 2}],
            "numbering_config": {"mode": "auto"},
        },
    )

    resp = auth.client.post(
        f"/onboarding/buildings/{b['id']}/floors",
        headers=hdr,
        json={"floors": [{"level": 3, "houses_count": 2}]},
    )
    assert resp.status_code == 200, resp.text
    # New floor uses its own level prefix → 301,302, no clash with 101,102.
    assert sorted(h["number"] for h in resp.json()) == ["301", "302"]

    db.expire_all()
    all_nums = sorted(
        h.number for h in db.query(House).filter(House.building_id == b["id"]).all()
    )
    assert all_nums == ["101", "102", "301", "302"]

    # Audit: 2 floor_added (1 map + 1 add), 2 houses_generated (1 map + 1 add).
    assert len(_audit_actions(db, society.id, "onboarding.floor_added")) == 2
    assert len(_audit_actions(db, society.id, "onboarding.houses_generated")) == 2


# ===========================================================================
# COMPLETE — flips society active and clears /me onboarding_required
# ===========================================================================

def test_complete_flips_active_and_me_onboarding_required_false(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _select_type(auth, hdr, "building")
    [b] = _create_building(auth, hdr, ["A"])
    _map(
        auth, hdr, b["id"],
        {
            "floors": [{"level": 1, "houses_count": 2}],
            "numbering_config": {"mode": "auto"},
        },
    )

    # Before completion /me reports the blocking wizard.
    me_before = auth.client.get("/me", headers=hdr).json()
    assert me_before["onboarding_required"] is True

    resp = auth.client.post("/onboarding/complete", headers=hdr)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"

    db.expire_all()
    assert db.get(Society, society.id).status == "active"
    progress = (
        db.query(OnboardingProgress)
        .filter(OnboardingProgress.society_id == society.id)
        .one()
    )
    assert progress.current_step == "completed"
    assert len(_audit_actions(db, society.id, "onboarding.completed")) == 1

    # /me no longer requires onboarding.
    me_after = auth.client.get("/me", headers=hdr).json()
    assert me_after["onboarding_required"] is False

    # GET /state reflects the completed/done terminal state.
    state = auth.client.get("/onboarding/state", headers=hdr).json()
    assert state["status"] == "active"
    assert state["current_step"] == "completed"
    assert state["next_action"] == "done"
