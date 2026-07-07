"""Concrete MinIO-backed :class:`ObjectStorage` — Vault module storage layer.

Implements the storage contract (``app/core/storage/__init__.py``) against a
MinIO server using the official ``minio`` client. See docs/modules/vault.md §3
(storage keys / ``StorageBackend``) and §4 (presigned preview/download URLs).

Public-endpoint signing
-----------------------
Objects are stored via the in-cluster endpoint (``minio:9000``), but presigned
URLs are handed to a browser on the host, which cannot resolve that name. MinIO
signs the request **host**, so the signature is only valid for the host baked
into it — a naive string-replace of the host would break the signature. We
therefore keep TWO clients: one bound to ``settings.minio_endpoint`` for
put/delete, and (when a distinct public endpoint is configured) a second bound
to ``settings.minio_public_endpoint`` used solely to *sign* GET URLs, so the
host in the signature matches the host the browser hits.
"""
from __future__ import annotations

import datetime
import io
import threading

from minio import Minio
from minio.error import S3Error

from app.core.config import settings
from app.core.storage import DEFAULT_PRESIGN_TTL_SECONDS, ObjectStorage


class MinIOStorage(ObjectStorage):
    """MinIO implementation of the object-storage contract (docs vault.md §3)."""

    def __init__(self) -> None:
        # Pin the region explicitly. Without it, the minio SDK's presigning path
        # (``Minio._get_region``) issues a live ``GetBucketLocation`` HTTP round
        # trip against the client's OWN endpoint the first time a bucket is
        # signed against — which, for ``_signing_client`` below, is exactly the
        # public/browser-facing host that (per this module's own docstring) may
        # be unreachable from inside the container. A single-region self-hosted
        # MinIO has no real "location" to discover, so pinning the standard
        # default avoids that unnecessary (and here, unreachable) network call.
        _REGION = "us-east-1"

        # Client for in-cluster reads/writes (put/delete).
        self._client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_root_user,
            secret_key=settings.minio_root_password,
            secure=settings.minio_secure,
            region=_REGION,
        )
        # Separate client used ONLY for signing presigned GET URLs, bound to the
        # browser-reachable host so the signed host matches the URL the browser
        # hits. Falls back to the primary client when no distinct public
        # endpoint is configured.
        public = settings.minio_public_endpoint
        if public and public != settings.minio_endpoint:
            self._signing_client = Minio(
                public,
                access_key=settings.minio_root_user,
                secret_key=settings.minio_root_password,
                secure=settings.minio_secure,
                region=_REGION,
            )
        else:
            self._signing_client = self._client

        self._bucket = settings.minio_bucket
        self._bucket_ready = False
        self._bucket_lock = threading.Lock()

    # -- bucket bootstrap ---------------------------------------------------

    def _ensure_bucket(self) -> None:
        """Create the bucket on first use, exactly once (idempotent, thread-safe).

        Guarded by a lock and a ``_bucket_ready`` flag so put/presign can
        auto-create the bucket without a separate provisioning step. Tolerates
        the race where a concurrent caller (or another process) wins the create.
        """
        if self._bucket_ready:
            return
        with self._bucket_lock:
            if self._bucket_ready:
                return
            try:
                if not self._client.bucket_exists(self._bucket):
                    self._client.make_bucket(self._bucket)
            except S3Error as exc:
                # Lost the create race — another caller already owns the bucket.
                if exc.code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                    raise
            self._bucket_ready = True

    # -- contract -----------------------------------------------------------

    def put_object(self, key: str, data: bytes, content_type: str) -> None:
        """Store ``data`` under ``key`` (docs vault.md §4 upload). Overwrites."""
        self._ensure_bucket()
        self._client.put_object(
            self._bucket,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )

    def presigned_get_url(
        self,
        key: str,
        *,
        expires_seconds: int = DEFAULT_PRESIGN_TTL_SECONDS,
        download_name: str | None = None,
        inline: bool = False,
    ) -> str:
        """Short-TTL signed GET URL for ``key`` (docs vault.md §4).

        Signed against the public endpoint so it resolves from the host browser.
        ``inline=True`` renders in place (PDF/images); otherwise the browser
        downloads. ``download_name`` sets the ``Content-Disposition`` filename,
        defaulting to the object-name portion of ``key``.
        """
        self._ensure_bucket()
        filename = download_name or key.rsplit("/", 1)[-1]
        disposition = "inline" if inline else "attachment"
        return self._signing_client.presigned_get_object(
            self._bucket,
            key,
            expires=datetime.timedelta(seconds=expires_seconds),
            response_headers={
                "response-content-disposition": f'{disposition}; filename="{filename}"',
            },
        )

    def delete_object(self, key: str) -> None:
        """Remove ``key`` (docs vault.md §4). A missing object is a no-op."""
        self._ensure_bucket()
        try:
            self._client.remove_object(self._bucket, key)
        except S3Error as exc:
            if exc.code != "NoSuchKey":
                raise
