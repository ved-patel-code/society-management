# Notifications Module — Design

> Design doc. Foundation reading: [../01-project-overview](../01-project-overview.md) · [../02-architecture](../02-architecture.md) · [../03-backend-and-db-principles](../03-backend-and-db-principles.md) · [../05-cross-module-contracts](../05-cross-module-contracts.md) · [../platform/platform-foundation](../platform/platform-foundation.md)
>
> **Confirmed decisions baked in:** **in-app only** in v1 (email via `EmailSender` + push = future) · a **modular, reusable** engine — two paths: **event-driven** (immediate) + **scheduled reminders** (recurring); other modules plug in without changing the engine · dues cadence = **advance (X days before) + due-day + every N days while unpaid, until paid**, admin sets X/N, each fire is **one consolidated notification** with the total across all unpaid months · recipients = **residents AND admins** · **clear-on-read** — a notification leaves the feed once read, **including when the underlying item is read anywhere** (via a `mark_read_for` hook) · **no opt-out/preferences** in v1.

## 1. Purpose & scope
A per-society **notification + reminder engine**. It delivers **in-app** notifications to owners (complaint updates, maintenance-due reminders, new notices) and admins (new/withdrawn complaint alerts — the red-dot/counter), and hosts a **reusable scheduled-reminder** system that any module can register rules with. It owns delivery, the in-app feed, and dedupe; the emitting modules stay ignorant of notification logic.

**Out of scope (now / future):**
- **Email & push channels** — v1 is in-app only; the `notify` API keeps a channel seam so `EmailSender`/push slot in later without touching callers.
- **Per-user preferences / opt-out / mute** — everyone receives their relevant notifications in v1.
- **Digests / batching**, **tenant recipients** (owners only), **read history** (the feed clears on read — it is not a permanent archive).

**Mobile:** backend-first; the shell shows an unread **badge** (count) + a feed panel; both the resident and admin **portals** ([platform-foundation §5.1](../platform/platform-foundation.md)) render the same bell.

## 2. Audience & permissions
All users read their **own** feed; admins additionally configure the reminder cadence. **Receiving** a notification needs no permission — it's decided by recipient resolution (§4).

Permissions (`notifications.*`):
- `notifications.read` — read own feed, unread count, mark-read / mark-all-read (residents + admin).
- `notifications.configure` — admin: set reminder config (advance days, interval, retention).

Default seeding: **resident** → `notifications.read`. **society_admin** → `notifications.read`, `notifications.configure`. Gated `require_module('notifications')` + `require_permission(...)`.

## 3. Data model
`id` BIGINT identity PK, `created_at`. Tenant-scoped by `society_id`. DB holds only PK/FK/NOT NULL/UNIQUE; all logic in the service layer.

