"""Complaints worker-job tests (Module 5, Wave F) — docs/modules/complaints.md §9.

Covers the daily auto-archive scan (:mod:`app.modules.complaints.services.jobs`):

- a complaint closed longer ago than the society's ``auto_archive_days`` is
  archived: ``status='archived'``, ``archived_at`` stamped, a
  ``(closed -> archived, changed_by=NULL)`` timeline row appended, and a
  ``complaint.archived`` audit row written (actor = system worker);
- a complaint closed RECENTLY (inside the window) is left untouched;
- a non-closed complaint (open / resolved) is never touched;
- idempotency — running the scan twice archives nothing the second time
  (only ``status='closed'`` rows are ever selected);
- per-society isolation — two societies' eligible complaints archive
  independently and the summary counts are correct;
- each society's configured ``auto_archive_days`` is respected (a smaller window
  archives what a larger window would still hold).

The worker helper :func:`_run_for_societies` is driven with a chosen ``as_of``
date for determinism (never the real "today"), exactly like the finance worker
tests. We also exercise :func:`_enabled_complaints_society_ids` and the whole-scan
:func:`run_daily_auto_archive` entry point. Assertions check DB state + audit
rows, not just return values.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select

from app.modules.complaints.models import (
    Complaint,
    ComplaintCategory,
    ComplaintStatusHistory,
)
from app.modules.complaints.schemas import format_reference
from app.modules.complaints.services.jobs import (
    _enabled_complaints_society_ids,
    _run_for_societies,
    run_daily_auto_archive,
)
from app.platform.models import AuditLog

from tests._complaints_helpers import (
    owned_house_for,
    second_society_with_complaints,
    setup_complaints,
)

# A fixed run date the whole module pins the window to (deterministic).
AS_OF = date(2026, 7, 8)


def _utc_midnight(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=timezone.utc)


# The worker now takes a full aware-UTC instant (``now``), not a date — the run
# is pinned to midnight of AS_OF so the window math + ``archived_at`` assertions
# stay exact.
NOW = _utc_midnight(AS_OF)


# ===========================================================================
# low-level fixture builders (insert rows directly for a controlled closed_at)
# ===========================================================================


def _category(db, society_id: int) -> ComplaintCategory:
    """A concrete active category to attach test complaints to."""
    cat = ComplaintCategory(
        society_id=society_id,
        name="Plumbing",
        is_active=True,
        is_system=True,
        created_by=None,
    )
    db.add(cat)
    db.flush()
    return cat


def _make_complaint(
    db,
    *,
    society_id: int,
    house_id: int,
    category_id: int,
    raised_by: int,
    reference_n: int,
    status: str,
    closed_at: datetime | None = None,
) -> Complaint:
    """Insert a complaint row directly with a chosen status + closed_at.

    Building the row directly (rather than driving the service transitions) lets
    each spec pin ``closed_at`` to an exact instant relative to the run date,
    which is what the archive window is measured against.
    """
    c = Complaint(
        society_id=society_id,
        reference=format_reference(reference_n),
        house_id=house_id,
        raised_by=raised_by,
        category_id=category_id,
        title="Leaky tap",
        description="Water everywhere.",
        status=status,
        closed_at=closed_at,
    )
    db.add(c)
    db.flush()
    return c


def _history(db, complaint_id: int) -> list[ComplaintStatusHistory]:
    return list(
        db.execute(
            select(ComplaintStatusHistory)
            .where(ComplaintStatusHistory.complaint_id == complaint_id)
            .order_by(ComplaintStatusHistory.id)
        ).scalars()
    )


def _archived_audit(db, society_id: int, complaint_id: int) -> list[AuditLog]:
    return list(
        db.execute(
            select(AuditLog)
            .where(
                AuditLog.society_id == society_id,
                AuditLog.action == "complaint.archived",
                AuditLog.entity_type == "complaint",
                AuditLog.entity_id == complaint_id,
            )
            .order_by(AuditLog.id)
        ).scalars()
    )


# ===========================================================================
# happy path: a complaint closed past the window is archived (DB + history +
# audit), plus a recently-closed one is left alone
# ===========================================================================


def test_archives_old_closed_leaves_recent(
    db, society, admin_user, superadmin, auth
):
    # auto_archive_days = 15 (default). Old = closed 20 days ago; recent = 5.
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    house_id = owned_house_for(auth, hdr, email="raiser@x.com")
    cat = _category(db, society.id)

    old = _make_complaint(
        db,
        society_id=society.id,
        house_id=house_id,
        category_id=cat.id,
        raised_by=admin_user.id,
        reference_n=1,
        status="closed",
        closed_at=_utc_midnight(AS_OF) - timedelta(days=20),
    )
    recent = _make_complaint(
        db,
        society_id=society.id,
        house_id=house_id,
        category_id=cat.id,
        raised_by=admin_user.id,
        reference_n=2,
        status="closed",
        closed_at=_utc_midnight(AS_OF) - timedelta(days=5),
    )
    db.commit()

    result = _run_for_societies(db, [society.id], NOW)
    db.expire_all()

    assert result == {"societies_processed": 1, "complaints_archived": 1}

    # The old complaint is archived, archived_at stamped at the run instant.
    db.refresh(old)
    assert old.status == "archived"
    assert old.archived_at == _utc_midnight(AS_OF)

    # It grew a (closed -> archived, changed_by=NULL) history row.
    hist = _history(db, old.id)
    assert len(hist) == 1
    row = hist[0]
    assert row.from_status == "closed"
    assert row.to_status == "archived"
    assert row.changed_by is None
    assert row.note is None

    # And exactly one complaint.archived audit row, actor = system (NULL).
    audits = _archived_audit(db, society.id, old.id)
    assert len(audits) == 1
    assert audits[0].actor_user_id is None
    assert audits[0].after == {"reference": old.reference}

    # The recently-closed complaint is untouched.
    db.refresh(recent)
    assert recent.status == "closed"
    assert recent.archived_at is None
    assert _history(db, recent.id) == []
    assert _archived_audit(db, society.id, recent.id) == []


# ===========================================================================
# non-closed complaints (open / resolved) are never touched
# ===========================================================================


def test_non_closed_never_archived(db, society, admin_user, superadmin, auth):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    house_id = owned_house_for(auth, hdr, email="raiser@x.com")
    cat = _category(db, society.id)

    # open + resolved with an ancient closed_at surrogate (resolved has none) —
    # neither is status='closed', so the scan's source set excludes them.
    open_c = _make_complaint(
        db,
        society_id=society.id,
        house_id=house_id,
        category_id=cat.id,
        raised_by=admin_user.id,
        reference_n=1,
        status="open",
    )
    resolved_c = _make_complaint(
        db,
        society_id=society.id,
        house_id=house_id,
        category_id=cat.id,
        raised_by=admin_user.id,
        reference_n=2,
        status="resolved",
    )
    db.commit()

    result = _run_for_societies(db, [society.id], NOW)
    db.expire_all()

    assert result == {"societies_processed": 1, "complaints_archived": 0}
    db.refresh(open_c)
    db.refresh(resolved_c)
    assert open_c.status == "open"
    assert resolved_c.status == "resolved"
    assert _archived_audit(db, society.id, open_c.id) == []
    assert _archived_audit(db, society.id, resolved_c.id) == []


# ===========================================================================
# idempotency: a second run archives nothing new
# ===========================================================================


def test_idempotent_second_run(db, society, admin_user, superadmin, auth):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    house_id = owned_house_for(auth, hdr, email="raiser@x.com")
    cat = _category(db, society.id)
    c = _make_complaint(
        db,
        society_id=society.id,
        house_id=house_id,
        category_id=cat.id,
        raised_by=admin_user.id,
        reference_n=1,
        status="closed",
        closed_at=_utc_midnight(AS_OF) - timedelta(days=20),
    )
    db.commit()

    first = _run_for_societies(db, [society.id], NOW)
    db.expire_all()
    assert first == {"societies_processed": 1, "complaints_archived": 1}

    again = _run_for_societies(db, [society.id], NOW)
    db.expire_all()
    assert again == {"societies_processed": 1, "complaints_archived": 0}

    # Still exactly one archive history row + one audit row (no duplicates).
    db.refresh(c)
    assert c.status == "archived"
    assert len(_history(db, c.id)) == 1
    assert len(_archived_audit(db, society.id, c.id)) == 1


# ===========================================================================
# per-society isolation: two societies each archive their own eligible rows;
# summary counts them independently
# ===========================================================================


def test_per_society_isolation(db, society, admin_user, superadmin, auth):
    # Society A.
    hdr_a = setup_complaints(db, society, admin_user, superadmin, auth)
    house_a = owned_house_for(auth, hdr_a, email="raiser-a@x.com")
    cat_a = _category(db, society.id)
    a1 = _make_complaint(
        db,
        society_id=society.id,
        house_id=house_a,
        category_id=cat_a.id,
        raised_by=admin_user.id,
        reference_n=1,
        status="closed",
        closed_at=_utc_midnight(AS_OF) - timedelta(days=30),
    )

    # Society B (independent) — one eligible, one recent.
    soc_b, admin_b, hdr_b = second_society_with_complaints(db, superadmin, auth)
    house_b = owned_house_for(auth, hdr_b, email="raiser-b@x.com")
    cat_b = _category(db, soc_b.id)
    b1 = _make_complaint(
        db,
        society_id=soc_b.id,
        house_id=house_b,
        category_id=cat_b.id,
        raised_by=admin_b.id,
        reference_n=1,
        status="closed",
        closed_at=_utc_midnight(AS_OF) - timedelta(days=30),
    )
    b_recent = _make_complaint(
        db,
        society_id=soc_b.id,
        house_id=house_b,
        category_id=cat_b.id,
        raised_by=admin_b.id,
        reference_n=2,
        status="closed",
        closed_at=_utc_midnight(AS_OF) - timedelta(days=1),
    )
    db.commit()

    result = _run_for_societies(db, [society.id, soc_b.id], NOW)
    db.expire_all()

    # Two societies processed; one archive each (b_recent held back).
    assert result == {"societies_processed": 2, "complaints_archived": 2}

    db.refresh(a1)
    db.refresh(b1)
    db.refresh(b_recent)
    assert a1.status == "archived"
    assert b1.status == "archived"
    assert b_recent.status == "closed"

    # Audit rows are scoped to the right society.
    assert len(_archived_audit(db, society.id, a1.id)) == 1
    assert len(_archived_audit(db, soc_b.id, b1.id)) == 1
    # A's archive did not leak into B's audit scope and vice versa.
    assert _archived_audit(db, society.id, b1.id) == []
    assert _archived_audit(db, soc_b.id, a1.id) == []


# ===========================================================================
# each society's configured auto_archive_days is respected
# ===========================================================================


def test_respects_configured_window(db, society, admin_user, superadmin, auth):
    # auto_archive_days = 30. A complaint closed 20 days ago is INSIDE the window
    # (would archive at the default 15) but must NOT archive here.
    hdr = setup_complaints(
        db, society, admin_user, superadmin, auth,
        config={"auto_archive_days": 30},
    )
    house_id = owned_house_for(auth, hdr, email="raiser@x.com")
    cat = _category(db, society.id)

    within = _make_complaint(
        db,
        society_id=society.id,
        house_id=house_id,
        category_id=cat.id,
        raised_by=admin_user.id,
        reference_n=1,
        status="closed",
        closed_at=_utc_midnight(AS_OF) - timedelta(days=20),
    )
    beyond = _make_complaint(
        db,
        society_id=society.id,
        house_id=house_id,
        category_id=cat.id,
        raised_by=admin_user.id,
        reference_n=2,
        status="closed",
        closed_at=_utc_midnight(AS_OF) - timedelta(days=40),
    )
    db.commit()

    result = _run_for_societies(db, [society.id], NOW)
    db.expire_all()

    assert result == {"societies_processed": 1, "complaints_archived": 1}
    db.refresh(within)
    db.refresh(beyond)
    assert within.status == "closed"  # 20 < 30-day window: held
    assert beyond.status == "archived"  # 40 > 30-day window: archived


# ===========================================================================
# enabled-society discovery + whole-scan entry point (reads today's UTC date)
# ===========================================================================


def test_enabled_ids_and_daily_scan_entrypoint(
    db, society, admin_user, superadmin, auth
):
    from app.common.time import utcnow

    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    # Complaints-enabled society is discoverable.
    assert society.id in _enabled_complaints_society_ids(db)

    house_id = owned_house_for(auth, hdr, email="raiser@x.com")
    cat = _category(db, society.id)
    # closed_at well past the default 15-day window vs. real "today".
    today = utcnow().date()
    c = _make_complaint(
        db,
        society_id=society.id,
        house_id=house_id,
        category_id=cat.id,
        raised_by=admin_user.id,
        reference_n=1,
        status="closed",
        closed_at=_utc_midnight(today) - timedelta(days=60),
    )
    db.commit()

    # The real daily scan owns its own session + commits; it should archive c.
    result = run_daily_auto_archive()
    db.expire_all()
    assert result["societies_processed"] >= 1
    assert result["complaints_archived"] >= 1

    db.refresh(c)
    assert c.status == "archived"
    assert len(_archived_audit(db, society.id, c.id)) == 1


def test_disabled_society_not_in_enabled_set(
    db, society, admin_user, superadmin, auth
):
    from app.platform.societies.schemas import ModuleAllocation
    from app.platform.societies.service import SocietyService

    # Enable only onboarding + houses (complaints NOT enabled).
    SocietyService(db).set_modules(
        society.id,
        [
            ModuleAllocation(module_key="onboarding", enabled=True, config={}),
            ModuleAllocation(module_key="houses", enabled=True, config={}),
        ],
        actor_user_id=superadmin.id,
    )
    db.commit()

    assert society.id not in _enabled_complaints_society_ids(db)
