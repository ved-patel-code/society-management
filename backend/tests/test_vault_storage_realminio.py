"""Real-MinIO storage tests — the ONLY file that talks to a live MinIO server.

SERIAL: run this file on its own (``-n0``), never under xdist parallelism,
because it exercises the real backend/bucket rather than the in-memory test
double. No ``storage_override`` fixture here — uploads must hit the real
MinIOStorage. Uses unique object keys (uuid4) per test so runs never collide.

Reachability: presigned GET URLs are signed against ``minio_public_endpoint``
(typically ``localhost:9000``), which is NOT reachable from inside the backend
container. We therefore never HTTP-fetch the signed URL from in-container code
(except when ``minio_public_endpoint`` is unset or equals the in-cluster
endpoint) — instead we verify stored bytes via a SECOND, independent in-cluster
Minio client (``stat_object``/``get_object``).
"""
from __future__ import annotations

import uuid

import pytest

minio = pytest.importorskip("minio")

from minio import Minio  # noqa: E402
from minio.error import S3Error  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.storage.minio_storage import MinIOStorage  # noqa: E402
from tests._vault_helpers import _admin_bearer, _create_folder, _setup  # noqa: E402


def _incluster_client() -> Minio:
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_root_user,
        secret_key=settings.minio_root_password,
        secure=settings.minio_secure,
    )


@pytest.fixture
def real_storage():
    """A real MinIOStorage; skip the whole test if MinIO is unreachable."""
    storage = MinIOStorage()
    try:
        storage._ensure_bucket()
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"Real MinIO unreachable: {exc}")
    return storage


def _uniq(name: str) -> str:
    return f"vaulttest/{uuid.uuid4().hex}/{name}"


def test_bucket_created_on_first_put(real_storage):
    key = _uniq("abc.txt")
    real_storage.put_object(key, b"abc", "text/plain")
    assert real_storage._client.bucket_exists(real_storage._bucket)


def test_put_then_stat_roundtrip_via_incluster(real_storage):
    key = _uniq("roundtrip.txt")
    real_storage.put_object(key, b"hello-roundtrip", "text/plain")
    client = _incluster_client()
    stat = client.stat_object(real_storage._bucket, key)
    assert stat.size == len(b"hello-roundtrip")
    resp = client.get_object(real_storage._bucket, key)
    try:
        assert resp.read() == b"hello-roundtrip"
    finally:
        resp.close()
        resp.release_conn()


def test_presigned_get_url_shape(real_storage):
    key = _uniq("f.pdf")
    real_storage.put_object(key, b"pdfbytes", "application/pdf")
    url = real_storage.presigned_get_url(
        key, download_name="f.pdf", inline=True
    )
    assert real_storage._bucket in url
    assert key in url
    assert "X-Amz-Signature" in url
    assert "response-content-disposition=inline" in url
    assert "f.pdf" in url


def test_presigned_get_url_download_disposition(real_storage):
    key = _uniq("f.pdf")
    real_storage.put_object(key, b"pdfbytes", "application/pdf")
    url = real_storage.presigned_get_url(
        key, download_name="f.pdf", inline=False
    )
    assert "response-content-disposition=attachment" in url
    assert "f.pdf" in url


def test_presigned_url_http_fetch_when_reachable(real_storage):
    if settings.minio_public_endpoint not in ("", settings.minio_endpoint):
        pytest.skip("minio_public_endpoint not reachable from inside the container")
    import httpx

    key = _uniq("reachable.txt")
    real_storage.put_object(key, b"reachable-bytes", "text/plain")
    url = real_storage.presigned_get_url(key, download_name="reachable.txt")
    resp = httpx.get(url, timeout=10)
    assert resp.status_code == 200
    assert resp.content == b"reachable-bytes"


def test_delete_object_then_absent(real_storage):
    key = _uniq("todelete.txt")
    real_storage.put_object(key, b"gone-soon", "text/plain")
    real_storage.delete_object(key)
    client = _incluster_client()
    with pytest.raises(S3Error) as exc_info:
        client.stat_object(real_storage._bucket, key)
    assert exc_info.value.code == "NoSuchKey"


def test_delete_missing_object_noop(real_storage):
    key = _uniq("never-existed.txt")
    real_storage.delete_object(key)  # must not raise


def test_full_http_upload_then_fetch(db, society, admin_user, superadmin, auth, real_storage):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    filename = f"{uuid.uuid4().hex}.pdf"
    resp = auth.client.post(
        "/vault/documents",
        headers=hdr,
        files={"file": (filename, b"real-minio-e2e", "application/pdf")},
        data={"folder_id": str(bills["id"])},
    )
    assert resp.status_code == 200, resp.text
    doc = resp.json()

    from app.modules.vault.models import VaultDocument

    row = db.get(VaultDocument, doc["id"])
    client = _incluster_client()
    obj = client.get_object(real_storage._bucket, row.storage_key)
    try:
        assert obj.read() == b"real-minio-e2e"
    finally:
        obj.close()
        obj.release_conn()

    dl = auth.client.get(f"/vault/documents/{doc['id']}/download", headers=hdr)
    assert dl.status_code == 200, dl.text
    body = dl.json()
    assert row.storage_key in body["url"]
    assert "X-Amz-Signature" in body["url"] or "Signature" in body["url"]
