"""Notifications frozen contracts + domains (docs/modules/notifications.md §3/§6/§8).

The request/response models the router speaks, the extensible ``type`` domain, and
the validated per-society config (``society_modules.config``). Kept here as ONE
frozen source so every wave agent builds against the same shapes.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# --- Notification type domain (extensible strings; service-enforced) ----------
TYPE_COMPLAINT_UPDATE = "complaint_update"
TYPE_COMPLAINT_NEW = "complaint_new"
TYPE_COMPLAINT_WITHDRAWN = "complaint_withdrawn"
TYPE_NOTICE = "notice"
TYPE_MAINTENANCE_DUE = "maintenance_due"

NOTIFICATION_TYPES = frozenset(
    {
        TYPE_COMPLAINT_UPDATE,
        TYPE_COMPLAINT_NEW,
        TYPE_COMPLAINT_WITHDRAWN,
        TYPE_NOTICE,
        TYPE_MAINTENANCE_DUE,
    }
)

# --- entity_type domain (the deep-link + mark_read_for key) -------------------
ENTITY_COMPLAINT = "complaint"
ENTITY_NOTICE = "notice"
ENTITY_HOUSE = "house"

# --- Config defaults + bounds (docs §8) ---------------------------------------
DEFAULT_DUES_ADVANCE_DAYS = 3
DEFAULT_DUES_REMINDER_INTERVAL_DAYS = 5
DEFAULT_READ_RETENTION_DAYS = 30

# The config keys this module owns in society_modules.config (the merge
# whitelist). Kept in step across schemas / support / config service.
CONFIG_KEYS = (
    "dues_advance_days",
    "dues_reminder_interval_days",
    "read_retention_days",
)

MIN_ADVANCE_DAYS = 0
MAX_ADVANCE_DAYS = 28
MIN_INTERVAL_DAYS = 1
MAX_INTERVAL_DAYS = 90
MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = 365


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class NotificationsConfig(BaseModel):
    """The validated Notifications config for a society (docs §8).

    Cadence knobs for the dues reminder (X advance days, every-N-days recurring)
    + how long read rows are retained before the purge. Defaults are the doc's
    3 / 5 / 30. Bounds are defence-in-depth (also enforced on the request).
    """

    dues_advance_days: int = Field(
        default=DEFAULT_DUES_ADVANCE_DAYS,
        ge=MIN_ADVANCE_DAYS,
        le=MAX_ADVANCE_DAYS,
    )
    dues_reminder_interval_days: int = Field(
        default=DEFAULT_DUES_REMINDER_INTERVAL_DAYS,
        ge=MIN_INTERVAL_DAYS,
        le=MAX_INTERVAL_DAYS,
    )
    read_retention_days: int = Field(
        default=DEFAULT_READ_RETENTION_DAYS,
        ge=MIN_RETENTION_DAYS,
        le=MAX_RETENTION_DAYS,
    )


# --- I/O contracts ------------------------------------------------------------


class NotificationOut(_Base):
    """One notification in the caller's feed (docs §6)."""

    id: int
    type: str
    title: str
    body: str
    payload: dict
    entity_type: str | None
    entity_id: int | None
    created_at: datetime


class FeedOut(BaseModel):
    """The unread feed page + the badge count (docs §6).

    ``items`` is the current page (newest first); ``unread_count`` is the TOTAL
    unread across the whole feed (independent of the page — it drives the badge).
    """

    items: list[NotificationOut]
    unread_count: int
    page: int
    page_size: int


class UnreadCountOut(BaseModel):
    """The lightweight badge count only (docs §6)."""

    unread_count: int


class MarkReadResult(BaseModel):
    """How many rows a mark-read / mark-all-read cleared (docs §6)."""

    cleared: int


class ConfigOut(BaseModel):
    """The society's Notifications config (docs §6/§8)."""

    dues_advance_days: int
    dues_reminder_interval_days: int
    read_retention_days: int


class ConfigUpdateRequest(BaseModel):
    """PARTIAL-merge config update — only provided (non-None) keys change (§6/§8).

    An all-None request is a 422 (nothing to update) — enforced in the service.
    """

    model_config = ConfigDict(extra="forbid")

    dues_advance_days: int | None = Field(
        default=None, ge=MIN_ADVANCE_DAYS, le=MAX_ADVANCE_DAYS
    )
    dues_reminder_interval_days: int | None = Field(
        default=None, ge=MIN_INTERVAL_DAYS, le=MAX_INTERVAL_DAYS
    )
    read_retention_days: int | None = Field(
        default=None, ge=MIN_RETENTION_DAYS, le=MAX_RETENTION_DAYS
    )
