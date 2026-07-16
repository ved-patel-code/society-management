# Notifications API Reference

Endpoint-level reference for the in-app notification feed: fetching your unread notifications,
the unread badge count, marking notifications read, and the admin reminder-cadence config.

**Scope note:** there are no super-admin endpoints in this module, and no endpoint ever takes a
user id or society id as a parameter — every route resolves "you" and "your society" from the
JWT, so a caller can only ever act on their own feed. There's also no "create notification"
endpoint — notifications are produced only internally, by other modules' events and a daily
reminder job, never by a direct API call.

Base path: **`/notifications`**.

---

## How notifications work

- **The feed shows unread notifications only — full stop.** There is no filter, no query
  param, and no separate endpoint to see notifications you've already read. Once a notification
  is marked read (by any of the means below), it disappears from every response you can query.
  This isn't a bug to work around — it's the intended design: the feed is a to-do list, not a
  permanent history.
- **A notification clears in three ways:**
  1. You explicitly mark it read — `POST /notifications/{id}/read` (one) or
     `POST /notifications/read-all` (all of them).
  2. You open the thing it's about. Reading a complaint or a notice (via their own modules'
     detail endpoints) automatically clears any related notification for you — you don't need
     to separately mark it read here.
  3. It ages out. Read notifications are permanently, irreversibly deleted after the society's
     configured retention window (`read_retention_days`, default 30) by a nightly cleanup job.
     There's no way to recover a purged notification through any endpoint.
- **What generates a notification** (for context — none of this is triggered by calling this
  module's own API):
  - A new complaint → notifies admins.
  - A complaint withdrawn → notifies admins.
  - A complaint's status changes → notifies the resident who raised it.
  - A notice is published → notifies every current house owner.
  - Maintenance dues become reminder-worthy → notifies the house's current owner(s) (see
    "Dues reminders" below).
- **Dues reminders are consolidated** — one notification per house per fire, covering the
  **total** of everything currently outstanding for that house (however many months), never one
  notification per unpaid month. It fires on three occasions, driven by the society's config:
  a configurable number of days *before* the most recent due date (a heads-up), on the due date
  itself, and then repeating every N days for as long as the house still owes something. Once
  the house is fully paid, reminders simply stop — there's no explicit "cancel."
- **This module requires `finance` to be enabled** for the society (a hard module dependency —
  attempting to enable `notifications` without `finance` already on fails at the module-allocation
  level with a `409 dependency_error`, not something this module's own endpoints would show
  you). Complaints and Notice Board are **not** hard dependencies — if either of those modules
  is disabled, their notifications just never get generated; nothing here breaks.

## Common error envelope

