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

from sqlalchemy.orm import Session

from app.common.errors import NotFoundError
from app.modules.houses.repository import HouseRepository
from app.modules.houses.schemas import (
    HouseDetailOut,
    HouseOut,
    OccupancyEditRequest,
    OccupancyOut,
    StatusChangeRequest,
    StatusHistoryOut,
)
from app.modules.onboarding.models import Building, House
from app.modules.onboarding.numbering import (
    building_display_code,
    individual_display_code,
)


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
        return [self._to_house_out(h) for h in houses], total

    def get_house_detail(self, society_id: int, house_id: int) -> HouseDetailOut:
        """House + current owner/tenant occupancy (docs §6)."""
        house = self._require_house(society_id, house_id)
        owner = self._repo.current_occupancy(house_id, "owner")
        tenant = self._repo.current_occupancy(house_id, "tenant")
        return HouseDetailOut(
            house=self._to_house_out(house),
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

    # --- writes (FROZEN — Wave C implements) -------------------------------

    def change_status(
        self,
        society_id: int,
        house_id: int,
        req: StatusChangeRequest,
        *,
        actor_user_id: int,
    ) -> HouseDetailOut:
        """Change a house's status, capturing the target's occupancy (docs §4/§6)."""
        raise NotImplementedError  # Wave C

    def edit_occupancy(
        self,
        society_id: int,
        house_id: int,
        party_type: str,
        req: OccupancyEditRequest,
        *,
        actor_user_id: int,
    ) -> HouseDetailOut:
        """Edit owner/tenant details (email change → owner replacement) (docs §4/§6)."""
        raise NotImplementedError  # Wave C

    # --- helpers -----------------------------------------------------------

    def _require_house(self, society_id: int, house_id: int) -> House:
        house = self._repo.get_house(society_id, house_id)
        if house is None:
            raise NotFoundError(
                "House not found.", details={"house_id": house_id}
            )
        return house

    def _to_house_out(self, house: House) -> HouseOut:
        """Shape a house row + derive its display code (never stored)."""
        if house.building_id is not None:
            building = self._session.get(Building, house.building_id)
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
