# Backend & Database Principles

> Foundation doc 3 of 5. The non-negotiable engineering rules. Every module follows them.
> Companion docs: [01-project-overview](01-project-overview.md) · [02-architecture](02-architecture.md) · [04-module-template](04-module-template.md) · [05-cross-module-contracts](05-cross-module-contracts.md)

## 1. Backend folder structure (module → feature → standard subfolders)
```
backend/app/
  core/                      # framework glue, NOT business logic
    config.py  db.py  security.py  deps.py  registry.py
    storage/   email/        # swappable interfaces + impls
  common/                    # SHARED helpers reused across modules/features
    pagination.py  validators.py  numbering.py  money.py  errors.py  time.py
    (anything used by many functions lives here so it's found without hunting)
  platform/                  # foundational, non-toggleable
    societies/  auth/  users/  roles/
  modules/                   # each toggleable module = one self-contained package
    <module>/
      __init__.py            # ModuleSpec (registers the module)
      permissions.py         # this module's permission keys
      <feature_a>/           # SUB-FOLDER PER FEATURE
        router.py            # HTTP endpoints (thin)
        service.py           # business logic (the brain)
        repository.py        # DB access (queries) for this feature
        schemas.py           # Pydantic request/response models
        models.py            # SQLAlchemy ORM tables (or a shared module models.py)
      <feature_b>/ ...
```
- **Standard subfolders inside each feature:** `router` (HTTP), `service` (logic), `repository` (DB), `schemas` (I/O), `models` (tables). Consistent everywhere.
- **`common/`** holds cross-cutting reusable functions (pagination, validators, number generation, money math, error types, time helpers) so shared logic is in one obvious place — no searching many files.
- **Module models** may live in a single `models.py` per module if a feature split is overkill; features that are big get their own subfolder.

## 2. Layer responsibilities (strict separation)
| Layer | Does | Never does |
|---|---|---|
| **router** | parse/validate request (Pydantic), call service, shape response | business logic, raw DB queries |
| **service** | ALL business logic, rules, permission checks, orchestration, transactions | build HTTP, write SQL by hand |
| **repository** | efficient DB queries, returns only needed data | business decisions |
| **schemas** | request/response contracts + field validation | — |

Routers stay thin; services own the logic; repositories own the queries.

## 3. Business logic & constraints — backend is the source of truth
- **ALL business logic, workflow rules, and validation live in the backend** (service layer). The DB does **not** hold business rules (no CHECK-based business rules, no logic in triggers).
- **The DB keeps ONLY integrity constraints as a safety net:** `PRIMARY KEY`, `FOREIGN KEY`, `NOT NULL`, `UNIQUE`. Reason: under **concurrent requests**, two calls can both pass a backend "is this unique?" check and both insert — only a DB `UNIQUE` reliably prevents the duplicate. Same for FKs preventing orphan rows. This is the industry-standard defense-in-depth split.
- **Every request is fully validated in the backend**: all fields, types, ranges, cross-field rules, and permissions — checked before touching the DB. Never trust the client.

## 4. Query efficiency rules
- **Select only required columns** — no `SELECT *` when a subset suffices.
- **Fetch only required rows** — always paginate lists; filter in SQL, not in Python.
- **Minimize round-trips** — no **N+1**. Use joins / `selectinload` / batched `IN` queries. One logical operation should aim for the fewest DB calls.
- **Push work to the DB** where it's cheaper (aggregations, counts, filtering) rather than pulling rows to compute in Python.
- **Return fast** — endpoints designed for low latency; heavy/slow work (dues generation, notifications) goes to the **worker**, not the request path.
- **Transactions**: a single business operation = a single transaction; use row locks where concurrency matters (e.g. payment allocation on dues).

## 5. Indexing & keys strategy
- **Primary key** on every table: **`BIGINT GENERATED ALWAYS AS IDENTITY`** (auto-incrementing integer), not UUID. Rationale: at scale a sequential 8-byte integer beats a 16-byte random UUID on every axis — smaller indexes and FKs, faster inserts (appends to the B-tree instead of fragmenting it), faster joins, better cache locality.
- **Enumeration is handled by scoping, not by hiding IDs:** sequential IDs are guessable, but every request is already scoped by `society_id` + permission checks, so a caller can never reach another society's row by guessing an ID. This is our defense.
- **`public_id` — ON HOLD (future, not implemented now):** a separate unguessable id for entities exposed in external/shareable URLs was considered but is deferred. We rely on auth + scoping for now, and may add it later only if/when we expose shareable public links.
- **Foreign keys** for every relationship (integrity net).
- **`society_id` is indexed** on every tenant table (it's in nearly every query's filter), usually as the **leading column of composite indexes** matching common query patterns (e.g. `(society_id, status)` for house-status filters, `(society_id, house_id, period_year, period_month)` for dues).
- **Unique indexes** enforce integrity (e.g. house `number` unique per society) and double as fast lookups.
- Index **foreign-key columns** used in joins/filters.
- Indexes are chosen per module's **common queries** (each module doc lists them). Avoid over-indexing write-heavy tables.

## 6. Validation, errors, conventions
- **Pydantic** models validate every request body/query; shared field validators in `common/validators.py`.
- **Consistent error shape**: a single error format `{code, message, details}` from `common/errors.py`; services raise typed domain errors mapped to HTTP by a central handler.
- **Naming**: snake_case (Python/DB), tables plural, timestamps `*_at`, booleans `is_*`.
- **API URL conventions** live in [02-architecture §3.1](02-architecture.md#31-api-url-conventions) — scope-based prefixes (`/admin/*` super-admin, `/{module}/*` society-scoped), root-level, no version prefix.
- **Every table**: `id` (BIGINT identity PK), `created_at`, `updated_at`; append-only/audit tables never UPDATE/DELETE by convention.

## 7. Audit trail (first-class requirement)
Every state-changing action by a **society_admin** (and super_admin) is recorded — this is not optional logging, it is a product requirement for accountability and for the future Elections/handover flow.
- Central **`audit_log`** table: `society_id`, `actor_user_id`, `action` (e.g. `house.status_changed`, `finance.payment_recorded`, `role.assigned`), `entity_type`, `entity_id`, `before`/`after` diff (JSONB), `at`.
- Written by the **service layer** (one place, consistently) whenever a tracked action commits — inside the same transaction as the change so an action and its audit record are atomic.
- Append-only: never updated or deleted.
- Each module's design doc lists which of its actions are audited.
- Powers "who changed what, when" views and gives the Elections module a full history of an admin's tenure.

## 8. Migrations
- **Alembic**, single linear history. `env.py` imports **all** module models so new modules just add a migration — no edits to old migrations.
- Seed data (permission catalog, system roles) via idempotent seed command driven by the module registry.
