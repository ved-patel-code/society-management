"""Shared test harness for the Notifications (Module 7) test suite.

Mirrors ``_notices_helpers`` / ``_finance_helpers`` / ``_complaints_helpers``:
module-enable in one call (notifications ``depends_on: finance``; complaints +
notices included so the event-driven paths are exercisable on the same society),
the must-change bearer dance, owner-with-login provisioning (owners are the
notice + dues-reminder recipients), admin-permission-holder provisioning (the
complaint-alert recipients), audit assertions, a domain-event capture fixture,
and helpers to drive complaints/notices/dues over HTTP so tests assert real
end-to-end notification creation.

Import from here in every ``test_notifications_*.py`` file (DRY).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from app.modules.notifications.repository import NotificationRepository
from app.platform.models import AuditLog, User
from app.platform.societies.schemas import ModuleAllocation, SocietyCreate
from app.platform.societies.service import SocietyService
from app.platform.users.provisioning import UserProvisioningService

from tests._houses_helpers import (
    _admin_bearer as _houses_admin_bearer,
    _make_building_with_houses,
    _set_status,
)
from tests.conftest import DEFAULT_MEMBER_PASSWORD

NEWPASS = "NewPass123"
FROZEN_TODAY = date(2026, 7, 8)


# ===========================================================================
# module enable + bearer helpers
# ===========================================================================


def enable_notifications(
    db,
    society,
    superadmin,
    *,
    config=None,
    with_complaints=True,
    with_notices=True,
    with_finance=True,
) -> None:
    """Enable onboarding + houses + vault (+finance +complaints +notices) +
    notifications in one ``set_modules`` call. Commits.

    Notifications ``depends_on: finance`` (the dues rule) — finance is on by
    default. Complaints + Notices are optional so soft-dependency tests can turn
    them off and assert the handlers no-op.
    """
    allocations = [
        ModuleAllocation(module_key="onboarding", enabled=True, config={}),
        ModuleAllocation(module_key="houses", enabled=True, config={}),
        ModuleAllocation(module_key="vault", enabled=True, config={}),
    ]
    if with_finance:
        allocations.append(
            ModuleAllocation(module_key="finance", enabled=True, config={})
        )
    if with_complaints:
        allocations.append(
            ModuleAllocation(module_key="complaints", enabled=True, config={})
        )
    if with_notices:
        allocations.append(
            ModuleAllocation(module_key="notices", enabled=True, config={})
        )
    allocations.append(
        ModuleAllocation(
            module_key="notifications", enabled=True, config=config or {}
        )
    )
    SocietyService(db).set_modules(
        society.id, allocations, actor_user_id=superadmin.id
    )
    db.commit()


def admin_bearer(auth, admin_user) -> dict[str, str]:
    """must_change -> change-password -> re-login. Returns a usable bearer header."""
    return _houses_admin_bearer(auth, admin_user)


def resident_bearer(auth, resident_user) -> dict[str, str]:
    """Same must-change dance for a resident/owner login."""
    tokens = auth.login_ok(resident_user.email, DEFAULT_MEMBER_PASSWORD)
    resp = auth.client.post(
        "/auth/change-password",
        headers=auth.bearer(tokens["access_token"]),
        json={"current_password": DEFAULT_MEMBER_PASSWORD, "new_password": NEWPASS},
    )
    assert resp.status_code == 200, resp.text
    sess = auth.login_ok(resident_user.email, NEWPASS)
    return auth.bearer(sess["access_token"])


def setup_notifications(db, society, admin_user, superadmin, auth, *, config=None, **kw):
    """Enable notifications (+deps) and return an activated admin bearer header.

    The admin holds ``complaints.read_all`` (so it is a complaint-alert recipient)
    and ``notifications.read``/``configure``.
    """
    enable_notifications(db, society, superadmin, config=config, **kw)
    return admin_bearer(auth, admin_user)


# ===========================================================================
# owned house tied to a LOGIN user (an owner = notice + dues recipient)
# ===========================================================================


def owned_house_for(auth, hdr, *, email, full_name="Owner One", persons_living=2):
    """Onboard a building house, move it to ``owned`` with the given owner email
    (provisioning links a resident login). Returns the house id."""
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    owner = {
        "full_name": full_name,
        "email": email,
        "contact_number": "555-0001",
        "persons_living": persons_living,
    }
    resp = _set_status(auth, hdr, hid, "owned", owner)
    assert resp.status_code == 200, resp.text
    return hid


def owner_login_bearer(auth, db, *, email):
    """Return ``(bearer_header, user)`` for a provisioned OWNER login."""
    user = db.query(User).filter(User.email == email.lower()).one()
    return resident_bearer(auth, user), user


def many_owned_houses(auth, hdr, *, count, email_prefix="scaleowner"):
    """Create ``count`` owned houses in ONE building, each with its own owner
    login. Returns ``[(house_id, owner_email), ...]``.

    Used by the SCALE fan-out test — a notice broadcasts to all these owners in
    one batched insert. Each owner is a distinct current owner (the data model
    allows one current owner per house)."""
    houses = _make_building_with_houses(
        auth, hdr, floors=[{"level": 1, "houses_count": count}]
    )
    out = []
    for i, house in enumerate(houses[:count]):
        email = f"{email_prefix}{i}@notif.local"
        owner = {
            "full_name": f"Owner {i}",
            "email": email,
            "contact_number": "555-0100",
            "persons_living": 1,
        }
        resp = _set_status(auth, hdr, house["id"], "owned", owner)
        assert resp.status_code == 200, resp.text
        out.append((house["id"], email))
    return out


# ===========================================================================
# drive the emitting modules over HTTP (real end-to-end notification creation)
# ===========================================================================


def raise_complaint_http(client, hdr, *, category_id, title="Leak", description="water"):
    """POST /complaints as an owner; returns the response."""
    return client.post(
        "/complaints",
        headers=hdr,
        json={
            "category_id": category_id,
            "title": title,
            "description": description,
        },
    )


def first_category_id(client, hdr) -> int:
    """The first (lazily-seeded) complaint category id for the society."""
    resp = client.get("/complaints/categories", headers=hdr)
    assert resp.status_code == 200, resp.text
    cats = resp.json()
    return cats[0]["id"]


def publish_notice_http(client, hdr, *, title="Notice", body="<p>hi</p>"):
    """POST /notices with publish=True; returns the response."""
    return client.post(
        "/notices",
        headers=hdr,
        json={"title": title, "body": body, "publish": True, "is_pinned": False},
    )


def set_rate_http(auth, hdr, amount, valid_from):
    """POST /finance/rate; returns the response."""
    return auth.client.post(
        "/finance/rate",
        headers=hdr,
        json={"amount": str(amount), "valid_from": str(valid_from)},
    )


# ===========================================================================
# notification feed reads (over HTTP) + direct DB introspection
# ===========================================================================


def get_feed(client, hdr):
    """GET /notifications; returns the parsed FeedOut dict (asserts 200)."""
    resp = client.get("/notifications", headers=hdr)
    assert resp.status_code == 200, resp.text
    return resp.json()


def get_unread_count(client, hdr) -> int:
    resp = client.get("/notifications/unread-count", headers=hdr)
    assert resp.status_code == 200, resp.text
    return resp.json()["unread_count"]


def db_notifications(db, society_id, user_id=None, type_=None):
    """The raw notification rows for a society (optionally a user / type), oldest
    first — for asserting recipients/dedupe/read_at directly."""
    from app.modules.notifications.models import Notification

    q = db.query(Notification).filter(Notification.society_id == society_id)
    if user_id is not None:
        q = q.filter(Notification.user_id == user_id)
    if type_ is not None:
        q = q.filter(Notification.type == type_)
    return q.order_by(Notification.id).all()


# ===========================================================================
# audit / second society / crafted bearer
# ===========================================================================


def audit_actions(db, society_id) -> list[tuple[str, str, int]]:
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.society_id == society_id)
        .order_by(AuditLog.id)
        .all()
    )
    return [(r.action, r.entity_type, r.entity_id) for r in rows]


def second_society_with_notifications(
    db, superadmin, auth, *, email="admin-b@notif.local"
):
    """A second independent society with notifications enabled + an admin bearer.
    Returns ``(society_b, admin_b, hdr_b)`` — for tenant-isolation specs."""
    soc_b = SocietyService(db).create_society(
        SocietyCreate(
            name="Notif Society B",
            storage_limit_bytes=5 * 1024**3,
            default_member_password=DEFAULT_MEMBER_PASSWORD,
        ),
        actor_user_id=superadmin.id,
    )
    db.commit()
    db.refresh(soc_b)
    admin_b = UserProvisioningService(db).create_or_link_user(
        email=email,
        society_id=soc_b.id,
        role_key="society_admin",
        profile={"full_name": "Admin B"},
        actor_user_id=superadmin.id,
    )
    db.commit()
    db.refresh(admin_b)
    enable_notifications(db, soc_b, superadmin)
    hdr_b = admin_bearer(auth, admin_b)
    return soc_b, admin_b, hdr_b


def crafted_bearer(make_token, *, user_id, society_id, role_ids) -> dict[str, str]:
    """A bearer header for a hand-crafted JWT (cross-society / no-perms attacks)."""
    token = make_token(
        user_id=user_id, active_society_id=society_id, role_ids=role_ids
    )
    return {"Authorization": f"Bearer {token}"}
