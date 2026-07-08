"""Tests for the Finance RESERVE ledger writes (docs/modules/finance.md §4/§6).

Wave E covers the three reserve writes:
- ``POST /finance/reserve/entries`` — post a dated inflow/outflow entry
  (opening/deposit/interest/resale_transfer/income/adjustment); direction is
  derived for fixed types and required for ``adjustment``.
- ``POST /finance/reserve/entries/{id}/reverse`` — post a negating entry, flag
  the original ``is_reversed``; both stay visible; balance nets back.
- ``POST /finance/reserve/reconcile`` — post an ``adjustment`` for the diff
  between the bank's actual balance and the computed ledger balance.

Reserve writes gate on ``finance.manage_reserve``; the balance/ledger read gates
on ``finance.read``. Balance = Σ inflow − Σ outflow across every entry
(reversals included, since they are ordinary negating rows).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import text

from app.modules.finance.models import LedgerEntry
from app.platform.models import AuditLog
from app.platform.societies.schemas import ModuleAllocation, SocietyCreate
from app.platform.societies.service import SocietyService
from app.platform.users.provisioning import UserProvisioningService

from tests._houses_helpers import _admin_bearer
from tests.conftest import DEFAULT_MEMBER_PASSWORD

_RESERVE_PERM = "finance.manage_reserve"
_READ_PERM = "finance.read"


# ===========================================================================
# harness
# ===========================================================================

def _enable_finance(db, society, superadmin) -> None:
    """Enable onboarding + houses + finance (finance depends_on houses).

    ``set_modules`` seeds each module's ``default_role_permissions`` — so
    ``society_admin`` gets all five ``finance.*`` perms automatically.
    """
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


def _strip_permission(db, society, perm_key: str) -> None:
    from app.platform.roles.repository import RoleRepository

    role = RoleRepository(db).society_role_by_key(society.id, "society_admin")
    perm_id = db.execute(
        text("SELECT id FROM permissions WHERE key=:k"), {"k": perm_key}
    ).scalar_one()
    db.execute(
        text("DELETE FROM role_permissions WHERE role_id=:r AND permission_id=:p"),
        {"r": role.id, "p": perm_id},
    )
    db.commit()


def _second_society(db, superadmin):
    """A second finance-enabled society + activated admin bearer (isolation)."""
    soc = SocietyService(db).create_society(
        SocietyCreate(
            name="Society B",
            storage_limit_bytes=5 * 1024**3,
            default_member_password=DEFAULT_MEMBER_PASSWORD,
        ),
        actor_user_id=superadmin.id,
    )
    db.commit()
    db.refresh(soc)
    admin_b = UserProvisioningService(db).create_or_link_user(
        email="adminb@test.local",
        society_id=soc.id,
        role_key="society_admin",
        profile={"full_name": "Admin B"},
        actor_user_id=superadmin.id,
    )
    db.commit()
    db.refresh(admin_b)
    _enable_finance(db, soc, superadmin)
    return soc, admin_b


def _post_entry(auth, hdr, **body):
    return auth.client.post("/finance/reserve/entries", headers=hdr, json=body)


def _get_reserve(auth, hdr):
    resp = auth.client.get("/finance/reserve", headers=hdr)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _balance(auth, hdr) -> Decimal:
    return Decimal(_get_reserve(auth, hdr)["balance"])


# ===========================================================================
# post entry — happy paths + balance math
# ===========================================================================

def test_post_inflow_types_and_balance(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)

    # opening / deposit / interest / income — all inflow, direction derived.
    assert _post_entry(
        auth, hdr, entry_type="opening", amount="10000.00",
        occurred_on="2026-01-01", description="Opening balance",
    ).status_code == 200
    assert _post_entry(
        auth, hdr, entry_type="deposit", amount="500.00", occurred_on="2026-02-01"
    ).status_code == 200
    assert _post_entry(
        auth, hdr, entry_type="interest", amount="25.50", occurred_on="2026-03-01"
    ).status_code == 200
    assert _post_entry(
        auth, hdr, entry_type="income", amount="74.50", occurred_on="2026-04-01"
    ).status_code == 200

    body = _get_reserve(auth, hdr)
    # Every one is inflow; balance is their sum.
    assert Decimal(body["balance"]) == Decimal("10600.00")
    assert body["total"] == 4
    # Direction derived and stored.
    for e in body["entries"]:
        assert e["direction"] == "inflow"
        assert e["is_reversed"] is False


def test_post_adjustment_inflow_and_outflow(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    assert _post_entry(
        auth, hdr, entry_type="opening", amount="1000.00", occurred_on="2026-01-01"
    ).status_code == 200

    # Adjustment REQUIRES an explicit direction.
    up = _post_entry(
        auth, hdr, entry_type="adjustment", amount="200.00",
        occurred_on="2026-02-01", direction="inflow",
    )
    assert up.status_code == 200, up.text
    assert up.json()["direction"] == "inflow"

    down = _post_entry(
        auth, hdr, entry_type="adjustment", amount="150.00",
        occurred_on="2026-03-01", direction="outflow",
    )
    assert down.status_code == 200, down.text
    assert down.json()["direction"] == "outflow"

    # 1000 + 200 − 150 = 1050.
    assert _balance(auth, hdr) == Decimal("1050.00")


def test_post_resale_transfer_with_house_link(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = _post_entry(
        auth, hdr, entry_type="resale_transfer", amount="5000.00",
        occurred_on="2026-05-01", description="Resale lump sum",
        source_type="house", source_id=42,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["direction"] == "inflow"
    assert body["source_type"] == "house"
    assert body["source_id"] == 42


def test_post_entry_audits_posted(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = _post_entry(
        auth, hdr, entry_type="deposit", amount="300.00", occurred_on="2026-06-01"
    )
    assert resp.status_code == 200, resp.text
    entry_id = resp.json()["id"]

    db.expire_all()
    audits = db.query(AuditLog).filter(
        AuditLog.action == "finance.reserve_entry_posted",
        AuditLog.society_id == society.id,
        AuditLog.entity_id == entry_id,
    ).all()
    assert len(audits) == 1
    assert audits[0].after["direction"] == "inflow"
    assert audits[0].after["amount"] == "300.00"
    assert audits[0].actor_user_id == admin_user.id


# ===========================================================================
# post entry — bad / edge
# ===========================================================================

def test_adjustment_without_direction_rejected(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = _post_entry(
        auth, hdr, entry_type="adjustment", amount="100.00",
        occurred_on="2026-02-01",
    )
    # Service raises ValidationError (422).
    assert resp.status_code == 422, resp.text


def test_unknown_entry_type_rejected(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    # collection is a system type, never postable directly.
    resp = _post_entry(
        auth, hdr, entry_type="collection", amount="100.00",
        occurred_on="2026-02-01",
    )
    assert resp.status_code == 422, resp.text

    bogus = _post_entry(
        auth, hdr, entry_type="not_a_type", amount="100.00",
        occurred_on="2026-02-01",
    )
    assert bogus.status_code == 422, bogus.text


def test_negative_amount_rejected(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = _post_entry(
        auth, hdr, entry_type="deposit", amount="-5.00", occurred_on="2026-02-01"
    )
    assert resp.status_code == 422, resp.text


# ===========================================================================
# reverse
# ===========================================================================

def test_reverse_manual_entry(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    assert _post_entry(
        auth, hdr, entry_type="opening", amount="1000.00", occurred_on="2026-01-01"
    ).status_code == 200
    dep = _post_entry(
        auth, hdr, entry_type="deposit", amount="400.00", occurred_on="2026-02-01"
    )
    dep_id = dep.json()["id"]
    assert _balance(auth, hdr) == Decimal("1400.00")

    rev = auth.client.post(
        f"/finance/reserve/entries/{dep_id}/reverse", headers=hdr
    )
    assert rev.status_code == 200, rev.text
    rev_body = rev.json()
    assert rev_body["entry_type"] == "reversal"
    assert rev_body["direction"] == "outflow"  # opposite of the deposit inflow
    assert Decimal(rev_body["amount"]) == Decimal("400.00")
    assert rev_body["reverses_entry_id"] == dep_id

    # Balance nets back to before the deposit.
    body = _get_reserve(auth, hdr)
    assert Decimal(body["balance"]) == Decimal("1000.00")

    # Both the original AND the reversal stay visible; original flagged reversed.
    by_id = {e["id"]: e for e in body["entries"]}
    assert by_id[dep_id]["is_reversed"] is True
    assert by_id[rev_body["id"]]["is_reversed"] is False
    assert body["total"] == 3  # opening + deposit + reversal


def test_reverse_outflow_entry_nets_up(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    assert _post_entry(
        auth, hdr, entry_type="opening", amount="1000.00", occurred_on="2026-01-01"
    ).status_code == 200
    adj = _post_entry(
        auth, hdr, entry_type="adjustment", amount="300.00",
        occurred_on="2026-02-01", direction="outflow",
    )
    adj_id = adj.json()["id"]
    assert _balance(auth, hdr) == Decimal("700.00")

    rev = auth.client.post(
        f"/finance/reserve/entries/{adj_id}/reverse", headers=hdr
    )
    assert rev.status_code == 200, rev.text
    assert rev.json()["direction"] == "inflow"  # opposite of outflow
    assert _balance(auth, hdr) == Decimal("1000.00")


def test_reverse_already_reversed_conflicts(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    dep = _post_entry(
        auth, hdr, entry_type="deposit", amount="100.00", occurred_on="2026-02-01"
    )
    dep_id = dep.json()["id"]
    assert auth.client.post(
        f"/finance/reserve/entries/{dep_id}/reverse", headers=hdr
    ).status_code == 200

    again = auth.client.post(
        f"/finance/reserve/entries/{dep_id}/reverse", headers=hdr
    )
    assert again.status_code == 409, again.text
    assert again.json()["code"] == "conflict"


def test_reverse_a_reversal_rejected(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    dep = _post_entry(
        auth, hdr, entry_type="deposit", amount="100.00", occurred_on="2026-02-01"
    )
    rev = auth.client.post(
        f"/finance/reserve/entries/{dep.json()['id']}/reverse", headers=hdr
    )
    rev_id = rev.json()["id"]
    # Reversing a reversal is not allowed.
    resp = auth.client.post(
        f"/finance/reserve/entries/{rev_id}/reverse", headers=hdr
    )
    assert resp.status_code == 422, resp.text


def test_reverse_system_collection_entry_rejected(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    # Insert a system-posted collection entry directly (as a payment flow would).
    entry = LedgerEntry(
        society_id=society.id,
        entry_type="collection",
        direction="inflow",
        amount=Decimal("250.00"),
        description="Payment settle",
        occurred_on=date(2026, 2, 1),
        source_type="payment",
        source_id=1,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)

    resp = auth.client.post(
        f"/finance/reserve/entries/{entry.id}/reverse", headers=hdr
    )
    # Collection/expense reverse via payment/expense void, not here.
    assert resp.status_code == 422, resp.text
    assert "void" in resp.json()["message"].lower()


def test_reverse_missing_entry_404(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    resp = auth.client.post(
        "/finance/reserve/entries/99999/reverse", headers=hdr
    )
    assert resp.status_code == 404, resp.text


def test_reverse_audits_reversed(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    dep = _post_entry(
        auth, hdr, entry_type="deposit", amount="100.00", occurred_on="2026-02-01"
    )
    dep_id = dep.json()["id"]
    auth.client.post(f"/finance/reserve/entries/{dep_id}/reverse", headers=hdr)

    db.expire_all()
    audits = db.query(AuditLog).filter(
        AuditLog.action == "finance.reserve_entry_reversed",
        AuditLog.society_id == society.id,
        AuditLog.entity_id == dep_id,
    ).all()
    assert len(audits) == 1
    assert "reversal_entry_id" in audits[0].after


# ===========================================================================
# reconcile
# ===========================================================================

def test_reconcile_with_positive_diff(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    assert _post_entry(
        auth, hdr, entry_type="opening", amount="1000.00", occurred_on="2026-01-01"
    ).status_code == 200

    # Bank says 1200 → +200 inflow adjustment.
    resp = auth.client.post(
        "/finance/reserve/reconcile",
        headers=hdr,
        json={"actual_balance": "1200.00", "occurred_on": "2026-02-01"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["entry_type"] == "adjustment"
    assert body["direction"] == "inflow"
    assert Decimal(body["amount"]) == Decimal("200.00")
    assert body["description"] == "Reconcile to bank"
    # New balance matches the bank exactly.
    assert _balance(auth, hdr) == Decimal("1200.00")


def test_reconcile_with_negative_diff(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    assert _post_entry(
        auth, hdr, entry_type="opening", amount="1000.00", occurred_on="2026-01-01"
    ).status_code == 200

    resp = auth.client.post(
        "/finance/reserve/reconcile",
        headers=hdr,
        json={
            "actual_balance": "900.00", "occurred_on": "2026-02-01",
            "description": "Bank shortfall",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["direction"] == "outflow"
    assert Decimal(body["amount"]) == Decimal("100.00")
    assert body["description"] == "Bank shortfall"
    assert _balance(auth, hdr) == Decimal("900.00")


def test_reconcile_zero_diff_rejected(db, society, admin_user, superadmin, auth):
    """Zero diff is NOT a phantom entry — the service returns a clean 422.

    Spec deviation choice (Wave E): the contract stays "an adjustment is posted
    only when there's a difference", so a no-op reconcile surfaces a
    ValidationError rather than creating a zero-amount ledger row.
    """
    hdr = _setup(db, society, admin_user, superadmin, auth)
    assert _post_entry(
        auth, hdr, entry_type="opening", amount="1000.00", occurred_on="2026-01-01"
    ).status_code == 200

    resp = auth.client.post(
        "/finance/reserve/reconcile",
        headers=hdr,
        json={"actual_balance": "1000.00", "occurred_on": "2026-02-01"},
    )
    assert resp.status_code == 422, resp.text
    assert "no difference" in resp.json()["message"].lower()

    # No adjustment was posted — the ledger is untouched.
    body = _get_reserve(auth, hdr)
    assert body["total"] == 1


def test_reconcile_audits_reconciled(db, society, admin_user, superadmin, auth):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _post_entry(
        auth, hdr, entry_type="opening", amount="1000.00", occurred_on="2026-01-01"
    )
    resp = auth.client.post(
        "/finance/reserve/reconcile",
        headers=hdr,
        json={"actual_balance": "1200.00", "occurred_on": "2026-02-01"},
    )
    entry_id = resp.json()["id"]

    db.expire_all()
    audits = db.query(AuditLog).filter(
        AuditLog.action == "finance.reserve_reconciled",
        AuditLog.society_id == society.id,
        AuditLog.entity_id == entry_id,
    ).all()
    assert len(audits) == 1
    assert audits[0].before == {"computed": "1000.00"}
    assert audits[0].after["actual"] == "1200.00"
    assert audits[0].after["adjustment"] == "200.00"


# ===========================================================================
# security + tenant isolation
# ===========================================================================

def test_post_entry_requires_manage_reserve(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _strip_permission(db, society, _RESERVE_PERM)
    resp = _post_entry(
        auth, hdr, entry_type="deposit", amount="100.00", occurred_on="2026-02-01"
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["details"]["required_permission"] == _RESERVE_PERM


def test_reverse_requires_manage_reserve(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    dep = _post_entry(
        auth, hdr, entry_type="deposit", amount="100.00", occurred_on="2026-02-01"
    )
    dep_id = dep.json()["id"]
    _strip_permission(db, society, _RESERVE_PERM)
    resp = auth.client.post(
        f"/finance/reserve/entries/{dep_id}/reverse", headers=hdr
    )
    assert resp.status_code == 403, resp.text


def test_reconcile_requires_manage_reserve(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _strip_permission(db, society, _RESERVE_PERM)
    resp = auth.client.post(
        "/finance/reserve/reconcile",
        headers=hdr,
        json={"actual_balance": "1200.00", "occurred_on": "2026-02-01"},
    )
    assert resp.status_code == 403, resp.text


def test_read_reserve_requires_read_perm(
    db, society, admin_user, superadmin, auth
):
    hdr = _setup(db, society, admin_user, superadmin, auth)
    _strip_permission(db, society, _READ_PERM)
    resp = auth.client.get("/finance/reserve", headers=hdr)
    assert resp.status_code == 403, resp.text


def test_cross_society_ledger_isolation(db, society, admin_user, superadmin, auth):
    hdr_a = _setup(db, society, admin_user, superadmin, auth)
    soc_b, admin_b = _second_society(db, superadmin)
    hdr_b = _admin_bearer(auth, admin_b)

    # A posts a large opening; B posts a small one.
    assert _post_entry(
        auth, hdr_a, entry_type="opening", amount="9000.00", occurred_on="2026-01-01"
    ).status_code == 200
    assert _post_entry(
        auth, hdr_b, entry_type="opening", amount="100.00", occurred_on="2026-01-01"
    ).status_code == 200

    # Each society sees only its own balance + entries.
    body_a = _get_reserve(auth, hdr_a)
    body_b = _get_reserve(auth, hdr_b)
    assert Decimal(body_a["balance"]) == Decimal("9000.00")
    assert Decimal(body_b["balance"]) == Decimal("100.00")
    assert body_a["total"] == 1
    assert body_b["total"] == 1

    # B cannot reverse A's entry (scoped lookup → 404, not found in B's tenant).
    a_entry_id = body_a["entries"][0]["id"]
    resp = auth.client.post(
        f"/finance/reserve/entries/{a_entry_id}/reverse", headers=hdr_b
    )
    assert resp.status_code == 404, resp.text
