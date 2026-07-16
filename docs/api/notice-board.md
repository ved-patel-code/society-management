# Notice Board API Reference

Endpoint-level reference for society-wide notices: composing/publishing/withdrawing, the
resident feed, attachments, mark-as-read, and admin read receipts.

**Scope note:** there are no super-admin endpoints in this module. Access is permission-driven —
a `society_admin` (holding `notices.publish` and `notices.read_receipts`, plus `notices.read`)
can compose, manage, and see receipts; a `resident` (holding only `notices.read`) sees the
active feed, opens notices (marking them read), and marks everything read.

Base path: **`/notices`**.

---

## How notices work

- **Lifecycle:** `draft → published → withdrawn`. `withdrawn` is terminal — nothing comes back
  from it. There's no "unpublish back to draft"; withdrawing is how you take a published notice
  down, and it's also how you discard a draft you no longer want.
- **Expiry is never stored as a status** — a notice has an optional `expires_at` timestamp, and
  "active" is computed at read time: `status == "published"` and (`expires_at` is unset, or
  still in the future). Once `expires_at` passes, the notice quietly drops out of the resident
  feed on its own — no job runs, no status changes — and it becomes visible only in the admin
  archive.
- **`last_edited_at`** is stamped only when the title or body content actually changes; toggling
  `is_pinned` or changing `expires_at` alone doesn't mark it edited.
- **Read tracking is per-user, first-open only.** Opening a notice (`GET /notices/{id}`)
  records that you've read it — the first read timestamp sticks even if you open it again later.
  There's no "mark unread." `POST /notices/read-all` does the same thing in bulk for every
  currently active notice.
