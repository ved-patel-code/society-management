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

from app.modules.complaints.repository import ComplaintRepository
from app.modules.complaints.schemas import (
    ComplaintCreateRequest,
    ComplaintDetailOut,
    ComplaintListOut,
    ComplaintUpdateRequest,
)


class ComplaintsCrudService:
    def __init__(self, session: Session, repo: ComplaintRepository) -> None:
        self._session = session
        self._repo = repo

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
        raise NotImplementedError("Wave B: ComplaintsCrudService.raise_complaint")

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
        raise NotImplementedError("Wave B: ComplaintsCrudService.edit_complaint")

    def withdraw_complaint(
        self, society_id: int, complaint_id: int, *, actor_user_id: int
    ) -> ComplaintDetailOut:
        """Withdraw an open complaint (raiser-only, while ``open``) (§4/§6)."""
        raise NotImplementedError(
            "Wave B: ComplaintsCrudService.withdraw_complaint"
        )

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
        raise NotImplementedError("Wave B: ComplaintsCrudService.list_complaints")

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
        raise NotImplementedError("Wave B: ComplaintsCrudService.get_detail")
