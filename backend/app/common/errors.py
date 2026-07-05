"""Typed domain errors + a single consistent error shape.

Services raise ``DomainError`` subclasses; a central FastAPI handler maps them
to ``{code, message, details}`` responses (docs/03 §6). Routers and repositories
never build HTTP responses themselves.
"""
from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Base class for all business-rule errors.

    ``status_code`` maps to HTTP; ``code`` is a stable machine-readable slug.
    """

    status_code: int = 400
    code: str = "bad_request"

    def __init__(
        self, message: str, *, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class ValidationError(DomainError):
    status_code = 422
    code = "validation_error"


class AuthenticationError(DomainError):
    """Credentials missing/invalid. Deliberately generic to avoid enumeration."""

    status_code = 401
    code = "authentication_error"


class PermissionDeniedError(DomainError):
    status_code = 403
    code = "permission_denied"


class NotFoundError(DomainError):
    status_code = 404
    code = "not_found"


class ConflictError(DomainError):
    """Uniqueness / state conflicts (e.g. duplicate role, one-society-per-user)."""

    status_code = 409
    code = "conflict"


class ModuleDisabledError(DomainError):
    """The society does not have the required module enabled."""

    status_code = 403
    code = "module_disabled"


class DependencyError(DomainError):
    """A module cannot be enabled because a dependency is missing (depends_on)."""

    status_code = 409
    code = "dependency_error"
