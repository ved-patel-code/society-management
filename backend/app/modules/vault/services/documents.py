"""Document service (docs/modules/vault.md §4) — Wave C.

Backend-proxied upload with atomic denylist (415) + quota (413) enforcement,
DB-only rename/move, soft-delete, and authorized presigned preview/download.
All methods are frozen stubs the Wave C sub-agent implements against the storage
provider (:func:`app.core.storage.provider.get_storage`) and the repository.

The service NEVER commits; upload does object-put + row-insert + usage-increment
in the one request transaction.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.vault.repository import VaultRepository
from app.modules.vault.schemas import (
    DocumentOut,
    DocumentUpdateRequest,
    PresignedUrlOut,
)


class DocumentService:
    """Upload / preview / download / rename / move / soft-delete of documents."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = VaultRepository(session)

    def upload(
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
        """Store a file: validate denylist + quota, put object, insert row, and
        increment ``used_bytes`` — all atomically (docs §4)."""
        raise NotImplementedError("Vault Wave C implements upload.")

    def preview_url(self, society_id: int, document_id: int) -> PresignedUrlOut:
        """Inline presigned URL (PDF/images) after authorizing the doc (docs §6)."""
        raise NotImplementedError("Vault Wave C implements preview_url.")

    def download_url(self, society_id: int, document_id: int) -> PresignedUrlOut:
        """Attachment presigned URL after authorizing the doc (docs §6)."""
        raise NotImplementedError("Vault Wave C implements download_url.")

    def update(
        self,
        society_id: int,
        document_id: int,
        req: DocumentUpdateRequest,
        *,
        actor_user_id: int,
    ) -> DocumentOut:
        """Rename and/or move — DB-only, the object is never touched (docs §4)."""
        raise NotImplementedError("Vault Wave C implements update.")

    def soft_delete(
        self, society_id: int, document_id: int, *, actor_user_id: int
    ) -> None:
        """Move a document to Trash (``deleted_at``); bytes still count (docs §4)."""
        raise NotImplementedError("Vault Wave C implements soft_delete.")
