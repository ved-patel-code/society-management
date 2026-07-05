"""AuthService — login, change-password, forgot-password (docs/PF §4).

The brain for the auth feature. All queries go through :class:`AuthRepository`;
the token lifecycle is delegated to :class:`TokenService`; portals come from
:class:`RoleService`. Every state change is written in the request's single
transaction and NEVER committed here (``get_session`` commits once — docs/PF §12).

Security invariants enforced here (docs/PF §4):
- **No account enumeration.** Login failures (missing user / inactive / bad
  password / no roles) all raise the SAME generic ``AuthenticationError``.
  Forgot-password always returns a generic acknowledgement and sends mail ONLY
  when the email maps to a real, role-bearing user.
- **Super-admin exception.** A ``is_platform_super_admin`` user may log in with no
  ``user_roles`` (active_society_id=None, role_ids=[], portals=['platform']).
- **must_change is not a login blocker.** Login succeeds and returns tokens even
  when ``password_state='must_change'``; the lockout on every other endpoint is
  core/deps.py's job. change-password is the escape hatch.
- Passwords: hashed (Argon2id) via core.security; never plaintext, never logged.
"""
from __future__ import annotations

import secrets
import string
from datetime import timedelta

from sqlalchemy.orm import Session

from app.common.errors import AuthenticationError, ValidationError
from app.common.time import utcnow
from app.common.validators import normalize_email, validate_password_policy
from app.core.config import settings
from app.core.email import EmailMessage, EmailSender
from app.core.security import hash_password, verify_password
from app.platform.audit.service import AuditService
from app.platform.auth.repository import AuthRepository
from app.platform.auth.token_service import TokenService
from app.platform.models import PasswordReset, User
from app.platform.roles.service import RoleService

# Generic, non-enumerating login failure (missing / inactive / bad pw / no roles).
_GENERIC_LOGIN_ERROR = "Invalid email or password."

# The platform-actor portal a role-less super_admin lands in.
_PLATFORM_PORTAL = "platform"

# A pre-computed Argon2id hash used to equalize verify timing on reject branches
# where there is no real user hash to check against (anti-enumeration, docs/PF §4).
_DUMMY_HASH = hash_password("timing-equalizer-not-a-real-password")


class LoginResult:
    """Value object returned by :meth:`login` (the router shapes the response)."""

    __slots__ = (
        "access_token",
        "refresh_token",
        "password_state",
        "available_portals",
    )

    def __init__(
        self,
        *,
        access_token: str,
        refresh_token: str,
        password_state: str,
        available_portals: list[str],
    ) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.password_state = password_state
        self.available_portals = available_portals


