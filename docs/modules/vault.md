# Vault Module — Design

> Design doc. Foundation reading: [../01-project-overview](../01-project-overview.md) · [../02-architecture](../02-architecture.md) · [../03-backend-and-db-principles](../03-backend-and-db-principles.md) · [../05-cross-module-contracts](../05-cross-module-contracts.md) · [../platform/platform-foundation](../platform/platform-foundation.md)
>
> **Confirmed decisions baked in:** file-manager **unlimited nesting**, subfolders allowed inside system folders · **Trash** (soft-delete → auto-purge after 30 days + manual Empty Trash; trashed items still count toward quota until purged) · allow **all file types EXCEPT executable/dangerous** (denylist) · **default 5 GB/society** (super_admin overrides) · **no per-file cap** — reject only if it exceeds available storage · **inline preview PDF + images**, others download/open-in-app · **search deferred** · **house-centric layout**: `Houses/<house>/Proof` + `Houses/<house>/Complaints`, auto-created on first use, system-managed root · admin-only · MinIO storage.

## 1. Purpose & scope
Per-society document storage with a **file-manager** experience: nested folders, upload, preview, download, rename/move, Trash. Admin-only. Enforces a per-society **GB limit**. Also the physical home for **ID-proof images** (House & Occupancy) and **complaint images** (Complaints), auto-filed under each house's folder, and **notice attachments** (Notice Board), auto-filed under a society-level `Notices/<notice>` folder.

**Out of scope (future):** antivirus scanning, filename search, file versioning, resident access / shareable links, inline preview of office docs.

**Mobile:** backend-first, but the API is shaped for phone navigation — folder-contents listing returns a **breadcrumb path** + folder/file entries so the frontend can render a simple drill-down.

## 2. Audience & permissions
- **society_admin** only. Residents have **no** vault access (complaint/ID images are surfaced through their own modules, not the vault).
- Permissions (`vault.*`): `vault.read` (browse, preview, download, usage, trash view), `vault.manage` (create/rename/move/delete folders & files, upload, restore, empty trash).
- Gated `require_module('vault')` + `require_permission(...)`.

## 3. Data model
`id` BIGINT PK, `created_at`, `updated_at`, `society_id`. DB holds PK/FK/NOT NULL/UNIQUE only.

