"""Complaint status workflow concern — WAVE C (docs/modules/complaints.md §3/§4/§6).

Owns the ADMIN status state machine (``POST /complaints/{id}/status``) AND the
resolve-with-proof flow. The admin may drive ``open -> in_progress``,
``in_progress -> resolved``, ``resolved -> closed``, and the reopen
``resolved -> in_progress`` — validated via ``support.assert_transition_allowed``
+ this service's admin-actor scoping (never ``withdrawn``/``archived``). Every
transition writes its timeline row through ``support.record_transition`` (uniform
write + timestamp stamping) and emits ``complaint.status_changed``; audits
``complaint.status_changed``.

RESOLVE is special (user decision): proof images are attached ONLY at the
``in_progress -> resolved`` transition — the resolve call carries the solution
``note`` + up to ``max_proof_images`` proof photos, filed to the Vault
(``ensure_house_folder(kind='complaints')`` + ``store_document(source='complaint')``)
and recorded in ``complaint_images(kind='proof')``. Proof is LOCKED afterward
(no add/remove once resolved). Audits ``complaint.image_added`` (proof).

FROZEN STUBS: Wave C fills the bodies, editing only THIS file + its own test file.
The transition table + the ``record_transition`` write choke-point already live in
``support.py``; the Vault reach is ``app.modules.vault.api``.
"""
from __future__ import annotations

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.common.errors import NotFoundError, ValidationError
from app.modules.complaints.models import Complaint, ComplaintImage
from app.modules.complaints.repository import ComplaintRepository
from app.modules.complaints.schemas import (
    KIND_PROOF,
    STATUS_RESOLVED,
    ComplaintDetailOut,
    StatusChangeRequest,
)
from app.modules.complaints.services import support
from app.modules.complaints import events
from app.modules.vault import api as vault_api
from app.platform.audit.service import AuditService

_ACTION_STATUS_CHANGED = "complaint.status_changed"
_ACTION_IMAGE_ADDED = "complaint.image_added"
_ENTITY = "complaint"


