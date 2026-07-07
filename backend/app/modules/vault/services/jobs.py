"""Vault background jobs (docs/modules/vault.md §9) — Wave D.

Two scheduled jobs, registered in the worker by Wave D:
- :func:`purge_trash` (daily)      — permanently delete items past retention.
- :func:`reconcile_usage` (nightly) — re-sum ``vault_documents`` to fix drift.

Both open their own ``SessionLocal`` (worker context, no request) and commit. They
are frozen stubs the Wave D sub-agent implements.
"""
from __future__ import annotations

from app.modules.vault.schemas import DEFAULT_TRASH_RETENTION_DAYS


def purge_trash(retention_days: int = DEFAULT_TRASH_RETENTION_DAYS) -> dict[str, int]:
    """Permanent-delete trashed items whose ``deleted_at`` is past retention.

    Deletes MinIO objects, decrements ``used_bytes``, and drops the rows.
    Idempotent. Returns a small summary for logging.
    """
    raise NotImplementedError("Vault Wave D implements purge_trash.")


def reconcile_usage() -> dict[str, int]:
    """Re-sum ``vault_documents`` per society and correct ``used_bytes`` drift."""
    raise NotImplementedError("Vault Wave D implements reconcile_usage.")