**vault_folders** — `society_id` FK, `parent_id` FK NULL (NULL = vault root), `name`, `is_system` BOOL (protected: not renamable/deletable), `system_key` NULL|`houses_root`|`house`|`house_proof`|`house_complaints`|`notices_root`|`notice`, `house_id` FK NULL (set on house/proof/complaints folders — links by id so a building rename never desyncs; **display name derived** from the house's current display code), `notice_id` FK NULL (set on per-notice folders — links by id), `created_by`, `deleted_at` NULL (Trash).
- Partial UNIQUE(`society_id`,`parent_id`,`name`) WHERE `deleted_at IS NULL`; idx(`society_id`,`parent_id`).

**vault_documents** — `society_id` FK, `folder_id` FK, `filename`, `content_type`, `size_bytes` BIGINT, `storage_key` (MinIO object key), `checksum`, `source`(manual|id_proof|complaint|notice), `source_ref` (occupancy/complaint/notice id), `uploaded_by`, `deleted_at` NULL, `deleted_by`.
- idx(`society_id`,`folder_id`) WHERE `deleted_at IS NULL`; idx(`deleted_at`) for the purge job.

**society_storage_usage** — `society_id` PK, `used_bytes` BIGINT, `updated_at`. (Counts live **and** trashed bytes until permanent delete.)

**Storage keys (MinIO):** `societies/{society_id}/{document_id}__{filename}` — keyed by document id so **rename/move is DB-only** (folder tree + name change), object untouched. `StorageBackend` interface (MinIO impl now, swappable to S3).

## 4. Business rules
**Folder tree:** unlimited nesting. Root holds admin custom folders (Bills, Property Records, …) + the system `Houses` root + the system `Notices` root. Creating/renaming/moving folders = DB tree ops. **System folders** (`Houses` root, per-house folders and their `Proof`/`Complaints` subfolders, the `Notices` root and its per-notice subfolders) are **not renamable/deletable**; the admin may add their **own** subfolders anywhere, including inside system folders.

**House folders (auto):** on the first file for a house, create `Houses/<house>/` + the needed `Proof/` or `Complaints/` subfolder on demand (system-managed, linked by `house_id`). ID proofs → `Proof/`; complaint images → `Complaints/<complaint ref>/`.

**Notice folders (auto):** on the first attachment for a notice, create `Notices/<notice id>/` on demand (system-managed, linked by `notice_id`). Notice attachments are **society-level** (not under any house).

**Upload:** backend-proxied (multipart) so **type + quota are enforced atomically**. Reject if `content_type`/extension is on the **denylist** (`.exe .dll .bat .cmd .com .scr .msi .sh .js .jar .ps1` …, configurable) → 415. Reject if `used_bytes + size > storage_limit_bytes` → 413. On success: store object, insert document, `used_bytes += size` in the same transaction.

**Preview/download:** short-TTL **presigned GET** URLs from MinIO (backend authorizes first, never proxies bytes). PDF + images → inline; everything else → download/open-in-app.

**Trash & quota:**
- Delete (folder or file) = **soft-delete** (`deleted_at`). Deleting a folder cascades soft-delete to its subtree. Trashed items **still count** toward `used_bytes`.
- **Restore** clears `deleted_at` (restores the subtree to its original location; if a parent is gone, restore the parent chain or land at root).
- **Auto-purge:** worker permanently deletes items whose `deleted_at` is older than **30 days** (delete MinIO objects, `used_bytes -=`, drop rows).
- **Empty Trash:** manual action → permanently delete all trashed items now.

**Usage accounting:** incremented on upload, decremented on **permanent** delete only; a nightly **reconcile** job re-sums `vault_documents` to correct drift.

All mutating actions write `audit_log`.

## 5. Audited actions
Written to `audit_log` (in-transaction, append-only):
- `vault.folder_created` / `folder_renamed` / `folder_moved` / `folder_deleted` — folder_id (+ old/new name or parent).
- `vault.document_uploaded` — document_id, folder_id, filename, size, source (manual/id_proof/complaint).
- `vault.document_renamed` / `document_moved` / `document_deleted` — document_id (+ before/after).
- `vault.item_restored` — id, type (folder/document).
- `vault.trash_emptied` — count + bytes permanently deleted.
- `vault.trash_purged` — worker auto-purge (actor = system), items past 30-day retention.

## 6. Endpoints (`/vault/*`, society from JWT, admin-only)
- `GET /vault/folders/{id}/contents` — subfolders + files in a folder (root if id omitted) + **breadcrumb path**; paginated. (`vault.read`)
- `POST /vault/folders` — create (`parent_id`, `name`). (`vault.manage`)
- `PATCH /vault/folders/{id}` — rename / move (blocked for system folders). (`vault.manage`)
- `DELETE /vault/folders/{id}` — soft-delete → Trash (blocked for system roots). (`vault.manage`)
- `POST /vault/documents` — upload (`folder_id` + file; multipart). (`vault.manage`)
- `GET /vault/documents/{id}/preview` — presigned inline URL. (`vault.read`)
- `GET /vault/documents/{id}/download` — presigned download URL. (`vault.read`)
- `PATCH /vault/documents/{id}` — rename / move. (`vault.manage`)
- `DELETE /vault/documents/{id}` — soft-delete → Trash. (`vault.manage`)
- `GET /vault/trash` — list trashed items (with original path). (`vault.read`)
- `POST /vault/trash/{folders|documents}/{id}/restore` — restore. (`vault.manage`)
- `POST /vault/trash/empty` — permanent delete all trash. (`vault.manage`)
- `GET /vault/usage` — used vs limit. (`vault.read`)

## 7. Inter-module contracts
- **Provides:** `store_document(society_id, target, file, source, source_ref) -> document_id` · `get_preview_url` / `get_download_url(document_id)` · `usage(society_id)` · `ensure_house_folder(house, kind=proof|complaints) -> folder` · `ensure_notice_folder(notice) -> folder` (society-level `Notices/<notice id>/`). Consumed by **House & Occupancy** (Proof), **Complaints** (Complaints), and **Notice Board** (Notices).
- **Consumes:** foundation `TenantContext` / `AuditService` / `StorageBackend`(MinIO) · `societies.storage_limit_bytes` · Onboarding **house registry** (house display code for folder labels) · worker (purge + reconcile jobs).

## 8. Feature flag / config
- Module key `vault`. `config`: file-type **denylist**, trash retention days (default 30). (Storage limit lives on `societies`.)

## 9. Background jobs
- **Trash auto-purge** (daily) — permanent-delete items past 30-day retention.
- **Usage reconcile** (nightly) — re-sum `vault_documents` per society.

## 10. Open questions / future
Antivirus scanning, filename/content search, file versioning, resident access + shareable links, office-doc inline preview, per-file size cap (none now).

## 11. Resolved decisions
1. **Backend-proxied upload** (atomic type + quota enforcement) rather than presigned direct upload.
2. **Trashed items count toward the quota** until permanently deleted.
3. Complaint images filed under **`Complaints/<complaint ref>/`** inside a house's folder.
4. House folders **linked by `house_id`**, display name **derived** from the house's current display code (rename-safe).
