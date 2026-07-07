"""Pydantic request/response contracts for House & Occupancy (docs §6).

Shapes + field validation only; business rules (transition legality, required
fields per target status, owner-identity) live in the service (docs/03 §2). These
are the FROZEN interface the wave sub-agents build against — extend additively,
do not repurpose existing fields.
"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.common.validators import normalize_email

# --- Domains (enforced in the service, not the DB) -------------------------
HOUSE_STATUSES: frozenset[str] = frozenset(
    {"empty", "owned", "rented", "to_let", "for_sale"}
)
NON_EMPTY_STATUSES: frozenset[str] = frozenset(
    {"owned", "rented", "to_let", "for_sale"}
)
PARTY_TYPES: frozenset[str] = frozenset({"owner", "tenant"})


# --- Occupancy payloads (embedded in status-change / edit requests) --------

class OwnerPayload(BaseModel):
    """Owner details captured on a status change (docs §4).

    ``email`` is required (it is the login identity + the owner-identity key).
    ``persons_living`` is required only for ``owned`` — validated in the service
    against the target status, not here.
    """

    full_name: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=1, max_length=320)
    contact_number: str = Field(min_length=1, max_length=32)
    persons_living: int | None = Field(default=None, ge=0)
    id_proof_type: str | None = Field(default=None, max_length=255)
    id_proof_document_id: int | None = Field(default=None)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        return normalize_email(v)


class TenantPayload(BaseModel):
    """Tenant details captured when entering ``rented`` (docs §4).

    ``email`` is optional (no tenant login in v1). ``persons_living`` required for
    a tenant — validated in the service.
    """

    full_name: str = Field(min_length=1, max_length=255)
    email: str | None = Field(default=None, max_length=320)
    contact_number: str = Field(min_length=1, max_length=32)
    persons_living: int | None = Field(default=None, ge=0)
    id_proof_type: str | None = Field(default=None, max_length=255)
    id_proof_document_id: int | None = Field(default=None)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, v: str | None) -> str | None:
        return normalize_email(v) if v else None


# --- Requests --------------------------------------------------------------

class StatusChangeRequest(BaseModel):
    """Body for ``POST /houses/{id}/status`` (docs §6).

    ``to_status`` must be a non-empty status (no transition to empty). ``owner`` is
    always required for a non-empty target; ``tenant`` is required only for
    ``rented`` — enforced in the service.
    """

    to_status: str = Field(description="owned | rented | to_let | for_sale")
    owner: OwnerPayload
    tenant: TenantPayload | None = None


class OccupancyEditRequest(BaseModel):
    """Body for ``PATCH /houses/{id}/occupancy/{party}`` (docs §6).

    All fields optional (partial edit). For the owner, changing ``email`` triggers
    the owner-replacement path (docs §4) — handled in the service.
    """

    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    email: str | None = Field(default=None, max_length=320)
    contact_number: str | None = Field(default=None, min_length=1, max_length=32)
    persons_living: int | None = Field(default=None, ge=0)
    id_proof_type: str | None = Field(default=None, max_length=255)
    id_proof_document_id: int | None = Field(default=None)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, v: str | None) -> str | None:
        return normalize_email(v) if v else None


# --- Responses -------------------------------------------------------------

class OccupancyOut(BaseModel):
    """A current occupancy record (owner or tenant)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    house_id: int
    party_type: str
    user_id: int | None
    full_name: str
    email: str | None
    contact_number: str | None
    persons_living: int | None
    id_proof_type: str | None
    id_proof_document_id: int | None
    is_current: bool
    valid_from: date
    valid_to: date | None


class HouseOut(BaseModel):
    """A house row + its derived display code (list/detail read shape)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    society_id: int
    building_id: int | None
    floor_id: int | None
    row_id: int | None
    position_in_row: int | None
    number: str
    status: str
    first_left_empty_on: date | None
    display_code: str = ""


class StatusHistoryOut(BaseModel):
    """One status-history entry (docs §3)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    house_id: int
    from_status: str
    to_status: str
    changed_by: int | None
    changed_at: datetime
    snapshot: dict | None


class HouseDetailOut(BaseModel):
    """Detail payload for ``GET /houses/{id}`` — house + current occupancy(ies)."""

    house: HouseOut
    owner: OccupancyOut | None = None
    tenant: OccupancyOut | None = None
