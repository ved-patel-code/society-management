"""Auth routes (docs/PF §4/§10). Filled in by the auth feature agent (P4).

Endpoints to implement: POST /auth/login, /auth/refresh, /auth/logout,
/auth/change-password, /auth/forgot-password. Keep this router THIN — parse the
request, call the auth service, shape the response (docs/03 §2).
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["auth"])

# Endpoints added by P4.
