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

from app.modules.complaints.repository import ComplaintRepository
from app.modules.complaints.schemas import ComplaintImageOut


class ImagesService:
    def __init__(self, session: Session, repo: ComplaintRepository) -> None:
        self._session = session
        self._repo = repo

    def add_report_image(
        self,
        society_id: int,
        complaint_id: int,
        file: UploadFile,
        *,
        actor_user_id: int,
    ) -> ComplaintImageOut:
        """Add a report image to an OPEN complaint (raiser-only, capped) (§4/§6)."""
        raise NotImplementedError("Wave D: ImagesService.add_report_image")

    def remove_report_image(
        self,
        society_id: int,
        complaint_id: int,
        image_id: int,
        *,
        actor_user_id: int,
    ) -> None:
        """Remove one's own report image while open -> Vault soft-delete (§4/§6)."""
        raise NotImplementedError("Wave D: ImagesService.remove_report_image")