Same shape as every other module — see the
[auth API reference](auth.md#common-error-envelope) for the full explanation.

| HTTP status | `code` | Meaning |
|-------------|--------|---------|
| 422 | `validation_error` | Bad input (bad pagination params, bad config values, or an empty config update). |
| 403 | `permission_denied` | Missing `notifications.read` or `notifications.configure`, or the module isn't enabled. |
| 404 | `not_found` | The notification doesn't exist, or isn't yours. |

All auth-related 401/403s (missing bearer token, expired token, forced password change) are
identical to every other protected endpoint — see the
[auth reference](auth.md#errors-5) rather than repeated per-endpoint here.

**Module/permission errors** (apply to every endpoint below, permission key varies):

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 403 | `module_disabled` | `"No active society."` | `{"module_key": "notifications"}` | Caller's token has no active society. |
| 403 | `module_disabled` | `"Module 'notifications' is not enabled for this society."` | `{"module_key": "notifications"}` | Notifications module isn't enabled. |
| 403 | `permission_denied` | `"You do not have permission to perform this action."` | `{"required_permission": "notifications.read"}` (or `"notifications.configure"`) | Caller's role(s) lack the needed permission. |

---

## `GET /notifications`

Your unread notification feed, newest first, paginated.

**Permission:** `notifications.read`.

### Request

| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `page` | integer ≥ 1 | `1` | |
| `page_size` | integer, 1–100 | `20` | |

```
GET /notifications?page=1&page_size=20
```

### Response — `200 OK`

| Field | Type | Notes |
|-------|------|-------|
| `items` | array of `NotificationOut` | This page. |
| `unread_count` | integer | **Total** unread count across your whole feed — independent of pagination, not just this page's count. |
| `page` | integer | Echoed. |
| `page_size` | integer | Echoed. |

`NotificationOut`:

| Field | Type | Notes |
|-------|------|-------|
| `id` | integer | |
| `type` | string | `"complaint_new"` \| `"complaint_update"` \| `"complaint_withdrawn"` \| `"notice"` \| `"maintenance_due"`. |
| `title` | string | |
| `body` | string | |
| `payload` | object | Structured data for deep-linking/rendering — shape depends on `type` (see below). |
| `entity_type` | string \| null | `"complaint"` \| `"notice"` \| `"house"` \| `null` — what this notification is about. |
| `entity_id` | integer \| null | The id of that entity. |
| `created_at` | datetime | |

`payload` shape by `type`:

| `type` | `payload` fields |
|--------|------------------|
| `complaint_new` | `{complaint_id, reference, house_id, category_id}` |
| `complaint_withdrawn` | `{complaint_id, reference, house_id}` |
| `complaint_update` | `{complaint_id, reference, from_status, to_status, note}` |
| `notice` | `{notice_id, title, published_at}` |
| `maintenance_due` | `{house_id, outstanding_total, months_outstanding, anchor_due_date}` — `outstanding_total` is a decimal string, `anchor_due_date` an ISO date string. |

```json
{
  "items": [
    {
      "id": 301,
      "type": "complaint_update",
      "title": "Complaint update",
      "body": "Your complaint C-000123 moved from in_progress to resolved.",
      "payload": {"complaint_id": 501, "reference": "C-000123", "from_status": "in_progress", "to_status": "resolved", "note": "Plumber fixed the leak."},
      "entity_type": "complaint",
      "entity_id": 501,
      "created_at": "2026-07-13T09:10:00Z"
    },
    {
      "id": 300,
      "type": "maintenance_due",
      "title": "Maintenance dues pending",
      "body": "You have 2 month(s) of maintenance dues outstanding, totalling 5000.00.",
      "payload": {"house_id": 103, "outstanding_total": "5000.00", "months_outstanding": 2, "anchor_due_date": "2026-07-01"},
      "entity_type": "house",
      "entity_id": 103,
      "created_at": "2026-07-13T06:00:00Z"
    }
  ],
  "unread_count": 2,
  "page": 1,
  "page_size": 20
}
```

### Errors

None beyond the shared module/permission errors and standard pagination-param validation.

---

## `GET /notifications/unread-count`

Lightweight badge count — just the number, no items.

**Permission:** `notifications.read`.

### Request

No parameters.

### Response — `200 OK`

```json
{
  "unread_count": 2
}
```

### Errors

None beyond the shared module/permission errors above.

---

## `POST /notifications/{notification_id}/read`

Marks a single notification read. Idempotent — marking an already-read notification of yours
succeeds with no error.

**Permission:** `notifications.read`. Own notifications only.

### Request

Path param `notification_id` only, no body.

### Response — `200 OK`

| Field | Type | Notes |
|-------|------|-------|
| `cleared` | integer | `1` if this call actually marked it read; `0` if it was already read (idempotent no-op). |

```json
{
  "cleared": 1
}
```

### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"Notification not found."` | `{"notification_id": ...}` | No such notification, or it belongs to someone else. Both cases return the identical response — you can't use this to probe for other users' notification ids. |

---

## `POST /notifications/read-all`

Marks every one of your unread notifications read, in one call.

**Permission:** `notifications.read`.

### Request

No parameters.

### Response — `200 OK`

| Field | Type | Notes |
|-------|------|-------|
| `cleared` | integer | Count actually cleared. `0` if your feed was already empty — not an error. |

```json
{
  "cleared": 2
}
```

### Errors

None beyond the shared module/permission errors above.

---

## `GET /notifications/config`

Returns the society's reminder cadence and retention configuration.

**Permission:** `notifications.configure` (admin only).

### Request

No parameters.

### Response — `200 OK`

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `dues_advance_days` | integer | `3` | How many days before the most recent outstanding due date to send a heads-up reminder. |
| `dues_reminder_interval_days` | integer | `5` | Once past the due date, how often (in days) to repeat the reminder while still unpaid. |
| `read_retention_days` | integer | `30` | How long a read notification is kept before being permanently purged. |

```json
{
  "dues_advance_days": 3,
  "dues_reminder_interval_days": 5,
  "read_retention_days": 30
}
```

### Errors

None beyond the shared module/permission errors above.

---

## `PUT /notifications/config`

Updates the society's configuration. **Partial merge** — only the fields you send are
changed; omitted fields keep their current value.

**Permission:** `notifications.configure` (admin only).

### Request

| Field | Type | Required | Bounds | Notes |
|-------|------|----------|--------|-------|
| `dues_advance_days` | integer \| null | No | 0–28 | |
| `dues_reminder_interval_days` | integer \| null | No | 1–90 | |
| `read_retention_days` | integer \| null | No | 1–365 | |

At least one field must be present. **Unknown fields are rejected** — this endpoint doesn't
silently ignore typos in field names.

```json
{
  "dues_advance_days": 5,
  "dues_reminder_interval_days": 7
}
```

### Response — `200 OK`

`ConfigOut` — the full resulting configuration after the merge (see `GET /notifications/config`
for field list).

```json
{
  "dues_advance_days": 5,
  "dues_reminder_interval_days": 7,
  "read_retention_days": 30
}
```

### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 422 | `validation_error` | `"Provide at least one config field to update."` | `{"fields": ["dues_advance_days", "dues_reminder_interval_days", "read_retention_days"]}` | Request body is entirely empty (all fields omitted). |
| 422 | `validation_error` | Standard schema errors | | A field is outside its bounds, or the body contains an unrecognized field name. |
