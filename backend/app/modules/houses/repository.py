"""House & Occupancy queries (docs/03 §2) — pure DB access, ``society_id``-scoped.

No business rules here; the service decides, the repository fetches. Every query
is tenant-scoped by ``society_id`` (cross-tenant isolation — docs/PF §7).

FROZEN interface: wave sub-agents implement service logic against these
signatures but must not change them. The house-registry reads (``list_houses``,
``current_owner_user_ids``) back the cross-module contract (docs §7).
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.houses.models import HouseOccupancy, HouseStatusHistory
from app.modules.onboarding.models import House


class HouseRepository:
    """Queries over houses / house_occupancies / house_status_history."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # --- houses ------------------------------------------------------------

    def get_house(self, society_id: int, house_id: int) -> House | None:
        return self._session.execute(
            select(House).where(
                House.id == house_id, House.society_id == society_id
            )
        ).scalar_one_or_none()

    def list_houses(
        self,
        society_id: int,
        *,
        status: str | None = None,
        building_id: int | None = None,
        floor_id: int | None = None,
        number: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[House], int]:
        """Filtered, paginated house list + total count (docs §6).

        Filters compose (all AND-ed). ``number`` matches the stored bare number
        across buildings; combine with ``building_id`` for a building-scoped
        search. Display-code reconstruction is intentionally not done here (v1).
        """
        conditions = [House.society_id == society_id]
        if status is not None:
            conditions.append(House.status == status)
        if building_id is not None:
            conditions.append(House.building_id == building_id)
        if floor_id is not None:
            conditions.append(House.floor_id == floor_id)
        if number is not None:
            conditions.append(House.number == number)

        total = self._session.execute(
            select(func.count()).select_from(House).where(*conditions)
        ).scalar_one()

        rows = (
            self._session.execute(
                select(House)
                .where(*conditions)
                .order_by(House.id)
                .offset(offset)
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return list(rows), int(total)

    # --- occupancies -------------------------------------------------------

    def current_occupancy(
        self, house_id: int, party_type: str
    ) -> HouseOccupancy | None:
        """The current owner or tenant record for a house (or None)."""
        return self._session.execute(
            select(HouseOccupancy).where(
                HouseOccupancy.house_id == house_id,
                HouseOccupancy.party_type == party_type,
                HouseOccupancy.is_current.is_(True),
            )
        ).scalar_one_or_none()

    def current_occupancies(self, house_id: int) -> list[HouseOccupancy]:
        """All current occupancy records (owner and/or tenant) for a house."""
        rows = (
            self._session.execute(
                select(HouseOccupancy).where(
                    HouseOccupancy.house_id == house_id,
                    HouseOccupancy.is_current.is_(True),
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    def add_occupancy(self, occupancy: HouseOccupancy) -> HouseOccupancy:
        self._session.add(occupancy)
        self._session.flush()
        return occupancy

    def close_occupancy(
        self, occupancy: HouseOccupancy, *, valid_to: date
    ) -> None:
        """Close a current occupancy (frees the partial-unique current slot).

        The caller MUST ``flush`` before inserting a replacement current row so the
        DB never momentarily sees two ``is_current`` rows for the same
        ``(house_id, party_type)`` (partial unique index).
        """
        occupancy.is_current = False
        occupancy.valid_to = valid_to

    def occupancy_by_user_and_house(
        self, user_id: int, house_id: int
    ) -> HouseOccupancy | None:
        """The current occupancy linking a user to a house (for access revoke)."""
        return self._session.execute(
            select(HouseOccupancy).where(
                HouseOccupancy.user_id == user_id,
                HouseOccupancy.house_id == house_id,
                HouseOccupancy.is_current.is_(True),
            )
        ).scalar_one_or_none()

    # --- status history ----------------------------------------------------

    def add_status_history(
        self, history: HouseStatusHistory
    ) -> HouseStatusHistory:
        self._session.add(history)
        self._session.flush()
        return history

    def list_history(self, house_id: int) -> list[HouseStatusHistory]:
        rows = (
            self._session.execute(
                select(HouseStatusHistory)
                .where(HouseStatusHistory.house_id == house_id)
                .order_by(HouseStatusHistory.id.desc())
            )
            .scalars()
            .all()
        )
        return list(rows)

    # --- cross-module contract (docs §7) -----------------------------------

    def current_owner_user_ids(self, society_id: int) -> set[int]:
        """The society's current owner login ids (Notice Board audience, etc.).

        Only current owner occupancies with a linked ``user_id`` count.
        """
        rows = self._session.execute(
            select(HouseOccupancy.user_id).where(
                HouseOccupancy.society_id == society_id,
                HouseOccupancy.party_type == "owner",
                HouseOccupancy.is_current.is_(True),
                HouseOccupancy.user_id.is_not(None),
            )
        ).all()
        return {r[0] for r in rows}
