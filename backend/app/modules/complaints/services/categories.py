"""Complaints categories concern — WAVE A (docs/modules/complaints.md §4/§6).

Owns: list active categories (lazy-seeds the 6 system defaults on first use),
create (active-name collision -> 409), rename / reactivate (rename must not
collide with another ACTIVE name), soft-deactivate (never hard-delete). Audits
``complaint_category.created`` / ``renamed`` / ``deactivated``.

FROZEN STUBS: methods below raise ``NotImplementedError`` so the routes are live +
gated at the green gate but cannot silently pass a test. Wave A fills the bodies.
It may add private helpers to THIS file only; it must not touch any other file
(the shared helpers it needs — ``ensure_default_categories`` — already live in
``support.py``; the repository queries already exist).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.complaints.repository import ComplaintRepository
from app.modules.complaints.schemas import (
    CategoryCreateRequest,
    CategoryOut,
    CategoryUpdateRequest,
)


class CategoriesService:
    def __init__(self, session: Session, repo: ComplaintRepository) -> None:
        self._session = session
        self._repo = repo

    def list_categories(self, society_id: int) -> list[CategoryOut]:
        """List ACTIVE categories (seeds system defaults on first access) (§6)."""
        raise NotImplementedError("Wave A: CategoriesService.list_categories")

    def create_category(
        self, society_id: int, req: CategoryCreateRequest, *, actor_user_id: int
    ) -> CategoryOut:
        """Create a category; active-name collision -> 409 (§4/§6)."""
        raise NotImplementedError("Wave A: CategoriesService.create_category")

    def update_category(
        self,
        society_id: int,
        category_id: int,
        req: CategoryUpdateRequest,
        *,
        actor_user_id: int,
    ) -> CategoryOut:
        """Rename and/or reactivate; rename collision -> 409 (§4/§6)."""
        raise NotImplementedError("Wave A: CategoriesService.update_category")

    def deactivate_category(
        self, society_id: int, category_id: int, *, actor_user_id: int
    ) -> CategoryOut:
        """Soft-deactivate a category (never hard-delete) (§4/§6)."""
        raise NotImplementedError("Wave A: CategoriesService.deactivate_category")
