"""Folder-tree service (docs/modules/vault.md §4) — Wave B.

Unlimited nesting; system-folder protection; auto-created house/notice folders.
Reads (contents + breadcrumb) are implemented in the Phase-0 core; the write ops
and the auto-ensure helpers are frozen stubs Wave B implements.

The service NEVER commits (``get_session`` commits once per request — docs/03 §2);
it flushes where an id is needed.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.common.errors import NotFoundError
from app.modules.vault.models import VaultFolder
from app.modules.vault.repository import VaultRepository
from app.modules.vault.schemas import (
    BreadcrumbItem,
    DocumentOut,
    FolderContentsOut,
    FolderCreateRequest,
    FolderOut,
    FolderUpdateRequest,
)

# Root breadcrumb sentinel (the vault has no single root row; None = root).
ROOT_LABEL = "Vault"


class FolderService:
    """Folder tree operations over ``vault_folders``."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = VaultRepository(session)

    # --- reads (implemented) ----------------------------------------------

    def get_contents(
        self,
        society_id: int,
        folder_id: int | None,
        *,
        offset: int,
        limit: int,
    ) -> FolderContentsOut:
        """Subfolders + paginated documents + breadcrumb for a folder (docs §6).

        ``folder_id=None`` lists the vault root (top-level folders; the root holds
        no documents directly). A trashed folder is treated as not found.
        """
        folder: VaultFolder | None = None
        if folder_id is not None:
            folder = self._require_live_folder(society_id, folder_id)

        child_folders = self._repo.list_child_folders(society_id, folder_id)
        folder_out = [self._folder_out(f) for f in child_folders]

        if folder_id is None:
            documents: list[DocumentOut] = []
            total = 0
        else:
            docs, total = self._repo.list_folder_documents(
                society_id, folder_id, offset=offset, limit=limit
            )
            documents = [DocumentOut.model_validate(d) for d in docs]

        return FolderContentsOut(
            folder=self._folder_out(folder) if folder else None,
            breadcrumb=self._breadcrumb(society_id, folder),
            folders=folder_out,
            documents=documents,
            total=total,
            page=(offset // limit) + 1 if limit else 1,
            page_size=limit,
        )

    def _breadcrumb(
        self, society_id: int, folder: VaultFolder | None
    ) -> list[BreadcrumbItem]:
        """Root→current path. Root is a sentinel (``id=None``)."""
        chain: list[BreadcrumbItem] = []
        cur = folder
        # Walk up to the root, guarding against cycles with a visited set.
        seen: set[int] = set()
        while cur is not None and cur.id not in seen:
            seen.add(cur.id)
            chain.append(BreadcrumbItem(id=cur.id, name=self._display_name(cur)))
            cur = (
                self._repo.get_folder(society_id, cur.parent_id)
                if cur.parent_id is not None
                else None
            )
        chain.reverse()
        return [BreadcrumbItem(id=None, name=ROOT_LABEL), *chain]

    def _folder_out(self, folder: VaultFolder) -> FolderOut:
        out = FolderOut.model_validate(folder)
        out.name = self._display_name(folder)
        return out

    def _display_name(self, folder: VaultFolder) -> str:
        """Display name for a folder.

        Regular folders use their stored ``name``. Wave B overrides this for
        house-linked system folders to DERIVE the label from the onboarding house
        display code (rename-safe — docs §3/§4). Phase-0 core returns the stored
        name so the read is coherent before Wave B lands.
        """
        return folder.name

    def _require_live_folder(
        self, society_id: int, folder_id: int
    ) -> VaultFolder:
        folder = self._repo.get_folder(society_id, folder_id)
        if folder is None or folder.deleted_at is not None:
            raise NotFoundError(
                "Folder not found.", details={"folder_id": folder_id}
            )
        return folder

    # --- writes (FROZEN — Wave B implements) ------------------------------

    def create_folder(
        self, society_id: int, req: FolderCreateRequest, *, actor_user_id: int
    ) -> FolderOut:
        raise NotImplementedError("Vault Wave B implements create_folder.")

    def update_folder(
        self,
        society_id: int,
        folder_id: int,
        req: FolderUpdateRequest,
        *,
        actor_user_id: int,
    ) -> FolderOut:
        raise NotImplementedError("Vault Wave B implements update_folder.")

    def delete_folder(
        self, society_id: int, folder_id: int, *, actor_user_id: int
    ) -> None:
        raise NotImplementedError("Vault Wave B implements delete_folder.")

    def ensure_house_folder(
        self, society_id: int, house_id: int, *, kind: str, actor_user_id: int
    ) -> VaultFolder:
        """Auto-create ``Houses/<house>/Proof`` or ``/Complaints`` (docs §4)."""
        raise NotImplementedError("Vault Wave B implements ensure_house_folder.")

    def ensure_notice_folder(
        self, society_id: int, notice_id: int, *, actor_user_id: int
    ) -> VaultFolder:
        """Auto-create the society-level ``Notices/<notice>`` folder (docs §4)."""
        raise NotImplementedError("Vault Wave B implements ensure_notice_folder.")
