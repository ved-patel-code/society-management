"""Edge-case tests for Complaints (Module 5): state-machine boundaries, the
reference serial, category deactivation edges, multi-house raise edges, config
partial-merge, and proof-image immutability outside resolve.
"""
from __future__ import annotations

import pytest

from app.modules.complaints.models import Complaint
from app.modules.complaints.schemas import ALLOWED_TRANSITIONS, COMPLAINT_STATUSES
from app.modules.complaints.services import support
from app.modules.complaints.repository import ComplaintRepository

from tests._complaints_helpers import (
    owned_house_for,
    owner_login_bearer,
    raise_complaint,
    resolve_http,
    setup_complaints,
)
from tests._houses_helpers import _make_building_with_houses, _set_status
from tests._vault_helpers import storage_override  # noqa: F401  (fixture)

pytestmark = pytest.mark.usefixtures("storage_override")


def _category_id(auth, hdr, name="Plumbing") -> int:
    resp = auth.client.get("/complaints/categories", headers=hdr)
    assert resp.status_code == 200, resp.text
    for c in resp.json():
        if c["name"] == name:
            return c["id"]
    raise AssertionError(f"category {name!r} not seeded")


def _seed_complaint_at(db, society_id, house_id, raised_by, category_id, status):
    """Insert + walk a complaint to ``status`` via the frozen write choke-point."""
    repo = ComplaintRepository(db)
    reference = repo.allocate_reference(society_id)
    complaint = Complaint(
        society_id=society_id,
        reference=reference,
        house_id=house_id,
        raised_by=raised_by,
        category_id=category_id,
        title="Edge case",
        description="edge",
        status="open",
    )
    repo.add_complaint(complaint)
    support.record_initial(repo, complaint, changed_by=raised_by)
    path = {
        "open": [],
        "in_progress": ["in_progress"],
        "resolved": ["in_progress", "resolved"],
        "closed": ["in_progress", "resolved", "closed"],
        "withdrawn": ["withdrawn"],
        "archived": ["in_progress", "resolved", "closed", "archived"],
    }[status]
    for step in path:
        support.record_transition(
            repo, complaint, to_status=step, note=None, changed_by=raised_by
        )
    db.commit()
    db.refresh(complaint)
    return complaint


def test_reference_serial_run_no_gaps_zero_padded(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)

    refs = [
        raise_complaint(auth, r_hdr, category_id=cat, title=f"c{i}", description="y")["reference"]
        for i in range(10)
    ]
    assert refs == [f"C-{i:06d}" for i in range(1, 11)]


