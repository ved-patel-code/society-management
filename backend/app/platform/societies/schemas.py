"""Pydantic request/response contracts for super-admin society + module ops.

Shapes only — field validation lives here; business rules stay in the service
(docs/03 §2/§6). ``type`` is never accepted at creation: the society_admin picks
building vs individual_houses during onboarding (docs/PF §3/§6).
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# Society status domain (enforced in the service, not the DB — docs/PF §3).
SOCIETY_STATUSES: frozenset[str] = frozenset({"onboarding", "active", "suspended"})


# --- Societies -------------------------------------------------------------

class SocietyCreate(BaseModel):
    """Body for ``POST /admin/societies`` (docs/PF §6/§10).

    ``default_member_password`` is REQUIRED and stored hashed (Argon2id) — the
    plaintext never leaves this request (docs/PF §3/§14.6).
    """

    name: str = Field(min_length=1, max_length=255)
    storage_limit_bytes: int = Field(gt=0)
    default_member_password: str = Field(min_length=1)
    currency: str = Field(default="INR", min_length=1, max_length=8)
    timezone: str = Field(default="Asia/Kolkata", min_length=1, max_length=64)
    settings: dict = Field(default_factory=dict)


class SocietyUpdate(BaseModel):
    """Body for ``PATCH /admin/societies/{id}`` — only mutable config.

    Every field is optional; only provided fields are changed. ``type`` and the
    default password are intentionally not mutable here (docs/PF §3/§6).
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    storage_limit_bytes: int | None = Field(default=None, gt=0)
    currency: str | None = Field(default=None, min_length=1, max_length=8)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    settings: dict | None = None
    status: str | None = None


class SocietyOut(BaseModel):
    """Society response shape. The password hash is never exposed."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: str | None
    status: str
    storage_limit_bytes: int
    currency: str
    timezone: str
    settings: dict
    created_at: datetime
    updated_at: datetime


# --- Module allocation -----------------------------------------------------

class ModuleAllocation(BaseModel):
    """One entry in the ``PUT /admin/societies/{id}/modules`` body."""

    module_key: str = Field(min_length=1, max_length=64)
    enabled: bool
    config: dict = Field(default_factory=dict)


class ModuleAllocationRequest(BaseModel):
    """Full body for ``PUT /admin/societies/{id}/modules``."""

    modules: list[ModuleAllocation] = Field(min_length=1)


class SocietyModuleOut(BaseModel):
    """Society-module response shape (docs/PF §3)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    society_id: int
    module_key: str
    enabled: bool
    config: dict
    enabled_by: int | None
    enabled_at: datetime | None
