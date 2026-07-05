# Complaints Module — Design

> Design doc. Foundation reading: [../01-project-overview](../01-project-overview.md) · [../02-architecture](../02-architecture.md) · [../03-backend-and-db-principles](../03-backend-and-db-principles.md) · [../05-cross-module-contracts](../05-cross-module-contracts.md) · [../platform/platform-foundation](../platform/platform-foundation.md)
>
> **Confirmed decisions baked in:** shared `/complaints/*` endpoints for resident + admin, **permissions decide access** · status flow **open → in_progress → resolved → closed → archived** (admin-driven; **admin manually closes** resolved complaints; **worker auto-archives** N days after close, **N configurable**, default 15) · resident may **edit + withdraw while `open`**, locked once `in_progress` · **categories predefined + admin-extendable**; **common-area issues are just a category** — every complaint still ties to the raiser's house · **status-only + optional admin note** per status change (no back-and-forth thread) · **resident report images ≤ 2**, **admin proof images ≤ 2**, both optional, all **stored in Vault** under `Houses/<house>/Complaints/<complaint>` · resident notified on status change (via Notifications, skeleton now).

## 1. Purpose & scope
Lets a **resident (owner)** raise a maintenance/issue complaint tied to their house, attach up to 2 photos, and track its status; lets the **society_admin** see all complaints, move them through a status workflow, add up to 2 proof photos on resolution, and manage the category list. Complaint photos are auto-filed into the **Vault**.

**Out of scope (now):**
- **Comment/conversation thread** between resident and admin — status changes carry an **optional admin note** only; no two-way messaging.
- **Admin-raised complaints** on behalf of a house, and **assignment** to a handler/staff member — the app has no staff/worker role yet (roles are data-driven; can be added later).
- **Tenant-raised complaints** — owners only for now (tenant login deferred platform-wide).
- **Common-area as a separate scope** — handled purely as a **category**; a complaint always attaches to the raiser's house.

**Mobile:** backend-first; list/detail responses are shaped for a phone drill-down (list card → detail with status timeline + images).

## 2. Audience & permissions
Both audiences share the `/complaints/*` endpoints; the service scopes data and gates actions by permission.

Permissions (`complaints.*`):
- `complaints.create` — raise a complaint; **edit / withdraw one's own** complaint while it is `open`; add/remove one's own **report** images while `open`.
- `complaints.read` — view complaints **scoped to the caller's own house(s)** (resident view).
- `complaints.read_all` — view **all** the society's complaints + admin filters (admin view).
- `complaints.update_status` — transition status, attach the optional note, add/remove **proof** images (admin).
- `complaints.manage_categories` — create / rename / deactivate categories (admin).
- `complaints.configure` — set module config, e.g. `auto_archive_days` (admin).

Default seeding (data-driven roles): **resident** → `create`, `read`. **society_admin** → `read`, `read_all`, `update_status`, `manage_categories`, `configure`. All gated `require_module('complaints')` + `require_permission(...)`.

**Dual-role (admin who also owns a house):** one account holding both roles has the **union** of both permission sets (see [platform-foundation §5.1](../platform/platform-foundation.md)). On the shared `/complaints/*` endpoints they can **raise** a complaint (resident portal — attaches to their house) and **resolve/close** it (admin portal). Self-handling one's own complaint is **permitted** (accepted consequence of view-only union permissions; no separation of duties in v1). The **portal** they pick is view-only — it changes which complaints view they land on, not what the backend allows.

## 3. Data model
Every table has `id` BIGINT identity PK, `society_id` FK, `created_at`, `updated_at` unless noted. DB holds only PK/FK/NOT NULL/UNIQUE; all logic in the service layer.

**complaint_categories** — `society_id` FK, `name`, `is_active` BOOL (default true), `is_system` BOOL (seeded defaults, still renamable but recommended kept), `created_by`.
- Partial UNIQUE(`society_id`, `name`) WHERE `is_active` — no two active categories share a name; idx(`society_id`, `is_active`).
- Seeded per society on module enable: **Plumbing, Electrical, Common Area, Security, Cleaning, Other** (admin extends/renames/deactivates).

