# Architecture

> Foundation doc 2 of 5. The stable technical shape of the system.
> Companion docs: [01-project-overview](01-project-overview.md) · [03-backend-and-db-principles](03-backend-and-db-principles.md) · [04-module-template](04-module-template.md) · [05-cross-module-contracts](05-cross-module-contracts.md)

## 1. Tech stack
| Layer | Choice |
|---|---|
| Frontend | Next.js (React), Tailwind — **built after backend**; mobile/phone-first responsive |
| Backend | Python **FastAPI** + SQLAlchemy (ORM) + Alembic (migrations) + Pydantic (validation) |
| Database | **PostgreSQL** |
| File storage | **MinIO** (S3-compatible); swappable to real S3 via a storage interface |
| Background jobs | Worker (APScheduler or Celery) for dues generation + notification reminders |
| Packaging | **Docker**, separate images for frontend and backend; **all dev inside Docker** |
| Orchestration | Docker Compose: `backend`, `frontend`, `postgres`, `minio`, `worker` |

## 2. Modular monolith
One backend process. Internally, **modules are self-contained packages**. Modules communicate through **explicit service interfaces** (never by reaching into each other's tables directly). This gives modularity and clean boundaries without microservice overhead. If a module ever needs to become a service later, its interface boundary makes extraction possible — but that's not a goal now.

## 3. Module framework
- **`ModuleSpec`** — each module declares: `key`, `name`, `router`, `permissions[]`, `default_config`, `depends_on[]`, `is_core`. Modules **self-register** into a `MODULE_REGISTRY` on startup.
- **`society_modules`** table — per-(society, module) flag with `enabled` + `config`. Super-admin toggles; enabling honors `depends_on`.
- **Two request gates** (FastAPI dependencies at router level):
  1. `require_module(key)` — is this module enabled for the caller's society?
  2. `require_permission(key)` — does the caller's role hold this capability?
- **Adding a module** = new package folder + `ModuleSpec` + Alembic migration + declared permissions. **Zero edits to existing modules.**

### 3.1 API URL conventions
The backend is API-only; all routes are **root-level (no version prefix** — if versioning is ever needed, everything gets prefixed with `/api/v{n}`). Prefixes are **scope-based, not per-role**.

| Group | Prefix | Society identified by | Gate |
|---|---|---|---|
| Auth | `/auth/*` | — | public (change-password needs the must-change token) |
| Current user | `/me` | JWT | authenticated |
| **Super-admin** (cross-society platform ops) | `/admin/*` | **id in path** — `/admin/societies/{id}/...` | super_admin |
| **Society-scoped modules** (society_admin + residents) | `/{module}/*` | **from the JWT** (TenantContext) — no id in path | `require_module` + `require_permission` |

- **Super-admin** operates across societies, so the target society is explicit in the path (`/admin/societies/{id}/...`).
- **Society-scoped module** routers each mount under their own key with the society resolved from the token — no `society_id` in the path: `/onboarding/*`, `/houses/*`, `/finance/*`, `/complaints/*`, `/vault/*`, `/notices/*`, `/notifications/*`.
- **society_admin and resident share the same module endpoints** — permissions (`require_permission`), not the URL, decide what each can do. There is deliberately **no per-role prefix**.

## 4. Roles & permissions (data-driven)
Roles are **not a hardcoded enum**. Tables: `roles` (incl. a **`portal`** attribute — `admin | resident | platform`), `permissions` (capability catalog per module), `role_permissions`, `user_roles` (user↔society↔role), `role_module_visibility` (which tabs a role sees).
- Seed roles: **super_admin** (platform), **society_admin** (== secretary, full society control), **resident**.
- **Future roles add with no code change** — e.g. a **tenant** role with limited module visibility + view-only permissions is just new rows.
- **A user can hold multiple roles in one society** (effective permissions = union) — e.g. an admin who also owns a flat. A user whose roles span more than one **portal** picks which portal to enter at login (a **view-only** chooser — it shapes the shell, not permissions) and can switch anytime without re-login. See [platform-foundation §5.1](platform/platform-foundation.md).
- **Reassignable roles:** the `society_admin` role can be moved from one user to another by rewriting a `user_roles` row. This is the mechanism the future **Elections** module uses to hand over society leadership in-app (see [01-project-overview](01-project-overview.md)).

### 4.1 Super-admin scope (platform operator)
The super-admin operates **above** any single society and does a deliberately narrow set of platform tasks:
- **Create a society** and its initial `society_admin` user account.
- **Assign modules** to a society (write `society_modules`) — enable/disable per customer.
- **Create society-scoped roles** and **set their permissions** (`roles`, `role_permissions`).
- Set society-level config (storage limit, default member password).

The super-admin does **not** run day-to-day society operations (finances, complaints, notices) — that's the society_admin's job.

**Interface (now vs later):** the super-admin has a *distinct* interface, but for now it is driven entirely through the **API / Swagger UI** (FastAPI's auto-generated docs). A dedicated super-admin frontend is a later deliverable; the backend endpoints are the real product for this role today.

## 5. Auth model
- **Email = global login identifier.**
- An email with **no `user_roles` in any society cannot log in and receives no reset email** (generic response, no account enumeration).
- Super-admin creates the initial society_admin user + assigns society/role (via Swagger UI for now). Initial password = society default; `password_state = must_change`.
- **First login** → forced password change before any other action.
- **Forgot password** → temp password emailed → forced change on next login.
- **JWT** carries `user_id`, `active_society_id`, `role_ids`. Schema supports multi-society membership later (society switcher) though v1 enforces one society per user at the service layer. The **active portal** is deliberately **not** in the token — it's view-only state (see §4).
- **Dual-portal accounts** (e.g. admin + resident) receive `available_portals` on login and choose a portal/screen; the choice is view-only and switchable in-session.

## 6. Multi-tenancy
- **v1:** app-layer scoping — a `TenantContext` dependency resolves the caller's `society_id`; every query filters by it; every write stamps it. Super-admin bypasses scoping explicitly.
- **Kept RLS-ready:** every tenant table carries `society_id`; composite uniques include it. Postgres Row-Level Security can be enabled later with no schema rework.

## 7. Container layout
```
docker-compose:
  backend   (FastAPI image)                  -> depends on postgres, minio
  worker    (same image, worker entrypoint)  -> postgres, minio
  frontend  (Next.js image)                  -> talks to backend API
  postgres  (Postgres)
  minio     (MinIO, S3 API)
```
All development runs inside these containers.

## 8. Repo layout (monorepo, modular monolith)
```
society/
  docs/                      # foundation docs + per-module design + as-built
  backend/                   # FastAPI modular monolith (built first)
  frontend/                  # Next.js (built later)
  infra/                     # docker-compose, env templates, minio/pg init
  docker-compose.yml
  README.md
```

## 9. Development workflow (git + Docker)

### Docker for all development
- **Everything runs in containers** — backend, worker, DB, MinIO, and (later) frontend. No dependency is installed on the host; `docker compose up` is the one command to run the stack.
- Run commands, tests, and migrations **inside the containers** (e.g. `docker compose exec backend ...`) so every developer and CI uses the identical environment.
- Secrets/config come from **env files** (templates in `infra/`, real values never committed).

### Git best practices
- **Version-controlled from the start**; `main` always stays in a working, deployable state.
- **One branch per unit of work** — a feature, fix, or module (e.g. `feat/onboarding-house-generation`). Never commit new features directly to `main`.
- **Small, focused, descriptive commits** — one logical change each; the message says *what* and *why*, not a vague "update".
- **Merge via review** — changes land on `main` through a reviewed pull/merge request, not direct pushes.
- **`.gitignore`** excludes secrets, `.env` files, build artifacts, and volumes; **never commit secrets or credentials.**
- **Migrations travel with their code** — the Alembic migration that a feature needs is committed in the same change as that feature.
- **Docs travel with their module** — a module's design/as-built doc is updated in the same change that builds or alters it, so docs never drift from code.
- Follow these when **adding a new feature, updating an existing one, or refactoring** — the workflow is the same every time.
