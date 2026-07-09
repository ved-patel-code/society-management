# Notifications (Module 7) — As-Built Index

> Lean navigation index (docs/04). Points to code; not a copy of it. Design source
> of truth: `docs/modules/notifications.md`. Build/QA record: `docs/build-log/notifications/`.

## Status
**COMPLETE** — built, code-reviewed (3 parallel review agents), tested. The
seventh toggleable feature module: a per-society **in-app notification + reminder
engine**. It turns the already-emitted domain events from Complaints and Notice
Board into real in-app notifications, and hosts a **scheduled maintenance-dues
reminder**. Two delivery paths: **event-driven** (synchronous, in the emitter's
request — one batched insert per fan-out) and **scheduled** (a daily worker scan).
Clear-on-read empties the feed as items are read (directly or by opening the
source item). `depends_on: finance` (the dues rule); complaint/notice events are
**soft** (handlers no-op if that module is off for the society). No new
third-party dependencies. Migration `0008_notifications` chained off `0007_notices`.

## File map
Module package `app/modules/notifications/`:
- `models.py` — 1 table `notifications` (one row per recipient per event). Four
  indexes: partial UNIQUE(`society_id`,`dedupe_key`) WHERE dedupe_key NOT NULL
  (idempotency); `(user_id, created_at)` WHERE read_at IS NULL (feed/badge);
  `(user_id, entity_type, entity_id)` WHERE read_at IS NULL (mark_read_for);
  `(read_at)` WHERE read_at IS NOT NULL (purge). Enum-like `type` service-enforced.
- `schemas.py` — frozen Pydantic contracts + the `type`/`entity`/config constants,
  `NotificationsConfig` (cadence + retention bounds), `CONFIG_KEYS`, the feed/
  count/mark-read/config I/O models.
- `spec.py` — `NOTIFICATIONS_SPEC` + `register_notifications`; key `notifications`,
  2 perms, `depends_on: finance`, `default_config` = 3/5/30, resident=`read` /
  society_admin=`read`,`configure`.
