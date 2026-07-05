# Platform Foundation — As-Built Index

> Lean navigation index (docs/04). Points to code; not a copy of it. Design source
> of truth: `docs/platform/platform-foundation.md`. Build/QA record:
> `docs/build-log/`.

## Status
**COMPLETE** — built, code-reviewed, 100 tests passing, merged to `main` (PR #1).
Shared core + all feature packages (roles, societies+modules, auth, users+provisioning,
/me) + tenant-scoping audit + full test suite.

## File map
Core (framework glue, not business logic):
- `app/core/config.py` — env-driven Settings (DB, JWT HS256 + TTLs, Argon2 cost knobs, email mode, MinIO, superadmin seed); validates `JWT_SECRET` ≥32 bytes.
- `app/core/db.py` — sync engine + `SessionLocal` + `Base` (BIGINT id + created/updated) + `get_session` (one txn/request).
- `app/core/security.py` — Argon2id hash/verify; PyJWT HS256 create/decode (alg pinned); refresh-token gen + SHA-256 hash.
- `app/core/registry.py` — `ModuleSpec` + `MODULE_REGISTRY` + `resolve_dependencies` (depends_on) + `all_permission_keys`.
- `app/core/deps.py` — `AuthContext` (+ must-change lockout), `TenantContext` (+ super_admin bypass), `require_super_admin`, `require_permission`, `require_module`, effective-permission union.
- `app/core/email/` — `EmailSender` interface + `TestEmailSender` (log) + `SmtpEmailSender` stub + factory.
- `app/core/storage/` — `ObjectStorage` interface stub (Vault wires later).
- `app/common/` — `errors.py` (typed DomainError + {code,message,details}), `pagination.py`, `time.py`, `validators.py` (email + password policy).

Platform (foundational, non-toggleable):
- `app/platform/models.py` — all 11 foundation tables (frozen schema).
- `app/platform/bootstrap.py` — global role templates + foundation `ModuleSpec` registration.
- `app/platform/audit/service.py` — `AuditService.record` (append-only, in-transaction).
- `app/platform/auth/{token_service,service,repository,router,schemas}.py` — auth feature.
- `app/platform/roles/{service,repository,router,schemas}.py` — roles/permissions feature.
- `app/platform/societies/{service,repository,router,schemas}.py` — societies + module allocation.
- `app/platform/users/{provisioning,repository,router,schemas}.py` + `{me_service,me_router}.py` — users/provisioning + /me.
- `app/cli/seed.py` — idempotent seed (permissions + role templates + first super_admin; policy-checks the seed password).
- `app/worker/entrypoint.py` + `jobs/cleanup.py` — APScheduler; daily purge of dead auth rows.
- `app/main.py` — app factory: error handlers, router mounts, `/health`, registry.
- `alembic/env.py` + `versions/0001_foundation_initial.py` — single initial migration (CITEXT + 11 tables).

## Functions (summary · deps · @location)
Auth:
- `TokenService.issue_pair / rotate / revoke_all_for_user / revoke_one` — access+refresh issuance; rotation on every use; reuse=theft → revoke all user tokens in an ISOLATED txn + audit `auth.token_reuse_detected`. deps: core/security, refresh_tokens, AuditService. @ auth/token_service.py
- `AuthService.login / change_password / forgot_password` — login (no enumeration, timing-equalized, super_admin allowed role-less), must-change escape hatch, forgot-password (test-mode email, generic response). deps: TokenService, RoleService.available_portals, EmailSender, users/password_resets. @ auth/service.py
Roles:
- `RoleService.instantiate_society_roles / effective_permission_keys / available_portals / visible_modules_for_portal / create_role / set_role_permissions` — roles-by-copy (incl. role_permissions + visibility; super_admin excluded; idempotent), permission union, view-only portals. deps: roles/permissions/user_roles/role_module_visibility, AuditService. @ roles/service.py
Societies:
- `SocietyService.create_society / list_societies / get_society / update_society / set_modules` — create (default pw policy-checked + Argon2id, type null, status onboarding, calls instantiate_society_roles), module enable with depends_on, no-op audit suppression. deps: societies/society_modules, MODULE_REGISTRY, RoleService, AuditService. @ societies/service.py
Users/provisioning:
- `UserProvisioningService.create_or_link_user / assign_role / remove_role / deactivate_user / revoke_house_access` — new-or-link (dual-role, one-society-per-user), role mgmt with token revocation, house-access skeleton (orphan-deactivate), last-admin `society.admin_emptied` warn-audit. deps: users/user_roles/roles/societies, TokenService, AuditService. @ users/provisioning.py
- `build_me_view` — /me view assembly (portals/modules/landing/permissions; view-only active_portal; super_admin fixed shell). deps: RoleService, AuthContext. @ users/me_service.py
Audit:
- `AuditService.record` — append one audit row in the caller's transaction. deps: audit_log. @ audit/audit service.py

## Tables owned
societies, society_modules, users, refresh_tokens, password_resets, roles, permissions,
role_permissions, user_roles, role_module_visibility, audit_log.

## Audited actions (emitted)
society.created, society.updated, society.admin_emptied, module.allocated,
user.created, user.deactivated, user.password_changed, role.created, role.assigned,
role.removed, permission.set_changed, house.access_revoked, auth.token_reuse_detected.

## Cross-module wiring (provided to other modules)
- `AuthContext` / `TenantContext` / `require_module` / `require_permission` — `app/core/deps.py`.
- `AuditService` — `app/platform/audit/service.py`.
- `EmailSender` — `app/core/email/`.
- `MODULE_REGISTRY` + `ModuleSpec` — `app/core/registry.py`.
- `UserProvisioningService` — `app/platform/users/provisioning.py` (Occupancy/Elections consume later).
- `RoleService` (effective permissions, portals, roles-by-copy) — `app/platform/roles/service.py`.

## Testing
Reusable harness in `backend/tests/` (see `test-infra` memory / `docs/build-log/`): isolated
`society_test` DB (per-xdist-worker), truncate+reseed, fast test-only Argon2 params, fixtures
(`society`, `admin_user`, `resident_user`, `auth`, `make_token`). Run:
`docker compose exec backend bash scripts/run-tests.sh`. 100 tests, all passing.

## Deviations from design
- **Theft path commits in an isolated session** (not the request session) to persist the
  chain-revocation + `auth.token_reuse_detected` audit through the raised 401 — the single
  documented exception to "services never commit" (docs/PF §12/§14.5).
- **`society.admin_emptied`** audit event added (warn-but-allow last-admin removal) — a
  reviewed decision beyond the original design (docs/build-log H2).
- **JWT_SECRET ≥32-byte** startup guard added (hardening surfaced by tests).
- Everything else matches `docs/platform/platform-foundation.md`.