def test_image_ops_locked_after_in_progress_409(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = raise_complaint(auth, r_hdr, category_id=cat, title="x", description="y")["id"]

    add1 = auth.client.post(
        f"/complaints/{cid}/images",
        headers=r_hdr,
        files={"file": ("r.jpg", b"x" * 10, "image/jpeg")},
    )
    assert add1.status_code == 200, add1.text
    image_id = add1.json()["id"]

    auth.client.post(
        f"/complaints/{cid}/status", headers=hdr, json={"to_status": "in_progress"}
    )

    add2 = auth.client.post(
        f"/complaints/{cid}/images",
        headers=r_hdr,
        files={"file": ("r2.jpg", b"x" * 10, "image/jpeg")},
    )
    assert add2.status_code == 409, add2.text

    remove = auth.client.delete(
        f"/complaints/{cid}/images/{image_id}", headers=r_hdr
    )
    assert remove.status_code == 409, remove.text


def test_withdraw_non_open_409(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = raise_complaint(auth, r_hdr, category_id=cat, title="x", description="y")["id"]
    auth.client.post(
        f"/complaints/{cid}/status", headers=hdr, json={"to_status": "in_progress"}
    )
    resp = auth.client.post(f"/complaints/{cid}/withdraw", headers=r_hdr)
    assert resp.status_code == 409, resp.text


@pytest.mark.parametrize("from_status", sorted(COMPLAINT_STATUSES))
def test_illegal_transitions_from_every_state_409(
    auth, db, society, admin_user, superadmin, from_status
):
    """Table-driven from ALLOWED_TRANSITIONS: any target NOT in the legal set is
    409 (via the admin /status route), EXCEPT resolved (which is 422 — must go
    through /resolve, not /status)."""
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    hid = owned_house_for(auth, hdr, email="raiser@x.com")
    _r_hdr, raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, hdr)
    complaint = _seed_complaint_at(
        db, society.id, hid, raiser.id, cat, from_status
    )

    legal = ALLOWED_TRANSITIONS[from_status]
    illegal_targets = COMPLAINT_STATUSES - legal - {from_status}
    for target in sorted(illegal_targets):
        if target == "resolved":
            # Resolving must go through /resolve; /status rejects it -> 422.
            resp = auth.client.post(
                f"/complaints/{complaint.id}/status",
                headers=hdr,
                json={"to_status": target},
            )
            assert resp.status_code == 422, (from_status, target, resp.text)
            continue
        if target not in ("in_progress", "closed"):
            # archived/withdrawn are rejected by the request schema itself
            # (ADMIN_TARGET_STATUSES) -> also 422, not the transition-table 409.
            resp = auth.client.post(
                f"/complaints/{complaint.id}/status",
                headers=hdr,
                json={"to_status": target},
            )
            assert resp.status_code == 422, (from_status, target, resp.text)
            continue
        resp = auth.client.post(
            f"/complaints/{complaint.id}/status",
            headers=hdr,
            json={"to_status": target},
        )
        assert resp.status_code == 409, (from_status, target, resp.text)


def test_archived_is_terminal(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    hid = owned_house_for(auth, hdr, email="raiser@x.com")
    _r_hdr, raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, hdr)
    complaint = _seed_complaint_at(db, society.id, hid, raiser.id, cat, "closed")

    from app.modules.complaints.services.jobs import _run_for_societies
    from app.common.time import utcnow
    from datetime import timedelta

    _run_for_societies(db, [society.id], utcnow() + timedelta(days=30))
    db.expire_all()
    row = db.query(Complaint).filter(Complaint.id == complaint.id).one()
    assert row.status == "archived"

    for target in ("open", "in_progress", "resolved", "closed", "withdrawn"):
        resp = auth.client.post(
            f"/complaints/{complaint.id}/status",
            headers=hdr,
            json={"to_status": target} if target != "resolved" else {"to_status": "resolved"},
        )
        assert resp.status_code in (409, 422), (target, resp.text)


def test_withdrawn_is_terminal(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    hid = owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, hdr)
    complaint = _seed_complaint_at(db, society.id, hid, raiser.id, cat, "withdrawn")

    resp = auth.client.post(f"/complaints/{complaint.id}/withdraw", headers=r_hdr)
    assert resp.status_code == 409, resp.text

    resp2 = auth.client.post(
        f"/complaints/{complaint.id}/status",
        headers=hdr,
        json={"to_status": "in_progress"},
    )
    assert resp2.status_code == 409, resp2.text


def test_reopen_clears_resolved_at_and_allows_reresolve(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    hid = owned_house_for(auth, hdr, email="raiser@x.com")
    _r_hdr, raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, hdr)
    complaint = _seed_complaint_at(db, society.id, hid, raiser.id, cat, "resolved")
    assert complaint.resolved_at is not None

    resp = auth.client.post(
        f"/complaints/{complaint.id}/status",
        headers=hdr,
        json={"to_status": "in_progress"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["resolved_at"] is None

    re_resolve = resolve_http(auth, hdr, complaint.id, note="fixed again")
    assert re_resolve.status_code == 200, re_resolve.text
    assert re_resolve.json()["resolved_at"] is not None


def test_deactivated_category_hidden_from_list_but_kept_on_existing(
    auth, db, society, admin_user, superadmin
):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = raise_complaint(auth, r_hdr, category_id=cat, title="x", description="y")["id"]

    assert auth.client.delete(f"/complaints/categories/{cat}", headers=hdr).status_code == 200

    listing = auth.client.get("/complaints/categories", headers=hdr).json()
    assert cat not in {c["id"] for c in listing}

    detail = auth.client.get(f"/complaints/{cid}", headers=hdr)
    assert detail.status_code == 200, detail.text
    assert detail.json()["category_id"] == cat


def test_raise_against_deactivated_category_422(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, _raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    assert auth.client.delete(f"/complaints/categories/{cat}", headers=hdr).status_code == 200

    resp = auth.client.post(
        "/complaints",
        headers=r_hdr,
        json={"category_id": cat, "title": "x", "description": "y"},
    )
    assert resp.status_code == 422, resp.text


def test_multi_house_raise_requires_house_id_422(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    owner = {"full_name": "Owner", "email": "multi@x.com", "contact_number": "1", "persons_living": 1}
    for h in houses:
        assert _set_status(auth, hdr, h["id"], "owned", owner).status_code == 200
    r_hdr, _u = owner_login_bearer(auth, db, email="multi@x.com")
    cat = _category_id(auth, r_hdr)

    resp = auth.client.post(
        "/complaints", headers=r_hdr, json={"category_id": cat, "title": "x", "description": "y"}
    )
    assert resp.status_code == 422, resp.text


def test_multi_house_raise_unowned_house_id_403(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    owner_a = {"full_name": "A", "email": "a2@x.com", "contact_number": "1", "persons_living": 1}
    owner_b = {"full_name": "B", "email": "b2@x.com", "contact_number": "2", "persons_living": 1}
    assert _set_status(auth, hdr, houses[0]["id"], "owned", owner_a).status_code == 200
    assert _set_status(auth, hdr, houses[1]["id"], "owned", owner_b).status_code == 200
    a_hdr, _a = owner_login_bearer(auth, db, email="a2@x.com")
    cat = _category_id(auth, hdr)

    resp = auth.client.post(
        "/complaints",
        headers=a_hdr,
        json={"category_id": cat, "title": "x", "description": "y", "house_id": houses[1]["id"]},
    )
    assert resp.status_code == 403, resp.text


def test_config_partial_merge_unchanged_keys(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    assert auth.client.put(
        "/complaints/config",
        headers=hdr,
        json={"auto_archive_days": 25, "max_report_images": 3, "max_proof_images": 4},
    ).status_code == 200

    resp = auth.client.put(
        "/complaints/config", headers=hdr, json={"max_proof_images": 7}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "auto_archive_days": 25,
        "max_report_images": 3,
        "max_proof_images": 7,
    }


def test_proof_not_removable_or_addable_outside_resolve(auth, db, society, admin_user, superadmin):
    """There is no general proof add/remove endpoint — proof images only ever
    come from /resolve, and there's no route accepting kind=proof directly."""
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    owned_house_for(auth, hdr, email="raiser@x.com")
    r_hdr, raiser = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = raise_complaint(auth, r_hdr, category_id=cat, title="x", description="y")["id"]
    auth.client.post(
        f"/complaints/{cid}/status", headers=hdr, json={"to_status": "in_progress"}
    )
    resolved = resolve_http(
        auth, hdr, cid, note="fixed", files=[("p.jpg", b"p" * 10, "image/jpeg")]
    )
    proof_image_id = resolved.json()["images"][0]["id"]

    # The only image mutation routes are the REPORT ones — DELETE on a proof
    # image id is scoped to kind=report, so it 404s (not removable this way).
    resp = auth.client.delete(
        f"/complaints/{cid}/images/{proof_image_id}", headers=r_hdr
    )
    assert resp.status_code == 404, resp.text

    # Once resolved, images.add_report_image also 409s (locked, not open).
    add_resp = auth.client.post(
        f"/complaints/{cid}/images",
        headers=r_hdr,
        files={"file": ("late.jpg", b"x" * 10, "image/jpeg")},
    )
    assert add_resp.status_code == 409, add_resp.text
