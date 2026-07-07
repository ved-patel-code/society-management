"""Adversarial auth-surface tests (docs/PF §4, §14.5).

Covers login (happy/no-enumeration/role-less), refresh rotation + theft,
logout, the must_change lockout + change-password escape hatch,
forgot-password enumeration-safety + DB side effects, JWT tampering, and the
invariant that a password/hash never appears in any auth response body.

Uses only the shared harness fixtures (conftest). Assertions check BOTH status
codes AND response bodies / DB state.
"""
from __future__ import annotations

from app.core.config import settings
from app.core.security import hash_password
from app.platform.models import AuditLog, PasswordReset, RefreshToken, User
from tests.conftest import (
    DEFAULT_MEMBER_PASSWORD,
    SUPERADMIN_EMAIL,
    SUPERADMIN_PASSWORD,
)


# --------------------------------------------------------------------------
# Login — happy paths
# --------------------------------------------------------------------------


def test_login_super_admin_happy(auth):
    body = auth.login_ok(SUPERADMIN_EMAIL, SUPERADMIN_PASSWORD)
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"
    assert body["password_state"] == "active"
    assert body["available_portals"] == ["platform"]


def test_login_provisioned_admin_must_change(auth, admin_user):
    body = auth.login_ok("admin@test.local", DEFAULT_MEMBER_PASSWORD)
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["password_state"] == "must_change"
    assert body["available_portals"] == ["admin"]


# --------------------------------------------------------------------------
# Login — no enumeration (identical generic 401 bodies)
# --------------------------------------------------------------------------


def test_login_no_enumeration_identical_bodies(auth, db, admin_user):
    # An inactive user (real account, correct password, but is_active=False).
    inactive = User(
        email="inactive@test.local",
        password_hash=hash_password("InactivePass123"),
        password_state="active",
        is_active=False,
        full_name="Inactive",
    )
    db.add(inactive)
    db.commit()

    unknown = auth.login("nobody@nowhere.test", "whatever123")
    wrong_pw = auth.login("admin@test.local", "definitely-wrong-000")
    inactive_resp = auth.login("inactive@test.local", "InactivePass123")

    for r in (unknown, wrong_pw, inactive_resp):
        assert r.status_code == 401, r.text

    # Byte-identical bodies — no field distinguishes the failure reason.
    assert unknown.content == wrong_pw.content
    assert unknown.content == inactive_resp.content

    payload = unknown.json()
    assert payload["code"] == "authentication_error"
    assert payload["message"] == "Invalid email or password."


def test_login_roleless_non_super_admin_rejected(auth, db):
    """A plain user with no user_roles (and not super-admin) cannot log in."""
    u = User(
        email="orphan@test.local",
        password_hash=hash_password("OrphanPass123"),
        password_state="active",
        is_active=True,
        is_platform_super_admin=False,
        full_name="Orphan",
    )
    db.add(u)
    db.commit()

    resp = auth.login("orphan@test.local", "OrphanPass123")
    assert resp.status_code == 401, resp.text
    body = resp.json()
    assert body["code"] == "authentication_error"
    assert body["message"] == "Invalid email or password."


# --------------------------------------------------------------------------
# Refresh — rotation
# --------------------------------------------------------------------------


def test_refresh_rotation_old_dies_new_works(auth, client, admin_user):
    login = auth.login_ok("admin@test.local", DEFAULT_MEMBER_PASSWORD)
    old_refresh = login["refresh_token"]

    r1 = client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert r1.status_code == 200, r1.text
    new_pair = r1.json()
    new_refresh = new_pair["refresh_token"]
    assert new_pair["access_token"]
    assert new_refresh != old_refresh

    # The OLD (rotated-away) token no longer works.
    r_old = client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert r_old.status_code == 401, r_old.text

    # The NEW token works... but note: reusing old_refresh above tripped theft
    # detection, which revokes the whole chain. So we assert theft behavior in a
    # dedicated test and here only assert the freshly-minted token rotated once
    # BEFORE any reuse. Re-run a clean rotation to prove the new token is usable.