- **Read receipts are a live count, not a snapshot.** The denominator is *whoever currently owns
  a house in the society right now* — not a frozen list from when the notice was published. If
  someone becomes an owner after the notice went out, they show up as unread until they open it;
  if a former owner already read it but has since sold their house, they drop out of both the
  read and unread lists entirely (they're not a current owner anymore).
- **Attachments have no count limit** — attach as many files as you want. They're not removable
  once a notice is withdrawn in the sense that withdrawing doesn't touch them; they stay
  attached to the (now-withdrawn) notice in Vault.
- **Drafts and withdrawn notices are only visible to admins.** A resident requesting a draft or
  withdrawn notice by id gets the same `404` as if it didn't exist at all — no existence leak.

## Common error envelope

Same shape as every other module — see the
[auth API reference](auth.md#common-error-envelope) for the full explanation.

| HTTP status | `code` | Meaning |
|-------------|--------|---------|
| 422 | `validation_error` | Bad input or a business rule was violated. |
| 403 | `permission_denied` | Missing the required permission, or the module isn't enabled. |
| 404 | `not_found` | Notice or attachment doesn't exist (or isn't visible to you). |
| 409 | `conflict` | Illegal status transition (e.g. publishing an already-withdrawn notice, editing a withdrawn one). |

All auth-related 401/403s (missing bearer token, expired token, forced password change) are
identical to every other protected endpoint — see the
[auth reference](auth.md#errors-5) rather than repeated per-endpoint here.

**Module/permission errors** (apply to every endpoint below, permission key varies):

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 403 | `module_disabled` | `"No active society."` | `{"module_key": "notices"}` | Caller's token has no active society. |
| 403 | `module_disabled` | `"Module 'notices' is not enabled for this society."` | `{"module_key": "notices"}` | Notices module isn't enabled. |
| 403 | `module_disabled` | `"Module 'vault' is not enabled for this society."` | `{"module_key": "vault"}` | Attachment endpoints only — Vault isn't enabled, even if `notices` is. |
| 403 | `permission_denied` | `"You do not have permission to perform this action."` | `{"required_permission": "notices.read"}` (or `"notices.publish"` / `"notices.read_receipts"`) | Caller's role(s) lack the needed permission. |

---

## `GET /notices`

Lists notices. Residents always get the active feed; admins can additionally filter by status
or view the archive scope.

**Permission:** `notices.read`.

### Request

| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `page` | integer ≥ 1 | `1` | |
| `page_size` | integer, 1–100 | `20` | |
| `status` | string | — | **Admin only** (ignored for residents). `"draft"` \| `"published"` \| `"withdrawn"`. |
| `scope` | string | — | **Admin only** (ignored for residents). `"active"` or `"archive"`. |

If neither `status` nor `scope` is given, everyone (including admins) gets the active feed. A
resident's `status`/`scope` query params are silently ignored — they always see the active feed
regardless of what's passed.

Ordering: pinned first, then newest `published_at` first.

```
GET /notices?page=1&page_size=20
```

### Response — `200 OK`

| Field | Type | Notes |
|-------|------|-------|
| `items` | array of `NoticeListItemOut` | This page. |
| `total` | integer | Total matching notices across all pages. |
| `unread_count` | integer | Count of currently-active notices the caller hasn't read yet — independent of the current page/filter. |

`NoticeListItemOut`:

| Field | Type | Notes |
|-------|------|-------|
| `id` | integer | |
| `title` | string | |
| `status` | string | `"draft"` \| `"published"` \| `"withdrawn"`. |
| `is_pinned` | boolean | |
| `published_at` | datetime \| null | |
| `expires_at` | datetime \| null | |
| `last_edited_at` | datetime \| null | |
| `attachment_count` | integer | |
| `is_read` | boolean | Whether the caller has opened this notice. |
| `created_at` | datetime | |
| `updated_at` | datetime | |

```json
{
  "items": [
    {
      "id": 12,
      "title": "Water supply maintenance on Sunday",
      "status": "published",
      "is_pinned": true,
      "published_at": "2026-07-10T08:00:00Z",
      "expires_at": "2026-07-14T00:00:00Z",
      "last_edited_at": null,
      "attachment_count": 1,
      "is_read": false,
      "created_at": "2026-07-10T07:55:00Z",
      "updated_at": "2026-07-10T08:00:00Z"
    }
  ],
  "total": 1,
  "unread_count": 1
}
```

### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 422 | `validation_error` | `"Unknown status filter."` | | Bad `status` value (admin caller). |
| 422 | `validation_error` | `"Unknown scope; expected 'active' or 'archive'."` | | Bad `scope` value (admin caller). |

---

## `POST /notices`

Composes a notice, as a draft by default. Set `publish: true` to publish it immediately in the
same call.

**Permission:** `notices.publish`.

### Request

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `title` | string, 1–200 chars | Yes | Trimmed; blank (after trimming/sanitizing) is rejected. |
| `body` | string, 1–50,000 chars | Yes | Rich text — sanitized server-side (see below). Blank after sanitizing is rejected. |
| `is_pinned` | boolean | No (default `false`) | |
| `expires_at` | datetime \| null | No | |
| `publish` | boolean | No (default `false`) | If `true`, publishes immediately (stamps `published_at`, emits the `notice_posted` event) instead of leaving it as a draft. |

**Body sanitization:** the body is rich text, sanitized against an allow-list — permitted tags:
`p`, `br`, `span`, `strong`, `b`, `em`, `i`, `u`, `s`, `ul`, `ol`, `li`, `a`, `h1`–`h4`,
`blockquote`, `code`, `pre`, `hr`. Only `a` tags may carry attributes (`href`, `title`), and only
`http`, `https`, and `mailto` link schemes are allowed — anything else (including
`javascript:`/`data:`) is stripped. No `img`, `iframe`, `style`, `script`, inline styling, or
event-handler attributes survive. Submit whatever HTML you like; what comes back (and what's
stored) is the sanitized version.

```json
{
  "title": "Water supply maintenance on Sunday",
  "body": "<p>Water supply will be <strong>shut off</strong> from 10am to 2pm for tank cleaning.</p>",
  "is_pinned": true,
  "expires_at": "2026-07-14T00:00:00Z",
  "publish": true
}
```

### Response — `200 OK`

`NoticeDetailOut`:

| Field | Type | Notes |
|-------|------|-------|
| `id` | integer | |
| `title` | string | |
| `body` | string | The sanitized version. |
| `status` | string | `"draft"` or `"published"`, depending on `publish`. |
| `is_pinned` | boolean | |
| `published_at` | datetime \| null | |
| `expires_at` | datetime \| null | |
| `last_edited_at` | datetime \| null | `null` on creation. |
| `created_by` | integer | |
| `withdrawn_at` | datetime \| null | |
| `withdrawn_by` | integer \| null | |
| `is_read` | boolean | `false` on creation (the author hasn't "opened" it via the read-tracking path). |
| `created_at` | datetime | |
| `updated_at` | datetime | |
| `attachments` | array of `NoticeAttachmentOut` | Empty on creation. |

`NoticeAttachmentOut`: `id`, `vault_document_id`, `preview_url` (string \| null),
`download_url` (string \| null — both `null` if Vault can't currently produce a link), `created_at`.

```json
{
  "id": 12,
  "title": "Water supply maintenance on Sunday",
  "body": "<p>Water supply will be <strong>shut off</strong> from 10am to 2pm for tank cleaning.</p>",
  "status": "published",
  "is_pinned": true,
  "published_at": "2026-07-13T09:00:00Z",
  "expires_at": "2026-07-14T00:00:00Z",
  "last_edited_at": null,
  "created_by": 9,
  "withdrawn_at": null,
  "withdrawn_by": null,
  "is_read": false,
  "created_at": "2026-07-13T09:00:00Z",
  "updated_at": "2026-07-13T09:00:00Z",
  "attachments": []
}
```

### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 422 | `validation_error` | `"Body must not be empty."` | | `body` is only markup (e.g. `"<p></p>"`), leaving nothing after sanitizing. |
| 422 | `validation_error` | Standard schema errors | | `title`/`body` blank or over length. |

---

## `GET /notices/{notice_id}`

Fetches a notice's full detail, and marks it read for the caller as a side effect.

**Permission:** `notices.read`.

### Request

Path param `notice_id` only.

### Response — `200 OK`

`NoticeDetailOut` (see field list above), with `is_read: true` always (this call is what marks
it read).

### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"Notice not found."` | | No such notice, **or** it exists but is a `draft`/`withdrawn` notice and the caller isn't a manager (`notices.publish` or super-admin) — both cases return the identical response, so a resident can't distinguish "doesn't exist" from "exists but hidden from you." |

---

## `PATCH /notices/{notice_id}`

Edits a notice. Any subset of the fields below; only send what you want to change.

**Permission:** `notices.publish`.

### Request

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `title` | string, 1–200 chars \| null | At least one field required | Same sanitization rules as create. |
| `body` | string, 1–50,000 chars \| null | At least one field required | Same sanitization rules as create. |
| `is_pinned` | boolean \| null | At least one field required | |
| `expires_at` | datetime \| null | At least one field required | **Explicitly sending `expires_at: null` clears the expiry.** Omitting the field entirely leaves the existing expiry untouched — these are different things in this endpoint. |

```json
{
  "is_pinned": false
}
```

### Response — `200 OK`

`NoticeDetailOut`, reflecting the edit. `last_edited_at` is updated only if `title` or `body`
content actually changed.

### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"Notice not found."` | | No such notice. |
| 409 | `conflict` | `"A withdrawn notice cannot be edited."` | `{"status": "withdrawn"}` | Notice has been withdrawn. |
| 422 | `validation_error` | `"Provide at least one field to edit."` | | Request body is entirely empty. |
| 422 | `validation_error` | `"Body must not be empty."` | | New `body` is markup-only. |

---

## `POST /notices/{notice_id}/publish`

Publishes a draft notice. Only legal from `draft`.

**Permission:** `notices.publish`.

### Request

Path param only, no body.

### Response — `200 OK`

`NoticeDetailOut`, now `status: "published"`, `published_at` set. Emits the `notice_posted`
event (see below).

### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"Notice not found."` | | No such notice. |
| 409 | `conflict` | `"Cannot move a notice from '{from_status}' to 'published'."` | `{"from_status": ..., "to_status": "published"}` | Notice is already `published` or is `withdrawn`. |

---

## `POST /notices/{notice_id}/withdraw`

Withdraws a notice — legal from either `draft` (discarding an unpublished draft) or
`published` (taking a live notice down). Terminal: a withdrawn notice can never be
un-withdrawn.

**Permission:** `notices.publish`.

### Request

Path param only, no body.

### Response — `200 OK`

`NoticeDetailOut`, now `status: "withdrawn"`, `withdrawn_at`/`withdrawn_by` set. Attachments
stay attached (not removed).

### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"Notice not found."` | | No such notice. |
| 409 | `conflict` | `"Cannot move a notice from 'withdrawn' to 'withdrawn'."` | `{"from_status": "withdrawn", "to_status": "withdrawn"}` | Already withdrawn. |

---

## `POST /notices/{notice_id}/attachments`

Attaches a file to a notice. No limit on how many attachments a notice can have.

**Permission:** `notices.publish`. Requires the `vault` module enabled.

### Request

`multipart/form-data`:

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `file` | file | Yes | |

### Response — `200 OK`

`NoticeDetailOut`, with the new attachment included in `attachments`.

### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"Notice not found."` | | No such notice. |
| 415 | `file_type_not_allowed` | (Vault's message) | | Blocked file type/extension — see the [house-occupancy reference](house-occupancy.md#post-housesouse_idoccupancypartyid-proof) for the exact Vault error shape. |
| 413 | `storage_quota_exceeded` | (Vault's message) | | Would exceed the society's Vault storage limit. |

---

## `DELETE /notices/{notice_id}/attachments/{attachment_id}`

Removes an attachment (soft-deletes it in Vault, then removes the link).

**Permission:** `notices.publish`. Requires the `vault` module enabled.

### Request

Path params only, no body.

### Response

`204 No Content` — empty body.

### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"Attachment not found."` | `{"notice_id": ..., "attachment_id": ...}` | No such attachment on this notice. |

---

## `POST /notices/read-all`

Marks every currently active notice as read for the caller, in one call.

**Permission:** `notices.read`.

### Request

No parameters.

### Response

`204 No Content` — empty body.

### Errors

None beyond the shared module/permission errors above.

---

## `GET /notices/{notice_id}/receipts`

Admin view of who has and hasn't read a notice, scoped to the society's **current** house
owners (not a snapshot from when it was published).

**Permission:** `notices.read_receipts`.

### Request

Path param `notice_id` only.

### Response — `200 OK`

| Field | Type | Notes |
|-------|------|-------|
| `notice_id` | integer | |
| `total_owners` | integer | Count of current house owners in the society right now. |
| `read_count` | integer | |
| `unread_count` | integer | |
| `read` | array of `{user_id, read_at}` | Sorted by `user_id`. |
| `unread` | array of `{user_id, read_at: null}` | Sorted by `user_id`. |

```json
{
  "notice_id": 12,
  "total_owners": 40,
  "read_count": 27,
  "unread_count": 13,
  "read": [
    {"user_id": 5, "read_at": "2026-07-13T09:12:00Z"},
    {"user_id": 9, "read_at": "2026-07-13T10:03:00Z"}
  ],
  "unread": [
    {"user_id": 17, "read_at": null}
  ]
}
```

### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"Notice not found."` | | No such notice. |

---

## `GET /notices/archive`

Admin-only listing of expired and withdrawn notices — the historical record that no longer
appears in the resident feed.

**Permission:** `notices.read_receipts`.

### Request

| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `page` | integer ≥ 1 | `1` | |
| `page_size` | integer, 1–100 | `20` | |

No status/scope filters here — this endpoint always returns withdrawn notices plus published
notices whose `expires_at` has passed.

### Response — `200 OK`

Same `NoticeListOut` shape as `GET /notices`. `is_read` is always `false` here (there's no
per-caller read concept in the archive view), and `unread_count` is always `0`.

### Errors

None beyond the shared module/permission errors above.
