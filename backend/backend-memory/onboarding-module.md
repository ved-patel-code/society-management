---
name: onboarding-module
description: "Module 1 (Onboarding) — built; key patterns future modules reuse (default-perms-on-enable, house registry)"
metadata: 
  node_type: memory
  type: project
  originSessionId: 3231d4ab-84ca-4168-954b-0186e3a68d8c
---

**Module 1 — Onboarding is BUILT** (PR #3, branch `feat/onboarding`, 218 tests green). Society structure wizard: buildings/floors/houses or rows/houses, 6 numbering modes, blocking resumable wizard, `houses` shared registry. Code in `backend/app/modules/onboarding/` (models, numbering [pure engine], schemas, repository, service, router, spec). Migration `0002_onboarding.py`. As-built: `docs/implemented/onboarding.md`; build log: `docs/build-log/onboarding/`.

**NEW reusable pattern every later module uses — default module permissions on enable:** `ModuleSpec` now has `default_role_permissions: dict[str, list[str]]` (e.g. onboarding → `{"society_admin": ["onboarding.manage","onboarding.read"]}`). When super_admin enables a module for a society via `PUT /admin/societies/{id}/modules`, `SocietyService.set_modules` calls `RoleService.grant_default_module_permissions` (additive, idempotent, audits `permission.granted_by_module`). This is the mechanism for each module doc's "Default seeding (data-driven roles)" line — a new module just declares `default_role_permissions` in its spec. Role templates are copied with EMPTY perms on society creation, so this grant-on-enable is how a society_admin actually gets a module's permissions.

**Blocking wizard:** `me_service.build_me_view` returns `onboarding_required=true` while `society.status='onboarding'` (view hint only; gates unchanged). `MeResponse` schema carries the field.

**House registry (cross-module contract, docs §7):** `OnboardingService.list_houses` / `resolve_house(society_id, number=, building_id=)` — Finance's "enter house number" + House & Occupancy consume this. `houses.status` + `first_left_empty_on` are created by onboarding's migration but WRITTEN by House & Occupancy (Module 2, the next build).

**DEFERRED (must complete once Finance + House & Occupancy exist):** onboarding's DELETE guard is **status-only** for v1 (blocks delete when a house `status != 'empty'`). The spec's fuller dues + occupancy guard is not wired yet — revisit `delete_building/floor/house` in `onboarding/service.py`. Tracked in as-built deviations §7.

**Wave process learning:** tightly-coupled service/repository logic → build with ONE implementation agent in dependency order (parallel agents collide on the same files). Parallelism belongs in Phase 3 (independent test files, one per dimension).

**Test-pipeline fix (harness, benefits all modules):** running many concurrent full-suite runs (e.g. several test agents each `pytest -n 4`) piles up zombie pytest procs that share the per-worker DBs (`society_test_gwN`) and deadlock/collide on `uq_permissions_key`. Fixed by making `conftest._seed_baseline` idempotent (pg `ON CONFLICT DO NOTHING`) and raising `run-tests.sh` default workers 4→8 (~26s→~21s). If suite runs hang with no output, check for/kill leftover pytest processes in the backend container.

See [[implementation-workflow]] [[test-infra]] [[modularity-model]] [[dual-role-portals]].
