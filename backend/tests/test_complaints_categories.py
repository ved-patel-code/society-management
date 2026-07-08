"""Tests for Complaints CATEGORIES (Wave A) — docs/modules/complaints.md §4/§6.

Wave A implements ``CategoriesService``: list (lazy-seeds the 6 system defaults,
active-only — the create-form list), create (active-name collision -> 409, a
deactivated name is reusable), PATCH rename (collision -> 409) + reactivate, and
DELETE soft-deactivate (idempotent, never hard-delete). Covers the happy paths,
the 409/422/404 bad paths, permission gating (``complaints.read`` reads the list;
``complaints.manage_categories`` is required for create/patch/delete), tenant
isolation, and the audit rows written for each mutation.

Enabling complaints (+ deps + vault) seeds ``society_admin`` all five admin
``complaints.*`` perms and ``resident`` -> ``create`` + ``read`` (spec.py).
"""
from __future__ import annotations

from sqlalchemy import select

from app.modules.complaints.models import ComplaintCategory
from app.platform.models import AuditLog

from tests._complaints_helpers import (
    audit_actions,
    resident_bearer,
    second_society_with_complaints,
    setup_complaints,
)

# The 6 system categories seeded lazily on first list (docs §3).
DEFAULT_CATEGORY_NAMES = {
    "Plumbing",
    "Electrical",
    "Common Area",
    "Security",
    "Cleaning",
    "Other",
}


# --- small HTTP helpers ------------------------------------------------------


def _list(auth, hdr):
    return auth.client.get("/complaints/categories", headers=hdr)


def _create(auth, hdr, name):
    return auth.client.post(
        "/complaints/categories", headers=hdr, json={"name": name}
    )


def _patch(auth, hdr, category_id, **body):
    return auth.client.patch(
        f"/complaints/categories/{category_id}", headers=hdr, json=body
    )


def _delete(auth, hdr, category_id):
    return auth.client.delete(
        f"/complaints/categories/{category_id}", headers=hdr
    )


def _names(listing) -> set[str]:
    return {c["name"] for c in listing}


def _by_name(listing, name) -> dict | None:
    return next((c for c in listing if c["name"] == name), None)


def _category_audits(db, society_id, action) -> list[AuditLog]:
    return (
        db.query(AuditLog)
        .filter(
            AuditLog.society_id == society_id,
            AuditLog.action == action,
        )
        .order_by(AuditLog.id)
        .all()
    )


# ===========================================================================
# list: lazy seed of the 6 system defaults (active-only, idempotent)
# ===========================================================================