- `repository.py` — SQL-only, `society_id`+`user_id`-scoped. `insert_many` (the
  batched `ON CONFLICT DO NOTHING` fan-out — the conflict target repeats the
  partial index's `index_where`), the unread feed/badge reads, the three mark-read
  writes (one / all / by-entity), and the two purge deletes.
- `service.py` — thin `NotificationsService` facade over feed + config concerns.
- `services/engine.py` — **the choke point** `NotificationEngine`:
  `notify` / `notify_many` (batched fan-out; per-recipient dedupe suffix) /
  `clear_for_entity`. The single seam every create passes through (channel seam
  for future email/push).
- `services/support.py` — `load_config` / `write_config` (partial merge) over
  `society_modules.config`.
- `services/feed.py` — feed page + badge + mark-read (own-scoped; 404 vs
  idempotent-no-op via `exists_owned`).
- `services/config_svc.py` — GET + partial-merge PUT config; audits
  `notifications.config_updated` (the ONLY audited action).
- `services/event_handlers.py` — the 5 handlers (`complaint_new` /
  `complaint_withdrawn` / `complaint_update` / `notice` / clear-on-read). Each
  opens its OWN session (`_in_own_session` — commits, rolls back + logs on error,
  never re-raises), works purely from the payload, resolves recipients
  data-driven.
- `services/dues_rule.py` — the maintenance-dues cadence: `is_fire_day` (pure) +
  `build_for_house` (consolidated total, per-house-per-day dedupe, owner fan-out).
- `services/jobs.py` — the 2 daily worker scans (`run_daily_dues_reminders`,
  `run_daily_read_purge`), fresh-session + commit + rollback PER SOCIETY (failure
  isolation), batched owner resolution (no N+1).
- `handlers.py` — `register_all` (the startup subscription seam; idempotent).
- `api.py` — public contract: `notify` / `notify_many` / `clear_for_entity` /
  `unread_count` + `subscribe_handlers` (startup wiring).
- `router.py` — 6 thin `/notifications/*` routes, dual-gated
  `require_module('notifications')` + permission; static routes before dynamic
  `/{id}/read`; own-scoped by `auth.user_id`. NO create endpoint.
- `alembic/versions/0008_notifications.py` — migration (chained off `0007_notices`);
  the 1 table + 4 indexes. No FK cascade.

Foundation touchpoints (additive):
- `app/main.py` — registers `register_notifications`, mounts the router, calls
  `subscribe_notifications()` at startup (turns the dormant emits live).
- `app/worker/entrypoint.py` — schedules the dues-reminder scan (06:00 UTC) +
  read-purge (04:30 UTC); calls `subscribe_notifications()` in the worker process.
- `alembic/env.py` — imports notifications models.
- `app/platform/roles/{repository,service}.py` — added
  `user_ids_with_permission(society_id, key)` (reverse lookup — resolve "admins"
  = permission-holders, data-driven).
- `app/modules/houses/{repository,service}.py` — added `society_id_for_house`,
  `owner_user_ids_for_house`, batched `owner_user_ids_by_house` (dues recipients,
  no N+1).

## Functions (summary · deps · @location)
- `NotificationEngine.notify_many` — batched idempotent fan-out to many
  recipients (per-recipient dedupe suffix). deps: repo.insert_many.
  @ services/engine.py
- `NotificationRepository.insert_many` — one `INSERT ... ON CONFLICT DO NOTHING`
  (partial-index arbiter with `index_where`); returns inserted count via
  RETURNING. deps: notifications table. @ repository.py
- `event_handlers.on_complaint_created / _withdrawn / _status_changed / _notice_posted`
  — resolve recipients (admins via `user_ids_with_permission`, owners via
  `current_owner_user_ids`) + `notify`. deps: RoleService, HouseService, engine.
  @ services/event_handlers.py
- `event_handlers.on_mark_read` — clear-on-read (both entities). deps:
  engine.clear_for_entity. @ services/event_handlers.py
- `dues_rule.is_fire_day` — pure cadence predicate (advance / due-day / recurring).
  @ services/dues_rule.py
- `dues_rule.build_for_house` — consolidated `maintenance_due` per owner, dedupe
  per (house, day). deps: finance.api.outstanding_dues, HouseService, engine.
  @ services/dues_rule.py
- `jobs.run_daily_dues_reminders / run_daily_read_purge` — per-society-isolated
  worker scans. deps: dues_rule, repo, support. @ services/jobs.py
- `FeedService.*` — feed / badge / mark-read (own-scoped). deps: repo.
  @ services/feed.py
- `ConfigService.get_config / update_config` — read / partial-merge + audit.
  deps: support, AuditService. @ services/config_svc.py

## Tables owned
`notifications`.

## Endpoints
`GET /notifications` (unread feed + count, paginated) · `GET /notifications/unread-count`
· `POST /notifications/{id}/read` · `POST /notifications/read-all` ·
`GET /notifications/config` · `PUT /notifications/config`. All dual-gated
`require_module('notifications')` + permission; society + caller from the JWT;
own-scoped (a caller only ever touches their own rows). No create endpoint —
notifications are created by the engine (event handlers + worker) only.

## Audited actions (emitted)
`notifications.config_updated` (before/after, in-transaction). Individual
notification creates/reads are NOT audited (high-volume, transient — docs §5).

## Cross-module wiring
- **Consumes:** the in-process event dispatcher (`app/common/events.py`) —
  subscribes `complaint.created` / `complaint.withdrawn` / `complaint.status_changed`
  / `notice_posted` / `complaint.mark_read` / `notice.mark_read`; Finance
  (`outstanding_dues`, `maintenance_due_day`); House & Occupancy
  (`current_owner_user_ids`, `owner_user_ids_for_house`,
  `owner_user_ids_by_house`, `society_id_for_house`); Roles
  (`user_ids_with_permission`); foundation `TenantContext` + gates +
  `AuditService` + the worker.
- **Provides:** `notify` / `notify_many` / `clear_for_entity` / `unread_count`
  (`notifications/api.py`) + `subscribe_handlers` (startup wiring).
- **Emitters (minimal additive change):** `complaints/events.py`, `notices/events.py`
  gained an optional `session=` kwarg (attached to the event payload so handlers
  write in the emitter's transaction — atomic); call sites pass `self._session`.
  Event names + payload data are unchanged, so this is wiring, not behavior. Zero
  change to the bus (`app/common/events.py`).

## Deviations from design (drift vs docs/modules/notifications.md)
1. **`mark_read_for` is an EVENT, not a direct call.** Design §7 describes reading
   modules calling `Notifications.mark_read_for(...)` directly; the as-built
   emitters (shipped before Notifications) `emit("complaint.mark_read" /
   "notice.mark_read", {...})`. Notifications subscribes to these — functionally
   equivalent, zero emitter change.
2. **Handlers run IN the emitter's transaction (session threaded on the payload).**
   Design §4.1 says handlers run "inline, in the emitter's request transaction, so
   a handler that writes rows commits/rolls back atomically with the state
   change." The bus is frozen at `emit(event, payload)`, so the emitters now put
   their request `session` on the payload (`events.emit_*(..., session=self._session)`)
   and the handler uses it — the complaint/notice and its notifications are ONE
   atomic unit (no crash-window gap; the design's actual intent). Each handler
   wraps its writes in a **SAVEPOINT** (`session.begin_nested()`): a handler
   failure rolls back ONLY its own writes and is logged+swallowed, so a bad
   subscriber can never poison the emitter's transaction (plan §7 — containment).
   This required a MINIMAL, additive change to the emitters (one `session=` kwarg
   at each emit site; the `events.py` wrapper attaches it) — not a behavior change.
   The publish path additionally `flush()`es the notice before emit so the handler
   sees its id in the same transaction. (An earlier own-session approach was
   rejected in review: it wasn't atomic and its independent commits broke test
   isolation.)
3. **Config default `dues_advance_days`/`interval`/`retention` = 3/5/30** seeded in
   the spec's `default_config` (applied on module enable), matching docs §8.
4. **Two new House interfaces + one Roles interface** were needed for data-driven
   recipient resolution (per-house owners; house→society; permission-holders) —
   pure additions, no change to existing behavior.

Everything else matches `docs/modules/notifications.md`.
