"""Pydantic request/response contracts for Vault (docs/modules/vault.md §6).

Shapes + field validation only; business rules (system-folder protection, quota,
denylist, tree ops) live in the service (docs/03 §2). These are the FROZEN
interface the wave sub-agents build against — extend additively, do not repurpose
existing fields.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# --- Domains (enforced in the service, not the DB) -------------------------
VAULT_SOURCES: frozenset[str] = frozenset(
    {"manual", "id_proof", "complaint", "notice"}
)
SYSTEM_KEYS: frozenset[str] = frozenset(
    {
        "houses_root",
        "house",
        "house_proof",
        "house_complaints",
        "notices_root",
        "notice",
    }
)
# System roots a society always has once vault is used; created on demand.
SYSTEM_ROOT_HOUSES = "Houses"
SYSTEM_ROOT_NOTICES = "Notices"

# Default dangerous/executable extension denylist (config-overridable — spec).
DEFAULT_DENYLIST_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".exe", ".dll", ".bat", ".cmd", ".com", ".scr", ".msi", ".sh",
        ".js", ".jar", ".ps1", ".vbs", ".vbe", ".wsf", ".wsh", ".hta",
        ".cpl", ".msc", ".reg", ".pif", ".gadget", ".apk", ".app",
    }
)
DEFAULT_TRASH_RETENTION_DAYS = 30

ITEM_TYPE_FOLDER = "folder"
ITEM_TYPE_DOCUMENT = "document"


# --- Requests --------------------------------------------------------------

class FolderCreateRequest(BaseModel):
    """Body for ``POST /vault/folders`` (docs §6). ``parent_id`` NULL = root."""

    parent_id: int | None = Field(default=None)
    name: str = Field(min_length=1, max_length=255)


class FolderUpdateRequest(BaseModel):
    """Body for ``PATCH /vault/folders/{id}`` — rename and/or move (docs §6).

    Both optional (at least one supplied). Blocked for system folders in-service.
    Moving to ``parent_id=None`` moves the folder to root.
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: int | None = Field(default=None)
    # Distinguishes "move to root" (parent_id=None + move=True) from "don't move".
    move: bool = Field(default=False)


class DocumentUpdateRequest(BaseModel):
    """Body for ``PATCH /vault/documents/{id}`` — rename and/or move (docs §6)."""

    filename: str | None = Field(default=None, min_length=1, max_length=255)
    folder_id: int | None = Field(default=None)


# --- Responses -------------------------------------------------------------

class FolderOut(BaseModel):
    """A folder row (name is the DERIVED display name for system folders)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    parent_id: int | None
    name: str
    is_system: bool
    system_key: str | None
    house_id: int | None
    notice_id: int | None
    created_at: datetime


class DocumentOut(BaseModel):
    """A document row (metadata; bytes are fetched via preview/download)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    folder_id: int
    filename: str
    content_type: str
    size_bytes: int
    source: str
    source_ref: int | None
    uploaded_by: int | None
    created_at: datetime


class BreadcrumbItem(BaseModel):
    """One step in a folder's path from root. Root itself has ``id=None``."""

    id: int | None
    name: str


class FolderContentsOut(BaseModel):
    """Payload for ``GET /vault/folders/{id}/contents`` (docs §6).

    ``folder`` is None when listing the vault root. ``documents`` is paginated
    (``total``/``page``/``page_size``); subfolders are returned in full (few).
    """

    folder: FolderOut | None
    breadcrumb: list[BreadcrumbItem]
    folders: list[FolderOut]
    documents: list[DocumentOut]
    total: int
    page: int
    page_size: int


class TrashItemOut(BaseModel):
    """One trashed folder or document, with its original path (docs §6)."""

    id: int
    type: str  # folder | document
    name: str
    original_path: str
    size_bytes: int | None
    deleted_at: datetime


class RestoreResult(BaseModel):
    """Result of restoring a trashed item."""

    id: int
    type: str
    restored_to_folder_id: int | None


class EmptyTrashResult(BaseModel):
    """Summary of a manual Empty-Trash (docs §6)."""

    deleted_count: int
    freed_bytes: int


class UsageOut(BaseModel):
    """Storage usage vs limit for the society (docs §6)."""

    used_bytes: int
    limit_bytes: int
    available_bytes: int


class PresignedUrlOut(BaseModel):
    """A short-TTL signed URL for preview/download (docs §6)."""

    url: str
    expires_in: int
