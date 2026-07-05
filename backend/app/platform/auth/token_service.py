"""TokenService — the access+refresh token lifecycle (docs/PF §4, §14.5).

Owns issuing token pairs, rotating refresh tokens on every use, detecting
reuse-of-a-rotated-token as theft, and bulk/single revocation. Only the SHA-256
HASH of a refresh token is ever stored; the raw value is handed to the client and
never persisted or logged (docs/PF §3/§4).

Rotation & theft (docs/PF §14.5, resolved decision 5):
- Every ``rotate`` revokes the presented token and issues a new one, linking the
  chain via ``replaced_by_id``.
- Presenting a token that is ALREADY revoked (or that no longer exists) is the
  theft signal: an attacker replaying a rotated-away token. We revoke the WHOLE
  chain (every active token for that user) and raise ``AuthenticationError``.

No commit here — the request-scoped session commits once at the end (docs/PF §12).
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from app.common.errors import AuthenticationError
from app.common.time import utcnow
from app.core.config import settings
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
)
from app.platform.auth.repository import AuthRepository
from app.platform.models import RefreshToken, User


class TokenService:
    """Issue, rotate, and revoke access/refresh tokens (wave-2 contract)."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = AuthRepository(session)

    # --- issue -----------------------------------------------------------

    def issue_pair(
        self,
        user: User,
        *,
        active_society_id: int | None,
        role_ids: list[int],
        user_agent: str | None = None,
        ip: str | None = None,
    ) -> tuple[str, str]:
        """Create an access JWT + a persisted (hashed) refresh token.

        Returns ``(access_token, raw_refresh_token)``. The raw refresh token is
        returned to the caller and never stored — only its hash lands in the DB.
        """
        access_token = create_access_token(
            user_id=user.id,
            active_society_id=active_society_id,
            role_ids=role_ids,
            password_state=user.password_state,
        )
        raw_refresh = self._create_refresh_row(user.id, user_agent=user_agent, ip=ip)[0]
        return access_token, raw_refresh

    # --- rotate ----------------------------------------------------------

    def rotate(
        self,
        raw_refresh_token: str,
        *,
        user_agent: str | None = None,
        ip: str | None = None,
    ) -> tuple[str, str]:
        """Rotate a refresh token on use (docs/PF §14.5).

        - Unknown or already-revoked token → THEFT: revoke the user's whole chain
          (when known) and raise ``AuthenticationError``.
        - Expired token → reject (revoke it, raise).
        - Valid + unexpired → revoke it, mint a new pair, and link the new refresh
          row via ``replaced_by_id``. Returns ``(access, raw_refresh)``.
        """
        token_hash = hash_refresh_token(raw_refresh_token)
        current = self._repo.find_refresh_token_by_hash(token_hash)

        # Unknown token: nothing to link; deny generically (no enumeration).
        if current is None:
            raise AuthenticationError("Invalid or expired session.")

        now = utcnow()

        # Reuse of an already-revoked (rotated-away) token == theft signal.
        # The chain revocation MUST survive the AuthenticationError we raise —
        # but ``get_session`` rolls back on any exception. So we commit this one
        # security side effect explicitly before raising (the sole, deliberate
        # exception to "services never commit"; docs/PF §14.5).
        if current.revoked_at is not None:
            self.revoke_all_for_user(current.user_id)
            self._session.commit()
            raise AuthenticationError("Invalid or expired session.")

        if current.expires_at <= now:
            current.revoked_at = now
            self._session.flush()
            raise AuthenticationError("Invalid or expired session.")

        user = self._repo.get_user(current.user_id)
        if user is None or not user.is_active:
            current.revoked_at = now
            self._session.flush()
            raise AuthenticationError("Invalid or expired session.")

        # Resolve current society + roles fresh so a rotated token reflects any
        # role changes since issue (the access claims must stay accurate).
        active_society_id, role_ids = self._repo.active_society_and_role_ids(user.id)

        # Mint the replacement refresh row, then revoke + link the old one.
        raw_refresh, new_row = self._create_refresh_row(
            user.id, user_agent=user_agent, ip=ip
        )
        current.revoked_at = now
        current.replaced_by_id = new_row.id
        self._session.flush()

        access_token = create_access_token(
            user_id=user.id,
            active_society_id=active_society_id,
            role_ids=role_ids,
            password_state=user.password_state,
        )
        return access_token, raw_refresh

    # --- revoke ----------------------------------------------------------

    def revoke_all_for_user(self, user_id: int) -> int:
        """Revoke every active refresh token for a user (logout-all / deactivate /
        role removal / theft). Returns the count revoked (docs/PF §4)."""
        now = utcnow()
        tokens = self._repo.active_refresh_tokens_for_user(user_id)
        for token in tokens:
            token.revoked_at = now
        if tokens:
            self._session.flush()
        return len(tokens)

    def revoke_one(self, raw_refresh_token: str) -> None:
        """Revoke a single refresh token (logout this session). Idempotent and
        silent: an unknown/already-revoked token is a no-op (no enumeration)."""
        token_hash = hash_refresh_token(raw_refresh_token)
        token = self._repo.find_refresh_token_by_hash(token_hash)
        if token is None or token.revoked_at is not None:
            return
        token.revoked_at = utcnow()
        self._session.flush()

    # --- helpers ---------------------------------------------------------

    def _create_refresh_row(
        self, user_id: int, *, user_agent: str | None, ip: str | None
    ) -> tuple[str, RefreshToken]:
        """Generate a raw refresh token and persist only its hash. Returns
        ``(raw_token, row)``."""
        raw = generate_refresh_token()
        expires_at = utcnow() + timedelta(days=settings.refresh_token_ttl_days)
        row = self._repo.add_refresh_token(
            RefreshToken(
                user_id=user_id,
                token_hash=hash_refresh_token(raw),
                expires_at=expires_at,
                user_agent=(user_agent or None),
                ip=(ip or None),
            )
        )
        return raw, row
