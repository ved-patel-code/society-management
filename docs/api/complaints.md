# Complaints API Reference

Endpoint-level reference for house-scoped complaints: categories, raising/editing/withdrawing
a complaint, the admin status workflow (including resolve-with-proof), and report/proof images.

**Scope note:** there are no super-admin endpoints in this module. Access is entirely
permission-driven — a `society_admin` (holding `complaints.read_all`, `complaints.update_status`,
`complaints.manage_categories`, `complaints.configure`) sees and manages everything; a
`resident` (holding `complaints.create` and `complaints.read`) can raise complaints and see/edit
only complaints tied to a house they currently own.

Base path: **`/complaints`**.

---

## How complaints work

- **Every complaint belongs to a house.** There's no "raised against common area" as a separate
  concept — "Common Area" is simply one of the default categories. The house is always the
  raiser's own house; a resident who owns more than one current house must say which one.
- **Status set:** `open → in_progress → resolved → closed → archived`, plus a side-branch
  `open → withdrawn`. Only these transitions are legal:
  - `open → in_progress` (admin)
  - `open → withdrawn` (the raiser only, and only while still `open`)
  - `in_progress → resolved` (admin — **only** via the dedicated resolve endpoint, not the
    generic status endpoint, so proof images can be attached in the same call)
  - `resolved → closed` (admin)
  - `resolved → in_progress` (admin — reopen/correction)
  - `closed → archived` (system only — a nightly worker job, never called directly)

  Anything else (e.g. `open → closed` directly, touching a `withdrawn`/`archived` complaint) is
  rejected with a `409 conflict`. **A resident can never reopen or otherwise change the status
  of their own complaint** — the only status-affecting action available to a resident is
  withdrawing an `open` complaint.
- **Auto-archive** runs nightly: any complaint that's been `closed` for longer than the
  society's configured `auto_archive_days` (default 15) is automatically moved to `archived`.
  This is fully automatic — there's no endpoint to trigger or opt out of it per-complaint.
- **Report images vs. proof images** are two distinct, differently-managed things:
  - **Report images** — up to `max_report_images` (default 2), attached by the **resident** via
    their own add/remove endpoints, and only editable while the complaint is still `open`.
  - **Proof images** — up to `max_proof_images` (default 2), attached by the **admin** in a
    single call to `POST /complaints/{id}/resolve` at the moment the complaint moves from
    `in_progress` to `resolved`. There is no separate add/remove endpoint for proof images —
    once a complaint is resolved, its proof images are permanently locked in.
  - Both kinds are stored in Vault under **one shared folder per house**,
    `Houses/<house>/Complaints` — images aren't split into a subfolder per complaint. Each
    stored file is tagged internally with the complaint it belongs to, so if you're browsing
    that folder directly in Vault you'll see every complaint's images for that house together,
    not grouped by complaint.
  - Both require the `vault` module to be enabled for the society (in addition to
    `complaints`) — if Vault isn't enabled, complaints still work text-only (create, list,
    status changes), but any image upload/delete call 403s.
- **Categories** are shared across the society (not per-house). Six defaults are seeded
  automatically the first time anyone touches the categories feature: `Plumbing`, `Electrical`,
  `Common Area`, `Security`, `Cleaning`, `Other`. Admins can add more, rename, or deactivate —
  deactivating is a soft delete (never removes history; the category name becomes free for
  reuse by a new active category).
- **One admin note per status change**, not a two-way discussion thread — each transition can
  carry an optional free-text `note`, recorded as its own timeline entry.

## Common error envelope

Same shape as every other module — see the
[auth API reference](auth.md#common-error-envelope) for the full explanation.

| HTTP status | `code` | Meaning |
|-------------|--------|---------|
| 422 | `validation_error` | Bad input or a business rule was violated. |
| 403 | `permission_denied` | Missing the required permission, module not enabled, or you're not the complaint's raiser / don't own the house. |
| 404 | `not_found` | Complaint / category / image doesn't exist (or isn't visible to you). |
| 409 | `conflict` | Action conflicts with current state (illegal status transition, locked once in progress, name collision, image cap reached). |

