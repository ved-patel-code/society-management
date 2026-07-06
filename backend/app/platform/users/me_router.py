"""Current-user (/me) route (docs/PF §6/§5.1). Filled by P6.

GET /me?portal= → profile + active society + available_portals + active portal +
that portal's visible modules + landing + permission hints. View hint only; authz
still uses the full role set. ``active_portal`` is never a JWT claim.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.core.deps import AuthContext, get_auth_context
from app.platform.users.me_service import build_me_view

router = APIRouter(tags=["me"])


class MeUser(BaseModel):
    """The caller's profile subset (never exposes the password hash)."""

    id: int
    email: str
    full_name: str | None
    phone: str | None


class MeResponse(BaseModel):
    """Shell-shaping view for the authenticated caller (docs/PF §6/§5.1).

    All fields are **view hints**. ``active_portal`` is the view-only portal
    resolved from the ``?portal=`` query param; it never affects authorization,
    which always uses the account's full role set via the two request gates.
    """

    user: MeUser
    active_society_id: int | None
    available_portals: list[str]
    active_portal: str | None
    modules: list[str]
    landing: str | None
    permissions: list[str]
    # Blocking-wizard hint (Onboarding module §4): true while the active society is
    # still in 'onboarding' status → the client locks the shell to the wizard.
    onboarding_required: bool = False


@router.get("/me", response_model=MeResponse)
def get_me(
    portal: str | None = Query(
        default=None,
        max_length=16,
        description=(
            "View-only portal selection (client/view state). Applied only if it "
            "is one of the caller's available_portals; never affects authZ."
        ),
    ),
    auth: AuthContext = Depends(get_auth_context),
    session: Session = Depends(get_session),
) -> MeResponse:
    """Return the caller's profile + active society + portal-scoped shell view.

    Read-only: no DB writes, no audit (docs/PF §12). Super-admins get the fixed
    platform shell; everyone else derives portals/modules/permissions from their
    roles in the active society (docs/PF §5.1).
    """
    view = build_me_view(auth, session, requested_portal=portal)
    return MeResponse.model_validate(view)