def test_refresh_new_token_is_usable(auth, client, admin_user):
    login = auth.login_ok("admin@test.local", DEFAULT_MEMBER_PASSWORD)
    r1 = client.post("/auth/refresh", json={"refresh_token": login["refresh_token"]})
    assert r1.status_code == 200, r1.text
    new_refresh = r1.json()["refresh_token"]

    # Using the new token (no reuse of the old) rotates cleanly again.
    r2 = client.post("/auth/refresh", json={"refresh_token": new_refresh})
    assert r2.status_code == 200, r2.text
    assert r2.json()["refresh_token"] != new_refresh


# --------------------------------------------------------------------------
# Refresh — theft (reuse of a rotated token)
# --------------------------------------------------------------------------


def test_refresh_theft_revokes_chain_and_audits(auth, client, db, admin_user):
    login = auth.login_ok("admin@test.local", DEFAULT_MEMBER_PASSWORD)
    old_refresh = login["refresh_token"]

    r1 = client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert r1.status_code == 200, r1.text
    new_refresh = r1.json()["refresh_token"]

    # Reuse of the rotated-away OLD token == theft signal.
    reuse = client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert reuse.status_code == 401, reuse.text

    # An audit row was written on the theft's isolated transaction.
    db.expire_all()
    rows = (
        db.query(AuditLog)
        .filter(
            AuditLog.action == "auth.token_reuse_detected",
            AuditLog.actor_user_id == admin_user.id,
        )
        .all()
    )
    assert rows, "expected an auth.token_reuse_detected audit row"
    assert rows[0].after["reason"] == "refresh_token_reuse"

    # The whole chain is revoked: the NEW token (rotated from old) is dead too.
    # (Presenting it now re-trips theft detection → still 401.)
    after_theft = client.post("/auth/refresh", json={"refresh_token": new_refresh})
    assert after_theft.status_code == 401, after_theft.text

    # Every refresh token for the user is revoked.
    db.expire_all()
    live = (
        db.query(RefreshToken)
        .filter(
            RefreshToken.user_id == admin_user.id,
            RefreshToken.revoked_at.is_(None),
        )
        .count()
    )
    assert live == 0


# --------------------------------------------------------------------------
# Logout
# --------------------------------------------------------------------------


