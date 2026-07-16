# House & Occupancy API Reference

Endpoint-level reference for house status management and owner/tenant occupancy: listing
houses, changing a house's status, editing occupancy details, viewing status history, and
uploading ID-proof images.

**Scope note:** every endpoint here is `society_admin`-scoped. There are no super-admin
endpoints in this module, and — unlike Onboarding — there's also **no resident-facing read
access at all**. `houses.read`, `houses.update_status`, and `houses.manage_occupancy` are
granted only to `society_admin` by default; residents hold none of them. A resident's view of
*their own* house (for dues, complaints, etc.) is served by **other modules** (Finance,
Complaints) calling into this module's service internally — not through any `/houses/*` route.
If you're building a resident-facing "my house" screen, that data comes from Finance's or
Complaints' own endpoints, not from here.

**Doc accuracy note:** the module's design doc and as-built index currently describe ID-proof
image upload as deferred/not yet built. In the actual running code it **is** implemented and
wired to Vault — `POST /houses/{house_id}/occupancy/{party}/id-proof` below is real and
documented as such. Those other docs just haven't been updated yet.

Base path: **`/houses`**.

---

## How house status & occupancy work

- **Statuses:** `empty`, `owned`, `rented`, `to_let`, `for_sale`. A house **can never go back to
  `empty`** once it's left that state — this is a hard, permanent rule.
- **Owner identity = email.** Whichever endpoint sets/edits the owner (`POST .../status` or
  `PATCH .../occupancy/owner`), the same rule applies: if the email you send matches the
  current owner's email (normalized — trimmed, lowercased), it's treated as an in-place edit of
  the same person. If the email is different, it's a full **owner replacement**: the old
  owner's occupancy is closed, their login is unlinked from this house and **all of their
  sessions are revoked** (they're deactivated entirely only if this was their last remaining
  role/house — otherwise they stay active), and a new owner account is created or linked by
  email (auto-provisioned with the society's default password + forced password change, same
  as any new resident account).
- **Tenants never get a login** (`user_id` is always `null` for a tenant occupancy) — this is a
  deliberate v1 limitation; tenant login/view is deferred. A tenant only exists while status is
  `rented`; leaving `rented` for any other status silently closes the current tenant record.
- **Per-status required fields:**
  - `owned` → owner required, owner's `persons_living` required.
  - `to_let` / `for_sale` → owner required, `persons_living` **must be omitted/null** (not
    tracked for these statuses), no tenant allowed.
  - `rented` → owner required (kept or updated) **and** tenant required, tenant's
    `persons_living` required.
- **ID proof is always optional**, and **never wiped by omission** — if you don't include
  `id_proof_type`/`id_proof_document_id` in a request, whatever was previously stored stays as
  is. Only an explicit new value overwrites it.
- **Same-status calls are just edits.** Posting a status change where `to_status` equals the
  house's current status doesn't create a status-history entry or a status-changed audit event
  — it's treated purely as an occupancy detail update.
- **`first_left_empty_on`** is stamped once — the first time a house ever leaves `empty` — and
  never changes after that, regardless of later status changes.

## Common error envelope

