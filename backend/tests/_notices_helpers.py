"""Shared test harness for the Notice Board (Module 6) test suite.

Mirrors ``_complaints_helpers`` / ``_finance_helpers``: module-enable in one
``enable_notices`` call (notices depends_on houses; vault included so attachment
specs exercise notices+vault on the same society), the must-change bearer dance,
an owned-house-with-login helper (owners are the read-receipt denominator + the
broadcast audience, so receipts specs need provisioned owners), audit assertions,
a domain-event capture fixture, and deterministic ``utcnow`` freezing across the
notices call sites. Import from here in every ``test_notices_*.py`` file (DRY).
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone

import pytest

from app.common import events as event_bus
from app.modules.notices.events import EVENT_MARK_READ, EVENT_POSTED
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

# A stable frozen "now" for expiry-deterministic specs.
FROZEN_TODAY = date(2026, 7, 8)

# A tiny valid 1x1 PNG (real magic bytes) for attachment specs.
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90\x77\x53\xde\x00\x00\x00\x0cIDATx\x9cc\xf8"
    b"\xcf\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND"
    b"\xaeB`\x82"
)

# A denylisted extension's bytes (Vault denies by extension → 415).
EXE_BYTES = b"MZ\x90\x00" + b"\x00" * 32


# ===========================================================================
# module enable + bearer helpers
# ===========================================================================


def enable_notices(db, society, superadmin, *, config=None, with_vault=True) -> None:
    """Enable onboarding + houses (+ vault) + notices in one call. Commits.

    Notices ``depends_on: houses``; vault is included by default so attachment
    specs exercise notices+vault on the same society (attachment routes gate
    ``require_module('vault')``). Pass ``with_vault=False`` to exercise the
    vault-off path (attachment routes should 403 while text notices still work).
    """
    allocations = [
        ModuleAllocation(module_key="onboarding", enabled=True, config={}),
        ModuleAllocation(module_key="houses", enabled=True, config={}),
    ]
    if with_vault:
        allocations.append(
            ModuleAllocation(module_key="vault", enabled=True, config={})
        )
    allocations.append(
        ModuleAllocation(module_key="notices", enabled=True, config=config or {})
    )
    SocietyService(db).set_modules(
        society.id, allocations, actor_user_id=superadmin.id
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


def setup_notices(db, society, admin_user, superadmin, auth, *, config=None):
    """Enable notices (+deps+vault) and return an activated admin bearer."""
    enable_notices(db, society, superadmin, config=config)
    return admin_bearer(auth, admin_user)


# ===========================================================================
# owned house tied to a LOGIN user (an owner = a broadcast recipient/reader)
# ===========================================================================


def owned_house_for(auth, hdr, *, email, full_name="Owner One", persons_living=2):
    """Onboard a building house and move it to ``owned`` with the given owner
    email, so provisioning links a resident login to the occupancy.

    Returns the house id. The owner ``email`` becomes a provisioned resident
    (``must_change``); use :func:`owner_login_bearer` for an activated bearer.
    Owners are the read-receipt denominator + the notice audience.
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
    """Return ``(bearer_header, user)`` for a provisioned OWNER login (a reader)."""
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


def second_society_with_notices(
    db, superadmin, auth, *, email="admin-b@notices.local"
):
    """A second independent society with notices enabled + an admin bearer.

    Returns ``(society_b, admin_b, hdr_b)`` — used by tenant-isolation specs.
    """
    soc_b = SocietyService(db).create_society(
        SocietyCreate(
            name="Notices Society B",
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
    enable_notices(db, soc_b, superadmin)
    hdr_b = admin_bearer(auth, admin_b)
    return soc_b, admin_b, hdr_b


# ===========================================================================
# domain-event capture (notice_posted / notice.mark_read)
# ===========================================================================


@contextmanager
def capture_events(*names: str):
    """Capture the given domain events emitted during the block.

    Yields a list of ``(event_name, payload)`` tuples appended as events fire.
    Defaults to both notices events when called with no names.
    """
    names = names or (EVENT_POSTED, EVENT_MARK_READ)
    captured: list[tuple[str, dict]] = []
    handlers = []
    for name in names:
        def _make(n):
            def _handler(payload):
                captured.append((n, payload))

            return _handler

        h = _make(name)
        handlers.append((name, h))
        event_bus.subscribe(name, h)
    try:
        yield captured
    finally:
        for name, h in handlers:
            event_bus.unsubscribe(name, h)


# ===========================================================================
# helpers to create notices over HTTP
# ===========================================================================


def create_notice_http(
    client, hdr, *, title="Notice", body="<p>hello</p>", publish=False,
    is_pinned=False, expires_at=None,
):
    """POST /notices; returns the response (caller asserts status)."""
    payload = {"title": title, "body": body, "publish": publish, "is_pinned": is_pinned}
    if expires_at is not None:
        payload["expires_at"] = expires_at
    return client.post("/notices", headers=hdr, json=payload)


def add_attachment_http(client, hdr, notice_id, *, data=PNG_BYTES, filename="a.png",
                        content_type="image/png"):
    """POST /notices/{id}/attachments (multipart); returns the response."""
    return client.post(
        f"/notices/{notice_id}/attachments",
        headers=hdr,
        files={"file": (filename, data, content_type)},
    )


# ===========================================================================
# date determinism — patch ``utcnow`` where it's USED
# ===========================================================================

_UTCNOW_CONSUMERS = (
    "app.modules.notices.services.support.utcnow",
    "app.modules.notices.services.receipts.utcnow",
    "app.modules.notices.services.lifecycle.utcnow",
    "app.modules.notices.service.utcnow",
)


def freeze_utcnow(monkeypatch, frozen_date: date = FROZEN_TODAY) -> None:
    """Patch every notices call site's ``utcnow`` to a fixed instant.

    ``raising=False`` so a consumer that does not import ``utcnow`` yet does not
    break the patch.
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
