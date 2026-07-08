"""Security / vulnerability tests beyond per-feature 403s (test-gate matrix §5).

Systematizes IDOR across every id-scoped route, JWT cross-tenant, must-change
lockout, and the HTTP-level 422-vs-500 money regression gate (the single most
important test in this suite: the ``jsonable_encoder`` fix in ``main.py`` must
render custom ``field_validator`` errors as 422, never a 500).
"""
from __future__ import annotations

from datetime import date

import pytest

from tests._finance_helpers import (
    enable_finance,
    finance_admin_bearer,
    owned_house,
    resident_bearer,
    second_society_with_finance,
    set_rate_http,
    setup_finance,
)
from tests._houses_helpers import DEFAULT_MEMBER_PASSWORD, _admin_bearer, _owner, _set_status


# ===========================================================================
# 422-not-500 regression gate (P1 — the most important test in this suite)
# ===========================================================================


def test_bad_money_post_returns_422_not_500(db, society, admin_user, superadmin, auth):
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    cats = auth.client.get("/finance/expense-categories", headers=hdr).json()
    category_id = cats[0]["id"]

    bad_bodies = [
        {"category_id": category_id, "amount": "-5", "incurred_on": "2026-01-01"},
        {"category_id": category_id, "amount": "0", "incurred_on": "2026-01-01"},
        {"category_id": category_id, "amount": "1.234", "incurred_on": "2026-01-01"},
        {
            "category_id": category_id,
            "amount": "99999999999999.99",
            "incurred_on": "2026-01-01",
        },
    ]
    for body in bad_bodies:
        resp = auth.client.post("/finance/expenses", headers=hdr, json=body)
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "validation_error"

    rate_bad = [
        {"amount": "0", "valid_from": "2026-01-01"},
        {"amount": "1000.00", "valid_from": "2026-01-15"},  # not month-aligned
    ]
    for body in rate_bad:
        resp = auth.client.post("/finance/rate", headers=hdr, json=body)
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "validation_error"

    hid = owned_house(auth, hdr)
    pay_resp = auth.client.post(
        f"/finance/houses/{hid}/payments",
        headers=hdr,
        json={"method": "wire", "pay_all": True},
    )
    assert pay_resp.status_code == 422, pay_resp.text
    assert pay_resp.json()["code"] == "validation_error"


