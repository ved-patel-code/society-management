"""Tests for Complaints CONFIG (Wave E) — docs/modules/complaints.md §4/§6/§8.

Wave E implements ``ConfigService``: ``GET /complaints/config`` (read, defaulted)
and ``PUT /complaints/config`` (PARTIAL MERGE — only the provided keys change).
Covers the defaults on a fresh society, the partial-merge proof (a single-field
PUT leaves the other keys untouched), a full three-field update, the schema-bound
422s (out-of-range / negative / above-ceiling values, and the empty-body 422),
permission gating (both routes require ``complaints.configure`` -> 403 without it),
tenant isolation (A's change never touches B), and the audited before/after row.

Enabling complaints seeds ``society_admin`` all five admin ``complaints.*`` perms
(incl. ``configure``) and ``resident`` -> ``create`` + ``read`` only (spec.py) — so
the resident is the permission-less caller for the 403 checks.
"""
from __future__ import annotations

from app.platform.models import AuditLog

from tests._complaints_helpers import (
    resident_bearer,
    second_society_with_complaints,
    setup_complaints,
)

# Documented defaults (docs §8): auto_archive_days=15, both image caps=2.
DEFAULT_CONFIG = {
    "auto_archive_days": 15,
    "max_report_images": 2,
    "max_proof_images": 2,
}


# --- small HTTP helpers ------------------------------------------------------


def _get(auth, hdr):
    return auth.client.get("/complaints/config", headers=hdr)


def _put(auth, hdr, **body):
    return auth.client.put("/complaints/config", headers=hdr, json=body)


def _config_audits(db, society_id) -> list[AuditLog]:
    return (
        db.query(AuditLog)
        .filter(
            AuditLog.society_id == society_id,
            AuditLog.action == "complaints.config_updated",
        )
        .order_by(AuditLog.id)
        .all()
    )


# ===========================================================================
# GET: defaults on a fresh society
# ===========================================================================


def test_get_returns_defaults(db, society, admin_user, superadmin, auth):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    resp = _get(auth, hdr)
    assert resp.status_code == 200, resp.text
    assert resp.json() == DEFAULT_CONFIG


# ===========================================================================
# PUT: partial-merge proof — a single-field update leaves the rest untouched
# ===========================================================================


def test_partial_update_preserves_other_keys(
    db, society, admin_user, superadmin, auth
):
    """The PARTIAL MERGE: setting only auto_archive_days keeps both image caps."""
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)

    resp = _put(auth, hdr, auto_archive_days=30)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "auto_archive_days": 30,
        "max_report_images": 2,
        "max_proof_images": 2,
    }

    # A fresh GET proves the image caps were NOT reset to their defaults by the
    # single-field write — they keep their (still-default) current value.
    after = _get(auth, hdr)
    assert after.status_code == 200, after.text
    assert after.json() == {
        "auto_archive_days": 30,
        "max_report_images": 2,
        "max_proof_images": 2,
    }


def test_partial_update_over_custom_values_preserves_them(
    db, society, admin_user, superadmin, auth
):
    """Merge proof with non-default neighbours: change one, the others persist."""
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)

    # First set all three to non-defaults.
    assert _put(
        auth, hdr, auto_archive_days=20, max_report_images=1, max_proof_images=3
    ).status_code == 200

    # Now change ONLY the report cap; the other two must survive the merge.
    resp = _put(auth, hdr, max_report_images=4)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "auto_archive_days": 20,
        "max_report_images": 4,
        "max_proof_images": 3,
    }
    assert _get(auth, hdr).json() == {
        "auto_archive_days": 20,
        "max_report_images": 4,
        "max_proof_images": 3,
    }


# ===========================================================================
# PUT: full three-field update
# ===========================================================================


