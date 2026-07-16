"""Auth routes (docs/PF §4/§10). Thin: parse the request, call the service,
shape the response (docs/03 §2). All logic lives in :class:`AuthService` /
:class:`TokenService`; typed errors surface through the central handler.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.deps import AuthContext, get_auth_context
from app.core.email import EmailSender, get_email_sender
from app.platform.auth.schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    MessageResponse,
    RefreshRequest,
    TokenPairResponse,
)
from app.platform.auth.service import AuthService
from app.platform.auth.token_service import TokenService

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _user_agent(request: Request) -> str | None:
    ua = request.headers.get("user-agent")
    return ua[:255] if ua else None


@router.post("/login", response_model=LoginResponse)
def login(
    body: LoginRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> LoginResponse:
    """Authenticate; return access+refresh tokens, password_state, and the
    view-only ``available_portals`` (docs/PF §4/§5.1)."""
    result = AuthService(session).login(
        email=body.email,
        password=body.password,
        user_agent=_user_agent(request),
        ip=_client_ip(request),
    )
    return LoginResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        password_state=result.password_state,
        available_portals=result.available_portals,
    )


@router.post("/token", response_model=LoginResponse, include_in_schema=False)
def login_via_oauth2_form(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(get_session),
) -> LoginResponse:
    """Swagger-only alias for :func:`login`. Lets the "Authorize" dialog's
    OAuth2 password form (email in ``username``) issue a token, so it can be
    pasted straight into requests. Not part of the public API — the real
    client contract is the JSON ``POST /auth/login`` above."""
    result = AuthService(session).login(
        email=form.username,
        password=form.password,
        user_agent=_user_agent(request),
        ip=_client_ip(request),
    )
    return LoginResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        password_state=result.password_state,
        available_portals=result.available_portals,
    )


@router.post("/refresh", response_model=TokenPairResponse)
def refresh(
    body: RefreshRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> TokenPairResponse:
    """Rotate a refresh token → a fresh pair (rotation on every use; reuse of a
    revoked token is treated as theft — docs/PF §14.5)."""
    access_token, refresh_token = TokenService(session).rotate(
        body.refresh_token,
        user_agent=_user_agent(request),
        ip=_client_ip(request),
    )
    return TokenPairResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/logout", response_model=MessageResponse)
def logout(
    body: LogoutRequest,
    session: Session = Depends(get_session),
) -> MessageResponse:
    """Revoke the presented refresh token (this session). Idempotent."""
    TokenService(session).revoke_one(body.refresh_token)
    return MessageResponse(message="Logged out.")


@router.post("/change-password", response_model=MessageResponse)
def change_password(
    body: ChangePasswordRequest,
    auth: AuthContext = Depends(get_auth_context),
    session: Session = Depends(get_session),
) -> MessageResponse:
    """Change the caller's password. The ONLY endpoint reachable while
    ``must_change`` (the lockout is enforced in core/deps.py). Flips the state to
    ``active`` and revokes all sessions (docs/PF §4)."""
    AuthService(session).change_password(
        user=auth.user,
        current_password=body.current_password,
        new_password=body.new_password,
    )
    return MessageResponse(message="Password changed. Please log in again.")


@router.post("/forgot-password", response_model=MessageResponse)
def forgot_password(
    body: ForgotPasswordRequest,
    session: Session = Depends(get_session),
    sender: EmailSender = Depends(get_email_sender),
) -> MessageResponse:
    """Request a temporary password. Always returns the SAME generic response so
    account existence is never revealed (docs/PF §4); mail is sent only for a
    real, role-bearing account."""
    AuthService(session).forgot_password(email=body.email, sender=sender)
    return MessageResponse(
        message=(
            "If an account exists for that email, a temporary password has been "
            "sent."
        )
    )