Same shape as every other module — see the
[auth API reference](auth.md#common-error-envelope) for the full explanation.

| HTTP status | `code` | Meaning |
|-------------|--------|---------|
| 422 | `validation_error` | Bad input or a business rule was violated (missing required field for the target status, bad email format, invalid party/status value). |
| 403 | `permission_denied` | Missing `houses.read` / `houses.update_status` / `houses.manage_occupancy`, or the module isn't enabled. |
| 404 | `not_found` | House / occupancy / folder doesn't exist (or isn't in the caller's society). |
| 409 | `conflict` | Action conflicts with current state (can't return to `empty`; email already belongs to another society). |
| 413 | `storage_quota_exceeded` | ID-proof upload only — file would exceed the society's Vault storage limit. |
| 415 | `file_type_not_allowed` | ID-proof upload only — file type/extension is blocked. |

All auth-related 401/403s (missing bearer token, expired token, forced password change) are
identical to every other protected endpoint — see the
[auth reference](auth.md#errors-5) rather than repeated per-endpoint here.

**Module/permission errors** (apply to every endpoint below):

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 403 | `module_disabled` | `"No active society."` | `{"module_key": "houses"}` | Caller's token has no active society. |
| 403 | `module_disabled` | `"Module 'houses' is not enabled for this society."` | `{"module_key": "houses"}` | Houses module isn't enabled. |
| 403 | `module_disabled` | `"Module 'vault' is not enabled for this society."` | `{"module_key": "vault"}` | ID-proof upload only — Vault isn't enabled, even if `houses` is. |
| 403 | `permission_denied` | `"You do not have permission to perform this action."` | `{"required_permission": "houses.read"}` (or `"houses.update_status"` / `"houses.manage_occupancy"`) | Caller's role(s) lack the needed permission. |

---

## `GET /houses`

Lists houses in the active society, paginated and filterable.

**Permission:** `houses.read`.

### Request

Query parameters (all optional except pagination defaults):

| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `page` | integer ≥ 1 | `1` | |
| `page_size` | integer, 1–100 | `20` | |
| `status` | string | — | Exact match: `empty` \| `owned` \| `rented` \| `to_let` \| `for_sale`. |
| `building_id` | integer | — | Exact match. |
| `floor_id` | integer | — | Exact match. |
| `number` | string | — | **Exact match** on the bare house number (e.g. `"201"`) — not a substring/display-code search. |

All filters are AND-combined.

```
GET /houses?status=owned&building_id=3&page=1&page_size=20
```

### Response — `200 OK`

Paginated envelope:

| Field | Type | Notes |
|-------|------|-------|
| `items` | array of `HouseOut` | This page's houses. |
| `total` | integer | Total matching houses across all pages. |
| `page` | integer | Echoed. |
| `page_size` | integer | Echoed. |

`HouseOut` fields:

| Field | Type | Notes |
|-------|------|-------|
| `id` | integer | |
| `society_id` | integer | |
| `building_id` | integer \| null | Set for building-type houses. |
| `floor_id` | integer \| null | Set for building-type houses. |
| `row_id` | integer \| null | Set for individual-houses-type houses. |
| `position_in_row` | integer \| null | Set for individual-houses-type houses. |
| `number` | string | Bare house number, e.g. `"201"`. |
| `status` | string | `empty` \| `owned` \| `rented` \| `to_let` \| `for_sale`. |
| `first_left_empty_on` | date \| null | First date this house left `empty`, if ever. |
| `display_code` | string | Derived, e.g. `"Wing A-201"` for a building house, or just the bare number for an individual house. |

```json
{
  "items": [
    {
      "id": 103,
      "society_id": 7,
      "building_id": 3,
      "floor_id": 11,
      "row_id": null,
      "position_in_row": null,
      "number": "201",
      "status": "owned",
      "first_left_empty_on": "2026-01-15",
      "display_code": "Wing A-201"
    }
  ],
  "total": 42,
  "page": 1,
  "page_size": 20
}
```

### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 422 | `validation_error` | `"No active society for this request."` | `{}` | Caller's token has no active society. |

---

## `GET /houses/{house_id}`

Full detail for one house: its record plus its current owner and tenant (if any).

**Permission:** `houses.read`.

### Request

Path param `house_id` only.

### Response — `200 OK`

| Field | Type | Notes |
|-------|------|-------|
| `house` | `HouseOut` | Same shape as in the list endpoint. |
| `owner` | `OccupancyOut` \| null | Current owner, or `null` if the house is `empty`. |
| `tenant` | `OccupancyOut` \| null | Current tenant, or `null` unless status is `rented`. |

`OccupancyOut` fields:

| Field | Type | Notes |
|-------|------|-------|
| `id` | integer | Occupancy record id. |
| `house_id` | integer | |
| `party_type` | string | `"owner"` or `"tenant"`. |
| `user_id` | integer \| null | Linked login, if any. **Always `null` for a tenant.** |
| `full_name` | string | |
| `email` | string \| null | Owner always has one; tenant may not. |
| `contact_number` | string \| null | |
| `persons_living` | integer \| null | Required for `owned`/`rented`; always `null` for `to_let`/`for_sale`. |
| `id_proof_type` | string \| null | Free-text label, e.g. `"Aadhaar"`. |
| `id_proof_document_id` | integer \| null | Vault document id, if an ID-proof image has been uploaded. |
| `is_current` | boolean | `true` for the active occupancy record. |
| `valid_from` | date | |
| `valid_to` | date \| null | Set when the occupancy is closed (replaced/left). |

```json
{
  "house": {
    "id": 103,
    "society_id": 7,
    "building_id": 3,
    "floor_id": 11,
    "row_id": null,
    "position_in_row": null,
    "number": "201",
    "status": "owned",
    "first_left_empty_on": "2026-01-15",
    "display_code": "Wing A-201"
  },
  "owner": {
    "id": 55,
    "house_id": 103,
    "party_type": "owner",
    "user_id": 42,
    "full_name": "Priya Sharma",
    "email": "priya.sharma@example.com",
    "contact_number": "+91-9876543210",
    "persons_living": 3,
    "id_proof_type": "Aadhaar",
    "id_proof_document_id": 210,
    "is_current": true,
    "valid_from": "2026-01-15",
    "valid_to": null
  },
  "tenant": null
}
```

### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"House not found."` | `{"house_id": ...}` | No such house in this society. |
| 422 | `validation_error` | `"No active society for this request."` | `{}` | No active society. |

---

## `GET /houses/{house_id}/history`

Status-change history for a house, newest first. Append-only audit trail.

**Permission:** `houses.read`.

### Request

Path param `house_id` only.

### Response — `200 OK`

Array of `StatusHistoryOut`:

| Field | Type | Notes |
|-------|------|-------|
| `id` | integer | |
| `house_id` | integer | |
| `from_status` | string | Status before the change. |
| `to_status` | string | Status after the change. |
| `changed_by` | integer \| null | User id who made the change. |
| `changed_at` | datetime | |
| `snapshot` | object \| null | Owner (and tenant, if `to_status == "rented"`) payload at the time of the change. |

```json
[
  {
    "id": 12,
    "house_id": 103,
    "from_status": "empty",
    "to_status": "owned",
    "changed_by": 9,
    "changed_at": "2026-01-15T09:30:00Z",
    "snapshot": {
      "owner": {
        "full_name": "Priya Sharma",
        "email": "priya.sharma@example.com",
        "contact_number": "+91-9876543210",
        "persons_living": 3,
        "id_proof_type": null,
        "id_proof_document_id": null
      }
    }
  }
]
```

### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"House not found."` | `{"house_id": ...}` | No such house. |
| 422 | `validation_error` | `"No active society for this request."` | `{}` | No active society. |

---

## `POST /houses/{house_id}/status`

Changes a house's status, capturing the owner (and tenant, if applicable) for the new status.
Also used to edit the current owner/tenant details while keeping the same status.

**Permission:** `houses.update_status`.

### Request

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `to_status` | string | Yes | `"owned"` \| `"rented"` \| `"to_let"` \| `"for_sale"`. Never `"empty"`. |
| `owner` | object (`OwnerPayload`) | Yes | See below. |
| `tenant` | object (`TenantPayload`) \| null | Only when `to_status == "rented"` | Rejected (422) if supplied for any other target status. |

`OwnerPayload`:

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `full_name` | string, 1–255 chars | Yes | |
| `email` | string, 1–320 chars | Yes | Normalized (trim + lowercase); must be a valid email shape. |
| `contact_number` | string, 1–32 chars | Yes | |
| `persons_living` | integer ≥ 0 \| null | Required for `owned`/`rented`; must be omitted for `to_let`/`for_sale`. | |
| `id_proof_type` | string, ≤255 chars \| null | No | |
| `id_proof_document_id` | integer \| null | No | A Vault document id — normally set via the ID-proof upload endpoint, not sent directly. |

`TenantPayload` — identical shape, except `email` is optional (`string, ≤320 chars \| null`,
normalized only if provided).

**Example — moving an empty house to `owned`:**

```json
{
  "to_status": "owned",
  "owner": {
    "full_name": "Priya Sharma",
    "email": "priya.sharma@example.com",
    "contact_number": "+91-9876543210",
    "persons_living": 3
  }
}
```

**Example — moving a house to `rented`:**

```json
{
  "to_status": "rented",
  "owner": {
    "full_name": "Priya Sharma",
    "email": "priya.sharma@example.com",
    "contact_number": "+91-9876543210"
  },
  "tenant": {
    "full_name": "Rahul Verma",
    "email": "rahul.verma@example.com",
    "contact_number": "+91-9988776655",
    "persons_living": 2
  }
}
```

### Response — `200 OK`

`HouseDetailOut` (same shape as `GET /houses/{house_id}`), reflecting the new status and
occupancy.

### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"House not found."` | `{"house_id": ...}` | No such house. |
| 409 | `conflict` | `"A house can never return to empty."` | `{}` | `to_status == "empty"`. |
| 422 | `validation_error` | `"Unknown target status."` | `{}` | `to_status` not one of the 4 allowed values. |
| 422 | `validation_error` | `"persons_living is required for owned."` | `{}` | `to_status: "owned"`, `owner.persons_living` missing. |
| 422 | `validation_error` | `"persons_living is not captured for to_let/for_sale."` | `{}` | `to_status` in `{to_let, for_sale}`, `owner.persons_living` was provided. |
| 422 | `validation_error` | `"tenant is required for rented."` | `{}` | `to_status: "rented"`, no `tenant` object given. |
| 422 | `validation_error` | `"persons_living is required for the tenant."` | `{}` | `to_status: "rented"`, `tenant.persons_living` missing. |
| 422 | `validation_error` | `"tenant is only valid for the rented status."` | `{}` | `tenant` supplied with a non-`rented` target. |
| 422 | `validation_error` | `"Invalid email address."` | `{"field": "email"}` | `owner.email` or `tenant.email` isn't a valid email shape. |
| 404 | `not_found` | `f"Role 'resident' does not exist for this society."` | `{"society_id": ..., "role_key": "resident"}` | Defensive — only if the `resident` role was never seeded for this society. |
| 409 | `conflict` | `"This email already belongs to another society."` | `{"email": ..., "society_id": ..., "existing_society_ids": [...]}` | The owner's email already holds roles in a **different** society (one-society-per-user rule). |
| 422 | `validation_error` | `"No active society for this request."` | `{}` | No active society. |

---

## `PATCH /houses/{house_id}/occupancy/{party}`

Edits the current owner's or tenant's details without necessarily changing the house's status.
All fields are optional/partial — only the fields you send are updated.

If you edit the owner's `email` to a **different** address than the current owner's, this
triggers the same full owner-replacement flow as changing status with a new owner email (old
owner's sessions revoked, new owner provisioned) — see "How house status & occupancy work"
above.

**Permission:** `houses.manage_occupancy`.

### Request

Path params: `house_id: int`, `party: "owner" | "tenant"`.

Body (`OccupancyEditRequest`, all fields optional):

| Field | Type | Notes |
|-------|------|-------|
| `full_name` | string, 1–255 chars | |
| `email` | string, ≤320 chars | Normalized if present. Changing the owner's email triggers replacement (see above). |
| `contact_number` | string, 1–32 chars | |
| `persons_living` | integer ≥ 0 | Rejected (422) if the house's current status is `to_let`/`for_sale` and `party == "owner"`. |
| `id_proof_type` | string, ≤255 chars | |
| `id_proof_document_id` | integer | Normally set via the ID-proof upload endpoint. |

Fields you omit are left untouched — this is a partial update, not a full replace.

```json
{
  "contact_number": "+91-9876500000"
}
```

### Response — `200 OK`

`HouseDetailOut`, reflecting the edit.

### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 422 | `validation_error` | `"Unknown occupancy party."` | `{"party_type": ...}` | `party` isn't `"owner"` or `"tenant"`. |
| 404 | `not_found` | `"House not found."` | `{"house_id": ...}` | No such house. |
| 404 | `not_found` | `f"No current {party_type} for this house."` | `{}` | House has no current owner/tenant of that type (e.g. editing a tenant on a house that isn't `rented`). |
| 422 | `validation_error` | `"persons_living is not captured for to_let/for_sale."` | `{}` | Editing the owner's `persons_living` while status is `to_let`/`for_sale`. |
| 422 | `validation_error` | `"Invalid email address."` | `{"field": "email"}` | Malformed `email`. |
| 409 | `conflict` | `"This email already belongs to another society."` | `{"email": ..., "society_id": ..., "existing_society_ids": [...]}` | New owner email already belongs to a different society (only when the edit changes the owner's email, triggering replacement). |
| 422 | `validation_error` | `"No active society for this request."` | `{}` | No active society. |

---

## `POST /houses/{house_id}/occupancy/{party}/id-proof`

Uploads an ID-proof image for the current owner or tenant, storing it in Vault under that
house's `Proof` folder and linking it to the occupancy record.

**Permission:** `houses.manage_occupancy`. **Requires both** the `houses` and `vault` modules
enabled for the society.

### Request

`multipart/form-data`. Path params: `house_id: int`, `party: "owner" | "tenant"`.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `file` | file | Yes | The ID-proof image/document. |
| `id_proof_type` | string (form field) | No | If given, overwrites the occupancy's stored `id_proof_type` label (e.g. `"Aadhaar"`, `"Passport"`). |

A filename that collides with an existing file in the folder is auto-renamed (e.g.
`aadhaar.pdf` → `aadhaar (1).pdf`) rather than rejected.

### Response — `200 OK`

`HouseDetailOut`, with the relevant occupancy's `id_proof_document_id` (and `id_proof_type`, if
sent) now updated.

### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 422 | `validation_error` | `"Unknown occupancy party."` | `{"party_type": ...}` | Bad `party`. |
| 404 | `not_found` | `"House not found."` | `{"house_id": ...}` | No such house. |
| 404 | `not_found` | `f"No current {party_type} for this house."` | `{}` | No current owner/tenant of that type. |
| 415 | `file_type_not_allowed` | `"This file type is not allowed."` | `{"filename": ..., "extension": ...}` or `{"filename": ..., "content_type": ...}` | File extension is on the society's denylist, or the content type is a blocked executable type. |
| 413 | `storage_quota_exceeded` | `"Uploading this file would exceed the storage quota."` | `{"used": ..., "size": ..., "limit": ...}` | Would exceed the society's Vault storage limit. |
| 422 | `validation_error` | `"Invalid filename."` | `{"filename": ...}` | Filename is empty, `.`, or `..` after sanitization. |
| 422 | `validation_error` | `"Filename exceeds 255 characters."` | `{"filename": ..., "max_length": 255}` | Filename too long. |
| 404 | `not_found` | `"Folder not found."` / `"Society not found."` | `{}` | Defensive — shouldn't normally occur since the folder is created automatically. |
