"""Trash + quota service (docs/modules/vault.md §4) — Wave D.

Restore (parent-chain rehydration), Empty Trash (immediate permanent delete), and
usage accounting. Listing trash and reading usage are implemented in the Phase-0
core; restore/empty-trash are frozen stubs Wave D implements.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.common.errors import ConflictError, NotFoundError, ValidationError
from app.core.storage.provider import get_storage
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
from app.platform.audit.service import AuditService
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
        """Clear ``deleted_at`` and rehydrate the parent chain (docs §4).

        Restoring a **folder** clears ``deleted_at`` on the folder AND on every
        currently-trashed descendant folder/document (the subtree cascade-trashed
        with it), then rehydrates its trashed ancestor chain so it is reachable.
        Restoring a **document** un-trashes it and rehydrates its parent-folder
        chain (documents require a NOT-NULL ``folder_id``; if the immediate parent
        row is gone we cannot reattach and raise ``ConflictError``). On restore a
        name that would collide with a live sibling gets ``" (restored)"``
        appended to satisfy the partial-unique index. Request path — no commit.
        """
        if item_type == ITEM_TYPE_DOCUMENT:
            return self._restore_document(
                society_id, item_id, actor_user_id=actor_user_id
            )
        if item_type == ITEM_TYPE_FOLDER:
            return self._restore_folder(
                society_id, item_id, actor_user_id=actor_user_id
            )
        raise ValidationError(f"Unknown trash item type: {item_type!r}")

    def _restore_document(
        self, society_id: int, document_id: int, *, actor_user_id: int
    ) -> RestoreResult:
        doc = self._repo.get_document(society_id, document_id)
        if doc is None:
            raise NotFoundError("Trashed document not found.")
        if doc.deleted_at is None:
            raise ConflictError("Document is not in the trash.")

        parent = self._repo.get_folder(society_id, doc.folder_id)
        if parent is None:
            # NOT-NULL folder_id: without a parent folder the doc is unreachable.
            raise ConflictError(
                "Cannot restore document: its parent folder no longer exists."
            )
        # Rehydrate the parent chain so the restored doc is reachable.
        self._rehydrate_chain(society_id, parent)

        doc.deleted_at = None
        doc.deleted_by = None
        # Avoid colliding with a live sibling of the same filename (documents
        # have no unique index, but keep names distinct for a clean UX).
        doc.filename = self._unique_restored_name(
            doc.filename,
            lambda name: self._repo.find_document_by_name(
                society_id, doc.folder_id, name
            ),
            self_id=doc.id,
        )

        self._session.flush()
        self._audit_restored(society_id, doc.id, ITEM_TYPE_DOCUMENT, actor_user_id)
        return RestoreResult(
            id=doc.id,
            type=ITEM_TYPE_DOCUMENT,
            restored_to_folder_id=doc.folder_id,
        )

    def _restore_folder(
        self, society_id: int, folder_id: int, *, actor_user_id: int
    ) -> RestoreResult:
        folder = self._repo.get_folder(society_id, folder_id)
        if folder is None:
            raise NotFoundError("Trashed folder not found.")
        if folder.deleted_at is None:
            raise ConflictError("Folder is not in the trash.")

        # Un-trash the whole trashed subtree (everything cascade-trashed with it).
        self._restore_subtree(society_id, folder)

        # Rehydrate ancestors so the folder is reachable from a live root.
        if folder.parent_id is not None:
            parent = self._repo.get_folder(society_id, folder.parent_id)
            if parent is not None:
                self._rehydrate_chain(society_id, parent)

        # Avoid colliding with a live sibling folder of the same name. Folders
        # DO have a partial-unique index, so an un-looped suffix could itself
        # collide and raise IntegrityError — loop until the name is free.
        folder.name = self._unique_restored_name(
            folder.name,
            lambda name: self._repo.find_folder_by_name(
                society_id, folder.parent_id, name
            ),
            self_id=folder.id,
        )

        self._session.flush()
        self._audit_restored(society_id, folder.id, ITEM_TYPE_FOLDER, actor_user_id)
        return RestoreResult(
            id=folder.id,
            type=ITEM_TYPE_FOLDER,
            restored_to_folder_id=folder.parent_id,
        )

    def _restore_subtree(self, society_id: int, folder: VaultFolder) -> None:
        """Clear ``deleted_at`` on ``folder`` and all currently-trashed descendants.

        Simple, robust rule (documented): restoring a folder un-trashes every
        descendant that is still in the trash, i.e. the cascade that was deleted
        with it. Cycle-guarded by tracking visited folder ids.
        """
        # Bucket trashed documents by folder once (avoids a full society-wide
        # trashed-docs scan per folder in the subtree — was O(folders × docs)).
        docs_by_folder: dict[int, list[VaultDocument]] = {}
        for doc in self._repo.list_trashed_documents(society_id):
            docs_by_folder.setdefault(doc.folder_id, []).append(doc)

        stack: list[VaultFolder] = [folder]
        seen: set[int] = set()
        while stack:
            cur = stack.pop()
            if cur.id in seen:
                continue
            seen.add(cur.id)
            cur.deleted_at = None
            # Un-trash trashed documents directly inside this folder.
            for doc in docs_by_folder.get(cur.id, []):
                doc.deleted_at = None
                doc.deleted_by = None
            # Recurse into trashed child folders.
            for child in self._repo.list_child_folders(
                society_id, cur.id, include_trashed=True
            ):
                if child.deleted_at is not None and child.id not in seen:
                    stack.append(child)

    @staticmethod
    def _unique_restored_name(name, find_by_name, *, self_id: int) -> str:
        """A sibling-unique name for a restored item.

        Returns ``name`` unchanged if free; otherwise appends ``" (restored)"``,
        then ``" (restored 2)"``, ``" (restored 3)"`` … until no LIVE sibling
        matches. Looping matters for folders (partial-unique index → an
        un-looped single suffix could itself collide and raise IntegrityError).
        ``find_by_name(name)`` returns a matching live row (or None); a match on
        the item itself (``self_id``) is not a collision.
        """
        def _taken(candidate: str) -> bool:
            existing = find_by_name(candidate)
            return existing is not None and existing.id != self_id

        if not _taken(name):
            return name
        candidate = f"{name} (restored)"
        n = 2
        while _taken(candidate):
            candidate = f"{name} (restored {n})"
            n += 1
        return candidate

    def _rehydrate_chain(self, society_id: int, folder: VaultFolder) -> None:
        """Walk ``parent_id`` up, un-trashing any trashed ancestor (docs §4)."""
        cur: VaultFolder | None = folder
        seen: set[int] = set()
        while cur is not None and cur.id not in seen:
            seen.add(cur.id)
            if cur.deleted_at is not None:
                cur.deleted_at = None
            cur = (
                self._repo.get_folder(society_id, cur.parent_id)
                if cur.parent_id is not None
                else None
            )

    def _audit_restored(
        self, society_id: int, entity_id: int, item_type: str, actor_user_id: int
    ) -> None:
        AuditService(self._session).record(
            action="vault.item_restored",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type=item_type,
            entity_id=entity_id,
            after={"id": entity_id, "type": item_type},
        )

    def empty_trash(
        self, society_id: int, *, actor_user_id: int
    ) -> EmptyTrashResult:
        """Permanently delete every trashed item now (docs §4).

        Deletes MinIO objects for trashed documents, then drops document rows
        (FK ``folder_id`` first), then folder rows deepest-first so a child never
        outlives its ``parent_id`` FK target. Decrements ``used_bytes`` by freed
        bytes (clamped at 0). Request path — no commit (``get_session`` commits).
        """
        freed_bytes = 0
        deleted_count = 0

        trashed_docs = self._repo.list_trashed_documents(society_id)
        for doc in trashed_docs:
            get_storage().delete_object(doc.storage_key)
            freed_bytes += doc.size_bytes
            self._repo.delete_document_row(doc)
            deleted_count += 1
        # Flush document deletes before dropping their parent folders (FK order).
        self._session.flush()

        trashed_folders = self._repo.list_trashed_folders(society_id)
        # Delete children before parents: order by descending depth so a folder's
        # descendants are removed before it (parent_id FK is satisfied).
        for folder in sorted(
            trashed_folders,
            key=lambda f: self._folder_depth(society_id, f),
            reverse=True,
        ):
            self._repo.delete_folder_row(folder)
            deleted_count += 1
        self._session.flush()

        usage = self._repo.get_or_create_usage(society_id, lock=True)
        usage.used_bytes = max(0, usage.used_bytes - freed_bytes)
        self._session.flush()

        AuditService(self._session).record(
            action="vault.trash_emptied",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="society",
            entity_id=society_id,
            after={"deleted_count": deleted_count, "freed_bytes": freed_bytes},
        )
        return EmptyTrashResult(
            deleted_count=deleted_count, freed_bytes=freed_bytes
        )

    def _folder_depth(self, society_id: int, folder: VaultFolder) -> int:
        """Depth from root (cycle-guarded) — used to delete leaves first."""
        depth = 0
        cur: VaultFolder | None = folder
        seen: set[int] = set()
        while cur is not None and cur.parent_id is not None and cur.id not in seen:
            seen.add(cur.id)
            depth += 1
            cur = self._repo.get_folder(society_id, cur.parent_id)
        return depth
