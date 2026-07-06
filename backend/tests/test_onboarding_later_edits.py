"""Phase-2 fix coverage for the Onboarding module (later edits + numbering).

Exercises the four CONFIRMED code-review fixes directly at the service layer
(the `db` fixture is a real session on the app engine, so audit rows and houses
are asserted by querying back):

  FIX 1 — continuous sequential seeds only from prior continuous-sequential
          houses (AUTO/custom/manual numbers never pollute the running sequence).
  FIX 2 — add-building / map-building work post-completion (society 'active').
  FIX 3 — POST-style add_floors adds floors to an already-mapped building.
  FIX 4 — building-level default_houses_per_floor with per-floor override.
"""
from __future__ import annotations

import pytest

from app.common.errors import ValidationError
from app.modules.onboarding.schemas import (
    BuildingAddFloorsRequest,
    BuildingMapRequest,
    BuildingsCreateRequest,
    RowsCreateRequest,
)
from app.modules.onboarding.service import OnboardingService
from app.platform.models import AuditLog, Society


def _svc(db):
    return OnboardingService(db)


def _map_req(*, mode, floors, sequential_scope="per_building", default=None,
             count_pad=2, ground_prefix="G"):
    return BuildingMapRequest.model_validate({
        "floors": floors,
        "numbering_config": {
            "mode": mode,
            "count_pad": count_pad,
            "ground_prefix": ground_prefix,
            "sequential_scope": sequential_scope,
        },
        "default_houses_per_floor": default,
    })


def _setup_building_society(db, society, superadmin):
    svc = _svc(db)
    svc.select_type(society.id, "building", actor_user_id=superadmin.id)
    db.flush()
    return svc


# ---------------------------------------------------------------------------
# FIX 1 — continuous sequential must not count AUTO / custom / manual numbers
# ---------------------------------------------------------------------------

def test_continuous_building_ignores_auto_numbers(db, society, superadmin):
    """AUTO tower A (yields '1001') then a continuous tower B → B starts at 1."""
    svc = _setup_building_society(db, society, superadmin)
    [a, b] = svc.create_buildings(
        society.id, BuildingsCreateRequest(names=["A", "B"]),
        actor_user_id=superadmin.id,
    )
    db.flush()

    # AUTO on level 10 → number "1001".
    svc.map_building(
        society.id, a.id,
        _map_req(mode="auto", floors=[{"level": 10, "houses_count": 1}]),
        actor_user_id=superadmin.id,
    )
    db.flush()

    houses_b = svc.map_building(
        society.id, b.id,
        _map_req(mode="sequential", sequential_scope="continuous",
                 floors=[{"level": 1, "houses_count": 3}]),
        actor_user_id=superadmin.id,
    )
    db.flush()
    # Must start at 1, NOT 1002.
    assert [h.number for h in houses_b] == ["1", "2", "3"]


def test_continuous_building_carries_across_sequential_towers(db, society, superadmin):
    """Two continuous sequential towers thread one running 1,2,3… sequence."""
    svc = _setup_building_society(db, society, superadmin)
    [a, b] = svc.create_buildings(
        society.id, BuildingsCreateRequest(names=["A", "B"]),
        actor_user_id=superadmin.id,
    )
    db.flush()
    svc.map_building(
        society.id, a.id,
        _map_req(mode="sequential", sequential_scope="continuous",
                 floors=[{"level": 1, "houses_count": 2}]),
        actor_user_id=superadmin.id,
    )
    db.flush()
    houses_b = svc.map_building(
        society.id, b.id,
        _map_req(mode="sequential", sequential_scope="continuous",
                 floors=[{"level": 1, "houses_count": 2}]),
        actor_user_id=superadmin.id,
    )
    db.flush()
    assert [h.number for h in houses_b] == ["3", "4"]


