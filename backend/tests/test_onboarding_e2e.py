"""Phase-3 END-TO-END + CROSS-MODULE-REGISTRY tests for the Onboarding module.

These drive whole admin journeys through the REAL HTTP stack, exercising the
Platform Foundation prerequisites for real (super-admin creates a society, enables
the onboarding module, provisions the society_admin) — nothing is mocked. Then the
onboarding wizard is driven purely over ``/onboarding/*`` with the admin's bearer
token, asserting HTTP status + response body + DB state + the ordered audit trail.

Coverage (complementary to test_onboarding_smoke / _numbering / _later_edits):
  * FULL BUILDING-TYPE E2E — cold-start foundation → type → 2 buildings → map A
    (AUTO + ground) → map B (prefill from A, continuous sequential) → override →
    preview → complete → /me flips onboarding_required; full audit sequence.
  * FULL INDIVIDUAL-TYPE E2E — type → rows (continuous sequential) → override →
    complete → society active; audit sequence.
  * REGISTRY reads (spec §7) — OnboardingService.list_houses / resolve_house as
    Finance / House-&-Occupancy will consume them (building + individual + 404).
  * POST-COMPLETION later edits, end-to-end over HTTP — add+map building, add a
    floor, rename, delete an empty house; society stays active; audit present.
  * BLOCKING WIZARD + tenant isolation — onboarding_required truthfully reflects
    each society's own state; a second society's admin sees only its own wizard.
"""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.modules.onboarding.service import OnboardingService
from app.platform.models import AuditLog, Society
from app.common.errors import NotFoundError
from app.platform.societies.schemas import ModuleAllocation
from app.platform.societies.service import SocietyService
from tests.conftest import (
    DEFAULT_MEMBER_PASSWORD,
    SUPERADMIN_EMAIL,
    SUPERADMIN_PASSWORD,
)


# ---------------------------------------------------------------------------
# Helpers — drive the FOUNDATION prerequisites over the real HTTP stack.
# ---------------------------------------------------------------------------

def _su_bearer(auth) -> dict[str, str]:
    login = auth.login_ok(SUPERADMIN_EMAIL, SUPERADMIN_PASSWORD)
    return auth.bearer(login["access_token"])


def _provision_society_with_onboarding(
    client: TestClient, auth, *, name: str, admin_email: str
) -> int:
    """Super-admin: create society + enable onboarding module + provision admin.

    Everything through ``/admin/*`` HTTP endpoints (the real foundation flow).
    Returns the new society id.
    """
    su = _su_bearer(auth)

    create = client.post(
        "/admin/societies",
        json={
            "name": name,
            "storage_limit_bytes": 5 * 1024**3,
            "default_member_password": DEFAULT_MEMBER_PASSWORD,
        },
        headers=su,
    )
    assert create.status_code == 201, create.text
    sid = create.json()["id"]

    # Enable the onboarding module → auto-grants onboarding.* to society_admin.
    mods = client.put(
        f"/admin/societies/{sid}/modules",
        json={"modules": [{"module_key": "onboarding", "enabled": True}]},
        headers=su,
    )
    assert mods.status_code == 200, mods.text
    onb = next(m for m in mods.json() if m["module_key"] == "onboarding")
    assert onb["enabled"] is True

    admin = client.post(
        f"/admin/societies/{sid}/users",
        json={"email": admin_email, "full_name": "Society Admin"},
        headers=su,
    )
    assert admin.status_code == 201, admin.text
    assert admin.json()["password_state"] == "must_change"
    return sid


def _admin_activated_bearer(auth, email: str, *, new_password: str) -> dict[str, str]:
    """First login (must_change) → change-password → re-login → usable bearer."""
    first = auth.login_ok(email, DEFAULT_MEMBER_PASSWORD)
    assert first["password_state"] == "must_change"
    cp = auth.client.post(
        "/auth/change-password",
        headers=auth.bearer(first["access_token"]),
        json={"current_password": DEFAULT_MEMBER_PASSWORD, "new_password": new_password},
    )
    assert cp.status_code == 200, cp.text
    relogin = auth.login_ok(email, new_password)
    return auth.bearer(relogin["access_token"])


