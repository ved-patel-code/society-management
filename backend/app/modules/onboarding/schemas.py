"""Pydantic request/response contracts for onboarding (docs/modules/onboarding.md §6).

Shapes + field validation only; business rules live in the service (docs/03 §2).
These are the FROZEN interface the wave sub-agents build against — extend
additively, do not repurpose existing fields.

The wizard's ``current_step`` state machine (spec §3 leaves it narrative — pinned
here so resume logic is deterministic):

    type_selection  → structure_mapping → review → completed

- ``type_selection``   : no type chosen yet (fresh society).
- ``structure_mapping``: type chosen; admin is defining buildings/floors or rows
  and generating houses (the bulk of the wizard, resumable via ``draft``).
- ``review``           : all structure generated; admin reviews before completing.
- ``completed``        : ``POST /onboarding/complete`` flipped the society active.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# --- Domains (enforced in the service, not the DB) -------------------------
SOCIETY_TYPES: frozenset[str] = frozenset({"building", "individual_houses"})
BUILDING_MODES: frozenset[str] = frozenset({"auto", "sequential", "manual"})
INDIVIDUAL_MODES: frozenset[str] = frozenset({"sequential", "custom", "manual"})
SEQUENTIAL_SCOPES: frozenset[str] = frozenset({"per_building", "continuous"})
ONBOARDING_STEPS: frozenset[str] = frozenset(
    {"type_selection", "structure_mapping", "review", "completed"}
)


# --- Type selection --------------------------------------------------------

class TypeSelectRequest(BaseModel):
    """Body for ``POST /onboarding/type``."""

    type: str = Field(description="building | individual_houses")


# --- Numbering config (embedded in building/row map requests) --------------

class BuildingNumberingConfig(BaseModel):
    """Numbering config for a building (spec §3/§4)."""

    mode: str = Field(description="auto | sequential | manual")
    count_pad: int = Field(default=2, ge=0, le=6)
    ground_prefix: str = Field(default="G", min_length=1, max_length=8)
    has_ground: bool = False
    sequential_scope: str = Field(default="per_building")
    display_separator: str = Field(default="-", max_length=4)


class FloorInput(BaseModel):
    """One floor's inputs when mapping a building."""

    level: int = Field(ge=0)
    is_ground: bool = False
    label: str | None = Field(default=None, max_length=64)
    # Optional per-floor override; when None the building's
    # ``default_houses_per_floor`` applies (spec §3). Both missing → 422 in service.
    houses_count: int | None = Field(default=None, ge=0)
    # MANUAL mode only: exact numbers the admin typed, in floor order.
    manual_numbers: list[str] = Field(default_factory=list)


class RowNumberingConfig(BaseModel):
    """Numbering config for an individual-type row (spec §3/§4)."""

    mode: str = Field(description="sequential | custom | manual")
    prefix: str = Field(default="", max_length=16)
    pad: int = Field(default=0, ge=0, le=6)


# --- Building flow requests ------------------------------------------------

class BuildingsCreateRequest(BaseModel):
    """Body for ``POST /onboarding/buildings`` — admin types each building name."""

    names: list[str] = Field(min_length=1)


class BuildingMapRequest(BaseModel):
    """Body for ``POST /onboarding/buildings/{id}/map`` — floors + numbering → generate."""

    floors: list[FloorInput] = Field(min_length=1)
    numbering_config: BuildingNumberingConfig
    # Building-level default houses-per-floor; each floor's own ``houses_count``
    # overrides it. A floor with neither set → ValidationError (spec §3).
    default_houses_per_floor: int | None = Field(default=None, ge=0)


class BuildingAddFloorsRequest(BaseModel):
    """Body for ``POST /onboarding/buildings/{id}/floors`` — add floors to a mapped building.

    Reuses the building's STORED ``numbering_config`` (mode is not re-specified).
    """

    floors: list[FloorInput] = Field(min_length=1)
    default_houses_per_floor: int | None = Field(default=None, ge=0)


# --- Individual flow requests ----------------------------------------------

class RowInput(BaseModel):
    """One row's inputs when mapping an individual-type society."""

    display_order: int = Field(ge=1)
    label: str | None = Field(default=None, max_length=64)
    houses_count: int = Field(ge=0)
    numbering_config: RowNumberingConfig
    manual_numbers: list[str] = Field(default_factory=list)


class RowsCreateRequest(BaseModel):
    """Body for ``POST /onboarding/rows`` — rows + houses/row + numbering → generate."""

    rows: list[RowInput] = Field(min_length=1)


# --- Overrides / later edits ----------------------------------------------

class HouseNumberOverride(BaseModel):
    """Body for ``PATCH /onboarding/houses/{id}`` — override a generated number."""

    number: str = Field(min_length=1, max_length=32)


class BuildingRenameRequest(BaseModel):
    """Body for ``PATCH /onboarding/buildings/{id}`` — rename (later edit)."""

    name: str = Field(min_length=1, max_length=128)


class DraftSaveRequest(BaseModel):
    """Body for ``PUT /onboarding/draft`` — persist in-progress inputs for resume."""

    draft: dict = Field(default_factory=dict)


# --- Responses -------------------------------------------------------------

class HouseOut(BaseModel):
    """A house row + its derived display code (registry read shape)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    society_id: int
    building_id: int | None
    floor_id: int | None
    row_id: int | None
    position_in_row: int | None
    number: str
    numbering_mode: str
    number_overridden: bool
    status: str
    display_code: str = ""


class FloorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    building_id: int
    level: int
    is_ground: bool
    label: str | None
    houses_count: int | None


class BuildingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    society_id: int
    name: str
    display_order: int
    numbering_config: dict


class RowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    society_id: int
    display_order: int
    label: str | None
    houses_count: int
    numbering_config: dict


class OnboardingStateOut(BaseModel):
    """Resume payload for ``GET /onboarding/state`` (spec §6)."""

    society_id: int
    type: str | None
    status: str
    current_step: str
    current_building_index: int | None
    draft: dict | None
    numbering_defaults: dict | None
    buildings: list[BuildingOut] = Field(default_factory=list)
    rows: list[RowOut] = Field(default_factory=list)
    next_action: str | None = None
