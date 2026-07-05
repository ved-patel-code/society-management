"""DB access for auth (docs/PF §4, docs/03 §2/§4).

All auth SQL lives here — the service owns logic, this layer owns queries. Reads
select narrowly and every write flushes (never commits — ``get_session`` commits
once at request end). No business decisions are made here.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.models import PasswordReset, RefreshToken, User, UserRole


class AuthRepository:
    """Thin query layer over the auth tables (users, refresh_tokens, password_resets)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # --- users -----------------------------------------------------------

    def get_user(self, user_id: int) -> User | None:
        return self._session.get(User, user_id)

    def find_user_by_email(self, email: str) -> User | None:
        """Look a user up by (case-insensitive CITEXT) email. ``email`` must be
        pre-normalized by the caller."""
        return self._session.execute(
            select(User).where(User.email == email)
        ).scalar_one_or_none()

    def active_society_and_role_ids(
        self, user_id: int
    ) -> tuple[int | None, list[int]]:
        """The user's active society + the role ids they hold there (docs/PF §4).

        v1 rule: one society per user. If the user has ``user_roles`` rows across
        (defensively) more than one society, the lowest ``society_id`` is chosen
        deterministically and only that society's roles are returned.
        """
        rows = self._session.execute(
            select(UserRole.society_id, UserRole.role_id).where(
                UserRole.user_id == user_id
            )
        ).all()
        if not rows:
            return None, []
        active_society_id = min(society_id for society_id, _ in rows)
        role_ids = [
            role_id for society_id, role_id in rows if society_id == active_society_id
        ]
        return active_society_id, role_ids

    def set_last_login(self, user: User, when: datetime) -> None:
        user.last_login_at = when
        self._session.flush()

    def set_password(self, user: User, *, password_hash: str, password_state: str) -> None:
        user.password_hash = password_hash
        user.password_state = password_state
        self._session.flush()

    # --- refresh tokens --------------------------------------------------

    def add_refresh_token(self, token: RefreshToken) -> RefreshToken:
        self._session.add(token)
        self._session.flush()  # assign PK within the txn (no commit)
        return token

    def find_refresh_token_by_hash(self, token_hash: str) -> RefreshToken | None:
        return self._session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        ).scalar_one_or_none()

    def active_refresh_tokens_for_user(self, user_id: int) -> list[RefreshToken]:
        """Every not-yet-revoked refresh token row for a user (revocation sweep)."""
        return list(
            self._session.execute(
                select(RefreshToken).where(
                    RefreshToken.user_id == user_id,
                    RefreshToken.revoked_at.is_(None),
                )
            ).scalars()
        )

    # --- password resets -------------------------------------------------

    def add_password_reset(self, reset: PasswordReset) -> PasswordReset:
        self._session.add(reset)
        self._session.flush()
        return reset

    def active_password_resets_for_user(
        self, user_id: int, *, now: datetime
    ) -> list[PasswordReset]:
        """Unconsumed, unexpired temp-password rows for a user."""
        return list(
            self._session.execute(
                select(PasswordReset).where(
                    PasswordReset.user_id == user_id,
                    PasswordReset.consumed_at.is_(None),
                    PasswordReset.expires_at > now,
                )
            ).scalars()
        )