def _actions_in_order(db, society_id: int) -> list[str]:
    """The onboarding audit actions for this society, in insertion (id) order."""
    rows = db.execute(
        select(AuditLog.action)
        .where(
            AuditLog.society_id == society_id,
            AuditLog.action.like("onboarding.%"),
        )
        .order_by(AuditLog.id)
    ).all()
    return [a for (a,) in rows]


# ---------------------------------------------------------------------------
# 1. FULL BUILDING-TYPE E2E
# ---------------------------------------------------------------------------

def test_full_building_type_e2e(client, auth, db):
    email = "bldg-admin@e2e.local"
    sid = _provision_society_with_onboarding(
        client, auth, name="Tower Estate", admin_email=email
    )
    hdr = _admin_activated_bearer(auth, email, new_password="AdminPass123")

    # --- /me shows the blocking wizard + onboarding permissions -------------
    me = client.get("/me", headers=hdr)
    assert me.status_code == 200, me.text
    me_body = me.json()
    assert me_body["onboarding_required"] is True
    assert me_body["active_society_id"] == sid
    assert "onboarding.manage" in me_body["permissions"]
    assert "onboarding.read" in me_body["permissions"]

    # --- step 1: select building type ---------------------------------------
    r = client.post("/onboarding/type", headers=hdr, json={"type": "building"})
    assert r.status_code == 200, r.text
    assert r.json()["type"] == "building"

    # --- step 2: create two buildings A, B ----------------------------------
    r = client.post("/onboarding/buildings", headers=hdr, json={"names": ["A", "B"]})
    assert r.status_code == 200, r.text
    buildings = r.json()
    assert [b["name"] for b in buildings] == ["A", "B"]
    a_id = buildings[0]["id"]
    b_id = buildings[1]["id"]

    # --- map A: AUTO + a ground floor + an upper floor ----------------------
    r = client.post(
        f"/onboarding/buildings/{a_id}/map",
        headers=hdr,
        json={
            "floors": [
                {"level": 0, "is_ground": True, "houses_count": 2},
                {"level": 1, "houses_count": 2},
            ],
            "numbering_config": {
                "mode": "auto",
                "count_pad": 2,
                "ground_prefix": "G",
                "display_separator": "-",
            },
        },
    )
    assert r.status_code == 200, r.text
    a_houses = r.json()
    # AUTO: ground → G01, G02 ; floor 1 → 101, 102.
    assert [h["number"] for h in a_houses] == ["G01", "G02", "101", "102"]
    assert all(h["status"] == "empty" for h in a_houses)
    # NOTE: the generate endpoint returns raw House rows, so display_code is the
    # HouseOut default "" here (it is computed only by the registry reads +
    # /preview). The correct "A-<num>" codes are asserted in the registry tests.
    assert all(h["display_code"] == "" for h in a_houses)

    # --- map B: prefill numbering from A but a DIFFERENT mode (continuous seq) -
    # A used AUTO, so B's continuous sequence must start at 1 (spec §4: continuous
    # seeds only from prior *continuous-sequential* houses, never AUTO numbers).
    r = client.post(
        f"/onboarding/buildings/{b_id}/map",
        headers=hdr,
        json={
            "floors": [{"level": 1, "houses_count": 3}],
            "numbering_config": {
                "mode": "sequential",
                "sequential_scope": "continuous",
                "display_separator": "-",
            },
        },
    )
    assert r.status_code == 200, r.text
    b_houses = r.json()
    assert [h["number"] for h in b_houses] == ["1", "2", "3"]

    # --- override one of B's numbers ----------------------------------------
    target = b_houses[0]  # number "1"
    r = client.patch(
        f"/onboarding/houses/{target['id']}", headers=hdr, json={"number": "PH1"}
    )
    assert r.status_code == 200, r.text
    ov = r.json()
    assert ov["number"] == "PH1"
    assert ov["number_overridden"] is True

    # --- preview building B (read) — /preview DOES compute display codes -----
    r = client.get(f"/onboarding/buildings/{b_id}/preview", headers=hdr)
    assert r.status_code == 200, r.text
    preview = r.json()
    assert sorted(h["number"] for h in preview) == ["2", "3", "PH1"]
    assert {h["display_code"] for h in preview} == {"B-2", "B-3", "B-PH1"}

    # --- complete → society flips active ------------------------------------
    r = client.post("/onboarding/complete", headers=hdr)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"

    db.expire_all()
    assert db.get(Society, sid).status == "active"

    # --- /me now reports onboarding_required == False -----------------------
    me2 = client.get("/me", headers=hdr).json()
    assert me2["onboarding_required"] is False

    # --- FULL audit sequence in order ---------------------------------------
    actions = _actions_in_order(db, sid)
    # type_selected → building_created x2 → (map A: floor_added x2 + houses_generated)
    # → (map B: floor_added + houses_generated) → house_number_overridden → completed
    assert actions == [
        "onboarding.type_selected",
        "onboarding.building_created",
        "onboarding.building_created",
        "onboarding.floor_added",
        "onboarding.floor_added",
        "onboarding.houses_generated",
        "onboarding.floor_added",
        "onboarding.houses_generated",
        "onboarding.house_number_overridden",
        "onboarding.completed",
    ]


