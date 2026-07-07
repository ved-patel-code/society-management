"""Vault background jobs (docs/modules/vault.md §9) — Wave D.

Two scheduled jobs, registered in the worker by Wave D:
- :func:`purge_trash` (daily)      — permanently delete items past retention.
- :func:`reconcile_usage` (nightly) — re-sum ``vault_documents`` to fix drift.

Both open their own ``SessionLocal`` (worker context, no request) and commit —
mirroring ``app/worker/jobs/cleanup.py``.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select

from app.common.time import utcnow
from app.core.db import SessionLocal
from app.core.storage.provider import get_storage
from app.modules.vault.models import SocietyStorageUsage, VaultDocument
from app.modules.vault.repository import VaultRepository
from app.modules.vault.schemas import DEFAULT_TRASH_RETENTION_DAYS
from app.platform.audit.service import AuditService

logger = logging.getLogger("app.worker.vault")


def purge_trash(retention_days: int = DEFAULT_TRASH_RETENTION_DAYS) -> dict[str, int]:
    """Permanent-delete trashed items whose ``deleted_at`` is past retention.

    Deletes MinIO objects, decrements each affected society's ``used_bytes``, and
    drops the rows. FK order: documents first (``folder_id`` FK), then folders
    deepest-first (``parent_id`` FK). Audits ``vault.trash_purged`` per society
    (actor = system). Idempotent — already-gone objects/rows are no-ops.
    """
    cutoff = utcnow() - timedelta(days=retention_days)
    session = SessionLocal()
    try:
        repo = VaultRepository(session)
        audit = AuditService(session)

        freed_by_society: dict[int, int] = {}

        docs = repo.documents_deleted_before(cutoff)
        for doc in docs:
            get_storage().delete_object(doc.storage_key)
            freed_by_society[doc.society_id] = (
                freed_by_society.get(doc.society_id, 0) + doc.size_bytes
            )
            repo.delete_document_row(doc)
        documents_purged = len(docs)
        # Flush document deletes before dropping their parent folders (FK order).
        session.flush()

        folders = repo.folders_deleted_before(cutoff)
        # Delete children before parents: deepest-first by parent-chain depth.
        # Flushed ONE ROW AT A TIME (not batched) — SQLAlchemy's flush() groups
        # same-table deletes into a single ``executemany`` ordered by primary
        # key, which silently discards our depth-descending Python sort and can
        # emit a parent's DELETE before its child's in the same batch, tripping
        # the (non-deferrable) parent_id FK. A flush per row preserves the
        # intended child-before-parent order.
        for folder in sorted(
            folders, key=lambda f: _folder_depth(repo, f), reverse=True
        ):
            repo.delete_folder_row(folder)
            session.flush()
        folders_purged = len(folders)

        total_freed = 0
        for society_id, freed in freed_by_society.items():
            usage = repo.get_or_create_usage(society_id, lock=True)
            usage.used_bytes = max(0, usage.used_bytes - freed)
            total_freed += freed
            audit.record(
                action="vault.trash_purged",
                actor_user_id=None,  # system
                society_id=society_id,
                entity_type="society",
                entity_id=society_id,
                after={"freed_bytes": freed},
            )

        session.commit()
        result = {
            "documents_purged": documents_purged,
            "folders_purged": folders_purged,
            "bytes_freed": total_freed,
        }
        logger.info("Vault trash purge: %s", result)
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _folder_depth(repo: VaultRepository, folder) -> int:  # type: ignore[no-untyped-def]
    """Depth of ``folder`` from root (cycle-guarded) — delete leaves first.

    Uses each folder's own ``society_id`` (purge spans all societies).
    """
    depth = 0
    cur = folder
    seen: set[int] = set()
    while cur is not None and cur.parent_id is not None and cur.id not in seen:
        seen.add(cur.id)
        depth += 1
        cur = repo.get_folder(cur.society_id, cur.parent_id)
    return depth


def reconcile_usage() -> dict[str, int]:
    """Re-sum ``vault_documents`` per society, correct ``used_bytes`` drift, and
    sweep orphan storage objects.

    Considers every society that has a ``society_storage_usage`` row OR any
    ``vault_documents`` (live or trashed). Recomputes the authoritative total
    (live + trashed bytes) and overwrites the stored value when it differs.

    Orphan sweep: object storage is not transactional with the DB, so a crash
    between ``put_object`` and the request commit can leave an object with no
    backing row (an orphan OBJECT — never an orphan row). For each society this
    lists the keys under ``societies/{id}/`` and deletes any that no live-or-
    trashed ``vault_documents`` row references. Idempotent.
    """
    session = SessionLocal()
    try:
        repo = VaultRepository(session)
        storage = get_storage()

        society_ids: set[int] = set()
        society_ids.update(
            session.execute(
                select(SocietyStorageUsage.society_id).distinct()
            ).scalars()
        )
        society_ids.update(
            session.execute(
                select(VaultDocument.society_id).distinct()
            ).scalars()
        )

        corrections = 0
        orphans_deleted = 0
        for society_id in society_ids:
            actual = repo.sum_all_document_bytes(society_id)
            usage = repo.get_or_create_usage(society_id)
            if usage.used_bytes != actual:
                usage.used_bytes = actual
                corrections += 1

            # Orphan-object sweep: delete stored keys with no backing row.
            referenced = repo.all_storage_keys(society_id)
            for key in storage.list_keys(f"societies/{society_id}/"):
                if key not in referenced:
                    storage.delete_object(key)
                    orphans_deleted += 1

        session.commit()
        result = {
            "societies_reconciled": len(society_ids),
            "corrections": corrections,
            "orphans_deleted": orphans_deleted,
        }
        logger.info("Vault usage reconcile: %s", result)
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
