"""Adversarial / permission-boundary tests for Notifications (Module 7).

Mirrors ``test_notices_security.py``. Covers:
- every route unauthenticated → 401 (parametrized);
- 403 without ``notifications.read`` on feed / unread-count / {id}/read / read-all;
- 403 without ``notifications.configure`` on GET/PUT config (a read-only resident
  is 403 there too);
- cross-society isolation: a user in society A never sees or clears society B's
  rows; a crafted JWT claiming a foreign ``active_society_id`` cannot act (the
  effective permission set is looked up live from the DB's ``user_roles`` for the
  claimed society, so the crafted ``role_ids`` claim is ignored);
- own-only: user X cannot mark user Y's notification read (404) even in the same
  society;
- super-admin bypass consistent with the sibling modules;
- config PUT validation: out-of-bounds → 422, extra keys → 422, all-None → 422,
  a valid partial merge preserves the other keys + writes the audit row.

Permissions are looked up LIVE from the DB on every request (not baked into the
JWT), so stripping a ``role_permissions`` row takes effect on the SAME bearer
with no re-login — same idiom as the notices suite.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.platform.roles.repository import RoleRepository

from tests._notifications_helpers import (
    admin_bearer,
    audit_actions,
    crafted_bearer,
    first_category_id,
    get_feed,
    owned_house_for,
    owner_login_bearer,
    publish_notice_http,
    raise_complaint_http,
    resident_bearer,
    second_society_with_notifications,
    setup_notifications,
)


# ===========================================================================
# helpers
# ===========================================================================


def _strip_permission(db, society_id, role_key, perm_key) -> None:
    """Delete one role_permissions grant (copy of the notices-suite helper)."""
    role = RoleRepository(db).society_role_by_key(society_id, role_key)
    perm_id = db.execute(
        text("SELECT id FROM permissions WHERE key=:k"), {"k": perm_key}
    ).scalar_one()
    db.execute(
        text("DELETE FROM role_permissions WHERE role_id=:r AND permission_id=:p"),
        {"r": role.id, "p": perm_id},
    )
    db.commit()


def _strip_all_notifications_perms(db, society_id, role_key) -> None:
    role = RoleRepository(db).society_role_by_key(society_id, role_key)
    db.execute(
        text(
            "DELETE FROM role_permissions WHERE role_id=:r AND permission_id IN "
            "(SELECT id FROM permissions WHERE key LIKE 'notifications.%')"
        ),
        {"r": role.id},
    )
    db.commit()


def _fill_admin_feed(auth, db, admin_hdr, *, email="owner@x.com", n=1) -> int:
    """Owner raises ``n`` complaints → admin gets ``complaint_new`` rows. Returns
    the first admin notification id."""
    owned_house_for(auth, admin_hdr, email=email)
    o_hdr, _owner_u = owner_login_bearer(auth, db, email=email)
    cat = first_category_id(auth.client, o_hdr)
    for i in range(n):
        resp = raise_complaint_http(
            auth.client, o_hdr, category_id=cat, title=f"Leak {i}"
        )
        assert resp.status_code == 200, resp.text
    feed = get_feed(auth.client, admin_hdr)
    return feed["items"][0]["id"]


# ===========================================================================
# unauthenticated -> 401
# ===========================================================================


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/notifications"),
        ("GET", "/notifications/unread-count"),
        ("POST", "/notifications/1/read"),
        ("POST", "/notifications/read-all"),
        ("GET", "/notifications/config"),
        ("PUT", "/notifications/config"),
    ],
)
def test_unauthenticated_401_all_routes(
    db, society, admin_user, superadmin, auth, method, path
):
    setup_notifications(db, society, admin_user, superadmin, auth)
    resp = auth.client.request(method, path)
    assert resp.status_code == 401, resp.text


# ===========================================================================
# 403 without notifications.read
# ===========================================================================


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/notifications"),
        ("GET", "/notifications/unread-count"),
        ("POST", "/notifications/1/read"),
        ("POST", "/notifications/read-all"),
    ],
)
def test_forbidden_without_read_perm(
    db, society, admin_user, resident_user, superadmin, auth, method, path
):
    """A resident stripped of ``notifications.read`` is 403 on every feed route."""
    setup_notifications(db, society, admin_user, superadmin, auth)
    _strip_all_notifications_perms(db, society.id, "resident")
    r_hdr = resident_bearer(auth, resident_user)

    resp = auth.client.request(method, path, headers=r_hdr)
    assert resp.status_code == 403, resp.text


def test_resident_with_read_can_use_feed_routes(
    db, society, admin_user, resident_user, superadmin, auth
):
    """Sanity: with the default ``notifications.read`` grant a resident reaches
    the feed routes (200), so the 403s above are the missing perm, not the route."""
    setup_notifications(db, society, admin_user, superadmin, auth)
    r_hdr = resident_bearer(auth, resident_user)

    assert auth.client.get("/notifications", headers=r_hdr).status_code == 200
    assert (
        auth.client.get("/notifications/unread-count", headers=r_hdr).status_code
        == 200
    )
    assert auth.client.post("/notifications/read-all", headers=r_hdr).status_code == 200


# ===========================================================================
# 403 without notifications.configure
# ===========================================================================


def test_config_forbidden_without_configure_perm(
    db, society, admin_user, superadmin, auth
):
    """An admin stripped of ``notifications.configure`` (keeps ``read``) is 403 on
    both config routes but still reaches the feed."""
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    _strip_permission(db, society.id, "society_admin", "notifications.configure")

    assert auth.client.get("/notifications/config", headers=hdr).status_code == 403
    assert (
        auth.client.put(
            "/notifications/config", headers=hdr, json={"dues_advance_days": 5}
        ).status_code
        == 403
    )
    # read still works.
    assert auth.client.get("/notifications", headers=hdr).status_code == 200


def test_read_only_resident_forbidden_on_config(
    db, society, admin_user, resident_user, superadmin, auth
):
    """A resident holds ``notifications.read`` but NOT ``configure`` by default →
    403 on config, 200 on the feed."""
    setup_notifications(db, society, admin_user, superadmin, auth)
    r_hdr = resident_bearer(auth, resident_user)

    assert auth.client.get("/notifications/config", headers=r_hdr).status_code == 403
    assert (
        auth.client.put(
            "/notifications/config", headers=r_hdr, json={"read_retention_days": 10}
        ).status_code
        == 403
    )
    assert auth.client.get("/notifications", headers=r_hdr).status_code == 200


# ===========================================================================
# own-only: cannot mark another user's notification read (404)
# ===========================================================================


def test_cannot_mark_another_users_notification_same_society(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    # Owner feed: publish a notice → the owner owns a ``notice`` row.
    owned_house_for(auth, hdr, email="owner@x.com")
    o_hdr, _owner_u = owner_login_bearer(auth, db, email="owner@x.com")
    assert publish_notice_http(auth.client, hdr, title="AGM").status_code == 200

    owner_feed = get_feed(auth.client, o_hdr)
    owner_notif_id = owner_feed["items"][0]["id"]

    # The admin (a distinct user in the SAME society) gets 404, not 403.
    resp = auth.client.post(f"/notifications/{owner_notif_id}/read", headers=hdr)
    assert resp.status_code == 404, resp.text
    # Owner row untouched.
    assert get_feed(auth.client, o_hdr)["unread_count"] == 1


# ===========================================================================
# cross-society isolation
# ===========================================================================


def test_cross_society_feed_never_shows_foreign_rows(
    db, society, admin_user, superadmin, auth
):
    """Society A's admin has a full feed; society B's admin sees none of it."""
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    _fill_admin_feed(auth, db, hdr, n=2)
    assert get_feed(auth.client, hdr)["unread_count"] == 2

    _soc_b, _admin_b, hdr_b = second_society_with_notifications(db, superadmin, auth)
    feed_b = get_feed(auth.client, hdr_b)
    assert feed_b["unread_count"] == 0
    assert feed_b["items"] == []

    # A's feed is unchanged.
    assert get_feed(auth.client, hdr)["unread_count"] == 2


def test_cross_society_cannot_clear_foreign_notification(
    db, society, admin_user, superadmin, auth
):
    """Society B's admin cannot mark-read a society-A notification id (404)."""
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    a_notif_id = _fill_admin_feed(auth, db, hdr, n=1)

    _soc_b, _admin_b, hdr_b = second_society_with_notifications(db, superadmin, auth)
    resp = auth.client.post(f"/notifications/{a_notif_id}/read", headers=hdr_b)
    assert resp.status_code == 404, resp.text

    # A's row is still unread.
    assert get_feed(auth.client, hdr)["unread_count"] == 1


