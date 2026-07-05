# Tenant-Scoping & Authorization-Gate Audit (P7)

> Audit of every `app/platform/*/repository.py` against docs/PF §7 (every tenant
> query filters `society_id`, every write stamps it) and the two gates in
> `core/deps.py` (docs/PF §5/§6). Proof tests live in
> `backend/tests/test_tenant_and_gates.py`.

## Method

For each repository, every query was classified as one of:
- **tenant-scoped** — reads/writes a table carrying `society_id` for a *specific*
  society → MUST filter/stamp `society_id`.
- **global (foundation)** — `users`, `refresh_tokens`, `password_resets` are
  global login/session tables (docs/PF §3): one identity, one login, sessions
  attached to the user, not a society. No `society_id` column exists; scoping them
  by society would be wrong.
- **tenant-root / super-admin** — `societies` itself is the tenant root and its
  reads are gated by `require_super_admin` (platform actor operating across
  societies, docs/PF §7). A society has no parent society to filter by.
- **PK-keyed** — keyed by a primary key (e.g. `roles.id`, `role_permissions` by
  `role_id`) where the row already belongs to exactly one society; the society
  membership is fixed by the PK, so an extra `society_id` predicate is redundant.

## Findings — PASS / FINDING per file

### `auth/repository.py` — **PASS**
- `get_user` / `find_user_by_email` / `set_last_login` / `set_password` → `users`
  (global). Correct: no `society_id`.
- `active_society_and_role_ids` → filters `user_roles` by `user_id`, returns the
  society_id + that society's role_ids (v1 one-society rule, lowest id chosen).
  Tenant table read is correctly keyed by the user; it *derives* the society.
- `add_refresh_token` / `find_refresh_token_by_hash` /
  `active_refresh_tokens_for_user` → `refresh_tokens` (global, per-user). Correct.
- `add_password_reset` / `active_password_resets_for_user` → `password_resets`
  (global, per-user). Correct.
- No tenant-scoped query is missing a `society_id` filter.

### `users/repository.py` — **PASS**
- `get` / `find_by_email` / `add` → `users` (global). Correct.
- `get_society` → `societies` by PK (super-admin provisioning path). Correct.
- `society_role_by_key` → filters `Role.society_id == society_id`. Stamped ✓.
- `get_user_role` → filters `user_id`, `society_id`, `role_id`. Scoped ✓.
- `user_society_ids` → per-user (one-society check); returns society_ids. Correct.
- `count_user_roles` → per-user orphan detection (any society). Correct — it is a
  cross-society "does the account still have ANY role" check by design (docs/PF §8).
- `add_user_role` / `delete_user_role` → the `UserRole` object carries
  `society_id`, set by the service (`create_or_link_user` / `assign_role`). Stamped ✓.

### `societies/repository.py` — **PASS**
- `add` / `get` / `list_page` → `societies` is the tenant-root table; these run
  under `require_super_admin` (platform scope). No parent `society_id` to filter —
  correct by design (docs/PF §7 super-admin bypass).
- `list_modules` → filters `SocietyModule.society_id == society_id`. Scoped ✓.
- `add_module` → the `SocietyModule` object carries `society_id`, set by the
  service (`set_modules`). Stamped ✓.

### `roles/repository.py` — **PASS**
- `get_role` → by PK; a role belongs to one society (fixed by PK). Correct.
- `global_templates_by_keys` → filters `Role.society_id IS NULL` (templates are
  intentionally global). Correct.
- `society_role_keys` / `society_role_by_key` → filter `Role.society_id`. Scoped ✓.
- `permission_ids_for_keys` → `permissions` catalog is global (docs/PF §3,
  `permissions.key` UNIQUE globally). Correct: no `society_id`.
- `role_permission_ids` / `role_permission_keys` / `add_role_permissions` /
  `clear_role_permissions` → keyed by `role_id` (PK-keyed); the role fixes the
  society. Correct.
- `copy_role_module_visibility` → keyed by `role_id`. Correct.
- `effective_permission_keys` → filters `UserRole.user_id` **and**
  `UserRole.society_id`. Scoped ✓ (this is the authorization union; matches the
  inline `_effective_permission_keys` in `core/deps.py`).
- `user_portals` → filters `UserRole.user_id` and `UserRole.society_id`. Scoped ✓.
- `visible_module_keys_for_portal` → filters `UserRole.society_id == society_id`
  **and** `SocietyModule.society_id == society_id` (both tenant tables scoped in
  the same join). Scoped ✓.

## Gates (`core/deps.py`) — no behavior change

Reviewed; no bug found, **no edits made** (task rule).
- `_effective_permission_keys` — society-scoped union; returns `∅` when
  `society_id is None`. Matches `RoleRepository.effective_permission_keys`.
- `require_permission(key)` — denies unless `auth.has_permission(key)`; super-admin
  short-circuits `True` (platform ops gate on the flag, not perm rows).
- `require_module(key)` — super-admin bypass; else requires an
  `enabled` `SocietyModule` row for `(active_society_id, key)`; both the
  `no active society` and `not enabled` cases raise `ModuleDisabledError` (403).

## Fixes applied

**None.** No tenant-scoped query was found missing its `society_id` filter, and no
write was found unstamped. Foundation global tables (`users`, `refresh_tokens`,
`password_resets`) and the tenant-root `societies` table are correctly not
society-filtered.

## Proof tests

`backend/tests/test_tenant_and_gates.py` (real DB via `SessionLocal`, all rows
cleaned up in a `finally`):
- **(a) require_permission** — HTTP via `TestClient` with real access tokens:
  caller lacking the perm → 403 `permission_denied`; caller holding it → 200.
- **(b) require_module** — dependency callable exercised with a real session:
  society without the module → 403 `module_disabled`; enabled → passes.
- **(c) cross-tenant isolation** — Society A + B via `SocietyService`; a role/user
  in A; asserts the roles/effective-permission/portal repository reads scoped to B
  return nothing of A's, and A's reads don't see B.
- **(d) super_admin bypass** — `require_module` returns the auth context (no 403)
  when `is_super_admin`, even with no active society and no module row.
