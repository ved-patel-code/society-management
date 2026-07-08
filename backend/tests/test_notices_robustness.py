"""Robustness / failure-injection / adversarial-input tests for Notice Board
(Module 6).

Covers an XSS payload battery against ``body`` on create/edit/publish (the
sanitizer strips every tag not on its allow-list — see
``app/common/html_sanitizer.py``: no ``script``/``img``/``iframe``/``svg``/
``style``, no event-handler attributes, no ``javascript:``/``data:``/
``vbscript:`` URL schemes), Vault failure-injection (415/413) rolling back the
whole request (no orphan row / state change / audit row), malformed-create
422s, nonexistent-id 404s across every resource-scoped route, and read
idempotency under a double-open.

UPDATED BEHAVIOR (supersedes the original ambiguity flag #3): ``title`` is now
ALSO sanitized — ``support.sanitize_title`` strips ALL markup (via
``common/html_sanitizer.sanitize_plain_text``, an ``nh3.clean`` call with an
empty tag allow-list), keeping only the visible text, on BOTH create and edit.
A title that is markup-only (e.g. ``<script></script>``) goes blank after
stripping and is rejected with a 422 (``"Title must not be blank after
removing markup."``). This module previously left the title unsanitized; that
gap has been closed in the app code — the tests below assert the NEW behavior
(title markup stripped, blank-after-strip -> 422), not the old verbatim-title
behavior.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.modules.notices.models import Notice, NoticeAttachment, NoticeRead
from app.modules.vault.models import VaultDocument

from tests._notices_helpers import (
    EXE_BYTES,
    PNG_BYTES,
    add_attachment_http,
    audit_actions,
    create_notice_http,
    owned_house_for,
    owner_login_bearer,
    setup_notices,
    society_with_tiny_quota,
)
from tests._vault_helpers import storage_override  # noqa: F401  (fixture)

pytestmark = pytest.mark.usefixtures("storage_override")


XSS_PAYLOADS = [
    '<script>alert(1)</script>',
    '<img src=x onerror="alert(1)">',
    '<a href="javascript:alert(1)">click</a>',
    '<a href="data:text/html,<script>alert(1)</script>">click</a>',
    '<svg onload="alert(1)">',
    '<div onmouseover="alert(1)">hover</div>',
    '<style>body{background:url("javascript:alert(1)")}</style>',
    '<scr<script>ipt>alert(1)</scr</script>ipt>',
    '<p onclick="alert(1)"><iframe src="evil"></iframe></p>',
    '<a href="vbscript:alert(1)">click</a>',
]


def _forbidden(text_lower: str) -> list[str]:
    """Any forbidden marker still present in the (lowercased) text."""
    markers = [
        "<script", "<img", "<iframe", "<svg", "<style",
        "javascript:", "data:text/html", "vbscript:",
    ]
    found = [m for m in markers if m in text_lower]
    # Event-handler attributes (on*=) — check a representative sample.
    for handler in ("onerror", "onload", "onmouseover", "onclick"):
        if handler in text_lower:
            found.append(handler)
    return found


# Each XSS payload is embedded in REAL surrounding content — the realistic
# injection scenario (an attacker slips a payload into an otherwise-legitimate
# notice), which also guarantees the body has surviving visible text so the
# empty-content guard (``sanitize_body``) doesn't 422 a body that is nothing but
# a stripped-out tag.
def _body_with(payload: str) -> str:
    return f"<p>Legitimate notice content.</p>{payload}<p>More text.</p>"


@pytest.mark.parametrize("payload", XSS_PAYLOADS)
def test_xss_neutralized_on_create(auth, db, society, admin_user, superadmin, payload):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    resp = create_notice_http(
        auth.client, hdr, title="XSS test", body=_body_with(payload), publish=True
    )
    assert resp.status_code == 200, resp.text
    body_lower = resp.json()["body"].lower()
    assert _forbidden(body_lower) == [], (payload, body_lower)
    # The legitimate surrounding content survives the sanitize.
    assert "legitimate notice content." in body_lower

    row = db.query(Notice).filter(Notice.id == resp.json()["id"]).one()
    assert _forbidden(row.body.lower()) == [], (payload, row.body)


@pytest.mark.parametrize("payload", XSS_PAYLOADS)
def test_xss_neutralized_on_edit(auth, db, society, admin_user, superadmin, payload):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    created = create_notice_http(
        auth.client, hdr, title="clean", body="<p>clean</p>", publish=True
    )
    nid = created.json()["id"]

    resp = auth.client.patch(
        f"/notices/{nid}", headers=hdr, json={"body": _body_with(payload)}
    )
    assert resp.status_code == 200, resp.text
    body_lower = resp.json()["body"].lower()
    assert _forbidden(body_lower) == [], (payload, body_lower)

    row = db.query(Notice).filter(Notice.id == nid).one()
    assert _forbidden(row.body.lower()) == [], (payload, row.body)


def test_xss_neutralized_on_publish_on_create(auth, db, society, admin_user, superadmin):
    """A published notice's feed-visible title AND body are both neutralized:
    the title keeps only its markup-stripped text ("Hello"), the body keeps
    only its safe-allow-list content ("hi")."""
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    xss_title = "<script>alert('title')</script>Hello"
    xss_body = '<p>hi</p><script>alert("body")</script>'

    resp = create_notice_http(
        auth.client, hdr, title=xss_title, body=xss_body, publish=True
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Body: sanitized (no script tag survives; safe tags/text kept).
    assert "<script" not in body["body"].lower()
    assert "hi" in body["body"]

    # Title: ALSO sanitized (all markup stripped, keeping only the visible
    # text) — no tag survives, only "Hello" remains.
    assert "<script" not in body["title"].lower()
    assert body["title"] == "Hello"

    row = db.query(Notice).filter(Notice.id == body["id"]).one()
    assert row.title == "Hello"
    assert "<script" not in row.body.lower()


def test_title_that_is_only_markup_422(auth, db, society, admin_user, superadmin):
    """A title that is ENTIRELY markup (e.g. ``<script></script>``) goes blank
    after stripping -> 422 (``sanitize_title``'s blank-after-strip guard)."""
    hdr = setup_notices(db, society, admin_user, superadmin, auth)

    resp = create_notice_http(
        auth.client, hdr, title="<script></script>", body="<p>a</p>"
    )
    assert resp.status_code == 422, resp.text

    resp2 = create_notice_http(auth.client, hdr, title="<b></b>", body="<p>a</p>")
    assert resp2.status_code == 422, resp2.text


def test_title_markup_stripped_on_edit(auth, db, society, admin_user, superadmin):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    nid = create_notice_http(
        auth.client, hdr, title="clean", body="<p>a</p>", publish=True
    ).json()["id"]

    resp = auth.client.patch(
        f"/notices/{nid}", headers=hdr,
        json={"title": "<b>Bold</b> <script>alert(1)</script>Title"},
    )
    assert resp.status_code == 200, resp.text
    assert "<script" not in resp.json()["title"].lower()
    assert "<b>" not in resp.json()["title"].lower()
    assert "Bold" in resp.json()["title"]
    assert "Title" in resp.json()["title"]

    row = db.query(Notice).filter(Notice.id == nid).one()
    assert "<script" not in row.title.lower()


# ===========================================================================
# Vault failure injection — no orphan state survives a rejected attachment
# ===========================================================================


def test_attachment_denied_type_415_rolls_back_state_unchanged(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    created = create_notice_http(
        auth.client, hdr, title="x", body="<p>a</p>", publish=True
    )
    nid = created.json()["id"]
    before_updated_at = created.json()["updated_at"]
    before_status = created.json()["status"]

    resp = add_attachment_http(
        auth.client, hdr, nid, data=EXE_BYTES, filename="bad.exe",
        content_type="application/octet-stream",
    )
    assert resp.status_code == 415, resp.text

    rows = db.execute(
        select(NoticeAttachment).where(NoticeAttachment.notice_id == nid)
    ).scalars().all()
    assert rows == []

    db.expire_all()
    detail = auth.client.get(f"/notices/{nid}", headers=hdr).json()
    assert detail["status"] == before_status
    assert detail["updated_at"] == before_updated_at
    assert detail["attachments"] == []

    assert ("notice.attachment_added", "notice", nid) not in audit_actions(
        db, society.id
    )


def test_attachment_quota_413_rolls_back_state_unchanged(db, superadmin, auth):
    soc, _admin, hdr = society_with_tiny_quota(db, superadmin, auth, limit_bytes=8)
    created = create_notice_http(
        auth.client, hdr, title="x", body="<p>a</p>", publish=True
    )
    nid = created.json()["id"]
    before_updated_at = created.json()["updated_at"]

    resp = add_attachment_http(auth.client, hdr, nid, filename="big.png")
    assert resp.status_code == 413, resp.text

    rows = db.execute(
        select(NoticeAttachment).where(NoticeAttachment.notice_id == nid)
    ).scalars().all()
    assert rows == []
    docs = db.execute(
        select(VaultDocument).where(VaultDocument.source_ref == nid)
    ).scalars().all()
    assert docs == []

    db.expire_all()
    detail = auth.client.get(f"/notices/{nid}", headers=hdr).json()
    assert detail["updated_at"] == before_updated_at
    assert detail["attachments"] == []

    assert ("notice.attachment_added", "notice", nid) not in audit_actions(
        db, soc.id
    )


# ===========================================================================
# malformed create -> 422
# ===========================================================================


@pytest.mark.parametrize(
    "payload",
    [
        {"body": "x"},  # missing title
        {"title": "   ", "body": "x"},  # blank title
        {"title": "\t\n", "body": "x"},  # whitespace-only title
        {"title": "x"},  # missing body
        {"title": "", "body": "x"},  # empty-string title (min_length=1)
        {"title": "x", "body": ""},  # empty-string body (min_length=1)
        {"title": "x" * 201, "body": "x"},  # title over 200 chars
        {"title": "x", "body": "y" * 50_001},  # body over 50000 chars
    ],
)
def test_malformed_create_422(auth, db, society, admin_user, superadmin, payload):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    resp = auth.client.post("/notices", headers=hdr, json=payload)
    assert resp.status_code == 422, resp.text


def test_empty_content_body_rejected_422(
    auth, db, society, admin_user, superadmin
):
    """A body with no visible text once markup is stripped is a 422 (a notice
    must carry real content). ``support.sanitize_body`` rejects a blank-after-
    strip body — a whitespace-only ``"   "`` or a tags-only ``"<p></p>"`` —
    mirroring the title's blank guard (Pydantic's ``min_length=1`` can't see
    through whitespace/tags)."""
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    for empty in ("   ", "<p></p>", "<p>   </p>", "<br>"):
        resp = auth.client.post(
            "/notices", headers=hdr, json={"title": "x", "body": empty}
        )
        assert resp.status_code == 422, f"{empty!r} -> {resp.status_code}"


# ===========================================================================
# nonexistent ids -> 404
# ===========================================================================


def test_nonexistent_ids_404(auth, db, society, admin_user, superadmin):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    created = create_notice_http(
        auth.client, hdr, title="real", body="<p>a</p>", publish=True
    )
    real_nid = created.json()["id"]

    assert auth.client.get("/notices/999999", headers=hdr).status_code == 404
    assert auth.client.patch(
        "/notices/999999", headers=hdr, json={"title": "x"}
    ).status_code == 404
    assert auth.client.post("/notices/999999/publish", headers=hdr).status_code == 404
    assert auth.client.post("/notices/999999/withdraw", headers=hdr).status_code == 404
    assert (
        add_attachment_http(auth.client, hdr, 999999, filename="x.png").status_code
        == 404
    )
    assert auth.client.get("/notices/999999/receipts", headers=hdr).status_code == 404
    assert (
        auth.client.delete(
            f"/notices/{real_nid}/attachments/999999", headers=hdr
        ).status_code
        == 404
    )


# ===========================================================================
# concurrent/duplicate read idempotency
# ===========================================================================


def test_concurrent_duplicate_read_is_idempotent(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_notices(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="dup@x.com")
    r_hdr, reader = owner_login_bearer(auth, db, email="dup@x.com")
    nid = create_notice_http(
        auth.client, hdr, title="x", body="<p>a</p>", publish=True
    ).json()["id"]

    r1 = auth.client.get(f"/notices/{nid}", headers=r_hdr)
    r2 = auth.client.get(f"/notices/{nid}", headers=r_hdr)
    assert r1.status_code == 200 and r1.json()["is_read"] is True
    assert r2.status_code == 200 and r2.json()["is_read"] is True

    db.expire_all()
    count = (
        db.query(NoticeRead)
        .filter(NoticeRead.notice_id == nid, NoticeRead.user_id == reader.id)
        .count()
    )
    assert count == 1

    assert auth.client.post("/notices/read-all", headers=r_hdr).status_code == 204
    db.expire_all()
    count_after = (
        db.query(NoticeRead)
        .filter(NoticeRead.notice_id == nid, NoticeRead.user_id == reader.id)
        .count()
    )
    assert count_after == 1