def test_crafted_foreign_society_token_cannot_act(
    db, society, admin_user, superadmin, auth, make_token
):
    """A crafted token: A's real admin user_id but claiming ``active_society_id=B``
    with NO roles. Effective perms come from the DB's ``user_roles`` for the
    claimed society (the ``role_ids`` claim is ignored), so the caller has no
    notifications grant in B → 401/403, and definitely no leak of A's rows."""
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    a_notif_id = _fill_admin_feed(auth, db, hdr, n=1)

    soc_b, _admin_b, _hdr_b = second_society_with_notifications(db, superadmin, auth)
    bad_hdr = crafted_bearer(
        make_token, user_id=admin_user.id, society_id=soc_b.id, role_ids=[]
    )

    # Feed: either rejected outright or scoped-empty — never A's rows.
    feed_resp = auth.client.get("/notifications", headers=bad_hdr)
    assert feed_resp.status_code in (401, 403, 200), feed_resp.text
    if feed_resp.status_code == 200:
        assert feed_resp.json()["unread_count"] == 0
        assert feed_resp.json()["items"] == []

    # Mark-read of A's id via the B-claiming token cannot clear it.
    read_resp = auth.client.post(
        f"/notifications/{a_notif_id}/read", headers=bad_hdr
    )
    assert read_resp.status_code in (401, 403, 404), read_resp.text
    assert get_feed(auth.client, hdr)["unread_count"] == 1


