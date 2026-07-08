"""Complaints CRUD concern — WAVE B (docs/modules/complaints.md §4/§6).

Owns: raise, edit-while-open, withdraw, list (with resident vs read_all
visibility scoping), and detail (timeline + images + clear-on-read). The RAISE
flow resolves the caller's owned house (``support.current_owned_houses`` via
HouseService — one -> infer; several -> require + verify ``house_id`` else
422/403), validates the category is active, allocates the reference
(``repo.allocate_reference``), inserts, writes the initial ``NULL -> open`` history
(``support.record_initial``), and emits ``complaint.created``. WITHDRAW is the
resident-only status edge (``open -> withdrawn``) and MUST use
``support.record_transition`` so the timeline shape stays uniform, then emits
``complaint.withdrawn``. DETAIL calls ``events.mark_read_for`` (clear-on-read).

FROZEN STUBS: methods raise ``NotImplementedError``. Wave B fills the bodies,
editing only THIS file + its own test file.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.common.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.modules.complaints import events
from app.modules.complaints.models import Complaint
from app.modules.complaints.repository import ComplaintRepository
from app.modules.complaints.schemas import (
    KIND_PROOF,
    KIND_REPORT,
    STATUS_OPEN,
    STATUS_WITHDRAWN,
    ComplaintCreateRequest,
    ComplaintDetailOut,
    ComplaintImageOut,
    ComplaintListItemOut,
    ComplaintListOut,
    ComplaintUpdateRequest,
    StatusHistoryOut,
)
from app.modules.complaints.services import support
from app.platform.audit.service import AuditService


class ComplaintsCrudService:
    def __init__(self, session: Session, repo: ComplaintRepository) -> None:
        self._session = session
        self._repo = repo

    # --- house resolution (via the House service interface, never tables) ---

    def _house_service(self):
        """Lazily import HouseService to avoid a module-load import cycle
        (complaints -> houses -> vault -> ...); mirrors finance's inline import."""
        from app.modules.houses.service import HouseService

        return HouseService(self._session)

    def raise_complaint(
        self,
        society_id: int,
        req: ComplaintCreateRequest,
        *,
        actor_user_id: int,
    ) -> ComplaintDetailOut:
        """Raise a complaint tied to the caller's owned house (§4/§6).

        Report images are attached in a follow-up call (Wave D) or by the router
        after create; this returns the created complaint's detail.
        """
        house_id = self._resolve_raiser_house(society_id, actor_user_id, req.house_id)

        # The category must exist in this society AND be active (docs §4): missing
        # is a 404 (bad reference), inactive is a 422 (unchoosable for new ones).
        category = self._repo.get_category(society_id, req.category_id)
        if category is None:
            raise NotFoundError(
                "Category not found.", details={"category_id": req.category_id}
            )
        if not category.is_active:
            raise ValidationError(
                "Category is not active; choose an active category.",
                details={"category_id": req.category_id},
            )

        # Reference allocation holds the per-society counter lock until commit.
        reference = self._repo.allocate_reference(society_id)
        complaint = self._repo.add_complaint(
            Complaint(
                society_id=society_id,
                reference=reference,
                house_id=house_id,
                raised_by=actor_user_id,
                category_id=req.category_id,
                title=req.title,
                description=req.description,
                status=STATUS_OPEN,
            )
        )
        # The initial NULL -> open timeline row (docs §4), attributed to the raiser.
        support.record_initial(self._repo, complaint, changed_by=actor_user_id)

        AuditService(self._session).record(
            action="complaint.created",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="complaint",
            entity_id=complaint.id,
            after={
                "reference": reference,
                "house_id": house_id,
                "category_id": req.category_id,
            },
        )
        events.emit_created(
            {
                "complaint_id": complaint.id,
                "house_id": house_id,
                "raised_by": actor_user_id,
                "reference": reference,
                "category_id": req.category_id,
            }
        )
        return self._detail(complaint)

    def _resolve_raiser_house(
        self, society_id: int, actor_user_id: int, requested_house_id: int | None
    ) -> int:
        """Resolve the raiser's owned house (docs §4).

        The caller must own at least one current house (else 403). With exactly
        one, it is inferred when ``house_id`` is omitted; a named ``house_id`` must
        be one the caller owns (else 403). Owning several with no ``house_id`` is
        ambiguous → 422 (the request must name the house).
        """
        owned = self._house_service().current_owned_houses(society_id, actor_user_id)
        if not owned:
            raise PermissionDeniedError(
                "Only a current house owner may raise a complaint."
            )
        owned_ids = {h.id for h in owned}

        if requested_house_id is not None:
            if requested_house_id not in owned_ids:
                raise PermissionDeniedError(
                    "You do not own the named house.",
                    details={"house_id": requested_house_id},
                )
            return requested_house_id

        if len(owned_ids) > 1:
            raise ValidationError(
                "You own several houses; specify house_id.",
                details={"owned_house_ids": sorted(owned_ids)},
            )
        return next(iter(owned_ids))

    def edit_complaint(
        self,
        society_id: int,
        complaint_id: int,
        req: ComplaintUpdateRequest,
        *,
        actor_user_id: int,
    ) -> ComplaintDetailOut:
        """Edit an open complaint (raiser-only, while ``open``) (§4/§6).

        Editable: title/description/category_id; a new category must be ACTIVE.
        """
        complaint = self._require_complaint(society_id, complaint_id)

        # Raiser-only (docs §4). Not the raiser -> 403 (even for an admin who does
        # not own it; editing a resident's complaint is not an admin action).
        if complaint.raised_by != actor_user_id:
            raise PermissionDeniedError(
                "Only the raiser may edit this complaint."
            )
        # Locked once it leaves ``open`` (docs §4).
        if complaint.status != STATUS_OPEN:
            raise ConflictError(
                "This complaint is locked once it is in progress.",
                details={"status": complaint.status},
            )

        # At least one field must change (docs §6).
        if (
            req.title is None
            and req.description is None
            and req.category_id is None
        ):
            raise ValidationError("Provide at least one field to edit.")

        before: dict = {}
        after: dict = {}

        if req.category_id is not None and req.category_id != complaint.category_id:
            category = self._repo.get_category(society_id, req.category_id)
            if category is None:
                raise NotFoundError(
                    "Category not found.",
                    details={"category_id": req.category_id},
                )
            if not category.is_active:
                raise ValidationError(
                    "Category is not active; choose an active category.",
                    details={"category_id": req.category_id},
                )
            before["category_id"] = complaint.category_id
            after["category_id"] = req.category_id
            complaint.category_id = req.category_id

        if req.title is not None and req.title != complaint.title:
            before["title"] = complaint.title
            after["title"] = req.title
            complaint.title = req.title

        if req.description is not None and req.description != complaint.description:
            before["description"] = complaint.description
            after["description"] = req.description
            complaint.description = req.description

        # Only audit/flush when something actually changed (a no-op edit that
        # re-sends the same values is accepted but writes nothing new).
        if after:
            self._session.flush()
            AuditService(self._session).record(
                action="complaint.updated",
                actor_user_id=actor_user_id,
                society_id=society_id,
                entity_type="complaint",
                entity_id=complaint.id,
                before=before,
                after=after,
            )
        return self._detail(complaint)

    def withdraw_complaint(
        self, society_id: int, complaint_id: int, *, actor_user_id: int
    ) -> ComplaintDetailOut:
        """Withdraw an open complaint (raiser-only, while ``open``) (§4/§6)."""
        complaint = self._require_complaint(society_id, complaint_id)

        if complaint.raised_by != actor_user_id:
            raise PermissionDeniedError(
                "Only the raiser may withdraw this complaint."
            )
        # Only while open (docs §4); any other state -> 409.
        if complaint.status != STATUS_OPEN:
            raise ConflictError(
                "Only an open complaint can be withdrawn.",
                details={"status": complaint.status},
            )

        # Route the status edge through the single choke-point so the timeline +
        # ``withdrawn_at`` stamping stay uniform with every other transition.
        support.record_transition(
            self._repo,
            complaint,
            to_status=STATUS_WITHDRAWN,
            note=None,
            changed_by=actor_user_id,
        )
        self._session.flush()

        AuditService(self._session).record(
            action="complaint.withdrawn",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="complaint",
            entity_id=complaint.id,
        )
        events.emit_withdrawn(
            {
                "complaint_id": complaint.id,
                "house_id": complaint.house_id,
                "raised_by": complaint.raised_by,
                "reference": complaint.reference,
            }
        )
        return self._detail(complaint)

    def list_complaints(
        self,
        society_id: int,
        *,
        caller_user_id: int,
        can_read_all: bool,
        status: str | None,
        category_id: int | None,
        house_id: int | None,
        date_from: date | None,
        date_to: date | None,
        q: str | None,
        offset: int,
        limit: int,
    ) -> ComplaintListOut:
        """List complaints: resident -> own house(s); read_all -> all (§4/§6).

        When ``can_read_all`` is False the service passes the caller's owned
        houses as the repository visibility allow-list, so a resident can never
        see another house's complaints (enforced in the repository query).
        """
        # Visibility allow-list: None => whole society (read_all); the caller's own
        # houses otherwise. An empty list means the caller owns nothing -> the
        # repository returns no rows (never "all").
        if can_read_all:
            house_ids: list[int] | None = None
        else:
            house_ids = [
                h.id
                for h in self._house_service().current_owned_houses(
                    society_id, caller_user_id
                )
            ]

        rows, total = self._repo.list_complaints(
            society_id,
            house_ids=house_ids,
            status=status,
            category_id=category_id,
            house_id=house_id,
            date_from=date_from,
            date_to=date_to,
            q=q,
            offset=offset,
            limit=limit,
        )

        # Batch the label/count lookups for the page (no N+1, docs §6).
        category_map = self._repo.categories_by_ids({r.category_id for r in rows})
        image_counts = self._repo.image_counts_for([r.id for r in rows])
        house_svc = self._house_service()
        # One display-code resolve per UNIQUE house on the page.
        display_codes = {
            hid: house_svc.house_display_code(society_id, hid)
            for hid in {r.house_id for r in rows}
        }

        items = [
            ComplaintListItemOut(
                id=r.id,
                reference=r.reference,
                title=r.title,
                status=r.status,
                category_id=r.category_id,
                category_name=(
                    category_map[r.category_id].name
                    if r.category_id in category_map
                    else ""
                ),
                house_id=r.house_id,
                house_display_code=display_codes.get(r.house_id),
                report_image_count=image_counts.get(r.id, {}).get(KIND_REPORT, 0),
                proof_image_count=image_counts.get(r.id, {}).get(KIND_PROOF, 0),
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]
        return ComplaintListOut(items=items, total=total)

    def get_detail(
        self,
        society_id: int,
        complaint_id: int,
        *,
        caller_user_id: int,
        can_read_all: bool,
    ) -> ComplaintDetailOut:
        """Complaint detail + timeline + images; clears the caller's alert (§6).

        Enforces visibility (resident may only open a complaint on a house they
        own). Calls ``events.mark_read_for(caller, 'complaint', id)``.
        """
        complaint = self._require_complaint(society_id, complaint_id)

        # Visibility (docs §4): a resident may only open a complaint on a house
        # they currently own — never another house's. read_all sees everything.
        if not can_read_all:
            owned_ids = {
                h.id
                for h in self._house_service().current_owned_houses(
                    society_id, caller_user_id
                )
            }
            if complaint.house_id not in owned_ids:
                raise PermissionDeniedError(
                    "You may only view complaints on a house you own."
                )

        detail = self._detail(complaint)
        # Clear-on-read (docs §6/§7): drop the caller's pending alert for this
        # complaint (no-op until Notifications subscribes).
        events.mark_read_for(caller_user_id, "complaint", complaint_id)
        return detail

    # --- helpers -----------------------------------------------------------

    def _require_complaint(self, society_id: int, complaint_id: int) -> Complaint:
        complaint = self._repo.get_complaint(society_id, complaint_id)
        if complaint is None:
            raise NotFoundError(
                "Complaint not found.", details={"complaint_id": complaint_id}
            )
        return complaint

    def _detail(self, complaint: Complaint) -> ComplaintDetailOut:
        """Assemble a :class:`ComplaintDetailOut` for one complaint (docs §6).

        Reused by raise/edit/withdraw/get_detail. Efficient by construction: one
        category read, one history read, one image read; each image's preview URL
        is fetched from Vault (falling back to ``None`` if Vault is unavailable —
        e.g. the module is disabled — so detail stays robust). No N+1 across
        complaints since callers hold a single row.
        """
        society_id = complaint.society_id

        category = self._repo.get_category(society_id, complaint.category_id)
        category_name = category.name if category is not None else ""
        house_display_code = self._house_service().house_display_code(
            society_id, complaint.house_id
        )

        timeline = [
            StatusHistoryOut.model_validate(h)
            for h in self._repo.list_status_history(complaint.id)
        ]

        images: list[ComplaintImageOut] = []
        for img in self._repo.list_images(complaint.id):
            out = ComplaintImageOut.model_validate(img)
            out.preview_url = self._preview_url(society_id, img.vault_document_id)
            images.append(out)

        return ComplaintDetailOut(
            id=complaint.id,
            reference=complaint.reference,
            house_id=complaint.house_id,
            house_display_code=house_display_code,
            raised_by=complaint.raised_by,
            category_id=complaint.category_id,
            category_name=category_name,
            title=complaint.title,
            description=complaint.description,
            status=complaint.status,
            resolved_at=complaint.resolved_at,
            closed_at=complaint.closed_at,
            archived_at=complaint.archived_at,
            withdrawn_at=complaint.withdrawn_at,
            created_at=complaint.created_at,
            updated_at=complaint.updated_at,
            timeline=timeline,
            images=images,
        )

    def _preview_url(self, society_id: int, document_id: int) -> str | None:
        """A signed inline preview URL for a stored image, or ``None`` if Vault
        can't produce one (module disabled / document gone) — detail must never
        fail because a preview can't be signed."""
        from app.modules.vault import api as vault_api

        try:
            return vault_api.get_preview_url(
                self._session, society_id, document_id
            ).url
        except Exception:
            return None
