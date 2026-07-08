"""Wave C — admin status workflow + resolve-with-proof (docs/modules/complaints.md
§3/§4/§6).

Covers ``StatusService.change_status`` (the non-resolve admin edges) and
``StatusService.resolve`` (the ``in_progress -> resolved`` transition that carries
proof images into the Vault). Wave B's raise flow is still a stub, so complaints
are seeded directly through the ORM + the frozen ``support`` write choke-point,
then driven through the real HTTP endpoints under test.

Assertions go past the HTTP status to the DB (status/timestamps, complaint_images
rows, status-history timeline) and the audit_log, per the wave contract.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.modules.complaints.models import (
    Complaint,
    ComplaintCategory,
    ComplaintImage,
    ComplaintStatusHistory,
)
from app.modules.complaints.repository import ComplaintRepository
from app.modules.complaints.services import support
from tests._complaints_helpers import (
    audit_actions,
    owned_house_for,
    owner_login_bearer,
    second_society_with_complaints,
    setup_complaints,
)
from tests._vault_helpers import storage_override  # noqa: F401  (fixture)

pytestmark = pytest.mark.usefixtures("storage_override")

_JPEG = ("proof.jpg", b"x" * 40, "image/jpeg")


# ===========================================================================
# seeding (Wave B raise is a stub — build the row directly)
# ===========================================================================


def _seed_category(db, society_id) -> int:
    cat = ComplaintCategory(
        society_id=society_id, name="Plumbing", is_active=True, is_system=True
    )
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat.id


def _seed_complaint(db, society_id, house_id, raised_by, category_id, *, status="open"):
    """Insert a complaint at ``open`` then walk it to ``status`` via the frozen
    ``support`` write choke-point (so timestamps + timeline match production)."""
    repo = ComplaintRepository(db)
    reference = repo.allocate_reference(society_id)
    complaint = Complaint(
        society_id=society_id,
        reference=reference,
        house_id=house_id,
        raised_by=raised_by,
        category_id=category_id,
        title="Leaking tap",
        description="The kitchen tap leaks.",
        status="open",
    )
    repo.add_complaint(complaint)
    support.record_initial(repo, complaint, changed_by=raised_by)

    # Walk to the requested state through the legal edges.
    path = {
        "open": [],
        "in_progress": ["in_progress"],
        "resolved": ["in_progress", "resolved"],
        "closed": ["in_progress", "resolved", "closed"],
    }[status]
    for step in path:
        support.record_transition(
            repo, complaint, to_status=step, note=None, changed_by=raised_by
        )
    db.commit()
    db.refresh(complaint)
    return complaint


def _arrange(db, society, admin_user, superadmin, auth, *, status="open", config=None):
    """Common arrange: enable complaints, provision a raiser + owned house, seed a
    complaint at ``status``. Returns (admin_hdr, complaint_id, house_id, raiser_id)."""
    admin_hdr = setup_complaints(
        db, society, admin_user, superadmin, auth, config=config
    )
    hid = owned_house_for(auth, admin_hdr, email="raiser@x.com")
    _bearer, raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat_id = _seed_category(db, society.id)
    complaint = _seed_complaint(
        db, society.id, hid, raiser.id, cat_id, status=status
    )
    return admin_hdr, complaint.id, hid, raiser.id


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


# ===========================================================================
# change_status — non-resolve admin edges
# ===========================================================================


def test_open_to_in_progress_happy(db, society, admin_user, superadmin, auth):
    admin_hdr, cid, _hid, _raiser = _arrange(
        db, society, admin_user, superadmin, auth, status="open"
    )

    resp = auth.client.post(
        f"/complaints/{cid}/status",
        headers=admin_hdr,
        json={"to_status": "in_progress", "note": "Assigned to plumber."},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "in_progress"

    # DB: status flipped.
    assert _get(db, cid).status == "in_progress"

    # Timeline: initial + the new transition carrying the note.
    hist = _history(db, cid)
    assert [(h.from_status, h.to_status) for h in hist] == [
        (None, "open"),
        ("open", "in_progress"),
    ]
    assert hist[-1].note == "Assigned to plumber."
    assert hist[-1].changed_by == admin_user.id

    # Timeline is present in the detail payload.
    assert body["timeline"][-1]["to_status"] == "in_progress"
    assert body["timeline"][-1]["note"] == "Assigned to plumber."

    # Audit row with before/after.
    assert ("complaint.status_changed", "complaint", cid) in audit_actions(
        db, society.id
    )


def test_open_to_closed_illegal_409(db, society, admin_user, superadmin, auth):
    admin_hdr, cid, _hid, _raiser = _arrange(
        db, society, admin_user, superadmin, auth, status="open"
    )

    resp = auth.client.post(
        f"/complaints/{cid}/status",
        headers=admin_hdr,
        json={"to_status": "closed"},
    )
    assert resp.status_code == 409, resp.text
    # Unchanged.
    assert _get(db, cid).status == "open"


def test_in_progress_to_resolved_via_status_rejected(
    db, society, admin_user, superadmin, auth
):
    """Resolving carries proof — it must go through the resolve route, not here."""
    admin_hdr, cid, _hid, _raiser = _arrange(
        db, society, admin_user, superadmin, auth, status="in_progress"
    )

    resp = auth.client.post(
        f"/complaints/{cid}/status",
        headers=admin_hdr,
        json={"to_status": "resolved"},
    )
    assert resp.status_code == 422, resp.text
    assert "resolve" in resp.json()["message"].lower()
    # Nothing changed.
    assert _get(db, cid).status == "in_progress"


def test_resolved_to_closed_sets_closed_at(
    db, society, admin_user, superadmin, auth
):
    admin_hdr, cid, _hid, _raiser = _arrange(
        db, society, admin_user, superadmin, auth, status="resolved"
    )

    resp = auth.client.post(
        f"/complaints/{cid}/status",
        headers=admin_hdr,
        json={"to_status": "closed", "note": "Confirmed fixed."},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "closed"

    row = _get(db, cid)
    assert row.status == "closed"
    assert row.closed_at is not None


def test_reopen_resolved_to_in_progress_clears_resolved_at(
    db, society, admin_user, superadmin, auth
):
    admin_hdr, cid, _hid, _raiser = _arrange(
        db, society, admin_user, superadmin, auth, status="resolved"
    )
    assert _get(db, cid).resolved_at is not None

    resp = auth.client.post(
        f"/complaints/{cid}/status",
        headers=admin_hdr,
        json={"to_status": "in_progress", "note": "Reopening — still leaking."},
    )
    assert resp.status_code == 200, resp.text

    row = _get(db, cid)
    assert row.status == "in_progress"
    assert row.resolved_at is None  # cleared on reopen


def test_closed_to_archived_not_allowed_via_admin(
    db, society, admin_user, superadmin, auth
):
    """Archive is worker-only: the admin schema rejects ``archived`` outright."""
    admin_hdr, cid, _hid, _raiser = _arrange(
        db, society, admin_user, superadmin, auth, status="closed"
    )

    resp = auth.client.post(
        f"/complaints/{cid}/status",
        headers=admin_hdr,
        json={"to_status": "archived"},
    )
    # ``archived`` is not in ADMIN_TARGET_STATUSES -> request-body validation 422.
    assert resp.status_code == 422, resp.text
    assert _get(db, cid).status == "closed"


def test_change_status_not_found_404(db, society, admin_user, superadmin, auth):
    admin_hdr = setup_complaints(db, society, admin_user, superadmin, auth)

    resp = auth.client.post(
        "/complaints/999999/status",
        headers=admin_hdr,
        json={"to_status": "in_progress"},
    )
    assert resp.status_code == 404, resp.text


def test_change_status_requires_permission_403(
    db, society, admin_user, superadmin, auth
):
    """A caller without ``complaints.update_status`` is 403 (strip it from admin)."""
    admin_hdr, cid, _hid, _raiser = _arrange(
        db, society, admin_user, superadmin, auth, status="open"
    )
    _strip_permission(db, society.id, "complaints.update_status")

    resp = auth.client.post(
        f"/complaints/{cid}/status",
        headers=admin_hdr,
        json={"to_status": "in_progress"},
    )
    assert resp.status_code == 403, resp.text
    assert _get(db, cid).status == "open"


# ===========================================================================
# resolve — in_progress -> resolved with proof images
# ===========================================================================


def test_resolve_no_images_happy(db, society, admin_user, superadmin, auth):
    admin_hdr, cid, _hid, _raiser = _arrange(
        db, society, admin_user, superadmin, auth, status="in_progress"
    )

    resp = auth.client.post(
        f"/complaints/{cid}/resolve",
        headers=admin_hdr,
        data={"note": "Replaced the washer."},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "resolved"
    assert body["images"] == []

    row = _get(db, cid)
    assert row.status == "resolved"
    assert row.resolved_at is not None
    # Solution note is the resolved history row's note.
    hist = _history(db, cid)
    assert hist[-1].to_status == "resolved"
    assert hist[-1].note == "Replaced the washer."
    assert _images(db, cid) == []


def test_resolve_with_proof_images_happy(
    db, society, admin_user, superadmin, auth
):
    admin_hdr, cid, _hid, _raiser = _arrange(
        db, society, admin_user, superadmin, auth, status="in_progress"
    )

    resp = auth.client.post(
        f"/complaints/{cid}/resolve",
        headers=admin_hdr,
        data={"note": "Fixed; see photos."},
        files=[
            ("images", ("before.jpg", b"a" * 30, "image/jpeg")),
            ("images", ("after.jpg", b"b" * 30, "image/jpeg")),
        ],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "resolved"
    assert len(body["images"]) == 2
    assert all(img["kind"] == "proof" for img in body["images"])
    # Preview URLs populated from Vault.
    assert all(img["preview_url"] for img in body["images"])

    # DB: two proof images, each pointing at a stored vault document.
    imgs = _images(db, cid)
    assert len(imgs) == 2
    assert {i.kind for i in imgs} == {"proof"}
    assert all(i.vault_document_id for i in imgs)
    assert all(i.added_by == admin_user.id for i in imgs)

    # Files actually landed in the vault (documents exist for the society).
    doc_ids = [i.vault_document_id for i in imgs]
    stored = db.execute(
        text("SELECT count(*) FROM vault_documents WHERE id = ANY(:ids)"),
        {"ids": doc_ids},
    ).scalar_one()
    assert stored == 2

    row = _get(db, cid)
    assert row.status == "resolved"
    assert row.resolved_at is not None

    # Audit: two image_added (proof) + one status_changed.
    actions = [a for a in audit_actions(db, society.id) if a[2] == cid]
    assert actions.count(("complaint.image_added", "complaint", cid)) == 2
    assert actions.count(("complaint.status_changed", "complaint", cid)) == 1


def test_resolve_exceeding_cap_422_rolls_back(
    db, society, admin_user, superadmin, auth
):
    """Too many proof images -> 422 BEFORE any upload; nothing is persisted."""
    admin_hdr, cid, _hid, _raiser = _arrange(
        db,
        society,
        admin_user,
        superadmin,
        auth,
        status="in_progress",
        config={"max_proof_images": 1},
    )

    resp = auth.client.post(
        f"/complaints/{cid}/resolve",
        headers=admin_hdr,
        data={"note": "too many"},
        files=[
            ("images", ("1.jpg", b"a" * 30, "image/jpeg")),
            ("images", ("2.jpg", b"b" * 30, "image/jpeg")),
        ],
    )
    assert resp.status_code == 422, resp.text

    # No images stored, status unchanged (whole request rolled back).
    assert _images(db, cid) == []
    assert _get(db, cid).status == "in_progress"
    vault_count = db.execute(
        text("SELECT count(*) FROM vault_documents WHERE society_id=:s"),
        {"s": society.id},
    ).scalar_one()
    assert vault_count == 0


def test_resolve_from_wrong_state_409(db, society, admin_user, superadmin, auth):
    """Resolving an ``open`` complaint is an illegal edge -> 409."""
    admin_hdr, cid, _hid, _raiser = _arrange(
        db, society, admin_user, superadmin, auth, status="open"
    )

    resp = auth.client.post(
        f"/complaints/{cid}/resolve",
        headers=admin_hdr,
        data={"note": "nope"},
    )
    assert resp.status_code == 409, resp.text
    assert _get(db, cid).status == "open"
    assert _images(db, cid) == []


def test_resolve_not_found_404(db, society, admin_user, superadmin, auth):
    admin_hdr = setup_complaints(db, society, admin_user, superadmin, auth)

    resp = auth.client.post(
        "/complaints/999999/resolve",
        headers=admin_hdr,
        data={"note": "x"},
    )
    assert resp.status_code == 404, resp.text


def test_resolve_requires_permission_403(
    db, society, admin_user, superadmin, auth
):
    admin_hdr, cid, _hid, _raiser = _arrange(
        db, society, admin_user, superadmin, auth, status="in_progress"
    )
    _strip_permission(db, society.id, "complaints.update_status")

    resp = auth.client.post(
        f"/complaints/{cid}/resolve",
        headers=admin_hdr,
        data={"note": "x"},
    )
    assert resp.status_code == 403, resp.text
    assert _get(db, cid).status == "in_progress"


# ===========================================================================
# tenant isolation
# ===========================================================================


def test_tenant_isolation_status(db, society, admin_user, superadmin, auth):
    """Society B's admin cannot transition society A's complaint (404, not 403)."""
    _admin_a, cid, _hid, _raiser = _arrange(
        db, society, admin_user, superadmin, auth, status="open"
    )
    _soc_b, _admin_b, hdr_b = second_society_with_complaints(db, superadmin, auth)

    resp = auth.client.post(
        f"/complaints/{cid}/status",
        headers=hdr_b,
        json={"to_status": "in_progress"},
    )
    assert resp.status_code == 404, resp.text
    # A's complaint untouched.
    assert _get(db, cid).status == "open"


def test_tenant_isolation_resolve(db, society, admin_user, superadmin, auth):
    _admin_a, cid, _hid, _raiser = _arrange(
        db, society, admin_user, superadmin, auth, status="in_progress"
    )
    _soc_b, _admin_b, hdr_b = second_society_with_complaints(db, superadmin, auth)

    resp = auth.client.post(
        f"/complaints/{cid}/resolve",
        headers=hdr_b,
        data={"note": "x"},
    )
    assert resp.status_code == 404, resp.text
    assert _get(db, cid).status == "in_progress"
    assert _images(db, cid) == []


# ===========================================================================
# helpers
# ===========================================================================


def _strip_permission(db, society_id, perm_key) -> None:
    """Remove a permission from the society_admin role (drive the 403 path)."""
    from app.platform.roles.repository import RoleRepository

    role = RoleRepository(db).society_role_by_key(society_id, "society_admin")
    perm_id = db.execute(
        text("SELECT id FROM permissions WHERE key=:k"), {"k": perm_key}
    ).scalar_one()
    db.execute(
        text(
            "DELETE FROM role_permissions WHERE role_id=:r AND permission_id=:p"
        ),
        {"r": role.id, "p": perm_id},
    )
    db.commit()
