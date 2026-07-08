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

Design decisions (docs §4/§6 leave two edges to the implementer):
- **PATCH is rename + REACTIVATE only.** ``is_active=True`` reactivates a
  deactivated category; ``is_active=False`` is REJECTED (422) with a pointer to the
  DELETE route, so deactivation has a single choke-point (one audit action, one
  code path) and PATCH never doubles as a soft-delete. A reactivation whose name
  already belongs to another active category is a 409 (the partial unique index
  would reject it anyway).
- **DELETE is idempotent.** Deactivating an already-inactive category is a no-op
  (returns it unchanged, no second audit row) so the soft-delete is safe to retry;
  a deactivated name is immediately free for reuse (the partial unique index only
  covers active rows).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.common.errors import ConflictError, NotFoundError, ValidationError
from app.modules.complaints.models import ComplaintCategory
from app.modules.complaints.repository import ComplaintRepository
from app.modules.complaints.schemas import (
    CategoryCreateRequest,
    CategoryOut,
    CategoryUpdateRequest,
)
from app.modules.complaints.services.support import ensure_default_categories
from app.platform.audit.service import AuditService


class CategoriesService:
    def __init__(self, session: Session, repo: ComplaintRepository) -> None:
        self._session = session
        self._repo = repo

    def list_categories(self, society_id: int) -> list[CategoryOut]:
        """List ACTIVE categories (seeds system defaults on first access) (§6).

        This is the create-form list, so only ACTIVE categories are returned; a
        deactivated category stays attached to historical complaints but is never
        offered for a new one. Seeds the 6 system defaults lazily on first use
        (idempotent) so a fresh society sees the standard list.
        """
        ensure_default_categories(self._session, society_id, self._repo)
        return [
            CategoryOut.model_validate(c)
            for c in self._repo.list_categories(society_id, active_only=True)
        ]

    def create_category(
        self, society_id: int, req: CategoryCreateRequest, *, actor_user_id: int
    ) -> CategoryOut:
        """Create a category; active-name collision -> 409 (§4/§6).

        Seeds the system defaults first (idempotent) so a new custom category can't
        duplicate a not-yet-seeded system name. Only ACTIVE names collide — a name
        freed by an earlier deactivation may be reused (the partial unique index
        covers active rows only).
        """
        # Seed the system defaults on first use so a custom name can't collide with
        # a not-yet-seeded system category (matches finance's add_category).
        ensure_default_categories(self._session, society_id, self._repo)

        if self._repo.active_category_by_name(society_id, req.name) is not None:
            raise ConflictError(
                f"An active complaint category named '{req.name}' already exists."
            )

        category = self._repo.add_category(
            ComplaintCategory(
                society_id=society_id,
                name=req.name,
                is_active=True,
                is_system=False,
                created_by=actor_user_id,
            )
        )
        AuditService(self._session).record(
            action="complaint_category.created",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="complaint_category",
            entity_id=category.id,
            after={"name": category.name},
        )
        return CategoryOut.model_validate(category)

    def update_category(
        self,
        society_id: int,
        category_id: int,
        req: CategoryUpdateRequest,
        *,
        actor_user_id: int,
    ) -> CategoryOut:
        """Rename and/or reactivate; rename collision -> 409 (§4/§6).

        At least one of ``name`` / ``is_active`` must be provided (422 otherwise).
        A RENAME to a name held by ANOTHER active category is a 409. ``is_active``
        may only REACTIVATE (``True``); ``is_active=False`` is rejected (422) —
        deactivation goes through DELETE so it has a single audited choke-point. A
        reactivation whose name is already held by another active category is a 409.
        """
        if req.name is None and req.is_active is None:
            raise ValidationError(
                "Provide a new name and/or is_active=true to update a category."
            )
        if req.is_active is False:
            raise ValidationError(
                "Deactivate a category via DELETE /complaints/categories/{id}, "
                "not by setting is_active=false.",
                details={"category_id": category_id},
            )

        category = self._repo.get_category(society_id, category_id)
        if category is None:
            raise NotFoundError(
                f"Complaint category {category_id} was not found."
            )

        before_name = category.name
        reactivating = req.is_active is True and not category.is_active
        # The name the category will carry after this update (rename may combine
        # with a reactivate — check the target name against active peers once).
        target_name = req.name if req.name is not None else category.name
        renaming = req.name is not None and req.name != category.name

        # Collision guard: another ACTIVE category must not already hold the name
        # this category will carry once active (covers rename, reactivate, and a
        # combined rename+reactivate). The partial unique index is the backstop.
        if renaming or reactivating:
            clash = self._repo.active_category_by_name(society_id, target_name)
            if clash is not None and clash.id != category.id:
                raise ConflictError(
                    f"An active complaint category named '{target_name}' "
                    "already exists."
                )

        if renaming:
            category.name = req.name
        if reactivating:
            category.is_active = True
        self._session.flush()

        audit = AuditService(self._session)
        if renaming:
            audit.record(
                action="complaint_category.renamed",
                actor_user_id=actor_user_id,
                society_id=society_id,
                entity_type="complaint_category",
                entity_id=category.id,
                before={"name": before_name},
                after={"name": category.name},
            )
        if reactivating:
            audit.record(
                action="complaint_category.reactivated",
                actor_user_id=actor_user_id,
                society_id=society_id,
                entity_type="complaint_category",
                entity_id=category.id,
                after={"name": category.name, "is_active": True},
            )
        return CategoryOut.model_validate(category)

    def deactivate_category(
        self, society_id: int, category_id: int, *, actor_user_id: int
    ) -> CategoryOut:
        """Soft-deactivate a category (never hard-delete) (§4/§6).

        Flips ``is_active=False`` so the category stays attached to historical
        complaints but is hidden from new-complaint choices; its name frees up for
        reuse (the partial unique index covers active rows only). Idempotent:
        deactivating an already-inactive category is a no-op (no second audit row),
        so the soft-delete is safe to retry.
        """
        category = self._repo.get_category(society_id, category_id)
        if category is None:
            raise NotFoundError(
                f"Complaint category {category_id} was not found."
            )
        if not category.is_active:
            # Already deactivated — idempotent no-op (safe DELETE retry).
            return CategoryOut.model_validate(category)

        category.is_active = False
        self._session.flush()

        AuditService(self._session).record(
            action="complaint_category.deactivated",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="complaint_category",
            entity_id=category.id,
            after={"name": category.name, "is_active": False},
        )
        return CategoryOut.model_validate(category)
