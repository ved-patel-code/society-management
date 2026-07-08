# Notice Board (Module 6) — As-Built Index

> Lean navigation index (docs/04). Points to code; not a copy of it. Design source
> of truth: `docs/modules/notice-board.md`. Build/QA record: `docs/build-log/notice-board/`.

## Status
**COMPLETE** — built (frozen core + 4 parallel waves), code-reviewed, tested. The
sixth toggleable feature module: a society-wide **broadcast board**. A
`society_admin` composes a rich-text notice (+ any number of Vault attachments) and
publishes it to all owners at once; residents read the active feed (their portal
landing page); admins see per-notice **read receipts** + an **archive**. Lifecycle
`draft → published → withdrawn`; edit-after-publish (shows an "edited · date"
marker), pin, optional **query-time expiry** (no worker). Notices are
society-scoped (no `house_id`). `depends_on: houses` (current-owner set = the
receipt denominator + audience); attachment routes also require the `vault`
module. Emits `notice_posted` + calls `mark_read_for` on open — both no-ops until
Notifications (Module 7) subscribes (skeleton-then-wire). One new dependency:
**`nh3`** (HTML sanitizer). Migration `0007_notices` chained off `0006_complaints`.

## File map
Module package `app/modules/notices/`:
- `models.py` — 3 tables: `notices` (society-scoped; status draft|published|
  withdrawn, is_pinned, published_at, expires_at, last_edited_at, created_by,
  withdrawn_at/by), `notice_attachments` (Vault link, no count cap),
  `notice_reads` (per-user read state). Composite index (admin filter) + partial
  index `WHERE status='published'` (hot active feed) + `UNIQUE(notice_id,user_id)`
  on reads. `expired` is COMPUTED at query time, never stored.
- `schemas.py` — frozen Pydantic contracts + the status domain constants,
  `ALLOWED_TRANSITIONS` (the transition table as data), `LIST_SCOPES`, field bounds.
- `repository.py` — SQL-only, `society_id`-scoped. `add_notice`,
  `get_notice(lock=True)`, `list_notices` (active/archive/status filters,
  pinned-first ordering, `(rows, total)`), attachment CRUD + batched
  `attachment_counts_for` (no N+1), `mark_read` (`ON CONFLICT DO NOTHING`),
  batched `read_notice_ids_for`, `has_read`, `reads_for_notice` (receipts),
  `active_notice_ids` / `active_notice_count`.
- `service.py` — thin `NoticesService` facade over the concern split; exposes
  `active_notice_count`.
- `services/support.py` — shared frozen internals: `sanitize_body` (the single
  body choke-point → `common/html_sanitizer`), `apply_publish` (THE single publish
  write: stamp `published_at` + emit `notice_posted` ONCE, shared by
  create-with-publish and the publish endpoint), `assert_transition_allowed`,
  `is_active` / `is_expired` (query-time expiry predicate), `assemble_detail` /
  `assemble_list_item` / `preview_url_or_none` / `download_url_or_none` (the one
  shared view builders, trashed-attachment-safe), `current_owner_ids` (Occupancy
  interface, no table access).
- `services/notices_crud.py` — create (sanitize; publish-on-create via
  `apply_publish`; audit), edit (content-only `last_edited_at`; `model_fields_set`
  clear-vs-omit expires_at; withdrawn→409; empty→422), list_feed (residents=active,
  admins=status/scope filters + drafts; batched, no N+1; unread_count independent
  of page), get_detail (draft/withdrawn→404 for non-managers; idempotent read +
  `mark_read_for`).
- `services/lifecycle.py` — publish (via `apply_publish`; illegal→409), withdraw
  (draft|published→withdrawn soft-delete; attachments left in Vault; double→409).
- `services/attachments.py` — add (`get_notice(lock=True)`, sync `file.file.read`,
  `ensure_notice_folder` + `store_document(source='notice')`, Vault 413/415
  propagate with no orphan row, no cap), remove (Vault soft-delete BEFORE dropping
  the row so a Vault error rolls back).
- `services/receipts.py` — read_all (idempotent across active notices), receipts
  (current-owner denominator, in-memory read/unread split, no per-owner loop),
  archive (expired + withdrawn, batched, admin-only).
- `router.py` — thin dual-gated routes (`/notices/*`); static `/read-all` +
  `/archive` declared before `/{notice_id}`; attachment routes also gate
  `require_module('vault')`; the read-vs-manage split is data-driven (`_can_manage`
  = super-admin or `notices.publish`).