class StatusService:
    def __init__(self, session: Session, repo: ComplaintRepository) -> None:
        self._session = session
        self._repo = repo

    def change_status(
        self,
        society_id: int,
        complaint_id: int,
        req: StatusChangeRequest,
        *,
        actor_user_id: int,
    ) -> ComplaintDetailOut:
        """Admin transition for the NON-resolve edges (§3/§4/§6).

        Handles ``open -> in_progress``, ``resolved -> closed``, and the reopen
        ``resolved -> in_progress``. The ``in_progress -> resolved`` edge is served
        by :meth:`resolve` (multipart, carries proof images) — routing ``resolved``
        here is rejected with guidance to use the resolve route.
        """
        complaint = self._repo.get_complaint(society_id, complaint_id)
        if complaint is None:
            raise NotFoundError(
                "Complaint not found.", details={"complaint_id": complaint_id}
            )

        # ``req.to_status`` is already constrained to ADMIN_TARGET_STATUSES by the
        # schema. Resolving carries proof images and therefore MUST go through the
        # multipart resolve route — reject it here with guidance.
        if req.to_status == STATUS_RESOLVED:
            raise ValidationError(
                "Resolve a complaint via POST /complaints/{id}/resolve so proof "
                "images can be attached.",
                details={"complaint_id": complaint_id, "to_status": req.to_status},
            )

        from_status = complaint.status
        # Edge legality (actor-independent) — 409 if illegal from the current state.
        support.assert_transition_allowed(from_status, req.to_status)

        support.record_transition(
            self._repo,
            complaint,
            to_status=req.to_status,
            note=req.note,
            changed_by=actor_user_id,
        )

        AuditService(self._session).record(
            action=_ACTION_STATUS_CHANGED,
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type=_ENTITY,
            entity_id=complaint.id,
            before={"from_status": from_status},
            after={"to_status": req.to_status, "note": req.note},
        )
        events.emit_status_changed(
            {
                "complaint_id": complaint.id,
                "house_id": complaint.house_id,
                "raised_by": complaint.raised_by,
                "from_status": from_status,
                "to_status": req.to_status,
                "note": req.note,
                "reference": complaint.reference,
            },
            session=self._session,
        )
        return support.assemble_detail(self._session, self._repo, complaint)

    async def resolve(
        self,
        society_id: int,
        complaint_id: int,
        *,
        note: str | None,
        images: list[UploadFile],
        actor_user_id: int,
    ) -> ComplaintDetailOut:
        """Resolve ``in_progress -> resolved`` with a solution note + proof images.

        Enforces the ``max_proof_images`` cap BEFORE upload; files each image to the
        Vault under the house's ``Complaints/<reference>`` folder; records
        ``complaint_images(kind='proof')``; writes the resolved transition (note)
        via ``support.record_transition``; emits ``complaint.status_changed``.
        Proof images cannot be added or removed after this call (§4, user decision).

        Async because the router hands over the raw ``UploadFile`` objects; the
        bytes are read here (``await file.read()``).
        """
        # Lock the complaint row: the proof-image cap is a read-check-insert, so
        # serialize concurrent resolves against each other (no over-committing past
        # max_proof_images) — a code-review finding.
        complaint = self._repo.get_complaint(
            society_id, complaint_id, lock=True
        )
        if complaint is None:
            raise NotFoundError(
                "Complaint not found.", details={"complaint_id": complaint_id}
            )

        from_status = complaint.status
        # Only ``in_progress -> resolved`` is legal here (else 409).
        support.assert_transition_allowed(from_status, STATUS_RESOLVED)

        # Drop empty multipart parts (a part with no filename carries no file).
        files = [f for f in images if f is not None and f.filename]

        # Enforce the per-kind cap BEFORE any upload so a rejected request never
        # leaves partial proof documents in the Vault (§4).
        cfg = support.load_config(self._session, society_id)
        if len(files) > cfg.max_proof_images:
            raise ValidationError(
                "Too many proof images.",
                details={
                    "provided": len(files),
                    "max_proof_images": cfg.max_proof_images,
                },
            )

        audit = AuditService(self._session)

        # File each proof photo into the Vault, then record its complaint_images
        # row. Vault raises 413 (quota) / 415 (denied type) — let those propagate.
        for file in files:
            data = await file.read()
            folder = vault_api.ensure_house_folder(
                self._session,
                society_id,
                complaint.house_id,
                kind=vault_api.HOUSE_FOLDER_COMPLAINTS,
                actor_user_id=actor_user_id,
            )
            doc = vault_api.store_document(
                self._session,
                society_id,
                folder.id,
                filename=file.filename or "proof",
                content_type=file.content_type or "application/octet-stream",
                data=data,
                source="complaint",
                source_ref=complaint.id,
                actor_user_id=actor_user_id,
            )
            self._repo.add_image(
                ComplaintImage(
                    society_id=society_id,
                    complaint_id=complaint.id,
                    kind=KIND_PROOF,
                    vault_document_id=doc.id,
                    added_by=actor_user_id,
                )
            )
            audit.record(
                action=_ACTION_IMAGE_ADDED,
                actor_user_id=actor_user_id,
                society_id=society_id,
                entity_type=_ENTITY,
                entity_id=complaint.id,
                after={"kind": KIND_PROOF, "vault_document_id": doc.id},
            )

        # Stamp resolved_at + write the timeline row (the solution note).
        support.record_transition(
            self._repo,
            complaint,
            to_status=STATUS_RESOLVED,
            note=note,
            changed_by=actor_user_id,
        )
        audit.record(
            action=_ACTION_STATUS_CHANGED,
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type=_ENTITY,
            entity_id=complaint.id,
            before={"from_status": from_status},
            after={"to_status": STATUS_RESOLVED, "note": note},
        )
        events.emit_status_changed(
            {
                "complaint_id": complaint.id,
                "house_id": complaint.house_id,
                "raised_by": complaint.raised_by,
                "from_status": from_status,
                "to_status": STATUS_RESOLVED,
                "note": note,
                "reference": complaint.reference,
            },
            session=self._session,
        )
        return support.assemble_detail(self._session, self._repo, complaint)

