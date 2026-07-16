# Onboarding API Reference

Endpoint-level reference for the Onboarding module: mapping a society's physical structure
(buildings/floors/houses, or rows/houses) and generating house numbers, then finalizing
onboarding so the rest of the app unlocks.

**Scope note:** this doc excludes society creation/naming and module allocation — those are
super-admin actions (`/admin/societies`, `/admin/societies/{id}/modules`) documented
separately. Everything below is `society_admin`-scoped: called by whoever is running
onboarding for their own society, with the society always resolved from the caller's JWT
(never a path/query param). There are no super-admin-only endpoints in this module at all.

Base path: **`/onboarding`**.

---

## How onboarding works

- **One state machine per society**, tracked in a `current_step` field:
  `type_selection → structure_mapping → review → completed`. The row is created automatically
  the first time any onboarding endpoint touches it — there's nothing to "start" explicitly.
- **Two mutually exclusive flows**, chosen once via `POST /onboarding/type`:
  - **`building`** — create buildings, then map each one (floors + numbering) to generate its houses.
  - **`individual_houses`** — create rows directly; each row generates its own houses.

  Once houses exist, the society's type **cannot be changed**.
- **Draft/resume.** `PUT /onboarding/draft` lets the client persist arbitrary in-progress wizard
  state (form values not yet submitted as real buildings/rows) so a browser refresh or a
  different device can resume exactly where the admin left off. `GET /onboarding/state` is the
  single source of truth for "what does this society have so far, and what should the wizard
  show next" — it returns a computed `next_action` hint (see below).
- **Finalizing.** `POST /onboarding/complete` flips the society from `status: "onboarding"` to
  `status: "active"`. This is what makes `GET /me`'s `onboarding_required` flip to `false`
  (see the [auth API reference](auth.md)) — the signal the frontend uses to unlock the rest of
  the app's UI. It requires a type to have been selected and at least one house to exist.
- **Permissions.** Two permission keys gate this module: `onboarding.manage` (everything that
  writes) and `onboarding.read` (state + preview, read-only). Both are granted to
  `society_admin` by default; residents get neither. Every endpoint also requires the
  `onboarding` module to be enabled for the active society.
- **Later edits are allowed.** Several endpoints (adding floors, renaming a building,
  overriding a house number, and the three delete endpoints) are **not blocked once onboarding
  is complete** — they're legitimate ongoing maintenance actions. They're grouped in their own
  section below, separate from the linear wizard flow, since they can be called both during
  and after onboarding.
- **`display_code` quirk.** `HouseOut` (the response shape returned by most house-related
  endpoints) includes a `display_code` field (e.g. `"A-201"`) that is a formatted,
  human-readable house code. **It is only populated on `GET /onboarding/buildings/{id}/preview`.**
  Every other endpoint that returns `HouseOut` (create/map/add-floors/rows/override) returns
  `display_code: ""` — the field exists on the schema but isn't computed on those responses.
  Don't rely on it outside of the preview endpoint; compute it client-side if needed elsewhere
  (building houses: `f"{building_name}{separator}{number}"`, e.g. `"A-201"`; individual houses:
  just `number`).

## Common error envelope

