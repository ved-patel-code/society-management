"""Tests for Complaints REPORT images (Wave D) — docs/modules/complaints.md §4/§6.

Wave D implements ``ImagesService`` — the resident's report photos only:
``POST /complaints/{id}/images`` (add a report image to an OPEN complaint,
raiser-only, ``<= max_report_images``, filed into the Vault) and
``DELETE /complaints/{id}/images/{imageId}`` (remove one's own report image ->
soft-delete the Vault document + drop the row). Proof images are NOT exercised
here (they belong to the resolve transition, Wave C).

Covered: the add happy path (Vault doc created, ``complaint_images`` row of kind
``report``, ``preview_url`` populated, ``complaint.image_added`` audit); the cap
(``max_report_images`` then the next add -> 409); non-raiser 403; add when not
``open`` -> 409; the remove happy path (row gone + Vault doc soft-deleted +
``complaint.image_removed`` audit); remove someone else's / a missing image ->
404; remove when not ``open`` -> 409; permission gating (``complaints.create``
required); and tenant isolation.

The raise/edit endpoints live in another (stubbed) wave, so complaints are
inserted directly against the model with a real seeded category + the provisioned
owner as ``raised_by`` — exactly what the routes would have produced.
"""
from __future__ import annotations

from sqlalchemy import select

from app.modules.complaints.models import Complaint, ComplaintImage
from app.modules.complaints.repository import ComplaintRepository
from app.modules.complaints.schemas import (
    KIND_PROOF,
    KIND_REPORT,
    STATUS_IN_PROGRESS,
    STATUS_OPEN,
)
from app.modules.vault.models import VaultDocument
from app.platform.models import AuditLog

from tests._complaints_helpers import (
    audit_actions,
    owned_house_for,
    owner_login_bearer,
    resident_bearer,
    second_society_with_complaints,
    setup_complaints,
)

# A tiny valid JPEG payload (bytes content is opaque to the report-image path;
# Vault keys off the extension, not the magic bytes).
_JPEG = b"\xff\xd8\xff\xe0jpeg-bytes"


# --- fixtures/builders -------------------------------------------------------


def _seed_category_id(auth, admin_hdr) -> int:
    """List categories as admin (lazy-seeds the 6 defaults) and return one id."""
    resp = auth.client.get("/complaints/categories", headers=admin_hdr)
    assert resp.status_code == 200, resp.text
    return resp.json()[0]["id"]


def _insert_complaint(
    db,
    society_id,
    *,
    house_id,
    raised_by,
    category_id,
    status=STATUS_OPEN,
):
    """Insert a complaint row directly (the raise route is a stub in another wave).

    Uses the real per-society reference allocator so the row is indistinguishable
    from one the create endpoint would produce. Commits so the API request (its
    own session) sees it.
    """
    reference = ComplaintRepository(db).allocate_reference(society_id)
    complaint = Complaint(
        society_id=society_id,
        reference=reference,
        house_id=house_id,
        raised_by=raised_by,
        category_id=category_id,
        title="Leaking tap",
        description="Kitchen tap won't stop dripping.",
        status=status,
    )
    db.add(complaint)
    db.commit()
    db.refresh(complaint)
    return complaint


def _make_raiser_and_complaint(
    db, society, admin_user, superadmin, auth, *, status=STATUS_OPEN
):
    """Full setup: enable complaints, provision an owner+house, insert a complaint.

    Returns ``(admin_hdr, raiser_hdr, raiser_user, complaint)``.
    """
    admin_hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    house_id = owned_house_for(auth, admin_hdr, email="raiser@x.com")
    raiser_hdr, raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    category_id = _seed_category_id(auth, admin_hdr)
    complaint = _insert_complaint(
        db,
        society.id,
        house_id=house_id,
        raised_by=raiser.id,
        category_id=category_id,
        status=status,
    )
    return admin_hdr, raiser_hdr, raiser, complaint


def _add_image(auth, hdr, complaint_id, *, name="r.jpg", data=_JPEG, ct="image/jpeg"):
    return auth.client.post(
        f"/complaints/{complaint_id}/images",
        headers=hdr,
        files={"file": (name, data, ct)},
    )


def _remove_image(auth, hdr, complaint_id, image_id):
    return auth.client.delete(
        f"/complaints/{complaint_id}/images/{image_id}", headers=hdr
    )


