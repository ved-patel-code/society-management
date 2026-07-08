"""Complaint REPORT-image concern — WAVE D (docs/modules/complaints.md §4/§6).

Owns the resident's report photos only: ``POST /complaints/{id}/images`` (add a
report image while the complaint is ``open``, ≤ ``max_report_images``) and
``DELETE /complaints/{id}/images/{imageId}`` (remove one's own report image ->
soft-delete the Vault document + drop the row). Enforces the cap BEFORE upload;
files to the Vault under ``Houses/<house>/Complaints/<reference>`` via
``ensure_house_folder(kind='complaints')`` + ``store_document(source='complaint')``;
surfaces Vault 413/415. Audits ``complaint.image_added`` / ``complaint.image_removed``.

PROOF images are NOT handled here — they are attached only at the resolve
transition (Wave C, ``services/status.py``), per the user decision that proof is
part of resolving and locked afterward.

FROZEN STUBS: Wave D fills the bodies, editing only THIS file + its own test file.
"""
from __future__ import annotations

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.common.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.modules.complaints.models import Complaint, ComplaintImage
from app.modules.complaints.repository import ComplaintRepository
from app.modules.complaints.schemas import (
    ComplaintImageOut,
    KIND_REPORT,
    STATUS_OPEN,
)
from app.modules.complaints.services import support
from app.modules.vault import api as vault_api
from app.modules.vault.service import VaultService
from app.platform.audit.service import AuditService

# Fallbacks for a multipart part that arrives without a filename / content-type
# (Vault sanitizes the name and the extension denylist is authoritative anyway).
_DEFAULT_FILENAME = "image"
_DEFAULT_CONTENT_TYPE = "application/octet-stream"


