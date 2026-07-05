# Notice Board Module — Design

> Design doc. Foundation reading: [../01-project-overview](../01-project-overview.md) · [../02-architecture](../02-architecture.md) · [../03-backend-and-db-principles](../03-backend-and-db-principles.md) · [../05-cross-module-contracts](../05-cross-module-contracts.md) · [../platform/platform-foundation](../platform/platform-foundation.md)
>
> **Confirmed decisions baked in:** society-wide **broadcast** (admin posts, residents read), shared `/notices/*` endpoints, **permissions decide access** · content = title + **rich text body** + **attachments** (unlimited count, Vault-quota-bound) · lifecycle **draft → published**, **edit after publish** (shows an "edited · date" marker), **withdraw** (soft-delete), **pin/priority**, optional **expiry** (expired drops off the active feed) · **whole-society targeting only** (no per-building/house targeting in v1) · **admin read receipts** (track per-user reads + admin sees who has/hasn't read) · emits **`notice_posted`** for Notifications (skeleton) · **admin-only archive** (residents see the active feed only) · **resident-portal landing page** = the active notice feed.

## 1. Purpose & scope
A society-wide **broadcast board**: the society_admin composes a notice and publishes it to **all owners at once**; residents read the feed (their **portal landing page**) and the admin sees a panel of everything they've posted plus **read receipts**. Notices carry a rich-text body and any number of **attachments** (filed in the Vault).

**Out of scope (now / future):**
- **Targeting subsets** (a building/block or specific houses) — every notice is whole-society in v1.
- **Comments / reactions / acknowledgements** — read receipts only (no "must acknowledge" button, no threads).
- **Resident-authored notices** — admin posts, residents read.
- **Scheduled future-publish** — publish is immediate (draft until then).
- **Tenant visibility** — owners only for now (tenant login deferred platform-wide).

**Mobile:** backend-first; the feed is a simple newest-first (pinned-on-top) list of cards → notice detail with attachments, shaped for a phone.

## 2. Audience & permissions
Both audiences share the `/notices/*` endpoints; the service scopes data and gates actions by permission.

Permissions (`notices.*`):
- `notices.read` — residents + admin: read the **active feed**, open a notice (marks it read), mark-all-read.
- `notices.publish` — admin: create/draft/edit/publish/withdraw/pin/set-expiry + manage attachments.
- `notices.read_receipts` — admin: view per-notice **read-receipt** lists and the **admin archive** (expired + withdrawn history).

Default seeding (data-driven roles): **resident** → `notices.read`. **society_admin** → `notices.read`, `notices.publish`, `notices.read_receipts`. All gated `require_module('notices')` + `require_permission(...)`.

**Dual-role note:** an admin who also owns a house (see [platform-foundation §5.1](../platform/platform-foundation.md)) holds the union — they read the board like any resident and post as admin; the portal they pick is view-only.

## 3. Data model
Every table has `id` BIGINT identity PK, `society_id` FK (where tenant-scoped), `created_at`, `updated_at` unless noted. DB holds only PK/FK/NOT NULL/UNIQUE; all logic in the service layer. **Notices are society-scoped, not house-scoped** (no `house_id`).

**notices** — the notice record.
- `society_id` FK, `title`, `body` (rich text, **stored sanitized** — see §4), `status` ENUM(`draft`,`published`,`withdrawn`) default `draft`, `is_pinned` BOOL default false, `published_at` NULL, `expires_at` NULL, `last_edited_at` NULL, `created_by` FK → users, `withdrawn_at` NULL, `withdrawn_by` FK → users NULL.
- idx(`society_id`, `status`, `is_pinned`, `published_at`) — serves the feed (published, pinned-first, newest-first) and admin filters. Partial idx(`society_id`, `is_pinned`, `published_at`) WHERE `status='published'` for the hot active-feed path.

**notice_attachments** — files on a notice; the **link to Vault** (Vault built → wired).
- `notice_id` FK, `vault_document_id` FK → vault_documents, `added_by` FK → users, `created_at`.
- idx(`notice_id`). **No count cap** — bounded only by the society's Vault storage quota (Vault enforces type/size/quota).

**notice_reads** — per-user read state (drives unread badges + admin receipts).
- `notice_id` FK, `user_id` FK, `read_at`.
- UNIQUE(`notice_id`, `user_id`); idx(`notice_id`). One row per reader, inserted idempotently on first open.

### Status enum & allowed transitions
```
draft ──(publish)──► published ──(withdraw)──► withdrawn (terminal)
                        │  ▲
                 (edit) │  │ (edit / pin / set-expiry — stays published)
                        └──┘
expiry: published + expires_at < now  ⇒  "expired" (COMPUTED, not a stored status)
                                          → leaves the active feed, into the admin archive
```
- `draft → published` (admin, `notices.publish`) — sets `published_at`, emits `notice_posted`.
- `published → withdrawn` (admin) — soft-delete; off residents' feed, kept for admin/audit.
- **Edit / pin / expiry** keep `status='published'`; editing content sets `last_edited_at`.
- **`expired`** is **not a stored status** — it's derived from `expires_at` at query time. No `draft → withdrawn` needed (deleting a draft = withdraw or hard drop before publish; withdraw works from either, service allows discarding a draft).

## 4. Business rules
**Compose & publish (admin):**
- Create as **draft** (default) or publish immediately. `title` required; `body` is rich text and is **sanitized server-side** (whitelist tags/attrs, strip `<script>`/event handlers/`javascript:` URLs) to prevent stored XSS — the stored value is already safe to render.
- **Publish** sets `status='published'`, `published_at=now`, and **emits the `notice_posted` domain event** (§7). Drafts are visible only to admins (the author panel), never to residents.

**Edit / pin / expiry (admin):**
- **Edit** a published notice's `title`/`body` → sets `last_edited_at` (UI shows "edited · <date>"). Editing pin/expiry alone does not set `last_edited_at`.
- **Pin:** `is_pinned=true` floats it to the top of the feed; multiple pins allowed (no hard limit), pinned sorted among themselves by `published_at` DESC.
- **Expiry:** optional `expires_at`. Past it, the notice **drops off the active feed** (evaluated at query time) into the **admin archive**; no expiry = active until withdrawn.

**Withdraw (admin):** `status='withdrawn'`, `withdrawn_at`/`withdrawn_by` set. Removed from residents' feed and archive; retained for the admin archive + audit. Attachments are left in Vault (admin can clean up via Vault).

**Active feed (residents + admin read view):** `status='published'` AND (`expires_at IS NULL OR expires_at > now`), ordered **pinned first, then `published_at` DESC**. Withdrawn and expired never appear here.

**Reading & receipts:**
- Opening a notice (`GET /notices/{id}`) **inserts a `notice_reads` row** for the caller if absent (idempotent) → their unread count drops, and calls Notifications `mark_read_for(caller, 'notice', id)` to clear that owner's `notice` alert. `read-all` marks every active notice read for the caller.
- **Admin receipts:** the denominator is the society's **current owners** (from the Occupancy interface, §7). Receipts = current owners LEFT JOIN `notice_reads` → read vs unread lists + counts. Owners provisioned **after** a notice was posted also see it (whole-society broadcast, not a frozen snapshot) and count as unread until they open it.
- **Reads are NOT audited** (high-volume, not an admin state-change).

**Archive (admin-only):** expired (`published` + past `expires_at`) and `withdrawn` notices. Residents have **no** archive — active feed only.

**Attachments:** backend-proxied upload to **Vault** via `store_document(...)`, filed under the notice's `Notices/<notice id>/` system folder (§7). Vault enforces type denylist (415) and quota (413) — surfaced to the caller. Removing an attachment soft-deletes the Vault document (Vault Trash) and drops the `notice_attachments` row.

All mutating actions write `audit_log` in the same transaction (§5).

## 5. Audited actions
Written to `audit_log` (in-transaction, append-only):
- `notice.created` — notice_id, initial status (draft|published), title.
- `notice.published` — notice_id (draft → published).
- `notice.edited` — notice_id, before/after of title/body (+ whether pin/expiry changed).
- `notice.withdrawn` — notice_id.
- `notice.pinned` / `notice.unpinned` — notice_id.
- `notice.expiry_set` — notice_id, before/after `expires_at`.
- `notice.attachment_added` / `notice.attachment_removed` — notice_id, vault_document_id.
- (Reads / read-receipts are **not** audited.)

## 6. Endpoints (`/notices/*`, society from JWT)
- `GET /notices` — **residents:** active feed (pinned-first, newest-first) + per-caller `is_read` flag + `unread_count`. **Admin:** same, plus filter by `status` (incl. own `draft`) / `scope=active|archive`. Paginated. (`notices.read`)
  - Queries: single indexed select on (`society_id`,`status`,`is_pinned`,`published_at`); batch-join attachment counts + the caller's reads (no N+1).
- `GET /notices/{id}` — detail: fields + attachments (with Vault preview/download URLs); **marks read** for the caller (idempotent insert). (`notices.read`; drafts only visible to `notices.publish`)
- `POST /notices` — create `{title, body, is_pinned?, expires_at?, publish?}` (draft unless `publish=true`). Sanitizes body; if publishing, emits `notice_posted`. (`notices.publish`)
- `PATCH /notices/{id}` — edit `title`/`body`/`is_pinned`/`expires_at`; sets `last_edited_at` on content change. (`notices.publish`)
- `POST /notices/{id}/publish` — publish a draft → `published`, `published_at`, emit `notice_posted`. (`notices.publish`)
- `POST /notices/{id}/withdraw` — soft-withdraw. (`notices.publish`)
- `POST /notices/{id}/attachments` — upload (multipart) → Vault. (`notices.publish`)
- `DELETE /notices/{id}/attachments/{attachmentId}` — remove attachment. (`notices.publish`)
- `POST /notices/read-all` — mark all active notices read for the caller. (`notices.read`)
- `GET /notices/{id}/receipts` — read vs unread lists + counts (denominator = current owners). (`notices.read_receipts`)
- `GET /notices/archive` — expired + withdrawn history. (`notices.read_receipts`)

## 7. Inter-module contracts
- **Consumes — Vault** (built): attachments filed under a **`Notices/<notice id>/`** system folder via `ensure_notice_folder(notice) -> folder` + `store_document(society_id, target, file, source='notice', source_ref=notice_id) -> document_id`; `get_preview_url` / `get_download_url`. (Vault gains the `notices_root`/`notice` system folders + the `notice` document source — see [vault.md](vault.md).)
- **Consumes — House & Occupancy:** `current_owner_user_ids(society_id) -> [user_id]` — the read-receipt **denominator** + the broadcast audience. (Small add to Occupancy's provides if not already exposed.)
- **Consumes — Foundation:** `TenantContext`, permission/module gates, `AuditService`, an HTML **sanitizer** util.
- **Provides / consumes — Notifications** (built): emits **`notice_posted`** (payload: `notice_id`, `society_id`, `title`, `published_at`) to the in-process dispatcher ([docs/05 §3](../05-cross-module-contracts.md)) → Notifications delivers a `notice` alert to all current owners. Also **calls `mark_read_for(user, 'notice', notice_id)`** on notice-open (the `notice_reads` insert) to clear that owner's `notice` alert. (No-op if Notifications is disabled.)
- **Provides — Platform/Frontend:** the **resident-portal landing feed** (active notices) referenced by [platform-foundation §5.1](../platform/platform-foundation.md); `GET /notices` (active) is what the resident shell lands on.

## 8. Feature flag / config
- Module key `notices`, `depends_on = ['houses']` (needs the current-owner set for receipts + audience).
- `society_modules.config` for `notices`: **none required in v1** (no per-society knobs; attachments are quota-bound, expiry/pin are per-notice).

## 9. Background jobs
- **None.** Expiry is evaluated **at query time** (active-feed filter), so no worker/scheduler is needed for this module.

## 10. Open questions / future
- **Targeting subsets** (building/block, specific houses) — v2.
- **Acknowledgements** ("I have read & agree" for AGM-type notices), **scheduled future-publish**, **categories/labels** for notices, **reactions/comments**.
- **Tenant** visibility (with tenant login).
- Optional server-side "remember last portal / last-seen marker" refinements.

## 11. Resolved decisions
1. **Whole-society broadcast only** (no per-building/house targeting in v1); notices are society-scoped (no `house_id`).
2. Content = title + **rich-text body (sanitized)** + **attachments with no count cap** (Vault-quota-bound).
3. Lifecycle **draft → published**, **edit after publish** (with an "edited · date" marker via `last_edited_at`), **withdraw** (soft-delete), **pin**, optional **expiry** (query-time, no worker).
4. **Admin read receipts** — per-user reads tracked; admin sees read vs unread against the **current-owner** denominator; reads not audited.
5. **`notice_posted`** domain event emitted on publish → Notifications (built) delivers a `notice` alert to all current owners; notice-open calls `mark_read_for` to clear it.
6. **Admin-only archive** — residents see the active feed only; expired + withdrawn are admin history.
