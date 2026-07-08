"""End-to-end lifecycle tests for Complaints (Module 5) — the full-gate arc.

Drives a complaint from raise through the whole legal state machine to
auto-archive via the REAL HTTP API (no ORM shortcuts for the transitions under
test), asserting DB state, the status timeline, the audit trail, Vault folder
placement, image counts, and emitted events at each step. Complements (does not
duplicate) the per-wave basic-case files.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text

from app.modules.complaints.models import (
    Complaint,
    ComplaintImage,
    ComplaintStatusHistory,
)
from app.modules.complaints.services.jobs import _run_for_societies
from app.modules.vault.models import VaultDocument

from tests._complaints_helpers import (
    FROZEN_TODAY,
    audit_actions,
    event_capture,  # noqa: F401  (fixture)
    freeze_utcnow,
    owned_house_for,
    owner_login_bearer,
    raise_complaint,
    resolve_http,
    setup_complaints,
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


def _get(db, complaint_id) -> Complaint:
    return db.query(Complaint).filter(Complaint.id == complaint_id).one()


def _history(db, complaint_id):
    return (
        db.query(ComplaintStatusHistory)
        .filter(ComplaintStatusHistory.complaint_id == complaint_id)
        .order_by(ComplaintStatusHistory.id)
        .all()
    )


def _images(db, complaint_id):
    return (
        db.query(ComplaintImage)
        .filter(ComplaintImage.complaint_id == complaint_id)
        .order_by(ComplaintImage.id)
        .all()
    )


def test_full_lifecycle_raise_to_archive(
    auth, db, society, admin_user, superadmin, monkeypatch
):
    """raise -> open -> in_progress -> resolved -> closed -> archived (worker)."""
    freeze_utcnow(monkeypatch)
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    hid = owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)

    created = raise_complaint(
        auth, r_hdr, category_id=cat, title="Leak", description="Tap leaks"
    )
    cid = created["id"]

    # Owner adds one report image.
    r1 = auth.client.post(
        f"/complaints/{cid}/images",
        headers=r_hdr,
        files={"file": ("leak.jpg", b"x" * 20, "image/jpeg")},
    )
    assert r1.status_code == 200, r1.text

    # Admin: open -> in_progress.
    resp = auth.client.post(
        f"/complaints/{cid}/status",
        headers=hdr,
        json={"to_status": "in_progress"},
    )
    assert resp.status_code == 200, resp.text

    # Admin resolves with 2 proof images.
    resp = resolve_http(
        auth,
        hdr,
        cid,
        note="Fixed the washer.",
        files=[
            ("before.jpg", b"a" * 20, "image/jpeg"),
            ("after.jpg", b"b" * 20, "image/jpeg"),
        ],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "resolved"

    # Admin: resolved -> closed.
    resp = auth.client.post(
        f"/complaints/{cid}/status", headers=hdr, json={"to_status": "closed"}
    )
    assert resp.status_code == 200, resp.text
    closed_at = _get(db, cid).closed_at
    assert closed_at is not None

    # Worker: run 15 days + 3 hours after close (default auto_archive_days=15).
    later = closed_at + timedelta(days=15, hours=3)
    result = _run_for_societies(db, [society.id], later)
    db.expire_all()
    assert result["complaints_archived"] == 1

    row = _get(db, cid)
    assert row.status == "archived"
    assert row.archived_at == later


def test_lifecycle_status_timeline_sequence(
    auth, db, society, admin_user, superadmin, monkeypatch
):
    freeze_utcnow(monkeypatch)
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = raise_complaint(
        auth, r_hdr, category_id=cat, title="Leak", description="Tap leaks"
    )["id"]

    auth.client.post(
        f"/complaints/{cid}/status", headers=hdr, json={"to_status": "in_progress"}
    )
    resolve_http(auth, hdr, cid, note="fixed")
    resp = auth.client.post(
        f"/complaints/{cid}/status", headers=hdr, json={"to_status": "closed"}
    )
    closed_at = resp.json()["closed_at"]
    from datetime import datetime

    closed_dt = datetime.fromisoformat(closed_at)
    later = closed_dt + timedelta(days=15, hours=3)
    _run_for_societies(db, [society.id], later)
    db.expire_all()

    hist = _history(db, cid)
    seq = [(h.from_status, h.to_status) for h in hist]
    assert seq == [
        (None, "open"),
        ("open", "in_progress"),
        ("in_progress", "resolved"),
        ("resolved", "closed"),
        ("closed", "archived"),
    ]
    # The worker's row has no actor.
    assert hist[-1].changed_by is None


def test_lifecycle_audit_trail_sequence(
    auth, db, society, admin_user, superadmin, monkeypatch
):
    freeze_utcnow(monkeypatch)
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = raise_complaint(
        auth, r_hdr, category_id=cat, title="Leak", description="Tap leaks"
    )["id"]

    auth.client.post(
        f"/complaints/{cid}/status", headers=hdr, json={"to_status": "in_progress"}
    )
    resolve_http(
        auth,
        hdr,
        cid,
        note="fixed",
        files=[
            ("a.jpg", b"a" * 20, "image/jpeg"),
            ("b.jpg", b"b" * 20, "image/jpeg"),
        ],
    )
    resp = auth.client.post(
        f"/complaints/{cid}/status", headers=hdr, json={"to_status": "closed"}
    )
    from datetime import datetime

    closed_dt = datetime.fromisoformat(resp.json()["closed_at"])
    later = closed_dt + timedelta(days=15, hours=3)
    _run_for_societies(db, [society.id], later)
    db.expire_all()

    actions = [
        a
        for a in audit_actions(db, society.id)
        if a[1] == "complaint" and a[2] == cid
    ]
    action_names = [a[0] for a in actions]
    assert action_names == [
        "complaint.created",
        "complaint.status_changed",
        "complaint.image_added",
        "complaint.image_added",
        "complaint.status_changed",
        "complaint.status_changed",
        "complaint.archived",
    ]

    # The archive row's actor is None (system).
    from app.platform.models import AuditLog

    archive_row = (
        db.query(AuditLog)
        .filter(
            AuditLog.society_id == society.id,
            AuditLog.entity_id == cid,
            AuditLog.action == "complaint.archived",
        )
        .one()
    )
    assert archive_row.actor_user_id is None


def test_lifecycle_vault_folder_path(
    auth, db, society, admin_user, superadmin, monkeypatch
):
    freeze_utcnow(monkeypatch)
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    created = raise_complaint(
        auth, r_hdr, category_id=cat, title="Leak", description="Tap leaks"
    )
    cid = created["id"]
    reference = created["reference"]
    display_code = created["house_display_code"]

    r1 = auth.client.post(
        f"/complaints/{cid}/images",
        headers=r_hdr,
        files={"file": ("leak.jpg", b"x" * 20, "image/jpeg")},
    )
    report_doc_id = r1.json()["vault_document_id"]

    auth.client.post(
        f"/complaints/{cid}/status", headers=hdr, json={"to_status": "in_progress"}
    )
    resolve_resp = resolve_http(
        auth, hdr, cid, note="fixed", files=[("proof.jpg", b"p" * 20, "image/jpeg")]
    )
    proof_doc_id = [
        img["vault_document_id"]
        for img in resolve_resp.json()["images"]
        if img["kind"] == "proof"
    ][0]

    # Both documents live under Houses/<display_code>/Complaints/<reference>/.
    from app.modules.vault.models import VaultFolder

    for doc_id in (report_doc_id, proof_doc_id):
        doc = db.get(VaultDocument, doc_id)
        assert doc is not None
        folder = db.get(VaultFolder, doc.folder_id)
        assert folder is not None
        assert folder.name == "Complaints"
        # Walk up: Complaints -> the house folder.
        house_folder = db.get(VaultFolder, folder.parent_id)
        assert house_folder is not None
        assert display_code is not None
        assert display_code in house_folder.name or house_folder.name == display_code


def test_lifecycle_image_counts(
    auth, db, society, admin_user, superadmin, monkeypatch
):
    freeze_utcnow(monkeypatch)
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    hid = owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = raise_complaint(
        auth, r_hdr, category_id=cat, title="Leak", description="Tap leaks"
    )["id"]

    auth.client.post(
        f"/complaints/{cid}/images",
        headers=r_hdr,
        files={"file": ("leak.jpg", b"x" * 20, "image/jpeg")},
    )
    auth.client.post(
        f"/complaints/{cid}/status", headers=hdr, json={"to_status": "in_progress"}
    )
    resolve_http(
        auth,
        hdr,
        cid,
        note="fixed",
        files=[
            ("a.jpg", b"a" * 20, "image/jpeg"),
            ("b.jpg", b"b" * 20, "image/jpeg"),
        ],
    )

    # list card counts.
    resp = auth.client.get("/complaints", headers=hdr, params={"house_id": hid})
    item = next(it for it in resp.json()["items"] if it["id"] == cid)
    assert item["report_image_count"] == 1
    assert item["proof_image_count"] == 2

    # detail images kind split.
    detail = auth.client.get(f"/complaints/{cid}", headers=hdr).json()
    assert len(detail["images"]) == 3
    kinds = [img["kind"] for img in detail["images"]]
    assert kinds.count("report") == 1
    assert kinds.count("proof") == 2


def test_lifecycle_events_emitted(
    auth, db, society, admin_user, superadmin, monkeypatch, event_capture
):
    freeze_utcnow(monkeypatch)
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    hid = owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    created = raise_complaint(
        auth, r_hdr, category_id=cat, title="Leak", description="Tap leaks"
    )
    cid = created["id"]

    auth.client.post(
        f"/complaints/{cid}/status", headers=hdr, json={"to_status": "in_progress"}
    )
    resolve_http(auth, hdr, cid, note="fixed")
    auth.client.post(
        f"/complaints/{cid}/status", headers=hdr, json={"to_status": "closed"}
    )
    auth.client.get(f"/complaints/{cid}", headers=hdr)

    from app.modules.complaints.events import EVENT_CREATED, EVENT_MARK_READ, EVENT_STATUS_CHANGED

    assert len(event_capture[EVENT_CREATED]) == 1
    created_ev = event_capture[EVENT_CREATED][0]
    assert created_ev["complaint_id"] == cid
    assert created_ev["house_id"] == hid
    assert created_ev["raised_by"] == raiser.id
    assert created_ev["reference"] == created["reference"]
    assert created_ev["category_id"] == cat

    status_events = event_capture[EVENT_STATUS_CHANGED]
    assert len(status_events) >= 3
    for ev in status_events:
        assert "from_status" in ev and "to_status" in ev
        assert "note" in ev and "reference" in ev

    reads = [
        p
        for p in event_capture[EVENT_MARK_READ]
        if p["entity_type"] == "complaint" and p["entity_id"] == cid
    ]
    assert reads


def test_lifecycle_reopen_then_reresolve(
    auth, db, society, admin_user, superadmin, monkeypatch
):
    freeze_utcnow(monkeypatch)
    hdr = setup_complaints(
        db, society, admin_user, superadmin, auth, config={"max_proof_images": 2}
    )
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = raise_complaint(
        auth, r_hdr, category_id=cat, title="Leak", description="Tap leaks"
    )["id"]

    auth.client.post(
        f"/complaints/{cid}/status", headers=hdr, json={"to_status": "in_progress"}
    )
    r1 = resolve_http(
        auth, hdr, cid, note="fixed once", files=[("a.jpg", b"a" * 20, "image/jpeg")]
    )
    assert r1.status_code == 200, r1.text
    assert _get(db, cid).resolved_at is not None

    # Reopen.
    reopen = auth.client.post(
        f"/complaints/{cid}/status",
        headers=hdr,
        json={"to_status": "in_progress", "note": "still leaking"},
    )
    assert reopen.status_code == 200, reopen.text
    assert _get(db, cid).resolved_at is None

    # Re-resolve with one more proof (cap=2, already have 1 -> add 1 more ok).
    r2 = resolve_http(
        auth, hdr, cid, note="fixed again", files=[("b.jpg", b"b" * 20, "image/jpeg")]
    )
    assert r2.status_code == 200, r2.text
    assert _get(db, cid).resolved_at is not None

    hist = _history(db, cid)
    resolved_rows = [h for h in hist if h.to_status == "resolved"]
    assert len(resolved_rows) == 2

    # Two resolves so far -> 2 proof images total (each resolve added 1, and the
    # cap (2) is checked PER resolve call against that call's own upload count,
    # not cumulatively against prior resolves — proof images are never removed,
    # so they simply accumulate across a reopen/re-resolve pair).
    imgs = _images(db, cid)
    proof_imgs = [i for i in imgs if i.kind == "proof"]
    assert len(proof_imgs) == 2

    # A third resolve call attempting to attach MORE THAN the cap (2) in that
    # single call is still rejected -> 422, cap enforced per-call.
    reopen2 = auth.client.post(
        f"/complaints/{cid}/status",
        headers=hdr,
        json={"to_status": "in_progress"},
    )
    assert reopen2.status_code == 200, reopen2.text
    r3 = resolve_http(
        auth,
        hdr,
        cid,
        note="third",
        files=[
            ("c.jpg", b"c" * 20, "image/jpeg"),
            ("d.jpg", b"d" * 20, "image/jpeg"),
            ("e.jpg", b"e" * 20, "image/jpeg"),
        ],
    )
    assert r3.status_code == 422, r3.text
    # No new proof stored beyond the 2 already there (the over-cap call rolled
    # back before any upload).
    imgs_after = _images(db, cid)
    assert len([i for i in imgs_after if i.kind == "proof"]) == 2


def test_e2e_text_only_complaint_without_vault(
    db, society, admin_user, superadmin, auth
):
    """Enable complaints WITHOUT vault: text-only raise + non-resolve status work;
    /resolve (vault-gated) is 403."""
    from app.platform.societies.schemas import ModuleAllocation
    from app.platform.societies.service import SocietyService

    SocietyService(db).set_modules(
        society.id,
        [
            ModuleAllocation(module_key="onboarding", enabled=True, config={}),
            ModuleAllocation(module_key="houses", enabled=True, config={}),
            ModuleAllocation(module_key="complaints", enabled=True, config={}),
        ],
        actor_user_id=superadmin.id,
    )
    db.commit()
    from tests._complaints_helpers import admin_bearer

    hdr = admin_bearer(auth, admin_user)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)

    created = raise_complaint(
        auth, r_hdr, category_id=cat, title="No vault", description="text only"
    )
    cid = created["id"]
    assert created["status"] == "open"

    resp = auth.client.post(
        f"/complaints/{cid}/status", headers=hdr, json={"to_status": "in_progress"}
    )
    assert resp.status_code == 200, resp.text

    # Resolve gates require_module('vault') -> 403 since vault isn't enabled.
    resolve_resp = auth.client.post(
        f"/complaints/{cid}/resolve", headers=hdr, data={"note": "x"}
    )
    assert resolve_resp.status_code == 403, resolve_resp.text
