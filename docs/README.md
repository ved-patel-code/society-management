# Society Management — Documentation

Start here. Read the foundation docs in order, then dive into individual modules.

> **All modules are designed; implementation is underway (backend-first).** History of requirements/decisions per module lives in **[../REMAINING-MODULES.md](../REMAINING-MODULES.md)**; the authoritative specs are the per-module docs below. As-built indexes for built modules live in **[implemented/](implemented/)** and build/QA records in **[build-log/](build-log/)**.
>
> **Built so far:** Module 0 — Platform Foundation ([implemented/platform-foundation.md](implemented/platform-foundation.md)) · Module 1 — Onboarding ([implemented/onboarding.md](implemented/onboarding.md)) · Module 2 — House & Occupancy ([implemented/house-occupancy.md](implemented/house-occupancy.md)) · Module 3 — Vault ([implemented/vault.md](implemented/vault.md)). Remaining modules build one at a time on feature branches, in the order below.

## Foundation (read first — stable shared context)
1. [01 — Project Overview](01-project-overview.md) — what the app is, modules, priorities.
2. [02 — Architecture](02-architecture.md) — stack, modular monolith, module framework, roles, auth, tenancy.
3. [03 — Backend & DB Principles](03-backend-and-db-principles.md) — folder structure, layers, logic-in-backend, query efficiency, indexing.
4. [04 — Module Template](04-module-template.md) — the fixed structure every module doc follows.
5. [05 — Cross-Module Contracts](05-cross-module-contracts.md) — how modules depend on / talk to each other.

> A new session can read docs 01–05 and then design any single module without re-explaining the app.

## Platform Foundation (`platform/`)
- [platform/platform-foundation.md](platform/platform-foundation.md) — ✅ designed. The always-on bedrock (auth, users, roles/permissions, societies, module allocation, tenant scoping) that every module depends on.

## Module design docs (`modules/`)
Written one at a time, before building. Order after the foundation: **Onboarding → House & Occupancy → Vault → Finance → Complaints → Notice Board → Notifications**. **All ✅ designed** — the design phase is complete; implementation (backend first) is next.
- [modules/onboarding.md](modules/onboarding.md) — ✅ designed · 🛠️ **built** ([as-built](implemented/onboarding.md)). Society structure mapping (buildings/floors/houses or rows/houses), 3 numbering modes, blocking resumable wizard.
- [modules/house-occupancy.md](modules/house-occupancy.md) — ✅ designed · 🛠️ **built** ([as-built](implemented/house-occupancy.md)). House status lifecycle, owner/tenant occupancy, ID proof (optional), status filters.
- [modules/vault.md](modules/vault.md) — ✅ designed · 🛠️ **built** ([as-built](implemented/vault.md)). File-manager document storage (MinIO), house-centric folders, Trash, 5 GB default limit, admin-only.
- [modules/finance.md](modules/finance.md) — ✅ designed. Effective-dated rate, materialized monthly dues, oldest-first collection + prepaid, computed reserve ledger, full analytics.
- [modules/complaints.md](modules/complaints.md) — ✅ designed. House-scoped complaints; open→in_progress→resolved→closed→archived (auto-archive after configurable days); predefined+extendable categories (common-area = a category); status-only + admin note; ≤2 report + ≤2 proof images in Vault.
- [modules/notice-board.md](modules/notice-board.md) — ✅ designed. Society-wide broadcast; draft→published, edit (with "edited" marker)/pin/withdraw/optional expiry; rich text + unlimited Vault attachments; admin read receipts; resident-portal landing feed; emits `notice_posted` for Notifications.
- [modules/notifications.md](modules/notifications.md) — ✅ designed. In-app notification + reminder engine (event-driven + scheduled rules); dues cadence advance/due-day/every-N with consolidated total; owners + admin alerts; clear-on-read feed; in-app only in v1 (email/push future).

## As-built reference (`implemented/`)
Written during/after building each module — the accurate "what actually exists" reference.
- [implemented/platform-foundation.md](implemented/platform-foundation.md) — Module 0, COMPLETE.
- [implemented/onboarding.md](implemented/onboarding.md) — Module 1, COMPLETE (218 tests).
- [implemented/house-occupancy.md](implemented/house-occupancy.md) — Module 2, COMPLETE (372 tests).
- [implemented/vault.md](implemented/vault.md) — Module 3, COMPLETE (521 tests).
