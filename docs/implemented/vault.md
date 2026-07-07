# Vault (Module 3) — As-Built Index

> Lean navigation index (docs/04). Points to code; not a copy of it. Design source
> of truth: `docs/modules/vault.md`. Build/QA record: `docs/build-log/vault/`.

## Status
**COMPLETE** — built, code-reviewed, tested. Full suite **521 passing, 1 skipped**
(150 Vault tests across smoke + happy + badpaths + edge + security + vulnerabilities
+ e2e + real-MinIO), on branch `feat/vault`. Third toggleable feature module:
per-society admin-only file-manager on MinIO with a storage quota and Trash; also
the physical home for cross-module files (ID proofs now; complaint images / notice
attachments when those modules build). Implements the foundation `ObjectStorage`
interface and wires the deferred House & Occupancy ID-proof FK + upload path.

## File map
Module package `app/modules/vault/`:
- `models.py` — 3 tables: `vault_folders` (self-ref tree, `is_system`+`system_key`,
  `house_id`/`notice_id` links, soft-delete), `vault_documents` (`storage_key`,
  `source`/`source_ref`, soft-delete + `deleted_by`), `society_storage_usage`
  (per-society byte total).
- `schemas.py` — frozen Pydantic contracts (folder/document/trash/usage) + domains
  (sources, system keys, denylist default, retention days).
- `repository.py` — SQL-only, `society_id`-scoped; folder/document/trash queries,
  usage (`get_or_create_usage(lock=True)` for the quota path), `sum_all_document_bytes`.
- `service.py` — thin `VaultService` facade over the concern-split internals.
- `services/folders.py` — folder tree (create/rename/move/delete, cycle-guarded,
  system-folder protection, cascade soft-delete), `ensure_house_folder` /
  `ensure_notice_folder`, rename-safe display-name derivation, contents+breadcrumb.
- `services/documents.py` — atomic upload (denylist 415, quota 413, collision
  auto-rename, sha256, storage-key-from-id), presigned preview/download, DB-only
  rename/move, soft-delete.
- `services/trash.py` — restore (subtree + ancestor rehydration, looped
  collision-suffix), empty-trash (permanent delete + usage decrement), usage read.
- `services/jobs.py` — worker jobs `purge_trash` (daily) + `reconcile_usage`
  (nightly); own `SessionLocal`, per-folder flush for FK-safe deletes.
- `api.py` — public inter-module contract (`store_document`, `ensure_house_folder`,
  `ensure_notice_folder`, `get_preview_url`/`get_download_url`, `usage`).
- `router.py` — thin `/vault/*` routes, dual-gated `require_module('vault')` + perm.
- `spec.py` — `VAULT_SPEC` (`vault.read`/`vault.manage`), `depends_on: ['onboarding']`,
  `default_role_permissions`, denylist + retention `default_config`, `register_vault`.
- `alembic/versions/0004_vault.py` — migration (chained off `0003_house_occupancy`);
  creates the 3 tables and adds the `house_occupancies.id_proof_document_id →
  vault_documents.id` FK (`ON DELETE SET NULL`).

Storage layer `app/core/storage/`:
- `__init__.py` — `ObjectStorage` ABC (extended: `presigned_get_url` inline/
  attachment + TTL, `delete_object`).
- `minio_storage.py` — `MinIOStorage`: lazy bucket bootstrap, put/delete, presigned
  GET signed against a separate public-endpoint client (host in signature matches
  the browser-reachable URL); `region` pinned to avoid an unreachable
  `GetBucketLocation` round-trip.
- `memory_storage.py` — in-memory `ObjectStorage` fake for tests.
- `provider.py` — `get_storage()` (cached MinIO singleton) + test override seam.

Consumer wiring (House & Occupancy):
- `app/modules/houses/{router,service}.py` — `POST /houses/{id}/occupancy/{party}/
  id-proof` (multipart), dual-gated on `houses`+`vault`; calls the vault api to file
  the proof under `Houses/<code>/Proof` and set `id_proof_document_id`.
- `app/modules/houses/models.py` — `id_proof_document_id` now declares the FK.
- `app/worker/entrypoint.py`, `app/main.py`, `alembic/env.py`, `app/core/config.py`
  (`MINIO_BUCKET`/`MINIO_PUBLIC_ENDPOINT`), `docker-compose.yml` (pinned MinIO image).

## Functions (summary · deps · @location)
- `FolderService.create/update/delete_folder` — tree ops; in-service name-collision
  for root AND nested; cycle-guarded move; system-folder guard; cascade soft-delete
  of the subtree. @ vault/services/folders.py
