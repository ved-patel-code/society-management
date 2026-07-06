# Onboarding (Module 1) — As-Built Index

> Lean navigation index (docs/04). Points to code; not a copy of it. Design source
> of truth: `docs/modules/onboarding.md`. Build/QA record: `docs/build-log/onboarding/`.

## Status
**COMPLETE** — built, code-reviewed, tested. Full suite **218 passing** (72
onboarding tests across numbering + smoke + happy + security + bad-path + e2e +
later-edits), on branch `feat/onboarding`. First toggleable feature module; the
`houses` registry it creates is consumed by every later module.

## File map
Module package `app/modules/onboarding/`:
- `models.py` — 5 tables: `onboarding_progress`, `buildings`, `floors`, `rows`,
  `houses` (the shared registry) with partial unique indexes.
- `numbering.py` — pure house-number engine (no DB): AUTO / SEQUENTIAL
  (per_building|continuous) / MANUAL for buildings; SEQUENTIAL / CUSTOM / MANUAL for
  individual; display-code + clash helpers.
- `schemas.py` — Pydantic request/response contracts + the `current_step` state
  machine (`type_selection → structure_mapping → review → completed`).
- `repository.py` — SQL-only, `society_id`-scoped; house-registry reads.
- `service.py` — all business logic + audit (never commits).
- `router.py` — thin `/onboarding/*` routes, gated `require_module('onboarding')` +
  permission.
- `spec.py` — `ONBOARDING_SPEC` (`onboarding.manage`/`onboarding.read`) +
  `default_role_permissions` + `register_onboarding`.
- `alembic/versions/0002_onboarding.py` — migration (chained off `0001_foundation`).

Foundation edits made for this module (minimal, documented as deviations):
- `app/core/registry.py` — `ModuleSpec.default_role_permissions` field.
- `app/platform/societies/service.py` — `set_modules` auto-grants a module's default
  role permissions on enable.
- `app/platform/roles/service.py` — `grant_default_module_permissions` (additive, audited).
- `app/platform/users/me_service.py` + `me_router.py` — `onboarding_required` flag on `/me`.
- `app/main.py`, `app/cli/seed.py`, `alembic/env.py` — register the module.
- `tests/conftest.py` (idempotent seed) + `scripts/run-tests.sh` (workers 4→8) —
  harness hardening (see build-log test-gate).

## Functions (summary · deps · @location)
- `OnboardingService.select_type / create_buildings / map_building / add_floors /
  preview_building / create_rows / override_house_number / complete / rename_building /
  delete_building / delete_floor / delete_house / get_state / save_draft` — the full
  wizard + later edits; generation via the pure engine; audit per §5; never commits.
  deps: repository, numbering, AuditService, Society. @ onboarding/service.py
- `OnboardingService.list_houses / resolve_house` — the cross-module **house
  registry** (list with display codes; resolve by (building, number) or by number).
  deps: repository. @ onboarding/service.py
- `generate_building_numbers / generate_row_numbers / building_display_code /
  individual_display_code / find_duplicate_numbers` — pure numbering. @ onboarding/numbering.py
- `RoleService.grant_default_module_permissions` — additive, idempotent grant of a
  module's default role permissions on enable. @ platform/roles/service.py

## Tables owned
`onboarding_progress`, `buildings`, `floors`, `rows`, `houses`.
(`houses.status` + `first_left_empty_on` are created here but WRITTEN by House &
Occupancy — clear column ownership, one table.)

## Endpoints
`GET /onboarding/state` · `PUT /onboarding/draft` · `POST /onboarding/type` ·
`POST /onboarding/buildings` · `POST /onboarding/buildings/{id}/map` ·
`POST /onboarding/buildings/{id}/floors` (add-floors) ·
`GET /onboarding/buildings/{id}/preview` · `PATCH /onboarding/buildings/{id}` (rename) ·
`POST /onboarding/rows` · `PATCH /onboarding/houses/{id}` (override) ·
`POST /onboarding/complete` · `DELETE /onboarding/{buildings|floors|houses}/{id}`.
All gated `require_module('onboarding')` + `onboarding.manage` (reads: `onboarding.read`).

## Audited actions (emitted)
`onboarding.type_selected`, `onboarding.building_created`, `onboarding.building_renamed`,
`onboarding.building_deleted`, `onboarding.floor_added`, `onboarding.floor_deleted`,
`onboarding.houses_generated`, `onboarding.house_number_overridden`,
`onboarding.house_deleted`, `onboarding.completed`. Plus (foundation)
`permission.granted_by_module` when the module is enabled.

## Cross-module wiring (provided to other modules)
- **House registry** — `OnboardingService.list_houses` / `resolve_house` (docs §7):
  list houses with derived display codes; resolve by `(building, number)` or by
  `number`. Finance's "enter house number" flow + House & Occupancy consume this.
- **`houses` table** — Onboarding owns structure columns; House & Occupancy owns
  `status` / `first_left_empty_on` / occupancy.

## Testing
Reuses the shared harness (`backend/tests/`): isolated per-worker `society_test`
DBs, truncate+reseed (now idempotent), fixtures. Run:
`docker compose exec backend bash scripts/run-tests.sh`. 218 tests pass (~21s).

## Deviations from design (drift vs docs/modules/onboarding.md)
1. **Default module permissions → society_admin on enable** (NEW reusable pattern):
   the spec §2 says `society_admin` gets `onboarding.manage`/`onboarding.read` but did
   not specify the mechanism. Implemented as `ModuleSpec.default_role_permissions` +
   auto-grant in `SocietyService.set_modules`. Every later module's "Default seeding
   (data-driven roles)" line uses this.
2. **`onboarding_required` on `/me`** (foundation `build_me_view` extension) — the
   blocking-wizard signal named in §4; the exact `/me` field was unspecified.
3. **`current_step` state machine pinned** to `type_selection → structure_mapping →
   review → completed` (§3 left it narrative).
4. **`default_houses_per_floor` added** to the building map + add-floors requests, with
   per-floor `houses_count` override — implements §3's "falls back to the building
   default" (no default field existed in the first cut). Floors may each have different
   counts.
5. **`POST /onboarding/buildings/{id}/floors` (add-floors) added** — §4/§6 list "add
   floor" as a later edit but named no endpoint; `map_building` is initial-map-once.
6. **Continuous sequential seeds only from prior continuous-sequential houses** (not
   AUTO/manual numbers) — required to honor §4 "one running sequence across towers."
7. **DELETE guard is status-only for v1 (DEFERRED richer guard):** delete is blocked
   when a house's `status != 'empty'`. The spec's fuller guard ("checked via House &
   Occupancy + Finance services" — dues + occupancy) is **deferred until those modules
   exist**; wire the real checks into `delete_building/floor/house` at that time. This
   is a known, tracked gap to complete once all modules are built.
8. Schema-only / future (unchanged from design, not implemented): `rows.both_sides`,
   bulk CSV import, changing society type after completion, re-numbering tools (§10).

Everything else matches `docs/modules/onboarding.md`.
