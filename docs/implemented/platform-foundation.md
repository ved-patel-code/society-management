# Platform Foundation — As-Built Index

> Lean navigation index (docs/04). Points to code; not a copy of it. Updated as
> each feature package lands. Design source of truth: `docs/platform/platform-foundation.md`.

## Status
- **Phase 1 (shared core) — built.** Repo scaffold, Docker stack, pinned+vetted deps,
  core glue, all 11 models, initial migration, seed CLI, minimal AuditService, worker
  cleanup job, app factory + pre-stubbed routers. Green gate passing.
- **Phase 2 (feature packages) — pending** (auth, societies+modules, users+provisioning,
  roles/permissions, /me, tenant-gate wiring). Each agent fills its pre-stubbed router
  and updates this index.

## File map (Phase 1)
- `app/core/config.py` — Settings from env (DB, JWT HS256, TTLs, email mode, MinIO, superadmin seed).
- `app/core/db.py` — sync engine + SessionLocal + `Base` (BIGINT id + created/updated) + `get_session` (one txn/request).
- `app/core/security.py` — Argon2id hash/verify; PyJWT HS256 create/decode (algorithm pinned); refresh token gen + SHA-256 hash.
- `app/core/registry.py` — `ModuleSpec` + `MODULE_REGISTRY` + `resolve_dependencies` (depends_on) + `all_permission_keys`.
- `app/core/deps.py` — `AuthContext` (+ must-change lockout), `TenantContext` (+ super_admin bypass), `require_super_admin`, `require_permission`, `require_module`, effective-permission union.
- `app/core/email/` — `EmailSender` interface + `TestEmailSender` (log) + `SmtpEmailSender` stub + factory.
- `app/core/storage/` — `ObjectStorage` interface stub (Vault wires later).
- `app/common/` — `errors.py` (typed DomainError + shape), `pagination.py`, `time.py`, `validators.py` (email + password policy).
- `app/platform/models.py` — all 11 foundation tables (frozen schema).
- `app/platform/bootstrap.py` — global role templates + foundation `ModuleSpec` registration.
- `app/platform/audit/service.py` — `AuditService.record` (append-only, in-transaction).
- `app/cli/seed.py` — idempotent seed: permissions catalog + role templates + first super_admin.
- `app/worker/entrypoint.py` + `jobs/cleanup.py` — APScheduler; daily purge of dead auth rows.
- `app/main.py` — app factory: error handlers, router mounts, `/health`, registry.
- `alembic/env.py` + `alembic/versions/0001_foundation_initial.py` — single initial migration (CITEXT + 11 tables).

## Tables owned
societies, society_modules, users, refresh_tokens, password_resets, roles, permissions,
role_permissions, user_roles, role_module_visibility, audit_log.

## Cross-module wiring (provided)
- `AuthContext` / `TenantContext` / `require_module` / `require_permission` — `app/core/deps.py`.
- `AuditService` — `app/platform/audit/service.py`.
- `EmailSender` — `app/core/email/`.
- `MODULE_REGISTRY` + `ModuleSpec` — `app/core/registry.py`.
- `UserProvisioningService` — pending (P5).

## Deviations from design
- None so far. (Record any here as feature packages land.)
