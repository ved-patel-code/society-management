"""Document service (docs/modules/vault.md §4) — Wave C.

Backend-proxied upload with atomic denylist (415) + quota (413) enforcement,
DB-only rename/move, soft-delete, and authorized presigned preview/download.

The service NEVER commits; upload does object-put + row-insert + usage-increment
in the one request transaction (``get_session`` commits once at request end, and
rolls back on any exception — so a failed ``put_object`` leaves no orphan row).
"""
from __future__ import annotations

import hashlib
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.errors import (
    DomainError,
    NotFoundError,
    ValidationError,
)
from app.common.time import utcnow
from app.core.storage import DEFAULT_PRESIGN_TTL_SECONDS
from app.core.storage.provider import get_storage
from app.modules.vault.models import VaultDocument
from app.modules.vault.repository import VaultRepository
from app.modules.vault.schemas import (
    DEFAULT_DENYLIST_EXTENSIONS,
    DocumentOut,
    DocumentUpdateRequest,
    PresignedUrlOut,
)
from app.platform.audit.service import AuditService
from app.platform.models import Society, SocietyModule

_MAX_FILENAME_LEN = 255

# There is no 413/415 error class in app.common.errors; the denylist and quota
# rules (docs §4) need those exact HTTP statuses, so we define small local
# DomainError subclasses with the right ``status_code``/``code`` (docs/03 §6).


class PayloadTooLargeError(DomainError):
    status_code = 413
    code = "storage_quota_exceeded"


class UnsupportedMediaTypeError(DomainError):
    status_code = 415
    code = "file_type_not_allowed"


# Content-type is advisory (the extension denylist is authoritative — docs §4),
# but obviously-executable MIME types are blocked regardless of extension.
_EXECUTABLE_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "application/x-msdownload",
        "application/x-msdos-program",
        "application/x-sh",
        "application/x-shellscript",
        "application/x-executable",
        "application/x-dosexec",
    }
)