def _image_audits(db, society_id, action):
    return (
        db.query(AuditLog)
        .filter(AuditLog.society_id == society_id, AuditLog.action == action)
        .order_by(AuditLog.id)
        .all()
    )


# ===========================================================================
# add: happy path — Vault doc + report row + preview_url + audit
# ===========================================================================


def test_add_report_image_happy_path(
    db, society, admin_user, superadmin, auth
):
    _admin_hdr, raiser_hdr, raiser, complaint = _make_raiser_and_complaint(
        db, society, admin_user, superadmin, auth
    )

    resp = _add_image(auth, raiser_hdr, complaint.id)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == KIND_REPORT
    assert body["vault_document_id"] > 0
    # A signed inline Vault preview URL is populated by the service.
    assert body["preview_url"]
    assert body["id"] > 0

    # complaint_images row of kind='report', linked to the raiser + a live Vault doc.
    db.expire_all()
    img = db.get(ComplaintImage, body["id"])
    assert img is not None
    assert img.kind == KIND_REPORT
    assert img.complaint_id == complaint.id
    assert img.added_by == raiser.id
    assert img.vault_document_id == body["vault_document_id"]

    doc = db.get(VaultDocument, body["vault_document_id"])
    assert doc is not None
    assert doc.deleted_at is None
    assert doc.source == "complaint"
    assert doc.source_ref == complaint.id

    # Audited complaint.image_added (kind=report, vault_document_id).
    audits = _image_audits(db, society.id, "complaint.image_added")
    assert len(audits) == 1
    assert audits[0].entity_type == "complaint"
    assert audits[0].entity_id == complaint.id
    assert audits[0].after["kind"] == KIND_REPORT
    assert audits[0].after["vault_document_id"] == body["vault_document_id"]


# ===========================================================================
# add: the cap — max_report_images then the next -> 409
# ===========================================================================


def test_add_report_image_enforces_cap(
    db, society, admin_user, superadmin, auth
):
    # Cap at 1 so a second add trips the limit deterministically.
    admin_hdr = setup_complaints(
        db, society, admin_user, superadmin, auth,
        config={"max_report_images": 1},
    )
    house_id = owned_house_for(auth, admin_hdr, email="raiser@x.com")
    raiser_hdr, raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    category_id = _seed_category_id(auth, admin_hdr)
    complaint = _insert_complaint(
        db, society.id, house_id=house_id, raised_by=raiser.id,
        category_id=category_id,
    )

    assert _add_image(auth, raiser_hdr, complaint.id).status_code == 200

    # The 2nd add exceeds max_report_images=1 -> 409, and stores nothing.
    over = _add_image(auth, raiser_hdr, complaint.id, name="r2.jpg")
    assert over.status_code == 409, over.text
    assert over.json()["code"] == "conflict"

    db.expire_all()
    rows = db.execute(
        select(ComplaintImage).where(
            ComplaintImage.complaint_id == complaint.id
        )
    ).scalars().all()
    assert len(rows) == 1
    # No orphan Vault doc from the rejected add (only the one accepted upload).
    docs = db.execute(
        select(VaultDocument).where(VaultDocument.source_ref == complaint.id)
    ).scalars().all()
    assert len(docs) == 1


# ===========================================================================
# add: non-raiser 403 / not-open 409 / missing complaint 404
# ===========================================================================


def test_add_report_image_by_non_raiser_forbidden(
    db, society, admin_user, resident_user, superadmin, auth
):
    _admin_hdr, _raiser_hdr, _raiser, complaint = _make_raiser_and_complaint(
        db, society, admin_user, superadmin, auth
    )
    # A different resident (holds complaints.create) is NOT the raiser -> 403.
    other_hdr = resident_bearer(auth, resident_user)
    resp = _add_image(auth, other_hdr, complaint.id)
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "permission_denied"

    db.expire_all()
    assert db.execute(
        select(ComplaintImage).where(
            ComplaintImage.complaint_id == complaint.id
        )
    ).scalars().all() == []