def test_logout_revokes_refresh(auth, client, admin_user):
    login = auth.login_ok("admin@test.local", DEFAULT_MEMBER_PASSWORD)
    refresh = login["refresh_token"]

    out = client.post("/auth/logout", json={"refresh_token": refresh})
    assert out.status_code == 200, out.text
    assert out.json()["message"]

    # The logged-out refresh token can no longer rotate.
    r = client.post("/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 401, r.text


def test_logout_unknown_token_is_idempotent(client):
    out = client.post("/auth/logout", json={"refresh_token": "not-a-real-token"})
    assert out.status_code == 200, out.text


# --------------------------------------------------------------------------
# must_change lockout + change-password escape hatch
# --------------------------------------------------------------------------


def test_must_change_blocks_me_but_allows_change_password(auth, client, admin_user):
    login = auth.login_ok("admin@test.local", DEFAULT_MEMBER_PASSWORD)
    access = login["access_token"]
    headers = auth.bearer(access)

    # /me is blocked (403) while must_change.
    me = client.get("/me", headers=headers)
    assert me.status_code == 403, me.text
    assert me.json()["code"] == "permission_denied"

    # change-password IS reachable.
    cp = client.post(
        "/auth/change-password",
        headers=headers,
        json={
            "current_password": DEFAULT_MEMBER_PASSWORD,
            "new_password": "BrandNewPass123",
        },
    )
    assert cp.status_code == 200, cp.text


def test_change_password_flips_state_and_revokes_and_relogin(
    auth, client, db, admin_user
):
    login = auth.login_ok("admin@test.local", DEFAULT_MEMBER_PASSWORD)
    access = login["access_token"]
    old_refresh = login["refresh_token"]
    headers = auth.bearer(access)

    cp = client.post(
        "/auth/change-password",
        headers=headers,
        json={
            "current_password": DEFAULT_MEMBER_PASSWORD,
            "new_password": "BrandNewPass123",
        },
    )
    assert cp.status_code == 200, cp.text

    # State flipped to active in the DB.
    db.expire_all()
    user = db.get(User, admin_user.id)
    assert user.password_state == "active"

    # Old sessions revoked: old refresh token no longer rotates.
    r = client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert r.status_code == 401, r.text

    # New login with the new password works and is now 'active'.
    new_login = auth.login_ok("admin@test.local", "BrandNewPass123")
    assert new_login["password_state"] == "active"
    assert new_login["available_portals"] == ["admin"]

    # And the old password no longer works.
    old = auth.login("admin@test.local", DEFAULT_MEMBER_PASSWORD)
    assert old.status_code == 401, old.text


# --------------------------------------------------------------------------
# change-password rules
# --------------------------------------------------------------------------


def test_change_password_wrong_current_401(auth, client, admin_user):
    login = auth.login_ok("admin@test.local", DEFAULT_MEMBER_PASSWORD)
    headers = auth.bearer(login["access_token"])

    cp = client.post(
        "/auth/change-password",
        headers=headers,
        json={
            "current_password": "wrong-current-000",
            "new_password": "BrandNewPass123",
        },
    )
    assert cp.status_code == 401, cp.text
    assert cp.json()["code"] == "authentication_error"


def test_change_password_new_equals_current_rejected(auth, client, db, admin_user):
    login = auth.login_ok("admin@test.local", DEFAULT_MEMBER_PASSWORD)
    headers = auth.bearer(login["access_token"])

    cp = client.post(
        "/auth/change-password",
        headers=headers,
        json={
            "current_password": DEFAULT_MEMBER_PASSWORD,
            "new_password": DEFAULT_MEMBER_PASSWORD,
        },
    )
    assert cp.status_code in (400, 422), cp.text
    assert cp.json()["code"] == "validation_error"

    # State unchanged (still must_change).
    db.expire_all()
    assert db.get(User, admin_user.id).password_state == "must_change"


# --------------------------------------------------------------------------
# forgot-password — no enumeration + DB side effects
# --------------------------------------------------------------------------


def test_forgot_password_unknown_email_no_side_effects(client, db):
    before_resets = db.query(PasswordReset).count()

    resp = client.post("/auth/forgot-password", json={"email": "ghost@nowhere.test"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["message"]

    db.expire_all()
    # No reset row created for a non-existent account.
    assert db.query(PasswordReset).count() == before_resets


def test_forgot_password_known_user_creates_reset_and_must_change(
    client, db, admin_user
):
    # Precondition: change to active so we can observe the flip back to must_change.
    admin = db.get(User, admin_user.id)
    admin.password_state = "active"
    db.commit()

    resp = client.post("/auth/forgot-password", json={"email": "admin@test.local"})
    assert resp.status_code == 200, resp.text

    db.expire_all()
    # A password_resets row was created for the real, role-bearing user.
    resets = (
        db.query(PasswordReset)
        .filter(PasswordReset.user_id == admin_user.id)
        .all()
    )
    assert len(resets) == 1
    assert resets[0].consumed_at is None
    assert resets[0].temp_password_hash  # hashed, never plaintext

    # The user's state was forced to must_change.
    assert db.get(User, admin_user.id).password_state == "must_change"


def test_forgot_password_generic_body_matches_for_known_and_unknown(
    client, admin_user
):
    known = client.post("/auth/forgot-password", json={"email": "admin@test.local"})
    unknown = client.post("/auth/forgot-password", json={"email": "ghost@nowhere.test"})
    assert known.status_code == 200
    assert unknown.status_code == 200
    # Enumeration-safe: identical acknowledgement regardless of existence.
    assert known.content == unknown.content


def test_forgot_password_super_admin_gets_no_reset(client, db):
    """Super-admin is role-less by design → forgot-password is a no-op for them."""
    su = db.query(User).filter(User.email == SUPERADMIN_EMAIL).one()

    resp = client.post("/auth/forgot-password", json={"email": SUPERADMIN_EMAIL})
    assert resp.status_code == 200, resp.text

    db.expire_all()
    assert db.query(PasswordReset).filter(PasswordReset.user_id == su.id).count() == 0
    # Super-admin state untouched.
    assert db.get(User, su.id).password_state == "active"


# --------------------------------------------------------------------------
# JWT tampering / bad bearer
# --------------------------------------------------------------------------


def _me_status(client, token):
    return client.get("/me", headers={"Authorization": f"Bearer {token}"}).status_code


def test_jwt_tampered_signature_rejected(client, make_token, superadmin):
    token = make_token(user_id=superadmin.id, password_state="active")
    # Tamper the signature at the BYTE level. Flipping a base64url *character*
    # (esp. the last one) is unreliable: the final char carries "don't care"
    # trailing bits, so a different char can decode to the SAME signature bytes
    # and the token still validates — an intermittent false pass. Decode → flip a
    # byte → re-encode guarantees a genuinely different, invalid signature.
    import base64

    head, payload, sig = token.split(".")

    def _b64url_decode(seg: str) -> bytes:
        return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))

    def _b64url_encode(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    sig_bytes = _b64url_decode(sig)
    tampered_bytes = bytes([sig_bytes[0] ^ 0xFF]) + sig_bytes[1:]
    tampered = f"{head}.{payload}.{_b64url_encode(tampered_bytes)}"

    # Guard: the tamper genuinely changed the signature bytes.
    assert tampered_bytes != sig_bytes
    assert _me_status(client, tampered) == 401


def test_jwt_wrong_secret_rejected(client, superadmin):
    # A token that is well-formed but signed with the wrong secret.
    import jwt as _jwt

    from app.common.time import utcnow

    now = utcnow()
    payload = {
        "sub": str(superadmin.id),
        "user_id": superadmin.id,
        "active_society_id": None,
        "role_ids": [],
        "password_state": "active",
        "iat": int(now.timestamp()),
        "exp": int(now.timestamp()) + 3600,
    }
    forged = _jwt.encode(
        payload, "not-the-real-secret", algorithm=settings.jwt_algorithm
    )
    assert _me_status(client, forged) == 401


def test_jwt_expired_rejected(client, superadmin):
    import jwt as _jwt

    from app.common.time import utcnow

    now = utcnow()
    payload = {
        "sub": str(superadmin.id),
        "user_id": superadmin.id,
        "active_society_id": None,
        "role_ids": [],
        "password_state": "active",
        "iat": int(now.timestamp()) - 7200,
        "exp": int(now.timestamp()) - 3600,  # expired an hour ago
    }
    expired = _jwt.encode(
        payload, settings.jwt_secret, algorithm=settings.jwt_algorithm
    )
    assert _me_status(client, expired) == 401


def test_blank_and_garbage_bearer_rejected(client):
    assert _me_status(client, "") == 401
    assert _me_status(client, "garbage-not-a-jwt") == 401
    # No Authorization header at all.
    assert client.get("/me").status_code == 401


def test_alg_none_token_rejected(client, superadmin):
    """An unsigned ('alg: none') token must be rejected (alg pinned to HS256)."""
    import jwt as _jwt

    payload = {
        "sub": str(superadmin.id),
        "user_id": superadmin.id,
        "active_society_id": None,
        "role_ids": [],
        "password_state": "active",
    }
    none_token = _jwt.encode(payload, key="", algorithm="none")
    assert _me_status(client, none_token) == 401


# --------------------------------------------------------------------------
# Password / hash never leaks in responses
# --------------------------------------------------------------------------


def test_password_never_leaks_in_auth_responses(auth, client, admin_user):
    login_resp = auth.login("admin@test.local", DEFAULT_MEMBER_PASSWORD)
    assert login_resp.status_code == 200

    refresh_resp = client.post(
        "/auth/refresh", json={"refresh_token": login_resp.json()["refresh_token"]}
    )
    assert refresh_resp.status_code == 200

    for resp in (login_resp, refresh_resp):
        text = resp.text
        assert "password_hash" not in text
        assert DEFAULT_MEMBER_PASSWORD not in text


def test_me_response_never_leaks_password(auth, client, db, admin_user):
    # Move admin to 'active' so /me is reachable.
    admin = db.get(User, admin_user.id)
    admin.password_state = "active"
    db.commit()

    login = auth.login_ok("admin@test.local", DEFAULT_MEMBER_PASSWORD)
    # Note: login token carries must_change claim from issue-time; re-login after
    # the DB flip yields an 'active' claim.
    me = client.get("/me", headers=auth.bearer(login["access_token"]))
    assert me.status_code == 200, me.text
    body = me.text
    assert "password_hash" not in body
    assert DEFAULT_MEMBER_PASSWORD not in body