All auth-related 401/403s (missing bearer token, expired token, forced password change) are
identical to every other protected endpoint — see the
[auth reference](auth.md#errors-5) rather than repeated per-endpoint here.

**Module/permission errors** (apply to every endpoint below, permission key varies):

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 403 | `module_disabled` | `"No active society."` | `{"module_key": "complaints"}` | Caller's token has no active society. |
| 403 | `module_disabled` | `"Module 'complaints' is not enabled for this society."` | `{"module_key": "complaints"}` | Complaints module isn't enabled. |
| 403 | `module_disabled` | `"Module 'vault' is not enabled for this society."` | `{"module_key": "vault"}` | Image endpoints and resolve only — Vault isn't enabled, even if `complaints` is. |
| 403 | `permission_denied` | `"You do not have permission to perform this action."` | `{"required_permission": "complaints.create"}` (or whichever key) | Caller's role(s) lack the needed permission. |

---

## Categories

### `GET /complaints/categories`

Lists active complaint categories. Both residents and admins call this to populate the
category picker when raising or editing a complaint.

**Permission:** `complaints.read`.

#### Request

No parameters.

#### Response — `200 OK`

Array of `CategoryOut`, sorted by name, **active categories only**:

| Field | Type | Notes |
|-------|------|-------|
| `id` | integer | |
| `name` | string | |
| `is_active` | boolean | Always `true` in this list. |
| `is_system` | boolean | `true` for the 6 seeded defaults. |

```json
[
  {"id": 3, "name": "Common Area", "is_active": true, "is_system": true},
  {"id": 1, "name": "Electrical", "is_active": true, "is_system": true},
  {"id": 2, "name": "Plumbing", "is_active": true, "is_system": true}
]
```

#### Errors

None beyond the shared module/permission errors above.

---

### `POST /complaints/categories`

Adds a category.

**Permission:** `complaints.manage_categories`.

#### Request

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `name` | string, 1–64 chars | Yes | Trimmed; must not collide with another **active** category name. |

```json
{
  "name": "Parking"
}
```

#### Response — `200 OK`

`CategoryOut`:

```json
{
  "id": 9,
  "name": "Parking",
  "is_active": true,
  "is_system": false
}
```

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 409 | `conflict` | `"An active complaint category named 'Parking' already exists."` | | Name collides with another active category. |

---

### `PATCH /complaints/categories/{category_id}`

Renames and/or reactivates a category. **Cannot be used to deactivate** — use `DELETE` instead.

**Permission:** `complaints.manage_categories`.

#### Request

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `name` | string, 1–64 chars \| null | At least one of `name`/`is_active` | New name. |
| `is_active` | boolean \| null | At least one of `name`/`is_active` | Only `true` is accepted here (reactivating a previously-deactivated category). |

```json
{
  "name": "Parking & Vehicles"
}
```

#### Response — `200 OK`

`CategoryOut`.

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 422 | `validation_error` | `"Provide a new name and/or is_active=true to update a category."` | | Both fields omitted. |
| 422 | `validation_error` | `"Deactivate a category via DELETE /complaints/categories/{id}, not by setting is_active=false."` | `{"category_id": ...}` | `is_active: false` sent — not supported by this endpoint. |
| 404 | `not_found` | `"Complaint category {id} was not found."` | | No such category. |
| 409 | `conflict` | `"An active complaint category named '{name}' already exists."` | | Rename/reactivate collides with another active category. |

---

### `DELETE /complaints/categories/{category_id}`

Deactivates a category (soft delete — history referencing it is preserved). Idempotent:
deactivating an already-inactive category is a no-op and still returns `200`.

**Permission:** `complaints.manage_categories`.

#### Request

Path param only, no body.

#### Response — `200 OK`

`CategoryOut`, now `is_active: false`. (Note: this returns `200` with the row, not `204`.)

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"Complaint category {id} was not found."` | | No such category. |

---

## Config

### `GET /complaints/config`

Returns the society's complaints configuration.

**Permission:** `complaints.configure`.

#### Request

No parameters.

#### Response — `200 OK`

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `auto_archive_days` | integer | `15` | Days a complaint stays `closed` before the nightly worker archives it. |
| `max_report_images` | integer | `2` | Cap on resident-uploaded report images per complaint. |
| `max_proof_images` | integer | `2` | Cap on admin-uploaded proof images at resolve. |

```json
{
  "auto_archive_days": 15,
  "max_report_images": 2,
  "max_proof_images": 2
}
```

#### Errors

None beyond the shared module/permission errors above.

---

### `PUT /complaints/config`

Updates the society's complaints configuration. **Partial merge** — only the fields you send
are changed; omitted fields keep their current value.

**Permission:** `complaints.configure`.

#### Request

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `auto_archive_days` | integer, 1–365 | No | |
| `max_report_images` | integer, 0–10 | No | |
| `max_proof_images` | integer, 0–10 | No | |

At least one field must be present.

```json
{
  "auto_archive_days": 30
}
```

#### Response — `200 OK`

`ComplaintsConfigOut` (see above), reflecting the merge.

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 422 | `validation_error` | `"Provide at least one config field to update."` | `{"fields": [...]}` | Empty request body. |

---

## Complaints

### `POST /complaints`

Raises a new complaint. Only a current house owner may raise one.

**Permission:** `complaints.create`.

#### Request

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `category_id` | integer | Yes | Must be an active category. |
| `title` | string, 1–200 chars | Yes | Trimmed, non-blank. |
| `description` | string, 1–5000 chars | Yes | Trimmed, non-blank. |
| `house_id` | integer \| null | Conditionally required | Omit if you own exactly one current house — it's inferred. Required (and must be one of your own houses) if you own more than one. |

```json
{
  "category_id": 3,
  "title": "Water leakage in common corridor",
  "description": "There's a steady water leak near the 2nd floor corridor, been going on for 2 days.",
  "house_id": 103
}
```

#### Response — `200 OK`

`ComplaintDetailOut`:

| Field | Type | Notes |
|-------|------|-------|
| `id` | integer | |
| `reference` | string | Human-readable id, e.g. `"C-000123"`. |
| `house_id` | integer | |
| `house_display_code` | string \| null | e.g. `"Wing A-201"`. |
| `raised_by` | integer | User id of the raiser. |
| `category_id` | integer | |
| `category_name` | string | |
| `title` | string | |
| `description` | string | |
| `status` | string | `"open"` on creation. |
| `resolved_at` | datetime \| null | |
| `closed_at` | datetime \| null | |
| `archived_at` | datetime \| null | |
| `withdrawn_at` | datetime \| null | |
| `created_at` | datetime | |
| `updated_at` | datetime | |
| `timeline` | array of `StatusHistoryOut` | One entry per status change, including the initial `open`. |
| `images` | array of `ComplaintImageOut` | Empty on creation. |

`StatusHistoryOut`: `id`, `from_status` (string \| null — `null` for the initial entry),
`to_status`, `note` (string \| null), `changed_by` (integer \| null — `null` for
system-triggered archiving), `created_at`.

`ComplaintImageOut`: `id`, `kind` (`"report"` or `"proof"`), `vault_document_id`,
`preview_url` (string \| null — `null` if Vault can't currently produce one), `created_at`.

```json
{
  "id": 501,
  "reference": "C-000123",
  "house_id": 103,
  "house_display_code": "Wing A-201",
  "raised_by": 42,
  "category_id": 3,
  "category_name": "Common Area",
  "title": "Water leakage in common corridor",
  "description": "There's a steady water leak near the 2nd floor corridor, been going on for 2 days.",
  "status": "open",
  "resolved_at": null,
  "closed_at": null,
  "archived_at": null,
  "withdrawn_at": null,
  "created_at": "2026-07-10T09:00:00Z",
  "updated_at": "2026-07-10T09:00:00Z",
  "timeline": [
    {"id": 1, "from_status": null, "to_status": "open", "note": null, "changed_by": 42, "created_at": "2026-07-10T09:00:00Z"}
  ],
  "images": []
}
```

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 403 | `permission_denied` | `"Only a current house owner may raise a complaint."` | | Caller owns no current house at all. |
| 403 | `permission_denied` | `"You do not own the named house."` | `{"house_id": ...}` | `house_id` given but caller doesn't own it. |
| 422 | `validation_error` | `"You own several houses; specify house_id."` | `{"owned_house_ids": [...]}` | `house_id` omitted, caller owns more than one current house. |
| 404 | `not_found` | `"Category not found."` | `{"category_id": ...}` | No such category. |
| 422 | `validation_error` | `"Category is not active; choose an active category."` | `{"category_id": ...}` | Category was deactivated. |

---

### `GET /complaints`

Lists complaints, paginated. Visibility depends on the caller's permissions:

- Holders of `complaints.read_all` (`society_admin`) or super-admins see **every** complaint
  in the society, subject to the filters below.
- A caller with only `complaints.read` (a plain resident) sees **only complaints tied to a
  house they currently own** — if they own zero houses, the list is always empty (never "all").

**Permission:** `complaints.read`.

#### Request

Query parameters (all optional except pagination defaults):

| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `page` | integer ≥ 1 | `1` | |
| `page_size` | integer, 1–100 | `20` | |
| `status` | string | — | Exact match. |
| `category_id` | integer | — | Exact match. |
| `house_id` | integer | — | Exact match — still subject to the visibility rule above (a resident can't use this to see another house's complaints). |
| `date_from` | date | — | Inclusive, matches complaints created on or after this date. |
| `date_to` | date | — | Inclusive of the whole day. |
| `q` | string, ≤100 chars | — | Case-insensitive match against `reference` or `title`. |

All filters AND-combined; results ordered newest-first.

```
GET /complaints?status=open&page=1&page_size=20
```

#### Response — `200 OK`

| Field | Type | Notes |
|-------|------|-------|
| `items` | array of `ComplaintListItemOut` | This page. |
| `total` | integer | Total matching complaints across all pages. |

`ComplaintListItemOut`:

| Field | Type | Notes |
|-------|------|-------|
| `id` | integer | |
| `reference` | string | |
| `title` | string | |
| `status` | string | |
| `category_id` | integer | |
| `category_name` | string | |
| `house_id` | integer | |
| `house_display_code` | string \| null | |
| `report_image_count` | integer | |
| `proof_image_count` | integer | |
| `created_at` | datetime | |
| `updated_at` | datetime | |

```json
{
  "items": [
    {
      "id": 501,
      "reference": "C-000123",
      "title": "Water leakage in common corridor",
      "status": "open",
      "category_id": 3,
      "category_name": "Common Area",
      "house_id": 103,
      "house_display_code": "Wing A-201",
      "report_image_count": 1,
      "proof_image_count": 0,
      "created_at": "2026-07-10T09:00:00Z",
      "updated_at": "2026-07-10T09:00:00Z"
    }
  ],
  "total": 1
}
```

#### Errors

None beyond standard query-param type validation (a malformed `date_from`/`date_to`/`page` etc.
produces the generic `422 validation_error`).

---

### `GET /complaints/{complaint_id}`

Full detail for one complaint, including its status timeline and images. Marks the complaint
as read for the caller as a side effect (used by the Notifications feed — currently a no-op
since Notifications isn't wired up to react to it yet).

**Permission:** `complaints.read`. Same visibility rule as the list endpoint applies here too.

#### Request

Path param `complaint_id` only.

#### Response — `200 OK`

`ComplaintDetailOut` (see field list under `POST /complaints`).

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"Complaint not found."` | `{"complaint_id": ...}` | No such complaint in this society. |
| 403 | `permission_denied` | `"You may only view complaints on a house you own."` | | Caller lacks `complaints.read_all` and doesn't own the complaint's house. |

---

### `PATCH /complaints/{complaint_id}`

Edits a complaint's title, description, and/or category. **Only the raiser can do this** (an
admin editing someone else's complaint is rejected), and only while the complaint is still
`open`.

**Permission:** `complaints.create`.

#### Request

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `title` | string, 1–200 chars \| null | At least one field required | |
| `description` | string, 1–5000 chars \| null | At least one field required | |
| `category_id` | integer \| null | At least one field required | Must be an active category. |

```json
{
  "title": "Water leakage in common corridor (2nd floor)"
}
```

#### Response — `200 OK`

`ComplaintDetailOut`, reflecting the edit.

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"Complaint not found."` | `{"complaint_id": ...}` | No such complaint. |
| 403 | `permission_denied` | `"Only the raiser may edit this complaint."` | | Anyone else, including an admin. |
| 409 | `conflict` | `"This complaint is locked once it is in progress."` | `{"status": ...}` | Complaint isn't `open` anymore. |
| 422 | `validation_error` | `"Provide at least one field to edit."` | | All three fields omitted. |
| 404 | `not_found` | `"Category not found."` | `{"category_id": ...}` | Bad `category_id`. |
| 422 | `validation_error` | `"Category is not active; choose an active category."` | `{"category_id": ...}` | Deactivated category. |

---

### `POST /complaints/{complaint_id}/withdraw`

Withdraws a complaint. **Only the raiser can do this**, and only while it's still `open`. This
is the only status-affecting action a resident can take on their own complaint.

**Permission:** `complaints.create`.

#### Request

Path param only, no body.

#### Response — `200 OK`

`ComplaintDetailOut`, now `status: "withdrawn"`, `withdrawn_at` set.

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"Complaint not found."` | `{"complaint_id": ...}` | No such complaint. |
| 403 | `permission_denied` | `"Only the raiser may withdraw this complaint."` | | Anyone else, including an admin. |
| 409 | `conflict` | `"Only an open complaint can be withdrawn."` | `{"status": ...}` | Complaint isn't `open` anymore. |

---

## Status workflow (admin)

### `POST /complaints/{complaint_id}/status`

Changes an admin-driven complaint status: `open → in_progress`, `resolved → closed`, or
`resolved → in_progress` (reopen). **Cannot be used to resolve a complaint** — use
`POST /complaints/{id}/resolve` for that, since resolving requires the proof-image step.

**Permission:** `complaints.update_status`.

#### Request

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `to_status` | string | Yes | `"in_progress"` \| `"resolved"` \| `"closed"`. (`"resolved"` is schema-valid but always rejected by the service — see errors.) |
| `note` | string, ≤5000 chars \| null | No | Recorded as this transition's timeline note. |

```json
{
  "to_status": "in_progress",
  "note": "Plumber scheduled for tomorrow morning."
}
```

#### Response — `200 OK`

`ComplaintDetailOut`, reflecting the new status and an appended timeline entry.

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"Complaint not found."` | `{"complaint_id": ...}` | No such complaint. |
| 422 | `validation_error` | `"Resolve a complaint via POST /complaints/{id}/resolve so proof images can be attached."` | | `to_status: "resolved"` sent here. |
| 409 | `conflict` | `"Cannot move a complaint from '{from_status}' to '{to_status}'."` | `{"from_status": ..., "to_status": ...}` | Any transition not in the allowed set (e.g. `open → closed` directly, acting on a `withdrawn`/`archived` complaint). |

---

### `POST /complaints/{complaint_id}/resolve`

Resolves an `in_progress` complaint, optionally attaching proof images in the same call. This
is the **only** way to reach `resolved`, and the **only** way proof images are ever attached —
there's no later endpoint to add/remove them.

**Permission:** `complaints.update_status`. Requires the `vault` module enabled (even if no
images are attached).

#### Request

`multipart/form-data`:

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `note` | string (form field) | No | Recorded as this transition's timeline note. |
| `images` | file[] | No | Up to `max_proof_images` (default 2). Checked before any upload happens. |

#### Response — `200 OK`

`ComplaintDetailOut`, now `status: "resolved"`, `resolved_at` set, `images` including the new
proof images (`kind: "proof"`).

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"Complaint not found."` | `{"complaint_id": ...}` | No such complaint. |
| 409 | `conflict` | `"Cannot move a complaint from '{from_status}' to 'resolved'."` | `{"from_status": ..., "to_status": "resolved"}` | Complaint isn't currently `in_progress`. |
| 422 | `validation_error` | `"Too many proof images."` | `{"provided": ..., "max_proof_images": ...}` | More files sent than the configured cap. |
| 415 | `file_type_not_allowed` | (Vault's message) | | A file's type/extension is blocked — see the [house-occupancy reference](house-occupancy.md#post-housesouse_idoccupancypartyid-proof) for the exact Vault error shape. |
| 413 | `storage_quota_exceeded` | (Vault's message) | | Upload would exceed the society's Vault storage limit. |

---

## Report images

### `POST /complaints/{complaint_id}/images`

Attaches a report image. **Only the raiser**, and only while the complaint is still `open`.

**Permission:** `complaints.create`. Requires the `vault` module enabled.

#### Request

`multipart/form-data`:

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `file` | file | Yes | |

#### Response — `200 OK`

`ComplaintImageOut`:

```json
{
  "id": 900,
  "kind": "report",
  "vault_document_id": 5001,
  "preview_url": "https://.../preview/5001",
  "created_at": "2026-07-10T09:05:00Z"
}
```

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"Complaint not found."` | `{"complaint_id": ...}` | No such complaint. |
| 403 | `permission_denied` | `"Only the complaint's raiser may manage its report images."` | `{"complaint_id": ...}` | Anyone else, including an admin. |
| 409 | `conflict` | `"Report images can only be changed while the complaint is open."` | `{"complaint_id": ..., "status": ...}` | Complaint has moved past `open`. |
| 409 | `conflict` | `"Report image limit reached for this complaint."` | `{"complaint_id": ..., "limit": ...}` | Already at `max_report_images`. |
| 415 | `file_type_not_allowed` | (Vault's message) | | Blocked file type/extension. |
| 413 | `storage_quota_exceeded` | (Vault's message) | | Would exceed storage quota. |

---

### `DELETE /complaints/{complaint_id}/images/{image_id}`

Removes a report image (soft-deletes it in Vault, then removes the link). **Only the raiser**,
and only while the complaint is still `open`.

**Permission:** `complaints.create`. Requires the `vault` module enabled.

#### Request

Path params only, no body.

#### Response

`204 No Content` — empty body.

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"Report image not found."` | `{"complaint_id": ..., "image_id": ...}` | No such image — **also returned if the image exists but is a proof image**, so a caller can't probe for proof image ids. |
| 403 | `permission_denied` | `"Only the complaint's raiser may manage its report images."` | `{"complaint_id": ...}` | Anyone else, including an admin. |
| 409 | `conflict` | `"Report images can only be changed while the complaint is open."` | `{"complaint_id": ..., "status": ...}` | Complaint has moved past `open`. |