def test_add_report_image_when_not_open_conflicts(
    db, society, admin_user, superadmin, auth
):
    _admin_hdr, raiser_hdr, _raiser, complaint = _make_raiser_and_complaint(
        db, society, admin_user, superadmin, auth
    )
    # An admin has moved it forward: report images are locked (§4).
    complaint.status = STATUS_IN_PROGRESS
    db.add(complaint)
    db.commit()

    resp = _add_image(auth, raiser_hdr, complaint.id)
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "conflict"


def test_add_report_image_missing_complaint_not_found(
    db, society, admin_user, superadmin, auth
):
    _admin_hdr, raiser_hdr, _raiser, _complaint = _make_raiser_and_complaint(
        db, society, admin_user, superadmin, auth
    )
    resp = _add_image(auth, raiser_hdr, 999999)
    assert resp.status_code == 404, resp.text


# ===========================================================================
# remove: happy path — row gone + Vault doc soft-deleted + audit
# ===========================================================================


def test_remove_report_image_happy_path(
    db, society, admin_user, superadmin, auth
):
    _admin_hdr, raiser_hdr, _raiser, complaint = _make_raiser_and_complaint(
        db, society, admin_user, superadmin, auth
    )
    added = _add_image(auth, raiser_hdr, complaint.id).json()
    image_id = added["id"]
    doc_id = added["vault_document_id"]

    resp = _remove_image(auth, raiser_hdr, complaint.id, image_id)
    assert resp.status_code == 204, resp.text

    # The complaint_images row is gone.
    db.expire_all()
    assert db.get(ComplaintImage, image_id) is None

    # The backing Vault document is soft-deleted (moved to Trash), not purged.
    doc = db.get(VaultDocument, doc_id)
    assert doc is not None
    assert doc.deleted_at is not None

    # Audited complaint.image_removed (kind=report, vault_document_id).
    audits = _image_audits(db, society.id, "complaint.image_removed")
    assert len(audits) == 1
    assert audits[0].entity_id == complaint.id
    assert audits[0].before["kind"] == KIND_REPORT
    assert audits[0].before["vault_document_id"] == doc_id


# ===========================================================================
# remove: bad paths — missing / other's image 404, not-open 409
# ===========================================================================


def test_remove_missing_image_not_found(
    db, society, admin_user, superadmin, auth
):
    _admin_hdr, raiser_hdr, _raiser, complaint = _make_raiser_and_complaint(
        db, society, admin_user, superadmin, auth
    )
    resp = _remove_image(auth, raiser_hdr, complaint.id, 999999)
    assert resp.status_code == 404, resp.text


def test_remove_image_of_another_complaint_not_found(
    db, society, admin_user, superadmin, auth
):
    """An image id that belongs to a DIFFERENT complaint is not found on this one."""
    admin_hdr, raiser_hdr, raiser, complaint = _make_raiser_and_complaint(
        db, society, admin_user, superadmin, auth
    )
    image_id = _add_image(auth, raiser_hdr, complaint.id).json()["id"]

    # A second complaint for the same raiser; the first complaint's image id is
    # not scoped to it -> 404 (get_image is complaint-scoped).
    other = _insert_complaint(
        db, society.id, house_id=complaint.house_id, raised_by=raiser.id,
        category_id=complaint.category_id,
    )
    resp = _remove_image(auth, raiser_hdr, other.id, image_id)
    assert resp.status_code == 404, resp.text

    # The image is untouched (still live, Vault doc not deleted).
    db.expire_all()
    img = db.get(ComplaintImage, image_id)
    assert img is not None
    assert db.get(VaultDocument, img.vault_document_id).deleted_at is None


