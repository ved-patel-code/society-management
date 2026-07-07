"""Vault public inter-module contract (docs/modules/vault.md §7).

The ONLY surface other modules import. House & Occupancy (ID proofs), Complaints
(complaint images), and Notice Board (attachments) call these to file bytes into
the vault and to fetch signed URLs — they never touch vault tables directly
(docs/05 cross-module contracts). Each call takes the caller's request-scoped
``Session`` so the write joins the caller's transaction.

Thin delegators over :class:`VaultService`; no logic here.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.vault.models import VaultFolder
from app.modules.vault.schemas import DocumentOut, PresignedUrlOut, UsageOut
from app.modules.vault.service import VaultService

# File-kind for a house folder (docs §4): Proof/ (ID proofs) vs Complaints/.
HOUSE_FOLDER_PROOF = "proof"
HOUSE_FOLDER_COMPLAINTS = "complaints"


def ensure_house_folder(
    session: Session,
    society_id: int,
    house_id: int,
    *,
    kind: str,
    actor_user_id: int,
) -> VaultFolder:
    """Ensure ``Houses/<house>/Proof`` or ``/Complaints`` exists (docs §4/§7)."""
    return VaultService(session).folders.ensure_house_folder(
        society_id, house_id, kind=kind, actor_user_id=actor_user_id
    )


def ensure_notice_folder(
    session: Session, society_id: int, notice_id: int, *, actor_user_id: int
) -> VaultFolder:
    """Ensure the society-level ``Notices/<notice>`` folder exists (docs §4/§7)."""
    return VaultService(session).folders.ensure_notice_folder(
        society_id, notice_id, actor_user_id=actor_user_id
    )


def store_document(
    session: Session,
    society_id: int,
    folder_id: int,
    *,
    filename: str,
    content_type: str,
    data: bytes,
    source: str,
    source_ref: int | None,
    actor_user_id: int,
) -> DocumentOut:
    """Store a file into a (usually system) folder and return it (docs §7).

    ``source`` is ``id_proof`` | ``complaint`` | ``notice`` for consumer calls.
    Enforces denylist + quota exactly like a manual upload.
    """
    return VaultService(session).upload_document(
        society_id,
        folder_id,
        filename=filename,
        content_type=content_type,
        data=data,
        actor_user_id=actor_user_id,
        source=source,
        source_ref=source_ref,
    )


def get_preview_url(
    session: Session, society_id: int, document_id: int
) -> PresignedUrlOut:
    """Signed inline URL for a stored document (docs §7)."""
    return VaultService(session).preview_url(society_id, document_id)


def get_download_url(
    session: Session, society_id: int, document_id: int
) -> PresignedUrlOut:
    """Signed download URL for a stored document (docs §7)."""
    return VaultService(session).download_url(society_id, document_id)


def usage(session: Session, society_id: int) -> UsageOut:
    """Current storage usage vs limit for the society (docs §7)."""
    return VaultService(session).usage(society_id)
