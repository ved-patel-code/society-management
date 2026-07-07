"""Edge-case tests for the House & Occupancy module.

Owner replacement mechanics (close/revoke/provision, orphan-deactivation),
id_proof retention, first_left_empty_on once-only semantics, same-status
reconciliation without history, and current_owner_user_ids scoping.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.modules.houses.models import HouseOccupancy, HouseStatusHistory
from app.platform.models import AuditLog, User, UserRole

from tests._houses_helpers import (
    _audit,
    _make_building_with_houses,
    _occ,
    _owner,
    _set_status,
    _setup,
    _tenant,
)


# ===========================================================================
# owner replacement mechanics
# ===========================================================================

def test_owner_replaced_closes_old_opens_new(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    r1 = _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    old_user_id = r1.json()["owner"]["user_id"]

    r2 = _set_status(auth, hdr, hid, "owned", _owner(email="newowner@x.com", persons_living=2))
    assert r2.status_code == 200, r2.text
    new_user_id = r2.json()["owner"]["user_id"]
    assert new_user_id != old_user_id

    db.expire_all()
    all_owner_rows = _occ(db, hid, "owner", current_only=False)
    assert len(all_owner_rows) == 2
    old_row = next(r for r in all_owner_rows if r.user_id == old_user_id)
    new_row = next(r for r in all_owner_rows if r.user_id == new_user_id)
    assert old_row.is_current is False
    assert old_row.valid_to == date.today()
    assert new_row.is_current is True

    new_user = db.get(User, new_user_id)
    assert new_user.password_state == "must_change"


def test_owner_replaced_audit_before_after_user_ids(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    r1 = _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    old_user_id = r1.json()["owner"]["user_id"]

    r2 = _set_status(auth, hdr, hid, "owned", _owner(email="newowner@x.com", persons_living=2))
    new_user_id = r2.json()["owner"]["user_id"]

    db.expire_all()
    rows = _audit(db, "house.owner_replaced", society_id=society.id, entity_id=hid)
    assert len(rows) == 1
    row = rows[0]
    assert row.before["user_id"] == old_user_id
    assert row.before["email"] == "owner1@x.com"
    assert row.after["user_id"] == new_user_id
    assert row.after["email"] == "newowner@x.com"


def test_replaced_owner_not_deactivated(db, society, admin_user, superadmin, auth):
    """The replaced owner still holds the resident role -> not deactivated."""
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    r1 = _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    old_user_id = r1.json()["owner"]["user_id"]

    _set_status(auth, hdr, hid, "owned", _owner(email="newowner@x.com", persons_living=2))

    db.expire_all()
    old_user = db.get(User, old_user_id)
    assert old_user.is_active is True
    roles = db.query(UserRole).filter(UserRole.user_id == old_user_id).all()
    assert len(roles) >= 1


def test_owner_replaced_emits_access_revoked_and_provisioning_audits(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    r1 = _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    old_user_id = r1.json()["owner"]["user_id"]

    r2 = _set_status(auth, hdr, hid, "owned", _owner(email="newowner@x.com", persons_living=2))
    new_user_id = r2.json()["owner"]["user_id"]

    db.expire_all()
    revoked = _audit(db, "house.access_revoked", entity_id=old_user_id)
    assert len(revoked) == 1
    after = revoked[0].after
    assert after["orphaned"] is False
    assert after["deactivated"] is False

    created = _audit(db, "user.created", entity_id=new_user_id)
    assert len(created) == 1
    assigned = _audit(db, "role.assigned", entity_id=new_user_id)
    assert len(assigned) >= 1


def test_replaced_then_orphan_deactivation_after_role_removed(
    db, society, admin_user, superadmin, auth
):
    """Simulate an owner losing their resident role entirely (e.g. an admin later
    removes it) so a SECOND replacement finds them fully orphaned."""
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr, floors=[{"level": 1, "houses_count": 2}])
    hid1, hid2 = houses[0]["id"], houses[1]["id"]

    # Same owner on both houses so removing one occupancy doesn't orphan them
    # (still current on hid2) -- then close hid2's occupancy directly + strip
    # the resident role to force a genuine orphan on the next replacement.
    r1 = _set_status(auth, hdr, hid1, "owned", _owner(persons_living=1))
    owner_user_id = r1.json()["owner"]["user_id"]

    # Manually close any other current occupancy + remove the resident role,
    # to simulate the account being fully orphaned before the next replace.
    from app.platform.models import Role

    role = db.query(Role).filter(Role.society_id == society.id, Role.key == "resident").one()
    db.query(UserRole).filter(
        UserRole.user_id == owner_user_id, UserRole.role_id == role.id
    ).delete()
    db.commit()

    r2 = _set_status(auth, hdr, hid1, "owned", _owner(email="newowner2@x.com", persons_living=1))
    assert r2.status_code == 200, r2.text

    db.expire_all()
    old_user = db.get(User, owner_user_id)
    assert old_user.is_active is False

    revoked = _audit(db, "house.access_revoked", entity_id=owner_user_id)
    assert len(revoked) == 1
    after = revoked[-1].after
    assert after["orphaned"] is True
    assert after["deactivated"] is True
    # NOTE: revoke_house_access does NOT emit a separate user.deactivated audit row
    # (that action is only emitted by UserProvisioningService.deactivate_user, a
    # different code path). The deactivation here is captured solely via
    # house.access_revoked.after.deactivated above.


def test_replacement_single_txn_no_unique_violation(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    resp = _set_status(auth, hdr, hid, "owned", _owner(email="newowner@x.com", persons_living=1))
    assert resp.status_code == 200, resp.text

    db.expire_all()
    all_rows = _occ(db, hid, "owner", current_only=False)
    current_rows = [r for r in all_rows if r.is_current]
    closed_rows = [r for r in all_rows if not r.is_current]
    assert len(current_rows) == 1
    assert len(closed_rows) == 1


# ===========================================================================
# PATCH-driven owner replacement
# ===========================================================================

def test_patch_owner_email_change_triggers_replacement(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    r1 = _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    old_user_id = r1.json()["owner"]["user_id"]

    resp = auth.client.patch(
        f"/houses/{hid}/occupancy/owner", headers=hdr, json={"email": "changed@x.com"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["owner"]["user_id"] != old_user_id
    assert resp.json()["owner"]["email"] == "changed@x.com"

    db.expire_all()
    replaced = _audit(db, "house.owner_replaced", society_id=society.id, entity_id=hid)
    assert len(replaced) == 1


def test_patch_email_change_carries_over_unchanged_fields_incl_id_proof(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(
        auth, hdr, hid, "owned",
        _owner(persons_living=3, id_proof_type="pan", id_proof_document_id=9),
    )
    resp = auth.client.patch(
        f"/houses/{hid}/occupancy/owner", headers=hdr, json={"email": "changed2@x.com"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()["owner"]
    assert body["email"] == "changed2@x.com"
    assert body["full_name"] == "Owner One"
    assert body["persons_living"] == 3
    assert body["id_proof_type"] == "pan"
    assert body["id_proof_document_id"] == 9


def test_patch_email_same_after_normalize_is_plain_edit_not_replace(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    r1 = _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    old_user_id = r1.json()["owner"]["user_id"]

    resp = auth.client.patch(
        f"/houses/{hid}/occupancy/owner", headers=hdr,
        json={"email": "  Owner1@X.com  ", "contact_number": "777-0000"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["owner"]["user_id"] == old_user_id
    assert resp.json()["owner"]["contact_number"] == "777-0000"

    db.expire_all()
    replaced = _audit(db, "house.owner_replaced", society_id=society.id, entity_id=hid)
    assert len(replaced) == 0


def test_tenant_replacement_is_edit_in_place(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "rented", _owner(), _tenant())
    resp = auth.client.patch(
        f"/houses/{hid}/occupancy/tenant", headers=hdr, json={"email": "newtenant@x.com"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["tenant"]["email"] == "newtenant@x.com"
    assert resp.json()["tenant"]["user_id"] is None

    db.expire_all()
    rows = _occ(db, hid, "tenant", current_only=False)
    assert len(rows) == 1  # same row, no new row


# ===========================================================================
# leaving rented closes tenant
# ===========================================================================

def test_leaving_rented_to_to_let_closes_tenant(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "rented", _owner(), _tenant())
    resp = _set_status(auth, hdr, hid, "to_let", _owner())
    assert resp.status_code == 200, resp.text
    assert resp.json()["tenant"] is None
    db.expire_all()
    closed = _occ(db, hid, "tenant", current_only=False)
    assert len(closed) == 1
    assert closed[0].is_current is False


def test_leaving_rented_to_for_sale_closes_tenant(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "rented", _owner(), _tenant())
    resp = _set_status(auth, hdr, hid, "for_sale", _owner())
    assert resp.status_code == 200, resp.text
    assert resp.json()["tenant"] is None


def test_owned_rented_owned_tenant_lifecycle(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=2))
    _set_status(auth, hdr, hid, "rented", _owner(persons_living=2), _tenant())
    resp = _set_status(auth, hdr, hid, "owned", _owner(persons_living=3))
    assert resp.status_code == 200, resp.text
    assert resp.json()["tenant"] is None
    db.expire_all()
    tenant_rows = _occ(db, hid, "tenant", current_only=False)
    assert len(tenant_rows) == 1
    assert tenant_rows[0].is_current is False


# ===========================================================================
# first_left_empty_on
# ===========================================================================

def test_first_left_empty_on_set_once(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    assert resp.status_code == 200, resp.text
    assert resp.json()["house"]["first_left_empty_on"] == date.today().isoformat()


def test_first_left_empty_on_not_overwritten_on_further_transitions(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    r1 = _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    first_date = r1.json()["house"]["first_left_empty_on"]
    r2 = _set_status(auth, hdr, hid, "to_let", _owner())
    assert r2.json()["house"]["first_left_empty_on"] == first_date


def test_first_left_empty_on_never_set_for_non_empty_to_non_empty(
    db, society, admin_user, superadmin, auth
):
    """Verifying the field only ever transitions from None once, never re-touched
    on subsequent non-empty->non-empty changes (same assertion path as above,
    different starting status to widen coverage)."""
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    r1 = _set_status(auth, hdr, hid, "to_let", _owner())
    first_date = r1.json()["house"]["first_left_empty_on"]
    assert first_date == date.today().isoformat()
    r2 = _set_status(auth, hdr, hid, "for_sale", _owner())
    assert r2.json()["house"]["first_left_empty_on"] == first_date


def test_first_left_empty_on_set_for_rented_origin(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "rented", _owner(), _tenant())
    assert resp.status_code == 200, resp.text
    assert resp.json()["house"]["first_left_empty_on"] == date.today().isoformat()


# ===========================================================================
# same-status behavior
# ===========================================================================

def test_same_status_owned_repost_no_history_but_occupancy_updated(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    resp = _set_status(auth, hdr, hid, "owned", _owner(persons_living=9))
    assert resp.status_code == 200, resp.text
    db.expire_all()
    history = db.execute(
        select(HouseStatusHistory).where(HouseStatusHistory.house_id == hid)
    ).scalars().all()
    assert len(history) == 1  # only from empty->owned
    assert len(_audit(db, "house.occupancy_updated", society_id=society.id, entity_id=hid)) == 1


def test_same_status_owned_different_email_replaces_owner_no_history(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    resp = _set_status(auth, hdr, hid, "owned", _owner(email="differentowner@x.com", persons_living=1))
    assert resp.status_code == 200, resp.text
    db.expire_all()
    history = db.execute(
        select(HouseStatusHistory).where(HouseStatusHistory.house_id == hid)
    ).scalars().all()
    assert len(history) == 1  # only from empty->owned, not from the replace
    replaced = _audit(db, "house.owner_replaced", society_id=society.id, entity_id=hid)
    assert len(replaced) == 1


# ===========================================================================
# owner email == admin email links role, no duplicate user
# ===========================================================================

def test_owner_email_equals_admin_email_links_role_no_duplicate_user(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(
        auth, hdr, hid, "owned", _owner(email=admin_user.email, persons_living=1)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["owner"]["user_id"] == admin_user.id

    db.expire_all()
    roles = db.query(UserRole).filter(UserRole.user_id == admin_user.id).all()
    assert len(roles) == 2  # society_admin + resident
    assigned = _audit(db, "role.assigned", entity_id=admin_user.id)
    assert len(assigned) >= 1
    # Exactly 1 user.created row total: from the admin_user FIXTURE's own initial
    # provisioning (conftest's create_or_link_user call) — the owner-linking call
    # here must NOT add a second one (no duplicate account created).
    created = _audit(db, "user.created", entity_id=admin_user.id)
    assert len(created) == 1

    users_with_email = db.query(User).filter(User.email == admin_user.email).count()
    assert users_with_email == 1


def test_current_owner_ids_includes_admin_when_admin_is_owner(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "owned", _owner(email=admin_user.email, persons_living=1))

    from app.modules.houses.service import HouseService

    db.expire_all()
    ids = HouseService(db).current_owner_user_ids(society.id)
    assert admin_user.id in ids


# ===========================================================================
# id_proof regression coverage
# ===========================================================================

def test_id_proof_retained_owned_to_to_let_omitting(db, society, admin_user, superadmin, auth):
    """PRIMARY regression: id_proof set on owned must survive a to_let post that
    omits it (the payload doesn't even accept persons_living/id_proof there, but
    the service must carry the stored value forward)."""
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(
        auth, hdr, hid, "owned",
        _owner(persons_living=1, id_proof_type="voter_id", id_proof_document_id=55),
    )
    resp = _set_status(auth, hdr, hid, "to_let", _owner())
    assert resp.status_code == 200, resp.text
    assert resp.json()["owner"]["id_proof_type"] == "voter_id"
    assert resp.json()["owner"]["id_proof_document_id"] == 55


def test_id_proof_retained_via_patch_omitting(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(
        auth, hdr, hid, "owned",
        _owner(persons_living=1, id_proof_type="pan", id_proof_document_id=3),
    )
    resp = auth.client.patch(
        f"/houses/{hid}/occupancy/owner", headers=hdr, json={"contact_number": "1-2-3"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["owner"]["id_proof_type"] == "pan"
    assert resp.json()["owner"]["id_proof_document_id"] == 3


def test_id_proof_updated_when_provided(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(
        auth, hdr, hid, "owned",
        _owner(persons_living=1, id_proof_type="pan", id_proof_document_id=3),
    )
    resp = auth.client.patch(
        f"/houses/{hid}/occupancy/owner", headers=hdr,
        json={"id_proof_type": "aadhaar", "id_proof_document_id": 77},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["owner"]["id_proof_type"] == "aadhaar"
    assert resp.json()["owner"]["id_proof_document_id"] == 77


def test_id_proof_nullable_roundtrip_on_create(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    assert resp.status_code == 200, resp.text
    assert resp.json()["owner"]["id_proof_type"] is None
    assert resp.json()["owner"]["id_proof_document_id"] is None


def test_tenant_id_proof_retained_on_edit(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(
        auth, hdr, hid, "rented", _owner(),
        _tenant(id_proof_type="passport", id_proof_document_id=21),
    )
    resp = auth.client.patch(
        f"/houses/{hid}/occupancy/tenant", headers=hdr, json={"contact_number": "555-1234"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["tenant"]["id_proof_type"] == "passport"
    assert resp.json()["tenant"]["id_proof_document_id"] == 21


# ===========================================================================
# case-insensitive / whitespace email == same owner
# ===========================================================================

def test_case_insensitive_whitespace_email_is_same_owner(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    r1 = _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    old_user_id = r1.json()["owner"]["user_id"]
    resp = _set_status(
        auth, hdr, hid, "owned", _owner(email="  OWNER1@X.COM  ", persons_living=5)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["owner"]["user_id"] == old_user_id
    assert resp.json()["owner"]["persons_living"] == 5
    db.expire_all()
    replaced = _audit(db, "house.owner_replaced", society_id=society.id, entity_id=hid)
    assert len(replaced) == 0


# ===========================================================================
# current_owner_user_ids scoping
# ===========================================================================

def test_current_owner_user_ids_empty_when_no_owners(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _make_building_with_houses(auth, hdr)
    from app.modules.houses.service import HouseService

    ids = HouseService(db).current_owner_user_ids(society.id)
    assert ids == set()


def test_current_owner_user_ids_excludes_tenants(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "rented", _owner(), _tenant())

    from app.modules.houses.service import HouseService

    db.expire_all()
    ids = HouseService(db).current_owner_user_ids(society.id)
    tenant_rows = _occ(db, hid, "tenant")
    assert all(t.user_id is None for t in tenant_rows)
    # Owner's user_id is present, tenant contributes nothing (user_id NULL anyway).
    owner_rows = _occ(db, hid, "owner")
    assert owner_rows[0].user_id in ids


def test_current_owner_user_ids_scoped_to_society(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))

    from app.platform.societies.schemas import SocietyCreate
    from app.platform.societies.service import SocietyService
    from app.platform.users.provisioning import UserProvisioningService
    from tests.conftest import DEFAULT_MEMBER_PASSWORD
    from tests._houses_helpers import _enable_houses

    soc_b = SocietyService(db).create_society(
        SocietyCreate(
            name="Society C", storage_limit_bytes=5 * 1024**3,
            default_member_password=DEFAULT_MEMBER_PASSWORD,
        ),
        actor_user_id=superadmin.id,
    )
    db.commit()
    db.refresh(soc_b)
    admin_c = UserProvisioningService(db).create_or_link_user(
        email="adminc@test.local", society_id=soc_b.id, role_key="society_admin",
        profile={"full_name": "Admin C"}, actor_user_id=superadmin.id,
    )
    db.commit()
    db.refresh(admin_c)
    _enable_houses(db, soc_b, superadmin)
    from tests._houses_helpers import _admin_bearer as _bearer_fn

    hdr_c = _bearer_fn(auth, admin_c)
    houses_c = _make_building_with_houses(auth, hdr_c, names=["C"])
    _set_status(auth, hdr_c, houses_c[0]["id"], "owned", _owner(email="cowner@x.com", persons_living=1))

    from app.modules.houses.service import HouseService

    db.expire_all()
    ids_a = HouseService(db).current_owner_user_ids(society.id)
    ids_c = HouseService(db).current_owner_user_ids(soc_b.id)
    assert ids_a.isdisjoint(ids_c)
    assert len(ids_a) == 1 and len(ids_c) == 1


# ===========================================================================
# replaced owner valid window + new owner valid window
# ===========================================================================

def test_replaced_owner_valid_window_closed(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    _set_status(auth, hdr, hid, "owned", _owner(email="newowner@x.com", persons_living=1))
    db.expire_all()
    rows = _occ(db, hid, "owner", current_only=False)
    old_row = next(r for r in rows if not r.is_current)
    assert old_row.valid_to == date.today()
    assert old_row.valid_from == date.today()  # created + closed same day in test


def test_new_owner_valid_from_today_valid_to_none(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    _set_status(auth, hdr, hid, "owned", _owner(persons_living=1))
    _set_status(auth, hdr, hid, "owned", _owner(email="newowner@x.com", persons_living=1))
    db.expire_all()
    rows = _occ(db, hid, "owner", current_only=True)
    assert len(rows) == 1
    assert rows[0].valid_from == date.today()
    assert rows[0].valid_to is None


def test_empty_to_rented_owner_present_for_contract(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = _set_status(auth, hdr, hid, "rented", _owner(), _tenant())
    assert resp.status_code == 200, resp.text
    assert resp.json()["owner"] is not None
    assert resp.json()["owner"]["email"] == "owner1@x.com"