def test_continuous_individual_ignores_custom_numbers(db, society, superadmin):
    """A custom row producing '1','2' then a sequential row → sequential starts at 1.

    The batch clash-check catches the real collision ('1','2') as a 422, not a 500.
    """
    svc = _svc(db)
    svc.select_type(society.id, "individual_houses", actor_user_id=superadmin.id)
    db.flush()

    with pytest.raises(ValidationError) as exc:
        svc.create_rows(
            society.id,
            RowsCreateRequest.model_validate({
                "rows": [
                    {"display_order": 1, "houses_count": 2,
                     "numbering_config": {"mode": "custom", "prefix": "", "pad": 0}},
                    {"display_order": 2, "houses_count": 2,
                     "numbering_config": {"mode": "sequential"}},
                ]
            }),
            actor_user_id=superadmin.id,
        )
    # sequential row restarted from baseline 0 → 1,2 which clashes with custom 1,2.
    assert "clashes" in (exc.value.details or {})
    assert set(exc.value.details["clashes"]) == {"1", "2"}


def test_continuous_individual_no_clash_when_custom_distinct(db, society, superadmin):
    """Custom row with a distinct prefix ('A1','A2') → sequential still starts at 1."""
    svc = _svc(db)
    svc.select_type(society.id, "individual_houses", actor_user_id=superadmin.id)
    db.flush()
    houses = svc.create_rows(
        society.id,
        RowsCreateRequest.model_validate({
            "rows": [
                {"display_order": 1, "houses_count": 2,
                 "numbering_config": {"mode": "custom", "prefix": "A", "pad": 0}},
                {"display_order": 2, "houses_count": 2,
                 "numbering_config": {"mode": "sequential"}},
            ]
        }),
        actor_user_id=superadmin.id,
    )
    db.flush()
    numbers = [h.number for h in houses]
    assert numbers == ["A1", "A2", "1", "2"]


# ---------------------------------------------------------------------------
# FIX 2 — post-completion later edits (add building + map) succeed
# ---------------------------------------------------------------------------

def test_add_and_map_building_after_completion(db, society, superadmin):
    svc = _setup_building_society(db, society, superadmin)
    [a] = svc.create_buildings(
        society.id, BuildingsCreateRequest(names=["A"]), actor_user_id=superadmin.id
    )
    db.flush()
    svc.map_building(
        society.id, a.id,
        _map_req(mode="auto", floors=[{"level": 1, "houses_count": 1}]),
        actor_user_id=superadmin.id,
    )
    db.flush()
    svc.complete(society.id, actor_user_id=superadmin.id)
    db.flush()
    assert db.get(Society, society.id).status == "active"

    # Later edit: add + map a new building post-completion.
    [b] = svc.create_buildings(
        society.id, BuildingsCreateRequest(names=["B"]), actor_user_id=superadmin.id
    )
    db.flush()
    houses = svc.map_building(
        society.id, b.id,
        _map_req(mode="auto", floors=[{"level": 1, "houses_count": 2}]),
        actor_user_id=superadmin.id,
    )
    db.flush()
    assert len(houses) == 2
    # Society stays active.
    assert db.get(Society, society.id).status == "active"


# ---------------------------------------------------------------------------
# FIX 3 — add floors to an already-mapped building
# ---------------------------------------------------------------------------

def test_add_floors_to_mapped_building(db, society, superadmin):
    svc = _setup_building_society(db, society, superadmin)
    [a] = svc.create_buildings(
        society.id, BuildingsCreateRequest(names=["A"]), actor_user_id=superadmin.id
    )
    db.flush()
    svc.map_building(
        society.id, a.id,
        _map_req(mode="auto", floors=[{"level": 1, "houses_count": 2}]),
        actor_user_id=superadmin.id,
    )
    db.flush()
    svc.complete(society.id, actor_user_id=superadmin.id)
    db.flush()

    new_houses = svc.add_floors(
        society.id, a.id,
        BuildingAddFloorsRequest.model_validate(
            {"floors": [{"level": 2, "houses_count": 2}]}
        ),
        actor_user_id=superadmin.id,
    )
    db.flush()
    # New floor's AUTO numbers use its own level prefix ("201","202") — no clash.
    assert sorted(h.number for h in new_houses) == ["201", "202"]

    # floor_added audit row present for the new floor.
    added = db.query(AuditLog).filter(
        AuditLog.society_id == society.id,
        AuditLog.action == "onboarding.floor_added",
        AuditLog.entity_type == "floor",
    ).all()
    # 1 from initial map + 1 from add_floors.
    assert len(added) == 2
    gen = db.query(AuditLog).filter(
        AuditLog.society_id == society.id,
        AuditLog.action == "onboarding.houses_generated",
    ).all()
    assert len(gen) == 2


