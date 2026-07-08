"""Wave B tests — Complaints CRUD (raise / edit / withdraw / list / detail).

Drives ``ComplaintsCrudService`` through the real HTTP API (gates + tenant
context) and asserts against the DB + audit_log, per docs/modules/complaints.md
§4/§5/§6/§7. The raiser is a provisioned OWNER login (so ``current_owned_houses``
resolves their house); the society_admin holds ``read_all`` for the admin views.

Coverage:
- raise: single-house infer, explicit house_id, multi-house-requires-id (422),
  house_id-not-owned (403), non-owner (403), inactive/missing category, the
  ``C-000001``/``C-000002`` reference format + increment, the initial NULL->open
  history row, the created event + audit;
- edit: happy while open, locked once in_progress (409), non-raiser (403),
  inactive category (422);
- withdraw: happy (status/withdrawn_at/history/event), non-open (409),
  non-raiser (403);
- list: resident sees only own house, admin read_all sees all + filters
  (status/category/house/q/date) + pagination + newest-first + image counts,
  resident with no house sees empty;
- detail: happy + timeline + resident cross-house 403 + mark_read fired;
- tenant isolation across a second society.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.common import events as event_bus
from app.modules.complaints.events import (
    EVENT_CREATED,
    EVENT_MARK_READ,
    EVENT_WITHDRAWN,
)
from app.modules.complaints.models import (
    Complaint,
    ComplaintCategory,
    ComplaintStatusHistory,
)
from app.platform.models import AuditLog

from tests._complaints_helpers import (
    admin_bearer,
    audit_actions,
    owned_house_for,
    owner_login_bearer,
    second_society_with_complaints,
    setup_complaints,
)
from tests._houses_helpers import _make_building_with_houses, _set_status


# ===========================================================================
# helpers
# ===========================================================================


def _categories(auth, hdr) -> list[dict]:
    """GET /complaints/categories (seeds the 6 system defaults on first access)."""
    resp = auth.client.get("/complaints/categories", headers=hdr)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _category_id(auth, hdr, name="Plumbing") -> int:
    for c in _categories(auth, hdr):
        if c["name"] == name:
            return c["id"]
    raise AssertionError(f"category {name!r} not seeded")


def _raise(auth, hdr, category_id, *, title="Leak", description="Tap leaks", house_id=None):
    body = {"category_id": category_id, "title": title, "description": description}
    if house_id is not None:
        body["house_id"] = house_id
    return auth.client.post("/complaints", headers=hdr, json=body)


def _raiser(auth, db, admin_hdr, *, email):
    """Provision an owner login tied to one owned house; return (bearer, user, hid)."""
    hid = owned_house_for(auth, admin_hdr, email=email)
    bearer, user = owner_login_bearer(auth, db, email=email)
    return bearer, user, hid


def _set_owner(auth, admin_hdr, house_id, *, email, full_name="Owner"):
    """Set an EXISTING house to ``owned`` by ``email`` (no second onboarding pass —
    re-running ``/onboarding/type`` + ``/onboarding/buildings`` 409s)."""
    owner = {
        "full_name": full_name,
        "email": email,
        "contact_number": "555-0002",
        "persons_living": 2,
    }
    assert _set_status(auth, admin_hdr, house_id, "owned", owner).status_code == 200


def _two_owner_houses(auth, db, admin_hdr, *, email_a="raiser@x.com", email_b="other@x.com"):
    """One building, two houses, two distinct owner logins.

    Returns ``(bearer_a, user_a, hid_a, bearer_b, user_b, hid_b)`` — the pattern
    every "second owner / cross-house" spec needs without a duplicate onboarding.
    """
    houses = _make_building_with_houses(auth, admin_hdr)
    hid_a, hid_b = houses[0]["id"], houses[1]["id"]
    _set_owner(auth, admin_hdr, hid_a, email=email_a, full_name="Owner A")
    _set_owner(auth, admin_hdr, hid_b, email=email_b, full_name="Owner B")
    bearer_a, user_a = owner_login_bearer(auth, db, email=email_a)
    bearer_b, user_b = owner_login_bearer(auth, db, email=email_b)
    return bearer_a, user_a, hid_a, bearer_b, user_b, hid_b


@pytest.fixture
def captured_events():
    """Subscribe capture handlers to the complaints events for the test, and
    clean them up after (events are process-global)."""
    seen: dict[str, list[dict]] = {
        EVENT_CREATED: [],
        EVENT_WITHDRAWN: [],
        EVENT_MARK_READ: [],
    }
    handlers = {}
    for name in seen:
        def _make(bucket):
            def _h(payload):
                bucket.append(payload)

            return _h

        h = _make(seen[name])
        handlers[name] = h
        event_bus.subscribe(name, h)
    try:
        yield seen
    finally:
        for name, h in handlers.items():
            event_bus.unsubscribe(name, h)


# ===========================================================================
# raise
# ===========================================================================


def test_raise_single_house_infers_house(auth, db, society, admin_user, superadmin, captured_events):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    r_hdr, raiser, hid = _raiser(auth, db, hdr, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)

    resp = _raise(auth, r_hdr, cat)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["house_id"] == hid
    assert body["raised_by"] == raiser.id
    assert body["status"] == "open"
    assert body["reference"] == "C-000001"
    assert body["category_id"] == cat
    assert body["category_name"] == "Plumbing"
    # display code from the building house helper (A-101).
    assert body["house_display_code"] == "A-101"

    # DB state.
    row = db.query(Complaint).filter(Complaint.id == body["id"]).one()
    assert row.house_id == hid and row.raised_by == raiser.id and row.status == "open"

    # Initial NULL -> open history row.
    hist = (
        db.query(ComplaintStatusHistory)
        .filter(ComplaintStatusHistory.complaint_id == body["id"])
        .all()
    )
    assert len(hist) == 1
    assert hist[0].from_status is None and hist[0].to_status == "open"
    assert hist[0].changed_by == raiser.id

    # Audit + event.
    assert ("complaint.created", "complaint", body["id"]) in audit_actions(db, society.id)
    assert len(captured_events[EVENT_CREATED]) == 1
    ev = captured_events[EVENT_CREATED][0]
    assert ev["complaint_id"] == body["id"] and ev["reference"] == "C-000001"
    assert ev["house_id"] == hid and ev["raised_by"] == raiser.id


def test_raise_explicit_house_id_owned(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    r_hdr, _raiser_u, hid = _raiser(auth, db, hdr, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)

    resp = _raise(auth, r_hdr, cat, house_id=hid)
    assert resp.status_code == 200, resp.text
    assert resp.json()["house_id"] == hid


def test_reference_increments(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    r_hdr, _u, _hid = _raiser(auth, db, hdr, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)

    first = _raise(auth, r_hdr, cat)
    second = _raise(auth, r_hdr, cat)
    assert first.json()["reference"] == "C-000001"
    assert second.json()["reference"] == "C-000002"


def test_raise_multi_house_requires_house_id(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    # Two houses owned by the SAME owner email → owns >1.
    houses = _make_building_with_houses(auth, hdr)
    owner = {
        "full_name": "Owner One",
        "email": "raiser@x.com",
        "contact_number": "555-0001",
        "persons_living": 2,
    }
    for h in houses:
        assert _set_status(auth, hdr, h["id"], "owned", owner).status_code == 200
    r_hdr, _u = owner_login_bearer(auth, db, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)

    resp = _raise(auth, r_hdr, cat)  # no house_id, owns 2
    assert resp.status_code == 422, resp.text

    # Naming one of them succeeds.
    ok = _raise(auth, r_hdr, cat, house_id=houses[0]["id"])
    assert ok.status_code == 200, ok.text
    assert ok.json()["house_id"] == houses[0]["id"]


def test_raise_house_id_not_owned(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    r_hdr, _u, hid = _raiser(auth, db, hdr, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)

    resp = _raise(auth, r_hdr, cat, house_id=hid + 999)
    assert resp.status_code == 403, resp.text


def test_raise_non_owner_forbidden(auth, db, society, admin_user, superadmin, resident_user):
    """A resident login with no owned house cannot raise (403)."""
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    # Seed categories via admin, get the id.
    cat = _category_id(auth, hdr)
    # resident_user is provisioned resident but owns no house.
    from tests._complaints_helpers import resident_bearer

    r_hdr = resident_bearer(auth, resident_user)
    resp = _raise(auth, r_hdr, cat)
    assert resp.status_code == 403, resp.text


def test_raise_missing_category(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    r_hdr, _u, _hid = _raiser(auth, db, hdr, email="raiser@x.com")
    _category_id(auth, r_hdr)  # ensure seed
    resp = _raise(auth, r_hdr, 999999)
    assert resp.status_code == 404, resp.text


def test_raise_inactive_category(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    r_hdr, _u, _hid = _raiser(auth, db, hdr, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    # Deactivate the category via admin route.
    resp = auth.client.delete(f"/complaints/categories/{cat}", headers=hdr)
    assert resp.status_code == 200, resp.text

    bad = _raise(auth, r_hdr, cat)
    assert bad.status_code == 422, bad.text


# ===========================================================================
# edit
# ===========================================================================


def test_edit_while_open(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    r_hdr, _u, _hid = _raiser(auth, db, hdr, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    other_cat = _category_id(auth, r_hdr, name="Electrical")
    cid = _raise(auth, r_hdr, cat).json()["id"]

    resp = auth.client.patch(
        f"/complaints/{cid}",
        headers=r_hdr,
        json={"title": "New title", "category_id": other_cat},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "New title"
    assert body["category_id"] == other_cat
    assert body["category_name"] == "Electrical"

    row = db.query(Complaint).filter(Complaint.id == cid).one()
    assert row.title == "New title" and row.category_id == other_cat
    assert ("complaint.updated", "complaint", cid) in audit_actions(db, society.id)


def test_edit_requires_a_field(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    r_hdr, _u, _hid = _raiser(auth, db, hdr, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = _raise(auth, r_hdr, cat).json()["id"]

    resp = auth.client.patch(f"/complaints/{cid}", headers=r_hdr, json={})
    assert resp.status_code == 422, resp.text


def test_edit_locked_once_in_progress(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    r_hdr, _u, _hid = _raiser(auth, db, hdr, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = _raise(auth, r_hdr, cat).json()["id"]

    # Wave C's status endpoint isn't under test here; mutate the row directly to
    # simulate the admin having moved it to in_progress (self-contained).
    row = db.query(Complaint).filter(Complaint.id == cid).one()
    row.status = "in_progress"
    db.commit()

    resp = auth.client.patch(
        f"/complaints/{cid}", headers=r_hdr, json={"title": "nope"}
    )
    assert resp.status_code == 409, resp.text


def test_edit_by_non_raiser_forbidden(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    r_hdr, _u, _hid, o2_hdr, _o2, _h2 = _two_owner_houses(auth, db, hdr)
    cat = _category_id(auth, r_hdr)
    cid = _raise(auth, r_hdr, cat).json()["id"]

    # A different provisioned owner (holds complaints.create) is not the raiser.
    resp = auth.client.patch(
        f"/complaints/{cid}", headers=o2_hdr, json={"title": "hijack"}
    )
    assert resp.status_code == 403, resp.text


def test_edit_to_inactive_category(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    r_hdr, _u, _hid = _raiser(auth, db, hdr, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    dead = _category_id(auth, r_hdr, name="Cleaning")
    cid = _raise(auth, r_hdr, cat).json()["id"]

    assert auth.client.delete(
        f"/complaints/categories/{dead}", headers=hdr
    ).status_code == 200

    resp = auth.client.patch(
        f"/complaints/{cid}", headers=r_hdr, json={"category_id": dead}
    )
    assert resp.status_code == 422, resp.text


# ===========================================================================
# withdraw
# ===========================================================================


def test_withdraw_while_open(auth, db, society, admin_user, superadmin, captured_events):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    r_hdr, _u, hid = _raiser(auth, db, hdr, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = _raise(auth, r_hdr, cat).json()["id"]

    resp = auth.client.post(f"/complaints/{cid}/withdraw", headers=r_hdr)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "withdrawn"
    assert body["withdrawn_at"] is not None

    row = db.query(Complaint).filter(Complaint.id == cid).one()
    assert row.status == "withdrawn" and row.withdrawn_at is not None

    hist = (
        db.query(ComplaintStatusHistory)
        .filter(ComplaintStatusHistory.complaint_id == cid)
        .order_by(ComplaintStatusHistory.id)
        .all()
    )
    # NULL->open then open->withdrawn.
    assert [(h.from_status, h.to_status) for h in hist] == [
        (None, "open"),
        ("open", "withdrawn"),
    ]
    assert ("complaint.withdrawn", "complaint", cid) in audit_actions(db, society.id)
    assert len(captured_events[EVENT_WITHDRAWN]) == 1
    assert captured_events[EVENT_WITHDRAWN][0]["complaint_id"] == cid


def test_withdraw_non_open_conflict(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    r_hdr, _u, _hid = _raiser(auth, db, hdr, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = _raise(auth, r_hdr, cat).json()["id"]

    row = db.query(Complaint).filter(Complaint.id == cid).one()
    row.status = "in_progress"
    db.commit()

    resp = auth.client.post(f"/complaints/{cid}/withdraw", headers=r_hdr)
    assert resp.status_code == 409, resp.text


def test_withdraw_by_non_raiser_forbidden(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    r_hdr, _u, _hid, o2_hdr, _o2, _h2 = _two_owner_houses(auth, db, hdr)
    cat = _category_id(auth, r_hdr)
    cid = _raise(auth, r_hdr, cat).json()["id"]

    resp = auth.client.post(f"/complaints/{cid}/withdraw", headers=o2_hdr)
    assert resp.status_code == 403, resp.text


# ===========================================================================
# list
# ===========================================================================


def test_list_resident_sees_only_own_house(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    r_hdr, _u, hid, o2_hdr, _o2, _h2 = _two_owner_houses(auth, db, hdr)
    cat = _category_id(auth, r_hdr)
    mine = _raise(auth, r_hdr, cat, title="mine").json()["id"]

    # A second owner raises on their own house.
    _raise(auth, o2_hdr, cat, title="theirs")

    resp = auth.client.get("/complaints", headers=r_hdr)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    ids = [it["id"] for it in body["items"]]
    assert ids == [mine]


def test_list_resident_no_house_empty(auth, db, society, admin_user, superadmin, resident_user):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    _category_id(auth, hdr)
    from tests._complaints_helpers import resident_bearer

    r_hdr = resident_bearer(auth, resident_user)
    resp = auth.client.get("/complaints", headers=r_hdr)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"items": [], "total": 0}


def test_list_admin_read_all_with_filters(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    r_hdr, _u, hid = _raiser(auth, db, hdr, email="raiser@x.com")
    plumbing = _category_id(auth, r_hdr)
    electrical = _category_id(auth, r_hdr, name="Electrical")

    c1 = _raise(auth, r_hdr, plumbing, title="Alpha leak").json()
    c2 = _raise(auth, r_hdr, electrical, title="Beta wiring").json()
    c3 = _raise(auth, r_hdr, plumbing, title="Gamma drip").json()

    # Admin (read_all) sees all three, newest-first.
    resp = auth.client.get("/complaints", headers=hdr)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    assert [it["id"] for it in body["items"]] == [c3["id"], c2["id"], c1["id"]]

    # Filter by category.
    resp = auth.client.get(
        "/complaints", headers=hdr, params={"category_id": electrical}
    )
    assert [it["id"] for it in resp.json()["items"]] == [c2["id"]]

    # Filter by house.
    resp = auth.client.get("/complaints", headers=hdr, params={"house_id": hid})
    assert resp.json()["total"] == 3

    # q on title.
    resp = auth.client.get("/complaints", headers=hdr, params={"q": "beta"})
    assert [it["id"] for it in resp.json()["items"]] == [c2["id"]]

    # q on reference.
    resp = auth.client.get(
        "/complaints", headers=hdr, params={"q": c1["reference"]}
    )
    assert [it["id"] for it in resp.json()["items"]] == [c1["id"]]

    # Status filter (withdraw c1, then filter for it).
    auth.client.post(f"/complaints/{c1['id']}/withdraw", headers=r_hdr)
    resp = auth.client.get(
        "/complaints", headers=hdr, params={"status": "withdrawn"}
    )
    assert [it["id"] for it in resp.json()["items"]] == [c1["id"]]

    # date_to in the past excludes everything created today.
    resp = auth.client.get(
        "/complaints", headers=hdr, params={"date_to": "2000-01-01"}
    )
    assert resp.json()["total"] == 0


def test_list_pagination_and_image_counts(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    r_hdr, _u, hid = _raiser(auth, db, hdr, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    ids = [_raise(auth, r_hdr, cat, title=f"c{i}").json()["id"] for i in range(3)]

    # Page 1: size 2 → newest two.
    resp = auth.client.get("/complaints", headers=hdr, params={"page": 1, "page_size": 2})
    body = resp.json()
    assert body["total"] == 3
    assert [it["id"] for it in body["items"]] == [ids[2], ids[1]]
    # Page 2.
    resp = auth.client.get("/complaints", headers=hdr, params={"page": 2, "page_size": 2})
    assert [it["id"] for it in resp.json()["items"]] == [ids[0]]

    # Image counts: attach a report image via the DB (Wave D route not under test),
    # then confirm the list surfaces the count.
    from tests._houses_helpers import _make_vault_doc
    from app.modules.complaints.models import ComplaintImage

    doc_id = _make_vault_doc(db, society.id, filename="report.jpg")
    db.add(
        ComplaintImage(
            society_id=society.id,
            complaint_id=ids[0],
            kind="report",
            vault_document_id=doc_id,
            added_by=None,
        )
    )
    db.commit()
    resp = auth.client.get("/complaints", headers=hdr, params={"house_id": hid})
    counts = {it["id"]: it["report_image_count"] for it in resp.json()["items"]}
    assert counts[ids[0]] == 1
    assert counts[ids[1]] == 0


# ===========================================================================
# detail
# ===========================================================================


def test_detail_happy_with_timeline_and_mark_read(
    auth, db, society, admin_user, superadmin, captured_events
):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    r_hdr, raiser, hid = _raiser(auth, db, hdr, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = _raise(auth, r_hdr, cat).json()["id"]

    resp = auth.client.get(f"/complaints/{cid}", headers=r_hdr)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == cid
    assert body["house_display_code"] == "A-101"
    assert body["category_name"] == "Plumbing"
    assert len(body["timeline"]) == 1
    assert body["timeline"][0]["from_status"] is None
    assert body["timeline"][0]["to_status"] == "open"
    assert body["images"] == []

    # Clear-on-read fired for the caller + this entity.
    reads = [
        p
        for p in captured_events[EVENT_MARK_READ]
        if p["entity_type"] == "complaint" and p["entity_id"] == cid
    ]
    assert reads and reads[-1]["user_id"] == raiser.id


def test_detail_not_found(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    resp = auth.client.get("/complaints/999999", headers=hdr)
    assert resp.status_code == 404, resp.text


def test_detail_resident_cross_house_forbidden(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    r_hdr, _u, _hid, o2_hdr, _o2, _h2 = _two_owner_houses(auth, db, hdr)
    cat = _category_id(auth, r_hdr)
    cid = _raise(auth, r_hdr, cat).json()["id"]

    # A different owner may not open the first raiser's complaint.
    resp = auth.client.get(f"/complaints/{cid}", headers=o2_hdr)
    assert resp.status_code == 403, resp.text


def test_detail_admin_can_read_any(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    r_hdr, _u, _hid = _raiser(auth, db, hdr, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = _raise(auth, r_hdr, cat).json()["id"]

    resp = auth.client.get(f"/complaints/{cid}", headers=hdr)
    assert resp.status_code == 200, resp.text


# ===========================================================================
# tenant isolation
# ===========================================================================


def test_tenant_isolation(auth, db, society, admin_user, superadmin):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    r_hdr, _u, _hid = _raiser(auth, db, hdr, email="raiser@x.com")
    cat = _category_id(auth, r_hdr)
    cid = _raise(auth, r_hdr, cat).json()["id"]

    # Society B: an independent admin cannot see or open A's complaint.
    _soc_b, _admin_b, hdr_b = second_society_with_complaints(db, superadmin, auth)
    resp = auth.client.get("/complaints", headers=hdr_b)
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 0

    resp = auth.client.get(f"/complaints/{cid}", headers=hdr_b)
    assert resp.status_code == 404, resp.text

    # Society A still has exactly its own complaint; categories are scoped to A.
    assert db.query(Complaint).filter(Complaint.society_id == society.id).count() == 1
    assert (
        db.query(ComplaintCategory)
        .filter(ComplaintCategory.society_id == society.id)
        .count()
        == 6
    )
