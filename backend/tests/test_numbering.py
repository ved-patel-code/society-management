"""Unit tests for the pure numbering engine (docs/modules/onboarding.md §4).

The engine is DB-free and deterministic, so it is exercised in isolation here —
the highest-risk logic (every mode, ground floor, padding, per-floor overrides,
continuous-across-towers sequencing, custom prefixes, manual count-mismatch
errors, and the clash helper) is pinned without any HTTP/DB scaffolding.
"""
from __future__ import annotations

import pytest

from app.modules.onboarding import numbering as N


def _nums(generated) -> list[str]:
    return [g.number for g in generated]


# --- AUTO (building) -------------------------------------------------------

def test_auto_upper_floors_zeropad_default():
    floors = [
        N.FloorSpec(level=1, is_ground=False, houses_count=3),
        N.FloorSpec(level=2, is_ground=False, houses_count=2),
    ]
    got = N.generate_building_numbers(floors, mode="auto")
    assert _nums(got) == ["101", "102", "103", "201", "202"]


def test_auto_restarts_count_per_floor():
    floors = [N.FloorSpec(level=1, is_ground=False, houses_count=2)]
    got = N.generate_building_numbers(floors, mode="auto")
    assert _nums(got) == ["101", "102"]


def test_auto_ground_floor_prefix_and_level_ten():
    floors = [
        N.FloorSpec(level=N.GROUND_LEVEL, is_ground=True, houses_count=2),
        N.FloorSpec(level=10, is_ground=False, houses_count=1),
    ]
    got = N.generate_building_numbers(floors, mode="auto")
    # Ground → G01.. ; floor 10 → 1001 (pad applies to the running position).
    assert _nums(got) == ["G01", "G02", "1001"]


def test_auto_custom_ground_prefix_and_pad():
    floors = [N.FloorSpec(level=N.GROUND_LEVEL, is_ground=True, houses_count=1)]
    got = N.generate_building_numbers(
        floors, mode="auto", count_pad=3, ground_prefix="LG"
    )
    assert _nums(got) == ["LG001"]


def test_auto_per_floor_house_count_override():
    # Each floor carries its own houses_count (per-floor override, docs §3/§4).
    floors = [
        N.FloorSpec(level=1, is_ground=False, houses_count=1),
        N.FloorSpec(level=2, is_ground=False, houses_count=4),
    ]
    got = N.generate_building_numbers(floors, mode="auto")
    assert _nums(got) == ["101", "201", "202", "203", "204"]


# --- SEQUENTIAL (building) -------------------------------------------------

def test_sequential_per_building_resets_at_one():
    floors = [
        N.FloorSpec(level=1, is_ground=False, houses_count=2),
        N.FloorSpec(level=2, is_ground=False, houses_count=2),
    ]
    got = N.generate_building_numbers(
        floors, mode="sequential", sequential_scope="per_building", start_at=99
    )
    # per_building ignores start_at and runs 1,2,3,4 across floors.
    assert _nums(got) == ["1", "2", "3", "4"]


def test_sequential_continuous_uses_start_at():
    floors = [N.FloorSpec(level=1, is_ground=False, houses_count=3)]
    got = N.generate_building_numbers(
        floors, mode="sequential", sequential_scope="continuous", start_at=10
    )
    assert _nums(got) == ["10", "11", "12"]


def test_sequential_continuous_across_towers():
    # Tower A seeds tower B's start via next_sequential_start (docs §4).
    tower_a = N.generate_building_numbers(
        [N.FloorSpec(level=1, is_ground=False, houses_count=2)],
        mode="sequential",
        sequential_scope="continuous",
        start_at=1,
    )
    next_start = N.next_sequential_start(tower_a, 1)
    tower_b = N.generate_building_numbers(
        [N.FloorSpec(level=1, is_ground=False, houses_count=2)],
        mode="sequential",
        sequential_scope="continuous",
        start_at=next_start,
    )
    assert _nums(tower_a) == ["1", "2"]
    assert next_start == 3
    assert _nums(tower_b) == ["3", "4"]


# --- MANUAL (building) -----------------------------------------------------

def test_manual_echoes_typed_numbers_and_strips():
    floors = [
        N.FloorSpec(
            level=1, is_ground=False, houses_count=2,
            manual_numbers=[" A1 ", "A2"],
        )
    ]
    got = N.generate_building_numbers(floors, mode="manual")
    assert _nums(got) == ["A1", "A2"]


