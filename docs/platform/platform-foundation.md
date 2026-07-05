# Platform Foundation — Design

> Design doc for the always-on foundation layer beneath every module.
> Foundation reading: [../01-project-overview](../01-project-overview.md) · [../02-architecture](../02-architecture.md) · [../03-backend-and-db-principles](../03-backend-and-db-principles.md) · [../05-cross-module-contracts](../05-cross-module-contracts.md)
>
> **Confirmed decisions baked in:** JWT access + revocable DB refresh token · fine per-action permissions · auto-provision/link resident accounts on occupancy · email via a single swappable `EmailSender` interface with a terminal/test mode · BIGINT identity PKs · backend owns logic, DB keeps PK/FK/NOT NULL/UNIQUE · app-layer tenant scoping (RLS-ready).

## 1. Purpose & scope
The **Platform Foundation** is the non-toggleable bedrock: identity, access control, and multi-tenancy. It provides societies, users, data-driven roles/permissions, auth/sessions, per-society module allocation, tenant scoping, a user-provisioning service, an email interface, and the audit log. **Every module (including Onboarding) depends on it.**

**In scope now:** societies + config, module allocation, users, roles/permissions, auth (login, first-login change, forgot-password), tenant scoping, user-provisioning service, email interface (test mode), audit log.

**Out of scope (noted, not built now):** dedicated super-admin frontend (Swagger UI for now), `public_id`, real SMTP (test-mode email now), multi-society-per-user (schema-ready; service enforces one society per user in v1), MFA.

## 2. Actors & bootstrap sequence
- **super_admin** (platform) — creates societies, allocates modules, creates the initial society_admin, defines society roles/permissions. Operates via Swagger/API for now.
- **society_admin** (== secretary) — full control within their society.
- **resident** (owner/tenant) — limited view; **auto-provisioned** when occupancy is set.

**Order that must complete before Onboarding can run:**
1. **Platform seed** — permission catalog (from module registry) + global role templates + first super_admin.
2. super_admin **creates a society** — sets its **name** + config (+ admin below); **`type` is left unset** (the society_admin picks building vs individual as onboarding step 1).
3. super_admin **allocates modules** to it (`society_modules`), including `onboarding`.
4. **Society roles instantiated** (society_admin, resident copied from templates into society-scoped rows).
5. super_admin **creates the society_admin user** + assigns the role.
6. society_admin **first login → forced password change**.
7. Onboarding is now runnable by that admin.

## 3. Data model
All tables: `id` BIGINT identity PK, `created_at`, `updated_at`. Tenant tables carry `society_id`. Only integrity constraints in DB (PK/FK/NOT NULL/UNIQUE); all logic in services.

**societies** — `name`, `type`(building|individual_houses) **NULLABLE** — set by the society_admin during onboarding (step 1), not at creation — `status`(onboarding|active|suspended), `storage_limit_bytes`, `default_member_password_hash` **NOT NULL**, `currency`, `timezone`, `settings` JSONB.
- **Super-admin MUST set the society default password at creation** (required input). Stored **hashed** (Argon2id), never plaintext. New member accounts receive this hashed default + `must_change`. Future: society_admin can change the default password.
- idx: PK. **No unique on `name`** — duplicate society names are allowed (distinguished by id).

**society_modules** — `society_id` FK, `module_key`, `enabled` BOOL, `config` JSONB, `enabled_by`, `enabled_at`.
- UNIQUE(`society_id`,`module_key`); idx(`society_id`).

**users** — `email` CITEXT UNIQUE (global login id), `password_hash`, `password_state`(must_change|active), `is_platform_super_admin` BOOL, `full_name`, `phone`, `is_active` BOOL, `last_login_at`.
- UNIQUE(`email`); idx(`email`).

**refresh_tokens** — `user_id` FK, `token_hash` (store hash, never raw), `expires_at`, `revoked_at` NULL, `created_at`, `user_agent`/`ip` (optional).
- idx(`user_id`), idx(`token_hash`). Rotated on use; revoke = set `revoked_at`.

**password_resets** — `user_id` FK, `temp_password_hash`, `expires_at`, `consumed_at` NULL.
- idx(`user_id`).

