"""Read-only ``/me`` view assembly (docs/PF §6, §5.1).

Turns an authenticated :class:`AuthContext` + the requested (view-only) portal
into the shell-shaping payload the frontend needs: profile, active society,
available portals, the resolved active portal, that portal's enabled-and-visible
modules, its landing page, and permission hints.

Everything here is a **view hint** — never an authorization source. The two
request gates always use the account's full role set; ``active_portal`` is
client/view state (a query param), never a JWT claim (docs/PF §5.1, §4-auth).
No DB writes, no audit — this is a pure read (docs/PF §12).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.deps import AuthContext
from app.platform.models import Society
from app.platform.roles.service import RoleService

# Portal → landing page the shell opens on (docs/PF §5.1, §6):
#   resident → Notice Board (module key "notices"), admin → admin dashboard,
#   platform → super-admin console.
_PORTAL_LANDING: dict[str, str] = {
    "resident": "notices",
    "admin": "dashboard",
    "platform": "admin",
}


def _resolve_active_portal(
    requested: str | None, available: list[str]
) -> str | None:
    """Pick the view-only active portal (docs/PF §5.1).

    The requested ``?portal=`` wins only if it is one the user actually has;
    otherwise fall back to the sole portal, or ``None`` when there is none. The
    choice never affects authorization.
    """
    if requested is not None and requested in available:
        return requested
    if len(available) == 1:
        return available[0]
    return None


def build_me_view(
    auth: AuthContext, session: Session, *, requested_portal: str | None
) -> dict:
    """Assemble the ``/me`` payload for the caller.

    Super-admins operate above societies (flag-based, no society roles), so we
    never touch :class:`RoleService` for them — they get the fixed platform
    shell (docs/PF §5.1). Everyone else derives portals/modules/permissions from
    their roles in the active society.
    """
    user = auth.user
    profile = {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "phone": user.phone,
    }

    if auth.is_super_admin:
        return {
            "user": profile,
            "active_society_id": None,
            "available_portals": ["platform"],
            "active_portal": "platform",
            "modules": [],
            "landing": _PORTAL_LANDING["platform"],
            "permissions": [],
        }

    society_id = auth.active_society_id
    roles = RoleService(session)

    available_portals = roles.available_portals(user.id, society_id)
    active_portal = _resolve_active_portal(requested_portal, available_portals)

    modules: list[str] = []
    if active_portal is not None and society_id is not None:
        modules = sorted(
            roles.visible_modules_for_portal(user.id, society_id, active_portal)
        )

    landing = (
        _PORTAL_LANDING.get(active_portal) if active_portal is not None else None
    )
    permissions = sorted(roles.effective_permission_keys(user.id, society_id))

    return {
        "user": profile,
        "active_society_id": society_id,
        "available_portals": available_portals,
        "active_portal": active_portal,
        "modules": modules,
        "landing": landing,
        "permissions": permissions,
        # Blocking wizard (Onboarding module §4): while the active society is still
        # in 'onboarding' status the client locks the shell to the onboarding
        # wizard. A pure view hint — authorization is unaffected (the gates stand).
        "onboarding_required": _is_onboarding_required(session, society_id),
    }


def _is_onboarding_required(session: Session, society_id: int | None) -> bool:
    """True when the caller's active society still needs its structure mapped.

    Signals the frontend to open the onboarding wizard and nothing else (docs
    onboarding §4). Read-only; never affects the two request gates.
    """
    if society_id is None:
        return False
    status = session.get(Society, society_id)
    return status is not None and status.status == "onboarding"