def test_manual_count_mismatch_raises():
    floors = [
        N.FloorSpec(
            level=1, is_ground=False, houses_count=2, manual_numbers=["A1"]
        )
    ]
    with pytest.raises(N.NumberingError):
        N.generate_building_numbers(floors, mode="manual")


def test_manual_empty_number_raises():
    floors = [
        N.FloorSpec(
            level=1, is_ground=False, houses_count=1, manual_numbers=["  "]
        )
    ]
    with pytest.raises(N.NumberingError):
        N.generate_building_numbers(floors, mode="manual")


# --- Validation guards -----------------------------------------------------

def test_unknown_building_mode_raises():
    with pytest.raises(N.NumberingError):
        N.generate_building_numbers([], mode="nope")


def test_unknown_sequential_scope_raises():
    with pytest.raises(N.NumberingError):
        N.generate_building_numbers([], mode="sequential", sequential_scope="galaxy")


def test_negative_floor_count_raises():
    floors = [N.FloorSpec(level=1, is_ground=False, houses_count=-1)]
    with pytest.raises(N.NumberingError):
        N.generate_building_numbers(floors, mode="auto")


# --- Individual: SEQUENTIAL ------------------------------------------------

def test_individual_sequential_uses_start_at():
    row = N.RowSpec(houses_count=3)
    got = N.generate_row_numbers(row, mode="sequential", start_at=5)
    assert _nums(got) == ["5", "6", "7"]
    assert [g.position_in_row for g in got] == [1, 2, 3]


def test_individual_sequential_continuous_across_rows():
    # Service threads a running counter; simulate two rows here.
    row1 = N.generate_row_numbers(
        N.RowSpec(houses_count=2), mode="sequential", start_at=1
    )
    row2 = N.generate_row_numbers(
        N.RowSpec(houses_count=2), mode="sequential", start_at=1 + len(row1)
    )
    assert _nums(row1) == ["1", "2"]
    assert _nums(row2) == ["3", "4"]


# --- Individual: CUSTOM ----------------------------------------------------

def test_individual_custom_alpha_prefix_no_pad():
    row = N.RowSpec(houses_count=3, prefix="alpha")
    got = N.generate_row_numbers(row, mode="custom")
    assert _nums(got) == ["alpha1", "alpha2", "alpha3"]


def test_individual_custom_numeric_prefix_restarts_per_row():
    # '10' prefix + count 1..3 → 101,102,103 (docs §4 individual custom).
    row = N.RowSpec(houses_count=3, prefix="10")
    got = N.generate_row_numbers(row, mode="custom")
    assert _nums(got) == ["101", "102", "103"]


def test_individual_custom_with_pad():
    row = N.RowSpec(houses_count=2, prefix="H", pad=3)
    got = N.generate_row_numbers(row, mode="custom")
    assert _nums(got) == ["H001", "H002"]


# --- Individual: MANUAL ----------------------------------------------------

def test_individual_manual_echoes():
    row = N.RowSpec(houses_count=2, manual_numbers=["X1", " X2 "])
    got = N.generate_row_numbers(row, mode="manual")
    assert _nums(got) == ["X1", "X2"]


def test_individual_manual_count_mismatch_raises():
    row = N.RowSpec(houses_count=2, manual_numbers=["X1"])
    with pytest.raises(N.NumberingError):
        N.generate_row_numbers(row, mode="manual")


def test_individual_unknown_mode_raises():
    with pytest.raises(N.NumberingError):
        N.generate_row_numbers(N.RowSpec(houses_count=1), mode="auto")


# --- Display codes ---------------------------------------------------------

def test_building_display_code_default_separator():
    assert N.building_display_code("A", "201") == "A-201"


def test_building_display_code_custom_separator():
    assert N.building_display_code("Tower1", "5", separator="/") == "Tower1/5"


def test_individual_display_code_is_bare_number():
    assert N.individual_display_code("42") == "42"


# --- Clash helper ----------------------------------------------------------

def test_find_duplicate_numbers_reports_dupes_once_order_stable():
    assert N.find_duplicate_numbers(["1", "2", "2", "3", "1", "1"]) == ["2", "1"]


def test_find_duplicate_numbers_empty_when_unique():
    assert N.find_duplicate_numbers(["1", "2", "3"]) == []
