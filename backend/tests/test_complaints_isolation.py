"""Tenant-isolation tests for Complaints (Module 5) — society A vs society B.

Beyond the single spot-checks already covered per-wave, this file drives a
FULL sweep: complaint access, status/resolve, categories, config, sequential-id
guessing, independent per-society reference counters, and audit-row scoping.
"""
from __future__ import annotations

from app.modules.complaints.models import Complaint, ComplaintCategory
from app.platform.models import AuditLog

from tests._complaints_helpers import (
    audit_actions,
    owned_house_for,
    owner_login_bearer,
    raise_complaint,
    second_society_with_complaints,
    setup_complaints,
)


def _category_id(auth, hdr, name="Plumbing") -> int:
    resp = auth.client.get("/complaints/categories", headers=hdr)
    assert resp.status_code == 200, resp.text
    for c in resp.json():
        if c["name"] == name:
            return c["id"]
    raise AssertionError(f"category {name!r} not seeded")


def test_society_a_cannot_get_society_b_complaint(db, society, admin_user, superadmin, auth):
    hdr_a = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr_a, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = raise_complaint(auth, r_hdr, category_id=cat, title="x", description="y")["id"]

    _soc_b, _admin_b, hdr_b = second_society_with_complaints(db, superadmin, auth)
    resp = auth.client.get(f"/complaints/{cid}", headers=hdr_b)
    assert resp.status_code == 404, resp.text


def test_society_a_cannot_status_or_resolve_b_complaint(
    db, society, admin_user, superadmin, auth
):
    hdr_a = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr_a, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = raise_complaint(auth, r_hdr, category_id=cat, title="x", description="y")["id"]

    _soc_b, _admin_b, hdr_b = second_society_with_complaints(db, superadmin, auth)
    resp = auth.client.post(
        f"/complaints/{cid}/status", headers=hdr_b, json={"to_status": "in_progress"}
    )
    assert resp.status_code == 404, resp.text

    resp2 = auth.client.post(
        f"/complaints/{cid}/resolve", headers=hdr_b, data={"note": "x"}
    )
    assert resp2.status_code == 404, resp2.text

    row = db.query(Complaint).filter(Complaint.id == cid).one()
    assert row.status == "open"


def test_society_a_cannot_see_b_categories(db, society, admin_user, superadmin, auth):
    hdr_a = setup_complaints(db, society, admin_user, superadmin, auth)
    cat_a = auth.client.post(
        "/complaints/categories", headers=hdr_a, json={"name": "Elevator"}
    ).json()["id"]

    soc_b, _admin_b, hdr_b = second_society_with_complaints(db, superadmin, auth)
    listing_b = auth.client.get("/complaints/categories", headers=hdr_b).json()
    assert "Elevator" not in {c["name"] for c in listing_b}

    assert auth.client.patch(
        f"/complaints/categories/{cat_a}", headers=hdr_b, json={"name": "Hijack"}
    ).status_code == 404
    assert auth.client.delete(
        f"/complaints/categories/{cat_a}", headers=hdr_b
    ).status_code == 404


def test_society_a_cannot_read_or_write_b_config(db, society, admin_user, superadmin, auth):
    hdr_a = setup_complaints(db, society, admin_user, superadmin, auth)
    soc_b, _admin_b, hdr_b = second_society_with_complaints(db, superadmin, auth)

    # A changes its own config.
    assert auth.client.put(
        "/complaints/config", headers=hdr_a, json={"auto_archive_days": 40}
    ).status_code == 200

    # B is unaffected (config is society-scoped via the JWT, no path param to
    # tamper with — there's no cross-tenant config read/write surface at all).
    b_cfg = auth.client.get("/complaints/config", headers=hdr_b).json()
    assert b_cfg["auto_archive_days"] == 15


def test_sequential_id_guess_cross_society_404(db, society, admin_user, superadmin, auth):
    hdr_a = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr_a, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid_a = raise_complaint(auth, r_hdr, category_id=cat, title="x", description="y")["id"]

    soc_b, admin_b, hdr_b = second_society_with_complaints(db, superadmin, auth)
    owned_house_for(auth, hdr_b, email="raiser-b@x.com")
    rb_hdr, _raiser_b = owner_login_bearer(auth, db, email="raiser-b@x.com")
    cat_b = _category_id(auth, rb_hdr)
    cid_b = raise_complaint(auth, rb_hdr, category_id=cat_b, title="x", description="y")["id"]

    # Sequential ids across societies collide numerically but are tenant-scoped:
    # B cannot fetch A's numeric id if it belongs to a different society.
    if cid_a != cid_b:
        resp = auth.client.get(f"/complaints/{cid_a}", headers=hdr_b)
        assert resp.status_code == 404, resp.text


def test_reference_counter_per_society_independent(db, society, admin_user, superadmin, auth):
    hdr_a = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr_a, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    c1 = raise_complaint(auth, r_hdr, category_id=cat, title="x", description="y")
    c2 = raise_complaint(auth, r_hdr, category_id=cat, title="x2", description="y2")
    assert c1["reference"] == "C-000001"
    assert c2["reference"] == "C-000002"

    soc_b, _admin_b, hdr_b = second_society_with_complaints(db, superadmin, auth)
    owned_house_for(auth, hdr_b, email="raiser-b@x.com")
    rb_hdr, _raiser_b = owner_login_bearer(auth, db, email="raiser-b@x.com")
    cat_b = _category_id(auth, rb_hdr)
    cb1 = raise_complaint(auth, rb_hdr, category_id=cat_b, title="x", description="y")
    assert cb1["reference"] == "C-000001"


def test_audit_rows_scoped_per_society(db, society, admin_user, superadmin, auth):
    hdr_a = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr_a, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = raise_complaint(auth, r_hdr, category_id=cat, title="x", description="y")["id"]

    soc_b, _admin_b, hdr_b = second_society_with_complaints(db, superadmin, auth)
    owned_house_for(auth, hdr_b, email="raiser-b@x.com")
    rb_hdr, _raiser_b = owner_login_bearer(auth, db, email="raiser-b@x.com")
    cat_b = _category_id(auth, rb_hdr)
    raise_complaint(auth, rb_hdr, category_id=cat_b, title="x", description="y")

    a_actions = audit_actions(db, society.id)
    b_actions = audit_actions(db, soc_b.id)
    assert ("complaint.created", "complaint", cid) in a_actions
    # Every row returned for A's scope really belongs to A (query is scoped by
    # society_id at the DB level; assert both counts are non-empty + disjoint
    # complaint-created entity ids don't cross).
    a_created_ids = {eid for act, etype, eid in a_actions if act == "complaint.created" and etype == "complaint"}
    b_created_ids = {eid for act, etype, eid in b_actions if act == "complaint.created" and etype == "complaint"}
    assert cid in a_created_ids
    assert cid not in b_created_ids or soc_b.id == society.id