# ===========================================================================
# super-admin behavior consistent with siblings
# ===========================================================================


def test_super_admin_bypasses_config_perm(db, society, admin_user, superadmin, auth):
    """A super-admin with ZERO notifications perms can still read config
    (is_super_admin bypasses has_permission), consistent with the notices suite."""
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    _strip_all_notifications_perms(db, society.id, "society_admin")
    admin_user.is_platform_super_admin = True
    db.add(admin_user)
    db.commit()
    # is_super_admin read fresh from the DB user each request — same bearer works.

    resp = auth.client.get("/notifications/config", headers=hdr)
    assert resp.status_code == 200, resp.text
    assert resp.json()["dues_advance_days"] == 3


# ===========================================================================
# config PUT validation
# ===========================================================================


def test_config_get_returns_defaults(db, society, admin_user, superadmin, auth):
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    resp = auth.client.get("/notifications/config", headers=hdr)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "dues_advance_days": 3,
        "dues_reminder_interval_days": 5,
        "read_retention_days": 30,
    }


@pytest.mark.parametrize(
    "body",
    [
        {"dues_advance_days": -1},      # below min 0
        {"dues_advance_days": 29},      # above max 28
        {"dues_reminder_interval_days": 0},   # below min 1
        {"dues_reminder_interval_days": 91},  # above max 90
        {"read_retention_days": 0},     # below min 1
        {"read_retention_days": 366},   # above max 365
    ],
)
def test_config_put_out_of_bounds_422(
    db, society, admin_user, superadmin, auth, body
):
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    resp = auth.client.put("/notifications/config", headers=hdr, json=body)
    assert resp.status_code == 422, resp.text


def test_config_put_extra_key_forbidden_422(db, society, admin_user, superadmin, auth):
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    resp = auth.client.put(
        "/notifications/config",
        headers=hdr,
        json={"dues_advance_days": 5, "bogus": 1},
    )
    assert resp.status_code == 422, resp.text


def test_config_put_all_none_422(db, society, admin_user, superadmin, auth):
    """An empty (all-None) request has nothing to update → 422 (service-enforced)."""
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    resp = auth.client.put("/notifications/config", headers=hdr, json={})
    assert resp.status_code == 422, resp.text


def test_config_put_partial_merge_preserves_other_keys(
    db, society, admin_user, superadmin, auth
):
    """A partial update changes only the provided key; the rest keep their values,
    and an audit row with before/after is written (docs §5)."""
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)

    resp = auth.client.put(
        "/notifications/config", headers=hdr, json={"dues_advance_days": 7}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "dues_advance_days": 7,             # changed
        "dues_reminder_interval_days": 5,   # preserved default
        "read_retention_days": 30,          # preserved default
    }

    # A GET reflects the merged value.
    got = auth.client.get("/notifications/config", headers=hdr)
    assert got.status_code == 200, got.text
    assert got.json()["dues_advance_days"] == 7
    assert got.json()["dues_reminder_interval_days"] == 5

    # A second partial merge touches a different key; the first survives.
    resp2 = auth.client.put(
        "/notifications/config", headers=hdr, json={"read_retention_days": 14}
    )
    assert resp2.status_code == 200, resp2.text
    assert resp2.json() == {
        "dues_advance_days": 7,
        "dues_reminder_interval_days": 5,
        "read_retention_days": 14,
    }

    # Audit: at least one notifications.config_updated row for this society.
    actions = audit_actions(db, society.id)
    assert any(a == "notifications.config_updated" for (a, _e, _i) in actions), actions


def test_config_put_boundary_values_ok(db, society, admin_user, superadmin, auth):
    """The inclusive bounds are accepted (0/28, 1/90, 1/365)."""
    hdr = setup_notifications(db, society, admin_user, superadmin, auth)
    resp = auth.client.put(
        "/notifications/config",
        headers=hdr,
        json={
            "dues_advance_days": 0,
            "dues_reminder_interval_days": 90,
            "read_retention_days": 365,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "dues_advance_days": 0,
        "dues_reminder_interval_days": 90,
        "read_retention_days": 365,
    }
