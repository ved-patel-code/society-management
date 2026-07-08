"""Complaints Pydantic contracts + enum-like domains (docs/modules/complaints.md).

FROZEN request/response models the router and the inter-module ``api`` speak, plus
the string domains + the status-transition table every layer shares (enforced in
the service — the DB stores raw strings, docs/03 §3). Wave sub-agents implement
service logic against THESE names; they add fields only additively, never rename.

The ``ALLOWED_TRANSITIONS`` table lives here as frozen DATA so the state machine
cannot drift between the three services that own its edges (admin / resident
withdraw / worker archive) — each funnels its write through
``support.record_transition``.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --- Domains (allowed string values; service-enforced) -----------------------

# Complaint lifecycle states (docs §3).
STATUS_OPEN = "open"
STATUS_IN_PROGRESS = "in_progress"
STATUS_RESOLVED = "resolved"
STATUS_CLOSED = "closed"
STATUS_ARCHIVED = "archived"
STATUS_WITHDRAWN = "withdrawn"

COMPLAINT_STATUSES = frozenset(
    {
        STATUS_OPEN,
        STATUS_IN_PROGRESS,
        STATUS_RESOLVED,
        STATUS_CLOSED,
        STATUS_ARCHIVED,
        STATUS_WITHDRAWN,
    }
)

# Terminal states — no transition leaves them.
TERMINAL_STATUSES = frozenset({STATUS_ARCHIVED, STATUS_WITHDRAWN})

# Image kinds (docs §3).
KIND_REPORT = "report"
KIND_PROOF = "proof"
IMAGE_KINDS = frozenset({KIND_REPORT, KIND_PROOF})

# The COMPLETE allowed-transition table (docs §3 "Status enum & allowed
# transitions"). Enforcement is actor-split across services, but the legal set of
# edges lives here once so no service invents an edge the others don't know:
#   - admin  (complaints.update_status): open->in_progress, in_progress->resolved,
#            resolved->closed, resolved->in_progress (reopen).
#   - resident (complaints.create, raiser): open->withdrawn (only while open).
#   - worker (system): closed->archived.
# Any target not in ALLOWED_TRANSITIONS[current] -> 409 ConflictError.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_OPEN: frozenset({STATUS_IN_PROGRESS, STATUS_WITHDRAWN}),
    STATUS_IN_PROGRESS: frozenset({STATUS_RESOLVED}),
    STATUS_RESOLVED: frozenset({STATUS_CLOSED, STATUS_IN_PROGRESS}),
    STATUS_CLOSED: frozenset({STATUS_ARCHIVED}),
    STATUS_ARCHIVED: frozenset(),
    STATUS_WITHDRAWN: frozenset(),
}

# Which transitions the ADMIN status endpoint may drive (docs §3). Withdraw is
# resident-only; archive is worker-only — both are excluded here so the admin
# endpoint can never reach them even though the edge exists for another actor.
ADMIN_TARGET_STATUSES = frozenset(
    {STATUS_IN_PROGRESS, STATUS_RESOLVED, STATUS_CLOSED}
)

# Config defaults (docs §8): society_modules.config for complaints.
DEFAULT_AUTO_ARCHIVE_DAYS = 15
DEFAULT_MAX_REPORT_IMAGES = 2
DEFAULT_MAX_PROOF_IMAGES = 2
# Guardrails for admin-set config.
MIN_AUTO_ARCHIVE_DAYS = 1
MAX_AUTO_ARCHIVE_DAYS = 365
MAX_IMAGES_CEILING = 10  # a sane upper bound on the per-kind cap

# Seeded system categories (docs §3), created lazily on first use.
DEFAULT_CATEGORIES = [
    "Plumbing",
    "Electrical",
    "Common Area",
    "Security",
    "Cleaning",
    "Other",
]

# Reference format (docs §3): C- + zero-padded running number.
REFERENCE_PREFIX = "C-"
REFERENCE_PAD = 6


def format_reference(value: int) -> str:
    """Render a per-society counter value as ``C-000123`` (widens past 999999)."""
    return f"{REFERENCE_PREFIX}{value:0{REFERENCE_PAD}d}"


class _Base(BaseModel):
    """ORM-friendly base for response models."""

    model_config = ConfigDict(from_attributes=True)


# ============================ Categories =====================================


class CategoryCreateRequest(BaseModel):
    """Create a category (docs §6). Name unique among ACTIVE categories."""

    name: str = Field(..., min_length=1, max_length=64)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank.")
        return v


class CategoryUpdateRequest(BaseModel):
    """Rename and/or reactivate a category (docs §6).

    ``name`` renames (must not collide with another active name);
    ``is_active=True`` reactivates a deactivated category. At least one field must
    be provided — enforced in the service.
    """

    name: str | None = Field(default=None, min_length=1, max_length=64)
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank.")
        return v


class CategoryOut(_Base):
    id: int
    name: str
    is_active: bool
    is_system: bool


# ============================ Complaints =====================================


class ComplaintCreateRequest(BaseModel):
    """Raise a complaint (docs §4/§6).

    ``house_id`` is optional: inferred when the caller owns exactly one current
    house; REQUIRED (and verified) when the caller owns several. Report images (if
    any) come as multipart files alongside this body — not modeled here.
    """

    category_id: int
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1, max_length=5000)
    house_id: int | None = None

    @field_validator("title", "description")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank.")
        return v


class ComplaintUpdateRequest(BaseModel):
    """Edit an open complaint (raiser, while ``open``) (docs §4/§6).

    Any subset of the three editable fields; the target category must be ACTIVE.
    At least one field must be provided — enforced in the service.
    """

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=5000)
    category_id: int | None = None

    @field_validator("title", "description")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("must not be blank.")
        return v


class StatusChangeRequest(BaseModel):
    """Admin status transition (docs §4/§6).

    ``to_status`` must be one of :data:`ADMIN_TARGET_STATUSES` and legal from the
    current status per :data:`ALLOWED_TRANSITIONS`. ``note`` is the optional
    per-transition admin note. NOTE: resolving (``in_progress -> resolved``) also
    accepts up to ``max_proof_images`` proof photos via the multipart resolve
    route — this JSON body is used for the non-resolve transitions and carries the
    note; the resolve route reads the same fields from multipart form data.
    """

    to_status: str
    note: str | None = Field(default=None, max_length=5000)

    @field_validator("to_status")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in ADMIN_TARGET_STATUSES:
            raise ValueError(
                f"to_status must be one of {sorted(ADMIN_TARGET_STATUSES)}."
            )
        return v


class StatusHistoryOut(_Base):
    id: int
    from_status: str | None
    to_status: str
    note: str | None
    changed_by: int | None
    created_at: datetime


class ComplaintImageOut(_Base):
    id: int
    kind: str
    vault_document_id: int
    # A signed inline preview URL (populated by the service from Vault).
    preview_url: str | None = None
    created_at: datetime


class ComplaintListItemOut(BaseModel):
    """A list card (docs §6): shaped for a phone drill-down list."""

    id: int
    reference: str
    title: str
    status: str
    category_id: int
    category_name: str
    house_id: int
    house_display_code: str | None = None
    report_image_count: int = 0
    proof_image_count: int = 0
    created_at: datetime
    updated_at: datetime


class ComplaintListOut(BaseModel):
    """Paginated complaint list envelope (carries the total for client paging)."""

    items: list[ComplaintListItemOut]
    total: int


class ComplaintDetailOut(BaseModel):
    """Full complaint detail (docs §6): fields + timeline + images."""

    id: int
    reference: str
    house_id: int
    house_display_code: str | None
    raised_by: int
    category_id: int
    category_name: str
    title: str
    description: str
    status: str
    resolved_at: datetime | None
    closed_at: datetime | None
    archived_at: datetime | None
    withdrawn_at: datetime | None
    created_at: datetime
    updated_at: datetime
    timeline: list[StatusHistoryOut] = []
    images: list[ComplaintImageOut] = []


# ============================ Config =========================================


class ComplaintsConfig(BaseModel):
    """Validated view of ``society_modules.config`` for complaints (docs §8)."""

    auto_archive_days: int = Field(
        default=DEFAULT_AUTO_ARCHIVE_DAYS,
        ge=MIN_AUTO_ARCHIVE_DAYS,
        le=MAX_AUTO_ARCHIVE_DAYS,
    )
    max_report_images: int = Field(
        default=DEFAULT_MAX_REPORT_IMAGES, ge=0, le=MAX_IMAGES_CEILING
    )
    max_proof_images: int = Field(
        default=DEFAULT_MAX_PROOF_IMAGES, ge=0, le=MAX_IMAGES_CEILING
    )


class ComplaintsConfigOut(_Base):
    auto_archive_days: int
    max_report_images: int
    max_proof_images: int


class ConfigUpdateRequest(BaseModel):
    """Update module config (docs §6/§8) — PARTIAL MERGE.

    Only the fields provided are changed; unspecified keys keep their current
    value (the write helper merges over the existing ``society_modules.config``).
    Bounds mirror :class:`ComplaintsConfig`.
    """

    auto_archive_days: int | None = Field(
        default=None, ge=MIN_AUTO_ARCHIVE_DAYS, le=MAX_AUTO_ARCHIVE_DAYS
    )
    max_report_images: int | None = Field(
        default=None, ge=0, le=MAX_IMAGES_CEILING
    )
    max_proof_images: int | None = Field(default=None, ge=0, le=MAX_IMAGES_CEILING)


# ============================ Cross-module provider ==========================


class OpenComplaintCountOut(BaseModel):
    """A house's open (non-terminal, non-archived) complaint count (docs §7)."""

    house_id: int
    open_count: int
