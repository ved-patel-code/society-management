"""End-to-end tests for the Vault module: ID-proof integration + background jobs."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text

from app.common.time import utcnow
from app.platform.societies.schemas import ModuleAllocation
from app.platform.societies.service import SocietyService
from tests._houses_helpers import _make_building_with_houses, _owner
from tests._vault_helpers import (  # noqa: F401
    _admin_bearer,
    _audit,
    _contents,
    _create_folder,
    _setup,
    _upload,
    storage_override,
)

pytestmark = pytest.mark.usefixtures("storage_override")


def _enable_onb_vault_houses(db, society, superadmin):
    SocietyService(db).set_modules(
        society.id,
        [
            ModuleAllocation(module_key="onboarding", enabled=True, config={}),
            ModuleAllocation(module_key="vault", enabled=True, config={}),
            ModuleAllocation(module_key="houses", enabled=True, config={}),
        ],
        actor_user_id=superadmin.id,
    )
    db.commit()


def test_full_admin_journey_id_proof(db, society, admin_user, superadmin, auth):
    _enable_onb_vault_houses(db, society, superadmin)
    hdr = _admin_bearer(auth, admin_user)
    houses = _make_building_with_houses(auth, hdr)
    house = houses[0]
    hid = house["id"]

    resp = auth.client.post(
        f"/houses/{hid}/status",
        headers=hdr,
        json={"to_status": "owned", "owner": _owner(persons_living=1)},
    )
    assert resp.status_code == 200, resp.text

    resp2 = auth.client.post(
        f"/houses/{hid}/occupancy/owner/id-proof",
        headers=hdr,
        files={"file": ("idproof.jpg", b"x" * 30, "image/jpeg")},
    )
    assert resp2.status_code == 200, resp2.text
    house_detail = resp2.json()

    root = _contents(auth, hdr, None)
    houses_root = next(f for f in root["folders"] if f["system_key"] == "houses_root")
    house_body = _contents(auth, hdr, houses_root["id"])
    house_folder = next(f for f in house_body["folders"] if f["system_key"] == "house")
    assert house_folder["name"] == "A-101"
    proof_body = _contents(auth, hdr, house_folder["id"])
    proof_folder = next(f for f in proof_body["folders"] if f["system_key"] == "house_proof")
    assert proof_folder["name"] == "Proof"

    docs_body = _contents(auth, hdr, proof_folder["id"])
    assert len(docs_body["documents"]) == 1
    doc = docs_body["documents"][0]
    assert doc["source"] == "id_proof"

    from app.modules.houses.models import HouseOccupancy

    occ = db.execute(
        text(
            "SELECT id, id_proof_document_id FROM house_occupancies "
            "WHERE house_id=:h AND party_type='owner' AND is_current=true"
        ),
        {"h": hid},
    ).one()
    assert doc["source_ref"] == occ.id
    assert occ.id_proof_document_id == doc["id"]

    usage = auth.client.get("/vault/usage", headers=hdr).json()
    assert usage["used_bytes"] == 30

    preview = auth.client.get(f"/vault/documents/{doc['id']}/preview", headers=hdr)
    assert preview.status_code == 200, preview.text


def test_id_proof_requires_vault_enabled(db, society, admin_user, superadmin, auth):
    from tests._houses_helpers import _enable_houses

    _enable_houses(db, society, superadmin)
    hdr = _admin_bearer(auth, admin_user)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    resp = auth.client.post(
        f"/houses/{hid}/status", headers=hdr, json={"to_status": "owned", "owner": _owner(persons_living=1)}
    )
    assert resp.status_code == 200, resp.text
    resp2 = auth.client.post(
        f"/houses/{hid}/occupancy/owner/id-proof",
        headers=hdr,
        files={"file": ("idproof.jpg", b"x", "image/jpeg")},
    )
    assert resp2.status_code == 403
    assert resp2.json()["details"]["module_key"] == "vault"


def test_house_folder_derived_name_rename_safe(db, society, admin_user, superadmin, auth):
    _enable_onb_vault_houses(db, society, superadmin)
    hdr = _admin_bearer(auth, admin_user)
    houses = _make_building_with_houses(auth, hdr)
    house = houses[0]
    hid = house["id"]
    building_id = house["building_id"]

    auth.client.post(
        f"/houses/{hid}/status", headers=hdr, json={"to_status": "owned", "owner": _owner(persons_living=1)}
    )
    auth.client.post(
        f"/houses/{hid}/occupancy/owner/id-proof",
        headers=hdr,
        files={"file": ("idproof.jpg", b"x", "image/jpeg")},
    )

    resp = auth.client.patch(
        f"/onboarding/buildings/{building_id}", headers=hdr, json={"name": "Z"}
    )
    assert resp.status_code == 200, resp.text

    root = _contents(auth, hdr, None)
    houses_root = next(f for f in root["folders"] if f["system_key"] == "houses_root")
    house_body = _contents(auth, hdr, houses_root["id"])
    house_folder = next(f for f in house_body["folders"] if f["system_key"] == "house")
    assert house_folder["name"] == "Z-101"


def test_second_id_proof_reuses_proof_folder(db, society, admin_user, superadmin, auth):
    _enable_onb_vault_houses(db, society, superadmin)
    hdr = _admin_bearer(auth, admin_user)
    houses = _make_building_with_houses(auth, hdr)
    hid = houses[0]["id"]
    auth.client.post(
        f"/houses/{hid}/status", headers=hdr, json={"to_status": "owned", "owner": _owner(persons_living=1)}
    )
    auth.client.post(
        f"/houses/{hid}/occupancy/owner/id-proof",
        headers=hdr,
        files={"file": ("idproof1.jpg", b"x", "image/jpeg")},
    )
    auth.client.post(
        f"/houses/{hid}/occupancy/owner/id-proof",
        headers=hdr,
        files={"file": ("idproof2.jpg", b"y", "image/jpeg")},
    )

    root = _contents(auth, hdr, None)
    houses_root = next(f for f in root["folders"] if f["system_key"] == "houses_root")
    house_body = _contents(auth, hdr, houses_root["id"])
    house_folders = [f for f in house_body["folders"] if f["system_key"] == "house"]
    assert len(house_folders) == 1
    proof_body = _contents(auth, hdr, house_folders[0]["id"])
    proof_folders = [f for f in proof_body["folders"] if f["system_key"] == "house_proof"]
    assert len(proof_folders) == 1
    docs = _contents(auth, hdr, proof_folders[0]["id"])["documents"]
    assert len(docs) == 2


def test_purge_job_drops_row_and_frees_usage(db, society, admin_user, superadmin, auth, storage_override):
    from app.modules.vault.services.jobs import purge_trash

    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    doc = _upload(auth, hdr, bills["id"], data=b"x" * 40)
    from app.modules.vault.models import VaultDocument

    storage_key = db.get(VaultDocument, doc["id"]).storage_key
    auth.client.delete(f"/vault/documents/{doc['id']}", headers=hdr)

    backdated = utcnow() - timedelta(days=40)
    db.execute(
        text("UPDATE vault_documents SET deleted_at=:d WHERE id=:i"),
        {"d": backdated, "i": doc["id"]},
    )
    db.commit()

    result = purge_trash()
    db.expire_all()

    assert result["documents_purged"] >= 1
    assert result["bytes_freed"] == 40
    assert db.get(VaultDocument, doc["id"]) is None
    assert not storage_override.exists(storage_key)
    usage = auth.client.get("/vault/usage", headers=hdr).json()
    assert usage["used_bytes"] == 0
    records = _audit(db, "vault.trash_purged", society_id=society.id)
    assert len(records) >= 1
    assert records[-1].actor_user_id is None


def test_purge_respects_retention(db, society, admin_user, superadmin, auth):
    from app.modules.vault.services.jobs import purge_trash

    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    doc = _upload(auth, hdr, bills["id"], data=b"x" * 15)
    auth.client.delete(f"/vault/documents/{doc['id']}", headers=hdr)

    purge_trash()
    db.expire_all()

    trash = auth.client.get("/vault/trash", headers=hdr).json()
    assert any(i["id"] == doc["id"] for i in trash)
    usage = auth.client.get("/vault/usage", headers=hdr).json()
    assert usage["used_bytes"] == 15


def test_purge_cascaded_folder_subtree(db, society, admin_user, superadmin, auth, storage_override):
    from app.modules.vault.services.jobs import purge_trash

    hdr = _setup(db, society, admin_user, superadmin, auth)
    a = _create_folder(auth, hdr, "A")
    b = _create_folder(auth, hdr, "B", parent_id=a["id"])
    doc = _upload(auth, hdr, b["id"], data=b"x" * 12)
    auth.client.delete(f"/vault/folders/{a['id']}", headers=hdr)

    backdated = utcnow() - timedelta(days=40)
    db.execute(
        text("UPDATE vault_folders SET deleted_at=:d WHERE society_id=:s"),
        {"d": backdated, "s": society.id},
    )
    db.execute(
        text("UPDATE vault_documents SET deleted_at=:d WHERE society_id=:s"),
        {"d": backdated, "s": society.id},
    )
    db.commit()

    result = purge_trash()
    db.expire_all()

    from app.modules.vault.models import VaultDocument, VaultFolder

    assert db.get(VaultFolder, a["id"]) is None
    assert db.get(VaultFolder, b["id"]) is None
    assert db.get(VaultDocument, doc["id"]) is None
    assert result["bytes_freed"] == 12


def test_reconcile_corrects_wrong_used_bytes(db, society, admin_user, superadmin, auth):
    from app.modules.vault.services.jobs import reconcile_usage

    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    _upload(auth, hdr, bills["id"], data=b"x" * 25)

    db.execute(
        text("UPDATE society_storage_usage SET used_bytes=999 WHERE society_id=:s"),
        {"s": society.id},
    )
    db.commit()

    result = reconcile_usage()
    db.expire_all()

    assert result["corrections"] >= 1
    usage = auth.client.get("/vault/usage", headers=hdr).json()
    assert usage["used_bytes"] == 25


def test_reconcile_counts_trashed_bytes(db, society, admin_user, superadmin, auth):
    from app.modules.vault.services.jobs import reconcile_usage

    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    doc = _upload(auth, hdr, bills["id"], data=b"x" * 25)
    auth.client.delete(f"/vault/documents/{doc['id']}", headers=hdr)

    db.execute(
        text("UPDATE society_storage_usage SET used_bytes=0 WHERE society_id=:s"),
        {"s": society.id},
    )
    db.commit()

    reconcile_usage()
    db.expire_all()

    usage = auth.client.get("/vault/usage", headers=hdr).json()
    assert usage["used_bytes"] == 25


def test_reconcile_sweeps_orphan_object(db, society, admin_user, superadmin, auth, storage_override):
    from app.modules.vault.services.jobs import reconcile_usage
    from app.modules.vault.models import VaultDocument

    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    doc = _upload(auth, hdr, bills["id"], data=b"x" * 20)
    referenced_key = db.get(VaultDocument, doc["id"]).storage_key

    # Simulate a crash between put_object and commit: an object with no backing
    # row, under this society's prefix. reconcile_usage() opens its own session
    # and calls get_storage() — the process-global override is the SAME instance
    # as storage_override, so the sweep sees this key.
    orphan_key = f"societies/{society.id}/999999__orphan.pdf"
    storage_override.put_object(orphan_key, b"x", "application/pdf")

    result = reconcile_usage()
    db.expire_all()

    assert result["orphans_deleted"] >= 1
    assert storage_override.get(orphan_key) is None
    assert orphan_key not in storage_override.list_keys(f"societies/{society.id}/")
    # The referenced document's object must NOT be swept.
    assert storage_override.exists(referenced_key)


def test_reconcile_keeps_referenced_objects(db, society, admin_user, superadmin, auth, storage_override):
    from app.modules.vault.services.jobs import reconcile_usage
    from app.modules.vault.models import VaultDocument

    hdr = _setup(db, society, admin_user, superadmin, auth)
    bills = _create_folder(auth, hdr, "Bills")
    doc1 = _upload(auth, hdr, bills["id"], filename="a.pdf", data=b"x" * 10)
    doc2 = _upload(auth, hdr, bills["id"], filename="b.pdf", data=b"x" * 12)
    key1 = db.get(VaultDocument, doc1["id"]).storage_key
    key2 = db.get(VaultDocument, doc2["id"]).storage_key

    result = reconcile_usage()
    db.expire_all()

    assert result["orphans_deleted"] == 0
    assert storage_override.exists(key1)
    assert storage_override.exists(key2)
