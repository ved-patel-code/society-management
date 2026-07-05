"""Security primitives: password hashing, JWT access tokens, refresh tokens.

- Passwords: Argon2id via passlib (docs/PF §4). Never store plaintext.
- Access token: PyJWT HS256, short-lived (docs/PF §4). Decoding ALWAYS pins
  ``algorithms=[settings.jwt_algorithm]`` — never leaves it unspecified — which
  closes the algorithm-confusion class (CVE-2026-48526 / CVE-2026-48523; also
  fixed by pinning PyJWT>=2.13.0).
- Refresh token: a high-entropy random string handed to the client; only its
  SHA-256 hash is stored in ``refresh_tokens`` (docs/PF §3/§4). Rotated on use.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Any

import jwt
from passlib.context import CryptContext

from app.common.time import utcnow
from app.core.config import settings

# Argon2id is passlib's default variant for the "argon2" scheme.
_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


# --- Passwords -------------------------------------------------------------

def hash_password(plain: str) -> str:
    """Return an Argon2id hash of ``plain``."""
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time check of ``plain`` against an Argon2id ``hashed`` value."""
    return _pwd_context.verify(plain, hashed)


# --- Access tokens (JWT) ---------------------------------------------------

def create_access_token(
    *,
    user_id: int,
    active_society_id: int | None,
    role_ids: list[int],
    password_state: str,
) -> str:
    """Build a signed short-lived access token (docs/PF §4).

    ``active_portal`` is deliberately NOT a claim — it is view-only client state.
    """
    now = utcnow()
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "user_id": user_id,
        "active_society_id": active_society_id,
        "role_ids": role_ids,
        "password_state": password_state,
        "iat": int(now.timestamp()),
        "exp": int(
            (now + timedelta(minutes=settings.access_token_ttl_minutes)).timestamp()
        ),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode + verify an access token. Pins the algorithm (never 'none').

    Raises ``jwt.PyJWTError`` (or a subclass) on any invalid/expired/tampered token.
    """
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
    )


# --- Refresh tokens --------------------------------------------------------

def generate_refresh_token() -> str:
    """A high-entropy opaque refresh token (given to the client, never stored raw)."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(raw: str) -> str:
    """Deterministic SHA-256 hash for storage + lookup in ``refresh_tokens``.

    (Deterministic — unlike the password hash — because we must look the token up
    by value; it is already high-entropy so a fast hash is appropriate here.)
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
