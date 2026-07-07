"""Storage provider — the single place business code obtains an ``ObjectStorage``.

Phase-0 stub: importable so services and the API facade can reference it, but the
concrete backends (:class:`MinIOStorage`, in-memory fake) and the selection logic
are built in Wave A. Tests override this via a fixture. The import of the concrete
backend is deferred into the function body so the module imports cleanly before
Wave A lands.
"""
from __future__ import annotations

from app.core.storage import ObjectStorage


def get_storage() -> ObjectStorage:
    """Return the process object-storage backend (MinIO in dev/prod).

    Wave A implements this (construct + cache a :class:`MinIOStorage` from
    ``settings``). Kept as a stub so Phase-0 imports succeed.
    """
    raise NotImplementedError(
        "get_storage() is implemented in Vault Wave A (storage layer)."
    )
