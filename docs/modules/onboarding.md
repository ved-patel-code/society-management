# Onboarding Module — Design

> Design doc. Foundation reading: [../01-project-overview](../01-project-overview.md) · [../02-architecture](../02-architecture.md) · [../03-backend-and-db-principles](../03-backend-and-db-principles.md) · [../05-cross-module-contracts](../05-cross-module-contracts.md) · [../platform/platform-foundation](../platform/platform-foundation.md)
>
> **Confirmed decisions baked in:** super_admin sets society NAME only, admin picks TYPE + maps structure in onboarding · house numbers unique **per building**, identity = building + number (display `A-201`) · **blocking, resumable wizard** (until complete, login shows only the wizard) · initial setup **+ basic later edits** (guarded deletes) · Ground floor supported (default prefix `G` → G01) · AUTO count zero-padded to 2 (configurable) · SEQUENTIAL scope selectable **per-building (default)** or continuous · individual custom = per-row prefix + count from 1 · resume saves completed buildings **and** an in-progress draft.

## 1. Purpose & scope
Map a society's physical structure so every **house** exists (created `empty`) and is usable by every other module. Covers: choose society **type**, define **buildings → floors → houses** OR **rows → houses**, three **numbering modes** + **prefill-repeat** across buildings, **manual overrides**, a **blocking resumable wizard**, and **basic later structure edits** (add building/floor/house, rename, override number; deletes guarded).

**Out of scope:** house **status/occupancy** and owner/tenant details (House & Occupancy module — it writes to the same `houses` rows). Both-sides-of-row (schema flag only; future). Bulk CSV import (future).

## 2. Audience & permissions
- **society_admin** runs onboarding. **super_admin** only sets the society name at creation (may view).
- Permissions (fine-grained, `onboarding.*`): `onboarding.manage` (map + edit structure, override numbers, complete), `onboarding.read`.
- Gated by `require_module('onboarding')` + `require_permission('onboarding.manage')`.

## 3. Data model
All tables: `id` BIGINT identity PK, `created_at`, `updated_at`, `society_id`. Logic in services; DB holds PK/FK/NOT NULL/UNIQUE only.

**societies** (foundation, amended) — `type`(building|individual_houses) **NULLABLE** until onboarding sets it; `status` flips `onboarding → active` on completion.

