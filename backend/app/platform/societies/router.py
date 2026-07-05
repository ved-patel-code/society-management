"""Super-admin society + module-allocation routes (docs/PF §6/§10). Filled by P3.

Endpoints: POST/GET/PATCH /admin/societies, PUT /admin/societies/{id}/modules.
All gate on ``require_super_admin`` (core/deps.py). Keep the router thin.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/admin", tags=["admin:societies"])

# Endpoints added by P3.
