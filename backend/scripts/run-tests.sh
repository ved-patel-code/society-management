#!/usr/bin/env bash
# Run the backend test suite against a DEDICATED, isolated test database.
# The suite truncates every table between tests, so it must never run against the
# dev/prod DB — this script creates + migrates `society_test` and points the app at it.
#
# Usage (from the repo root):
#   docker compose exec backend bash infra/run-tests.sh [pytest args...]
# e.g.
#   docker compose exec backend bash infra/run-tests.sh -q
#   docker compose exec backend bash infra/run-tests.sh tests/test_auth.py -v
set -euo pipefail

TEST_DB="society_test"

# Derive the test DATABASE_URL from the app's configured one, swapping the db name.
BASE_URL="${DATABASE_URL:?DATABASE_URL must be set}"
TEST_URL="${BASE_URL%/*}/${TEST_DB}"

# Create the test database if it doesn't exist (connect via the default db).
ADMIN_URL="${BASE_URL%/*}/postgres"
python - "$ADMIN_URL" "$TEST_DB" <<'PY'
import sys
import psycopg
admin_url, test_db = sys.argv[1], sys.argv[2]
# psycopg wants a libpq URL, not the SQLAlchemy '+psycopg' form.
libpq = admin_url.replace("postgresql+psycopg://", "postgresql://")
with psycopg.connect(libpq, autocommit=True) as conn:
    exists = conn.execute(
        "SELECT 1 FROM pg_database WHERE datname = %s", (test_db,)
    ).fetchone()
    if not exists:
        conn.execute(f'CREATE DATABASE "{test_db}"')
        print(f"created database {test_db}")
    else:
        print(f"database {test_db} already exists")
PY

# Fast, test-ONLY Argon2id params. Password hashing is deliberately expensive in
# production (~83ms/hash); tests only need the code path to run, so we drop the
# cost to ~1ms/hash here. These are NEVER set in the real .env — production keeps
# passlib's strong defaults. (Correctness of the hashing logic is unaffected.)
export ARGON2_TIME_COST="${ARGON2_TIME_COST:-1}"
export ARGON2_MEMORY_COST="${ARGON2_MEMORY_COST:-8}"
export ARGON2_PARALLELISM="${ARGON2_PARALLELISM:-1}"

# Migrate the test DB to head, then run pytest — both with DATABASE_URL overridden.
export DATABASE_URL="$TEST_URL"
echo "using test database: $TEST_URL"
alembic upgrade head
exec pytest "$@"
