"""Object-storage interface (MinIO now, S3-swappable) — Vault module contract.

The foundation shipped this as a stub (``put_object`` + ``presigned_get_url``);
the Vault module (docs/modules/vault.md §3) extends it to the full contract it
needs and provides a concrete :class:`MinIOStorage` plus an in-memory fake for
tests. Business code depends only on this ABC, obtained via
:func:`app.core.storage.provider.get_storage`.

Design doc calls this ``StorageBackend``; the code name stays ``ObjectStorage``
(the name already wired into the foundation) — they are the same interface.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

# Default lifetime for a signed preview/download link. Short-lived on purpose:
# the backend authorizes the request, then hands out a URL good for a few minutes.
DEFAULT_PRESIGN_TTL_SECONDS = 300


class ObjectStorage(ABC):
    """Swappable object store (MinIO now, S3 later). Wired by the Vault module."""

    @abstractmethod
    def put_object(self, key: str, data: bytes, content_type: str) -> None:
        """Store ``data`` under ``key`` with the given content type (overwrites)."""
        raise NotImplementedError

    @abstractmethod
    def presigned_get_url(
        self,
        key: str,
        *,
        expires_seconds: int = DEFAULT_PRESIGN_TTL_SECONDS,
        download_name: str | None = None,
        inline: bool = False,
    ) -> str:
        """A short-TTL signed GET URL for ``key``.

        ``inline=True`` asks the browser to render in place (PDF/images);
        otherwise the response is an attachment. ``download_name`` sets the
        filename in the ``Content-Disposition`` header (defaults to the key's
        object name). The signed host is the public endpoint (see settings) so
        the URL resolves from outside the Docker network.
        """
        raise NotImplementedError

    @abstractmethod
    def delete_object(self, key: str) -> None:
        """Permanently remove ``key`` (idempotent — a missing key is a no-op)."""
        raise NotImplementedError