**notifications** — one row per recipient per event.
- `society_id` FK, `user_id` FK (recipient), `type` (extensible string: `complaint_update` | `maintenance_due` | `notice` | `complaint_new` | `complaint_withdrawn` | …), `title`, `body`, `payload` JSONB (data for rendering + deep-link), `entity_type` NULL, `entity_id` NULL (deep-link target **and** the key for `mark_read_for`), `dedupe_key` NULL, `read_at` NULL.
- **Partial UNIQUE(`society_id`, `dedupe_key`)** WHERE `dedupe_key IS NOT NULL` — idempotency for scheduled fires (a re-run can't double-post).
- idx(`user_id`, `read_at`) — the feed + unread count (hot path, `WHERE read_at IS NULL`).
- idx(`user_id`, `entity_type`, `entity_id`) WHERE `read_at IS NULL` — `mark_read_for` lookup.
- idx(`read_at`) — the purge job.

**No** preferences table (no opt-out v1). **No** deliveries table (in-app only — a per-channel `notification_deliveries` table is the future add for email/push). **Reminder rules live in code** (a registry), not a table; scheduler state is derived (stateless — see §4/§9), so there is no "last-fired" table.

**Config** (not a table — lives in `society_modules.config`, see §8): `dues_advance_days`, `dues_reminder_interval_days`, `read_retention_days`.

## 4. Business rules

### 4.1 The engine (reusable)
- **`notify(society_id, user_id, type, payload, ref=(entity_type, entity_id), dedupe_key=None)`** — creates one in-app notification; **idempotent** on `dedupe_key` (insert-or-skip). This is the single choke point; the channel seam here is where email/push attach later.
- **Event-driven path** — modules `emit(event, payload)` to the in-process **domain-event dispatcher** ([docs/05 §3](../05-cross-module-contracts.md)); Notifications registers `event → handler`; each handler resolves recipients and calls `notify`. Emitting modules never build notifications themselves.
- **Scheduled path** — a **reminder registry**: modules register `ReminderRule(key, is_fire_day(due_date, cfg, today), build(society, house))`. A daily worker runs enabled rules per society. Adding a future recurring reminder = registering a rule; the engine is untouched.

### 4.2 Event subscriptions (v1)
| Event (emitter) | Notification `type` | Recipients |
|---|---|---|
| `complaint.status_changed` (Complaints) | `complaint_update` | the raising owner (`raised_by`) |
| `complaint.created` (Complaints) | `complaint_new` | admins = users holding `complaints.read_all` in the society |
| `complaint.withdrawn` (Complaints) | `complaint_withdrawn` | those admins |
| `notice_posted` (Notice Board) | `notice` | all current owners (`current_owner_user_ids`) |

Recipient resolution is **data-driven** — "admins" = whoever currently holds the relevant permission (no frozen recipient list); owner recipients come from the Occupancy interface. Each `notice_posted` fans out to one row per current owner.

### 4.3 Dues reminders (the v1 scheduled rule)
- Consumes **Finance** `outstanding_dues(house_id)` + the society's `maintenance_due_day`. Reminder **cadence params live in Notifications config**, not Finance.
- **Cadence (per owing house):** anchor on the **most recent outstanding `due_date`**; fire when:
  - `today == due_date - X` → **advance** heads-up (X = `dues_advance_days`),
  - `today == due_date` → **due-day** reminder,
  - `today > due_date` AND `(today - due_date) % N == 0` → **recurring** (N = `dues_reminder_interval_days`), while any balance remains.
- **Consolidated:** each fire builds **one** `maintenance_due` notification whose total = **sum of all unpaid months** for the house (never one-per-month).
- **Stateless & idempotent:** `dedupe_key = "dues:{house_id}:{today}"` → at most one dues reminder per house per day; the cadence is computed from dates + config, so no "last-fired" state is stored. A later scheduled fire is a **new** notification even if the earlier one was already read while still unpaid. Reminders **stop automatically** once the house is fully paid (no outstanding dues → nothing to build).

### 4.4 Clear-on-read (the feed empties as you read)
- The feed = notifications with `read_at IS NULL`; the **unread badge** counts them.
- `read_at` is set (row leaves the feed) when **either**:
  - (a) the user opens the notification — `POST /notifications/{id}/read` (or mark-all), **or**
  - (b) the **source module reports the item read** via **`mark_read_for(user_id, entity_type, entity_id)`** — clears all of that user's pending notifications for that entity. Wired: Notice Board on notice-open, Complaints on complaint-open (raiser and admin). See §7.
- Read rows are retained briefly then **purged** by the worker after `read_retention_days` (kept meanwhile so a same-day re-open/idempotency check is cheap).

## 5. Audited actions
Written to `audit_log` (in-transaction, append-only):
- `notifications.config_updated` — before/after of `dues_advance_days` / `dues_reminder_interval_days` / `read_retention_days`.

Individual notification **creates and reads are NOT audited** — high-volume, transient, and not admin state-changes (the source events are already audited in their own modules).

## 6. Endpoints (`/notifications/*`, society from JWT)
- `GET /notifications` — the caller's **unread feed** (newest first, paginated) + `unread_count`. (`notifications.read`)
  - Query: single indexed select on (`user_id`, `read_at`) WHERE `read_at IS NULL`.
- `GET /notifications/unread-count` — lightweight badge count only. (`notifications.read`)
- `POST /notifications/{id}/read` — mark one read (clears it). (`notifications.read`, own only)
- `POST /notifications/read-all` — mark all own read. (`notifications.read`)
- `GET /notifications/config` — read reminder config. (`notifications.configure`)
- `PUT /notifications/config` — set `dues_advance_days` / `dues_reminder_interval_days` / `read_retention_days`. (`notifications.configure`)

Notifications are **created by the engine** (event handlers + worker), not by a public POST — there is no "create notification" endpoint.

## 7. Inter-module contracts
- **Provides:**
  - `notify(society_id, user_id, type, payload, ref?, dedupe_key?)` — core create (idempotent).
  - **`mark_read_for(user_id, entity_type, entity_id)`** — the clear-on-read hook reading modules call.
  - `unread_count(user_id)` — for the shell badge.
  - **Event-subscription** + **reminder-rule** registration (the two plug-in seams).
- **Consumes:**
  - the in-process **event dispatcher** ([docs/05 §3](../05-cross-module-contracts.md)) — subscribes to `complaint.created` / `complaint.status_changed` / `complaint.withdrawn` / `notice_posted`.
  - **Finance** — `outstanding_dues(house_id)` + `maintenance_due_day` (dues rule).
  - **House & Occupancy** — `current_owner_user_ids(society_id)` (notice recipients).
  - **Foundation** — the **permission catalog** (resolve "admins" = holders of a permission), `TenantContext`, the worker/scheduler, `AuditService`.
- **Wiring expected in other modules (documented there):**
  - **Complaints** emits `complaint.created` / `complaint.withdrawn` (in addition to `complaint.status_changed`); calls `mark_read_for('complaint', id)` when the raiser opens their complaint and when an admin opens a complaint.
  - **Notice Board** calls `mark_read_for('notice', id)` when an owner opens a notice (on the `notice_reads` insert).

## 8. Feature flag / config
- Module key `notifications`, `depends_on = ['finance']` (the dues rule needs Finance). Complaint/notice events are **soft** dependencies — a handler is a no-op if that module isn't enabled for the society.
- `society_modules.config` for `notifications`:
  - `dues_advance_days` (X, default **3**),
  - `dues_reminder_interval_days` (N, default **5**),
  - `read_retention_days` (default **30**).

## 9. Background jobs (worker)
- **Dues reminder scan** (daily) — for each society with `notifications` + `finance` enabled, evaluate the dues `ReminderRule` against each owing house (stateless cadence from due dates + config) and create consolidated `maintenance_due` notifications (idempotent via `dedupe_key`). Consumes Finance interfaces.
- **Read-purge** (daily) — delete notifications whose `read_at` is older than `read_retention_days`.

## 10. Open questions / future
- **Email + push channels** (the channel seam is in `notify`); **per-user preferences / opt-out / mute**; **digests / quiet hours**; **tenant recipients** (with tenant login); **more admin alert types** (payment recorded, new resident onboarded) and **more scheduled rules** (e.g. AGM reminders) — all add via a new event subscription or reminder rule, no engine change.

## 11. Resolved decisions
1. **In-app only** in v1; `notify` keeps a channel seam for email/push later.
2. **Reusable engine**: event-driven (immediate) + scheduled reminder-rule registry; modules plug in by emitting an event or registering a rule.
3. **Dues cadence** = advance (X) + due-day + every N days while unpaid; **admin-set X/N**; **one consolidated notification** per fire (total of all unpaid months); **stateless/idempotent** via `dedupe_key`; auto-stops when paid.
4. **Recipients = owners AND admins**; admins resolved by permission (`complaints.read_all`), owners via the Occupancy interface.
5. **Clear-on-read** — feed empties as items are read, including when the underlying item is read anywhere (`mark_read_for`); read rows purged after `read_retention_days`.
6. **No opt-out/preferences** in v1; notifications not individually audited (only config changes).