class AuthService:
    """Login, password-change, and forgot-password flows (docs/PF §4)."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = AuthRepository(session)
        self._tokens = TokenService(session)
        self._roles = RoleService(session)
        self._audit = AuditService(session)

    # --- login -----------------------------------------------------------

    def login(
        self,
        *,
        email: str,
        password: str,
        user_agent: str | None = None,
        ip: str | None = None,
    ) -> LoginResult:
        """Authenticate and issue a token pair (docs/PF §4).

        Rejects — with ONE generic message (no enumeration) — a missing/inactive
        user, a bad password, or a user with no ``user_roles`` in any society. The
        sole exception: a platform super-admin logs in even with no roles.
        """
        normalized = normalize_email(email)
        user = self._repo.find_user_by_email(normalized)

        # Missing/inactive user: run a dummy verify so the reject branch takes
        # comparable Argon2id time to a real password check (anti-enumeration by
        # timing — docs/PF §4). Then raise the identical generic error.
        if user is None or not user.is_active:
            verify_password(password, _DUMMY_HASH)
            raise AuthenticationError(_GENERIC_LOGIN_ERROR)

        if not verify_password(password, user.password_hash):
            raise AuthenticationError(_GENERIC_LOGIN_ERROR)

        active_society_id, role_ids = self._repo.active_society_and_role_ids(user.id)

        if not role_ids:
            # A role-less account cannot log in — UNLESS it is the platform
            # super-admin, whose authority is the is_platform_super_admin flag
            # (it holds no user_roles by design). It lands in the platform portal.
            if not user.is_platform_super_admin:
                # Equalize timing with the real-verify path (docs/PF §4).
                verify_password(password, _DUMMY_HASH)
                raise AuthenticationError(_GENERIC_LOGIN_ERROR)
            active_society_id = None
            available_portals = [_PLATFORM_PORTAL]
        else:
            available_portals = self._roles.available_portals(
                user.id, active_society_id
            )

        access_token, refresh_token = self._tokens.issue_pair(
            user,
            active_society_id=active_society_id,
            role_ids=role_ids,
            user_agent=user_agent,
            ip=ip,
        )
        self._repo.set_last_login(user, utcnow())

        # NOTE: login success/failure is NOT audited here — docs/PF §12 defers
        # login attempt logging (counts/security only, §15).
        return LoginResult(
            access_token=access_token,
            refresh_token=refresh_token,
            password_state=user.password_state,
            available_portals=available_portals,
        )

    # --- change password -------------------------------------------------

    def change_password(
        self, *, user: User, current_password: str, new_password: str
    ) -> None:
        """Change the caller's password (docs/PF §4).

        The only endpoint reachable while ``must_change``. Verifies the current
        password, enforces the policy, requires the new password to DIFFER from the
        current, flips ``password_state`` to ``active``, consumes any live temp
        reset, and revokes all sessions (forcing re-login). Audits
        ``user.password_changed``.
        """
        if not verify_password(current_password, user.password_hash):
            # Generic auth error — do not reveal which field was wrong.
            raise AuthenticationError("Current password is incorrect.")

        validate_password_policy(new_password)

        # New must differ from the current/temp/default (docs/PF §4).
        if verify_password(new_password, user.password_hash):
            raise ValidationError(
                "New password must be different from the current password.",
                details={"field": "new_password"},
            )

        self._repo.set_password(
            user, password_hash=hash_password(new_password), password_state="active"
        )

        # Consume any live temp-password rows so a leaked temp can't be reused.
        now = utcnow()
        for reset in self._repo.active_password_resets_for_user(user.id, now=now):
            reset.consumed_at = now
        self._session.flush()

        # Force re-login everywhere: the changed credential invalidates sessions.
        self._tokens.revoke_all_for_user(user.id)

        self._audit.record(
            action="user.password_changed",
            actor_user_id=user.id,
            society_id=None,
            entity_type="user",
            entity_id=user.id,
        )

    # --- forgot password -------------------------------------------------

    def forgot_password(self, *, email: str, sender: EmailSender) -> None:
        """Issue a temp password by email IF the address maps to a role-bearing
        user; otherwise do nothing (docs/PF §4).

        NEVER reveals whether the account exists — the router returns the same
        generic 200 regardless. No email is sent for an unknown/role-less address.
        """
        try:
            normalized = normalize_email(email)
        except ValidationError:
            # Malformed address: silently succeed (no enumeration, no email).
            self._equalize_forgot_timing()
            return

        user = self._repo.find_user_by_email(normalized)
        if user is None or not user.is_active:
            self._equalize_forgot_timing()
            return

        # Only role-bearing accounts (real society members) get a reset. The
        # super-admin recovers via the seed command, not forgot-password.
        _, role_ids = self._repo.active_society_and_role_ids(user.id)
        if not role_ids:
            self._equalize_forgot_timing()
            return

        temp_password = self._generate_temp_password()
        expires_at = utcnow() + timedelta(
            minutes=settings.password_reset_ttl_minutes
        )

        # Hash ONCE and reuse the same digest for both the reset row and the
        # user's credential — two hashes would waste a second Argon2 pass and
        # yield two salts for the same secret.
        temp_password_hash = hash_password(temp_password)

        self._repo.add_password_reset(
            PasswordReset(
                user_id=user.id,
                temp_password_hash=temp_password_hash,
                expires_at=expires_at,
            )
        )
        # The temp password IS the new login credential; force a change on use.
        self._repo.set_password(
            user,
            password_hash=temp_password_hash,
            password_state="must_change",
        )
        # Revoke live sessions so a prior attacker session can't outlive the reset.
        self._tokens.revoke_all_for_user(user.id)

        sender.send(
            EmailMessage(
                to=user.email,
                subject="Your temporary password",
                body=(
                    "A temporary password was requested for your account.\n\n"
                    f"Temporary password: {temp_password}\n\n"
                    "Log in with it and you will be prompted to set a new "
                    "password. It expires in "
                    f"{settings.password_reset_ttl_minutes} minutes."
                ),
            )
        )

    # --- helpers ---------------------------------------------------------

    @staticmethod
    def _equalize_forgot_timing() -> None:
        """Burn one Argon2 hash on the no-op path so an unknown/inactive/role-less
        address costs comparably to the real reset (which hashes + writes + mails),
        blunting the timing enumeration signal (docs/PF §4). One hash is enough."""
        hash_password("timing-equalizer-not-a-real-password")

    @staticmethod
    def _generate_temp_password() -> str:
        """A high-entropy temp password that satisfies the policy (>= min length,
        at least one letter and one digit). Never logged; only its hash is stored.
        """
        alphabet = string.ascii_letters + string.digits
        while True:
            candidate = "".join(secrets.choice(alphabet) for _ in range(16))
            if any(c.isalpha() for c in candidate) and any(
                c.isdigit() for c in candidate
            ):
                return candidate
