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

from app.modules.complaints.repository import ComplaintRepository
from app.modules.complaints.schemas import ComplaintDetailOut, StatusChangeRequest


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
        raise NotImplementedError("Wave C: StatusService.change_status")

    def resolve(
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
        """
        raise NotImplementedError("Wave C: StatusService.resolve")