def test_first_list_seeds_system_defaults(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    resp = _list(auth, hdr)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert _names(body) == DEFAULT_CATEGORY_NAMES
    assert all(c["is_system"] is True for c in body)
    assert all(c["is_active"] is True for c in body)

    # Idempotent: a second list does not re-seed (still exactly 6).
    again = _list(auth, hdr)
    assert len(again.json()) == 6

    # Alphabetical order (repo lists by name).
    assert [c["name"] for c in again.json()] == sorted(DEFAULT_CATEGORY_NAMES)


# ===========================================================================
# create: happy path + duplicate-active-name 409 + reuse-of-deactivated-name
# ===========================================================================


def test_create_category_happy_path(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    resp = _create(auth, hdr, "Elevator")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Elevator"
    assert body["is_system"] is False
    assert body["is_active"] is True
    assert body["id"] > 0

    # Now appears in the create-form list (7 total: 6 system + 1 custom).
    listing = _list(auth, hdr).json()
    assert len(listing) == 7
    assert "Elevator" in _names(listing)

    # created_by stamped to the admin; audited complaint_category.created.
    db.expire_all()
    row = db.get(ComplaintCategory, body["id"])
    assert row.created_by == admin_user.id
    audits = _category_audits(db, society.id, "complaint_category.created")
    assert len(audits) == 1
    assert audits[0].entity_type == "complaint_category"
    assert audits[0].entity_id == body["id"]
    assert audits[0].after == {"name": "Elevator"}


def test_create_duplicate_active_name_conflicts(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    assert _create(auth, hdr, "Elevator").status_code == 200

    dup = _create(auth, hdr, "Elevator")
    assert dup.status_code == 409, dup.text
    assert dup.json()["code"] == "conflict"

    # Colliding with a SEEDED system name also conflicts (seeded on first use).
    sys_dup = _create(auth, hdr, "Plumbing")
    assert sys_dup.status_code == 409, sys_dup.text

    # Only one 'Elevator' row exists (the failed create wrote nothing).
    db.expire_all()
    rows = db.execute(
        select(ComplaintCategory).where(
            ComplaintCategory.society_id == society.id,
            ComplaintCategory.name == "Elevator",
        )
    ).scalars().all()
    assert len(rows) == 1


def test_create_blank_name_rejected(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    # Whitespace-only trims to empty -> schema 422.
    assert _create(auth, hdr, "   ").status_code == 422
    assert _create(auth, hdr, "").status_code == 422


def test_deactivated_name_is_reusable(
    db, society, admin_user, superadmin, auth
):
    """A deactivated name frees up: a new active category may reclaim it (§4)."""
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    first = _create(auth, hdr, "Pest Control")
    assert first.status_code == 200, first.text
    first_id = first.json()["id"]

    # Deactivate it, then re-create a NEW active category with the same name.
    assert _delete(auth, hdr, first_id).status_code == 200
    second = _create(auth, hdr, "Pest Control")
    assert second.status_code == 200, second.text
    second_id = second.json()["id"]
    assert second_id != first_id

    # Two rows share the name but only the new one is active (partial unique idx).
    db.expire_all()
    rows = db.execute(
        select(ComplaintCategory).where(
            ComplaintCategory.society_id == society.id,
            ComplaintCategory.name == "Pest Control",
        )
    ).scalars().all()
    assert len(rows) == 2
    assert {r.is_active for r in rows} == {True, False}

    # The active list shows exactly one 'Pest Control' (the reclaimed one).
    listing = _list(auth, hdr).json()
    assert [c for c in listing if c["name"] == "Pest Control"] == [
        _by_name(listing, "Pest Control")
    ]
    assert _by_name(listing, "Pest Control")["id"] == second_id


# ===========================================================================
# rename: happy path + collision 409 + 404 + no-op field 422
# ===========================================================================


def test_rename_category_happy_path(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    cat_id = _create(auth, hdr, "Garden").json()["id"]

    resp = _patch(auth, hdr, cat_id, name="Landscaping")
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Landscaping"

    # Old name gone from the list, new name present.
    listing = _names(_list(auth, hdr).json())
    assert "Garden" not in listing
    assert "Landscaping" in listing

    # Audited complaint_category.renamed with before/after name.
    audits = _category_audits(db, society.id, "complaint_category.renamed")
    assert len(audits) == 1
    assert audits[0].entity_id == cat_id
    assert audits[0].before == {"name": "Garden"}
    assert audits[0].after == {"name": "Landscaping"}


def test_rename_collision_with_active_conflicts(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    cat_id = _create(auth, hdr, "Garden").json()["id"]

    # 'Security' is a seeded active category -> renaming into it is a 409.
    resp = _patch(auth, hdr, cat_id, name="Security")
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "conflict"

    # The category kept its original name (rename rolled back cleanly).
    db.expire_all()
    assert db.get(ComplaintCategory, cat_id).name == "Garden"


def test_rename_to_same_name_is_noop_ok(
    db, society, admin_user, superadmin, auth
):
    """Renaming a category to its OWN current name is allowed (no self-collision)."""
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    cat_id = _create(auth, hdr, "Garden").json()["id"]
    resp = _patch(auth, hdr, cat_id, name="Garden")
    assert resp.status_code == 200, resp.text
    # No rename audit row (the name did not actually change).
    assert _category_audits(db, society.id, "complaint_category.renamed") == []


def test_patch_missing_category_not_found(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    resp = _patch(auth, hdr, 999999, name="Whatever")
    assert resp.status_code == 404, resp.text


def test_patch_empty_body_rejected(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    cat_id = _create(auth, hdr, "Garden").json()["id"]
    # Neither name nor is_active -> 422.
    resp = _patch(auth, hdr, cat_id)
    assert resp.status_code == 422, resp.text


def test_patch_is_active_false_rejected(
    db, society, admin_user, superadmin, auth
):
    """PATCH is rename+reactivate only; is_active=false must use DELETE (422)."""
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    cat_id = _create(auth, hdr, "Garden").json()["id"]
    resp = _patch(auth, hdr, cat_id, is_active=False)
    assert resp.status_code == 422, resp.text
    # It stayed active (nothing was deactivated by the rejected PATCH).
    db.expire_all()
    assert db.get(ComplaintCategory, cat_id).is_active is True


# ===========================================================================
# reactivate (via PATCH is_active=true)
# ===========================================================================


def test_reactivate_deactivated_category(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    cat_id = _create(auth, hdr, "Fire Safety").json()["id"]
    assert _delete(auth, hdr, cat_id).status_code == 200

    # Gone from the active list, then reactivated back into it.
    assert "Fire Safety" not in _names(_list(auth, hdr).json())
    resp = _patch(auth, hdr, cat_id, is_active=True)
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is True
    assert "Fire Safety" in _names(_list(auth, hdr).json())

    # Audited complaint_category.reactivated.
    audits = _category_audits(db, society.id, "complaint_category.reactivated")
    assert len(audits) == 1
    assert audits[0].entity_id == cat_id


def test_reactivate_colliding_with_active_conflicts(
    db, society, admin_user, superadmin, auth
):
    """Reactivating a name now held by another active category is a 409 (§4)."""
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    old_id = _create(auth, hdr, "Roofing").json()["id"]
    assert _delete(auth, hdr, old_id).status_code == 200
    # Reclaim the freed name with a NEW active category.
    assert _create(auth, hdr, "Roofing").status_code == 200

    # Reactivating the OLD row would create a second active 'Roofing' -> 409.
    resp = _patch(auth, hdr, old_id, is_active=True)
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "conflict"

    db.expire_all()
    assert db.get(ComplaintCategory, old_id).is_active is False


# ===========================================================================
# deactivate (DELETE): soft, hidden from list, idempotent, audited
# ===========================================================================


def test_deactivate_category_soft_and_hidden(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    cat_id = _create(auth, hdr, "Painting").json()["id"]

    resp = _delete(auth, hdr, cat_id)
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is False

    # Soft: the row still exists (never hard-deleted) but is inactive.
    db.expire_all()
    row = db.get(ComplaintCategory, cat_id)
    assert row is not None
    assert row.is_active is False

    # No longer in the create-form list.
    assert "Painting" not in _names(_list(auth, hdr).json())

    # Audited complaint_category.deactivated.
    audits = _category_audits(db, society.id, "complaint_category.deactivated")
    assert len(audits) == 1
    assert audits[0].entity_id == cat_id
    assert audits[0].after == {"name": "Painting", "is_active": False}


def test_deactivate_is_idempotent(
    db, society, admin_user, superadmin, auth
):
    """Deactivating an already-inactive category is a no-op (safe retry, §4)."""
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    cat_id = _create(auth, hdr, "Painting").json()["id"]
    assert _delete(auth, hdr, cat_id).status_code == 200

    # A second DELETE succeeds but writes no second audit row.
    second = _delete(auth, hdr, cat_id)
    assert second.status_code == 200, second.text
    assert second.json()["is_active"] is False
    audits = _category_audits(db, society.id, "complaint_category.deactivated")
    assert len(audits) == 1


def test_deactivate_missing_category_not_found(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    resp = _delete(auth, hdr, 999999)
    assert resp.status_code == 404, resp.text


# ===========================================================================
# permission gating: read reads the list; manage_categories gates mutations
# ===========================================================================


def test_resident_can_read_list_but_not_mutate(
    db, society, admin_user, resident_user, superadmin, auth
):
    """resident holds complaints.read (list ok) but not manage_categories (403)."""
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    cat_id = _create(auth, hdr, "Elevator").json()["id"]

    res_hdr = resident_bearer(auth, resident_user)

    # CAN read the create-form list (complaints.read).
    listing = _list(auth, res_hdr)
    assert listing.status_code == 200, listing.text
    assert "Elevator" in _names(listing.json())

    # CANNOT create / rename / deactivate (needs complaints.manage_categories).
    assert _create(auth, res_hdr, "Nope").status_code == 403
    assert _patch(auth, res_hdr, cat_id, name="Nope").status_code == 403
    assert _delete(auth, res_hdr, cat_id).status_code == 403

    # No mutation leaked through the 403s.
    db.expire_all()
    assert db.get(ComplaintCategory, cat_id).name == "Elevator"
    assert db.get(ComplaintCategory, cat_id).is_active is True


# ===========================================================================
# tenant isolation: society A's categories are invisible / untouchable from B
# ===========================================================================


def test_tenant_isolation_between_societies(
    db, society, admin_user, superadmin, auth
):
    hdr_a = setup_complaints(db, society, admin_user, superadmin, auth)
    cat_a = _create(auth, hdr_a, "Elevator")
    assert cat_a.status_code == 200
    cat_a_id = cat_a.json()["id"]

    soc_b, _admin_b, hdr_b = second_society_with_complaints(db, superadmin, auth)

    # B's list is its own fresh 6-default seed — A's 'Elevator' is not visible.
    listing_b = _list(auth, hdr_b).json()
    assert _names(listing_b) == DEFAULT_CATEGORY_NAMES
    assert "Elevator" not in _names(listing_b)

    # B cannot patch or delete A's category (tenant-scoped lookup -> 404).
    assert _patch(auth, hdr_b, cat_a_id, name="Hijacked").status_code == 404
    assert _delete(auth, hdr_b, cat_a_id).status_code == 404

    # A's category is untouched and still active.
    db.expire_all()
    row_a = db.get(ComplaintCategory, cat_a_id)
    assert row_a.society_id == society.id
    assert row_a.name == "Elevator"
    assert row_a.is_active is True

    # B may reuse the SAME name independently (its own society scope).
    dup_in_b = _create(auth, hdr_b, "Elevator")
    assert dup_in_b.status_code == 200, dup_in_b.text
    assert dup_in_b.json()["id"] != cat_a_id

    # A's audit trail carries only A's category events (no B rows).
    a_actions = [
        a for a in audit_actions(db, society.id)
        if a[0].startswith("complaint_category.")
    ]
    assert ("complaint_category.created", "complaint_category", cat_a_id) in a_actions
    assert all(entity_id == cat_a_id for _, _, entity_id in a_actions)
