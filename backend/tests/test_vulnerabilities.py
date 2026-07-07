"""Adversarial security / vulnerability suite (docs/PF §4/§5/§7/§14).

A senior security QA sweep of the Platform Foundation. Every test asserts the
*negative* — that an attack does NOT work — and, where relevant, the positive
control that legitimate access DOES. Covers:

- Cross-tenant isolation: a non-super society_admin cannot reach ``/admin/*`` at
  all (403), so it can never pivot to another society through admin routes;
  ID-guessing a foreign society is 403 (gate fires before the lookup); and at the
  repository layer a B-scoped read never returns A's rows.
- No plaintext secrets at rest: default member password, user password, and
  refresh tokens are all hashed; the raw refresh token handed to the client is
  never the value stored.
- JWT tampering: flipped signature, wrong-secret signature, ``alg:none``, and a
  blank/garbage bearer are all 401 (algorithm pinned).
- must_change lockout cannot be bypassed on ``/me`` or an ``/admin`` route.
- SQL-injection smoke: an injection email fails to authenticate (generic 401, no
  500); a society name with quotes/semicolons round-trips verbatim.
- Secrets never surface in any response/error body.
- super_admin bypass is correct but bounded: the flag — not forged ``role_ids`` —
  is what admits ``/admin/*``.

Uses the shared harness fixtures (conftest). Asserts status + body + DB.
"""
from __future__ import annotations

import hashlib

import jwt
from sqlalchemy import select

