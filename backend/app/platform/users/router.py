"""Super-admin user management routes (docs/PF §8/§10). Filled by P5.

Endpoints: POST /admin/societies/{id}/users, PATCH /admin/users/{id} (deactivate),
POST /admin/users/{id}/roles. Gate on ``require_super_admin``. Thin router.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/admin", tags=["admin:users"])

# Endpoints added by P5.
