# Complaints (Module 5) — As-Built Index

> Lean navigation index (docs/04). Points to code; not a copy of it. Design source
> of truth: `docs/modules/complaints.md`. Build/QA record: `docs/build-log/complaints/`.

## Status
**COMPLETE** — built (frozen core + 6 parallel waves), code-reviewed, tested. The
fifth toggleable feature module: house-scoped complaints. A resident (owner) raises a
complaint tied to their house (title + description + category + ≤2 report photos),
tracks status; a society_admin sees all, drives the workflow
(`open → in_progress → resolved → closed → archived`), attaches a solution note + ≤2
proof photos when resolving (locked after), and manages categories. Photos are filed
into the Vault. A worker auto-archives closed complaints after a configurable number
of days. `depends_on: houses`; image routes also require the `vault` module.
No new third-party dependencies. Migration `0006_complaints` chained off
`0005_finance`.

## File map
Module package `app/modules/complaints/`:
- `models.py` — 5 tables: `complaint_categories`, `complaints`,
  `complaint_status_history`, `complaint_images`, `complaint_reference_counters`.
  Two partial indexes (active-name uniqueness; the `status='closed'` archive scan).
  Enum-like domains service-enforced.
- `schemas.py` — frozen Pydantic contracts + the status/kind constants,
  `ALLOWED_TRANSITIONS` (the transition table as data), `ADMIN_TARGET_STATUSES`,
  `format_reference`, config defaults, `ComplaintsConfig`.
- `repository.py` — SQL-only, `society_id`-scoped. Categories, the FOR-UPDATE
  `allocate_reference`, complaints (incl. `get_complaint(lock=True)`,
  resident-scoped `list_complaints` with the visibility allow-list + inclusive
  `date_to` + LIKE-escaped `q`, `closed_to_archive`), history, images (incl.
  batched `image_counts_for` — no N+1).
- `service.py` — thin `ComplaintsService` facade over the concern split; exposes
  `open_complaint_count`.
- `services/support.py` — shared internals: `load_config` / `write_config`
  (partial merge), `ensure_default_categories` (lazy seed), `allocate_reference`,
  `assert_transition_allowed`, `record_transition` (the single status-history +
  timestamp choke-point) / `record_initial`, and `assemble_detail` /
  `preview_url_or_none` (the one shared detail builder, trashed-image-safe).
- `services/categories.py` — list (lazy-seed) / create (active-name 409) / rename +
  reactivate (PATCH; `is_active=false` → 422, DELETE deactivates) / soft-deactivate.
- `services/complaints_crud.py` — raise (owner-house resolution, active category,
  reference, initial history, event), edit-while-open (raiser, active category),
  withdraw, list (resident own-house vs read_all), detail (clear-on-read).
- `services/status.py` — admin non-resolve transitions; `resolve`
  (`in_progress → resolved`, multipart note + proof images to Vault, cap before
  upload under a row lock, proof locked after).
- `services/images.py` — report images: add (raiser, while open, capped under a row
  lock) / remove (Vault soft-delete + drop row). Proof is not handled here.
- `services/config_svc.py` — GET + partial-merge PUT config; audits before/after.
- `services/jobs.py` — `run_daily_auto_archive` worker scan (own session,
  commit-per-society, failure-isolated, idempotent) + `_run_for_societies(now)`
  testable helper.
- `api.py` — cross-module contract: `open_complaint_count(session, society_id,
  house_id)`.
- `events.py` — the notification call surface: `emit_created` / `emit_withdrawn` /
  `emit_status_changed` / `mark_read_for`, routing to `app.common.events`.
- `router.py` — 15 thin `/complaints/*` routes, dual-gated
  `require_module('complaints')` + permission; image/resolve routes also
  `require_module('vault')`; read-vs-read_all split in the handlers.
- `spec.py` — `COMPLAINTS_SPEC` (`depends_on: ['houses']`, 6 perms, `default_config`
  = `{auto_archive_days:15, max_report_images:2, max_proof_images:2}`, admin=5 /
  resident=`create`,`read`).
- `alembic/versions/0006_complaints.py` — migration (chained off `0005_finance`);
  the 5 tables + indexes. No FK cascade.

New shared infra:
- `app/common/events.py` — a real in-process domain-event dispatcher
  (`subscribe` / `unsubscribe` / `clear` / `emit`): synchronous, in the emitter's
  transaction, no-op with no subscribers, handler exceptions logged + swallowed.
  Notifications will `subscribe` to `complaint.*` at startup with zero change here.

Consumer/provider wiring (House & Occupancy):
- `app/modules/houses/{repository,service}.py` — added `current_owned_houses(
  society_id, user_id)` (owner occupancies, is_current, party_type='owner') +
  `house_display_code(society_id, house_id)` — consumed by Complaints via the
  service interface, never tables.

Foundation touchpoints:
- `app/main.py` — registers `register_complaints` + mounts the router.
- `alembic/env.py` — imports complaints models.
- `app/worker/entrypoint.py` — schedules the daily auto-archive scan (01:30 UTC).

## Functions (summary · deps · @location)
- `ComplaintRepository.allocate_reference` — `C-000123` per-society sequence under a
  FOR-UPDATE counter row. deps: `complaint_reference_counters`. @ repository.py
- `support.record_transition` — the single status write: stamps the entry timestamp,
  clears `resolved_at` on reopen, sets status, appends the history row. deps: repo,
  `_ENTRY_TIMESTAMP`. @ services/support.py