from app.core.config import settings
from app.core.security import hash_password
from app.platform.models import RefreshToken, Society, User
from app.platform.societies.schemas import SocietyCreate
from app.platform.societies.service import SocietyService
from app.platform.users.provisioning import UserProvisioningService
from tests.conftest import (
    DEFAULT_MEMBER_PASSWORD,
    SUPERADMIN_EMAIL,
    SUPERADMIN_PASSWORD,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _su_headers(auth) -> dict[str, str]:
    tok = auth.login_ok(SUPERADMIN_EMAIL, SUPERADMIN_PASSWORD)["access_token"]
    return auth.bearer(tok)


def _create_society_via_api(client, headers, name: str, password: str) -> int:
    resp = client.post(
        "/admin/societies",
        json={
            "name": name,
            "storage_limit_bytes": 5 * 1024**3,
            "default_member_password": password,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _activate(db, email: str, password: str) -> None:
    """Flip a provisioned (must_change) user to active with a known password so it
    can log in and be used as an authenticated *non-super* caller."""
    user = db.query(User).filter(User.email == email).one()
    user.password_state = "active"
    user.password_hash = hash_password(password)
    db.commit()


def _me_status(client, token: str) -> int:
    return client.get(
        "/me", headers={"Authorization": f"Bearer {token}"}
    ).status_code


# ==========================================================================
# 1. CROSS-TENANT ISOLATION
# ==========================================================================


def test_non_super_admin_cannot_reach_admin_routes_at_all(client, auth, db):
    """A society_admin (non-super) provisioned in A gets 403 on EVERY /admin/*
    route — so it can never reach another society's data via the admin surface."""
    su = _su_headers(auth)
    a_id = _create_society_via_api(client, su, "Society A", DEFAULT_MEMBER_PASSWORD)
    b_id = _create_society_via_api(client, su, "Society B", DEFAULT_MEMBER_PASSWORD)

    # Provision A's admin.
    r = client.post(
        f"/admin/societies/{a_id}/users",
        json={"email": "a-admin@test.local", "full_name": "A Admin"},
        headers=su,
    )
    assert r.status_code == 201, r.text

    _activate(db, "a-admin@test.local", "AdminPass123")
    a_token = auth.login_ok("a-admin@test.local", "AdminPass123")["access_token"]
    a_hdr = auth.bearer(a_token)

    # A's admin is denied on the full admin surface (list / get own / create).
    assert client.get("/admin/societies", headers=a_hdr).status_code == 403
    assert (
        client.get(f"/admin/societies/{a_id}", headers=a_hdr).status_code == 403
    )
    create_b_user = client.post(
        f"/admin/societies/{b_id}/users",
        json={"email": "hijack@test.local"},
        headers=a_hdr,
    )
    assert create_b_user.status_code == 403, create_b_user.text
    assert create_b_user.json()["code"] == "permission_denied"


def test_id_guessing_foreign_society_is_403_not_404(client, auth, db):
    """A's admin (non-super) gets 403 on GET /admin/societies/{B_id} — and the SAME
    403 for a non-existent id. The super-admin gate fires BEFORE the lookup, so the
    response never leaks whether the guessed id exists (no 404 oracle)."""
    su = _su_headers(auth)
    a_id = _create_society_via_api(client, su, "Society A", DEFAULT_MEMBER_PASSWORD)
    b_id = _create_society_via_api(client, su, "Society B", DEFAULT_MEMBER_PASSWORD)

    r = client.post(
        f"/admin/societies/{a_id}/users",
        json={"email": "a-admin2@test.local"},
        headers=su,
    )
    assert r.status_code == 201, r.text
    _activate(db, "a-admin2@test.local", "AdminPass123")
    a_hdr = auth.bearer(
        auth.login_ok("a-admin2@test.local", "AdminPass123")["access_token"]
    )

    # Real foreign id, own id, and a nonexistent id: all identical 403.
    for guessed in (b_id, a_id, 999_999):
        resp = client.get(f"/admin/societies/{guessed}", headers=a_hdr)
        assert resp.status_code == 403, (guessed, resp.text)
        assert resp.json()["code"] == "permission_denied"


def test_repository_reads_never_cross_tenants(db, superadmin):
    """At the repository/service layer, a B-scoped read never returns A's rows
    (mirror of test_tenant_and_gates style). Proves tenant scoping is enforced at
    the single source of truth even though society-scoped HTTP routes don't exist
    yet."""
    from app.platform.roles.repository import RoleRepository
    from app.platform.societies.repository import SocietyRepository
    from app.platform.users.repository import UserRepository

    svc = SocietyService(db)
    soc_a = svc.create_society(
        SocietyCreate(
            name="Iso A",
            storage_limit_bytes=1_000_000,
            default_member_password=DEFAULT_MEMBER_PASSWORD,
        ),
        actor_user_id=superadmin.id,
    )
    soc_b = svc.create_society(
        SocietyCreate(
            name="Iso B",
            storage_limit_bytes=1_000_000,
            default_member_password=DEFAULT_MEMBER_PASSWORD,
        ),
        actor_user_id=superadmin.id,
    )
    db.commit()

    # Provision an admin in A only.
    a_admin = UserProvisioningService(db).create_or_link_user(
        email="iso-a-admin@test.local",
        society_id=soc_a.id,
        role_key="society_admin",
        profile={"full_name": "Iso A Admin"},
        actor_user_id=superadmin.id,
    )
    db.commit()

    roles = RoleRepository(db)
    users = UserRepository(db)
    socs = SocietyRepository(db)

    # A's society_admin role is visible scoped to A, invisible scoped to B.
    assert roles.society_role_by_key(soc_a.id, "society_admin") is not None
    assert roles.society_role_by_key(soc_b.id, "society_admin") is not None  # own copy
    # But the ROLE ROW for A's admin belongs to A; B's copy is a distinct row.
    a_role = roles.society_role_by_key(soc_a.id, "society_admin")
    b_role = roles.society_role_by_key(soc_b.id, "society_admin")
    assert a_role.id != b_role.id

    # The A admin's user_role is reachable ONLY under society A.
    assert users.get_user_role(a_admin.id, soc_a.id, a_role.id) is not None
    assert users.get_user_role(a_admin.id, soc_b.id, a_role.id) is None
    assert users.get_user_role(a_admin.id, soc_b.id, b_role.id) is None

    # Effective permissions / portals for the A admin: present in A, empty in B.
    assert "admin" in roles.user_portals(a_admin.id, soc_a.id)
    assert roles.user_portals(a_admin.id, soc_b.id) == []
    assert roles.effective_permission_keys(a_admin.id, soc_b.id) == set()

    # Both societies exist and are distinct rows.
    assert socs.get(soc_a.id) is not None
    assert socs.get(soc_b.id) is not None
    assert soc_a.id != soc_b.id


# ==========================================================================
# 2. NO PLAINTEXT SECRETS IN THE DB
# ==========================================================================


def test_no_plaintext_secrets_at_rest(client, auth, db):
    """After create-society + provision-user + login: the society default password,
    the user password, and the refresh token are all stored HASHED. The raw refresh
    token handed to the client is NOT any stored token_hash; sha256(raw) IS."""
    su = _su_headers(auth)
    sid = _create_society_via_api(
        client, su, "Secrets Society", DEFAULT_MEMBER_PASSWORD
    )

    r = client.post(
        f"/admin/societies/{sid}/users",
        json={"email": "secret-admin@test.local", "full_name": "Secret Admin"},
        headers=su,
    )
    assert r.status_code == 201, r.text

    # society.default_member_password_hash — Argon2id, never the plaintext.
    soc = db.get(Society, sid)
    assert soc.default_member_password_hash.startswith("$argon2")
    assert DEFAULT_MEMBER_PASSWORD not in soc.default_member_password_hash

    # users.password_hash — Argon2id, never the plaintext.
    user = db.query(User).filter(User.email == "secret-admin@test.local").one()
    assert user.password_hash.startswith("$argon2")
    assert DEFAULT_MEMBER_PASSWORD not in user.password_hash

    # Login → issue a refresh token.
    login = auth.login_ok("secret-admin@test.local", DEFAULT_MEMBER_PASSWORD)
    raw_refresh = login["refresh_token"]

    stored_hashes = [
        h for (h,) in db.execute(select(RefreshToken.token_hash)).all()
    ]
    assert stored_hashes, "expected at least one refresh_tokens row"

    for h in stored_hashes:
        # 64-char lowercase hex == sha256 digest, never the raw token.
        assert len(h) == 64
        int(h, 16)  # raises if not hex
        assert h != raw_refresh

    # The raw token is absent from the DB; its sha256 IS present.
    assert raw_refresh not in stored_hashes
    assert hashlib.sha256(raw_refresh.encode()).hexdigest() in stored_hashes


# ==========================================================================
# 3. JWT TAMPERING
# ==========================================================================


def test_jwt_flipped_signature_rejected(client, make_token, superadmin):
    token = make_token(user_id=superadmin.id, password_state="active")
    # Tamper at the BYTE level (decode → XOR a byte → re-encode). Flipping a
    # base64url *character* — especially the last one — is unreliable: the final
    # char carries "don't care" trailing bits, so a different char can decode to
    # the SAME signature bytes and the token still validates (~15% intermittent
    # false pass). This mirrors the fix applied to the twin test in test_auth.py.
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
    forged = jwt.encode(
        payload, "totally-the-wrong-secret", algorithm=settings.jwt_algorithm
    )
    assert _me_status(client, forged) == 401


def test_jwt_alg_none_rejected(client, superadmin):
    """An unsigned ('alg: none') token must be rejected — the decoder pins the
    algorithm to HS256, closing the algorithm-confusion class."""
    payload = {
        "sub": str(superadmin.id),
        "user_id": superadmin.id,
        "active_society_id": None,
        "role_ids": [],
        "password_state": "active",
    }
    none_token = jwt.encode(payload, key="", algorithm="none")
    assert _me_status(client, none_token) == 401


def test_blank_and_garbage_bearer_rejected(client):
    assert _me_status(client, "") == 401
    assert _me_status(client, "garbage.not.a.jwt") == 401
    assert _me_status(client, "not-even-dotted") == 401
    assert client.get("/me").status_code == 401


# ==========================================================================
# 4. must_change CANNOT BE BYPASSED
# ==========================================================================


def test_must_change_token_rejected_on_me_and_admin(client, auth, make_token, db):
    """A must_change user's token is rejected (403) on /me AND on an /admin route;
    only /auth/change-password is reachable."""
    su = _su_headers(auth)
    sid = _create_society_via_api(client, su, "MC Society", DEFAULT_MEMBER_PASSWORD)
    r = client.post(
        f"/admin/societies/{sid}/users",
        json={"email": "mc-admin@test.local"},
        headers=su,
    )
    assert r.status_code == 201, r.text

    # Real login token carries the must_change claim from issue time.
    login = auth.login_ok("mc-admin@test.local", DEFAULT_MEMBER_PASSWORD)
    assert login["password_state"] == "must_change"
    headers = auth.bearer(login["access_token"])

    # /me → 403 permission_denied (the global lockout).
    me = client.get("/me", headers=headers)
    assert me.status_code == 403, me.text
    assert me.json()["code"] == "permission_denied"
    assert me.json()["details"]["password_state"] == "must_change"

    # An /admin route → 403 as well (lockout fires in get_auth_context, before the
    # super-admin gate even runs). Never leaks that this is a non-super account.
    admin_call = client.get("/admin/societies", headers=headers)
    assert admin_call.status_code == 403, admin_call.text

    # Even a *crafted* must_change token for this user cannot reach /me.
    user = db.query(User).filter(User.email == "mc-admin@test.local").one()
    crafted = make_token(
        user_id=user.id,
        active_society_id=sid,
        role_ids=[],
        password_state="must_change",
    )
    assert _me_status(client, crafted) == 403

    # ONLY change-password works.
    cp = client.post(
        "/auth/change-password",
        headers=headers,
        json={
            "current_password": DEFAULT_MEMBER_PASSWORD,
            "new_password": "FreshPass123",
        },
    )
    assert cp.status_code == 200, cp.text


# ==========================================================================
# 5. SQL-INJECTION SAFETY (SMOKE)
# ==========================================================================


def test_sql_injection_login_does_not_authenticate(auth):
    """Classic injection payloads in the email field NEVER authenticate — they are
    either rejected at the edge (422 email validation) or fail auth (generic 401).
    Crucially: never a 500, never a leaked SQL error, never a bypass (200)."""
    payloads = (
        "x' OR '1'='1",
        "admin@test.local'; DROP TABLE users;--",
        # Well-formed-looking address whose local part carries injection syntax,
        # so it can pass the email regex and reach the parameterized query layer.
        "attacker'or'1'='1@test.local",
        "a@b.co' OR 1=1 --",
    )
    for payload in payloads:
        resp = auth.login(payload, "anything")
        # Must be rejected: validation (422) or auth (401). NEVER 200, NEVER 500.
        assert resp.status_code in (401, 422), (payload, resp.text)
        # No SQL error / stack trace surfaced in any branch.
        lowered = resp.text.lower()
        assert "syntax" not in lowered
        assert "sqlstate" not in lowered
        assert "traceback" not in lowered
        if resp.status_code == 401:
            body = resp.json()
            assert body["code"] == "authentication_error"
            assert body["message"] == "Invalid email or password."


def test_society_name_with_quotes_stored_verbatim(client, auth, db):
    """A society name containing quotes/semicolons is stored + returned VERBATIM
    (parameterized queries) — proof no injection or truncation occurs, and the
    users table still exists afterwards."""
    su = _su_headers(auth)
    nasty = "Robert'); DROP TABLE users;-- \"O'Brien\" & <b>x</b>"
    sid = _create_society_via_api(client, su, nasty, DEFAULT_MEMBER_PASSWORD)

    # Round-trips verbatim via the API.
    got = client.get(f"/admin/societies/{sid}", headers=su)
    assert got.status_code == 200, got.text
    assert got.json()["name"] == nasty

    # And verbatim in the DB.
    soc = db.get(Society, sid)
    assert soc.name == nasty

    # The users table was NOT dropped — the super-admin still resolves.
    assert db.query(User).filter(User.email == SUPERADMIN_EMAIL).count() == 1


# ==========================================================================
# 6. PASSWORD / TOKEN NEVER IN RESPONSE OR ERROR BODIES
# ==========================================================================


def test_secrets_never_in_response_bodies(client, auth, db):
    """Scan login/refresh/me/create-society/create-user JSON: none contains
    'password_hash', 'default_member_password_hash', or the literal default
    password."""
    su = _su_headers(auth)

    forbidden = (
        "password_hash",
        "default_member_password_hash",
        DEFAULT_MEMBER_PASSWORD,
    )

    def _assert_clean(resp, *, label: str) -> None:
        for token in forbidden:
            assert token not in resp.text, f"{label} leaked {token!r}: {resp.text}"

    # create-society
    create_soc = client.post(
        "/admin/societies",
        json={
            "name": "Scan Society",
            "storage_limit_bytes": 5 * 1024**3,
            "default_member_password": DEFAULT_MEMBER_PASSWORD,
        },
        headers=su,
    )
    assert create_soc.status_code == 201
    _assert_clean(create_soc, label="create-society")
    sid = create_soc.json()["id"]

    # create-user
    create_user = client.post(
        f"/admin/societies/{sid}/users",
        json={"email": "scan-admin@test.local", "full_name": "Scan Admin"},
        headers=su,
    )
    assert create_user.status_code == 201
    _assert_clean(create_user, label="create-user")

    # login
    login = auth.login("scan-admin@test.local", DEFAULT_MEMBER_PASSWORD)
    assert login.status_code == 200
    _assert_clean(login, label="login")

    # refresh
    refresh = client.post(
        "/auth/refresh", json={"refresh_token": login.json()["refresh_token"]}
    )
    assert refresh.status_code == 200
    _assert_clean(refresh, label="refresh")

    # /me (activate first so it is reachable)
    _activate(db, "scan-admin@test.local", "ScanPass123")
    me_login = auth.login_ok("scan-admin@test.local", "ScanPass123")
    me = client.get("/me", headers=auth.bearer(me_login["access_token"]))
    assert me.status_code == 200, me.text
    _assert_clean(me, label="me")


# ==========================================================================
# 7. super_admin BYPASS IS CORRECT BUT BOUNDED
# ==========================================================================


def test_super_admin_can_reach_admin_but_normal_user_cannot(client, auth, db):
    su = _su_headers(auth)
    # super_admin CAN list societies.
    assert client.get("/admin/societies", headers=su).status_code == 200

    # A normal (non-super) user CANNOT.
    sid = _create_society_via_api(client, su, "Bound Society", DEFAULT_MEMBER_PASSWORD)
    r = client.post(
        f"/admin/societies/{sid}/users",
        json={"email": "normal@test.local"},
        headers=su,
    )
    assert r.status_code == 201, r.text
    _activate(db, "normal@test.local", "NormalPass123")
    normal_hdr = auth.bearer(
        auth.login_ok("normal@test.local", "NormalPass123")["access_token"]
    )
    assert client.get("/admin/societies", headers=normal_hdr).status_code == 403


def test_forged_role_ids_do_not_grant_super_admin(client, auth, make_token, db):
    """A normal user cannot forge super-admin by stuffing arbitrary role_ids into
    their token: is_super_admin comes ONLY from the is_platform_super_admin DB flag,
    which get_auth_context reads server-side. A crafted, correctly-SIGNED token with
    bogus role_ids still gets 403 on /admin/*."""
    su = _su_headers(auth)
    sid = _create_society_via_api(
        client, su, "Forge Society", DEFAULT_MEMBER_PASSWORD
    )
    r = client.post(
        f"/admin/societies/{sid}/users",
        json={"email": "forger@test.local"},
        headers=su,
    )
    assert r.status_code == 201, r.text
    _activate(db, "forger@test.local", "ForgePass123")

    user = db.query(User).filter(User.email == "forger@test.local").one()
    assert user.is_platform_super_admin is False

    # Mint a VALID token (right secret) but with arbitrary/huge role_ids — trying to
    # impersonate privilege via claims. The token is genuinely signed, so it passes
    # signature checks; authority must still be denied.
    forged = make_token(
        user_id=user.id,
        active_society_id=sid,
        role_ids=[1, 2, 3, 999999],
        password_state="active",
    )
    resp = client.get("/admin/societies", headers=auth.bearer(forged))
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "permission_denied"

    # Also cannot create users in any society with the forged token.
    resp2 = client.post(
        f"/admin/societies/{sid}/users",
        json={"email": "victim@test.local"},
        headers=auth.bearer(forged),
    )
    assert resp2.status_code == 403, resp2.text
