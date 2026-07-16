---
name: modularity-model
description: "Core modularity rules for the Society Management app — feature flags, data-driven roles, skeleton-then-wire"
metadata: 
  node_type: memory
  type: project
  originSessionId: a92a120b-70b1-4de9-817d-84fa741891a8
---

Non-negotiable modularity principles for the Society app:
- **Per-society feature flags**: one codebase, all modules present. `society_modules` table decides which modules are enabled per society. Super-admin toggles.
- **Data-driven roles**: roles are NOT a fixed enum. `roles` + `permissions` + `role_permissions` + `user_roles` tables. Seed defaults: super_admin, society_admin (== secretary), resident. A society can add custom roles later — e.g. a future **tenant** role with limited module visibility + view-only access. Each role maps to which modules/UI are visible.
- **Skeleton-then-wire for cross-module deps**: when one module needs another not yet built (e.g. onboarding owned-status ID-proof image needs the Vault module), build the schema/field skeleton now, add a NOTE that it requires module X, and wire it once X is built. Never re-architect.
- **Payments abstracted behind a provider interface** so a payment gateway can be added later (future goal) without changing finance core.
- Mobile/phone-friendly responsive UI is a hard requirement.

**Super-admin scope:** narrow platform operator — creates societies + initial society_admin user, assigns modules to a society, creates society-scoped roles + sets their permissions. Does NOT run day-to-day society ops. Has a distinct interface but for now driven via **Swagger UI / API only** (dedicated frontend is future).

**Primary keys:** use `BIGINT GENERATED ALWAYS AS IDENTITY` (= auto-increment integer, not UUID) — faster/smaller/better joins at scale. Enumeration mitigated by society_id scoping + permission checks. `public_id` (opaque id for shareable URLs) is ON HOLD / not implemented now — future only.

**Audit trail** is a first-class requirement: `audit_log` records every state-changing society_admin/super_admin action (actor, action, entity, before/after diff), written in the same transaction by the service layer, append-only.

**Elections module (future):** in-app handover of the `society_admin` role to another user via rewriting `user_roles`. Enabled by the data-driven roles model + audit trail.

**Dev workflow (documented in docs/02 §9):** ALL development runs in Docker containers (run commands/tests/migrations via `docker compose exec`; no host installs). Git from the start: `main` always deployable, one branch per feature/fix/module, small descriptive commits, merge via review, never commit secrets, migrations + module docs travel in the same change as the code. Same workflow for new features, updates, and refactors.

**Why:** User repeatedly stressed the app must be modular so modules add/remove per customer and new functions add without changing old code. Stated on 2026-07-04.
See [[tech-stack]].
