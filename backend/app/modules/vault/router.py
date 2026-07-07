"""Vault routes (docs/modules/vault.md §6), prefix ``/vault``.

Society comes from the JWT (``TenantContext.society_id``) — never a path id. Every
route gates on ``require_module('vault')`` + a permission (read: ``vault.read``;
mutations: ``vault.manage``). The router stays thin: resolve tenant → call
``VaultService`` → shape the response.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from sqlalchemy.orm import Session

from app.common.errors import ValidationError
from app.common.pagination import PageParams
from app.core.db import get_session
from app.core.deps import (
    AuthContext,
    TenantContext,
    get_tenant_context,
    require_module,
    require_permission,
)
from app.modules.vault.schemas import (
    ITEM_TYPE_DOCUMENT,
    ITEM_TYPE_FOLDER,
    DocumentOut,
    DocumentUpdateRequest,
    EmptyTrashResult,
    FolderContentsOut,
    FolderCreateRequest,
    FolderOut,
    FolderUpdateRequest,
    PresignedUrlOut,
    RestoreResult,
    TrashItemOut,
    UsageOut,
)
from app.modules.vault.service import VaultService

router = APIRouter(prefix="/vault", tags=["vault"])

# Reusable gate dependencies (module enabled + the right permission).
_READ = [Depends(require_module("vault")), Depends(require_permission("vault.read"))]
_MANAGE = [
    Depends(require_module("vault")),
    Depends(require_permission("vault.manage")),
]

# Trash path segment → internal item type.
_TRASH_ITEM_TYPES = {"folders": ITEM_TYPE_FOLDER, "documents": ITEM_TYPE_DOCUMENT}


def _society_id(tenant: TenantContext) -> int:
    """Resolve the active society id or fail — feature routes are society-scoped."""
    if tenant.society_id is None:
        raise ValidationError("No active society for this request.")
    return tenant.society_id


# --- folder browse ---------------------------------------------------------

@router.get("/folders/contents", response_model=FolderContentsOut, dependencies=_READ)
def root_contents(
    page: PageParams = Depends(),
    auth: AuthContext = Depends(require_permission("vault.read")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> FolderContentsOut:
    """List the vault root (top-level folders + system roots)."""
    return VaultService(session).get_contents(
        _society_id(tenant), None, offset=page.offset, limit=page.limit
    )


@router.get(
    "/folders/{folder_id}/contents",
    response_model=FolderContentsOut,
    dependencies=_READ,
)
def folder_contents(
    folder_id: int,
    page: PageParams = Depends(),
    auth: AuthContext = Depends(require_permission("vault.read")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> FolderContentsOut:
    """Subfolders + paginated documents + breadcrumb for a folder."""
    return VaultService(session).get_contents(
        _society_id(tenant), folder_id, offset=page.offset, limit=page.limit
    )


# --- folder mutations ------------------------------------------------------

@router.post("/folders", response_model=FolderOut, dependencies=_MANAGE)
def create_folder(
    body: FolderCreateRequest,
    auth: AuthContext = Depends(require_permission("vault.manage")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> FolderOut:
    """Create a folder under ``parent_id`` (root when null)."""
    return VaultService(session).create_folder(
        _society_id(tenant), body, actor_user_id=auth.user_id
    )


@router.patch("/folders/{folder_id}", response_model=FolderOut, dependencies=_MANAGE)
def update_folder(
    folder_id: int,
    body: FolderUpdateRequest,
    auth: AuthContext = Depends(require_permission("vault.manage")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> FolderOut:
    """Rename and/or move a folder (blocked for system folders)."""
    return VaultService(session).update_folder(
        _society_id(tenant), folder_id, body, actor_user_id=auth.user_id
    )


@router.delete("/folders/{folder_id}", status_code=204, dependencies=_MANAGE)
def delete_folder(
    folder_id: int,
    auth: AuthContext = Depends(require_permission("vault.manage")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> Response:
    """Soft-delete a folder (subtree) to Trash (blocked for system roots)."""
    VaultService(session).delete_folder(
        _society_id(tenant), folder_id, actor_user_id=auth.user_id
    )
    return Response(status_code=204)


# --- documents -------------------------------------------------------------

@router.post("/documents", response_model=DocumentOut, dependencies=_MANAGE)
async def upload_document(
    folder_id: int = Form(...),
    file: UploadFile = File(...),
    auth: AuthContext = Depends(require_permission("vault.manage")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> DocumentOut:
    """Upload a file into a folder (multipart; denylist + quota enforced)."""
    data = await file.read()
    return VaultService(session).upload_document(
        _society_id(tenant),
        folder_id,
        filename=file.filename or "unnamed",
        content_type=file.content_type or "application/octet-stream",
        data=data,
        actor_user_id=auth.user_id,
    )


@router.get(
    "/documents/{document_id}/preview",
    response_model=PresignedUrlOut,
    dependencies=_READ,
)
def preview_document(
    document_id: int,
    auth: AuthContext = Depends(require_permission("vault.read")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> PresignedUrlOut:
    """Short-TTL inline presigned URL (PDF/images render in place)."""
    return VaultService(session).preview_url(_society_id(tenant), document_id)


@router.get(
    "/documents/{document_id}/download",
    response_model=PresignedUrlOut,
    dependencies=_READ,
)
def download_document(
    document_id: int,
    auth: AuthContext = Depends(require_permission("vault.read")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> PresignedUrlOut:
    """Short-TTL attachment presigned URL."""
    return VaultService(session).download_url(_society_id(tenant), document_id)


@router.patch(
    "/documents/{document_id}", response_model=DocumentOut, dependencies=_MANAGE
)
def update_document(
    document_id: int,
    body: DocumentUpdateRequest,
    auth: AuthContext = Depends(require_permission("vault.manage")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> DocumentOut:
    """Rename and/or move a document (DB-only; object untouched)."""
    return VaultService(session).update_document(
        _society_id(tenant), document_id, body, actor_user_id=auth.user_id
    )


@router.delete("/documents/{document_id}", status_code=204, dependencies=_MANAGE)
def delete_document(
    document_id: int,
    auth: AuthContext = Depends(require_permission("vault.manage")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> Response:
    """Soft-delete a document to Trash."""
    VaultService(session).delete_document(
        _society_id(tenant), document_id, actor_user_id=auth.user_id
    )
    return Response(status_code=204)


# --- trash / usage ---------------------------------------------------------

@router.get("/trash", response_model=list[TrashItemOut], dependencies=_READ)
def list_trash(
    auth: AuthContext = Depends(require_permission("vault.read")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> list[TrashItemOut]:
    """List trashed folders + documents with their original paths."""
    return VaultService(session).list_trash(_society_id(tenant))


@router.post(
    "/trash/{item_type}/{item_id}/restore",
    response_model=RestoreResult,
    dependencies=_MANAGE,
)
def restore_item(
    item_type: str,
    item_id: int,
    auth: AuthContext = Depends(require_permission("vault.manage")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> RestoreResult:
    """Restore a trashed folder/document (``item_type`` = folders | documents)."""
    internal = _TRASH_ITEM_TYPES.get(item_type)
    if internal is None:
        raise ValidationError(
            "Unknown trash item type.", details={"item_type": item_type}
        )
    return VaultService(session).restore(
        _society_id(tenant), internal, item_id, actor_user_id=auth.user_id
    )


@router.post("/trash/empty", response_model=EmptyTrashResult, dependencies=_MANAGE)
def empty_trash(
    auth: AuthContext = Depends(require_permission("vault.manage")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> EmptyTrashResult:
    """Permanently delete every trashed item now."""
    return VaultService(session).empty_trash(
        _society_id(tenant), actor_user_id=auth.user_id
    )


@router.get("/usage", response_model=UsageOut, dependencies=_READ)
def get_usage(
    auth: AuthContext = Depends(require_permission("vault.read")),
    tenant: TenantContext = Depends(get_tenant_context),
    session: Session = Depends(get_session),
) -> UsageOut:
    """Used vs limit storage for the society."""
    return VaultService(session).usage(_society_id(tenant))