Same shape used across the whole backend — see the [auth API reference](auth.md#common-error-envelope)
for the full explanation. Quick reference for the codes that appear in this module:

| HTTP status | `code`              | Meaning |
|-------------|---------------------|---------|
| 422         | `validation_error`  | Bad input, or a business rule was violated (e.g. invalid numbering mode, empty name). |
| 403         | `permission_denied` | Authenticated, but lacks `onboarding.manage`/`onboarding.read`, or module not enabled. |
| 404         | `not_found`         | Referenced building/floor/house doesn't exist (or isn't in the caller's society). |
| 409         | `conflict`          | The action conflicts with current state (duplicate name/number, already mapped, occupied, onboarding already complete). |

All auth-related errors (missing/invalid bearer token, forced password change) are identical
to every other protected endpoint — see the
[auth reference's `GET /me` error table](auth.md#errors-5) for the exact 401/403 cases; they
are not repeated per-endpoint below.

**Module/permission errors** (apply to every endpoint in this doc):

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 403 | `module_disabled` | `"No active society."` | `{"module_key": "onboarding"}` | Caller's token has no active society. |
| 403 | `module_disabled` | `"Module 'onboarding' is not enabled for this society."` | `{"module_key": "onboarding"}` | Onboarding module isn't enabled for this society. |
| 403 | `permission_denied` | `"You do not have permission to perform this action."` | `{"required_permission": "onboarding.manage"}` or `"onboarding.read"` | Caller's role(s) lack the needed permission. |

---

## Wizard flow

### `GET /onboarding/state`

Returns everything about the society's onboarding progress so far, plus a `next_action` hint
telling the client which step to show. Read-only — safe to call anytime, including before
anything else has happened (it lazily initializes the progress record on first call).

**Permission:** `onboarding.read`.

#### Request

No parameters.

#### Response — `200 OK`

| Field | Type | Notes |
|-------|------|-------|
| `society_id` | integer | The active society. |
| `type` | string \| null | `"building"` or `"individual_houses"`, or `null` if not yet chosen. |
| `status` | string | Society status: `"onboarding"` or `"active"`. |
| `current_step` | string | `"type_selection"` \| `"structure_mapping"` \| `"review"` \| `"completed"`. |
| `current_building_index` | integer \| null | Display order of the building last mapped (building-type only), for resuming a multi-building wizard. |
| `draft` | object \| null | Whatever was last saved via `PUT /onboarding/draft`, verbatim. `{}`/`null` if nothing saved yet. |
| `numbering_defaults` | object \| null | The numbering config used for the last building mapped, so the UI can prefill the next building's form. `{}`/`null` if none yet. |
| `buildings` | array of `BuildingOut` | All buildings created so far (empty for individual-houses societies). |
| `rows` | array of `RowOut` | All rows created so far (empty for building-type societies). |
| `next_action` | string \| null | Computed hint: `"select_type"` \| `"create_buildings"` \| `"map_building"` \| `"create_rows"` \| `"review"` \| `"done"`. |

`BuildingOut` fields: `id, society_id, name, display_order, numbering_config` (the stored
`BuildingNumberingConfig` as a plain object, `{}` before the building has been mapped).

`RowOut` fields: `id, society_id, display_order, label, houses_count, numbering_config` (the
stored `RowNumberingConfig` as a plain object).

**Example — mid-onboarding, building-type society, one building created but not yet mapped:**

```json
{
  "society_id": 7,
  "type": "building",
  "status": "onboarding",
  "current_step": "structure_mapping",
  "current_building_index": null,
  "draft": {"current_step": "structure_mapping", "selected_building_id": 3},
  "numbering_defaults": {},
  "buildings": [
    {
      "id": 3,
      "society_id": 7,
      "name": "Wing A",
      "display_order": 1,
      "numbering_config": {}
    }
  ],
  "rows": [],
  "next_action": "map_building"
}
```

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"Society not found."` | `{"society_id": 7}` | Defensive only — shouldn't normally occur since the JWT implies an existing society. |

---

### `PUT /onboarding/draft`

Saves arbitrary in-progress wizard form state, so a refresh or a different device/tab can pick
up where the admin left off. Not audited (it's scratch data, not a committed change).

**Permission:** `onboarding.manage`.

#### Request

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `draft` | object | No (defaults to `{}`) | Stored verbatim. If it contains a `"current_step"` key, that value also updates `progress.current_step`. If it contains `"current_building_index"`, that also updates `progress.current_building_index`. |

```json
{
  "draft": {
    "current_step": "structure_mapping",
    "selected_building_id": 3,
    "floors_form": [{"level": 0, "is_ground": true, "houses_count": 4}]
  }
}
```

#### Response — `200 OK`

```json
{
  "status": "saved"
}
```

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 409 | `conflict` | `"Onboarding is already complete for this society."` | `{"society_id": 7, "status": "active"}` | Onboarding already finished — drafts can no longer be saved. |
| 404 | `not_found` | `"Society not found."` | `{"society_id": ...}` | Defensive. |

---

### `POST /onboarding/type`

Wizard step 1 — chooses the society's structure type. Sets `current_step` to
`"structure_mapping"` on success.

**Permission:** `onboarding.manage`.

#### Request

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `type` | string | Yes | Must be `"building"` or `"individual_houses"`. |

```json
{
  "type": "building"
}
```

#### Response — `200 OK`

```json
{
  "society_id": 7,
  "type": "building",
  "status": "onboarding"
}
```

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 422 | `validation_error` | `"Invalid society type."` | `{"field": "type", "allowed": ["building", "individual_houses"]}` | `type` isn't one of the two allowed values. |
| 409 | `conflict` | `"Onboarding is already complete for this society."` | `{"society_id": ..., "status": ...}` | Onboarding already finished. |
| 409 | `conflict` | `"Cannot change society type after houses exist."` | `{"current_type": "building"}` | Trying to switch type after houses were already generated. |
| 404 | `not_found` | `"Society not found."` | `{"society_id": ...}` | Defensive. |

---

## Building-type flow

Applies only when `type == "building"`. Sequence: create buildings → map each building (floors
+ numbering → houses) → optionally preview → `POST /onboarding/complete`.

### `POST /onboarding/buildings`

Creates one or more buildings (towers/wings) by name. New buildings always append after any
existing ones — `display_order` never resets.

**Permission:** `onboarding.manage`.

#### Request

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `names` | string[] | Yes, at least 1 | Each name is trimmed. Empty (after trim), duplicated within the request, or already used by an existing building in this society → rejected. Case-sensitive uniqueness. |

```json
{
  "names": ["Wing A", "Wing B"]
}
```

#### Response — `200 OK`

Array of `BuildingOut` (see field list under `GET /onboarding/state`), one per created building,
in the order given:

```json
[
  {
    "id": 3,
    "society_id": 7,
    "name": "Wing A",
    "display_order": 1,
    "numbering_config": {}
  },
  {
    "id": 4,
    "society_id": 7,
    "name": "Wing B",
    "display_order": 2,
    "numbering_config": {}
  }
]
```

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 422 | `validation_error` | `"Buildings can only be created for a 'building'-type society."` | `{"type": "individual_houses"}` | Society type isn't `"building"`. |
| 422 | `validation_error` | `"Building name cannot be empty."` | `{"field": "names"}` | A name is blank after trimming. |
| 409 | `conflict` | `"Duplicate building name in request."` | `{"name": "Wing A"}` | Same name appears twice in `names`. |
| 409 | `conflict` | `"A building with this name already exists."` | `{"name": "Wing A"}` | Name collides with an existing building. |
| 404 | `not_found` | `"Society not found."` | `{"society_id": ...}` | Defensive. |

---

### `POST /onboarding/buildings/{building_id}/map`

The core generator: define this building's floors + numbering scheme, and it creates all of
that building's houses in one call. **Each building can only be mapped once** — to add more
floors to an already-mapped building, use `POST /onboarding/buildings/{id}/floors` instead.

**Permission:** `onboarding.manage`.

#### Request

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `floors` | array of floor objects | Yes, at least 1 | See below. |
| `numbering_config` | object | Yes | See below. |
| `default_houses_per_floor` | integer ≥ 0 | No | Fallback house count for any floor that doesn't specify its own `houses_count`. If a floor has neither, it's an error. |

Each **floor object**:

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `level` | integer ≥ 0 | Yes | `0` is reserved for the ground floor. Upper floors must be ≥ 1 and distinct. At most one floor may have `is_ground: true`, and it must be `level: 0`. |
| `is_ground` | boolean | No (default `false`) | Marks this as the ground floor. |
| `label` | string, ≤64 chars | No | Optional display label. |
| `houses_count` | integer ≥ 0 | No | Number of houses on this floor. Falls back to `default_houses_per_floor` if omitted. |
| `manual_numbers` | string[] | No (default `[]`) | Only used when `numbering_config.mode == "manual"` — must have exactly `houses_count` entries, all non-empty after trim. |

The **`numbering_config`** object:

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `mode` | string | Yes | `"auto"` \| `"sequential"` \| `"manual"` — see numbering modes below. |
| `count_pad` | integer, 0–6 | No (default `2`) | Zero-padding width for `auto` mode numbers, e.g. `pad=2` → `01, 02, ...`. |
| `ground_prefix` | string, 1–8 chars | No (default `"G"`) | Prefix used for the ground floor in `auto` mode. |
| `has_ground` | boolean | No (default `false`) | Informational flag matching whether a ground floor is present. |
| `sequential_scope` | string | No (default `"per_building"`) | `"per_building"` (counter restarts at 1 per building) or `"continuous"` (counter continues across all of this society's buildings). Only relevant for `mode: "sequential"`. |
| `display_separator` | string, ≤4 chars | No (default `"-"`) | Separator used when computing `display_code` (e.g. `"A-201"`). |

**Numbering modes:**

- **`auto`** — per floor, restarting at 1: `number = prefix + zero_padded(position, count_pad)`, where `prefix` is `ground_prefix` on the ground floor or the floor's `level` elsewhere. E.g. floor 2, `count_pad=2` → `201, 202, ..., 210`; ground floor → `G01, G02, ...`.
- **`sequential`** — one running counter across all floors (ground first, then ascending level), starting at 1 for `sequential_scope: "per_building"`, or continuing from this society's highest previously-generated sequential building number for `"continuous"`. Numbers are plain digits (no prefix/padding), e.g. `1, 2, 3, ...`.
- **`manual`** — each floor's `manual_numbers` list is used verbatim (trimmed), count must match `houses_count`.

```json
{
  "floors": [
    {"level": 0, "is_ground": true, "houses_count": 2},
    {"level": 1, "houses_count": 4},
    {"level": 2, "houses_count": 4}
  ],
  "numbering_config": {
    "mode": "auto",
    "count_pad": 2,
    "ground_prefix": "G",
    "has_ground": true,
    "display_separator": "-"
  }
}
```

#### Response — `200 OK`

Array of `HouseOut` for every house just generated in this building (`display_code` is `""` on
this response — see the note at the top of this doc):

| Field | Type | Notes |
|-------|------|-------|
| `id` | integer | |
| `society_id` | integer | |
| `building_id` | integer \| null | Set for building-type houses. |
| `floor_id` | integer \| null | Set for building-type houses. |
| `row_id` | integer \| null | `null` for building-type houses. |
| `position_in_row` | integer \| null | `null` for building-type houses. |
| `number` | string | The generated (or manual) house number, e.g. `"201"`, `"G01"`. |
| `numbering_mode` | string | `"auto"` \| `"sequential"` \| `"manual"` — how this number was produced. |
| `number_overridden` | boolean | `false` until changed via `PATCH /onboarding/houses/{id}`. |
| `status` | string | Always `"empty"` on creation. |
| `display_code` | string | `""` on this endpoint (see note above). |

```json
[
  {"id": 101, "society_id": 7, "building_id": 3, "floor_id": 10, "row_id": null, "position_in_row": null, "number": "G01", "numbering_mode": "auto", "number_overridden": false, "status": "empty", "display_code": ""},
  {"id": 102, "society_id": 7, "building_id": 3, "floor_id": 10, "row_id": null, "position_in_row": null, "number": "G02", "numbering_mode": "auto", "number_overridden": false, "status": "empty", "display_code": ""},
  {"id": 103, "society_id": 7, "building_id": 3, "floor_id": 11, "row_id": null, "position_in_row": null, "number": "101", "numbering_mode": "auto", "number_overridden": false, "status": "empty", "display_code": ""}
]
```

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 422 | `validation_error` | `"Building mapping requires a 'building'-type society."` | `{"type": ...}` | Society isn't building-type. |
| 404 | `not_found` | `"Building not found."` | `{"building_id": ...}` | No such building in this society. |
| 409 | `conflict` | `"This building has already been mapped."` | `{"building_id": ...}` | The building already has houses — use `.../floors` instead. |
| 422 | `validation_error` | `"Invalid building numbering mode."` | `{"field": "mode", "allowed": ["auto", "sequential", "manual"]}` | Bad `numbering_config.mode`. |
| 422 | `validation_error` | `"Invalid sequential_scope."` | `{"field": "sequential_scope", "allowed": ["per_building", "continuous"]}` | Bad `sequential_scope`. |
| 422 | `validation_error` | `"A building may have at most one ground floor."` | `{"field": "floors"}` | More than one `is_ground: true` floor in the request. |
| 422 | `validation_error` | `"The ground floor must have level 0."` | `{"level": ...}` | Ground floor's `level` isn't `0`. |
| 422 | `validation_error` | `"Upper floors must have level >= 1."` | `{"level": ...}` | A non-ground floor has `level < 1`. |
| 422 | `validation_error` | `"Duplicate floor level."` | `{"level": ...}` | Two floors share the same `level`. |
| 422 | `validation_error` | `"A floor needs houses_count or a building default_houses_per_floor."` | `{"field": "houses_count", "level": ..., "is_ground": ...}` | A floor has no house count and no fallback was given. |
| 422 | `validation_error` | `"floor houses count cannot be negative (got {count})."` | `{"field": "numbering"}` | Defensive — negative count. |
| 422 | `validation_error` | `"Manual numbers count must match houses_count for the floor (level {level}: expected {n}, got {m})."` | `{"field": "numbering"}` | `manual_numbers` length mismatch, `mode: "manual"`. |
| 422 | `validation_error` | `"Empty manual house number on floor level {level}."` | `{"field": "numbering"}` | A `manual_numbers` entry is blank after trim. |
| 422 | `validation_error` | `"House number clash — batch rejected."` | `{"clashes": ["201", "202", ...]}` | Generated/manual numbers collide with each other or with existing numbers in this building — the whole batch is rejected together. |

---

### `POST /onboarding/buildings/{building_id}/floors`

Adds more floors (and their houses) to a building that's **already been mapped once**. Reuses
the building's stored numbering config from the original `map_building` call — you don't
re-specify `mode` here, only the new floors.

**Permission:** `onboarding.manage`. Not blocked once onboarding is complete — this is also a
legitimate post-onboarding growth action (e.g. a new floor gets built).

#### Request

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `floors` | array of floor objects | Yes, at least 1 | Same shape as in `map_building`. Validated against both the new batch and this building's *existing* floors (no duplicate ground floor, no duplicate levels). |
| `default_houses_per_floor` | integer ≥ 0 | No | Same fallback behavior as `map_building`. |

```json
{
  "floors": [
    {"level": 3, "houses_count": 4}
  ]
}
```

#### Response — `200 OK`

Array of `HouseOut` — only the newly generated houses for the new floors (same shape as
`map_building`'s response, `display_code` again `""`).

```json
[
  {"id": 110, "society_id": 7, "building_id": 3, "floor_id": 12, "row_id": null, "position_in_row": null, "number": "301", "numbering_mode": "auto", "number_overridden": false, "status": "empty", "display_code": ""}
]
```

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 422 | `validation_error` | `"Adding floors requires a 'building'-type society."` | `{"type": ...}` | Society isn't building-type. |
| 404 | `not_found` | `"Building not found."` | `{"building_id": ...}` | No such building. |
| 422 | `validation_error` | `"This building has no stored numbering config; map it first."` | `{"building_id": ...}` | Building was never mapped via `POST .../map`. |
| 422 | `validation_error` | Same ground/level/duplicate-floor messages as `map_building` | (same shapes) | Floor shape conflicts with the new batch or the building's existing floors. |
| 422 | `validation_error` | Same `NumberingError` messages as `map_building` (count/manual-number mismatches) | `{"field": "numbering"}` | See `map_building` errors. |
| 422 | `validation_error` | `"House number clash — batch rejected."` | `{"clashes": [...]}` | New numbers collide with each other or the building's existing numbers. |

---

### `GET /onboarding/buildings/{building_id}/preview`

Read-only listing of a building's already-generated houses — use this to show the admin what
was created before they move on. **This is the only endpoint where `display_code` is actually
populated** (e.g. `"A-201"`).

**Permission:** `onboarding.read`.

#### Request

Path param `building_id` only, no body.

#### Response — `200 OK`

Array of `HouseOut`, same fields as above, but with `display_code` filled in:

```json
[
  {"id": 101, "society_id": 7, "building_id": 3, "floor_id": 10, "row_id": null, "position_in_row": null, "number": "G01", "numbering_mode": "auto", "number_overridden": false, "status": "empty", "display_code": "Wing A-G01"}
]
```

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"Building not found."` | `{"building_id": ...}` | No such building in this society. |

---

## Individual-houses-type flow

Applies only when `type == "individual_houses"`. There's just one endpoint: define one or more
rows (each with its own numbering) and it generates all their houses in one call.

### `POST /onboarding/rows`

**Permission:** `onboarding.manage`. Blocked once onboarding is already complete.

#### Request

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `rows` | array of row objects | Yes, at least 1 | Processed in ascending `display_order`. |

Each **row object**:

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `display_order` | integer ≥ 1 | Yes | Must be unique — duplicated within the request or colliding with an existing row → rejected. |
| `label` | string, ≤64 chars | No | Optional display label, e.g. `"Row A"`. |
| `houses_count` | integer ≥ 0 | Yes | Number of houses in this row. |
| `numbering_config` | object | Yes | `{"mode": "sequential" \| "custom" \| "manual", "prefix": str (≤16 chars, default ""), "pad": int (0–6, default 0)}`. |
| `manual_numbers` | string[] | No (default `[]`) | Only used with `mode: "manual"` — must match `houses_count`, all non-empty after trim. |

**Numbering modes:**

- **`sequential`** — one continuous `1, 2, 3, ...` counter shared across **all rows in the
  request, and continuing from any previously created rows** in this society (only counting
  houses whose stored `numbering_mode` is `"sequential"`). No prefix/padding.
- **`custom`** — per-row counter restarting at 1: `number = prefix + (zero_padded(position, pad) if pad else str(position))`. E.g. `prefix: "alpha", pad: 0` → `alpha1, alpha2, ...`. **Note:** because the `houses.numbering_mode` column only supports `auto|sequential|manual`, houses generated with `custom` are stored with `numbering_mode: "manual"` — this is a known quirk of the current schema, not a bug in your integration.
- **`manual`** — `manual_numbers` used verbatim (trimmed), count must match `houses_count`.

```json
{
  "rows": [
    {
      "display_order": 1,
      "label": "Row A",
      "houses_count": 5,
      "numbering_config": {"mode": "sequential"}
    },
    {
      "display_order": 2,
      "label": "Row B",
      "houses_count": 3,
      "numbering_config": {"mode": "custom", "prefix": "B-", "pad": 2}
    }
  ]
}
```

#### Response — `200 OK`

Array of `HouseOut` for every house generated across all rows in the request (`row_id` and
`position_in_row` set, `building_id`/`floor_id` are `null`; `display_code` is `""` — see note
at the top of this doc):

```json
[
  {"id": 201, "society_id": 7, "building_id": null, "floor_id": null, "row_id": 5, "position_in_row": 1, "number": "1", "numbering_mode": "sequential", "number_overridden": false, "status": "empty", "display_code": ""},
  {"id": 206, "society_id": 7, "building_id": null, "floor_id": null, "row_id": 6, "position_in_row": 1, "number": "B-01", "numbering_mode": "manual", "number_overridden": false, "status": "empty", "display_code": ""}
]
```

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 422 | `validation_error` | `"Rows can only be created for an 'individual_houses'-type society."` | `{"type": ...}` | Society isn't individual-houses type. |
| 409 | `conflict` | `"Onboarding is already complete for this society."` | (society status) | Onboarding already finished. |
| 422 | `validation_error` | `"Invalid individual numbering mode."` | `{"field": "mode", "allowed": ["sequential", "custom", "manual"]}` | Bad `numbering_config.mode`. |
| 409 | `conflict` | `"Duplicate row display_order in request."` | `{"display_order": ...}` | Same `display_order` twice in `rows`. |
| 409 | `conflict` | `"A row with this display_order already exists."` | `{"display_order": ...}` | Collides with an existing row. |
| 422 | `validation_error` | `"row houses count cannot be negative (got {count})."` | `{"field": "numbering", "row": ...}` | Defensive. |
| 422 | `validation_error` | `"Unknown individual numbering mode '{mode}'."` | `{"field": "numbering", "row": ...}` | Defensive (pre-validated above already). |
| 422 | `validation_error` | `"Manual numbers count must match houses_count for the row (expected {n}, got {m})."` | `{"field": "numbering", "row": ...}` | `mode: "manual"`, count mismatch. |
| 422 | `validation_error` | `"Empty manual house number in row."` | `{"field": "numbering", "row": ...}` | A `manual_numbers` entry is blank after trim. |
| 422 | `validation_error` | `"House number clash — batch rejected."` | `{"clashes": [...]}` | Generated/manual numbers collide with each other or existing individual house numbers. |
| 404 | `not_found` | `"Society not found."` | `{"society_id": ...}` | Defensive. |

---

## Finishing onboarding

### `POST /onboarding/complete`

Finalizes onboarding: flips the society from `status: "onboarding"` to `status: "active"`.
After this succeeds, `GET /me`'s `onboarding_required` becomes `false` for this society (see
the [auth reference](auth.md#get-me)).

**Permission:** `onboarding.manage`.

**Requires:** a type has been selected, and at least one house exists.

#### Request

No parameters.

#### Response — `200 OK`

```json
{
  "society_id": 7,
  "status": "active"
}
```

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 409 | `conflict` | `"Onboarding is already complete for this society."` | `{"status": "active"}` | Already finalized. |
| 422 | `validation_error` | `"Cannot complete onboarding before selecting a society type."` | `{"missing": "type"}` | `POST /onboarding/type` was never called. |
| 422 | `validation_error` | `"Cannot complete onboarding before at least one house exists."` | `{"missing": "houses"}` | No houses have been generated yet. |
| 404 | `not_found` | `"Society not found."` | `{"society_id": ...}` | Defensive. |

---

## Post-onboarding edits

These endpoints are **not limited to the onboarding wizard** — they're ordinary maintenance
actions the society admin can use at any time, including long after onboarding is complete.
Each is blocked instead by its own specific conflict (occupancy, non-empty status, name
collision), not by onboarding status.

### `PATCH /onboarding/buildings/{building_id}` — rename a building

**Permission:** `onboarding.manage`.

#### Request

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `name` | string, 1–128 chars | Yes | Trimmed. If unchanged after trimming, no uniqueness check runs. |

```json
{
  "name": "Wing A - North"
}
```

#### Response — `200 OK`

`BuildingOut`:

```json
{
  "id": 3,
  "society_id": 7,
  "name": "Wing A - North",
  "display_order": 1,
  "numbering_config": {"mode": "auto", "count_pad": 2, "ground_prefix": "G", "has_ground": true, "sequential_scope": "per_building", "display_separator": "-"}
}
```

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"Building not found."` | `{"building_id": ...}` | No such building. |
| 422 | `validation_error` | `"Building name cannot be empty."` | `{"field": "name"}` | Blank after trim. |
| 409 | `conflict` | `"A building with this name already exists."` | `{"name": ...}` | Collides with another building in this society. |

---

### `PATCH /onboarding/houses/{house_id}` — override a house number

Manually overrides a single house's number, regardless of how it was originally generated.
Works at any time, not just during onboarding.

**Permission:** `onboarding.manage`.

#### Request

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `number` | string, 1–32 chars | Yes | Trimmed; must be unique within the correct scope — among the same building's houses for building-type houses, or society-wide for individual houses (excluding this house's own current number). |

```json
{
  "number": "201-A"
}
```

#### Response — `200 OK`

`HouseOut`, with `number_overridden` now `true` (`display_code` is `""` here too — see the
top-of-doc note):

```json
{
  "id": 103,
  "society_id": 7,
  "building_id": 3,
  "floor_id": 11,
  "row_id": null,
  "position_in_row": null,
  "number": "201-A",
  "numbering_mode": "auto",
  "number_overridden": true,
  "status": "empty",
  "display_code": ""
}
```

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"House not found."` | `{"house_id": ...}` | No such house. |
| 422 | `validation_error` | `"House number cannot be empty."` | `{"field": "number"}` | Blank after trim. |
| 422 | `validation_error` | `"House number already in use."` | `{"clashes": ["201-A"]}` | Collides with another house's number in the same scope. Note: this is `validation_error` (422), not a `conflict` (409), despite being a uniqueness clash. |

---

### `DELETE /onboarding/buildings/{building_id}`

Deletes a building along with its floors and houses — **only if none of its houses are
occupied or non-empty**.

**Permission:** `onboarding.manage`.

#### Request

Path param only, no body.

#### Response

`204 No Content` — empty body.

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"Building not found."` | `{"building_id": ...}` | No such building. |
| 409 | `conflict` | `"Cannot delete a building with occupied houses."` | `{"building_id": ...}` | Any house in this building has `status != "empty"`. |
| 409 | `conflict` | `"Cannot delete: houses have active occupancy."` | `{"building_id": ...}` | Any house in this building has a current occupancy record. |

---

### `DELETE /onboarding/floors/{floor_id}`

Deletes a floor and its houses — same occupancy/status guards as deleting a building, scoped
to just this floor.

**Permission:** `onboarding.manage`.

#### Request

Path param only, no body.

#### Response

`204 No Content` — empty body.

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"Floor not found."` | `{"floor_id": ...}` | No such floor. |
| 409 | `conflict` | `"Cannot delete a floor with occupied houses."` | `{"floor_id": ...}` | Any house on this floor has `status != "empty"`. |
| 409 | `conflict` | `"Cannot delete: houses have active occupancy."` | `{"floor_id": ...}` | Any house on this floor has a current occupancy record. |

---

### `DELETE /onboarding/houses/{house_id}`

Deletes a single house — only if it's empty and has no current occupancy.

**Permission:** `onboarding.manage`.

#### Request

Path param only, no body.

#### Response

`204 No Content` — empty body.

#### Errors

| Status | `code` | `message` | `details` | When |
|--------|--------|-----------|-----------|------|
| 404 | `not_found` | `"House not found."` | `{"house_id": ...}` | No such house. |
| 409 | `conflict` | `"Cannot delete an occupied house."` | `{"house_id": ..., "status": ...}` | House's `status != "empty"`. |
| 409 | `conflict` | `"Cannot delete: houses have active occupancy."` | `{"house_id": ...}` | House has a current occupancy record. |
