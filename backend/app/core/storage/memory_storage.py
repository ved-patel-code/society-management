"""In-memory :class:`ObjectStorage` fake for tests — Vault module storage layer.

A dict-backed stand-in for :class:`MinIOStorage` (docs/modules/vault.md §3) so
the suite never touches a real MinIO server. Presigned URLs are deterministic
fake strings (no real signing). Injected via
:func:`app.core.storage.provider.set_storage_override`. Extra helpers
(:meth:`exists`, :meth:`get`) let tests assert on stored bytes.
"""
from __future__ import annotations

from app.core.config import settings
from app.core.storage import DEFAULT_PRESIGN_TTL_SECONDS, ObjectStorage


class InMemoryStorage(ObjectStorage):
    """Dict-backed object store for tests (docs vault.md §3). Not for production."""

    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, str]] = {}
        self._bucket = settings.minio_bucket

    # -- contract -----------------------------------------------------------

    def put_object(self, key: str, data: bytes, content_type: str) -> None:
        """Store ``data`` under ``key`` (overwrites)."""
        self._objects[key] = (data, content_type)

    def presigned_get_url(
        self,
        key: str,
        *,
        expires_seconds: int = DEFAULT_PRESIGN_TTL_SECONDS,
        download_name: str | None = None,
        inline: bool = False,
    ) -> str:
        """Deterministic fake signed URL (no real signing) for assertions."""
        filename = download_name or key.rsplit("/", 1)[-1]
        return (
            f"memory://{self._bucket}/{key}"
            f"?inline={inline}&name={filename}&exp={expires_seconds}"
        )

    def delete_object(self, key: str) -> None:
        """Remove ``key`` (a missing object is a no-op)."""
        self._objects.pop(key, None)

    # -- test helpers -------------------------------------------------------

    def exists(self, key: str) -> bool:
        """True if ``key`` is currently stored."""
        return key in self._objects

    def get(self, key: str) -> bytes | None:
        """Stored bytes for ``key``, or ``None`` if absent."""
        entry = self._objects.get(key)
        return entry[0] if entry is not None else None
