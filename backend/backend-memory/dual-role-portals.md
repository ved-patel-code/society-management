---
name: dual-role-portals
description: "Society app — how one account can be both admin and resident, and the view-only portal chooser"
metadata: 
  node_type: memory
  type: project
  originSessionId: a2ff181d-c513-4a7e-a058-12022bdce190
---

Society app supports **one person being both society_admin and a resident (home owner)** — a first-class case, not an edge case.

**How it works (Platform Foundation §5.1):**
- **One `users` account** (email = login). Occupancy's `create_or_link_user` **adds the resident role to the existing admin account** instead of creating a duplicate.
- **`user_roles` is many-to-many** → account holds both roles; effective permissions = **UNION** of both. Data still follows `house_id` (their own dues/complaints behave like any resident's).

**Portal chooser (VIEW-ONLY — key decision):**
- New column **`roles.portal`** (`admin | resident | platform`). `available_portals(user, society)` = distinct portals across their roles.
- **At login**, if >1 portal, the client shows a **portal chooser** ("continue as Admin / Resident"). Login response returns `available_portals`.
- The choice is **view-only**: shapes tabs/modules/landing (resident→Notice Board, admin→admin dashboard) but **never restricts authorization** — the request gates always use the full role set.
- Therefore **`active_portal` is NOT a JWT claim** (contrast `active_society_id`, which is authZ-relevant). It's client/view state passed to `GET /me?portal=`.
- **Switch anytime, no re-login** (just re-request `GET /me`).
- **Accepted consequence:** an admin who owns a flat can raise AND resolve their own complaint (no separation of duties in v1). Portal-scoped permission locking was **rejected** in favor of view-only.

**Why:** User asked whether an admin can also be a resident and wanted the dual-use account to ask which portal/screen to enter. Decided 2026-07-05. Documented in `docs/platform/platform-foundation.md §5.1`, `docs/02-architecture.md`, `docs/modules/complaints.md`.
See [[modularity-model]] [[docs-structure]].
