"""Trash + quota service (docs/modules/vault.md §4) — Wave D.

Restore (parent-chain rehydration), Empty Trash (immediate permanent delete), and
usage accounting. Listing trash and reading usage are implemented in the Phase-0
core; restore/empty-trash are frozen stubs Wave D implements.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.vault.models import VaultDocument, VaultFolder
from app.modules.vault.repository import VaultRepository
from app.modules.vault.schemas import (
    ITEM_TYPE_DOCUMENT,
    ITEM_TYPE_FOLDER,
    EmptyTrashResult,
    RestoreResult,
    TrashItemOut,
    UsageOut,
)
from app.platform.models import Society


class TrashService:
    """Trash listing/restore/empty + storage-usage reads."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = VaultRepository(session)

    # --- reads (implemented) ----------------------------------------------

    def list_trash(self, society_id: int) -> list[TrashItemOut]:
        """Trashed folders + documents with their original paths (docs §6)."""
        items: list[TrashItemOut] = []
        for f in self._repo.list_trashed_folders(society_id):
            items.append(
                TrashItemOut(
                    id=f.id,
                    type=ITEM_TYPE_FOLDER,
                    name=f.name,
                    original_path=self._folder_path(society_id, f),
                    size_bytes=None,
                    deleted_at=f.deleted_at,  # type: ignore[arg-type]
                )
            )
        for d in self._repo.list_trashed_documents(society_id):
            parent = self._repo.get_folder(society_id, d.folder_id)
            base = (
                self._folder_path(society_id, parent) if parent is not None else ""
            )
            items.append(
                TrashItemOut(
                    id=d.id,
                    type=ITEM_TYPE_DOCUMENT,
                    name=d.filename,
                    original_path=f"{base}/{d.filename}".replace("//", "/"),
                    size_bytes=d.size_bytes,
                    deleted_at=d.deleted_at,  # type: ignore[arg-type]
                )
            )
        return items

    def usage(self, society_id: int) -> UsageOut:
        """Used vs limit bytes for the society (docs §6)."""
        usage = self._repo.get_usage(society_id)
        used = usage.used_bytes if usage is not None else 0
        society = self._session.get(Society, society_id)
        limit = society.storage_limit_bytes if society is not None else 0
        return UsageOut(
            used_bytes=used,
            limit_bytes=limit,
            available_bytes=max(limit - used, 0),
        )

    def _folder_path(self, society_id: int, folder: VaultFolder) -> str:
        """Root→folder path like ``/Houses/A-201/Proof`` (cycle-guarded)."""
        names: list[str] = []
        cur: VaultFolder | None = folder
        seen: set[int] = set()
        while cur is not None and cur.id not in seen:
            seen.add(cur.id)
            names.append(cur.name)
            cur = (
                self._repo.get_folder(society_id, cur.parent_id)
                if cur.parent_id is not None
                else None
            )
        names.reverse()
        return "/" + "/".join(names)

    # --- writes (FROZEN — Wave D implements) ------------------------------

    def restore(
        self, society_id: int, item_type: str, item_id: int, *, actor_user_id: int
    ) -> RestoreResult:
        """Clear ``deleted_at`` and rehydrate the parent chain (docs §4)."""
        raise NotImplementedError("Vault Wave D implements restore.")

    def empty_trash(
        self, society_id: int, *, actor_user_id: int
    ) -> EmptyTrashResult:
        """Permanently delete every trashed item now (docs §4)."""
        raise NotImplementedError("Vault Wave D implements empty_trash.")
