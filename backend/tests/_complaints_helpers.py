"""Shared test harness for the Complaints (Module 5) test suite.

Mirrors ``_finance_helpers`` / ``_houses_helpers``: module-enable in one
``set_modules`` call (complaints depends_on houses; vault included for images),
the must-change bearer dance, an owned-house-with-login helper (the raiser must be
a provisioned owner so ``current_owned_houses`` finds their house), audit
assertions, and deterministic ``utcnow`` freezing across the complaints call
sites. Import from here in every ``test_complaints_*.py`` file (DRY).
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone

import pytest

from app.common import events as event_bus
from app.modules.complaints.events import (
    EVENT_CREATED,
    EVENT_MARK_READ,
    EVENT_STATUS_CHANGED,
    EVENT_WITHDRAWN,
)
from app.platform.models import AuditLog
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

# A stable frozen "today" for date-deterministic specs (auto-archive window).
FROZEN_TODAY = date(2026, 7, 8)

# --- robustness/adversarial fixtures ------------------------------------------

# A tiny valid 1x1 PNG (real magic bytes — some paths may sniff content).
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90\x77\x53\xde\x00\x00\x00\x0cIDATx\x9cc\xf8"
    b"\xcf\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND"
    b"\xaeB`\x82"
)

# A denylisted extension's bytes (content is irrelevant — Vault denies by
# extension, docs/modules/vault.md).
EXE_BYTES = b"MZ\x90\x00" + b"\x00" * 32


# ===========================================================================
# module enable + bearer helpers
# ===========================================================================


def enable_complaints(db, society, superadmin, *, config=None) -> None:
    """Enable onboarding + houses + vault + complaints in one call. Commits.

    Complaints ``depends_on: houses``; vault is included so image specs exercise
    complaints+vault on the same society (image routes gate ``require_module``
    vault).
    """
    SocietyService(db).set_modules(
        society.id,
        [
            ModuleAllocation(module_key="onboarding", enabled=True, config={}),
            ModuleAllocation(module_key="houses", enabled=True, config={}),
            ModuleAllocation(module_key="vault", enabled=True, config={}),
            ModuleAllocation(
                module_key="complaints", enabled=True, config=config or {}
            ),
        ],
        actor_user_id=superadmin.id,
    )
    db.commit()


def admin_bearer(auth, admin_user) -> dict[str, str]:
    """must_change -> change-password -> re-login. Returns a usable bearer header."""
    return _houses_admin_bearer(auth, admin_user)


def resident_bearer(auth, resident_user) -> dict[str, str]:
    """Same must-change dance for a resident login."""
    tokens = auth.login_ok(resident_user.email, DEFAULT_MEMBER_PASSWORD)
    resp = auth.client.post(
        "/auth/change-password",
        headers=auth.bearer(tokens["access_token"]),
        json={"current_password": DEFAULT_MEMBER_PASSWORD, "new_password": NEWPASS},
    )
    assert resp.status_code == 200, resp.text
    sess = auth.login_ok(resident_user.email, NEWPASS)
    return auth.bearer(sess["access_token"])


def setup_complaints(db, society, admin_user, superadmin, auth, *, config=None):
    """Enable complaints (+deps+vault) and return an activated admin bearer."""
    enable_complaints(db, society, superadmin, config=config)
    return admin_bearer(auth, admin_user)


# ===========================================================================
# owned house tied to a LOGIN user (the raiser)
# ===========================================================================


def owned_house_for(auth, hdr, *, email, full_name="Owner One", persons_living=2):
    """Onboard a building house and move it to ``owned`` with the given owner
    email, so provisioning links a resident login to the occupancy.

    Returns the house id. The owner ``email`` becomes a provisioned resident
    (``must_change``) — pass that same email to a ``resident_user``-style login
    (or use :func:`resident_bearer` after fetching the user) so the raiser's
    ``current_owned_houses`` resolves this house.
    """
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
    """Return an activated bearer for a provisioned OWNER login (the raiser).

    After ``owned_house_for`` provisions the owner as a resident login, this runs
    the must-change dance and returns the bearer header. Looks the user up by
    email to drive the login.
    """
    from app.platform.models import User

    user = db.query(User).filter(User.email == email.lower()).one()
    return resident_bearer(auth, user), user


# ===========================================================================
# audit
# ===========================================================================


def audit_actions(db, society_id) -> list[tuple[str, str, int]]:
    """``[(action, entity_type, entity_id), ...]`` for a society, oldest-first."""
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.society_id == society_id)
        .order_by(AuditLog.id)
        .all()
    )
    return [(r.action, r.entity_type, r.entity_id) for r in rows]


def second_society_with_complaints(
    db, superadmin, auth, *, email="admin-b@complaints.local"
):
    """A second independent society with complaints enabled + an admin bearer.

    Returns ``(society_b, admin_b, hdr_b)`` — used by tenant-isolation specs.
    """
    soc_b = SocietyService(db).create_society(
        SocietyCreate(
            name="Complaints Society B",
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
    enable_complaints(db, soc_b, superadmin)
    hdr_b = admin_bearer(auth, admin_b)
    return soc_b, admin_b, hdr_b


# ===========================================================================
# date determinism — patch ``utcnow`` where it's USED (see finance helper note)
# ===========================================================================

_UTCNOW_CONSUMERS = (
    "app.modules.complaints.services.support.utcnow",
    "app.modules.complaints.services.jobs.utcnow",
)


def freeze_utcnow(monkeypatch, frozen_date: date = FROZEN_TODAY) -> None:
    """Patch every complaints call site's ``utcnow`` to a fixed instant.

    ``raising=False`` so a consumer that does not import ``utcnow`` yet (a stub
    before its wave lands) does not break the patch.
    """
    frozen_dt = datetime.combine(
        frozen_date, datetime.min.time(), tzinfo=timezone.utc
    )
    for target in _UTCNOW_CONSUMERS:
        monkeypatch.setattr(target, lambda: frozen_dt, raising=False)


@contextmanager
def frozen_today(frozen_date: date = FROZEN_TODAY):
    """Context-manager form of :func:`freeze_utcnow`."""
    mp = pytest.MonkeyPatch()
    try:
        freeze_utcnow(mp, frozen_date)
        yield
    finally:
        mp.undo()


# ===========================================================================
# raise / images / resolve — thin HTTP wrappers (gate-test-matrix helpers)
# ===========================================================================


def raise_complaint(
    auth, hdr, *, category_id, title, description, house_id=None
) -> dict:
    """POST /complaints; assert 200; return the parsed body."""
    body = {"category_id": category_id, "title": title, "description": description}
    if house_id is not None:
        body["house_id"] = house_id
    resp = auth.client.post("/complaints", headers=hdr, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def add_report_image_http(auth, hdr, cid, *, name, data, ct):
    """POST /complaints/{cid}/images — raw response (caller asserts status)."""
    return auth.client.post(
        f"/complaints/{cid}/images",
        headers=hdr,
        files={"file": (name, data, ct)},
    )


def resolve_http(auth, hdr, cid, *, note=None, files=None):
    """POST /complaints/{cid}/resolve — multipart note + optional proof files.

    ``files`` is a list of ``(name, data, content_type)`` tuples (or None for no
    images). Raw response — caller asserts status.
    """
    data = {}
    if note is not None:
        data["note"] = note
    multipart = None
    if files:
        multipart = [("images", f) for f in files]
    return auth.client.post(
        f"/complaints/{cid}/resolve",
        headers=hdr,
        data=data,
        files=multipart,
    )


# ===========================================================================
# vault trash / quota forcing
# ===========================================================================


def trash_vault_document(db, document_id) -> None:
    """Soft-delete a vault document directly (simulate Trash), committed."""
    from sqlalchemy import text

    db.execute(
        text("UPDATE vault_documents SET deleted_at = now() WHERE id = :id"),
        {"id": document_id},
    )
    db.commit()


def society_with_tiny_quota(db, superadmin, auth, *, limit_bytes=8):
    """A fresh society with a tiny ``storage_limit_bytes`` (forces Vault 413).

    Enables onboarding + houses + vault + complaints, provisions + activates a
    society_admin, and returns ``(society, admin, hdr)``.
    """
    soc = SocietyService(db).create_society(
        SocietyCreate(
            name="Complaints Tiny Quota Society",
            storage_limit_bytes=limit_bytes,
            default_member_password=DEFAULT_MEMBER_PASSWORD,
        ),
        actor_user_id=superadmin.id,
    )
    db.commit()
    db.refresh(soc)
    admin = UserProvisioningService(db).create_or_link_user(
        email="admin-quota@complaints.local",
        society_id=soc.id,
        role_key="society_admin",
        profile={"full_name": "Admin Quota"},
        actor_user_id=superadmin.id,
    )
    db.commit()
    db.refresh(admin)
    enable_complaints(db, soc, superadmin)
    hdr = admin_bearer(auth, admin)
    return soc, admin, hdr


# ===========================================================================
# event capture (extends the pattern used by test_complaints_crud.py's
# ``captured_events`` fixture — includes STATUS_CHANGED too)
# ===========================================================================


@pytest.fixture
def event_capture():
    """Subscribe capture handlers to ALL complaints events for the test.

    Returns ``{event_name: [payload, ...]}``; unsubscribes on teardown (events
    are process-global). Superset of the ``captured_events`` fixture in
    test_complaints_crud.py (adds ``EVENT_STATUS_CHANGED``) — kept as a
    separate fixture name (per the matrix) so existing tests importing
    ``captured_events`` from that module are undisturbed.
    """
    seen: dict[str, list[dict]] = {
        EVENT_CREATED: [],
        EVENT_WITHDRAWN: [],
        EVENT_STATUS_CHANGED: [],
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
# crafted / cross-society bearer
# ===========================================================================


def crafted_bearer(make_token, *, user_id, society_id, role_ids) -> dict[str, str]:
    """A bearer header for a hand-crafted JWT (cross-society / no-perms attacks)."""
    token = make_token(
        user_id=user_id, active_society_id=society_id, role_ids=role_ids
    )
    return {"Authorization": f"Bearer {token}"}