- `FolderService.ensure_house_folder / ensure_notice_folder` — idempotent
  auto-create of the `Houses/<code>/Proof|Complaints` and `Notices/<id>` system
  chains, house label DERIVED from the onboarding display code (rename-safe). @ folders.py
- `DocumentService.upload` — atomic denylist+quota+put+row+usage in one txn; quota
  row locked (`FOR UPDATE`). @ vault/services/documents.py
- `DocumentService.preview_url / download_url` — society-scoped authorize → short-TTL
  presigned GET (inline vs attachment). @ documents.py
- `TrashService.restore / empty_trash` — subtree+ancestor rehydration with looped
  collision suffix; permanent delete + usage decrement. @ vault/services/trash.py
- `purge_trash / reconcile_usage` — worker jobs (own session/commit). @ services/jobs.py
- `vault.api.store_document / ensure_house_folder / get_preview_url / ...` —
  cross-module contract other modules import. @ vault/api.py
- `HouseService.set_id_proof` — files an ID-proof via the vault api + sets the FK. @ houses/service.py

## Tables owned
`vault_folders`, `vault_documents`, `society_storage_usage`. (Adds the FK on
`house_occupancies.id_proof_document_id`, a column owned by House & Occupancy.)

## Endpoints
`GET /vault/folders/contents` (root) · `GET /vault/folders/{id}/contents`
(subfolders+documents+breadcrumb, paginated) · `POST /vault/folders` ·
`PATCH /vault/folders/{id}` (rename/move) · `DELETE /vault/folders/{id}` (→Trash) ·
`POST /vault/documents` (multipart upload) · `GET /vault/documents/{id}/preview` ·
`GET /vault/documents/{id}/download` · `PATCH /vault/documents/{id}` (rename/move) ·
`DELETE /vault/documents/{id}` (→Trash) · `GET /vault/trash` ·
`POST /vault/trash/{folders|documents}/{id}/restore` · `POST /vault/trash/empty` ·
`GET /vault/usage`. Plus `POST /houses/{id}/occupancy/{party}/id-proof` (consumer).
All gated `require_module('vault')` + permission (reads `vault.read`; mutations
`vault.manage`). Society always from the JWT, never a path/body id.

## Audited actions (emitted)
`vault.folder_created` / `folder_renamed` / `folder_moved` / `folder_deleted` ·
`vault.document_uploaded` / `document_renamed` / `document_moved` / `document_deleted`
· `vault.item_restored` · `vault.trash_emptied` · `vault.trash_purged` (worker,
actor=system) · `house.id_proof_uploaded` (consumer).

## Cross-module wiring
- **Consumes:** foundation `ObjectStorage`(MinIO) + `AuditService` + `TenantContext`;
  `societies.storage_limit_bytes`; Onboarding house registry (display code for
  folder labels); the worker (purge + reconcile jobs).
- **Provides:** `store_document`, `ensure_house_folder(kind=proof|complaints)`,
  `ensure_notice_folder`, `get_preview_url`/`get_download_url`, `usage` — consumed
  by House & Occupancy (ID proofs, WIRED) and, when built, Complaints (images) and
  Notice Board (attachments).
- **Deferred wiring (skeleton-then-wire):** `vault_folders.notice_id` is a nullable
  BIGINT with no FK yet — the Notice Board migration adds the FK to `notices`.

## Testing
Reuses the shared harness (`backend/tests/`): isolated per-worker `society_test`
DBs, truncate+reseed, fixtures + `tests/_vault_helpers.py`. Bulk tests use an
in-memory `ObjectStorage` fake (parallel-safe); `test_vault_storage_realminio.py`
exercises real MinIO. Run: `docker compose exec backend bash scripts/run-tests.sh`.
521 pass, 1 skip (public-endpoint reachability guard).

## Deviations from design (drift vs docs/modules/vault.md)
1. **Interface name** — the doc calls the storage interface `StorageBackend`; the
   code keeps the foundation name `ObjectStorage` (same interface).
2. **`society_storage_usage` PK** — uses the standard `Base` synthetic `id` PK plus a
   UNIQUE on `society_id` (the doc says `society_id` PK); functionally equivalent and
   consistent with every other table.
3. **Upload collision** — auto-renames (`report (1).pdf`) rather than rejecting, for
   a file-manager UX (docs left this open).
4. **MinIO server image pinned** — `docker-compose.yml` pins a vetted release rather
   than `:latest`, for reproducible builds.

Everything else matches `docs/modules/vault.md`.