**onboarding_progress** (new; one per society) — `society_id` UNIQUE FK, `type_selected`, `current_step`, `current_building_index`, `draft` JSONB (in-progress building's typed inputs, for exact resume), `numbering_defaults` JSONB (last-used mode/pad/ground prefix for prefill), `updated_at`.

**buildings** (building type) — `society_id` FK, `name`, `display_order`, `numbering_config` JSONB (mode, count_pad=2, ground_prefix='G', has_ground, sequential_scope=per_building, display_separator='-'). UNIQUE(`society_id`,`name`); idx(`society_id`).

**floors** (building type) — `society_id` FK, `building_id` FK, `level` INT (upper floors 1..N), `is_ground` BOOL, `label`, `houses_count` (per-floor override lives here). UNIQUE(`building_id`,`level`) + a partial unique for the single ground floor; idx(`building_id`).

**rows** (individual type) — `society_id` FK, `display_order`, `label`, `houses_count`, `numbering_config` JSONB (mode, per-row `prefix`, pad), `both_sides` BOOL DEFAULT false **(future, schema only)**. UNIQUE(`society_id`,`display_order`); idx(`society_id`).

**houses** — `society_id` FK; **building type:** `building_id` FK, `floor_id` FK, `row_id` NULL; **individual type:** `building_id` NULL, `row_id` FK, `position_in_row`; `number` (bare, e.g. `201`), `numbering_mode`(auto|sequential|manual), `number_overridden` BOOL, `status`(empty|owned|rented|to_let|for_sale) DEFAULT empty, `first_left_empty_on` DATE NULL.
- **Uniqueness (partial indexes):** building type → UNIQUE(`society_id`,`building_id`,`number`) WHERE `building_id IS NOT NULL`; individual type → UNIQUE(`society_id`,`number`) WHERE `building_id IS NULL`.
- idx(`society_id`,`status`) for status filters (used by House & Occupancy).
- **Display code (derived, not stored):** building → `{building.name}{separator}{number}` (e.g. `A-201`); individual → `{number}`. Derived so renaming a building never drifts.

## 4. Business rules
**Gating (blocking wizard):** while `society.status='onboarding'`, `GET /me` returns an `onboarding_required` state and only the onboarding module is accessible. Reopening the app resumes the wizard (from `onboarding_progress` + already-created buildings). Completion flips status → `active`, unlocking the app.

**Type selection (step 1):** sets `societies.type`. Cannot change once houses exist except via a guarded full reset.

**Building flow:** define count + names (admin types each building name — no auto-fill schemes) → per building: floors count (+ ground toggle), houses-per-floor (with per-floor overrides), numbering mode + config → generate that building's houses (reviewable, editable) → **prefill-repeat**: building 2..N open prefilled from the previous building's config, editable. Numbering mode may differ per building.

**Numbering algorithms:**
- **AUTO (building):** for each floor lowest→highest, `number = prefix + zeropad(count, pad)`, count restarts per floor. `prefix = ground_prefix if is_ground else str(level)`. Default pad 2 (`floor 2 → 201..210`, `ground → G01..`, `floor 10 → 1001..`).
- **SEQUENTIAL (building):** running counter from lowest floor up. `sequential_scope`: **per_building** (reset each tower — default) or **continuous** (one running sequence across towers in order).
- **MANUAL (building):** admin types each number.
- **Individual SEQUENTIAL:** one continuous `1,2,3..` across all rows from row 1.
- **Individual CUSTOM:** per-row `prefix` + count from 1 each row, no pad by default (`'alpha' → alpha1..`, `'10' → 101..110`).
- **Individual MANUAL:** admin types each.

**Overrides:** any generated number is editable (during the wizard or later); sets `number_overridden=true`; uniqueness enforced by the partial indexes (batch rejected on clash, offending numbers reported).

**Later edits (post-completion):** add building/floor/house, rename building, override numbers. **Delete a house/floor/building is blocked if any house is not `empty` (occupied) or has dues** (checked via House & Occupancy + Finance services). All edits audited.

**Invariants:** every house created `status='empty'`, `first_left_empty_on=NULL`. Generation runs in one transaction per building.

## 5. Audited actions
Written to `audit_log` (in-transaction, append-only):
- `onboarding.type_selected` — society type set.
- `onboarding.building_created` / `building_renamed` / `building_deleted` — building_id (+ old/new name on rename).
- `onboarding.floor_added` / `floor_deleted` — building_id, level.
- `onboarding.houses_generated` — building_id/row, numbering mode, count.
- `onboarding.house_number_overridden` — house_id, old → new number.
- `onboarding.house_deleted` — house_id (guarded).
- `onboarding.completed` — society status → active.

## 6. Endpoints (`/onboarding/*`, society from JWT)
- `GET /onboarding/state` — resume payload: type, buildings/rows so far, current step, in-progress draft, next action.
- `POST /onboarding/type` — set building | individual_houses.
- **Building:** `POST /onboarding/buildings` (count + names) · `POST /onboarding/buildings/{id}/map` (floors + ground toggle + per-floor houses + numbering config → generate) · `GET /onboarding/buildings/{id}/preview` (generated numbers) · `PATCH /onboarding/houses/{id}` (override number) · `PUT /onboarding/draft` (save in-progress inputs).
- **Individual:** `POST /onboarding/rows` (rows + houses/row + numbering config → generate) · `PATCH /onboarding/houses/{id}` (override).
- `POST /onboarding/complete` — validate + flip `society.status=active`.
- **Later edits:** `POST /onboarding/buildings` / `.../map` (add) · `PATCH /onboarding/buildings/{id}` (rename) · `DELETE /onboarding/{buildings|floors|houses}/{id}` (guarded).
All gated `require_module('onboarding')` + `require_permission('onboarding.manage')` (reads: `onboarding.read`).

## 7. Inter-module contracts
- **Provides:** the **house registry** — list houses (with derived display code), resolve a house by `(building, number)` or by `number` (used by Finance's "enter house number" flow), and structure reads for other modules.
- **Consumes:** foundation `TenantContext`, `AuditService`, `require_module/permission`, and society status gating.
- **Shared `houses` table ownership:** Onboarding **owns structure columns** (location, number, mode); **House & Occupancy owns** `status`, `first_left_empty_on`, occupancy. Clear column ownership, one table (closely-related house data).

## 8. Feature flag / config
- Module key `onboarding` (enabled by default; effectively core — a society can't operate unmapped).
- `society_modules.config`: default numbering pad width, default ground prefix.

## 9. Background jobs
None.

## 10. Open questions / future
Both-sides-of-row, bulk CSV structure import, changing society type after completion (guarded reset), and re-numbering tools.

## 11. Resolved decisions
1. **Per-building numbering mode**, prefilled from the previous building (changeable).
2. **Building names typed by the admin** — no auto-fill naming schemes.
3. **One shared `houses` table** with column ownership (Onboarding = structure, Occupancy = status/occupancy).
4. **Dedicated `onboarding_progress` table** for wizard draft/resume.
5. **`societies.type` nullable** — set during onboarding (foundation amended).
