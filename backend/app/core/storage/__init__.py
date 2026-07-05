"""Object-storage interface (MinIO). STUB for the foundation.

The Vault module implements/consumes this later. Present now only so the shape
exists and config is wired; no foundation feature stores objects.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class ObjectStorage(ABC):
    """Swappable object store (MinIO now, S3 later). Wired by the Vault module."""

    @abstractmethod
    def put_object(self, key: str, data: bytes, content_type: str) -> None:  # pragma: no cover
        raise NotImplementedError

    @abstractmethod
    def presigned_get_url(self, key: str) -> str:  # pragma: no cover
        raise NotImplementedError
