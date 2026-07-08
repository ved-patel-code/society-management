"""Complaints queries (docs/03 §2) — pure DB access, ``society_id``-scoped.

No business rules here; the service decides, the repository fetches. Every query
is tenant-scoped by ``society_id`` (cross-tenant isolation — docs/PF §7). The
resident-vs-admin VISIBILITY filter lives HERE (``list_complaints`` takes an
optional ``house_ids`` allow-list) so a resident can never see another house's
complaints — the endpoint cannot forget to scope it (docs §4 "enforced in the
repository query, not the endpoint").

FROZEN interface: wave sub-agents implement service logic against these
signatures but must not change them. Every method reads/writes exactly one
concern; the FOR-UPDATE reference allocator and the batched image-count fetch
(no N+1) are the two performance-critical paths.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.complaints.models import (
    Complaint,
    ComplaintCategory,
    ComplaintImage,
    ComplaintReferenceCounter,
    ComplaintStatusHistory,
)
from app.modules.complaints.schemas import format_reference


class ComplaintRepository:
    """Queries over the five complaint tables, all ``society_id``-scoped."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # --- categories --------------------------------------------------------

    def get_category(
        self, society_id: int, category_id: int
    ) -> ComplaintCategory | None:
        return self._session.execute(
            select(ComplaintCategory).where(
                ComplaintCategory.id == category_id,
                ComplaintCategory.society_id == society_id,
            )
        ).scalar_one_or_none()

    def list_categories(
        self, society_id: int, *, active_only: bool
    ) -> list[ComplaintCategory]:
        conditions = [ComplaintCategory.society_id == society_id]
        if active_only:
            conditions.append(ComplaintCategory.is_active.is_(True))
        rows = (
            self._session.execute(
                select(ComplaintCategory)
                .where(*conditions)
                .order_by(ComplaintCategory.name)
            )
            .scalars()
            .all()
        )
        return list(rows)

    def count_categories(self, society_id: int) -> int:
        return int(
            self._session.execute(
                select(func.count())
                .select_from(ComplaintCategory)
                .where(ComplaintCategory.society_id == society_id)
            ).scalar_one()
        )

    def active_category_by_name(
        self, society_id: int, name: str
    ) -> ComplaintCategory | None:
        """The ACTIVE category with this exact name (collision check)."""
        return self._session.execute(
            select(ComplaintCategory).where(
                ComplaintCategory.society_id == society_id,
                ComplaintCategory.name == name,
                ComplaintCategory.is_active.is_(True),
            )
        ).scalar_one_or_none()

    def add_category(self, category: ComplaintCategory) -> ComplaintCategory:
        self._session.add(category)
        self._session.flush()
        return category

    def categories_by_ids(
        self, category_ids: set[int]
    ) -> dict[int, ComplaintCategory]:
        """``{id: category}`` for a set of ids in ONE query (list label batch)."""
        if not category_ids:
            return {}
        rows = (
            self._session.execute(
                select(ComplaintCategory).where(
                    ComplaintCategory.id.in_(category_ids)
                )
            )
            .scalars()
            .all()
        )
        return {c.id: c for c in rows}

    # --- reference allocation (FOR UPDATE) ---------------------------------

    def allocate_reference(self, society_id: int) -> str:
        """Allocate the next ``C-000123`` reference for a society (docs §3).

        Takes a ``SELECT ... FOR UPDATE`` on the society's counter row (creating it
        at 0 on first use), increments it, and returns the formatted reference.
        Serializes concurrent creates so two complaints never get the same number;
        the partial UNIQUE(society_id, reference) is the backstop. Must be called
        inside the create transaction (the lock is held until commit). Mirrors the
        vault ``get_or_create_usage(lock=True)`` idiom.
        """
        stmt = (
            select(ComplaintReferenceCounter)
            .where(ComplaintReferenceCounter.society_id == society_id)
            .with_for_update()
        )
        counter = self._session.execute(stmt).scalar_one_or_none()
        if counter is None:
            counter = ComplaintReferenceCounter(
                society_id=society_id, next_value=0
            )
            self._session.add(counter)
            self._session.flush()
            # Re-take the row under lock now that it exists (matches vault idiom).
            counter = self._session.execute(
                stmt.where(ComplaintReferenceCounter.id == counter.id)
            ).scalar_one()
        counter.next_value = counter.next_value + 1
        self._session.flush()
        return format_reference(counter.next_value)

    # --- complaints --------------------------------------------------------

    def get_complaint(
        self, society_id: int, complaint_id: int, *, lock: bool = False
    ) -> Complaint | None:
        """Fetch one society-scoped complaint. ``lock=True`` takes a
        ``SELECT ... FOR UPDATE`` so a read-check-insert on the complaint's images
        (the per-kind cap) is serialized against concurrent adders — without it two
        parallel uploads can both pass the cap check and over-commit (docs §4)."""
        stmt = select(Complaint).where(
            Complaint.id == complaint_id,
            Complaint.society_id == society_id,
        )
        if lock:
            stmt = stmt.with_for_update()
        return self._session.execute(stmt).scalar_one_or_none()

    def add_complaint(self, complaint: Complaint) -> Complaint:
        self._session.add(complaint)
        self._session.flush()
        return complaint

    def list_complaints(
        self,
        society_id: int,
        *,
        house_ids: list[int] | None = None,
        status: str | None = None,
        category_id: int | None = None,
        house_id: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        q: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Complaint], int]:
        """Filtered, paginated complaint list + total, newest first (docs §6).

        ``house_ids`` is the VISIBILITY allow-list: pass the caller's own houses
        for a resident (``complaints.read``); pass ``None`` for an admin
        (``complaints.read_all``) to see the whole society. An EMPTY list means the
        caller owns no house → no rows (never "all"). Filters compose (AND). ``q``
        matches reference or title (case-insensitive). No N+1 — image counts are
        batched separately via :meth:`image_counts_for`.
        """
        conditions = [Complaint.society_id == society_id]
        if house_ids is not None:
            if not house_ids:
                return [], 0
            conditions.append(Complaint.house_id.in_(house_ids))
        if status is not None:
            conditions.append(Complaint.status == status)
        if category_id is not None:
            conditions.append(Complaint.category_id == category_id)
        if house_id is not None:
            conditions.append(Complaint.house_id == house_id)
        if date_from is not None:
            conditions.append(Complaint.created_at >= date_from)
        if date_to is not None:
            # Inclusive of the whole ``date_to`` day: created_at is a timestamptz,
            # so compare against the START of the NEXT day (< date_to+1), else a
            # complaint raised at any time on date_to (e.g. today) is dropped.
            conditions.append(Complaint.created_at < date_to + timedelta(days=1))
        if q:
            # Parameterized (no SQL injection), but escape LIKE metacharacters so a
            # literal ``%``/``_`` in the query is matched literally, not as a
            # wildcard (a bare ``%`` would otherwise match everything).
            term = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace(
                "_", "\\_"
            )
            like = f"%{term}%"
            conditions.append(
                func.lower(Complaint.reference).like(func.lower(like), escape="\\")
                | func.lower(Complaint.title).like(func.lower(like), escape="\\")
            )

        total = self._session.execute(
            select(func.count()).select_from(Complaint).where(*conditions)
        ).scalar_one()

        rows = (
            self._session.execute(
                select(Complaint)
                .where(*conditions)
                .order_by(Complaint.id.desc())
                .offset(offset)
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return list(rows), int(total)

    def open_complaint_count(self, society_id: int, house_id: int) -> int:
        """Non-terminal, non-archived complaint count for a house (docs §7)."""
        return int(
            self._session.execute(
                select(func.count())
                .select_from(Complaint)
                .where(
                    Complaint.society_id == society_id,
                    Complaint.house_id == house_id,
                    Complaint.status.notin_(
                        ("archived", "withdrawn", "closed")
                    ),
                )
            ).scalar_one()
        )

    def closed_to_archive(
        self, society_id: int, *, older_than: datetime
    ) -> list[Complaint]:
        """Closed complaints whose ``closed_at <= older_than`` (archive scan §9).

        Uses the partial index ``ix_complaints_status_closed_at``. Idempotent
        source set for the worker — only ``status='closed'`` rows.
        """
        rows = (
            self._session.execute(
                select(Complaint).where(
                    Complaint.society_id == society_id,
                    Complaint.status == "closed",
                    Complaint.closed_at.is_not(None),
                    Complaint.closed_at <= older_than,
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    # --- status history ----------------------------------------------------

    def add_status_history(
        self, history: ComplaintStatusHistory
    ) -> ComplaintStatusHistory:
        self._session.add(history)
        self._session.flush()
        return history

    def list_status_history(
        self, complaint_id: int
    ) -> list[ComplaintStatusHistory]:
        """The complaint's timeline, oldest first (docs §6)."""
        rows = (
            self._session.execute(
                select(ComplaintStatusHistory)
                .where(ComplaintStatusHistory.complaint_id == complaint_id)
                .order_by(ComplaintStatusHistory.id)
            )
            .scalars()
            .all()
        )
        return list(rows)

    # --- images ------------------------------------------------------------

    def add_image(self, image: ComplaintImage) -> ComplaintImage:
        self._session.add(image)
        self._session.flush()
        return image

    def get_image(
        self, complaint_id: int, image_id: int
    ) -> ComplaintImage | None:
        return self._session.execute(
            select(ComplaintImage).where(
                ComplaintImage.id == image_id,
                ComplaintImage.complaint_id == complaint_id,
            )
        ).scalar_one_or_none()

    def list_images(
        self, complaint_id: int, *, kind: str | None = None
    ) -> list[ComplaintImage]:
        conditions = [ComplaintImage.complaint_id == complaint_id]
        if kind is not None:
            conditions.append(ComplaintImage.kind == kind)
        rows = (
            self._session.execute(
                select(ComplaintImage)
                .where(*conditions)
                .order_by(ComplaintImage.id)
            )
            .scalars()
            .all()
        )
        return list(rows)

    def count_images(self, complaint_id: int, *, kind: str) -> int:
        """Current count of a kind of image on a complaint (cap enforcement)."""
        return int(
            self._session.execute(
                select(func.count())
                .select_from(ComplaintImage)
                .where(
                    ComplaintImage.complaint_id == complaint_id,
                    ComplaintImage.kind == kind,
                )
            ).scalar_one()
        )

    def remove_image(self, image: ComplaintImage) -> None:
        self._session.delete(image)
        self._session.flush()

    def image_counts_for(
        self, complaint_ids: list[int]
    ) -> dict[int, dict[str, int]]:
        """Batched ``{complaint_id: {kind: count}}`` for a page (no N+1) (docs §6).

        One grouped query over all complaints on the page instead of a per-row
        count. Complaints with no images are simply absent from the map (the caller
        defaults to 0).
        """
        if not complaint_ids:
            return {}
        rows = self._session.execute(
            select(
                ComplaintImage.complaint_id,
                ComplaintImage.kind,
                func.count(),
            )
            .where(ComplaintImage.complaint_id.in_(complaint_ids))
            .group_by(ComplaintImage.complaint_id, ComplaintImage.kind)
        ).all()
        result: dict[int, dict[str, int]] = {}
        for complaint_id, kind, count in rows:
            result.setdefault(int(complaint_id), {})[kind] = int(count)
        return result