**complaints** — the complaint record.
- `society_id` FK, `reference` (human id, e.g. `C-000123`), `house_id` FK **NOT NULL** (raiser's house), `raised_by` FK → users (the owner), `category_id` FK → complaint_categories, `title`, `description`,
- `status` ENUM(`open`,`in_progress`,`resolved`,`closed`,`archived`,`withdrawn`) default `open`,
- `resolved_at` NULL, `closed_at` NULL (set on entry to `closed`; drives auto-archive), `archived_at` NULL, `withdrawn_at` NULL.
- Partial UNIQUE(`society_id`, `reference`).
- idx(`society_id`, `status`) — admin list filtered by status; idx(`society_id`, `house_id`) — resident's own list + house profile; idx(`society_id`, `category_id`) — category filter; idx(`status`, `closed_at`) WHERE `status='closed'` — the auto-archive worker scan.

**complaint_status_history** — the status **timeline** (append-only), also the home of admin notes.
- `complaint_id` FK, `from_status` NULL (NULL = initial create), `to_status`, `note` NULL (admin's optional note for this transition), `changed_by` FK → users NULL (NULL = system/worker, e.g. auto-archive), `created_at`.
- idx(`complaint_id`, `created_at`).

**complaint_images** — report + proof photos; the **skeleton-then-wire link to Vault**, now wired.
- `complaint_id` FK, `kind` ENUM(`report`,`proof`), `vault_document_id` FK → vault_documents, `added_by` FK → users, `created_at`.
- idx(`complaint_id`, `kind`). Counts enforced in service: ≤ `max_report_images` (2) per complaint for `report`, ≤ `max_proof_images` (2) for `proof`.

**Reference numbers:** per-society running sequence rendered as `C-` + zero-padded (e.g. `C-000123`). Allocated in the create transaction (per-society counter row / `SELECT max+1` under the society lock) so numbers are gap-tolerant but unique.

### Status enum & allowed transitions
```
                 ┌── withdraw (resident, while open) ──► withdrawn (terminal)
open ──► in_progress ──► resolved ──► closed ──► archived (terminal)
              ▲             │                    (worker, auto after N days)
              └── reopen ───┘  (admin correction: resolved → in_progress)
```
- **resident** (`complaints.create`, owner of the complaint): `open → withdrawn` only, and only while `open`.
- **admin** (`complaints.update_status`): `open → in_progress`, `in_progress → resolved`, `resolved → closed` (**manual close**), and the correction step `resolved → in_progress` (reopen before it's closed). Proof images may be attached at `resolved`.
- **worker** (system): `closed → archived`, automatically `auto_archive_days` after `closed_at`.
- No other transitions; anything else → 409. `withdrawn` and `archived` are terminal.

## 4. Business rules
**Raising (resident):**
- Caller must have an **owner occupancy**. The complaint's `house_id` is the caller's **current owned house** (`house_occupancies.user_id = caller`, `is_current`, `party_type='owner'`). If the caller owns **exactly one** current house it's inferred; if **several**, the request must name `house_id` and the service verifies the caller owns it → else 403/422.
- `category_id` must reference an **active** category in this society; `title` required, `description` required. Status starts `open`; a `complaint_status_history` row `(NULL → open)` is written.
- Up to **2 report images** may be attached (optional) — each uploaded to Vault (below).

**Editing / withdrawing (resident):** only the **raiser**, only while `status='open'`. Editable: `title`, `description`, `category_id`, and add/remove **report** images. Once admin moves it to `in_progress`, the complaint is **locked** to the resident (read-only). Withdraw sets `status='withdrawn'`, `withdrawn_at`, writes history.

**Status workflow (admin):** transitions restricted to the table above; each writes a `complaint_status_history` row with the optional `note`. Entering `resolved` may include up to **2 proof images**. Entering `closed` sets `closed_at` (starts the archive clock). Reopen (`resolved → in_progress`) clears `resolved_at`.

**Images (both, via Vault):**
- Backend-proxied upload to **Vault** through `store_document(...)`, filed under `Houses/<house>/Complaints/<complaint reference>/` (folder auto-created via `ensure_house_folder`). `complaint_images` stores the returned `vault_document_id`.
- Limits enforced **before** upload: `report` ≤ 2 (resident, while `open`), `proof` ≤ 2 (admin, at/after `resolved`). All optional. Type/quota rejection is Vault's (415/413) — surfaced to the caller.
- Removing an image soft-deletes the Vault document (Vault Trash) and drops the `complaint_images` row.

**Categories (admin):** create / rename / **deactivate** (never hard-delete). A deactivated category stays attached to existing complaints (historical), but can't be chosen for new ones. Renaming an active category can't collide with another active name.

**Visibility:** `complaints.read` (resident) → service returns only complaints whose `house_id` the caller owns. `complaints.read_all` (admin) → all society complaints. A resident can never see another house's complaints; enforced in the repository query, not the endpoint.

**Auditing & events:** every state change writes `audit_log` in the same transaction (see §5) and emits domain events for Notifications (see §7): **`complaint.created`** (on raise → admin `complaint_new` alert), **`complaint.withdrawn`** (→ admin `complaint_withdrawn` alert), and **`complaint.status_changed`** (→ the raising owner's `complaint_update`).

**Clear-on-read wiring:** on opening a complaint (`GET /complaints/{id}`), the service calls Notifications **`mark_read_for(caller, 'complaint', complaint_id)`** — the raiser opening it clears their `complaint_update`; an admin opening it clears that admin's `complaint_new` / `complaint_withdrawn`.

## 5. Audited actions
Written to `audit_log` (in-transaction, append-only):
- `complaint.created` — complaint_id, reference, house_id, category_id.
- `complaint.updated` — complaint_id, before/after of title/description/category (resident edit while open).
- `complaint.withdrawn` — complaint_id.
- `complaint.status_changed` — complaint_id, from_status → to_status, note (covers in_progress / resolved / closed / reopen).
- `complaint.image_added` / `complaint.image_removed` — complaint_id, kind (report|proof), vault_document_id.
- `complaint.archived` — complaint_id (actor = system worker).
- `complaint_category.created` / `renamed` / `deactivated` — category_id (+ before/after name).
- `complaints.config_updated` — before/after of `auto_archive_days` (etc.).

## 6. Endpoints (`/complaints/*`, society from JWT)
- `POST /complaints` — raise (`category_id`, `title`, `description`, optional `house_id` if multi-house, optional images multipart). Writes complaint + initial history; files images to Vault. (`complaints.create`)
  - Queries: resolve owner house (occupancy lookup), validate active category, allocate reference, insert.
- `GET /complaints` — list. Resident → own house(s); admin → all + filters `status`, `category_id`, `house_id`, `from`/`to` date, `q` (reference/title). Paginated, newest first. (`complaints.read` / `complaints.read_all`)
  - Queries: single indexed select on (`society_id`,`status`|`house_id`|`category_id`); joins category name + house display code; **no N+1** (batch image counts).
- `GET /complaints/{id}` — detail: fields + category + house code + **status timeline** (history with notes) + images (with Vault preview URLs). Calls Notifications `mark_read_for(caller, 'complaint', id)` (clear-on-read). (`complaints.read` owner / `complaints.read_all`)
- `PATCH /complaints/{id}` — resident edit while `open` (`title`/`description`/`category_id`). (`complaints.create`, owner-only)
- `POST /complaints/{id}/withdraw` — resident withdraw while `open`. (`complaints.create`, owner-only)
- `POST /complaints/{id}/images` — add image (`kind` derived: resident→report while open; admin→proof). Multipart → Vault. Enforces per-kind cap. (`complaints.create` owner / `complaints.update_status`)
- `DELETE /complaints/{id}/images/{imageId}` — remove an image (own report / admin proof). (`complaints.create` owner / `complaints.update_status`)
- `POST /complaints/{id}/status` — admin transition `{ to_status, note? }`; validates against the transition table; may accompany proof images already added. (`complaints.update_status`)
- `GET /complaints/categories` — list active categories (for the create form). (`complaints.read`)
- `POST /complaints/categories` — create `{name}`. (`complaints.manage_categories`)
- `PATCH /complaints/categories/{id}` — rename / reactivate. (`complaints.manage_categories`)
- `DELETE /complaints/categories/{id}` — deactivate (soft). (`complaints.manage_categories`)
- `GET /complaints/config` — read module config. (`complaints.configure`)
- `PUT /complaints/config` — set `auto_archive_days` (etc.). (`complaints.configure`)

## 7. Inter-module contracts
- **Consumes — Vault** (built): `ensure_house_folder(house, kind='complaints') -> folder`, `store_document(society_id, target, file, source='complaint', source_ref=complaint_id) -> document_id`, `get_preview_url` / `get_download_url(document_id)`. Complaint images live under `Houses/<house>/Complaints/<complaint reference>/`.
- **Consumes — House & Occupancy / Onboarding:** resolve the caller's **current owned house** (occupancy interface) and the **house display code** for labels (never reads their tables directly).
- **Consumes — Foundation:** `TenantContext`, permission/module gates, `AuditService`, the **worker** (auto-archive job).
- **Provides / consumes — Notifications** (built): emits three **domain events** to the in-process dispatcher (docs/05 §3) — `complaint.created` (→ admin `complaint_new`), `complaint.withdrawn` (→ admin `complaint_withdrawn`), and `complaint.status_changed` (payload: `complaint_id`, `house_id`, `raised_by`, `from_status`, `to_status`, `note`, `reference` → owner `complaint_update`). Also **calls `mark_read_for(user, 'complaint', complaint_id)`** on complaint-open to clear the caller's related alert. (A handler is a no-op if Notifications is disabled for the society.)
- **Provides (optional, small):** `open_complaint_count(house_id)` for a future house profile / resale view — read-only helper, not required by any built module yet.

## 8. Feature flag / config
- Module key `complaints`, `depends_on = ['houses']` (needs house registry + occupancy). Enabling seeds the **default categories** for the society.
- `society_modules.config` for `complaints`:
  - `auto_archive_days` (default **15**) — days after `closed_at` before the worker archives.
  - `max_report_images` (default 2), `max_proof_images` (default 2).

## 9. Background jobs
- **Auto-archive** (daily, worker) — select `status='closed'` AND `closed_at <= now - auto_archive_days` (uses the partial index), set `status='archived'`, `archived_at`, write history `(closed → archived, changed_by=NULL)` + `complaint.archived` audit. **Idempotent** (only touches `closed` rows).

## 10. Open questions / future
- Resident-driven **reopen / "not fixed"** after `resolved` (currently only admin reopens; would need the comment thread).
- **Comment thread**, **admin-raised** complaints, **assignment** to a handler (all deferred — see §1).
- **Tenant** raising/viewing complaints (with tenant login).
- SLA timers / escalation, per-category default handlers, resident satisfaction rating on close.

## 11. Resolved decisions
1. Status flow **open → in_progress → resolved → closed → archived**; admin **manually** closes resolved, **worker auto-archives** after a **configurable** number of days (default 15).
2. Categories **predefined + admin-extendable**; **common-area is a category**, not a separate scope — every complaint ties to the raiser's house (`house_id` NOT NULL).
3. **Status-only + optional admin note** per transition (timeline in `complaint_status_history`); no two-way thread.
4. Resident may **edit + withdraw while `open`**; locked at `in_progress`.
5. **≤ 2 report images** (resident) and **≤ 2 proof images** (admin), all optional, all stored in **Vault** under the house's `Complaints/<reference>` folder.
6. **No admin-raised complaints and no assignment** in v1.
