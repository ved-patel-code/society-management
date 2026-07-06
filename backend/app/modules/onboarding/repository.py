"""Onboarding queries (docs/03 §2) — pure DB access, ``society_id``-scoped.

No business rules here; the service decides, the repository fetches. Every query
is tenant-scoped by ``society_id`` (cross-tenant isolation — docs/PF §7). The
house-registry reads at the bottom are the cross-module contract other modules
consume via the service.

FROZEN interface: wave sub-agents implement the bodies but must not change the
signatures the service/contract depends on.
"""
from __future__ import annotations

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.onboarding.models import (
    Building,
    Floor,
    House,
    OnboardingProgress,
    Row,
)


class OnboardingRepository:
    """Queries over onboarding_progress / buildings / floors / rows / houses."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # --- progress ----------------------------------------------------------

    def get_progress(self, society_id: int) -> OnboardingProgress | None:
        return self._session.execute(
            select(OnboardingProgress).where(
                OnboardingProgress.society_id == society_id
            )
        ).scalar_one_or_none()

    def add_progress(self, progress: OnboardingProgress) -> OnboardingProgress:
        self._session.add(progress)
        self._session.flush()
        return progress

    # --- buildings / floors ------------------------------------------------

    def list_buildings(self, society_id: int) -> list[Building]:
        rows = (
            self._session.execute(
                select(Building)
                .where(Building.society_id == society_id)
                .order_by(Building.display_order)
            )
            .scalars()
            .all()
        )
        return list(rows)

    def get_building(self, society_id: int, building_id: int) -> Building | None:
        return self._session.execute(
            select(Building).where(
                Building.id == building_id, Building.society_id == society_id
            )
        ).scalar_one_or_none()

    def add_building(self, building: Building) -> Building:
        self._session.add(building)
        self._session.flush()
        return building

    def list_floors(self, building_id: int) -> list[Floor]:
        rows = (
            self._session.execute(
                select(Floor)
                .where(Floor.building_id == building_id)
                .order_by(Floor.level)
            )
            .scalars()
            .all()
        )
        return list(rows)

    def get_floor(self, society_id: int, floor_id: int) -> Floor | None:
        return self._session.execute(
            select(Floor).where(
                Floor.id == floor_id, Floor.society_id == society_id
            )
        ).scalar_one_or_none()

    def add_floor(self, floor: Floor) -> Floor:
        self._session.add(floor)
        self._session.flush()
        return floor

    # --- rows --------------------------------------------------------------

    def list_rows(self, society_id: int) -> list[Row]:
        rows = (
            self._session.execute(
                select(Row)
                .where(Row.society_id == society_id)
                .order_by(Row.display_order)
            )
            .scalars()
            .all()
        )
        return list(rows)

    def get_row(self, society_id: int, row_id: int) -> Row | None:
        return self._session.execute(
            select(Row).where(Row.id == row_id, Row.society_id == society_id)
        ).scalar_one_or_none()

    def add_row(self, row: Row) -> Row:
        self._session.add(row)
        self._session.flush()
        return row

    # --- houses ------------------------------------------------------------

    def add_house(self, house: House) -> House:
        self._session.add(house)
        self._session.flush()
        return house

    def get_house(self, society_id: int, house_id: int) -> House | None:
        return self._session.execute(
            select(House).where(
                House.id == house_id, House.society_id == society_id
            )
        ).scalar_one_or_none()

    def list_houses_for_building(self, building_id: int) -> list[House]:
        rows = (
            self._session.execute(
                select(House)
                .where(House.building_id == building_id)
                .order_by(House.id)
            )
            .scalars()
            .all()
        )
        return list(rows)

    def building_numbers(self, society_id: int, building_id: int) -> set[str]:
        """Existing bare numbers in a building (for clash detection)."""
        rows = self._session.execute(
            select(House.number).where(
                House.society_id == society_id, House.building_id == building_id
            )
        ).all()
        return {r[0] for r in rows}

    def individual_numbers(self, society_id: int) -> set[str]:
        """Existing bare numbers among individual houses (clash detection)."""
        rows = self._session.execute(
            select(House.number).where(
                House.society_id == society_id, House.building_id.is_(None)
            )
        ).all()
        return {r[0] for r in rows}

    def max_continuous_number(self, society_id: int) -> int:
        """Highest integer house number so far (continuous-sequential seed).

        Only purely-numeric numbers count; returns 0 when none exist.
        """
        rows = self._session.execute(
            select(House.number).where(House.society_id == society_id)
        ).all()
        best = 0
        for (num,) in rows:
            if num.isdigit():
                best = max(best, int(num))
        return best

    # --- house registry (cross-module contract — docs §7) ------------------

    def list_all_houses(self, society_id: int) -> list[House]:
        rows = (
            self._session.execute(
                select(House)
                .where(House.society_id == society_id)
                .order_by(House.id)
            )
            .scalars()
            .all()
        )
        return list(rows)

    def resolve_by_building_and_number(
        self, society_id: int, building_id: int, number: str
    ) -> House | None:
        return self._session.execute(
            select(House).where(
                House.society_id == society_id,
                House.building_id == building_id,
                House.number == number,
            )
        ).scalar_one_or_none()

    def resolve_by_number(self, society_id: int, number: str) -> House | None:
        """Resolve an individual-type house by its society-unique number."""
        return self._session.execute(
            select(House).where(
                House.society_id == society_id,
                House.building_id.is_(None),
                House.number == number,
            )
        ).scalar_one_or_none()

    def has_non_empty_houses_for_building(self, building_id: int) -> bool:
        """Any house in this building not in 'empty' status (delete guard)."""
        row = self._session.execute(
            select(House.id).where(
                House.building_id == building_id, House.status != "empty"
            ).limit(1)
        ).first()
        return row is not None

    def has_non_empty_houses_for_floor(self, floor_id: int) -> bool:
        row = self._session.execute(
            select(House.id).where(
                House.floor_id == floor_id, House.status != "empty"
            ).limit(1)
        ).first()
        return row is not None

    # --- guarded deletes (later edits — docs §4) ---------------------------

    def delete_house(self, house: House) -> None:
        self._session.delete(house)

    def delete_floor(self, floor: Floor) -> None:
        self._session.delete(floor)

    def delete_building(self, building: Building) -> None:
        self._session.delete(building)

    def delete_houses_for_building(self, building_id: int) -> None:
        """Bulk-delete a building's houses (cascade before the building is removed)."""
        self._session.execute(
            sa_delete(House).where(House.building_id == building_id)
        )

    def delete_floors_for_building(self, building_id: int) -> None:
        """Bulk-delete a building's floors (cascade before the building is removed)."""
        self._session.execute(
            sa_delete(Floor).where(Floor.building_id == building_id)
        )

    def delete_houses_for_floor(self, floor_id: int) -> None:
        """Bulk-delete a floor's houses (cascade before the floor is removed)."""
        self._session.execute(
            sa_delete(House).where(House.floor_id == floor_id)
        )
