"""Regression tests for Complaints (Module 5) — locks in specific code-review
fixes so they never silently regress.

Covers: the date_to filter's inclusive-end-of-day boundary, detail/status
survival when a proof document has been trashed out from under it, the
sequential per-kind image-cap enforcement (report=409 vs proof=422 asymmetry),
the literal LIKE-metacharacter escaping in ``q`` search, and the worker window
using a real instant (not a midnight-truncated date).
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select, text

from app.modules.complaints.models import Complaint, ComplaintImage
from app.modules.complaints.services.jobs import _run_for_societies
from app.modules.vault.models import VaultDocument

from tests._complaints_helpers import (
    FROZEN_TODAY,
    freeze_utcnow,
    owned_house_for,
    owner_login_bearer,
    raise_complaint,
    resolve_http,
    setup_complaints,
    trash_vault_document,
)
from tests._vault_helpers import storage_override  # noqa: F401  (fixture)

pytestmark = pytest.mark.usefixtures("storage_override")


def _category_id(auth, hdr, name="Plumbing") -> int:
    resp = auth.client.get("/complaints/categories", headers=hdr)
    assert resp.status_code == 200, resp.text
    for c in resp.json():
        if c["name"] == name:
            return c["id"]
    raise AssertionError(f"category {name!r} not seeded")


def test_date_to_filter_inclusive_of_end_day(
    auth, db, society, admin_user, superadmin, monkeypatch
):
    """A complaint created ON date_to is included; the next day is excluded."""
    freeze_utcnow(monkeypatch)
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    created = raise_complaint(
        auth, r_hdr, category_id=cat, title="today", description="y"
    )

    resp = auth.client.get(
        "/complaints", headers=hdr, params={"date_to": FROZEN_TODAY.isoformat()}
    )
    assert resp.status_code == 200, resp.text
    assert created["id"] in [it["id"] for it in resp.json()["items"]]

    excluded_day = (FROZEN_TODAY - timedelta(days=1)).isoformat()
    resp2 = auth.client.get(
        "/complaints", headers=hdr, params={"date_to": excluded_day}
    )
    assert created["id"] not in [it["id"] for it in resp2.json()["items"]]


def test_detail_survives_trashed_proof_document(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = raise_complaint(auth, r_hdr, category_id=cat, title="x", description="y")["id"]
    auth.client.post(
        f"/complaints/{cid}/status", headers=hdr, json={"to_status": "in_progress"}
    )
    resolved = resolve_http(
        auth, hdr, cid, note="fixed", files=[("p.jpg", b"p" * 20, "image/jpeg")]
    )
    proof_doc_id = resolved.json()["images"][0]["vault_document_id"]

    trash_vault_document(db, proof_doc_id)

    resp = auth.client.get(f"/complaints/{cid}", headers=hdr)
    assert resp.status_code == 200, resp.text
    proof_img = next(
        img for img in resp.json()["images"] if img["vault_document_id"] == proof_doc_id
    )
    assert proof_img["preview_url"] is None


def test_status_change_survives_trashed_proof_document(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = raise_complaint(auth, r_hdr, category_id=cat, title="x", description="y")["id"]
    auth.client.post(
        f"/complaints/{cid}/status", headers=hdr, json={"to_status": "in_progress"}
    )
    resolved = resolve_http(
        auth, hdr, cid, note="fixed", files=[("p.jpg", b"p" * 20, "image/jpeg")]
    )
    proof_doc_id = resolved.json()["images"][0]["vault_document_id"]
    trash_vault_document(db, proof_doc_id)

    resp = auth.client.post(
        f"/complaints/{cid}/status", headers=hdr, json={"to_status": "closed"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "closed"

    row = db.query(Complaint).filter(Complaint.id == cid).one()
    assert row.status == "closed"


def test_report_image_cap_sequential_enforced_409(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_complaints(
        db, society, admin_user, superadmin, auth, config={"max_report_images": 2}
    )
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = raise_complaint(auth, r_hdr, category_id=cat, title="x", description="y")["id"]

    for i in range(2):
        resp = auth.client.post(
            f"/complaints/{cid}/images",
            headers=r_hdr,
            files={"file": (f"r{i}.jpg", b"x" * 10, "image/jpeg")},
        )
        assert resp.status_code == 200, resp.text

    third = auth.client.post(
        f"/complaints/{cid}/images",
        headers=r_hdr,
        files={"file": ("r3.jpg", b"x" * 10, "image/jpeg")},
    )
    assert third.status_code == 409, third.text

    db.expire_all()
    rows = db.execute(
        select(ComplaintImage).where(ComplaintImage.complaint_id == cid)
    ).scalars().all()
    assert len(rows) == 2
    docs = db.execute(
        select(VaultDocument).where(VaultDocument.source_ref == cid)
    ).scalars().all()
    assert len(docs) == 2


def test_proof_image_cap_enforced_422(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(
        db, society, admin_user, superadmin, auth, config={"max_proof_images": 2}
    )
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = raise_complaint(auth, r_hdr, category_id=cat, title="x", description="y")["id"]
    auth.client.post(
        f"/complaints/{cid}/status", headers=hdr, json={"to_status": "in_progress"}
    )

    resp = resolve_http(
        auth,
        hdr,
        cid,
        note="too many",
        files=[
            ("1.jpg", b"a" * 10, "image/jpeg"),
            ("2.jpg", b"b" * 10, "image/jpeg"),
            ("3.jpg", b"c" * 10, "image/jpeg"),
        ],
    )
    assert resp.status_code == 422, resp.text

    # Nothing was uploaded (rejected BEFORE upload) — 0 rows, 0 vault docs.
    db.expire_all()
    rows = db.execute(
        select(ComplaintImage).where(ComplaintImage.complaint_id == cid)
    ).scalars().all()
    assert rows == []
    docs = db.execute(
        select(VaultDocument).where(VaultDocument.source_ref == cid)
    ).scalars().all()
    assert docs == []
    assert db.query(Complaint).filter(Complaint.id == cid).one().status == "in_progress"


def test_q_search_percent_is_literal(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    c1 = raise_complaint(
        auth, r_hdr, category_id=cat, title="50% done", description="y"
    )
    c2 = raise_complaint(auth, r_hdr, category_id=cat, title="plain", description="y")

    resp = auth.client.get("/complaints", headers=hdr, params={"q": "50%"})
    ids = [it["id"] for it in resp.json()["items"]]
    assert c1["id"] in ids
    assert c2["id"] not in ids

    # A bare '%' must not match "plain" (would if % were a live wildcard, since
    # '%' matches everything) — literal escaping means it matches only titles
    # that contain a literal '%' character.
    resp2 = auth.client.get("/complaints", headers=hdr, params={"q": "%"})
    ids2 = [it["id"] for it in resp2.json()["items"]]
    assert c2["id"] not in ids2
    assert c1["id"] in ids2


def test_q_search_underscore_is_literal(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    c1 = raise_complaint(auth, r_hdr, category_id=cat, title="a_b", description="y")
    c2 = raise_complaint(auth, r_hdr, category_id=cat, title="xy", description="y")

    resp = auth.client.get("/complaints", headers=hdr, params={"q": "a_b"})
    ids = [it["id"] for it in resp.json()["items"]]
    assert c1["id"] in ids
    assert c2["id"] not in ids

    # A bare '_' must not match "xy" (would if _ were a live single-char
    # wildcard) since 'xy' has no literal underscore.
    resp2 = auth.client.get("/complaints", headers=hdr, params={"q": "_"})
    ids2 = [it["id"] for it in resp2.json()["items"]]
    assert c2["id"] not in ids2
    assert c1["id"] in ids2


def test_worker_window_uses_real_instant_archives(
    auth, db, society, admin_user, superadmin, monkeypatch
):
    """closed N days+3h before now -> archived (real-instant precision, not a
    midnight-truncated date comparison)."""
    freeze_utcnow(monkeypatch)
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = raise_complaint(auth, r_hdr, category_id=cat, title="x", description="y")["id"]
    auth.client.post(
        f"/complaints/{cid}/status", headers=hdr, json={"to_status": "in_progress"}
    )
    resolve_http(auth, hdr, cid, note="fixed")
    resp = auth.client.post(
        f"/complaints/{cid}/status", headers=hdr, json={"to_status": "closed"}
    )
    from datetime import datetime

    closed_at = datetime.fromisoformat(resp.json()["closed_at"])

    later = closed_at + timedelta(days=15, hours=3)
    result = _run_for_societies(db, [society.id], later)
    db.expire_all()
    assert result["complaints_archived"] == 1
    assert db.query(Complaint).filter(Complaint.id == cid).one().status == "archived"


def test_worker_window_boundary_not_yet_due(
    auth, db, society, admin_user, superadmin, monkeypatch
):
    """closed N days-1h before now -> NOT archived (still inside the window)."""
    freeze_utcnow(monkeypatch)
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = raise_complaint(auth, r_hdr, category_id=cat, title="x", description="y")["id"]
    auth.client.post(
        f"/complaints/{cid}/status", headers=hdr, json={"to_status": "in_progress"}
    )
    resolve_http(auth, hdr, cid, note="fixed")
    resp = auth.client.post(
        f"/complaints/{cid}/status", headers=hdr, json={"to_status": "closed"}
    )
    from datetime import datetime

    closed_at = datetime.fromisoformat(resp.json()["closed_at"])

    just_before = closed_at + timedelta(days=15, hours=-1)
    result = _run_for_societies(db, [society.id], just_before)
    db.expire_all()
    assert result["complaints_archived"] == 0
    assert db.query(Complaint).filter(Complaint.id == cid).one().status == "closed"