def test_add_floors_wrong_society_is_404(db, society, superadmin):
    """A building_id that isn't the caller's society → NotFoundError (404)."""
    from app.common.errors import NotFoundError

    svc = _setup_building_society(db, society, superadmin)
    with pytest.raises(NotFoundError):
        svc.add_floors(
            society.id, 999999,
            BuildingAddFloorsRequest.model_validate(
                {"floors": [{"level": 2, "houses_count": 1}]}
            ),
            actor_user_id=superadmin.id,
        )


def test_add_floors_continuous_carries_sequence(db, society, superadmin):
    svc = _setup_building_society(db, society, superadmin)
    [a] = svc.create_buildings(
        society.id, BuildingsCreateRequest(names=["A"]), actor_user_id=superadmin.id
    )
    db.flush()
    svc.map_building(
        society.id, a.id,
        _map_req(mode="sequential", sequential_scope="continuous",
                 floors=[{"level": 1, "houses_count": 2}]),
        actor_user_id=superadmin.id,
    )
    db.flush()
    added = svc.add_floors(
        society.id, a.id,
        BuildingAddFloorsRequest.model_validate(
            {"floors": [{"level": 2, "houses_count": 2}]}
        ),
        actor_user_id=superadmin.id,
    )
    db.flush()
    # Continuous seed carried on from prior sequential houses (1,2 → 3,4).
    assert [h.number for h in added] == ["3", "4"]


# ---------------------------------------------------------------------------
# FIX 4 — building default_houses_per_floor + per-floor override
# ---------------------------------------------------------------------------

def test_default_houses_per_floor_applies(db, society, superadmin):
    svc = _setup_building_society(db, society, superadmin)
    [a] = svc.create_buildings(
        society.id, BuildingsCreateRequest(names=["A"]), actor_user_id=superadmin.id
    )
    db.flush()
    # Floors omit houses_count → building default (3) applies to both.
    houses = svc.map_building(
        society.id, a.id,
        _map_req(mode="auto", default=3,
                 floors=[{"level": 1}, {"level": 2}]),
        actor_user_id=superadmin.id,
    )
    db.flush()
    assert len(houses) == 6


def test_per_floor_override_wins(db, society, superadmin):
    svc = _setup_building_society(db, society, superadmin)
    [a] = svc.create_buildings(
        society.id, BuildingsCreateRequest(names=["A"]), actor_user_id=superadmin.id
    )
    db.flush()
    # Default 3, but level 2 overrides to 1.
    houses = svc.map_building(
        society.id, a.id,
        _map_req(mode="auto", default=3,
                 floors=[{"level": 1}, {"level": 2, "houses_count": 1}]),
        actor_user_id=superadmin.id,
    )
    db.flush()
    per_level = {}
    for h in houses:
        # AUTO prefix is the level, so count by leading char group.
        per_level.setdefault(h.number[0], 0)
        per_level[h.number[0]] += 1
    assert per_level["1"] == 3   # level 1 uses default
    assert per_level["2"] == 1   # level 2 override


def test_both_counts_missing_is_422(db, society, superadmin):
    svc = _setup_building_society(db, society, superadmin)
    [a] = svc.create_buildings(
        society.id, BuildingsCreateRequest(names=["A"]), actor_user_id=superadmin.id
    )
    db.flush()
    with pytest.raises(ValidationError):
        svc.map_building(
            society.id, a.id,
            _map_req(mode="auto", default=None, floors=[{"level": 1}]),
            actor_user_id=superadmin.id,
        )
