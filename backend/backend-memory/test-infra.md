---
name: test-infra
description: "The reusable backend test harness — isolated test DB, fixtures, how every module runs its tests"
metadata: 
  node_type: memory
  type: project
  originSessionId: 1f571a20-3780-4435-b9c7-82794e0ab22f
---

The backend test infrastructure (built during Platform Foundation, Phase 4) is **shared by every future module** — not foundation-specific.

**Location:** `backend/tests/conftest.py`, `backend/tests/conftest_dbguard.py`, `backend/scripts/run-tests.sh`, `backend/pytest.ini`.

**How to run (always via the script — it isolates + migrates the test DB):**
`docker compose exec backend bash scripts/run-tests.sh -q` (or pass specific files / pytest args).

**Isolation model:**
- Runs against a DEDICATED `society_test` database (auto-created + `alembic upgrade head` by the script), NEVER the dev `society` DB. So tests are safe to rerun any number of times without touching dev data.
- A guard (`conftest_dbguard.assert_test_database`) REFUSES to run if `DATABASE_URL`'s db name doesn't end in `_test` — prevents accidentally truncating dev/prod.
- **Truncate-and-reseed before EVERY test** (autouse `_reset_db`): truncates all tables (RESTART IDENTITY CASCADE) then seeds baseline (permissions + global role templates + one super-admin). Deterministic, order-independent, fully repeatable after code changes.
- The truncate list is DERIVED DYNAMICALLY from `Base.metadata.sorted_tables` — so a future module's new tables are cleaned automatically with NO edit to the harness.

**Reusable fixtures (conftest.py):** `db` (Session), `client` (TestClient), `auth` (login/bearer/login_ok helper), `superadmin`, `society` (fresh, roles copied), `admin_user` + `resident_user` (provisioned, must_change, password=DEFAULT_MEMBER_PASSWORD), `make_token` (mint crafted-claim access tokens). Known creds exported: SUPERADMIN_EMAIL/PASSWORD, DEFAULT_MEMBER_PASSWORD.

**How a future module plugs in:** add models + Alembic migration (part of building it) → run-tests.sh migrates them into the test DB → models import so truncate covers them → its test files import the existing fixtures and add module-specific ones on top. No harness changes needed.

**Note:** this is a clean-slate test DB (empty schema reseeded per test) — the correct choice for deterministic correctness tests, NOT a copy of production data. A production-data replica would only be for load/perf/debugging, which is a separate future concern.

See [[implementation-workflow]] [[tech-stack]].
