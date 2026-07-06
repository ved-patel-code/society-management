# Onboarding (Module 1) — Build Approach

How Module 1 was built. Design source of truth: `docs/modules/onboarding.md`.
As-built index: `docs/implemented/onboarding.md`.

## Process (lead-core-then-waves, all in Docker)
Same proven process as Module 0, on one integration branch `feat/onboarding`
(feature branch → PR → `main`; never commit to `main` directly).

**Phase 0 — Lead builds the frozen core** (green gate before any fan-out):
- `app/modules/onboarding/` package: `models.py` (5 tables), `numbering.py` (pure
  house-number engine), `schemas.py` (frozen Pydantic contracts + `current_step`
  state machine), `repository.py` (SQL-only, society-scoped), `service.py`
  (type-selection + registry reads + helpers implemented; generation/override/
  complete/later-edit signatures stubbed for the waves), `router.py` (thin, gated),
  `spec.py` (ModuleSpec + register).
- Alembic migration `0002_onboarding.py` (chained off `0001_foundation`).
- Minimal allowed foundation edits (each documented as a deviation): `ModuleSpec`
  gains `default_role_permissions`; `SocietyService.set_modules` auto-grants a
  module's default role permissions on enable; `RoleService.grant_default_module_
  permissions`; `me_service`/`me_router` add `onboarding_required`; register the
  module in `main.py`, `cli/seed.py`, `alembic/env.py`.
- Gate: migration applies, permissions seed, module enable auto-grants
  `onboarding.*` to `society_admin`, type selection works through HTTP, `/me` reports
  `onboarding_required`. 108 tests (100 foundation + 8 smoke).

**Phase 1 — Wave logic** (one Opus 4.8 agent, dependency order to avoid file
collisions on the shared service/repository): state/resume/draft, building flow
(create + map + preview), individual flow (rows), override, complete, later edits
(rename + guarded delete). Verified with 27 numbering unit tests + a full HTTP e2e
driver (type → buildings → AUTO+ground map → preview → override → clash-reject →
complete → /me unlock, all audit rows present). 135 tests.

**Phase 2 — Code-review gate** (Opus 4.8 reviewer, read-only) → must-fixes applied:
see `code-review-findings.md`. 146 tests.

**Phase 3 — Test gate** (parallel Opus 4.8 agents: happy / security / bad-path /
e2e): see `test-gate.md`.

## Sub-agent model assignment (user decision)
- Codebase exploration/mapping agents: **Sonnet 5 (medium)**.
- Code writing, testing, code review agents: **Opus 4.8 (medium)**.

## Key decisions made during the build (not in the design doc)
1. **Default module permissions → roles on enable** (reusable pattern): `ModuleSpec.
   default_role_permissions` + auto-grant in `set_modules`. Onboarding grants
   `society_admin` → `onboarding.manage`/`onboarding.read` when the module is enabled
   for a society. This is the mechanism every later module's "Default seeding
   (data-driven roles)" line uses. Idempotent, additive, audited
   (`permission.granted_by_module`).
2. **`current_step` state machine pinned** to `type_selection → structure_mapping →
   review → completed` (the spec left it narrative).
3. **Delete guard is status-only for v1**: block delete when a house's status !=
   'empty'. The fuller Finance-dues / occupancy guard is **deferred** and MUST be
   wired once House & Occupancy + Finance exist (tracked in the as-built deviations).
4. **`onboarding_required` added to `/me`** so the client locks the shell to the
   wizard while `society.status='onboarding'` (view hint only; authorization
   unchanged).
5. **Building default_houses_per_floor + per-floor override** added (spec §3 named a
   building default but no field existed); floors may each have different counts.
6. **Add-floors endpoint** `POST /onboarding/buildings/{id}/floors` added so floors
   can be added to an already-mapped building as a post-completion later edit.
7. **Continuous sequential seeds only from prior continuous-sequential houses** (not
   AUTO/manual numbers), so "one running sequence across towers" stays clean.
