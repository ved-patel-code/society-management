"""Notice Board Pydantic contracts + enum-like domain (docs/modules/notice-board.md).

FROZEN request/response models the router + inter-module ``api`` speak, plus the
``status`` string domain and the ``ALLOWED_TRANSITIONS`` table every layer shares
(enforced in the service — the DB stores raw strings, docs/03 §3). Wave
sub-agents implement service logic against THESE names; they add fields only
additively, never rename.

``ALLOWED_TRANSITIONS`` lives here as frozen DATA so the state machine cannot
drift between the CRUD (create/edit) and lifecycle (publish/withdraw) services —
each funnels its status write through ``support`` (``apply_publish`` /
``assert_transition_allowed``).
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --- Domain (allowed string values; service-enforced) ------------------------

# Notice lifecycle states (docs §3). ``expired`` is COMPUTED (published +
# expires_at < now), never stored — so it is deliberately NOT in this set.
STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"
STATUS_WITHDRAWN = "withdrawn"

NOTICE_STATUSES = frozenset({STATUS_DRAFT, STATUS_PUBLISHED, STATUS_WITHDRAWN})

# Terminal states — no transition leaves them.
TERMINAL_STATUSES = frozenset({STATUS_WITHDRAWN})

# The COMPLETE allowed-transition table (docs §3 "Status enum & allowed
# transitions"). Edit/pin/expiry keep ``status='published'`` (not a transition).
#   - publish  (notices.publish): draft → published.
#   - withdraw (notices.publish): draft → withdrawn OR published → withdrawn
#     (the service allows discarding a draft; docs §3).
# Any target not in ALLOWED_TRANSITIONS[current] → 409 ConflictError.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_DRAFT: frozenset({STATUS_PUBLISHED, STATUS_WITHDRAWN}),
    STATUS_PUBLISHED: frozenset({STATUS_WITHDRAWN}),
    STATUS_WITHDRAWN: frozenset(),
}

# Admin list ``scope`` filter (docs §6): the active feed vs the archive.
SCOPE_ACTIVE = "active"
SCOPE_ARCHIVE = "archive"
LIST_SCOPES = frozenset({SCOPE_ACTIVE, SCOPE_ARCHIVE})

# Field bounds.
TITLE_MAX = 200
BODY_MAX = 50_000  # generous ceiling for a sanitized rich-text body


class _Base(BaseModel):
    """ORM-friendly base for response models."""

    model_config = ConfigDict(from_attributes=True)


# ============================ Requests =======================================


class NoticeCreateRequest(BaseModel):
    """Compose a notice (docs §4/§6).

    Created as a ``draft`` unless ``publish=true`` (publish-on-create). ``body``
    is rich text and is sanitized server-side before storage (docs §4).
    Attachments are added via follow-up multipart calls, not modeled here.
    """

    title: str = Field(..., min_length=1, max_length=TITLE_MAX)
    body: str = Field(..., min_length=1, max_length=BODY_MAX)
    is_pinned: bool = False
    expires_at: datetime | None = None
    publish: bool = False

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title must not be blank.")
        return v


class NoticeUpdateRequest(BaseModel):
    """Edit a notice (admin) (docs §4/§6).

    Any subset of the four editable fields. Editing CONTENT (``title``/``body``)
    stamps ``last_edited_at``; editing ``is_pinned``/``expires_at`` alone does
    not. ``expires_at`` may be cleared by passing ``null`` (sentinel handling is
    the service's concern). At least one field must be provided — enforced in the
    service.
    """

    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX)
    body: str | None = Field(default=None, min_length=1, max_length=BODY_MAX)
    is_pinned: bool | None = None
    expires_at: datetime | None = None

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("title must not be blank.")
        return v


# ============================ Responses ======================================


class NoticeAttachmentOut(_Base):
    id: int
    vault_document_id: int
    # Signed URLs populated by the service from Vault (guarded → None if the
    # document was trashed / Vault is disabled).
    preview_url: str | None = None
    download_url: str | None = None
    created_at: datetime


class NoticeListItemOut(BaseModel):
    """A feed card (docs §6): shaped for a phone list."""

    id: int
    title: str
    status: str
    is_pinned: bool
    published_at: datetime | None
    expires_at: datetime | None
    last_edited_at: datetime | None
    attachment_count: int = 0
    is_read: bool = False
    created_at: datetime
    updated_at: datetime


class NoticeListOut(BaseModel):
    """Paginated feed envelope + the caller's unread count (docs §6)."""

    items: list[NoticeListItemOut]
    total: int
    unread_count: int = 0


class NoticeDetailOut(BaseModel):
    """Full notice detail (docs §6): fields + attachments + caller read state."""

    id: int
    title: str
    body: str
    status: str
    is_pinned: bool
    published_at: datetime | None
    expires_at: datetime | None
    last_edited_at: datetime | None
    created_by: int
    withdrawn_at: datetime | None
    withdrawn_by: int | None
    is_read: bool = False
    created_at: datetime
    updated_at: datetime
    attachments: list[NoticeAttachmentOut] = []


# ============================ Read receipts ==================================


class ReceiptUserOut(BaseModel):
    """One owner in a read-receipt list (read or unread)."""

    user_id: int
    read_at: datetime | None = None


class NoticeReceiptsOut(BaseModel):
    """Admin read receipts for a notice (docs §4/§6).

    Denominator = the society's CURRENT owners (from Occupancy). Owners
    provisioned after the notice was posted appear as unread until they open it.
    """

    notice_id: int
    total_owners: int
    read_count: int
    unread_count: int
    read: list[ReceiptUserOut] = []
    unread: list[ReceiptUserOut] = []
