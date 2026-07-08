"""Finance E2E across ALL modules + transparency invariant (test-gate matrix §1/§7).

No existing test walks onboarding->houses->finance(+vault) as one journey. These
assert cross-module WIRING on real data (finance consuming ``houses_owing`` /
``house_exists``, not hand-inserted dues), the full audit trail, and the
voids-stay-visible transparency invariant across list endpoints + analytics.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.modules.finance.models import HouseDue, LedgerEntry

from tests._finance_helpers import (
    FROZEN_TODAY,
    audit_actions,
    enable_finance,
    finance_admin_bearer,
    freeze_utcnow,
    owned_house,
    reserve_balance,
    resident_bearer,
    set_rate_http,
    setup_finance,
)
from tests._houses_helpers import (
    _make_building_with_houses,
    _make_vault_doc,
    _owner,
    _set_status,
)

RATE = Decimal("2000.00")


def _dues_for(db, society_id, house_id=None):
    stmt = select(HouseDue).where(HouseDue.society_id == society_id)
    if house_id is not None:
        stmt = stmt.where(HouseDue.house_id == house_id)
    return list(
        db.execute(stmt.order_by(HouseDue.period_year, HouseDue.period_month))
        .scalars()
        .all()
    )


# ===========================================================================
# §1 — full E2E spine
# ===========================================================================


def test_full_society_finance_lifecycle(
    db, society, admin_user, superadmin, auth, monkeypatch
):
    freeze_utcnow(monkeypatch)
    hdr = setup_finance(db, society, admin_user, superadmin, auth)

    # Onboard a building with 3 houses; move 2 to owned/rented, 1 stays empty.
    houses = _make_building_with_houses(
        auth, hdr, floors=[{"level": 1, "houses_count": 3}]
    )
    h1, h2, h3 = (h["id"] for h in houses)
    r1 = _set_status(auth, hdr, h1, "owned", _owner(email="o1@x.com", persons_living=2))
    assert r1.status_code == 200, r1.text
    r2 = _set_status(
        auth,
        hdr,
        h2,
        "rented",
        _owner(email="o2@x.com", persons_living=1),
        tenant={"full_name": "Tenant Two", "contact_number": "555-1002", "persons_living": 1},
    )
    assert r2.status_code == 200, r2.text
    # h3 left empty.

    rate_resp = set_rate_http(auth, hdr, RATE, FROZEN_TODAY.replace(day=1))
    assert rate_resp.status_code == 200, rate_resp.text

    gen = auth.client.post("/finance/dues/generate", headers=hdr)
    assert gen.status_code == 200, gen.text
    assert gen.json()["created"] == 2  # only the 2 non-empty houses, current month

    # Dues generated ONLY for the 2 non-empty houses.
    db.expire_all()
    assert len(_dues_for(db, society.id, h1)) == 1
    assert len(_dues_for(db, society.id, h2)) == 1
    assert _dues_for(db, society.id, h3) == []

    # Pay oldest month on house1.
    pay1 = auth.client.post(
        f"/finance/houses/{h1}/payments", headers=hdr, json={"method": "cash", "months": 1}
    )
    assert pay1.status_code == 200, pay1.text

    # pay_all on house2.
    pay2 = auth.client.post(
        f"/finance/houses/{h2}/payments",
        headers=hdr,
        json={"method": "online", "pay_all": True},
    )
    assert pay2.status_code == 200, pay2.text

    # Record an expense.
    cats = auth.client.get("/finance/expense-categories", headers=hdr)
    assert cats.status_code == 200, cats.text
    category_id = cats.json()[0]["id"]
    exp = auth.client.post(
        "/finance/expenses",
        headers=hdr,
        json={
            "category_id": category_id,
            "amount": "500.00",
            "incurred_on": str(FROZEN_TODAY),
        },
    )
    assert exp.status_code == 200, exp.text

    # Post an opening reserve entry.
    opening = auth.client.post(
        "/finance/reserve/entries",
        headers=hdr,
        json={
            "entry_type": "opening",
            "amount": "10000.00",
            "occurred_on": str(FROZEN_TODAY),
        },
    )
    assert opening.status_code == 200, opening.text

    collection = auth.client.get("/finance/analytics/collection", headers=hdr)
    assert collection.status_code == 200, collection.text
    body = collection.json()
    # expected = rate x 2 houses x 1 month (current period only, both just billed).
    assert Decimal(body["expected"]) == RATE * 2
    assert Decimal(body["collected"]) == RATE * 2

    reserve = auth.client.get("/finance/reserve", headers=hdr)
    assert reserve.status_code == 200, reserve.text
    # reserve = collections (2 x RATE) + opening (10000) - expense (500).
    expected_balance = RATE * 2 + Decimal("10000.00") - Decimal("500.00")
    assert Decimal(reserve.json()["balance"]) == expected_balance
    assert reserve_balance(db, society.id) == expected_balance


def test_finance_consumes_real_houses_owing(
    db, society, admin_user, superadmin, auth, monkeypatch
):
    freeze_utcnow(monkeypatch)
    hdr = setup_finance(db, society, admin_user, superadmin, auth)

    houses = _make_building_with_houses(
        auth, hdr, floors=[{"level": 1, "houses_count": 4}]
    )
    h1, h2, h3, h4 = (h["id"] for h in houses)
    assert (
        _set_status(auth, hdr, h1, "owned", _owner(email="a@x.com", persons_living=1)).status_code
        == 200
    )
    assert (
        _set_status(auth, hdr, h2, "owned", _owner(email="b@x.com", persons_living=1)).status_code
        == 200
    )
    assert (
        _set_status(
            auth,
            hdr,
            h3,
            "rented",
            _owner(email="c@x.com", persons_living=1),
            tenant={"full_name": "T", "contact_number": "555-2", "persons_living": 1},
        ).status_code
        == 200
    )
    # h4 left empty.

    set_rate_http(auth, hdr, RATE, FROZEN_TODAY.replace(day=1))
    gen = auth.client.post("/finance/dues/generate", headers=hdr)
    assert gen.status_code == 200, gen.text
    assert gen.json()["created"] == 3

    db.expire_all()
    assert len(_dues_for(db, society.id, h1)) == 1
    assert len(_dues_for(db, society.id, h2)) == 1
    assert len(_dues_for(db, society.id, h3)) == 1
    assert _dues_for(db, society.id, h4) == []

    preview = auth.client.get(
        "/finance/rate/preview", headers=hdr, params={"amount": "3000.00"}
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["dues_owing_houses"] == 3


def test_first_left_empty_on_drives_backfill_start(
    db, society, admin_user, superadmin, auth, monkeypatch
):
    """Freeze today to month M; set first_left_empty_on to M-3 directly (the
    real houses-module column a status change stamps); generate must backfill
    from that column, not from "today"."""
    freeze_utcnow(monkeypatch)
    hdr = setup_finance(db, society, admin_user, superadmin, auth)

    hid = owned_house(auth, hdr)

    from app.modules.onboarding.models import House

    house = db.get(House, hid)
    house.first_left_empty_on = date(2026, 4, 10)  # M-3 relative to July 2026
    db.commit()

    set_rate_http(auth, hdr, RATE, date(2026, 1, 1))
    gen = auth.client.post("/finance/dues/generate", headers=hdr)
    assert gen.status_code == 200, gen.text
    assert gen.json()["created"] == 4  # Apr, May, Jun, Jul

    db.expire_all()
    dues = _dues_for(db, society.id, hid)
    periods = [(d.period_year, d.period_month) for d in dues]
    assert periods == [(2026, 4), (2026, 5), (2026, 6), (2026, 7)]
    for d in dues:
        assert d.due_date.day == 1  # default maintenance_due_day


def test_resident_reads_their_house_dues_via_login(
    db, society, admin_user, resident_user, superadmin, auth, monkeypatch
):
    """Ownership scope FIX (post-matrix): the owner reading THEIR house -> 200;
    the SAME resident reading a DIFFERENT house -> 403 (not the old 200)."""
    freeze_utcnow(monkeypatch)
    hdr = setup_finance(db, society, admin_user, superadmin, auth)

    houses = _make_building_with_houses(
        auth, hdr, floors=[{"level": 1, "houses_count": 2}]
    )
    my_house, other_house = (h["id"] for h in houses)

    resp = _set_status(
        auth, hdr, my_house, "owned", _owner(email=resident_user.email, persons_living=1)
    )
    assert resp.status_code == 200, resp.text
    resp2 = _set_status(
        auth, hdr, other_house, "owned", _owner(email="stranger@x.com", persons_living=1)
    )
    assert resp2.status_code == 200, resp2.text

    set_rate_http(auth, hdr, RATE, date(2026, 1, 1))
    auth.client.post("/finance/dues/generate", headers=hdr)

    rhdr = resident_bearer(auth, resident_user)

    mine = auth.client.get(f"/finance/houses/{my_house}/dues", headers=rhdr)
    assert mine.status_code == 200, mine.text
    assert Decimal(mine.json()["outstanding_total"]) > 0

    foreign = auth.client.get(f"/finance/houses/{other_house}/dues", headers=rhdr)
    assert foreign.status_code == 403, foreign.text
    assert foreign.json()["code"] == "permission_denied"


def test_prepaid_then_owner_replaced_months_stay_paid(
    db, society, admin_user, superadmin, auth, monkeypatch
):
    freeze_utcnow(monkeypatch)
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    hid = owned_house(auth, hdr, email="owner-old@x.com")
    set_rate_http(auth, hdr, RATE, date(2026, 1, 1))

    gen = auth.client.post("/finance/dues/generate", headers=hdr)
    assert gen.status_code == 200, gen.text
    pay = auth.client.post(
        f"/finance/houses/{hid}/payments", headers=hdr, json={"method": "cash", "pay_all": True}
    )
    assert pay.status_code == 200, pay.text

    prepaid = auth.client.post(
        f"/finance/houses/{hid}/prepaid",
        headers=hdr,
        json={"months_count": 6, "method": "online"},
    )
    assert prepaid.status_code == 200, prepaid.text

    # Replace the owner via a new status change (same target status, new email).
    replace = _set_status(
        auth, hdr, hid, "owned", _owner(email="owner-new@x.com", persons_living=2)
    )
    assert replace.status_code == 200, replace.text

    dues_resp = auth.client.get(f"/finance/houses/{hid}/dues", headers=hdr)
    assert dues_resp.status_code == 200, dues_resp.text
    history = dues_resp.json()["history"]
    prepaid_rows = [d for d in history if d["source"] == "prepaid"]
    assert len(prepaid_rows) == 6
    for row in prepaid_rows:
        assert row["status"] == "paid"


def test_full_void_reconcile_analytics_trail(
    db, society, admin_user, superadmin, auth, monkeypatch
):
    freeze_utcnow(monkeypatch)
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    hid = owned_house(auth, hdr)
    set_rate_http(auth, hdr, RATE, date(2026, 1, 1))
    auth.client.post("/finance/dues/generate", headers=hdr)

    pay = auth.client.post(
        f"/finance/houses/{hid}/payments", headers=hdr, json={"method": "cash", "pay_all": True}
    )
    assert pay.status_code == 200, pay.text
    pid = pay.json()["id"]

    cats = auth.client.get("/finance/expense-categories", headers=hdr).json()
    category_id = cats[0]["id"]
    exp = auth.client.post(
        "/finance/expenses",
        headers=hdr,
        json={"category_id": category_id, "amount": "300.00", "incurred_on": str(FROZEN_TODAY)},
    )
    assert exp.status_code == 200, exp.text
    eid = exp.json()["id"]
    void_exp = auth.client.post(
        f"/finance/expenses/{eid}/void", headers=hdr, json={"reason": "mistake"}
    )
    assert void_exp.status_code == 200, void_exp.text

    deposit = auth.client.post(
        "/finance/reserve/entries",
        headers=hdr,
        json={"entry_type": "deposit", "amount": "1000.00", "occurred_on": str(FROZEN_TODAY)},
    )
    assert deposit.status_code == 200, deposit.text
    entry_id = deposit.json()["id"]
    reverse = auth.client.post(
        f"/finance/reserve/entries/{entry_id}/reverse", headers=hdr
    )
    assert reverse.status_code == 200, reverse.text

    reserve_before = auth.client.get("/finance/reserve", headers=hdr).json()["balance"]
    reconcile = auth.client.post(
        "/finance/reserve/reconcile",
        headers=hdr,
        json={
            "actual_balance": str(Decimal(reserve_before) + Decimal("50.00")),
            "occurred_on": str(FROZEN_TODAY),
        },
    )
    assert reconcile.status_code == 200, reconcile.text

    void_pay = auth.client.post(
        f"/finance/payments/{pid}/void", headers=hdr, json={"reason": "dup"}
    )
    assert void_pay.status_code == 200, void_pay.text

    db.expire_all()
    reserve = auth.client.get("/finance/reserve", headers=hdr)
    assert reserve.status_code == 200
    assert Decimal(reserve.json()["balance"]) == reserve_balance(db, society.id)

    income = auth.client.get("/finance/analytics/income", headers=hdr)
    assert income.status_code == 200
    assert Decimal(income.json()["total_collection"]) == Decimal("0")  # payment voided
    assert Decimal(income.json()["total_expense"]) == Decimal("0")  # expense voided

    actions = {a for a, _, _ in audit_actions(db, society.id)}
    expected_actions = {
        "society.created",
        "module.allocated",
        "finance.rate_set",
        "finance.payment_recorded",
        "finance.expense_recorded",
        "finance.expense_voided",
        "finance.reserve_entry_posted",
        "finance.reserve_entry_reversed",
        "finance.reserve_reconciled",
        "finance.payment_voided",
    }
    assert expected_actions <= actions


def test_e2e_vault_and_finance_coexist(
    db, society, admin_user, superadmin, auth, monkeypatch
):
    freeze_utcnow(monkeypatch)
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    hid = owned_house(auth, hdr)

    doc_id = _make_vault_doc(db, society.id, filename="idproof.jpg")
    assert doc_id > 0

    set_rate_http(auth, hdr, RATE, date(2026, 1, 1))
    auth.client.post("/finance/dues/generate", headers=hdr)
    pay = auth.client.post(
        f"/finance/houses/{hid}/payments", headers=hdr, json={"method": "cash", "pay_all": True}
    )
    assert pay.status_code == 200, pay.text

    actions = {a for a, _, _ in audit_actions(db, society.id)}
    assert "finance.payment_recorded" in actions


# ===========================================================================
# §7 — transparency invariant
# ===========================================================================


def _entries(db, society_id):
    return list(
        db.execute(
            select(LedgerEntry).where(LedgerEntry.society_id == society_id)
        ).scalars()
    )


def test_voided_payment_original_and_reversal_both_in_reserve_and_ledger(
    db, society, admin_user, superadmin, auth, monkeypatch
):
    freeze_utcnow(monkeypatch)
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    hid = owned_house(auth, hdr)
    set_rate_http(auth, hdr, RATE, date(2026, 1, 1))
    auth.client.post("/finance/dues/generate", headers=hdr)

    before = reserve_balance(db, society.id)
    pay = auth.client.post(
        f"/finance/houses/{hid}/payments", headers=hdr, json={"method": "cash", "pay_all": True}
    )
    pid = pay.json()["id"]

    void = auth.client.post(f"/finance/payments/{pid}/void", headers=hdr, json={"reason": "x"})
    assert void.status_code == 200, void.text

    db.expire_all()
    entries = _entries(db, society.id)
    collection = [e for e in entries if e.entry_type == "collection"]
    reversal = [e for e in entries if e.entry_type == "reversal"]
    assert len(collection) == 1 and collection[0].is_reversed is True
    assert len(reversal) == 1
    assert reversal[0].reverses_entry_id == collection[0].id
    assert reserve_balance(db, society.id) == before

    reserve = auth.client.get("/finance/reserve", headers=hdr).json()
    types = {e["entry_type"] for e in reserve["entries"]}
    assert {"collection", "reversal"} <= types


def test_voided_expense_both_visible_reserve_nets(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    cats = auth.client.get("/finance/expense-categories", headers=hdr).json()
    category_id = cats[0]["id"]

    before = reserve_balance(db, society.id)
    exp = auth.client.post(
        "/finance/expenses",
        headers=hdr,
        json={"category_id": category_id, "amount": "200.00", "incurred_on": "2026-07-01"},
    )
    eid = exp.json()["id"]
    void = auth.client.post(
        f"/finance/expenses/{eid}/void", headers=hdr, json={"reason": "wrong"}
    )
    assert void.status_code == 200, void.text

    db.expire_all()
    entries = _entries(db, society.id)
    original = [e for e in entries if e.entry_type == "expense"]
    reversal = [e for e in entries if e.entry_type == "reversal"]
    assert len(original) == 1 and original[0].is_reversed is True
    assert len(reversal) == 1
    assert reserve_balance(db, society.id) == before


def test_voided_payment_still_in_lists_but_excluded_from_recorded_analytics(
    db, society, admin_user, superadmin, auth, monkeypatch
):
    freeze_utcnow(monkeypatch)
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    hid = owned_house(auth, hdr)
    set_rate_http(auth, hdr, RATE, date(2026, 1, 1))
    auth.client.post("/finance/dues/generate", headers=hdr)

    pay = auth.client.post(
        f"/finance/houses/{hid}/payments", headers=hdr, json={"method": "cash", "pay_all": True}
    )
    pid = pay.json()["id"]
    income_before = auth.client.get("/finance/analytics/income", headers=hdr).json()
    assert Decimal(income_before["total_collection"]) == RATE

    void = auth.client.post(f"/finance/payments/{pid}/void", headers=hdr, json={"reason": "x"})
    assert void.status_code == 200, void.text

    income_after = auth.client.get("/finance/analytics/income", headers=hdr).json()
    assert Decimal(income_after["total_collection"]) == Decimal("0")

    collection_after = auth.client.get("/finance/analytics/collection", headers=hdr).json()
    assert Decimal(collection_after["collected"]) == Decimal("0")
    assert Decimal(collection_after["outstanding"]) == RATE

    # But the ledger still shows both entries (visible in reports).
    db.expire_all()
    entries = _entries(db, society.id)
    assert any(e.entry_type == "collection" for e in entries)
    assert any(e.entry_type == "reversal" for e in entries)


def test_voided_expense_visible_in_list_excluded_from_expense_analytics(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    cats = auth.client.get("/finance/expense-categories", headers=hdr).json()
    category_id = cats[0]["id"]

    exp1 = auth.client.post(
        "/finance/expenses",
        headers=hdr,
        json={"category_id": category_id, "amount": "100.00", "incurred_on": "2026-07-01"},
    )
    exp2 = auth.client.post(
        "/finance/expenses",
        headers=hdr,
        json={"category_id": category_id, "amount": "150.00", "incurred_on": "2026-07-02"},
    )
    eid2 = exp2.json()["id"]
    void = auth.client.post(
        f"/finance/expenses/{eid2}/void", headers=hdr, json={"reason": "dup"}
    )
    assert void.status_code == 200, void.text

    default_list = auth.client.get("/finance/expenses", headers=hdr).json()
    assert default_list["total"] == 2  # default include_voided=True

    filtered = auth.client.get(
        "/finance/expenses", headers=hdr, params={"include_voided": "false"}
    ).json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["id"] == exp1.json()["id"]

    analytics = auth.client.get("/finance/analytics/expenses", headers=hdr).json()
    assert Decimal(analytics["total_expense"]) == Decimal("100.00")


def test_reversed_reserve_entry_visible_and_flagged(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    before = reserve_balance(db, society.id)
    deposit = auth.client.post(
        "/finance/reserve/entries",
        headers=hdr,
        json={"entry_type": "deposit", "amount": "500.00", "occurred_on": "2026-07-01"},
    )
    entry_id = deposit.json()["id"]
    reverse = auth.client.post(f"/finance/reserve/entries/{entry_id}/reverse", headers=hdr)
    assert reverse.status_code == 200, reverse.text

    reserve = auth.client.get("/finance/reserve", headers=hdr).json()
    assert reserve["total"] == 2
    entries_by_id = {e["id"]: e for e in reserve["entries"]}
    assert entries_by_id[entry_id]["is_reversed"] is True
    assert Decimal(reserve["balance"]) == before


def test_reconcile_adjustment_visible_in_ledger(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    computed = reserve_balance(db, society.id)
    actual = computed + Decimal("777.00")
    reconcile = auth.client.post(
        "/finance/reserve/reconcile",
        headers=hdr,
        json={"actual_balance": str(actual), "occurred_on": "2026-07-01"},
    )
    assert reconcile.status_code == 200, reconcile.text
    body = reconcile.json()
    assert body["entry_type"] == "adjustment"
    assert body["direction"] == "inflow"
    assert Decimal(body["amount"]) == Decimal("777.00")

    reserve = auth.client.get("/finance/reserve", headers=hdr).json()
    assert Decimal(reserve["balance"]) == actual
    assert any(e["entry_type"] == "adjustment" for e in reserve["entries"])