**roles** — `society_id` FK NULL (NULL = global template), `key`, `name`, `is_system` BOOL, `scope`(platform|society), `portal`(admin|resident|platform).
- UNIQUE(`society_id`,`key`).
- **`portal`** declares which UI context (portal) a role belongs to — the basis for the login **portal chooser** (see §5). Seeded: `society_admin`→`admin`, `resident`→`resident`, `super_admin`→`platform`. Set by the super-admin when creating a custom role, so new roles (e.g. a future `tenant`) stay data-driven. `portal` is a **view concept only** — it never restricts permissions.

**permissions** — `key` UNIQUE (e.g. `houses.update_status`), `module_key`, `description`. Seeded from the module registry.
- UNIQUE(`key`); idx(`module_key`).

**role_permissions** — `role_id` FK, `permission_id` FK. UNIQUE(`role_id`,`permission_id`).

**user_roles** — `user_id` FK, `society_id` FK, `role_id` FK, `assigned_by`, `assigned_at`.
- UNIQUE(`user_id`,`society_id`,`role_id`); idx(`user_id`,`society_id`); idx(`society_id`,`role_id`).

**role_module_visibility** — `role_id` FK, `module_key`, `visible` BOOL. UNIQUE(`role_id`,`module_key`). Drives which tabs the frontend shows.

**audit_log** — `society_id` NULL, `actor_user_id`, `action`, `entity_type`, `entity_id`, `before` JSONB, `after` JSONB, `at`.
- idx(`society_id`,`at`); idx(`actor_user_id`,`at`). Append-only.