class ImagesService:
    """Resident REPORT-image lifecycle: add (capped, while ``open``) + remove.

    The router does NOT ``await`` these methods (it forwards the raw
    :class:`UploadFile`); the bytes are read here synchronously off the
    underlying spooled file object, mirroring Wave C's ``StatusService.resolve``
    (also a plain ``def`` that consumes ``UploadFile``s). This keeps the frozen
    router signature intact — the method returns a value, not a coroutine.
    """

    def __init__(self, session: Session, repo: ComplaintRepository) -> None:
        self._session = session
        self._repo = repo

    # --- helpers -----------------------------------------------------------

    def _require_complaint(
        self, society_id: int, complaint_id: int
    ) -> Complaint:
        complaint = self._repo.get_complaint(society_id, complaint_id)
        if complaint is None:
            raise NotFoundError(
                "Complaint not found.", details={"complaint_id": complaint_id}
            )
        return complaint

    @staticmethod
    def _require_raiser(complaint: Complaint, actor_user_id: int) -> None:
        """Report images are the raiser's own (docs §2/§4) — else 403."""
        if complaint.raised_by != actor_user_id:
            raise PermissionDeniedError(
                "Only the complaint's raiser may manage its report images.",
                details={"complaint_id": complaint.id},
            )

    @staticmethod
    def _require_open(complaint: Complaint) -> None:
        """Report images are editable only while ``open`` (locked at in_progress)."""
        if complaint.status != STATUS_OPEN:
            raise ConflictError(
                "Report images can only be changed while the complaint is open.",
                details={
                    "complaint_id": complaint.id,
                    "status": complaint.status,
                },
            )

    # --- operations --------------------------------------------------------

    def add_report_image(
        self,
        society_id: int,
        complaint_id: int,
        file: UploadFile,
        *,
        actor_user_id: int,
    ) -> ComplaintImageOut:
        """Add a report image to an OPEN complaint (raiser-only, capped) (§4/§6).

        Steps (docs §4): the complaint must exist (404); the actor must be its
        raiser (403); it must still be ``open`` (409 — report images are locked
        once an admin moves it forward). The ``max_report_images`` cap is enforced
        BEFORE any upload (409 — never store bytes we would then reject). The image
        is filed into the Vault under ``Houses/<house>/Complaints/…`` (folder
        auto-created) and a ``complaint_images(kind='report')`` row is written.
        Vault's own 413 (quota) / 415 (denied type) propagate untouched (§4). The
        write is audited ``complaint.image_added``.
        """
        complaint = self._require_complaint(society_id, complaint_id)
        self._require_raiser(complaint, actor_user_id)
        self._require_open(complaint)

        # Cap BEFORE upload so a rejected add never leaves an orphan Vault object.
        config = support.load_config(self._session, society_id)
        current = self._repo.count_images(complaint_id, kind=KIND_REPORT)
        if current >= config.max_report_images:
            raise ConflictError(
                "Report image limit reached for this complaint.",
                details={
                    "complaint_id": complaint_id,
                    "limit": config.max_report_images,
                },
            )

        data = file.file.read()

        # File into the house's Complaints folder (auto-created), then record the
        # link. Vault raises 413/415 here — let them surface to the caller.
        folder = vault_api.ensure_house_folder(
            self._session,
            society_id,
            complaint.house_id,
            kind=vault_api.HOUSE_FOLDER_COMPLAINTS,
            actor_user_id=actor_user_id,
        )
        document = vault_api.store_document(
            self._session,
            society_id,
            folder.id,
            filename=file.filename or _DEFAULT_FILENAME,
            content_type=file.content_type or _DEFAULT_CONTENT_TYPE,
            data=data,
            source="complaint",
            source_ref=complaint_id,
            actor_user_id=actor_user_id,
        )

        image = self._repo.add_image(
            ComplaintImage(
                society_id=society_id,
                complaint_id=complaint_id,
                kind=KIND_REPORT,
                vault_document_id=document.id,
                added_by=actor_user_id,
            )
        )

        AuditService(self._session).record(
            action="complaint.image_added",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="complaint",
            entity_id=complaint_id,
            after={
                "image_id": image.id,
                "kind": KIND_REPORT,
                "vault_document_id": document.id,
            },
        )

        return self._to_out(society_id, image)

    def remove_report_image(
        self,
        society_id: int,
        complaint_id: int,
        image_id: int,
        *,
        actor_user_id: int,
    ) -> None:
        """Remove one's own report image while open -> Vault soft-delete (§4/§6).

        The complaint and the image (scoped to it) must exist (404); the image
        must be a ``report`` image (proof is not user-removable — a proof id here
        is treated as not-found for this route); the actor must be the raiser
        (403); the complaint must still be ``open`` (409). Removal soft-deletes the
        backing Vault document (moves it to Trash / stamps ``deleted_at``) and
        drops the ``complaint_images`` row. Audited ``complaint.image_removed``.
        """
        complaint = self._require_complaint(society_id, complaint_id)
        image = self._repo.get_image(complaint_id, image_id)
        # A proof image is not removable via this resident route — surface it as
        # not-found rather than leaking its existence (docs §4).
        if image is None or image.kind != KIND_REPORT:
            raise NotFoundError(
                "Report image not found.",
                details={"complaint_id": complaint_id, "image_id": image_id},
            )
        self._require_raiser(complaint, actor_user_id)
        self._require_open(complaint)

        vault_document_id = image.vault_document_id
        # Soft-delete the backing document (Vault Trash) BEFORE dropping the row,
        # so a Vault error aborts the whole removal (the txn rolls back).
        VaultService(self._session).delete_document(
            society_id, vault_document_id, actor_user_id=actor_user_id
        )
        self._repo.remove_image(image)

        AuditService(self._session).record(
            action="complaint.image_removed",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="complaint",
            entity_id=complaint_id,
            before={
                "image_id": image_id,
                "kind": KIND_REPORT,
                "vault_document_id": vault_document_id,
            },
        )

    # --- assembly ----------------------------------------------------------

    def _to_out(
        self, society_id: int, image: ComplaintImage
    ) -> ComplaintImageOut:
        """Shape a stored image + its signed Vault preview URL (docs §6)."""
        preview = vault_api.get_preview_url(
            self._session, society_id, image.vault_document_id
        )
        return ComplaintImageOut(
            id=image.id,
            kind=image.kind,
            vault_document_id=image.vault_document_id,
            preview_url=preview.url,
            created_at=image.created_at,
        )
