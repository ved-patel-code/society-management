"""OnboardingService — the onboarding module's business logic + audit (docs/03 §2).

All rules live here (docs/modules/onboarding.md §4/§5): type selection, structure
mapping via the pure ``numbering`` engine, house generation in one transaction per
building/row batch, number overrides with clash reporting, the blocking-wizard
state machine, completion (flip society → active), later edits, and the
cross-module house-registry reads.

Every state change writes an audit row in the SAME session; the service NEVER
commits (``get_session`` commits once — docs/PF §12).

FROZEN CORE + WAVE STUBS: the Phase-0 lead implements type selection, the state
machine helpers, the registry reads, and the audit snapshots. The generation /
override / complete / later-edit methods are the wave sub-agents' slices — their
signatures are frozen; their bodies raise ``NotImplementedError`` until built.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.common.errors import ConflictError, NotFoundError, ValidationError
from app.modules.onboarding import numbering
from app.modules.onboarding.models import Building, House, OnboardingProgress
from app.modules.onboarding.repository import OnboardingRepository
from app.modules.onboarding.schemas import (
    SOCIETY_TYPES,
    BuildingMapRequest,
    BuildingsCreateRequest,
    RowsCreateRequest,
)
from app.platform.audit.service import AuditService
from app.platform.models import Society


class OnboardingService:
    """Orchestrates the onboarding wizard for the caller's active society."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = OnboardingRepository(session)
        self._audit = AuditService(session)

    # --- society + progress helpers (lead-owned) ---------------------------

    def _get_society(self, society_id: int) -> Society:
        society = self._session.get(Society, society_id)
        if society is None:
            raise NotFoundError(
                "Society not found.", details={"society_id": society_id}
            )
        return society

    def _get_or_create_progress(self, society_id: int) -> OnboardingProgress:
        """Fetch the wizard progress row, creating a fresh one on first touch."""
        progress = self._repo.get_progress(society_id)
        if progress is None:
            progress = self._repo.add_progress(
                OnboardingProgress(
                    society_id=society_id,
                    current_step="type_selection",
                )
            )
        return progress

    def _require_onboarding_open(self, society: Society) -> None:
        """Reject wizard writes once onboarding is complete (society active)."""
        if society.status != "onboarding":
            raise ConflictError(
                "Onboarding is already complete for this society.",
                details={"society_id": society.id, "status": society.status},
            )

    # --- type selection (lead-owned; step 1) -------------------------------

    def select_type(
        self, society_id: int, society_type: str, *, actor_user_id: int
    ) -> Society:
        """Set ``societies.type`` (step 1). Cannot change once houses exist (spec §4)."""
        if society_type not in SOCIETY_TYPES:
            raise ValidationError(
                "Invalid society type.",
                details={"field": "type", "allowed": sorted(SOCIETY_TYPES)},
            )
        society = self._get_society(society_id)
        self._require_onboarding_open(society)

        progress = self._get_or_create_progress(society_id)

        # Changing type after structure exists would orphan houses — block it.
        if society.type is not None and society.type != society_type:
            if self._repo.list_all_houses(society_id):
                raise ConflictError(
                    "Cannot change society type after houses exist.",
                    details={"current_type": society.type},
                )

        before_type = society.type
        society.type = society_type
        progress.type_selected = society_type
        progress.current_step = "structure_mapping"
        self._session.flush()

        self._audit.record(
            action="onboarding.type_selected",
            actor_user_id=actor_user_id,
            society_id=society_id,
            entity_type="society",
            entity_id=society_id,
            before={"type": before_type},
            after={"type": society_type},
        )
        return society

    # --- house registry (cross-module contract — docs §7; lead-owned) ------

    def list_houses(self, society_id: int) -> list[dict[str, Any]]:
        """All houses with derived display codes — the registry read other modules use."""
        houses = self._repo.list_all_houses(society_id)
        buildings = {b.id: b for b in self._repo.list_buildings(society_id)}
        return [self._house_with_display(h, buildings) for h in houses]

    def resolve_house(
        self,
        society_id: int,
        *,
        number: str,
        building_id: int | None = None,
    ) -> dict[str, Any]:
        """Resolve a house by (building, number) or by number (individual). 404 if absent."""
        if building_id is not None:
            house = self._repo.resolve_by_building_and_number(
                society_id, building_id, number
            )
        else:
            house = self._repo.resolve_by_number(society_id, number)
        if house is None:
            raise NotFoundError(
                "House not found.",
                details={"number": number, "building_id": building_id},
            )
        buildings = {b.id: b for b in self._repo.list_buildings(society_id)}
        return self._house_with_display(house, buildings)

    def _house_with_display(
        self, house: House, buildings: dict[int, Building]
    ) -> dict[str, Any]:
        if house.building_id is not None:
            building = buildings.get(house.building_id)
            separator = "-"
            if building is not None:
                separator = building.numbering_config.get("display_separator", "-")
            name = building.name if building is not None else ""
            display = numbering.building_display_code(
                name, house.number, separator=separator
            )
        else:
            display = numbering.individual_display_code(house.number)
        return {
            "id": house.id,
            "society_id": house.society_id,
            "building_id": house.building_id,
            "floor_id": house.floor_id,
            "row_id": house.row_id,
            "position_in_row": house.position_in_row,
            "number": house.number,
            "numbering_mode": house.numbering_mode,
            "number_overridden": house.number_overridden,
            "status": house.status,
            "display_code": display,
        }

    # --- audit snapshot helper (lead-owned) --------------------------------

    @staticmethod
    def _house_snapshot(house: House) -> dict[str, Any]:
        return {
            "building_id": house.building_id,
            "floor_id": house.floor_id,
            "row_id": house.row_id,
            "number": house.number,
            "numbering_mode": house.numbering_mode,
            "status": house.status,
        }

    # ======================================================================
    # WAVE SLICES — signatures frozen; bodies built by the wave sub-agents.
    # ======================================================================

    # Wave C: state / resume / draft
    def get_state(self, society_id: int) -> dict[str, Any]:
        raise NotImplementedError("Wave C: build GET /onboarding/state resume payload.")

    def save_draft(
        self, society_id: int, draft: dict, *, actor_user_id: int
    ) -> OnboardingProgress:
        raise NotImplementedError("Wave C: persist in-progress wizard draft.")

    # Wave A: building flow
    def create_buildings(
        self, society_id: int, data: BuildingsCreateRequest, *, actor_user_id: int
    ) -> list[Building]:
        raise NotImplementedError("Wave A: create buildings from typed names.")

    def map_building(
        self,
        society_id: int,
        building_id: int,
        data: BuildingMapRequest,
        *,
        actor_user_id: int,
    ) -> list[House]:
        raise NotImplementedError(
            "Wave A: floors + numbering → generate houses in one transaction."
        )

    def preview_building(
        self, society_id: int, building_id: int
    ) -> list[dict[str, Any]]:
        raise NotImplementedError("Wave A: generated numbers preview.")

    # Wave B: individual flow
    def create_rows(
        self, society_id: int, data: RowsCreateRequest, *, actor_user_id: int
    ) -> list[House]:
        raise NotImplementedError(
            "Wave B: rows + houses/row + numbering → generate houses."
        )

    # Wave C: override + complete
    def override_house_number(
        self, society_id: int, house_id: int, number: str, *, actor_user_id: int
    ) -> House:
        raise NotImplementedError(
            "Wave C: override a house number with uniqueness/clash reporting."
        )

    def complete(self, society_id: int, *, actor_user_id: int) -> Society:
        raise NotImplementedError(
            "Wave C: validate + flip society.status onboarding → active."
        )

    # Wave D: later edits (post-completion)
    def rename_building(
        self, society_id: int, building_id: int, name: str, *, actor_user_id: int
    ) -> Building:
        raise NotImplementedError("Wave D: rename a building (later edit).")

    def delete_building(
        self, society_id: int, building_id: int, *, actor_user_id: int
    ) -> None:
        raise NotImplementedError(
            "Wave D: delete a building, guarded by status='empty' (deferred dues guard)."
        )

    def delete_floor(
        self, society_id: int, floor_id: int, *, actor_user_id: int
    ) -> None:
        raise NotImplementedError("Wave D: delete a floor, guarded by status='empty'.")

    def delete_house(
        self, society_id: int, house_id: int, *, actor_user_id: int
    ) -> None:
        raise NotImplementedError("Wave D: delete a house, guarded by status='empty'.")