def test_negative_and_zero_payment_amount_guarded(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_finance(db, society, admin_user, superadmin, auth)
    hid = owned_house(auth, hdr)

    for months in (0, -1):
        resp = auth.client.post(
            f"/finance/houses/{hid}/payments",
            headers=hdr,
            json={"method": "cash", "months": months},
        )
        assert resp.status_code == 422, resp.text

    resp = auth.client.post(
        f"/finance/houses/{hid}/prepaid",
        headers=hdr,
        json={"months_count": -3, "method": "cash"},
    )
    assert resp.status_code == 422, resp.text


# ===========================================================================
# cross-society IDOR
# ===========================================================================


def test_cross_society_dues_read_is_404(db, society, admin_user, superadmin, auth, monkeypatch):
    from tests._finance_helpers import freeze_utcnow

    freeze_utcnow(monkeypatch)
    hdr_a = setup_finance(db, society, admin_user, superadmin, auth)
    hid_a = owned_house(auth, hdr_a)
    set_rate_http(auth, hdr_a, "1000.00", date(2026, 1, 1))
    auth.client.post("/finance/dues/generate", headers=hdr_a)

    _, _, hdr_b = second_society_with_finance(db, superadmin, auth)

    resp = auth.client.get(f"/finance/houses/{hid_a}/dues", headers=hdr_b)
    assert resp.status_code == 404, resp.text


def test_cross_society_pay_and_prepaid_404(
    db, society, admin_user, superadmin, auth, monkeypatch
):
    from tests._finance_helpers import freeze_utcnow

    freeze_utcnow(monkeypatch)
    hdr_a = setup_finance(db, society, admin_user, superadmin, auth)
    hid_a = owned_house(auth, hdr_a)
    set_rate_http(auth, hdr_a, "1000.00", date(2026, 1, 1))
    auth.client.post("/finance/dues/generate", headers=hdr_a)

    _, _, hdr_b = second_society_with_finance(db, superadmin, auth)

    pay = auth.client.post(
        f"/finance/houses/{hid_a}/payments", headers=hdr_b, json={"method": "cash", "pay_all": True}
    )
    assert pay.status_code == 404, pay.text

    prepaid = auth.client.post(
        f"/finance/houses/{hid_a}/prepaid",
        headers=hdr_b,
        json={"months_count": 3, "method": "cash"},
    )
    assert prepaid.status_code == 404, prepaid.text


def test_idor_across_all_id_scoped_ids(db, society, admin_user, superadmin, auth, monkeypatch):
    from tests._finance_helpers import freeze_utcnow

    freeze_utcnow(monkeypatch)
    hdr_a = setup_finance(db, society, admin_user, superadmin, auth)
    hid_a = owned_house(auth, hdr_a)
    set_rate_http(auth, hdr_a, "1000.00", date(2026, 1, 1))
    auth.client.post("/finance/dues/generate", headers=hdr_a)

    pay_a = auth.client.post(
        f"/finance/houses/{hid_a}/payments", headers=hdr_a, json={"method": "cash", "pay_all": True}
    )
    assert pay_a.status_code == 200, pay_a.text
    pid_a = pay_a.json()["id"]

    cats_a = auth.client.get("/finance/expense-categories", headers=hdr_a).json()
    exp_a = auth.client.post(
        "/finance/expenses",
        headers=hdr_a,
        json={"category_id": cats_a[0]["id"], "amount": "100.00", "incurred_on": "2026-01-01"},
    )
    assert exp_a.status_code == 200, exp_a.text
    eid_a = exp_a.json()["id"]

    entry_a = auth.client.post(
        "/finance/reserve/entries",
        headers=hdr_a,
        json={"entry_type": "deposit", "amount": "500.00", "occurred_on": "2026-01-01"},
    )
    assert entry_a.status_code == 200, entry_a.text
    entry_id_a = entry_a.json()["id"]

    _, _, hdr_b = second_society_with_finance(db, superadmin, auth)

    assert (
        auth.client.post(
            f"/finance/payments/{pid_a}/void", headers=hdr_b, json={"reason": "x"}
        ).status_code
        == 404
    )
    assert (
        auth.client.post(
            f"/finance/expenses/{eid_a}/void", headers=hdr_b, json={"reason": "x"}
        ).status_code
        == 404
    )
    assert (
        auth.client.post(
            f"/finance/reserve/entries/{entry_id_a}/reverse", headers=hdr_b
        ).status_code
        == 404
    )
    reserve_entry_house = auth.client.post(
        "/finance/reserve/entries",
        headers=hdr_b,
        json={
            "entry_type": "resale_transfer",
            "amount": "1.00",
            "occurred_on": "2026-01-01",
            "source_type": "house",
            "source_id": hid_a,
        },
    )
    assert reserve_entry_house.status_code == 404, reserve_entry_house.text


def test_jwt_perms_for_society_a_rejected_against_society_b(
    db, society, admin_user, superadmin, auth, make_token
):
    from app.platform.models import UserRole

    setup_finance(db, society, admin_user, superadmin, auth)
    soc_b, _, _ = second_society_with_finance(db, superadmin, auth)

    # Society A's admin role_ids (a "stolen" token carrying A's roles).
    role_ids = [
        r[0]
        for r in db.query(UserRole.role_id)
        .filter(UserRole.user_id == admin_user.id, UserRole.society_id == society.id)
        .all()
    ]
    assert role_ids  # sanity: the admin really has roles in A

    # Craft a token with A's role_ids but active_society_id = B.
    token = make_token(
        user_id=admin_user.id,
        active_society_id=soc_b.id,
        role_ids=role_ids,
    )
    hdr = auth.bearer(token)
    resp = auth.client.post(
        "/finance/rate", headers=hdr, json={"amount": "1000.00", "valid_from": "2026-01-01"}
    )
    # A's role_ids don't grant anything in B's context (permission lookup is
    # scoped by society, docs/PF §7) — the mutation is rejected.
    assert resp.status_code == 403, resp.text


def test_resident_read_allowed_all_mutations_403(
    db, society, admin_user, resident_user, superadmin, auth
):
    enable_finance(db, society, superadmin)
    rhdr = resident_bearer(auth, resident_user)

    for path in (
        "/finance/rate",
        "/finance/reserve",
        "/finance/analytics/collection",
        "/finance/analytics/arrears",
        "/finance/analytics/expenses",
        "/finance/analytics/income",
        "/finance/analytics/trends",
        "/finance/expenses",
        "/finance/expense-categories",
    ):
        resp = auth.client.get(path, headers=rhdr)
        assert resp.status_code == 200, f"{path}: {resp.text}"

    mutations = [
        ("POST", "/finance/rate", {"amount": "1000.00", "valid_from": "2026-01-01"}),
        ("POST", "/finance/houses/1/payments", {"method": "cash", "pay_all": True}),
        ("POST", "/finance/houses/1/prepaid", {"months_count": 3, "method": "cash"}),
        ("POST", "/finance/payments/1/void", {"reason": "x"}),
        (
            "POST",
            "/finance/expenses",
            {"category_id": 1, "amount": "1.00", "incurred_on": "2026-01-01"},
        ),
        ("POST", "/finance/expense-categories", {"name": "New Cat"}),
        ("POST", "/finance/expenses/1/void", {"reason": "x"}),
        (
            "POST",
            "/finance/reserve/entries",
            {"entry_type": "deposit", "amount": "1.00", "occurred_on": "2026-01-01"},
        ),
        ("POST", "/finance/reserve/entries/1/reverse", None),
        (
            "POST",
            "/finance/reserve/reconcile",
            {"actual_balance": "1.00", "occurred_on": "2026-01-01"},
        ),
        ("POST", "/finance/dues/generate", None),
    ]
    for method, path, body in mutations:
        resp = auth.client.request(method, path, headers=rhdr, json=body)
        assert resp.status_code == 403, f"{path}: {resp.text}"


def test_manage_rate_and_dues_generate_require_perm(
    db, society, admin_user, resident_user, superadmin, auth
):
    enable_finance(db, society, superadmin)
    rhdr = resident_bearer(auth, resident_user)

    resp = auth.client.post(
        "/finance/rate", headers=rhdr, json={"amount": "1000.00", "valid_from": "2026-01-01"}
    )
    assert resp.status_code == 403, resp.text

    resp2 = auth.client.post("/finance/dues/generate", headers=rhdr)
    assert resp2.status_code == 403, resp2.text


def test_must_change_password_locks_finance_routes(
    db, society, admin_user, superadmin, auth
):
    enable_finance(db, society, superadmin)
    tokens = auth.login_ok(admin_user.email, DEFAULT_MEMBER_PASSWORD)
    hdr = auth.bearer(tokens["access_token"])

    resp = auth.client.get("/finance/reserve", headers=hdr)
    assert resp.status_code == 403, resp.text


def test_sequential_ids_do_not_leak_cross_tenant(
    db, society, admin_user, superadmin, auth, monkeypatch
):
    from tests._finance_helpers import freeze_utcnow

    freeze_utcnow(monkeypatch)
    hdr_a = setup_finance(db, society, admin_user, superadmin, auth)
    hid_a = owned_house(auth, hdr_a)
    set_rate_http(auth, hdr_a, "1000.00", date(2026, 1, 1))
    auth.client.post("/finance/dues/generate", headers=hdr_a)

    _, _, hdr_b = second_society_with_finance(db, superadmin, auth)
    hid_b = owned_house(auth, hdr_b, email="owner-b@x.com")
    set_rate_http(auth, hdr_b, "1000.00", date(2026, 1, 1))
    auth.client.post("/finance/dues/generate", headers=hdr_b)

    pay_a = auth.client.post(
        f"/finance/houses/{hid_a}/payments", headers=hdr_a, json={"method": "cash", "pay_all": True}
    )
    pay_b = auth.client.post(
        f"/finance/houses/{hid_b}/payments", headers=hdr_b, json={"method": "cash", "pay_all": True}
    )
    assert pay_a.status_code == 200 and pay_b.status_code == 200
    pid_a, pid_b = pay_a.json()["id"], pay_b.json()["id"]
    assert pid_a != pid_b

    # Neither tenant can void the other's payment by guessing a neighboring id.
    assert (
        auth.client.post(f"/finance/payments/{pid_b}/void", headers=hdr_a, json={"reason": "x"}).status_code
        == 404
    )
    assert (
        auth.client.post(f"/finance/payments/{pid_a}/void", headers=hdr_b, json={"reason": "x"}).status_code
        == 404
    )