def test_full_update_sets_all_three(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    resp = _put(
        auth, hdr, auto_archive_days=45, max_report_images=5, max_proof_images=0
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "auto_archive_days": 45,
        "max_report_images": 5,
        "max_proof_images": 0,
    }
    assert _get(auth, hdr).json() == body


# ===========================================================================
# PUT: schema-bound validation (422) + empty-body (422)
# ===========================================================================


def test_out_of_bounds_values_rejected(
    db, society, admin_user, superadmin, auth
):
    """Bounds live on the request schema: below MIN / above MAX / negative -> 422."""
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)

    # auto_archive_days out of [1, 365].
    assert _put(auth, hdr, auto_archive_days=0).status_code == 422
    assert _put(auth, hdr, auto_archive_days=500).status_code == 422

    # image caps: negative and above the 10 ceiling.
    assert _put(auth, hdr, max_report_images=-1).status_code == 422
    assert _put(auth, hdr, max_proof_images=-1).status_code == 422
    assert _put(auth, hdr, max_report_images=11).status_code == 422
    assert _put(auth, hdr, max_proof_images=11).status_code == 422

    # None of the rejected writes leaked — config is still the defaults.
    assert _get(auth, hdr).json() == DEFAULT_CONFIG
    assert _config_audits(db, society.id) == []


def test_empty_body_rejected(db, society, admin_user, superadmin, auth):
    """No field provided -> nothing to update -> 422 (service guard)."""
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    resp = _put(auth, hdr)
    assert resp.status_code == 422, resp.text

    # Config unchanged and no audit row written.
    assert _get(auth, hdr).json() == DEFAULT_CONFIG
    assert _config_audits(db, society.id) == []


# ===========================================================================
# permission gating: both routes require complaints.configure
# ===========================================================================


def test_configure_permission_required_on_get_and_put(
    db, society, admin_user, resident_user, superadmin, auth
):
    """resident lacks complaints.configure -> 403 on BOTH GET and PUT (docs §6)."""
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)
    res_hdr = resident_bearer(auth, resident_user)

    assert _get(auth, res_hdr).status_code == 403
    assert _put(auth, res_hdr, auto_archive_days=30).status_code == 403

    # The blocked PUT changed nothing (admin still sees the defaults).
    assert _get(auth, hdr).json() == DEFAULT_CONFIG
    assert _config_audits(db, society.id) == []


# ===========================================================================
# tenant isolation: A's config change never touches B
# ===========================================================================


def test_tenant_isolation_between_societies(
    db, society, admin_user, superadmin, auth
):
    hdr_a = setup_complaints(db, society, admin_user, superadmin, auth)
    soc_b, _admin_b, hdr_b = second_society_with_complaints(db, superadmin, auth)

    # Change A's config only.
    assert _put(
        auth, hdr_a, auto_archive_days=90, max_report_images=5
    ).status_code == 200

    # A reflects the change; B is still at the untouched defaults.
    assert _get(auth, hdr_a).json() == {
        "auto_archive_days": 90,
        "max_report_images": 5,
        "max_proof_images": 2,
    }
    assert _get(auth, hdr_b).json() == DEFAULT_CONFIG

    # A's audit trail carries the config event; B's has none.
    a_audits = _config_audits(db, society.id)
    assert len(a_audits) == 1
    assert _config_audits(db, soc_b.id) == []


# ===========================================================================
# audit: complaints.config_updated with full before/after
# ===========================================================================


def test_update_audits_before_after(
    db, society, admin_user, superadmin, auth
):
    hdr = setup_complaints(db, society, admin_user, superadmin, auth)

    resp = _put(auth, hdr, auto_archive_days=30, max_proof_images=1)
    assert resp.status_code == 200, resp.text

    audits = _config_audits(db, society.id)
    assert len(audits) == 1
    row = audits[0]
    assert row.entity_type == "society_module"
    assert row.entity_id == society.id
    assert row.actor_user_id == admin_user.id
    # Full three-key before/after (not just the changed keys), so the row is
    # self-describing; the untouched key carries its unchanged value.
    assert row.before == {
        "auto_archive_days": 15,
        "max_report_images": 2,
        "max_proof_images": 2,
    }
    assert row.after == {
        "auto_archive_days": 30,
        "max_report_images": 2,
        "max_proof_images": 1,
    }
