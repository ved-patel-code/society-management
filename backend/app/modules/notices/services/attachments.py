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

from app.modules.notices.repository import NoticeRepository
from app.modules.notices.schemas import NoticeDetailOut


class AttachmentsService:
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

        Returns the updated notice detail (with the new attachment).
        """
        raise NotImplementedError

    async def remove_attachment(
        self,
        society_id: int,
        notice_id: int,
        attachment_id: int,
        *,
        actor_user_id: int,
    ) -> None:
        """Remove an attachment (Vault soft-delete + drop row) (admin) (§4/§7)."""
        raise NotImplementedError
