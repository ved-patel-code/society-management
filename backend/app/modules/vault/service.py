"""Vault service facade (docs/modules/vault.md §4/§6).

A thin composition over the concern-split internals (``services/folders.py``,
``documents.py``, ``trash.py``) so the router and the cross-module API depend on a
single ``VaultService`` while parallel waves own disjoint files. The facade adds
no business logic — it delegates.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.vault.schemas import (
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
from app.modules.vault.services.documents import DocumentService
from app.modules.vault.services.folders import FolderService
from app.modules.vault.services.trash import TrashService


class VaultService:
    """Single entry point for vault operations (delegates to sub-services)."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self.folders = FolderService(session)
        self.documents = DocumentService(session)
        self.trash = TrashService(session)

    # --- folders -----------------------------------------------------------

    def get_contents(
        self, society_id: int, folder_id: int | None, *, offset: int, limit: int
    ) -> FolderContentsOut:
        return self.folders.get_contents(
            society_id, folder_id, offset=offset, limit=limit
        )

    def create_folder(
        self, society_id: int, req: FolderCreateRequest, *, actor_user_id: int
    ) -> FolderOut:
        return self.folders.create_folder(
            society_id, req, actor_user_id=actor_user_id
        )

    def update_folder(
        self,
        society_id: int,
        folder_id: int,
        req: FolderUpdateRequest,
        *,
        actor_user_id: int,
    ) -> FolderOut:
        return self.folders.update_folder(
            society_id, folder_id, req, actor_user_id=actor_user_id
        )

    def delete_folder(
        self, society_id: int, folder_id: int, *, actor_user_id: int
    ) -> None:
        self.folders.delete_folder(society_id, folder_id, actor_user_id=actor_user_id)

    # --- documents ---------------------------------------------------------

    def upload_document(
        self,
        society_id: int,
        folder_id: int,
        *,
        filename: str,
        content_type: str,
        data: bytes,
        actor_user_id: int,
        source: str = "manual",
        source_ref: int | None = None,
    ) -> DocumentOut:
        return self.documents.upload(
            society_id,
            folder_id,
            filename=filename,
            content_type=content_type,
            data=data,
            actor_user_id=actor_user_id,
            source=source,
            source_ref=source_ref,
        )

    def preview_url(self, society_id: int, document_id: int) -> PresignedUrlOut:
        return self.documents.preview_url(society_id, document_id)

    def download_url(self, society_id: int, document_id: int) -> PresignedUrlOut:
        return self.documents.download_url(society_id, document_id)

    def update_document(
        self,
        society_id: int,
        document_id: int,
        req: DocumentUpdateRequest,
        *,
        actor_user_id: int,
    ) -> DocumentOut:
        return self.documents.update(
            society_id, document_id, req, actor_user_id=actor_user_id
        )

    def delete_document(
        self, society_id: int, document_id: int, *, actor_user_id: int
    ) -> None:
        self.documents.soft_delete(
            society_id, document_id, actor_user_id=actor_user_id
        )

    # --- trash / usage -----------------------------------------------------

    def list_trash(self, society_id: int) -> list[TrashItemOut]:
        return self.trash.list_trash(society_id)

    def restore(
        self, society_id: int, item_type: str, item_id: int, *, actor_user_id: int
    ) -> RestoreResult:
        return self.trash.restore(
            society_id, item_type, item_id, actor_user_id=actor_user_id
        )

    def empty_trash(
        self, society_id: int, *, actor_user_id: int
    ) -> EmptyTrashResult:
        return self.trash.empty_trash(society_id, actor_user_id=actor_user_id)

    def usage(self, society_id: int) -> UsageOut:
        return self.trash.usage(society_id)
