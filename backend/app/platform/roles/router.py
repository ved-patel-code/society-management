"""Super-admin role/permission routes (docs/PF §5/§10). Filled by P2.

Endpoints: POST /admin/societies/{id}/roles (accepts portal),
PUT /admin/roles/{id}/permissions. Gate on ``require_super_admin``. Thin router.
The effective-permission union + role-copy-on-society-creation live in P2's service.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/admin", tags=["admin:roles"])

# Endpoints added by P2.