# ---------------------------------------------------------------------------
# 2. FULL INDIVIDUAL-TYPE E2E
# ---------------------------------------------------------------------------

def test_full_individual_type_e2e(client, auth, db):
    email = "indiv-admin@e2e.local"
    sid = _provision_society_with_onboarding(
        client, auth, name="Villa Lane", admin_email=email
    )
    hdr = _admin_activated_bearer(auth, email, new_password="AdminPass123")

    assert client.get("/me", headers=hdr).json()["onboarding_required"] is True

    # --- select individual_houses type --------------------------------------
    r = client.post(
        "/onboarding/type", headers=hdr, json={"type": "individual_houses"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["type"] == "individual_houses"

    # --- two sequential rows → one continuous 1..5 sequence -----------------
    r = client.post(
        "/onboarding/rows",
        headers=hdr,
        json={
            "rows": [
                {
                    "display_order": 1,
                    "houses_count": 2,
                    "numbering_config": {"mode": "sequential"},
                },
                {
                    "display_order": 2,
                    "houses_count": 3,
                    "numbering_config": {"mode": "sequential"},
                },
            ]
        },
    )
    assert r.status_code == 200, r.text
    houses = r.json()
    assert [h["number"] for h in houses] == ["1", "2", "3", "4", "5"]
    assert all(h["building_id"] is None for h in houses)
    # /rows returns raw House rows → display_code is the "" default (computed only
    # by the registry reads, asserted in test_registry_reads_individual_type).
    assert all(h["display_code"] == "" for h in houses)

    # --- override one number -------------------------------------------------
    r = client.patch(
        f"/onboarding/houses/{houses[4]['id']}", headers=hdr, json={"number": "99"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["number"] == "99"
    assert r.json()["number_overridden"] is True

    # --- complete → active ---------------------------------------------------
    r = client.post("/onboarding/complete", headers=hdr)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"
    db.expire_all()
    assert db.get(Society, sid).status == "active"
    assert client.get("/me", headers=hdr).json()["onboarding_required"] is False

    # --- audit sequence: type → 2 houses_generated (one per row) → override → complete
    actions = _actions_in_order(db, sid)
    assert actions == [
        "onboarding.type_selected",
        "onboarding.houses_generated",
        "onboarding.houses_generated",
        "onboarding.house_number_overridden",
        "onboarding.completed",
    ]


# ---------------------------------------------------------------------------
# 3. CROSS-MODULE REGISTRY reads (spec §7) — the Finance / House-&-Occupancy contract
# ---------------------------------------------------------------------------

def test_registry_reads_building_type(client, auth, db):
    """After completion, other modules read houses via OnboardingService directly."""
    email = "reg-bldg@e2e.local"
    sid = _provision_society_with_onboarding(
        client, auth, name="Registry Towers", admin_email=email
    )
    hdr = _admin_activated_bearer(auth, email, new_password="AdminPass123")

    client.post("/onboarding/type", headers=hdr, json={"type": "building"})
    a_id = client.post(
        "/onboarding/buildings", headers=hdr, json={"names": ["A"]}
    ).json()[0]["id"]
    client.post(
        f"/onboarding/buildings/{a_id}/map",
        headers=hdr,
        json={
            "floors": [{"level": 1, "houses_count": 2}],
            "numbering_config": {"mode": "auto", "display_separator": "-"},
        },
    )
    client.post("/onboarding/complete", headers=hdr)

    # Registry reads via the SERVICE (what Finance / House & Occupancy consume).
    db.expire_all()
    svc = OnboardingService(db)

    houses = svc.list_houses(sid)
    assert {h["number"] for h in houses} == {"101", "102"}
    # Display codes carry the building name + separator.
    assert {h["display_code"] for h in houses} == {"A-101", "A-102"}
    assert all(h["building_id"] == a_id for h in houses)

    # resolve_house by (building_id, number) for a building-type society.
    resolved = svc.resolve_house(sid, number="101", building_id=a_id)
    assert resolved["number"] == "101"
    assert resolved["display_code"] == "A-101"
    assert resolved["building_id"] == a_id

    # A missing number → NotFoundError (the negative contract).
    try:
        svc.resolve_house(sid, number="999", building_id=a_id)
        raised = False
    except NotFoundError:
        raised = True
    assert raised, "resolve_house on a missing number must raise NotFoundError"


def test_registry_reads_individual_type(client, auth, db):
    email = "reg-indiv@e2e.local"
    sid = _provision_society_with_onboarding(
        client, auth, name="Registry Villas", admin_email=email
    )
    hdr = _admin_activated_bearer(auth, email, new_password="AdminPass123")

    client.post("/onboarding/type", headers=hdr, json={"type": "individual_houses"})
    client.post(
        "/onboarding/rows",
        headers=hdr,
        json={
            "rows": [
                {
                    "display_order": 1,
                    "houses_count": 3,
                    "numbering_config": {"mode": "sequential"},
                }
            ]
        },
    )
    client.post("/onboarding/complete", headers=hdr)

    db.expire_all()
    svc = OnboardingService(db)

    houses = svc.list_houses(sid)
    assert {h["number"] for h in houses} == {"1", "2", "3"}
    # Individual display code is the bare number; no building.
    assert all(h["display_code"] == h["number"] for h in houses)
    assert all(h["building_id"] is None for h in houses)

    # resolve_house by number alone (no building) for individual type.
    resolved = svc.resolve_house(sid, number="2")
    assert resolved["number"] == "2"
    assert resolved["display_code"] == "2"

    try:
        svc.resolve_house(sid, number="404")
        raised = False
    except NotFoundError:
        raised = True
    assert raised


# ---------------------------------------------------------------------------
# 4. POST-COMPLETION later edits — end-to-end over HTTP; society stays active
# ---------------------------------------------------------------------------

def test_post_completion_later_edits_e2e(client, auth, db):
    email = "later-edit@e2e.local"
    sid = _provision_society_with_onboarding(
        client, auth, name="Evolving Estate", admin_email=email
    )
    hdr = _admin_activated_bearer(auth, email, new_password="AdminPass123")

    client.post("/onboarding/type", headers=hdr, json={"type": "building"})
    a_id = client.post(
        "/onboarding/buildings", headers=hdr, json={"names": ["A"]}
    ).json()[0]["id"]
    client.post(
        f"/onboarding/buildings/{a_id}/map",
        headers=hdr,
        json={
            "floors": [{"level": 1, "houses_count": 2}],
            "numbering_config": {"mode": "auto", "display_separator": "-"},
        },
    )
    complete = client.post("/onboarding/complete", headers=hdr)
    assert complete.json()["status"] == "active"

    # --- later edit 1: add a NEW building + map it (post-completion) --------
    b_id = client.post(
        "/onboarding/buildings", headers=hdr, json={"names": ["B"]}
    ).json()[0]["id"]
    r = client.post(
        f"/onboarding/buildings/{b_id}/map",
        headers=hdr,
        json={
            "floors": [{"level": 1, "houses_count": 2}],
            "numbering_config": {"mode": "auto", "display_separator": "-"},
        },
    )
    assert r.status_code == 200, r.text
    assert [h["number"] for h in r.json()] == ["101", "102"]

    # --- later edit 2: add a floor to existing building A -------------------
    r = client.post(
        f"/onboarding/buildings/{a_id}/floors",
        headers=hdr,
        json={"floors": [{"level": 2, "houses_count": 2}]},
    )
    assert r.status_code == 200, r.text
    # New floor uses its own level prefix → 201, 202 (no clash with 101/102).
    assert sorted(h["number"] for h in r.json()) == ["201", "202"]

    # --- later edit 3: rename building A ------------------------------------
    r = client.patch(
        f"/onboarding/buildings/{a_id}", headers=hdr, json={"name": "A-Renamed"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "A-Renamed"

    # --- later edit 4: delete an empty house (from B) -----------------------
    b_house = client.get(
        f"/onboarding/buildings/{b_id}/preview", headers=hdr
    ).json()[0]
    r = client.delete(f"/onboarding/houses/{b_house['id']}", headers=hdr)
    assert r.status_code == 204, r.text

    # Society STILL active throughout the later edits.
    db.expire_all()
    assert db.get(Society, sid).status == "active"

    # Deleted house is gone; the other B house remains.
    remaining = client.get(
        f"/onboarding/buildings/{b_id}/preview", headers=hdr
    ).json()
    assert b_house["id"] not in {h["id"] for h in remaining}
    assert len(remaining) == 1

    # --- audit rows for the later edits are present -------------------------
    actions = set(_actions_in_order(db, sid))
    assert "onboarding.building_renamed" in actions
    assert "onboarding.house_deleted" in actions
    # completed appears once (later edits do not re-complete).
    assert _actions_in_order(db, sid).count("onboarding.completed") == 1


# ---------------------------------------------------------------------------
# 5. BLOCKING WIZARD flag + cross-tenant isolation
# ---------------------------------------------------------------------------

def test_blocking_wizard_and_cross_tenant_isolation(client, auth, db):
    """Each society's admin sees only its OWN onboarding state.

    Society 1 completes onboarding; Society 2 is still mid-wizard. The two admins'
    ``/me`` blocking-wizard flags and ``GET /onboarding/state`` reflect only their
    own tenant — a light cross-tenant sanity check.
    """
    email1 = "tenant1-admin@e2e.local"
    sid1 = _provision_society_with_onboarding(
        client, auth, name="Tenant One", admin_email=email1
    )
    hdr1 = _admin_activated_bearer(auth, email1, new_password="AdminPass123")

    email2 = "tenant2-admin@e2e.local"
    sid2 = _provision_society_with_onboarding(
        client, auth, name="Tenant Two", admin_email=email2
    )
    hdr2 = _admin_activated_bearer(auth, email2, new_password="AdminPass123")

    assert sid1 != sid2

    # Both start blocked.
    assert client.get("/me", headers=hdr1).json()["onboarding_required"] is True
    assert client.get("/me", headers=hdr2).json()["onboarding_required"] is True

    # Tenant 1 drives + completes an individual-type onboarding.
    client.post("/onboarding/type", headers=hdr1, json={"type": "individual_houses"})
    client.post(
        "/onboarding/rows",
        headers=hdr1,
        json={
            "rows": [
                {
                    "display_order": 1,
                    "houses_count": 1,
                    "numbering_config": {"mode": "sequential"},
                }
            ]
        },
    )
    # Tenant 2 only selects a type (still mid-wizard, no houses yet).
    client.post("/onboarding/type", headers=hdr2, json={"type": "building"})

    client.post("/onboarding/complete", headers=hdr1)

    # --- isolation: tenant 1 done, tenant 2 STILL blocked -------------------
    assert client.get("/me", headers=hdr1).json()["onboarding_required"] is False
    assert client.get("/me", headers=hdr2).json()["onboarding_required"] is True

    # GET /onboarding/state reflects each tenant's own society + type only.
    state1 = client.get("/onboarding/state", headers=hdr1)
    assert state1.status_code == 200, state1.text
    s1 = state1.json()
    assert s1["society_id"] == sid1
    assert s1["type"] == "individual_houses"
    assert s1["status"] == "active"
    assert s1["next_action"] == "done"

    state2 = client.get("/onboarding/state", headers=hdr2)
    assert state2.status_code == 200, state2.text
    s2 = state2.json()
    assert s2["society_id"] == sid2
    assert s2["type"] == "building"
    assert s2["status"] == "onboarding"
    # Tenant 2 has a type but no buildings yet → next step is create_buildings.
    assert s2["next_action"] == "create_buildings"

    # Tenant 2 cannot complete (no houses) — 422, and stays blocked.
    cannot = client.post("/onboarding/complete", headers=hdr2)
    assert cannot.status_code == 422, cannot.text
    db.expire_all()
    assert db.get(Society, sid2).status == "onboarding"

    # Audit isolation: tenant 1's completed row is scoped to sid1, not sid2.
    assert "onboarding.completed" in _actions_in_order(db, sid1)
    assert "onboarding.completed" not in _actions_in_order(db, sid2)


# ---------------------------------------------------------------------------
# 6. Second-society admin uses SERVICE registry only for its own tenant
#    (belt-and-braces isolation for the cross-module contract).
# ---------------------------------------------------------------------------

def test_registry_is_tenant_scoped(db, society, superadmin):
    """list_houses / resolve_house never leak another society's houses."""
    # Society A (the fixture) — building type, one house.
    svc = OnboardingService(db)
    svc.select_type(society.id, "building", actor_user_id=superadmin.id)
    db.flush()

    # A second, independent society with its own house sharing the same number.
    other = SocietyService(db).create_society(
        _other_society_create(), actor_user_id=superadmin.id
    )
    db.flush()
    SocietyService(db).set_modules(
        other.id,
        [ModuleAllocation(module_key="onboarding", enabled=True, config={})],
        actor_user_id=superadmin.id,
    )
    db.flush()
    svc.select_type(other.id, "individual_houses", actor_user_id=superadmin.id)
    db.flush()

    from app.modules.onboarding.schemas import RowsCreateRequest

    svc.create_rows(
        other.id,
        RowsCreateRequest.model_validate(
            {
                "rows": [
                    {
                        "display_order": 1,
                        "houses_count": 2,
                        "numbering_config": {"mode": "sequential"},
                    }
                ]
            }
        ),
        actor_user_id=superadmin.id,
    )
    db.flush()

    # Society A has no houses; the other society's "1","2" must NOT be visible to A.
    assert svc.list_houses(society.id) == []
    try:
        svc.resolve_house(society.id, number="1")
        leaked = True
    except NotFoundError:
        leaked = False
    assert not leaked, "resolve_house leaked another tenant's house"

    # The other tenant resolves its own house fine.
    assert svc.resolve_house(other.id, number="1")["number"] == "1"


def _other_society_create():
    from app.platform.societies.schemas import SocietyCreate

    return SocietyCreate(
        name="Second Society",
        storage_limit_bytes=5 * 1024**3,
        default_member_password=DEFAULT_MEMBER_PASSWORD,
    )
