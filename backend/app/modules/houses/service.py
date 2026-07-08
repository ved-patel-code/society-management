"""House & Occupancy service (docs/modules/house-occupancy.md §4).

All business logic: status-transition legality, required-fields-per-status,
owner-identity/replacement, occupancy open/close, ``first_left_empty_on``
once-only, and the audit + status-history writes. The service NEVER commits
(``get_session`` commits once at request end — docs/03 §2); it flushes where an
id or ordering (partial-unique current slot) is needed.

Read methods are implemented in the frozen core. The write methods
(``change_status``, ``edit_occupancy``) are frozen stubs — Wave C implements the
state machine per the plan's pseudocode.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.common.errors import ConflictError, NotFoundError, ValidationError
from app.common.time import utcnow
from app.modules.houses.models import HouseOccupancy, HouseStatusHistory
from app.modules.houses.repository import HouseRepository
from app.modules.vault import api as vault_api
from app.modules.houses.schemas import (
    NON_EMPTY_STATUSES,
    PARTY_TYPES,
    HouseDetailOut,
    HouseOut,
    OccupancyEditRequest,
    OccupancyOut,
    OwnerPayload,
    StatusChangeRequest,
    StatusHistoryOut,
    TenantPayload,
)
from app.modules.onboarding.models import Building, House
from app.modules.onboarding.numbering import (
    building_display_code,
    individual_display_code,
)
from app.platform.audit.service import AuditService
from app.platform.users.provisioning import UserProvisioningService

# Owner login role — must already exist for the society (seeded on enable).
_RESIDENT_ROLE = "resident"


class HouseService:
    """Occupancy lifecycle orchestration over the shared house registry."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = HouseRepository(session)

    # --- reads -------------------------------------------------------------

    def list_houses(
        self,
        society_id: int,
        *,
        status: str | None,
        building_id: int | None,
        floor_id: int | None,
        number: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[HouseOut], int]:
        """Filtered, paginated house list with derived display codes (docs §6)."""
        houses, total = self._repo.list_houses(
            society_id,
            status=status,
            building_id=building_id,
            floor_id=floor_id,
            number=number,
            offset=offset,
            limit=limit,
        )
        # Batch-load the page's buildings in one query (no per-row lookup / N+1).
        building_ids = {h.building_id for h in houses if h.building_id is not None}
        buildings = self._repo.buildings_by_ids(building_ids)
        return [
            self._to_house_out(h, buildings.get(h.building_id))
            for h in houses
        ], total

    def get_house_detail(self, society_id: int, house_id: int) -> HouseDetailOut:
        """House + current owner/tenant occupancy (docs §6)."""
        house = self._require_house(society_id, house_id)
        owner = self._repo.current_occupancy(house_id, "owner")
        tenant = self._repo.current_occupancy(house_id, "tenant")
        return HouseDetailOut(
            house=self._to_house_out(house, self._building_for(house)),
            owner=OccupancyOut.model_validate(owner) if owner else None,
            tenant=OccupancyOut.model_validate(tenant) if tenant else None,
        )

    def get_history(
        self, society_id: int, house_id: int
    ) -> list[StatusHistoryOut]:
        """A house's status-change history, newest first (docs §6)."""
        self._require_house(society_id, house_id)
        return [
            StatusHistoryOut.model_validate(h)
            for h in self._repo.list_history(house_id)
        ]

    def current_owner_user_ids(self, society_id: int) -> set[int]:
        """Cross-module contract: current owner login ids (docs §7)."""
        return self._repo.current_owner_user_ids(society_id)

    def house_exists(self, society_id: int, house_id: int) -> bool:
        """Cross-module contract: does this house belong to the society? Used by
        Finance to validate a reserve entry's house link (tenant isolation)."""
        return self._repo.get_house(society_id, house_id) is not None

    def is_current_occupant(
        self, society_id: int, user_id: int, house_id: int
    ) -> bool:
        """Cross-module contract: is this user the current owner/tenant of this
        house (within the society)? Used by Finance to scope a resident's dues
        read to their own house (docs finance §2). House must be in the society."""
        if self._repo.get_house(society_id, house_id) is None:
            return False
        return (
            self._repo.occupancy_by_user_and_house(user_id, house_id) is not None
        )

    def houses_owing(self, society_id: int):
        """Cross-module contract (Finance): dues-owing houses as
        ``(house_id, first_left_empty_on)`` for status != empty. Empty houses
        never owe (docs/modules/finance.md §4/§7)."""
        return self._repo.houses_owing(society_id)

    def house_by_number(
        self, society_id: int, number: str, *, building_id: int | None = None
    ) -> House | None:
        """Cross-module contract (Finance): resolve a house by bare number for the
        "enter house number → see dues" flow (docs/modules/finance.md §4/§6)."""
        return self._repo.house_by_number(
            society_id, number, building_id=building_id
        )

    # --- writes (FROZEN — Wave C implements) -------------------------------

    def change_status(
        self,
        society_id: int,
        house_id: int,
        req: StatusChangeRequest,
        *,
        actor_user_id: int,
    ) -> HouseDetailOut:
        """Change a house's status, capturing the target's occupancy (docs §4/§6).

        One transaction (never commits): validate the transition + required
        fields, reconcile the owner (create/update/replace per email identity)
        and the tenant (open/edit/close), stamp ``first_left_empty_on`` on the
        first move away from empty, then — only for a real status change — write
        the status-history + ``house.status_changed`` audit and the new status.
        A same-status POST is treated as an edit (reconcile only).
        """
        house = self._require_house(society_id, house_id)
        current = house.status

        self._validate_transition(current, req.to_status)
        self._validate_required_fields(req)

        audit = AuditService(self._session)

        # Owner is reconciled for every non-empty target (identity = email).
        cur_owner = self._repo.current_occupancy(house_id, "owner")
        self._reconcile_owner(
            society_id, house, cur_owner, req.owner, actor_user_id, audit
        )

        # Tenant: reconcile when rented, otherwise close any current tenant.
        cur_tenant = self._repo.current_occupancy(house_id, "tenant")
        if req.to_status == "rented":
            assert req.tenant is not None  # guaranteed by required-field validation
            self._reconcile_tenant(
                society_id, house, cur_tenant, req.tenant, actor_user_id, audit
            )
        elif cur_tenant is not None:
            # Leaving rented closes the tenant occupancy (status audit covers it).
            self._repo.close_occupancy(cur_tenant, valid_to=self._today())
            self._session.flush()

        # first_left_empty_on: once-only, on the first move away from empty.
        if current == "empty" and house.first_left_empty_on is None:
            house.first_left_empty_on = self._today()

        # Status history + status write + audit — only for a real transition.
        if current != req.to_status:
            snapshot: dict = {"owner": req.owner.model_dump()}
            if req.to_status == "rented" and req.tenant is not None:
                snapshot["tenant"] = req.tenant.model_dump()
            self._repo.add_status_history(
                HouseStatusHistory(
                    society_id=society_id,
                    house_id=house_id,
                    from_status=current,
                    to_status=req.to_status,
                    changed_by=actor_user_id,
                    snapshot=snapshot,
                )
            )
            house.status = req.to_status
            self._session.flush()
            audit.record(
                action="house.status_changed",
                actor_user_id=actor_user_id,
                society_id=society_id,
                entity_type="house",
                entity_id=house_id,
                before={"status": current},
                after={"status": req.to_status},
            )

        return self.get_house_detail(society_id, house_id)

    def edit_occupancy(
        self,
        society_id: int,
        house_id: int,
        party_type: str,
        req: OccupancyEditRequest,
        *,
        actor_user_id: int,
    ) -> HouseDetailOut:
        """Edit owner/tenant details (email change → owner replacement) (docs §4/§6).

        Applies only the provided (non-None) fields to the current occupancy.
        For the owner, a changed email is the identity-replacement path (close +
        revoke old login, provision new, open a fresh occupancy carrying over
        unchanged fields). A tenant email change is a plain field update (no
        login). ``persons_living`` is re-validated against the current status.
        """
        if party_type not in PARTY_TYPES:
            raise ValidationError(
                "Unknown occupancy party.", details={"party_type": party_type}
            )

        house = self._require_house(society_id, house_id)
        cur = self._repo.current_occupancy(house_id, party_type)
        if cur is None:
            raise NotFoundError(f"No current {party_type} for this house.")

        # to_let/for_sale hold no persons_living for the owner (consistent with
        # change_status). Validated against the house's CURRENT status.
        if (
            party_type == "owner"
            and house.status in {"to_let", "for_sale"}
            and req.persons_living is not None
        ):
            raise ValidationError(
                "persons_living is not captured for to_let/for_sale."
            )

        audit = AuditService(self._session)

        # Owner email change → replacement (same as reconcile_owner replace path).
        if (
            party_type == "owner"
            and req.email is not None
            and req.email != cur.email
        ):
            self._replace_owner(
                society_id,
                house,
                cur,
                self._merged_owner_payload(cur, req),
                actor_user_id,
                audit,
            )
            return self.get_house_detail(society_id, house_id)

        # Plain in-place edit of the provided fields.
        before = self._occupancy_fields(cur)
        for field in (
            "full_name",
            "email",
            "contact_number",
            "persons_living",
            "id_proof_type",
            "id_proof_document_id",
        ):
            value = getattr(req, field)
            if value is not None:
                setattr(cur, field, value)
        self._session.flush()

        after = self._occupancy_fields(cur)
        changed_before = {k: before[k] for k in before if before[k] != after[k]}
        changed_after = {k: after[k] for k in after if before[k] != after[k]}
        audit.record(
            action="house.occupancy_updated",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="house",
            entity_id=house.id,
            before=changed_before,
            after=changed_after,
        )
        return self.get_house_detail(society_id, house_id)

    def set_id_proof(
        self,
        society_id: int,
        house_id: int,
        party_type: str,
        *,
        filename: str,
        content_type: str,
        data: bytes,
        id_proof_type: str | None,
        actor_user_id: int,
    ) -> HouseDetailOut:
        """Store an ID-proof image for the current owner/tenant (docs §4/§7).

        Files the bytes into the house's vault ``Proof`` folder (auto-created)
        via the frozen vault contract — atomic with this transaction — then
        links the resulting document to the current occupancy. Vault enforces the
        denylist/quota and may raise 413/415 DomainErrors, which propagate.
        """
        if party_type not in PARTY_TYPES:
            raise ValidationError(
                "Unknown occupancy party.", details={"party_type": party_type}
            )

        self._require_house(society_id, house_id)
        occupancy = self._repo.current_occupancy(house_id, party_type)
        if occupancy is None:
            raise NotFoundError(f"No current {party_type} for this house.")

        folder = vault_api.ensure_house_folder(
            self._session,
            society_id,
            house_id,
            kind=vault_api.HOUSE_FOLDER_PROOF,
            actor_user_id=actor_user_id,
        )
        doc = vault_api.store_document(
            self._session,
            society_id,
            folder.id,
            filename=filename,
            content_type=content_type,
            data=data,
            source="id_proof",
            source_ref=occupancy.id,
            actor_user_id=actor_user_id,
        )

        occupancy.id_proof_document_id = doc.id
        if id_proof_type is not None:
            occupancy.id_proof_type = id_proof_type
        self._session.flush()

        AuditService(self._session).record(
            action="house.id_proof_uploaded",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="house",
            entity_id=house_id,
            after={
                "party": party_type,
                "document_id": doc.id,
                "occupancy_id": occupancy.id,
            },
        )
        return self.get_house_detail(society_id, house_id)

    # --- write helpers (Wave C) --------------------------------------------

    def _validate_transition(self, current: str, to_status: str) -> None:
        """Legal-transition gate (docs §4): never back to empty; target known."""
        if to_status == "empty":
            raise ConflictError("A house can never return to empty.")
        if to_status not in NON_EMPTY_STATUSES:
            raise ValidationError("Unknown target status.")

    def _validate_required_fields(self, req: StatusChangeRequest) -> None:
        """Required-fields-per-target-status gate (docs §4).

        owned needs owner.persons_living; to_let/for_sale forbid it; rented needs
        a tenant with persons_living. Tenant is only valid for rented.
        """
        to_status = req.to_status
        if to_status == "owned":
            if req.owner.persons_living is None:
                raise ValidationError(
                    "persons_living is required for owned."
                )
        elif to_status in {"to_let", "for_sale"}:
            if req.owner.persons_living is not None:
                raise ValidationError(
                    "persons_living is not captured for to_let/for_sale."
                )
        elif to_status == "rented":
            if req.tenant is None:
                raise ValidationError("tenant is required for rented.")
            if req.tenant.persons_living is None:
                raise ValidationError(
                    "persons_living is required for the tenant."
                )

        # Tenant payload only makes sense for a rented target.
        if to_status != "rented" and req.tenant is not None:
            raise ValidationError(
                "tenant is only valid for the rented status."
            )

    def _reconcile_owner(
        self,
        society_id: int,
        house: House,
        cur_owner: HouseOccupancy | None,
        payload: OwnerPayload,
        actor_user_id: int,
        audit: AuditService,
    ) -> None:
        """Create, update, or replace the owner by email identity (docs §4).

        No current owner → provision the login + open the occupancy. Same email →
        update fields in place (keep the login). Different email → replacement.
        """
        if cur_owner is None:
            provisioning = UserProvisioningService(self._session)
            user = provisioning.create_or_link_user(
                email=payload.email,
                society_id=society_id,
                role_key=_RESIDENT_ROLE,
                profile={
                    "full_name": payload.full_name,
                    "phone": payload.contact_number,
                },
                actor_user_id=actor_user_id,
            )
            self._repo.add_occupancy(
                HouseOccupancy(
                    society_id=society_id,
                    house_id=house.id,
                    party_type="owner",
                    user_id=user.id,
                    full_name=payload.full_name,
                    email=payload.email,
                    contact_number=payload.contact_number,
                    persons_living=payload.persons_living,
                    id_proof_type=payload.id_proof_type,
                    id_proof_document_id=payload.id_proof_document_id,
                    is_current=True,
                    valid_from=self._today(),
                )
            )
            audit.record(
                action="house.occupancy_created",
                actor_user_id=actor_user_id,
                society_id=society_id,
                entity_type="house",
                entity_id=house.id,
                after={"party_type": "owner", "email": payload.email},
            )
            return

        if payload.email == cur_owner.email:
            # Same owner — update details, keep the login. ID proof is RETAINED
            # across status changes (spec §4): a payload that omits it (None) must
            # not wipe the stored proof — carry it over. persons_living, by
            # contrast, is taken as-is: required for owned, intentionally cleared
            # to None for to_let/for_sale (spec §3 decision 3).
            before = self._occupancy_fields(cur_owner)
            cur_owner.full_name = payload.full_name
            cur_owner.email = payload.email
            cur_owner.contact_number = payload.contact_number
            cur_owner.persons_living = payload.persons_living
            if payload.id_proof_type is not None:
                cur_owner.id_proof_type = payload.id_proof_type
            if payload.id_proof_document_id is not None:
                cur_owner.id_proof_document_id = payload.id_proof_document_id
            self._session.flush()
            after = self._occupancy_fields(cur_owner)
            audit.record(
                action="house.occupancy_updated",
                actor_user_id=actor_user_id,
                society_id=society_id,
                entity_type="house",
                entity_id=house.id,
                before=before,
                after=after,
            )
            return

        # Different email → owner replaced.
        self._replace_owner(
            society_id, house, cur_owner, payload, actor_user_id, audit
        )

    def _replace_owner(
        self,
        society_id: int,
        house: House,
        cur_owner: HouseOccupancy,
        payload: OwnerPayload,
        actor_user_id: int,
        audit: AuditService,
    ) -> None:
        """Owner-identity replacement (docs §4): close old, revoke, provision new.

        Ordering is load-bearing: close + flush FIRST to free the partial-unique
        current slot before opening the replacement occupancy.
        """
        old_user_id = cur_owner.user_id
        old_email = cur_owner.email

        self._repo.close_occupancy(cur_owner, valid_to=self._today())
        self._session.flush()  # free the current slot before the new INSERT

        provisioning = UserProvisioningService(self._session)
        if old_user_id is not None:
            provisioning.revoke_house_access(
                user_id=old_user_id,
                house_id=house.id,
                actor_user_id=actor_user_id,
            )

        new_user = provisioning.create_or_link_user(
            email=payload.email,
            society_id=society_id,
            role_key=_RESIDENT_ROLE,
            profile={
                "full_name": payload.full_name,
                "phone": payload.contact_number,
            },
            actor_user_id=actor_user_id,
        )
        self._repo.add_occupancy(
            HouseOccupancy(
                society_id=society_id,
                house_id=house.id,
                party_type="owner",
                user_id=new_user.id,
                full_name=payload.full_name,
                email=payload.email,
                contact_number=payload.contact_number,
                persons_living=payload.persons_living,
                id_proof_type=payload.id_proof_type,
                id_proof_document_id=payload.id_proof_document_id,
                is_current=True,
                valid_from=self._today(),
            )
        )
        audit.record(
            action="house.owner_replaced",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="house",
            entity_id=house.id,
            before={"user_id": old_user_id, "email": old_email},
            after={"user_id": new_user.id, "email": payload.email},
        )

    def _reconcile_tenant(
        self,
        society_id: int,
        house: House,
        cur_tenant: HouseOccupancy | None,
        payload: TenantPayload,
        actor_user_id: int,
        audit: AuditService,
    ) -> None:
        """Open or edit the tenant occupancy (docs §4). Tenant login is deferred,
        so ``user_id`` stays NULL and there is no replacement flow — always an
        in-place edit when a current tenant exists.
        """
        if cur_tenant is None:
            self._repo.add_occupancy(
                HouseOccupancy(
                    society_id=society_id,
                    house_id=house.id,
                    party_type="tenant",
                    user_id=None,
                    full_name=payload.full_name,
                    email=payload.email,
                    contact_number=payload.contact_number,
                    persons_living=payload.persons_living,
                    id_proof_type=payload.id_proof_type,
                    id_proof_document_id=payload.id_proof_document_id,
                    is_current=True,
                    valid_from=self._today(),
                )
            )
            audit.record(
                action="house.occupancy_created",
                actor_user_id=actor_user_id,
                society_id=society_id,
                entity_type="house",
                entity_id=house.id,
                after={"party_type": "tenant", "email": payload.email},
            )
            return

        # In-place tenant edit. ID proof is retained when the payload omits it
        # (carry-over), mirroring the owner path; the rest is taken as-is
        # (full_name/contact_number/persons_living are required for rented).
        before = self._occupancy_fields(cur_tenant)
        cur_tenant.full_name = payload.full_name
        cur_tenant.email = payload.email
        cur_tenant.contact_number = payload.contact_number
        cur_tenant.persons_living = payload.persons_living
        if payload.id_proof_type is not None:
            cur_tenant.id_proof_type = payload.id_proof_type
        if payload.id_proof_document_id is not None:
            cur_tenant.id_proof_document_id = payload.id_proof_document_id
        self._session.flush()
        after = self._occupancy_fields(cur_tenant)
        audit.record(
            action="house.occupancy_updated",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="house",
            entity_id=house.id,
            before=before,
            after=after,
        )

    @staticmethod
    def _occupancy_fields(occupancy: HouseOccupancy) -> dict:
        """The editable occupancy fields as a plain JSON-able dict (audit diffs)."""
        return {
            "full_name": occupancy.full_name,
            "email": occupancy.email,
            "contact_number": occupancy.contact_number,
            "persons_living": occupancy.persons_living,
            "id_proof_type": occupancy.id_proof_type,
            "id_proof_document_id": occupancy.id_proof_document_id,
        }

    def _merged_owner_payload(
        self, cur: HouseOccupancy, req: OccupancyEditRequest
    ) -> OwnerPayload:
        """Build the replacement owner payload for an edit-driven email change,
        carrying over any field the edit does not change from the old record.
        """
        return OwnerPayload(
            full_name=req.full_name if req.full_name is not None else cur.full_name,
            email=req.email,  # replace path only runs when email is provided
            contact_number=(
                req.contact_number
                if req.contact_number is not None
                else cur.contact_number
            ),
            persons_living=(
                req.persons_living
                if req.persons_living is not None
                else cur.persons_living
            ),
            id_proof_type=(
                req.id_proof_type
                if req.id_proof_type is not None
                else cur.id_proof_type
            ),
            id_proof_document_id=(
                req.id_proof_document_id
                if req.id_proof_document_id is not None
                else cur.id_proof_document_id
            ),
        )

    @staticmethod
    def _today() -> date:
        """Project-standard 'today' (UTC date), matching provisioning's usage."""
        return utcnow().date()

    # --- helpers -----------------------------------------------------------

    def _require_house(self, society_id: int, house_id: int) -> House:
        house = self._repo.get_house(society_id, house_id)
        if house is None:
            raise NotFoundError(
                "House not found.", details={"house_id": house_id}
            )
        return house

    def _building_for(self, house: House) -> Building | None:
        """The house's building (single-house callers). Returns None for
        individual-type houses. List callers batch-load instead (no N+1)."""
        if house.building_id is None:
            return None
        return self._session.get(Building, house.building_id)

    def _to_house_out(
        self, house: House, building: Building | None
    ) -> HouseOut:
        """Shape a house row + derive its display code (never stored).

        ``building`` is passed in by the caller — batch-loaded for lists, fetched
        once for a single house — so this never issues a per-row query.
        """
        if house.building_id is not None:
            separator = "-"
            name = ""
            if building is not None:
                separator = building.numbering_config.get(
                    "display_separator", "-"
                )
                name = building.name
            display = building_display_code(
                name, house.number, separator=separator
            )
        else:
            display = individual_display_code(house.number)

        out = HouseOut.model_validate(house)
        out.display_code = display
        return out
