"""Tests for Finance EXPENSES + categories (docs/modules/finance.md §4/§6).

Wave D writes: ``add_category``, ``record_expense``, ``void_expense``. Covers the
default-category seed-on-first-list, custom category + duplicate 409, expense
record → outflow ledger posted (reserve drops), category/amount validation, void →
reversal (original + reversal both visible, ``is_reversed`` set, reserve restored),
double-void 409, and the security surface (read vs manage_expenses, cross-society
isolation). Finance depends_on houses (→ onboarding); enabling seeds ``society_admin``
all five ``finance.*`` perms and ``resident`` ``finance.read``.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.modules.finance.models import Expense, ExpenseCategory, LedgerEntry
from app.platform.models import AuditLog
from app.platform.societies.schemas import ModuleAllocation
from app.platform.societies.service import SocietyService
from app.platform.users.provisioning import UserProvisioningService

from tests.conftest import DEFAULT_MEMBER_PASSWORD
from tests._houses_helpers import _admin_bearer

DEFAULT_CATEGORY_NAMES = {
    "Electricity",
    "Water",
    "Housekeeping",
    "Security",
    "Repairs",
    "Salaries",
    "Misc",
}


def _enable_finance(db, society, superadmin) -> None:
    SocietyService(db).set_modules(
        society.id,
        [
            ModuleAllocation(module_key="onboarding", enabled=True, config={}),
            ModuleAllocation(module_key="houses", enabled=True, config={}),
            ModuleAllocation(module_key="finance", enabled=True, config={}),
        ],
        actor_user_id=superadmin.id,
    )
    db.commit()


def _setup(db, society, admin_user, superadmin, auth) -> dict[str, str]:
    """Enable finance (+ deps) and return an activated admin bearer header."""
    _enable_finance(db, society, superadmin)
    return _admin_bearer(auth, admin_user)


def _add_category(auth, hdr, name):
    return auth.client.post(
        "/finance/expense-categories", headers=hdr, json={"name": name}
    )


def _record_expense(auth, hdr, category_id, amount, incurred_on, description=None):
    return auth.client.post(
        "/finance/expenses",
        headers=hdr,
        json={
            "category_id": category_id,
            "amount": str(amount),
            "incurred_on": incurred_on,
            "description": description,
        },
    )


def _reserve_balance(auth, hdr) -> Decimal:
    resp = auth.client.get("/finance/reserve", headers=hdr)
    assert resp.status_code == 200, resp.text
    return Decimal(resp.json()["balance"])


def _first_category_id(auth, hdr) -> int:
    resp = auth.client.get("/finance/expense-categories", headers=hdr)
    assert resp.status_code == 200, resp.text
    return resp.json()[0]["id"]


# ===========================================================================
# categories: default seed + add custom + duplicate 409
# ===========================================================================

def test_default_categories_seeded_on_first_list(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = auth.client.get("/finance/expense-categories", headers=hdr)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    names = {c["name"] for c in body}
    assert names == DEFAULT_CATEGORY_NAMES
    assert all(c["is_system"] is True for c in body)
    # Idempotent: a second list does not re-seed (still exactly 7).
    again = auth.client.get("/finance/expense-categories", headers=hdr)
    assert len(again.json()) == 7


def test_add_custom_category(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = _add_category(auth, hdr, "Gardening")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Gardening"
    assert body["is_system"] is False
    assert body["id"] > 0

    # Now listed alongside the 7 system defaults (8 total).
    listing = auth.client.get("/finance/expense-categories", headers=hdr).json()
    assert len(listing) == 8
    assert "Gardening" in {c["name"] for c in listing}

    # Audited finance.category_added.
    db.expire_all()
    audits = db.query(AuditLog).filter(
        AuditLog.action == "finance.category_added",
        AuditLog.society_id == society.id,
        AuditLog.entity_id == body["id"],
    ).all()
    assert len(audits) == 1
    assert audits[0].after == {"name": "Gardening", "category_id": body["id"]}


def test_add_duplicate_category_conflicts(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    assert _add_category(auth, hdr, "Gardening").status_code == 200
    dup = _add_category(auth, hdr, "Gardening")
    assert dup.status_code == 409, dup.text
    assert dup.json()["code"] == "conflict"

    # Duplicating a SYSTEM default name also conflicts (seeded on first use).
    sys_dup = _add_category(auth, hdr, "Electricity")
    assert sys_dup.status_code == 409, sys_dup.text

    # Only one extra row was written.
    db.expire_all()
    rows = db.execute(
        select(ExpenseCategory).where(
            ExpenseCategory.society_id == society.id,
            ExpenseCategory.name == "Gardening",
        )
    ).scalars().all()
    assert len(rows) == 1


def test_add_category_blank_name_rejected(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = _add_category(auth, hdr, "")
    assert resp.status_code == 422, resp.text


# ===========================================================================
# record expense: ledger outflow + reserve drop + validation + pagination
# ===========================================================================

def test_record_expense_posts_outflow_and_drops_reserve(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    cat_id = _first_category_id(auth, hdr)

    before = _reserve_balance(auth, hdr)
    resp = _record_expense(
        auth, hdr, cat_id, "500.00", "2026-07-05", description="July power bill"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "recorded"
    assert Decimal(body["amount"]) == Decimal("500.00")
    assert body["description"] == "July power bill"
    exp_id = body["id"]

    # Reserve dropped by exactly the expense amount (one outflow entry).
    after = _reserve_balance(auth, hdr)
    assert after == before - Decimal("500.00")

    # Exactly one expense OUTFLOW ledger entry linked to the expense.
    db.expire_all()
    entries = db.execute(
        select(LedgerEntry).where(
            LedgerEntry.society_id == society.id,
            LedgerEntry.source_type == "expense",
            LedgerEntry.source_id == exp_id,
        )
    ).scalars().all()
    assert len(entries) == 1
    e = entries[0]
    assert e.entry_type == "expense"
    assert e.direction == "outflow"
    assert e.amount == Decimal("500.00")
    assert e.occurred_on.isoformat() == "2026-07-05"
    assert e.is_reversed is False

    # Audited.
    audits = db.query(AuditLog).filter(
        AuditLog.action == "finance.expense_recorded",
        AuditLog.entity_id == exp_id,
    ).all()
    assert len(audits) == 1


def test_record_expense_invalid_category_not_found(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = _record_expense(auth, hdr, 999999, "100.00", "2026-07-05")
    assert resp.status_code == 404, resp.text


def test_record_expense_zero_amount_rejected(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    cat_id = _first_category_id(auth, hdr)
    resp = _record_expense(auth, hdr, cat_id, "0.00", "2026-07-05")
    assert resp.status_code == 422, resp.text


def test_record_expense_negative_amount_rejected(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    cat_id = _first_category_id(auth, hdr)
    resp = _record_expense(auth, hdr, cat_id, "-50.00", "2026-07-05")
    assert resp.status_code == 422, resp.text


def test_list_expenses_paginated_newest_first(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    cat_id = _first_category_id(auth, hdr)
    # Three expenses on ascending incurred_on dates.
    assert _record_expense(auth, hdr, cat_id, "10.00", "2026-01-10").status_code == 200
    assert _record_expense(auth, hdr, cat_id, "20.00", "2026-03-10").status_code == 200
    third = _record_expense(auth, hdr, cat_id, "30.00", "2026-05-10")
    assert third.status_code == 200
    third_id = third.json()["id"]

    resp = auth.client.get(
        "/finance/expenses", headers=hdr, params={"page": 1, "page_size": 2}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    items = body["items"]
    assert body["total"] == 3
    assert len(items) == 2
    # Newest incurred_on first.
    assert items[0]["incurred_on"] == "2026-05-10"
    assert items[1]["incurred_on"] == "2026-03-10"

    page2 = auth.client.get(
        "/finance/expenses", headers=hdr, params={"page": 2, "page_size": 2}
    )
    assert page2.status_code == 200, page2.text
    page2_body = page2.json()
    assert len(page2_body["items"]) == 1
    assert page2_body["items"][0]["incurred_on"] == "2026-01-10"
    assert page2_body["total"] == 3

    # include_voided=false excludes a voided expense from the listing.
    void = auth.client.post(
        f"/finance/expenses/{third_id}/void", headers=hdr, json={"reason": "test"}
    )
    assert void.status_code == 200, void.text
    filtered = auth.client.get(
        "/finance/expenses",
        headers=hdr,
        params={"page": 1, "page_size": 10, "include_voided": False},
    )
    assert filtered.status_code == 200, filtered.text
    filtered_body = filtered.json()
    assert filtered_body["total"] == 2
    assert all(i["id"] != third_id for i in filtered_body["items"])


# ===========================================================================
# void expense: reversal posted, both visible, reserve restored, double-void 409
# ===========================================================================

def test_void_expense_posts_reversal_and_restores_reserve(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    cat_id = _first_category_id(auth, hdr)

    baseline = _reserve_balance(auth, hdr)
    rec = _record_expense(auth, hdr, cat_id, "750.00", "2026-07-05")
    assert rec.status_code == 200, rec.text
    exp_id = rec.json()["id"]
    assert _reserve_balance(auth, hdr) == baseline - Decimal("750.00")

    void = auth.client.post(
        f"/finance/expenses/{exp_id}/void",
        headers=hdr,
        json={"reason": "duplicate entry"},
    )
    assert void.status_code == 200, void.text
    body = void.json()
    assert body["status"] == "voided"
    assert body["void_reason"] == "duplicate entry"
    assert body["voided_at"] is not None

    # Reserve returns to baseline (outflow negated by the reversal inflow).
    assert _reserve_balance(auth, hdr) == baseline

    db.expire_all()
    # Original expense flagged voided in the DB.
    expense = db.get(Expense, exp_id)
    assert expense.status == "voided"
    assert expense.voided_by == admin_user.id
    assert expense.void_reason == "duplicate entry"

    # Original outflow + reversal inflow — BOTH visible; original is_reversed.
    entries = db.execute(
        select(LedgerEntry)
        .where(
            LedgerEntry.society_id == society.id,
            LedgerEntry.source_type == "expense",
            LedgerEntry.source_id == exp_id,
        )
        .order_by(LedgerEntry.id)
    ).scalars().all()
    assert len(entries) == 2
    original, reversal = entries
    assert original.entry_type == "expense"
    assert original.direction == "outflow"
    assert original.is_reversed is True
    assert reversal.entry_type == "reversal"
    assert reversal.direction == "inflow"
    assert reversal.amount == original.amount
    assert reversal.reverses_entry_id == original.id

    # Both stay visible in GET /finance/reserve (transparency, docs §4).
    ledger = auth.client.get(
        "/finance/reserve", headers=hdr, params={"page": 1, "page_size": 50}
    ).json()
    entry_types = [e["entry_type"] for e in ledger["entries"]]
    assert "expense" in entry_types
    assert "reversal" in entry_types

    # Audited finance.expense_voided (+ reason).
    audits = db.query(AuditLog).filter(
        AuditLog.action == "finance.expense_voided",
        AuditLog.entity_id == exp_id,
    ).all()
    assert len(audits) == 1
    assert audits[0].after["reason"] == "duplicate entry"


def test_double_void_conflicts(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    cat_id = _first_category_id(auth, hdr)
    exp_id = _record_expense(auth, hdr, cat_id, "100.00", "2026-07-05").json()["id"]

    first = auth.client.post(
        f"/finance/expenses/{exp_id}/void", headers=hdr, json={"reason": "oops"}
    )
    assert first.status_code == 200, first.text
    second = auth.client.post(
        f"/finance/expenses/{exp_id}/void", headers=hdr, json={"reason": "again"}
    )
    assert second.status_code == 409, second.text
    assert second.json()["code"] == "conflict"

    # No third ledger entry from the failed re-void (still exactly 2).
    db.expire_all()
    entries = db.execute(
        select(LedgerEntry).where(
            LedgerEntry.society_id == society.id,
            LedgerEntry.source_id == exp_id,
            LedgerEntry.source_type == "expense",
        )
    ).scalars().all()
    assert len(entries) == 2


def test_void_missing_expense_not_found(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = auth.client.post(
        "/finance/expenses/999999/void", headers=hdr, json={"reason": "x"}
    )
    assert resp.status_code == 404, resp.text


# ===========================================================================
# analytics / net: voided expenses excluded from expense totals
# ===========================================================================

def test_analytics_expenses_excludes_voided(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    cat_id = _first_category_id(auth, hdr)
    keep = _record_expense(auth, hdr, cat_id, "300.00", "2026-07-05").json()["id"]
    drop = _record_expense(auth, hdr, cat_id, "200.00", "2026-07-06").json()["id"]
    assert keep and drop

    auth.client.post(
        f"/finance/expenses/{drop}/void", headers=hdr, json={"reason": "wrong"}
    )

    # Recorded-only expense analytics: 300 remains (200 voided excluded).
    resp = auth.client.get("/finance/analytics/expenses", headers=hdr)
    assert resp.status_code == 200, resp.text
    assert Decimal(resp.json()["total_expense"]) == Decimal("300.00")

    # Income/net: total_expense reflects the ledger (expense − reversal = 300).
    income = auth.client.get("/finance/analytics/income", headers=hdr)
    assert income.status_code == 200, income.text
    assert Decimal(income.json()["total_expense"]) == Decimal("300.00")


# ===========================================================================
# security: read vs manage_expenses; cross-society isolation
# ===========================================================================

def _resident_bearer(auth, resident_user) -> dict[str, str]:
    """A resident holds finance.read but NOT finance.manage_expenses."""
    tokens = auth.login_ok(resident_user.email, DEFAULT_MEMBER_PASSWORD)
    resp = auth.client.post(
        "/auth/change-password",
        headers=auth.bearer(tokens["access_token"]),
        json={"current_password": DEFAULT_MEMBER_PASSWORD, "new_password": "NewPass123"},
    )
    assert resp.status_code == 200, resp.text
    sess = auth.login_ok(resident_user.email, "NewPass123")
    return auth.bearer(sess["access_token"])


def test_resident_can_read_but_not_write(
    db, society, admin_user, resident_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    cat_id = _first_category_id(auth, hdr)
    exp_id = _record_expense(auth, hdr, cat_id, "100.00", "2026-07-05").json()["id"]

    res_hdr = _resident_bearer(auth, resident_user)

    # CAN read categories + expenses (finance.read).
    assert auth.client.get(
        "/finance/expense-categories", headers=res_hdr
    ).status_code == 200
    assert auth.client.get("/finance/expenses", headers=res_hdr).status_code == 200

    # CANNOT add a category / record / void (needs finance.manage_expenses).
    assert _add_category(auth, res_hdr, "Nope").status_code == 403
    assert _record_expense(auth, res_hdr, cat_id, "5.00", "2026-07-05").status_code == 403
    assert auth.client.post(
        f"/finance/expenses/{exp_id}/void", headers=res_hdr, json={"reason": "x"}
    ).status_code == 403


def test_cross_society_isolation(
    db, society, admin_user, superadmin, auth
):
    """A second society's admin cannot see/void the first society's expense."""
    hdr = _setup(db, society, admin_user, superadmin, auth)
    cat_id = _first_category_id(auth, hdr)
    exp_id = _record_expense(auth, hdr, cat_id, "400.00", "2026-07-05").json()["id"]

    # Second society + its admin, finance enabled.
    from app.platform.societies.schemas import SocietyCreate

    other = SocietyService(db).create_society(
        SocietyCreate(
            name="Other Society",
            storage_limit_bytes=5 * 1024**3,
            default_member_password=DEFAULT_MEMBER_PASSWORD,
        ),
        actor_user_id=superadmin.id,
    )
    db.commit()
    db.refresh(other)
    other_admin = UserProvisioningService(db).create_or_link_user(
        email="other-admin@test.local",
        society_id=other.id,
        role_key="society_admin",
        profile={"full_name": "Other Admin"},
        actor_user_id=superadmin.id,
    )
    db.commit()
    _enable_finance(db, other, superadmin)
    other_hdr = _admin_bearer(auth, other_admin)

    # Other society's category list is its own fresh seed (different ids).
    other_cat_id = _first_category_id(auth, other_hdr)
    assert other_cat_id != cat_id

    # Voiding society 1's expense from society 2 → 404 (tenant-scoped lookup).
    resp = auth.client.post(
        f"/finance/expenses/{exp_id}/void", headers=other_hdr, json={"reason": "x"}
    )
    assert resp.status_code == 404, resp.text

    # Recording against society 1's category id from society 2 → 404.
    cross = _record_expense(auth, other_hdr, cat_id, "10.00", "2026-07-05")
    assert cross.status_code == 404, cross.text
