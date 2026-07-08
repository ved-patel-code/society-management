"""Notice attachments concern — WAVE C (docs/modules/notice-board.md §4/§6/§7).

Owns the Vault-backed attachment routes:
- ``POST   /notices/{id}/attachments``               add (multipart → Vault).
- ``DELETE /notices/{id}/attachments/{attachmentId}`` remove.

Business rules Wave C enforces (docs §4/§7):
- Add: file the bytes into the notice's ``Notices/<notice id>/`` system folder
  via ``vault_api.ensure_notice_folder`` + ``vault_api.store_document(...,
  source='notice', source_ref=notice_id)``, then insert a ``notice_attachments``
  row. NO count cap — bounded only by the society's Vault quota. Vault's 413
  (quota) / 415 (denied type) propagate unchanged (the whole request rolls back,
  no orphan row). Audit ``notice.attachment_added``.
- Remove: soft-delete the Vault document (Vault Trash) BEFORE dropping the local
  row, so a Vault error rolls the whole removal back (no dangling document /
  orphan row). Missing attachment → 404. Audit ``notice.attachment_removed``.

Both admin-only (``notices.publish``) and additionally require the ``vault``
module (route-gated). Match the complaints multipart convention exactly (keep the
router/service async-ness consistent — never leave an unawaited coroutine).

FROZEN STUBS: Wave C fills the bodies, editing only THIS file + its own test file.
"""
from __future__ import annotations

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.common.errors import NotFoundError
from app.modules.notices.models import NoticeAttachment
from app.modules.notices.repository import NoticeRepository
from app.modules.notices.schemas import NoticeDetailOut
from app.modules.notices.services import support
from app.modules.vault import api as vault_api
from app.modules.vault.service import VaultService
from app.platform.audit.service import AuditService

# Fallbacks for a multipart part that arrives without a filename / content-type
# (Vault sanitizes the name and the extension denylist is authoritative anyway).
_DEFAULT_FILENAME = "attachment"
_DEFAULT_CONTENT_TYPE = "application/octet-stream"


class AttachmentsService:
    """Notice attachment lifecycle: add (→ Vault) + remove (Vault soft-delete).

    Although the router ``await``s these methods, the ``UploadFile`` bytes are
    read SYNCHRONOUSLY off the underlying spooled file object (``file.file.read()``),
    exactly like the complaints image service — never ``await file.read()``. A
    stray awaited coroutine left dangling poisons the request's session; reading
    synchronously keeps the write on the caller's transaction cleanly.
    """

    def __init__(self, session: Session, repo: NoticeRepository) -> None:
        self._session = session
        self._repo = repo

    async def add_attachment(
        self,
        society_id: int,
        notice_id: int,
        file: UploadFile,
        *,
        actor_user_id: int,
    ) -> NoticeDetailOut:
        """File an attachment into the notice's Vault folder (admin) (§4/§7).

        The notice must exist (404). The row is locked (``FOR UPDATE``) so
        concurrent attachment mutation of the same notice serializes. The bytes
        are filed into the notice's ``Notices/<notice id>/`` system folder (auto-
        created) via ``ensure_notice_folder`` + ``store_document(source='notice')``,
        then a ``notice_attachments`` row is written. There is NO count cap —
        attachments are bounded only by the society's Vault quota. Vault's own 413
        (quota) / 415 (denied type) propagate untouched, rolling back the whole
        request so no orphan row survives. Audited ``notice.attachment_added``.

        Returns the updated notice detail (with the new attachment).
        """
        notice = self._repo.get_notice(society_id, notice_id, lock=True)
        if notice is None:
            raise NotFoundError(
                "Notice not found.", details={"notice_id": notice_id}
            )

        data = file.file.read()

        # File into the notice's Vault folder (auto-created), then record the
        # link. Vault raises 413/415 here — let them surface to the caller.
        folder = vault_api.ensure_notice_folder(
            self._session,
            society_id,
            notice_id,
            actor_user_id=actor_user_id,
        )
        document = vault_api.store_document(
            self._session,
            society_id,
            folder.id,
            filename=file.filename or _DEFAULT_FILENAME,
            content_type=file.content_type or _DEFAULT_CONTENT_TYPE,
            data=data,
            source="notice",
            source_ref=notice_id,
            actor_user_id=actor_user_id,
        )

        self._repo.add_attachment(
            NoticeAttachment(
                society_id=society_id,
                notice_id=notice_id,
                vault_document_id=document.id,
                added_by=actor_user_id,
            )
        )

        AuditService(self._session).record(
            action="notice.attachment_added",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="notice",
            entity_id=notice_id,
            after={
                "notice_id": notice_id,
                "vault_document_id": document.id,
            },
        )

        return support.assemble_detail(
            self._session,
            self._repo,
            notice,
            is_read=self._repo.has_read(society_id, notice_id, actor_user_id),
        )

    async def remove_attachment(
        self,
        society_id: int,
        notice_id: int,
        attachment_id: int,
        *,
        actor_user_id: int,
    ) -> None:
        """Remove an attachment (Vault soft-delete + drop row) (admin) (§4/§7).

        The attachment (scoped to its notice + society) must exist (404). The
        backing Vault document is soft-deleted (moved to Trash / stamps
        ``deleted_at``) BEFORE the ``notice_attachments`` row is dropped, so a
        Vault error aborts the whole removal (the txn rolls back — no dangling
        document / orphan row). Audited ``notice.attachment_removed``.
        """
        attachment = self._repo.get_attachment(
            society_id, notice_id, attachment_id
        )
        if attachment is None:
            raise NotFoundError(
                "Attachment not found.",
                details={"notice_id": notice_id, "attachment_id": attachment_id},
            )

        vault_document_id = attachment.vault_document_id
        # Soft-delete the backing document (Vault Trash) BEFORE dropping the row,
        # so a Vault error aborts the whole removal (the txn rolls back).
        VaultService(self._session).delete_document(
            society_id, vault_document_id, actor_user_id=actor_user_id
        )
        self._repo.delete_attachment(attachment)

        AuditService(self._session).record(
            action="notice.attachment_removed",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="notice",
            entity_id=notice_id,
            before={"vault_document_id": vault_document_id},
        )
