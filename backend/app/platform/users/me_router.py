"""Current-user (/me) route (docs/PF §6/§5.1). Filled by P6.

GET /me?portal= → profile + active society + available_portals + active portal +
that portal's visible modules + landing + permission hints. View hint only; authz
still uses the full role set. ``active_portal`` is never a JWT claim.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["me"])

# GET /me added by P6.