- `support.assemble_detail` — the one detail builder (category + house code +
  timeline + images with guarded preview). deps: repo, HouseService, vault api. @ support.py
- `CategoriesService.*` — list (lazy seed) / create / update (rename+reactivate) /
  deactivate. deps: repo, `ensure_default_categories`, AuditService. @ services/categories.py
- `ComplaintsCrudService.raise_complaint` — owner-house resolution + active category
  + reference + initial history + `complaint.created`. deps: HouseService, repo,
  support, events, AuditService. @ services/complaints_crud.py
- `ComplaintsCrudService.list_complaints` — visibility allow-list (resident own /
  read_all all) + filters + batched labels/counts. deps: repo, HouseService. @ complaints_crud.py
- `StatusService.change_status` — admin non-resolve edges via the transition guard.
  deps: support, events, AuditService. @ services/status.py
- `StatusService.resolve` — `in_progress → resolved` with proof images (cap before
  upload, row lock, Vault store). deps: vault api, support, repo, events. @ status.py
- `ImagesService.add_report_image / remove_report_image` — report image add (capped,
  locked) / remove (Vault soft-delete). deps: vault api, VaultService, repo. @ services/images.py
- `ConfigService.get_config / update_config` — read / partial-merge write + audit.
  deps: support.load_config/write_config, AuditService. @ services/config_svc.py
- `jobs.run_daily_auto_archive` — worker scan, per-society commit/isolation,
  idempotent, real-instant window. deps: SessionLocal, support, repo. @ services/jobs.py
- `complaints.api.open_complaint_count` — cross-module read helper. @ api.py
- `common.events.emit / subscribe` — the in-process dispatcher. @ app/common/events.py

## Tables owned
`complaint_categories`, `complaints`, `complaint_status_history`,
`complaint_images`, `complaint_reference_counters`.

## Endpoints
Categories: `GET /complaints/categories` · `POST /complaints/categories` ·
`PATCH /complaints/categories/{id}` · `DELETE /complaints/categories/{id}`.
Complaints: `POST /complaints` · `GET /complaints` (filters status/category/house/
date/`q`, paginated) · `GET /complaints/{id}` · `PATCH /complaints/{id}` ·
`POST /complaints/{id}/withdraw`.
Status: `POST /complaints/{id}/status` (non-resolve) ·
`POST /complaints/{id}/resolve` (multipart: note + proof images).
Report images: `POST /complaints/{id}/images` · `DELETE /complaints/{id}/images/{imageId}`.
Config: `GET /complaints/config` · `PUT /complaints/config`.
All dual-gated `require_module('complaints')` + permission; image/resolve routes
also `require_module('vault')`. Society always from the JWT. Read scope is
data-driven: `complaints.read_all` (or super-admin) sees the whole society; a
`complaints.read`-only resident is scoped to their own house(s) in the repository
query.

## Audited actions (emitted)
`complaint.created` / `complaint.updated` / `complaint.withdrawn` /
`complaint.status_changed` / `complaint.image_added` / `complaint.image_removed` /
`complaint.archived` (actor = system worker) · `complaint_category.created` /
`renamed` / `reactivated` / `deactivated` · `complaints.config_updated`. All
in-transaction.

## Cross-module wiring
- **Consumes:** House & Occupancy (`current_owned_houses`, `house_display_code`,
  `house_exists`, `is_current_occupant` via `HouseService`); Vault (`store_document`,
  `ensure_house_folder(kind='complaints')`, `get_preview_url`, `delete_document`);
  foundation `TenantContext` + permission/module gates + `AuditService` + the worker.
- **Provides:** `open_complaint_count` (`complaints/api.py`).
- **Provides / consumes — events:** emits `complaint.created` / `complaint.withdrawn`
  / `complaint.status_changed` + a clear-on-read signal to `app.common.events`;
  Notifications subscribes when built (skeleton-then-wire; no-op today).

## Testing
Reuses the shared harness (`backend/tests/`): isolated per-worker `society_test`
DBs, truncate+reseed, existing fixtures + `tests/_complaints_helpers.py`. 168
complaints tests across 13 files — 6 per-wave (categories/crud/status/images/config/
jobs) + the Phase-3 gate (e2e/enable/security/isolation/regression/edge/robustness).
Full suite **857 passed, 2 skipped**. Run:
`docker compose exec backend bash scripts/run-tests.sh`.

## Deviations from design (drift vs docs/modules/complaints.md)
1. **Default categories** are seeded LAZILY on first use of the categories feature
   (`ensure_default_categories`) rather than at module-enable — the enable flow is
   shared/foundation-owned and must not be edited per-module (same rule + tactic as
   Finance's lazy category seed). Functionally equivalent.
2. **Proof images** are attached ONLY during the `in_progress → resolved` transition
   (multipart `POST /complaints/{id}/resolve`: solution note + ≤2 proof photos) and
   are LOCKED afterward — refining §4/§6's "proof images may be attached at
   `resolved`" per an explicit product decision. There is no standalone proof
   add/remove endpoint; the cap is per resolve call.
3. **Config `PUT`** is a partial merge (unspecified keys keep their current value).
4. **Notifications wiring** is a real in-process dispatcher built one module early
   (`app/common/events.py`); the design's "✅ wired" was aspirational (Notifications
   is not built). Complaints emits today; the dispatcher is a no-op until
   Notifications subscribes.
5. A `complaint_category.reactivated` audit action was added (not in the original §5
   list — now documented) for turning a deactivated category active again via PATCH.

Everything else matches `docs/modules/complaints.md`.