## 4. Auth design
- **Password hashing:** **ALL passwords stored hashed with Argon2id** (via passlib/argon2) — including the society default password; **never plaintext anywhere**. Policy: min length + complexity; new password must differ from the default/temp.
- **Access token (JWT):** short-lived (~15 min). Claims: `user_id`, `active_society_id`, `role_ids`, `password_state`. Signed (HS256/RS256). Stateless — no DB lookup on normal requests.
- **Refresh token:** random, **stored hashed** in `refresh_tokens`; **rotated** on each use; revocable (logout, deactivate, admin removal). Longer-lived (e.g. 7–30 days).
- **Login** (`email` + password): reject if email has **no `user_roles` in any society** (generic message, no enumeration) or `is_active=false`. On success issue access+refresh; set `last_login_at`; the response also returns **`available_portals`** (distinct `roles.portal` for the user's roles in the active society) so the client knows whether to show the **portal chooser** (see §5).
- **`active_portal` is NOT a token claim.** Because portals are **view-only** (they shape the shell, not authorization — §5), the chosen portal lives in client/view state and is passed to `GET /me`; it never enters the JWT. Contrast `active_society_id`, which *is* a claim because tenant scoping is authorization-relevant.
- **First-login / must-change:** if `password_state=must_change`, the access token flags it and **every endpoint except change-password is rejected** until the password is changed.
- **Forgot-password:** if the email maps to a society → create `password_resets` temp password, send via `EmailSender`, set `password_state=must_change`; if not → generic 200, no email. Login with temp → forced change; temp `consumed_at` set.
- **Revocation on sensitive events:** deactivating a user, removing their role, or **removing them from a house (occupancy change / status update)** revokes their refresh tokens; the short access lifetime bounds the residual window.

## 5. Roles & permissions design
- **Fine per-action permission keys**, organized by module, **seeded from each module's `ModuleSpec.permissions`** on startup/seed.
- **Global role templates** (`super_admin`, `society_admin`, `resident`) with `society_id NULL`.
- **On society creation**, the default society roles (`society_admin`, `resident`) are **instantiated as society-scoped rows** (copied from templates) so each society can customize its roles/permissions independently without affecting others. `super_admin` stays global/platform.
- **Effective permissions** = union of `role_permissions` across all the user's roles in the active society. **Multiple roles per user in a society are supported** (e.g. an admin who also owns a flat).
- **Custom roles** (e.g. a future view-only **tenant**) = new society-scoped `roles` row + chosen permissions + visibility. No code change.
- `role_module_visibility` gives the frontend a simple "tabs to show" list per role.

### 5.1 Dual-role users & portals (admin who is also a resident)
The **same person** can be both the society_admin and a home owner — this is a first-class case, not an edge case, and falls straight out of the model above:
- **One account.** Identity is one `users` row (email = login). When Occupancy provisions the owner of a house and the email already belongs to the admin, `create_or_link_user` (§8) **adds the `resident` role to the existing account** — never a duplicate login.
- **Both roles, permissions unioned.** `user_roles` is many-to-many (`UNIQUE(user_id, society_id, role_id)`), so the account holds `society_admin` **and** `resident`; effective permissions = the **union** of both (the rule above). Data still follows the house (`house_id`), so their own dues/complaints behave exactly like any resident's.

**Portal chooser (view-only):**
- A **portal** is a UI context tied to roles via `roles.portal` (`admin | resident | platform`). `available_portals` for a user = the distinct `roles.portal` across their roles in the active society.
- **At login,** if `available_portals` has **more than one** entry, the client shows a **portal chooser** ("continue as Admin / as Resident"); with a single portal it proceeds directly.
- The choice is **view-only**: it decides which **tabs/modules + landing page** the shell renders (resident → Notice Board; admin → admin dashboard). It does **not** restrict authorization — the two request gates always use the account's **full** role set. So `active_portal` is never a token claim; it's client/view state passed to `GET /me`.
- **Switching is instant, no re-login:** a "Switch portal" action just re-requests `GET /me` for the other portal.
- **Consequence (accepted):** because permissions are unioned, an admin who owns a flat can both **raise** a complaint (resident portal) and **resolve/close** it (admin portal) — no separation of duties. Acceptable for a small society where the secretary genuinely does both; revisit only if strict separation is ever required.

## 6. Societies & module allocation
- **Create society** (super_admin) with config (storage limit, default member password, currency, timezone); status starts `onboarding`.
- **Allocate modules** — write `society_modules`; enabling a module **enforces `depends_on`** (e.g. can't enable finance without onboarding).
- **Two request gates** (FastAPI dependencies): `require_module(key)` (enabled for this society?) + `require_permission(key)` (role holds it?).
- **`GET /me`** returns the caller's profile + active society + **`available_portals`** + the **`active_portal`** (selected portal, from a query param / client state; defaults to the sole portal) + the **portal-scoped enabled-and-visible modules + landing + permission hints** for that portal, so the frontend renders the right shell. Authorization itself (the two gates) still uses the caller's full role set — `GET /me` is a view hint, not the authZ source.

## 7. Tenant scoping
- **`TenantContext`** dependency resolves `active_society_id` from the token / `user_roles`.
- Every repository query **filters by `society_id`**; every write **stamps it**. Enforced in the repository/service layer (single source of truth).
- **super_admin bypass** — platform actor operates across societies via an explicit context flag.
- **RLS-ready:** schema keeps `society_id` everywhere so Postgres Row-Level Security can be added later with no rework.

## 8. User-provisioning & access service (interface consumed by other modules)
- `create_or_link_user(email, society_id, role_key, profile) -> user` — creates a new user with the **society default password (hashed) + must_change**, or **links an existing email** by adding the role. Used by super_admin (create society_admin) and by **Occupancy** (auto-provision the owner account).
- Enforces **one society per user** in v1 (flags a conflict if the email already belongs to another society).
- `revoke_house_access(user, house)` — removes the user's occupancy link to that house and **revokes their refresh tokens** (old email can't get back in). **Deactivates the account only if it has no remaining house/role** (orphaned); otherwise the account stays. If the **same email continues** into the new occupancy (e.g. owner across owned→rented), access is **not** revoked.
- `assign_role`, `remove_role`, `deactivate_user` — also used later by the **Elections** module for admin handover.
- All actions write `audit_log`.

**Data-ownership principle:** all house-scoped data (dues, payments, complaints, documents) FKs to **`house_id`, never to a user**. Occupant accounts link to a house via `house_occupancies.user_id`. Changing/replacing occupants never alters house data or history — a status change made by mistake loses nothing.

## 9. Email interface
- **`EmailSender`** interface — one place to swap providers.
- **Modes:** `test` (renders the email to the terminal/log so flows are verifiable without any provider — the default in dev) and `smtp` (real provider, wired later). Chosen by config; callers never change.
- Used by: forgot-password, initial default-password notice.

## 10. Endpoints (design intent)
Follows the **API URL conventions** in [../02-architecture](../02-architecture.md#31-api-url-conventions): root-level (no version prefix), scope-based prefixes — `/admin/*` for super-admin (society id in path), `/{module}/*` for society-scoped (society from the JWT).

Auth: `POST /auth/login` (returns `available_portals`), `POST /auth/refresh`, `POST /auth/logout`, `POST /auth/change-password`, `POST /auth/forgot-password`.
Me: `GET /me?portal=` (profile + society + `available_portals` + active portal + that portal's visible modules + landing + permissions).
Super-admin — Societies: `POST/GET/PATCH /admin/societies`. Modules: `PUT /admin/societies/{id}/modules`. Users: `POST /admin/societies/{id}/users`, `PATCH /admin/users/{id}` (deactivate), `POST /admin/users/{id}/roles`. Roles: `POST /admin/societies/{id}/roles` (accepts `portal`), `PUT /admin/roles/{id}/permissions`.
(Each carries its module/permission gates; shapes detailed when built. Society-scoped module endpoints — `/onboarding/*`, `/houses/*`, etc. — are defined in each module's own design doc.)

## 11. Inter-module contracts
**Provides to all modules:** current-user/`AuthContext`, `TenantContext`, `require_module`, `require_permission`, `UserProvisioningService`, `AuditService`, `EmailSender`, the `MODULE_REGISTRY`.
**Consumes:** each module's `ModuleSpec` (permissions + module key) at registration/seed time.

## 12. Audit (foundation-level events recorded)
society created/updated, module allocated/toggled, user created/deactivated, role created, role assigned/removed, permission set changed. (Login success/failure: counts/security only — see open questions.) Append-only, written in the same transaction as the change.

## 13. Background jobs
- **Expired-token cleanup** — periodic purge of expired/revoked `refresh_tokens` and consumed `password_resets`. Minor; runs in the worker. No other foundation jobs.

## 14. Resolved decisions
1. **Society-scoped roles by copy** — on society creation, copy `society_admin` + `resident` templates into per-society rows for independent customization.
2. **Login brute-force protection (rate-limit/lockout) — DEFERRED** to later (before production). Not built now.
3. **Society names — duplicates allowed** (no global unique constraint; distinguished by id).
4. **Super-admin bootstrap** — first super_admin seeded via a one-time **CLI/seed command** reading env vars (no public signup route).
5. **Refresh-token rotation on every use** — revoke old + issue new; reuse of a rotated token = theft signal → revoke the chain.
6. **Society default password REQUIRED at creation** — super_admin must set it; stored hashed (Argon2id), never plaintext. Future: society_admin can change it.
7. **Rented dues (v1): owner only** — owner is responsible for dues and has the login. **Tenant login & view are DEFERRED** (designed later; docs updated then).
8. **Occupant removal:** unlink from house + revoke sessions; **deactivate the account only if orphaned** (no other house/role). House data/history preserved.
9. **Data tied to house, not login** — dues/payments/complaints/docs FK to `house_id`; occupant↔login via `house_occupancies.user_id`.
10. **Dual-role accounts (admin who is also a resident)** — supported via multiple `user_roles` + union of permissions + link-existing-email provisioning (§5.1). One account, no duplicate login.
11. **Portal chooser — VIEW-ONLY.** Multi-portal accounts pick a portal at login (`roles.portal` → `available_portals`); the choice shapes tabs/modules/landing only and never restricts authorization. `active_portal` is client/view state, **not a JWT claim**. **Switchable in-session, no re-login.**

## 15. Open questions / future
- **Tenant (renter) login + view** (deferred — design later), **society_admin changing the society default password** (future), login brute-force protection (deferred, add before production), `public_id` (deferred), multi-society-per-user, real SMTP provider, super-admin frontend, MFA, login-attempt logging policy.
- **Portals:** per-portal **default landing pages** finalize when Notice Board is designed (resident landing = Notice Board). Server-side "remember my last portal" = client-side for v1 (could persist later). Portal-scoped **permission locking** (stricter separation of duties) was **rejected in favor of view-only** — revisit only if required.

---

## Notes carried into the House & Occupancy design (per-status access)
- **Access follows occupancy; data follows the house.** A status/info change revokes the departing occupant's login (refresh tokens killed → old email can't get in).
- **empty:** no logins tied to the house.
- **owned:** owner account auto-provisioned (login).
- **owned → rented:** prefill the owner email from the prior owned record → **owner keeps access (not revoked)** and remains responsible for dues; **tenant login & view = DEFERRED** (design later).
- **to_let / for_sale:** owner account (login); **maintenance/dues obligations still apply** (owner responsible).
- **Owner replaced (e.g. sold):** old owner unlinked + sessions revoked (deactivated if orphaned); new owner provisioned; **house history retained**.
- Link occupant↔login via `house_occupancies.user_id`; all house data FKs to `house_id`.
