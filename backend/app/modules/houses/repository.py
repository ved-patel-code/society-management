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
from app.modules.onboarding.models import Building, House


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

    def buildings_by_ids(
        self, building_ids: set[int]
    ) -> dict[int, Building]:
        """Fetch buildings for a set of ids in ONE query (display-code batch).

        Returns an ``{id: Building}`` map so a house list can derive display codes
        without a per-row building lookup (no N+1). Empty input → no query.
        """
        if not building_ids:
            return {}
        rows = (
            self._session.execute(
                select(Building).where(Building.id.in_(building_ids))
            )
            .scalars()
            .all()
        )
        return {b.id: b for b in rows}

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

    def houses_owing(
        self, society_id: int
    ) -> list[tuple[int, date | None]]:
        """Dues-owing houses: ``(house_id, first_left_empty_on)`` for status !=
        empty (Finance contract — docs/modules/finance.md §4/§7).

        Empty houses never owe. Uses ``ix_houses_society_status``. Returns only the
        two columns Finance needs (no full-row load, no N+1).
        """
        rows = self._session.execute(
            select(House.id, House.first_left_empty_on).where(
                House.society_id == society_id,
                House.status != "empty",
            )
        ).all()
        return [(int(r[0]), r[1]) for r in rows]

    def house_by_number(
        self, society_id: int, number: str, *, building_id: int | None = None
    ) -> House | None:
        """Resolve a house by its bare number (the "enter house number" flow —
        Finance collection, docs/modules/finance.md §4/§6).

        For building-type societies a bare number is unique only within a
        building, so ``building_id`` disambiguates; individual-type numbers are
        unique per society. Returns None if not found or ambiguous.
        """
        conditions = [House.society_id == society_id, House.number == number]
        if building_id is not None:
            conditions.append(House.building_id == building_id)
        rows = (
            self._session.execute(select(House).where(*conditions).limit(2))
            .scalars()
            .all()
        )
        # Ambiguous (multiple buildings share the number, no building_id) → None.
        return rows[0] if len(rows) == 1 else None

    def current_owned_houses(
        self, society_id: int, user_id: int
    ) -> list[House]:
        """The houses a user CURRENTLY OWNS in a society (Complaints contract §7).

        Owner occupancies only (``party_type='owner'``, ``is_current``), joined to
        the house so the caller can read the house + derive its display code. Used
        by Complaints to infer/verify the raiser's house (docs/modules/
        complaints.md §4). Ordered by house id for a stable "which house?" prompt.
        """
        rows = (
            self._session.execute(
                select(House)
                .join(HouseOccupancy, HouseOccupancy.house_id == House.id)
                .where(
                    House.society_id == society_id,
                    HouseOccupancy.society_id == society_id,
                    HouseOccupancy.user_id == user_id,
                    HouseOccupancy.party_type == "owner",
                    HouseOccupancy.is_current.is_(True),
                )
                .order_by(House.id)
            )
            .scalars()
            .all()
        )
        return list(rows)

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