- `api.py` — inter-module provider: `active_notice_count` (read-only, stable
  contract; no built consumer yet).
- `events.py` — `emit_posted` + `mark_read_for` → `app/common/events`; no-op until
  Notifications subscribes (skeleton-then-wire).
- `spec.py` — `NOTICES_SPEC` + `register_notices`; key `notices`, 3 perms,
  `depends_on: houses`, `default_config={}`, resident=read / society_admin=all 3.

Shared Foundation util:
- `app/common/html_sanitizer.py` — `sanitize_html(raw)` via `nh3.clean` with the
  formatting-only whitelist (strips script/style/img/iframe/event-handlers,
  restricts `a` href to http/https/mailto). Reusable by any future rich-text field.

Migration:
- `alembic/versions/0007_notices.py` — the 3 tables + 2 indexes + the reads UNIQUE,
  off `0006_complaints`.

## Functions
- `sanitize_html` — clean rich text to the safe-to-store/render whitelist. deps:
  nh3. @ `app/common/html_sanitizer.py`
- `apply_publish` — the single publish write: guard + stamp `published_at` + set
  status + emit `notice_posted` once. deps: `assert_transition_allowed`, events,
  utcnow. @ `services/support.py`
- `assemble_detail` — build `NoticeDetailOut` (fields + attachments with guarded
  URLs + is_read). deps: repo.list_attachments, preview/download_url_or_none.
  @ `services/support.py`
- `current_owner_ids` — the society's current owner user ids (receipt denominator +
  audience). deps: `HouseService.current_owner_user_ids`. @ `services/support.py`
- `NoticesCrudService.list_feed` — the feed/list with batched counts + reads (no
  N+1) + unread badge. deps: repo.list_notices, attachment_counts_for,
  read_notice_ids_for, active_notice_ids. @ `services/notices_crud.py`
- `NoticesCrudService.get_detail` — detail + clear-on-read; draft/withdrawn→404 for
  non-managers. deps: repo.get_notice, mark_read, events.mark_read_for,
  assemble_detail. @ `services/notices_crud.py`
- `LifecycleService.publish` / `.withdraw` — the two status edges + audit. deps:
  support.apply_publish / assert_transition_allowed. @ `services/lifecycle.py`
- `AttachmentsService.add_attachment` / `.remove_attachment` — Vault-backed
  add/remove with rollback-safe ordering. deps: vault api, VaultService. @
  `services/attachments.py`
- `ReceiptsService.receipts` — current-owner LEFT JOIN reads, in-memory split.
  deps: current_owner_ids, repo.reads_for_notice. @ `services/receipts.py`

## Tables owned
`notices`, `notice_attachments`, `notice_reads` (columns/DDL live in
`0007_notices` + `docs/modules/notice-board.md §3`, not here).

## Cross-module wiring
- **Consumes Vault** (built): `ensure_notice_folder`, `store_document(source=
  'notice')`, `get_preview_url` / `get_download_url`, `VaultService.delete_document`
  — attachments under `Notices/<notice id>/`. Vault was pre-wired for notices.
- **Consumes House & Occupancy** (built): `current_owner_user_ids(society_id)` —
  the receipt denominator + broadcast audience (via `support.current_owner_ids`).
- **Consumes Foundation**: `TenantContext`, module/permission gates, `AuditService`,
  the `common/html_sanitizer` util.
- **Provides / emits Notifications** (Module 7, not yet built): `notice_posted`
  (payload notice_id/society_id/title/published_at) + `mark_read_for` on notice-open
  → `app/common/events`; no-op until Notifications subscribes.
- **Provides Platform/Frontend**: the resident-portal landing feed (`GET /notices`
  active).
- **Provides (inter-module)**: `api.active_notice_count(session, society_id)`.

## Deviations from design
- **Notifications wired as skeleton no-op.** The design doc marks `notice_posted` +
  `mark_read_for` as "wired," but Notifications is Module 7 (built after this). Per
  the established skeleton-then-wire pattern (same as Complaints), the emits go to
  `app/common/events` with no subscribers — a safe no-op that Notifications will
  subscribe to with zero call-site changes.
- **`notice_posted` payload `published_at` is ISO-string** (not a raw datetime) so a
  future Notifications subscriber can serialize it without special handling (matches
  the audit idiom).
- **Sanitizer is a Foundation util** (`app/common/html_sanitizer.py`), not a
  notices-private helper — the design lists it as a Foundation contract, so future
  rich-text modules reuse it.
