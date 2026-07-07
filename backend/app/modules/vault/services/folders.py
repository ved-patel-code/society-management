"""Folder-tree service (docs/modules/vault.md §4) — Wave B.

Unlimited nesting; system-folder protection; auto-created house/notice folders.
Reads (contents + breadcrumb) are implemented in the Phase-0 core; the write ops
and the auto-ensure helpers are frozen stubs Wave B implements.

The service NEVER commits (``get_session`` commits once per request — docs/03 §2);
it flushes where an id is needed.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.errors import ConflictError, NotFoundError, ValidationError
from app.common.time import utcnow
from app.modules.onboarding.models import Building, House
from app.modules.onboarding.numbering import (
    building_display_code,
    individual_display_code,
)
from app.modules.vault.models import VaultFolder
from app.modules.vault.repository import VaultRepository
from app.modules.vault.schemas import (
    SYSTEM_ROOT_HOUSES,
    SYSTEM_ROOT_NOTICES,
    BreadcrumbItem,
    DocumentOut,
    FolderContentsOut,
    FolderCreateRequest,
    FolderOut,
    FolderUpdateRequest,
)
from app.platform.audit.service import AuditService

# Root breadcrumb sentinel (the vault has no single root row; None = root).
ROOT_LABEL = "Vault"

# System-folder audit label mapping for the ensure_* helpers.
_HOUSE_KIND_SUBFOLDER = {
    "proof": ("house_proof", "Proof"),
    "complaints": ("house_complaints", "Complaints"),
}


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

        Regular folders use their stored ``name``. House-linked system folders
        (``system_key == "house"``) DERIVE their label from the onboarding house's
        current display code so a building/house rename never desyncs the vault
        label (rename-safe — docs §3/§4). The per-house ``Proof``/``Complaints``
        subfolders keep their stored literal names; everything else returns the
        stored ``name``. A missing house falls back to the stored name gracefully.
        """
        if folder.system_key == "house" and folder.house_id is not None:
            code = self._house_display_code(folder.society_id, folder.house_id)
            return code if code is not None else folder.name
        return folder.name

    def _house_display_code(
        self, society_id: int, house_id: int
    ) -> str | None:
        """Current display code for a house (docs §3) or ``None`` if missing.

        Building house → ``building_display_code`` (e.g. ``A-201``) using the
        building's configured ``display_separator``; individual house → bare
        number. Returns ``None`` when the house row is gone (caller falls back).
        """
        house = self._session.execute(
            select(House).where(
                House.id == house_id, House.society_id == society_id
            )
        ).scalar_one_or_none()
        if house is None:
            return None
        if house.building_id is not None:
            building = self._session.execute(
                select(Building).where(
                    Building.id == house.building_id,
                    Building.society_id == society_id,
                )
            ).scalar_one_or_none()
            if building is None:
                return None
            separator = building.numbering_config.get("display_separator", "-")
            return building_display_code(
                building.name, house.number, separator=separator
            )
        return individual_display_code(house.number)

    def _require_live_folder(
        self, society_id: int, folder_id: int
    ) -> VaultFolder:
        folder = self._repo.get_folder(society_id, folder_id)
        if folder is None or folder.deleted_at is not None:
            raise NotFoundError(
                "Folder not found.", details={"folder_id": folder_id}
            )
        return folder

    # --- helpers ----------------------------------------------------------

    def _validate_name(self, name: str) -> str:
        """Trim + validate a user-supplied folder name (docs §4).

        Rejects empty (after strip) and any name containing ``/`` (path
        separator) → ``ValidationError``.
        """
        cleaned = name.strip()
        if not cleaned:
            raise ValidationError("Folder name cannot be empty.")
        if "/" in cleaned:
            raise ValidationError(
                "Folder name cannot contain '/'.", details={"name": name}
            )
        return cleaned

    def _assert_no_sibling(
        self,
        society_id: int,
        parent_id: int | None,
        name: str,
        *,
        exclude_id: int | None = None,
    ) -> None:
        """Reject a name that collides with a LIVE sibling (docs §4).

        The DB partial-unique does NOT cover root-level siblings (SQL NULL is
        distinct), so this in-service check guards BOTH root and non-root. When
        renaming, ``exclude_id`` skips the folder itself.

        Compares against each sibling's DISPLAY name — which for a house system
        folder is DERIVED from the current house code, not the stored name — so a
        custom folder can't be created with a name that visually duplicates a
        renamed house folder (the stored name may be stale after a renumber).
        """
        for sib in self._repo.list_child_folders(society_id, parent_id):
            if sib.id == exclude_id:
                continue
            if name in (sib.name, self._display_name(sib)):
                raise ConflictError(
                    "A folder with this name already exists here.",
                    details={"parent_id": parent_id, "name": name},
                )

    # --- writes -----------------------------------------------------------

    def create_folder(
        self, society_id: int, req: FolderCreateRequest, *, actor_user_id: int
    ) -> FolderOut:
        """Create a non-system folder under ``parent_id`` (root when None) — docs §4.

        Validates the name, requires a live parent (if given), and rejects a
        collision with a live sibling (root and non-root alike). Audits
        ``vault.folder_created``.
        """
        name = self._validate_name(req.name)

        if req.parent_id is not None:
            # Parent must exist and be live (trashed → not found).
            self._require_live_folder(society_id, req.parent_id)

        self._assert_no_sibling(society_id, req.parent_id, name)

        folder = VaultFolder(
            society_id=society_id,
            parent_id=req.parent_id,
            name=name,
            is_system=False,
            system_key=None,
            house_id=None,
            notice_id=None,
            created_by=actor_user_id,
        )
        self._repo.add_folder(folder)  # flushes → id assigned

        AuditService(self._session).record(
            action="vault.folder_created",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="vault_folder",
            entity_id=folder.id,
            after={"name": name, "parent_id": req.parent_id},
        )
        return self._folder_out(folder)

    def update_folder(
        self,
        society_id: int,
        folder_id: int,
        req: FolderUpdateRequest,
        *,
        actor_user_id: int,
    ) -> FolderOut:
        """Rename and/or move a folder (docs §4).

        System folders are protected (not renamable/movable). A rename validates
        the name and collision-checks the target parent's live siblings. A move
        (only when ``req.move``) validates the destination parent (live, in
        society), forbids moving a folder into itself or a descendant (cycle
        guard), and collision-checks the destination. At least one of name/move
        must be supplied. Moving INTO a system folder is allowed.
        """
        folder = self._require_live_folder(society_id, folder_id)

        if folder.is_system:
            raise ConflictError(
                "System folders cannot be renamed or moved.",
                details={"folder_id": folder_id},
            )

        wants_rename = req.name is not None
        wants_move = req.move
        if not wants_rename and not wants_move:
            raise ValidationError(
                "Provide a new name and/or a move.",
                details={"folder_id": folder_id},
            )

        # Resolve the effective parent for post-mutation collision checks: a move
        # changes it, otherwise it stays put.
        target_parent_id = req.parent_id if wants_move else folder.parent_id

        if wants_move:
            self._validate_move(society_id, folder, req.parent_id)

        # --- Rename ---
        if wants_rename:
            new_name = self._validate_name(req.name)  # type: ignore[arg-type]
            if new_name != folder.name:
                self._assert_no_sibling(
                    society_id,
                    target_parent_id,
                    new_name,
                    exclude_id=folder.id,
                )
                before_name = folder.name
                folder.name = new_name
                self._session.flush()
                AuditService(self._session).record(
                    action="vault.folder_renamed",
                    actor_user_id=actor_user_id,
                    society_id=society_id,
                    entity_type="vault_folder",
                    entity_id=folder.id,
                    before={"name": before_name},
                    after={"name": new_name},
                )

        # --- Move ---
        if wants_move and req.parent_id != folder.parent_id:
            # Collision-check in the destination using the (possibly renamed) name.
            self._assert_no_sibling(
                society_id,
                req.parent_id,
                folder.name,
                exclude_id=folder.id,
            )
            before_parent = folder.parent_id
            folder.parent_id = req.parent_id
            self._session.flush()
            AuditService(self._session).record(
                action="vault.folder_moved",
                actor_user_id=actor_user_id,
                society_id=society_id,
                entity_type="vault_folder",
                entity_id=folder.id,
                before={"parent_id": before_parent},
                after={"parent_id": req.parent_id},
            )

        return self._folder_out(folder)

    def _validate_move(
        self,
        society_id: int,
        folder: VaultFolder,
        new_parent_id: int | None,
    ) -> None:
        """Validate a move destination (docs §4).

        Destination must exist, be live, and belong to the society (when not
        root). A folder cannot move into itself or any descendant — walk the new
        parent's ancestor chain to root and reject if ``folder.id`` appears
        (cycle guard).
        """
        if new_parent_id is None:
            return  # moving to root is always structurally valid

        if new_parent_id == folder.id:
            raise ValidationError(
                "A folder cannot be moved into itself.",
                details={"folder_id": folder.id},
            )

        new_parent = self._require_live_folder(society_id, new_parent_id)

        # Walk ancestors of the destination up to root; if we meet the folder we
        # are moving, the destination is one of its descendants → cycle.
        cur: VaultFolder | None = new_parent
        seen: set[int] = set()
        while cur is not None and cur.id not in seen:
            if cur.id == folder.id:
                raise ConflictError(
                    "A folder cannot be moved into one of its own descendants.",
                    details={"folder_id": folder.id, "parent_id": new_parent_id},
                )
            seen.add(cur.id)
            cur = (
                self._repo.get_folder(society_id, cur.parent_id)
                if cur.parent_id is not None
                else None
            )

    def delete_folder(
        self, society_id: int, folder_id: int, *, actor_user_id: int
    ) -> None:
        """Soft-delete a folder + cascade to its live subtree (docs §4).

        System folders (roots AND per-house/notice folders) are protected. The
        top folder, every live descendant folder, and every live document in the
        subtree get ``deleted_at`` stamped (documents also ``deleted_by``).
        Storage/usage are untouched — trashed bytes still count (Wave D's
        permanent-delete concern). Audits ``vault.folder_deleted`` once with a
        cascaded-item count.
        """
        folder = self._require_live_folder(society_id, folder_id)

        if folder.is_system:
            raise ConflictError(
                "System folders cannot be deleted.",
                details={"folder_id": folder_id},
            )

        now = utcnow()
        cascaded_folders = 0
        cascaded_documents = 0

        # BFS over the live subtree, including the top folder itself.
        queue: list[VaultFolder] = [folder]
        visited: set[int] = set()
        while queue:
            cur = queue.pop()
            if cur.id in visited:
                continue
            visited.add(cur.id)

            cur.deleted_at = now
            if cur.id != folder.id:
                cascaded_folders += 1

            # Soft-delete this folder's live documents.
            cascaded_documents += self._soft_delete_folder_documents(
                society_id, cur.id, now=now, actor_user_id=actor_user_id
            )

            # Enqueue live children.
            queue.extend(
                self._repo.list_child_folders(society_id, cur.id)
            )

        self._session.flush()

        AuditService(self._session).record(
            action="vault.folder_deleted",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="vault_folder",
            entity_id=folder.id,
            before={"name": folder.name, "parent_id": folder.parent_id},
            after={
                "deleted_at": now.isoformat(),
                "cascaded_folders": cascaded_folders,
                "cascaded_documents": cascaded_documents,
            },
        )

    def _soft_delete_folder_documents(
        self,
        society_id: int,
        folder_id: int,
        *,
        now,
        actor_user_id: int,
    ) -> int:
        """Soft-delete every live document directly in a folder; return the count."""
        count = 0
        page = 500
        # Always re-query from offset 0: each page we trash leaves the live set,
        # so the next query returns the following live rows (no offset drift). We
        # flush after each page so those rows are excluded from the next query's
        # ``deleted_at IS NULL`` filter, terminating the loop.
        while True:
            docs, _ = self._repo.list_folder_documents(
                society_id, folder_id, offset=0, limit=page
            )
            if not docs:
                break
            for doc in docs:
                doc.deleted_at = now
                doc.deleted_by = actor_user_id
                count += 1
            self._session.flush()
        return count

    # --- auto-ensure (inter-module contract — docs §4/§7) -----------------

    def ensure_house_folder(
        self, society_id: int, house_id: int, *, kind: str, actor_user_id: int
    ) -> VaultFolder:
        """Auto-create ``Houses/<house>/Proof`` or ``/Complaints`` (docs §4).

        Idempotent: ensures the system ``Houses`` root, the per-house folder
        (linked by ``house_id``; stored name = current house display code), and
        the ``Proof``/``Complaints`` leaf. Reuses existing rows and audits
        ``vault.folder_created`` only for rows it actually creates. Returns the
        leaf folder.
        """
        if kind not in _HOUSE_KIND_SUBFOLDER:
            raise ValidationError(
                "Unknown house folder kind.",
                details={"kind": kind},
            )
        leaf_key, leaf_name = _HOUSE_KIND_SUBFOLDER[kind]

        # House display code is required (also validates the house exists).
        display_code = self._house_display_code(society_id, house_id)
        if display_code is None:
            raise NotFoundError(
                "House not found.", details={"house_id": house_id}
            )

        # 1) Houses root (society-level system folder).
        houses_root = self._ensure_system_folder(
            society_id,
            system_key="houses_root",
            name=SYSTEM_ROOT_HOUSES,
            parent_id=None,
            actor_user_id=actor_user_id,
        )

        # 2) Per-house folder under the Houses root (linked by house_id).
        house_folder = self._repo.find_system_folder(
            society_id, "house", house_id=house_id, parent_id=houses_root.id
        )
        if house_folder is None:
            house_folder = self._create_system_folder(
                society_id,
                system_key="house",
                name=display_code,
                parent_id=houses_root.id,
                house_id=house_id,
                actor_user_id=actor_user_id,
            )

        # 3) Proof / Complaints leaf under the per-house folder.
        leaf = self._repo.find_system_folder(
            society_id, leaf_key, house_id=house_id, parent_id=house_folder.id
        )
        if leaf is None:
            leaf = self._create_system_folder(
                society_id,
                system_key=leaf_key,
                name=leaf_name,
                parent_id=house_folder.id,
                house_id=house_id,
                actor_user_id=actor_user_id,
            )
        return leaf

    def ensure_notice_folder(
        self, society_id: int, notice_id: int, *, actor_user_id: int
    ) -> VaultFolder:
        """Auto-create the society-level ``Notices/<notice>`` folder (docs §4).

        Idempotent: ensures the system ``Notices`` root and the per-notice folder
        (linked by ``notice_id``; stored name = the notice id, since the notices
        table does not exist yet). Audits only rows it creates. Returns the
        per-notice folder.
        """
        notices_root = self._ensure_system_folder(
            society_id,
            system_key="notices_root",
            name=SYSTEM_ROOT_NOTICES,
            parent_id=None,
            actor_user_id=actor_user_id,
        )

        notice_folder = self._repo.find_system_folder(
            society_id, "notice", notice_id=notice_id, parent_id=notices_root.id
        )
        if notice_folder is None:
            notice_folder = self._create_system_folder(
                society_id,
                system_key="notice",
                name=str(notice_id),
                parent_id=notices_root.id,
                notice_id=notice_id,
                actor_user_id=actor_user_id,
            )
        return notice_folder

    def _ensure_system_folder(
        self,
        society_id: int,
        *,
        system_key: str,
        name: str,
        parent_id: int | None,
        actor_user_id: int,
    ) -> VaultFolder:
        """Find-or-create a keyed system folder (used for the Houses/Notices roots)."""
        existing = self._repo.find_system_folder(
            society_id, system_key, parent_id=parent_id
        )
        if existing is not None:
            return existing
        return self._create_system_folder(
            society_id,
            system_key=system_key,
            name=name,
            parent_id=parent_id,
            actor_user_id=actor_user_id,
        )

    def _create_system_folder(
        self,
        society_id: int,
        *,
        system_key: str,
        name: str,
        parent_id: int | None,
        house_id: int | None = None,
        notice_id: int | None = None,
        actor_user_id: int,
    ) -> VaultFolder:
        """Insert a system folder + audit its creation (used by the ensure_* helpers)."""
        folder = VaultFolder(
            society_id=society_id,
            parent_id=parent_id,
            name=name,
            is_system=True,
            system_key=system_key,
            house_id=house_id,
            notice_id=notice_id,
            created_by=actor_user_id,
        )
        self._repo.add_folder(folder)  # flushes → id assigned

        AuditService(self._session).record(
            action="vault.folder_created",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="vault_folder",
            entity_id=folder.id,
            after={
                "name": name,
                "parent_id": parent_id,
                "system_key": system_key,
            },
        )
        return folder