class DocumentService:
    """Upload / preview / download / rename / move / soft-delete of documents."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = VaultRepository(session)

    # --- helpers -----------------------------------------------------------

    def _sanitize_filename(self, filename: str) -> str:
        """Reduce to a safe basename (docs §4 upload rules).

        Strips any path component and rejects traversal/empty names
        (``/``, ``\\``, ``..``) → ValidationError; enforces the 255-char cap.
        """
        raw = (filename or "").strip()
        # Take the basename regardless of separator style (defends against both
        # POSIX and Windows path fragments in a client-supplied name).
        base = raw.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if not base or base in {".", ".."}:
            raise ValidationError(
                "Invalid filename.", details={"filename": filename}
            )
        if len(base) > _MAX_FILENAME_LEN:
            raise ValidationError(
                f"Filename exceeds {_MAX_FILENAME_LEN} characters.",
                details={"filename": base, "max_length": _MAX_FILENAME_LEN},
            )
        return base

    def _effective_denylist(self, society_id: int) -> frozenset[str]:
        """Per-society denylist override or the default (docs §8).

        Reads ``SocietyModule.config['denylist_extensions']`` for the vault
        module when present, else falls back to DEFAULT_DENYLIST_EXTENSIONS.
        """
        module = self._session.execute(
            select(SocietyModule).where(
                SocietyModule.society_id == society_id,
                SocietyModule.module_key == "vault",
            )
        ).scalar_one_or_none()
        if module is not None:
            override = (module.config or {}).get("denylist_extensions")
            if override:
                return frozenset(
                    ext.lower() for ext in override if isinstance(ext, str)
                )
        return DEFAULT_DENYLIST_EXTENSIONS

    def _check_file_type(
        self, society_id: int, filename: str, content_type: str
    ) -> None:
        """Reject denied file types (docs §4) → 415.

        The lowercased extension (incl. dot) is checked against the effective
        denylist; a missing/empty extension is allowed. An obviously-executable
        content-type is blocked regardless of extension.
        """
        ext = os.path.splitext(filename)[1].lower()
        if ext and ext in self._effective_denylist(society_id):
            raise UnsupportedMediaTypeError(
                "This file type is not allowed.",
                details={"filename": filename, "extension": ext},
            )
        if content_type and content_type.lower() in _EXECUTABLE_CONTENT_TYPES:
            raise UnsupportedMediaTypeError(
                "This file type is not allowed.",
                details={"filename": filename, "content_type": content_type},
            )

    def _disambiguate_filename(
        self, society_id: int, folder_id: int, filename: str
    ) -> str:
        """Auto-rename on collision to match a file-manager UX (docs §4 choice).

        If a LIVE document with the same name already sits in the folder, append
        " (n)" before the extension (``report.pdf`` → ``report (1).pdf``) with
        the first free ``n``. Chosen over a hard ConflictError so re-uploading a
        same-named file "just works" like a desktop file manager.
        """
        if (
            self._repo.find_document_by_name(society_id, folder_id, filename)
            is None
        ):
            return filename
        stem, ext = os.path.splitext(filename)
        n = 1
        while True:
            candidate = f"{stem} ({n}){ext}"
            if len(candidate) <= _MAX_FILENAME_LEN and (
                self._repo.find_document_by_name(
                    society_id, folder_id, candidate
                )
                is None
            ):
                return candidate
            n += 1

    def _require_live_folder(self, society_id: int, folder_id: int):
        """A LIVE folder in this society (the vault root is not a folder)."""
        folder = self._repo.get_folder(society_id, folder_id)
        if folder is None or folder.deleted_at is not None:
            raise NotFoundError(
                "Folder not found.", details={"folder_id": folder_id}
            )
        return folder

    def _require_live_document(
        self, society_id: int, document_id: int
    ) -> VaultDocument:
        doc = self._repo.get_document(society_id, document_id)
        if doc is None or doc.deleted_at is not None:
            raise NotFoundError(
                "Document not found.", details={"document_id": document_id}
            )
        return doc

    # --- operations --------------------------------------------------------

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
        increment ``used_bytes`` — all atomically (docs §4).

        Ordering: the DB row + usage increment happen in the same request
        transaction as (and just before) the object put. If ``put_object``
        raises, ``get_session`` rolls the whole transaction back, so no orphan
        metadata row survives — storage errors are NOT swallowed.
        """
        # 1. Target must be a live folder in this society.
        self._require_live_folder(society_id, folder_id)

        # 2. Sanitize the filename (basename, no traversal, <=255).
        safe_name = self._sanitize_filename(filename)

        # 3. Denylist (415) — extension is authoritative, MIME advisory.
        self._check_file_type(society_id, safe_name, content_type)

        # 4. Quota (413) — no per-file cap; only the available-storage check.
        size = len(data)
        usage = self._repo.get_or_create_usage(society_id)
        society = self._session.get(Society, society_id)
        if society is None:
            raise NotFoundError(
                "Society not found.", details={"society_id": society_id}
            )
        if usage.used_bytes + size > society.storage_limit_bytes:
            raise PayloadTooLargeError(
                "Uploading this file would exceed the storage quota.",
                details={
                    "used": usage.used_bytes,
                    "size": size,
                    "limit": society.storage_limit_bytes,
                },
            )

        # 5. Collision — auto-rename (file-manager UX) rather than reject.
        safe_name = self._disambiguate_filename(society_id, folder_id, safe_name)

        # 6. Persist atomically. Insert the row first so the id is known, then
        #    derive the storage key from that id (docs §3 key scheme).
        doc = VaultDocument(
            society_id=society_id,
            folder_id=folder_id,
            filename=safe_name,
            content_type=content_type,
            size_bytes=size,
            storage_key="",  # placeholder, set once the id is assigned
            checksum=hashlib.sha256(data).hexdigest(),
            source=source,
            source_ref=source_ref,
            uploaded_by=actor_user_id,
        )
        self._repo.add_document(doc)  # flush → doc.id
        doc.storage_key = f"societies/{society_id}/{doc.id}__{safe_name}"
        self._session.flush()

        # 7. Put the object. On failure the transaction rolls back (no orphan).
        get_storage().put_object(doc.storage_key, data, content_type)

        # 8. Account for the bytes (live + trashed count until permanent delete).
        usage.used_bytes += size
        self._session.flush()

        # 9. Audit.
        AuditService(self._session).record(
            action="vault.document_uploaded",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="vault_document",
            entity_id=doc.id,
            after={
                "document_id": doc.id,
                "folder_id": folder_id,
                "filename": safe_name,
                "size": size,
                "source": source,
            },
        )
        return DocumentOut.model_validate(doc)

    def preview_url(self, society_id: int, document_id: int) -> PresignedUrlOut:
        """Inline presigned URL (PDF/images render in place) — docs §4/§6.

        A read (``vault.read``); no audit. Requires a live document.
        """
        doc = self._require_live_document(society_id, document_id)
        url = get_storage().presigned_get_url(
            doc.storage_key,
            expires_seconds=DEFAULT_PRESIGN_TTL_SECONDS,
            download_name=doc.filename,
            inline=True,
        )
        return PresignedUrlOut(url=url, expires_in=DEFAULT_PRESIGN_TTL_SECONDS)

    def download_url(self, society_id: int, document_id: int) -> PresignedUrlOut:
        """Attachment presigned URL (forces download) — docs §4/§6.

        A read (``vault.read``); no audit. Requires a live document.
        """
        doc = self._require_live_document(society_id, document_id)
        url = get_storage().presigned_get_url(
            doc.storage_key,
            expires_seconds=DEFAULT_PRESIGN_TTL_SECONDS,
            download_name=doc.filename,
            inline=False,
        )
        return PresignedUrlOut(url=url, expires_in=DEFAULT_PRESIGN_TTL_SECONDS)

    def update(
        self,
        society_id: int,
        document_id: int,
        req: DocumentUpdateRequest,
        *,
        actor_user_id: int,
    ) -> DocumentOut:
        """Rename and/or move — DB-only, the object is never touched (docs §4).

        ``storage_key`` embeds the document id, so a rename/move only changes
        metadata (folder tree + filename); the stored bytes stay put.
        """
        if req.filename is None and req.folder_id is None:
            raise ValidationError(
                "Provide a new filename and/or a destination folder."
            )
        doc = self._require_live_document(society_id, document_id)

        # Rename — sanitize + re-check the denylist (renaming to .exe is blocked)
        # + collision in the CURRENT folder.
        if req.filename is not None:
            before_name = doc.filename
            new_name = self._sanitize_filename(req.filename)
            self._check_file_type(society_id, new_name, doc.content_type)
            new_name = self._disambiguate_filename(
                society_id, doc.folder_id, new_name
            )
            if new_name != before_name:
                doc.filename = new_name
                self._session.flush()
                AuditService(self._session).record(
                    action="vault.document_renamed",
                    actor_user_id=actor_user_id,
                    society_id=society_id,
                    entity_type="vault_document",
                    entity_id=doc.id,
                    before={"filename": before_name},
                    after={"filename": new_name},
                )

        # Move — destination must be a live folder in this society; check for a
        # name collision there.
        if req.folder_id is not None and req.folder_id != doc.folder_id:
            before_folder = doc.folder_id
            self._require_live_folder(society_id, req.folder_id)
            doc.filename = self._disambiguate_filename(
                society_id, req.folder_id, doc.filename
            )
            doc.folder_id = req.folder_id
            self._session.flush()
            AuditService(self._session).record(
                action="vault.document_moved",
                actor_user_id=actor_user_id,
                society_id=society_id,
                entity_type="vault_document",
                entity_id=doc.id,
                before={"folder_id": before_folder},
                after={"folder_id": req.folder_id},
            )

        return DocumentOut.model_validate(doc)

    def soft_delete(
        self, society_id: int, document_id: int, *, actor_user_id: int
    ) -> None:
        """Move a document to Trash (``deleted_at``); bytes still count (docs §4).

        The object is NOT deleted and usage is NOT decremented — trashed bytes
        count toward the quota until permanent purge (Wave D).
        """
        doc = self._require_live_document(society_id, document_id)
        doc.deleted_at = utcnow()
        doc.deleted_by = actor_user_id
        self._session.flush()
        AuditService(self._session).record(
            action="vault.document_deleted",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="vault_document",
            entity_id=doc.id,
            before={
                "document_id": doc.id,
                "folder_id": doc.folder_id,
                "filename": doc.filename,
            },
        )
