"""Vault queries (docs/03 §2) — pure DB access, ``society_id``-scoped.

No business rules here; the service decides, the repository fetches/persists.
Every query is tenant-scoped by ``society_id`` (cross-tenant isolation — docs/PF
§7). FROZEN interface: wave sub-agents implement service logic against these
signatures but must not change them.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.vault.models import (
    SocietyStorageUsage,
    VaultDocument,
    VaultFolder,
)


class VaultRepository:
    """Queries over vault_folders / vault_documents / society_storage_usage."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # --- folders -----------------------------------------------------------

    def get_folder(self, society_id: int, folder_id: int) -> VaultFolder | None:
        return self._session.execute(
            select(VaultFolder).where(
                VaultFolder.id == folder_id,
                VaultFolder.society_id == society_id,
            )
        ).scalar_one_or_none()

    def list_child_folders(
        self, society_id: int, parent_id: int | None, *, include_trashed: bool = False
    ) -> list[VaultFolder]:
        """Live child folders of ``parent_id`` (root when None), name-ordered."""
        conditions = [
            VaultFolder.society_id == society_id,
            VaultFolder.parent_id.is_(None)
            if parent_id is None
            else VaultFolder.parent_id == parent_id,
        ]
        if not include_trashed:
            conditions.append(VaultFolder.deleted_at.is_(None))
        rows = (
            self._session.execute(
                select(VaultFolder).where(*conditions).order_by(VaultFolder.name)
            )
            .scalars()
            .all()
        )
        return list(rows)

    def find_folder_by_name(
        self, society_id: int, parent_id: int | None, name: str
    ) -> VaultFolder | None:
        """A live sibling folder with this exact name (collision check)."""
        return self._session.execute(
            select(VaultFolder).where(
                VaultFolder.society_id == society_id,
                VaultFolder.parent_id.is_(None)
                if parent_id is None
                else VaultFolder.parent_id == parent_id,
                VaultFolder.name == name,
                VaultFolder.deleted_at.is_(None),
            )
        ).scalar_one_or_none()

    def find_system_folder(
        self,
        society_id: int,
        system_key: str,
        *,
        house_id: int | None = None,
        notice_id: int | None = None,
        parent_id: int | None = None,
    ) -> VaultFolder | None:
        """Locate a system folder by its key + subject link (idempotent ensure)."""
        conditions = [
            VaultFolder.society_id == society_id,
            VaultFolder.system_key == system_key,
            VaultFolder.deleted_at.is_(None),
        ]
        if house_id is not None:
            conditions.append(VaultFolder.house_id == house_id)
        if notice_id is not None:
            conditions.append(VaultFolder.notice_id == notice_id)
        if parent_id is not None:
            conditions.append(VaultFolder.parent_id == parent_id)
        return self._session.execute(
            select(VaultFolder).where(*conditions)
        ).scalar_one_or_none()

    def add_folder(self, folder: VaultFolder) -> VaultFolder:
        self._session.add(folder)
        self._session.flush()
        return folder

    # --- documents ---------------------------------------------------------

    def get_document(
        self, society_id: int, document_id: int
    ) -> VaultDocument | None:
        return self._session.execute(
            select(VaultDocument).where(
                VaultDocument.id == document_id,
                VaultDocument.society_id == society_id,
            )
        ).scalar_one_or_none()

    def list_folder_documents(
        self,
        society_id: int,
        folder_id: int,
        *,
        offset: int = 0,
        limit: int = 20,
        include_trashed: bool = False,
    ) -> tuple[list[VaultDocument], int]:
        """Paginated live documents directly inside a folder + total count."""
        conditions = [
            VaultDocument.society_id == society_id,
            VaultDocument.folder_id == folder_id,
        ]
        if not include_trashed:
            conditions.append(VaultDocument.deleted_at.is_(None))
        total = self._session.execute(
            select(func.count()).select_from(VaultDocument).where(*conditions)
        ).scalar_one()
        rows = (
            self._session.execute(
                select(VaultDocument)
                .where(*conditions)
                .order_by(VaultDocument.filename, VaultDocument.id)
                .offset(offset)
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return list(rows), int(total)

    def add_document(self, document: VaultDocument) -> VaultDocument:
        self._session.add(document)
        self._session.flush()
        return document

    def find_document_by_name(
        self, society_id: int, folder_id: int, filename: str
    ) -> VaultDocument | None:
        """A live document with this filename in the folder (collision check)."""
        return self._session.execute(
            select(VaultDocument).where(
                VaultDocument.society_id == society_id,
                VaultDocument.folder_id == folder_id,
                VaultDocument.filename == filename,
                VaultDocument.deleted_at.is_(None),
            )
        ).scalar_one_or_none()

    # --- trash -------------------------------------------------------------

    def list_trashed_folders(self, society_id: int) -> list[VaultFolder]:
        rows = (
            self._session.execute(
                select(VaultFolder)
                .where(
                    VaultFolder.society_id == society_id,
                    VaultFolder.deleted_at.is_not(None),
                )
                .order_by(VaultFolder.deleted_at.desc())
            )
            .scalars()
            .all()
        )
        return list(rows)

    def list_trashed_documents(self, society_id: int) -> list[VaultDocument]:
        rows = (
            self._session.execute(
                select(VaultDocument)
                .where(
                    VaultDocument.society_id == society_id,
                    VaultDocument.deleted_at.is_not(None),
                )
                .order_by(VaultDocument.deleted_at.desc())
            )
            .scalars()
            .all()
        )
        return list(rows)

    def documents_deleted_before(self, cutoff: datetime) -> list[VaultDocument]:
        """All-society trashed documents past the retention cutoff (purge job)."""
        rows = (
            self._session.execute(
                select(VaultDocument).where(
                    VaultDocument.deleted_at.is_not(None),
                    VaultDocument.deleted_at < cutoff,
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    def folders_deleted_before(self, cutoff: datetime) -> list[VaultFolder]:
        rows = (
            self._session.execute(
                select(VaultFolder).where(
                    VaultFolder.deleted_at.is_not(None),
                    VaultFolder.deleted_at < cutoff,
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    def delete_document_row(self, document: VaultDocument) -> None:
        self._session.delete(document)

    def delete_folder_row(self, folder: VaultFolder) -> None:
        self._session.delete(folder)

    # --- usage -------------------------------------------------------------

    def get_usage(self, society_id: int) -> SocietyStorageUsage | None:
        return self._session.execute(
            select(SocietyStorageUsage).where(
                SocietyStorageUsage.society_id == society_id
            )
        ).scalar_one_or_none()

    def get_or_create_usage(
        self, society_id: int, *, lock: bool = False
    ) -> SocietyStorageUsage:
        """The society's usage row, creating it if absent.

        ``lock=True`` takes a row-level ``SELECT ... FOR UPDATE`` so a
        read-check-increment (the upload quota path) is serialized against
        concurrent writers — without it two parallel uploads can both pass the
        quota check and over-commit, or lose an increment (docs §4).
        """
        stmt = select(SocietyStorageUsage).where(
            SocietyStorageUsage.society_id == society_id
        )
        if lock:
            stmt = stmt.with_for_update()
        usage = self._session.execute(stmt).scalar_one_or_none()
        if usage is None:
            usage = SocietyStorageUsage(society_id=society_id, used_bytes=0)
            self._session.add(usage)
            self._session.flush()
            if lock:
                # Lock the freshly-inserted row so the increment stays serialized.
                self._session.execute(
                    stmt.where(SocietyStorageUsage.id == usage.id)
                ).scalar_one()
        return usage

    def sum_all_document_bytes(self, society_id: int) -> int:
        """Re-sum live AND trashed document bytes for a society (reconcile job)."""
        total = self._session.execute(
            select(func.coalesce(func.sum(VaultDocument.size_bytes), 0)).where(
                VaultDocument.society_id == society_id
            )
        ).scalar_one()
        return int(total)

    def all_storage_keys(self, society_id: int) -> set[str]:
        """Every referenced object key for a society (live AND trashed).

        The orphan-object sweep compares MinIO keys under the society's prefix
        against this set; a key not here has no backing row and is safe to drop.
        """
        rows = self._session.execute(
            select(VaultDocument.storage_key).where(
                VaultDocument.society_id == society_id
            )
        ).all()
        return {r[0] for r in rows}
