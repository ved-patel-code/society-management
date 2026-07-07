"""Storage provider — the single place business code obtains an ``ObjectStorage``.

Business code calls :func:`get_storage` and depends only on the
:class:`ObjectStorage` ABC (docs/modules/vault.md §3/§7). In dev/prod this
returns a process-cached :class:`MinIOStorage`. Tests inject an
:class:`InMemoryStorage` via :func:`set_storage_override` (cleared with
:func:`reset_storage_override`) so they never touch a real MinIO server.

The concrete backend is imported lazily inside :func:`get_storage` to keep this
module cheap to import and free of a hard MinIO dependency at import time.
"""
from __future__ import annotations

from functools import lru_cache

from app.core.storage import ObjectStorage

# Test seam: when set, :func:`get_storage` returns this instead of the real
# MinIO backend. Managed exclusively via the setter/reset helpers below.
_override: ObjectStorage | None = None


@lru_cache
def _cached_minio() -> ObjectStorage:
    """Process-singleton MinIO backend (built once, on first real use)."""
    from app.core.storage.minio_storage import MinIOStorage

    return MinIOStorage()


def get_storage() -> ObjectStorage:
    """Return the process object-storage backend.

    Returns the test override if one is installed, otherwise the cached
    :class:`MinIOStorage` singleton (constructed lazily on first call).
    """
    if _override is not None:
        return _override
    return _cached_minio()


def set_storage_override(storage: ObjectStorage) -> None:
    """Install a storage backend (e.g. :class:`InMemoryStorage`) for tests."""
    global _override
    _override = storage


def reset_storage_override() -> None:
    """Clear any test override so :func:`get_storage` uses the real backend."""
    global _override
    _override = None