def test_remove_proof_image_via_report_route_not_found(
    db, society, admin_user, superadmin, auth
):
    """A proof image is not removable through the resident report route (§4).

    Proof images are attached at resolve (Wave C); this route only manages report
    images, so a proof id surfaces as 404 rather than deleting it.
    """
    _admin_hdr, raiser_hdr, raiser, complaint = _make_raiser_and_complaint(
        db, society, admin_user, superadmin, auth
    )
    # Insert a proof image directly (as the resolve transition would).
    proof_doc = VaultDocument(
        society_id=society.id,
        folder_id=None,
        filename="proof.jpg",
        content_type="image/jpeg",
        size_bytes=len(_JPEG),
        storage_key=f"societies/{society.id}/proof-test",
        checksum="deadbeef",
        source="complaint",
        source_ref=complaint.id,
        uploaded_by=raiser.id,
    )
    # folder_id is NOT NULL in the vault schema; place it under the house folder.
    from app.modules.vault import api as vault_api

    folder = vault_api.ensure_house_folder(
        db, society.id, complaint.house_id,
        kind=vault_api.HOUSE_FOLDER_COMPLAINTS, actor_user_id=raiser.id,
    )
    proof_doc.folder_id = folder.id
    db.add(proof_doc)
    db.flush()
    proof_img = ComplaintImage(
        society_id=society.id,
        complaint_id=complaint.id,
        kind=KIND_PROOF,
        vault_document_id=proof_doc.id,
        added_by=admin_user.id,
    )
    db.add(proof_img)
    db.commit()
    db.refresh(proof_img)

    resp = _remove_image(auth, raiser_hdr, complaint.id, proof_img.id)
    assert resp.status_code == 404, resp.text

    # The proof image + its doc are untouched.
    db.expire_all()
    assert db.get(ComplaintImage, proof_img.id) is not None
    assert db.get(VaultDocument, proof_doc.id).deleted_at is None


def test_remove_report_image_when_not_open_conflicts(
    db, society, admin_user, superadmin, auth
):
    _admin_hdr, raiser_hdr, _raiser, complaint = _make_raiser_and_complaint(
        db, society, admin_user, superadmin, auth
    )
    image_id = _add_image(auth, raiser_hdr, complaint.id).json()["id"]

    # Once locked (in_progress), the raiser can no longer remove report images.
    complaint.status = STATUS_IN_PROGRESS
    db.add(complaint)
    db.commit()

    resp = _remove_image(auth, raiser_hdr, complaint.id, image_id)
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "conflict"

    # The image survived the rejected removal.
    db.expire_all()
    img = db.get(ComplaintImage, image_id)
    assert img is not None
    assert db.get(VaultDocument, img.vault_document_id).deleted_at is None


# ===========================================================================
# permission gating: complaints.create is required for both routes
# ===========================================================================


def test_image_routes_require_create_permission(
    db, society, admin_user, superadmin, auth
):
    """The society_admin lacks ``complaints.create`` -> 403 on add + remove.

    (Admin holds read_all/update_status/manage_categories/configure but not
    create — the report-image routes gate on ``complaints.create``, spec.py.)
    """
    admin_hdr, raiser_hdr, _raiser, complaint = _make_raiser_and_complaint(
        db, society, admin_user, superadmin, auth
    )
    image_id = _add_image(auth, raiser_hdr, complaint.id).json()["id"]

    # Admin (no complaints.create) is gated out of both image routes.
    assert _add_image(auth, admin_hdr, complaint.id).status_code == 403
    assert _remove_image(auth, admin_hdr, complaint.id, image_id).status_code == 403


# ===========================================================================
# tenant isolation: society B cannot touch society A's complaint image
# ===========================================================================


def test_tenant_isolation_between_societies(
    db, society, admin_user, superadmin, auth
):
    _admin_hdr, raiser_hdr, _raiser, complaint = _make_raiser_and_complaint(
        db, society, admin_user, superadmin, auth
    )
    image_id = _add_image(auth, raiser_hdr, complaint.id).json()["id"]

    # Society B, its own admin + a provisioned raiser there.
    soc_b, _admin_b, hdr_b = second_society_with_complaints(db, superadmin, auth)
    house_b = owned_house_for(auth, hdr_b, email="raiser-b@x.com")
    raiser_b_hdr, _raiser_b = owner_login_bearer(auth, db, email="raiser-b@x.com")

    # B's raiser cannot see or touch A's complaint (society-scoped lookup -> 404).
    assert _add_image(auth, raiser_b_hdr, complaint.id).status_code == 404
    assert _remove_image(
        auth, raiser_b_hdr, complaint.id, image_id
    ).status_code == 404

    # A's image is untouched, and A's audit trail carries no B rows.
    db.expire_all()
    img = db.get(ComplaintImage, image_id)
    assert img is not None
    assert db.get(VaultDocument, img.vault_document_id).deleted_at is None
    a_image_actions = [
        a for a in audit_actions(db, society.id)
        if a[0].startswith("complaint.image_")
    ]
    assert all(entity_id == complaint.id for _, _, entity_id in a_image_actions)
    assert house_b != complaint.house_id
