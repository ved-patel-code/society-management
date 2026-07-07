# House & Occupancy (Module 2) — As-Built Index

> Lean navigation index (docs/04). Points to code; not a copy of it. Design source
> of truth: `docs/modules/house-occupancy.md`. Build/QA record:
> `docs/build-log/house-occupancy/`.

## Status
**COMPLETE** — built, code-reviewed, tested. Full suite **372 passing** (154
House & Occupancy tests across smoke + happy + badpaths + security + edge + e2e),
on branch `feat/house-occupancy`. Second toggleable feature module; writes the
`status` / `first_left_empty_on` / occupancy of the shared `houses` registry that
Onboarding created, and provides `current_owner_user_ids` for later modules.

## File map
Module package `app/modules/houses/`:
- `models.py` — 2 tables: `house_occupancies` (partial-unique current owner/tenant
  per house), `house_status_history` (append-only). Does NOT redeclare `houses`.
- `schemas.py` — Pydantic contracts (`StatusChangeRequest`, `OccupancyEditRequest`,
  `OwnerPayload`/`TenantPayload`, `HouseOut`/`HouseDetailOut`/`OccupancyOut`/
  `StatusHistoryOut`); email normalization; status/party domains.
- `repository.py` — SQL-only, `society_id`-scoped; occupancy CRUD, history, batched
  building fetch, `current_owner_user_ids`.
- `service.py` — all business logic: transition state machine, owner-identity/
  replacement, occupancy open/edit/close, `first_left_empty_on` once-only, audit +
  status-history writes (never commits).
- `router.py` — thin `/houses/*` routes, gated `require_module('houses')` +
  permission.
- `spec.py` — `HOUSES_SPEC` (`houses.read`/`update_status`/`manage_occupancy`),
  `depends_on: ['onboarding']`, `default_role_permissions`, `register_houses`.
- `alembic/versions/0003_house_occupancy.py` — migration (chained off
  `0002_onboarding`).

Foundation / onboarding edits made for this module (documented deviations):
- `app/platform/users/provisioning.py` — `revoke_house_access` completed: unlinks
  the current `house_occupancies` row (lazy import) and deactivates only when
  orphaned = no roles AND no remaining current occupancy.
- `app/modules/onboarding/{service,repository}.py` — occupancy-aware delete guard
  (`has_current_occupancy_for_building/floor/house`), completing the deferred
  guard from Module 1.
- `app/main.py`, `alembic/env.py` — register the module + import its models.
- `tests/test_roles_provisioning.py` — placeholder-key fix (a test used
  `houses.update_status` as a fake key that now really exists).

## Functions (summary · deps · @location)
- `HouseService.change_status` — the transition state machine: validate legality
  (never →empty) + required-fields-per-status, reconcile owner (create/update/
  replace by email identity), open/edit/close tenant on entering/leaving rented,
  stamp `first_left_empty_on` once, write status-history + audit only on a real
  transition (same-status POST = edit). deps: repository, AuditService,
  UserProvisioningService, House. @ houses/service.py
- `HouseService.edit_occupancy` — partial owner/tenant edit; owner email change →
  replacement (carry-over unchanged fields). @ houses/service.py
- `HouseService.list_houses / get_house_detail / get_history` — filtered/paginated
  reads with derived display codes (buildings batch-loaded — no N+1). @ houses/service.py
- `HouseService.current_owner_user_ids` — cross-module contract (docs §7): current
  owner login ids for the society. @ houses/service.py
- `UserProvisioningService.revoke_house_access` — occupant removal: unlink current
  occupancy, revoke tokens, deactivate if orphaned. @ platform/users/provisioning.py

## Tables owned
`house_occupancies`, `house_status_history`.
(Writes `houses.status` + `houses.first_left_empty_on`, owned by this module but
created by Onboarding's migration — clear column ownership, one table.)

## Endpoints
`GET /houses` (filters: status, building_id, floor_id, number; paginated) ·
`GET /houses/{id}` (detail + current owner/tenant) · `GET /houses/{id}/history` ·
`POST /houses/{id}/status` (change status + occupancy payload) ·
`PATCH /houses/{id}/occupancy/{party}` (edit owner/tenant).
All gated `require_module('houses')` + permission (reads: `houses.read`; status:
`houses.update_status`; occupancy edit: `houses.manage_occupancy`). Society always
from the JWT (`TenantContext`), never a path/body id.

## Audited actions (emitted)
`house.status_changed`, `house.occupancy_created`, `house.occupancy_updated`,
`house.owner_replaced`, `house.access_revoked` (+ foundation `user.created`,
`role.assigned`, `user.deactivated` via provisioning on owner create/replace).

## Cross-module wiring
- **Consumes:** Onboarding house registry (the `houses` rows); foundation
  `UserProvisioningService` (`create_or_link_user` with `role_key='resident'`,
  `revoke_house_access`), `AuditService`, `TenantContext`.
- **Provides:** `status` + `first_left_empty_on` for Finance (dues); current
  owner→house mapping for resident access; `current_owner_user_ids(society_id)`
  for Notice Board audience / Notifications recipients; occupancy state for
  Onboarding's delete guard.
- **Deferred wiring (skeleton-then-wire):** `house_occupancies.id_proof_document_id`
  is a nullable BIGINT with **no FK yet** — the Vault module's migration adds the
  FK to `vault_documents` and the actual image upload path.

## Testing
Reuses the shared harness (`backend/tests/`): isolated per-worker `society_test`
DBs, truncate+reseed, fixtures + `tests/_houses_helpers.py`. Run:
`docker compose exec backend bash scripts/run-tests.sh`. 372 tests pass (~41s).
Includes an N+1 perf guard on `list_houses`.

## Deviations from design (drift vs docs/modules/house-occupancy.md)
1. **Owner login role = `resident`** — the spec's "owner login auto-provisioned"
   did not name the role; owners get the society's `resident` role (sets up future
   owner-portal read; portal read features deferred).
2. **ID-proof image upload deferred** — nullable `id_proof_type` (text) +
   `id_proof_document_id` (BIGINT, no FK) columns exist and are editable/retained,
   but no upload path until Vault is built (spec §3/§7 "wired when Vault built").
3. **Tenant login/view deferred** — tenant occupancies keep `user_id=NULL`; no
   login provisioned (spec §4).
4. **`revoke_house_access` completed** (foundation deviation) — the Module 0
   skeleton now unlinks the occupancy and refines the orphan check (no roles AND
   no current occupancy). Lazy import avoids a platform→module load-time dep.
5. **Onboarding delete guard completed** (onboarding deviation) — the Module 1
   status-only guard now also blocks on a current occupancy. Resolves onboarding
   as-built deviation #7.
6. **N+1 removed** — `list_houses` batch-loads the page's buildings in one query
   (guarded by a perf test), rather than a per-row lookup.

Everything else matches `docs/modules/house-occupancy.md`.
