"""Pydantic request/response models for the auth endpoints (docs/PF §4/§10).

Kept intentionally thin: shape validation only. Business rules (password policy,
enumeration-safety, rotation) live in the service/security layers, not here.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


# --- login -----------------------------------------------------------------


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    password_state: str
    available_portals: list[str]


# --- refresh ---------------------------------------------------------------


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


# --- logout ----------------------------------------------------------------


class LogoutRequest(BaseModel):
    refresh_token: str


# --- change password -------------------------------------------------------


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=1)


# --- forgot password -------------------------------------------------------


class ForgotPasswordRequest(BaseModel):
    email: str


class MessageResponse(BaseModel):
    """Generic acknowledgement (enumeration-safe — same shape regardless of
    whether the email/account exists)."""

    message: str
